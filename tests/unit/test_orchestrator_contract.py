"""One orchestrator contract, run against both adapters (AC-16a, ADR 0002).

Every test in this module runs twice, once per adapter, and asserts only what a
caller can observe: the answer, its citations and evidence, the plan label, the
run status, the errors and the run span. A scripted provider, a stub tool source
and a stub retriever keep the suite free of LM Studio, PostgreSQL and the MCP
server. Nothing here inspects the graph or the loop internals, so a third
adapter can be added to ``KINDS`` without touching the assertions.

The plan is the one role-specific difference: the LangGraph path asks a
supervisor for it and the agent loop derives it from its tool calls (design.md
section 6.9). Both must end up with the same label for the same work.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from tests.fakes import MemoryRunStore, MemorySpanExporter

from switchboard.adapters.orchestrator.agent_loop import AgentLoopOrchestrator
from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.agent_loop import derive_plan
from switchboard.agents.data import DataAgent
from switchboard.agents.direct import DirectAgent
from switchboard.agents.docs import DocsAgent
from switchboard.core.config import Limits
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ToolPermissionError
from switchboard.core.models import (
    ChatRequest,
    CompletionResponse,
    ProviderCapabilities,
    RetrievedChunk,
    RunContext,
    TenantContext,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)
from switchboard.core.ports import Agent, ToolSource
from switchboard.registry import AGENTS, Registry

KINDS = ("langgraph", "agent_loop")

ORDERS_SUMMARY = ToolSpec(
    name="orders_summary",
    version="1",
    description="Count orders, optionally filtered by date.",
    input_schema={"type": "object", "properties": {}},
)

CALL_ORDERS = ToolCall(name="orders_summary@1", arguments={"status": "delivered"}, call_id="c1")
CALL_DOCS = ToolCall(
    name="search_docs@1", arguments={"query": "return window", "k": 5}, call_id="c2"
)


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class StubToolSource:
    """Lists one read tool and returns a fixed result, or denies every call."""

    name = "stub_olist"

    def __init__(self, *, denied: bool = False) -> None:
        self.denied = denied
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        return [ORDERS_SUMMARY]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        self.calls.append((name, dict(args)))
        if self.denied:
            raise ToolPermissionError(f"{name} is not allowed for this tenant")
        return ToolResult(
            tool=name,
            version="1",
            total_rows=6,
            summary={"count": 6},
            rows=[{"order_id": "abc", "count": 6}],
        )


class WriteToolSource:
    """A tool source that offers a write tool; the loop must never expose it."""

    name = "stub_writes"

    def __init__(self) -> None:
        self.calls = 0

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="delete_order",
                version="1",
                description="Delete an order.",
                input_schema={"type": "object", "properties": {}},
                risk="write",
            )
        ]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        self.calls += 1
        raise AssertionError("a write tool must never run")


class StubRetriever:
    name = "stub_docs"

    async def retrieve(
        self, query: str, ctx: RunContext, k: int = 5, *, round: int = 1
    ) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id="chunk-1",
                document_id="doc-1",
                source="returns.md",
                title="Returns",
                text="Returns are accepted within 15 calendar days of delivery.",
                score=0.81,
                ordinal=0,
            )
        ]


class ContractProvider:
    """One scripted model that serves both adapters.

    A plan request (``response_schema``) returns the configured plan. A data or
    loop step with no tool result in the messages yet returns the configured tool
    calls; every other call returns the final answer. The role comes from the
    request purpose, which is the same metadata the production agents set.
    """

    name = "fake"

    def __init__(
        self,
        *,
        plan: list[str] | None = None,
        tool_calls: list[ToolCall] | None = None,
        answer: str,
        delay: float = 0.0,
        repeat_tool_calls: bool = False,
    ) -> None:
        self.plan = list(plan or ["direct"])
        self.tool_calls = list(tool_calls or [])
        self.answer = answer
        self.delay = delay
        self.repeat_tool_calls = repeat_tool_calls
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
        usage = Usage(input_tokens=11, output_tokens=3)
        if request.response_schema is not None:
            return CompletionResponse(
                text=None,
                parsed={"agents": self.plan, "reason": "scripted plan"},
                usage=usage,
                model="fake",
                provider="fake",
                latency_ms=1,
            )
        if self.delay:
            await asyncio.sleep(self.delay)
        purpose = str(request.metadata.get("purpose", ""))
        has_tool_message = any(message.role == "tool" for message in request.messages)
        if (
            self.tool_calls
            and purpose in {"agent.data", "agent.agent_loop"}
            and (self.repeat_tool_calls or not has_tool_message)
        ):
            calls = (
                self.tool_calls
                if purpose == "agent.agent_loop"
                else [call for call in self.tool_calls if not call.name.startswith("search_docs")]
            )
            if calls:
                return CompletionResponse(
                    text=None,
                    tool_calls=[call.model_copy() for call in calls],
                    usage=usage,
                    model="fake",
                    provider="fake",
                    latency_ms=1,
                )
        return CompletionResponse(
            text=self.answer,
            usage=usage,
            model="fake",
            provider="fake",
            latency_ms=1,
        )


def context(allowed: set[str] | None = None) -> RunContext:
    return RunContext(
        run_id="run-contract",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents=allowed or {"direct", "docs", "data"},
            allowed_tools={"orders_summary@1"},
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def tracer_and_store() -> tuple[Any, MemoryRunStore]:
    store = MemoryRunStore()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(store)))
    return provider.get_tracer("switchboard.test.contract"), store


def make_orchestrator(
    kind: str,
    provider: ContractProvider,
    *,
    agents: list[Agent],
    tool_sources: list[ToolSource] | None = None,
    retriever: Any = None,
    limits: Limits | None = None,
    tracer: Any = None,
) -> Any:
    registry = Registry([FakeEntryPoint(agent.spec.name, AGENTS, agent) for agent in agents])
    common: dict[str, Any] = {
        "provider": provider,
        "registry": registry,
        "context_policy": ClearOldestToolResults(),
        "limits": limits or Limits(run_timeout_s=5.0, max_loop_steps=4),
        "tool_sources": list(tool_sources or []),
        "retriever": retriever,
        "tracer": tracer,
    }
    if kind == "agent_loop":
        return AgentLoopOrchestrator(**common)
    return LangGraphOrchestrator(**common)


def assert_run_span(
    store: MemoryRunStore,
    *,
    orchestrator: str,
    status: str,
    plan_label: str | None,
) -> None:
    rows = [row for row in store.spans if row["name"] == "run"]
    assert len(rows) == 1, f"expected one run span, got {[row['name'] for row in store.spans]}"
    attributes = rows[0]["attributes"]
    assert attributes["switchboard.orchestrator"] == orchestrator
    assert attributes["switchboard.status"] == status
    assert attributes.get("switchboard.plan") == plan_label


@pytest.mark.parametrize("kind", KINDS)
async def test_a_direct_answer(kind: str) -> None:
    provider = ContractProvider(
        plan=["direct"], answer="Hello. I can answer order and policy questions."
    )
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(kind, provider, agents=[DirectAgent()], tracer=tracer)

    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    assert result.status == "ok"
    assert result.plan is not None and result.plan.label == "direct"
    assert result.answer is not None
    assert result.answer.answer == "Hello. I can answer order and policy questions."
    assert result.answer.citations == []
    assert result.answer.evidence == []
    assert result.gate is not None and result.gate.passed
    assert result.attempts == 0
    assert result.usage.input_tokens > 0
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="direct")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_docs_answer(kind: str) -> None:
    # The LangGraph docs agent numbers document blocks d1; the loop sees the
    # same chunks as tool result t1.
    answer = (
        "Returns are accepted within 15 calendar days of delivery [d1]."
        if kind == "langgraph"
        else "Returns are accepted within 15 calendar days of delivery [t1]."
    )
    provider = ContractProvider(plan=["docs"], tool_calls=[CALL_DOCS], answer=answer)
    tools = StubToolSource()
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind,
        provider,
        agents=[DocsAgent()],
        tool_sources=[tools],
        retriever=StubRetriever(),
        tracer=tracer,
    )

    result = await orchestrator.run(
        ChatRequest(query="What is the return window?", tenant_id="default"), context()
    )

    assert result.status == "ok"
    assert result.plan is not None and result.plan.label == "docs"
    assert result.answer is not None
    assert result.answer.answer == answer
    assert len(result.answer.citations) == 1
    assert result.answer.evidence and not result.answer.evidence[0].empty
    assert result.gate is not None and result.gate.passed
    assert result.gate.failed_checks == []
    # A docs question never touches the data tools.
    assert tools.calls == []
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="docs")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_data_answer(kind: str) -> None:
    provider = ContractProvider(
        plan=["data"],
        tool_calls=[CALL_ORDERS],
        answer="There were 6 delivered orders [t1].",
    )
    tools = StubToolSource()
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind, provider, agents=[DataAgent()], tool_sources=[tools], tracer=tracer
    )

    result = await orchestrator.run(
        ChatRequest(query="How many delivered orders?", tenant_id="default"), context()
    )

    assert result.status == "ok"
    assert result.plan is not None and result.plan.label == "data"
    assert result.answer is not None
    assert result.answer.answer == "There were 6 delivered orders [t1]."
    assert len(result.answer.citations) == 1
    assert result.answer.evidence and not result.answer.evidence[0].empty
    assert result.gate is not None and result.gate.passed
    assert tools.calls == [("orders_summary@1", {"status": "delivered"})]
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="data")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_plan_derived_from_two_sources(kind: str) -> None:
    if kind == "langgraph":
        # Document blocks are d1 in the docs agent; the data tool result is t1.
        answer = "Returns take 15 calendar days [d1], and 6 orders were delivered [t1]."
        refs = {"d1", "t1"}
    else:
        # The loop numbers every tool result in call order.
        answer = "Returns take 15 calendar days [t1], and 6 orders were delivered [t2]."
        refs = {"t1", "t2"}
    provider = ContractProvider(
        plan=["docs", "data"], tool_calls=[CALL_DOCS, CALL_ORDERS], answer=answer
    )
    tools = StubToolSource()
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind,
        provider,
        agents=[DocsAgent(), DataAgent()],
        tool_sources=[tools],
        retriever=StubRetriever(),
        tracer=tracer,
    )

    result = await orchestrator.run(
        ChatRequest(query="Return window and how many delivered?", tenant_id="default"),
        context(),
    )

    assert result.status == "ok"
    assert result.plan is not None and result.plan.label == "data+docs"
    assert result.answer is not None
    assert {citation.ref for citation in result.answer.citations} == refs
    assert result.gate is not None and result.gate.passed
    assert tools.calls == [("orders_summary@1", {"status": "delivered"})]
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="data+docs")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_denied_tool_leaves_a_partial_answer(kind: str) -> None:
    provider = ContractProvider(
        plan=["data"],
        tool_calls=[CALL_ORDERS],
        answer="The orders source is unavailable, so I cannot answer that.",
    )
    tools = StubToolSource(denied=True)
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind, provider, agents=[DataAgent()], tool_sources=[tools], tracer=tracer
    )

    result = await orchestrator.run(
        ChatRequest(query="How many delivered orders?", tenant_id="default"), context()
    )

    assert result.status == "partial"
    assert result.attempts == 1
    assert result.gate is not None
    assert result.gate.failed_checks == ["evidence_present"]
    assert any(error.kind == "CheckFailed" for error in result.errors)
    assert result.answer is not None
    assert any(
        record.status == "denied" and record.tool == "orders_summary@1"
        for record in result.answer.tool_calls
    )
    assert "could not confirm this answer" in result.answer.answer
    assert_run_span(store, orchestrator=orchestrator.name, status="partial", plan_label="data")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_run_that_takes_too_long(kind: str) -> None:
    provider = ContractProvider(plan=["direct"], answer="too late", delay=0.5)
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind,
        provider,
        agents=[DirectAgent()],
        limits=Limits(run_timeout_s=0.05),
        tracer=tracer,
    )

    started = time.perf_counter()
    result = await orchestrator.run(ChatRequest(query="slow", tenant_id="default"), context())
    elapsed = time.perf_counter() - started

    assert elapsed < 0.4
    assert result.status == "timeout"
    assert result.errors[0].kind == "RunTimeout"
    assert_run_span(store, orchestrator=orchestrator.name, status="timeout", plan_label=None)


def test_the_plan_is_derived_from_the_tools_that_ran() -> None:
    known = ["orders_summary@1", "result_page@1", "search_docs@1"]

    assert derive_plan([], known).agents == ["direct"]
    assert derive_plan(["search_docs@1"], known).agents == ["docs"]
    assert derive_plan(["orders_summary@1"], known).agents == ["data"]
    # result_page@1 only pages a data result, so it belongs to data.
    assert derive_plan(["result_page@1"], known).agents == ["data"]
    assert derive_plan(["search_docs@1", "orders_summary@1"], known).agents == ["data", "docs"]
    # A tool the loop did not expose cannot move the label.
    assert derive_plan(["delete_order@1"], known).agents == ["direct"]
    assert derive_plan(["search_docs@1"], known).reason.startswith("Derived from the tools")


async def test_the_agent_loop_never_exposes_or_runs_a_write_tool() -> None:
    writes = WriteToolSource()
    provider = ContractProvider(
        tool_calls=[ToolCall(name="delete_order@1", arguments={}, call_id="w1")],
        answer="I cannot delete orders; every tool here is read-only.",
    )
    tracer, _store = tracer_and_store()
    orchestrator = make_orchestrator(
        "agent_loop",
        provider,
        agents=[DirectAgent()],
        tool_sources=[writes],
        tracer=tracer,
    )

    result = await orchestrator.run(
        ChatRequest(query="Delete order abc.", tenant_id="default"), context()
    )

    assert writes.calls == 0
    assert result.answer is not None
    assert all(
        record.tool != "delete_order@1" or record.status == "error"
        for record in result.answer.tool_calls
    )
    assert any(record.status == "error" for record in result.answer.tool_calls)
    exposed = {tool.wire_name for tool in provider.requests[0].tools}
    assert "delete_order@1" not in exposed
    assert "result_page@1" in exposed


async def test_the_agent_loop_stops_at_the_step_limit() -> None:
    provider = ContractProvider(tool_calls=[CALL_ORDERS], answer="unused", repeat_tool_calls=True)
    tools = StubToolSource()
    tracer, _store = tracer_and_store()
    orchestrator = make_orchestrator(
        "agent_loop",
        provider,
        agents=[DataAgent()],
        tool_sources=[tools],
        limits=Limits(run_timeout_s=5.0, max_loop_steps=2),
        tracer=tracer,
    )

    result = await orchestrator.run(
        ChatRequest(query="How many delivered orders?", tenant_id="default"), context()
    )

    assert result.status == "partial"
    assert result.answer is not None
    assert result.answer.partial is True
    # Two model calls per attempt, and the gate's one corrective action runs a
    # second attempt; the step budget is per attempt, not per run.
    assert len(provider.requests) == 4
    assert len(tools.calls) == 4
    assert result.attempts == 1
    assert result.plan is not None and result.plan.label == "data"


def test_registry_resolves_the_agent_loop_entry_point() -> None:
    from switchboard.registry import ORCHESTRATORS

    assert Registry().load(ORCHESTRATORS, "agent_loop") is AgentLoopOrchestrator
