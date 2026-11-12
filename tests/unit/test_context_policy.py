"""ClearOldestToolResults from design.md section 8.6."""

from switchboard.core.context import (
    ClearOldestToolResults,
    cleared_marker,
    estimate_message_tokens,
)
from switchboard.core.models import ContextBudget, Message

POLICY = ClearOldestToolResults()


def tool(result_id: str, chars: int, rows: int = 42, cleared: bool = False) -> Message:
    return Message(
        role="tool",
        content="x" * chars,
        tool_call_id=f"call_{result_id}",
        result_id=result_id,
        total_rows=rows,
        cleared=cleared,
    )


def budget(fill_ratio: float = 0.7) -> ContextBudget:
    return ContextBudget(context_window=1000, max_tokens=0, fill_ratio=fill_ratio)


def total_tokens(messages: list[Message]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def test_under_the_limit_is_returned_unchanged() -> None:
    messages = [
        Message(role="system", content="be brief"),
        Message(role="user", content="how many?"),
        tool("r1", 30),
    ]
    result = POLICY.fit(messages, budget())
    assert result.messages == messages
    assert result.compacted is False
    assert result.cleared_results == 0


def test_oldest_result_is_cleared_first_and_system_is_untouched() -> None:
    system = Message(role="system", content="s" * 100)
    first = tool("r1", 1800)
    second = tool("r2", 300)
    latest = tool("r3", 300)
    result = POLICY.fit([system, first, second, latest], budget())

    assert result.compacted is True
    assert result.cleared_results == 1
    assert result.messages[0] == system
    assert result.messages[1].cleared is True
    assert result.messages[1].content == "[result r1: 42 rows, cleared]"
    assert result.messages[2] == second
    assert result.messages[3] == latest
    assert result.estimated_tokens == total_tokens(result.messages)


def test_multiple_results_are_cleared_until_the_budget_fits() -> None:
    messages = [
        Message(role="system", content="s" * 100),
        tool("r1", 1200),
        tool("r2", 1200),
        tool("r3", 100),
    ]
    result = POLICY.fit(messages, budget(fill_ratio=0.3))
    assert result.cleared_results == 2
    assert result.messages[1].cleared is True
    assert result.messages[2].cleared is True
    assert result.messages[3] == messages[3]
    assert result.estimated_tokens <= result.limit_tokens


def test_latest_result_is_never_cleared_even_when_still_over_budget() -> None:
    messages = [
        Message(role="system", content="s" * 100),
        tool("r1", 100),
        tool("r2", 3000),
    ]
    result = POLICY.fit(messages, budget())
    assert result.messages[2] == messages[2]
    assert result.messages[2].cleared is False
    assert result.estimated_tokens > result.limit_tokens


def test_no_tool_results_means_no_compaction() -> None:
    messages = [
        Message(role="system", content="s" * 3000),
        Message(role="user", content="u" * 3000),
    ]
    result = POLICY.fit(messages, budget())
    assert result.messages == messages
    assert result.compacted is False
    assert result.cleared_results == 0


def test_already_cleared_messages_are_skipped() -> None:
    messages = [
        Message(role="system", content="s" * 100),
        tool("r1", 1200, cleared=True),
        tool("r2", 1200),
        tool("r3", 100),
    ]
    result = POLICY.fit(messages, budget(fill_ratio=0.3))
    assert result.messages[1] == messages[1]
    assert result.messages[2].cleared is True
    assert result.cleared_results == 1


def test_marker_names_the_result_and_its_row_count() -> None:
    message = tool("r_7f3a", 10, rows=1834)
    assert cleared_marker(message) == "[result r_7f3a: 1834 rows, cleared]"
