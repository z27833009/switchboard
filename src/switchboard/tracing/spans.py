"""Span helpers (design.md section 12.1).

Names, the ``switchboard.*`` attributes and the error and tool-result events
live here so the orchestrator, the provider, the tool client and the MCP server
produce the same shapes. Importing this module costs nothing until a tracer
provider is configured; with no provider the OTel API returns no-op spans.

The GenAI attributes follow the semantic conventions where they exist:
``gen_ai.operation.name``, ``gen_ai.system``, ``gen_ai.tool.name``,
``gen_ai.request.model``, ``gen_ai.usage.input_tokens`` and
``gen_ai.usage.output_tokens``. Cached input tokens stay in the project
namespace because the conventions have no stable name for them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from switchboard.core.models import Usage

TOOL_RESULT_MAX_BYTES = 64 * 1024
TOOL_RESULT_ATTRIBUTE = "switchboard.tool.result"

# CompletionRequest.metadata keys an agent may set so the provider can record
# the context decision on the chat span (design.md section 12.1).
CONTEXT_METADATA = {
    "context_tokens_estimated": "switchboard.context.tokens_estimated",
    "context_compacted": "switchboard.context.compacted",
    "context_cleared_results": "switchboard.context.cleared_results",
}


def run_span_name() -> str:
    return "run"


def supervisor_span_name() -> str:
    return "supervisor.plan"


def agent_span_name(agent: str) -> str:
    return f"agent.{agent}"


def chat_span_name(model: str) -> str:
    return f"chat {model}"


def embeddings_span_name(model: str) -> str:
    return f"embeddings {model}"


def tool_span_name(name: str) -> str:
    return f"execute_tool {name}"


def set_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    """Set non-None primitive attributes, skipping the ones OTel would reject."""
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, str | bool | int | float):
            span.set_attribute(key, value)


def apply_usage(span: Span, usage: Usage) -> None:
    span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
    span.set_attribute("switchboard.usage.cached_input_tokens", usage.cached_input_tokens)
    # Only Anthropic reports a prompt cache write today; the attribute is
    # written for every provider so a comparison sees a zero, not a gap
    # (design.md sections 8.5 and 12.1).
    span.set_attribute("switchboard.usage.cache_write_input_tokens", usage.cache_write_input_tokens)


def apply_request_metadata(span: Span, metadata: Mapping[str, Any]) -> None:
    """Record run id and context-policy facts a provider receives per call."""
    run_id = metadata.get("run_id")
    if isinstance(run_id, str) and run_id:
        span.set_attribute("switchboard.run_id", run_id)
    for key, attribute in CONTEXT_METADATA.items():
        value = metadata.get(key)
        if isinstance(value, (bool, int)):
            span.set_attribute(attribute, value)


def record_error(span: Span, error: BaseException) -> None:
    """Attach an exception event and mark the span failed.

    The status description is the exception class, never the message, so a
    message that quotes a tool result cannot leak into a span status.
    """
    span.record_exception(error)
    span.set_status(Status(StatusCode.ERROR, type(error).__name__))


def args_hash(arguments: Mapping[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inject_traceparent() -> str:
    """Return the W3C ``traceparent`` of the current span, or an empty string."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent", "")


def extract_traceparent(header: str | None) -> Context:
    """Build a parent context from an incoming ``traceparent`` header."""
    if not header:
        return Context()
    return extract({"traceparent": header}, context=Context())


