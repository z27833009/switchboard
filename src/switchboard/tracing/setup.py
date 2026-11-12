"""Tracer provider setup, shutdown and flush (design.md sections 12.2, ADR 0004).

One process configures one provider. The API, the eval CLI and the MCP server
are separate processes and each builds its own exporter; by default they all
write to the same ``spans`` table. The exporter is selected by
``tracing.exporter`` in ``platform.yaml`` (``postgres``, ``otlp`` or ``none``)
and resolved by ``switchboard.tracing.config`` so both processes make the same
decision. ``otlp`` sends the same spans to an OTLP/HTTP collector such as
Jaeger; the PostgreSQL exporter stays the default and is never switched
implicitly.

``force_flush_async`` is the response-path boundary: the API awaits it with a
2 second cap before returning, and the MCP server awaits it at the end of every
tool call, so a trace read right after the response is complete. A false
result means the trace is incomplete and must be marked so by the caller
(T1.13 reads the run's spans).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from switchboard.tracing.config import (
    DEFAULT_EXPORTER,
    OTLP_ENDPOINT_VARIABLE,
    SUPPORTED_EXPORTERS,
)
from switchboard.tracing.pg_exporter import PostgresSpanExporter

logger = logging.getLogger(__name__)

FLUSH_TIMEOUT_S = 2.0
OTLP_TRACES_PATH = "/v1/traces"


@dataclass
class TracingHandle:
    """The configured provider plus the flush and shutdown calls a process needs."""

    provider: TracerProvider
    exporter: str = DEFAULT_EXPORTER

    def force_flush(self, timeout_s: float = FLUSH_TIMEOUT_S) -> bool:
        return force_flush(timeout_s, provider=self.provider)

    async def force_flush_async(self, timeout_s: float = FLUSH_TIMEOUT_S) -> bool:
        return await force_flush_async(timeout_s, provider=self.provider)

    def shutdown(self) -> None:
        self.provider.shutdown()


_HANDLE: TracingHandle | None = None


def otlp_traces_url(endpoint: str) -> str:
    """Return the full traces URL for a base or traces OTLP endpoint.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is a base URL by convention, so
    ``http://jaeger:4318`` becomes ``http://jaeger:4318/v1/traces``. A value
    that already names the traces path is left alone instead of getting the
    suffix twice.
    """
    value = endpoint.strip()
    if not value:
        raise ValueError("an OTLP endpoint is required")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"OTLP endpoint {endpoint!r} must be an http or https URL")
    path = parsed.path.rstrip("/")
    if not path.endswith(OTLP_TRACES_PATH):
        path = f"{path}{OTLP_TRACES_PATH}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def create_span_exporter(
    exporter: str = DEFAULT_EXPORTER,
    *,
    dsn: str = "",
    otlp_endpoint: str | None = None,
) -> SpanExporter | None:
    """Build the span exporter for one selection, or ``None`` when tracing is off.

    A missing database URL keeps the Phase 1 behaviour: tracing is off rather
    than a startup failure, because some CLI paths never touch the database.
    OTLP is different: selecting it without a destination is a configuration
    error, since the process would otherwise drop every span.
    """
    if exporter not in SUPPORTED_EXPORTERS:
        raise ValueError(f"unknown tracing exporter {exporter!r}")
    if exporter == "none":
        return None
    if exporter == "postgres":
        return PostgresSpanExporter(dsn) if dsn else None
    if not otlp_endpoint:
        raise ValueError(f"tracing.exporter is 'otlp' but {OTLP_ENDPOINT_VARIABLE} is not set")
    return OTLPSpanExporter(endpoint=otlp_traces_url(otlp_endpoint))


def configure_tracing(
    *,
    service_name: str,
    dsn: str = "",
    exporter: str = DEFAULT_EXPORTER,
    otlp_endpoint: str | None = None,
) -> TracingHandle | None:
    """Install the selected batch exporter once per process.

    Returns ``None`` when tracing is off or when the postgres exporter has no
    DSN, which leaves the OTel API a no-op. A second call returns the current
    handle so a module level call and a lifespan call cannot install two
    providers.
    """
    global _HANDLE
    if _HANDLE is not None:
        return _HANDLE
    span_exporter = create_span_exporter(exporter, dsn=dsn, otlp_endpoint=otlp_endpoint)
    if span_exporter is None:
        return None

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        provider = existing
    else:
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
        )
        provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(provider)
    _HANDLE = TracingHandle(provider=provider, exporter=exporter)
    return _HANDLE


def current_handle() -> TracingHandle | None:
    return _HANDLE


def force_flush(timeout_s: float = FLUSH_TIMEOUT_S, *, provider: object | None = None) -> bool:
    """Flush the batch processor synchronously; False means the trace is partial."""
    target = provider if provider is not None else trace.get_tracer_provider()
    flush = getattr(target, "force_flush", None)
    if flush is None:
        return True
    try:
        return bool(flush(timeout_millis=int(timeout_s * 1000)))
    except Exception as exc:
        logger.warning("trace flush failed: %s", type(exc).__name__)
        return False


async def force_flush_async(
    timeout_s: float = FLUSH_TIMEOUT_S, *, provider: object | None = None
) -> bool:
    """Run ``force_flush`` off the event loop with a hard timeout.

    The SDK's ``force_flush`` blocks until every batch is exported; running it in
    a worker thread bounds the API and tool-call response path instead of
    trusting the timeout argument the batch processor ignores.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(force_flush, timeout_s, provider=provider),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning("trace flush timed out after %.1f s", timeout_s)
        return False
