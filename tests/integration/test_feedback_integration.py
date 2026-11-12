"""Feedback and harvest against the real PostgreSQL (AC-24a, AC-24b).

    uv run --env-file .env pytest tests/integration/test_feedback_integration.py -q

No LM Studio and no MCP server are involved: runs are written with the
application-role store, feedback goes through the HTTP route, and the harvest
reads the same rows. The tests cover the stored row, the tenant rule (404 for
another tenant's run, composite foreign key for a mismatched pair), the
append-only grants of ``switchboard_app`` and the since and tenant filters.

Skipped unless ``.env`` points at the compose database for both roles.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from tests.fakes import FailingOrchestrator, tenants_config

from switchboard.api.app import create_app
from switchboard.api.audit import PgAuditStore
from switchboard.api.deps import AppDeps
from switchboard.api.store import PgRunStore
from switchboard.core.models import AgentResult, RunResult, Usage
from switchboard.eval.harvest import harvest

APP_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
SECRET = "s" * 32

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (APP_URL and ADMIN_URL),
        reason="set SWITCHBOARD_DATABASE_URL and SWITCHBOARD_DATABASE_ADMIN_URL",
    ),
]


@asynccontextmanager
async def app_and_pool() -> AsyncIterator[tuple[AsyncClient, asyncpg.Pool]]:
    pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=4)
    deps = AppDeps(
        store=PgRunStore(pool),
        audit=PgAuditStore(pool),
        orchestrators={"lmstudio": FailingOrchestrator()},
        tenants=tenants_config(),
        run_token_secret=SECRET,
        run_timeout_s=30.0,
    )
    client = AsyncClient(transport=ASGITransport(app=create_app(deps=deps)), base_url="http://test")
    try:
        yield client, pool
    finally:
        await client.aclose()
        await pool.close()


async def create_run(pool: asyncpg.Pool, *, tenant_id: str, answer: str) -> str:
    """One stored run with the same shape a chat leaves behind."""
    run_id = str(uuid.uuid4())
    query = f"feedback integration probe {run_id}"
    started = datetime.now(UTC)
    store = PgRunStore(pool)
    await store.start_run(
        run_id=run_id,
        tenant_id=tenant_id,
        session_id=None,
        query=query,
        orchestrator="scripted",
        provider="mock",
    )
    await store.finish_run(
        RunResult(
            run_id=run_id,
            tenant_id=tenant_id,
            query=query,
            status="ok",
            usage=Usage(input_tokens=0, output_tokens=0),
            answer=AgentResult(answer=answer, usage=Usage(input_tokens=0, output_tokens=0)),
            started_at=started,
            ended_at=datetime.now(UTC),
        ),
        trace_complete=True,
    )
    return run_id


async def delete_runs(connection: asyncpg.Connection, run_ids: list[str]) -> None:
    identifiers = [uuid.UUID(run_id) for run_id in run_ids]
    await connection.execute(
        "DELETE FROM public.feedback WHERE run_id = ANY($1::uuid[])", identifiers
    )
    await connection.execute("DELETE FROM public.runs WHERE id = ANY($1::uuid[])", identifiers)


async def test_feedback_is_stored_read_and_kept_in_its_tenant() -> None:
    admin = await asyncpg.connect(ADMIN_URL)
    run_ids: list[str] = []
    try:
        async with app_and_pool() as (client, pool):
            run_id = await create_run(
                pool, tenant_id="default", answer="The March count is 1328 (t1)."
            )
            run_ids.append(run_id)

            posted = await client.post(
                f"/runs/{run_id}/feedback",
                params={"tenant_id": "default"},
                json={"rating": -1, "note": "  the count looks wrong  "},
            )
            assert posted.status_code == 201, posted.text
            body = posted.json()
            assert body["run_id"] == run_id
            assert body["tenant_id"] == "default"
            assert body["rating"] == -1
            assert body["note"] == "the count looks wrong"
            assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
            feedback_id = uuid.UUID(body["id"])

            stored = await admin.fetchrow(
                "SELECT run_id, tenant_id, rating, note FROM public.feedback WHERE id = $1",
                feedback_id,
            )
            assert stored is not None
            assert stored["run_id"] == uuid.UUID(run_id)
            assert stored["tenant_id"] == "default"
            assert stored["rating"] == -1
            assert stored["note"] == "the count looks wrong"

            own = await client.get(f"/runs/{run_id}", params={"tenant_id": "default"})
            assert own.status_code == 200, own.text
            assert own.json()["latest_feedback"]["id"] == body["id"]
            assert own.json()["latest_feedback"]["rating"] == -1

            # The run and its feedback are invisible to the other tenant.
            other_detail = await client.get(f"/runs/{run_id}", params={"tenant_id": "seller_a"})
            assert other_detail.status_code == 404
            cross_post = await client.post(
                f"/runs/{run_id}/feedback",
                params={"tenant_id": "seller_a"},
                json={"rating": -1},
            )
            assert cross_post.status_code == 404
            assert cross_post.json()["detail"] == "run not found"
            count = await admin.fetchval(
                "SELECT count(*) FROM public.feedback WHERE run_id = $1", uuid.UUID(run_id)
            )
            assert count == 1

            # Invalid input is refused and nothing is written.
            for rejected in (0, 2, -2):
                bad = await client.post(
                    f"/runs/{run_id}/feedback",
                    params={"tenant_id": "default"},
                    json={"rating": rejected},
                )
                assert bad.status_code == 422, (rejected, bad.text)
            too_long = await client.post(
                f"/runs/{run_id}/feedback",
                params={"tenant_id": "default"},
                json={"rating": -1, "note": "x" * 2001},
            )
            assert too_long.status_code == 422
            count = await admin.fetchval(
                "SELECT count(*) FROM public.feedback WHERE run_id = $1", uuid.UUID(run_id)
            )
            assert count == 1

            # No edit and no delete: a change of mind appends a row.
            delete = await client.delete(
                f"/runs/{run_id}/feedback", params={"tenant_id": "default"}
            )
            assert delete.status_code == 405
            second = await client.post(
                f"/runs/{run_id}/feedback",
                params={"tenant_id": "default"},
                json={"rating": 1},
            )
            assert second.status_code == 201, second.text
            latest = await client.get(f"/runs/{run_id}", params={"tenant_id": "default"})
            assert latest.json()["latest_feedback"]["id"] == second.json()["id"]
            assert latest.json()["latest_feedback"]["rating"] == 1
            count = await admin.fetchval(
                "SELECT count(*) FROM public.feedback WHERE run_id = $1", uuid.UUID(run_id)
            )
            assert count == 2
    finally:
        await delete_runs(admin, run_ids)
        await admin.close()


async def test_feedback_grants_are_append_only_and_the_run_pair_is_foreign_keyed() -> None:
    app = await asyncpg.connect(APP_URL)
    admin = await asyncpg.connect(ADMIN_URL)
    run_id = str(uuid.uuid4())
    try:
        await admin.execute(
            """
            INSERT INTO public.runs (id, tenant_id, query, orchestrator, status)
            VALUES ($1, 'default', 'grant probe', 'scripted', 'ok')
            """,
            uuid.UUID(run_id),
        )

        # The application role may append and read.
        feedback_id = await app.fetchval(
            """
            INSERT INTO public.feedback (run_id, tenant_id, rating, note)
            VALUES ($1, 'default', -1, 'grant probe')
            RETURNING id
            """,
            uuid.UUID(run_id),
        )
        assert feedback_id is not None
        selected = await app.fetchrow(
            "SELECT id, rating FROM public.feedback WHERE id = $1", feedback_id
        )
        assert selected is not None
        assert selected["rating"] == -1

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute("UPDATE public.feedback SET rating = 1 WHERE id = $1", feedback_id)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute("DELETE FROM public.feedback WHERE id = $1", feedback_id)

        # A feedback row cannot point at another tenant's run, and the database
        # rejects the pair even when the route is bypassed.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await app.execute(
                """
                INSERT INTO public.feedback (run_id, tenant_id, rating)
                VALUES ($1, 'seller_a', -1)
                """,
                uuid.UUID(run_id),
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await admin.execute(
                """
                INSERT INTO public.feedback (run_id, tenant_id, rating)
                VALUES ($1, 'seller_a', -1)
                """,
                uuid.UUID(run_id),
            )

        # Rating 0 and an oversized note are refused by the table as well.
        with pytest.raises(asyncpg.CheckViolationError):
            await admin.execute(
                """
                INSERT INTO public.feedback (run_id, tenant_id, rating)
                VALUES ($1, 'default', 0)
                """,
                uuid.UUID(run_id),
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await admin.execute(
                """
                INSERT INTO public.feedback (run_id, tenant_id, rating, note)
                VALUES ($1, 'default', -1, $2)
                """,
                uuid.UUID(run_id),
                "x" * 2001,
            )
    finally:
        await delete_runs(admin, [run_id])
        await app.close()
        await admin.close()


async def test_harvest_reads_negative_feedback_since_the_date_for_one_tenant(
    tmp_path: Path,
) -> None:
    admin = await asyncpg.connect(ADMIN_URL)
    run_ids: list[str] = []
    try:
        async with app_and_pool() as (client, pool):
            negative = await create_run(
                pool, tenant_id="default", answer="The March count is 1328 (t1)."
            )
            positive = await create_run(
                pool, tenant_id="default", answer="The return window is 15 days (d1)."
            )
            other_tenant = await create_run(
                pool, tenant_id="seller_a", answer="Seller A had 154 late orders (t1)."
            )
            run_ids.extend([negative, positive, other_tenant])

            ratings = [
                (negative, "default", -1, "the count is wrong"),
                (positive, "default", 1, None),
                (other_tenant, "seller_a", -1, "seller scope missing"),
            ]
            for run_id, tenant_id, rating, note in ratings:
                posted = await client.post(
                    f"/runs/{run_id}/feedback",
                    params={"tenant_id": tenant_id},
                    json={"rating": rating, "note": note},
                )
                assert posted.status_code == 201, posted.text

            today = datetime.now(UTC).date()
            default_result = await harvest(
                pool, since=today, tenant_id="default", directory=tmp_path / "default"
            )
            default_cases = [
                json.loads(line)
                for line in default_result.path.read_text(encoding="utf-8").splitlines()
            ]
            default_ids = {case["run_id"] for case in default_cases}
            assert negative in default_ids
            assert positive not in default_ids
            assert other_tenant not in default_ids
            assert default_result.candidates == len(default_cases)

            case = next(item for item in default_cases if item["run_id"] == negative)
            assert case["candidate_id"] == f"candidate-{negative}"
            assert case["rating"] == -1
            assert case["note"] == "the count is wrong"
            assert case["tenant_id"] == "default"
            assert case["plan"] is None
            assert case["status"] == "ok"
            assert case["query"].startswith("feedback integration probe")
            assert case["answer"] == "The March count is 1328 (t1)."
            assert case["run_started_at"] is not None
            assert case["rated_at"] is not None
            for label in ("expected_agents", "expected_tool", "expected_args", "split"):
                assert label not in case

            # Running the command again rewrites the same file.
            first_text = default_result.path.read_text(encoding="utf-8")
            again = await harvest(
                pool, since=today, tenant_id="default", directory=tmp_path / "default"
            )
            assert again.path == default_result.path
            assert again.path.read_text(encoding="utf-8") == first_text

            # The other tenant gets only its own negative run.
            seller_result = await harvest(
                pool, since=today, tenant_id="seller_a", directory=tmp_path / "seller"
            )
            seller_ids = {
                json.loads(line)["run_id"]
                for line in seller_result.path.read_text(encoding="utf-8").splitlines()
            }
            assert other_tenant in seller_ids
            assert negative not in seller_ids

            # Nothing was rated after tomorrow, so a later since yields no cases.
            tomorrow = today + timedelta(days=1)
            future = await harvest(
                pool, since=tomorrow, tenant_id=None, directory=tmp_path / "future"
            )
            assert future.feedback_rows == 0
            assert future.candidates == 0
            assert future.path.read_text(encoding="utf-8") == ""
    finally:
        await delete_runs(admin, run_ids)
        await admin.close()
