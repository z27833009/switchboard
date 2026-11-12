"""Contract tests for the mock and OpenAI-compatible embedders."""

from types import SimpleNamespace
from typing import Any, cast

import openai
import pytest

from switchboard.adapters._openai_errors import map_error
from switchboard.adapters.embedders.mock import MockEmbedder
from switchboard.adapters.embedders.openai_compat import OpenAICompatibleEmbedder
from switchboard.core.config import EmbedderConfig
from switchboard.core.errors import (
    ConfigError,
    ProviderRequestError,
    ProviderTransientError,
)


async def test_mock_embedder_is_deterministic_and_fixed_dimension() -> None:
    embedder = MockEmbedder(dimension=8)
    first = await embedder.embed(["hello", "hello", "world"])
    assert first[0] == first[1]
    assert first[0] != first[2]
    assert all(len(vector) == 8 for vector in first)
    assert first == await embedder.embed(["hello", "hello", "world"])


async def test_mock_embedder_returns_empty_for_no_input() -> None:
    assert await MockEmbedder(dimension=8).embed([]) == []


class FakeEmbeddings:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=vector)
                for index, vector in enumerate(self.vectors)
            ]
        )


def embedder_with(embeddings: FakeEmbeddings, *, dimension: int = 3) -> OpenAICompatibleEmbedder:
    client = SimpleNamespace(embeddings=embeddings)
    return OpenAICompatibleEmbedder(
        name="lmstudio",
        model="bge-m3",
        base_url="http://test.invalid/v1",
        api_key="test",
        dimension=dimension,
        client=client,
    )


async def test_openai_embedder_orders_vectors_by_index() -> None:
    fake = FakeEmbeddings([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    embedder = embedder_with(fake)
    vectors = await embedder.embed(["a", "b"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert fake.calls[0]["model"] == "bge-m3"
    assert fake.calls[0]["input"] == ["a", "b"]


async def test_openai_embedder_rejects_a_wrong_dimension() -> None:
    fake = FakeEmbeddings([[0.1, 0.2]])
    with pytest.raises(ProviderRequestError, match="dimension"):
        await embedder_with(fake, dimension=3).embed(["a"])


async def test_openai_embedder_rejects_a_wrong_count() -> None:
    fake = FakeEmbeddings([[0.1, 0.2, 0.3]])
    with pytest.raises(ProviderRequestError, match="vectors"):
        await embedder_with(fake).embed(["a", "b"])


async def test_openai_embedder_maps_transport_errors() -> None:
    class Failing:
        async def create(self, **kwargs: Any) -> Any:
            raise openai.APITimeoutError(request=cast(Any, None))

    embedder = OpenAICompatibleEmbedder(
        name="lmstudio",
        model="bge-m3",
        base_url="http://test.invalid/v1",
        api_key="test",
        dimension=3,
        client=SimpleNamespace(embeddings=Failing()),
    )
    with pytest.raises(ProviderTransientError):
        await embedder.embed(["a"])


def test_embedder_from_config_requires_url_and_model() -> None:
    config = EmbedderConfig(
        adapter="openai_compatible",
        base_url="http://test.invalid/v1",
        model="bge-m3",
        dimension=1024,
    )
    embedder = OpenAICompatibleEmbedder.from_config("lmstudio", config)
    assert embedder.dimension == 1024
    with pytest.raises(ConfigError, match="base_url"):
        OpenAICompatibleEmbedder.from_config(
            "lmstudio", EmbedderConfig(adapter="openai_compatible", dimension=1024)
        )


def test_mock_embedder_from_config_uses_the_dimension() -> None:
    embedder = MockEmbedder.from_config("mock", EmbedderConfig(adapter="mock", dimension=1024))
    assert embedder.dimension == 1024


def test_map_error_is_shared_by_embedders() -> None:
    assert isinstance(
        map_error(openai.APITimeoutError(request=cast(Any, None))),
        ProviderTransientError,
    )
