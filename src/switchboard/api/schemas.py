"""Request and response models for the API.

Requests are validated with the core models (``ChatRequest``). The feedback
body lives here because it is an API contract and not a domain model: nothing
inside a run needs it. These models only describe what the API accepts and
returns, so the database rows and the client payload stay separate from the
domain contract in ``core.models``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator

from switchboard.core.models import (
    Citation,
    ErrorRecord,
    RoutingDecision,
    RunResult,
    RunStatus,
    Usage,
)

AuditStatus = Literal["ok", "error", "denied"]
REDACTED_ARGUMENT = "[redacted]"
# Feedback: -1 is a negative rating, 1 is positive (design.md sections 14 and
# 20.1). Zero carries no signal and every other value is rejected.
FeedbackRating = Literal[-1, 1]
FEEDBACK_NOTE_MAX_CHARS = 2000


class FeedbackSubmission(BaseModel):
    """The body of ``POST /runs/{id}/feedback`` (AC-24a).

    ``rating`` is -1 or 1 and nothing else; a note is optional, at most
    ``FEEDBACK_NOTE_MAX_CHARS`` characters, and is trimmed, so a blank note is
    stored as no note rather than as an empty string.
    """

    rating: FeedbackRating
    note: str | None = Field(default=None, max_length=FEEDBACK_NOTE_MAX_CHARS)

    @field_validator("rating", mode="before")
    @classmethod
    def rating_is_an_integer(cls, value: object) -> object:
        """Reject booleans, floats and strings: a rating is a JSON integer."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("rating must be the integer -1 or 1")
        return value

    @field_validator("note")
    @classmethod
    def blank_note_is_no_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class FeedbackSummary(BaseModel):
    """One stored feedback row, as the write and the run detail return it.

    Feedback is append-only, so ``GET /runs/{id}`` shows the newest row as the
    run's current verdict; a change of mind adds a row instead of editing one.
    """

    id: str
    run_id: str
    tenant_id: str
    rating: FeedbackRating
    note: str | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FeedbackSummary:
        return cls(
            id=str(row["id"]),
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            rating=cast(FeedbackRating, int(row["rating"])),
            note=cast(str | None, row.get("note")),
            created_at=cast(datetime, row["created_at"]),
        )


class ChatResponse(BaseModel):
    """The final payload of ``POST /chat`` and of the ``run.finished`` event."""

    run_id: str
    tenant_id: str
    session_id: str | None = None
    status: RunStatus
    answer: str | None = None
    plan: RoutingDecision | None = None
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))
    cost_usd: float | None = None
    cache_hit: bool = False
    attempts: int = 0
    errors: list[ErrorRecord] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    trace_incomplete: bool = False

    @classmethod
    def from_result(
        cls,
        result: RunResult,
        *,
        session_id: str | None = None,
        trace_incomplete: bool = False,
    ) -> ChatResponse:
        answer = result.answer
        return cls(
            run_id=result.run_id,
            tenant_id=result.tenant_id,
            session_id=session_id,
            status=result.status,
            answer=answer.answer if answer is not None else None,
            plan=result.plan,
            citations=answer.citations if answer is not None else [],
            usage=result.usage,
            cost_usd=result.cost_usd,
            cache_hit=result.cache_hit,
            attempts=result.attempts,
            errors=result.errors,
            started_at=result.started_at,
            ended_at=result.ended_at,
            trace_incomplete=trace_incomplete,
        )


class RunSummary(BaseModel):
    """One row of ``GET /runs`` and the body of ``GET /runs/{id}``."""

    run_id: str
    tenant_id: str
    session_id: str | None = None
    query: str
    orchestrator: str
    provider: str | None = None
    plan: str | None = None
    status: RunStatus
    cache_hit: bool = False
    attempts: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    trace_complete: bool = True
    started_at: datetime | None = None
    ended_at: datetime | None = None
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    # Newest feedback row for this run; only ``GET /runs/{id}`` loads it, so
    # the run list stays one query (design.md section 14).
    latest_feedback: FeedbackSummary | None = None

    @classmethod
    def from_row(
        cls,
        row: Mapping[str, Any],
        *,
        latest_feedback: Mapping[str, Any] | None = None,
    ) -> RunSummary:
        citations = [
            Citation.model_validate(item)
            for item in cast(list[dict[str, Any]], row.get("citations") or [])
        ]
        errors = [
            ErrorRecord.model_validate(item)
            for item in cast(list[dict[str, Any]], row.get("errors") or [])
        ]
        return cls(
            run_id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            session_id=cast(str | None, row.get("session_id")),
            query=str(row["query"]),
            orchestrator=str(row["orchestrator"]),
            provider=cast(str | None, row.get("provider")),
            plan=cast(str | None, row.get("plan")),
            status=cast(RunStatus, row["status"]),
            cache_hit=bool(row.get("cache_hit", False)),
            attempts=int(row.get("attempts", 0)),
            input_tokens=int(row.get("input_tokens", 0)),
            cached_input_tokens=int(row.get("cached_input_tokens", 0)),
            output_tokens=int(row.get("output_tokens", 0)),
            cost_usd=cast(float | None, row.get("cost_usd")),
            trace_complete=bool(row.get("trace_complete", True)),
            started_at=cast(datetime | None, row.get("started_at")),
            ended_at=cast(datetime | None, row.get("ended_at")),
            answer=cast(str | None, row.get("answer")),
            citations=citations,
            errors=errors,
            latest_feedback=(
                None if latest_feedback is None else FeedbackSummary.from_row(latest_feedback)
            ),
        )


