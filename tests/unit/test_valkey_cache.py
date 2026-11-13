"""Valkey semantic cache: unit tests with a fake client and fake FT responses.

No Valkey server is involved. ``FakeValkey`` keeps the hashes the adapter
writes, answers ``FT.CREATE``, ``FT.INFO`` and ``FT.SEARCH`` in the shape the
pinned ``valkey-search`` module uses, and applies the tenant, index-version and
expiry filters from the query, so a wrong filter in the adapter shows up as a
wrong result rather than as a passing mock. Integration behaviour (a real HNSW
index, a real TTL and real tenant isolation) is in
``tests/integration/test_valkey_cache_integration.py``.
"""

from __future__ import annotations

import fnmatch
import math
import struct
from pathlib import Path
from typing import Any, cast

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from tests.fakes import MemoryRunStore, MemorySpanExporter
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import ResponseError

from switchboard.adapters.cache.valkey_semantic import (
    DEFAULT_TTL_DAYS,
    INDEX_NAME,
    KEY_PREFIX,
    VECTOR_DIMENSION,
    ValkeySemanticCache,
    clamp_similarity,
    embedding_bytes,
    entry_key_name,
    escape_tag,
    key_parts,
    knn_query,
    parse_url,
    tenant_token,
)
from switchboard.config import load_settings
from switchboard.core.cache import normalize_query
from switchboard.core.errors import CacheError, ConfigError
from switchboard.core.models import (
    AgentResult,
    CachedAnswer,
    CacheKey,
    RunContext,
    TenantContext,
    Usage,
)
from switchboard.registry import CACHES, Registry
from switchboard.tracing.spans import cache_lookup_span

REPO = Path(__file__).resolve().parents[2]
RUN_ID = "11111111-2222-3333-4444-555555555555"
NOW = 1_700_000_000


def unit_vector(index: int = 0) -> list[float]:
    values = [0.0] * VECTOR_DIMENSION
    values[index] = 1.0
    return values


def mixed_vector(main: float, other: float) -> list[float]:
    values = [0.0] * VECTOR_DIMENSION
    values[0] = main
    values[1] = other
    return values


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_left * norm_right)))


class FakeEmbedder:
    name = "fake"
    dimension = VECTOR_DIMENSION

    def __init__(
        self, vector: list[float] | None = None, *, fail: bool = False, dimension: int = 1024
    ) -> None:
        self.vector = vector if vector is not None else mixed_vector(1.0, 0.0)
        self.fail = fail
        self.dimension = dimension
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("embedder down")
        return [list(self.vector) for _ in texts]


class FakePool:
    def __init__(self, version: int = 1, *, fail: bool = False) -> None:
        self.version = version
        self.fail = fail
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.queries.append((sql, args))
        if self.fail:
            raise RuntimeError("database down")
        return self.version


