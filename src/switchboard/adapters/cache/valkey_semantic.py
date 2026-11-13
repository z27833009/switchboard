"""Valkey semantic cache (design.md section 10, ADR 0003, ADR 0007).

The second ``Cache`` adapter: the same key, the same tenant, index-version and
TTL scoping and the same threshold as ``PgSemanticCache``, but the entries live
in Valkey and the nearest neighbour comes from the ``valkey-search`` module
(``FT.CREATE`` and ``FT.SEARCH`` with an HNSW vector field) instead of pgvector.
It exists to show that the port, not PostgreSQL, is what the orchestrators
depend on, and to measure the two stores on one frozen dataset
(``evals/reports/cache-adapters.md``).

Layout
------

One index, ``sb_cache``, over HASH keys with the prefix ``sb:cache:``:

* ``tenant_id`` TAG, ``index_version`` NUMERIC, ``expires_at`` NUMERIC and
  ``embedding`` VECTOR HNSW FLOAT32 DIM 1024 COSINE are indexed.
* ``plan``, ``query_text`` and ``answer`` are plain hash fields. The
  ``valkey-search`` module in the pinned bundle indexes only TAG, NUMERIC and
  VECTOR fields, so they are returned by ``FT.SEARCH ... RETURN`` rather than
  declared in the schema.
* The key name is ``sb:cache:<tenant token>:<index version>:<expires at>:<question
  token>``. Repeating the version and the expiry in the name lets a prune and an
  invalidate decide what to delete from the name alone, with one SCAN and one
  DEL instead of a field read per key. The tenant token is a truncated SHA-256
  of the tenant id, so a tenant id containing a glob or a TAG separator can
  never widen a scan into another tenant's keyspace. The question token is a
  truncated SHA-256 of the normalized question, so one question has exactly one
  entry per tenant and index version: storing it again refreshes that entry and
  its TTL instead of duplicating the vector.

Distance semantics
------------------

``valkey-search`` 1.0.3 computes a filtered KNN query as
``1 - dot(normalized stored vector, raw query vector)`` and does not normalize
the query, so a raw vector can yield a negative distance. This adapter
normalizes the query vector before sending it, which makes the reported
distance identical to cosine distance and therefore identical to pgvector's
``<=>``. ``similarity = 1 - distance`` is then clamped to [0, 1], exactly like
``PgSemanticCache``.

Failure policy
--------------

A Valkey or embedding failure becomes a ``CacheError``. The orchestrator treats
that as a miss, so a cache outage can never fail a run. Index creation is
idempotent and is attempted at startup; a failure there is logged and retried on
the next lookup, which keeps the API startable with Valkey down.

Known limitation of the pinned module
-------------------------------------

``valkey-search`` does not remove a deleted document from its HNSW graph:
``FT.INFO`` keeps counting it in ``num_docs``, and after enough store and delete
cycles a filtered KNN query can return nothing at all even though matching keys
exist. The adapter then misses (it can never return a wrong answer), and
``rebuild_index()`` drops and recreates the index, leaving the keys in place,
which restores it. The benchmark calls that before measuring; a deployment that
keeps the adapter for a long time needs the same call as a periodic job. See
``evals/reports/cache-adapters.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import struct
import time
from typing import Any
from urllib.parse import unquote, urlparse

import valkey
import valkey.asyncio as valkey_async
from opentelemetry import trace

from switchboard.core.cache import normalize_query
from switchboard.core.errors import CacheError, ConfigError
from switchboard.core.models import CachedAnswer, CacheHit, CacheKey, RunContext
from switchboard.tracing.spans import record_error, set_attributes

logger = logging.getLogger(__name__)

INDEX_NAME = "sb_cache"
KEY_PREFIX = "sb:cache:"
# The design fixes the vector at 1024 dimensions, the bge-m3 dimension; the
# index is created with the same value pgvector's vector(1024) column uses.
VECTOR_DIMENSION = 1024
DEFAULT_TTL_DAYS = 7
DEFAULT_THRESHOLD = 0.92
DEFAULT_VALKEY_URL = "valkey://localhost:6379/0"
# How many neighbours FT.SEARCH asks for before the adapter keeps the nearest.
# The filter runs inside the query, so one candidate is enough for the answer;
# a handful keeps the module's result set small without a second round trip.
KNN_CANDIDATES = 5
SCAN_BATCH = 200
DELETE_BATCH = 200

# FT.CREATE fields, in the order the module expects them. TEXT is deliberately
# absent: the pinned valkey-search module rejects it (see the module docstring).
INDEX_SCHEMA: tuple[str, ...] = (
    "tenant_id",
    "TAG",
    "index_version",
    "NUMERIC",
    "expires_at",
    "NUMERIC",
    "embedding",
    "VECTOR",
    "HNSW",
    "6",
    "TYPE",
    "FLOAT32",
    "DIM",
    str(VECTOR_DIMENSION),
    "DISTANCE_METRIC",
    "COSINE",
)

RETURN_FIELDS: tuple[str, ...] = ("plan", "answer", "query_text", "distance")

# Characters a TAG query escapes, from the RediSearch query syntax. Escaping a
# comma matters most: unescaped, ``{a,b}`` is an OR of two tags.
_TAG_SPECIALS = frozenset("\\,.<>{}[]\"':;!@#$%^&*()-+=~|/ ")


def escape_tag(value: str) -> str:
    """Escape a tenant id for a TAG filter, so it can never become a union."""
    return "".join(f"\\{char}" if char in _TAG_SPECIALS else char for char in value)


def tenant_token(tenant_id: str) -> str:
    """A glob-free, colon-free token for the key name (see the module docstring)."""
    return hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16]


def entry_key_name(tenant_id: str, index_version: int, expires_at: int, query_text: str) -> str:
    """``sb:cache:<tenant token>:<version>:<expires at>:<question token>``.

    The last segment is a hash of the normalized question, so storing the same
    question again refreshes that one entry instead of adding a duplicate. That
    keeps the entry count bounded and avoids exact duplicate vectors, which the
    pinned module's filtered KNN handles badly (see ``rebuild_index``).
    """
    question = hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:16]
    return f"{KEY_PREFIX}{tenant_token(tenant_id)}:{index_version}:{expires_at}:{question}"


def key_parts(name: str | bytes) -> tuple[str, int, int] | None:
    """Split a key name into (tenant token, index version, expires at)."""
    text = name.decode("utf-8") if isinstance(name, bytes) else name
    if not text.startswith(KEY_PREFIX):
        return None
    segments = text[len(KEY_PREFIX) :].split(":")
    if len(segments) != 4:
        return None
    token, version, expires_at, _unique = segments
    try:
        return token, int(version), int(expires_at)
    except ValueError:
        return None


def embedding_bytes(vector: list[float]) -> bytes:
    """Little-endian FLOAT32, the byte order the VECTOR field expects.

    The vector is normalized first: valkey-search does not normalize the query
    side of a filtered KNN query, and cosine distance only equals pgvector's
    when both sides are unit length (see the module docstring).
    """
    if len(vector) != VECTOR_DIMENSION:
        raise CacheError(
            f"the semantic cache needs a {VECTOR_DIMENSION}-dimension query vector, "
            f"got {len(vector)}"
        )
    values = list(vector)
    norm = math.sqrt(sum(value * value for value in values))
    values = [value / norm for value in values] if norm > 0.0 else [0.0] * len(values)
    return struct.pack(f"<{len(values)}f", *values)


def clamp_similarity(distance: float) -> float:
    """``1 - distance`` in [0, 1], matching ``PgSemanticCache``."""
    return max(0.0, min(1.0, 1.0 - distance))


def knn_query(key: CacheKey, *, now: int, candidates: int = KNN_CANDIDATES) -> str:
    """The FT.SEARCH query: tenant, version and expiry filters, then KNN.

    The two numbers are integers the adapter computed, and the tag is escaped,
    so no caller text reaches the query string.
    """
    return (
        f"(@tenant_id:{{{escape_tag(key.tenant_id)}}}) "
        f"(@index_version:[{int(key.index_version)} {int(key.index_version)}]) "
        f"(@expires_at:[({int(now)} +inf]) "
        f"=>[KNN {int(candidates)} @embedding $vec AS distance]"
    )


def parse_url(url: str) -> dict[str, Any]:
    """A ``valkey://`` (or ``redis://``) URL into client keyword arguments."""
    parsed = urlparse(url)
    if parsed.scheme not in {"valkey", "valkeys", "redis", "rediss"}:
        raise ConfigError(
            f"VALKEY_URL must start with valkey:// or redis://, got {parsed.scheme or url!r}"
        )
    if not parsed.hostname:
        raise ConfigError(f"VALKEY_URL has no host: {url!r}")
    database = parsed.path.lstrip("/") or "0"
    try:
        db = int(database)
    except ValueError as exc:
        raise ConfigError(f"VALKEY_URL has a non-numeric database {database!r}") from exc
    return {
        "host": parsed.hostname,
        "port": parsed.port or 6379,
        "db": db,
        "username": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
        "ssl": parsed.scheme in {"valkeys", "rediss"},
    }