class RunsResponse(BaseModel):
    runs: list[RunSummary]
    count: int


class RunAggregateRow(BaseModel):
    """One day or provider bucket of ``GET /runs?group_by=`` (AC-19b).

    ``cost_usd`` is the sum of the runs that have a price entry and is null
    when none does. ``unpriced_runs`` counts the rest, so an aggregate never
    passes an incomplete sum off as the full cost.
    """

    bucket: str
    run_count: int
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    unpriced_runs: int = 0

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> RunAggregateRow:
        return cls(
            bucket=str(row["bucket"]),
            run_count=int(row["run_count"]),
            input_tokens=int(row.get("input_tokens", 0)),
            cached_input_tokens=int(row.get("cached_input_tokens", 0)),
            output_tokens=int(row.get("output_tokens", 0)),
            cost_usd=cast(float | None, row.get("cost_usd")),
            unpriced_runs=int(row.get("unpriced_runs", 0)),
        )


class RunAggregateResponse(BaseModel):
    """``GET /runs?group_by=day|provider`` for one tenant."""

    group_by: Literal["day", "provider"]
    tenant_id: str
    groups: list[RunAggregateRow] = Field(default_factory=list)
    count: int = 0


class AuditRow(BaseModel):
    """One ``tool_audit`` row as ``GET /audit`` returns it (AC-18a, AC-18b).

    ``args_redacted`` is already redacted at write time by the MCP server. The
    read-side guard also redacts every key the tool manifest lists in
    ``sensitive_args``, so a raw sensitive value can never reach a response.
    """

    id: str
    run_id: str | None = None
    tenant_id: str
    tool_name: str
    tool_version: str
    args_hash: str
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    status: AuditStatus
    duration_ms: int
    created_at: datetime

    @classmethod
    def from_row(
        cls,
        row: Mapping[str, Any],
        sensitive_args: Mapping[str, Sequence[str]] | None = None,
    ) -> AuditRow:
        tool_name = str(row["tool_name"])
        tool_version = str(row["tool_version"])
        hidden: set[str] = set()
        if sensitive_args:
            hidden.update(sensitive_args.get(f"{tool_name}@{tool_version}", ()))
            hidden.update(sensitive_args.get(tool_name, ()))
        args = {
            key: REDACTED_ARGUMENT if key in hidden else value
            for key, value in dict(cast(Mapping[str, Any], row.get("args_redacted") or {})).items()
        }
        return cls(
            id=str(row["id"]),
            run_id=cast(str | None, row.get("run_id")),
            tenant_id=str(row["tenant_id"]),
            tool_name=tool_name,
            tool_version=tool_version,
            args_hash=str(row["args_hash"]),
            args_redacted=args,
            status=cast(AuditStatus, row["status"]),
            duration_ms=int(row["duration_ms"]),
            created_at=cast(datetime, row["created_at"]),
        )


class AuditResponse(BaseModel):
    """One page of audit rows, newest first, with the offset that produced it."""

    rows: list[AuditRow] = Field(default_factory=list)
    count: int = 0
    limit: int = 50
    offset: int = 0


class TraceSpan(BaseModel):
    """One node of the span tree (design 12.3)."""

    span_id: str
    parent_span_id: str | None = None
    trace_id: str
    name: str
    kind: str
    status: str
    start: datetime
    end: datetime
    duration_ms: int
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    children: list[TraceSpan] = Field(default_factory=list)


class TraceResponse(BaseModel):
    """The assembled tree for one run (AC-05a) plus its completeness flag."""

    run_id: str
    trace_id: str | None = None
    incomplete: bool = False
    span_count: int = 0
    spans: list[TraceSpan] = Field(default_factory=list)


__all__ = [
    "FEEDBACK_NOTE_MAX_CHARS",
    "REDACTED_ARGUMENT",
    "AuditResponse",
    "AuditRow",
    "AuditStatus",
    "ChatResponse",
    "FeedbackRating",
    "FeedbackSubmission",
    "FeedbackSummary",
    "RunAggregateResponse",
    "RunAggregateRow",
    "RunSummary",
    "RunsResponse",
    "TraceResponse",
    "TraceSpan",
]