class FakeValkey:
    """The subset of the async client the adapter uses, with FT semantics."""

    def __init__(
        self,
        *,
        index_exists: bool = False,
        index_dimension: int = VECTOR_DIMENSION,
        fail_on: set[str] | None = None,
        forced_distance: float | None = None,
        scan_batch: int = 2,
    ) -> None:
        self.index_exists = index_exists
        self.index_dimension = index_dimension
        self.fail_on = {command.upper() for command in (fail_on or set())}
        self.forced_distance = forced_distance
        self.scan_batch = scan_batch
        self.commands: list[tuple[Any, ...]] = []
        self.hashes: dict[str, dict[str, Any]] = {}
        self.ttls: dict[str, int] = {}
        self.closed = False

    def _maybe_fail(self, command: str) -> None:
        if command in self.fail_on:
            raise ValkeyConnectionError("connection refused")

    async def execute_command(self, *args: Any) -> Any:
        command = str(args[0]).upper()
        self.commands.append(args)
        self._maybe_fail(command)
        if command == "FT.CREATE":
            if self.index_exists:
                raise ResponseError(f"Index {INDEX_NAME} already exists.")
            self.index_exists = True
            return b"OK"
        if command == "FT.INFO":
            return [
                b"index_name",
                INDEX_NAME.encode(),
                b"attributes",
                [
                    [
                        b"identifier",
                        b"embedding",
                        b"attribute",
                        b"embedding",
                        b"type",
                        b"VECTOR",
                        b"dimensions",
                        self.index_dimension,
                    ]
                ],
            ]
        if command == "FT.DROPINDEX":
            if not self.index_exists:
                raise ResponseError(f"Unknown Index name {INDEX_NAME}")
            self.index_exists = False
            return b"OK"
        if command == "FT.SEARCH":
            return self._search(args)
        raise AssertionError(f"unexpected command {command}")

    async def hset(self, name: str, mapping: dict[str, Any]) -> int:
        self.commands.append(("HSET", name))
        self._maybe_fail("HSET")
        self.hashes.setdefault(name, {}).update(mapping)
        return len(mapping)

    async def hincrby(self, name: str, field: str, amount: int = 1) -> int:
        self.commands.append(("HINCRBY", name, field, amount))
        self._maybe_fail("HINCRBY")
        row = self.hashes[name]
        row[field] = int(row.get(field, 0)) + amount
        return int(row[field])

    async def expire(self, name: str, seconds: int) -> bool:
        self.commands.append(("EXPIRE", name, seconds))
        self._maybe_fail("EXPIRE")
        self.ttls[name] = int(seconds)
        return True

    async def scan(
        self, cursor: int = 0, match: str = "*", count: int = 10
    ) -> tuple[int, list[bytes]]:
        self.commands.append(("SCAN", cursor, match))
        self._maybe_fail("SCAN")
        names = sorted(name for name in self.hashes if fnmatch.fnmatch(name, match))
        start = int(cursor)
        chunk = names[start : start + self.scan_batch]
        next_cursor = start + self.scan_batch if start + self.scan_batch < len(names) else 0
        return next_cursor, [name.encode() for name in chunk]

    async def delete(self, *names: str) -> int:
        self.commands.append(("DEL", *names))
        self._maybe_fail("DEL")
        removed = 0
        for name in names:
            if self.hashes.pop(name, None) is not None:
                self.ttls.pop(name, None)
                removed += 1
        return removed

    async def aclose(self) -> None:
        self.closed = True

    def _search(self, args: tuple[Any, ...]) -> list[Any]:
        query = str(args[2])
        params_index = args.index("PARAMS")
        payload = args[params_index + 3]
        vector = list(struct.unpack(f"<{VECTOR_DIMENSION}f", payload))
        tenant = self._tenant_from(query)
        low, high = self._range(query, "index_version")
        expires_after = self._exclusive_after(query, "expires_at")
        knn = int(query.split("KNN ")[1].split()[0])
        limit = 1
        if "LIMIT" in args:
            limit_index = args.index("LIMIT")
            limit = int(args[limit_index + 2])
        rows: list[tuple[float, str, dict[str, Any]]] = []
        for name, row in self.hashes.items():
            if str(row["tenant_id"]) != tenant:
                continue
            version = int(row["index_version"])
            if version < low or version > high:
                continue
            if expires_after is not None and int(row["expires_at"]) <= expires_after:
                continue
            stored = list(struct.unpack(f"<{VECTOR_DIMENSION}f", row["embedding"]))
            distance = 1.0 - cosine(stored, vector)
            if self.forced_distance is not None:
                distance = self.forced_distance
            rows.append((distance, name, row))
        rows.sort(key=lambda item: item[0])
        selected = rows[: min(knn, limit)]
        response: list[Any] = [len(selected)]
        for distance, name, row in selected:
            response.append(name.encode())
            response.append(
                [
                    b"plan",
                    str(row["plan"]).encode(),
                    b"answer",
                    str(row["answer"]).encode(),
                    b"query_text",
                    str(row["query_text"]).encode(),
                    b"distance",
                    repr(distance).encode(),
                ]
            )
        return response

    @staticmethod
    def _tenant_from(query: str) -> str:
        start = query.index("{") + 1
        end = query.index("}")
        raw = query[start:end]
        return "".join(
            raw[index + 1] if raw[index] == "\\" else raw[index]
            for index in range(len(raw))
            if index == 0 or raw[index - 1] != "\\"
        )

    @staticmethod
    def _range(query: str, field: str) -> tuple[int, int]:
        start = query.index(f"@{field}:[") + len(f"@{field}:[")
        end = query.index("]", start)
        low, high = query[start:end].split()
        return int(low), int(high)

    @staticmethod
    def _exclusive_after(query: str, field: str) -> int | None:
        marker = f"@{field}:[("
        if marker not in query:
            return None
        start = query.index(marker) + len(marker)
        end = query.index(" ", start)
        return int(query[start:end])


