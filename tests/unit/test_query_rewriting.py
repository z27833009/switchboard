"""Query rewriting decision (design.md section 9.3 and AC-25a)."""

from __future__ import annotations

import pytest

from switchboard.agents.retrieval import (
    RetrievalStrategy,
    clean_rewrite,
    retrieve_with_strategy,
    rewrite_prompt,
    strategy_from_settings,
)
from switchboard.core.errors import FixtureMissError
from switchboard.core.models import (
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
    RetrievalSettings,
    RetrievedChunk,
    RunContext,
    TenantContext,
    Turn,
    Usage,
)


def context(min_score: float = 0.5, rewrite: bool = True) -> RunContext:
    return RunContext(
        run_id="run-rewrite",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents={"docs"},
            allowed_tools=set(),
            provider="fake",
            retrieval=RetrievalSettings(min_score=min_score, rewrite=rewrite, search="vector"),
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def chunks(*scores: float) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=f"chunk-{index}",
            document_id="doc",
            source="returns-policy.md",
            title="Returns",
            text="text",
            score=score,
            ordinal=index,
        )
        for index, score in enumerate(scores)
    ]


class ScriptedRetriever:
    name = "scripted"

    def __init__(self, results: dict[str, list[RetrievedChunk]]) -> None:
        self._results = results
        self.calls: list[tuple[str, int]] = []

    async def retrieve(
        self, query: str, ctx: RunContext, k: int = 5, *, round: int = 1
    ) -> list[RetrievedChunk]:
        self.calls.append((query, round))
        return self._results.get(query, chunks(0.1))


class ScriptedProvider:
    name = "fake"

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[CompletionRequest] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=False,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return CompletionResponse(
            text=item,
            usage=Usage(input_tokens=7, output_tokens=3),
            model="fake",
            provider="fake",
            latency_ms=1,
        )


CONDITIONAL = RetrievalStrategy(name="conditional", search="vector", rewrite_mode="conditional")
ALWAYS = RetrievalStrategy(name="always", search="vector", rewrite_mode="always")
NEVER = RetrievalStrategy(name="as_written", search="vector", rewrite_mode="never")


async def test_conditional_rewrites_once_when_the_top_score_is_low() -> None:
    retriever = ScriptedRetriever(
        {
            "vague question": chunks(0.2),
            "clearer question": chunks(0.8),
        }
    )
    provider = ScriptedProvider(["clearer question"])

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="vague question",
        ctx=context(min_score=0.5),
        strategy=CONDITIONAL,
    )

    assert run.rewrite_rounds == 1
    assert run.retrieval_rounds == 2
    assert run.queries == ["vague question", "clearer question"]
    assert run.rewritten_queries == ["clearer question"]
    assert retriever.calls == [("vague question", 1), ("clearer question", 2)]
    assert run.chunks[0].score == 0.8
    assert run.usage.input_tokens == 7
    assert run.rewrite_error is None


async def test_conditional_does_not_rewrite_when_the_top_score_is_high() -> None:
    retriever = ScriptedRetriever({"good question": chunks(0.9)})
    provider = ScriptedProvider([])

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="good question",
        ctx=context(min_score=0.5),
        strategy=CONDITIONAL,
    )

    assert run.rewrite_rounds == 0
    assert run.retrieval_rounds == 1
    assert provider.calls == []
    assert run.queries == ["good question"]


async def test_always_rewrites_even_when_the_score_is_high() -> None:
    retriever = ScriptedRetriever({"original": chunks(0.9), "rewritten": chunks(0.8)})
    provider = ScriptedProvider(["rewritten"])

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="original",
        ctx=context(min_score=0.5),
        strategy=ALWAYS,
    )

    assert run.rewrite_rounds == 1
    assert run.retrieval_rounds == 1
    assert run.queries == ["rewritten"]
    assert run.rewritten_queries == ["rewritten"]
    assert len(provider.calls) == 1


async def test_as_written_never_calls_the_provider() -> None:
    retriever = ScriptedRetriever({"weak": chunks(0.01)})
    provider = ScriptedProvider([])

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="weak",
        ctx=context(min_score=0.5),
        strategy=NEVER,
    )

    assert run.rewrite_rounds == 0
    assert run.retrieval_rounds == 1
    assert provider.calls == []


