"""Run and span persistence (design.md sections 12.2 and 14).

``RunStore`` is the small surface the API needs: start a run, finish it, list
runs, read one run, read the spans of one run, load the recent turns of one
session, append a feedback row and read the latest one. ``PgRunStore`` is the
production implementation on the application-role pool; tests use a memory store
so HTTP and SSE tests run without PostgreSQL.

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
    output_tokens, cost_usd, started_at, ended_at, trace_complete, pending_approval
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
    trace_complete = $14,
    pending_approval = $15::jsonb
WHERE id = $1
"""

# One atomic claim of a paused run (T3.01). The tenant and the status are in the
# WHERE clause, so a foreign run, a run that is not waiting, and a second click
# on the same approval all return no row (the API answers 409). The status goes
# back to running for the resumed graph, which finishes it normally.
CLAIM_APPROVAL_SQL = """
UPDATE public.runs SET status = 'running'
WHERE id = $1 AND tenant_id = $2 AND status = 'waiting_approval'
RETURNING id
"""

LIST_RUNS_SQL = f"""
SELECT {RUN_COLUMNS}
FROM public.runs
WHERE tenant_id = $1
  AND ($3::text IS NULL OR session_id = $3)
ORDER BY started_at DESC
LIMIT $2
"""

# One row per conversation. The first query is the stable title; the newest
# row supplies the status and plan. A sessionless run is not a conversation and
# is excluded, so the sidebar never repeats one conversation as several rows.
LIST_SESSIONS_SQL = """
WITH firsts AS (
    SELECT DISTINCT ON (session_id)
        session_id, tenant_id, query AS title, started_at
    FROM public.runs
    WHERE tenant_id = $1 AND session_id IS NOT NULL
    ORDER BY session_id, started_at ASC, id ASC
),
lasts AS (
    SELECT DISTINCT ON (session_id)
        session_id, query AS last_query, status, plan, started_at AS last_run_at
    FROM public.runs
    WHERE tenant_id = $1 AND session_id IS NOT NULL
    ORDER BY session_id, started_at DESC, id DESC
),
counts AS (
    SELECT session_id, count(*) AS run_count
    FROM public.runs
    WHERE tenant_id = $1 AND session_id IS NOT NULL
    GROUP BY session_id
)
SELECT f.session_id, f.tenant_id, COALESCE(m.title, f.title) AS title,
       l.last_query, l.status, l.plan, c.run_count, f.started_at, l.last_run_at,
       m.deleted_at
FROM firsts f
JOIN lasts l USING (session_id)
JOIN counts c USING (session_id)
LEFT JOIN public.session_metadata m
       ON m.tenant_id = f.tenant_id AND m.session_id = f.session_id
WHERE $3::boolean OR m.deleted_at IS NULL
ORDER BY l.last_run_at DESC, f.session_id DESC
LIMIT $2
"""

INSERT_SESSION_AUDIT_SQL = """
INSERT INTO public.session_audit (tenant_id, session_id, action, actor)
VALUES ($1, $2, $3, $4)
"""

LIST_SESSION_AUDIT_SQL = """
SELECT id::text AS id, tenant_id, session_id, action, actor, created_at
FROM public.session_audit
WHERE tenant_id = $1
ORDER BY created_at DESC, id DESC
LIMIT $2
"""

LIST_USERS_SQL = """
SELECT u.id, u.email, u.display_name, u.created_at, m.role
FROM public.users u
JOIN public.memberships m ON m.user_id = u.id
WHERE m.tenant_id = $1
ORDER BY u.display_name ASC, u.email ASC
"""

UPSERT_USER_SQL = """
INSERT INTO public.users (id, email, display_name)
VALUES ($1, $2, $3)
ON CONFLICT (email) DO UPDATE SET display_name = EXCLUDED.display_name
RETURNING id, email, display_name, created_at
"""

UPSERT_MEMBERSHIP_SQL = """
INSERT INTO public.memberships (tenant_id, user_id, role)
VALUES ($1, $2, $3)
ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role
"""

