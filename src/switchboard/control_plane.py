"""Normalized control-plane read model (V3.01).

The settings documents are the validated write path. This module projects the
loaded ``Settings`` and the agent registry into queryable tables so later
control-plane screens do not have to parse JSON documents. The projection is
rebuilt in one transaction, so readers never see a half-synced graph.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import asyncpg

from switchboard.adapters.tools.mcp_client import discover_tools
from switchboard.api.settings import (
    load_agent_specs,
    load_manifest_tool_sources,
    load_manifest_tool_specs,
)
from switchboard.core.config import Settings
from switchboard.core.errors import ConfigError
from switchboard.core.models import ToolSpec
from switchboard.core.tool_pinning import tool_fingerprint
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


def build_control_plane_rows(
    settings: Settings,
    registry: Registry,
    tool_specs_by_source: Mapping[str, list[ToolSpec]] | None = None,
) -> ControlPlaneRows:
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
    if tool_specs_by_source is None:
        tool_sources = load_manifest_tool_sources(settings)
        tool_specs_by_source = {}
        for spec in load_manifest_tool_specs(settings):
            source_name = tool_sources.get(spec.wire_name)
            if source_name is not None:
                tool_specs_by_source.setdefault(source_name, []).append(spec)
    tools = [
        (
            source_name,
            spec.wire_name,
            spec.name,
            spec.version,
            spec.description,
            spec.risk,
            spec.tenant_scoped,
            json.dumps(spec.input_schema),
            json.dumps(spec.sensitive_args),
            tool_fingerprint(spec),
        )
        for source_name, specs in sorted(tool_specs_by_source.items())
        for spec in specs
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
    """Replace the normalized tables and pin changed tools (V3.10)."""
    manifest_specs = load_manifest_tool_specs(settings)
    manifest_sources = load_manifest_tool_sources(settings)
    by_source: dict[str, list[ToolSpec]] = {}
    for spec in manifest_specs:
        source_name = manifest_sources.get(spec.wire_name)
        if source_name is not None:
            by_source.setdefault(source_name, []).append(spec)
    tool_specs_by_source: dict[str, list[ToolSpec]] = {}
    for source in settings.platform.tool_sources:
        specs: list[ToolSpec] | None = None
        if source.adapter == "mcp" and source.url:
            try:
                specs = await discover_tools(source.url)
            except ConfigError:
                # The server may still be starting. The manifest is the
                # fallback for the projection, and the runtime source will
                # report unavailable tools when it is actually called.
                specs = None
        if specs is None:
            specs = by_source.get(source.name)
        if specs is not None:
            tool_specs_by_source[source.name] = specs
    rows = build_control_plane_rows(settings, registry, tool_specs_by_source)
    async with pool.acquire() as connection, connection.transaction():
        existing_rows = await connection.fetch(
            "SELECT server, wire_name, fingerprint, approved_fingerprint "
            "FROM public.control_mcp_tools"
        )
        existing = {
            (str(row["server"]), str(row["wire_name"])): (
                row["fingerprint"],
                row["approved_fingerprint"],
            )
            for row in existing_rows
        }
        sources_with_history = {key[0] for key in existing}
        tool_rows: list[tuple[Any, ...]] = []
        changes: list[tuple[str, str, str]] = []
        for row in rows.mcp_tools:
            server, wire_name, fingerprint = str(row[0]), str(row[1]), str(row[9])
            prior = existing.get((server, wire_name))
            if prior is None:
                # The first sync of a source approves its current tools. A tool
                # added to a source that already had tools needs approval.
                approved = fingerprint if server not in sources_with_history else None
                if server in sources_with_history:
                    changes.append((server, wire_name, "new"))
            elif prior[1] is None:
                # Rows created before V3.10 have no approved fingerprint. If
                # the current fingerprint is unchanged, treat the first sync as
                # the approval; a changed tool stays unapproved.
                approved = fingerprint if prior[0] == fingerprint else None
                if prior[0] != fingerprint:
                    changes.append((server, wire_name, "changed"))
            else:
                approved = prior[1]
                if prior[0] != fingerprint:
                    changes.append((server, wire_name, "changed"))
            tool_rows.append((*row, approved))

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
            "input_schema, sensitive_args, fingerprint, approved_fingerprint) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11)",
            tool_rows,
        )
        if changes:
            revision = int(
                await connection.fetchval(
                    "SELECT COALESCE(MAX(revision), 0) FROM public.settings_revisions"
                )
                or 0
            )
            await connection.executemany(
                "INSERT INTO public.config_audit "
                "(kind, revision, action, actor, detail) VALUES ($1,$2,$3,$4,$5::jsonb)",
                [
                    (
                        "platform",
                        revision,
                        "tool_changed" if state == "changed" else "tool_added",
                        "system:control-plane",
                        json.dumps({"server": server, "wire_name": wire_name, "state": state}),
                    )
                    for server, wire_name, state in changes
                ],
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


async def load_tool_approvals(pool: asyncpg.Pool) -> dict[str, dict[str, str]]:
    """Approved wire name to fingerprint for each server (V3.10)."""
    rows = await pool.fetch(
        "SELECT server, wire_name, fingerprint, approved_fingerprint FROM public.control_mcp_tools"
    )
    approved: dict[str, dict[str, str]] = {}
    for row in rows:
        if (
            row["approved_fingerprint"] is not None
            and row["fingerprint"] == row["approved_fingerprint"]
        ):
            approved.setdefault(str(row["server"]), {})[str(row["wire_name"])] = str(
                row["fingerprint"]
            )
    return approved


async def load_tool_statuses(pool: asyncpg.Pool) -> dict[tuple[str, str], str]:
    """``approved`` or ``needs_approval`` per server and wire name (V3.10)."""
    rows = await pool.fetch(
        "SELECT server, wire_name, fingerprint, approved_fingerprint FROM public.control_mcp_tools"
    )
    statuses: dict[tuple[str, str], str] = {}
    for row in rows:
        ok = (
            row["approved_fingerprint"] is not None
            and row["fingerprint"] == row["approved_fingerprint"]
        )
        statuses[(str(row["server"]), str(row["wire_name"]))] = (
            "approved" if ok else "needs_approval"
        )
    return statuses


async def approve_tool_source(pool: asyncpg.Pool, server: str) -> int:
    """Approve every current tool of one source and audit the action."""
    async with pool.acquire() as connection, connection.transaction():
        rows = await connection.fetch(
            "UPDATE public.control_mcp_tools "
            "SET approved_fingerprint = fingerprint "
            "WHERE server = $1 RETURNING wire_name",
            server,
        )
        revision = int(
            await connection.fetchval(
                "SELECT COALESCE(MAX(revision), 0) FROM public.settings_revisions"
            )
            or 0
        )
        await connection.execute(
            "INSERT INTO public.config_audit "
            "(kind, revision, action, actor, detail) VALUES ($1,$2,$3,$4,$5::jsonb)",
            "platform",
            revision,
            "approve_tools",
            "local:admin",
            json.dumps({"server": server, "tools": len(rows)}),
        )
        return len(rows)


__all__ = [
    "ControlPlaneRows",
    "approve_tool_source",
    "build_control_plane_rows",
    "load_tool_approvals",
    "load_tool_statuses",
    "sync_control_plane",
]
