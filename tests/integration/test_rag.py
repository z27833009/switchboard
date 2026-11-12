"""Ingestion and retrieval against the compose database and LM Studio.

    uv run --env-file .env pytest tests/integration/test_rag.py -q

Ingests the five synthetic policy documents, proves the second run changes
nothing, and checks the expected source for three queries.
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.retrievers.ingest import ingest_directory
from switchboard.adapters.retrievers.pgvector import PgVectorRetriever
from switchboard.config import load_settings
from switchboard.core.config import tenant_context
from switchboard.core.models import RunContext

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

        tenant = tenant_context("default", settings.tenants)
        ctx = RunContext(
            run_id="run-rag-test",
            tenant=tenant,
            run_token="unused",
            trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        )
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