def ctx(tenant_id: str = "default") -> RunContext:
    return RunContext(
        run_id="run-valkey-1",
        tenant=TenantContext(
            tenant_id=tenant_id,
            allowed_agents={"docs"},
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def key(
    *,
    tenant_id: str = "default",
    index_version: int = 1,
    vector: list[float] | None = None,
    query: str = "what is the return window?",
) -> CacheKey:
    return CacheKey(
        tenant_id=tenant_id,
        normalized_query=query,
        embedding=vector if vector is not None else mixed_vector(1.0, 0.0),
        index_version=index_version,
    )


def answer(text: str = "Returns take 15 calendar days [d1].") -> CachedAnswer:
    return CachedAnswer(
        plan="docs",
        answer=AgentResult(answer=text, usage=Usage(input_tokens=10, output_tokens=5)),
    )


def cache(
    *,
    client: FakeValkey | None = None,
    embedder: FakeEmbedder | None = None,
    pool: FakePool | None = None,
    clock: Any = None,
) -> tuple[ValkeySemanticCache, FakeValkey, FakeEmbedder]:
    fake = client if client is not None else FakeValkey()
    embed = embedder if embedder is not None else FakeEmbedder()
    built = ValkeySemanticCache(
        client=cast(Any, fake),
        pool=cast(Any, pool if pool is not None else FakePool()),
        embedder=embed,
        clock=clock if clock is not None else (lambda: NOW),
    )
    return built, fake, embed


def test_embedding_bytes_are_little_endian_float32_and_unit_length() -> None:
    payload = embedding_bytes([3.0, 4.0, *([0.0] * (VECTOR_DIMENSION - 2))])
    assert len(payload) == VECTOR_DIMENSION * 4
    values = struct.unpack(f"<{VECTOR_DIMENSION}f", payload)
    assert values[0] == pytest.approx(0.6, abs=1e-6)
    assert values[1] == pytest.approx(0.8, abs=1e-6)
    assert math.sqrt(sum(value * value for value in values)) == pytest.approx(1.0, abs=1e-5)


def test_embedding_bytes_rejects_the_wrong_dimension_and_a_zero_vector_is_zero() -> None:
    with pytest.raises(CacheError, match="1024"):
        embedding_bytes([1.0, 0.0])
    zeros = struct.unpack(f"<{VECTOR_DIMENSION}f", embedding_bytes([0.0] * VECTOR_DIMENSION))
    assert set(zeros) == {0.0}


def test_similarity_is_clamped_to_the_unit_interval() -> None:
    assert clamp_similarity(0.0) == 1.0
    assert clamp_similarity(0.06) == pytest.approx(0.94)
    assert clamp_similarity(-0.5) == 1.0
    assert clamp_similarity(2.0) == 0.0


def test_key_names_carry_the_tenant_token_version_and_expiry() -> None:
    name = entry_key_name("default", 4, NOW + 60, "what is the return window?")
    parts = key_parts(name)
    assert parts == (tenant_token("default"), 4, NOW + 60)
    assert name.startswith(KEY_PREFIX)
    assert key_parts("sb:other:x:1:2") is None
    assert key_parts(b"sb:cache:bad") is None
    assert ":" not in tenant_token("some:tenant")
    # One entry per question: the same question maps to the same key, a
    # different one does not.
    again = entry_key_name("default", 4, NOW + 90, "what is the return window?")
    assert again.split(":")[-1] == name.split(":")[-1]
    other = entry_key_name("default", 4, NOW + 60, "how many orders were late?")
    assert other.split(":")[-1] != name.split(":")[-1]


def test_the_knn_query_filters_tenant_version_and_expiry() -> None:
    query = knn_query(key(tenant_id="default", index_version=3), now=NOW)
    assert "(@tenant_id:{default})" in query
    assert "(@index_version:[3 3])" in query
    assert f"(@expires_at:[({NOW} +inf])" in query
    assert query.endswith("=>[KNN 5 @embedding $vec AS distance]")


def test_a_tenant_with_tag_characters_cannot_widen_the_filter() -> None:
    assert escape_tag("a,b") == "a\\,b"
    assert escape_tag("star*") == "star\\*"
    assert escape_tag("default") == "default"
    assert "a\\,b" in knn_query(key(tenant_id="a,b"), now=NOW)


def test_parse_url_reads_the_database_and_rejects_another_scheme() -> None:
    options = parse_url("valkey://user:secret@valkey:6380/2")
    assert options["host"] == "valkey"
    assert options["port"] == 6380
    assert options["db"] == 2
    assert options["username"] == "user"
    assert options["password"] == "secret"
    assert options["ssl"] is False
    assert parse_url("rediss://cache:6379/0")["ssl"] is True
    with pytest.raises(ConfigError, match="valkey"):
        parse_url("postgres://localhost/db")


def test_the_adapter_requires_the_1024_dimension_embedder() -> None:
    with pytest.raises(ConfigError, match="1024"):
        ValkeySemanticCache(
            client=cast(Any, FakeValkey()),
            pool=cast(Any, FakePool()),
            embedder=FakeEmbedder(dimension=8),
        )


async def test_ensure_index_sends_the_hash_index_schema_once() -> None:
    built, fake, _embedder = cache()
    await built.ensure_index()
    await built.ensure_index()
    creates = [command for command in fake.commands if command[0] == "FT.CREATE"]
    assert len(creates) == 1
    args = creates[0]
    assert args[1] == INDEX_NAME
    assert args[2:6] == ("ON", "HASH", "PREFIX", 1)
    assert args[6] == KEY_PREFIX
    schema = args[8:]
    assert schema[0:2] == ("tenant_id", "TAG")
    assert schema[2:4] == ("index_version", "NUMERIC")
    assert schema[4:6] == ("expires_at", "NUMERIC")
    assert schema[6:10] == ("embedding", "VECTOR", "HNSW", "6")
    assert "FLOAT32" in schema and "COSINE" in schema
    assert str(VECTOR_DIMENSION) in schema


async def test_an_existing_index_is_not_an_error() -> None:
    built, fake, _embedder = cache(client=FakeValkey(index_exists=True))
    await built.start()
    assert any(command[0] == "FT.INFO" for command in fake.commands)


async def test_a_stale_index_with_another_dimension_is_a_cache_error() -> None:
    built, _fake, _embedder = cache(client=FakeValkey(index_exists=True, index_dimension=4))
    with pytest.raises(CacheError, match="DIM 4"):
        await built.ensure_index()


async def test_start_swallows_a_down_store() -> None:
    built, _fake, _embedder = cache(client=FakeValkey(fail_on={"FT.CREATE"}))
    await built.start()
    with pytest.raises(CacheError, match="creation"):
        await built.ensure_index()


async def test_rebuild_index_drops_and_creates_leaving_the_keys_alone() -> None:
    built, fake, _embedder = cache()
    await built.store(key(), answer())
    assert len(fake.hashes) == 1
    await built.rebuild_index()
    commands = [command[0] for command in fake.commands]
    assert "FT.DROPINDEX" in commands
    assert commands.count("FT.CREATE") == 2
    assert len(fake.hashes) == 1, "a rebuild must not delete a cached key"
    assert fake.index_exists


async def test_rebuild_index_tolerates_a_missing_index() -> None:
    built, _fake, _embedder = cache()
    await built.rebuild_index()
    assert built.name == "valkey_semantic"


async def test_key_for_embeds_and_reads_the_index_version() -> None:
    built, _fake, embedder = cache(pool=FakePool(version=9))
    built_key = await built.key_for(ctx(), "  What is the RETURN window? ")
    assert built_key.tenant_id == "default"
    assert built_key.normalized_query == "what is the return window?"
    assert built_key.index_version == 9
    assert len(built_key.embedding) == VECTOR_DIMENSION
    assert embedder.calls == [["  What is the RETURN window? "]]


async def test_key_for_maps_failures_to_cache_errors() -> None:
    built, _fake, _embedder = cache(embedder=FakeEmbedder(fail=True))
    with pytest.raises(CacheError, match="embedding"):
        await built.key_for(ctx(), "hello")
    built, _fake, _embedder = cache(pool=FakePool(fail=True))
    with pytest.raises(CacheError, match="index version"):
        await built.key_for(ctx(), "hello")


async def test_lookup_misses_on_an_empty_cache_and_records_it_on_the_span() -> None:
    built, _fake, _embedder = cache()
    store = MemoryRunStore()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(store)))
    with cache_lookup_span(provider.get_tracer("test"), run_id=RUN_ID, threshold=0.94):
        assert await built.lookup(key(), threshold=0.94) is None
    span = next(item for item in store.spans if item["name"] == "cache.lookup")
    assert span["attributes"]["switchboard.cache.hit"] is False
    assert "switchboard.cache.similarity" not in span["attributes"]


