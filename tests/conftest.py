"""Fixtures for the API tests, built from ``tests/fakes.py``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider

from switchboard.api.step_events import StepEventPublisher
from switchboard.core.config import TenantsConfig
from tests.fakes import (
    AppHarness,
    FakeOrchestrator,
    MemoryRunStore,
    build_harness,
    make_provider,
    tenants_config,
)


@pytest.fixture
def tenants() -> TenantsConfig:
    return tenants_config()


@pytest.fixture
def store() -> MemoryRunStore:
    return MemoryRunStore()


@pytest.fixture
def publisher() -> StepEventPublisher:
    return StepEventPublisher()


@pytest.fixture
def tracer_provider(
    publisher: StepEventPublisher, store: MemoryRunStore
) -> Iterator[TracerProvider]:
    provider = make_provider(publisher, store)
    try:
        yield provider
    finally:
        provider.shutdown()


@pytest.fixture
def orchestrator(tracer_provider: TracerProvider) -> FakeOrchestrator:
    return FakeOrchestrator(tracer=tracer_provider.get_tracer("switchboard.test.fake"))


@pytest.fixture
async def harness(
    tenants: TenantsConfig,
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    tracer_provider: TracerProvider,
    orchestrator: FakeOrchestrator,
) -> AsyncIterator[AppHarness]:
    built = build_harness(
        tenants=tenants,
        store=store,
        publisher=publisher,
        provider=tracer_provider,
        orchestrator=orchestrator,
    )
    try:
        yield built
    finally:
        await built.client.aclose()
