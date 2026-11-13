"""Approve and reject endpoints (T3.01, AC-15b).

The run row is the source of truth: a waiting run, its pending tool call and the
tenant all come from storage, so these tests drive the real routes with a fake
resumable orchestrator and the in-memory store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from tests.fakes import AppHarness, FakeOrchestrator, MemoryRunStore, build_harness, make_provider

from switchboard.api.step_events import StepEventPublisher
from switchboard.core.config import TenantsConfig
from switchboard.core.errors import ApprovalUnavailable
from switchboard.core.models import (
    AgentResult,
    ApprovalDecision,
    ChatRequest,
    PendingApproval,
    RunContext,
    RunResult,
    Usage,
)

PENDING = PendingApproval(
    request_id="req-1",
    tool="add_order_note@1",
    arguments={"order_id": "abc123", "note": "Call before delivery."},
    reason="the tenant lists this tool in tools.ask",
)


class PausedOrchestrator:
    """Pauses on every run and records what the API asked it to resume."""

    name = "langgraph"

    def __init__(self, *, fail_resume: bool = False) -> None:
        self.resumes: list[tuple[str, ApprovalDecision, RunContext | None]] = []
        self.run_count = 0
        self._fail_resume = fail_resume

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        self.run_count += 1
        return RunResult(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant.tenant_id,
            query=request.query,
            status="waiting_approval",
            pending_approval=PENDING,
            started_at=datetime.now(UTC),
        )

    async def resume(
        self, run_id: str, decision: ApprovalDecision, *, ctx: RunContext | None = None
    ) -> RunResult:
        if self._fail_resume:
            raise ApprovalUnavailable("the paused state is gone")
        self.resumes.append((run_id, decision, ctx))
        tenant_id = ctx.tenant.tenant_id if ctx is not None else "seller_a"
        now = datetime.now(UTC)
        if not decision.approved:
            return RunResult(
                run_id=run_id,
                tenant_id=tenant_id,
                query="Add a note",
                status="rejected",
                answer=AgentResult(
                    answer="The tool call add_order_note@1 was rejected.",
                    usage=Usage(input_tokens=0, output_tokens=0),
                ),
                started_at=now,
                ended_at=now,
            )
        return RunResult(
            run_id=run_id,
            tenant_id=tenant_id,
            query="Add a note",
            status="ok",
            answer=AgentResult(
                answer="The note was added [t1].",
                usage=Usage(input_tokens=1, output_tokens=1),
            ),
            started_at=now,
            ended_at=now,
        )


def harness_with(tenants: TenantsConfig, orchestrator: Any) -> AppHarness:
    publisher = StepEventPublisher()
    store = MemoryRunStore()
    provider = make_provider(publisher, store)
    return build_harness(
        tenants=tenants,
        store=store,
        publisher=publisher,
        provider=provider,
        orchestrator=orchestrator,
    )


async def start_paused_run(harness: AppHarness) -> str:
    """A waiting run row, as POST /chat would leave it."""
    response = await harness.client.post(
        "/chat",
        json={"query": "Add a note to order abc123", "tenant_id": "seller_a"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "waiting_approval"
    assert body["pending_approval"]["tool"] == "add_order_note@1"
    assert body["ended_at"] is None
    return str(body["run_id"])


async def test_chat_pauses_with_the_pending_call_visible(tenants: TenantsConfig) -> None:
    harness = harness_with(tenants, PausedOrchestrator())
    try:
        run_id = await start_paused_run(harness)
        row = harness.store.runs[run_id]
        assert row["status"] == "waiting_approval"
        assert row["pending_approval"]["request_id"] == "req-1"

        detail = await harness.client.get(f"/runs/{run_id}?tenant_id=seller_a")
        assert detail.status_code == 200
        assert detail.json()["pending_approval"]["arguments"] == PENDING.arguments
        assert detail.json()["status"] == "waiting_approval"
    finally:
        await harness.client.aclose()


async def test_approve_resumes_with_the_row_decision(tenants: TenantsConfig) -> None:
    orchestrator = PausedOrchestrator()
    harness = harness_with(tenants, orchestrator)
    try:
        run_id = await start_paused_run(harness)
        response = await harness.client.post(
            f"/runs/{run_id}/approve?tenant_id=seller_a",
            json={"tool": "delete_everything", "request_id": "forged"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
        # The tenant and the decision come from the row, never from the body.
        (resumed_id, decision, ctx) = orchestrator.resumes[0]
        assert resumed_id == run_id
        assert decision.request_id == "req-1"
        assert decision.approved is True
        assert ctx is not None and ctx.tenant.tenant_id == "seller_a"
        assert ctx.run_id == run_id

        row = harness.store.runs[run_id]
        assert row["status"] == "ok"
        assert row["pending_approval"] is None
        assert row["ended_at"] is not None
    finally:
        await harness.client.aclose()


async def test_reject_ends_the_run_without_resuming_the_tool(tenants: TenantsConfig) -> None:
    orchestrator = PausedOrchestrator()
    harness = harness_with(tenants, orchestrator)
    try:
        run_id = await start_paused_run(harness)
        response = await harness.client.post(f"/runs/{run_id}/reject?tenant_id=seller_a")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "rejected"
        assert body["answer"] == "The tool call add_order_note@1 was rejected."
        assert orchestrator.resumes[0][1].approved is False
        assert harness.store.runs[run_id]["status"] == "rejected"
    finally:
        await harness.client.aclose()


async def test_a_second_decision_is_a_conflict(tenants: TenantsConfig) -> None:
    orchestrator = PausedOrchestrator()
    harness = harness_with(tenants, orchestrator)
    try:
        run_id = await start_paused_run(harness)
        first = await harness.client.post(f"/runs/{run_id}/approve?tenant_id=seller_a")
        assert first.status_code == 200
        second = await harness.client.post(f"/runs/{run_id}/approve?tenant_id=seller_a")
        assert second.status_code == 409
        # The same run cannot be rejected after it finished either.
        third = await harness.client.post(f"/runs/{run_id}/reject?tenant_id=seller_a")
        assert third.status_code == 409
        assert len(orchestrator.resumes) == 1
    finally:
        await harness.client.aclose()


async def test_a_run_that_is_not_waiting_is_a_conflict(tenants: TenantsConfig) -> None:
    harness = harness_with(
        tenants, FakeOrchestrator(tracer=TracerProvider().get_tracer("switchboard.test"))
    )
    try:
        response = await harness.client.post(
            "/chat", json={"query": "hello", "tenant_id": "seller_a"}
        )
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        for action in ("approve", "reject"):
            decision = await harness.client.post(f"/runs/{run_id}/{action}?tenant_id=seller_a")
            assert decision.status_code == 409, decision.text
    finally:
        await harness.client.aclose()


async def test_another_tenant_cannot_decide_a_foreign_run(tenants: TenantsConfig) -> None:
    orchestrator = PausedOrchestrator()
    harness = harness_with(tenants, orchestrator)
    try:
        run_id = await start_paused_run(harness)
        for action in ("approve", "reject"):
            response = await harness.client.post(f"/runs/{run_id}/{action}?tenant_id=default")
            assert response.status_code == 404, response.text
        assert orchestrator.resumes == []
        unknown = await harness.client.post(
            "/runs/00000000-0000-0000-0000-000000000000/approve?tenant_id=seller_a"
        )
        assert unknown.status_code == 404
        malformed = await harness.client.post("/runs/not-a-run/approve?tenant_id=seller_a")
        assert malformed.status_code == 404
    finally:
        await harness.client.aclose()


async def test_an_orchestrator_without_the_hook_is_a_conflict(tenants: TenantsConfig) -> None:
    fake = FakeOrchestrator(tracer=TracerProvider().get_tracer("switchboard.test"))
    harness = harness_with(tenants, fake)
    try:
        run_id = "11111111-1111-1111-1111-111111111111"
        await harness.store.start_run(
            run_id=run_id,
            tenant_id="seller_a",
            session_id=None,
            query="Add a note",
            orchestrator="fake",
        )
        row = harness.store.runs[run_id]
        row["status"] = "waiting_approval"
        row["pending_approval"] = PENDING.model_dump(mode="json")
        response = await harness.client.post(f"/runs/{run_id}/approve?tenant_id=seller_a")
        assert response.status_code == 409, response.text
        assert "no approval hook" in response.json()["detail"]
        # The run was not claimed, so it is still waiting.
        assert row["status"] == "waiting_approval"
    finally:
        await harness.client.aclose()


async def test_a_lost_paused_state_is_a_conflict_and_ends_the_run(
    tenants: TenantsConfig,
) -> None:
    harness = harness_with(tenants, PausedOrchestrator(fail_resume=True))
    try:
        run_id = await start_paused_run(harness)
        response = await harness.client.post(f"/runs/{run_id}/approve?tenant_id=seller_a")
        assert response.status_code == 409, response.text
        row = harness.store.runs[run_id]
        assert row["status"] == "provider_error"
        assert row["errors"][0]["kind"] == "ApprovalUnavailable"
    finally:
        await harness.client.aclose()
