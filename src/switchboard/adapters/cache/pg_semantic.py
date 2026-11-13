"""Postgres semantic cache (design.md section 10, ADR 0007).

One table, ``public.cache_entries``, holds the answer of a cacheable plan with
the query embedding that produced it. The lookup runs before the supervisor, so
a hit makes no model call: it costs the query embedding and one SQL statement.
The key is scoped by tenant, document index version and TTL, and a candidate
must reach the tenant's similarity threshold before it counts as a hit.

The adapter owns the parts that need the database or the embedder:

* ``key_for`` embeds the raw query and reads the tenant's current
  ``doc_index_version``. The embedding is also handed back to the orchestrator,
  which can pass it to retrieval on a miss (design 10.1).
* ``lookup`` selects the nearest unexpired entry of the same tenant and index
  version, then applies the threshold. The cosine similarity is recorded on the
  ``cache.lookup`` span whether or not it reaches the threshold.
* ``store`` prunes expired entries and entries from older index versions for
  that tenant, then inserts the new one with ``expires_at = now() + ttl``.
* ``invalidate`` deletes every entry of a tenant and returns the count.

A database or embedding failure becomes a ``CacheError``; the orchestrator
treats it as a miss, so the cache can never turn a working run into a failed
one.
"""

from __future__ import annotations

import math
from typing import Any

import asyncpg
from opentelemetry import trace

from switchboard.core.cache import normalize_query
from switchboard.core.errors import CacheError, ConfigError
from switchboard.core.models import CachedAnswer, CacheHit, CacheKey, RunContext
from switchboard.tracing.spans import record_error, set_attributes

# The design fixes the column at vector(1024), the bge-m3 dimension.
VECTOR_DIMENSION = 1024
DEFAULT_TTL_DAYS = 7
DEFAULT_THRESHOLD = 0.92

LOOKUP_SQL = """
WITH current AS (
    SELECT doc_index_version AS version FROM public.tenants WHERE id = $1
)
SELECT e.id, e.plan, e.answer, e.index_version,
       1 - (e.embedding <=> $2::vector) AS similarity
FROM public.cache_entries e
CROSS JOIN current
WHERE e.tenant_id = $1
  AND e.index_version = $3
  AND e.index_version = current.version
  AND e.expires_at > now()
ORDER BY e.embedding <=> $2::vector
LIMIT 1
"""

PRUNE_SQL = """
DELETE FROM public.cache_entries
WHERE tenant_id = $1
  AND (index_version <> $2 OR expires_at <= now())
"""

INSERT_SQL = """
INSERT INTO public.cache_entries
    (tenant_id, plan, query_text, embedding, answer, index_version, expires_at)
VALUES ($1, $2, $3, $4::vector, $5::jsonb, $6, now() + make_interval(days => $7))
"""

INVALIDATE_SQL = """
WITH deleted AS (
    DELETE FROM public.cache_entries WHERE tenant_id = $1 RETURNING 1
)
SELECT count(*) FROM deleted
"""


def vector_literal(vector: list[float]) -> str:
    """pgvector input as a JSON array, the same form the retriever and ingest use."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity in [0, 1], the value pgvector's ``<=>`` turns into 1 - d.

    ``Cosine similarity`` is in [-1, 1] mathematically; the cache clamps the
    negative half to zero because ``CacheHit.similarity`` and the threshold are
    both non-negative. The eval suite uses this function so its threshold sweep
    measures the same arithmetic the database applies.
    """
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_left * norm_right)))


