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
import base64
import binascii
import hmac
import io
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

import asyncpg
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.telemetry import TelemetryConfig
from pypdf import PdfReader

from switchboard.adapters.tools.mcp_client import discover_tools
from switchboard.api.audit import AuditQuery, as_utc
from switchboard.api.auth import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    AuthUser,
    hash_password,
    sign_session,
    verify_password,
    verify_session,
)
from switchboard.api.deps import AppDeps, build_app_deps
from switchboard.api.run_tokens import (
    RUN_TOKEN_MIN_BYTES,
    mint_run_token,
    require_run_token_secret,
)
from switchboard.api.schemas import (
    AttachmentParse,
    AttachmentParsed,
    AuditResponse,
    AuditRow,
    AuditStatus,
    AuthLogin,
    AuthStatus,
    AuthUserPayload,
    ChatResponse,
    DocumentChunk,
    DocumentChunksResponse,
    DocumentIngestResult,
    DocumentReindexResult,
    DocumentsResponse,
    DocumentSummary,
    DocumentUpload,
    FeedbackSubmission,
    FeedbackSummary,
    McpServerResult,
    McpServerSubmission,
    ProviderCredentialUpdate,
    ProviderSettingsUpdate,
    ProviderTestResult,
    RunAggregateResponse,
    RunAggregateRow,
    RunsResponse,
    RunSummary,
    SessionAuditResponse,
    SessionAuditRow,
    SessionBulkDelete,
    SessionRename,
    SessionsResponse,
    SessionSummary,
    SettingsResponse,
    SettingsRevision,
    TenantCreation,
    TenantSettingsUpdate,
    TenantsResponse,
    TraceResponse,
    UserCreate,
    UsersResponse,
    UserSummary,
)
from switchboard.api.step_events import RUN_FINISHED, StepEvent
from switchboard.api.store import parse_run_id, rows_are_complete
from switchboard.api.trace import assemble_trace
from switchboard.core.config import tenant_context
from switchboard.core.errors import ApprovalUnavailable, ConfigError, ProviderTransientError
from switchboard.core.models import (
    ApprovalDecision,
    ChatRequest,
    ErrorRecord,
    PendingApproval,
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


def _check_preset(tenant: TenantContext, preset: str) -> None:
    """Reject an agent preset the tenant cannot run (V3.09).

    The composer lists the tenant's allowed agents from ``GET /settings``. The
    API repeats the check before a run row exists, so a hand-written request
    cannot select an unregistered or disallowed agent. The orchestrator keeps
    its own guard as the second line of defence.
    """
    if preset == "auto" or preset in tenant.allowed_agents:
        return
    raise HTTPException(
        status_code=422,
        detail=(
            f"agent preset {preset!r} is not allowed for tenant "
            f"{tenant.tenant_id!r}; allowed: {', '.join(sorted(tenant.allowed_agents)) or 'none'}"
        ),
    )


def _check_orchestrator(deps: AppDeps, tenant: TenantContext, name: str | None) -> None:
    """Reject an unknown replay target before a run row exists (T3.07).

    ``name=None`` is the default path and is not validated here, exactly as
    before this task. A named orchestrator must be one the process built for the
    tenant's provider, otherwise the request is a 400 rather than a 500 or a
    run that never had a chance.
    """
    if name is None:
        return
    try:
        deps.orchestrator_for(tenant.provider, name, tenant.model)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    orchestrator = deps.orchestrator_for(tenant.provider, request.orchestrator, tenant.model)
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
        attachments=[attachment.model_dump(mode="json") for attachment in request.attachments],
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
    _check_preset(tenant, payload.preset)
    _check_orchestrator(deps, tenant, payload.orchestrator)
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
    _check_preset(tenant, payload.preset)
    _check_orchestrator(deps, tenant, payload.orchestrator)
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
    include_deleted: bool = Query(
        default=False, description="Also list conversations hidden from the sidebar."
    ),
) -> SessionsResponse:
    """The tenant's conversations, newest first, one row per session.

    The sidebar shows conversations rather than every run, so a follow-up turn
    does not appear as a second entry. A run without a session id is excluded:
    it has no conversation to resume.
    """
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    rows = await deps.store.list_sessions(
        tenant_id=tenant.tenant_id, limit=limit, include_deleted=include_deleted
    )
    return SessionsResponse(
        sessions=[SessionSummary.from_row(row) for row in rows],
        count=len(rows),
    )


