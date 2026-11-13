"""Database access for the Olist MCP tools.

Every query runs in a read-only transaction with a 5 second statement timeout,
through the SELECT-only role (design.md section 7.1). Audit writes happen
outside that transaction and are mandatory: a failed audit write fails the call
(design.md section 7.4).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from mcp_servers.olist.settings import TenantSettings

ROW_CAP = 200
STATEMENT_TIMEOUT = "5s"
# The note tool's one argument is model-written text; the cap is declared in the
# manifest, enforced again by the tool wrapper and by the table's check
# constraint (T3.01, ADR 0015).
NOTE_MAX_CHARS = 500


@dataclass(frozen=True)
class ToolOutcome:
    summary: dict[str, Any]
    rows: list[dict[str, Any]]
    total_rows: int


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def rows_from(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{key: json_value(value) for key, value in record.items()} for record in records]


def seller_filter(tenant: TenantSettings) -> list[str] | None:
    return list(tenant.seller_ids) if tenant.seller_ids is not None else None


async def run_readonly(
    pool: asyncpg.Pool,
    operation: Callable[[asyncpg.Connection], Awaitable[ToolOutcome]],
) -> ToolOutcome:
    async with pool.acquire() as connection, connection.transaction(readonly=True):
        await connection.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        return await operation(connection)


async def run_readwrite(
    pool: asyncpg.Pool,
    operation: Callable[[asyncpg.Connection], Awaitable[ToolOutcome]],
) -> ToolOutcome:
    """One read-write transaction for the single append-only write tool (T3.01).

    The database role can still only INSERT into ``olist.order_notes``; the
    transaction is writable so that one statement can commit, with the same
    statement timeout as every read.
    """
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        return await operation(connection)


def hash_args(args: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def redact_args(args: Mapping[str, Any], sensitive: Sequence[str]) -> dict[str, Any]:
    hidden = set(sensitive)
    return {key: "[redacted]" if key in hidden else value for key, value in args.items()}


async def insert_audit(
    pool: asyncpg.Pool,
    *,
    run_id: str | None,
    tenant_id: str,
    tool_name: str,
    tool_version: str,
    args: Mapping[str, Any],
    sensitive_args: Sequence[str],
    status: str,
    duration_ms: int,
) -> uuid.UUID:
    audit_id = uuid.uuid4()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO public.tool_audit
                (id, run_id, tenant_id, tool_name, tool_version, args_hash,
                 args_redacted, status, duration_ms)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
            """,
            audit_id,
            uuid.UUID(run_id) if run_id else None,
            tenant_id,
            tool_name,
            tool_version,
            hash_args(args),
            json.dumps(redact_args(args, sensitive_args), default=str, ensure_ascii=False),
            status,
            duration_ms,
        )
    return audit_id


async def orders_summary(
    connection: asyncpg.Connection,
    tenant: TenantSettings,
    date_from: date,
    date_to: date,
    status: str | None,
) -> ToolOutcome:
    sellers = seller_filter(tenant)
    summary = await connection.fetchrow(
        """
        WITH scoped AS (
            SELECT o.order_id, o.order_status
            FROM olist.orders o
            WHERE o.order_purchase_timestamp >= $1::date
              AND o.order_purchase_timestamp < ($2::date + interval '1 day')
              AND ($3::text[] IS NULL OR EXISTS (
                    SELECT 1 FROM olist.order_items i
                    WHERE i.order_id = o.order_id AND i.seller_id = ANY($3::text[])
              ))
              AND ($4::text IS NULL OR o.order_status = $4)
        ), scoped_items AS (
            SELECT i.price
            FROM olist.order_items i
            JOIN scoped s ON s.order_id = i.order_id
            WHERE $3::text[] IS NULL OR i.seller_id = ANY($3::text[])
        )
        SELECT (SELECT count(*) FROM scoped)::int AS orders,
               (SELECT count(*) FROM scoped_items)::int AS items,
               (SELECT coalesce(sum(price), 0)::float FROM scoped_items) AS revenue
        """,
        date_from,
        date_to,
        sellers,
        status,
    )
    status_rows = await connection.fetch(
        """
        WITH scoped AS (
            SELECT o.order_id, o.order_status
            FROM olist.orders o
            WHERE o.order_purchase_timestamp >= $1::date
              AND o.order_purchase_timestamp < ($2::date + interval '1 day')
              AND ($3::text[] IS NULL OR EXISTS (
                    SELECT 1 FROM olist.order_items i
                    WHERE i.order_id = o.order_id AND i.seller_id = ANY($3::text[])
              ))
              AND ($4::text IS NULL OR o.order_status = $4)
        )
        SELECT order_status AS status, count(*)::int AS orders
        FROM scoped
        GROUP BY order_status
        ORDER BY orders DESC, status
        LIMIT 200
        """,
        date_from,
        date_to,
        sellers,
        status,
    )
    return ToolOutcome(
        summary={
            "orders": int(summary["orders"]),
            "items": int(summary["items"]),
            "revenue": float(summary["revenue"]),
        },
        rows=rows_from(status_rows),
        total_rows=len(status_rows),
    )


