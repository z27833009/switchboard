"""OpenAI-compatible embedding adapter (LM Studio, DeepSeek, gateways)."""

from __future__ import annotations

from typing import Any

import openai
from openai import AsyncOpenAI
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from switchboard.adapters._openai_errors import map_error
from switchboard.core.config import EmbedderConfig
from switchboard.core.errors import ConfigError, ProviderRequestError
from switchboard.tracing.spans import embeddings_span_name, record_error


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
        tracer: trace.Tracer | None = None,
    ) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.name = name
        self.dimension = dimension
        self._model = model
        self._tracer = tracer or trace.get_tracer("switchboard.embedder.openai_compatible")
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
        with self._tracer.start_as_current_span(
            embeddings_span_name(self._model),
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("gen_ai.operation.name", "embeddings")
            span.set_attribute("gen_ai.system", self.name)
            span.set_attribute("gen_ai.request.model", self._model)
            span.set_attribute("switchboard.embeddings.inputs", len(texts))
            span.set_attribute("switchboard.embeddings.dimension", self.dimension)
            try:
                vectors = await self._embed(texts)
            except Exception as exc:
                record_error(span, exc)
                raise
            return vectors

    async def _embed(self, texts: list[str]) -> list[list[float]]:
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
