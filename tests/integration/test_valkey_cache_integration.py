"""Valkey semantic cache against the live service.

    docker compose up -d valkey
    uv run --env-file .env pytest tests/integration/test_valkey_cache_integration.py -q

These tests need a Valkey with the ``valkey-search`` module and nothing else:
the vectors are handcrafted, so no embedding model is called and no LM Studio
is required. The tests cover what a fake client cannot: a real HNSW index, the
real TAG, NUMERIC and KNN query, a real TTL, tenant isolation, old index
versions and the invalidation count. Every key these tests write is removed
afterwards, and the tenant row is deleted again when the test created it.
"""

from __future__ import annotations

import math
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import asyncpg
import pytest

from switchboard.adapters.cache.valkey_semantic import (
    VECTOR_DIMENSION,
    ValkeySemanticCache,
    create_client,
    tenant_token,
)
from switchboard.core.cache import normalize_query
from switchboard.core.models import (
    AgentResult,
    CachedAnswer,
    CacheKey,
    RunContext,
    TenantContext,
    Usage,
)

REPO = Path(__file__).resolve().parents[2]
VALKEY_URL = os.environ.get("VALKEY_URL", "")
DATABASE_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
TENANT = "valkey_it"
OTHER_TENANT = "valkey_it_other"
NOW = 1_700_000_000

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not VALKEY_URL, reason="set VALKEY_URL"),
]


class FixedEmbedder:
    name = "fixed"
    dimension = VECTOR_DIMENSION

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self.vector) for _ in texts]


class NoPool:
    async def fetchval(self, sql: str, *args: Any) -> Any:  # pragma: no cover
        raise AssertionError("this test does not read the tenant version")


def direction(index: int) -> list[float]:
    values = [0.0] * VECTOR_DIMENSION
    values[index] = 1.0
    return values


def blend(main: float, other: float) -> list[float]:
    values = [0.0] * VECTOR_DIMENSION
    values[0] = main
    values[1] = other
    norm = math.sqrt(main * main + other * other)
    return [value / norm for value in values]


def key(
    vector: list[float],
    *,
    tenant_id: str = TENANT,
    index_version: int = 1,
    query: str = "what is the return window?",
) -> CacheKey:
    return CacheKey(
        tenant_id=tenant_id,
        normalized_query=normalize_query(query),
        embedding=vector,
        index_version=index_version,
    )


def answer(text: str = "Returns take 15 calendar days [d1].") -> CachedAnswer:
    return CachedAnswer(
        plan="docs",
        answer=AgentResult(answer=text, usage=Usage(input_tokens=10, output_tokens=4)),
    )


async def raw_client() -> Any:
    return create_client(VALKEY_URL)


async def tenant_keys(client: Any, tenant_id: str) -> list[str]:
    prefix = f"sb:cache:{tenant_token(tenant_id)}:*"
    names: list[str] = []
    cursor = 0
    while True:
        cursor, batch = await client.scan(cursor=cursor, match=prefix, count=500)
        names.extend(name.decode() for name in batch)
        if int(cursor) == 0:
            return names


@pytest.fixture
async def cache() -> AsyncIterator[tuple[ValkeySemanticCache, Any]]:
    client = await raw_client()
    adapter = ValkeySemanticCache(
        client=create_client(VALKEY_URL),
        pool=cast(Any, NoPool()),
        embedder=FixedEmbedder(direction(0)),
        clock=lambda: NOW,
    )
    try:
        await adapter.invalidate(TENANT)
        await adapter.invalidate(OTHER_TENANT)
        yield adapter, client
    finally:
        await adapter.invalidate(TENANT)
        await adapter.invalidate(OTHER_TENANT)
        await adapter.aclose()
        await client.aclose()


async def test_the_service_carries_the_search_module_and_an_idempotent_index(
    cache: tuple[ValkeySemanticCache, Any],
) -> None:
    adapter, _client = cache
    described = await adapter.describe()
    assert described.get("error") is None
    modules = described.get("modules") or {}
    assert "search" in modules, f"the valkey-search module is missing: {described}"
    await adapter.ensure_index()
    await adapter.ensure_index()
    # The rebuild path the benchmark uses; the keys are left in place.
    await adapter.rebuild_index()
    await adapter.ensure_index()


async def test_a_real_knn_hit_a_near_miss_and_tenant_isolation(
    cache: tuple[ValkeySemanticCache, Any],
) -> None:
    adapter, _client = cache
    stored = key(direction(0))
    await adapter.store(stored, answer())

    exact = await adapter.lookup(stored, threshold=0.94)
    assert exact is not None
    assert exact.similarity == pytest.approx(1.0, abs=1e-4)
    assert exact.entry.answer.answer.startswith("Returns")

    near = await adapter.lookup(key(blend(0.99, 0.14)), threshold=0.94)
    assert near is not None, "a 0.99-similar vector must hit at 0.94"
    assert near.similarity == pytest.approx(0.99, abs=1e-3)

    assert await adapter.lookup(key(direction(1)), threshold=0.94) is None
    assert await adapter.lookup(key(direction(0), tenant_id=OTHER_TENANT), threshold=0.0) is None


