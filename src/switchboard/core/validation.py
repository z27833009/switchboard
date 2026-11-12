"""Plan validation for the supervisor (design.md section 6.3).

The model output is untrusted: the supervisor may return an empty plan, a
duplicate, an agent the tenant cannot use, too many agents, or an exclusive
agent next to another one. All of those become one ``PlanValidationError``
whose reasons drive the single repair call.
"""

from __future__ import annotations

from collections.abc import Mapping

from switchboard.core.errors import PlanValidationError
from switchboard.core.models import AgentSpec, RoutingDecision


def validate_plan(
    decision: RoutingDecision,
    *,
    agents: Mapping[str, AgentSpec],
    max_size: int,
) -> None:
    """Raise ``PlanValidationError`` unless the plan may run for this tenant."""
    reasons: list[str] = []
    if not decision.agents:
        reasons.append("plan is empty")
    if len(decision.agents) != len(set(decision.agents)):
        reasons.append("plan contains duplicate agents")
    if len(decision.agents) > max_size:
        reasons.append(f"plan has {len(decision.agents)} agents, limit is {max_size}")

    unknown = sorted(set(decision.agents) - set(agents))
    if unknown:
        reasons.append(f"unknown agents: {', '.join(unknown)}")

    exclusive = sorted(
        agent.name
        for name in decision.agents
        if (agent := agents.get(name)) is not None and agent.exclusive
    )
    if exclusive and len(decision.agents) > 1:
        reasons.append(f"exclusive agents must run alone: {', '.join(exclusive)}")

    if reasons:
        raise PlanValidationError(reasons)


def plan_is_cacheable(decision: RoutingDecision, agents: Mapping[str, AgentSpec]) -> bool:
    """A plan is cacheable when every agent in it is cacheable."""
    if not decision.agents:
        return False
    return all(name in agents and agents[name].cacheable for name in decision.agents)
