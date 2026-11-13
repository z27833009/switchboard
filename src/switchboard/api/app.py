"""FastAPI application: chat, runs, trace, audit and health (design.md sections 5, 12).

The routes are thin: validate the request with the core models, resolve the
tenant, mint the run token, insert the run row, hand the run to the orchestrator,
flush the span batches, write the final row and return. Tests build the app with
an injected ``AppDeps`` so no LM Studio or PostgreSQL is needed; production goes
through ``build_app_deps`` in ``deps.py``.

``POST /chat/stream`` returns server-sent events produced by
``StepEventProcessor`` (design 12.5). The events come from the same spans the
trace endpoint reads, and the run starts emitting before it ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

import asyncpg
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.telemetry import TelemetryConfig

from switchboard.api.audit import AuditQuery, as_utc
from switchboard.api.deps import AppDeps, build_app_deps
from switchboard.api.run_tokens import (
    RUN_TOKEN_MIN_BYTES,
    mint_run_token,
    require_run_token_secret,
)
from switchboard.api.schemas import (
    AuditResponse,
    AuditRow,
    AuditStatus,
    ChatResponse,
    FeedbackSubmission,
    FeedbackSummary,
    RunAggregateResponse,
    RunAggregateRow,
    RunsResponse,
    RunSummary,
    SessionsResponse,
    SessionSummary,
    SettingsResponse,
    TraceResponse,
)
from switchboard.api.step_events import RUN_FINISHED, StepEvent
from switchboard.api.store import parse_run_id, rows_are_complete
from switchboard.api.trace import assemble_trace
from switchboard.core.config import tenant_context
from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    ChatRequest,
    ErrorRecord,
    RunContext,
    RunResult,
    TenantContext,
)

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 3.0
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
# FastAPI's native OpenTelemetry is off: this project instruments its own
# boundaries and the run span is the root of a run's trace (design 12.1, 12.2).
NATIVE_TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
}


def _deps(app: FastAPI) -> AppDeps:
    value = getattr(app.state, "deps", None)
    if value is None:
        raise HTTPException(status_code=503, detail="the application has not started")
    return cast(AppDeps, value)


def _tenant(deps: AppDeps, tenant_id: str) -> TenantContext:
    try:
        return tenant_context(tenant_id, deps.tenants)
    except ConfigError as exc:
        raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id!r}") from exc


def _run_context(
    deps: AppDeps, tenant: TenantContext, run_id: str, trace_parent: str
) -> RunContext:
    return RunContext(
        run_id=run_id,
        tenant=tenant,
        run_token=mint_run_token(
            deps.run_token_secret,
            tenant_id=tenant.tenant_id,
            run_id=run_id,
            run_timeout_s=deps.run_timeout_s,
        ),
        trace_parent=trace_parent,
    )


def _failed_result(
    request: ChatRequest, run_id: str, tenant_id: str, exc: BaseException
) -> RunResult:
    now = datetime.now(UTC)
    return RunResult(
        run_id=run_id,
        tenant_id=tenant_id,
        query=request.query,
        status="provider_error",
        errors=[
            ErrorRecord(
                kind=type(exc).__name__,
                message="the orchestrator failed before producing a result",
                stage="api",
            )
        ],
        started_at=now,
        ended_at=now,
    )


async def request_with_history(deps: AppDeps, request: ChatRequest) -> ChatRequest:
    """Attach the session's stored turns, when the request names a session.

    Server-side history is authoritative: a request with a ``session_id`` gets
    the last ``deps.history_turns`` completed runs of that tenant and session
    (oldest first), and any client-supplied ``history`` is ignored. That way the
    browser only has to keep a session id, and a client cannot inject turns.
    Without a ``session_id`` the client history is used unchanged (design.md
    6.3, AC-23a).
    """
    if not request.session_id:
        return request
    turns = await deps.store.load_session_turns(
        tenant_id=request.tenant_id,
        session_id=request.session_id,
        limit=deps.history_turns,
    )
    return request.model_copy(update={"history": turns})


async def execute_run(
    deps: AppDeps,
    request: ChatRequest,
    tenant: TenantContext,
    *,
    run_id: str,
    trace_parent: str,
) -> tuple[RunResult, bool]:
    """One run: run row, orchestrator, flush, final row (design 5 and 12.2)."""
    orchestrator = deps.orchestrator_for(tenant.provider)
    context = _run_context(deps, tenant, run_id, trace_parent)
    # Loaded before the new run row exists, so the current query is not part of
    # its own history.
    request = await request_with_history(deps, request)
    await deps.store.start_run(
        run_id=run_id,
        tenant_id=tenant.tenant_id,
        session_id=request.session_id,
        query=request.query,
        orchestrator=orchestrator.name,
        # The provider a tenant is configured with. Recorded so `GET /runs`
        # can aggregate cost per provider (AC-19b).
        provider=tenant.provider,
    )
    try:
        result = await orchestrator.run(request, context)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("run %s: orchestrator failed with %s", run_id, type(exc).__name__)
        result = _failed_result(request, run_id, tenant.tenant_id, exc)
    flushed = await deps.flush()
    await deps.store.finish_run(result, trace_complete=flushed)
    return result, flushed


def _spawn(app: FastAPI, task: asyncio.Task[None]) -> None:
    tasks: set[asyncio.Task[None]] = app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _run_and_publish(
    deps: AppDeps,
    request: ChatRequest,
    tenant: TenantContext,
    *,
    run_id: str,
    trace_parent: str,
) -> None:
    try:
        result, flushed = await execute_run(
            deps, request, tenant, run_id=run_id, trace_parent=trace_parent
        )
        response = ChatResponse.from_result(
            result, session_id=request.session_id, trace_incomplete=not flushed
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("run %s: could not finish with %s", run_id, type(exc).__name__)
        now = datetime.now(UTC)
        response = ChatResponse(
            run_id=run_id,
            tenant_id=tenant.tenant_id,
            session_id=request.session_id,
            status="provider_error",
            errors=[
                ErrorRecord(
                    kind=type(exc).__name__,
                    message="the run could not be stored",
                    stage="api",
                )
            ],
            started_at=now,
            ended_at=now,
        )
    payload = response.model_dump(mode="json")
    payload.pop("run_id", None)
    deps.publisher.publish(StepEvent(RUN_FINISHED, run_id, payload))


async def _event_stream(
    deps: AppDeps, queue: asyncio.Queue[StepEvent], run_id: str
) -> AsyncIterator[str]:
    try:
        while True:
            event = await queue.get()
            yield event.to_sse()
            if event.type == RUN_FINISHED:
                return
    finally:
        deps.publisher.unregister(run_id)


router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    """Run one request and return the final payload (AC-01a)."""
    deps = _deps(request.app)
    tenant = _tenant(deps, payload.tenant_id)
    run_id = str(uuid.uuid4())
    try:
        result, flushed = await execute_run(
            deps,
            payload,
            tenant,
            run_id=run_id,
            trace_parent=request.headers.get("traceparent", ""),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("run %s: request failed with %s", run_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="the run could not be completed") from exc
    return ChatResponse.from_result(
        result, session_id=payload.session_id, trace_incomplete=not flushed
    )


@router.post("/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest) -> StreamingResponse:
    """Stream the run's steps as server-sent events (AC-22a, design 12.5)."""
    deps = _deps(request.app)
    tenant = _tenant(deps, payload.tenant_id)
    run_id = str(uuid.uuid4())
    queue = deps.publisher.register(run_id)
    task = asyncio.create_task(
        _run_and_publish(
            deps,
            payload,
            tenant,
            run_id=run_id,
            trace_parent=request.headers.get("traceparent", ""),
        )
    )
    _spawn(request.app, task)
    return StreamingResponse(
        _event_stream(deps, queue, run_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _load_run(deps: AppDeps, run_id: str, tenant_id: str) -> dict[str, Any]:
    """Load one run for a tenant; another tenant's run is a 404, not a leak.

    The demo has no caller authentication (requirements, non-goals), so the
    tenant id is a required query parameter and the store filters on it. Runs
    and traces therefore never appear to a tenant that did not own them
    (AC-11c, design.md section 11).
    """
    tenant = _tenant(deps, tenant_id)
    try:
        normalized = parse_run_id(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    row = await deps.store.get_run(normalized, tenant_id=tenant.tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@router.get("/sessions", response_model=SessionsResponse)
async def list_sessions(
    request: Request,
    tenant_id: str = Query(min_length=1, description="Tenant whose conversations to list."),
    limit: int = Query(default=20, ge=1, le=100),
) -> SessionsResponse:
    """The tenant's conversations, newest first, one row per session.

    The sidebar shows conversations rather than every run, so a follow-up turn
    does not appear as a second entry. A run without a session id is excluded:
    it has no conversation to resume.
    """
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    rows = await deps.store.list_sessions(tenant_id=tenant.tenant_id, limit=limit)
    return SessionsResponse(
        sessions=[SessionSummary.from_row(row) for row in rows],
        count=len(rows),
    )


@router.get("/runs", response_model=RunsResponse | RunAggregateResponse)
async def list_runs(
    request: Request,
    tenant_id: str = Query(min_length=1, description="Tenant whose runs to list."),
    limit: int = Query(default=50, ge=1, le=200),
    session_id: Annotated[
        str | None,
        Query(
            min_length=1,
            description="Only the runs of this conversation, for the chat transcript.",
        ),
    ] = None,
    group_by: Annotated[
        Literal["day", "provider"] | None,
        Query(description="Aggregate cost and tokens per UTC day or per provider."),
    ] = None,
) -> RunsResponse | RunAggregateResponse:
    """The run list of one tenant, newest first, or its cost buckets (AC-19b).

    Without ``group_by`` the response is the run list, unchanged. ``session_id``
    narrows it to one conversation, which is how the chat page rebuilds a
    stored transcript from real run rows instead of keeping browser history.
    With ``group_by=day`` or ``group_by=provider`` the response holds one
    bucket per day (UTC) or provider with the run count, input, cached input and
    output tokens and the summed cost. ``limit`` only applies to the run list; a
    bucket covers every run of the tenant, and neither filter applies to it.
    """
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    if group_by is None:
        rows = await deps.store.list_runs(
            tenant_id=tenant.tenant_id, limit=limit, session_id=session_id
        )
        return RunsResponse(runs=[RunSummary.from_row(row) for row in rows], count=len(rows))
    groups = await deps.store.aggregate_runs(tenant_id=tenant.tenant_id, group_by=group_by)
    return RunAggregateResponse(
        group_by=group_by,
        tenant_id=tenant.tenant_id,
        groups=[RunAggregateRow.from_row(row) for row in groups],
        count=len(groups),
    )


@router.get("/runs/{run_id}", response_model=RunSummary)
async def get_run(
    request: Request,
    run_id: str,
    tenant_id: str = Query(min_length=1, description="Tenant that owns the run."),
) -> RunSummary:
    """One run with its final answer, citations and newest feedback, for its tenant."""
    deps = _deps(request.app)
    row = await _load_run(deps, run_id, tenant_id)
    feedback = await deps.store.latest_feedback(
        run_id=str(row["id"]), tenant_id=str(row["tenant_id"])
    )
    return RunSummary.from_row(row, latest_feedback=feedback)


@router.post("/runs/{run_id}/feedback", response_model=FeedbackSummary, status_code=201)
async def post_run_feedback(
    request: Request,
    payload: FeedbackSubmission,
    run_id: str,
    tenant_id: str = Query(min_length=1, description="Tenant that owns the run."),
) -> FeedbackSummary:
    """Store a rating and an optional note for one run (AC-24a).

    The tenant is required and the run is loaded with it, so another tenant's
    run is a 404 and never receives feedback (design.md section 11). The row
    copies the run's own tenant, and the table's composite foreign key rejects
    a mismatched pair. Feedback is append-only: there is no update and no
    delete route, so a change of mind adds a row and the newest row is the
    run's current verdict (design.md sections 14 and 20.1).
    """
    deps = _deps(request.app)
    row = await _load_run(deps, run_id, tenant_id)
    try:
        created = await deps.store.add_feedback(
            run_id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            rating=payload.rating,
            note=payload.note,
        )
    except asyncpg.ForeignKeyViolationError as exc:
        # The run was removed between the read and the insert. Same answer as an
        # unknown run, never a 500 and never a row for a missing run.
        raise HTTPException(status_code=404, detail="run not found") from exc
    return FeedbackSummary.from_row(created)


@router.get("/runs/{run_id}/trace", response_model=TraceResponse)
async def get_run_trace(
    request: Request,
    run_id: str,
    tenant_id: str = Query(min_length=1, description="Tenant that owns the run."),
) -> TraceResponse:
    """The span tree of one run (AC-05a), marked incomplete after a lost flush."""
    deps = _deps(request.app)
    row = await _load_run(deps, run_id, tenant_id)
    rows = await deps.store.fetch_spans(str(row["id"]))
    incomplete = not bool(row.get("trace_complete", True)) or not rows_are_complete(rows)
    return assemble_trace(run_id=str(row["id"]), rows=rows, incomplete=incomplete)


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(
    request: Request,
    tenant_id: str = Query(min_length=1, description="Tenant whose settings to read."),
) -> SettingsResponse:
    """The read-only runtime settings for one tenant (design.md section 14).

    The payload is built once at startup from the loaded configuration, the
    agent registry and the tool manifests, so it cannot disagree with what the
    process runs. It carries no API key, base URL, database URL or token
    secret; ``api/settings.py`` lists the boundary. A tenant that is not in the
    configuration is a 404, and an application built without a snapshot (a test
    that injects its own ``AppDeps``) is a 503 rather than an invented answer.
    """
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    snapshot = deps.settings_snapshot
    if snapshot is None or tenant.tenant_id not in snapshot:
        raise HTTPException(status_code=503, detail="the settings snapshot is not available")
    return SettingsResponse.model_validate(snapshot[tenant.tenant_id])


@router.get("/audit", response_model=AuditResponse)
async def list_audit(
    request: Request,
    tenant_id: Annotated[str, Query(min_length=1, description="Tenant whose tool calls to list.")],
    run_id: Annotated[
        uuid.UUID | None, Query(description="Only tool calls made by this run.")
    ] = None,
    tool_name: Annotated[
        str | None, Query(min_length=1, description="Only calls of this tool name.")
    ] = None,
    status: Annotated[AuditStatus | None, Query(description="ok, error or denied.")] = None,
    from_ts: Annotated[
        datetime | None,
        Query(alias="from", description="Only calls created at or after this time."),
    ] = None,
    to_ts: Annotated[
        datetime | None,
        Query(alias="to", description="Only calls created at or before this time."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditResponse:
    """One tenant's tool calls, newest first (AC-18a, AC-18b).

    The tenant is required and applied in SQL, so another tenant's rows are
    invisible (design.md section 11). ``args_redacted`` is redacted at write
    time and again here from the manifest's ``sensitive_args``.
    """
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    start = as_utc(from_ts)
    end = as_utc(to_ts)
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="from must be on or before to")
    query = AuditQuery(
        tenant_id=tenant.tenant_id,
        run_id=None if run_id is None else str(run_id),
        tool_name=tool_name,
        status=status,
        from_ts=start,
        to_ts=end,
        limit=limit,
        offset=offset,
    )
    rows = await deps.audit.list_audit(query)
    return AuditResponse(
        rows=[AuditRow.from_row(row, deps.sensitive_args) for row in rows],
        count=len(rows),
        limit=limit,
        offset=offset,
    )


async def check_postgres(dsn: str) -> dict[str, str]:
    if not dsn:
        return {"status": "error", "detail": "SWITCHBOARD_DATABASE_URL is not set"}
    try:
        connection = await asyncpg.connect(dsn=dsn, timeout=HTTP_TIMEOUT_S)
        try:
            await connection.fetchval("SELECT 1")
        finally:
            await connection.close()
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    return {"status": "ok"}


def check_lmstudio(base_url: str) -> dict[str, Any]:
    if not base_url:
        return {"status": "error", "detail": "LMSTUDIO_BASE_URL is not set"}
    try:
        with urllib.request.urlopen(
            base_url.rstrip("/") + "/models", timeout=HTTP_TIMEOUT_S
        ) as response:
            payload: Any = json.loads(response.read())
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    models = [
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return {"status": "ok", "models": sorted(models)}


def check_http(url: str) -> dict[str, str]:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as response:
            response.read()
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    return {"status": "ok"}


async def default_health() -> tuple[int, dict[str, Any]]:
    """The T1.02 dependency report: postgres, mcp_olist, lmstudio."""
    postgres = await check_postgres(os.environ.get("SWITCHBOARD_DATABASE_URL", ""))
    lmstudio = await asyncio.to_thread(check_lmstudio, os.environ.get("LMSTUDIO_BASE_URL", ""))
    tools_url = os.environ.get("SWITCHBOARD_TOOLS_URL", "")
    if tools_url:
        mcp_olist = await asyncio.to_thread(check_http, tools_url.rstrip("/") + "/health")
    else:
        mcp_olist = {"status": "error", "detail": "SWITCHBOARD_TOOLS_URL is not set"}
    dependencies: dict[str, dict[str, Any]] = {
        "postgres": postgres,
        "mcp_olist": mcp_olist,
        "lmstudio": lmstudio,
    }
    healthy = all(item["status"] == "ok" for item in dependencies.values())
    return (
        200 if healthy else 503,
        {
            "status": "ok" if healthy else "error",
            "dependencies": dependencies,
        },
    )


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    deps = _deps(request.app)
    check = deps.health or default_health
    status_code, content = await check()
    return JSONResponse(status_code=status_code, content=content)


def create_app(*, deps: AppDeps | None = None) -> FastAPI:
    """Build the app; ``deps`` injects fakes for tests, ``None`` builds production."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        built: AppDeps | None = None
        if app.state.deps is None:
            built = await build_app_deps()
            app.state.deps = built
        try:
            yield
        finally:
            if built is not None and built.closer is not None:
                await built.closer()

    application = FastAPI(
        title="Switchboard API",
        version="0.1.0",
        lifespan=lifespan,
        # FastAPI 0.162 traces requests and dependencies itself. The design's
        # span set is the project's own (design 12.1) and the run span is the
        # root of a run's trace, so the framework spans would only add noise to
        # the span table and the trace tree.
        telemetry=NATIVE_TELEMETRY_OFF,
    )
    application.state.deps = deps
    application.state.background_tasks = set()
    application.include_router(router)
    return application


app = create_app()

__all__ = [
    "HTTP_TIMEOUT_S",
    "NATIVE_TELEMETRY_OFF",
    "RUN_TOKEN_MIN_BYTES",
    "SSE_HEADERS",
    "app",
    "chat",
    "chat_stream",
    "check_http",
    "check_lmstudio",
    "check_postgres",
    "create_app",
    "default_health",
    "execute_run",
    "get_run",
    "get_run_trace",
    "get_settings",
    "health",
    "list_audit",
    "list_runs",
    "post_run_feedback",
    "request_with_history",
    "require_run_token_secret",
]
