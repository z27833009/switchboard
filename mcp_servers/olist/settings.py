"""Manifest and tenant configuration for the Olist MCP server.

The server is a separate process and deliberately does not import
``switchboard.core`` (design.md section 3); it owns just the small data it
needs to enforce permissions and version tools. The tenant file is therefore
validated twice: the API checks the full schema and the registered agent names,
and this module checks the parts the trust boundary depends on (the tool wire
names against the manifest, the ask list, and the seller ids).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TENANT_KEYS = frozenset({"provider", "agents", "tools", "seller_ids", "retrieval", "cache"})
AGENT_KEYS = frozenset({"allow"})
TOOL_KEYS = frozenset({"allow", "ask"})
RETRIEVAL_KEYS = frozenset({"k", "min_score", "rewrite"})
CACHE_KEYS = frozenset({"enabled", "threshold", "ttl_days"})


class ServerConfigError(Exception):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    risk: str
    tenant_scoped: bool
    sensitive_args: tuple[str, ...]

    @property
    def wire_name(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class TenantSettings:
    allowed_tools: frozenset[str]
    seller_ids: tuple[str, ...] | None
    # ask is the v2 human-approval list. The approval hook does not exist yet,
    # so an ask tool is not in allowed_tools and a call is denied and audited.
    ask_tools: frozenset[str] = field(default_factory=frozenset)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ServerConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ServerConfigError(f"{path} must contain a YAML mapping")
    return payload


def load_manifest(path: Path) -> dict[str, ToolDefinition]:
    payload = _read_yaml(path)
    entries = payload.get("tools")
    if not isinstance(entries, list) or not entries:
        raise ServerConfigError(f"{path} has no tools list")
    tools: dict[str, ToolDefinition] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ServerConfigError(f"{path} has a non-mapping tool entry")
        try:
            tool = ToolDefinition(
                name=str(entry["name"]),
                version=str(entry["version"]),
                description=str(entry["description"]),
                input_schema=dict(entry["input_schema"]),
                risk=str(entry.get("risk", "read")),
                tenant_scoped=bool(entry.get("tenant_scoped", False)),
                sensitive_args=tuple(str(item) for item in entry.get("sensitive_args", [])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ServerConfigError(f"{path} has an invalid tool entry: {exc}") from exc
        if tool.wire_name in tools:
            raise ServerConfigError(f"{path} declares {tool.wire_name} twice")
        tools[tool.wire_name] = tool
    return tools


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ServerConfigError(f"{field} must be a mapping")
    return value


def _known_keys(value: Mapping[str, Any], allowed: frozenset[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ServerConfigError(f"{field} has unknown keys {unknown}; allowed: {sorted(allowed)}")


def _names(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ServerConfigError(f"{field} must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ServerConfigError(f"{field} entries must be strings")
    cleaned = [item.strip() for item in value]
    if any(not name for name in cleaned):
        raise ServerConfigError(f"{field} must not contain empty names")
    duplicates = sorted({name for name in cleaned if cleaned.count(name) > 1})
    if duplicates:
        raise ServerConfigError(f"{field} contains duplicates: {', '.join(duplicates)}")
    return cleaned


def load_tenants(path: Path, manifest: Mapping[str, ToolDefinition]) -> dict[str, TenantSettings]:
    """Load the tenant allowlists and seller scope, checked against the manifest.

    An unknown tool wire name is a startup error rather than a run-time denial:
    it means the file and the server disagree about what exists, and a typo in
    an allowlist would otherwise silently narrow or widen access.
    """
    payload = _read_yaml(path)
    entries = payload.get("tenants")
    if not isinstance(entries, dict) or not entries:
        raise ServerConfigError(f"{path} has no tenants mapping")
    known_tools = sorted(manifest)
    tenants: dict[str, TenantSettings] = {}
    for raw_id, entry in entries.items():
        tenant_id = str(raw_id).strip()
        if not tenant_id:
            raise ServerConfigError(f"{path} has an empty tenant id")
        if not isinstance(entry, dict):
            raise ServerConfigError(f"tenant {tenant_id!r} is not a mapping")
        _known_keys(entry, TENANT_KEYS, f"tenant {tenant_id!r}")
        provider = entry.get("provider")
        if provider is not None and (not isinstance(provider, str) or not provider.strip()):
            raise ServerConfigError(f"tenant {tenant_id!r} provider must be a non-empty string")

        agents = _mapping(entry.get("agents", {}), f"tenant {tenant_id!r} agents")
        _known_keys(agents, AGENT_KEYS, f"tenant {tenant_id!r} agents")
        # Agent names are registered in the API process; the server cannot
        # resolve them and only validates the shape here.
        _names(agents.get("allow", []), f"tenant {tenant_id!r} agents.allow")

        tools = _mapping(entry.get("tools", {}), f"tenant {tenant_id!r} tools")
        _known_keys(tools, TOOL_KEYS, f"tenant {tenant_id!r} tools")
        allow = _names(tools.get("allow", []), f"tenant {tenant_id!r} tools.allow")
        ask = _names(tools.get("ask", []), f"tenant {tenant_id!r} tools.ask")
        for field_name, values in (("tools.allow", allow), ("tools.ask", ask)):
            unknown = sorted(set(values) - set(known_tools))
            if unknown:
                raise ServerConfigError(
                    f"tenant {tenant_id!r} {field_name} names unknown tools "
                    f"{unknown}; manifest tools: {known_tools}"
                )
        overlap = sorted(set(allow) & set(ask))
        if overlap:
            raise ServerConfigError(
                f"tenant {tenant_id!r} tools.allow and tools.ask share "
                f"{', '.join(overlap)}; a tool is either allowed automatically "
                "or requires approval"
            )

        seller_ids = entry.get("seller_ids")
        if seller_ids is not None:
            sellers = _names(seller_ids, f"tenant {tenant_id!r} seller_ids")
            if not sellers:
                raise ServerConfigError(
                    f"tenant {tenant_id!r} seller_ids must be null (all sellers) "
                    "or a non-empty list"
                )
        else:
            sellers = []

        for field_name, allowed_keys in (
            ("retrieval", RETRIEVAL_KEYS),
            ("cache", CACHE_KEYS),
        ):
            if field_name in entry:
                settings = _mapping(entry[field_name], f"tenant {tenant_id!r} {field_name}")
                _known_keys(settings, allowed_keys, f"tenant {tenant_id!r} {field_name}")

        tenants[tenant_id] = TenantSettings(
            allowed_tools=frozenset(allow),
            seller_ids=tuple(sellers) if seller_ids is not None else None,
            ask_tools=frozenset(ask),
        )
    return tenants
