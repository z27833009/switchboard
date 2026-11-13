"""RAG suite: four retrieval strategies over one frozen dataset.

Each case names the chunks that answer it by their portable key
(``source#ordinal``), not by database id. The suite resolves the keys against
the tenant's ingested corpus at eval time, then runs the four strategies of
design.md section 9.3 on the same case:

* query as written: vector search, no rewriting;
* always rewrite: rewrite first, then vector search;
* conditional rewrite: vector search, rewrite once when the best score is
  below the tenant's ``retrieval.min_score``;
* conditional rewrite plus hybrid search: the same rewriting rule with vector
  plus full-text search fused by RRF.

The corpus and the embeddings must exist, so the suite needs the compose
database and the configured embedder. The provider is used only for rewrite
calls; a run with ``--provider mock`` replays those calls from fixtures but
still needs the real embedder to measure retrieval.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import asyncpg

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.retrievers.pgvector import PgVectorRetriever
from switchboard.agents.retrieval import RetrievalStrategy, retrieve_with_strategy
from switchboard.core.config import Settings, tenant_context
from switchboard.core.errors import ConfigError, FixtureMissError
from switchboard.core.models import (
    Citation,
    EvalCase,
    EvalOutcome,
    JudgeEvidence,
    JudgeRequest,
    RetrievedChunk,
    RunContext,
    Score,
    Turn,
    Usage,
)
from switchboard.core.ports import Embedder, EvalDeps, Judge
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.rag_answer import answer_from_chunks
from switchboard.eval.scorers.rag import (
    chunk_key,
    parse_chunk_key,
    recall_at_k,
    reciprocal_rank,
    score_rag,
)
from switchboard.eval.suite import SuiteContext

TOP_K = 5
# Judge mode reads the retrieval result of the production strategy (design 9.3).
JUDGE_STRATEGY = "conditional_hybrid"
STRATEGIES: tuple[RetrievalStrategy, ...] = (
    RetrievalStrategy(name="query_as_written", search="vector", rewrite_mode="never"),
    RetrievalStrategy(name="always_rewrite", search="vector", rewrite_mode="always"),
    RetrievalStrategy(name="conditional_rewrite", search="vector", rewrite_mode="conditional"),
    RetrievalStrategy(name="conditional_hybrid", search="hybrid", rewrite_mode="conditional"),
)
STRATEGY_NAMES = tuple(strategy.name for strategy in STRATEGIES)


def relevant_keys(case: EvalCase) -> list[str]:
    raw = case.payload.get("relevant_chunk_ids")
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) for item in raw):
        raise ConfigError(
            f"case {case.id!r} needs a non-empty relevant_chunk_ids list of chunk keys"
        )
    keys = list(dict.fromkeys(raw))
    for key in keys:
        parse_chunk_key(key)
    return keys


def prepare_case(case: EvalCase) -> list[str]:
    if not case.query.strip():
        raise ConfigError(f"case {case.id!r} has an empty query")
    if case.split not in {"dev", "test"}:
        raise ConfigError(f"case {case.id!r} needs a dev or test split")
    needs_rewrite = case.payload.get("needs_rewrite")
    if needs_rewrite is not None and not isinstance(needs_rewrite, bool):
        raise ConfigError(f"case {case.id!r} needs_rewrite must be a boolean")
    history = case.payload.get("history")
    if history is not None:
        if not isinstance(history, list):
            raise ConfigError(f"case {case.id!r} history must be a list of turns")
        try:
            [Turn.model_validate(turn) for turn in history]
        except Exception as exc:
            raise ConfigError(f"case {case.id!r} has an invalid history turn: {exc}") from exc
    return relevant_keys(case)


def case_history(case: EvalCase) -> list[Turn]:
    raw = case.payload.get("history") or []
    return [Turn.model_validate(turn) for turn in raw]


def needs_rewrite(case: EvalCase) -> bool:
    return bool(case.payload.get("needs_rewrite", False)) or "needs_rewrite" in case.tags


def run_context(case: EvalCase, settings: Settings, tenant_id: str) -> RunContext:
    return RunContext(
        run_id=f"rag-{case.id}",
        tenant=tenant_context(tenant_id, settings.tenants),
        run_token="unused",
        trace_parent="00-" + "c" * 32 + "-" + "d" * 16 + "-01",
    )


class RagSuite:
    name = "rag"

    def __init__(
        self,
        *,
        settings: Settings,
        dataset_root: Path = DEFAULT_DATASET_ROOT,
        tenant_id: str = "default",
        judge: Judge | None = None,
    ) -> None:
        if tenant_id not in settings.tenants.tenants:
            raise ConfigError(f"the rag suite needs a tenant named {tenant_id!r}")
        self._settings = settings
        self._dataset_root = Path(dataset_root)
        self._tenant_id = tenant_id
        self._judge = judge
        self._pool: asyncpg.Pool | None = None
        self._embedder: Embedder | None = None
        self._retrievers: dict[str, PgVectorRetriever] = {}
        self._resolved: dict[str, str] | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_context(cls, context: SuiteContext) -> RagSuite:
        if context.settings is None:
            raise ConfigError("the rag suite needs the loaded settings")
        return cls(
            settings=context.settings,
            dataset_root=context.dataset_root,
            judge=context.judge,
        )

    def load(self, version: str) -> list[EvalCase]:
        cases = load_cases(self.name, version, self._dataset_root)
        for case in cases:
            prepare_case(case)
        return cases

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._retrievers.clear()
            self._resolved = None

    async def _ensure_index(self) -> None:
        if self._pool is not None:
            return
        async with self._lock:
            if self._pool is not None:
                return
            dsn = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
            if not dsn:
                raise ConfigError("the rag suite needs SWITCHBOARD_DATABASE_URL")
            embedder_name = self._settings.platform.embedder
            try:
                embedder_config = self._settings.providers.embedders[embedder_name]
            except KeyError as exc:
                raise ConfigError(f"unknown embedder {embedder_name!r}") from exc
            self._embedder = create_embedder(embedder_name, embedder_config)
            pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
            self._pool = pool
            self._retrievers = {
                "vector": PgVectorRetriever(pool=pool, embedder=self._embedder, search="vector"),
                "hybrid": PgVectorRetriever(pool=pool, embedder=self._embedder, search="hybrid"),
            }
            self._resolved = await self._load_chunk_keys(pool)

    async def _load_chunk_keys(self, pool: asyncpg.Pool) -> dict[str, str]:
        rows = await pool.fetch(
            """
            SELECT d.source, c.ordinal, c.id::text AS chunk_id
            FROM public.chunks c
            JOIN public.documents d ON d.id = c.document_id
            WHERE c.tenant_id = $1
            """,
            self._tenant_id,
        )
        resolved = {chunk_key(row["source"], int(row["ordinal"])): row["chunk_id"] for row in rows}
        if not resolved:
            raise ConfigError(
                f"tenant {self._tenant_id!r} has no chunks; run `switchboard ingest` first"
            )
        return resolved

    async def _resolve(self, keys: list[str]) -> dict[str, str]:
        await self._ensure_index()
        resolved = self._resolved or {}
        missing = sorted(set(keys) - set(resolved))
        if missing:
            raise ConfigError(
                "the corpus does not contain these chunk keys (re-ingest or fix the "
                "dataset): " + ", ".join(missing)
            )
        return {key: resolved[key] for key in keys}

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        keys = prepare_case(case)
        await self._resolve(keys)
        ctx = run_context(case, self._settings, self._tenant_id)
        history = case_history(case)
        usage = Usage(input_tokens=0, output_tokens=0)
        strategies: dict[str, Any] = {}
        total_latency = 0

        for strategy in STRATEGIES:
            retriever = self._retrievers[strategy.search]
            started = time.perf_counter()
            try:
                run = await retrieve_with_strategy(
                    retriever,
                    deps.provider,
                    query=case.query,
                    ctx=ctx,
                    strategy=strategy,
                    k=TOP_K,
                    history=history,
                )
            except FixtureMissError:
                raise
            except Exception as exc:
                latency = int((time.perf_counter() - started) * 1000)
                total_latency += latency
                strategies[strategy.name] = {
                    "retrieved": [],
                    "recall_at_5": 0.0,
                    "mrr": 0.0,
                    "rewrite_rounds": 0,
                    "retrieval_rounds": 1,
                    "latency_ms": latency,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                continue
            latency = int((time.perf_counter() - started) * 1000)
            total_latency += latency
            usage = usage.plus(run.usage)
            retrieved = [chunk_key(chunk.source, chunk.ordinal) for chunk in run.chunks]
            payload: dict[str, Any] = {
                "retrieved": retrieved,
                "recall_at_5": recall_at_k(retrieved, keys, TOP_K),
                "mrr": reciprocal_rank(retrieved, keys),
                "rewrite_rounds": run.rewrite_rounds,
                "retrieval_rounds": run.retrieval_rounds,
                "rewritten_queries": run.rewritten_queries,
                "latency_ms": latency,
                "error": run.rewrite_error,
            }
            if self._judge is not None and strategy.name == JUDGE_STRATEGY:
                # Kept out of the report unless judge mode is on, so a default
                # rag report is byte-identical to the pre-T3.04 one.
                payload["chunks"] = [chunk.model_dump(mode="json") for chunk in run.chunks]
            strategies[strategy.name] = payload

        details: dict[str, Any] = {
            "strategies": strategies,
            "relevant": keys,
            "needs_rewrite": needs_rewrite(case),
        }
        if self._judge is not None:
            judge_details, judge_usage = await self._judge_answer(case, deps, strategies)
            details["judge"] = judge_details
            usage = usage.plus(judge_usage)

        return EvalOutcome(
            case_id=case.id,
            details=details,
            usage=usage,
            # Retrieval only: the answer and judge latencies are recorded per
            # case in the judge object, so this stays comparable to the plain
            # rag report (design.md 20.2).
            latency_ms=total_latency,
        )

    async def _judge_answer(
        self,
        case: EvalCase,
        deps: EvalDeps,
        strategies: dict[str, Any],
    ) -> tuple[dict[str, Any], Usage]:
        """Answer from the production retrieval result, then judge it.

        A judge or generation error is recorded on the case and never fails the
        suite: the retrieval metrics are still the measurement that matters.
        """
        if self._judge is None:  # pragma: no cover - only called in judge mode
            return {}, Usage(input_tokens=0, output_tokens=0)
        payload = strategies.get(JUDGE_STRATEGY) or {}
        raw_chunks = payload.get("chunks") or []
        chunks = [RetrievedChunk.model_validate(item) for item in raw_chunks]
        if not chunks:
            return (
                {
                    "verdict": None,
                    "score": None,
                    "reason": "",
                    "answer": None,
                    "generation_latency_ms": 0,
                    "judge_latency_ms": 0,
                    "error": payload.get("error") or "the production strategy returned no chunks",
                },
                Usage(input_tokens=0, output_tokens=0),
            )
        try:
            generated = await answer_from_chunks(deps.provider, query=case.query, chunks=chunks)
        except Exception as exc:
            return (
                {
                    "verdict": None,
                    "score": None,
                    "reason": "",
                    "answer": None,
                    "generation_latency_ms": 0,
                    "judge_latency_ms": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                Usage(input_tokens=0, output_tokens=0),
            )
        evidence = [
            JudgeEvidence(kind="document", ref=f"d{index}", text=chunk.text)
            for index, chunk in enumerate(chunks, start=1)
        ]
        citations = [
            Citation(
                kind="document",
                ref=f"d{index}",
                source_id=chunk.chunk_id,
                snippet=chunk.text[:240],
            )
            for index, chunk in enumerate(chunks, start=1)
            if f"[d{index}]" in generated.answer
        ]
        details: dict[str, Any] = {
            "answer": generated.answer,
            "generation_latency_ms": generated.latency_ms,
            "judge_latency_ms": 0,
            "error": None,
        }
        try:
            verdict = await self._judge.judge(
                JudgeRequest(
                    query=case.query,
                    answer=generated.answer,
                    evidence=evidence,
                    citations=citations,
                )
            )
        except Exception as exc:
            details.update(
                {
                    "verdict": None,
                    "score": None,
                    "reason": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return details, generated.usage
        details.update(
            {
                "verdict": verdict.verdict,
                "score": verdict.score,
                "reason": verdict.reason,
                "judge_latency_ms": verdict.latency_ms,
            }
        )
        usage = generated.usage
        if verdict.usage is not None:
            usage = usage.plus(verdict.usage)
        return details, usage

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        return score_rag(case, outcome)


__all__ = [
    "STRATEGIES",
    "STRATEGY_NAMES",
    "TOP_K",
    "RagSuite",
    "case_history",
    "needs_rewrite",
    "prepare_case",
    "relevant_keys",
    "run_context",
]
