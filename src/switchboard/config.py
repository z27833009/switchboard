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
    PricesConfig,
    ProvidersConfig,
    Settings,
    TenantsConfig,
    ToolSourceConfig,
    substitute_env,
)
from switchboard.core.errors import ConfigError
from switchboard.eval.manifest import load_tool_specs
from switchboard.registry import AGENTS, Registry


def read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    payload = yaml.safe_load(text)
    return {} if payload is None else payload


def load_model[ModelT: BaseModel](
    path: Path, model: type[ModelT], env: Mapping[str, str]
) -> ModelT:
    try:
        return model.model_validate(substitute_env(read_yaml(path), env))
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def source_tool_specs(source: ToolSourceConfig) -> list[Any]:
    """Manifest tools plus inline discovered tools for one source."""
    specs = list(source.tools)
    if source.manifest:
        specs.extend(load_tool_specs(Path(source.manifest)))
    return specs


def declared_tool_names(settings: Settings) -> set[str]:
    """Wire names of every tool a source declares, manifest or discovered.

    A source with neither a manifest nor inline tools cannot be checked; the
    committed config points at the manifests so the demo tenant files are
    validated. The path is relative to the process working directory.
    """
    names: set[str] = set()
    for source in settings.platform.tool_sources:
        names.update(spec.wire_name for spec in source_tool_specs(source))
    return names


def declared_tool_risks(settings: Settings) -> dict[str, str]:
    """Wire name to risk for every tool a source declares.

    The startup check needs the risk, not only the name: a tool declared
    ``write`` must never sit in ``tools.allow``, because that would let it run
    without an approval (design.md section 6.7, ADR 0015).
    """
    risks: dict[str, str] = {}
    for source in settings.platform.tool_sources:
        for spec in source_tool_specs(source):
            risks[spec.wire_name] = spec.risk
    return risks


def validate_tenant_permissions(
    settings: Settings,
    *,
    agent_names: Iterable[str],
    tool_names: Iterable[str],
    tool_risks: Mapping[str, str] | None = None,
) -> None:
    """Fail with a named error when a tenant names an unregistered agent or tool.

    The schema itself (pydantic) rejects malformed wire names and duplicates.
    This check answers the other question: does the name exist? It runs at
    startup, so a typo cannot surface as a silently missing agent or a denied
    tool call during a run (AC-11a, design.md section 11).

    With ``tool_risks`` it also refuses a write tool in ``tools.allow``: a write
    tool only ever runs behind the approval hook, so listing it under ``allow``
    is a configuration error rather than a way to skip the interrupt.
    """
    known_agents = sorted(set(agent_names))
    known_tools = sorted(set(tool_names))
    risks = dict(tool_risks or {})
    for tenant_id, tenant in settings.tenants.tenants.items():
        unknown_agents = sorted(set(tenant.agents.allow) - set(known_agents))
        if unknown_agents:
            raise ConfigError(
                f"tenant {tenant_id!r} allows unknown agents {unknown_agents}; "
                f"registered agents: {known_agents or 'none'}"
            )
        unapproved_writes = sorted(
            name for name in tenant.tools.allow if risks.get(name) == "write"
        )
        if unapproved_writes:
            raise ConfigError(
                f"tenant {tenant_id!r} allows write tools {unapproved_writes} under "
                "tools.allow; a write tool must be listed under tools.ask so a run "
                "pauses for approval"
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


def validate_settings(settings: Settings, label: str) -> Settings:
    """Apply the startup checks shared by YAML and database configuration."""
    try:
        # The aggregate model checks references between the four documents.
        settings = Settings.model_validate(settings)
    except ValueError as exc:
        raise ConfigError(f"{label}: {exc}") from exc
    validate_tenant_permissions(
        settings,
        agent_names=Registry().names(AGENTS),
        tool_names=declared_tool_names(settings),
        tool_risks=declared_tool_risks(settings),
    )
    return settings


def settings_from_documents(
    documents: Mapping[str, Mapping[str, Any]],
    *,
    env: Mapping[str, str] | None = None,
    label: str = "settings",
) -> Settings:
    """Validate the four database documents with the same models as YAML.

    ``documents`` maps ``platform``, ``providers``, ``tenants`` and ``prices``
    to their raw JSON objects. Environment substitution happens here, at the
    process boundary, so a stored document can keep ``${VAR}`` placeholders.
    """
    source = dict(os.environ) if env is None else dict(env)
    missing = sorted({"platform", "providers", "tenants", "prices"} - set(documents))
    if missing:
        raise ConfigError(f"{label}: missing documents {missing}")
    try:
        platform = PlatformConfig.model_validate(substitute_env(documents["platform"], source))
        providers = ProvidersConfig.model_validate(substitute_env(documents["providers"], source))
        tenants = TenantsConfig.model_validate(substitute_env(documents["tenants"], source))
        prices = PricesConfig.model_validate(substitute_env(documents["prices"], source))
    except ValueError as exc:
        raise ConfigError(f"{label}: {exc}") from exc
    try:
        settings = Settings(platform=platform, providers=providers, tenants=tenants, prices=prices)
    except ValueError as exc:
        raise ConfigError(f"{label}: {exc}") from exc
    return validate_settings(settings, label)


def load_settings(config_dir: Path, env: Mapping[str, str] | None = None) -> Settings:
    """Read platform.yaml, providers.yaml, tenants.yaml and prices.yaml, then validate."""
    source = dict(os.environ) if env is None else dict(env)
    platform = load_model(config_dir / "platform.yaml", PlatformConfig, source)
    providers = load_model(config_dir / "providers.yaml", ProvidersConfig, source)
    tenants = load_model(config_dir / "tenants.yaml", TenantsConfig, source)
    prices = load_model(config_dir / "prices.yaml", PricesConfig, source)
    try:
        settings = Settings(platform=platform, providers=providers, tenants=tenants, prices=prices)
    except ValueError as exc:
        raise ConfigError(f"{config_dir}: {exc}") from exc
    return validate_settings(settings, str(config_dir))