class PgSemanticCache:
    name = "pg_semantic"

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        embedder: Any,
    ) -> None:
        dimension = int(getattr(embedder, "dimension", 0))
        if dimension != VECTOR_DIMENSION:
            raise ConfigError(
                f"the semantic cache needs a {VECTOR_DIMENSION}-dimension embedder, "
                f"got {dimension}; the cache_entries.embedding column is vector(1024)"
            )
        self._pool = pool
        self._embedder = embedder

    @classmethod
    def from_deps(cls, *, pool: asyncpg.Pool, embedder: Any) -> PgSemanticCache:
        return cls(pool=pool, embedder=embedder)

    async def key_for(self, ctx: RunContext, query: str) -> CacheKey:
        try:
            vectors = await self._embedder.embed([query])
        except CacheError:
            raise
        except Exception as exc:
            raise CacheError(f"query embedding failed: {type(exc).__name__}") from exc
        if len(vectors) != 1 or len(vectors[0]) != VECTOR_DIMENSION:
            raise CacheError("the embedder returned no 1024-dimension query vector")
        try:
            version = await self._pool.fetchval(
                "SELECT doc_index_version FROM public.tenants WHERE id = $1",
                ctx.tenant.tenant_id,
            )
        except Exception as exc:
            raise CacheError(f"index version lookup failed: {type(exc).__name__}") from exc
        if version is None:
            raise CacheError(f"unknown tenant {ctx.tenant.tenant_id!r}")
        return CacheKey(
            tenant_id=ctx.tenant.tenant_id,
            normalized_query=normalize_query(query),
            embedding=list(vectors[0]),
            index_version=int(version),
        )

    async def lookup(
        self, key: CacheKey, *, threshold: float = DEFAULT_THRESHOLD
    ) -> CacheHit | None:
        """Return the nearest entry at or above ``threshold``, or None.

        The orchestrator wraps this call in a ``cache.lookup`` span; the adapter
        records ``hit`` and ``similarity`` on the current span so a miss below
        the threshold is still visible with the similarity that missed.
        """
        span = trace.get_current_span()
        try:
            row = await self._pool.fetchrow(
                LOOKUP_SQL,
                key.tenant_id,
                vector_literal(key.embedding),
                key.index_version,
            )
        except Exception as exc:
            error = CacheError(f"cache lookup failed: {type(exc).__name__}")
            record_error(span, error)
            raise error from exc
        if row is None:
            set_attributes(span, {"switchboard.cache.hit": False})
            return None
        similarity = max(0.0, min(1.0, float(row["similarity"])))
        hit = similarity >= threshold
        set_attributes(
            span,
            {
                "switchboard.cache.hit": hit,
                "switchboard.cache.similarity": round(similarity, 6),
            },
        )
        if not hit:
            return None
        try:
            await self._pool.execute(
                "UPDATE public.cache_entries SET hits = hits + 1 WHERE id = $1",
                row["id"],
            )
            entry = CachedAnswer(
                plan=str(row["plan"]),
                answer=_answer_from_row(row["answer"]),
            )
        except Exception as exc:
            error = CacheError(f"cache hit could not be read: {type(exc).__name__}")
            record_error(span, error)
            raise error from exc
        return CacheHit(entry=entry, similarity=similarity)

    async def store(
        self, key: CacheKey, answer: CachedAnswer, *, ttl_days: int = DEFAULT_TTL_DAYS
    ) -> None:
        span = trace.get_current_span()
        if not key.embedding:
            set_attributes(span, {"switchboard.cache.stored": False})
            return
        payload = answer.answer.model_dump_json()
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                await connection.execute(PRUNE_SQL, key.tenant_id, key.index_version)
                await connection.execute(
                    INSERT_SQL,
                    key.tenant_id,
                    answer.plan,
                    key.normalized_query,
                    vector_literal(key.embedding),
                    payload,
                    key.index_version,
                    ttl_days,
                )
        except Exception as exc:
            error = CacheError(f"cache store failed: {type(exc).__name__}")
            record_error(span, error)
            raise error from exc
        set_attributes(span, {"switchboard.cache.stored": True})

    async def invalidate(self, tenant_id: str) -> int:
        try:
            removed = await self._pool.fetchval(INVALIDATE_SQL, tenant_id)
        except Exception as exc:
            raise CacheError(f"cache invalidation failed: {type(exc).__name__}") from exc
        return int(removed or 0)


def _answer_from_row(payload: Any) -> Any:
    """Parse the stored ``AgentResult`` JSON, accepting asyncpg's str or a dict."""
    import json

    from switchboard.core.models import AgentResult

    raw = json.loads(payload) if isinstance(payload, str) else payload
    return AgentResult.model_validate(raw)


__all__ = [
    "DEFAULT_THRESHOLD",
    "DEFAULT_TTL_DAYS",
    "VECTOR_DIMENSION",
    "PgSemanticCache",
    "cosine_similarity",
    "vector_literal",
]
