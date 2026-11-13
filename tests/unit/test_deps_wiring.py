"""Production wiring resolves every component through the registry (ADR 0011)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import asyncpg
import pytest
from tests.fakes import MemoryRunStore, tenants_config

from switchboard.adapters.cache.none import NoCache
from switchboard.adapters.orchestrator.agent_loop import AgentLoopOrchestrator
from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.api.deps import (
    AppDeps,
    build_orchestrator_set,
    build_orchestrators,
    context_policy_for,
)
from switchboard.api.step_events import StepEventPublisher
from switchboard.core.config import (
    EmbedderConfig,
    JudgeSettings,
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
from switchboard.core.ports import Orchestrator
from switchboard.registry import (
    CACHES,
    EMBEDDERS,
    JUDGES,
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


class SecondOrchestratorClass:
    name = "second"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeJudgeClass:
    name = "llm"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class UnbuildableOrchestratorClass:
    """Stands in for an optional target whose provider key is absent (T3.07)."""

    name = "unbuildable"

    def __init__(self, **kwargs: Any) -> None:
        raise ConfigError("provider 'paid' needs an api_key; set the environment variable")


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
            Entry(name="none", group=CACHES, value=NoCache),
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
    assert built.kwargs["cache"].name == "none"
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


def judge_registry() -> Registry:
    return Registry(
        entries=[
            Entry(name="fake", group=PROVIDERS, value=FakeProvider),
            Entry(name="fake", group=ORCHESTRATORS, value=FakeOrchestratorClass),
            Entry(name="second", group=ORCHESTRATORS, value=SecondOrchestratorClass),
            Entry(name="fake", group=RETRIEVERS, value=FakeRetriever),
            Entry(name="fake", group=TOOL_SOURCES, value=FakeToolSource),
            Entry(name="mock", group=EMBEDDERS, value=None),
            Entry(name="none", group=CACHES, value=NoCache),
            Entry(name="llm", group=JUDGES, value=FakeJudgeClass),
        ]
    )


def test_the_judge_is_not_built_when_it_is_disabled() -> None:
    pool = cast(asyncpg.Pool, object())
    built = build_orchestrator_set(settings_for(), judge_registry(), pool)

    default = cast(FakeOrchestratorClass, built.default["fake"])
    assert default.kwargs["judge"] is None


def test_an_enabled_judge_is_built_once_and_shared_by_every_orchestrator() -> None:
    pool = cast(asyncpg.Pool, object())
    settings = settings_for()
    settings = settings.model_copy(
        update={
            "platform": settings.platform.model_copy(
                update={
                    "judge": JudgeSettings(
                        enabled=True, provider="fake", threshold=0.7, max_evidence_chars=300
                    )
                }
            )
        }
    )

    built = build_orchestrator_set(settings, judge_registry(), pool)

    default = cast(FakeOrchestratorClass, built.default["fake"])
    replay = cast(SecondOrchestratorClass, built.replay[("fake", "second")])
    judge = default.kwargs["judge"]
    assert isinstance(judge, FakeJudgeClass)
    assert judge is replay.kwargs["judge"]
    assert judge.kwargs["threshold"] == 0.7
    assert judge.kwargs["max_evidence_chars"] == 300
    # The judge runs on its own provider, resolved from the judge settings.
    assert judge.kwargs["provider"].name == "fake"


def agent_loop_settings() -> Settings:
    """The real adapters (mock provider, mock embedder, no tool source)."""
    return Settings(
        platform=PlatformConfig(
            orchestrator="agent_loop",
            tool_sources=[],
            retriever="none",
            cache="none",
            embedder="mock",
            context_policy="clear_oldest_tool_results",
            limits=Limits(run_timeout_s=12),
        ),
        providers=ProvidersConfig(
            providers={
                "mock": ProviderConfig(
                    adapter="mock",
                    model="qwen3-8b",
                    fixtures="evals/fixtures/mock_provider",
                )
            },
            embedders={"mock": EmbedderConfig(adapter="mock", dimension=1024)},
        ),
        tenants=TenantsConfig(
            tenants={
                "default": TenantConfig(
                    provider="mock",
                    agents=TenantAgentSettings(allow=["direct"]),
                    tools=TenantToolSettings(allow=[]),
                )
            }
        ),
        prices=PricesConfig(
            verified_on=date(2026, 11, 12),
            entries=[
                PriceEntry(
                    provider="lmstudio",
                    model=None,
                    input_per_million=0.0,
                    cached_input_per_million=0.0,
                    output_per_million=0.0,
                )
            ],
        ),
    )


def test_platform_yaml_selects_the_agent_loop_orchestrator() -> None:
    """platform.yaml ``orchestrator: agent_loop`` builds the second adapter."""
    pool = cast(asyncpg.Pool, object())
    orchestrators = build_orchestrators(agent_loop_settings(), Registry(), pool)

    assert set(orchestrators) == {"mock"}
    built = orchestrators["mock"]
    assert isinstance(built, AgentLoopOrchestrator)
    assert built.name == "agent_loop"


def test_the_default_orchestrator_is_still_langgraph() -> None:
    pool = cast(asyncpg.Pool, object())
    settings = agent_loop_settings()
    settings = settings.model_copy(
        update={"platform": settings.platform.model_copy(update={"orchestrator": "langgraph"})}
    )
    orchestrators = build_orchestrators(settings, Registry(), pool)

    assert isinstance(orchestrators["mock"], LangGraphOrchestrator)


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


def registry_with_replay() -> Registry:
    return Registry(
        entries=[
            Entry(name="fake", group=PROVIDERS, value=FakeProvider),
            Entry(name="fake", group=ORCHESTRATORS, value=FakeOrchestratorClass),
            Entry(name="second", group=ORCHESTRATORS, value=SecondOrchestratorClass),
            Entry(name="fake", group=RETRIEVERS, value=FakeRetriever),
            Entry(name="fake", group=TOOL_SOURCES, value=FakeToolSource),
            Entry(name="mock", group=EMBEDDERS, value=None),
            Entry(name="none", group=CACHES, value=NoCache),
        ]
    )


def test_every_registered_orchestrator_gets_a_replay_instance_per_provider() -> None:
    pool = cast(asyncpg.Pool, object())
    built = build_orchestrator_set(settings_for(), registry_with_replay(), pool)

    assert set(built.default) == {"fake"}
    assert set(built.replay) == {("fake", "second")}
    default = cast(FakeOrchestratorClass, built.default["fake"])
    replay = cast(SecondOrchestratorClass, built.replay[("fake", "second")])
    # The replay target shares the provider instance, price row and the other
    # components of the default path, so only the orchestrator class differs.
    assert replay.kwargs["provider"] is default.kwargs["provider"]
    assert replay.kwargs["price"] is default.kwargs["price"]
    assert replay.kwargs["retriever"] is default.kwargs["retriever"]
    assert replay.kwargs["cache"] is default.kwargs["cache"]
    assert replay.kwargs["answer_delta"] is None


def test_an_unbuildable_replay_target_is_skipped_not_fatal() -> None:
    """A missing provider key on an optional target never stops startup."""
    registry_with_unbuildable = Registry(
        entries=[
            Entry(name="fake", group=PROVIDERS, value=FakeProvider),
            Entry(name="fake", group=ORCHESTRATORS, value=FakeOrchestratorClass),
            Entry(name="unbuildable", group=ORCHESTRATORS, value=UnbuildableOrchestratorClass),
            Entry(name="fake", group=RETRIEVERS, value=FakeRetriever),
            Entry(name="fake", group=TOOL_SOURCES, value=FakeToolSource),
            Entry(name="mock", group=EMBEDDERS, value=None),
            Entry(name="none", group=CACHES, value=NoCache),
        ]
    )
    pool = cast(asyncpg.Pool, object())
    built = build_orchestrator_set(settings_for(), registry_with_unbuildable, pool)

    assert set(built.default) == {"fake"}
    assert built.replay == {}


def test_app_deps_selects_a_named_replay_target() -> None:
    default = cast(Orchestrator, FakeOrchestratorClass())
    replay = cast(Orchestrator, SecondOrchestratorClass())
    deps = AppDeps(
        store=MemoryRunStore(),
        audit=MemoryRunStore(),
        orchestrators={"fake": default},
        replay_orchestrators={("fake", "second"): replay},
        tenants=tenants_config(),
        run_token_secret="s" * 32,
        run_timeout_s=30.0,
        publisher=StepEventPublisher(),
    )

    assert deps.orchestrator_for("fake") is default
    assert deps.orchestrator_for("fake", "second") is replay
    # The platform default answers to its own name, so a replay can repeat it.
    assert deps.orchestrator_for("fake", "fake") is default
    assert deps.available_orchestrators("fake") == ["fake", "second"]
    assert deps.available_orchestrators("other") == []
    with pytest.raises(ConfigError, match="no orchestrator named 'missing'"):
        deps.orchestrator_for("fake", "missing")
