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

# Both ids occur in the committed 1000-order sample, so this test behaves the
# same against the sample in CI and the full download locally.
ORDER_ID = "044bcd01474ba715e7cb11d5a70ccf0e"


def mint(tenant_id: str = "default", ttl_s: int = 60) -> tuple[str, str]:
    run_id = str(uuid.uuid4())
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "switchboard-mcp",
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


async def test_lists_the_six_manifest_tools() -> None:
    token, _ = mint()
    async with mcp_session(token) as session:
        tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == {
        "orders_summary@1",
        "orders_late@1",
        "order_lookup@1",
        "top_products@1",
        "describe_schema@1",
        "query_readonly_sql@1",
    }


async def test_calls_every_allowed_tool_and_writes_audit_rows() -> None:
    # The sixth manifest tool, query_readonly_sql@1, is disabled for the demo
    # tenants; its denial is covered in tests/integration/test_sql_tool.py.
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
                # The sample has 65 late orders; against the full download this
                # branch also proves the 200-row cap.
                assert body["total_rows"] > 0
                assert len(body["rows"]) == min(body["total_rows"], 200)

    rows = await audit_rows(run_id)
    assert [row["tool_name"] for row in rows] == [name.split("@")[0] for name in calls]
    assert all(row["status"] == "ok" for row in rows)
    assert all(row["tenant_id"] == "default" for row in rows)


async def test_missing_forged_and_expired_tokens_are_rejected() -> None:
    forged = jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "switchboard-mcp",
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


# Both ids are looked up in the loaded Olist tables, so the test works against
# the 1000-order sample in CI and the full download locally.
SELLER_ID = "955fee9216a65b617aa5c0531780ce60"
SELLER_RANGE_SQL = """
    o.order_purchase_timestamp >= '2017-01-01'::date
    AND o.order_purchase_timestamp < '2018-09-01'::date
"""


async def seller_fixtures() -> dict[str, Any]:
    """One order the seller owns, one they do not, and the expected aggregates."""
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        seller_order = await connection.fetchval(
            "SELECT i.order_id FROM olist.order_items i WHERE i.seller_id = $1 "
            "ORDER BY i.order_id LIMIT 1",
            SELLER_ID,
        )
        other_order = await connection.fetchval(
            "SELECT o.order_id FROM olist.orders o WHERE NOT EXISTS ("
            "  SELECT 1 FROM olist.order_items i WHERE i.order_id = o.order_id "
            "  AND i.seller_id = $1) ORDER BY o.order_id LIMIT 1",
            SELLER_ID,
        )
        aggregates = await connection.fetchrow(
            f"""
            SELECT count(DISTINCT o.order_id)::int AS orders,
                   count(*)::int AS items,
                   coalesce(sum(i.price), 0)::float AS revenue
            FROM olist.orders o
            JOIN olist.order_items i ON i.order_id = o.order_id
            WHERE i.seller_id = $1 AND {SELLER_RANGE_SQL}
            """,
            SELLER_ID,
        )
        products = await connection.fetchval(
            f"""
            SELECT count(DISTINCT i.product_id)::int
            FROM olist.orders o
            JOIN olist.order_items i ON i.order_id = o.order_id
            WHERE i.seller_id = $1 AND {SELLER_RANGE_SQL}
            """,
            SELLER_ID,
        )
        top_product = await connection.fetchrow(
            f"""
            SELECT i.product_id, count(*)::int AS value
            FROM olist.orders o
            JOIN olist.order_items i ON i.order_id = o.order_id
            WHERE i.seller_id = $1 AND {SELLER_RANGE_SQL}
            GROUP BY i.product_id
            ORDER BY value DESC, i.product_id
            LIMIT 1
            """,
            SELLER_ID,
        )
    finally:
        await connection.close()
    assert seller_order and other_order and aggregates and top_product
    return {
        "seller_order": seller_order,
        "other_order": other_order,
        "orders": int(aggregates["orders"]),
        "items": int(aggregates["items"]),
        "revenue": float(aggregates["revenue"]),
        "products": int(products or 0),
        "top_product": top_product["product_id"],
        "top_value": int(top_product["value"]),
    }


async def test_seller_tenant_cannot_see_another_sellers_orders() -> None:
    fixtures = await seller_fixtures()
    arguments = {"date_from": "2017-01-01", "date_to": "2018-08-31"}
    seller_token, seller_run = mint("seller_a")
    default_token, _ = mint("default")
    async with mcp_session(seller_token) as session:
        summary = payload(await session.call_tool("orders_summary@1", arguments))
        own = payload(
            await session.call_tool("order_lookup@1", {"order_id": fixtures["seller_order"]})
        )
        other = payload(
            await session.call_tool("order_lookup@1", {"order_id": fixtures["other_order"]})
        )
        top = payload(
            await session.call_tool("top_products@1", {**arguments, "metric": "count", "limit": 5})
        )
    async with mcp_session(default_token) as session:
        default_summary = payload(await session.call_tool("orders_summary@1", arguments))
        default_other = payload(
            await session.call_tool("order_lookup@1", {"order_id": fixtures["other_order"]})
        )
        default_top = payload(
            await session.call_tool("top_products@1", {**arguments, "metric": "count", "limit": 5})
        )

    # orders_summary counts and revenue are the seller's slice, not the market.
    assert summary["summary"]["orders"] == fixtures["orders"]
    assert summary["summary"]["items"] == fixtures["items"]
    assert summary["summary"]["revenue"] == pytest.approx(fixtures["revenue"], abs=0.01)
    assert summary["summary"]["orders"] < default_summary["summary"]["orders"]
    assert summary["summary"]["revenue"] < default_summary["summary"]["revenue"]

    # order_lookup returns nothing for an order the seller is not part of,
    # while the unrestricted tenant still sees that same order.
    assert other["summary"] == {"found": False}
    assert other["rows"] == []
    assert other["total_rows"] == 0
    assert default_other["summary"]["found"] is True

    # An owned order never exposes another seller's items.
    assert own["summary"]["found"] is True
    assert own["rows"]
    assert all(row["seller_id"] == SELLER_ID for row in own["rows"])

    # top_products ranks only the seller's catalogue.
    assert top["summary"]["products"] == fixtures["products"]
    assert top["summary"]["products"] < default_top["summary"]["products"]
    assert top["rows"][0]["product_id"] == fixtures["top_product"]
    assert top["rows"][0]["value"] == fixtures["top_value"]

    rows = await audit_rows(seller_run)
    assert [row["tenant_id"] for row in rows] == ["seller_a"] * len(rows)


async def test_seller_fields_in_arguments_are_ignored() -> None:
    token, run_id = mint("seller_a")
    arguments = {"date_from": "2017-01-01", "date_to": "2018-08-31"}
    async with mcp_session(token) as session:
        baseline = payload(await session.call_tool("orders_summary@1", arguments))
        spoofed = payload(
            await session.call_tool(
                "orders_summary@1",
                {
                    **arguments,
                    "tenant_id": "default",
                    "seller_id": "some-other-seller",
                    "seller_ids": ["some-other-seller"],
                },
            )
        )
    assert spoofed["summary"] == baseline["summary"]
    assert spoofed["total_rows"] == baseline["total_rows"]
    rows = await audit_rows(run_id)
    assert len(rows) == 2
    assert all(row["tenant_id"] == "seller_a" for row in rows)
