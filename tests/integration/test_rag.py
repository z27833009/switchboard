"""Ingestion and retrieval against the compose database and LM Studio.

    uv run --env-file .env pytest tests/integration/test_rag.py -q

Ingests the five synthetic policy documents, proves the second run changes
nothing, and checks the expected source for three queries.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.providers.factory import create_provider
from switchboard.adapters.retrievers.ingest import ingest_directory
from switchboard.adapters.retrievers.pgvector import PgVectorRetriever
from switchboard.agents.retrieval import RetrievalStrategy, retrieve_with_strategy
from switchboard.config import load_settings
from switchboard.core.config import tenant_context
from switchboard.core.models import RetrievalSettings, RunContext
from switchboard.registry import Registry

REPO = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
LMSTUDIO_URL = os.environ.get("LMSTUDIO_BASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (DATABASE_URL and LMSTUDIO_URL),
        reason="set SWITCHBOARD_DATABASE_URL and LMSTUDIO_BASE_URL",
    ),
]

QUERIES = [
    ("What is the return window for electronics?", "returns-policy.md"),
    (
        "When are late deliveries eligible for compensation?",
        "late-delivery-compensation.md",
    ),
    ("How do I start selling on the marketplace?", "seller-onboarding.md"),
]


def context_for(tenant_id: str, settings: Any) -> RunContext:
    return RunContext(
        run_id=f"run-rag-{tenant_id}",
        tenant=tenant_context(tenant_id, settings.tenants),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


async def test_ingest_is_idempotent_and_retrieval_finds_the_expected_sources() -> None:
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        await ingest_directory(pool, embedder, REPO / "data" / "documents", "default")
        second = await ingest_directory(pool, embedder, REPO / "data" / "documents", "default")
        assert second.added == 0
        assert second.updated == 0
        assert second.unchanged == 5
        assert second.chunks_written == 0

        ctx = context_for("default", settings)
        retriever = PgVectorRetriever(pool=pool, embedder=embedder)
        for query, expected_source in QUERIES:
            chunks = await retriever.retrieve(query, ctx, k=5)
            assert chunks, query
            assert chunks[0].source == expected_source, (
                query,
                [(chunk.source, round(chunk.score, 3)) for chunk in chunks],
            )
    finally:
        await pool.close()


async def test_retrieval_is_isolated_per_tenant() -> None:
    """seller_a has its own copy of the corpus and never sees default chunks (AC-11a)."""
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        default_ingest = await ingest_directory(
            pool, embedder, REPO / "data" / "documents", "default"
        )
        seller_ingest = await ingest_directory(
            pool, embedder, REPO / "data" / "documents", "seller_a"
        )
        second = await ingest_directory(pool, embedder, REPO / "data" / "documents", "seller_a")
        # First run on a fresh tenant writes 29 chunks; a later run is a no-op,
        # so the test is repeatable against the same database.
        if seller_ingest.added or seller_ingest.updated:
            assert seller_ingest.chunks_written == 29
        else:
            assert seller_ingest.unchanged == 5
        assert default_ingest.added + default_ingest.updated + default_ingest.unchanged == 5
        assert second.added == 0
        assert second.updated == 0
        assert second.unchanged == 5
        assert second.chunks_written == 0

        default_docs = {
            row["id"]
            for row in await pool.fetch(
                "SELECT id::text AS id FROM public.documents WHERE tenant_id = 'default'"
            )
        }
        seller_docs = {
            row["id"]
            for row in await pool.fetch(
                "SELECT id::text AS id FROM public.documents WHERE tenant_id = 'seller_a'"
            )
        }
        assert len(default_docs) == len(seller_docs) == 5
        assert default_docs.isdisjoint(seller_docs)

        retriever = PgVectorRetriever(pool=pool, embedder=embedder)
        query = QUERIES[0][0]
        default_chunks = await retriever.retrieve(query, context_for("default", settings), k=50)
        seller_chunks = await retriever.retrieve(query, context_for("seller_a", settings), k=50)
        assert default_chunks and seller_chunks

        default_chunk_ids = {chunk.chunk_id for chunk in default_chunks}
        seller_chunk_ids = {chunk.chunk_id for chunk in seller_chunks}
        assert default_chunk_ids.isdisjoint(seller_chunk_ids)
        assert {chunk.document_id for chunk in default_chunks}.isdisjoint(
            {chunk.document_id for chunk in seller_chunks}
        )
        # Every returned chunk belongs to the tenant that asked, not only the
        # top one: check the document set from the database too.
        assert {chunk.document_id for chunk in default_chunks} <= default_docs
        assert {chunk.document_id for chunk in seller_chunks} <= seller_docs
        assert len(default_chunk_ids) == 29
        assert len(seller_chunk_ids) == 29
    finally:
        await pool.close()


async def test_hybrid_search_finds_full_text_matches_and_keeps_the_tenant_filter() -> None:
    """The GIN branch contributes chunks and never crosses a tenant (AC-11a, 9.2)."""
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        await ingest_directory(pool, embedder, REPO / "data" / "documents", "default")
        await ingest_directory(pool, embedder, REPO / "data" / "documents", "seller_a")
        hybrid = PgVectorRetriever(pool=pool, embedder=embedder, search="hybrid")

        for query, expected_source in QUERIES:
            chunks = await hybrid.retrieve(query, context_for("default", settings), k=5)
            assert chunks, query
            assert chunks[0].source == expected_source, (
                query,
                [(chunk.source, round(chunk.score, 3)) for chunk in chunks],
            )

        # "boleto" is a rare term that the full-text branch ranks first even
        # though the embedding alone is less specific.
        boleto = await hybrid.retrieve("boleto", context_for("default", settings), k=5)
        assert f"{boleto[0].source}#{boleto[0].ordinal}" == "payments.md#3"

        default_chunks = await hybrid.retrieve(
            QUERIES[0][0], context_for("default", settings), k=50
        )
        seller_chunks = await hybrid.retrieve(
            QUERIES[0][0], context_for("seller_a", settings), k=50
        )
        assert len(default_chunks) == len(seller_chunks) == 29
        assert {chunk.chunk_id for chunk in default_chunks}.isdisjoint(
            {chunk.chunk_id for chunk in seller_chunks}
        )
    finally:
        await pool.close()


async def test_conditional_rewrite_makes_two_rounds_below_the_threshold() -> None:
    """A high threshold forces the rewrite round and a second retrieval (AC-25a)."""
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    provider = create_provider(Registry(), "lmstudio", settings.providers.providers["lmstudio"])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        await ingest_directory(pool, embedder, REPO / "data" / "documents", "default")
        retriever = PgVectorRetriever(pool=pool, embedder=embedder, search="vector")
        ctx = context_for("default", settings)
        ctx = ctx.model_copy(
            update={
                "tenant": ctx.tenant.model_copy(
                    update={
                        "retrieval": RetrievalSettings(
                            min_score=0.99, rewrite=True, rewrite_mode="conditional"
                        )
                    }
                )
            }
        )
        run = await retrieve_with_strategy(
            retriever,
            provider,
            query=QUERIES[0][0],
            ctx=ctx,
            strategy=RetrievalStrategy(
                name="conditional", search="vector", rewrite_mode="conditional"
            ),
            k=5,
        )
        assert run.rewrite_rounds == 1
        assert run.retrieval_rounds == 2
        assert len(run.queries) == 2
        assert run.chunks
        assert run.usage.input_tokens > 0
    finally:
        await pool.close()
