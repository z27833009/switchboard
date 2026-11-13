"""Tests for the read-only ``GET /settings`` snapshot (design.md section 14).

The snapshot is built from a self-contained configuration written into a temp
directory, so the test does not depend on the committed demo values. A fake
agent registry keeps the assertions about agent specs independent of the
installed entry points.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from tests.fakes import (
    AppHarness,
    FakeOrchestrator,
    MemoryRunStore,
    build_harness,
    make_provider,
)

from switchboard.api.settings import (
    SECRET_KEYS,
    build_settings_snapshots,
    load_agent_specs,
    snapshot_contains_secret,
)
from switchboard.api.step_events import StepEventPublisher
from switchboard.config import load_settings
from switchboard.core.config import Settings
from switchboard.core.errors import ConfigError
from switchboard.core.models import AgentSpec
from switchboard.registry import AGENTS, Registry

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "mcp_servers" / "olist" / "manifest.yaml"

PROVIDER_KEY = "test-provider-key-do-not-leak"
BASE_URL = "http://secret-internal-host:1234/v1"
DATABASE_URL = "postgresql://switchboard:db-password@localhost:5432/switchboard"

PLATFORM = f"""
orchestrator: langgraph
tool_sources:
  - name: olist
    adapter: mcp
    url: {BASE_URL}/mcp
    manifest: {MANIFEST.as_posix()}
retriever: pgvector
cache: pg_semantic
embedder: lmstudio
context_policy: clear_oldest_tool_results
limits:
  run_timeout_s: 60
  max_tool_steps: 4
  max_loop_steps: 8
  max_plan_size: 3
  fallback_plan: ["docs", "data"]
  model_view_rows: 20
  context_fill: 0.7
  history_turns: 3
"""

PROVIDERS = f"""
providers:
  lmstudio:
    adapter: openai_compatible
    base_url: {BASE_URL}
    api_key: {PROVIDER_KEY}
    model: qwen3-8b
    capabilities:
      tools: true
      json_schema: false
      streaming: true
      reasoning: false
      context_window: 8192
embedders:
  lmstudio:
    adapter: openai_compatible
    base_url: {BASE_URL}
    api_key: {PROVIDER_KEY}
    model: bge-m3
    dimension: 1024
"""

TENANTS = """
tenants:
  default:
    provider: lmstudio
    agents:
      allow: ["direct", "docs"]
    tools:
      allow: ["orders_summary@1", "orders_late@1"]
      ask: []
    seller_ids: null
    retrieval:
      search: hybrid
      rewrite: true
    cache:
      enabled: false
      threshold: 0.94
      ttl_days: 7
  seller_a:
    provider: lmstudio
    agents:
      allow: ["docs"]
    tools:
      allow: ["orders_summary@1"]
      ask: []
    seller_ids: ["seller-1"]
    retrieval:
      search: vector
      rewrite: false
    cache:
      enabled: true
      threshold: 0.9
      ttl_days: 3
"""

PRICES = """
verified_on: "2026-11-12"
entries:
  - provider: lmstudio
    model: null
    input_per_million: 0.0
    cached_input_per_million: 0.0
    output_per_million: 0.0
    note: no API cost
