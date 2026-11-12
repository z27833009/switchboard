"""Session turns and the clarification status against the running PostgreSQL.

    uv run --env-file .env pytest tests/integration/test_sessions_integration.py -q

The orchestrator is scripted and makes no model call, so this covers the real
``runs`` table, the tenant and session filter in SQL, the API history loader and
the persisted ``needs_clarification`` status without LM Studio.

Skipped unless ``.env`` provides the database URL and the run token secret.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from tests.fakes import tenants_config

from switchboard.api.app import create_app
from switchboard.api.audit import PgAuditStore
from switchboard.api.deps import AppDeps
from switchboard.api.store import PgRunStore
from switchboard.core.models import (
    AgentResult,
    ChatRequest,
    RunContext,
    RunResult,
    RunStatus,
    Usage,
)

APP_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (APP_URL and ADMIN_URL and SECRET),
        reason="set SWITCHBOARD_DATABASE_URL, SWITCHBOARD_DATABASE_ADMIN_URL and the secret",
    ),
]


class ScriptedOrchestrator:
    """Records the history the API handed it and returns a deterministic result."""

    name = "scripted"

    def __init__(self, *, clarify: bool = False) -> None:
        self.clarify = clarify
        self.histories: list[list[Any]] = []

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        self.histories.append(list(request.history))
        started = datetime.now(UTC)
        if self.clarify:
            status: RunStatus = "needs_clarification"
            answer = AgentResult(
                answer="Which order or seller do you mean?",
                usage=Usage(input_tokens=1, output_tokens=1),
                needs_clarification=True,
            )
        else:
            status = "ok"
            answer = AgentResult(
                answer=f"answer to {request.query}",
                usage=Usage(input_tokens=1, output_tokens=1),
            )
        return RunResult(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant.tenant_id,
            query=request.query,
            status=status,
            answer=answer,
            usage=Usage(input_tokens=1, output_tokens=1),
            started_at=started,
            ended_at=datetime.now(UTC),
        )


async def test_store_reads_only_this_tenants_session_oldest_first() -> None:
    pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=2)
    store = PgRunStore(pool)
    session = f"it-{uuid.uuid4()}"
    try:
        for index, tenant in enumerate(["default", "default", "default", "seller_a"]):
            run_id = str(uuid.uuid4())
            await store.start_run(
                run_id=run_id,
                tenant_id=tenant,
                session_id=session,
                query=f"question {index}",
                orchestrator="scripted",
                provider="lmstudio",
            )
            await store.finish_run(
                RunResult(
                    run_id=run_id,
                    tenant_id=tenant,
                    query=f"question {index}",
                    status="ok",
                    answer=AgentResult(
                        answer=f"answer {index}", usage=Usage(input_tokens=0, output_tokens=0)
                    ),
                    started_at=datetime.now(UTC),
                    ended_at=datetime.now(UTC),
                ),
                trace_complete=True,
            )

        turns = await store.load_session_turns(tenant_id="default", session_id=session, limit=2)
        assert [(turn.role, turn.content) for turn in turns] == [
            ("user", "question 1"),
            ("assistant", "answer 1"),
            ("user", "question 2"),
            ("assistant", "answer 2"),
        ]
        seller_turns = await store.load_session_turns(
            tenant_id="seller_a", session_id=session, limit=2
        )
        assert [(turn.role, turn.content) for turn in seller_turns] == [
            ("user", "question 3"),
            ("assistant", "answer 3"),
        ]
    finally:
        await pool.close()


async def test_http_follow_up_uses_stored_turns_and_clarify_status_persists() -> None:
    pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=4)
    orchestrator = ScriptedOrchestrator()
    deps = AppDeps(
        store=PgRunStore(pool),
        audit=PgAuditStore(pool),
        orchestrators={"lmstudio": orchestrator},
        tenants=tenants_config(),
        run_token_secret=SECRET,
        run_timeout_s=60.0,
        history_turns=3,
    )
    app = create_app(deps=deps)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30.0)
    session = f"it-{uuid.uuid4()}"
    try:
        first = await client.post(
            "/chat",
            json={
                "query": "How many orders were delivered late in March 2018?",
                "tenant_id": "default",
                "session_id": session,
            },
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            "/chat",
            json={"query": "And in April?", "tenant_id": "default", "session_id": session},
        )
        assert second.status_code == 200, second.text
        assert second.json()["session_id"] == session

        assert orchestrator.histories[0] == []
        assert [turn.content for turn in orchestrator.histories[1]] == [
            "How many orders were delivered late in March 2018?",
            "answer to How many orders were delivered late in March 2018?",
        ]

        clarify_orchestrator = ScriptedOrchestrator(clarify=True)
        clarify_deps = AppDeps(
            store=PgRunStore(pool),
            audit=PgAuditStore(pool),
            orchestrators={"lmstudio": clarify_orchestrator},
            tenants=tenants_config(),
            run_token_secret=SECRET,
            run_timeout_s=60.0,
        )
        clarify_app = create_app(deps=clarify_deps)
        clarify_client = AsyncClient(
            transport=ASGITransport(app=clarify_app), base_url="http://test", timeout=30.0
        )
        try:
            ambiguous = await clarify_client.post(
                "/chat",
                json={
                    "query": "How many were late?",
                    "tenant_id": "default",
                    "session_id": session,
                },
            )
        finally:
            await clarify_client.aclose()
        assert ambiguous.status_code == 200, ambiguous.text
        body = ambiguous.json()
        assert body["status"] == "needs_clarification"
        assert body["answer"] == "Which order or seller do you mean?"
        clarify_run_id = body["run_id"]
    finally:
        await client.aclose()
        await pool.close()

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        row = await connection.fetchrow(
            "SELECT session_id, status, answer FROM public.runs WHERE id = $1",
            uuid.UUID(clarify_run_id),
        )
    finally:
        await connection.close()

    assert row is not None
    assert row["session_id"] == session
    assert row["status"] == "needs_clarification"
    assert row["answer"] == "Which order or seller do you mean?"