async def test_a_stored_entry_hits_and_round_trips_the_answer() -> None:
    built, fake, _embedder = cache()
    stored = key()
    await built.store(stored, answer())
    name = next(iter(fake.hashes))
    assert fake.ttls[name] == DEFAULT_TTL_DAYS * 86400
    row = fake.hashes[name]
    assert row["tenant_id"] == "default"
    assert row["index_version"] == 1
    assert len(row["embedding"]) == VECTOR_DIMENSION * 4
    hit = await built.lookup(stored, threshold=0.94)
    assert hit is not None
    assert hit.similarity == pytest.approx(1.0)
    assert hit.entry.plan == "docs"
    assert hit.entry.answer.answer.startswith("Returns")
    assert row["hits"] == 1


async def test_lookup_is_tenant_scoped() -> None:
    built, _fake, _embedder = cache()
    await built.store(key(tenant_id="default"), answer())
    hit = await built.lookup(key(tenant_id="default"), threshold=0.94)
    assert hit is not None
    assert await built.lookup(key(tenant_id="seller_a"), threshold=0.0) is None


async def test_lookup_is_index_version_scoped() -> None:
    built, _fake, _embedder = cache()
    await built.store(key(index_version=1), answer())
    assert await built.lookup(key(index_version=1), threshold=0.94) is not None
    assert await built.lookup(key(index_version=2), threshold=0.0) is None


