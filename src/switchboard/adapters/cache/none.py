"""The no-cache adapter: every lookup misses and every store is a no-op.

``platform.yaml`` selects the Postgres semantic cache from T2.18 on; this
adapter stays registered so the no-cache path is one config value away and the
``Cache`` port keeps a second implementation. It also means a deployment can
turn caching off without removing the table or the wiring.

``key_for`` deliberately does not embed the query: with caching off, a request
must not pay for an embedding it will not use.
"""

from __future__ import annotations

from typing import Any

from switchboard.core.cache import normalize_query
from switchboard.core.models import CachedAnswer, CacheHit, CacheKey, RunContext


class NoCache:
    name = "none"

    @classmethod
    def from_deps(cls, *, pool: Any, embedder: Any) -> NoCache:
        del pool, embedder
        return cls()

    async def key_for(self, ctx: RunContext, query: str) -> CacheKey:
        return CacheKey(
            tenant_id=ctx.tenant.tenant_id,
            normalized_query=normalize_query(query),
            embedding=[],
            index_version=0,
        )

    async def lookup(self, key: CacheKey, *, threshold: float = 0.92) -> CacheHit | None:
        del key, threshold
        return None

    async def store(self, key: CacheKey, answer: CachedAnswer, *, ttl_days: int = 7) -> None:
        del key, answer, ttl_days
        return None

    async def invalidate(self, tenant_id: str) -> int:
        del tenant_id
        return 0


__all__ = ["NoCache"]
