"""Price math and the per-call cost attribute on the chat span (design 12.4).

The price row is synthetic: these tests pin the arithmetic, not today's provider
prices, so a price page update never changes a test expectation. The committed
``config/prices.yaml`` is checked in ``test_config.py`` for its source and date.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from switchboard.adapters.providers.openai_compat import OpenAICompatibleProvider
from switchboard.core.config import PriceEntry, PricesConfig
from switchboard.core.models import (
    CompletionRequest,
    Message,
    ProviderCapabilities,
    Usage,
)
from switchboard.core.pricing import compute_cost, cost_usd, lookup_price

CAPABILITIES = ProviderCapabilities(
    tools=True, json_schema=True, streaming=False, reasoning=False, context_window=8192
)

PAID = PriceEntry(
    provider="paid",
    model="paid-model",
    input_per_million=0.30,
    cached_input_per_million=0.006,
    output_per_million=1.20,
    source="https://example.invalid/prices",
)

LOCAL = PriceEntry(
    provider="local",
    model=None,
    input_per_million=0.0,
    cached_input_per_million=0.0,
    output_per_million=0.0,
)


def prices() -> PricesConfig:
    return PricesConfig(verified_on=date(2026, 11, 12), entries=[PAID, LOCAL])


def test_no_tokens_cost_nothing() -> None:
    assert compute_cost(Usage(input_tokens=0, output_tokens=0), PAID) == 0.0


def test_output_tokens_use_the_output_rate() -> None:
    usage = Usage(input_tokens=0, output_tokens=1_000_000)
    assert compute_cost(usage, PAID) == pytest.approx(1.20)


def test_cached_input_tokens_use_the_cached_rate() -> None:
    usage = Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0)
    assert compute_cost(usage, PAID) == pytest.approx(0.006)


def test_mixed_cached_and_uncached_input() -> None:
    usage = Usage(input_tokens=1_000_000, cached_input_tokens=250_000, output_tokens=500_000)
    # 750k uncached at 0.30, 250k cached at 0.006, 500k output at 1.20.
    expected = 750_000 * 0.30 / 1_000_000 + 250_000 * 0.006 / 1_000_000
    expected += 500_000 * 1.20 / 1_000_000
    assert compute_cost(usage, PAID) == pytest.approx(expected)
    assert compute_cost(usage, PAID) == pytest.approx(0.8265)


def test_a_local_zero_price_costs_zero() -> None:
    usage = Usage(input_tokens=123_456, cached_input_tokens=1_000, output_tokens=7_890)
    assert compute_cost(usage, LOCAL) == 0.0


def test_more_cached_than_input_is_clamped_not_negative() -> None:
    usage = Usage(input_tokens=100, cached_input_tokens=150, output_tokens=0)
    # 100 cached at 0.006, no uncached input; never a negative amount.
    assert compute_cost(usage, PAID) == pytest.approx(100 * 0.006 / 1_000_000)


def test_cost_keeps_sub_cent_precision() -> None:
    # One cached token at 0.006 per million is 6e-9; it must not round to zero.
    usage = Usage(input_tokens=1, cached_input_tokens=1, output_tokens=0)
    assert compute_cost(usage, PAID) == pytest.approx(6e-09)
    plain = Usage(input_tokens=1, output_tokens=0)
    assert compute_cost(plain, PAID) == 0.0000003


def test_a_specific_model_row_wins_over_the_provider_row() -> None:
    config = PricesConfig(verified_on=date(2026, 11, 12), entries=[LOCAL, PAID])
    assert config.lookup("paid", "paid-model") is PAID
    assert config.lookup("paid", "another-model") is None
    assert config.lookup("local", "any-model") is LOCAL
    assert config.lookup("mock", "anything") is None


def test_lookup_price_without_a_table_is_none() -> None:
    assert lookup_price(None, "paid", "paid-model") is None
    assert cost_usd(Usage(input_tokens=10, output_tokens=5), None, "paid") is None


def test_cost_usd_is_absent_without_a_price_entry() -> None:
    usage = Usage(input_tokens=1_000, output_tokens=1_000)
    assert cost_usd(usage, prices(), "mock", "qwen3-8b") is None
    assert cost_usd(usage, prices(), "paid", "other-model") is None
    assert cost_usd(usage, prices(), "local") == 0.0


class FakeCompletions:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response


def completion(*, prompt: int = 1_000, cached: int = 200, output: int = 500) -> Any:
    message = SimpleNamespace(content="done", tool_calls=None, model_extra={})
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=output,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=usage, model="paid-model"
    )


def build_provider(*, price: PriceEntry | None) -> tuple[OpenAICompatibleProvider, Any]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    fake = FakeCompletions(completion())
    adapter = OpenAICompatibleProvider(
        name="paid",
        model="paid-model",
        base_url="https://example.invalid/v1",
        api_key="test",
        capabilities=CAPABILITIES,
        price=price,
        client=SimpleNamespace(chat=SimpleNamespace(completions=fake)),
        tracer=provider.get_tracer("test"),
    )
    return adapter, exporter


def chat_span_attributes(exporter: InMemorySpanExporter) -> dict[str, Any]:
    spans = [span for span in exporter.get_finished_spans() if span.name == "chat paid-model"]
    assert len(spans) == 1
    attributes = dict(spans[0].attributes or {})
    assert attributes["switchboard.usage.cached_input_tokens"] == 200
    return attributes


async def test_the_chat_span_records_cost_from_usage() -> None:
    adapter, exporter = build_provider(price=PAID)
    request = CompletionRequest(messages=[Message(role="user", content="hello")])

    response = await adapter.complete(request)

    assert response.usage == Usage(input_tokens=1_000, cached_input_tokens=200, output_tokens=500)
    attributes = chat_span_attributes(exporter)
    expected = (800 * 0.30 + 200 * 0.006 + 500 * 1.20) / 1_000_000
    assert attributes["switchboard.cost_usd"] == pytest.approx(expected)


async def test_the_chat_span_has_no_cost_without_a_price_entry() -> None:
    adapter, exporter = build_provider(price=None)
    request = CompletionRequest(messages=[Message(role="user", content="hello")])

    await adapter.complete(request)

    attributes = chat_span_attributes(exporter)
    assert "switchboard.cost_usd" not in attributes


async def test_a_local_price_records_zero_but_present() -> None:
    adapter, exporter = build_provider(price=LOCAL)
    request = CompletionRequest(messages=[Message(role="user", content="hello")])

    await adapter.complete(request)

    assert chat_span_attributes(exporter)["switchboard.cost_usd"] == 0.0
