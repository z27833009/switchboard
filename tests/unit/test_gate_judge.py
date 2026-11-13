"""Gate layer 2 in both orchestrators (T3.04, design.md section 6.8).

The same fake judge drives the LangGraph plan graph and the agent loop, so the
two adapters cannot drift: layer 2 runs only after layer 1 passes, a weak
verdict is one failed ``judge`` check with action ``none`` and the original
answer kept, and a judge error never fails the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.fakes import UnusedToolSource

from switchboard.adapters.orchestrator.agent_loop import AgentLoopOrchestrator
from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.agents.direct import DirectAgent
from switchboard.core.config import Limits
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import JudgeError
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    ChatRequest,
    Citation,
    CompletionResponse,
    EvidenceRef,
    GateVerdict,
    JudgeRequest,
    JudgeVerdict,
    ProviderCapabilities,
    RunContext,
    TenantContext,
    Usage,
)
from switchboard.core.ports import AgentDeps
from switchboard.gate.checks import evaluate_gate
from switchboard.gate.judge import run_judge
from switchboard.registry import AGENTS, Registry

KINDS = ("langgraph", "agent_loop")


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class ScriptedProvider:
    """A plan for a schema request, a fixed answer otherwise."""

    name = "fake"

    def __init__(
        self,
        *,
        plan: list[str] | None = None,
        answer: str = "Hello. I can answer order and policy questions.",
    ) -> None:
        self.plan = list(plan or ["direct"])
        self.answer = answer
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
        if request.response_schema is not None:
            return CompletionResponse(
                text=None,
                parsed={"agents": self.plan, "reason": "scripted plan"},
                usage=Usage(input_tokens=5, output_tokens=2),
                model="fake",
                provider="fake",
                latency_ms=1,
            )
        return CompletionResponse(
            text=self.answer,
            usage=Usage(input_tokens=10, output_tokens=4),
            model="fake",
            provider="fake",
            latency_ms=1,
        )


class FakeJudge:
    """A Judge port implementation with a scripted verdict or error."""

    name = "fake-judge"

    def __init__(
        self,
        *,
        verdict: JudgeVerdict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.verdict = verdict or JudgeVerdict(
            verdict="acceptable",
            score=0.9,
            reason="grounded",
            usage=Usage(input_tokens=2, output_tokens=1),
            latency_ms=5,
            cost_usd=0.0,
        )
        self.error = error
        self.requests: list[JudgeRequest] = []

    async def judge(self, request: JudgeRequest) -> JudgeVerdict:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.verdict


class ScriptedAnswerAgent:
    """A registered agent that returns fixed results in order."""

    def __init__(self, name: str, results: list[AgentResult]) -> None:
        self.spec = AgentSpec(
            name=name, description=f"{name} stub", cacheable=False, needs=["tools"]
        )
        self.results = list(results)
        self.tasks: list[AgentTask] = []

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        self.tasks.append(task)
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def context() -> RunContext:
    return RunContext(
        run_id="run-judge",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents={"direct", "data"},
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def make_orchestrator(
    kind: str,
    provider: ScriptedProvider,
    *,
    judge: Any,
    agents: list[Any] | None = None,
    tool_sources: list[Any] | None = None,
    tracer: Any = None,
) -> Any:
    chosen = agents or [DirectAgent()]
    registry = Registry([FakeEntryPoint(agent.spec.name, AGENTS, agent) for agent in chosen])
    common: dict[str, Any] = {
        "provider": provider,
        "registry": registry,
        "context_policy": ClearOldestToolResults(),
        "limits": Limits(run_timeout_s=5.0, max_loop_steps=3),
        "tool_sources": list(tool_sources or []),
        "retriever": None,
        "judge": judge,
        "tracer": tracer,
    }
    if kind == "agent_loop":
        return AgentLoopOrchestrator(**common)
    return LangGraphOrchestrator(**common)


def grounded(answer: str, number: str) -> AgentResult:
    return AgentResult(
        answer=answer,
        citations=[Citation(kind="tool", ref="t1", source_id="result-t1", snippet=number)],
        evidence=[
            EvidenceRef(
                kind="tool",
                ref="t1",
                source_id="result-t1",
                text=f'{{"late_orders": {number}}}',
            )
        ],
        usage=Usage(input_tokens=10, output_tokens=4),
    )


@pytest.mark.parametrize("kind", KINDS)
async def test_an_acceptable_judge_verdict_keeps_the_run_ok(kind: str) -> None:
    judge = FakeJudge()
    provider = ScriptedProvider()
    orchestrator = make_orchestrator(kind, provider, judge=judge)

    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    assert result.status == "ok"
    assert result.gate is not None
    assert result.gate.passed is True
    assert result.gate.failed_checks == []
    assert not [error for error in result.errors if error.stage == "gate.judge"]
    assert len(judge.requests) == 1
    assert judge.requests[0].query == "hello"
    assert judge.requests[0].answer == "Hello. I can answer order and policy questions."
    # The judge call is billed: its usage is part of the run total. The plan
    # graph pays for the supervisor call as well; the loop does not.
    baseline = 15 if kind == "langgraph" else 10
    assert result.usage.input_tokens == baseline + 2
    assert result.usage.output_tokens == (6 if kind == "langgraph" else 4) + 1


@pytest.mark.parametrize("kind", KINDS)
async def test_a_weak_judge_verdict_is_one_failed_check_with_no_retry(kind: str) -> None:
    judge = FakeJudge(
        verdict=JudgeVerdict(
            verdict="weak",
            score=0.2,
            reason="the answer claims a 30 day return window that the evidence contradicts",
        )
    )
    provider = ScriptedProvider()
    orchestrator = make_orchestrator(kind, provider, judge=judge)

    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    assert result.status == "partial"
    assert result.gate is not None
    assert result.gate.passed is False
    assert result.gate.failed_checks == ["judge"]
    assert result.gate.action == "none"
    assert "return window" in result.gate.reason
    # The original answer is kept, with the quality note appended.
    assert result.answer is not None
    assert result.answer.answer.startswith("Hello. I can answer order and policy questions.")
    assert "could not confirm this answer" in result.answer.answer
    assert result.answer.partial is True
    # No extra retry loop: one plan (or one loop) and exactly one judge call.
    assert len(judge.requests) == 1
    check_errors = [error for error in result.errors if error.kind == "CheckFailed"]
    assert len(check_errors) == 1
    assert check_errors[0].details["failed_checks"] == ["judge"]
    assert check_errors[0].retryable is False
    assert result.attempts == 0


@pytest.mark.parametrize("kind", KINDS)
async def test_a_judge_error_does_not_fail_the_run(kind: str) -> None:
    judge = FakeJudge(error=JudgeError("the judge returned no structured object"))
    provider = ScriptedProvider()
    orchestrator = make_orchestrator(kind, provider, judge=judge)

    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    assert result.status == "ok"
    assert result.gate is not None
    assert result.gate.passed is True
    assert result.gate.failed_checks == []
    # The error is visible in the gate reason and in the run errors.
    assert "judge error" in result.gate.reason
    assert "returned no structured object" in result.gate.reason
    judge_errors = [error for error in result.errors if error.stage == "gate.judge"]
    assert [error.kind for error in judge_errors] == ["JudgeError"]
    assert result.answer is not None
    assert "could not confirm" not in result.answer.answer


@pytest.mark.parametrize("kind", KINDS)
async def test_layer_two_runs_only_after_layer_one_passes(kind: str) -> None:
    """A grounded answer is judged; a rejected one is never sent to the judge."""
    judge = FakeJudge()
    # The loop always answers in text, so its plan is direct and layer 1 passes;
    # the LangGraph path runs a data agent with no evidence, which layer 1 fails.
    if kind == "langgraph":
        agent = ScriptedAnswerAgent(
            "data",
            [
                AgentResult(
                    answer="I could not reach the tool.",
                    usage=Usage(input_tokens=10, output_tokens=4),
                    partial=True,
                )
            ],
        )
        provider = ScriptedProvider(plan=["data"], answer="unused")
        orchestrator = make_orchestrator(
            kind, provider, judge=judge, agents=[agent], tool_sources=[UnusedToolSource()]
        )
    else:
        provider = ScriptedProvider(answer="I could not reach the tool.")
        orchestrator = make_orchestrator(kind, provider, judge=judge)

    result = await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    assert result.gate is not None
    if kind == "langgraph":
        # evidence_present fails, so the judge must not be asked to review it.
        assert result.gate.passed is False
        assert "evidence_present" in result.gate.failed_checks
        assert judge.requests == []
    else:
        # A direct answer needs no evidence, so layer 1 passes and the judge runs.
        assert result.gate.passed is True
        assert len(judge.requests) == 1


async def test_the_langgraph_judge_sees_the_answer_that_passed_layer_one() -> None:
    judge = FakeJudge()
    agent = ScriptedAnswerAgent(
        "data",
        [
            grounded("There were 9,999 late orders [t1].", "7262"),
            grounded("There were 7,262 late orders [t1].", "7262"),
        ],
    )
    provider = ScriptedProvider(plan=["data"], answer="unused")
    orchestrator = make_orchestrator(
        "langgraph", provider, judge=judge, agents=[agent], tool_sources=[UnusedToolSource()]
    )

    result = await orchestrator.run(
        ChatRequest(query="How many were late?", tenant_id="default"), context()
    )

    assert result.attempts == 1
    assert result.gate is not None and result.gate.passed
    # Exactly one judge call, on the retried answer, not on the rejected one.
    assert len(judge.requests) == 1
    assert judge.requests[0].answer == "There were 7,262 late orders [t1]."


async def test_the_gate_span_records_the_judge_outcome() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    judge = FakeJudge(verdict=JudgeVerdict(verdict="weak", score=0.3, reason="unsupported"))
    orchestrator = make_orchestrator(
        "langgraph",
        ScriptedProvider(),
        judge=judge,
        tracer=provider.get_tracer("switchboard.test.judge.gate"),
    )

    await orchestrator.run(ChatRequest(query="hello", tenant_id="default"), context())

    gate_spans = [span for span in exporter.get_finished_spans() if span.name == "gate"]
    assert len(gate_spans) == 1
    attributes = dict(gate_spans[0].attributes or {})
    assert attributes["switchboard.gate.verdict"] == "fail"
    assert attributes["switchboard.gate.failed_checks"] == "judge"
    assert attributes["switchboard.gate.judge"] == "rejected"
    assert attributes["switchboard.gate.judge_score"] == 0.3


async def test_run_judge_skips_a_clarification_question() -> None:
    judge = FakeJudge()
    answer = AgentResult(
        answer="Which order do you mean?",
        usage=Usage(input_tokens=1, output_tokens=1),
        needs_clarification=True,
    )
    verdict = GateVerdict(passed=True, reason="all deterministic checks passed")

    outcome = await run_judge(judge, query="What about that one?", answer=answer, verdict=verdict)

    assert outcome.outcome == "skipped"
    assert outcome.verdict is verdict
    assert judge.requests == []


async def test_run_judge_skips_a_failed_layer_one_regardless_of_adapter() -> None:
    judge = FakeJudge()
    answer = AgentResult(answer="partial", usage=Usage(input_tokens=1, output_tokens=1))
    failed = evaluate_gate(
        query="q",
        answer=answer,
        evidence_agents=["data"],
        expected_sources=["data"],
        result_sources=["data"],
        merged=False,
    )

    outcome = await run_judge(judge, query="q", answer=answer, verdict=failed)

    assert outcome.outcome == "skipped"
    assert outcome.verdict is failed
    assert judge.requests == []
