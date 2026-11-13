"""Application dependencies and their production wiring.

``create_app`` takes an ``AppDeps`` so tests can inject a fake orchestrator,
provider and store. This module builds the real one from ``config/*.yaml``, the
application-role pool, the pgvector retriever, the MCP tool source, the context
policy and the LangGraph orchestrator resolved through the registry
(ADR 0011, design.md section 16).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import asyncpg

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.judges.factory import create_judge
from switchboard.adapters.providers.factory import create_provider
from switchboard.api.audit import AuditStore, PgAuditStore, load_sensitive_args
from switchboard.api.run_tokens import require_run_token_secret
from switchboard.api.settings import build_settings_snapshots
from switchboard.api.step_events import StepEventProcessor, StepEventPublisher
from switchboard.api.store import PgRunStore, RunStore
from switchboard.config import load_settings
from switchboard.core.config import PriceEntry, Settings, TenantsConfig
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ConfigError
from switchboard.core.ports import ContextPolicy, Judge, Orchestrator
from switchboard.core.pricing import lookup_price
from switchboard.registry import (
    CACHES,
    ORCHESTRATORS,
    RETRIEVERS,
    TOOL_SOURCES,
    Registry,
)
from switchboard.tracing import configure_tracing, load_tracing_options

logger = logging.getLogger(__name__)

HealthCheck = Callable[[], Awaitable[tuple[int, dict[str, Any]]]]


class TracerFlusher(Protocol):
    """The part of ``TracingHandle`` the response path uses (design 12.2)."""

    async def force_flush_async(self, timeout_s: float = 2.0) -> bool: ...


@dataclass(frozen=True)
class AppDeps:
    """Everything the routes need, injected once at app creation."""

    store: RunStore
    audit: AuditStore
    orchestrators: Mapping[str, Orchestrator]
    tenants: TenantsConfig
    run_token_secret: str
    run_timeout_s: float
    # Optional replay targets keyed by (tenant provider, registered orchestrator
    # name). The default path never reads this map (T3.07).
    replay_orchestrators: Mapping[tuple[str, str], Orchestrator] = field(default_factory=dict)
    # How many completed turns of a session the API gives the orchestrator
    # (limits.history_turns in config/platform.yaml, design.md 6.3).
    history_turns: int = 3
    publisher: StepEventPublisher = field(default_factory=StepEventPublisher)
    tracing: TracerFlusher | None = None
    health: HealthCheck | None = None
    closer: Callable[[], Awaitable[None]] | None = None
    # Tool manifest sensitive_args, keyed by wire name, for the read-side guard
    # in GET /audit (AC-18b).
    sensitive_args: Mapping[str, Sequence[str]] = field(default_factory=dict)
    # JSON-safe runtime settings per tenant for GET /settings (T3.03). None in
    # a test that injects AppDeps without one, which the route answers with 503
    # rather than inventing values.
    settings_snapshot: Mapping[str, Mapping[str, Any]] | None = None

    def orchestrator_for(self, tenant_provider: str, name: str | None = None) -> Orchestrator:
        """The default orchestrator, or a named replay target when ``name`` is set.

        ``name=None`` is exactly the pre-T3.07 path: one orchestrator per tenant
        provider. A name is looked up among the optional replay targets built at
        startup for that provider; the platform default answers to its own name
        too, so a replay can repeat the default configuration.
        """
        default = self.orchestrators.get(tenant_provider)
        if name is None:
            if default is not None:
                return default
            raise ConfigError(
                f"no orchestrator for provider {tenant_provider!r}; "
                f"available: {', '.join(sorted(self.orchestrators)) or 'none'}"
            )
        if default is not None and default.name == name:
            return default
        try:
            return self.replay_orchestrators[(tenant_provider, name)]
        except KeyError as exc:
            available = ", ".join(self.available_orchestrators(tenant_provider)) or "none"
            raise ConfigError(
                f"no orchestrator named {name!r} for provider {tenant_provider!r}; "
                f"available: {available}"
            ) from exc

    def available_orchestrators(self, tenant_provider: str) -> list[str]:
        """The orchestrator names this process can run for one tenant provider."""
        names: set[str] = set()
        default = self.orchestrators.get(tenant_provider)
        if default is not None:
            names.add(default.name)
        names.update(
            name for provider, name in self.replay_orchestrators if provider == tenant_provider
        )
        return sorted(names)

    async def flush(self) -> bool:
        """Flush span batches before the answer leaves (design 12.2)."""
        if self.tracing is None:
            return True
        return await self.tracing.force_flush_async()


def context_policy_for(name: str) -> ContextPolicy:
    if name == "clear_oldest_tool_results":
        return ClearOldestToolResults()
    raise ConfigError(f"unknown context policy {name!r}")


def build_tool_sources(settings: Settings, registry: Registry) -> list[Any]:
    sources: list[Any] = []
    for source in settings.platform.tool_sources:
        component: Any = registry.load(TOOL_SOURCES, source.adapter)
        factory = getattr(component, "from_config", None)
        if factory is None:
            raise ConfigError(f"tool source adapter {source.adapter!r} has no from_config")
        sources.append(factory(source))
    return sources


def build_retriever(
    settings: Settings, registry: Registry, pool: asyncpg.Pool, embedder: Any
) -> Any:
    name = settings.platform.retriever
    if name == "none":
        return None
    component: Any = registry.load(RETRIEVERS, name)
    return component(pool=pool, embedder=embedder)


def build_cache(settings: Settings, registry: Registry, pool: asyncpg.Pool, embedder: Any) -> Any:
    """The configured Cache adapter; ``none`` is a registered adapter too."""
    component: Any = registry.load(CACHES, settings.platform.cache)
    factory = getattr(component, "from_deps", None)
    if factory is None:
        raise ConfigError(f"cache adapter {settings.platform.cache!r} has no from_deps")
    return factory(pool=pool, embedder=embedder)


@dataclass(frozen=True)
class BuiltOrchestrators:
    """The default orchestrator per provider plus the optional replay targets.

    ``default`` is the pre-T3.07 mapping (tenant provider to the platform
    orchestrator). ``replay`` is keyed by (tenant provider, registered
    orchestrator name) and is only read when a request names an orchestrator.
    """

    default: dict[str, Orchestrator]
    replay: dict[tuple[str, str], Orchestrator]


def build_orchestrator_set(
    settings: Settings,
    registry: Registry,
    pool: asyncpg.Pool,
    *,
    answer_delta: Callable[[str, str], None] | None = None,
) -> BuiltOrchestrators:
    """Build every orchestrator instance the API needs (design 6.1, 16, T3.07).

    The platform orchestrator is built once per provider a tenant uses, exactly
    as before. Every other registered orchestrator name gets an optional replay
    instance for each of those providers, sharing the embedder, retriever, cache,
    tool sources, context policy and provider instance of the default path. An
    optional target that cannot be built (for example because its provider key
    is absent from the environment) is skipped with a warning: replay must never
    stop the process.
    """
    embedder_config = settings.providers.embedders[settings.platform.embedder]
    embedder = create_embedder(settings.platform.embedder, embedder_config)
    retriever = build_retriever(settings, registry, pool, embedder)
    cache = build_cache(settings, registry, pool, embedder)
    tool_sources = build_tool_sources(settings, registry)
    context_policy = context_policy_for(settings.platform.context_policy)
    default_name = settings.platform.orchestrator
    default_class: Any = registry.load(ORCHESTRATORS, default_name)
    replay_classes: dict[str, Any] = {
        name: registry.load(ORCHESTRATORS, name)
        for name in registry.names(ORCHESTRATORS)
        if name != default_name
    }

    providers: dict[str, Any] = {}
    prices: dict[str, PriceEntry | None] = {}

    def provider_and_price(provider_name: str) -> tuple[Any, PriceEntry | None]:
        if provider_name not in providers:
            try:
                provider_config = settings.providers.providers[provider_name]
            except KeyError as exc:
                raise ConfigError(f"unknown provider {provider_name!r}") from exc
            price = lookup_price(settings.prices, provider_name, provider_config.model)
            providers[provider_name] = create_provider(
                registry,
                provider_name,
                provider_config,
                price,
                providers=settings.providers.providers,
                prices=settings.prices,
            )
            prices[provider_name] = price
        return providers[provider_name], prices[provider_name]

    # Layer 2 of the gate (design 6.8). Off by default, and built once so both
    # orchestrators and the replay targets share one judge instance.
    judge: Judge | None = None
    if settings.platform.judge.enabled:
        judge_provider, _judge_price = provider_and_price(settings.platform.judge.provider)
        judge = create_judge(registry, provider=judge_provider, settings=settings.platform.judge)

    def instantiate(orchestrator_class: Any, provider_name: str) -> Orchestrator:
        provider, price = provider_and_price(provider_name)
        return cast(
            Orchestrator,
            orchestrator_class(
                provider=provider,
                registry=registry,
                context_policy=context_policy,
                limits=settings.platform.limits,
                embedder=embedder,
                tool_sources=tool_sources,
                retriever=retriever,
                cache=cache,
                price=price,
                answer_delta=answer_delta,
                judge=judge,
            ),
        )

    default: dict[str, Orchestrator] = {}
    replay: dict[tuple[str, str], Orchestrator] = {}
    provider_names = sorted({tenant.provider for tenant in settings.tenants.tenants.values()})
    for provider_name in provider_names:
        # The default target is required: a broken tenant provider still fails
        # startup, exactly as it did before this task.
        default[provider_name] = instantiate(default_class, provider_name)
        for name, orchestrator_class in replay_classes.items():
            try:
                replay[(provider_name, name)] = instantiate(orchestrator_class, provider_name)
            except ConfigError as exc:
                logger.warning(
                    "skipping replay orchestrator %r for provider %r: %s",
                    name,
                    provider_name,
                    exc,
                )
    return BuiltOrchestrators(default=default, replay=replay)


def build_orchestrators(
    settings: Settings,
    registry: Registry,
    pool: asyncpg.Pool,
    *,
    answer_delta: Callable[[str, str], None] | None = None,
) -> dict[str, Orchestrator]:
    """One orchestrator per provider name used by a tenant (design 6.1, 16)."""
    return build_orchestrator_set(settings, registry, pool, answer_delta=answer_delta).default


async def build_app_deps(env: Mapping[str, str] | None = None) -> AppDeps:
    """Build the production dependency graph and the pool it owns."""
    source = dict(os.environ) if env is None else dict(env)
    secret = require_run_token_secret(source)
    config_dir = Path(source.get("SWITCHBOARD_CONFIG_DIR", "config"))
    settings = load_settings(config_dir, source)
    dsn = source.get("SWITCHBOARD_DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("SWITCHBOARD_DATABASE_URL must be set")

    # The exporter comes from tracing.exporter in platform.yaml, resolved the
    # same way in this process and in the MCP server (design 12.2). The
    # environment is only the substitution input, not a second switch.
    tracing_options = load_tracing_options(config_dir / "platform.yaml", source)
    tracing = configure_tracing(
        service_name="switchboard-api",
        dsn=dsn,
        exporter=tracing_options.exporter,
        otlp_endpoint=tracing_options.otlp_endpoint,
    )
    publisher = StepEventPublisher()
    step_events = StepEventProcessor(publisher)
    if tracing is not None:
        # Step events are span-derived; with tracing off the stream carries only
        # answer.delta and run.finished (design 12.5).
        tracing.provider.add_span_processor(step_events)

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
    try:
        registry = Registry()
        built = build_orchestrator_set(
            settings, registry, pool, answer_delta=step_events.publish_answer_delta
        )
        # Which names this process can actually run for each provider, so the
        # trace page never offers a replay target that would answer 400.
        available: dict[str, list[str]] = {}
        for provider, default_orchestrator in built.default.items():
            names = {default_orchestrator.name}
            names.update(
                name for (replay_provider, name) in built.replay if replay_provider == provider
            )
            available[provider] = sorted(names)
        # Built once from the same config, registry and manifests the
        # orchestrators use, so GET /settings cannot drift from what runs.
        settings_snapshot = build_settings_snapshots(
            settings, registry, available_orchestrators=available
        )
    except BaseException:
        await pool.close()
        if tracing is not None:
            tracing.shutdown()
        raise

    async def close() -> None:
        await pool.close()
        if tracing is not None:
            tracing.shutdown()

    return AppDeps(
        store=PgRunStore(pool),
        audit=PgAuditStore(pool),
        orchestrators=built.default,
        replay_orchestrators=built.replay,
        tenants=settings.tenants,
        run_token_secret=secret,
        run_timeout_s=settings.platform.limits.run_timeout_s,
        history_turns=settings.platform.limits.history_turns,
        publisher=publisher,
        tracing=tracing,
        closer=close,
        sensitive_args=load_sensitive_args(settings.platform.tool_sources),
        settings_snapshot=settings_snapshot,
    )


__all__ = [
    "AppDeps",
    "AuditStore",
    "BuiltOrchestrators",
    "HealthCheck",
    "PgAuditStore",
    "build_app_deps",
    "build_cache",
    "build_orchestrator_set",
    "build_orchestrators",
    "build_retriever",
    "build_tool_sources",
    "context_policy_for",
    "load_sensitive_args",
]