"""


@dataclass
class Entry:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class DirectAgent:
    spec = AgentSpec(
        name="direct", description="small talk and capability questions", cacheable=True
    )


class DocsAgent:
    spec = AgentSpec(
        name="docs",
        description="policy documents",
        cacheable=True,
        needs=["retriever"],
    )


class DataAgent:
    spec = AgentSpec(
        name="data",
        description="records and aggregates",
        cacheable=False,
        needs=["tools"],
    )


def write_config(directory: Path) -> Path:
    (directory / "platform.yaml").write_text(textwrap.dedent(PLATFORM), encoding="utf-8")
    (directory / "providers.yaml").write_text(textwrap.dedent(PROVIDERS), encoding="utf-8")
    (directory / "tenants.yaml").write_text(textwrap.dedent(TENANTS), encoding="utf-8")
    (directory / "prices.yaml").write_text(textwrap.dedent(PRICES), encoding="utf-8")
    return directory


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(write_config(tmp_path), {})


@pytest.fixture
def registry() -> Registry:
    return Registry(
        [
            Entry("direct", AGENTS, DirectAgent),
            Entry("docs", AGENTS, DocsAgent),
            Entry("data", AGENTS, DataAgent),
        ]
    )


@pytest.fixture
def snapshot(settings: Settings, registry: Registry) -> dict[str, dict[str, Any]]:
    return build_settings_snapshots(settings, registry)


@pytest.fixture
def tracer(store: MemoryRunStore, publisher: StepEventPublisher) -> Iterator[TracerProvider]:
    provider = make_provider(publisher, store)
    try:
        yield provider
    finally:
        provider.shutdown()


@pytest.fixture
async def settings_harness(
    settings: Settings,
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    tracer: TracerProvider,
    snapshot: dict[str, dict[str, Any]],
) -> AsyncIterator[AppHarness]:
    built = build_harness(
        tenants=settings.tenants,
        store=store,
        publisher=publisher,
        provider=tracer,
        orchestrator=FakeOrchestrator(tracer=tracer.get_tracer("switchboard.test.settings")),
        settings_snapshot=snapshot,
    )
    try:
        yield built
    finally:
        await built.client.aclose()


def test_agent_specs_come_from_the_registry(registry: Registry) -> None:
    specs = load_agent_specs(registry)
    assert [spec.name for spec in specs] == ["data", "direct", "docs"]
    assert [spec.needs for spec in specs] == [["tools"], [], ["retriever"]]


def test_an_agent_without_a_spec_is_a_config_error() -> None:
    class Nameless:
        pass

    with pytest.raises(ConfigError, match="AgentSpec"):
        load_agent_specs(Registry([Entry("nameless", AGENTS, Nameless)]))


async def test_known_tenant_returns_the_expected_shape(
    settings_harness: AppHarness,
) -> None:
    response = await settings_harness.client.get("/settings", params={"tenant_id": "default"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["read_only"] is True
    assert body["platform"] == {
        "orchestrator": "langgraph",
        "retriever": "pgvector",
        "embedder": "lmstudio",
        "cache": "pg_semantic",
        "context_policy": "clear_oldest_tool_results",
        "limits": {
            "run_timeout_s": 60.0,
            "max_tool_steps": 4,
            "max_loop_steps": 8,
            "max_plan_size": 3,
            "fallback_plan": ["docs", "data"],
            "model_view_rows": 20,
            "context_fill": 0.7,
            "history_turns": 3,
        },
    }
    assert body["tenant"] == {
        "tenant_id": "default",
        "provider": "lmstudio",
        "seller_ids": None,
        "agents_allow": ["direct", "docs"],
        "tools_allow": ["orders_summary@1", "orders_late@1"],
        "tools_ask": [],
        "retrieval": {
            "k": 5,
            "min_score": 0.35,
            "rewrite": True,
            "rewrite_mode": "conditional",
            "search": "hybrid",
        },
        "cache": {"enabled": False, "threshold": 0.94, "ttl_days": 7},
    }
    assert body["provider"] == {
        "name": "lmstudio",
        "model": "qwen3-8b",
        "capabilities": {
            "tools": True,
            "json_schema": False,
            "streaming": True,
            "reasoning": False,
            "context_window": 8192,
        },
        "price": {
            "input_per_million": 0.0,
            "cached_input_per_million": 0.0,
            "output_per_million": 0.0,
            "no_api_cost": True,
            "verified_on": "2026-11-12",
        },
    }
    assert body["tenants"] == ["default", "seller_a"]
    # Without an explicit availability map the snapshot names the platform
    # orchestrator and nothing else, so it never claims a target it did not
    # build (T3.07).
    assert body["available_orchestrators"] == ["langgraph"]

    tools = {tool["wire_name"]: tool for tool in body["tools"]}
    assert set(tools) == {
        "describe_schema@1",
        "order_lookup@1",
        "orders_late@1",
        "orders_summary@1",
        "query_readonly_sql@1",
        "top_products@1",
    }
    assert tools["orders_summary@1"]["allowed"] is True
    assert tools["order_lookup@1"]["allowed"] is False
    # The free-form SQL tool ships disabled: it is listed but never allowed.
    assert tools["query_readonly_sql@1"]["allowed"] is False
    assert tools["query_readonly_sql@1"]["risk"] == "read"
    assert tools["query_readonly_sql@1"]["tenant_scoped"] is True
    assert tools["orders_summary@1"]["risk"] == "read"
    assert tools["orders_summary@1"]["tenant_scoped"] is True
    assert tools["describe_schema@1"]["tenant_scoped"] is False
    assert tools["orders_summary@1"]["description"] != ""

    agents = {agent["name"]: agent for agent in body["agents"]}
    assert set(agents) == {"data", "direct", "docs"}
    assert agents["direct"]["allowed"] is True
    assert agents["data"]["allowed"] is False
    assert agents["docs"]["needs"] == ["retriever"]
    assert agents["data"]["cacheable"] is False


async def test_another_tenant_gets_only_its_allowlists(settings_harness: AppHarness) -> None:
    response = await settings_harness.client.get("/settings", params={"tenant_id": "seller_a"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["tenant"]["tenant_id"] == "seller_a"
    assert body["tenant"]["seller_ids"] == ["seller-1"]
    assert body["tenant"]["agents_allow"] == ["docs"]
    assert body["tenant"]["tools_allow"] == ["orders_summary@1"]
    assert body["tenant"]["retrieval"]["search"] == "vector"
    assert body["tenant"]["cache"] == {"enabled": True, "threshold": 0.9, "ttl_days": 3}

    tools = {tool["wire_name"]: tool for tool in body["tools"]}
    assert tools["orders_summary@1"]["allowed"] is True
    assert tools["orders_late@1"]["allowed"] is False
    agents = {agent["name"]: agent for agent in body["agents"]}
    assert agents["docs"]["allowed"] is True
    assert agents["direct"]["allowed"] is False

    # The default tenant's allowlists are not reachable through this response.
    assert "orders_late@1" not in body["tenant"]["tools_allow"]
    assert "direct" not in body["tenant"]["agents_allow"]


async def test_unknown_tenant_is_404(settings_harness: AppHarness) -> None:
    response = await settings_harness.client.get("/settings", params={"tenant_id": "acme"})
    assert response.status_code == 404
    assert "unknown tenant" in response.json()["detail"]
    missing = await settings_harness.client.get("/settings")
    assert missing.status_code == 422


async def test_no_api_key_base_url_or_database_url_is_returned(
    settings_harness: AppHarness,
) -> None:
    for tenant_id in ("default", "seller_a"):
        response = await settings_harness.client.get("/settings", params={"tenant_id": tenant_id})
        assert response.status_code == 200
        text = response.text
        for secret in (PROVIDER_KEY, BASE_URL, DATABASE_URL, "db-password"):
            assert secret not in text
        for key in SECRET_KEYS:
            assert f'"{key}"' not in text
        assert snapshot_contains_secret(response.json()) == []


async def test_missing_snapshot_is_503(
    settings: Settings,
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    tracer: TracerProvider,
) -> None:
    built = build_harness(
        tenants=settings.tenants,
        store=store,
        publisher=publisher,
        provider=tracer,
        orchestrator=FakeOrchestrator(tracer=tracer.get_tracer("switchboard.test.settings")),
    )
    try:
        response = await built.client.get("/settings", params={"tenant_id": "default"})
        assert response.status_code == 503
        assert "not available" in response.json()["detail"]
        # A known tenant still 404s before the snapshot check, so the two
        # outcomes stay distinguishable.
        unknown = await built.client.get("/settings", params={"tenant_id": "acme"})
        assert unknown.status_code == 404
    finally:
        await built.client.aclose()


def test_snapshot_is_json_and_free_of_secrets(snapshot: dict[str, dict[str, Any]]) -> None:
    encoded = json.dumps(snapshot)
    assert PROVIDER_KEY not in encoded
    assert BASE_URL not in encoded
    for payload in snapshot.values():
        assert snapshot_contains_secret(payload) == []