def create_client(url: str) -> Any:
    """A client that fails a call in at most about two seconds.

    A cache lookup sits in front of every run, so a down Valkey must not hold a
    request for the client default: the orchestrator's timeout would fire first
    and the run would fail instead of missing the cache.
    """
    options = parse_url(url)
    return valkey_async.Valkey(
        **options,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
        decode_responses=False,
    )


class ValkeySemanticCache:
    name = "valkey_semantic"

    def __init__(
        self,
        *,
        client: Any,
        pool: Any,
        embedder: Any,
        clock: Any = None,
    ) -> None:
        dimension = int(getattr(embedder, "dimension", 0))
        if dimension != VECTOR_DIMENSION:
            raise ConfigError(
                f"the semantic cache needs a {VECTOR_DIMENSION}-dimension embedder, "
                f"got {dimension}; the Valkey vector field is FLOAT32 DIM 1024"
            )
        self._client = client
        self._pool = pool
        self._embedder = embedder
        # A seam for the TTL tests; production reads the wall clock.
        self._clock = clock if clock is not None else time.time
        self._index_ready = False
        self._index_lock = asyncio.Lock()

    @classmethod
    def from_deps(
        cls,
        *,
        pool: Any,
        embedder: Any,
        url: str | None = None,
        client: Any = None,
        clock: Any = None,
    ) -> ValkeySemanticCache:
        chosen = (
            client
            if client is not None
            else create_client(url or os.environ.get("VALKEY_URL") or DEFAULT_VALKEY_URL)
        )
        return cls(client=chosen, pool=pool, embedder=embedder, clock=clock)

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # pragma: no cover - shutdown only
            logger.debug("closing the Valkey client failed: %s", exc)

    async def start(self) -> None:
        """Create the index at startup without ever failing the process.

        Valkey down must not stop the API: the adapter retries on the next
        lookup and every failure is a cache miss.
        """
        try:
            await self.ensure_index()
        except CacheError as exc:
            logger.warning("Valkey cache index is not ready yet: %s", exc)

    async def ensure_index(self) -> None:
        """``FT.CREATE`` once; an existing index is success (idempotent startup)."""
        if self._index_ready:
            return
        async with self._index_lock:
            if self._index_ready:
                return
            await self._create_index()
            self._index_ready = True

    async def _create_index(self) -> None:
        try:
            await self._client.execute_command(
                "FT.CREATE",
                INDEX_NAME,
                "ON",
                "HASH",
                "PREFIX",
                1,
                KEY_PREFIX,
                "SCHEMA",
                *INDEX_SCHEMA,
            )
        except Exception as exc:
            message = str(exc)
            if "already exists" not in message.lower():
                raise CacheError(f"index creation failed: {message or type(exc).__name__}") from exc
            # A stale index from an older schema is the one failure that
            # "already exists" would otherwise hide until a silent miss.
            await self._check_existing_dimension()

    async def rebuild_index(self) -> None:
        """Drop and recreate the index, leaving the keys in place.

        The pinned module does not delete a document from its HNSW graph: the
        entry stays as a tombstone, ``FT.INFO`` keeps counting it in
        ``num_docs``, and after many store and delete cycles a lookup can start
        returning nothing at all even though matching keys exist and an
        unfiltered KNN finds nothing either. A rebuild restores it, and the
        keys are re-indexed in the background. The adapter only ever misses when
        that happens (it can never return a wrong answer), so this is an
        operator escape hatch rather than a startup step; the benchmark calls it
        before measuring so every run starts from a clean graph.
        """
        try:
            try:
                await self._client.execute_command("FT.DROPINDEX", INDEX_NAME)
            except Exception as exc:
                if "unknown index" not in str(exc).lower():
                    raise
            await self._create_index()
        except CacheError:
            raise
        except Exception as exc:
            raise CacheError(f"index rebuild failed: {type(exc).__name__}") from exc
        self._index_ready = True

    async def _check_existing_dimension(self) -> None:
        try:
            info = await self._client.execute_command("FT.INFO", INDEX_NAME)
        except Exception:
            return
        dimension = _find_value(info, "dimensions")
        if dimension is None:
            return
        try:
            found = int(dimension)
        except (TypeError, ValueError):
            return
        if found != VECTOR_DIMENSION:
            raise CacheError(
                f"index {INDEX_NAME!r} exists with DIM {found}, expected {VECTOR_DIMENSION}; "
                f"drop it with FT.DROPINDEX {INDEX_NAME} and restart to recreate it"
            )

    async def describe(self) -> dict[str, Any]:
        """Version facts for the benchmark report and the integration tests."""
        info: dict[str, Any] = {
            "index": INDEX_NAME,
            "key_prefix": KEY_PREFIX,
            "client": f"valkey-py {getattr(valkey, '__version__', 'unknown')}",
        }
        try:
            server = await self._client.info("server")
            info["server"] = _as_text(server.get("valkey_version") or server.get("redis_version"))
            modules = await self._client.execute_command("MODULE", "LIST")
            found: dict[str, str] = {}
            for module in modules or []:
                pairs = _pairs(module)
                name = _as_text(pairs.get("name"))
                version = _as_text(pairs.get("ver"))
                if name:
                    found[name] = version or ""
            info["modules"] = found
        except Exception as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
        return info

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
        """The nearest unexpired entry of this tenant and version, above the threshold.

        The orchestrator wraps this call in a ``cache.lookup`` span; the adapter
        records ``hit`` and ``similarity`` on the current span so a miss below
        the threshold is still visible with the similarity that missed.
        """
        span = trace.get_current_span()
        if not key.embedding:
            set_attributes(span, {"switchboard.cache.hit": False})
            return None
        payload = embedding_bytes(key.embedding)
        try:
            await self.ensure_index()
            response = await self._client.execute_command(
                "FT.SEARCH",
                INDEX_NAME,
                knn_query(key, now=int(self._clock())),
                "PARAMS",
                2,
                "vec",
                payload,
                # No SORTBY: the pinned valkey-search module rejects it and
                # already returns KNN results nearest first.
                "RETURN",
                len(RETURN_FIELDS),
                *RETURN_FIELDS,
                "LIMIT",
                0,
                1,
            )
        except CacheError:
            raise
        except Exception as exc:
            error = CacheError(f"cache lookup failed: {type(exc).__name__}")
            record_error(span, error)
            raise error from exc
        row = _first_row(response)
        if row is None:
            set_attributes(span, {"switchboard.cache.hit": False})
            return None
        try:
            distance = float(_as_text(row.get("distance")) or "nan")
        except ValueError as exc:
            error = CacheError("cache lookup returned no usable distance")
            record_error(span, error)
            raise error from exc
        if not math.isfinite(distance):
            set_attributes(span, {"switchboard.cache.hit": False})
            return None
        similarity = clamp_similarity(distance)
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
            entry = CachedAnswer(
                plan=_as_text(row.get("plan")) or "",
                answer=_answer_from_field(row.get("answer")),
            )
            name = _as_text(row.get("__key__")) or ""
            if name:
                await self._client.hincrby(name, "hits", 1)
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
        if ttl_days <= 0:
            raise ConfigError("ttl_days must be positive")
        payload = embedding_bytes(key.embedding)
        ttl_seconds = int(ttl_days * 86400)
        now = int(self._clock())
        expires_at = now + ttl_seconds
        name = entry_key_name(key.tenant_id, key.index_version, expires_at, key.normalized_query)
        try:
            await self.ensure_index()
            await self._prune(key, now=now)
            await self._client.hset(
                name,
                mapping={
                    "tenant_id": key.tenant_id,
                    "index_version": int(key.index_version),
                    "expires_at": expires_at,
                    "plan": answer.plan,
                    "query_text": key.normalized_query,
                    "answer": answer.answer.model_dump_json(),
                    "hits": 0,
                    "embedding": payload,
                },
            )
            # A real TTL, so a forgotten entry is removed by Valkey itself; the
            # expires_at filter is what makes an expired entry invisible first.
            await self._client.expire(name, ttl_seconds)
        except Exception as exc:
            error = CacheError(f"cache store failed: {type(exc).__name__}")
            record_error(span, error)
            raise error from exc
        set_attributes(span, {"switchboard.cache.stored": True})

    async def invalidate(self, tenant_id: str) -> int:
        """Delete every entry of one tenant and return the number removed."""
        try:
            names = await self._scan(self._tenant_prefix(tenant_id))
            return await self._delete(names)
        except CacheError:
            raise
        except Exception as exc:
            raise CacheError(f"cache invalidation failed: {type(exc).__name__}") from exc

    async def _prune(self, key: CacheKey, *, now: int) -> int:
        """Delete this tenant's older index versions and expired entries."""
        names = await self._scan(self._tenant_prefix(key.tenant_id))
        stale: list[str] = []
        for name in names:
            parts = key_parts(name)
            if parts is None:
                continue
            _token, version, expires_at = parts
            if version != int(key.index_version) or expires_at <= now:
                stale.append(name)
        return await self._delete(stale)

    def _tenant_prefix(self, tenant_id: str) -> str:
        return f"{KEY_PREFIX}{tenant_token(tenant_id)}:"

    async def _scan(self, match: str) -> list[str]:
        names: list[str] = []
        cursor: Any = 0
        while True:
            cursor, batch = await self._client.scan(
                cursor=cursor, match=f"{match}*", count=SCAN_BATCH
            )
            names.extend(_as_text(name) or "" for name in batch or [])
            if int(cursor) == 0:
                break
        return [name for name in names if name]

    async def _delete(self, names: list[str]) -> int:
        removed = 0
        for start in range(0, len(names), DELETE_BATCH):
            chunk = names[start : start + DELETE_BATCH]
            if not chunk:
                continue
            removed += int(await self._client.delete(*chunk) or 0)
        return removed


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return str(value)


