"""Tree assembly for GET /runs/{id}/trace, independent of the database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from switchboard.api.trace import assemble_trace


def at(offset_ms: int) -> datetime:
    base = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
    return base + timedelta(milliseconds=offset_ms)


def span(
    span_id: str,
    name: str,
    *,
    parent: str | None = None,
    start_ms: int = 0,
    end_ms: int = 10,
    kind: str = "internal",
    attributes: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "span_id": span_id,
        "trace_id": "a" * 32,
        "parent_span_id": parent,
        "run_id": None,
        "name": name,
        "kind": kind,
        "start_time": at(start_ms),
        "end_time": at(end_ms),
        "status": "ok",
        "attributes": attributes or {},
        "events": events or [],
    }


def test_children_are_nested_and_sorted_by_start() -> None:
    rows = [
        span("child-b", "agent.docs", parent="root", start_ms=20, end_ms=40),
        span("root", "run", start_ms=0, end_ms=60, kind="server", attributes={"x": 1}),
        span("grandchild", "chat qwen3-8b", parent="child-b", start_ms=22, end_ms=30),
        span("child-a", "agent.data", parent="root", start_ms=10, end_ms=35),
    ]
    trace = assemble_trace(run_id="r1", rows=rows, incomplete=False)

    assert trace.span_count == 4
    assert trace.trace_id == "a" * 32
    assert trace.incomplete is False
    assert [node.name for node in trace.spans] == ["run"]
    root = trace.spans[0]
    assert root.attributes == {"x": 1}
    assert [node.name for node in root.children] == ["agent.data", "agent.docs"]
    assert root.children[1].children[0].name == "chat qwen3-8b"
    assert root.duration_ms == 60
    assert root.children[0].duration_ms == 25


def test_orphan_spans_become_roots_and_keep_the_events() -> None:
    rows = [
        span(
            "orphan",
            "execute_tool orders_summary@1",
            parent="missing",
            events=[{"name": "tool.result", "attributes": {"switchboard.tool.result": "{}"}}],
        ),
    ]
    trace = assemble_trace(run_id="r1", rows=rows, incomplete=True)
    assert trace.incomplete is True
    assert [node.span_id for node in trace.spans] == ["orphan"]
    assert trace.spans[0].events[0]["name"] == "tool.result"


def test_empty_trace_is_empty_and_incomplete() -> None:
    trace = assemble_trace(run_id="r1", rows=[], incomplete=True)
    assert trace.span_count == 0
    assert trace.spans == []
    assert trace.trace_id is None
