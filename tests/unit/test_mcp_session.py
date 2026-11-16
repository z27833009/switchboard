"""V3.11: one MCP session per run and source, and list-changed invalidation."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, ClassVar

from mcp.types import CallToolResult, TextContent, ToolListChangedNotification
from mcp.types import Tool as McpTool

from switchboard.adapters.tools import mcp_client
from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.core.models import RunContext, TenantContext


class FakeHttpClient:
    async def __aenter__(self) -> FakeHttpClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class FakeSession:
    instances: ClassVar[list[FakeSession]] = []

    def __init__(self, *_: Any, **kwargs: Any) -> None:
        self.message_handler = kwargs["message_handler"]
        self.initialize_count = 0
        self.list_count = 0
        self.call_count = 0
        self.closed = False
        self.tools = [McpTool(name="orders_summary@1", description="summary", input_schema={})]
        FakeSession.instances.append(self)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.closed = True

    async def initialize(self) -> None:
        self.initialize_count += 1

    async def list_tools(self) -> Any:
        self.list_count += 1
        return type("Result", (), {"tools": list(self.tools)})()

    async def call_tool(self, name: str, args: dict[str, Any]) -> CallToolResult:
        self.call_count += 1
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps({"total_rows": 0, "rows": [], "summary": {}}),
                )
            ]
        )

    async def tools_changed(self) -> None:
        await self.message_handler(ToolListChangedNotification())


def tenant(*, allowed: set[str] | None = None) -> TenantContext:
    return TenantContext(
        tenant_id="default",
        allowed_agents={"data"},
        allowed_tools=allowed or {"orders_summary@1"},
        provider="lmstudio",
    )


def context(*, allowed: set[str] | None = None) -> RunContext:
    return RunContext(
        run_id=str(uuid.uuid4()),
        tenant=tenant(allowed=allowed),
        run_token="signed-token",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def patch_transport(monkeypatch: Any) -> None:
    FakeSession.instances = []

    def create_client(**_: Any) -> FakeHttpClient:
        return FakeHttpClient()

    @asynccontextmanager
    async def stream(_: str, *, http_client: Any) -> Any:
        yield (None, None)

    monkeypatch.setattr(mcp_client, "create_mcp_http_client", create_client)
    monkeypatch.setattr(mcp_client, "streamable_http_client", stream)
    monkeypatch.setattr(mcp_client, "ClientSession", FakeSession)


async def test_three_calls_share_one_initialize(monkeypatch: Any) -> None:
    patch_transport(monkeypatch)
    source = McpToolSource(url="http://mcp.test:8001")
    ctx = context()
    assert len(await source.list_tools(ctx)) == 1
    for _ in range(3):
        await source.call("orders_summary@1", {}, ctx)

    assert len(FakeSession.instances) == 1
    session = FakeSession.instances[0]
    assert session.initialize_count == 1
    assert session.list_count == 1
    assert session.call_count == 3
    assert not session.closed

    await source.close_run(ctx.run_id)
    assert session.closed


async def test_each_run_gets_its_own_session(monkeypatch: Any) -> None:
    patch_transport(monkeypatch)
    source = McpToolSource(url="http://mcp.test:8001")
    first = context()
    second = context()
    await source.list_tools(first)
    await source.list_tools(second)
    assert len(FakeSession.instances) == 2
    assert all(session.initialize_count == 1 for session in FakeSession.instances)
    await source.close_run(first.run_id)
    await source.close_run(second.run_id)


async def test_list_changed_forces_a_relist(monkeypatch: Any) -> None:
    patch_transport(monkeypatch)
    source = McpToolSource(url="http://mcp.test:8001")
    ctx = context(allowed={"orders_summary@1", "orders_new@1"})
    assert [spec.wire_name for spec in await source.list_tools(ctx)] == ["orders_summary@1"]

    session = FakeSession.instances[0]
    session.tools.append(McpTool(name="orders_new@1", description="new", input_schema={}))
    await session.tools_changed()
    names = [spec.wire_name for spec in await source.list_tools(ctx)]

    assert names == ["orders_summary@1", "orders_new@1"]
    assert session.list_count == 2
    await source.close_run(ctx.run_id)