@router.get("/sessions/audit", response_model=SessionAuditResponse)
async def list_session_audit(
    request: Request,
    tenant_id: str = Query(min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> SessionAuditResponse:
    """The conversation rename, hide and restore actions for one tenant."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    rows = await deps.store.list_session_actions(tenant_id=tenant.tenant_id, limit=limit)
    return SessionAuditResponse(
        tenant_id=tenant.tenant_id,
        rows=[SessionAuditRow.from_row(row) for row in rows],
        count=len(rows),
    )


@router.patch("/sessions/{session_id}", status_code=204)
async def rename_session(request: Request, session_id: str, payload: SessionRename) -> Response:
    """Rename one conversation without changing its runs or traces."""
    deps = _deps(request.app)
    tenant = _tenant(deps, payload.tenant_id)
    renamed = await deps.store.rename_session(
        tenant_id=tenant.tenant_id, session_id=session_id, title=payload.title
    )
    if not renamed:
        raise HTTPException(status_code=404, detail="session not found")
    await deps.store.record_session_action(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        action="rename",
        actor="local:admin",
    )
    return Response(status_code=204)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    request: Request,
    session_id: str,
    tenant_id: str = Query(min_length=1),
) -> Response:
    """Hide one conversation from the sidebar; runs and traces remain stored."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    deleted = await deps.store.delete_session(tenant_id=tenant.tenant_id, session_id=session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    await deps.store.record_session_action(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        action="delete",
        actor="local:admin",
    )
    return Response(status_code=204)


@router.post("/sessions/{session_id}/restore", status_code=204)
async def restore_session(
    request: Request,
    session_id: str,
    tenant_id: str = Query(min_length=1),
) -> Response:
    """Put one hidden conversation back in the sidebar without touching its runs."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    restored = await deps.store.restore_session(tenant_id=tenant.tenant_id, session_id=session_id)
    if not restored:
        raise HTTPException(status_code=404, detail="conversation is not deleted")
    await deps.store.record_session_action(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        action="restore",
        actor="local:admin",
    )
    return Response(status_code=204)


@router.post("/sessions/bulk-delete")
async def bulk_delete_sessions(
    request: Request, payload: SessionBulkDelete
) -> dict[str, list[str]]:
    """Hide many conversations at once, returning the ids that did not exist."""
    deps = _deps(request.app)
    tenant = _tenant(deps, payload.tenant_id)
    missing: list[str] = []
    for session_id in dict.fromkeys(payload.session_ids):
        deleted = await deps.store.delete_session(tenant_id=tenant.tenant_id, session_id=session_id)
        if not deleted:
            missing.append(session_id)
        else:
            await deps.store.record_session_action(
                tenant_id=tenant.tenant_id,
                session_id=session_id,
                action="bulk_delete",
                actor="local:admin",
            )
    return {
        "deleted": [value for value in payload.session_ids if value not in missing],
        "missing": missing,
    }


@router.get("/users", response_model=UsersResponse)
async def list_users(
    request: Request,
    tenant_id: str = Query(min_length=1, description="Tenant whose members to list."),
) -> UsersResponse:
    """The users who belong to one tenant and the role each holds."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    rows = await deps.store.list_users(tenant_id=tenant.tenant_id)
    return UsersResponse(
        tenant_id=tenant.tenant_id,
        users=[UserSummary.from_row(row) for row in rows],
        count=len(rows),
    )


@router.post("/users", response_model=UserSummary, status_code=201)
async def create_user(request: Request, payload: UserCreate) -> UserSummary:
    """Create a user, or add an existing email, to one tenant with a role."""
    deps = _deps(request.app)
    tenant = _tenant(deps, payload.tenant_id)
    row = await deps.store.create_user(
        tenant_id=tenant.tenant_id,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
    )
    return UserSummary.from_row(row)


@router.post("/attachments/parse", response_model=AttachmentParsed)
async def parse_attachment(payload: AttachmentParse) -> AttachmentParsed:
    """Extract plain text from one PDF; no OCR and no stored file."""
    if not payload.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="only .pdf attachments are parsed here")
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
    if len(raw) > 10_000_000:
        raise HTTPException(status_code=413, detail="PDF is larger than 10 MB")
    try:
        reader = PdfReader(io.BytesIO(raw))
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF could not be read: {exc}") from exc
    if not text:
        raise HTTPException(status_code=422, detail="the PDF contains no extractable text")
    return AttachmentParsed(name=payload.filename, content=text)