async def test_a_real_ttl_and_the_expiry_filter(
    cache: tuple[ValkeySemanticCache, Any],
) -> None:
    adapter, client = cache
    stored = key(direction(0))
    await adapter.store(stored, answer(), ttl_days=1)
    names = await tenant_keys(client, TENANT)
    assert len(names) == 1
    ttl = int(await client.ttl(names[0]))
    assert 0 < ttl <= 86400, "store must set a real TTL"

    assert await adapter.lookup(stored, threshold=0.94) is not None
    # The entry is still in Valkey, but the lookup filters on expires_at.
    later = ValkeySemanticCache(
        client=create_client(VALKEY_URL),
        pool=cast(Any, NoPool()),
        embedder=FixedEmbedder(direction(0)),
        clock=lambda: NOW + 2 * 86400,
    )
    try:
        assert await later.lookup(stored, threshold=0.94) is None
    finally:
        await later.aclose()


async def test_an_old_index_version_is_excluded_and_pruned(
    cache: tuple[ValkeySemanticCache, Any],
) -> None:
    adapter, client = cache
    current = key(direction(0), index_version=1)
    await adapter.store(current, answer("version one"))
    next_version = key(direction(0), index_version=2)
    await adapter.store(next_version, answer("version two"))

    assert await adapter.lookup(current, threshold=0.0) is None
    assert await adapter.lookup(next_version, threshold=0.94) is not None
    names = await tenant_keys(client, TENANT)
    assert names, "the new entry must exist"
    for name in names:
        assert name.split(":")[3] == "2", f"the older version was not pruned: {name}"


async def test_invalidate_counts_and_keeps_another_tenant(
    cache: tuple[ValkeySemanticCache, Any],
) -> None:
    adapter, client = cache
    for index in range(3):
        await adapter.store(key(direction(index), query=f"question {index}"), answer())
    await adapter.store(key(direction(0), tenant_id=OTHER_TENANT, query="question 0"), answer())

    assert await adapter.invalidate(TENANT) == 3
    assert await tenant_keys(client, TENANT) == []
    assert len(await tenant_keys(client, OTHER_TENANT)) == 1
    assert await adapter.invalidate(TENANT) == 0
    assert await adapter.invalidate(OTHER_TENANT) == 1


async def test_key_for_reads_the_tenant_version_from_postgres() -> None:
    if not DATABASE_URL:
        pytest.skip("set SWITCHBOARD_DATABASE_URL")
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=2)
    adapter = ValkeySemanticCache(
        client=create_client(VALKEY_URL),
        pool=pool,
        embedder=FixedEmbedder(direction(0)),
        clock=lambda: NOW,
    )
    tenant_id = f"{TENANT}_{uuid.uuid4().hex[:8]}"
    try:
        await pool.execute(
            "INSERT INTO public.tenants (id, name, doc_index_version) VALUES ($1, $2, 3) "
            "ON CONFLICT (id) DO NOTHING",
            tenant_id,
            "Valkey integration",
        )
        ctx = RunContext(
            run_id=f"valkey-it-{uuid.uuid4()}",
            tenant=TenantContext(
                tenant_id=tenant_id,
                allowed_agents={"docs"},
                allowed_tools=set(),
                provider="lmstudio",
            ),
            run_token="unused",
            trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        )
        built = await adapter.key_for(ctx, "  What is the RETURN window? ")
        assert built.tenant_id == tenant_id
        assert built.normalized_query == "what is the return window?"
        assert built.index_version == 3

        await adapter.store(built, answer())
        hit = await adapter.lookup(built, threshold=0.94)
        assert hit is not None
        assert (
            await adapter.lookup(
                built.model_copy(update={"tenant_id": OTHER_TENANT}), threshold=0.0
            )
            is None
        )
        assert await adapter.invalidate(tenant_id) == 1
    finally:
        await adapter.invalidate(tenant_id)
        await pool.execute("DELETE FROM public.cache_entries WHERE tenant_id = $1", tenant_id)
        await pool.execute("DELETE FROM public.tenants WHERE id = $1", tenant_id)
        await adapter.aclose()
        await pool.close()


async def test_build_cache_resolves_the_valkey_adapter_from_the_platform_config() -> None:
    """The registry path the API uses, not a hand-built adapter (T3.09)."""
    if not DATABASE_URL:
        pytest.skip("set SWITCHBOARD_DATABASE_URL")
    from switchboard.api.deps import build_cache
    from switchboard.config import load_settings
    from switchboard.registry import Registry

    source = {
        **os.environ,
        "LMSTUDIO_BASE_URL": os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:9/v1"),
        "LMSTUDIO_MODEL": os.environ.get("LMSTUDIO_MODEL", "qwen3-8b"),
        "LMSTUDIO_EMBEDDING_MODEL": os.environ.get("LMSTUDIO_EMBEDDING_MODEL", "bge-m3"),
    }
    settings = load_settings(REPO / "config", source)
    platform = settings.platform.model_copy(update={"cache": "valkey_semantic"})
    settings = settings.model_copy(update={"platform": platform})
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=2)
    cache_adapter: ValkeySemanticCache | None = None
    try:
        built = build_cache(settings, Registry(), pool, FixedEmbedder(direction(0)))
        assert isinstance(built, ValkeySemanticCache)
        cache_adapter = built
        await cache_adapter.start()
        stored = key(direction(0))
        await cache_adapter.store(stored, answer())
        assert await cache_adapter.lookup(stored, threshold=0.94) is not None
        assert await cache_adapter.invalidate(TENANT) >= 1
    finally:
        try:
            if cache_adapter is not None:
                await cache_adapter.invalidate(TENANT)
                await cache_adapter.aclose()
        finally:
            await pool.close()
