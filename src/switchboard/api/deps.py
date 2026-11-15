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
from tempfile import TemporaryDirectory
from typing import Any, Protocol, cast

import asyncpg

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.judges.factory import create_judge
from switchboard.adapters.providers.factory import create_provider
from switchboard.adapters.retrievers.ingest import IngestResult, ingest_directory
from switchboard.api.audit import AuditStore, PgAuditStore, load_sensitive_args
from switchboard.api.run_tokens import require_run_token_secret
from switchboard.api.settings import build_settings_snapshots
from switchboard.api.step_events import StepEventProcessor, StepEventPublisher
from switchboard.api.store import PgRunStore, RunStore
from switchboard.config import load_settings
from switchboard.config_store import SettingsStore
from switchboard.control_plane import sync_control_plane
from switchboard.core.config import PriceEntry, Settings, TenantsConfig, provider_model_key
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
    # The database control-plane writer (V3.03). None with YAML or an injected
    # test app; the write route answers 503 rather than guessing a database.
    settings_store: Any | None = None
    # Uploaded markdown/text ingestion (V3.05). None in a test app, so the
    # upload route answers 503 instead of pretending to embed.
    ingest_document: Any | None = None
    # The same embedder the retriever uses, exposed for document reindex.
    embedder: Any | None = None
    # V3.02 local auth. Off by default so the existing local demo and tests
    # keep working; compose sets SWITCHBOARD_AUTH_REQUIRED=true to require it.
    auth_required: bool = False
    auth_secret: str = ""
    admin_password: str | None = None

    def orchestrator_for(
        self, tenant_provider: str, name: str | None = None, model: str | None = None
    ) -> Orchestrator:
        """The default orchestrator, or a named replay target when ``name`` is set.

        The key is the provider plus the tenant's optional model override, so
        two tenants on one provider can run different models in one process.
        """
        key = provider_model_key(tenant_provider, model)
        default = self.orchestrators.get(key)
        if name is None:
            if default is not None:
                return default
            raise ConfigError(
                f"no orchestrator for provider {key!r}; "
                f"available: {', '.join(sorted(self.orchestrators)) or 'none'}"
            )
        if default is not None and default.name == name:
            return default
        try:
            return self.replay_orchestrators[(key, name)]
        except KeyError as exc:
            available = ", ".join(self.available_orchestrators(tenant_provider, model)) or "none"
            raise ConfigError(
                f"no orchestrator named {name!r} for provider {key!r}; available: {available}"
            ) from exc

    def available_orchestrators(self, tenant_provider: str, model: str | None = None) -> list[str]:
        """The orchestrator names this process can run for one provider and model."""
        key = provider_model_key(tenant_provider, model)
        names: set[str] = set()
        default = self.orchestrators.get(key)
        if default is not None:
            names.add(default.name)
        names.update(name for provider, name in self.replay_orchestrators if provider == key)
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


DocumentIngester = Callable[[str, str, str], Awaitable[IngestResult]]


def build_document_ingester(pool: asyncpg.Pool, embedder: Any) -> DocumentIngester:
    """Ingest one uploaded text document through the same chunker as the corpus."""

    async def ingest(tenant_id: str, filename: str, content: str) -> IngestResult:
        with TemporaryDirectory(prefix="switchboard-upload-") as directory:
            root = Path(directory)
            (root / filename).write_text(content, encoding="utf-8")
            return await ingest_directory(pool, embedder, root, tenant_id)

    return ingest


@dataclass(frozen=True)
class BuiltOrchestrators:
    """The default orchestrator per provider plus the optional replay targets.

    ``default`` is the pre-T3.07 mapping (tenant provider to the platform
    orchestrator). ``replay`` is keyed by (tenant provider, registered
    orchestrator name) and is only read when a request names an orchestrator.
    ``cache`` is the shared ``Cache`` adapter instance, so startup can create a
    store-side index once and shutdown can close the connection it owns
    (T3.09); it is ``None`` for a test that builds only orchestrators.
    """

    default: dict[str, Orchestrator]
    replay: dict[tuple[str, str], Orchestrator]
    cache: Any = None
    embedder: Any = None


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

    providers: dict[tuple[str, str | None], Any] = {}
    prices: dict[tuple[str, str | None], PriceEntry | None] = {}

    def provider_and_price(
        provider_name: str, model_override: str | None
    ) -> tuple[Any, PriceEntry | None]:
        key = (provider_name, model_override)
        if key not in providers:
            try:
                provider_config = settings.providers.providers[provider_name]
            except KeyError as exc:
                raise ConfigError(f"unknown provider {provider_name!r}") from exc
            if model_override is not None:
                provider_config = provider_config.model_copy(update={"model": model_override})
            price = lookup_price(settings.prices, provider_name, provider_config.model)
            providers[key] = create_provider(
                registry,
                provider_name,
                provider_config,
                price,
                providers=settings.providers.providers,
                prices=settings.prices,
            )
            prices[key] = price
        return providers[key], prices[key]

    # Layer 2 of the gate (design 6.8). Off by default, and built once so both
    # orchestrators and the replay targets share one judge instance.
    judge: Judge | None = None
    if settings.platform.judge.enabled:
        judge_provider, _judge_price = provider_and_price(settings.platform.judge.provider, None)
        judge = create_judge(registry, provider=judge_provider, settings=settings.platform.judge)

    def instantiate(
        orchestrator_class: Any, provider_name: str, model_override: str | None
    ) -> Orchestrator:
        provider, price = provider_and_price(provider_name, model_override)
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
    provider_models = sorted(
        {(tenant.provider, tenant.model) for tenant in settings.tenants.tenants.values()},
        key=lambda item: (item[0], item[1] or ""),
    )
    for provider_name, model_override in provider_models:
        key = provider_model_key(provider_name, model_override)
        # The default target is required: a broken tenant provider still fails
        # startup, exactly as it did before this task.
        default[key] = instantiate(default_class, provider_name, model_override)
        for name, orchestrator_class in replay_classes.items():
            try:
                replay[(key, name)] = instantiate(orchestrator_class, provider_name, model_override)
            except ConfigError as exc:
                logger.warning(
                    "skipping replay orchestrator %r for provider %r: %s",
                    name,
                    key,
                    exc,
                )
    return BuiltOrchestrators(default=default, replay=replay, cache=cache, embedder=embedder)


