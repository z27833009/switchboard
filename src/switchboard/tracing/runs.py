"""Run row persistence (design.md section 12.2).

The run row is written with status ``running`` before the orchestrator creates
its first span, so a trace can be read while the run is still in flight and the
finish step (T1.13) only has to update the row. The ``runs`` table owns the
status values and counters; ``spans.run_id`` has no foreign key to it.
"""

from __future__ import annotations

import uuid

import asyncpg

INSERT_RUN_SQL = """
INSERT INTO public.runs (
    id, tenant_id, session_id, query, orchestrator, provider, status, started_at
) VALUES ($1, $2, $3, $4, $5, $6, 'running', now())
ON CONFLICT (id) DO NOTHING
"""


async def insert_run(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    tenant_id: str,
    query: str,
    orchestrator: str,
    session_id: str | None = None,
    provider: str | None = None,
) -> None:
    """Insert the run row with status ``running`` at the start of a run."""
    await pool.execute(
        INSERT_RUN_SQL,
        uuid.UUID(run_id),
        tenant_id,
        session_id,
        query,
        orchestrator,
        provider,
    )