def _pairs(values: Any) -> dict[str, Any]:
    """MODULE LIST returns a flat name/value list (or nested lists of them)."""
    result: dict[str, Any] = {}
    if not isinstance(values, (list, tuple)):
        return result
    for index in range(0, len(values) - 1, 2):
        key = _as_text(values[index])
        if key is not None:
            result[key] = values[index + 1]
    return result


def _find_value(values: Any, key: str) -> Any:
    """Depth-first first value stored under ``key`` in a nested FT.INFO reply."""
    if isinstance(values, (list, tuple)):
        for item in values:
            found = _find_value(item, key)
            if found is not None:
                return found
        # A pair list at this level: "<key>", <value>.
        for index in range(0, len(values) - 1, 2):
            if _as_text(values[index]) == key:
                return values[index + 1]
        return None
    return None


def _first_row(response: Any) -> dict[str, Any] | None:
    """The first ``FT.SEARCH`` result as ``{field: value, "__key__": name}``.

    The module answers ``[count, key, [field, value, ...], key, [...]]``; a
    ``RETURN`` list keeps the requested order, but the parser does not rely on
    it.
    """
    if not isinstance(response, (list, tuple)) or len(response) < 3:
        return None
    count = int(response[0] or 0)
    if count == 0:
        return None
    raw_key = response[1]
    fields = response[2]
    if not isinstance(fields, (list, tuple)):
        return None
    row = _pairs(fields)
    row["__key__"] = raw_key
    return row


def _answer_from_field(payload: Any) -> Any:
    from switchboard.core.models import AgentResult

    text = _as_text(payload) or "{}"
    return AgentResult.model_validate(json.loads(text))


__all__ = [
    "DEFAULT_THRESHOLD",
    "DEFAULT_TTL_DAYS",
    "DEFAULT_VALKEY_URL",
    "INDEX_NAME",
    "INDEX_SCHEMA",
    "KEY_PREFIX",
    "VECTOR_DIMENSION",
    "ValkeySemanticCache",
    "clamp_similarity",
    "create_client",
    "embedding_bytes",
    "entry_key_name",
    "escape_tag",
    "key_parts",
    "knn_query",
    "parse_url",
    "tenant_token",
]
