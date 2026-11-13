"""End-to-end calls against the running reviews MCP server (T3.08).

Run with the compose stack up:

    uv run --env-file .env pytest tests/integration/test_mcp_reviews.py -q

The test mints real run tokens with the API helper, so it exercises the same
audience and claims the platform uses. The sample loaded in CI has seller_a
items, so the same assertions hold against the 1000-order sample and the full
download.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, cast

import asyncpg
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import (  # type: ignore[attr-defined]
    create_mcp_http_client,
    streamable_http_client,
)

from switchboard.api.run_tokens import mint_run_token

MCP_URL = os.environ.get("MCP_REVIEWS_URL", "").rstrip("/")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MCP_URL and ADMIN_URL and SECRET),
        reason="set MCP_REVIEWS_URL and the database/secret variables",
    ),
]

SELLER_ID = "955fee9216a65b617aa5c0531780ce60"
DATE_FROM = "2017-01-01"
DATE_TO = "2018-08-31"


def mint(tenant_id: str = "seller_a", ttl_s: int = 60) -> tuple[str, str]:
    run_id = str(uuid.uuid4())
    token = mint_run_token(
        SECRET,
        tenant_id=tenant_id,
        run_id=run_id,
        run_timeout_s=ttl_s,
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


async def fixtures() -> dict[str, Any]:
    """Expected aggregates and one owned and one foreign order from the loaded data."""
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        aggregates = await connection.fetchrow(
            """
            WITH scoped AS (
                SELECT DISTINCT o.order_id
                FROM olist.orders o
                JOIN olist.order_items i ON i.order_id = o.order_id
                WHERE i.seller_id = $1
                  AND o.order_purchase_timestamp >= $2::date
                  AND o.order_purchase_timestamp < ($3::date + interval '1 day')
            )
            SELECT count(*)::int AS reviews,
                   coalesce(round(avg(r.review_score)::numeric, 4), 0)::float AS average,
                   count(*) FILTER (WHERE r.review_score <= 2)::int AS low
            FROM olist.order_reviews r
            JOIN scoped s ON s.order_id = r.order_id
            """,
            SELLER_ID,
            date.fromisoformat(DATE_FROM),
            date.fromisoformat(DATE_TO),
        )
        owned_order = await connection.fetchval(
            """
            SELECT r.order_id
            FROM olist.order_reviews r
            JOIN olist.order_items i ON i.order_id = r.order_id
            WHERE i.seller_id = $1
            ORDER BY r.order_id
            LIMIT 1
            """,
            SELLER_ID,
        )
        owned_reviews = await connection.fetchval(
            "SELECT count(*)::int FROM olist.order_reviews WHERE order_id = $1",
            owned_order,
        )
        foreign_order = await connection.fetchval(
            """
            SELECT o.order_id
            FROM olist.orders o
            WHERE EXISTS (
                    SELECT 1 FROM olist.order_reviews r WHERE r.order_id = o.order_id
                  )
              AND NOT EXISTS (
                    SELECT 1 FROM olist.order_items i
                    WHERE i.order_id = o.order_id AND i.seller_id = $1
                  )
            ORDER BY o.order_id
            LIMIT 1
            """,
            SELLER_ID,
        )
    finally:
        await connection.close()
    assert owned_order and foreign_order
    return {
        "reviews": int(aggregates["reviews"]),
        "average": float(aggregates["average"]),
        "low": int(aggregates["low"]),
        "owned_order": owned_order,
        "owned_reviews": int(owned_reviews or 0),
        "foreign_order": foreign_order,
    }


def test_health_is_reachable() -> None:
    response = httpx.get(MCP_URL + "/health", timeout=5.0)
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "mcp-reviews"
    assert body["database"] is True
    assert body["tools"] == 2


def test_missing_and_forged_tokens_are_rejected_generically() -> None:
    for headers in ({}, {"Authorization": "Bearer forged.token.value"}):
        response = httpx.post(
            MCP_URL + "/mcp",
            headers={**headers, "content-type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            timeout=5.0,
        )
        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}


async def test_seller_a_lists_and_calls_both_review_tools() -> None:
    expected = await fixtures()
    token, run_id = mint()
    async with mcp_session(token) as session:
        tools = await session.list_tools()
        summary = await session.call_tool(
            "review_summary@1", {"date_from": DATE_FROM, "date_to": DATE_TO}
        )
        reviews = await session.call_tool("order_reviews@1", {"order_id": expected["owned_order"]})
    assert {tool.name for tool in tools.tools} == {"review_summary@1", "order_reviews@1"}

    assert summary.is_error is False, error_text(summary)
    body = payload(summary)
    assert set(body) >= {"summary", "rows", "total_rows", "audit_id"}
    assert body["summary"]["review_count"] == expected["reviews"]
    assert body["summary"]["average_score"] == pytest.approx(expected["average"], abs=1e-4)
    assert body["summary"]["low_score_count"] == expected["low"]
    distribution = body["summary"]["score_distribution"]
    assert set(distribution) == {"1", "2", "3", "4", "5"}
    assert sum(distribution.values()) == expected["reviews"]
    assert len(body["rows"]) == 5
    assert body["total_rows"] == expected["reviews"]

    assert reviews.is_error is False, error_text(reviews)
    review_body = payload(reviews)
    assert review_body["summary"]["found"] is True
    assert review_body["summary"]["reviews"] == expected["owned_reviews"]
    assert review_body["total_rows"] == expected["owned_reviews"]
    assert review_body["rows"]
    for row in review_body["rows"]:
        assert 1 <= row["score"] <= 5
        assert set(row) >= {"score", "title", "message", "created_at", "answered_at"}

    rows = await audit_rows(run_id)
    assert [row["tool_name"] for row in rows] == ["review_summary", "order_reviews"]
    assert all(row["status"] == "ok" for row in rows)
    assert all(row["tenant_id"] == "seller_a" for row in rows)


async def test_a_foreign_order_returns_a_clean_empty_result() -> None:
    expected = await fixtures()
    token, _ = mint()
    async with mcp_session(token) as session:
        result = await session.call_tool("order_reviews@1", {"order_id": expected["foreign_order"]})
    assert result.is_error is False, error_text(result)
    body = payload(result)
    assert body["summary"]["found"] is False
    assert body["summary"]["reviews"] == 0
    assert body["summary"]["average_score"] is None
    assert body["rows"] == []
    assert body["total_rows"] == 0


async def test_the_default_tenant_cannot_call_the_review_tools() -> None:
    token, run_id = mint("default")
    async with mcp_session(token) as session:
        result = await session.call_tool(
            "review_summary@1", {"date_from": DATE_FROM, "date_to": DATE_TO}
        )
    assert result.is_error is True
    assert "not allowed" in error_text(result).lower()
    rows = await audit_rows(run_id)
    assert len(rows) == 1
    assert (rows[0]["tool_name"], rows[0]["status"], rows[0]["tenant_id"]) == (
        "review_summary",
        "denied",
        "default",
    )


async def test_identity_arguments_are_rejected_not_trusted() -> None:
    expected = await fixtures()
    token, _ = mint()
    async with mcp_session(token) as session:
        spoofed = await session.call_tool(
            "review_summary@1",
            {
                "date_from": DATE_FROM,
                "date_to": DATE_TO,
                "tenant_id": "default",
                "seller_id": "some-other-seller",
            },
        )
    # The schema is strict, so an identity argument is a validation error and
    # the tenant scope stays the one in the token.
    assert spoofed.is_error is True
    assert "validation" in error_text(spoofed).lower() or "invalid" in error_text(spoofed).lower()
    assert expected["reviews"] >= 0
