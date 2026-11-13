"""Orchestrator contract: LangGraph and the agent loop share one module.

Every case runs twice, once per adapter loaded from the ``switchboard.orchestrators``
entry points, and asserts only what a caller can observe: the plan label, the
answer, citations and evidence, the status and the run span. The plan is the
only role-specific difference: LangGraph asks a supervisor, the loop derives it
from its tool calls (design.md 6.9), and both must end with the same label. The
unit module keeps the deeper LangGraph and loop-specific cases (repair, step
limit, write tool); this module is the port contract for both adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from tests.contracts.support import completion, policy_under_test, run_context
from tests.fakes import MemoryRunStore, MemorySpanExporter

from switchboard.adapters.orchestrator.agent_loop import AgentLoopOrchestrator
from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.agents.data import DataAgent
from switchboard.agents.direct import DirectAgent
from switchboard.agents.docs import DocsAgent
from switchboard.core.config import Limits
from switchboard.core.errors import ToolPermissionError
from switchboard.core.models import (
    AgentResult,
    ChatRequest,
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
    RetrievedChunk,
    RunContext,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from switchboard.core.ports import Agent, Orchestrator, ToolSource
from switchboard.registry import AGENTS, ORCHESTRATORS, Registry

KINDS = ("langgraph", "agent_loop")
ORDERS_SPEC = ToolSpec(
    name="orders_summary",
    version="1",
    description="Count orders.",
    input_schema={"type": "object", "properties": {}},
)
CALL_ORDERS = ToolCall(name="orders_summary@1", arguments={"status": "delivered"}, call_id="c1")
CALL_DOCS = ToolCall(
    name="search_docs@1", arguments={"query": "return window", "k": 5}, call_id="c2"
)


@dataclass
class Entry:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class StubToolSource:
    name = "stub_olist"

    def __init__(self, *, denied: bool = False) -> None:
        self.denied = denied
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        del ctx
        return [ORDERS_SPEC]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        del ctx
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


class StubRetriever:
    name = "stub_docs"

    async def retrieve(
        self, query: str, ctx: RunContext, k: int = 5, *, round: int = 1
    ) -> list[RetrievedChunk]:
        del query, ctx, k, round
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


class OrchestratorProvider:
    """One scripted model for both adapters, selected by request purpose."""

    name = "contract"

    def __init__(
        self,
        *,
        plan: list[str],
        tool_calls: list[ToolCall] | None,
        answer: str,
    ) -> None:
        self.plan = plan
        self.tool_calls = list(tool_calls or [])
        self.answer = answer
        self.requests: list[CompletionRequest] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if request.response_schema is not None:
            return completion(parsed={"agents": self.plan, "reason": "scripted plan"})
        purpose = str(request.metadata.get("purpose", ""))
        has_tool_message = any(message.role == "tool" for message in request.messages)
        if (
            self.tool_calls
            and purpose in {"agent.data", "agent.agent_loop"}
            and not has_tool_message
        ):
            calls = (
                self.tool_calls
                if purpose == "agent.agent_loop"
                else [call for call in self.tool_calls if not call.name.startswith("search_docs")]
            )
            if calls:
                return completion(tool_calls=calls)
        return completion(self.answer)


def tracer_and_store() -> tuple[Any, MemoryRunStore]:
    store = MemoryRunStore()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(store)))
    return provider.get_tracer("switchboard.test.contract"), store


def make_orchestrator(
    kind: str,
    provider: OrchestratorProvider,
    *,
    agents: list[Agent],
    tool_sources: list[ToolSource] | None = None,
    retriever: Any = None,
    tracer: Any = None,
) -> Orchestrator:
    classes: dict[str, Any] = Registry().load_all(ORCHESTRATORS)
    registry = Registry([Entry(agent.spec.name, AGENTS, agent) for agent in agents])
    return cast(
        Orchestrator,
        classes[kind](
            provider=provider,
            registry=registry,
            context_policy=policy_under_test(),
            limits=Limits(run_timeout_s=5.0, max_loop_steps=4),
            tool_sources=list(tool_sources or []),
            retriever=retriever,
            tracer=tracer,
        ),
    )


def context() -> RunContext:
    return run_context(allowed_agents={"direct", "docs", "data"})


def agent_answer(result: Any) -> AgentResult:
    assert result.answer is not None
    return cast(AgentResult, result.answer)


def assert_run_span(
    store: MemoryRunStore, *, orchestrator: str, status: str, plan_label: str | None
) -> None:
    rows = [row for row in store.spans if row["name"] == "run"]
    assert len(rows) == 1
    attributes = rows[0]["attributes"]
    assert attributes["switchboard.orchestrator"] == orchestrator
    assert attributes["switchboard.status"] == status
    assert attributes.get("switchboard.plan") == plan_label


def test_the_registry_exposes_both_adapters() -> None:
    classes = Registry().load_all(ORCHESTRATORS)
    assert set(classes) == set(KINDS)
    assert classes["langgraph"] is LangGraphOrchestrator
    assert classes["agent_loop"] is AgentLoopOrchestrator


@pytest.mark.parametrize("kind", KINDS)
async def test_a_direct_answer(kind: str) -> None:
    provider = OrchestratorProvider(plan=["direct"], tool_calls=None, answer="Hello.")
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(kind, provider, agents=[DirectAgent()], tracer=tracer)

    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    assert result.status == "ok"
    assert result.plan is not None and result.plan.label == "direct"
    answer = agent_answer(result)
    assert answer.answer == "Hello."
    assert answer.citations == []
    assert answer.evidence == []
    assert result.gate is not None and result.gate.passed
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="direct")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_docs_answer_cites_the_chunk(kind: str) -> None:
    answer_text = (
        "Returns are accepted within 15 calendar days of delivery [d1]."
        if kind == "langgraph"
        else "Returns are accepted within 15 calendar days of delivery [t1]."
    )
    provider = OrchestratorProvider(plan=["docs"], tool_calls=[CALL_DOCS], answer=answer_text)
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind,
        provider,
        agents=[DocsAgent()],
        retriever=StubRetriever(),
        tracer=tracer,
    )

    result = await orchestrator.run(
        ChatRequest(query="What is the return window?", tenant_id="default"), context()
    )

    assert result.status == "ok"
    assert result.plan is not None and result.plan.label == "docs"
    answer = agent_answer(result)
    assert len(answer.citations) == 1
    # The docs agent numbers a chunk d1; the loop sees the same chunk as a tool
    # result t1, so its citation is a tool citation.
    assert answer.citations[0].kind == ("document" if kind == "langgraph" else "tool")
    assert answer.evidence and answer.evidence[0].text
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="docs")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_data_answer_cites_the_tool_call(kind: str) -> None:
    provider = OrchestratorProvider(
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
    answer = agent_answer(result)
    assert len(answer.citations) == 1
    assert answer.citations[0].kind == "tool"
    assert answer.evidence and not answer.evidence[0].empty
    assert tools.calls == [("orders_summary@1", {"status": "delivered"})]
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="data")


@pytest.mark.parametrize("kind", KINDS)
async def test_two_sources_produce_one_plan_and_both_citations(kind: str) -> None:
    if kind == "langgraph":
        answer_text = "Returns take 15 calendar days [d1], and 6 orders were delivered [t1]."
        refs = {"d1", "t1"}
    else:
        answer_text = "Returns take 15 calendar days [t1], and 6 orders were delivered [t2]."
        refs = {"t1", "t2"}
    provider = OrchestratorProvider(
        plan=["docs", "data"],
        tool_calls=[CALL_DOCS, CALL_ORDERS],
        answer=answer_text,
    )
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind,
        provider,
        agents=[DocsAgent(), DataAgent()],
        tool_sources=[StubToolSource()],
        retriever=StubRetriever(),
        tracer=tracer,
    )

    result = await orchestrator.run(
        ChatRequest(query="Return window and delivered orders?", tenant_id="default"), context()
    )

    assert result.status == "ok"
    assert result.plan is not None and result.plan.label == "data+docs"
    assert {citation.ref for citation in agent_answer(result).citations} == refs
    assert_run_span(store, orchestrator=orchestrator.name, status="ok", plan_label="data+docs")


@pytest.mark.parametrize("kind", KINDS)
async def test_a_denied_tool_leaves_a_partial_answer(kind: str) -> None:
    provider = OrchestratorProvider(
        plan=["data"],
        tool_calls=[CALL_ORDERS],
        answer="The orders source is unavailable, so I cannot answer that.",
    )
    tracer, store = tracer_and_store()
    orchestrator = make_orchestrator(
        kind,
        provider,
        agents=[DataAgent()],
        tool_sources=[StubToolSource(denied=True)],
        tracer=tracer,
    )

    result = await orchestrator.run(
        ChatRequest(query="How many delivered orders?", tenant_id="default"), context()
    )

    assert result.status == "partial"
    assert result.attempts == 1
    assert result.gate is not None
    assert result.gate.failed_checks == ["evidence_present"]
    assert any(error.kind == "CheckFailed" for error in result.errors)
    answer = agent_answer(result)
    assert any(
        record.status == "denied" and record.tool == "orders_summary@1"
        for record in answer.tool_calls
    )
    assert "could not confirm this answer" in answer.answer
    assert_run_span(store, orchestrator=orchestrator.name, status="partial", plan_label="data")
