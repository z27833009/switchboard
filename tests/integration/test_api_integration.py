"""API end to end against the running PostgreSQL and the real MCP server.

    uv run --env-file .env pytest tests/integration/test_api_integration.py -q

No LM Studio is involved: the orchestrator is a small scripted one that makes
one real ``orders_summary@1`` call through ``McpToolSource`` using the run token
the API minted. This covers the token contract (ADR 0014), the SSE stream, the
run row write, the flush and the trace read in one path.

Skipped unless the compose stack and ``.env`` are present. CI runs it once the
PostgreSQL service exists (T1.16).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer
from tests.fakes import FailingOrchestrator, tenants_config

from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.api.app import create_app
from switchboard.api.deps import AppDeps
from switchboard.api.step_events import StepEventProcessor, StepEventPublisher
from switchboard.api.store import PgRunStore
from switchboard.core.models import (
    AgentResult,
    ChatRequest,
    Citation,
    RoutingDecision,
    RunContext,
    RunResult,
    Usage,
)
from switchboard.tracing import PostgresSpanExporter, TracingHandle
from switchboard.tracing.spans import agent_span, run_span, set_attributes, supervisor_span

MCP_URL = os.environ.get("MCP_OLIST_URL", "").rstrip("/")
APP_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MCP_URL and APP_URL and ADMIN_URL and SECRET),
        reason="set MCP_OLIST_URL and the database/secret variables",
    ),
]

QUERY = "How many orders were placed in March 2018?"


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event_type = next(line[len("event: ") :] for line in lines if line.startswith("event: "))
        data = json.loads(
            next(line[len("data: ") :] for line in lines if line.startswith("data: "))
        )
        events.append((event_type, data))
    return events


class LiveToolOrchestrator:
    """One plan, one real tool call; enough to exercise the whole API path."""

    name = "live-tool"

    def __init__(self, *, tracer: Tracer, source: McpToolSource) -> None:
        self._tracer = tracer
        self._source = source

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        started = datetime.now(UTC)
        with run_span(
            self._tracer,
            tenant_id=ctx.tenant.tenant_id,
            run_id=ctx.run_id,
            orchestrator=self.name,
        ):
            with supervisor_span(self._tracer, run_id=ctx.run_id) as plan:
                set_attributes(
                    plan,
                    {
                        "switchboard.plan": "data",
                        "switchboard.plan.reason": "orders question",
                    },
                )
            with agent_span(self._tracer, agent="data", run_id=ctx.run_id) as agent:
                result = await self._source.call(
                    "orders_summary@1",
                    {"date_from": "2018-03-01", "date_to": "2018-03-31"},
                    ctx,
                )
                set_attributes(
                    agent,
                    {
                        "switchboard.partial": False,
                        "switchboard.citations": 1,
                        "switchboard.tool.rows": result.total_rows,
                    },
                )
            with self._tracer.start_as_current_span(
                "gate", attributes={"switchboard.run_id": ctx.run_id}
            ) as gate:
                set_attributes(
                    gate,
                    {"switchboard.gate.verdict": "pass", "switchboard.gate.action": "pass"},
                )
        return RunResult(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant.tenant_id,
            query=request.query,
            plan=RoutingDecision(agents=["data"], reason="orders question"),
            status="ok",
            usage=Usage(input_tokens=0, output_tokens=0),
            answer=AgentResult(
                answer=f"orders_summary@1 returned {result.total_rows} rows (t1).",
                citations=[
                    Citation(
                        kind="tool",
                        ref="t1",
                        source_id="orders_summary@1",
                        audit_id=result.audit_id,
                        snippet=f"{result.total_rows} rows",
                    )
                ],
                usage=Usage(input_tokens=0, output_tokens=0),
            ),
            started_at=started,
            ended_at=datetime.now(UTC),
        )


async def test_chat_stream_persists_the_run_and_its_trace() -> None:
    publisher = StepEventPublisher()
    provider = TracerProvider()
    provider.add_span_processor(StepEventProcessor(publisher))
    provider.add_span_processor(BatchSpanProcessor(PostgresSpanExporter(APP_URL)))
    handle = TracingHandle(provider=provider)
    tracer = provider.get_tracer("switchboard.test.api_integration")
    source = McpToolSource(url=MCP_URL, tracer=tracer)
    orchestrator = LiveToolOrchestrator(tracer=tracer, source=source)

    pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=4)
    deps = AppDeps(
        store=PgRunStore(pool),
        orchestrators={"lmstudio": orchestrator},
        tenants=tenants_config(),
        run_token_secret=SECRET,
        run_timeout_s=60.0,
        publisher=publisher,
        tracing=handle,
    )
    app = create_app(deps=deps)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0)
    try:
        async with client.stream(
            "POST", "/chat/stream", json={"query": QUERY, "tenant_id": "default"}
        ) as response:
            assert response.status_code == 200, await response.aread()
            assert response.headers["content-type"].startswith("text/event-stream")
            body = await response.aread()

        events = parse_sse(body.decode("utf-8"))
        types = [event_type for event_type, _ in events]
        assert types[0] == "run.started"
        assert "plan.decided" in types
        assert "agent.started" in types
        assert "tool.called" in types
        assert "agent.finished" in types
        assert "gate.checked" in types
        assert types[-1] == "run.finished"

        run_id = events[0][1]["run_id"]
        tool_event = next(payload for name, payload in events if name == "tool.called")
        assert tool_event["status"] == "ok"
        assert tool_event["tool"] == "orders_summary@1"
        assert isinstance(tool_event["total_rows"], int)
        assert tool_event["total_rows"] > 0
        audit_id = tool_event["audit_id"]
        assert isinstance(audit_id, str) and uuid.UUID(audit_id)

        finished = events[-1][1]
        assert finished["status"] == "ok"
        assert finished["trace_incomplete"] is False
        assert finished["citations"][0]["audit_id"] == audit_id

        # Read-after-run: no sleep, no retry (AC-05d).
        trace_response = await client.get(f"/runs/{run_id}/trace")
        assert trace_response.status_code == 200
        trace = trace_response.json()
        assert trace["incomplete"] is False
        assert trace["trace_id"] is not None

        def flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
            flat: list[dict[str, Any]] = []
            for node in nodes:
                flat.append(node)
                flat.extend(flatten(node["children"]))
            return flat

        spans = flatten(trace["spans"])
        names = [span["name"] for span in spans]
        assert names.count("execute_tool orders_summary@1") == 2, names
        client_span = next(
            span
            for span in spans
            if span["name"] == "execute_tool orders_summary@1" and span["kind"] == "client"
        )
        server_span = next(
            span
            for span in spans
            if span["name"] == "execute_tool orders_summary@1" and span["kind"] == "server"
        )
        assert server_span["parent_span_id"] == client_span["span_id"]
        assert client_span["attributes"]["switchboard.tool.audit_id"] == audit_id
        assert server_span["attributes"]["switchboard.tool.audit_id"] == audit_id
        assert server_span["attributes"]["switchboard.tool.total_rows"] == tool_event["total_rows"]
        tool_events = [event for event in server_span["events"] if event["name"] == "tool.result"]
        assert len(tool_events) == 1

        detail = await client.get(f"/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "ok"
        assert str(tool_event["total_rows"]) in (detail.json()["answer"] or "")
        assert detail.json()["trace_complete"] is True

        listed = await client.get("/runs", params={"tenant_id": "default", "limit": 20})
        assert listed.status_code == 200
        assert run_id in {run["run_id"] for run in listed.json()["runs"]}
    finally:
        await client.aclose()
        await pool.close()
        provider.shutdown()

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        run_row = await connection.fetchrow(
            "SELECT status, plan, answer, trace_complete, ended_at FROM public.runs WHERE id = $1",
            uuid.UUID(run_id),
        )
        audit = await connection.fetchrow(
            "SELECT tenant_id, tool_name, status FROM public.tool_audit WHERE run_id = $1",
            uuid.UUID(run_id),
        )
    finally:
        await connection.close()

    assert run_row is not None
    assert run_row["status"] == "ok"
    assert run_row["plan"] == "data"
    assert run_row["trace_complete"] is True
    assert run_row["ended_at"] is not None
    assert audit is not None
    assert audit["tenant_id"] == "default"
    assert audit["tool_name"] == "orders_summary"
    assert audit["status"] == "ok"


async def test_failed_run_persists_its_errors_and_is_marked_incomplete() -> None:
    pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=2)
    deps = AppDeps(
        store=PgRunStore(pool),
        orchestrators={"lmstudio": FailingOrchestrator()},
        tenants=tenants_config(),
        run_token_secret=SECRET,
        run_timeout_s=60.0,
    )
    app = create_app(deps=deps)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30.0)
    try:
        response = await client.post("/chat", json={"query": QUERY, "tenant_id": "default"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "provider_error"
        assert body["errors"][0]["kind"] == "RuntimeError"

        trace = await client.get(f"/runs/{body['run_id']}/trace")
        assert trace.status_code == 200
        # No run span exists, so the tree is empty and honestly marked incomplete.
        assert trace.json()["incomplete"] is True
        assert trace.json()["span_count"] == 0
    finally:
        await client.aclose()
        await pool.close()

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        run_row = await connection.fetchrow(
            "SELECT status, errors, answer, trace_complete FROM public.runs WHERE id = $1",
            uuid.UUID(body["run_id"]),
        )
    finally:
        await connection.close()

    assert run_row is not None
    assert run_row["status"] == "provider_error"
    assert run_row["answer"] is None
    assert run_row["trace_complete"] is True
    errors = run_row["errors"]
    assert isinstance(errors, str)
    assert json.loads(errors)[0]["kind"] == "RuntimeError"
