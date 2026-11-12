"""Tracer provider setup, shutdown and flush (design.md sections 12.2, ADR 0004).

One process configures one provider. The API, the eval CLI and the MCP server
are separate processes and each builds its own exporter writing to the same
``spans`` table. ``force_flush_async`` is the response-path boundary: the API
awaits it with a 2 second cap before returning, and the MCP server awaits it at
the end of every tool call, so a trace read right after the response is
complete. A false result means the trace is incomplete and must be marked so by
the caller (T1.13 reads the run's spans).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from switchboard.tracing.pg_exporter import PostgresSpanExporter

logger = logging.getLogger(__name__)

FLUSH_TIMEOUT_S = 2.0
SUPPORTED_EXPORTERS = ("none", "postgres")


@dataclass
class TracingHandle:
    """The configured provider plus the flush and shutdown calls a process needs."""

    provider: TracerProvider

    def force_flush(self, timeout_s: float = FLUSH_TIMEOUT_S) -> bool:
        return force_flush(timeout_s, provider=self.provider)

    async def force_flush_async(self, timeout_s: float = FLUSH_TIMEOUT_S) -> bool:
        return await force_flush_async(timeout_s, provider=self.provider)

    def shutdown(self) -> None:
        self.provider.shutdown()


_HANDLE: TracingHandle | None = None


def configure_tracing(
    *,
    service_name: str,
    dsn: str,
    exporter: str = "postgres",
) -> TracingHandle | None:
    """Install the batch PostgreSQL exporter once per process.

    Returns ``None`` when tracing is off or no DSN is available, which leaves
    the OTel API a no-op. A second call returns the current handle so a module
    level call and a lifespan call cannot install two providers.
    """
    global _HANDLE
    if _HANDLE is not None:
        return _HANDLE
    if exporter not in SUPPORTED_EXPORTERS:
        raise ValueError(f"unknown tracing exporter {exporter!r}")
    if exporter == "none" or not dsn:
        return None

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        provider = existing
    else:
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
        )
        provider.add_span_processor(BatchSpanProcessor(PostgresSpanExporter(dsn)))
        trace.set_tracer_provider(provider)
    _HANDLE = TracingHandle(provider=provider)
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