LIST_DOCUMENTS_SQL = """
SELECT d.id::text AS id, d.source, d.title, d.content_hash, d.ingested_at,
       count(c.id)::int AS chunks
FROM public.documents d
LEFT JOIN public.chunks c
       ON c.document_id = d.id AND c.tenant_id = d.tenant_id
WHERE d.tenant_id = $1
GROUP BY d.id, d.source, d.title, d.content_hash, d.ingested_at
ORDER BY d.ingested_at DESC, d.source ASC
"""

LIST_DOCUMENT_CHUNKS_SQL = """
SELECT c.ordinal, c.text
FROM public.chunks c
JOIN public.documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id
WHERE d.id = $1 AND d.tenant_id = $2
ORDER BY c.ordinal ASC
"""

DELETE_DOCUMENT_SQL = """
DELETE FROM public.documents
WHERE id = $1 AND tenant_id = $2
RETURNING id
"""

BUMP_DOC_INDEX_SQL = """
UPDATE public.tenants
SET doc_index_version = doc_index_version + 1
WHERE id = $1
"""

SESSION_EXISTS_SQL = """
SELECT 1 FROM public.runs
WHERE tenant_id = $1 AND session_id = $2
LIMIT 1
"""

RENAME_SESSION_SQL = """
INSERT INTO public.session_metadata (tenant_id, session_id, title, updated_at)
VALUES ($1, $2, $3, now())
ON CONFLICT (tenant_id, session_id) DO UPDATE
SET title = EXCLUDED.title, deleted_at = NULL, updated_at = now()
"""

DELETE_SESSION_SQL = """
INSERT INTO public.session_metadata (tenant_id, session_id, deleted_at, updated_at)
VALUES ($1, $2, now(), now())
ON CONFLICT (tenant_id, session_id) DO UPDATE
SET deleted_at = now(), updated_at = now()
"""

