"""Step event stream from spans (design.md section 12.5).

``StepEventProcessor`` is a second ``SpanProcessor`` next to the PostgreSQL
exporter. It publishes the step events of one run to an in-process queue while
``POST /chat/stream`` consumes that queue, so the live view and the stored trace
come from the same instrumentation. ``run.finished`` is not a span: the stream
publishes it after the flush and the run row write.

Phase 1 has no token streaming: ``answer.delta`` arrives with provider streaming
in v1.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.trace import SpanKind

RUN_STARTED = "run.started"
PLAN_DECIDED = "plan.decided"
AGENT_STARTED = "agent.started"
AGENT_FINISHED = "agent.finished"
TOOL_CALLED = "tool.called"
GATE_CHECKED = "gate.checked"
RUN_FINISHED = "run.finished"

PHASE_ONE_EVENTS = (
    RUN_STARTED,
    PLAN_DECIDED,
    AGENT_STARTED,
    AGENT_FINISHED,
    TOOL_CALLED,
    GATE_CHECKED,
    RUN_FINISHED,
)

RUN_ID_ATTRIBUTE = "switchboard.run_id"
AGENT_PREFIX = "agent."
TOOL_PREFIX = "execute_tool "


@dataclass(frozen=True)
class StepEvent:
    """One server-sent event: a type, the run it belongs to and its payload."""

    type: str
    run_id: str
    data: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {"run_id": self.run_id, **self.data}

    def to_sse(self) -> str:
        body = json.dumps(self.payload(), ensure_ascii=False, default=str)
        return f"event: {self.type}\ndata: {body}\n\n"


class StepEventPublisher:
    """Per-run queues. Publishing to a run with no active stream is a no-op."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[StepEvent]] = {}

    def register(self, run_id: str) -> asyncio.Queue[StepEvent]:
        queue: asyncio.Queue[StepEvent] = asyncio.Queue()
        self._queues[run_id] = queue
        return queue

    def unregister(self, run_id: str) -> None:
        self._queues.pop(run_id, None)

    def active_runs(self) -> list[str]:
        return sorted(self._queues)

    def publish(self, event: StepEvent) -> None:
        queue = self._queues.get(event.run_id)
        if queue is not None:
            queue.put_nowait(event)


def _run_id(span: ReadableSpan | Span) -> str | None:
    attributes = span.attributes or {}
    value = attributes.get(RUN_ID_ATTRIBUTE)
    return value if isinstance(value, str) and value else None


def _agent_of(span: ReadableSpan | Span) -> str | None:
    attributes = span.attributes or {}
    value = attributes.get("switchboard.agent")
    if isinstance(value, str) and value:
        return value
    if span.name.startswith(AGENT_PREFIX):
        return span.name[len(AGENT_PREFIX) :] or None
    return None


def _duration_ms(span: ReadableSpan) -> int:
    if span.start_time is None or span.end_time is None:
        return 0
    return max(0, (span.end_time - span.start_time) // 1_000_000)


class StepEventProcessor(SpanProcessor):
    """Translates selected span starts and ends into step events (design 12.5)."""

    def __init__(self, publisher: StepEventPublisher) -> None:
        self._publisher = publisher

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        run_id = _run_id(span)
        if run_id is None:
            return
        if span.name == "run":
            self._publisher.publish(StepEvent(RUN_STARTED, run_id))
        elif span.name.startswith(AGENT_PREFIX):
            agent = _agent_of(span)
            if agent is not None:
                self._publisher.publish(StepEvent(AGENT_STARTED, run_id, {"agent": agent}))

    def on_end(self, span: ReadableSpan) -> None:
        run_id = _run_id(span)
        if run_id is None:
            return
        attributes = span.attributes or {}
        name = span.name

        if name == "supervisor.plan":
            label = attributes.get("switchboard.plan")
            agents = (
                [part for part in str(label).split("+") if part]
                if isinstance(label, str) and label
                else []
            )
            reason = attributes.get("switchboard.plan.reason")
            self._publisher.publish(
                StepEvent(
                    PLAN_DECIDED,
                    run_id,
                    {
                        "plan": label,
                        "agents": agents,
                        "reason": reason if isinstance(reason, str) else "",
                    },
                )
            )
        elif name.startswith(AGENT_PREFIX):
            agent = _agent_of(span)
            if agent is None:
                return
            partial = attributes.get("switchboard.partial")
            citations = attributes.get("switchboard.citations")
            self._publisher.publish(
                StepEvent(
                    AGENT_FINISHED,
                    run_id,
                    {
                        "agent": agent,
                        "partial": bool(partial) if isinstance(partial, bool) else False,
                        "citations": citations if isinstance(citations, int) else 0,
                    },
                )
            )
        elif name.startswith(TOOL_PREFIX) and span.kind != SpanKind.SERVER:
            status = attributes.get("switchboard.tool.status")
            total_rows = attributes.get("switchboard.tool.total_rows")
            audit_id = attributes.get("switchboard.tool.audit_id")
            self._publisher.publish(
                StepEvent(
                    TOOL_CALLED,
                    run_id,
                    {
                        "tool": name[len(TOOL_PREFIX) :],
                        "status": status if isinstance(status, str) else "unknown",
                        "total_rows": total_rows if isinstance(total_rows, int) else None,
                        "duration_ms": _duration_ms(span),
                        "audit_id": audit_id if isinstance(audit_id, str) else None,
                    },
                )
            )
        elif name == "gate":
            verdict = attributes.get("switchboard.gate.verdict")
            action = attributes.get("switchboard.gate.action")
            self._publisher.publish(
                StepEvent(
                    GATE_CHECKED,
                    run_id,
                    {
                        "verdict": verdict if isinstance(verdict, str) else "unknown",
                        "action": action if isinstance(action, str) else "unknown",
                    },
                )
            )

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


__all__ = [
    "AGENT_FINISHED",
    "AGENT_STARTED",
    "GATE_CHECKED",
    "PHASE_ONE_EVENTS",
    "PLAN_DECIDED",
    "RUN_FINISHED",
    "RUN_STARTED",
    "TOOL_CALLED",
    "StepEvent",
    "StepEventProcessor",
    "StepEventPublisher",
]
