"""Semantic cache adapter: unit tests with a fake pool and fake embedder.

The database is not involved: ``FakePool`` records the SQL the adapter issues and
returns a row shaped like the real query. Integration behaviour (real pgvector,
TTL, tenant isolation, re-ingest) is in ``tests/integration/test_cache_integration.py``.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from tests.fakes import MemoryRunStore, MemorySpanExporter

from switchboard.adapters.cache.pg_semantic import (
    VECTOR_DIMENSION,
    PgSemanticCache,
    cosine_similarity,
    vector_literal,
)
from switchboard.core.cache import agents_from_label, normalize_query
from switchboard.core.errors import CacheError, ConfigError
from switchboard.core.models import (
    AgentResult,
    CachedAnswer,
    CacheKey,
    RunContext,
    TenantContext,
    Usage,
)
from switchboard.tracing.spans import cache_lookup_span, cache_store_span

RUN_ID = "11111111-2222-3333-4444-555555555555"


class FakeEmbedder:
    name = "fake"
    dimension = VECTOR_DIMENSION

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("embedder down")
        return [[0.01] * VECTOR_DIMENSION for _ in texts]


class FakeTransaction:
    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeConnection:
    def __init__(self, pool: FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(self, sql: str, *args: Any) -> str:
        self._pool.executed.append((sql, args))
        return "OK"


class FakePool:
    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        version: int = 7,
        count: int = 3,
        fail: bool = False,
    ) -> None:
        self.row = row
        self.version = version
        self.count = count
        self.fail = fail
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetched.append((sql, args))
        if self.fail:
            raise RuntimeError("database down")
        if "doc_index_version" in sql:
            return self.version
        return self.count

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.fetched.append((sql, args))
        if self.fail:
            raise RuntimeError("database down")
        return self.row

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        if self.fail:
            raise RuntimeError("database down")
        return "UPDATE 1"

    def acquire(self) -> FakeConnection:
        return FakeConnection(self)


def ctx() -> RunContext:
    return RunContext(
        run_id="run-cache-1",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents={"docs"},
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def key(index_version: int = 7) -> CacheKey:
    return CacheKey(
        tenant_id="default",
        normalized_query="what is the return window?",
        embedding=[0.01] * VECTOR_DIMENSION,
        index_version=index_version,
    )


def answer() -> CachedAnswer:
    return CachedAnswer(
        plan="docs",
        answer=AgentResult(
            answer="Returns take 15 calendar days [d1].",
            usage=Usage(input_tokens=10, output_tokens=5),
        ),
    )


def row(similarity: float) -> dict[str, Any]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "plan": "docs",
        "answer": answer().answer.model_dump_json(),
        "index_version": 7,
        "similarity": similarity,
    }


def test_normalize_query_and_label_helpers() -> None:
    assert normalize_query("  What  is the RETURN window? ") == "what is the return window?"
    assert agents_from_label("data+docs") == ["data", "docs"]
    assert agents_from_label("docs") == ["docs"]


def test_cosine_similarity_is_clamped_and_deterministic() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([-1.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [1.0, 2.0])


def test_vector_literal_is_a_pgvector_array() -> None:
    assert vector_literal([0.5, -1.0]) == "[0.5,-1.0]"


def test_the_adapter_requires_the_1024_dimension_column() -> None:
    class Small:
        dimension = 8

    with pytest.raises(ConfigError, match="1024"):
        PgSemanticCache(pool=cast(Any, FakePool()), embedder=Small())


async def test_key_for_embeds_and_reads_the_index_version() -> None:
    pool = FakePool(version=4)
    embedder = FakeEmbedder()
    cache = PgSemanticCache(pool=cast(Any, pool), embedder=embedder)

    built = await cache.key_for(ctx(), "What is the RETURN window?")

    assert built.tenant_id == "default"
    assert built.normalized_query == "what is the return window?"
    assert len(built.embedding) == VECTOR_DIMENSION
    assert built.index_version == 4
    assert embedder.calls == [["What is the RETURN window?"]]


async def test_key_for_maps_an_embedding_failure_to_cache_error() -> None:
    cache = PgSemanticCache(pool=cast(Any, FakePool()), embedder=FakeEmbedder(fail=True))
    with pytest.raises(CacheError, match="embedding"):
        await cache.key_for(ctx(), "hello")


async def test_lookup_misses_when_no_entry_is_near_enough() -> None:
    pool = FakePool(row=row(0.919))
    cache = PgSemanticCache(pool=cast(Any, pool), embedder=FakeEmbedder())

    assert await cache.lookup(key(), threshold=0.92) is None
    assert pool.executed == []


async def test_lookup_hits_at_the_threshold_boundary_and_counts_the_hit() -> None:
    pool = FakePool(row=row(0.92))
    cache = PgSemanticCache(pool=cast(Any, pool), embedder=FakeEmbedder())

    hit = await cache.lookup(key(), threshold=0.92)

    assert hit is not None
    assert hit.similarity == pytest.approx(0.92)
    assert hit.entry.plan == "docs"
    assert hit.entry.answer.answer.startswith("Returns")
    assert any("hits = hits + 1" in sql for sql, _args in pool.executed)


async def test_lookup_records_similarity_and_threshold_on_the_span() -> None:
    store = MemoryRunStore()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(store)))
    tracer = provider.get_tracer("switchboard.test.cache")
    cache = PgSemanticCache(pool=cast(Any, FakePool(row=row(0.93))), embedder=FakeEmbedder())

    with cache_lookup_span(tracer, run_id=RUN_ID, threshold=0.92):
        assert await cache.lookup(key(), threshold=0.92) is not None

    span = next(span for span in store.spans if span["name"] == "cache.lookup")
    assert span["attributes"]["switchboard.cache.hit"] is True
    assert span["attributes"]["switchboard.cache.threshold"] == 0.92
    assert span["attributes"]["switchboard.cache.similarity"] == pytest.approx(0.93)
    assert span["run_id"] == RUN_ID


async def test_lookup_maps_a_database_failure_to_cache_error() -> None:
    cache = PgSemanticCache(pool=cast(Any, FakePool(fail=True)), embedder=FakeEmbedder())
    with pytest.raises(CacheError, match="lookup"):
        await cache.lookup(key(), threshold=0.92)


async def test_store_prunes_and_inserts_with_the_ttl() -> None:
    pool = FakePool()
    cache = PgSemanticCache(pool=cast(Any, pool), embedder=FakeEmbedder())

    with cache_store_span(
        trace.get_tracer("switchboard.test.cache"),
        run_id="run-cache-1",
        plan="docs",
        ttl_days=3,
    ):
        await cache.store(key(), answer(), ttl_days=3)

    assert len(pool.executed) == 2
    assert "DELETE FROM public.cache_entries" in pool.executed[0][0]
    insert_sql, insert_args = pool.executed[1]
    assert "INSERT INTO public.cache_entries" in insert_sql
    assert insert_args[0] == "default"
    assert insert_args[1] == "docs"
    assert insert_args[6] == 3
    stored = json.loads(insert_args[4])
    assert stored["answer"].startswith("Returns")


async def test_store_skips_an_empty_key() -> None:
    pool = FakePool()
    cache = PgSemanticCache(pool=cast(Any, pool), embedder=FakeEmbedder())
    empty = CacheKey(
        tenant_id="default",
        normalized_query="hello",
        embedding=[],
        index_version=7,
    )
    await cache.store(empty, answer())
    assert pool.executed == []


async def test_invalidate_counts_the_deleted_rows() -> None:
    pool = FakePool(count=2)
    cache = PgSemanticCache(pool=cast(Any, pool), embedder=FakeEmbedder())
    assert await cache.invalidate("default") == 2


async def test_a_cache_hit_round_trips_the_stored_answer() -> None:
    pool = FakePool(row=row(0.99))
    cache = PgSemanticCache(pool=cast(Any, pool), embedder=FakeEmbedder())
    hit = await cache.lookup(key(), threshold=0.92)
    assert hit is not None
    assert hit.entry.answer.model_dump()["answer"] == "Returns take 15 calendar days [d1]."
