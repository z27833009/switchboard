"""Cache suite: paraphrase hits and near misses over a frozen pair dataset.

Each case is a pair of questions and one label: ``should_hit`` when the second
question asks for the same answer as the first, ``false`` when similar wording
needs a different answer (a parameter change such as March versus April, a
different product category, or a policy question next to a data question).

The suite embeds both questions with the configured embedder once and reports
the cosine similarity. The threshold sweep lives in the scorer and the report,
so one run measures every threshold in ``THRESHOLDS``. It makes no model call
and needs no database.

With ``--provider mock`` it uses the deterministic mock embedder. That run
checks the suite mechanics in CI only; the markdown report says so, because a
hash-based fake has no semantic similarity and its numbers are not a quality
measurement. The measured report uses bge-m3 on LM Studio.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from switchboard.adapters.cache.pg_semantic import cosine_similarity
from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.embedders.mock import MockEmbedder
from switchboard.adapters.retrievers.ingest import embed_in_batches
from switchboard.core.config import Settings
from switchboard.core.errors import ConfigError
from switchboard.core.models import EvalCase, EvalOutcome, Score, Usage
from switchboard.core.ports import EvalDeps
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.scorers.cache import CHOSEN_THRESHOLD
from switchboard.eval.suite import SuiteContext

PAIR_TYPES = ("paraphrase", "near_miss", "cross_type")


def prepare_case(case: EvalCase) -> tuple[str, str, bool]:
    payload = case.payload
    query_a = payload.get("query_a")
    query_b = payload.get("query_b")
    should_hit = payload.get("should_hit")
    pair_type = payload.get("pair_type")
    if not isinstance(query_a, str) or not query_a.strip():
        raise ConfigError(f"case {case.id!r} needs a non-empty query_a")
    if not isinstance(query_b, str) or not query_b.strip():
        raise ConfigError(f"case {case.id!r} needs a non-empty query_b")
    if not isinstance(should_hit, bool):
        raise ConfigError(f"case {case.id!r} needs a boolean should_hit")
    if pair_type not in PAIR_TYPES:
        raise ConfigError(f"case {case.id!r} needs pair_type one of {', '.join(PAIR_TYPES)}")
    if case.split not in {"dev", "test"}:
        raise ConfigError(f"case {case.id!r} needs a dev or test split")
    return query_a, query_b, should_hit


class CacheSuite:
    name = "cache"

    def __init__(
        self,
        *,
        settings: Settings,
        provider: str = "mock",
        dataset_root: Path = DEFAULT_DATASET_ROOT,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._dataset_root = Path(dataset_root)
        self._loaded: list[EvalCase] = []
        self._vectors: dict[str, list[float]] = {}
        self._embedding_source = ""
        self._lock = asyncio.Lock()

    @classmethod
    def from_context(cls, context: SuiteContext) -> CacheSuite:
        if context.settings is None:
            raise ConfigError("the cache suite needs the loaded settings")
        return cls(
            settings=context.settings,
            provider=context.provider,
            dataset_root=context.dataset_root,
        )

    def load(self, version: str) -> list[EvalCase]:
        cases = load_cases(self.name, version, self._dataset_root)
        for case in cases:
            prepare_case(case)
        self._loaded = list(cases)
        return cases

    async def _ensure_vectors(self) -> None:
        if self._vectors:
            return
        async with self._lock:
            if self._vectors:
                return
            embedder = self._build_embedder()
            texts = sorted({text for case in self._loaded for text in prepare_case(case)[:2]})
            vectors = await embed_in_batches(embedder, texts)
            if len(vectors) != len(texts):
                raise ConfigError(
                    f"embedder returned {len(vectors)} vectors for {len(texts)} questions"
                )
            self._vectors = dict(zip(texts, vectors, strict=True))

    def _build_embedder(self) -> Any:
        name = "mock" if self._provider == "mock" else self._settings.platform.embedder
        config = self._settings.providers.embedders.get(name)
        if name == "mock":
            dimension = config.dimension if config is not None else 1024
            self._embedding_source = f"mock (deterministic sha256, dimension {dimension})"
            return MockEmbedder(dimension=dimension)
        if config is None:
            raise ConfigError(f"unknown embedder {name!r} for the cache suite")
        model = config.model or "no model"
        self._embedding_source = f"{name} ({config.adapter}, {model})"
        return create_embedder(name, config)

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        del deps  # Only the embedder is needed; no model call is made.
        query_a, query_b, should_hit = prepare_case(case)
        await self._ensure_vectors()
        vector_a = self._vectors[query_a]
        vector_b = self._vectors[query_b]
        similarity = cosine_similarity(vector_a, vector_b)
        return EvalOutcome(
            case_id=case.id,
            details={
                "query_a": query_a,
                "query_b": query_b,
                "should_hit": should_hit,
                "pair_type": case.payload.get("pair_type"),
                "similarity": round(similarity, 6),
                "embedding_source": self._embedding_source,
            },
            usage=Usage(input_tokens=0, output_tokens=0),
            latency_ms=0,
        )

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        _query_a, _query_b, should_hit = prepare_case(case)
        similarity = float(outcome.details.get("similarity", 0.0))
        predicted = similarity >= CHOSEN_THRESHOLD
        correct = predicted == should_hit
        return Score(
            case_id=case.id,
            metrics={
                "similarity": round(similarity, 6),
                "hit_at_chosen_threshold": 1.0 if predicted else 0.0,
                "correct_at_chosen_threshold": 1.0 if correct else 0.0,
            },
            passed=correct,
        )


__all__ = ["PAIR_TYPES", "CacheSuite", "prepare_case"]
