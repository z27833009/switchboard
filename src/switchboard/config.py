"""YAML loading and the aggregate settings object.

PyYAML is used here and not in ``core/config.py``, so the import-linter rule
that core depends only on the standard library and pydantic stays true.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from switchboard.core.config import (
    PlatformConfig,
    ProvidersConfig,
    Settings,
    TenantsConfig,
    substitute_env,
)
from switchboard.core.errors import ConfigError
from switchboard.eval.manifest import load_tool_specs
from switchboard.registry import AGENTS, Registry


def read_yaml(path: Path) -> Any:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {} if payload is None else payload


def load_model[ModelT: BaseModel](
    path: Path, model: type[ModelT], env: Mapping[str, str]
) -> ModelT:
    try:
        return model.model_validate(substitute_env(read_yaml(path), env))
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def declared_tool_names(settings: Settings) -> set[str]:
    """Wire names of every tool source that declares a manifest.

    A source without a manifest cannot be checked; the committed config points
    at ``mcp_servers/olist/manifest.yaml`` so the demo tenant files are
    validated. The path is relative to the process working directory.
    """
    names: set[str] = set()
    for source in settings.platform.tool_sources:
        if source.manifest:
            names.update(spec.wire_name for spec in load_tool_specs(Path(source.manifest)))
    return names


def validate_tenant_permissions(
    settings: Settings,
    *,
    agent_names: Iterable[str],
    tool_names: Iterable[str],
) -> None:
    """Fail with a named error when a tenant names an unregistered agent or tool.

    The schema itself (pydantic) rejects malformed wire names and duplicates.
    This check answers the other question: does the name exist? It runs at
    startup, so a typo cannot surface as a silently missing agent or a denied
    tool call during a run (AC-11a, design.md section 11).
    """
    known_agents = sorted(set(agent_names))
    known_tools = sorted(set(tool_names))
    for tenant_id, tenant in settings.tenants.tenants.items():
        unknown_agents = sorted(set(tenant.agents.allow) - set(known_agents))
        if unknown_agents:
            raise ConfigError(
                f"tenant {tenant_id!r} allows unknown agents {unknown_agents}; "
                f"registered agents: {known_agents or 'none'}"
            )
        # Without any manifest there is no server-side source of truth to check
        # against; the MCP server still validates its own copy of this file.
        if not known_tools:
            continue
        for field_name, values in (
            ("tools.allow", tenant.tools.allow),
            ("tools.ask", tenant.tools.ask),
        ):
            unknown_tools = sorted(set(values) - set(known_tools))
            if unknown_tools:
                raise ConfigError(
                    f"tenant {tenant_id!r} {field_name} names unknown tools "
                    f"{unknown_tools}; manifest tools: {known_tools}"
                )


def load_settings(config_dir: Path, env: Mapping[str, str] | None = None) -> Settings:
    """Read platform.yaml, providers.yaml and tenants.yaml, then validate them."""
    source = dict(os.environ) if env is None else dict(env)
    platform = load_model(config_dir / "platform.yaml", PlatformConfig, source)
    providers = load_model(config_dir / "providers.yaml", ProvidersConfig, source)
    tenants = load_model(config_dir / "tenants.yaml", TenantsConfig, source)
    try:
        settings = Settings(platform=platform, providers=providers, tenants=tenants)
    except ValueError as exc:
        raise ConfigError(f"{config_dir}: {exc}") from exc
    validate_tenant_permissions(
        settings,
        agent_names=Registry().names(AGENTS),
        tool_names=declared_tool_names(settings),
    )
    return settings
