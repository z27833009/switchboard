"""Configuration models and hand-written environment substitution.

YAML parsing lives in ``switchboard/config.py`` so core keeps its promise of
importing only the standard library and pydantic (design.md section 2). This
module owns the schema, the ``${VAR}`` substitution and the conversion from
tenant settings to a ``TenantContext``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    CacheSettings,
    ProviderCapabilities,
    RetrievalSettings,
    TenantContext,
)

# ``${VAR}`` is required, ``${VAR:-default}`` falls back to the default (which
# may be empty) when the variable is unset. The default form exists for a key
# that a local-only setup does not need, so the config can load without it.
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# A tool wire name is ``name@major`` (design.md section 7.2), so a missing or
# misspelled version is caught at startup instead of turning into a denied call
# at run time.
WIRE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*@[1-9][0-9]*$")


def substitute_env(value: Any, env: Mapping[str, str]) -> Any:
    """Replace every ``${VAR}`` in strings, recursively.

    A missing variable without a default is an error rather than an empty
    string: a silently empty API key or base URL is harder to diagnose than a
    refused start. ``${VAR:-default}`` (including an empty default) is the
    explicit opt-in for a value that may be absent.
    """

    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            default = match.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            raise ConfigError(f"environment variable {name} is not set")

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
    # Optional path to the source's tool manifest. When present, the API checks
    # every tenant tool allowlist against the wire names the source serves
    # (design.md sections 7.2 and 11). The path is relative to the process
    # working directory, like the other file paths in the project.
    manifest: str | None = None


class TracingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # postgres keeps the Phase 1 exporter; otlp sends to a collector such as
    # Jaeger; none leaves the OTel API a no-op (design.md section 12.2).
    exporter: Literal["none", "postgres", "otlp"] = "postgres"


class JudgeSettings(BaseModel):
    """Layer 2 of the quality gate (design.md section 6.8).

    Disabled by default. It is enabled only if the agreement run on the
    human-labeled sample supports it, so the production path is the layer 1
    checks until then. ``threshold`` is the minimum ``score`` the model must
    also report for an ``acceptable`` verdict to count, so a low-confidence
    pass is a rejection. ``max_evidence_chars`` caps how much evidence text one
    judge call sees, per evidence item.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "lmstudio"
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    max_evidence_chars: int = Field(default=2000, ge=0)


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
    judge: JudgeSettings = Field(default_factory=JudgeSettings)
    # Hard ceiling for one eval run against a paid provider (design.md section
    # 20.2). The CLI refuses an estimate above it even with --confirm-cost; a
    # one-off run may lower or raise it with --cost-cap-usd.
    cost_cap_usd: float = Field(default=5.0, ge=0.0)
    limits: Limits = Field(default_factory=Limits)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    base_url: str | None = None
    api_key: str | None = None
    # An alternative to writing the key in ``api_key``: name the environment
    # variable and the adapter reads it from the process environment when the
    # provider is built (design.md section 8.1). The committed config uses it
    # for a provider whose SDK would otherwise pick its own variable. An unset
    # variable is a named ConfigError before any request, never an empty key.
    api_key_env: str | None = None
    model: str | None = None
    # Output cap for an API that requires one on every request (the Anthropic
    # Messages API). A CompletionRequest.max_tokens still wins per call.
    max_tokens: int | None = Field(default=None, ge=1)
    capabilities: ProviderCapabilities | None = None
    fixtures: str | None = None
    timeout_s: float = Field(default=120.0, gt=0)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    # Some OpenAI-compatible APIs reject function names outside a pattern
    # (DeepSeek requires ^[a-zA-Z0-9_-]+$, so the @major wire-name suffix is not
    # allowed). When set, the adapter sends a sanitised name and maps the
    # model's call back to the wire name; the tool, audit and trace keep the
    # original name. Null means send wire names unchanged.
    tool_name_pattern: str | None = None
    # A provider may name one fallback provider (design.md section 8.4). The
    # factory wraps the pair in a decorator, so agents and the orchestrator see
    # a single ModelProvider. The fallback is used only after the primary's
    # retry budget is exhausted on a transient error.
    fallback_provider: str | None = None
    retry_attempts: int = Field(default=2, ge=0)
    retry_backoff_s: list[float] = Field(default_factory=lambda: [1.0, 3.0])

    @field_validator("tool_name_pattern")
    @classmethod
    def _check_tool_name_pattern(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"tool_name_pattern is not a valid regex: {exc}") from exc
        return value

    @field_validator("api_key_env")
    @classmethod
    def _check_api_key_env(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("api_key_env must be an environment variable name or null")
        return cleaned

    @field_validator("fallback_provider")
    @classmethod
    def _check_fallback_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("fallback_provider must be a provider name or null")
        return cleaned

    @field_validator("retry_backoff_s")
    @classmethod
    def _check_backoff(cls, values: list[float]) -> list[float]:
        if any(value < 0 for value in values):
            raise ValueError("retry_backoff_s values must be zero or positive")
        return values

    @model_validator(mode="after")
    def _check_retry_budget(self) -> Self:
        if len(self.retry_backoff_s) < self.retry_attempts:
            raise ValueError(
                "retry_backoff_s needs at least one value per retry attempt "
                f"({self.retry_attempts} attempts, {len(self.retry_backoff_s)} values)"
            )
        return self


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


def unique_names(values: list[str], field: str) -> list[str]:
    """Trim names, reject empties and duplicates with the field in the message."""
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError(f"{field} must not contain empty names")
    duplicates = sorted({value for value in cleaned if cleaned.count(value) > 1})
    if duplicates:
        raise ValueError(f"{field} contains duplicates: {', '.join(duplicates)}")
    return cleaned


def wire_names(values: list[str], field: str) -> list[str]:
    """Every entry must be a versioned tool wire name such as ``orders_late@1``."""
    cleaned = unique_names(values, field)
    invalid = [value for value in cleaned if not WIRE_NAME_PATTERN.match(value)]
    if invalid:
        raise ValueError(f"{field} entries must look like name@1: {', '.join(invalid)}")
    return cleaned


class TenantAgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)

    @field_validator("allow")
    @classmethod
    def _check_allow(cls, values: list[str]) -> list[str]:
        cleaned = unique_names(values, "agents.allow")
        if not cleaned:
            raise ValueError("agents.allow must list at least one agent")
        return cleaned


class TenantToolSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)

    @field_validator("allow")
    @classmethod
    def _check_allow(cls, values: list[str]) -> list[str]:
        return wire_names(values, "tools.allow")

    @field_validator("ask")
    @classmethod
    def _check_ask(cls, values: list[str]) -> list[str]:
        return wire_names(values, "tools.ask")

    @model_validator(mode="after")
    def _allow_and_ask_are_disjoint(self) -> Self:
        overlap = sorted(set(self.allow) & set(self.ask))
        if overlap:
            raise ValueError(
                f"tools.allow and tools.ask share {', '.join(overlap)}; a tool is either "
                "allowed automatically or requires approval"
            )
        return self


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    agents: TenantAgentSettings = Field(default_factory=TenantAgentSettings)
    tools: TenantToolSettings = Field(default_factory=TenantToolSettings)
    seller_ids: list[str] | None = None
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)

    @field_validator("seller_ids")
    @classmethod
    def _check_seller_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = unique_names(value, "seller_ids")
        if not cleaned:
            raise ValueError("seller_ids must be null (all sellers) or a non-empty list")
        return cleaned


class TenantsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenants: dict[str, TenantConfig] = Field(min_length=1)

    @field_validator("tenants")
    @classmethod
    def _check_tenant_ids(cls, values: dict[str, TenantConfig]) -> dict[str, TenantConfig]:
        empty = [key for key in values if not key.strip()]
        if empty:
            raise ValueError("tenant ids must not be empty")
        return values