async def test_lookup_excludes_an_expired_entry() -> None:
    clock = {"now": NOW}
    built, _fake, _embedder = cache(clock=lambda: clock["now"])
    stored = key()
    await built.store(stored, answer(), ttl_days=1)
    assert await built.lookup(stored, threshold=0.94) is not None
    clock["now"] = NOW + 2 * 86400
    assert await built.lookup(stored, threshold=0.0) is None


async def test_lookup_hits_at_the_threshold_and_misses_below_it() -> None:
    built, _fake, _embedder = cache()
    # cos = 0.94 exactly between these two unit vectors.
    await built.store(key(vector=mixed_vector(0.94, math.sqrt(1 - 0.94**2))), answer())
    query = mixed_vector(1.0, 0.0)
    probe = await built.lookup(key(vector=query), threshold=0.0)
    assert probe is not None
    similarity = probe.similarity
    assert similarity == pytest.approx(0.94, abs=1e-5)
    assert await built.lookup(key(vector=query), threshold=similarity + 1e-4) is None
    hit = await built.lookup(key(vector=query), threshold=similarity)
    assert hit is not None
    assert hit.similarity == pytest.approx(similarity)


async def test_similarity_is_clamped_from_a_negative_distance() -> None:
    built, _fake, _embedder = cache(client=FakeValkey(forced_distance=-0.25))
    await built.store(key(), answer())
    hit = await built.lookup(key(), threshold=0.94)
    assert hit is not None
    assert hit.similarity == 1.0


async def test_a_distance_above_one_clamps_the_similarity_to_zero() -> None:
    built, _fake, _embedder = cache(client=FakeValkey(forced_distance=2.0))
    await built.store(key(), answer())
    assert await built.lookup(key(), threshold=0.5) is None


async def test_store_writes_a_real_ttl_and_prunes_older_versions_and_expired_keys() -> None:
    clock = {"now": NOW}
    built, fake, _embedder = cache(clock=lambda: clock["now"])
    await built.store(key(index_version=1), answer())
    clock["now"] = NOW + 10
    await built.store(key(index_version=1), answer())
    assert len(fake.hashes) == 2
    # A newer index version makes the older entries stale in the same call.
    clock["now"] = NOW + 20
    await built.store(key(index_version=2), answer())
    versions = sorted(int(row["index_version"]) for row in fake.hashes.values())
    assert versions == [2]
    # An expired entry of the current version is pruned on the next store.
    clock["now"] = NOW + 30
    await built.store(key(index_version=3), answer(), ttl_days=1)
    clock["now"] = NOW + 30 + 2 * 86400
    await built.store(key(index_version=3), answer(), ttl_days=1)
    rows = [row for row in fake.hashes.values() if int(row["index_version"]) == 3]
    assert len(rows) == 1


