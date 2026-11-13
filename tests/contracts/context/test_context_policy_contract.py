"""ContextPolicy contract for ``ClearOldestToolResults`` (ADR 0012).

The policy may only replace the body of an old tool result with its cleared
marker. It never adds, removes or reorders a message, never touches the system
prompt or the newest tool result, and reports an estimate it can explain. Under
the budget it returns the input unchanged.
"""

from __future__ import annotations

import pytest
from tests.contracts.support import policy_under_test

from switchboard.core.context import estimate_message_tokens
from switchboard.core.models import ContextBudget, FitResult, Message
from switchboard.core.ports import ContextPolicy


def tool(result_id: str, chars: int, rows: int = 42) -> Message:
    return Message(
        role="tool",
        content="x" * chars,
        tool_call_id=f"call_{result_id}",
        result_id=result_id,
        total_rows=rows,
    )


def budget(fill_ratio: float = 0.7) -> ContextBudget:
    return ContextBudget(context_window=1000, max_tokens=0, fill_ratio=fill_ratio)


def total(messages: list[Message]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


@pytest.fixture
def policy() -> ContextPolicy:
    return policy_under_test()


async def test_under_the_budget_the_messages_are_unchanged(policy: ContextPolicy) -> None:
    messages = [
        Message(role="system", content="be brief"),
        Message(role="user", content="how many?"),
        tool("r1", 30),
    ]
    result = policy.fit(messages, budget())
    assert isinstance(result, FitResult)
    assert result.messages == messages
    assert result.compacted is False
    assert result.cleared_results == 0
    assert result.estimated_tokens == total(messages)
    assert result.estimated_tokens <= result.limit_tokens


async def test_the_system_prompt_and_the_newest_tool_result_are_never_touched(
    policy: ContextPolicy,
) -> None:
    system = Message(role="system", content="s" * 100)
    user = Message(role="user", content="How many orders were late?")
    first = tool("r1", 1200)
    second = tool("r2", 1200)
    latest = tool("r3", 300)
    result = policy.fit([system, user, first, second, latest], budget(fill_ratio=0.3))

    assert result.compacted is True
    assert result.messages[0] == system
    assert result.messages[1] == user
    assert result.messages[-1] == latest
    assert result.messages[-1].cleared is False
    assert result.messages[2].cleared is True
    # Oldest first: the second result is only cleared when the budget still
    # needs it.
    assert result.messages[3].cleared is True
    assert result.cleared_results == 2
    assert result.estimated_tokens == total(result.messages)


async def test_messages_are_never_added_removed_or_reordered(policy: ContextPolicy) -> None:
    messages = [
        Message(role="system", content="s" * 100),
        tool("r1", 900),
        Message(role="user", content="u" * 100),
        tool("r2", 900),
        tool("r3", 50),
    ]
    result = policy.fit(messages, budget(fill_ratio=0.2))
    assert len(result.messages) == len(messages)
    assert [message.role for message in result.messages] == [message.role for message in messages]
    for before, after in zip(messages, result.messages, strict=True):
        if before.role != "tool" or after.cleared:
            assert after.tool_call_id == before.tool_call_id
            assert after.result_id == before.result_id


async def test_a_budget_that_cannot_be_met_still_leaves_the_newest_result(
    policy: ContextPolicy,
) -> None:
    messages = [
        Message(role="system", content="s" * 100),
        tool("r1", 100),
        tool("r2", 3000),
    ]
    result = policy.fit(messages, budget())
    assert result.messages[-1] == messages[-1]
    assert result.messages[-1].cleared is False
    assert result.estimated_tokens > result.limit_tokens


async def test_no_tool_results_means_no_compaction(policy: ContextPolicy) -> None:
    messages = [
        Message(role="system", content="s" * 3000),
        Message(role="user", content="u" * 3000),
    ]
    result = policy.fit(messages, budget())
    assert result.messages == messages
    assert result.compacted is False
    assert result.cleared_results == 0


async def test_an_already_cleared_result_is_not_counted_twice(policy: ContextPolicy) -> None:
    already = tool("r1", 1200)
    already = already.model_copy(
        update={"content": "[result r1: 42 rows, cleared]", "cleared": True}
    )
    messages = [
        Message(role="system", content="s" * 100),
        already,
        tool("r2", 1200),
        tool("r3", 100),
    ]
    result = policy.fit(messages, budget(fill_ratio=0.3))
    assert result.messages[1] == already
    assert result.messages[2].cleared is True
    assert result.cleared_results == 1
