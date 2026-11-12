"""Run and span persistence (design.md sections 12.2 and 14).

``RunStore`` is the small surface the API needs: start a run, finish it, list
runs, read one run, read the spans of one run, and load the recent turns of one
session. ``PgRunStore`` is the production implementation on the application-role
pool; tests use a memory store so HTTP and SSE tests run without PostgreSQL.

Session turns are read with the tenant and the session id in SQL, so a request
can never see another tenant's or another session's turns (design.md 11 and
section 6.3), and they are returned oldest first.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import asyncpg

from switchboard.core.models import RunResult, Turn
from switchboard.tracing.runs import insert_run

RUN_COLUMNS = """
    id, tenant_id, session_id, query, orchestrator, provider, plan, answer, citations,
    errors, status, cache_hit, attempts, input_tokens, cached_input_tokens,
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
    cost_usd = $12,
    ended_at = $13,
    trace_complete = $14
WHERE id = $1
"""

LIST_RUNS_SQL = f"""
SELECT {RUN_COLUMNS}
FROM public.runs
WHERE tenant_id = $1
ORDER BY started_at DESC
LIMIT $2
"""

# Aggregation for `GET /runs?group_by=`. Days are UTC because started_at is a
# timestamptz and a report should not move with the server's local timezone.
# `provider` is null on rows written before T2.06; they are grouped as
# "unknown" instead of disappearing. A null cost is counted, not summed, so a
# group with unpriced runs shows it rather than looking fully priced.
AGGREGATE_RUNS_SQL = {
    "day": """
        SELECT to_char((started_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS bucket,
               count(*)::int AS run_count,
               coalesce(sum(input_tokens), 0)::bigint AS input_tokens,
               coalesce(sum(cached_input_tokens), 0)::bigint AS cached_input_tokens,
               coalesce(sum(output_tokens), 0)::bigint AS output_tokens,
               sum(cost_usd) AS cost_usd,
               count(*) FILTER (WHERE cost_usd IS NULL)::int AS unpriced_runs
        FROM public.runs
        WHERE tenant_id = $1
        GROUP BY bucket
        ORDER BY bucket DESC
    """,
    "provider": """
        SELECT coalesce(provider, 'unknown') AS bucket,
               count(*)::int AS run_count,
               coalesce(sum(input_tokens), 0)::bigint AS input_tokens,
               coalesce(sum(cached_input_tokens), 0)::bigint AS cached_input_tokens,
               coalesce(sum(output_tokens), 0)::bigint AS output_tokens,
               sum(cost_usd) AS cost_usd,
               count(*) FILTER (WHERE cost_usd IS NULL)::int AS unpriced_runs
        FROM public.runs
        WHERE tenant_id = $1
        GROUP BY bucket
        ORDER BY bucket ASC
    """,
}

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

# The last N turns of one session: one completed run is one turn, so the newest
# ``limit`` rows are selected first and then returned oldest first. Both the
# tenant and the session id are in the WHERE clause, so neither can leak
# (design.md sections 6.3 and 11). A run still running, or one that stored no
# answer, is not a turn.
SESSION_TURNS_SQL = """
SELECT query, answer
FROM (
    SELECT id, query, answer, started_at
    FROM public.runs
    WHERE tenant_id = $1
      AND session_id = $2
      AND status <> 'running'
      AND answer IS NOT NULL
      AND answer <> ''
    ORDER BY started_at DESC, id DESC
    LIMIT $3
) AS recent
ORDER BY started_at ASC, id ASC
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
    # sums of numeric come back as Decimal; the API and the report speak float.
    payload["cost_usd"] = float(cost) if cost is not None else None
    return payload


def aggregate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One bucket of ``GET /runs?group_by=``; cost stays None when unpriced."""
    payload = dict(row)
    payload["bucket"] = str(payload["bucket"])
    payload["run_count"] = int(payload["run_count"])
    payload["input_tokens"] = int(payload["input_tokens"])
    payload["cached_input_tokens"] = int(payload["cached_input_tokens"])
    payload["output_tokens"] = int(payload["output_tokens"])
    payload["unpriced_runs"] = int(payload.get("unpriced_runs") or 0)
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


def session_turns(rows: Sequence[Mapping[str, Any]]) -> list[Turn]:
    """Turn stored run rows into user and assistant turns, oldest first."""
    turns: list[Turn] = []
    for row in rows:
        query = row.get("query")
        answer = row.get("answer")
        if not isinstance(query, str) or not query.strip():
            continue
        if not isinstance(answer, str) or not answer.strip():
            continue
        turns.append(Turn(role="user", content=query))
        turns.append(Turn(role="assistant", content=answer))
    return turns


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
        provider: str | None = None,
    ) -> None: ...

    async def finish_run(self, result: RunResult, *, trace_complete: bool) -> None: ...

    async def list_runs(self, *, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]: ...

    async def aggregate_runs(
        self, *, tenant_id: str, group_by: Literal["day", "provider"]
    ) -> list[dict[str, Any]]: ...

    async def get_run(self, run_id: str, *, tenant_id: str) -> dict[str, Any] | None: ...

    async def load_session_turns(
        self, *, tenant_id: str, session_id: str, limit: int
    ) -> list[Turn]: ...

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
        provider: str | None = None,
    ) -> None:
        await insert_run(
            self._pool,
            run_id=run_id,
            tenant_id=tenant_id,
            query=query,
            orchestrator=orchestrator,
            session_id=session_id,
            provider=provider,
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
            result.cost_usd,
            ended_at,
            trace_complete,
        )

    async def list_runs(self, *, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(LIST_RUNS_SQL, tenant_id, limit)
        return [run_row(row) for row in rows]

    async def aggregate_runs(
        self, *, tenant_id: str, group_by: Literal["day", "provider"]
    ) -> list[dict[str, Any]]:
        try:
            sql = AGGREGATE_RUNS_SQL[group_by]
        except KeyError as exc:
            raise ValueError(f"unknown run aggregation {group_by!r}") from exc
        rows = await self._pool.fetch(sql, tenant_id)
        return [aggregate_row(row) for row in rows]

    async def get_run(self, run_id: str, *, tenant_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(GET_RUN_SQL, uuid.UUID(run_id), tenant_id)
        return None if row is None else run_row(row)

    async def load_session_turns(
        self, *, tenant_id: str, session_id: str, limit: int
    ) -> list[Turn]:
        if limit <= 0:
            return []
        rows = await self._pool.fetch(SESSION_TURNS_SQL, tenant_id, session_id, limit)
        return session_turns(rows)

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
    "AGGREGATE_RUNS_SQL",
    "SESSION_TURNS_SQL",
    "PgRunStore",
    "RunStore",
    "aggregate_row",
    "decode_json",
    "parse_run_id",
    "rows_are_complete",
    "run_row",
    "session_turns",
    "span_row",
]