@router.get("/documents", response_model=DocumentsResponse)
async def list_documents(
    request: Request,
    tenant_id: str = Query(min_length=1, description="Tenant whose documents to list."),
) -> DocumentsResponse:
    """The tenant's ingested RAG documents, newest first, with their chunk counts."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    rows = await deps.store.list_documents(tenant_id=tenant.tenant_id)
    return DocumentsResponse(
        tenant_id=tenant.tenant_id,
        documents=[DocumentSummary.from_row(row) for row in rows],
        count=len(rows),
    )


@router.get("/documents/{document_id}/chunks", response_model=DocumentChunksResponse)
async def list_document_chunks(
    request: Request,
    document_id: str,
    tenant_id: str = Query(min_length=1),
) -> DocumentChunksResponse:
    """The ordered chunks of one document, for a read-only preview."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    rows = await deps.store.list_document_chunks(
        tenant_id=tenant.tenant_id, document_id=document_id
    )
    return DocumentChunksResponse(
        document_id=document_id,
        chunks=[DocumentChunk(ordinal=int(row["ordinal"]), text=str(row["text"])) for row in rows],
        count=len(rows),
    )


@router.post("/documents/{document_id}/reindex", response_model=DocumentReindexResult)
async def reindex_document(
    request: Request,
    document_id: str,
    tenant_id: str = Query(min_length=1),
) -> DocumentReindexResult:
    """Re-embed the stored chunks of one document without re-chunking it."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    if deps.embedder is None:
        raise HTTPException(status_code=503, detail="the embedder is not available")
    chunks = await deps.store.list_document_chunks(
        tenant_id=tenant.tenant_id, document_id=document_id
    )
    if not chunks:
        raise HTTPException(status_code=404, detail="document has no stored chunks")
    texts = [str(row["text"]) for row in chunks]
    try:
        vectors = await deps.embedder.embed(texts)
        version = await deps.store.reindex_document(
            tenant_id=tenant.tenant_id,
            document_id=document_id,
            embeddings=vectors,
        )
    except ProviderTransientError as exc:
        raise HTTPException(status_code=503, detail=f"the embedder is unavailable: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if version is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentReindexResult(
        status="ok",
        document_id=document_id,
        chunks_embedded=len(vectors),
        index_version=version,
    )


@router.post("/documents", response_model=DocumentIngestResult, status_code=201)
async def upload_document(request: Request, payload: DocumentUpload) -> DocumentIngestResult:
    """Chunk, embed and upsert one uploaded markdown or text document."""
    deps = _deps(request.app)
    tenant = _tenant(deps, payload.tenant_id)
    if deps.ingest_document is None:
        raise HTTPException(
            status_code=503,
            detail="document ingestion is not available in this process",
        )
    try:
        result = await deps.ingest_document(tenant.tenant_id, payload.filename, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderTransientError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"the embedder is unavailable, so the document was not indexed: {exc}",
        ) from exc
    return DocumentIngestResult(
        status="ok",
        added=result.added,
        updated=result.updated,
        unchanged=result.unchanged,
        chunks_written=result.chunks_written,
        index_version=result.index_version,
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    request: Request,
    document_id: str,
    tenant_id: str = Query(min_length=1),
) -> Response:
    """Delete one document and its chunks, and invalidate the tenant's index version."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    deleted = await deps.store.delete_document(tenant_id=tenant.tenant_id, document_id=document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="document not found")
    return Response(status_code=204)


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


