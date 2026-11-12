"""Build the embedder named in configuration."""

from __future__ import annotations

from switchboard.adapters.embedders.mock import MockEmbedder
from switchboard.adapters.embedders.openai_compat import OpenAICompatibleEmbedder
from switchboard.core.config import EmbedderConfig
from switchboard.core.errors import ConfigError
from switchboard.core.ports import Embedder


def create_embedder(name: str, config: EmbedderConfig) -> Embedder:
    if config.adapter == "mock":
        return MockEmbedder.from_config(name, config)
    if config.adapter == "openai_compatible":
        return OpenAICompatibleEmbedder.from_config(name, config)
    raise ConfigError(f"unknown embedder adapter {config.adapter!r}")
