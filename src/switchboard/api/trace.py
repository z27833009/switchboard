"""Span tree assembly for ``GET /runs/{id}/trace`` (design.md section 12.3).

Spans are written by two processes (the API and the MCP server) and arrive in
arbitrary order, so the tree is built from ``parent_span_id`` rather than from
insertion order. A span whose parent is not in the result becomes a root: that
happens when a batch was lost, and the response is then marked incomplete by the
caller rather than pretending the tree is whole.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from switchboard.api.schemas import TraceResponse, TraceSpan


def duration_ms(start: Any, end: Any) -> int:
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def span_node(row: Mapping[str, Any]) -> TraceSpan:
    return TraceSpan(
        span_id=str(row["span_id"]),
        parent_span_id=cast(str | None, row.get("parent_span_id")),
        trace_id=str(row["trace_id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        start=row["start_time"],
        end=row["end_time"],
        duration_ms=duration_ms(row.get("start_time"), row.get("end_time")),
        attributes=dict(cast(Mapping[str, Any], row.get("attributes") or {})),
        events=[dict(item) for item in cast(Sequence[Mapping[str, Any]], row.get("events") or [])],
    )


def assemble_trace(
    *,
    run_id: str,
    rows: Sequence[Mapping[str, Any]],
    incomplete: bool,
) -> TraceResponse:
    ordered = sorted(rows, key=lambda row: (row["start_time"], str(row["span_id"])))
    nodes: dict[str, TraceSpan] = {}
    by_row: list[tuple[Mapping[str, Any], TraceSpan]] = []
    for row in ordered:
        node = span_node(row)
        nodes[node.span_id] = node
        by_row.append((row, node))

    roots: list[TraceSpan] = []
    for _row, node in by_row:
        parent_id = node.parent_span_id
        parent = nodes.get(parent_id) if parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)

    trace_ids = {node.trace_id for _, node in by_row}
    return TraceResponse(
        run_id=run_id,
        trace_id=next(iter(trace_ids)) if len(trace_ids) == 1 else None,
        incomplete=incomplete,
        span_count=len(ordered),
        spans=roots,
    )


__all__ = ["assemble_trace", "duration_ms", "span_node"]