def add_tool_result_event(span: Span, payload: Any) -> None:
    """Publish the full tool result as a span event, capped at 64 KB (AC-21d)."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    encoded = text.encode("utf-8")
    truncated = len(encoded) > TOOL_RESULT_MAX_BYTES
    if truncated:
        text = encoded[:TOOL_RESULT_MAX_BYTES].decode("utf-8", errors="ignore")
    span.add_event(
        "tool.result",
        {
            TOOL_RESULT_ATTRIBUTE: text,
            "switchboard.tool.result.bytes": len(encoded),
            "switchboard.tool.result.truncated": truncated,
        },
    )


@contextmanager
def run_span(
    tracer: trace.Tracer,
    *,
    tenant_id: str,
    run_id: str,
    orchestrator: str,
) -> Iterator[Span]:
    with tracer.start_as_current_span(
        run_span_name(),
        kind=SpanKind.SERVER,
        record_exception=False,
        set_status_on_exception=False,
        attributes={
            "switchboard.tenant_id": tenant_id,
            "switchboard.run_id": run_id,
            "switchboard.orchestrator": orchestrator,
        },
    ) as span:
        yield span


@contextmanager
def supervisor_span(tracer: trace.Tracer, *, run_id: str) -> Iterator[Span]:
    with tracer.start_as_current_span(
        supervisor_span_name(),
        record_exception=False,
        set_status_on_exception=False,
        attributes={"switchboard.run_id": run_id},
    ) as span:
        yield span


@contextmanager
def gate_span(tracer: trace.Tracer, *, run_id: str) -> Iterator[Span]:
    with tracer.start_as_current_span(
        "gate",
        record_exception=False,
        set_status_on_exception=False,
        attributes={"switchboard.run_id": run_id},
    ) as span:
        yield span


@contextmanager
def judge_span(tracer: trace.Tracer, *, threshold: float) -> Iterator[Span]:
    """One judge call: the untrusted review that follows a passed layer 1."""
    with tracer.start_as_current_span(
        "judge",
        kind=SpanKind.INTERNAL,
        record_exception=False,
        set_status_on_exception=False,
        attributes={"switchboard.judge.threshold": threshold},
    ) as span:
        yield span


@contextmanager
def agent_span(tracer: trace.Tracer, *, agent: str, run_id: str) -> Iterator[Span]:
    with tracer.start_as_current_span(
        agent_span_name(agent),
        record_exception=False,
        set_status_on_exception=False,
        attributes={
            "switchboard.run_id": run_id,
            "switchboard.agent": agent,
        },
    ) as span:
        yield span


@contextmanager
def retrieve_span(
    tracer: trace.Tracer,
    *,
    k: int,
    strategy: str,
    round: int,
) -> Iterator[Span]:
    with tracer.start_as_current_span(
        "retrieve",
        kind=SpanKind.INTERNAL,
        record_exception=False,
        set_status_on_exception=False,
        attributes={
            "switchboard.retrieval.k": k,
            "switchboard.retrieval.strategy": strategy,
            "switchboard.retrieval.round": round,
        },
    ) as span:
        yield span


@contextmanager
def cache_lookup_span(tracer: trace.Tracer, *, run_id: str, threshold: float) -> Iterator[Span]:
    with tracer.start_as_current_span(
        "cache.lookup",
        kind=SpanKind.INTERNAL,
        record_exception=False,
        set_status_on_exception=False,
        attributes={
            "switchboard.run_id": run_id,
            "switchboard.cache.threshold": threshold,
        },
    ) as span:
        yield span


@contextmanager
def cache_store_span(
    tracer: trace.Tracer, *, run_id: str, plan: str, ttl_days: int
) -> Iterator[Span]:
    with tracer.start_as_current_span(
        "cache.store",
        kind=SpanKind.INTERNAL,
        record_exception=False,
        set_status_on_exception=False,
        attributes={
            "switchboard.run_id": run_id,
            "switchboard.cache.plan": plan,
            "switchboard.cache.ttl_days": ttl_days,
        },
    ) as span:
        yield span


__all__ = [
    "CONTEXT_METADATA",
    "TOOL_RESULT_ATTRIBUTE",
    "TOOL_RESULT_MAX_BYTES",
    "add_tool_result_event",
    "agent_span",
    "agent_span_name",
    "apply_request_metadata",
    "apply_usage",
    "args_hash",
    "cache_lookup_span",
    "cache_store_span",
    "chat_span_name",
    "embeddings_span_name",
    "extract_traceparent",
    "gate_span",
    "inject_traceparent",
    "judge_span",
    "record_error",
    "retrieve_span",
    "run_span",
    "run_span_name",
    "set_attributes",
    "supervisor_span",
    "supervisor_span_name",
    "tool_span_name",
]
