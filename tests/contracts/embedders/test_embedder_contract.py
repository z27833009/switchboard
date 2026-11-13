"""Embedder contract: OpenAI-compatible (fake client) and deterministic mock.

The shared cases cover the part every embedder must honour: a fixed
``dimension``, one vector per input in input order, determinism and an empty
input. The OpenAI-compatible adapter adds the error cases: a wrong vector count,
a wrong dimension and a transport error mapped to the core taxonomy. The mock
has no network, so it cannot fail that way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import openai
import pytest

from switchboard.adapters.embedders.mock import MockEmbedder
from switchboard.adapters.embedders.openai_compat import OpenAICompatibleEmbedder
from switchboard.core.errors import ProviderRequestError, ProviderTransientError
from switchboard.core.ports import Embedder
from switchboard.registry import EMBEDDERS, Registry


class FakeEmbeddings:
    """A deterministic client; the vector depends on the input index only."""

    def __init__(
        self,
        *,
        dimension: int = 4,
        count: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.dimension = dimension
        self.count = count
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        texts = kwargs["input"]
        count = len(texts) if self.count is None else self.count
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    index=index,
                    embedding=[float(index + step) / 10 for step in range(self.dimension)],
                )
                for index in range(count)
            ]
        )


@dataclass(frozen=True)
class EmbedderContract:
    label: str
    embedder: Embedder


def build_openai_contract() -> EmbedderContract:
    return EmbedderContract(
        label="openai_compatible",
        embedder=OpenAICompatibleEmbedder(
            name="lmstudio",
            model="bge-m3",
            base_url="http://test.invalid/v1",
            api_key="test",
            dimension=4,
            client=SimpleNamespace(embeddings=FakeEmbeddings(dimension=4)),
        ),
    )


def build_mock_contract() -> EmbedderContract:
    return EmbedderContract(
        label="mock",
        embedder=MockEmbedder(dimension=4),
    )


BUILDERS = (build_openai_contract, build_mock_contract)


@pytest.fixture(params=BUILDERS, ids=["openai_compatible", "mock"])
def contract(request: pytest.FixtureRequest) -> EmbedderContract:
    builder = cast(Callable[[], EmbedderContract], request.param)
    return builder()


def test_registered_embedder_adapters_have_a_contract_case() -> None:
    assert Registry().names(EMBEDDERS) == ["mock", "openai_compatible"]


async def test_one_vector_per_input_with_the_declared_dimension(
    contract: EmbedderContract,
) -> None:
    texts = ["return window", "late delivery", "seller onboarding"]
    vectors = await contract.embedder.embed(texts)
    assert len(vectors) == len(texts)
    assert all(len(vector) == contract.embedder.dimension for vector in vectors)
    assert contract.embedder.dimension > 0
    assert contract.embedder.name


async def test_embedding_is_deterministic(contract: EmbedderContract) -> None:
    first = await contract.embedder.embed(["return window"])
    second = await contract.embedder.embed(["return window"])
    assert first == second


async def test_order_is_preserved(contract: EmbedderContract) -> None:
    both = await contract.embedder.embed(["return window", "late delivery"])
    alone = await contract.embedder.embed(["return window"])
    assert both[0] == alone[0]
    assert both[1] != both[0]


async def test_an_empty_input_makes_no_call(contract: EmbedderContract) -> None:
    assert await contract.embedder.embed([]) == []


async def test_openai_compatible_errors_use_the_core_taxonomy() -> None:
    wrong_count = OpenAICompatibleEmbedder(
        name="lmstudio",
        model="bge-m3",
        base_url="http://test.invalid/v1",
        api_key="test",
        dimension=4,
        client=SimpleNamespace(embeddings=FakeEmbeddings(count=1)),
    )
    with pytest.raises(ProviderRequestError, match="vectors"):
        await wrong_count.embed(["one", "two"])

    wrong_dimension = OpenAICompatibleEmbedder(
        name="lmstudio",
        model="bge-m3",
        base_url="http://test.invalid/v1",
        api_key="test",
        dimension=8,
        client=SimpleNamespace(embeddings=FakeEmbeddings(dimension=4)),
    )
    with pytest.raises(ProviderRequestError, match="dimension"):
        await wrong_dimension.embed(["one"])

    failing = OpenAICompatibleEmbedder(
        name="lmstudio",
        model="bge-m3",
        base_url="http://test.invalid/v1",
        api_key="test",
        dimension=4,
        client=SimpleNamespace(
            embeddings=FakeEmbeddings(error=openai.APITimeoutError(request=cast(Any, None)))
        ),
    )
    with pytest.raises(ProviderTransientError):
        await failing.embed(["one"])


def test_mock_rejects_a_zero_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        MockEmbedder(dimension=0)
