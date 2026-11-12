"""Tracing: provider setup, PostgreSQL exporter and span helpers.

Design.md sections 12.1 and 12.2, ADR 0004. Import from here rather than from
the submodules so the public surface stays small.
"""

from __future__ import annotations

from switchboard.tracing.config import (
    DEFAULT_EXPORTER,
    OTLP_ENDPOINT_VARIABLE,
    SUPPORTED_EXPORTERS,
    TracingOptions,
    load_tracing_options,
    resolve_tracing_options,
)
from switchboard.tracing.pg_exporter import PostgresSpanExporter, span_to_row
from switchboard.tracing.runs import insert_run
from switchboard.tracing.setup import (
    FLUSH_TIMEOUT_S,
    TracingHandle,
    configure_tracing,
    create_span_exporter,
    current_handle,
    force_flush,
    force_flush_async,
    otlp_traces_url,
)
from switchboard.tracing.spans import (
    TOOL_RESULT_ATTRIBUTE,
    TOOL_RESULT_MAX_BYTES,
    add_tool_result_event,
    agent_span,
    agent_span_name,
    apply_request_metadata,
    apply_usage,
    args_hash,
    chat_span_name,
    embeddings_span_name,
    extract_traceparent,
    inject_traceparent,
    record_error,
    run_span,
    run_span_name,
    set_attributes,
    supervisor_span,
    supervisor_span_name,
    tool_span_name,
)

__all__ = [
    "DEFAULT_EXPORTER",
    "FLUSH_TIMEOUT_S",
    "OTLP_ENDPOINT_VARIABLE",
    "SUPPORTED_EXPORTERS",
    "TOOL_RESULT_ATTRIBUTE",
    "TOOL_RESULT_MAX_BYTES",
    "PostgresSpanExporter",
    "TracingHandle",
    "TracingOptions",
    "add_tool_result_event",
    "agent_span",
    "agent_span_name",
    "apply_request_metadata",
    "apply_usage",
    "args_hash",
    "chat_span_name",
    "configure_tracing",
    "create_span_exporter",
    "current_handle",
    "embeddings_span_name",
    "extract_traceparent",
    "force_flush",
    "force_flush_async",
    "inject_traceparent",
    "insert_run",
    "load_tracing_options",
    "otlp_traces_url",
    "record_error",
    "resolve_tracing_options",
    "run_span",
    "run_span_name",
    "set_attributes",
    "span_to_row",
    "supervisor_span",
    "supervisor_span_name",
    "tool_span_name",
]
