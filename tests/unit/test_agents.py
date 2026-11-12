"""Agents, citations, bounded tool views and merge behaviour."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from switchboard.agents.data import DataAgent
from switchboard.agents.direct import DirectAgent
from switchboard.agents.docs import DocsAgent
from switchboard.agents.merge import MergeAgent
from switchboard.agents.results import ResultPageToolSource, ResultStore
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ToolPermissionError, ToolValidationError
from switchboard.core.models import (
    AgentResult,
    AgentTask,
    Budget,
    Citation,
    CompletionResponse,
    EvidenceRef,
    ProviderCapabilities,
    RetrievedChunk,
    RunContext,
    TenantContext,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)
from switchboard.core.ports import AgentDeps
from switchboard.registry import AGENTS, Registry

RUN_CONTEXT = RunContext(
    run_id="run-1",
    tenant=TenantContext(
        tenant_id="default",
        allowed_agents={"direct", "docs", "data"},
        allowed_tools={"orders_late@1", "result_page@1"},
        provider="lmstudio",
    ),
    run_token="unused",
    trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
)


class FakeProvider:
    name = "fake"

    def __init__(self, responses: list[CompletionResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: Any) -> CompletionResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class RecordingPolicy:
    def __init__(self) -> None:
        self.calls = 0
        self._policy = ClearOldestToolResults()

    def fit(self, messages: list[Any], budget: Any) -> Any:
        self.calls += 1
        return self._policy.fit(messages, budget)


class FakeRetriever:
    name = "fake"

    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls = 0

    async def retrieve(self, query: str, ctx: RunContext, k: int = 5) -> list[RetrievedChunk]:
        self.calls += 1
        return self.chunks[:k]


class FakeToolSource:
    name = "fake"

    def __init__(self, specs: list[ToolSpec], results: list[Any]) -> None:
        self.specs = specs
        self.results = list(results)
        self.calls: list[str] = []

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        return self.specs

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        self.calls.append(name)
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return cast(ToolResult, item)


def completion(
    text: str | None = None,
    *,
    tool_calls: list[ToolCall] | None = None,
    usage: Usage | None = None,
) -> CompletionResponse:
    return CompletionResponse(
        text=text,
        tool_calls=tool_calls or [],
        usage=usage or Usage(input_tokens=10, output_tokens=5),
        model="qwen3-8b",
        provider="fake",
        latency_ms=1,
    )


def deps(
    provider: FakeProvider,
    *,
    retriever: FakeRetriever | None = None,
    tool_sources: list[FakeToolSource] | None = None,
    policy: RecordingPolicy | None = None,
) -> AgentDeps:
    return AgentDeps(
        provider=provider,
        context_policy=policy or RecordingPolicy(),
        ctx=RUN_CONTEXT,
        retriever=retriever,
        tool_sources=[cast(Any, source) for source in tool_sources or []],
        model_view_rows=20,
    )


def task(query: str = "hello") -> AgentTask:
    return AgentTask(query=query, budget=Budget(max_tool_steps=4, max_tokens=1024))


def late_spec() -> ToolSpec:
    return ToolSpec(
        name="orders_late",
        version="1",
        description="Late orders.",
        input_schema={"type": "object"},
    )


def late_result(rows: int = 500) -> ToolResult:
    return ToolResult(
        tool="orders_late@1",
        version="1",
        total_rows=rows,
        summary={"late_orders": rows},
        rows=[{"order_id": f"o{index}"} for index in range(rows)],
        result_id="r1",
        audit_id="audit-1",
    )


async def test_direct_agent_answers_without_evidence() -> None:
    provider = FakeProvider([completion("Hello!")])
    result = await DirectAgent().run(task(), deps(provider))
    assert result.answer == "Hello!"
    assert result.citations == []
    assert result.partial is False


async def test_docs_agent_returns_citations_and_evidence() -> None:
    chunk = RetrievedChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        source="returns-policy.md",
        title="Returns policy",
        text="Electronics can be returned within 15 days.",
        score=0.9,
        ordinal=0,
    )
    provider = FakeProvider([completion("Electronics have a 15 day window [d1].")])
    policy = RecordingPolicy()
    result = await DocsAgent().run(
        task("What is the electronics return window?"),
        deps(provider, retriever=FakeRetriever([chunk]), policy=policy),
    )
    assert result.citations[0].source_id == "chunk-1"
    assert result.citations[0].snippet.startswith("Electronics")
    assert result.evidence == [EvidenceRef(kind="document", ref="d1", source_id="chunk-1")]
    assert policy.calls == 1


async def test_docs_agent_is_partial_when_nothing_is_retrieved() -> None:
    provider = FakeProvider([])
    result = await DocsAgent().run(task(), deps(provider, retriever=FakeRetriever([])))
    assert result.partial is True
    assert provider.requests == []


async def test_data_agent_shows_at_most_the_view_limit_then_cites_the_tool() -> None:
    call = ToolCall(name="orders_late@1", arguments={}, call_id="call-1")
    provider = FakeProvider(
        [completion(tool_calls=[call]), completion("There were 500 late orders [t1].")]
    )
    policy = RecordingPolicy()
    source = FakeToolSource([late_spec()], [late_result(500)])
    result = await DataAgent().run(
        task("How many orders were late?"),
        deps(provider, tool_sources=[source], policy=policy),
    )

    assert result.partial is False
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].audit_id == "audit-1"
    assert result.citations[0].kind == "tool"
    assert result.citations[0].audit_id == "audit-1"
    assert policy.calls == 2

    tool_message = next(
        message for message in provider.requests[1].messages if message.role == "tool"
    )
    body = tool_message.content.split(">\n", 1)[1].rsplit("\n</tool_result>", 1)[0]
    view = json.loads(body)
    assert view["total_rows"] == 500
    assert view["truncated"] is True
    assert len(view["rows_preview"]) == 20


async def test_data_agent_can_page_a_stored_result() -> None:
    first = ToolCall(name="orders_late@1", arguments={}, call_id="call-1")
    page = ToolCall(
        name="result_page@1",
        arguments={"result_id": "r1", "offset": 20, "limit": 20},
        call_id="call-2",
    )
    provider = FakeProvider(
        [
            completion(tool_calls=[first]),
            completion(tool_calls=[page]),
            completion("The second page starts at order 21 [t2]."),
        ]
    )
    source = FakeToolSource([late_spec()], [late_result(500)])
    result = await DataAgent().run(task(), deps(provider, tool_sources=[source]))
    assert len(result.tool_calls) == 2
    assert result.tool_calls[1].tool == "result_page@1"
    assert result.citations[0].ref == "t2"


async def test_data_agent_marks_a_denied_tool_as_partial() -> None:
    call = ToolCall(name="orders_late@1", arguments={}, call_id="call-1")
    provider = FakeProvider(
        [completion(tool_calls=[call]), completion("I cannot access that source.")]
    )
    source = FakeToolSource([late_spec()], [ToolPermissionError("not allowed for tenant")])
    result = await DataAgent().run(task(), deps(provider, tool_sources=[source]))
    assert result.tool_calls[0].status == "denied"
    assert result.partial is True
    assert result.citations == []


async def test_merge_keeps_both_sets_of_citations_and_names_missing_sources() -> None:
    data = AgentResult(
        answer="500 late orders [t1].",
        citations=[Citation(kind="tool", ref="t1", source_id="r1", snippet="500")],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    docs = AgentResult(
        answer="Compensation applies after three days [d1].",
        citations=[Citation(kind="document", ref="d1", source_id="c1", snippet="three days")],
        usage=Usage(input_tokens=20, output_tokens=10),
    )
    merged = MergeAgent().merge({"data": data, "docs": docs})
    assert {citation.ref for citation in merged.citations} == {"t1", "d1"}
    assert merged.usage.input_tokens == 30
    assert merged.partial is False

    partial = MergeAgent().merge({"data": data}, missing=["docs"])
    assert partial.partial is True
    assert "Missing source: docs" in partial.answer


async def test_prefix_is_stable_across_queries_and_tools_are_sorted() -> None:
    call = ToolCall(name="orders_late@1", arguments={}, call_id="call-1")
    source = FakeToolSource([late_spec()], [late_result(1), late_result(1)])
    provider = FakeProvider(
        [
            completion(tool_calls=[call]),
            completion("done"),
            completion(tool_calls=[call]),
            completion("done"),
        ]
    )
    agent = DataAgent()
    application = deps(provider, tool_sources=[source])
    await agent.run(task("first query"), application)
    await agent.run(task("second query"), application)

    first_prefix = [message.content for message in provider.requests[0].messages[:2]]
    second_prefix = [message.content for message in provider.requests[2].messages[:2]]
    assert first_prefix == second_prefix
    wire_names = [tool.wire_name for tool in provider.requests[0].tools]
    assert wire_names == sorted(wire_names)


async def test_result_page_tool_source_rejects_unknown_results() -> None:
    store = ResultStore()
    source = ResultPageToolSource(store)
    with pytest.raises(ToolValidationError, match="unknown result_id"):
        await source.call(
            "result_page@1",
            {"result_id": "missing", "offset": 0, "limit": 20},
            RUN_CONTEXT,
        )


def test_registry_resolves_the_agent_entry_points() -> None:
    registry = Registry()
    assert set(registry.names(AGENTS)) == {"direct", "docs", "data"}
    assert registry.load(AGENTS, "direct") is DirectAgent
