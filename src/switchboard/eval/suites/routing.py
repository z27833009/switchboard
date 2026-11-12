"""Routing suite: one plan decision per case, valid plan scored against the label.

The suite runs one of the two orchestrators' plan decisions on each case, so the
same dataset scores both adapters on the plan label (AC-16b):

- ``langgraph`` (the default, unchanged): the production supervisor request of
  design.md section 6.3, the same system prompt, agent list and plan schema.
- ``agent_loop``: the first step of the agent loop (design.md section 6.9), the
  production loop prompt, the tenant's read-only tools plus the platform tools
  ``result_page@1`` and ``search_docs@1``. The plan is derived from the tool
  calls in that decision with :func:`switchboard.agent_loop.derive_plan`, the
  same function the adapter uses, so it is a real measurement of the model's
  choice rather than a hand-written label.

Neither mode runs agents or executes tools: the supervisor mode never did, and
the agent-loop mode stops after the first decision. A live loop can call more
tools on later steps, so the plan a whole run would earn is a superset of the
first decision's tools: with exact-set scoring the first-decision number can
land above or below the live loop's. The report states which measurement it is,
and T2.12 makes the mechanism comparison.

A dataset version freezes the agent universe it measures: the supervisor mode
offers only agents named in that version's labels. A newly registered agent
(``clarify`` in v1) therefore cannot move a frozen regression number by growing
the prompt; it enters the measurement with the next dataset version, which is
recorded deliberately. The prompt helpers themselves are unchanged, so the v1
supervisor request is byte-identical to the T1.17a fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from switchboard.adapters.tools.platform import search_docs_spec
from switchboard.agent_loop import derive_plan, loop_request
from switchboard.agents.results import result_page_spec
from switchboard.core.errors import ConfigError, PlanValidationError
from switchboard.core.models import (
    AgentSpec,
    CompletionRequest,
    EvalCase,
    EvalOutcome,
    Message,
    RoutingDecision,
    Score,
    ToolSpec,
)
from switchboard.core.ports import EvalDeps
from switchboard.core.validation import validate_plan
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.scorers.routing import score_routing
from switchboard.eval.suite import SuiteContext
from switchboard.routing import SUPERVISOR_SYSTEM, plan_schema, supervisor_prompt

SUPERVISOR_MODE = "langgraph"
AGENT_LOOP_MODE = "agent_loop"
MODES = (SUPERVISOR_MODE, AGENT_LOOP_MODE)


def build_routing_request(
    query: str,
    available: list[AgentSpec],
    max_plan_size: int,
) -> CompletionRequest:
    names = [spec.name for spec in available]
    return CompletionRequest(
        messages=[
            Message(role="system", content=SUPERVISOR_SYSTEM),
            Message(role="user", content=supervisor_prompt(available, query)),
        ],
        response_schema=plan_schema(names, max_plan_size),
        temperature=0.0,
        max_tokens=256,
        metadata={"purpose": "supervisor.plan"},
    )


class RoutingSuite:
    name = "routing"

    def __init__(
        self,
        *,
        agent_specs: list[AgentSpec],
        max_plan_size: int,
        dataset_root: Path = DEFAULT_DATASET_ROOT,
        orchestrator: str = SUPERVISOR_MODE,
        tenant_tools: list[ToolSpec] | None = None,
        retriever_configured: bool = True,
    ) -> None:
        if orchestrator not in MODES:
            raise ConfigError(
                f"the routing suite cannot score the {orchestrator!r} orchestrator; "
                f"supported: {', '.join(MODES)}"
            )
        self._specs = sorted(agent_specs, key=lambda spec: spec.name)
        self._max_plan_size = max_plan_size
        self._dataset_root = Path(dataset_root)
        self._orchestrator = orchestrator
        self._tenant_tools = sorted(
            (spec for spec in (tenant_tools or []) if spec.risk == "read"),
            key=lambda spec: spec.wire_name,
        )
        self._retriever_configured = retriever_configured
        # Set by ``load``; None means the dataset has not named its agents yet
        # and every registered spec is offered (the pre-load test path).
        self._labelled: set[str] | None = None

    @classmethod
    def from_context(cls, context: SuiteContext) -> RoutingSuite:
        if not context.agent_specs:
            raise ConfigError("the routing suite needs at least one registered agent")
        return cls(
            agent_specs=context.agent_specs,
            max_plan_size=context.max_plan_size,
            dataset_root=context.dataset_root,
            orchestrator=context.orchestrator,
            tenant_tools=context.tenant_tools,
            retriever_configured=context.retriever_configured,
        )

    def load(self, version: str) -> list[EvalCase]:
        cases = load_cases(self.name, version, self._dataset_root)
        self._labelled = {name for case in cases for name in case.expected_agents}
        return cases

    def _available(self) -> list[AgentSpec]:
        if self._labelled is None:
            return self._specs
        selected = [spec for spec in self._specs if spec.name in self._labelled]
        if not selected:
            raise ConfigError(
                "the routing dataset labels no agent this tenant allows; "
                f"labelled: {', '.join(sorted(self._labelled)) or 'none'}"
            )
        return selected

    def _agent_loop_tools(self) -> list[ToolSpec]:
        """The tools the first loop step offers (design.md sections 6.9, 7.5)."""
        tools = [*self._tenant_tools, result_page_spec()]
        if self._retriever_configured:
            tools.append(search_docs_spec())
        return sorted(tools, key=lambda spec: spec.wire_name)

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        if self._orchestrator == AGENT_LOOP_MODE:
            return await self._execute_agent_loop(case, deps)
        return await self._execute_supervisor(case, deps)

    async def _execute_supervisor(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        available = self._available()
        request = build_routing_request(case.query, available, self._max_plan_size)
        response = await deps.provider.complete(request)
        parsed: dict[str, Any] = response.parsed or {}
        details: dict[str, Any] = {"raw": parsed, "mode": SUPERVISOR_MODE}
        by_name = {spec.name: spec for spec in available}
        try:
            decision = RoutingDecision.model_validate(parsed)
            validate_plan(decision, agents=by_name, max_size=self._max_plan_size)
        except ValidationError as exc:
            details["invalid"] = "model output did not match the plan schema"
            return EvalOutcome(
                case_id=case.id,
                answer=response.text,
                details=details,
                usage=response.usage,
                latency_ms=response.latency_ms,
                error=f"plan schema validation failed: {exc}",
            )
        except PlanValidationError as exc:
            details["invalid"] = "; ".join(exc.reasons)
            return EvalOutcome(
                case_id=case.id,
                answer=response.text,
                details=details,
                usage=response.usage,
                latency_ms=response.latency_ms,
                error="plan validation failed: " + "; ".join(exc.reasons),
            )

        details["reason"] = decision.reason
        return EvalOutcome(
            case_id=case.id,
            answer=response.text,
            plan=decision.agents,
            details=details,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )

    async def _execute_agent_loop(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        tools = self._agent_loop_tools()
        request = loop_request(
            case.query,
            tools,
            max_tokens=min(4096, deps.provider.capabilities().context_window // 2),
        )
        response = await deps.provider.complete(request)
        calls = [call.name for call in response.tool_calls]
        decision = derive_plan(calls, [spec.wire_name for spec in tools])
        details: dict[str, Any] = {
            "mode": AGENT_LOOP_MODE,
            "tools": [spec.wire_name for spec in tools],
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments} for call in response.tool_calls
            ],
            "reason": decision.reason,
        }
        return EvalOutcome(
            case_id=case.id,
            answer=response.text,
            plan=decision.agents,
            tool_calls=calls,
            details=details,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        return score_routing(case, outcome)


__all__ = [
    "AGENT_LOOP_MODE",
    "MODES",
    "SUPERVISOR_MODE",
    "RoutingSuite",
    "build_routing_request",
]
