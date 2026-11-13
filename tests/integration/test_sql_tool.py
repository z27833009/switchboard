"""The read-only SQL tool against the real database and MCP server (T3.05).

Three layers are exercised here:

- the fact view and its tenant filter, direct through the ``switchboard_tools``
  role and the tool function the MCP wrapper calls;
- the read-only transaction and the grants (the tool role may SELECT the view
  and nothing may write it, the API role may not read it);
- the MCP boundary: the demo tenants do not allow the tool, so it is hidden by
  ``McpToolSource``, a direct call is denied and audited, and a refused statement
  never reaches the database.

No model call is made. Run with the compose stack up:

    uv run --env-file .env pytest tests/integration/test_sql_tool.py -q
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import asyncpg
import jwt
import pytest
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import (  # type: ignore[attr-defined]
    create_mcp_http_client,
    streamable_http_client,
)
from mcp_servers.olist.db import ROW_CAP, ToolOutcome, query_readonly_sql, run_readonly
from mcp_servers.olist.settings import TenantSettings
from mcp_servers.olist.sql_guard import validate_readonly_sql

from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.core.models import RunContext, TenantContext

MCP_URL = os.environ.get("MCP_OLIST_URL", "").rstrip("/")
TOOLS_URL = os.environ.get("SWITCHBOARD_TOOLS_DATABASE_URL", "")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
APP_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")
REPO = Path(__file__).resolve().parents[2]
TENANTS_FILE = REPO / "config" / "tenants.yaml"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MCP_URL and TOOLS_URL and ADMIN_URL and APP_URL and SECRET),
        reason="set MCP_OLIST_URL and the database/secret variables",
    ),
]

SELLER_ID = "955fee9216a65b617aa5c0531780ce60"
FACT_SQL = "SELECT * FROM olist.sql_order_facts ORDER BY order_id, order_item_id"


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
async def mcp_session(token: str) -> AsyncIterator[ClientSession]:
    async with (
        create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http_client,
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


def tenant_settings(seller_ids: tuple[str, ...] | None) -> TenantSettings:
    return TenantSettings(
        allowed_tools=frozenset({"query_readonly_sql@1"}),
        seller_ids=seller_ids,
    )


async def expected_counts() -> tuple[int, int]:
    """Item rows in the view: every seller, then just ``SELLER_ID``."""
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        total = await connection.fetchval(
            "SELECT count(*)::int FROM olist.order_items i "
            "JOIN olist.orders o ON o.order_id = i.order_id"
        )
        seller = await connection.fetchval(
            "SELECT count(*)::int FROM olist.order_items i "
            "JOIN olist.orders o ON o.order_id = i.order_id WHERE i.seller_id = $1",
            SELLER_ID,
        )
    finally:
        await connection.close()
    return int(total or 0), int(seller or 0)


async def audit_rows(run_id: str) -> list[Any]:
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        rows: list[Any] = await connection.fetch(
            "SELECT tool_name, status FROM public.tool_audit WHERE run_id = $1",
            uuid.UUID(run_id),
        )
        return rows
    finally:
        await connection.close()


async def call_as_tools(sql: str, seller_ids: tuple[str, ...] | None) -> ToolOutcome:
    pool = await asyncpg.create_pool(dsn=TOOLS_URL, min_size=1, max_size=2)
    try:
        statement = validate_readonly_sql(sql)
        return await run_readonly(
            pool,
            lambda connection: query_readonly_sql(
                connection, tenant_settings(seller_ids), statement
            ),
        )
    finally:
        await pool.close()


async def test_view_exposes_the_documented_fact_shape() -> None:
    connection = await asyncpg.connect(TOOLS_URL)
    try:
        columns = await connection.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'olist' AND table_name = 'sql_order_facts' "
            "ORDER BY ordinal_position"
        )
    finally:
        await connection.close()
    assert [row["column_name"] for row in columns] == [
        "order_id",
        "order_item_id",
        "seller_id",
        "product_id",
        "category",
        "price",
        "freight_value",
        "order_status",
        "purchase_date",
        "delivered_date",
        "estimated_date",
        "days_late",
    ]


async def test_unset_and_empty_setting_mean_every_seller() -> None:
    total, seller = await expected_counts()
    assert 0 < seller < total
    connection = await asyncpg.connect(TOOLS_URL)
    try:
        unset = await connection.fetchval("SELECT count(*)::int FROM olist.sql_order_facts")
        async with connection.transaction():
            await connection.execute("SELECT set_config('switchboard.seller_ids', '', true)")
            empty = await connection.fetchval("SELECT count(*)::int FROM olist.sql_order_facts")
            await connection.execute(
                "SELECT set_config('switchboard.seller_ids', $1, true)", SELLER_ID
            )
            scoped = await connection.fetchval("SELECT count(*)::int FROM olist.sql_order_facts")
            sellers = await connection.fetchval(
                "SELECT count(DISTINCT seller_id)::int FROM olist.sql_order_facts"
            )
    finally:
        await connection.close()
    assert int(unset or 0) == total
    assert int(empty or 0) == total
    assert int(scoped or 0) == seller
    assert int(sellers or 0) == 1


async def test_default_scope_reads_every_seller_and_caps_rows() -> None:
    total, _ = await expected_counts()
    outcome = await call_as_tools(FACT_SQL, None)
    assert outcome.total_rows == total
    assert len(outcome.rows) == ROW_CAP
    assert outcome.summary["rows_returned"] == ROW_CAP
    assert outcome.summary["row_cap"] == ROW_CAP
    assert outcome.summary["truncated"] is True
    assert f"LIMIT {ROW_CAP}" not in FACT_SQL
    sellers = await call_as_tools(
        "SELECT count(DISTINCT seller_id) AS sellers FROM olist.sql_order_facts", None
    )
    assert sellers.rows[0]["sellers"] > 1


async def test_seller_scope_returns_only_that_seller_and_caps_rows() -> None:
    total, seller = await expected_counts()
    outcome = await call_as_tools(FACT_SQL, (SELLER_ID,))
    assert outcome.total_rows == seller
    assert outcome.total_rows < total
    assert len(outcome.rows) == min(seller, ROW_CAP)
    assert all(row["seller_id"] == SELLER_ID for row in outcome.rows)
    assert outcome.summary["truncated"] is (seller > ROW_CAP)


async def test_seller_scope_applies_to_aggregates_too() -> None:
    _, seller = await expected_counts()
    sql = "SELECT count(*) AS items, count(DISTINCT order_id) AS orders FROM olist.sql_order_facts"
    scoped = await call_as_tools(sql, (SELLER_ID,))
    everything = await call_as_tools(sql, None)
    assert scoped.total_rows == 1
    assert len(scoped.rows) == 1
    assert scoped.rows[0]["items"] == seller
    assert everything.rows[0]["items"] > scoped.rows[0]["items"]


async def test_a_model_supplied_predicate_cannot_widen_the_scope() -> None:
    _, seller = await expected_counts()
    # The model may filter within its scope, never past it: the predicate runs on
    # rows the view already scoped, so another seller contributes nothing.
    other = await call_as_tools(
        "SELECT count(*) AS items FROM olist.sql_order_facts WHERE seller_id = 'someone-else'",
        (SELLER_ID,),
    )
    assert other.rows[0]["items"] == 0
    scoped = await call_as_tools(
        "SELECT count(*) AS items FROM olist.sql_order_facts WHERE 1 = 1",
        (SELLER_ID,),
    )
    assert scoped.rows[0]["items"] == seller


async def test_write_attempts_fail_inside_the_read_only_transaction() -> None:
    pool = await asyncpg.create_pool(dsn=TOOLS_URL, min_size=1, max_size=2)
    try:
        # A write inside the read-only transaction is refused by PostgreSQL, not
        # by the application, so a guard miss still cannot change a row.
        async with pool.acquire() as connection, connection.transaction(readonly=True):
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                await connection.execute("INSERT INTO olist.sellers (seller_id) VALUES ('probe')")
    finally:
        await pool.close()


async def test_tool_role_cannot_write_the_view_and_app_cannot_read_it() -> None:
    tools = await asyncpg.connect(TOOLS_URL)
    try:
        with pytest.raises(asyncpg.PostgresError):
            await tools.execute("INSERT INTO olist.sql_order_facts (order_id) VALUES ('probe')")
    finally:
        await tools.close()
    app = await asyncpg.connect(APP_URL)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute("SELECT * FROM olist.sql_order_facts LIMIT 1")
    finally:
        await app.close()


def demo_allowlist(tenant_id: str) -> set[str]:
    payload = yaml.safe_load(TENANTS_FILE.read_text(encoding="utf-8"))
    allowed: set[str] = set(payload["tenants"][tenant_id]["tools"]["allow"])
    return allowed


async def test_demo_tenants_do_not_list_the_sql_tool() -> None:
    source = McpToolSource(url=MCP_URL)
    assert len(demo_allowlist("default")) == 5
    for tenant_id in ("default", "seller_a"):
        allowed = demo_allowlist(tenant_id)
        assert "query_readonly_sql@1" not in allowed
        context = RunContext(
            run_id=str(uuid.uuid4()),
            tenant=TenantContext(
                tenant_id=tenant_id,
                allowed_agents={"data"},
                allowed_tools=allowed,
                provider="lmstudio",
                seller_ids=[SELLER_ID] if tenant_id == "seller_a" else None,
            ),
            run_token=mint(tenant_id)[0],
            trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        )
        specs = await source.list_tools(context)
        assert {spec.wire_name for spec in specs} == allowed
        assert "query_readonly_sql@1" not in {spec.wire_name for spec in specs}
        assert len(specs) == len(allowed)


async def test_demo_tenants_are_denied_and_the_denial_is_audited() -> None:
    sql = "SELECT count(*) AS items FROM olist.sql_order_facts"
    for tenant_id in ("default", "seller_a"):
        token, run_id = mint(tenant_id)
        async with mcp_session(token) as session:
            result = await session.call_tool("query_readonly_sql@1", {"sql": sql})
        assert result.is_error is True
        assert "not allowed" in error_text(result)
        rows = await audit_rows(run_id)
        assert [(row["tool_name"], row["status"]) for row in rows] == [
            ("query_readonly_sql", "denied")
        ]


async def test_refused_sql_never_reaches_the_database() -> None:
    token, run_id = mint("default")
    async with mcp_session(token) as session:
        result = await session.call_tool(
            "query_readonly_sql@1", {"sql": "DELETE FROM olist.orders"}
        )
    assert result.is_error is True
    assert "only SELECT statements are allowed" in error_text(result)
    # The guard runs inside the tool function, before the dispatcher verifies the
    # token or opens a transaction, so no audit row is written either.
    assert await audit_rows(run_id) == []


async def test_the_server_still_registers_the_tool() -> None:
    token, _ = mint("default")
    async with mcp_session(token) as session:
        tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    assert "query_readonly_sql@1" in names
    assert len(names) == 6
    spec = next(tool for tool in tools.tools if tool.name == "query_readonly_sql@1")
    schema = spec.model_dump(by_alias=True)["inputSchema"]
    assert schema["properties"]["sql"]["maxLength"] == 4000
