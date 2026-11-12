"""Run and span persistence (design.md sections 12.2 and 14).

``RunStore`` is the small surface the API needs: start a run, finish it, list
runs, read one run and read the spans of one run. ``PgRunStore`` is the
production implementation on the application-role pool; tests use a memory
store so HTTP and SSE tests run without PostgreSQL.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from switchboard.core.models import RunResult
from switchboard.tracing.runs import insert_run

RUN_COLUMNS = """
    id, tenant_id, session_id, query, orchestrator, plan, answer, citations, errors,
    status, cache_hit, attempts, input_tokens, cached_input_tokens,
    output_tokens, cost_usd, started_at, ended_at, trace_complete
"""

SPAN_COLUMNS = """
    span_id, trace_id, parent_span_id, run_id, name, kind,
    start_time, end_time, status, attributes, events
"""

FINISH_RUN_SQL = """
UPDATE public.runs SET
    plan = $2,
    answer = $3,
    citations = $4::jsonb,
    errors = $5::jsonb,
    status = $6,
    cache_hit = $7,
    attempts = $8,
    input_tokens = $9,
    cached_input_tokens = $10,
    output_tokens = $11,
    ended_at = $12,
    trace_complete = $13
WHERE id = $1
"""

LIST_RUNS_SQL = f"""
SELECT {RUN_COLUMNS}
FROM public.runs
WHERE tenant_id = $1
ORDER BY started_at DESC
LIMIT $2
"""

# Tenant scoping of a single read is part of the query, not a check in the
# route, so every caller of the store gets it (design.md section 11).
GET_RUN_SQL = f"SELECT {RUN_COLUMNS} FROM public.runs WHERE id = $1 AND tenant_id = $2"

FETCH_SPANS_SQL = f"""
SELECT {SPAN_COLUMNS}
FROM public.spans
WHERE run_id = $1
   OR trace_id IN (SELECT trace_id FROM public.spans WHERE run_id = $1)
ORDER BY start_time, span_id
"""


def decode_json(value: Any, default: Any) -> Any:
    """asyncpg returns jsonb as text unless a codec is installed."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def run_row(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["id"] = str(payload["id"])
    payload["citations"] = decode_json(payload.get("citations"), [])
    payload["errors"] = decode_json(payload.get("errors"), [])
    cost = payload.get("cost_usd")
    payload["cost_usd"] = float(cost) if cost is not None else None
    return payload


def span_row(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    if payload.get("run_id") is not None:
        payload["run_id"] = str(payload["run_id"])
    payload["attributes"] = decode_json(payload.get("attributes"), {})
    payload["events"] = decode_json(payload.get("events"), [])
    return payload


class RunStore(Protocol):
    """What the API needs from run storage, implemented by the pool and tests."""

    async def start_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        session_id: str | None,
        query: str,
        orchestrator: str,
    ) -> None: ...

    async def finish_run(self, result: RunResult, *, trace_complete: bool) -> None: ...

    async def list_runs(self, *, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]: ...

    async def get_run(self, run_id: str, *, tenant_id: str) -> dict[str, Any] | None: ...

    async def fetch_spans(self, run_id: str) -> list[dict[str, Any]]: ...


class PgRunStore:
    """Runs and spans in PostgreSQL through the application role."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def start_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        session_id: str | None,
        query: str,
        orchestrator: str,
    ) -> None:
        await insert_run(
            self._pool,
            run_id=run_id,
            tenant_id=tenant_id,
            query=query,
            orchestrator=orchestrator,
            session_id=session_id,
        )

    async def finish_run(self, result: RunResult, *, trace_complete: bool) -> None:
        answer = result.answer
        ended_at = result.ended_at or datetime.now(UTC)
        await self._pool.execute(
            FINISH_RUN_SQL,
            uuid.UUID(result.run_id),
            result.plan.label if result.plan is not None else None,
            answer.answer if answer is not None else None,
            json.dumps(
                [citation.model_dump(mode="json") for citation in answer.citations]
                if answer is not None
                else []
            ),
            json.dumps([error.model_dump(mode="json") for error in result.errors]),
            result.status,
            result.cache_hit,
            result.attempts,
            result.usage.input_tokens,
            result.usage.cached_input_tokens,
            result.usage.output_tokens,
            ended_at,
            trace_complete,
        )

    async def list_runs(self, *, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(LIST_RUNS_SQL, tenant_id, limit)
        return [run_row(row) for row in rows]

    async def get_run(self, run_id: str, *, tenant_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(GET_RUN_SQL, uuid.UUID(run_id), tenant_id)
        return None if row is None else run_row(row)

    async def fetch_spans(self, run_id: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(FETCH_SPANS_SQL, uuid.UUID(run_id))
        return [span_row(row) for row in rows]


def parse_run_id(run_id: str) -> str:
    """Reject a malformed run id the same way as an unknown one."""
    try:
        return str(uuid.UUID(run_id))
    except ValueError as exc:
        raise ValueError(f"{run_id!r} is not a run id") from exc


def rows_are_complete(name_rows: Sequence[Mapping[str, Any]]) -> bool:
    return any(row.get("name") == "run" for row in name_rows)


__all__ = [
    "PgRunStore",
    "RunStore",
    "decode_json",
    "parse_run_id",
    "rows_are_complete",
    "run_row",
    "span_row",
]