def build_orchestrators(
    settings: Settings,
    registry: Registry,
    pool: asyncpg.Pool,
    *,
    answer_delta: Callable[[str, str], None] | None = None,
) -> dict[str, Orchestrator]:
    """One orchestrator per provider name used by a tenant (design 6.1, 16)."""
    return build_orchestrator_set(settings, registry, pool, answer_delta=answer_delta).default


async def _start_cache(cache: Any) -> None:
    """Optional startup hook of a Cache adapter, if it has one (T3.09)."""
    if cache is None:
        return
    start = getattr(cache, "start", None)
    if start is None:
        return
    try:
        await start()
    except Exception as exc:  # the adapter is expected to swallow its own
        logger.warning("cache startup failed: %s", exc)


async def _close_cache(cache: Any) -> None:
    if cache is None:
        return
    aclose = getattr(cache, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as exc:  # pragma: no cover - shutdown only
        logger.warning("closing the cache failed: %s", exc)


async def build_app_deps(env: Mapping[str, str] | None = None) -> AppDeps:
    """Build the production dependency graph and the pool it owns."""
    source = dict(os.environ) if env is None else dict(env)
    secret = require_run_token_secret(source)
    config_dir = Path(source.get("SWITCHBOARD_CONFIG_DIR", "config"))
    dsn = source.get("SWITCHBOARD_DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("SWITCHBOARD_DATABASE_URL must be set")

    # V3.01: the database is the runtime source of truth. The YAML files are
    # still the first boot seed and the export format; a deployment that has
    # not opted in keeps the old YAML path, which is also what unit tests use.
    config_source = source.get("SWITCHBOARD_CONFIG_SOURCE", "yaml").strip().lower()
    if config_source == "db":
        connection = await asyncpg.connect(dsn=dsn)
        try:
            store = SettingsStore(connection, config_dir, source)
            settings = await store.load()
            settings = await store.apply_stored_credentials(settings)
        finally:
            await connection.close()
    elif config_source == "yaml":
        settings = load_settings(config_dir, source)
    else:
        raise ConfigError(
            f"SWITCHBOARD_CONFIG_SOURCE must be 'db' or 'yaml', got {config_source!r}"
        )

    # The exporter comes from tracing.exporter in platform.yaml, resolved the
    # same way in this process and in the MCP server (design 12.2). The
    # environment is only the substitution input, not a second switch. The
    # exporter is bootstrap-only and intentionally stays on the YAML file until
    # a later task changes process startup.
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
        await sync_control_plane(pool, settings, registry)
        # A cache that owns a store-side index creates it here, once, so the
        # first run does not pay for it. The adapter logs a warning instead of
        # raising when the store is down: a cache may never fail a run (T3.09).
        await _start_cache(built.cache)
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
        settings_connection = None
        settings_store = None
        if config_source == "db":
            # One reserved connection owns the revisioned writes; the rest of
            # the pool stays available to requests. V3.03 uses this seam.
            settings_connection = await pool.acquire()
            settings_store = SettingsStore(settings_connection, config_dir, source)
    except BaseException:
        await pool.close()
        if tracing is not None:
            tracing.shutdown()
        raise

    async def close() -> None:
        await _close_cache(built.cache)
        if settings_connection is not None:
            await pool.release(settings_connection)
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
        settings_store=settings_store,
        ingest_document=build_document_ingester(pool, built.embedder),
        embedder=built.embedder,
        auth_required=source.get("SWITCHBOARD_AUTH_REQUIRED", "").strip().lower()
        in {"1", "true", "yes"},
        auth_secret=secret,
        admin_password=source.get("SWITCHBOARD_ADMIN_PASSWORD") or None,
    )


__all__ = [
    "AppDeps",
    "AuditStore",
    "BuiltOrchestrators",
    "DocumentIngester",
    "HealthCheck",
    "PgAuditStore",
    "build_app_deps",
    "build_cache",
    "build_document_ingester",
    "build_orchestrator_set",
    "build_orchestrators",
    "build_retriever",
    "build_tool_sources",
    "context_policy_for",
    "load_sensitive_args",
]
