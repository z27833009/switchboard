"""Retriever contract: in-memory implementation and pgvector on the real corpus.

The shared cases are the ones a caller can rely on from any retriever: a bounded
result set of well-formed chunks, a deterministic order and a named error for an
invalid ``k``. The pgvector adapter is exercised against the compose database and
the configured embedder, so those cases are marked integration and skip when
``SWITCHBOARD_DATABASE_URL`` or ``LMSTUDIO_BASE_URL`` is absent. CI does not run
them; the Phase 1 and v1 reports already cover the live path.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from tests.contracts.support import (
    CONFIG_DIR,
    REPO_ROOT,
    InMemoryRetriever,
    chunk,
    run_context,
)

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.retrievers.ingest import ingest_directory
from switchboard.adapters.retrievers.pgvector import PgVectorRetriever
from switchboard.config import load_settings
from switchboard.core.errors import RetrievalError
from switchboard.core.models import RetrievedChunk
from switchboard.core.ports import Retriever

DATABASE_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
LMSTUDIO_URL = os.environ.get("LMSTUDIO_BASE_URL", "")
QUESTION = "What is the return window for electronics?"
NEEDS_STACK = pytest.mark.skipif(
    not (DATABASE_URL and LMSTUDIO_URL),
    reason="set SWITCHBOARD_DATABASE_URL and LMSTUDIO_BASE_URL",
)


def contract_retriever() -> Retriever:
    return InMemoryRetriever([chunk(0), chunk(1, source="payments.md"), chunk(2)])


async def check_retriever_contract(retriever: Retriever, *, k: int = 5) -> list[RetrievedChunk]:
    ctx = run_context()
    first = await retriever.retrieve(QUESTION, ctx, k=k)
    second = await retriever.retrieve(QUESTION, ctx, k=k)
    assert first == second
    assert len(first) <= k
    for item in first:
        assert isinstance(item, RetrievedChunk)
        assert item.chunk_id
        assert item.document_id
        assert item.source
        assert isinstance(item.text, str)
        assert isinstance(item.score, float)
        assert item.ordinal >= 0
    return first


async def test_the_shared_cases_run_against_the_in_memory_retriever() -> None:
    hits = await check_retriever_contract(contract_retriever(), k=2)
    assert len(hits) == 2


async def test_rank_order_and_round_are_stable() -> None:
    retriever = contract_retriever()
    ctx = run_context()
    first = await retriever.retrieve(QUESTION, ctx, k=3, round=1)
    again = await retriever.retrieve(QUESTION, ctx, k=3, round=2)
    assert [item.chunk_id for item in first] == [item.chunk_id for item in again]
    assert first[0].score >= first[-1].score


async def test_an_invalid_k_is_a_retrieval_error() -> None:
    retriever = contract_retriever()
    with pytest.raises(RetrievalError):
        await retriever.retrieve(QUESTION, run_context(), k=0)


async def test_an_empty_index_returns_no_chunks() -> None:
    retriever = InMemoryRetriever([])
    assert await retriever.retrieve(QUESTION, run_context()) == []


def test_the_pgvector_adapter_is_the_registered_retriever() -> None:
    from switchboard.registry import RETRIEVERS, Registry

    assert Registry().names(RETRIEVERS) == ["pgvector"]
    assert Registry().load(RETRIEVERS, "pgvector") is PgVectorRetriever


@pytest.mark.integration
@NEEDS_STACK
async def test_the_pgvector_retriever_passes_the_shared_contract() -> None:
    settings = load_settings(CONFIG_DIR)
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        await ingest_directory(pool, embedder, REPO_ROOT / "data" / "documents", "default")
        retriever = PgVectorRetriever(pool=pool, embedder=embedder)
        hits = await check_retriever_contract(retriever, k=5)
        assert hits, "the default tenant has no chunks; run `switchboard ingest` first"
        assert hits[0].source == "returns-policy.md"
        with pytest.raises(RetrievalError):
            await retriever.retrieve(QUESTION, run_context(), k=0)
        with pytest.raises(RetrievalError):
            await retriever.retrieve(QUESTION, run_context(), k=51)
    finally:
        await pool.close()
