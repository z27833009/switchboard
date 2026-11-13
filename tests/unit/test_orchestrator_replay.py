"""Replay one request on a named orchestrator (T3.07).

The trace page sends ``orchestrator`` with the recorded query. These tests use
two fake orchestrators in one ``AppDeps``: the platform default and a named
replay target. They prove the request selects the right one, that the run row
records the name that actually ran, that an unknown name is a 400 before a run
row exists, and that ``GET /settings`` exposes the names the process built.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from tests.fakes import (
    AppHarness,
    FakeOrchestrator,
    MemoryRunStore,
    build_harness,
)

from switchboard.api.settings import build_settings_snapshots
from switchboard.api.step_events import StepEventPublisher
from switchboard.config import load_settings
from switchboard.core.config import TenantsConfig
from switchboard.registry import Registry

REPO = Path(__file__).resolve().parents[2]
CONFIG_ENV = {
    "LMSTUDIO_BASE_URL": "http://test.invalid/v1",
    "LMSTUDIO_MODEL": "qwen3-8b",
    "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
    "MCP_OLIST_URL": "http://test.invalid:8001",
}
QUERY = "How many orders were placed in March 2018?"


@pytest.fixture(scope="module")
def settings_snapshot() -> dict[str, dict[str, Any]]:
    settings = load_settings(REPO / "config", CONFIG_ENV)
    return build_settings_snapshots(
        settings,
        Registry(),
        available_orchestrators={"lmstudio": ["agent_loop", "langgraph"]},
    )


@pytest.fixture
def replay_orchestrator(tracer_provider: TracerProvider) -> FakeOrchestrator:
    return FakeOrchestrator(
        tracer=tracer_provider.get_tracer("switchboard.test.replay"),
        name="agent_loop",
        answer_text="Replayed by the agent loop.",
    )


@pytest.fixture
async def replay_harness(
    tenants: TenantsConfig,
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    tracer_provider: TracerProvider,
    orchestrator: FakeOrchestrator,
    replay_orchestrator: FakeOrchestrator,
    settings_snapshot: dict[str, dict[str, Any]],
) -> AsyncIterator[AppHarness]:
    built = build_harness(
        tenants=tenants,
        store=store,
        publisher=publisher,
        provider=tracer_provider,
        orchestrator=orchestrator,
        replay_orchestrators={("lmstudio", "agent_loop"): replay_orchestrator},
        settings_snapshot=settings_snapshot,
    )
    try:
        yield built
    finally:
        await built.client.aclose()


async def test_default_chat_uses_the_platform_orchestrator(
    replay_harness: AppHarness, orchestrator: FakeOrchestrator
) -> None:
    response = await replay_harness.client.post(
        "/chat", json={"query": QUERY, "tenant_id": "default"}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    row = replay_harness.store.runs[body["run_id"]]
    assert row["orchestrator"] == "fake"
    assert orchestrator.queries == [QUERY]
    assert replay_harness.deps.available_orchestrators("lmstudio") == ["agent_loop", "fake"]


async def test_replay_runs_on_the_named_orchestrator(
    replay_harness: AppHarness,
    orchestrator: FakeOrchestrator,
    replay_orchestrator: FakeOrchestrator,
) -> None:
    response = await replay_harness.client.post(
        "/chat",
        json={"query": QUERY, "tenant_id": "default", "orchestrator": "agent_loop"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    row = replay_harness.store.runs[body["run_id"]]
    assert row["orchestrator"] == "agent_loop"
    assert row["query"] == QUERY
    assert body["answer"] == "Replayed by the agent loop."
    # Only the replay target ran; the recorded request was not sent to the
    # platform default.
    assert replay_orchestrator.queries == [QUERY]
    assert orchestrator.queries == []


async def test_replay_streams_and_records_the_named_orchestrator(
    replay_harness: AppHarness,
    orchestrator: FakeOrchestrator,
    replay_orchestrator: FakeOrchestrator,
) -> None:
    response = await replay_harness.client.post(
        "/chat/stream",
        json={"query": QUERY, "tenant_id": "default", "orchestrator": "agent_loop"},
    )
    assert response.status_code == 200, response.text
    assert "run.finished" in response.text
    assert len(replay_harness.store.runs) == 1
    row = next(iter(replay_harness.store.runs.values()))
    assert row["orchestrator"] == "agent_loop"
    assert replay_orchestrator.queries == [QUERY]
    assert orchestrator.queries == []


async def test_an_unknown_orchestrator_is_a_400_before_any_run(
    replay_harness: AppHarness, orchestrator: FakeOrchestrator
) -> None:
    for path in ("/chat", "/chat/stream"):
        response = await replay_harness.client.post(
            path,
            json={"query": QUERY, "tenant_id": "default", "orchestrator": "nope"},
        )
        assert response.status_code == 400, response.text
        assert "no orchestrator named 'nope'" in response.json()["detail"]

    assert replay_harness.store.runs == {}
    assert orchestrator.queries == []

    empty = await replay_harness.client.post(
        "/chat",
        json={"query": QUERY, "tenant_id": "default", "orchestrator": ""},
    )
    assert empty.status_code == 422


async def test_settings_lists_the_built_orchestrators(replay_harness: AppHarness) -> None:
    response = await replay_harness.client.get("/settings", params={"tenant_id": "default"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available_orchestrators"] == ["agent_loop", "langgraph"]
    # The default path is still the one platform.yaml names.
    assert body["platform"]["orchestrator"] == "langgraph"
