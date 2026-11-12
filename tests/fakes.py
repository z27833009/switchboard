"""Shared test doubles: a memory run store, a fake orchestrator and an app harness.

The fake orchestrator emits real OpenTelemetry spans through the same helpers
the LangGraph adapter uses, so the HTTP and SSE tests exercise
``StepEventProcessor`` and the span tree without LM Studio, PostgreSQL or the
MCP server. The production wiring is covered separately in
``test_deps_wiring.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Tracer

from switchboard.api.app import create_app
from switchboard.api.deps import AppDeps
from switchboard.api.step_events import StepEventProcessor, StepEventPublisher
from switchboard.config import load_model
from switchboard.core.config import TenantsConfig
from switchboard.core.models import (
    AgentResult,
    ChatRequest,
    Citation,
    ErrorRecord,
    RoutingDecision,
    RunContext,
    RunResult,
    RunStatus,
    Usage,
)
from switchboard.tracing.pg_exporter import span_to_row
from switchboard.tracing.spans import agent_span, run_span, set_attributes, supervisor_span

SECRET = "t" * 32


class MemoryRunStore:
    """The ``RunStore`` protocol in memory, with the same row shapes as PostgreSQL."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.spans: list[dict[str, Any]] = []

    async def start_run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        session_id: str | None,
        query: str,
        orchestrator: str,
    ) -> None:
        self.runs[run_id] = {
            "id": run_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "query": query,
            "orchestrator": orchestrator,
            "plan": None,
            "answer": None,
            "citations": [],
            "errors": [],
            "status": "running",
            "cache_hit": False,
            "attempts": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            "trace_complete": True,
            "started_at": datetime.now(UTC),
            "ended_at": None,
        }

    async def finish_run(self, result: RunResult, *, trace_complete: bool) -> None:
        row = self.runs[result.run_id]
        answer = result.answer
        row.update(
            {
                "plan": result.plan.label if result.plan is not None else None,
                "answer": answer.answer if answer is not None else None,
                "citations": (
                    [citation.model_dump(mode="json") for citation in answer.citations]
                    if answer is not None
                    else []
                ),
                "errors": [error.model_dump(mode="json") for error in result.errors],
                "status": result.status,
                "cache_hit": result.cache_hit,
                "attempts": result.attempts,
                "input_tokens": result.usage.input_tokens,
                "cached_input_tokens": result.usage.cached_input_tokens,
                "output_tokens": result.usage.output_tokens,
                "ended_at": result.ended_at or datetime.now(UTC),
                "trace_complete": trace_complete,
            }
        )

    async def list_runs(
        self, *, tenant_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.runs.values()
            if tenant_id is None or row["tenant_id"] == tenant_id
        ]
        rows.sort(key=lambda row: row["started_at"], reverse=True)
        return rows[:limit]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.runs.get(run_id)
        return None if row is None else dict(row)

    async def fetch_spans(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(span) for span in self.spans if span.get("run_id") == run_id]


SPAN_COLUMNS = (
    "span_id",
    "trace_id",
    "parent_span_id",
    "run_id",
    "name",
    "kind",
    "start_time",
    "end_time",
    "status",
    "attributes",
    "events",
)


class MemorySpanExporter(SpanExporter):
    """Records finished spans in the memory store, like the PostgreSQL exporter."""

    def __init__(self, store: MemoryRunStore) -> None:
        self._store = store

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            row = dict(zip(SPAN_COLUMNS, span_to_row(span), strict=True))
            row["attributes"] = json.loads(cast(str, row["attributes"]))
            row["events"] = json.loads(cast(str, row["events"]))
            row["run_id"] = None if row["run_id"] is None else str(row["run_id"])
            self._store.spans.append(row)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def make_provider(publisher: StepEventPublisher, store: MemoryRunStore) -> TracerProvider:
    """A tracer provider with the step events and the memory span sink attached."""
    provider = TracerProvider()
    provider.add_span_processor(StepEventProcessor(publisher))
    provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(store)))
    return provider


@dataclass
class FakeFlush:
    """Stands in for ``TracingHandle.force_flush_async`` in flush-timeout tests."""

    result: bool = True

    async def force_flush_async(self, timeout_s: float = 2.0) -> bool:
        return self.result


