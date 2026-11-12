"""Configuration models and hand-written environment substitution.

YAML parsing lives in ``switchboard/config.py`` so core keeps its promise of
importing only the standard library and pydantic (design.md section 2). This
module owns the schema, the ``${VAR}`` substitution and the conversion from
tenant settings to a ``TenantContext``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    CacheSettings,
    ProviderCapabilities,
    RetrievalSettings,
    TenantContext,
)

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def substitute_env(value: Any, env: Mapping[str, str]) -> Any:
    """Replace every ``${VAR}`` in strings, recursively.

    A missing variable is an error rather than an empty string: a silently
    empty API key or base URL is harder to diagnose than a refused start.
    """

    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in env:
                raise ConfigError(f"environment variable {name} is not set")
            return env[name]

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {key: substitute_env(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute_env(item, env) for item in value]
    return value


class ToolSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    adapter: str
    url: str | None = None


class TracingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exporter: Literal["none", "postgres", "otlp"] = "none"


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_timeout_s: float = Field(default=60.0, gt=0)
    max_tool_steps: int = Field(default=4, ge=0)
    max_loop_steps: int = Field(default=8, ge=1)
    max_plan_size: int = Field(default=3, ge=1)
    fallback_plan: list[str] = Field(default_factory=lambda: ["docs", "data"])
    model_view_rows: int = Field(default=20, ge=1)
    context_fill: float = Field(default=0.7, gt=0.0, le=1.0)
    history_turns: int = Field(default=3, ge=0)


class PlatformConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orchestrator: str
    tool_sources: list[ToolSourceConfig] = Field(default_factory=list)
    retriever: str
    cache: str = "none"
    embedder: str
    context_policy: str = "clear_oldest_tool_results"
    tracing: TracingSettings = Field(default_factory=TracingSettings)
    limits: Limits = Field(default_factory=Limits)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    capabilities: ProviderCapabilities | None = None
    fixtures: str | None = None


class EmbedderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    dimension: int = Field(gt=0)
    fixtures: str | None = None


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(min_length=1)
    embedders: dict[str, EmbedderConfig] = Field(min_length=1)


class TenantAgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)


class TenantToolSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    agents: TenantAgentSettings = Field(default_factory=TenantAgentSettings)
    tools: TenantToolSettings = Field(default_factory=TenantToolSettings)
    seller_ids: list[str] | None = None
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)


class TenantsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenants: dict[str, TenantConfig] = Field(min_length=1)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: PlatformConfig
    providers: ProvidersConfig
    tenants: TenantsConfig

    @model_validator(mode="after")
    def references_exist(self) -> Self:
        provider_names = set(self.providers.providers)
        embedder_names = set(self.providers.embedders)
        if self.platform.embedder not in embedder_names:
            raise ValueError(f"unknown embedder {self.platform.embedder!r}")
        for tenant_id, tenant in self.tenants.tenants.items():
            if tenant.provider not in provider_names:
                raise ValueError(f"tenant {tenant_id!r} uses unknown provider {tenant.provider!r}")
        if len(set(self.platform.limits.fallback_plan)) != len(self.platform.limits.fallback_plan):
            raise ValueError("fallback_plan contains duplicates")
        return self


def tenant_context(tenant_id: str, tenants: TenantsConfig) -> TenantContext:
    """Resolve one tenant's settings into the per-run context."""
    try:
        config = tenants.tenants[tenant_id]
    except KeyError as exc:
        raise ConfigError(f"unknown tenant {tenant_id!r}") from exc
    return TenantContext(
        tenant_id=tenant_id,
        allowed_agents=set(config.agents.allow),
        allowed_tools=set(config.tools.allow),
        ask_tools=set(config.tools.ask),
        seller_ids=config.seller_ids,
        provider=config.provider,
        retrieval=config.retrieval,
        cache=config.cache,
    )
