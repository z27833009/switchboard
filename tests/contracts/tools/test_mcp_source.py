"""Contract tests for McpToolSource against the running Olist server.

    uv run --env-file .env pytest tests/contracts/tools/test_mcp_source.py -q

Skipped in CI until T1.16 adds the PostgreSQL service and the server.
"""

from __future__ import annotations

import os
import time
import uuid

import jwt
import pytest

from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.core.errors import RunTokenError, ToolPermissionError
from switchboard.core.models import RunContext, TenantContext

MCP_URL = os.environ.get("MCP_OLIST_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (MCP_URL and SECRET), reason="set MCP_OLIST_URL and the token secret"),
]

ALL_TOOLS = {
    "orders_summary@1",
    "orders_late@1",
    "order_lookup@1",
    "top_products@1",
    "describe_schema@1",
}


def mint(tenant_id: str = "default", secret: str = SECRET) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "mcp-olist",
            "tenant_id": tenant_id,
            "run_id": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 60,
            "jti": str(uuid.uuid4()),
        },
        secret,
        algorithm="HS256",
    )


def context(tenant_id: str, allowed_tools: set[str], token: str | None = None) -> RunContext:
    tenant = TenantContext(
        tenant_id=tenant_id,
        allowed_agents={"data"},
        allowed_tools=allowed_tools,
        provider="lmstudio",
        seller_ids=["3442f8959a84dea7ee197c632cb2df15"] if tenant_id == "seller_a" else None,
    )
    return RunContext(
        run_id=str(uuid.uuid4()),
        tenant=tenant,
        run_token=token or mint(tenant_id),
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


async def test_lists_only_the_tenant_tools() -> None:
    source = McpToolSource(url=MCP_URL)
    default = await source.list_tools(context("default", ALL_TOOLS))
    assert {spec.wire_name for spec in default} == ALL_TOOLS
    describe = next(spec for spec in default if spec.name == "describe_schema")
    assert describe.tenant_scoped is False
    assert all(spec.risk == "read" for spec in default)

    seller = await source.list_tools(context("seller_a", {"orders_summary@1", "order_lookup@1"}))
    assert {spec.wire_name for spec in seller} == {
        "orders_summary@1",
        "order_lookup@1",
    }


async def test_calls_a_tool_and_returns_a_tool_result() -> None:
    source = McpToolSource(url=MCP_URL)
    ctx = context("default", ALL_TOOLS)
    result = await source.call(
        "orders_summary@1",
        {"date_from": "2018-03-01", "date_to": "2018-03-31"},
        ctx,
    )
    assert result.tool == "orders_summary@1"
    assert result.version == "1"
    assert result.total_rows >= 0
    assert result.audit_id
    uuid.UUID(result.audit_id)
    assert result.duration_ms >= 0


async def test_server_denial_maps_to_tool_permission_error() -> None:
    source = McpToolSource(url=MCP_URL)
    ctx = context("seller_a", {"orders_summary@1", "order_lookup@1"})
    with pytest.raises(ToolPermissionError):
        await source.call(
            "orders_late@1",
            {"date_from": "2018-03-01", "date_to": "2018-03-31"},
            ctx,
        )


async def test_forged_token_maps_to_run_token_error() -> None:
    source = McpToolSource(url=MCP_URL)
    ctx = context("default", ALL_TOOLS, token=mint(secret="wrong-" + "x" * 32))
    with pytest.raises(RunTokenError):
        await source.call(
            "orders_summary@1",
            {"date_from": "2018-03-01", "date_to": "2018-03-31"},
            ctx,
        )
