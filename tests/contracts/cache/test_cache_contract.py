"""Cache contract: the registered no-cache adapter.

Only one cache adapter exists until T2.18 adds the Postgres semantic cache, so
this module pins the ``NoCache`` behaviour the orchestrators rely on while
caching is off: every lookup misses, a store changes nothing and invalidation
reports zero removed entries. The same cases will run against the Postgres
adapter with its own expectations in T2.18.
"""

from __future__ import annotations

import pytest

from switchboard.adapters.cache.none import NoCache
from switchboard.core.models import (
    AgentResult,
    CachedAnswer,
    CacheKey,
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


def test_the_registry_exposes_the_no_cache_adapter() -> None:
    assert Registry().names(CACHES) == ["none"]
    assert Registry().load(CACHES, "none") is NoCache


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
