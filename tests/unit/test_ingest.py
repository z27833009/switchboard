"""Deterministic chunking and embedding adapters."""

from typing import Any, cast

import pytest

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.embedders.mock import MockEmbedder
from switchboard.adapters.retrievers.ingest import (
    chunk_document,
    content_hash,
    document_title,
    split_words,
    vector_literal,
)
from switchboard.adapters.retrievers.pgvector import PgVectorRetriever
from switchboard.core.config import EmbedderConfig
from switchboard.core.errors import ConfigError, RetrievalError
from switchboard.core.models import RunContext, TenantContext
from switchboard.registry import RETRIEVERS, Registry


def test_headings_split_the_document() -> None:
    content = "# Title\n\nIntro.\n\n## First\n\nBody one.\n\n## Second\n\nBody two."
    chunks = chunk_document(content)
    assert len(chunks) == 3
    assert "Intro." in chunks[0]
    assert chunks[1].startswith("## First")
    assert chunks[2].startswith("## Second")


def test_short_sections_are_single_chunks() -> None:
    chunks = chunk_document("# A\n\none two three")
    assert chunks == ["# A one two three"]


def test_long_sections_overlap() -> None:
    words = [f"w{index}" for index in range(1000)]
    chunks = split_words(" ".join(words), max_words=512, overlap=64)
    assert len(chunks) == 3
    assert len(chunks[0].split()) == 512
    first = chunks[0].split()
    second = chunks[1].split()
    assert first[-64:] == second[:64]


def test_empty_content_has_no_chunks() -> None:
    assert chunk_document("") == []
    assert chunk_document("   \n\n") == []


def test_title_comes_from_the_first_heading() -> None:
    assert document_title("# Returns policy\n\nbody", "fallback") == "Returns policy"
    assert document_title("no heading", "fallback") == "fallback"


def test_content_hash_is_stable() -> None:
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("hello!")


def test_vector_literal_is_json() -> None:
    assert vector_literal([0.5, -1.0]) == "[0.5,-1.0]"


def test_embedder_factory_chooses_the_adapter() -> None:
    mock = create_embedder("mock", EmbedderConfig(adapter="mock", dimension=8))
    assert isinstance(mock, MockEmbedder)
    with pytest.raises(ConfigError, match="unknown embedder adapter"):
        create_embedder("x", EmbedderConfig(adapter="nope", dimension=8))


def tenant_context() -> RunContext:
    tenant = TenantContext(
        tenant_id="default",
        allowed_agents={"docs"},
        allowed_tools=set(),
        provider="lmstudio",
    )
    return RunContext(
        run_id="run-1",
        tenant=tenant,
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


class WrongDimensionEmbedder:
    name = "mock"
    dimension = 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


async def test_retriever_rejects_a_wrong_dimension_before_hitting_the_database() -> None:
    retriever = PgVectorRetriever(pool=cast(Any, object()), embedder=WrongDimensionEmbedder())
    with pytest.raises(RetrievalError, match="dimension"):
        await retriever.retrieve("query", tenant_context())


def test_registry_resolves_the_retriever_entry_point() -> None:
    assert Registry().load(RETRIEVERS, "pgvector") is PgVectorRetriever
