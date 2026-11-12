"""Manifest and tenant configuration for the Olist MCP server.

The server is a separate process and deliberately does not import
``switchboard.core`` (design.md section 3); it owns just the small data it
needs to enforce permissions and version tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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


def load_tenants(path: Path) -> dict[str, TenantSettings]:
    payload = _read_yaml(path)
    entries = payload.get("tenants")
    if not isinstance(entries, dict) or not entries:
        raise ServerConfigError(f"{path} has no tenants mapping")
    tenants: dict[str, TenantSettings] = {}
    for tenant_id, entry in entries.items():
        if not isinstance(entry, dict):
            raise ServerConfigError(f"tenant {tenant_id!r} is not a mapping")
        tools = entry.get("tools") or {}
        allow = tools.get("allow") or []
        if not isinstance(allow, list):
            raise ServerConfigError(f"tenant {tenant_id!r} tools.allow must be a list")
        seller_ids = entry.get("seller_ids")
        if seller_ids is not None and not isinstance(seller_ids, list):
            raise ServerConfigError(f"tenant {tenant_id!r} seller_ids must be a list or null")
        tenants[str(tenant_id)] = TenantSettings(
            allowed_tools=frozenset(str(name) for name in allow),
            seller_ids=tuple(str(seller) for seller in seller_ids)
            if seller_ids is not None
            else None,
        )
    return tenants
