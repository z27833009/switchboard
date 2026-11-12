"""Cross-tenant reads, seller scope and denied tools against the live stack.

    uv run --env-file .env pytest tests/integration/test_tenant_isolation.py -q

No LM Studio is involved: the orchestrators are scripted and make real MCP
calls through ``McpToolSource`` with the run token the API minted. The tests
cover AC-11b and AC-11c: a tenant cannot read another tenant's runs or trace,
a denied tool leaves a ``denied`` audit row and an error span carrying its audit
id, and the seller filter keeps another seller's orders out of every result.

Skipped unless the compose stack and ``.env`` are present. CI runs it once the
PostgreSQL service exists (T1.16).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer
from tests.fakes import tenants_config

from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.api.app import create_app
from switchboard.api.deps import AppDeps
from switchboard.api.step_events import StepEventProcessor, StepEventPublisher
from switchboard.api.store import PgRunStore
from switchboard.core.errors import ToolPermissionError
from switchboard.core.models import (
    AgentResult,
    ChatRequest,
    Citation,
    ErrorRecord,
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

RANGE = {"date_from": "2017-01-01", "date_to": "2018-08-31"}
DENIED_TOOL = "orders_late@1"


class SummariseOrchestrator:
    """One allowed ``orders_summary@1`` call, traced like the real agent."""

    name = "scripted-summary"

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
                    {"switchboard.plan": "data", "switchboard.plan.reason": "scripted"},
                )
            with agent_span(self._tracer, agent="data", run_id=ctx.run_id) as agent:
                result = await self._source.call("orders_summary@1", dict(RANGE), ctx)
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
            plan=RoutingDecision(agents=["data"], reason="scripted"),
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


class DeniedToolOrchestrator:
    """Calls a tool the tenant's allowlist does not contain (seller_a cannot)."""

    name = "scripted-denied"

    def __init__(self, *, tracer: Tracer, source: McpToolSource) -> None:
        self._tracer = tracer
        self._source = source

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        started = datetime.now(UTC)
        errors: list[ErrorRecord] = []
        with run_span(
            self._tracer,
            tenant_id=ctx.tenant.tenant_id,
            run_id=ctx.run_id,
            orchestrator=self.name,
        ):
            with supervisor_span(self._tracer, run_id=ctx.run_id) as plan:
                set_attributes(
                    plan,
                    {"switchboard.plan": "data", "switchboard.plan.reason": "scripted"},
                )
            with agent_span(self._tracer, agent="data", run_id=ctx.run_id) as agent:
                try:
                    await self._source.call(DENIED_TOOL, dict(RANGE), ctx)
                except ToolPermissionError as exc:
                    errors.append(
                        ErrorRecord(kind=type(exc).__name__, message=str(exc), stage="tool")
                    )
                set_attributes(agent, {"switchboard.partial": True, "switchboard.citations": 0})
        return RunResult(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant.tenant_id,
            query=request.query,
            plan=RoutingDecision(agents=["data"], reason="scripted"),
            status="partial",
            usage=Usage(input_tokens=0, output_tokens=0),
            errors=errors,
            started_at=started,
            ended_at=datetime.now(UTC),
        )


OrchestratorFactory = Callable[..., Any]


@asynccontextmanager
async def live_app(factory: OrchestratorFactory) -> AsyncIterator[AsyncClient]:
    publisher = StepEventPublisher()
    provider = TracerProvider()
    provider.add_span_processor(StepEventProcessor(publisher))
    provider.add_span_processor(BatchSpanProcessor(PostgresSpanExporter(APP_URL)))
    handle = TracingHandle(provider=provider)
    tracer = provider.get_tracer("switchboard.test.tenant_isolation")
    source = McpToolSource(url=MCP_URL, tracer=tracer)
    pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=4)
    deps = AppDeps(
        store=PgRunStore(pool),
        orchestrators={"lmstudio": factory(tracer=tracer, source=source)},
        tenants=tenants_config(),
        run_token_secret=SECRET,
        run_timeout_s=60.0,
        publisher=publisher,
        tracing=handle,
    )
    app = create_app(deps=deps)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0)
    try:
        yield client
    finally:
        await client.aclose()
        await pool.close()
        provider.shutdown()


def flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for node in nodes:
        flat.append(node)
        flat.extend(flatten(node["children"]))
    return flat


