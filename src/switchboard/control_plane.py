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

from switchboard.api.settings import load_agent_specs
from switchboard.core.config import Settings
from switchboard.registry import Registry


@dataclass(frozen=True)
class ControlPlaneRows:
    providers: list[tuple[Any, ...]]
    models: list[tuple[Any, ...]]
    mcp_servers: list[tuple[Any, ...]]
    agents: list[tuple[Any, ...]]


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
    agents = [
        (
            spec.name,
            spec.description,
            spec.cacheable,
            spec.exclusive,
            json.dumps(list(spec.needs)),
        )
        for spec in load_agent_specs(registry)
    ]
    return ControlPlaneRows(
        providers=providers,
        models=models,
        mcp_servers=servers,
        agents=agents,
    )


async def sync_control_plane(pool: asyncpg.Pool, settings: Settings, registry: Registry) -> None:
    """Replace the four normalized tables from the loaded configuration."""
    rows = build_control_plane_rows(settings, registry)
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
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
            "INSERT INTO public.control_agents "
            "(name, description, cacheable, exclusive, needs) "
            "VALUES ($1,$2,$3,$4,$5::jsonb)",
            rows.agents,
        )


__all__ = ["ControlPlaneRows", "build_control_plane_rows", "sync_control_plane"]
