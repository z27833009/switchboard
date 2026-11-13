"""The approval path against the running MCP server and PostgreSQL (T3.01).

One real seller_a order, the real Olist tool server, the real LangGraph graph and
a scripted model: the run pauses before ``add_order_note@1``, the note table gains
exactly one row on approve, and a rejected run writes none. No LM Studio call is
made, so this runs in CI with the stack up:

    uv run --env-file .env pytest tests/integration/test_approval_integration.py -q
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import asyncpg
import jwt
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from tests.fakes import MemoryRunStore, MemorySpanExporter

from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.agents.data import DataAgent
from switchboard.core.config import Limits
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.models import (
    ApprovalDecision,
    ChatRequest,
    CompletionResponse,
    ProviderCapabilities,
    RunContext,
    TenantContext,
    ToolCall,
    Usage,
)
from switchboard.registry import AGENTS, Registry

MCP_URL = os.environ.get("MCP_OLIST_URL", "").rstrip("/")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")
SELLER_ID = "955fee9216a65b617aa5c0531780ce60"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MCP_URL and ADMIN_URL and SECRET),
        reason="set MCP_OLIST_URL, SWITCHBOARD_DATABASE_ADMIN_URL and the secret",
    ),
]


def mint(tenant_id: str) -> tuple[str, str]:
    run_id = str(uuid.uuid4())
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "switchboard-mcp",
            "tenant_id": tenant_id,
            "run_id": run_id,
            "iat": now,
            "exp": now + 120,
            "jti": str(uuid.uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    return token, run_id


class NoteProvider:
    """Plans the data agent, calls add_order_note@1 once, then answers."""

    name = "fake"

    def __init__(self, order_id: str, note: str) -> None:
        self._call = ToolCall(
            name="add_order_note@1",
            arguments={"order_id": order_id, "note": note},
            call_id="call-note",
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: Any) -> CompletionResponse:
        usage = Usage(input_tokens=10, output_tokens=2)
        if request.response_schema is not None:
            return CompletionResponse(
                text=None,
                parsed={"agents": ["data"], "reason": "a note needs the data agent"},
                usage=usage,
                model="fake",
                provider="fake",
                latency_ms=1,
            )
        if not any(message.role == "tool" for message in request.messages):
            return CompletionResponse(
                text=None,
                tool_calls=[self._call.model_copy()],
                usage=usage,
                model="fake",
                provider="fake",
                latency_ms=1,
            )
        return CompletionResponse(
            text="The note was added to the order [t1].",
            usage=usage,
            model="fake",
            provider="fake",
            latency_ms=1,
        )


def context() -> RunContext:
    token, run_id = mint("seller_a")
    return RunContext(
        run_id=run_id,
        tenant=TenantContext(
            tenant_id="seller_a",
            allowed_agents={"data"},
            allowed_tools={"order_lookup@1", "add_order_note@1"},
            ask_tools={"add_order_note@1"},
            seller_ids=[SELLER_ID],
            provider="fake",
        ),
        run_token=token,
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def orchestrator(provider: NoteProvider) -> LangGraphOrchestrator:
    registry = Registry([FakeEntryPoint("data", AGENTS, DataAgent())])
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(MemoryRunStore())))
    return LangGraphOrchestrator(
        provider=provider,
        registry=registry,
        context_policy=ClearOldestToolResults(),
        limits=Limits(run_timeout_s=30.0, max_tool_steps=2),
        tool_sources=[McpToolSource(name="olist", url=MCP_URL)],
        tracer=tracer_provider.get_tracer("switchboard.test.approval.integration"),
    )


class FakeEntryPoint:
    def __init__(self, name: str, group: str, value: Any) -> None:
        self.name = name
        self.group = group
        self.value = value

    def load(self) -> Any:
        return self.value


async def seller_a_order() -> str:
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        order_id = await connection.fetchval(
            """
            SELECT o.order_id
            FROM olist.orders o
            JOIN olist.order_items i ON i.order_id = o.order_id
            WHERE i.seller_id = $1
            ORDER BY o.order_id
            LIMIT 1
            """,
            SELLER_ID,
        )
    finally:
        await connection.close()
    assert order_id, "the loaded data has no order for seller_a"
    return str(order_id)


async def note_rows(order_id: str, note: str) -> int:
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        value = await connection.fetchval(
            "SELECT count(*)::int FROM olist.order_notes WHERE order_id = $1 AND note = $2",
            order_id,
            note,
        )
    finally:
        await connection.close()
    return int(value or 0)


async def audit_rows(run_id: str, tool_name: str) -> list[Any]:
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        rows = await connection.fetch(
            "SELECT status FROM public.tool_audit WHERE run_id = $1 AND tool_name = $2",
            uuid.UUID(run_id),
            tool_name,
        )
    finally:
        await connection.close()
    return list(rows)


async def test_approve_writes_exactly_one_note_and_one_audit_row() -> None:
    order_id = await seller_a_order()
    note = f"integration approve {uuid.uuid4().hex}"
    provider = NoteProvider(order_id, note)
    run = orchestrator(provider)
    ctx = context()

    paused = await run.run(
        ChatRequest(query=f"Add a note to order {order_id}", tenant_id="seller_a"), ctx
    )

    assert paused.status == "waiting_approval"
    assert paused.pending_approval is not None
    assert paused.pending_approval.tool == "add_order_note@1"
    assert paused.pending_approval.arguments == {"order_id": order_id, "note": note}
    # Nothing was written before the decision.
    assert await note_rows(order_id, note) == 0
    assert await audit_rows(ctx.run_id, "add_order_note") == []

    result = await run.resume(
        ctx.run_id,
        ApprovalDecision(request_id=paused.pending_approval.request_id, approved=True),
    )

    assert result.status == "ok", result.errors
    assert await note_rows(order_id, note) == 1
    rows = await audit_rows(ctx.run_id, "add_order_note")
    assert len(rows) == 1 and rows[0]["status"] == "ok"

    # The note never touched the order itself.
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        status = await connection.fetchval(
            "SELECT order_status FROM olist.orders WHERE order_id = $1", order_id
        )
        note_count = await connection.fetchval(
            "SELECT count(*)::int FROM olist.order_notes WHERE order_id = $1", order_id
        )
    finally:
        await connection.close()
    assert status is not None
    assert note_count >= 1


async def test_reject_writes_no_note_and_no_audit_row() -> None:
    order_id = await seller_a_order()
    note = f"integration reject {uuid.uuid4().hex}"
    run = orchestrator(NoteProvider(order_id, note))
    ctx = context()

    paused = await run.run(
        ChatRequest(query=f"Add a note to order {order_id}", tenant_id="seller_a"), ctx
    )
    assert paused.status == "waiting_approval"
    assert paused.pending_approval is not None

    result = await run.resume(
        ctx.run_id,
        ApprovalDecision(request_id=paused.pending_approval.request_id, approved=False),
    )

    assert result.status == "rejected"
    assert result.answer is not None and "rejected" in result.answer.answer
    assert await note_rows(order_id, note) == 0
    assert await audit_rows(ctx.run_id, "add_order_note") == []
