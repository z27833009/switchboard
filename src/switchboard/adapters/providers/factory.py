"""Build the model provider named in configuration, through the registry.

The price row is resolved here, from ``config/prices.yaml``, so the adapter gets
the one piece of pricing it needs and the factory stays the composition root for
providers (design.md sections 8.1 and 12.4). A provider that names a
``fallback_provider`` is wrapped in ``FallbackProvider`` here, so the rest of
the platform keeps seeing one ``ModelProvider``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from switchboard.adapters.providers.fallback import FallbackProvider, Sleeper
from switchboard.core.config import PriceEntry, PricesConfig, ProviderConfig
from switchboard.core.errors import ConfigError
from switchboard.core.ports import ModelProvider
from switchboard.registry import PROVIDERS, Registry


def _build(
    registry: Registry,
    name: str,
    config: ProviderConfig,
    price: PriceEntry | None,
) -> ModelProvider:
    component: Any = registry.load(PROVIDERS, config.adapter)
    factory = getattr(component, "from_config", None)
    if factory is None:
        raise ConfigError(f"provider adapter {config.adapter!r} has no from_config")
    return cast(ModelProvider, factory(name, config, price))


def create_provider(
    registry: Registry,
    name: str,
    config: ProviderConfig,
    price: PriceEntry | None = None,
    *,
    providers: Mapping[str, ProviderConfig] | None = None,
    prices: PricesConfig | None = None,
    use_fallback: bool = True,
    sleeper: Sleeper | None = None,
) -> ModelProvider:
    """Resolve the adapter, wrapping it when a fallback provider is configured.

    ``providers`` and ``prices`` are needed only for the fallback lookup. The
    eval runner passes ``use_fallback=False`` so a measurement run cannot
    silently score a second provider.
    """
    provider = _build(registry, name, config, price)
    fallback_name = config.fallback_provider
    if fallback_name is None or not use_fallback:
        return provider
    if providers is None or prices is None:
        raise ConfigError(
            f"provider {name!r} has a fallback_provider, so the factory needs the "
            "provider map and the price table"
        )
    if price is None:
        raise ConfigError(
            f"provider {name!r} has a fallback_provider but no price entry; a mixed "
            "run cannot be costed (design.md 12.4)"
        )
    try:
        fallback_config = providers[fallback_name]
    except KeyError as exc:
        raise ConfigError(
            f"provider {name!r} names unknown fallback provider {fallback_name!r}"
        ) from exc
    fallback_price = prices.lookup(fallback_name, fallback_config.model)
    if fallback_price is None:
        raise ConfigError(
            f"fallback provider {fallback_name!r} has no price entry; a mixed run "
            "cannot be costed (design.md 12.4)"
        )
    fallback = _build(registry, fallback_name, fallback_config, fallback_price)
    return FallbackProvider(
        primary=provider,
        fallback=fallback,
        retry_attempts=config.retry_attempts,
        backoff_s=config.retry_backoff_s,
        sleeper=sleeper,
    )


__all__ = ["create_provider"]
