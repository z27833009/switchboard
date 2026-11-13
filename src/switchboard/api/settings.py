"""Read-only runtime snapshot for ``GET /settings`` (design.md section 14, T3.03).

The snapshot is built once in ``build_app_deps`` from the loaded config, the
entry-point registry and the tool manifests, and served per tenant. It exists so
the settings dialog can show what this process actually runs with instead of a
hand-written page.

It holds no secret. Provider and embedder API keys and base URLs, the database
URL, the run token secret and tool ``sensitive_args`` are never copied into it:
a field is only added here when it is safe to hand to a browser. The values are
plain JSON (no dates, sets or models) so the route can return them unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from switchboard.core.config import PriceEntry, Settings, TenantConfig
from switchboard.core.errors import ConfigError
from switchboard.core.models import AgentSpec, ToolSpec
from switchboard.eval.manifest import load_tool_specs
from switchboard.registry import AGENTS, Registry

# The keys a browser must never receive. The tests assert their absence, and
# this tuple documents the boundary for a later editor of this module.
SECRET_KEYS = (
    "api_key",
    "base_url",
    "database_url",
    "dsn",
    "run_token_secret",
    "sensitive_args",
)


def load_agent_specs(registry: Registry) -> list[AgentSpec]:
    """Every registered agent's spec, sorted by name.

    Agents are registered, never hard-coded (ADR 0011), so the dialog lists
    whatever this installation exposes through ``switchboard.agents``.
    """
    specs: list[AgentSpec] = []
    for name in registry.names(AGENTS):
        component: Any = registry.load(AGENTS, name)
        spec = getattr(component, "spec", None)
        if not isinstance(spec, AgentSpec):
            raise ConfigError(f"agent entry point {name!r} does not expose an AgentSpec")
        specs.append(spec)
    return sorted(specs, key=lambda spec: spec.name)


def load_manifest_tool_specs(settings: Settings) -> list[ToolSpec]:
    """The tool specs every configured manifest declares, one per wire name.

    A tool source without a manifest contributes nothing: the in-process
    platform tools (``search_docs@1``, ``result_page@1``) are not described by a
    manifest, so the snapshot does not invent rows for them.
    """
    specs: dict[str, ToolSpec] = {}
    for source in settings.platform.tool_sources:
        if not source.manifest:
            continue
        for spec in load_tool_specs(Path(source.manifest)):
            specs.setdefault(spec.wire_name, spec)
    return sorted(specs.values(), key=lambda spec: spec.wire_name)


def _price_payload(entry: PriceEntry | None, verified_on: str | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "input_per_million": entry.input_per_million,
        "cached_input_per_million": entry.cached_input_per_million,
        "output_per_million": entry.output_per_million,
        # A zero row is a model with no API cost (LM Studio), which is a
        # different statement from "price unknown" (null).
        "no_api_cost": entry.is_zero,
        "verified_on": verified_on,
    }


def _tenant_payload(
    settings: Settings,
    tenant_id: str,
    tenant: TenantConfig,
    agent_specs: list[AgentSpec],
    tool_specs: list[ToolSpec],
    available_orchestrators: Sequence[str] | None,
) -> dict[str, Any]:
    provider = settings.providers.providers[tenant.provider]
    price = settings.prices.lookup(tenant.provider, provider.model)
    return {
        "read_only": True,
        "platform": {
            "orchestrator": settings.platform.orchestrator,
            "retriever": settings.platform.retriever,
            "embedder": settings.platform.embedder,
            "cache": settings.platform.cache,
            "context_policy": settings.platform.context_policy,
            "limits": settings.platform.limits.model_dump(mode="json"),
        },
        # The orchestrator names this process can run for the tenant's
        # provider (the platform default plus built replay targets, T3.07).
        "available_orchestrators": (
            [settings.platform.orchestrator]
            if available_orchestrators is None
            else list(available_orchestrators)
        ),
        "tenant": {
            "tenant_id": tenant_id,
            "provider": tenant.provider,
            "seller_ids": None if tenant.seller_ids is None else list(tenant.seller_ids),
            "agents_allow": list(tenant.agents.allow),
            "tools_allow": list(tenant.tools.allow),
            "tools_ask": list(tenant.tools.ask),
            "retrieval": tenant.retrieval.model_dump(mode="json"),
            "cache": tenant.cache.model_dump(mode="json"),
        },
        "provider": {
            # The name and model are public; the key and the base URL are not
            # copied out of ProviderConfig.
            "name": tenant.provider,
            "model": provider.model,
            "capabilities": (
                None
                if provider.capabilities is None
                else provider.capabilities.model_dump(mode="json")
            ),
            "price": _price_payload(
                price, settings.prices.verified_on.isoformat() if price is not None else None
            ),
        },
        "tools": [
            {
                "name": spec.name,
                "version": spec.version,
                "wire_name": spec.wire_name,
                "description": spec.description,
                "risk": spec.risk,
                "tenant_scoped": spec.tenant_scoped,
                "allowed": spec.wire_name in tenant.tools.allow,
            }
            for spec in tool_specs
        ],
        "agents": [
            {
                "name": spec.name,
                "description": spec.description,
                "cacheable": spec.cacheable,
                "needs": list(spec.needs),
                "allowed": spec.name in tenant.agents.allow,
            }
            for spec in agent_specs
        ],
        "tenants": sorted(settings.tenants.tenants),
    }


def build_settings_snapshots(
    settings: Settings,
    registry: Registry,
    *,
    available_orchestrators: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """One JSON-safe settings payload per configured tenant, keyed by tenant id.

    Building every tenant up front keeps the route a dictionary lookup: the
    dialog cannot trigger file reads, registry loads or a partially built
    answer, and an injected ``AppDeps`` without a snapshot is a clean 503.

    ``available_orchestrators`` is keyed by provider name and lists the
    orchestrator names the API actually built for replay (T3.07). Without it
    (a test that only wires the platform default), the payload names the
    platform orchestrator and nothing else, so it never claims a target that
    may not run.
    """
    agent_specs = load_agent_specs(registry)
    tool_specs = load_manifest_tool_specs(settings)
    return {
        tenant_id: _tenant_payload(
            settings,
            tenant_id,
            tenant,
            agent_specs,
            tool_specs,
            None
            if available_orchestrators is None
            else available_orchestrators.get(tenant.provider),
        )
        for tenant_id, tenant in sorted(settings.tenants.tenants.items())
    }


def snapshot_contains_secret(payload: Mapping[str, Any]) -> list[str]:
    """Secret-shaped keys found in a payload, for the tests and a startup check.

    The snapshot is assembled from explicit fields, so this is a guard against a
    later edit that dumps a config model wholesale. It walks the whole payload
    and returns the offending key paths (empty when the payload is clean).
    """
    found: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                if str(key).lower() in SECRET_KEYS:
                    found.append(child)
                walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(payload, "")
    return found


__all__ = [
    "SECRET_KEYS",
    "build_settings_snapshots",
    "load_agent_specs",
    "load_manifest_tool_specs",
    "snapshot_contains_secret",
]