async def test_history_is_contextualized_before_the_first_round() -> None:
    retriever = ScriptedRetriever(
        {
            "Can I still return it?": chunks(0.2),
            "laptop return window": chunks(0.9),
        }
    )
    provider = ScriptedProvider(["laptop return window"])
    history = [
        Turn(role="user", content="I bought a laptop five days ago."),
        Turn(role="assistant", content="Electronics have a shorter window."),
    ]

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="Can I still return it?",
        ctx=context(min_score=0.5),
        strategy=CONDITIONAL,
        history=history,
    )

    assert run.rewrite_rounds == 1
    assert run.retrieval_rounds == 1
    assert run.queries == ["laptop return window"]
    assert run.rewritten_queries == ["laptop return window"]
    prompt = provider.calls[0].messages[-1].content
    assert "Conversation so far:" in prompt
    assert "I bought a laptop five days ago." in prompt


async def test_a_second_rewrite_is_allowed_after_context_and_a_low_score() -> None:
    retriever = ScriptedRetriever(
        {
            "follow-up": chunks(0.9),
            "contextualized": chunks(0.2),
            "second rewrite": chunks(0.8),
        }
    )
    provider = ScriptedProvider(["contextualized", "second rewrite"])
    history = [Turn(role="user", content="We talked about returns.")]

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="follow-up",
        ctx=context(min_score=0.5),
        strategy=CONDITIONAL,
        history=history,
    )

    assert run.rewrite_rounds == 2
    assert run.retrieval_rounds == 2
    assert run.queries == ["contextualized", "second rewrite"]
    assert run.rewritten_queries == ["contextualized", "second rewrite"]
    assert len(provider.calls) == 2


async def test_a_failed_rewrite_keeps_the_first_retrieval() -> None:
    retriever = ScriptedRetriever({"weak": chunks(0.1)})
    provider = ScriptedProvider([RuntimeError("model down")])

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="weak",
        ctx=context(min_score=0.5),
        strategy=CONDITIONAL,
    )

    assert run.rewrite_rounds == 0
    assert run.retrieval_rounds == 1
    assert run.rewrite_error is not None and "model down" in run.rewrite_error
    assert run.chunks[0].score == 0.1


async def test_an_empty_rewrite_is_a_failure() -> None:
    retriever = ScriptedRetriever({"weak": chunks(0.1)})
    provider = ScriptedProvider(["   \n  "])

    run = await retrieve_with_strategy(
        retriever,
        provider,
        query="weak",
        ctx=context(min_score=0.5),
        strategy=CONDITIONAL,
    )

    assert run.rewrite_rounds == 0
    assert run.rewrite_error is not None
    assert "no usable query" in run.rewrite_error


async def test_a_missing_fixture_stops_the_run_instead_of_degrading() -> None:
    retriever = ScriptedRetriever({"weak": chunks(0.1)})
    provider = ScriptedProvider([FixtureMissError("no fixture")])

    with pytest.raises(FixtureMissError):
        await retrieve_with_strategy(
            retriever,
            provider,
            query="weak",
            ctx=context(min_score=0.5),
            strategy=CONDITIONAL,
        )


def test_clean_rewrite_takes_the_first_line_and_strips_quotes() -> None:
    assert clean_rewrite('"return window for electronics"\nmore text') == (
        "return window for electronics"
    )
    assert clean_rewrite(None) == ""
    assert clean_rewrite("  \n ") == ""


def test_rewrite_prompt_marks_a_retry() -> None:
    plain = rewrite_prompt("question", [], retry=False)
    retry = rewrite_prompt("question", [], retry=True)
    assert "Question to rewrite: question" in plain
    assert "first search returned no sufficiently relevant passage" in retry


def test_strategy_from_settings_maps_the_tenant_flags() -> None:
    assert strategy_from_settings("docs", RetrievalSettings()).rewrite_mode == "never"
    enabled = RetrievalSettings(rewrite=True, search="hybrid")
    strategy = strategy_from_settings("docs", enabled)
    assert strategy.rewrite_mode == "conditional"
    assert strategy.search == "hybrid"
    assert strategy_from_settings("docs", enabled).name == "docs"