@router.delete("/runs/{run_id}/attachments/{attachment_name}", status_code=204)
async def delete_run_attachment(
    request: Request,
    run_id: str,
    attachment_name: str,
    tenant_id: str = Query(min_length=1),
) -> Response:
    """Remove one attachment from a stored run without touching the answer."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    rows = await deps.store.delete_run_attachment(
        run_id=run_id, tenant_id=tenant.tenant_id, name=attachment_name
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="run not found")
    return Response(status_code=204)


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


async def _resolve_approval(
    deps: AppDeps,
    *,
    run_id: str,
    tenant_id: str,
    approved: bool,
) -> ChatResponse:
    """Decide one pending tool call and finish the resumed run (T3.01).

    The run row is the only source of truth: its status must be
    ``waiting_approval`` and its stored ``pending_approval`` names the call, so a
    client cannot approve something the graph did not ask for. The claim is one
    atomic UPDATE, which is what makes a double click a 409 rather than two
    executions. The tenant comes from the row, never from the body.
    """
    row = await _load_run(deps, run_id, tenant_id)
    tenant = _tenant(deps, str(row["tenant_id"]))
    status = str(row.get("status"))
    if status != "waiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"run {row['id']} is not waiting for approval (status {status})",
        )
    try:
        pending = PendingApproval.model_validate(row.get("pending_approval"))
    except Exception as exc:
        raise HTTPException(
            status_code=409, detail=f"run {row['id']} has no pending tool call"
        ) from exc
    orchestrator = deps.orchestrator_for(tenant.provider, str(row["orchestrator"]), tenant.model)
    resume = getattr(orchestrator, "resume", None)
    if resume is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"orchestrator {orchestrator.name!r} has no approval hook; only the "
                "LangGraph path can resume a paused run"
            ),
        )
    claimed = await deps.store.claim_approval(
        run_id=str(row["id"]), tenant_id=str(row["tenant_id"])
    )
    if not claimed:
        raise HTTPException(
            status_code=409,
            detail=f"run {row['id']} is not waiting for approval any more",
        )
    decision = ApprovalDecision(request_id=pending.request_id, approved=approved)
    # A fresh run token: the original one may have expired while the run waited
    # (ADR 0014). RunContext keeps the same run id, so the trace stays one run.
    context = _run_context(deps, tenant, str(row["id"]), "")
    try:
        result = await resume(str(row["id"]), decision, ctx=context)
    except ApprovalUnavailable as exc:
        await _fail_waiting_run(deps, row, kind="ApprovalUnavailable", message=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("run %s: resume failed with %s", run_id, type(exc).__name__)
        await _fail_waiting_run(
            deps,
            row,
            kind=type(exc).__name__,
            message="the approval could not be applied",
        )
        raise HTTPException(status_code=500, detail="the approval could not be applied") from exc
    flushed = await deps.flush()
    await deps.store.finish_run(result, trace_complete=flushed)
    return ChatResponse.from_result(
        result,
        session_id=cast(str | None, row.get("session_id")),
        trace_incomplete=not flushed,
    )


async def _fail_waiting_run(deps: AppDeps, row: dict[str, Any], *, kind: str, message: str) -> None:
    """Store a clear end state instead of leaving a claimed run at ``running``."""
    now = datetime.now(UTC)
    await deps.store.finish_run(
        RunResult(
            run_id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            query=str(row["query"]),
            status="provider_error",
            errors=[ErrorRecord(kind=kind, message=message, stage="api.approval")],
            started_at=row.get("started_at"),
            ended_at=now,
        ),
        trace_complete=False,
    )


@router.post("/runs/{run_id}/approve", response_model=ChatResponse)
async def approve_run(
    request: Request,
    run_id: str,
    tenant_id: str = Query(min_length=1, description="Tenant that owns the run."),
) -> ChatResponse:
    """Approve the pending tool call and run to the end (AC-15b)."""
    return await _resolve_approval(
        _deps(request.app), run_id=run_id, tenant_id=tenant_id, approved=True
    )


@router.post("/runs/{run_id}/reject", response_model=ChatResponse)
async def reject_run(
    request: Request,
    run_id: str,
    tenant_id: str = Query(min_length=1, description="Tenant that owns the run."),
) -> ChatResponse:
    """Reject the pending tool call: nothing executes and the run ends."""
    return await _resolve_approval(
        _deps(request.app), run_id=run_id, tenant_id=tenant_id, approved=False
    )


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


@router.post("/settings/mcp-servers", response_model=McpServerResult, status_code=201)
async def add_mcp_server(request: Request, submission: McpServerSubmission) -> McpServerResult:
    """Add one MCP source to the database control plane (V3.03).

    A manifest is optional: without one the API reads ``tools/list`` from the
    running server, stores the discovered specs inline on the source and uses
    them for tenant validation and the settings snapshot. ``POST /settings/apply``
    then rebuilds the runtime tool sources.
    """
    deps = _deps(request.app)
    store = deps.settings_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="the settings control plane is not available; set SWITCHBOARD_CONFIG_SOURCE=db",
        )
    manifest = (submission.manifest or "").strip() or None
    discovered_tools = None
    if manifest is None:
        try:
            specs = await discover_tools(submission.url)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not specs:
            raise HTTPException(
                status_code=400,
                detail="the MCP server returned no tools to discover",
            )
        discovered_tools = [spec.model_dump(mode="json") for spec in specs]
    try:
        revision = await store.add_tool_source(
            name=submission.name,
            url=submission.url,
            manifest=manifest,
            tools=discovered_tools,
            actor="local:admin",
        )
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if discovered_tools is None:
        note = "apply changes to build and expose the new MCP server"
    else:
        note = (
            f"discovered {len(discovered_tools)} tools; "
            "apply changes to build and expose the new MCP server"
        )
    return McpServerResult(status="ok", revision=revision, note=note)


@router.post("/settings/mcp-servers/{server_name}/approve", response_model=SettingsRevision)
async def approve_mcp_server(request: Request, server_name: str) -> SettingsRevision:
    """Approve the current fingerprints of one source's tools (V3.10)."""
    deps = _deps(request.app)
    if deps.approve_tools is None:
        raise HTTPException(
            status_code=503,
            detail="the control-plane tool approval store is not available",
        )
    count = await deps.approve_tools(server_name)
    if count == 0:
        raise HTTPException(status_code=404, detail=f"unknown MCP server {server_name!r}")
    return SettingsRevision(
        status="ok",
        revision=0,
        note=f"approved {count} tool(s); apply changes to enable them",
    )


