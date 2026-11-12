"""Response models for the API.

Requests are validated with the core models (``ChatRequest``). These models
only describe what the API returns, so the database rows and the client payload
stay separate from the domain contract in ``core.models``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, Field

from switchboard.core.models import (
    Citation,
    ErrorRecord,
    RoutingDecision,
    RunResult,
    RunStatus,
    Usage,
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

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> RunSummary:
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
        )


class RunsResponse(BaseModel):
    runs: list[RunSummary]
    count: int


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
    "ChatResponse",
    "RunSummary",
    "RunsResponse",
    "TraceResponse",
    "TraceSpan",
]
