"""Olist MCP server (T1.07).

Run with ``python -m mcp_servers.olist.server``. It exposes five read-only
tools over streamable HTTP, verifies the signed run token on every call, applies
the tenant allowlist and seller filter from server-side config, and writes an
audit row before returning (design.md sections 7.1 to 7.4).
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import asyncpg
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_servers.olist import db
from mcp_servers.olist.auth import TokenError, bearer_token, verify_token
from mcp_servers.olist.db import (
    ROW_CAP,
    ToolOutcome,
    insert_audit,
    run_readonly,
)
from mcp_servers.olist.settings import (
    ServerConfigError,
    TenantSettings,
    ToolDefinition,
    load_manifest,
    load_tenants,
)

MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")
MANIFEST: dict[str, ToolDefinition] = load_manifest(MANIFEST_PATH)
IDENTITY_ARGUMENTS = frozenset(
    {"tenant", "tenant_id", "seller_id", "seller_ids", "run_id", "run_token"}
)
OrderStatus = Literal[
    "created",
    "approved",
    "invoiced",
    "processing",
    "shipped",
    "delivered",
    "unavailable",
    "canceled",
]
Metric = Literal["revenue", "count"]


class ToolArgumentError(Exception):
    pass


@dataclass
class ServerState:
    pool: asyncpg.Pool
    tenants: dict[str, TenantSettings]
    secret: str

    @classmethod
    async def create(cls) -> ServerState:
        secret = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")
        if len(secret.encode("utf-8")) < 32:
            raise RuntimeError("SWITCHBOARD_RUN_TOKEN_SECRET must be set to at least 32 bytes")
        dsn = os.environ.get("SWITCHBOARD_TOOLS_DATABASE_URL", "")
        if not dsn:
            raise RuntimeError("SWITCHBOARD_TOOLS_DATABASE_URL is not set")
        tenants_path = Path(os.environ.get("SWITCHBOARD_TENANTS_FILE", "config/tenants.yaml"))
        tenants = load_tenants(tenants_path)
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5, timeout=5.0)
        try:
            exists = await pool.fetchval("SELECT to_regclass('public.tool_audit')")
            if exists is None:
                raise RuntimeError(
                    "public.tool_audit is missing; apply docker/postgres/init/02-platform.sql"
                )
        except Exception:
            await pool.close()
            raise
        return cls(pool=pool, tenants=tenants, secret=secret)


_STATE: ServerState | None = None


def state() -> ServerState:
    if _STATE is None:
        raise RuntimeError("server state is not initialised")
    return _STATE


@asynccontextmanager
async def lifespan(_: MCPServer[ServerState]) -> AsyncIterator[ServerState]:
    global _STATE
    created = await ServerState.create()
    _STATE = created
    try:
        yield created
    finally:
        _STATE = None
        await created.pool.close()


mcp = MCPServer(
    "olist",
    version="1.0.0",
    instructions=(
        "Read-only Olist order data. Dates are YYYY-MM-DD and inclusive. "
        "Every result carries summary, rows, total_rows and audit_id."
    ),
    lifespan=lifespan,
)


def sanitise_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop identity fields; the token is the only identity source."""
    return {key: value for key, value in arguments.items() if key not in IDENTITY_ARGUMENTS}


def tool_meta(wire_name: str) -> dict[str, Any]:
    """Metadata carried over MCP so the client can rebuild a ToolSpec."""
    definition = MANIFEST[wire_name]
    return {
        "switchboard": {
            "version": definition.version,
            "risk": definition.risk,
            "tenant_scoped": definition.tenant_scoped,
            "sensitive_args": list(definition.sensitive_args),
        }
    }


