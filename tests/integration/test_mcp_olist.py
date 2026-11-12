"""End-to-end MCP calls against the running Olist server.

Run with the compose stack up:

    uv run --env-file .env pytest tests/integration/test_mcp_olist.py -q

CI runs this once the PostgreSQL service exists (T1.16).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import asyncpg
import jwt
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import (  # type: ignore[attr-defined]
    create_mcp_http_client,
    streamable_http_client,
)

MCP_URL = os.environ.get("MCP_OLIST_URL", "").rstrip("/")
TOOLS_URL = os.environ.get("SWITCHBOARD_TOOLS_DATABASE_URL", "")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MCP_URL and TOOLS_URL and ADMIN_URL and SECRET),
        reason="set MCP_OLIST_URL and the database/secret variables",
    ),
]

ORDER_ID = "e481f51cbdc54678b7cc49136f2d6af7"
SELLER_A = "3442f8959a84dea7ee197c632cb2df15"


def mint(tenant_id: str = "default", ttl_s: int = 60) -> tuple[str, str]:
    run_id = str(uuid.uuid4())
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "mcp-olist",
            "tenant_id": tenant_id,
            "run_id": run_id,
            "iat": now,
            "exp": now + ttl_s,
            "jti": str(uuid.uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    return token, run_id


@asynccontextmanager
async def mcp_session(token: str | None) -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        create_mcp_http_client(headers=headers) as http_client,
        streamable_http_client(MCP_URL + "/mcp", http_client=http_client) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None)
    if structured:
        return cast(dict[str, Any], structured)
    return cast(dict[str, Any], json.loads(result.content[0].text))


def error_text(result: Any) -> str:
    return " ".join(getattr(item, "text", "") for item in result.content)


async def audit_rows(run_id: str) -> list[Any]:
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        rows: list[Any] = await connection.fetch(
            "SELECT tenant_id, tool_name, status FROM public.tool_audit "
            "WHERE run_id = $1 ORDER BY created_at",
            uuid.UUID(run_id),
        )
        return rows
    finally:
        await connection.close()


async def denied_unknown_count() -> int:
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        value = await connection.fetchval(
            "SELECT count(*)::int FROM public.tool_audit "
            "WHERE status = 'denied' AND tenant_id = 'unknown'"
        )
        return int(value or 0)
    finally:
        await connection.close()


async def test_lists_the_five_manifest_tools() -> None:
    token, _ = mint()
    async with mcp_session(token) as session:
        tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == {
        "orders_summary@1",
        "orders_late@1",
        "order_lookup@1",
        "top_products@1",
        "describe_schema@1",
    }


async def test_calls_every_tool_and_writes_audit_rows() -> None:
    token, run_id = mint()
    async with mcp_session(token) as session:
        calls: dict[str, dict[str, Any]] = {
            "orders_summary@1": {"date_from": "2018-03-01", "date_to": "2018-03-31"},
            "orders_late@1": {
                "date_from": "2017-01-01",
                "date_to": "2018-08-31",
            },
            "order_lookup@1": {"order_id": ORDER_ID},
            "top_products@1": {
                "date_from": "2018-03-01",
                "date_to": "2018-03-31",
                "metric": "count",
                "limit": 5,
            },
            "describe_schema@1": {},
        }
        for name, arguments in calls.items():
            result = await session.call_tool(name, arguments)
            assert result.is_error is False, error_text(result)
            body = payload(result)
            assert set(body) >= {"summary", "rows", "total_rows", "audit_id"}
            assert len(body["rows"]) <= 200
            uuid.UUID(body["audit_id"])
            if name == "orders_late@1":
                assert body["total_rows"] > 200
                assert len(body["rows"]) == 200

    rows = await audit_rows(run_id)
    assert [row["tool_name"] for row in rows] == [name.split("@")[0] for name in calls]
    assert all(row["status"] == "ok" for row in rows)
    assert all(row["tenant_id"] == "default" for row in rows)


async def test_missing_forged_and_expired_tokens_are_rejected() -> None:
    forged = jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "mcp-olist",
            "tenant_id": "default",
            "run_id": str(uuid.uuid4()),
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "jti": str(uuid.uuid4()),
        },
        "wrong-secret-" + "x" * 32,
        algorithm="HS256",
    )
    expired, _ = mint(ttl_s=-10)

    before = await denied_unknown_count()
    for token in (None, forged, expired):
        async with mcp_session(token) as session:
            result = await session.call_tool(
                "orders_summary@1",
                {"date_from": "2018-03-01", "date_to": "2018-03-31"},
            )
        assert result.is_error is True
        assert "authentication" in error_text(result).lower()
    assert await denied_unknown_count() >= before + 3


async def test_tenant_allowlist_and_seller_scope() -> None:
    token, run_id = mint("seller_a")
    async with mcp_session(token) as session:
        allowed = await session.call_tool(
            "orders_summary@1",
            {"date_from": "2017-01-01", "date_to": "2018-08-31"},
        )
        denied = await session.call_tool(
            "orders_late@1",
            {"date_from": "2018-03-01", "date_to": "2018-03-31"},
        )
    assert allowed.is_error is False, error_text(allowed)
    assert payload(allowed)["summary"]["orders"] >= 0
    assert denied.is_error is True
    assert "not allowed" in error_text(denied)

    rows = await audit_rows(run_id)
    statuses = {row["tool_name"]: row["status"] for row in rows}
    assert statuses["orders_summary"] == "ok"
    assert statuses["orders_late"] == "denied"


async def test_tenant_in_arguments_is_not_used() -> None:
    token, run_id = mint()
    async with mcp_session(token) as session:
        result = await session.call_tool(
            "orders_summary@1",
            {
                "date_from": "2018-03-01",
                "date_to": "2018-03-31",
                "tenant_id": "seller_a",
            },
        )
    # FastMCP drops unknown arguments; the dispatcher also strips identity keys.
    # The call stays on the token's default tenant.
    assert result.is_error is False, error_text(result)
    body = payload(result)
    assert body["summary"]["orders"] > 0
    rows = await audit_rows(run_id)
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


async def test_tool_role_cannot_write_olist_or_modify_audit() -> None:
    connection = await asyncpg.connect(TOOLS_URL)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                "INSERT INTO olist.customers (customer_id, customer_unique_id) "
                "VALUES ('probe', 'probe')"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("DELETE FROM public.tool_audit")
    finally:
        await connection.close()


async def test_seller_scope_is_applied_to_the_seller_tenant() -> None:
    default_token, _ = mint("default")
    seller_token, _ = mint("seller_a")
    arguments = {"date_from": "2017-01-01", "date_to": "2018-08-31"}
    async with mcp_session(default_token) as session:
        default = payload(await session.call_tool("orders_summary@1", arguments))
    async with mcp_session(seller_token) as session:
        seller = payload(await session.call_tool("orders_summary@1", arguments))
    assert 0 < seller["summary"]["orders"] < default["summary"]["orders"]
    assert seller["summary"]["revenue"] < default["summary"]["revenue"]