RESTORE_SESSION_SQL = """
UPDATE public.session_metadata
SET deleted_at = NULL, updated_at = now()
WHERE tenant_id = $1 AND session_id = $2 AND deleted_at IS NOT NULL
RETURNING session_id
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

# Feedback is append-only (05-feedback.sql): the API inserts and reads, and a
# new rating is a new row. Both statements carry the tenant, and the table's
# composite foreign key rejects a row that would point at another tenant's run
# (design.md sections 11 and 14).
FEEDBACK_COLUMNS = "id, run_id, tenant_id, rating, note, created_at"

INSERT_FEEDBACK_SQL = f"""
INSERT INTO public.feedback (run_id, tenant_id, rating, note)
VALUES ($1, $2, $3, $4)
RETURNING {FEEDBACK_COLUMNS}
"""

LATEST_FEEDBACK_SQL = f"""
SELECT {FEEDBACK_COLUMNS}
FROM public.feedback
WHERE run_id = $1 AND tenant_id = $2
ORDER BY created_at DESC, id DESC
LIMIT 1
"""

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
    # A waiting run carries the tool call the graph paused on (T3.01). It is
    # null for every other status.
    payload["pending_approval"] = decode_json(payload.get("pending_approval"), None)
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


def feedback_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One feedback row in the shape the API returns."""
    payload = dict(row)
    payload["id"] = str(payload["id"])
    payload["run_id"] = str(payload["run_id"])
    payload["rating"] = int(payload["rating"])
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

    async def claim_approval(self, *, run_id: str, tenant_id: str) -> bool: ...

    async def list_runs(
        self,
        *,
        tenant_id: str,
        limit: int = 50,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def list_sessions(
        self, *, tenant_id: str, limit: int = 20, include_deleted: bool = False
    ) -> list[dict[str, Any]]: ...

    async def rename_session(self, *, tenant_id: str, session_id: str, title: str) -> bool: ...

    async def delete_session(self, *, tenant_id: str, session_id: str) -> bool: ...

    async def restore_session(self, *, tenant_id: str, session_id: str) -> bool: ...

    async def record_session_action(
        self,
        *,
        tenant_id: str,
        session_id: str,
        action: Literal["rename", "delete", "restore", "bulk_delete"],
        actor: str,
    ) -> None: ...

    async def list_session_actions(
        self, *, tenant_id: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    async def list_users(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def create_user(
        self,
        *,
        tenant_id: str,
        email: str,
        display_name: str,
        role: Literal["admin", "member"],
    ) -> dict[str, Any]: ...

    async def list_documents(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def list_document_chunks(
        self, *, tenant_id: str, document_id: str
    ) -> list[dict[str, Any]]: ...

    async def reindex_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        embeddings: list[list[float]],
    ) -> int | None: ...

    async def delete_document(self, *, tenant_id: str, document_id: str) -> bool: ...

    async def aggregate_runs(
        self, *, tenant_id: str, group_by: Literal["day", "provider"]
    ) -> list[dict[str, Any]]: ...

    async def get_run(self, run_id: str, *, tenant_id: str) -> dict[str, Any] | None: ...

    async def load_session_turns(
        self, *, tenant_id: str, session_id: str, limit: int
    ) -> list[Turn]: ...

    async def fetch_spans(self, run_id: str) -> list[dict[str, Any]]: ...

    async def add_feedback(
        self, *, run_id: str, tenant_id: str, rating: int, note: str | None
    ) -> dict[str, Any]: ...

    async def latest_feedback(self, *, run_id: str, tenant_id: str) -> dict[str, Any] | None: ...


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
        # A paused run has not ended; its ended_at is the real end after approve
        # or reject overwrites this row (T3.01).
        ended_at = (
            None if result.status == "waiting_approval" else result.ended_at or datetime.now(UTC)
        )
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
            (
                json.dumps(result.pending_approval.model_dump(mode="json"))
                if result.pending_approval is not None
                else None
            ),
        )

    async def claim_approval(self, *, run_id: str, tenant_id: str) -> bool:
        row = await self._pool.fetchrow(CLAIM_APPROVAL_SQL, uuid.UUID(run_id), tenant_id)
        return row is not None

    async def list_runs(
        self, *, tenant_id: str, limit: int = 50, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(LIST_RUNS_SQL, tenant_id, limit, session_id)
        return [run_row(row) for row in rows]

    async def list_sessions(
        self, *, tenant_id: str, limit: int = 20, include_deleted: bool = False
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(LIST_SESSIONS_SQL, tenant_id, limit, include_deleted)
        return [dict(row) for row in rows]

    async def rename_session(self, *, tenant_id: str, session_id: str, title: str) -> bool:
        async with self._pool.acquire() as connection, connection.transaction():
            exists = await connection.fetchval(SESSION_EXISTS_SQL, tenant_id, session_id)
            if exists is None:
                return False
            await connection.execute(RENAME_SESSION_SQL, tenant_id, session_id, title)
        return True

    async def delete_session(self, *, tenant_id: str, session_id: str) -> bool:
        async with self._pool.acquire() as connection, connection.transaction():
            exists = await connection.fetchval(SESSION_EXISTS_SQL, tenant_id, session_id)
            if exists is None:
                return False
            await connection.execute(DELETE_SESSION_SQL, tenant_id, session_id)
        return True

    async def restore_session(self, *, tenant_id: str, session_id: str) -> bool:
        row = await self._pool.fetchrow(RESTORE_SESSION_SQL, tenant_id, session_id)
        return row is not None

    async def record_session_action(
        self,
        *,
        tenant_id: str,
        session_id: str,
        action: Literal["rename", "delete", "restore", "bulk_delete"],
        actor: str,
    ) -> None:
        await self._pool.execute(INSERT_SESSION_AUDIT_SQL, tenant_id, session_id, action, actor)

    async def list_session_actions(
        self, *, tenant_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(LIST_SESSION_AUDIT_SQL, tenant_id, limit)
        return [dict(row) for row in rows]

    async def list_users(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(LIST_USERS_SQL, tenant_id)
        return [dict(row) for row in rows]

    async def create_user(
        self,
        *,
        tenant_id: str,
        email: str,
        display_name: str,
        role: Literal["admin", "member"],
    ) -> dict[str, Any]:
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(UPSERT_USER_SQL, str(uuid.uuid4()), email, display_name)
            if row is None:  # pragma: no cover - RETURNING always yields the row
                raise RuntimeError("the user row was not returned")
            await connection.execute(UPSERT_MEMBERSHIP_SQL, tenant_id, row["id"], role)
            result = dict(row)
            result["role"] = role
            return result

    async def list_documents(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(LIST_DOCUMENTS_SQL, tenant_id)
        return [dict(row) for row in rows]

    async def list_document_chunks(
        self, *, tenant_id: str, document_id: str
    ) -> list[dict[str, Any]]:
        try:
            parsed = uuid.UUID(document_id)
        except ValueError:
            return []
        rows = await self._pool.fetch(LIST_DOCUMENT_CHUNKS_SQL, parsed, tenant_id)
        return [dict(row) for row in rows]

    async def reindex_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        embeddings: list[list[float]],
    ) -> int | None:
        """Replace the stored vectors of every chunk and bump the index version."""
        try:
            parsed = uuid.UUID(document_id)
        except ValueError:
            return None
        async with self._pool.acquire() as connection, connection.transaction():
            exists = await connection.fetchval(
                "SELECT 1 FROM public.documents WHERE id = $1 AND tenant_id = $2",
                parsed,
                tenant_id,
            )
            if exists is None:
                return None
            ordinals = await connection.fetch(
                "SELECT ordinal FROM public.chunks WHERE document_id = $1 AND tenant_id = $2 "
                "ORDER BY ordinal ASC",
                parsed,
                tenant_id,
            )
            if len(ordinals) != len(embeddings):
                raise ValueError(
                    f"document has {len(ordinals)} chunks but {len(embeddings)} vectors were given"
                )
            for row, vector in zip(ordinals, embeddings, strict=True):
                await connection.execute(
                    "UPDATE public.chunks SET embedding = $3::vector "
                    "WHERE document_id = $1 AND tenant_id = $2 AND ordinal = $4",
                    parsed,
                    tenant_id,
                    json.dumps(vector, separators=(",", ":")),
                    int(row["ordinal"]),
                )
            version = await connection.fetchval(
                "UPDATE public.tenants SET doc_index_version = doc_index_version + 1 "
                "WHERE id = $1 RETURNING doc_index_version",
                tenant_id,
            )
        return None if version is None else int(version)

    async def delete_document(self, *, tenant_id: str, document_id: str) -> bool:
        """Delete one document and its chunks, then invalidate the tenant index version."""
        try:
            parsed = uuid.UUID(document_id)
        except ValueError:
            return False
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(DELETE_DOCUMENT_SQL, parsed, tenant_id)
            if row is None:
                return False
            await connection.execute(BUMP_DOC_INDEX_SQL, tenant_id)
        return True

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

    async def add_feedback(
        self, *, run_id: str, tenant_id: str, rating: int, note: str | None
    ) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            INSERT_FEEDBACK_SQL, uuid.UUID(run_id), tenant_id, rating, note
        )
        if row is None:  # pragma: no cover - RETURNING always yields the row
            raise RuntimeError("the feedback row was not returned")
        return feedback_row(row)

    async def latest_feedback(self, *, run_id: str, tenant_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(LATEST_FEEDBACK_SQL, uuid.UUID(run_id), tenant_id)
        return None if row is None else feedback_row(row)


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
    "BUMP_DOC_INDEX_SQL",
    "CLAIM_APPROVAL_SQL",
    "DELETE_DOCUMENT_SQL",
    "INSERT_SESSION_AUDIT_SQL",
    "LIST_DOCUMENTS_SQL",
    "LIST_DOCUMENT_CHUNKS_SQL",
    "LIST_SESSION_AUDIT_SQL",
    "LIST_USERS_SQL",
    "SESSION_TURNS_SQL",
    "UPSERT_MEMBERSHIP_SQL",
    "UPSERT_USER_SQL",
    "PgRunStore",
    "RunStore",
    "aggregate_row",
    "decode_json",
    "feedback_row",
    "parse_run_id",
    "rows_are_complete",
    "run_row",
    "session_turns",
    "span_row",
]