async def orders_late(
    connection: asyncpg.Connection,
    tenant: TenantSettings,
    date_from: date,
    date_to: date,
    min_days_late: int,
) -> ToolOutcome:
    sellers = seller_filter(tenant)
    summary = await connection.fetchrow(
        """
        WITH scoped AS (
            SELECT o.order_id,
                   (o.order_delivered_customer_date::date
                    - o.order_estimated_delivery_date::date) AS days_late
            FROM olist.orders o
            WHERE o.order_purchase_timestamp >= $1::date
              AND o.order_purchase_timestamp < ($2::date + interval '1 day')
              AND ($3::text[] IS NULL OR EXISTS (
                    SELECT 1 FROM olist.order_items i
                    WHERE i.order_id = o.order_id AND i.seller_id = ANY($3::text[])
              ))
              AND o.order_delivered_customer_date IS NOT NULL
              AND o.order_estimated_delivery_date IS NOT NULL
              AND o.order_delivered_customer_date > o.order_estimated_delivery_date
        ), late AS (
            SELECT * FROM scoped WHERE days_late >= $4
        )
        SELECT count(*)::int AS late_orders,
               coalesce(avg(days_late), 0)::float AS avg_days_late,
               coalesce(max(days_late), 0)::int AS max_days_late
        FROM late
        """,
        date_from,
        date_to,
        sellers,
        min_days_late,
    )
    late_rows = await connection.fetch(
        """
        WITH scoped AS (
            SELECT o.order_id, o.order_status, o.order_purchase_timestamp,
                   o.order_delivered_customer_date, o.order_estimated_delivery_date,
                   (o.order_delivered_customer_date::date
                    - o.order_estimated_delivery_date::date) AS days_late
            FROM olist.orders o
            WHERE o.order_purchase_timestamp >= $1::date
              AND o.order_purchase_timestamp < ($2::date + interval '1 day')
              AND ($3::text[] IS NULL OR EXISTS (
                    SELECT 1 FROM olist.order_items i
                    WHERE i.order_id = o.order_id AND i.seller_id = ANY($3::text[])
              ))
              AND o.order_delivered_customer_date IS NOT NULL
              AND o.order_estimated_delivery_date IS NOT NULL
              AND o.order_delivered_customer_date > o.order_estimated_delivery_date
        ), late AS (
            SELECT * FROM scoped WHERE days_late >= $4
        )
        SELECT order_id, order_status, order_purchase_timestamp,
               order_delivered_customer_date, order_estimated_delivery_date, days_late
        FROM late
        ORDER BY days_late DESC, order_id
        LIMIT 200
        """,
        date_from,
        date_to,
        sellers,
        min_days_late,
    )
    return ToolOutcome(
        summary={
            "late_orders": int(summary["late_orders"]),
            "avg_days_late": float(summary["avg_days_late"]),
            "max_days_late": int(summary["max_days_late"]),
            "min_days_late": min_days_late,
        },
        rows=rows_from(late_rows),
        total_rows=int(summary["late_orders"]),
    )