async def test_store_skips_an_empty_embedding() -> None:
    built, fake, _embedder = cache()
    await built.store(key(vector=[]), answer())
    assert fake.hashes == {}


async def test_store_rejects_a_non_positive_ttl() -> None:
    built, _fake, _embedder = cache()
    with pytest.raises(ConfigError, match="ttl_days"):
        await built.store(key(), answer(), ttl_days=0)


async def test_invalidate_removes_only_that_tenant_and_counts_the_keys() -> None:
    built, fake, _embedder = cache()
    await built.store(key(tenant_id="default", query="one"), answer())
    await built.store(key(tenant_id="default", query="two"), answer())
    await built.store(key(tenant_id="seller_a", query="one"), answer())
    assert len(fake.hashes) == 3
    assert await built.invalidate("default") == 2
    assert len(fake.hashes) == 1
    assert next(iter(fake.hashes.values()))["tenant_id"] == "seller_a"
    assert await built.invalidate("default") == 0


async def test_storing_the_same_question_again_refreshes_one_entry() -> None:
    built, fake, _embedder = cache()
    await built.store(key(query="what is the return window?"), answer())
    await built.store(key(query=normalize_query("  What is the RETURN window?  ")), answer())
    assert len(fake.hashes) == 1, "the normalized question is the same entry"
    assert await built.lookup(key(), threshold=0.94) is not None


async def test_scan_pages_through_every_key() -> None:
    built, fake, _embedder = cache(client=FakeValkey(scan_batch=1))
    for index in range(5):
        await built.store(key(query=f"question {index}"), answer())
    assert len(fake.hashes) == 5
    assert await built.invalidate("default") == 5
    assert fake.hashes == {}


async def test_a_lookup_failure_is_a_cache_error() -> None:
    built, _fake, _embedder = cache(client=FakeValkey(fail_on={"FT.SEARCH"}))
    with pytest.raises(CacheError, match="lookup"):
        await built.lookup(key(), threshold=0.94)


async def test_a_store_failure_is_a_cache_error_and_marks_the_span() -> None:
    built, _fake, _embedder = cache(client=FakeValkey(fail_on={"HSET"}))
    store = MemoryRunStore()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(MemorySpanExporter(store)))
    with (
        pytest.raises(CacheError, match="store"),
        provider.get_tracer("test").start_as_current_span("cache.store"),
    ):
        await built.store(key(), answer())
    span = next(item for item in store.spans if item["name"] == "cache.store")
    assert span["status"] != "unset"


async def test_an_invalidate_failure_is_a_cache_error() -> None:
    built, _fake, _embedder = cache(client=FakeValkey(fail_on={"SCAN"}))
    with pytest.raises(CacheError, match="invalidation"):
        await built.invalidate("default")


async def test_describe_reports_the_client_and_the_search_module() -> None:
    class Describing(FakeValkey):
        async def info(self, section: str = "default") -> dict[str, Any]:
            return {"valkey_version": "9.0.7"}

    built, _fake, _embedder = cache(client=Describing())
    described = await built.describe()
    assert described["server"] == "9.0.7"
    assert described["index"] == INDEX_NAME
    assert described["client"].startswith("valkey-py")


def test_the_registry_exposes_the_valkey_adapter_and_the_default_stays_postgres() -> None:
    registry = Registry()
    assert "valkey_semantic" in registry.names(CACHES)
    assert registry.load(CACHES, "valkey_semantic") is ValkeySemanticCache
    settings = load_settings(
        REPO / "config",
        {
            "LMSTUDIO_BASE_URL": "http://host.docker.internal:18080/v1",
            "LMSTUDIO_MODEL": "qwen3-8b",
            "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
            "MCP_OLIST_URL": "http://localhost:8001",
            "MCP_REVIEWS_URL": "http://localhost:8002",
        },
    )
    assert settings.platform.cache == "pg_semantic"
