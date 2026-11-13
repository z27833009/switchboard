"""The no-cache adapter: every lookup misses and every store is a no-op.

``platform.yaml`` selects ``cache: none`` in Phase 1 and v1, so the
orchestrators keep their pass-through cache nodes until the semantic cache
adapter (T2.18) replaces this one. The adapter exists so the ``Cache`` port has
a registered implementation and a contract test even while caching is off.
"""

from __future__ import annotations

from switchboard.core.models import CachedAnswer, CacheHit, CacheKey


class NoCache:
    name = "none"

    async def lookup(self, key: CacheKey) -> CacheHit | None:
        del key
        return None

    async def store(self, key: CacheKey, answer: CachedAnswer) -> None:
        del key, answer
        return None

    async def invalidate(self, tenant_id: str) -> int:
        del tenant_id
        return 0


__all__ = ["NoCache"]