async def order_lookup(
    connection: asyncpg.Connection,
    tenant: TenantSettings,
    order_id: str,
) -> ToolOutcome:
    sellers = seller_filter(tenant)
    order = await connection.fetchrow(
        """
        SELECT o.order_id, o.order_status, o.order_purchase_timestamp,
               o.order_delivered_customer_date, o.order_estimated_delivery_date,
               c.customer_city, c.customer_state
        FROM olist.orders o
        JOIN olist.customers c ON c.customer_id = o.customer_id
        WHERE o.order_id = $1
          AND ($2::text[] IS NULL OR EXISTS (
                SELECT 1 FROM olist.order_items i
                WHERE i.order_id = o.order_id AND i.seller_id = ANY($2::text[])
          ))
        """,
        order_id,
        sellers,
    )
    if order is None:
        return ToolOutcome(summary={"found": False}, rows=[], total_rows=0)

    item_count = await connection.fetchval(
        """
        SELECT count(*)::int FROM olist.order_items
        WHERE order_id = $1 AND ($2::text[] IS NULL OR seller_id = ANY($2::text[]))
        """,
        order_id,
        sellers,
    )
    item_total = await connection.fetchval(
        """
        SELECT coalesce(sum(price), 0)::float FROM olist.order_items
        WHERE order_id = $1 AND ($2::text[] IS NULL OR seller_id = ANY($2::text[]))
        """,
        order_id,
        sellers,
    )
    payment_total = None
    if sellers is None:
        payment_total = await connection.fetchval(
            """
            SELECT coalesce(sum(payment_value), 0)::float
            FROM olist.order_payments
            WHERE order_id = $1
            """,
            order_id,
        )
    item_rows = await connection.fetch(
        """
        SELECT i.order_item_id, i.product_id, i.seller_id, i.shipping_limit_date,
               i.price, i.freight_value, p.product_category_name,
               t.product_category_name_english AS category
        FROM olist.order_items i
        LEFT JOIN olist.products p ON p.product_id = i.product_id
        LEFT JOIN olist.product_category_name_translation t
               ON t.product_category_name = p.product_category_name
        WHERE i.order_id = $1
          AND ($2::text[] IS NULL OR i.seller_id = ANY($2::text[]))
        ORDER BY i.order_item_id
        LIMIT 200
        """,
        order_id,
        sellers,
    )
    summary = {
        "found": True,
        "order_id": order["order_id"],
        "status": order["order_status"],
        "purchase_timestamp": json_value(order["order_purchase_timestamp"]),
        "delivered_customer_date": json_value(order["order_delivered_customer_date"]),
        "estimated_delivery_date": json_value(order["order_estimated_delivery_date"]),
        "customer_city": order["customer_city"],
        "customer_state": order["customer_state"],
        "item_count": int(item_count or 0),
        "item_total": float(item_total or 0),
        "payment_total": float(payment_total or 0) if payment_total is not None else None,
    }
    return ToolOutcome(
        summary=summary,
        rows=rows_from(item_rows),
        total_rows=int(item_count or 0),
    )


async def top_products(
    connection: asyncpg.Connection,
    tenant: TenantSettings,
    date_from: date,
    date_to: date,
    metric: str,
    limit: int,
) -> ToolOutcome:
    sellers = seller_filter(tenant)
    total_products = await connection.fetchval(
        """
        SELECT count(DISTINCT i.product_id)::int
        FROM olist.order_items i
        JOIN olist.orders o ON o.order_id = i.order_id
        WHERE o.order_purchase_timestamp >= $1::date
          AND o.order_purchase_timestamp < ($2::date + interval '1 day')
          AND ($3::text[] IS NULL OR i.seller_id = ANY($3::text[]))
        """,
        date_from,
        date_to,
        sellers,
    )
    records = await connection.fetch(
        """
        SELECT p.product_id,
               coalesce(t.product_category_name_english,
                        p.product_category_name, 'unknown') AS category,
               CASE WHEN $4::text = 'revenue'
                    THEN sum(i.price)::float
                    ELSE count(*)::int
               END AS value,
               count(DISTINCT i.order_id)::int AS orders
        FROM olist.order_items i
        JOIN olist.orders o ON o.order_id = i.order_id
        JOIN olist.products p ON p.product_id = i.product_id
        LEFT JOIN olist.product_category_name_translation t
               ON t.product_category_name = p.product_category_name
        WHERE o.order_purchase_timestamp >= $1::date
          AND o.order_purchase_timestamp < ($2::date + interval '1 day')
          AND ($3::text[] IS NULL OR i.seller_id = ANY($3::text[]))
        GROUP BY p.product_id, p.product_category_name,
                 t.product_category_name_english
        ORDER BY value DESC, p.product_id
        LIMIT $5
        """,
        date_from,
        date_to,
        sellers,
        metric,
        limit,
    )
    rows = [{"rank": index + 1, **row} for index, row in enumerate(rows_from(records))]
    return ToolOutcome(
        summary={
            "metric": metric,
            "products": int(total_products or 0),
            "limit": limit,
        },
        rows=rows,
        total_rows=int(total_products or 0),
    )


