"""PostgreSQL span exporter (design.md sections 12.2 and 14, ADR 0004).

The exporter is installed behind the SDK's ``BatchSpanProcessor``: its
``export`` method runs on the processor's worker thread, so it opens a short
lived ``asyncpg`` connection and inserts the batch in one round trip. No span
attribute, prompt or token is ever logged; a failed batch is reported by
exception type only.

The ``spans`` table is append-only and has no foreign key on ``run_id``, so a
span from the MCP process can be written before the run row is committed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)

INSERT_SQL = """
INSERT INTO public.spans (
    span_id, trace_id, parent_span_id, run_id, name, kind,
    start_time, end_time, status, attributes, events
) VALUES (
    $1, $2, $3, $4, $5, $6,
    $7, $8, $9, $10::jsonb, $11::jsonb
)
ON CONFLICT DO NOTHING
"""


def jsonable(value: Any) -> Any:
    """Return a value ``json.dumps`` accepts without losing the common types."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(item) for item in value]
    return str(value)


def timestamp_from_ns(value: int) -> datetime:
    seconds, rest = divmod(int(value), 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=rest // 1000)


def span_id_hex(value: int | None) -> str | None:
    if value is None:
        return None
    return format(value, "016x")


def trace_id_hex(value: int) -> str:
    return format(value, "032x")


def run_id_of(attributes: dict[str, Any]) -> uuid.UUID | None:
    raw = attributes.get("switchboard.run_id")
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def span_to_row(span: ReadableSpan) -> tuple[Any, ...]:
    """Map a finished span to the ``spans`` columns, JSON-safe throughout."""
    attributes = {str(key): jsonable(value) for key, value in (span.attributes or {}).items()}
    events = [
        {
            "name": event.name,
            "time": timestamp_from_ns(event.timestamp).isoformat(),
            "attributes": {
                str(key): jsonable(value) for key, value in (event.attributes or {}).items()
            },
        }
        for event in span.events
    ]
    context = span.context
    parent = span.parent
    return (
        span_id_hex(context.span_id),
        trace_id_hex(context.trace_id),
        span_id_hex(parent.span_id) if parent is not None else None,
        run_id_of(attributes),
        span.name,
        span.kind.name.lower(),
        timestamp_from_ns(span.start_time or 0),
        timestamp_from_ns(span.end_time or span.start_time or 0),
        span.status.status_code.name.lower(),
        json.dumps(attributes, ensure_ascii=False, default=str),
        json.dumps(events, ensure_ascii=False, default=str),
    )


async def insert_rows(dsn: str, rows: list[tuple[Any, ...]], *, timeout_s: float) -> None:
    connection = await asyncpg.connect(dsn, timeout=timeout_s)
    try:
        await connection.executemany(INSERT_SQL, rows)
    finally:
        await connection.close()


class PostgresSpanExporter(SpanExporter):
    """Writes finished spans to ``public.spans`` (design.md section 12.2)."""

    def __init__(self, dsn: str, *, timeout_s: float = 5.0) -> None:
        if not dsn:
            raise ValueError("a database URL is required for the span exporter")
        self._dsn = dsn
        self._timeout_s = timeout_s

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows = [span_to_row(span) for span in spans]
        if not rows:
            return SpanExportResult.SUCCESS
        try:
            asyncio.run(insert_rows(self._dsn, rows, timeout_s=self._timeout_s))
        except Exception as exc:
            # The message could echo a column value; the type is enough to see
            # that the exporter is failing while the trace endpoint shows less.
            logger.warning("span export failed: %s", type(exc).__name__)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        # The batch processor has already written every batch by the time it
        # calls force_flush; there is no second buffer here.
        return True

    def shutdown(self) -> None:
        return None
