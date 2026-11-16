"""Normalized control-plane read model (V3.01).

The settings documents are the validated write path. This module projects the
loaded ``Settings`` and the agent registry into queryable tables so later
control-plane screens do not have to parse JSON documents. The projection is
rebuilt in one transaction, so readers never see a half-synced graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from switchboard.api.settings import (
    load_agent_specs,
    load_manifest_tool_sources,
    load_manifest_tool_specs,
)
from switchboard.core.config import Settings
from switchboard.registry import Registry


@dataclass(frozen=True)
class ControlPlaneRows:
    providers: list[tuple[Any, ...]]
    models: list[tuple[Any, ...]]
    mcp_servers: list[tuple[Any, ...]]
    mcp_tools: list[tuple[Any, ...]]
    agents: list[tuple[Any, ...]]
    agent_versions: list[tuple[Any, ...]]
    tenant_bindings: list[tuple[Any, ...]]


def build_control_plane_rows(settings: Settings, registry: Registry) -> ControlPlaneRows:
    providers: list[tuple[Any, ...]] = []
    models: list[tuple[Any, ...]] = []
    for name, provider in sorted(settings.providers.providers.items()):
        providers.append(
            (
                name,
                provider.adapter,
                provider.base_url,
                provider.model,
                json.dumps(
                    provider.capabilities.model_dump(mode="json")
                    if provider.capabilities is not None
                    else None
                ),
            )
        )
        model = provider.model or "unknown"
        price = settings.prices.lookup(name, provider.model)
        models.append(
            (
                name,
                model,
                json.dumps(
                    provider.capabilities.model_dump(mode="json")
                    if provider.capabilities is not None
                    else None
                ),
                json.dumps(price.model_dump(mode="json") if price is not None else None),
            )
        )
    servers = [
        (source.name, source.adapter, source.url, source.manifest)
        for source in sorted(settings.platform.tool_sources, key=lambda item: item.name)
    ]
    tool_sources = load_manifest_tool_sources(settings)
    tools = [
        (
            tool_sources[spec.wire_name],
            spec.wire_name,
            spec.name,
            spec.version,
            spec.description,
            spec.risk,
            spec.tenant_scoped,
            json.dumps(spec.input_schema),
            json.dumps(spec.sensitive_args),
        )
        for spec in load_manifest_tool_specs(settings)
        if spec.wire_name in tool_sources
    ]
    agent_specs = load_agent_specs(registry)
    agents = [
        (
            spec.name,
            spec.description,
            spec.cacheable,
            spec.exclusive,
            json.dumps(list(spec.needs)),
        )
        for spec in agent_specs
    ]
    agent_versions = [
        (spec.name, 1, json.dumps(spec.model_dump(mode="json"))) for spec in agent_specs
    ]
    tenant_bindings = [
        (
            tenant_id,
            tenant.provider,
            tenant.model,
            json.dumps(list(tenant.agents.allow)),
            json.dumps(list(tenant.tools.allow)),
            json.dumps(list(tenant.tools.ask)),
            json.dumps(
                {
                    name: binding.model_dump(mode="json")
                    for name, binding in tenant.connectors.items()
                }
            ),
            json.dumps(tenant.retrieval.model_dump(mode="json")),
            json.dumps(tenant.cache.model_dump(mode="json")),
        )
        for tenant_id, tenant in sorted(settings.tenants.tenants.items())
    ]
    return ControlPlaneRows(
        providers=providers,
        models=models,
        mcp_servers=servers,
        mcp_tools=tools,
        agents=agents,
        agent_versions=agent_versions,
        tenant_bindings=tenant_bindings,
    )


async def sync_control_plane(pool: asyncpg.Pool, settings: Settings, registry: Registry) -> None:
    """Replace the normalized tables from the loaded configuration."""
    rows = build_control_plane_rows(settings, registry)
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "DELETE FROM public.control_tenant_bindings; "
            "DELETE FROM public.control_mcp_tools; "
            "DELETE FROM public.control_agent_versions; "
            "DELETE FROM public.control_models; "
            "DELETE FROM public.control_providers; "
            "DELETE FROM public.control_mcp_servers; "
            "DELETE FROM public.control_agents"
        )
        await connection.executemany(
            "INSERT INTO public.control_providers "
            "(name, adapter, base_url, model, capabilities) VALUES ($1,$2,$3,$4,$5::jsonb)",
            rows.providers,
        )
        await connection.executemany(
            "INSERT INTO public.control_models "
            "(provider, model, capabilities, price) VALUES ($1,$2,$3::jsonb,$4::jsonb)",
            rows.models,
        )
        await connection.executemany(
            "INSERT INTO public.control_mcp_servers "
            "(name, adapter, url, manifest) VALUES ($1,$2,$3,$4)",
            rows.mcp_servers,
        )
        await connection.executemany(
            "INSERT INTO public.control_mcp_tools "
            "(server, wire_name, name, version, description, risk, tenant_scoped, "
            "input_schema, sensitive_args) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)",
            rows.mcp_tools,
        )
        await connection.executemany(
            "INSERT INTO public.control_agents "
            "(name, description, cacheable, exclusive, needs) "
            "VALUES ($1,$2,$3,$4,$5::jsonb)",
            rows.agents,
        )
        await connection.executemany(
            "INSERT INTO public.control_agent_versions (agent, version, spec) "
            "VALUES ($1,$2,$3::jsonb)",
            rows.agent_versions,
        )
        await connection.executemany(
            "INSERT INTO public.control_tenant_bindings "
            "(tenant_id, provider, model, agents, tools_allow, tools_ask, connectors, "
            "retrieval, cache) "
            "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb)",
            rows.tenant_bindings,
        )


__all__ = ["ControlPlaneRows", "build_control_plane_rows", "sync_control_plane"]
