"""Use a second provider after the primary's retry budget is exhausted.

Design.md sections 6.6 and 8.4. The decorator implements ``ModelProvider``, so
agents, the orchestrator and the eval runner keep calling one provider and never
learn that a fallback exists. Only a transient transport failure (timeout,
connection error, 429 or 5xx, all mapped to ``ProviderTransientError``) is
retried and then handed to the fallback. A non-retryable request error such as
an auth failure or a malformed request fails fast: retrying it or sending it to
another provider would hide a configuration bug and could double-bill.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from switchboard.core.errors import ProviderTransientError, SwitchboardError
from switchboard.core.models import (
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
)
from switchboard.core.ports import ModelProvider

Sleeper = Callable[[float], Awaitable[None]]


class FallbackProvider:
    """Primary provider with retries, then a fallback provider.

    ``primary_calls``, ``fallback_calls``, ``retries`` and ``last_primary_error``
    are cumulative counters for tests and for a metrics reader. The same facts
    are on the ``provider.fallback`` span of every call, which is what an
    operator sees in the trace.
    """

    def __init__(
        self,
        *,
        primary: ModelProvider,
        fallback: ModelProvider,
        retry_attempts: int = 2,
        backoff_s: Sequence[float] = (1.0, 3.0),
        sleeper: Sleeper | None = None,
        tracer: trace.Tracer | None = None,
    ) -> None:
        if retry_attempts < 0:
            raise ValueError("retry_attempts must be zero or positive")
        if len(backoff_s) < retry_attempts:
            raise ValueError("backoff_s needs a value for every retry attempt")
        self.name = primary.name
        self._primary = primary
        self._fallback = fallback
        self._retry_attempts = retry_attempts
        self._backoff_s = tuple(backoff_s)
        self._sleep = sleeper or asyncio.sleep
        self._tracer = tracer or trace.get_tracer("switchboard.provider.fallback")
        self.primary_calls = 0
        self.fallback_calls = 0
        self.retries = 0
        self.last_primary_error: str | None = None

    def capabilities(self) -> ProviderCapabilities:
        return self._primary.capabilities()

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        attempts = 0
        last_error: ProviderTransientError | None = None
        with self._tracer.start_as_current_span(
            f"provider.fallback {self._primary.name}->{self._fallback.name}",
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("switchboard.provider.primary", self._primary.name)
            span.set_attribute("switchboard.provider.fallback", self._fallback.name)
            for attempt in range(self._retry_attempts + 1):
                attempts += 1
                try:
                    response = await self._primary.complete(request)
                except ProviderTransientError as exc:
                    last_error = exc
                    self.last_primary_error = type(exc).__name__
                    if attempt >= self._retry_attempts:
                        break
                    span.add_event("provider.retry", {"attempt": attempt + 1})
                    self.retries += 1
                    await self._sleep(self._backoff_s[attempt])
                except SwitchboardError as exc:
                    # Not a transport failure: no retry and no fallback.
                    span.set_attribute("switchboard.provider.fallback_used", False)
                    span.set_attribute("switchboard.provider.attempts", attempts)
                    span.set_attribute("switchboard.provider.error_kind", type(exc).__name__)
                    raise
                else:
                    self.primary_calls += 1
                    self._record(span, used=False, attempts=attempts)
                    return response

            self.fallback_calls += 1
            self._record(
                span,
                used=True,
                attempts=attempts,
                error=type(last_error).__name__ if last_error is not None else "unknown",
            )
            return await self._fallback.complete(request)

    @staticmethod
    def _record(
        span: trace.Span,
        *,
        used: bool,
        attempts: int,
        error: str | None = None,
    ) -> None:
        span.set_attribute("switchboard.provider.fallback_used", used)
        span.set_attribute("switchboard.provider.attempts", attempts)
        span.set_attribute("switchboard.provider.retries", max(0, attempts - 1))
        if error is not None:
            span.set_attribute("switchboard.provider.fallback_reason", error)


__all__ = ["FallbackProvider", "Sleeper"]