def _check_range(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise ToolArgumentError("date_from must be on or before date_to")


async def _dispatch(
    ctx: Context[Any, Any],
    wire_name: str,
    arguments: dict[str, Any],
    operation: Callable[[asyncpg.Connection, TenantSettings], Awaitable[ToolOutcome]],
) -> dict[str, Any]:
    tool = MANIFEST[wire_name]
    clean = sanitise_arguments(arguments)
    started = time.perf_counter()
    tenant_id = "unknown"
    run_id: str | None = None

    try:
        try:
            headers = ctx.headers or {}
        except ValueError as exc:
            raise TokenError("missing request context") from exc
        claims = verify_token(bearer_token(headers), state().secret)
        tenant_id = claims.tenant_id
        run_id = claims.run_id
        tenant = state().tenants.get(tenant_id)
        if tenant is None:
            raise TokenError(f"tenant {tenant_id!r} is not configured")
        if wire_name not in tenant.allowed_tools:
            raise PermissionError(f"tool {wire_name} is not allowed for tenant {tenant_id!r}")

        outcome = await run_readonly(state().pool, lambda connection: operation(connection, tenant))
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_id = await insert_audit(
            state().pool,
            run_id=run_id,
            tenant_id=tenant_id,
            tool_name=tool.name,
            tool_version=tool.version,
            args=clean,
            sensitive_args=tool.sensitive_args,
            status="ok",
            duration_ms=duration_ms,
        )
        return {
            "summary": outcome.summary,
            "rows": outcome.rows,
            "total_rows": outcome.total_rows,
            "audit_id": str(audit_id),
        }
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        status = "denied" if isinstance(exc, (TokenError, PermissionError)) else "error"
        await insert_audit(
            state().pool,
            run_id=run_id,
            tenant_id=tenant_id,
            tool_name=tool.name,
            tool_version=tool.version,
            args=clean,
            sensitive_args=tool.sensitive_args,
            status=status,
            duration_ms=duration_ms,
        )
        raise


async def _call_tool(
    ctx: Context[Any, Any],
    wire_name: str,
    arguments: dict[str, Any],
    operation: Callable[[asyncpg.Connection, TenantSettings], Awaitable[ToolOutcome]],
) -> dict[str, Any]:
    try:
        return await _dispatch(ctx, wire_name, arguments, operation)
    except ToolError:
        raise
    except TokenError as exc:
        raise ToolError(f"authentication failed: {exc}") from exc
    except PermissionError as exc:
        raise ToolError(f"permission denied: {exc}") from exc
    except ToolArgumentError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(f"tool execution failed: {type(exc).__name__}") from exc


@mcp.tool(
    name="orders_summary@1",
    description=MANIFEST["orders_summary@1"].description,
    meta=tool_meta("orders_summary@1"),
)
async def orders_summary(
    ctx: Context[Any, Any],
    date_from: date,
    date_to: date,
    status: OrderStatus | None = None,
) -> dict[str, Any]:
    _check_range(date_from, date_to)
    arguments: dict[str, Any] = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }
    if status is not None:
        arguments["status"] = status
    return await _call_tool(
        ctx,
        "orders_summary@1",
        arguments,
        lambda connection, tenant: db.orders_summary(
            connection, tenant, date_from, date_to, status
        ),
    )


@mcp.tool(
    name="orders_late@1",
    description=MANIFEST["orders_late@1"].description,
    meta=tool_meta("orders_late@1"),
)
async def orders_late(
    ctx: Context[Any, Any],
    date_from: date,
    date_to: date,
    min_days_late: int | None = None,
) -> dict[str, Any]:
    _check_range(date_from, date_to)
    minimum = min_days_late if min_days_late is not None else 1
    if minimum < 1:
        raise ToolArgumentError("min_days_late must be at least 1")
    arguments: dict[str, Any] = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }
    if min_days_late is not None:
        arguments["min_days_late"] = min_days_late
    return await _call_tool(
        ctx,
        "orders_late@1",
        arguments,
        lambda connection, tenant: db.orders_late(connection, tenant, date_from, date_to, minimum),
    )


@mcp.tool(
    name="order_lookup@1",
    description=MANIFEST["order_lookup@1"].description,
    meta=tool_meta("order_lookup@1"),
)
async def order_lookup(
    ctx: Context[Any, Any],
    order_id: str,
) -> dict[str, Any]:
    if not order_id.strip():
        raise ToolArgumentError("order_id must not be empty")
    return await _call_tool(
        ctx,
        "order_lookup@1",
        {"order_id": order_id},
        lambda connection, tenant: db.order_lookup(connection, tenant, order_id),
    )


@mcp.tool(
    name="top_products@1",
    description=MANIFEST["top_products@1"].description,
    meta=tool_meta("top_products@1"),
)
async def top_products(
    ctx: Context[Any, Any],
    date_from: date,
    date_to: date,
    metric: Metric,
    limit: int,
) -> dict[str, Any]:
    _check_range(date_from, date_to)
    if limit < 1 or limit > ROW_CAP:
        raise ToolArgumentError(f"limit must be between 1 and {ROW_CAP}")
    return await _call_tool(
        ctx,
        "top_products@1",
        {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "metric": metric,
            "limit": limit,
        },
        lambda connection, tenant: db.top_products(
            connection, tenant, date_from, date_to, metric, limit
        ),
    )


@mcp.tool(
    name="describe_schema@1",
    description=MANIFEST["describe_schema@1"].description,
    meta=tool_meta("describe_schema@1"),
)
async def describe_schema(ctx: Context[Any, Any]) -> dict[str, Any]:
    return await _call_tool(
        ctx,
        "describe_schema@1",
        {},
        lambda connection, _: db.describe_schema(connection),
    )


@mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
async def health(_: Request) -> Response:
    if _STATE is not None:
        try:
            database_ok = bool(await _STATE.pool.fetchval("SELECT 1"))
        except Exception:
            database_ok = False
    else:
        database_ok = False
    body = {
        "status": "ok" if database_ok else "error",
        "service": "mcp-olist",
        "database": database_ok,
        "tools": len(MANIFEST),
    }
    return JSONResponse(body, status_code=200 if database_ok else 503)


def main() -> None:
    port = int(os.environ.get("MCP_OLIST_PORT", "8001"))
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
        stateless_http=True,
    )


if __name__ == "__main__":
    try:
        main()
    except ServerConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