@router.post("/settings/tenants", response_model=SettingsRevision, status_code=201)
async def create_tenant_settings(request: Request, payload: TenantCreation) -> SettingsRevision:
    """Create a tenant, its database row and its allowlists in one revision."""
    deps = _deps(request.app)
    store = deps.settings_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="the settings control plane is not available; set SWITCHBOARD_CONFIG_SOURCE=db",
        )
    try:
        revision = await store.create_tenant(
            payload.tenant_id,
            name=payload.name.strip(),
            provider=payload.provider,
            model=payload.model,
            agents_allow=payload.agents_allow,
            tools_allow=payload.tools_allow,
            tools_ask=payload.tools_ask,
            connectors={
                name: binding.model_dump(mode="json")
                for name, binding in payload.connectors.items()
            },
            actor="local:admin",
        )
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SettingsRevision(
        status="ok",
        revision=revision,
        note="restart the API to build and expose the new tenant",
    )


@router.patch("/settings/tenants/{tenant_id}", response_model=SettingsRevision)
async def update_tenant_settings(
    request: Request, tenant_id: str, payload: TenantSettingsUpdate
) -> SettingsRevision:
    """Update one tenant's agent and tool allowlists in the control plane."""
    deps = _deps(request.app)
    tenant = _tenant(deps, tenant_id)
    store = deps.settings_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="the settings control plane is not available; set SWITCHBOARD_CONFIG_SOURCE=db",
        )
    try:
        revision = await store.update_tenant(
            tenant.tenant_id,
            agents_allow=payload.agents_allow,
            tools_allow=payload.tools_allow,
            tools_ask=payload.tools_ask,
            model=payload.model,
            actor="local:admin",
        )
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SettingsRevision(
        status="ok",
        revision=revision,
        note="restart the API to apply tenant allowlist changes",
    )


@router.patch("/settings/providers/{provider_name}", response_model=SettingsRevision)
async def update_provider_settings(
    request: Request, provider_name: str, payload: ProviderSettingsUpdate
) -> SettingsRevision:
    """Change one provider's model; API keys and base URLs stay in the environment."""
    deps = _deps(request.app)
    store = deps.settings_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="the settings control plane is not available; set SWITCHBOARD_CONFIG_SOURCE=db",
        )
    try:
        revision = await store.update_provider_model(
            provider_name, payload.model.strip(), actor="local:admin"
        )
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SettingsRevision(
        status="ok",
        revision=revision,
        note="restart the API to apply the provider model change",
    )