async def describe_schema(connection: asyncpg.Connection) -> ToolOutcome:
    records = await connection.fetch(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'olist'
        ORDER BY table_name, ordinal_position
        LIMIT 200
        """
    )
    rows = rows_from(records)
    return ToolOutcome(
        summary={
            "tables": len({row["table_name"] for row in rows}),
            "columns": len(rows),
        },
        rows=rows,
        total_rows=len(rows),
    )


# The fact view reads this transaction-local setting (design.md sections 7.1 and
# 11). An empty value means every seller, so the server always sets it and never
# relies on an unset default.
SQL_SELLER_SETTING = "switchboard.seller_ids"


def seller_scope(tenant: TenantSettings) -> str:
    return ",".join(tenant.seller_ids or ())


async def query_readonly_sql(
    connection: asyncpg.Connection,
    tenant: TenantSettings,
    statement: str,
) -> ToolOutcome:
    """Run one validated SELECT over ``olist.sql_order_facts``.

    ``statement`` must be the output of ``sql_guard.validate_readonly_sql``: the
    guard, not this function, is the boundary that keeps the SQL a read of the
    one view. The seller filter is applied first, on this transaction, so it
    cannot be supplied or overridden by the model's arguments (ADR 0014).
    """
    await connection.execute(
        f"SELECT set_config('{SQL_SELLER_SETTING}', $1, true)",
        seller_scope(tenant),
    )
    total = await connection.fetchval(f"SELECT count(*)::int FROM ({statement}) AS guarded_query")
    records = await connection.fetch(
        f"SELECT * FROM ({statement}) AS guarded_query LIMIT {ROW_CAP}"
    )
    rows = rows_from(records)
    total_rows = int(total or 0)
    return ToolOutcome(
        summary={
            "rows_returned": len(rows),
            "row_cap": ROW_CAP,
            "truncated": total_rows > len(rows),
            "columns": list(records[0].keys()) if records else [],
        },
        rows=rows,
        total_rows=total_rows,
    )


async def add_order_note(
    connection: asyncpg.Connection,
    tenant: TenantSettings,
    order_id: str,
    note: str,
) -> ToolOutcome:
    """Append one note to one of the tenant's orders (T3.01, ADR 0015).

    The order has to belong to the tenant's seller ids, checked in the same
    transaction and from server-side config, so the model's arguments cannot
    widen the scope. Nothing else is written: no order status, no existing row.
    A foreign or unknown order is an empty result, like ``order_lookup``, so the
    model learns the truth without a stack trace and the audit row records it.
    """
    sellers = seller_filter(tenant)
    owned = await connection.fetchval(
        """
        SELECT 1
        FROM olist.orders o
        WHERE o.order_id = $1
          AND ($2::text[] IS NULL OR EXISTS (
                SELECT 1 FROM olist.order_items i
                WHERE i.order_id = o.order_id AND i.seller_id = ANY($2::text[])
          ))
        LIMIT 1
        """,
        order_id,
        sellers,
    )
    if owned is None:
        return ToolOutcome(
            summary={"inserted": False, "found": False, "order_id": order_id},
            rows=[],
            total_rows=0,
        )
    row = await connection.fetchrow(
        """
        INSERT INTO olist.order_notes (order_id, tenant_id, note)
        VALUES ($1, $2, $3)
        RETURNING id, order_id, tenant_id, note, created_at
        """,
        order_id,
        tenant.tenant_id,
        note,
    )
    if row is None:  # pragma: no cover - INSERT RETURNING always yields a row
        raise RuntimeError("the note row was not returned")
    return ToolOutcome(
        summary={
            "inserted": True,
            "found": True,
            "order_id": order_id,
            "note_id": str(row["id"]),
            "created_at": json_value(row["created_at"]),
        },
        rows=rows_from([row]),
        total_rows=1,
    )
