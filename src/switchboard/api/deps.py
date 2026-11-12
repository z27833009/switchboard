"""Application dependencies and their production wiring.

``create_app`` takes an ``AppDeps`` so tests can inject a fake orchestrator,
provider and store. This module builds the real one from ``config/*.yaml``, the
application-role pool, the pgvector retriever, the MCP tool source, the context
policy and the LangGraph orchestrator resolved through the registry
(ADR 0011, design.md section 16).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import asyncpg

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.providers.factory import create_provider
from switchboard.api.audit import AuditStore, PgAuditStore, load_sensitive_args
from switchboard.api.run_tokens import require_run_token_secret
from switchboard.api.step_events import StepEventProcessor, StepEventPublisher
from switchboard.api.store import PgRunStore, RunStore
from switchboard.config import load_settings
from switchboard.core.config import Settings, TenantsConfig
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ConfigError
from switchboard.core.ports import ContextPolicy, Orchestrator
from switchboard.core.pricing import lookup_price
from switchboard.registry import (
    ORCHESTRATORS,
    RETRIEVERS,
    TOOL_SOURCES,
    Registry,
)
from switchboard.tracing import configure_tracing

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
    publisher: StepEventPublisher = field(default_factory=StepEventPublisher)
    tracing: TracerFlusher | None = None
    health: HealthCheck | None = None
    closer: Callable[[], Awaitable[None]] | None = None
    # Tool manifest sensitive_args, keyed by wire name, for the read-side guard
    # in GET /audit (AC-18b).
    sensitive_args: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def orchestrator_for(self, tenant_provider: str) -> Orchestrator:
        try:
            return self.orchestrators[tenant_provider]
        except KeyError as exc:
            raise ConfigError(
                f"no orchestrator for provider {tenant_provider!r}; "
                f"available: {', '.join(sorted(self.orchestrators)) or 'none'}"
            ) from exc

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


def build_orchestrators(
    settings: Settings,
    registry: Registry,
    pool: asyncpg.Pool,
) -> dict[str, Orchestrator]:
    """One orchestrator per provider name used by a tenant (design 6.1, 16)."""
    embedder_config = settings.providers.embedders[settings.platform.embedder]
    embedder = create_embedder(settings.platform.embedder, embedder_config)
    retriever = build_retriever(settings, registry, pool, embedder)
    tool_sources = build_tool_sources(settings, registry)
    context_policy = context_policy_for(settings.platform.context_policy)
    orchestrator_class: Any = registry.load(ORCHESTRATORS, settings.platform.orchestrator)

    orchestrators: dict[str, Orchestrator] = {}
    provider_names = sorted({tenant.provider for tenant in settings.tenants.tenants.values()})
    for provider_name in provider_names:
        try:
            provider_config = settings.providers.providers[provider_name]
        except KeyError as exc:
            raise ConfigError(f"unknown provider {provider_name!r}") from exc
        price = lookup_price(settings.prices, provider_name, provider_config.model)
        provider = create_provider(
            registry,
            provider_name,
            provider_config,
            price,
            providers=settings.providers.providers,
            prices=settings.prices,
        )
        orchestrators[provider_name] = cast(
            Orchestrator,
            orchestrator_class(
                provider=provider,
                registry=registry,
                context_policy=context_policy,
                limits=settings.platform.limits,
                embedder=embedder,
                tool_sources=tool_sources,
                retriever=retriever,
                price=price,
            ),
        )
    return orchestrators


async def build_app_deps(env: Mapping[str, str] | None = None) -> AppDeps:
    """Build the production dependency graph and the pool it owns."""
    source = dict(os.environ) if env is None else dict(env)
    secret = require_run_token_secret(source)
    settings = load_settings(Path(source.get("SWITCHBOARD_CONFIG_DIR", "config")), source)
    if settings.platform.cache != "none":
        raise ConfigError(
            f"cache {settings.platform.cache!r} is not implemented in Phase 1 "
            "(design.md section 10 is v1)"
        )
    dsn = source.get("SWITCHBOARD_DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("SWITCHBOARD_DATABASE_URL must be set")

    tracing = configure_tracing(
        service_name="switchboard-api",
        dsn=dsn,
        exporter=source.get("SWITCHBOARD_TRACING_EXPORTER", "postgres"),
    )
    publisher = StepEventPublisher()
    if tracing is not None:
        # Step events are span-derived; with tracing off the stream carries only
        # run.finished (design 12.5).
        tracing.provider.add_span_processor(StepEventProcessor(publisher))

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
    try:
        registry = Registry()
        orchestrators = build_orchestrators(settings, registry, pool)
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
        orchestrators=orchestrators,
        tenants=settings.tenants,
        run_token_secret=secret,
        run_timeout_s=settings.platform.limits.run_timeout_s,
        publisher=publisher,
        tracing=tracing,
        closer=close,
        sensitive_args=load_sensitive_args(settings.platform.tool_sources),
    )


__all__ = [
    "AppDeps",
    "AuditStore",
    "HealthCheck",
    "PgAuditStore",
    "build_app_deps",
    "build_orchestrators",
    "build_retriever",
    "build_tool_sources",
    "context_policy_for",
    "load_sensitive_args",
]
