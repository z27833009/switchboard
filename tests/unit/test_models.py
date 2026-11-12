"""Domain model behaviour that other layers depend on."""

import pytest
from pydantic import ValidationError

from switchboard.core.models import (
    AgentSpec,
    CompletionRequest,
    ContextBudget,
    Message,
    RoutingDecision,
    ToolResult,
    Usage,
)


def test_routing_label_is_sorted_and_stable() -> None:
    assert RoutingDecision(agents=["data", "docs"], reason="both").label == "data+docs"
    assert RoutingDecision(agents=["docs", "data"], reason="both").label == "data+docs"
    assert RoutingDecision(agents=["direct"], reason="small talk").label == "direct"


def test_usage_keeps_cached_input_tokens_separate() -> None:
    usage = Usage(input_tokens=100, cached_input_tokens=80, output_tokens=10)
    assert usage.total_tokens == 110
    other = Usage(input_tokens=5, cached_input_tokens=1, output_tokens=2)
    assert usage.plus(other) == Usage(input_tokens=105, cached_input_tokens=81, output_tokens=12)


def test_agent_spec_rejects_multiline_description() -> None:
    with pytest.raises(ValidationError):
        AgentSpec(name="docs", description="line one\nline two", cacheable=True)


def test_agent_spec_rejects_an_uppercase_name() -> None:
    with pytest.raises(ValidationError):
        AgentSpec(name="Docs", description="policies", cacheable=True)


def test_tool_result_view_is_bounded_and_keeps_the_total() -> None:
    result = ToolResult(
        tool="orders_late@1",
        version="1",
        total_rows=500,
        rows=[{"order_id": str(index)} for index in range(500)],
        result_id="r_7f3a",
    )
    view = result.view(limit=20)
    assert len(view.rows_preview) == 20
    assert view.total_rows == 500
    assert view.truncated is True
    assert view.result_id == "r_7f3a"


def test_tool_result_view_requires_a_result_id() -> None:
    result = ToolResult(tool="orders_late@1", version="1", total_rows=0)
    with pytest.raises(ValueError, match="result id"):
        result.view(limit=20)


def test_context_budget_leaves_room_for_the_answer() -> None:
    budget = ContextBudget(context_window=8192, max_tokens=1024, fill_ratio=0.7)
    assert budget.limit_tokens == int((8192 - 1024) * 0.7)


def test_completion_request_keeps_the_delta_callback_out_of_its_dump() -> None:
    fragments: list[str] = []
    request = CompletionRequest(
        messages=[Message(role="user", content="hi")],
        stream=True,
        delta_callback=fragments.append,
    )

    dumped = request.model_dump()
    assert dumped["stream"] is True
    assert "delta_callback" not in dumped
    assert request.delta_callback is not None
    request.delta_callback("OK")
    assert fragments == ["OK"]
