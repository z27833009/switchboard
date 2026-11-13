"""Semantic cache against the compose database and bge-m3.

    uv run --env-file .env pytest tests/integration/test_cache_integration.py -q

Covers the parts a fake pool cannot: the real pgvector cosine operator, the TTL
filter, tenant isolation, the threshold boundary and invalidation by a real
re-ingest that bumps the tenant's document index version. Every row these tests
write is removed afterwards.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from tests.fakes import MemoryRunStore, MemorySpanExporter

from switchboard.adapters.cache.pg_semantic import PgSemanticCache
from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.adapters.retrievers.ingest import ingest_directory
from switchboard.adapters.retrievers.pgvector import PgVectorRetriever
from switchboard.agents.docs import DocsAgent
from switchboard.config import load_settings
from switchboard.core.cache import normalize_query
from switchboard.core.config import Limits, Settings, tenant_context
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.models import (
    AgentResult,
    CachedAnswer,
    CacheSettings,
    ChatRequest,
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
    RetrievalSettings,
    RunContext,
    Usage,
)
from switchboard.registry import AGENTS, Registry

REPO = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
LMSTUDIO_URL = os.environ.get("LMSTUDIO_BASE_URL", "")
PREFIX = "cache integration test"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (DATABASE_URL and LMSTUDIO_URL),
        reason="set SWITCHBOARD_DATABASE_URL and LMSTUDIO_BASE_URL",
    ),
]


def context_for(tenant_id: str, settings: Settings) -> RunContext:
    ctx = RunContext(
        run_id=f"cache-it-{uuid.uuid4()}",
        tenant=tenant_context(tenant_id, settings.tenants),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )
    return ctx.model_copy(
        update={
            "tenant": ctx.tenant.model_copy(
                update={"cache": CacheSettings(enabled=True, threshold=0.92, ttl_days=7)}
            )
        }
    )


def answer(text: str) -> CachedAnswer:
    return CachedAnswer(
        plan="docs",
        answer=AgentResult(
            answer=text,
            usage=Usage(input_tokens=10, output_tokens=4),
        ),
    )


async def cleanup(pool: asyncpg.Pool, tenant_id: str) -> None:
    await pool.execute(
        "DELETE FROM public.cache_entries WHERE tenant_id = $1 AND query_text LIKE $2",
        tenant_id,
        f"{PREFIX}%",
    )


async def test_paraphrase_hit_threshold_boundary_and_ttl() -> None:
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        ctx = context_for("default", settings)
        cache = PgSemanticCache(pool=pool, embedder=embedder)
        await cleanup(pool, "default")
        stored_question = f"{PREFIX}: what is the return window for electronics?"
        paraphrase = f"{PREFIX}: how long do I have to return an electronic item?"

        key = await cache.key_for(ctx, stored_question)
        await cache.store(key, answer("Electronics can be returned within 15 days [d1]."))

        hit = await cache.lookup(key, threshold=0.92)
        assert hit is not None, "the exact query must hit"
        assert hit.similarity == pytest.approx(1.0)
        assert hit.entry.plan == "docs"
        assert hit.entry.answer.answer.startswith("Electronics")
        row = await pool.fetchrow(
            "SELECT hits FROM public.cache_entries WHERE tenant_id = 'default' AND query_text = $1",
            normalize_query(stored_question),
        )
        assert row is not None and row["hits"] == 1

        paraphrase_key = await cache.key_for(ctx, paraphrase)
        assert paraphrase_key.embedding != key.embedding
        paraphrase_hit = await cache.lookup(paraphrase_key, threshold=0.80)
        assert paraphrase_hit is not None, "a paraphrase at or above 0.80 must hit"
        assert paraphrase_hit.similarity >= 0.80
        assert await cache.lookup(key, threshold=0.99) is not None
        assert await cache.lookup(paraphrase_key, threshold=0.99) is None

        # A TTL in the past makes the entry invisible without deleting it.
        await pool.execute(
            "UPDATE public.cache_entries SET expires_at = now() - interval '1 day' "
            "WHERE tenant_id = 'default' AND query_text = $1",
            normalize_query(stored_question),
        )
        assert await cache.lookup(key, threshold=0.92) is None
    finally:
        await cleanup(pool, "default")
        await pool.close()


async def test_a_hit_is_tenant_scoped() -> None:
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        default_ctx = context_for("default", settings)
        seller_ctx = context_for("seller_a", settings)
        cache = PgSemanticCache(pool=pool, embedder=embedder)
        await cleanup(pool, "default")
        await cleanup(pool, "seller_a")
        question = f"{PREFIX}: what is the standard return window?"
        key = await cache.key_for(default_ctx, question)
        await cache.store(key, answer("The standard return window is 30 days [d1]."))

        assert await cache.lookup(key, threshold=0.92) is not None
        seller_key = await cache.key_for(seller_ctx, question)
        assert seller_key.tenant_id == "seller_a"
        assert await cache.lookup(seller_key, threshold=0.0) is None
    finally:
        await cleanup(pool, "default")
        await cleanup(pool, "seller_a")
        await pool.close()


async def test_reingest_invalidates_the_tenant_entries() -> None:
    """A real re-ingest bumps the version, so an old entry stops matching."""
    tenant_id = "cache_integration"
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        await pool.execute(
            "INSERT INTO public.tenants (id, name) VALUES ($1, 'Cache integration') "
            "ON CONFLICT (id) DO NOTHING",
            tenant_id,
        )
        await cleanup(pool, tenant_id)
        with tempfile.TemporaryDirectory(prefix="switchboard-cache-ingest-") as directory:
            documents = Path(directory)
            document = documents / "policy.md"
            document.write_text(
                "# Cache integration policy\n\nThe cache integration return window is 10 days.\n",
                encoding="utf-8",
            )
            first = await ingest_directory(pool, embedder, documents, tenant_id)
            assert first.added == 1
            version_one = first.index_version
            base_ctx = context_for("default", settings)
            ctx = base_ctx.model_copy(
                update={"tenant": base_ctx.tenant.model_copy(update={"tenant_id": tenant_id})}
            )
            cache = PgSemanticCache(pool=pool, embedder=embedder)
            question = f"{PREFIX}: what is the cache integration return window?"
            key = await cache.key_for(ctx, question)
            assert key.index_version == version_one
            await cache.store(key, answer("It is 10 days [d1]."))
            assert await cache.lookup(key, threshold=0.92) is not None

            document.write_text(
                "# Cache integration policy\n\nThe cache integration return window is 20 days.\n",
                encoding="utf-8",
            )
            second = await ingest_directory(pool, embedder, documents, tenant_id)
            assert second.updated == 1
            assert second.index_version == version_one + 1

            # The old key carries the old version, so it cannot match.
            assert await cache.lookup(key, threshold=0.0) is None
            new_key = await cache.key_for(ctx, question)
            assert new_key.index_version == version_one + 1
            assert await cache.lookup(new_key, threshold=0.92) is None
            await cache.store(new_key, answer("It is 20 days [d1]."))
            old_rows = await pool.fetchval(
                "SELECT count(*) FROM public.cache_entries "
                "WHERE tenant_id = $1 AND index_version = $2",
                tenant_id,
                version_one,
            )
            assert old_rows == 0, "the next store must delete older index versions"
            assert await cache.lookup(new_key, threshold=0.92) is not None
    finally:
        await cleanup(pool, tenant_id)
        await pool.execute("DELETE FROM public.documents WHERE tenant_id = $1", tenant_id)
        await pool.execute("DELETE FROM public.tenants WHERE id = $1", tenant_id)
        await pool.close()


async def test_the_ingest_cli_bumps_the_version_used_by_the_cache() -> None:
    """A no-op ingest does not bump; the cache key keeps its version."""
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        cache = PgSemanticCache(pool=pool, embedder=embedder)
        ctx = context_for("default", settings)
        first = await cache.key_for(ctx, f"{PREFIX}: version probe one")
        await ingest_directory(pool, embedder, REPO / "data" / "documents", "default")
        second = await cache.key_for(ctx, f"{PREFIX}: version probe two")
        assert second.index_version == first.index_version
    finally:
        await pool.close()


@dataclass
class RegistryEntry:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class ScriptedDocsProvider:
    """A model that plans docs and cites the first chunk; counts every call."""

    name = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=False,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        usage = Usage(input_tokens=20, output_tokens=6)
        if request.response_schema is not None:
            return CompletionResponse(
                text=None,
                parsed={"agents": ["docs"], "reason": "scripted docs plan"},
                usage=usage,
                model="scripted",
                provider="scripted",
                latency_ms=1,
            )
        return CompletionResponse(
            text="The policy documents explain the return window [d1].",
            usage=usage,
            model="scripted",
            provider="scripted",
            latency_ms=1,
        )


def orchestrator_context() -> RunContext:
    ctx = context_for("default", load_settings(REPO / "config"))
    return ctx.model_copy(
        update={
            "tenant": ctx.tenant.model_copy(
                update={
                    "retrieval": RetrievalSettings(rewrite=False),
                }
            )
        }
    )


async def test_a_real_cache_hit_makes_no_model_call() -> None:
    """AC-10d through the real adapter: the second run skips the supervisor."""
    settings = load_settings(REPO / "config")
    embedder_name = settings.platform.embedder
    embedder = create_embedder(embedder_name, settings.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    try:
        await cleanup(pool, "default")
        cache = PgSemanticCache(pool=pool, embedder=embedder)
        retriever = PgVectorRetriever(pool=pool, embedder=embedder, search="hybrid")
        provider = ScriptedDocsProvider()
        store = MemoryRunStore()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(store)))
        orchestrator = LangGraphOrchestrator(
            provider=provider,
            registry=Registry([RegistryEntry("docs", AGENTS, DocsAgent)]),
            context_policy=ClearOldestToolResults(),
            limits=Limits(run_timeout_s=30.0),
            embedder=embedder,
            tool_sources=[],
            retriever=retriever,
            cache=cache,
            tracer=tracer_provider.get_tracer("switchboard.test.cache.integration"),
            price=None,
        )
        ctx = orchestrator_context()
        question = f"{PREFIX} {uuid.uuid4().hex}: what is the return window for electronics?"
        request = ChatRequest(query=question, tenant_id="default")

        first = await orchestrator.run(request, ctx)
        assert first.cache_hit is False
        assert first.status == "ok"
        calls_after_first = provider.calls
        assert calls_after_first > 0
        stored = await pool.fetchval(
            "SELECT count(*) FROM public.cache_entries "
            "WHERE tenant_id = 'default' AND query_text = $1",
            normalize_query(question),
        )
        assert stored == 1

        second = await orchestrator.run(request, ctx)
        assert second.cache_hit is True
        assert provider.calls == calls_after_first, "a hit must make no model call"
        assert second.status == "ok"
        assert second.plan is not None and second.plan.label == "docs"
        assert second.answer is not None
        assert second.usage.input_tokens == 0
        lookup_spans = [row for row in store.spans if row["name"] == "cache.lookup"]
        assert lookup_spans
        assert any(row["attributes"].get("switchboard.cache.hit") for row in lookup_spans)
        store_spans = [row for row in store.spans if row["name"] == "cache.store"]
        assert store_spans
    finally:
        await cleanup(pool, "default")
        await pool.close()
