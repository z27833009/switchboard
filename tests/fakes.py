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
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Tracer

from switchboard.api.app import create_app
from switchboard.api.audit import AuditQuery
from switchboard.api.deps import AppDeps
from switchboard.api.step_events import (
    ANSWER_DELTA,
    StepEvent,
    StepEventProcessor,
    StepEventPublisher,
)
from switchboard.api.store import session_turns
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
    Turn,
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
        self.audit_rows: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []

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
        self.runs[run_id] = {
            "id": run_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "query": query,
            "orchestrator": orchestrator,
            "provider": provider,
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
                "cost_usd": result.cost_usd,
                "ended_at": result.ended_at or datetime.now(UTC),
                "trace_complete": trace_complete,
            }
        )

    async def list_runs(
        self, *, tenant_id: str, limit: int = 50, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.runs.values()
            if row["tenant_id"] == tenant_id
            and (session_id is None or row["session_id"] == session_id)
        ]
        rows.sort(key=lambda row: row["started_at"], reverse=True)
        return rows[:limit]

    async def list_sessions(self, *, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in self.runs.values():
            if row["tenant_id"] != tenant_id or row["session_id"] is None:
                continue
            session_id = row["session_id"]
            started = row["started_at"]
            current = grouped.get(session_id)
            if current is None:
                grouped[session_id] = {
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "title": row["query"],
                    "last_query": row["query"],
                    "status": row["status"],
                    "plan": row["plan"],
                    "run_count": 1,
                    "started_at": started,
                    "last_run_at": started,
                }
                continue
            current["run_count"] += 1
            if started < current["started_at"]:
                current["started_at"] = started
                current["title"] = row["query"]
            if started >= current["last_run_at"]:
                current["last_run_at"] = started
                current["last_query"] = row["query"]
                current["status"] = row["status"]
                current["plan"] = row["plan"]
        rows = sorted(grouped.values(), key=lambda row: row["last_run_at"], reverse=True)
        return rows[:limit]

    async def aggregate_runs(
        self, *, tenant_id: str, group_by: Literal["day", "provider"]
    ) -> list[dict[str, Any]]:
        """The same buckets and ordering as ``AGGREGATE_RUNS_SQL`` (design 12.4)."""
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in self.runs.values():
            if row["tenant_id"] != tenant_id:
                continue
            if group_by == "day":
                started = row["started_at"]
                bucket = (
                    started.astimezone(UTC).date().isoformat()
                    if isinstance(started, datetime)
                    else str(started)[:10]
                )
            else:
                bucket = str(row.get("provider") or "unknown")
            buckets.setdefault(bucket, []).append(row)

        aggregates: list[dict[str, Any]] = []
        for bucket, rows in buckets.items():
            costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
            aggregates.append(
                {
                    "bucket": bucket,
                    "run_count": len(rows),
                    "input_tokens": sum(int(row.get("input_tokens", 0)) for row in rows),
                    "cached_input_tokens": sum(
                        int(row.get("cached_input_tokens", 0)) for row in rows
                    ),
                    "output_tokens": sum(int(row.get("output_tokens", 0)) for row in rows),
                    "cost_usd": round(sum(costs), 8) if costs else None,
                    "unpriced_runs": sum(1 for row in rows if row["cost_usd"] is None),
                }
            )
        aggregates.sort(key=lambda item: item["bucket"], reverse=group_by == "day")
        return aggregates

    async def get_run(self, run_id: str, *, tenant_id: str) -> dict[str, Any] | None:
        row = self.runs.get(run_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return dict(row)

    async def load_session_turns(
        self, *, tenant_id: str, session_id: str, limit: int
    ) -> list[Turn]:
        """The same filter and ordering as ``SESSION_TURNS_SQL`` (design 6.3)."""
        rows = [
            row
            for row in self.runs.values()
            if row["tenant_id"] == tenant_id
            and row["session_id"] == session_id
            and row["status"] != "running"
            and isinstance(row.get("answer"), str)
            and row["answer"] != ""
        ]
        rows.sort(key=lambda row: (row["started_at"], row["id"]), reverse=True)
        selected = rows[:limit] if limit > 0 else []
        selected.reverse()
        return session_turns(selected)

    async def fetch_spans(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(span) for span in self.spans if span.get("run_id") == run_id]

    async def add_feedback(
        self, *, run_id: str, tenant_id: str, rating: int, note: str | None
    ) -> dict[str, Any]:
        """Append a row; a mismatched run and tenant is refused like the FK."""
        run = self.runs.get(run_id)
        if run is None or run["tenant_id"] != tenant_id:
            raise LookupError("feedback target run not found")
        row = {
            "id": str(uuid.uuid4()),
            "run_id": run_id,
            "tenant_id": tenant_id,
            "rating": int(rating),
            "note": note,
            "created_at": datetime.now(UTC),
        }
        self.feedback.append(row)
        return dict(row)

    async def latest_feedback(self, *, run_id: str, tenant_id: str) -> dict[str, Any] | None:
        rows = [
            (index, row)
            for index, row in enumerate(self.feedback)
            if row["run_id"] == run_id and row["tenant_id"] == tenant_id
        ]
        if not rows:
            return None
        # Insertion order breaks a same-microsecond tie, so the fake matches
        # the append-only rule instead of ordering equal timestamps by UUID.
        rows.sort(key=lambda item: (item[1]["created_at"], item[0]), reverse=True)
        return dict(rows[0][1])

    async def list_audit(self, query: AuditQuery) -> list[dict[str, Any]]:
        """The same tenant filter, ordering and offset as ``LIST_AUDIT_SQL``."""
        rows: list[dict[str, Any]] = []
        for row in self.audit_rows:
            if row["tenant_id"] != query.tenant_id:
                continue
            if query.run_id is not None and row.get("run_id") != query.run_id:
                continue
            if query.tool_name is not None and row["tool_name"] != query.tool_name:
                continue
            if query.status is not None and row["status"] != query.status:
                continue
            created = cast(datetime, row["created_at"])
            if query.from_ts is not None and created < query.from_ts:
                continue
            if query.to_ts is not None and created > query.to_ts:
                continue
            rows.append(dict(row))
        rows.sort(key=lambda item: (item["created_at"], str(item["id"])), reverse=True)
        return rows[query.offset : query.offset + query.limit]


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
        usage: Usage | None = None,
        cost_usd: float | None = None,
        publisher: StepEventPublisher | None = None,
        answer_deltas: tuple[str, ...] = (),
        answer_text: str | None = None,
        name: str | None = None,
    ) -> None:
        self._tracer = tracer
        if name is not None:
            # A second fake for replay tests records the name the API must store
            # on the run row (T3.07).
            self.name = name
        self._agents = agents
        self._status = status
        self._tool_name = tool_name
        self._total_rows = total_rows
        self._usage = usage or Usage(input_tokens=150, output_tokens=20)
        self._cost_usd = cost_usd
        self._publisher = publisher
        self._answer_deltas = answer_deltas
        if answer_text is not None:
            self._answer_text = answer_text
        elif answer_deltas:
            self._answer_text = "".join(answer_deltas)
        else:
            self._answer_text = "There were 7262 orders in March 2018 (t1)."
        self.queries: list[str] = []
        self.histories: list[list[Turn]] = []

    async def run(self, request: ChatRequest, ctx: RunContext) -> RunResult:
        self.queries.append(request.query)
        self.histories.append(list(request.history))
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
                    if agent == self._agents[-1]:
                        # The real path is provider -> AgentDeps.answer_delta ->
                        # StepEventProcessor. The fake publishes directly; the
                        # stream sees the same event and ordering (T2.08).
                        for fragment in self._answer_deltas:
                            if self._publisher is not None:
                                self._publisher.publish(
                                    StepEvent(ANSWER_DELTA, ctx.run_id, {"text": fragment})
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
                answer=self._answer_text,
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
            usage=self._usage,
            cost_usd=self._cost_usd,
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
    sensitive_args: Mapping[str, Sequence[str]] | None = None,
    history_turns: int = 3,
    settings_snapshot: Mapping[str, Mapping[str, Any]] | None = None,
    replay_orchestrators: Mapping[tuple[str, str], Any] | None = None,
) -> AppHarness:
    """Build an app with fakes and a client bound to it (no lifespan, no IO)."""
    deps = AppDeps(
        store=store,
        audit=store,
        orchestrators={"lmstudio": orchestrator},
        replay_orchestrators=dict(replay_orchestrators or {}),
        tenants=tenants,
        run_token_secret=SECRET,
        run_timeout_s=30.0,
        history_turns=history_turns,
        publisher=publisher,
        tracing=flush,
        health=fake_health,
        sensitive_args=sensitive_args or {},
        settings_snapshot=settings_snapshot,
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
