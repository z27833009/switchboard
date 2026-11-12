"""OpenAI-compatible embedding adapter (LM Studio, DeepSeek, gateways)."""

from __future__ import annotations

from typing import Any

import openai
from openai import AsyncOpenAI

from switchboard.adapters._openai_errors import map_error
from switchboard.core.config import EmbedderConfig
from switchboard.core.errors import ConfigError, ProviderRequestError


class OpenAICompatibleEmbedder:
    name: str
    dimension: int

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str,
        dimension: int,
        timeout_s: float = 120.0,
        client: Any | None = None,
    ) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.name = name
        self.dimension = dimension
        self._model = model
        self._client: Any = client or AsyncOpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s
        )

    @classmethod
    def from_config(cls, name: str, config: EmbedderConfig) -> OpenAICompatibleEmbedder:
        if not config.base_url or not config.model:
            raise ConfigError(f"embedder {name!r} needs base_url and model")
        return cls(
            name=name,
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key or "lm-studio",
            dimension=config.dimension,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.embeddings.create(model=self._model, input=texts)
        except openai.OpenAIError as exc:
            raise map_error(exc) from exc

        items = sorted(response.data, key=lambda item: item.index)
        vectors = [[float(value) for value in item.embedding] for item in items]
        if len(vectors) != len(texts):
            raise ProviderRequestError(
                f"embedder returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        for vector in vectors:
            if len(vector) != self.dimension:
                raise ProviderRequestError(
                    f"embedder returned dimension {len(vector)}, expected {self.dimension}"
                )
        return vectors