@router.put("/settings/providers/{provider_name}/credential", response_model=SettingsRevision)
async def save_provider_credential(
    request: Request,
    provider_name: str,
    payload: ProviderCredentialUpdate,
) -> SettingsRevision:
    """Encrypt and store one provider API key; the response never contains it."""
    deps = _deps(request.app)
    store = deps.settings_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="the settings control plane is not available; set SWITCHBOARD_CONFIG_SOURCE=db",
        )
    try:
        revision = await store.save_provider_credential(
            provider_name, payload.api_key, actor="local:admin"
        )
    except ConfigError as exc:
        status_code = 503 if "SWITCHBOARD_SETTINGS_KEY" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return SettingsRevision(
        status="ok",
        revision=revision,
        note="credential stored encrypted; apply changes to use it",
    )


@router.post("/settings/providers/{provider_name}/test", response_model=ProviderTestResult)
async def test_provider_connection(request: Request, provider_name: str) -> ProviderTestResult:
    """Ask the provider for its model list without returning the secret (V3.03)."""
    deps = _deps(request.app)
    store = deps.settings_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="the settings control plane is not available; set SWITCHBOARD_CONFIG_SOURCE=db",
        )
    try:
        settings = await store.load()
        settings = await store.apply_stored_credentials(settings)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider = settings.providers.providers.get(provider_name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider_name!r}")
    key = provider.api_key or os.environ.get(provider.api_key_env or "", "")
    result = await asyncio.to_thread(check_provider, provider.base_url or "", key, provider.adapter)
    return ProviderTestResult.model_validate(result)


@router.post("/settings/apply", response_model=SettingsRevision)
async def apply_settings(request: Request) -> SettingsRevision:
    """Rebuild the runtime dependency graph from the control plane without a restart."""
    application = request.app
    reloader = getattr(application.state, "reloader", None)
    if reloader is None:
        raise HTTPException(
            status_code=503,
            detail="this process cannot rebuild its runtime settings",
        )
    old = application.state.deps
    new = await reloader()
    application.state.deps = new
    if old is not None and old.closer is not None:
        await old.closer()
    return SettingsRevision(
        status="ok",
        revision=0,
        note="runtime settings rebuilt from the control plane",
    )


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


