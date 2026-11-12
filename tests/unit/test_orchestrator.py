"""LangGraph plan graph: plans, repair, fallback, fan-out, timeout."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest

from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.core.config import Limits, PriceEntry
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ToolExecutionError
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    ChatRequest,
    CompletionResponse,
    ProviderCapabilities,
    RunContext,
    TenantContext,
    Usage,
)
from switchboard.core.ports import AgentDeps
from switchboard.registry import AGENTS, ORCHESTRATORS, Registry


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class StubAgent:
    def __init__(
        self,
        name: str,
        *,
        delay: float = 0.0,
        partial: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.spec = AgentSpec(
            name=name,
            description=f"{name} stub agent",
            cacheable=True,
        )
        self.delay = delay
        self.partial = partial
        self.error = error

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return AgentResult(
            answer=f"{self.spec.name} answer",
            usage=Usage(input_tokens=1, output_tokens=1),
            partial=self.partial,
        )


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


def context(allowed: set[str] | None = None) -> RunContext:
    return RunContext(
        run_id="run-1",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents=allowed or {"direct", "docs", "data", "fourth"},
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def make(
    provider: PlanProvider,
    agents: list[StubAgent],
    *,
    limits: Limits | None = None,
    price: PriceEntry | None = None,
) -> LangGraphOrchestrator:
    registry = Registry([FakeEntryPoint(agent.spec.name, AGENTS, agent) for agent in agents])
    return LangGraphOrchestrator(
        provider=provider,
        registry=registry,
        context_policy=ClearOldestToolResults(),
        limits=limits or Limits(run_timeout_s=5.0),
        price=price,
    )


async def test_direct_plan_runs_one_agent() -> None:
    provider = PlanProvider([{"agents": ["direct"], "reason": "small talk"}])
    orchestrator = make(provider, [StubAgent("direct")])
    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())
    assert result.plan is not None
    assert result.plan.agents == ["direct"]
    assert result.answer is not None
    assert result.answer.answer == "direct answer"
    assert result.status == "ok"
    assert result.usage.input_tokens == 6
    # No price row means no cost, not a silent zero (design 12.4).
    assert result.cost_usd is None


async def test_a_priced_run_carries_its_cost() -> None:
    provider = PlanProvider([{"agents": ["direct"], "reason": "small talk"}])
    price = PriceEntry(
        provider="fake",
        model=None,
        input_per_million=1.0,
        cached_input_per_million=0.1,
        output_per_million=2.0,
        source="https://example.invalid/prices",
    )
    orchestrator = make(provider, [StubAgent("direct")], price=price)
    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    usage = result.usage
    expected = (
        (usage.input_tokens - usage.cached_input_tokens) * 1.0
        + usage.cached_input_tokens * 0.1
        + usage.output_tokens * 2.0
    ) / 1_000_000
    assert result.cost_usd == pytest.approx(expected)


@pytest.mark.parametrize(("agent_name", "plan_label"), [("docs", "docs"), ("data", "data")])
async def test_single_docs_and_data_plans(agent_name: str, plan_label: str) -> None:
    provider = PlanProvider([{"agents": [agent_name], "reason": "one source"}])
    orchestrator = make(provider, [StubAgent(agent_name)])
    result = await orchestrator.run(ChatRequest(query="question", tenant_id="default"), context())
    assert result.plan is not None
    assert result.plan.label == plan_label
    assert result.status == "ok"


async def test_parallel_agents_overlap_in_time() -> None:
    provider = PlanProvider([{"agents": ["docs", "data"], "reason": "both sources"}])
    orchestrator = make(
        provider,
        [StubAgent("docs", delay=0.2), StubAgent("data", delay=0.2)],
    )
    started = time.perf_counter()
    result = await orchestrator.run(ChatRequest(query="both?", tenant_id="default"), context())
    elapsed = time.perf_counter() - started
    assert elapsed < 0.35
    assert result.answer is not None
    assert "docs answer" in result.answer.answer
    assert "data answer" in result.answer.answer


async def test_invalid_plan_is_repaired_once() -> None:
    provider = PlanProvider(
        [
            {"agents": ["ghost"], "reason": "wrong"},
            {"agents": ["direct"], "reason": "fixed"},
        ]
    )
    orchestrator = make(provider, [StubAgent("direct")])
    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())
    assert result.plan is not None
    assert result.plan.agents == ["direct"]
    assert len(provider.requests) == 2
    assert any(error.kind == "PlanValidationError" for error in result.errors)


async def test_invalid_plan_twice_uses_the_fallback() -> None:
    provider = PlanProvider(
        [
            {"agents": ["ghost"], "reason": "wrong"},
            {"agents": ["ghost"], "reason": "still wrong"},
        ]
    )
    orchestrator = make(
        provider,
        [StubAgent("docs"), StubAgent("data")],
        limits=Limits(fallback_plan=["docs", "data"], run_timeout_s=5.0),
    )
    result = await orchestrator.run(ChatRequest(query="both?", tenant_id="default"), context())
    assert result.plan is not None
    assert result.plan.label == "data+docs"
    assert any(error.message == "fallback plan used" for error in result.errors)


async def test_a_fourth_agent_appears_without_code_changes() -> None:
    provider = PlanProvider([{"agents": ["fourth"], "reason": "new agent"}])
    orchestrator = make(provider, [StubAgent("direct"), StubAgent("fourth")])
    result = await orchestrator.run(ChatRequest(query="new?", tenant_id="default"), context())
    assert result.plan is not None
    assert result.plan.agents == ["fourth"]
    enum = provider.requests[0].response_schema["properties"]["agents"]["items"]["enum"]
    assert "fourth" in enum
    assert "- fourth: fourth stub agent" in provider.requests[0].messages[1].content


async def test_run_timeout_returns_a_timeout_result() -> None:
    provider = PlanProvider([{"agents": ["direct"], "reason": "slow"}])
    orchestrator = make(
        provider,
        [StubAgent("direct", delay=1.0)],
        limits=Limits(run_timeout_s=0.1),
    )
    started = time.perf_counter()
    result = await orchestrator.run(ChatRequest(query="slow", tenant_id="default"), context())
    assert time.perf_counter() - started < 0.8
    assert result.status == "timeout"
    assert result.errors[0].kind == "RunTimeout"


async def test_agent_failure_becomes_a_partial_run() -> None:
    provider = PlanProvider([{"agents": ["direct", "data"], "reason": "both"}])
    orchestrator = make(
        provider,
        [
            StubAgent("direct"),
            StubAgent("data", error=ToolExecutionError("database unavailable")),
        ],
    )
    result = await orchestrator.run(ChatRequest(query="both?", tenant_id="default"), context())
    assert result.status == "partial"
    assert result.answer is not None
    assert "data agent failed" in result.answer.answer
    assert any(error.kind == "ToolExecutionError" for error in result.errors)


def test_registry_resolves_the_orchestrator_entry_point() -> None:
    assert Registry().load(ORCHESTRATORS, "langgraph") is LangGraphOrchestrator
