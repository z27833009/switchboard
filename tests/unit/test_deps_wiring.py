"""Production wiring resolves every component through the registry (ADR 0011)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import asyncpg
import pytest
from tests.fakes import MemoryRunStore, tenants_config

from switchboard.api.deps import AppDeps, build_orchestrators, context_policy_for
from switchboard.api.step_events import StepEventPublisher
from switchboard.core.config import (
    EmbedderConfig,
    Limits,
    PlatformConfig,
    PriceEntry,
    PricesConfig,
    ProviderConfig,
    ProvidersConfig,
    Settings,
    TenantAgentSettings,
    TenantConfig,
    TenantsConfig,
    TenantToolSettings,
    ToolSourceConfig,
)
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ConfigError
from switchboard.registry import (
    EMBEDDERS,
    ORCHESTRATORS,
    PROVIDERS,
    RETRIEVERS,
    TOOL_SOURCES,
    Registry,
)


@dataclass
class Entry:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class FakeProvider:
    def __init__(self, *, name: str, model: str, price: PriceEntry | None = None) -> None:
        self.name = name
        self.model = model
        self.price = price

    @classmethod
    def from_config(
        cls, name: str, config: ProviderConfig, price: PriceEntry | None = None
    ) -> FakeProvider:
        return cls(name=name, model=config.model or "", price=price)


class FakeRetriever:
    def __init__(self, *, pool: Any, embedder: Any) -> None:
        self.pool = pool
        self.embedder = embedder
        self.name = "fake"


class FakeToolSource:
    def __init__(self, *, name: str) -> None:
        self.name = name

    @classmethod
    def from_config(cls, config: ToolSourceConfig) -> FakeToolSource:
        return cls(name=config.name)


class FakeOrchestratorClass:
    name = "fake"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def settings_for(retriever: str = "fake") -> Settings:
    return Settings(
        platform=PlatformConfig(
            orchestrator="fake",
            tool_sources=[ToolSourceConfig(name="tools", adapter="fake")],
            retriever=retriever,
            cache="none",
            embedder="fake",
            context_policy="clear_oldest_tool_results",
            limits=Limits(run_timeout_s=12),
        ),
        providers=ProvidersConfig(
            providers={"fake": ProviderConfig(adapter="fake", model="fake-model")},
            embedders={"fake": EmbedderConfig(adapter="mock", dimension=8)},
        ),
        tenants=TenantsConfig(
            tenants={
                "default": TenantConfig(
                    provider="fake",
                    agents=TenantAgentSettings(allow=["direct"]),
                    tools=TenantToolSettings(allow=[]),
                )
            }
        ),
        prices=PricesConfig(
            verified_on=date(2026, 11, 12),
            entries=[
                PriceEntry(
                    provider="fake",
                    model="fake-model",
                    input_per_million=1.0,
                    cached_input_per_million=0.1,
                    output_per_million=2.0,
                    source="https://example.invalid/prices",
                )
            ],
        ),
    )


def registry() -> Registry:
    return Registry(
        entries=[
            Entry(name="fake", group=PROVIDERS, value=FakeProvider),
            Entry(name="fake", group=ORCHESTRATORS, value=FakeOrchestratorClass),
            Entry(name="fake", group=RETRIEVERS, value=FakeRetriever),
            Entry(name="fake", group=TOOL_SOURCES, value=FakeToolSource),
            Entry(name="mock", group=EMBEDDERS, value=None),
        ]
    )


def test_orchestrators_are_built_per_provider_with_registered_components() -> None:
    pool = cast(asyncpg.Pool, object())
    orchestrators = build_orchestrators(settings_for(), registry(), pool)

    assert set(orchestrators) == {"fake"}
    built = cast(FakeOrchestratorClass, orchestrators["fake"])
    provider = cast(FakeProvider, built.kwargs["provider"])
    assert provider.name == "fake"
    assert provider.model == "fake-model"
    assert provider.price is not None
    assert provider.price.input_per_million == 1.0
    assert built.kwargs["price"] is provider.price
    assert isinstance(built.kwargs["context_policy"], ClearOldestToolResults)
    assert built.kwargs["limits"].run_timeout_s == 12
    assert isinstance(built.kwargs["retriever"], FakeRetriever)
    assert built.kwargs["retriever"].pool is pool
    assert built.kwargs["retriever"].embedder.dimension == 8
    assert [source.name for source in built.kwargs["tool_sources"]] == ["tools"]
    assert built.kwargs["registry"].names(PROVIDERS) == ["fake"]


def test_no_retriever_configured_means_none() -> None:
    pool = cast(asyncpg.Pool, object())
    orchestrators = build_orchestrators(settings_for(retriever="none"), registry(), pool)
    built = cast(FakeOrchestratorClass, orchestrators["fake"])
    assert built.kwargs["retriever"] is None


def test_orchestrators_receive_the_answer_delta_sink() -> None:
    pool = cast(asyncpg.Pool, object())

    def sink(run_id: str, text: str) -> None:
        return None

    orchestrators = build_orchestrators(settings_for(), registry(), pool, answer_delta=sink)
    built = cast(FakeOrchestratorClass, orchestrators["fake"])
    assert built.kwargs["answer_delta"] is sink


def test_unknown_context_policy_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown context policy"):
        context_policy_for("summarise_everything")


def test_app_deps_rejects_a_tenant_without_an_orchestrator() -> None:
    deps = AppDeps(
        store=MemoryRunStore(),
        audit=MemoryRunStore(),
        orchestrators={},
        tenants=tenants_config(),
        run_token_secret="s" * 32,
        run_timeout_s=30.0,
        publisher=StepEventPublisher(),
    )
    with pytest.raises(ConfigError, match="no orchestrator for provider"):
        deps.orchestrator_for("lmstudio")
