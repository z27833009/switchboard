"""Manifest and tenant configuration for the Olist MCP server.

The server is a separate process and deliberately does not import
``switchboard.core`` (design.md section 3); it owns just the small data it
needs to enforce permissions and version tools. The tenant file is therefore
validated twice: the API checks the full schema and the registered agent names,
and this module checks the parts the trust boundary depends on (the tool wire
names against the manifest, the ask list, and the seller ids).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TENANT_KEYS = frozenset({"provider", "agents", "tools", "seller_ids", "retrieval", "cache"})
AGENT_KEYS = frozenset({"allow"})
TOOL_KEYS = frozenset({"allow", "ask"})
RETRIEVAL_KEYS = frozenset({"k", "min_score", "rewrite", "rewrite_mode", "search"})
CACHE_KEYS = frozenset({"enabled", "threshold", "ttl_days"})
# Wire names are name@major. A name this server does not serve belongs to
# another MCP server (the tenants file is shared); the API checks every entry
# against the union of the manifests in platform.yaml.
WIRE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*@[0-9]+$")


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
    # ask is the v2 human-approval list: the orchestrator pauses before one of
    # these tools runs, and this server accepts the call because it cannot see
    # the decision (ADR 0015).
    ask_tools: frozenset[str] = field(default_factory=frozenset)
    # The tenant id is what the write tool stores beside a note; it always comes
    # from the verified token, never from an argument.
    tenant_id: str = ""


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
        if tool.risk not in {"read", "write"}:
            raise ServerConfigError(
                f"{path} gives {tool.wire_name} risk {tool.risk!r}; expected read or write"
            )
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
    """Load the tenant allowlists and seller scope, checked against this manifest.

    The tenants file is shared by every MCP server, so an allowlist entry this
    server does not serve belongs to another server and is ignored here. Only
    the shape of a wire name is checked locally; the API checks every entry
    against the union of the manifests declared in platform.yaml, which is
    where a typo fails at startup (design.md sections 7.2, 7.3 and 11).
    """
    payload = _read_yaml(path)
    entries = payload.get("tenants")
    if not isinstance(entries, dict) or not entries:
        raise ServerConfigError(f"{path} has no tenants mapping")
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
            malformed = sorted(name for name in values if not WIRE_NAME_PATTERN.match(name))
            if malformed:
                raise ServerConfigError(
                    f"tenant {tenant_id!r} {field_name} names malformed wire names "
                    f"{malformed}; expected name@major"
                )
        overlap = sorted(set(allow) & set(ask))
        if overlap:
            raise ServerConfigError(
                f"tenant {tenant_id!r} tools.allow and tools.ask share "
                f"{', '.join(overlap)}; a tool is either allowed automatically "
                "or requires approval"
            )
        # A write tool runs only behind the approval hook, so listing one under
        # tools.allow would silently skip the decision (T3.01, ADR 0015). The
        # API checks the same rule against every declared manifest.
        unapproved_writes = sorted(
            name for name in allow if name in manifest and manifest[name].risk == "write"
        )
        if unapproved_writes:
            raise ServerConfigError(
                f"tenant {tenant_id!r} tools.allow lists write tools "
                f"{', '.join(unapproved_writes)}; a write tool must be in tools.ask"
            )

        seller_ids = entry.get("seller_ids")
        if seller_ids is not None:
            sellers = _names(seller_ids, f"tenant {tenant_id!r} seller_ids")
            if not sellers:
                raise ServerConfigError(
                    f"tenant {tenant_id!r} seller_ids must be null (all sellers) "
                    "or a non-empty list"
                )
            # The SQL fact view receives the scope as one comma-separated setting,
            # so a comma inside an id would silently widen the filter.
            if any("," in seller for seller in sellers):
                raise ServerConfigError(f"tenant {tenant_id!r} seller_ids must not contain commas")
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
            allowed_tools=frozenset(name for name in allow if name in manifest),
            seller_ids=tuple(sellers) if seller_ids is not None else None,
            ask_tools=frozenset(name for name in ask if name in manifest),
            tenant_id=tenant_id,
        )
    return tenants