class FakeOrchestrator:
    """One run with the same span names and attributes the real adapter sets."""

    name = "fake"

    def __init__(
        self,
        *,
        tracer: Tracer,
        agents: tuple[str, ...] = ("data", "docs"),
        status: RunStatus = "ok",
        tool_name: str = "orders_summary@1",
        total_rows: int = 7262,
    ) -> None:
        self._tracer = tracer
        self._agents = agents
        self._status = status
        self._tool_name = tool_name
        self._total_rows = total_rows
        self.queries: list[str] = []

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        self.queries.append(request.query)
        started = datetime.now(UTC)
        with run_span(
            self._tracer,
            tenant_id=ctx.tenant.tenant_id,
            run_id=ctx.run_id,
            orchestrator=self.name,
        ):
            # A yield point proves the stream can emit before the run ends.
            await asyncio.sleep(0.01)
            with supervisor_span(self._tracer, run_id=ctx.run_id) as plan_span:
                set_attributes(
                    plan_span,
                    {
                        "switchboard.plan": "+".join(self._agents),
                        "switchboard.plan.reason": "orders and policy question",
                    },
                )
            for agent in self._agents:
                with agent_span(self._tracer, agent=agent, run_id=ctx.run_id) as agent_span_ctx:
                    with self._tracer.start_as_current_span(
                        f"execute_tool {self._tool_name}",
                        kind=SpanKind.CLIENT,
                        attributes={
                            "gen_ai.tool.name": self._tool_name,
                            "switchboard.run_id": ctx.run_id,
                        },
                    ) as tool_span:
                        set_attributes(
                            tool_span,
                            {
                                "switchboard.tool.status": "ok",
                                "switchboard.tool.total_rows": self._total_rows,
                                "switchboard.tool.audit_id": "audit-0001",
                            },
                        )
                    set_attributes(
                        agent_span_ctx,
                        {"switchboard.partial": False, "switchboard.citations": 1},
                    )
            with self._tracer.start_as_current_span(
                "gate", attributes={"switchboard.run_id": ctx.run_id}
            ) as gate_span:
                set_attributes(
                    gate_span,
                    {"switchboard.gate.verdict": "pass", "switchboard.gate.action": "pass"},
                )
        return RunResult(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant.tenant_id,
            query=request.query,
            plan=RoutingDecision(agents=list(self._agents), reason="orders and policy question"),
            answer=AgentResult(
                answer="There were 7262 orders in March 2018 (t1).",
                citations=[
                    Citation(
                        kind="tool",
                        ref="t1",
                        source_id=self._tool_name,
                        audit_id="audit-0001",
                        snippet="7262 rows",
                    )
                ],
                usage=Usage(input_tokens=120, output_tokens=12),
            ),
            status=self._status,
            usage=Usage(input_tokens=150, output_tokens=20),
            errors=(
                [ErrorRecord(kind="ToolExecutionError", message="one branch failed")]
                if self._status == "partial"
                else []
            ),
            started_at=started,
            ended_at=datetime.now(UTC),
        )


class FailingOrchestrator:
    name = "failing"

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        raise RuntimeError("the model endpoint is unreachable")


def tenants_config() -> TenantsConfig:
    path = Path(__file__).resolve().parents[1] / "config" / "tenants.yaml"
    return load_model(path, TenantsConfig, {})


async def fake_health() -> tuple[int, dict[str, Any]]:
    return 200, {
        "status": "ok",
        "dependencies": {
            "postgres": {"status": "ok"},
            "mcp_olist": {"status": "ok"},
            "lmstudio": {"status": "ok"},
        },
    }


@dataclass
class AppHarness:
    app: Any
    deps: AppDeps
    store: MemoryRunStore
    publisher: StepEventPublisher
    provider: TracerProvider
    orchestrator: FakeOrchestrator
    client: AsyncClient = field(init=False)


def build_harness(
    *,
    tenants: TenantsConfig,
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    provider: TracerProvider,
    orchestrator: Any,
    flush: Any = None,
) -> AppHarness:
    """Build an app with fakes and a client bound to it (no lifespan, no IO)."""
    deps = AppDeps(
        store=store,
        orchestrators={"lmstudio": orchestrator},
        tenants=tenants,
        run_token_secret=SECRET,
        run_timeout_s=30.0,
        publisher=publisher,
        tracing=flush,
        health=fake_health,
    )
    app = create_app(deps=deps)
    harness = AppHarness(
        app=app,
        deps=deps,
        store=store,
        publisher=publisher,
        provider=provider,
        orchestrator=orchestrator,
    )
    harness.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return harness