class PriceEntry(BaseModel):
    """One provider/model price row, per million tokens in USD (design 12.4).

    ``model`` is null when the price applies to every model that provider
    serves, which is how the local zero price is written. A specific model row
    wins over the provider-wide row in ``PricesConfig.lookup``.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str | None = None
    input_per_million: float = Field(ge=0)
    cached_input_per_million: float = Field(ge=0)
    # Writing a prompt prefix to the provider's cache is billed above the input
    # rate (Anthropic: 1.25x for the 5 minute cache). None means the provider
    # has no separate write price, so a write costs the input rate; every row
    # written before this field existed therefore keeps its exact cost.
    cache_write_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float = Field(ge=0)
    source: str | None = None
    note: str | None = None

    @property
    def is_zero(self) -> bool:
        """True when every rate is zero, as for a model served on this machine."""
        write = self.cache_write_per_million
        if write is None:
            write = self.input_per_million
        return not (
            self.input_per_million
            or self.cached_input_per_million
            or self.output_per_million
            or write
        )


class PricesConfig(BaseModel):
    """The price table read from ``config/prices.yaml`` (design 12.4).

    Every value comes from the provider's price page on ``verified_on``; a paid
    row without a source URL is rejected, because a remembered price is not a
    verified price.
    """

    model_config = ConfigDict(extra="forbid")

    verified_on: date
    entries: list[PriceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_entries(self) -> Self:
        seen: set[tuple[str, str | None]] = set()
        for entry in self.entries:
            key = (entry.provider, entry.model)
            if key in seen:
                label = entry.provider if entry.model is None else f"{entry.provider}/{entry.model}"
                raise ValueError(f"duplicate price entry for {label}")
            seen.add(key)
            if not entry.is_zero and not (entry.source or "").strip():
                label = entry.provider if entry.model is None else f"{entry.provider}/{entry.model}"
                raise ValueError(f"price entry for {label} has a nonzero price and needs a source")
        return self

    def lookup(self, provider: str, model: str | None = None) -> PriceEntry | None:
        """The most specific row for a provider and model, or None."""
        fallback: PriceEntry | None = None
        for entry in self.entries:
            if entry.provider != provider:
                continue
            if entry.model is None:
                fallback = entry
            elif model is not None and entry.model == model:
                return entry
        return fallback


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: PlatformConfig
    providers: ProvidersConfig
    tenants: TenantsConfig
    prices: PricesConfig

    @model_validator(mode="after")
    def references_exist(self) -> Self:
        provider_names = set(self.providers.providers)
        embedder_names = set(self.providers.embedders)
        if self.platform.embedder not in embedder_names:
            raise ValueError(f"unknown embedder {self.platform.embedder!r}")
        if self.platform.judge.enabled and self.platform.judge.provider not in provider_names:
            raise ValueError(
                f"the quality gate judge is enabled with unknown provider "
                f"{self.platform.judge.provider!r}"
            )
        for tenant_id, tenant in self.tenants.tenants.items():
            if tenant.provider not in provider_names:
                raise ValueError(f"tenant {tenant_id!r} uses unknown provider {tenant.provider!r}")
        for name, provider in self.providers.providers.items():
            fallback_name = provider.fallback_provider
            if fallback_name is None:
                continue
            if fallback_name == name:
                raise ValueError(f"provider {name!r} cannot use itself as a fallback")
            if fallback_name not in provider_names:
                raise ValueError(
                    f"provider {name!r} names unknown fallback provider {fallback_name!r}"
                )
            fallback = self.providers.providers[fallback_name]
            if fallback.fallback_provider is not None:
                raise ValueError(
                    f"provider {name!r} fallback {fallback_name!r} declares its own "
                    "fallback; only one fallback level is supported"
                )
            # A mixed run must still be costed exactly, so both rows are
            # required before the decorator may be built (design.md 12.4).
            if self.prices.lookup(name, provider.model) is None:
                raise ValueError(f"provider {name!r} with a fallback needs a price entry")
            if self.prices.lookup(fallback_name, fallback.model) is None:
                raise ValueError(
                    f"fallback provider {fallback_name!r} needs a price entry "
                    f"before it can absorb a run from {name!r}"
                )
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
