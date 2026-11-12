"""Audit API and append-only grants against the real database (AC-18a, AC-18b).

    uv run --env-file .env pytest tests/integration/test_audit_integration.py -q

One synthetic audit row is written with the tool role through the same
``insert_audit`` the MCP server calls, including a ``sensitive_args`` entry, and
then read back through ``GET /audit`` with the application role. The grant test
proves both roles see the append-only split from design.md section 13.3.

Skipped unless ``.env`` points at the compose database for both roles. CI runs
it once the PostgreSQL service exists (T1.16).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from mcp_servers.olist.db import hash_args, insert_audit
from tests.fakes import FailingOrchestrator, tenants_config

from switchboard.api.app import create_app
from switchboard.api.audit import PgAuditStore
from switchboard.api.deps import AppDeps
from switchboard.api.store import PgRunStore

APP_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
TOOLS_URL = os.environ.get("SWITCHBOARD_TOOLS_DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (APP_URL and TOOLS_URL),
        reason="set SWITCHBOARD_DATABASE_URL and SWITCHBOARD_TOOLS_DATABASE_URL",
    ),
]

SECRET = "s" * 32
SECRET_ARG_VALUE = "synthetic-secret-6e1f"


def app_for(pool: asyncpg.Pool) -> Any:
    deps = AppDeps(
        store=PgRunStore(pool),
        audit=PgAuditStore(pool),
        orchestrators={"lmstudio": FailingOrchestrator()},
        tenants=tenants_config(),
        run_token_secret=SECRET,
        run_timeout_s=30.0,
    )
    return create_app(deps=deps)


async def test_audit_api_lists_redacted_rows_and_hides_other_tenants() -> None:
    run_id = str(uuid.uuid4())
    args = {"order_id": SECRET_ARG_VALUE, "limit": 1}
    tools_pool = await asyncpg.create_pool(dsn=TOOLS_URL, min_size=1, max_size=2)
    app_pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=2)
    try:
        audit_id = await insert_audit(
            tools_pool,
            run_id=run_id,
            tenant_id="seller_a",
            tool_name="order_lookup",
            tool_version="1",
            args=args,
            sensitive_args=["order_id"],
            status="ok",
            duration_ms=7,
        )
        app = app_for(app_pool)
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30.0)
        try:
            own = await client.get("/audit", params={"tenant_id": "seller_a", "run_id": run_id})
            assert own.status_code == 200, own.text
            body = own.json()
            assert body["count"] == 1, body
            row = body["rows"][0]
            assert row["id"] == str(audit_id)
            assert row["run_id"] == run_id
            assert row["tenant_id"] == "seller_a"
            assert row["tool_name"] == "order_lookup"
            assert row["tool_version"] == "1"
            assert row["args_hash"] == hash_args(args)
            assert row["args_redacted"] == {"order_id": "[redacted]", "limit": 1}
            assert row["status"] == "ok"
            assert row["duration_ms"] == 7
            assert datetime.fromisoformat(row["created_at"]).tzinfo is not None
            assert SECRET_ARG_VALUE not in own.text

            # The same row is invisible to the other tenant, in a filtered and
            # in an unfiltered read (design.md section 11).
            cross = await client.get("/audit", params={"tenant_id": "default", "run_id": run_id})
            assert cross.status_code == 200
            assert cross.json()["count"] == 0
            cross_list = await client.get(
                "/audit", params={"tenant_id": "default", "tool_name": "order_lookup"}
            )
            assert str(audit_id) not in cross_list.text
            unknown = await client.get("/audit", params={"tenant_id": "no_such_tenant"})
            assert unknown.status_code == 404
            other_status = await client.get(
                "/audit", params={"tenant_id": "seller_a", "run_id": run_id, "status": "denied"}
            )
            assert other_status.json()["count"] == 0
        finally:
            await client.aclose()

        # The stored payload is redacted too, not only the response.
        stored = await app_pool.fetchrow(
            "SELECT args_redacted FROM public.tool_audit WHERE id = $1", audit_id
        )
        assert stored is not None
        assert json.loads(stored["args_redacted"]) == {
            "order_id": "[redacted]",
            "limit": 1,
        }
    finally:
        await tools_pool.close()
        await app_pool.close()


async def test_tool_audit_grants_are_append_only() -> None:
    tools_pool = await asyncpg.create_pool(dsn=TOOLS_URL, min_size=1, max_size=2)
    app = await asyncpg.connect(APP_URL)
    try:
        audit_id = await insert_audit(
            tools_pool,
            run_id=str(uuid.uuid4()),
            tenant_id="seller_a",
            tool_name="orders_late",
            tool_version="1",
            args={"date_from": "2018-01-01", "date_to": "2018-01-31"},
            sensitive_args=[],
            status="error",
            duration_ms=4,
        )
        tools = await tools_pool.acquire()
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await tools.execute(
                    "UPDATE public.tool_audit SET status = 'ok' WHERE id = $1", audit_id
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await tools.execute("DELETE FROM public.tool_audit WHERE id = $1", audit_id)
        finally:
            await tools_pool.release(tools)

        count = await app.fetchval("SELECT count(*) FROM public.tool_audit")
        assert count is not None and count > 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute(
                "INSERT INTO public.tool_audit "
                "(tenant_id, tool_name, tool_version, args_hash, status, duration_ms) "
                "VALUES ('seller_a', 'probe', '1', 'probe', 'ok', 0)"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute("UPDATE public.tool_audit SET status = 'ok' WHERE id = $1", audit_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute("DELETE FROM public.tool_audit WHERE id = $1", audit_id)
    finally:
        await tools_pool.close()
        await app.close()