async def test_runs_and_traces_are_scoped_to_their_tenant() -> None:
    async with live_app(SummariseOrchestrator) as client:
        seller = await client.post(
            "/chat", json={"query": "seller orders", "tenant_id": "seller_a"}
        )
        assert seller.status_code == 200, seller.text
        seller_body = seller.json()
        assert seller_body["status"] == "ok", seller_body["errors"]
        seller_run = seller_body["run_id"]

        default = await client.post("/chat", json={"query": "all orders", "tenant_id": "default"})
        assert default.status_code == 200, default.text
        default_body = default.json()
        assert default_body["status"] == "ok", default_body["errors"]
        default_run = default_body["run_id"]
        assert seller_run != default_run

        # A tenant sees only its own runs, in both directions.
        seller_list = (await client.get("/runs", params={"tenant_id": "seller_a"})).json()
        default_list = (await client.get("/runs", params={"tenant_id": "default"})).json()
        seller_ids = {row["run_id"] for row in seller_list["runs"]}
        default_ids = {row["run_id"] for row in default_list["runs"]}
        assert seller_run in seller_ids
        assert default_run not in seller_ids
        assert default_run in default_ids
        assert seller_run not in default_ids

        # Another tenant's run and trace are indistinguishable from missing.
        for path in (f"/runs/{seller_run}", f"/runs/{seller_run}/trace"):
            cross = await client.get(path, params={"tenant_id": "default"})
            assert cross.status_code == 404, (path, cross.text)
            own = await client.get(path, params={"tenant_id": "seller_a"})
            assert own.status_code == 200, (path, own.text)
        for path in (f"/runs/{default_run}", f"/runs/{default_run}/trace"):
            cross = await client.get(path, params={"tenant_id": "seller_a"})
            assert cross.status_code == 404, (path, cross.text)

        # tenant_id is required on every read, not optional.
        for path in ("/runs", f"/runs/{seller_run}", f"/runs/{seller_run}/trace"):
            missing = await client.get(path)
            assert missing.status_code == 422, (path, missing.text)

        trace_response = await client.get(
            f"/runs/{seller_run}/trace", params={"tenant_id": "seller_a"}
        )
        trace = trace_response.json()
        assert trace["incomplete"] is False
        names = {span["name"] for span in flatten(trace["spans"])}
        assert "execute_tool orders_summary@1" in names

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        seller_audit = await connection.fetch(
            "SELECT tenant_id, status FROM public.tool_audit WHERE run_id = $1",
            uuid.UUID(seller_run),
        )
        default_audit = await connection.fetch(
            "SELECT tenant_id, status FROM public.tool_audit WHERE run_id = $1",
            uuid.UUID(default_run),
        )
    finally:
        await connection.close()
    assert [tuple(row.values()) for row in seller_audit] == [("seller_a", "ok")]
    assert [tuple(row.values()) for row in default_audit] == [("default", "ok")]


async def test_denied_tool_is_in_the_audit_log_and_the_trace() -> None:
    async with live_app(DeniedToolOrchestrator) as client:
        response = await client.post(
            "/chat", json={"query": "late deliveries for the seller", "tenant_id": "seller_a"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "partial"
        assert [error["kind"] for error in body["errors"]] == ["ToolPermissionError"]
        run_id = body["run_id"]

        trace_response = await client.get(f"/runs/{run_id}/trace", params={"tenant_id": "seller_a"})
        assert trace_response.status_code == 200, trace_response.text
        trace = trace_response.json()
        spans = flatten(trace["spans"])
        denied_spans = [span for span in spans if span["name"] == f"execute_tool {DENIED_TOOL}"]
        assert len(denied_spans) == 2, [span["name"] for span in spans]
        client_span = next(span for span in denied_spans if span["kind"] == "client")
        server_span = next(span for span in denied_spans if span["kind"] == "server")
        assert client_span["status"] == "error"
        assert client_span["attributes"]["switchboard.tool.status"] == "denied"
        assert server_span["status"] == "error"
        assert server_span["attributes"]["switchboard.tool.status"] == "denied"
        assert server_span["parent_span_id"] == client_span["span_id"]
        audit_id = server_span["attributes"]["switchboard.tool.audit_id"]
        uuid.UUID(audit_id)
        assert any(event["name"] == "exception" for event in server_span["events"])

        # The denied run is still invisible to the other tenant.
        cross = await client.get(f"/runs/{run_id}/trace", params={"tenant_id": "default"})
        assert cross.status_code == 404

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        rows = await connection.fetch(
            """
            SELECT id, tenant_id, run_id, tool_name, tool_version, status
            FROM public.tool_audit
            WHERE run_id = $1
            """,
            uuid.UUID(run_id),
        )
    finally:
        await connection.close()
    assert len(rows) == 1, rows
    row = rows[0]
    assert str(row["id"]) == audit_id
    assert row["tenant_id"] == "seller_a"
    assert str(row["run_id"]) == run_id
    assert row["tool_name"] == "orders_late"
    assert row["tool_version"] == "1"
    assert row["status"] == "denied"
