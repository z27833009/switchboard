"""Fallback decorator: retry budget, non-retryable fast fail, visible usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar, cast

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from switchboard.adapters.providers.factory import create_provider
from switchboard.adapters.providers.fallback import FallbackProvider
from switchboard.core.config import PriceEntry, PricesConfig, ProviderConfig
from switchboard.core.errors import ProviderRequestError, ProviderTransientError
from switchboard.core.models import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderCapabilities,
    Usage,
)
from switchboard.registry import PROVIDERS, Registry

CAPABILITIES = ProviderCapabilities(
    tools=True,
    json_schema=False,
    streaming=True,
    reasoning=False,
    context_window=8192,
)


def request() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="hello")])


def response(model: str, provider: str) -> CompletionResponse:
    return CompletionResponse(
        text="ok",
        usage=Usage(input_tokens=10, output_tokens=5),
        model=model,
        provider=provider,
        latency_ms=1,
    )


class ScriptedProvider:
    def __init__(self, name: str, outcomes: list[Any]) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return CAPABILITIES

    async def complete(self, completion: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return cast(CompletionResponse, outcome)


def build(
    primary: ScriptedProvider, fallback: ScriptedProvider
) -> tuple[FallbackProvider, list[float], InMemorySpanExporter]:
    slept: list[float] = []
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)

    provider = FallbackProvider(
        primary=primary,
        fallback=fallback,
        retry_attempts=2,
        backoff_s=(1.0, 3.0),
        sleeper=sleeper,
        tracer=tracer_provider.get_tracer("test"),
    )
    return provider, slept, exporter


async def test_transient_errors_exhaust_the_retries_then_use_the_fallback() -> None:
    primary = ScriptedProvider(
        "primary",
        [
            ProviderTransientError("timeout"),
            ProviderTransientError("503"),
            ProviderTransientError("connection reset"),
        ],
    )
    fallback = ScriptedProvider("backup", [response("backup-model", "backup")])
    provider, slept, exporter = build(primary, fallback)

    result = await provider.complete(request())

    assert result.provider == "backup"
    assert primary.calls == 3
    assert fallback.calls == 1
    assert slept == [1.0, 3.0]
    assert provider.primary_calls == 0
    assert provider.fallback_calls == 1
    assert provider.retries == 2
    assert provider.last_primary_error == "ProviderTransientError"

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = spans[0].attributes or {}
    assert attributes["switchboard.provider.fallback_used"] is True
    assert attributes["switchboard.provider.attempts"] == 3
    assert attributes["switchboard.provider.retries"] == 2
    assert attributes["switchboard.provider.fallback_reason"] == "ProviderTransientError"


async def test_a_non_retryable_request_error_fails_fast() -> None:
    primary = ScriptedProvider("primary", [ProviderRequestError("401 invalid key")])
    fallback = ScriptedProvider("backup", [response("backup-model", "backup")])
    provider, slept, exporter = build(primary, fallback)

    with pytest.raises(ProviderRequestError, match="401"):
        await provider.complete(request())

    assert primary.calls == 1
    assert fallback.calls == 0
    assert slept == []
    attributes = exporter.get_finished_spans()[0].attributes or {}
    assert attributes["switchboard.provider.fallback_used"] is False
    assert attributes["switchboard.provider.error_kind"] == "ProviderRequestError"


async def test_a_retry_can_succeed_on_the_primary_without_the_fallback() -> None:
    primary = ScriptedProvider(
        "primary",
        [ProviderTransientError("timeout"), response("primary-model", "primary")],
    )
    fallback = ScriptedProvider("backup", [response("backup-model", "backup")])
    provider, slept, exporter = build(primary, fallback)

    result = await provider.complete(request())

    assert result.provider == "primary"
    assert primary.calls == 2
    assert fallback.calls == 0
    assert slept == [1.0]
    assert provider.retries == 1
    attributes = exporter.get_finished_spans()[0].attributes or {}
    assert attributes["switchboard.provider.fallback_used"] is False
    assert attributes["switchboard.provider.attempts"] == 2


class FakeAdapter:
    scripts: ClassVar[dict[str, list[Any]]] = {}
    built: ClassVar[dict[str, ScriptedProvider]] = {}

    @classmethod
    def from_config(
        cls, name: str, config: ProviderConfig, price: PriceEntry | None = None
    ) -> ScriptedProvider:
        provider = ScriptedProvider(name, cls.scripts[name])
        cls.built[name] = provider
        return provider


@dataclass
class Entry:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


def prices() -> PricesConfig:
    return PricesConfig(
        verified_on=date(2026, 11, 12),
        entries=[
            PriceEntry(
                provider="paid",
                model="paid-model",
                input_per_million=1.0,
                cached_input_per_million=0.1,
                output_per_million=2.0,
                source="https://example.invalid/prices",
            ),
            PriceEntry(
                provider="local",
                model="local-model",
                input_per_million=0.0,
                cached_input_per_million=0.0,
                output_per_million=0.0,
            ),
        ],
    )


async def test_the_factory_wraps_a_configured_fallback() -> None:
    FakeAdapter.scripts = {
        "paid": [ProviderTransientError("503"), ProviderTransientError("503")],
        "local": [response("local-model", "local")],
    }
    FakeAdapter.built = {}
    registry = Registry([Entry("fake", PROVIDERS, FakeAdapter)])
    paid = ProviderConfig(
        adapter="fake",
        model="paid-model",
        capabilities=CAPABILITIES,
        fallback_provider="local",
        retry_attempts=1,
        retry_backoff_s=[0.0],
    )
    local = ProviderConfig(adapter="fake", model="local-model", capabilities=CAPABILITIES)
    slept: list[float] = []

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)

    provider = create_provider(
        registry,
        "paid",
        paid,
        prices().lookup("paid", "paid-model"),
        providers={"paid": paid, "local": local},
        prices=prices(),
        sleeper=sleeper,
    )
    assert isinstance(provider, FallbackProvider)
    result = await provider.complete(request())
    assert result.provider == "local"
    assert FakeAdapter.built["paid"].calls == 2
    assert FakeAdapter.built["local"].calls == 1
    assert slept == [0.0]


async def test_evaluation_can_disable_the_fallback() -> None:
    FakeAdapter.scripts = {"paid": [response("paid-model", "paid")]}
    FakeAdapter.built = {}
    registry = Registry([Entry("fake", PROVIDERS, FakeAdapter)])
    paid = ProviderConfig(
        adapter="fake",
        model="paid-model",
        capabilities=CAPABILITIES,
        fallback_provider="local",
    )
    local = ProviderConfig(adapter="fake", model="local-model", capabilities=CAPABILITIES)
    provider = create_provider(
        registry,
        "paid",
        paid,
        prices().lookup("paid", "paid-model"),
        providers={"paid": paid, "local": local},
        prices=prices(),
        use_fallback=False,
    )
    assert not isinstance(provider, FallbackProvider)
    result = await provider.complete(request())
    assert result.provider == "paid"
