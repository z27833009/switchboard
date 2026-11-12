"""Build the model provider named in configuration, through the registry."""

from __future__ import annotations

from typing import Any, cast

from switchboard.core.config import ProviderConfig
from switchboard.core.errors import ConfigError
from switchboard.core.ports import ModelProvider
from switchboard.registry import PROVIDERS, Registry


def create_provider(registry: Registry, name: str, config: ProviderConfig) -> ModelProvider:
    """Resolve ``config.adapter`` to an entry point and call its ``from_config``."""
    component: Any = registry.load(PROVIDERS, config.adapter)
    factory = getattr(component, "from_config", None)
    if factory is None:
        raise ConfigError(f"provider adapter {config.adapter!r} has no from_config")
    return cast(ModelProvider, factory(name, config))


__all__ = ["create_provider"]
