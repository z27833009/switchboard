"""MCP tool fingerprints and the runtime pinning wrapper (V3.10)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from switchboard.adapters.tools.mcp_client import PinnedToolSource
from switchboard.core.errors import ToolPermissionError
from switchboard.core.models import ToolSpec
from switchboard.core.tool_pinning import tool_fingerprint


def spec(description: str = "Look up one order.") -> ToolSpec:
    return ToolSpec(
        name="order_lookup",
        version="1",
        description=description,
        input_schema={"type": "object", "properties": {"order_id": {"type": "string"}}},
    )


def test_fingerprint_is_stable_and_covers_the_description() -> None:
    first = tool_fingerprint(spec())
    assert first == tool_fingerprint(spec())
    assert first != tool_fingerprint(spec("Look up one order with items."))
    assert len(first) == 64


class _Source:
    name = "olist"

    def __init__(self, tools: list[ToolSpec]) -> None:
        self.tools = tools

    async def list_tools(self, ctx: Any) -> list[ToolSpec]:
        return list(self.tools)

    async def call(self, name: str, args: dict[str, Any], ctx: Any) -> Any:
        return {"name": name, "args": args}


async def test_pinned_source_hides_and_denies_a_changed_tool() -> None:
    original = spec()
    changed = spec("The description changed.")
    pinned = PinnedToolSource(
        cast(Any, _Source([original])),
        {"order_lookup@1": tool_fingerprint(original)},
    )
    ctx = cast(Any, None)

    assert [item.wire_name for item in await pinned.list_tools(ctx)] == ["order_lookup@1"]

    changed_source = PinnedToolSource(
        cast(Any, _Source([changed])),
        {"order_lookup@1": tool_fingerprint(original)},
    )
    assert await changed_source.list_tools(ctx) == []
    with pytest.raises(ToolPermissionError, match="not approved"):
        await changed_source.call("order_lookup@1", {}, ctx)
