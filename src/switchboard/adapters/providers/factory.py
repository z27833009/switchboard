"""Build the model provider named in configuration, through the registry.

The price row is resolved here, from ``config/prices.yaml``, so the adapter gets
the one piece of pricing it needs and the factory stays the composition root for
providers (design.md sections 8.1 and 12.4).
"""

from __future__ import annotations

from typing import Any, cast

from switchboard.core.config import PriceEntry, ProviderConfig
from switchboard.core.errors import ConfigError
from switchboard.core.ports import ModelProvider
from switchboard.registry import PROVIDERS, Registry


def create_provider(
    registry: Registry,
    name: str,
    config: ProviderConfig,
    price: PriceEntry | None = None,
) -> ModelProvider:
    """Resolve ``config.adapter`` to an entry point and call its ``from_config``."""
    component: Any = registry.load(PROVIDERS, config.adapter)
    factory = getattr(component, "from_config", None)
    if factory is None:
        raise ConfigError(f"provider adapter {config.adapter!r} has no from_config")
    return cast(ModelProvider, factory(name, config, price))


__all__ = ["create_provider"]
