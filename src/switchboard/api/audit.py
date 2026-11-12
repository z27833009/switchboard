"""Audit log reads for ``GET /audit`` (design.md sections 7.4 and 13.3).

The table is append-only: only the tool role inserts and only the application
role reads (design.md section 13.3). This module holds the read side, which
filters by tenant in SQL so another tenant's rows are invisible (design.md
section 11), and the mapping from the tool manifest's ``sensitive_args`` that
redacts a sensitive key even if a row ever reached the table unredacted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import asyncpg

from switchboard.api.schemas import AuditStatus
from switchboard.api.store import decode_json
from switchboard.core.config import ToolSourceConfig
from switchboard.eval.manifest import load_tool_specs

AUDIT_COLUMNS = """
    id, run_id, tenant_id, tool_name, tool_version, args_hash,
    args_redacted, status, duration_ms, created_at
"""

# Filters are part of the statement, never a post-hoc check in the route, so a
# caller of the store cannot accidentally read another tenant's rows.
LIST_AUDIT_SQL = f"""
SELECT {AUDIT_COLUMNS}
FROM public.tool_audit
WHERE tenant_id = $1
  AND ($2::uuid IS NULL OR run_id = $2)
  AND ($3::text IS NULL OR tool_name = $3)
  AND ($4::text IS NULL OR status = $4)
  AND ($5::timestamptz IS NULL OR created_at >= $5)
  AND ($6::timestamptz IS NULL OR created_at <= $6)
ORDER BY created_at DESC, id DESC
LIMIT $7 OFFSET $8
"""


@dataclass(frozen=True)
class AuditQuery:
    """The validated filters of one ``GET /audit`` call."""

    tenant_id: str
    run_id: str | None = None
    tool_name: str | None = None
    status: AuditStatus | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None
    limit: int = 50
    offset: int = 0


def as_utc(value: datetime | None) -> datetime | None:
    """Treat a naive timestamp as UTC so asyncpg always gets an aware value."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def audit_db_row(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["id"] = str(payload["id"])
    if payload.get("run_id") is not None:
        payload["run_id"] = str(payload["run_id"])
    payload["args_redacted"] = decode_json(payload.get("args_redacted"), {})
    return payload


def load_sensitive_args(sources: Sequence[ToolSourceConfig]) -> dict[str, tuple[str, ...]]:
    """Read ``sensitive_args`` from every tool manifest the platform declares.

    The MCP server redacts at write time from the same field; the API keeps the
    list as a second guard so no response can return a raw sensitive argument
    even if a row predates the field or was written by hand (AC-18b).
    """
    mapping: dict[str, tuple[str, ...]] = {}
    for source in sources:
        if not source.manifest:
            continue
        for spec in load_tool_specs(Path(source.manifest)):
            if spec.sensitive_args:
                mapping[spec.wire_name] = tuple(spec.sensitive_args)
    return mapping


class AuditStore(Protocol):
    """What the audit route needs from storage, implemented by the pool and tests."""

    async def list_audit(self, query: AuditQuery) -> list[dict[str, Any]]: ...


class PgAuditStore:
    """Audit rows in PostgreSQL through the application role (SELECT only)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_audit(self, query: AuditQuery) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            LIST_AUDIT_SQL,
            query.tenant_id,
            query.run_id,
            query.tool_name,
            query.status,
            query.from_ts,
            query.to_ts,
            query.limit,
            query.offset,
        )
        return [audit_db_row(row) for row in rows]


__all__ = [
    "AUDIT_COLUMNS",
    "LIST_AUDIT_SQL",
    "AuditQuery",
    "AuditStore",
    "PgAuditStore",
    "as_utc",
    "audit_db_row",
    "load_sensitive_args",
]
