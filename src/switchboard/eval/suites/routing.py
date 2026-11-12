"""Routing suite: one supervisor completion per case, validated and scored.

The request is the production supervisor request (design.md section 6.3): the
same system prompt, the same prompt built from the registered agent specs and
the same plan schema. The agents are not run, so all 60 cases stay fast.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from switchboard.core.errors import ConfigError, PlanValidationError
from switchboard.core.models import (
    AgentSpec,
    CompletionRequest,
    EvalCase,
    EvalOutcome,
    Message,
    RoutingDecision,
    Score,
)
from switchboard.core.ports import EvalDeps
from switchboard.core.validation import validate_plan
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.scorers.routing import score_routing
from switchboard.eval.suite import SuiteContext
from switchboard.routing import SUPERVISOR_SYSTEM, plan_schema, supervisor_prompt


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
    ) -> None:
        self._specs = sorted(agent_specs, key=lambda spec: spec.name)
        self._by_name: dict[str, AgentSpec] = {spec.name: spec for spec in self._specs}
        self._max_plan_size = max_plan_size
        self._dataset_root = Path(dataset_root)

    @classmethod
    def from_context(cls, context: SuiteContext) -> RoutingSuite:
        if not context.agent_specs:
            raise ConfigError("the routing suite needs at least one registered agent")
        return cls(
            agent_specs=context.agent_specs,
            max_plan_size=context.max_plan_size,
            dataset_root=context.dataset_root,
        )

    def load(self, version: str) -> list[EvalCase]:
        return load_cases(self.name, version, self._dataset_root)

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        request = build_routing_request(case.query, self._specs, self._max_plan_size)
        response = await deps.provider.complete(request)
        parsed: dict[str, Any] = response.parsed or {}
        details: dict[str, Any] = {"raw": parsed}
        try:
            decision = RoutingDecision.model_validate(parsed)
            validate_plan(decision, agents=self._by_name, max_size=self._max_plan_size)
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

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        return score_routing(case, outcome)


__all__ = ["RoutingSuite", "build_routing_request"]
