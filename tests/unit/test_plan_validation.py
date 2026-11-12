"""Plan validation from design.md section 6.3."""

import pytest

from switchboard.core.errors import PlanValidationError
from switchboard.core.models import AgentSpec, RoutingDecision
from switchboard.core.validation import plan_is_cacheable, validate_plan

AGENTS = {
    "direct": AgentSpec(name="direct", description="small talk", cacheable=True, exclusive=True),
    "docs": AgentSpec(name="docs", description="policies", cacheable=True),
    "data": AgentSpec(name="data", description="orders", cacheable=False),
    "clarify": AgentSpec(name="clarify", description="ask back", cacheable=False, exclusive=True),
}


def test_valid_single_and_parallel_plans_pass() -> None:
    validate_plan(RoutingDecision(agents=["direct"], reason="hi"), agents=AGENTS, max_size=3)
    validate_plan(
        RoutingDecision(agents=["data", "docs"], reason="both"), agents=AGENTS, max_size=3
    )


def test_empty_plan_is_rejected() -> None:
    with pytest.raises(PlanValidationError, match="plan is empty"):
        validate_plan(RoutingDecision(agents=[], reason=""), agents=AGENTS, max_size=3)


def test_duplicate_and_unknown_agents_are_reported_together() -> None:
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(
            RoutingDecision(agents=["data", "data", "nope"], reason=""),
            agents=AGENTS,
            max_size=3,
        )
    assert caught.value.reasons == (
        "plan contains duplicate agents",
        "unknown agents: nope",
    )


def test_size_limit_is_enforced() -> None:
    with pytest.raises(PlanValidationError, match="limit is 1"):
        validate_plan(
            RoutingDecision(agents=["data", "docs"], reason=""), agents=AGENTS, max_size=1
        )


def test_exclusive_agent_must_run_alone() -> None:
    with pytest.raises(PlanValidationError, match="exclusive agents"):
        validate_plan(
            RoutingDecision(agents=["direct", "docs"], reason=""),
            agents=AGENTS,
            max_size=3,
        )


def test_plan_is_cacheable_only_when_every_agent_is() -> None:
    assert plan_is_cacheable(RoutingDecision(agents=["docs"], reason=""), AGENTS)
    assert not plan_is_cacheable(RoutingDecision(agents=["data", "docs"], reason=""), AGENTS)
    assert not plan_is_cacheable(RoutingDecision(agents=[], reason=""), AGENTS)
