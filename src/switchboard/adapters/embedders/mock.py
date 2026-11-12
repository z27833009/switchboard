"""Deterministic embedder for tests and CI."""

from __future__ import annotations

import hashlib

from switchboard.core.config import EmbedderConfig


class MockEmbedder:
    name = "mock"

    def __init__(self, *, name: str = "mock", dimension: int = 1024) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.name = name
        self.dimension = dimension

    @classmethod
    def from_config(cls, name: str, config: EmbedderConfig) -> MockEmbedder:
        return cls(name=name, dimension=config.dimension)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            values.extend((byte / 127.5) - 1.0 for byte in block)
            counter += 1
        return values[: self.dimension]
