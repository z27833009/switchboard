"""Cache contract: the registered no-cache adapter.

``NoCache`` remains the no-op implementation and the second registered
``Cache`` adapter. This module pins the behaviour the orchestrators rely on
while caching is off: every lookup misses, a store changes nothing, invalidation
reports zero removed entries and ``key_for`` never pays for an embedding. The
Postgres adapter has its own unit and integration tests in ``tests/unit/test_cache.py``
and ``tests/integration/test_cache_integration.py``.
"""

from __future__ import annotations

import pytest

from switchboard.adapters.cache.none import NoCache
from switchboard.core.models import (
    AgentResult,
    CachedAnswer,
    CacheKey,
    RunContext,
    TenantContext,
    Usage,
)
from switchboard.core.ports import Cache
from switchboard.registry import CACHES, Registry


def key(tenant_id: str = "default", query: str = "What is the return window?") -> CacheKey:
    return CacheKey(
        tenant_id=tenant_id,
        normalized_query=query,
        embedding=[0.1, 0.2, 0.3],
        index_version=1,
    )


def answer() -> CachedAnswer:
    return CachedAnswer(
        plan="docs",
        answer=AgentResult(
            answer="Returns take 15 calendar days [d1].",
            usage=Usage(input_tokens=10, output_tokens=5),
        ),
    )


@pytest.fixture
def cache() -> Cache:
    return NoCache()


def test_the_registry_exposes_three_cache_adapters() -> None:
    assert Registry().names(CACHES) == ["none", "pg_semantic", "valkey_semantic"]
    assert Registry().load(CACHES, "none") is NoCache


async def test_key_for_does_not_embed_the_query(cache: Cache) -> None:
    ctx = RunContext(
        run_id="run-1",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents={"docs"},
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )
    built = await cache.key_for(ctx, "  What is the RETURN   window?  ")
    assert built.tenant_id == "default"
    assert built.normalized_query == "what is the return window?"
    assert built.embedding == []
    assert built.index_version == 0


async def test_every_lookup_misses(cache: Cache) -> None:
    assert cache.name
    assert await cache.lookup(key()) is None
    assert await cache.lookup(key(tenant_id="seller_a")) is None


async def test_store_is_a_no_op(cache: Cache) -> None:
    cache_key = key()
    await cache.store(cache_key, answer())
    assert await cache.lookup(cache_key) is None


async def test_invalidate_removes_nothing(cache: Cache) -> None:
    assert await cache.invalidate("default") == 0
    assert await cache.invalidate("seller_a") == 0
