"""Orchestrator corrective actions: one retry, one re-plan, attempts limit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.fakes import UnusedToolSource

from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.core.config import Limits
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    ChatRequest,
    Citation,
    CompletionResponse,
    EvidenceRef,
    ProviderCapabilities,
    RunContext,
    TenantContext,
    Usage,
)
from switchboard.core.ports import AgentDeps
from switchboard.registry import AGENTS, Registry


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class PlanProvider:
    name = "fake"

    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self.plans = list(plans)
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
        return CompletionResponse(
            text=None,
            parsed=self.plans.pop(0),
            usage=Usage(input_tokens=5, output_tokens=2),
            model="fake",
            provider="fake",
            latency_ms=1,
        )


class ScriptedAgent:
    def __init__(
        self,
        name: str,
        *,
        needs: list[Literal["tools", "retriever"]],
        results: list[AgentResult],
    ) -> None:
        self.spec = AgentSpec(
            name=name,
            description=f"{name} stub",
            cacheable=False,
            needs=needs,
        )
        self.results = list(results)
        self.tasks: list[AgentTask] = []

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        self.tasks.append(task)
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


def grounded(answer: str, number: str, ref: str = "t1", *, empty: bool = False) -> AgentResult:
    return AgentResult(
        answer=answer,
        citations=[Citation(kind="tool", ref=ref, source_id=f"result-{ref}", snippet=number)],
        evidence=[
            EvidenceRef(
                kind="tool",
                ref=ref,
                source_id=f"result-{ref}",
                text=f'{{"total_rows": {number}, "late_orders": {number}}}',
                empty=empty,
            )
        ],
        usage=Usage(input_tokens=10, output_tokens=4),
    )


def no_evidence(answer: str) -> AgentResult:
    return AgentResult(
        answer=answer,
        usage=Usage(input_tokens=10, output_tokens=4),
        partial=True,
    )


def context() -> RunContext:
    return RunContext(
        run_id="run-gate",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents={"docs", "data"},
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def make(
    provider: PlanProvider,
    agents: list[ScriptedAgent],
    *,
    tracer: Any = None,
) -> LangGraphOrchestrator:
    registry = Registry([FakeEntryPoint(agent.spec.name, AGENTS, agent) for agent in agents])
    return LangGraphOrchestrator(
        provider=provider,
        registry=registry,
        context_policy=ClearOldestToolResults(),
        limits=Limits(run_timeout_s=5.0, fallback_plan=["docs", "data"]),
        tool_sources=[UnusedToolSource()],
        retriever=object(),
        tracer=tracer,
    )


async def test_an_ungrounded_number_triggers_one_retry_with_feedback() -> None:
    provider = PlanProvider([{"agents": ["data"], "reason": "records"}])
    agent = ScriptedAgent(
        "data",
        needs=["tools"],
        results=[
            grounded("There were 9,999 late orders [t1].", "7262"),
            grounded("There were 7,262 late orders [t1].", "7262"),
        ],
    )
    orchestrator = make(provider, [agent])

    result = await orchestrator.run(
        ChatRequest(query="How many were late?", tenant_id="default"), context()
    )

    assert result.status == "ok"
    assert result.attempts == 1
    assert len(agent.tasks) == 2
    assert agent.tasks[0].feedback is None
    assert agent.tasks[1].feedback is not None
    assert "9,999" in agent.tasks[1].feedback
    assert result.gate is not None
    assert result.gate.passed is True
    check_errors = [error for error in result.errors if error.kind == "CheckFailed"]
    assert len(check_errors) == 1
    assert check_errors[0].retryable is True
    # The rejected attempt was billed too: one supervisor call (5/2) and two
    # data agent calls (10/4 each), not just the attempt that produced the
    # final answer (AC-19a).
    assert result.usage == Usage(input_tokens=25, cached_input_tokens=0, output_tokens=10)


async def test_empty_evidence_triggers_one_replan_with_a_different_plan() -> None:
    provider = PlanProvider(
        [
            {"agents": ["docs"], "reason": "policy"},
            {"agents": ["data"], "reason": "records instead"},
        ]
    )
    docs = ScriptedAgent(
        "docs",
        needs=["retriever"],
        results=[no_evidence("I could not find any policy documents that answer this question.")],
    )
    data = ScriptedAgent(
        "data",
        needs=["tools"],
        results=[grounded("There were 42 late orders [t1].", "42")],
    )
    orchestrator = make(provider, [docs, data])

    result = await orchestrator.run(
        ChatRequest(query="How many were late?", tenant_id="default"), context()
    )

    assert result.status == "ok"
    assert result.attempts == 1
    assert result.plan is not None
    assert result.plan.agents == ["data"]
    assert result.previous_plan is not None
    assert result.previous_plan.agents == ["docs"]
    assert len(docs.tasks) == 1
    assert len(data.tasks) == 1
    assert len(provider.requests) == 2
    replan_prompt = provider.requests[1].messages[-1].content
    assert "previous plan was docs" in replan_prompt
    assert "must differ" in replan_prompt
    assert result.gate is not None
    assert result.gate.passed is True
    # Both plans were paid for: two supervisor calls and one call per branch.
    assert result.usage == Usage(input_tokens=30, cached_input_tokens=0, output_tokens=12)


async def test_the_attempts_limit_stops_a_second_corrective_action() -> None:
    provider = PlanProvider([{"agents": ["data"], "reason": "records"}])
    agent = ScriptedAgent(
        "data",
        needs=["tools"],
        results=[grounded("There were 9,999 late orders [t1].", "7262")],
    )
    orchestrator = make(provider, [agent])

    result = await orchestrator.run(
        ChatRequest(query="How many were late?", tenant_id="default"), context()
    )

    assert result.attempts == 1
    assert len(agent.tasks) == 2
    assert result.gate is not None
    assert result.gate.passed is False
    assert result.gate.action == "none"
    assert "attempts limit" in result.gate.reason
    assert result.status == "partial"
    check_errors = [error for error in result.errors if error.kind == "CheckFailed"]
    assert len(check_errors) == 2
    assert [error.retryable for error in check_errors] == [True, False]


async def test_a_replan_must_differ_from_the_previous_plan() -> None:
    provider = PlanProvider(
        [
            {"agents": ["docs"], "reason": "policy"},
            {"agents": ["docs"], "reason": "same again"},
            {"agents": ["docs"], "reason": "still the same"},
        ]
    )
    docs = ScriptedAgent(
        "docs",
        needs=["retriever"],
        results=[no_evidence("I could not find any policy documents that answer this question.")],
    )
    data = ScriptedAgent(
        "data",
        needs=["tools"],
        results=[grounded("There were 42 late orders [t1].", "42")],
    )
    orchestrator = make(provider, [docs, data])

    result = await orchestrator.run(
        ChatRequest(query="How many were late?", tenant_id="default"), context()
    )

    assert result.plan is not None
    assert result.plan.label == "data+docs"
    assert result.previous_plan is not None
    assert result.previous_plan.agents == ["docs"]
    assert any(
        error.message == "the new plan must differ from the previous plan"
        for error in result.errors
    )
    assert result.attempts == 1
    assert result.gate is not None
    assert result.gate.passed is True


async def test_gate_span_records_failed_checks_and_action() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    planner = PlanProvider([{"agents": ["data"], "reason": "records"}])
    agent = ScriptedAgent(
        "data",
        needs=["tools"],
        results=[
            grounded("There were 9,999 late orders [t1].", "7262"),
            grounded("There were 7,262 late orders [t1].", "7262"),
        ],
    )
    orchestrator = make(planner, [agent], tracer=provider.get_tracer("switchboard.test.gate"))

    result = await orchestrator.run(
        ChatRequest(query="How many were late?", tenant_id="default"), context()
    )

    gate_spans = [span for span in exporter.get_finished_spans() if span.name == "gate"]
    assert len(gate_spans) == 2
    first = dict(gate_spans[0].attributes or {})
    assert first["switchboard.gate.verdict"] == "fail"
    assert first["switchboard.gate.failed_checks"] == "numbers_grounded"
    assert first["switchboard.gate.action"] == "retry"
    second = dict(gate_spans[1].attributes or {})
    assert second["switchboard.gate.verdict"] == "pass"
    assert second["switchboard.gate.action"] == "pass"
    run_span = next(span for span in exporter.get_finished_spans() if span.name == "run")
    assert result.attempts == 1
    assert dict(run_span.attributes or {})["switchboard.attempts"] == 1