def check_lmstudio(base_url: str, api_key: str = "lm-studio") -> dict[str, Any]:
    if not base_url:
        return {"status": "error", "detail": "LMSTUDIO_BASE_URL is not set"}
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            payload: Any = json.loads(response.read())
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    models = [
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return {"status": "ok", "models": sorted(models)}


def check_provider(base_url: str, api_key: str, adapter: str) -> dict[str, Any]:
    """One provider connectivity check without returning the key (V3.03)."""
    if not base_url:
        return {"status": "error", "detail": "provider has no base URL"}
    base = base_url.rstrip("/")
    if adapter == "anthropic":
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        url = base + "/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            payload: Any = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {"status": "error", "detail": f"HTTP {exc.code} from the provider"}
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    models = [
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return {
        "status": "ok",
        "detail": f"provider returned {len(models)} model(s)",
        "models": sorted(str(model) for model in models),
    }


def check_http(url: str) -> dict[str, str]:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as response:
            response.read()
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    return {"status": "ok"}


async def default_health() -> tuple[int, dict[str, Any]]:
    """The dependency report: postgres, both MCP servers, lmstudio (T1.02, T3.08)."""
    postgres = await check_postgres(os.environ.get("SWITCHBOARD_DATABASE_URL", ""))
    lmstudio = await asyncio.to_thread(
        check_lmstudio,
        os.environ.get("LMSTUDIO_BASE_URL", ""),
        os.environ.get("LMSTUDIO_API_KEY", "lm-studio"),
    )
    tools_url = os.environ.get("SWITCHBOARD_TOOLS_URL", "")
    if tools_url:
        mcp_olist = await asyncio.to_thread(check_http, tools_url.rstrip("/") + "/health")
    else:
        mcp_olist = {"status": "error", "detail": "SWITCHBOARD_TOOLS_URL is not set"}
    reviews_url = os.environ.get("MCP_REVIEWS_URL", "")
    if reviews_url:
        mcp_reviews = await asyncio.to_thread(check_http, reviews_url.rstrip("/") + "/health")
    else:
        mcp_reviews = {"status": "error", "detail": "MCP_REVIEWS_URL is not set"}
    dependencies: dict[str, dict[str, Any]] = {
        "postgres": postgres,
        "mcp_olist": mcp_olist,
        "mcp_reviews": mcp_reviews,
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


def _auth_user(request: Request) -> AuthUser | None:
    user = getattr(request.state, "auth", None)
    return user if isinstance(user, AuthUser) else None


def _auth_payload(user: AuthUser) -> AuthUserPayload:
    return AuthUserPayload(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        tenant_id=user.tenant_id,
        role=cast(Literal["admin", "member"], user.role),
    )


@router.get("/tenants", response_model=TenantsResponse)
async def list_tenants(request: Request) -> TenantsResponse:
    """The configured tenant ids, safe to show before sign-in."""
    deps = _deps(request.app)
    tenants = sorted(deps.tenants.tenants)
    # The demo tenant is the one the seeded administrator belongs to. Keep it
    # first so the sign-in page does not default to a tenant with no users.
    if "default" in tenants:
        tenants.remove("default")
        tenants.insert(0, "default")
    return TenantsResponse(tenants=tenants)


@router.get("/auth/status", response_model=AuthStatus)
async def auth_status(request: Request) -> AuthStatus:
    """Whether this process requires a session, and who the cookie belongs to."""
    deps = _deps(request.app)
    user = _auth_user(request)
    return AuthStatus(
        auth_required=deps.auth_required,
        authenticated=user is not None,
        user=None if user is None else _auth_payload(user),
    )


@router.post("/auth/login", response_model=AuthStatus)
async def auth_login(request: Request, payload: AuthLogin, response: Response) -> AuthStatus:
    """Verify one local password and set the HttpOnly session cookie."""
    deps = _deps(request.app)
    tenant = _tenant(deps, payload.tenant_id)
    row = await deps.store.get_login_user(
        email=payload.email.strip().lower(), tenant_id=tenant.tenant_id
    )
    if row is None or not bool(row.get("active", True)):
        raise HTTPException(status_code=401, detail="email or password is incorrect")
    password_hash = cast(str | None, row.get("password_hash"))
    password_ok = False
    if password_hash:
        password_ok = verify_password(payload.password, password_hash)
    elif deps.admin_password is not None and hmac.compare_digest(
        payload.password, deps.admin_password
    ):
        password_ok = True
        await deps.store.set_user_password(
            user_id=str(row["id"]), password_hash=hash_password(payload.password)
        )
    if not password_ok:
        raise HTTPException(status_code=401, detail="email or password is incorrect")
    user = AuthUser(
        user_id=str(row["id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        tenant_id=tenant.tenant_id,
        role=str(row["role"]),
    )
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user, deps.auth_secret),
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    request.state.auth = user
    return AuthStatus(
        auth_required=deps.auth_required,
        authenticated=True,
        user=_auth_payload(user),
    )


@router.post("/auth/logout", response_model=AuthStatus)
async def auth_logout(request: Request, response: Response) -> AuthStatus:
    """Clear the session cookie; the client decides where to go next."""
    deps = _deps(request.app)
    response.delete_cookie(SESSION_COOKIE, path="/")
    request.state.auth = None
    return AuthStatus(auth_required=deps.auth_required, authenticated=False)


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
            app.state.reloader = build_app_deps
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

    @application.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Response:
        current = application.state.deps
        if current is None or not current.auth_required:
            return cast(Response, await call_next(request))
        path = request.url.path
        public = (
            path in {"/health", "/tenants"}
            or path.startswith("/auth/")
            or path in {"/docs", "/openapi.json", "/redoc"}
        )
        token = request.cookies.get(SESSION_COOKIE)
        user = None if token is None else verify_session(token, current.auth_secret)
        request.state.auth = user
        if public:
            return cast(Response, await call_next(request))
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "sign in is required"},
            )
        admin_only = path.startswith(("/settings", "/users", "/documents"))
        if admin_only and request.method not in {"GET", "HEAD", "OPTIONS"} and user.role != "admin":
            return JSONResponse(
                status_code=403,
                content={"detail": "this action requires the admin role"},
            )
        return cast(Response, await call_next(request))

    application.include_router(router)
    return application


app = create_app()

__all__ = [
    "HTTP_TIMEOUT_S",
    "NATIVE_TELEMETRY_OFF",
    "RUN_TOKEN_MIN_BYTES",
    "SSE_HEADERS",
    "app",
    "approve_run",
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
    "reject_run",
    "request_with_history",
    "require_run_token_secret",
]
