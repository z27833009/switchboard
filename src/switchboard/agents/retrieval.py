"""Conditional query rewriting (design.md section 9.3).

Rewriting is a model call, so it is triggered by conditions. The docs agent
calls this module with the tenant's retrieval settings; the rag eval calls it
with each of the four strategies so one dataset can compare them.

At most two retrieval rounds are made. Context is handled before the first
round: with history the question is rewritten into a standalone question. After
a round, a conditional strategy rewrites once when the best cosine score in the
top k is below ``retrieval.min_score``; the always strategy rewrites first and
retrieves once.

The strategy reports how many rewrite rounds and retrieval rounds it used, so
an eval can tell rewriting cost from retrieval benefit. A rewrite that fails
(provider error or an empty answer) keeps the previous query and is recorded on
the run instead of losing the retrieval result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from switchboard.agents.prompts import REWRITE_SYSTEM
from switchboard.core.errors import FixtureMissError
from switchboard.core.models import (
    CompletionRequest,
    Message,
    RetrievalSettings,
    RetrievedChunk,
    RunContext,
    Turn,
    Usage,
)
from switchboard.core.ports import ModelProvider, Retriever
from switchboard.core.pricing import add_cost

RewriteMode = Literal["never", "always", "conditional"]
SearchMode = Literal["vector", "hybrid"]

MAX_RETRIEVAL_ROUNDS = 2
MAX_REWRITE_ROUNDS = 2
REWRITE_MAX_TOKENS = 160
MAX_REWRITE_CHARS = 500


@dataclass(frozen=True)
class RetrievalStrategy:
    """One way to combine search mode and rewriting, named for reports."""

    name: str
    search: SearchMode
    rewrite_mode: RewriteMode


@dataclass(frozen=True)
class RetrievalRun:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    strategy: str = ""
    queries: list[str] = field(default_factory=list)
    rewritten_queries: list[str] = field(default_factory=list)
    rewrite_rounds: int = 0
    retrieval_rounds: int = 0
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))
    cost_usd: float | None = None
    rewrite_error: str | None = None

    @property
    def top_score(self) -> float:
        return max((chunk.score for chunk in self.chunks), default=0.0)


def strategy_from_settings(name: str, settings: RetrievalSettings) -> RetrievalStrategy:
    """The production strategy: rewriting only when the tenant enabled it."""
    mode: RewriteMode = settings.rewrite_mode if settings.rewrite else "never"
    return RetrievalStrategy(name=name, search=settings.search, rewrite_mode=mode)


def rewrite_prompt(query: str, history: list[Turn], *, retry: bool) -> str:
    lines: list[str] = []
    if history:
        lines.append("Conversation so far:")
        lines.extend(f"{turn.role}: {turn.content}" for turn in history)
        lines.append("")
    lines.append(f"Question to rewrite: {query}")
    if retry:
        lines.append(
            "The first search returned no sufficiently relevant passage. Use different "
            "words from the question and from the corpus vocabulary."
        )
    return "\n".join(lines)


def clean_rewrite(text: str | None) -> str:
    """Keep one line, strip quotes and backticks; an empty answer is a failure."""
    for line in (text or "").splitlines():
        candidate = line.strip().strip("\"'`").strip()
        if candidate:
            return candidate[:MAX_REWRITE_CHARS]
    return ""


async def _rewrite(
    provider: ModelProvider,
    *,
    query: str,
    history: list[Turn],
    ctx: RunContext,
    retry: bool,
) -> tuple[str, Usage, float | None]:
    request = CompletionRequest(
        messages=[
            Message(role="system", content=REWRITE_SYSTEM),
            Message(role="user", content=rewrite_prompt(query, history, retry=retry)),
        ],
        temperature=0.0,
        max_tokens=REWRITE_MAX_TOKENS,
        metadata={
            "purpose": "retriever.rewrite",
            "run_id": ctx.run_id,
            "tenant_id": ctx.tenant.tenant_id,
            "retrieval.retry": retry,
        },
    )
    response = await provider.complete(request)
    cleaned = clean_rewrite(response.text)
    if not cleaned:
        raise ValueError("the rewrite returned no usable query")
    return cleaned, response.usage, response.cost_usd


async def retrieve_with_strategy(
    retriever: Retriever,
    provider: ModelProvider,
    *,
    query: str,
    ctx: RunContext,
    strategy: RetrievalStrategy,
    k: int = 5,
    history: list[Turn] | None = None,
    embedding: list[float] | None = None,
) -> RetrievalRun:
    """Retrieve once or twice, rewriting only when the strategy says so.

    ``embedding`` is the semantic cache's vector for the original query when the
    lookup missed. It is used for the first round only when no rewrite replaced
    that query, so the vector always belongs to the text being searched
    (design.md section 10.1).
    """
    turns = list(history or [])
    usage = Usage(input_tokens=0, output_tokens=0)
    cost: float | None = None
    queries: list[str] = []
    rewritten_queries: list[str] = []
    rewrite_rounds = 0
    rewrite_error: str | None = None
    current = query.strip() or query

    contextualize = strategy.rewrite_mode in ("always", "conditional") and bool(turns)
    rewrite_first = strategy.rewrite_mode == "always" and not turns
    if contextualize or rewrite_first:
        try:
            current, spent, spent_cost = await _rewrite(
                provider,
                query=current,
                history=turns,
                ctx=ctx,
                retry=False,
            )
        except FixtureMissError:
            raise
        except Exception as exc:
            rewrite_error = f"{type(exc).__name__}: {exc}"
        else:
            usage = usage.plus(spent)
            cost = add_cost(cost, spent_cost)
            rewrite_rounds += 1
            rewritten_queries.append(current)

    queries.append(current)
    reused = embedding if embedding is not None and current == query else None
    chunks = await retriever.retrieve(
        current,
        ctx,
        k,
        round=1,
        **({} if reused is None else {"embedding": reused}),
    )
    retrieval_rounds = 1

    if (
        strategy.rewrite_mode == "conditional"
        and rewrite_error is None
        and rewrite_rounds < MAX_REWRITE_ROUNDS
        and max((chunk.score for chunk in chunks), default=0.0) < ctx.tenant.retrieval.min_score
    ):
        try:
            rewritten, spent, spent_cost = await _rewrite(
                provider,
                query=query,
                history=turns,
                ctx=ctx,
                retry=True,
            )
        except FixtureMissError:
            raise
        except Exception as exc:
            rewrite_error = f"{type(exc).__name__}: {exc}"
        else:
            usage = usage.plus(spent)
            cost = add_cost(cost, spent_cost)
            rewrite_rounds += 1
            rewritten_queries.append(rewritten)
            if rewritten != current:
                current = rewritten
                queries.append(current)
                chunks = await retriever.retrieve(current, ctx, k, round=2)
                retrieval_rounds = 2

    return RetrievalRun(
        chunks=chunks,
        strategy=strategy.name,
        queries=queries,
        rewritten_queries=rewritten_queries,
        rewrite_rounds=rewrite_rounds,
        retrieval_rounds=retrieval_rounds,
        usage=usage,
        cost_usd=cost,
        rewrite_error=rewrite_error,
    )


__all__ = [
    "MAX_RETRIEVAL_ROUNDS",
    "MAX_REWRITE_ROUNDS",
    "RetrievalRun",
    "RetrievalStrategy",
    "clean_rewrite",
    "retrieve_with_strategy",
    "rewrite_prompt",
    "strategy_from_settings",
]
