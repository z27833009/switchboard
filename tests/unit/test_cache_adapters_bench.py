"""Cache adapter benchmark: the measurement loop and the report, with a fake cache.

The fake ``Cache`` keeps its entries in a dictionary keyed by tenant and answers
a lookup with the nearest stored vector, so the counts the report publishes are
tested without PostgreSQL, Valkey or an embedding model. The live run is
``switchboard eval cache-adapters --provider lmstudio`` and its output is
``evals/reports/cache-adapters.md``.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    CachedAnswer,
    CacheHit,
    CacheKey,
    RunContext,
)
from switchboard.eval.cache_adapters import (
    PairSpec,
    build_pairs,
    build_report,
    load_pair_cases,
    measure_adapter,
    render_markdown,
    summarize_measurement,
    write_report,
)

REPO = Path(__file__).resolve().parents[2]


def unit_vector(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else [0.0] * len(values)


def pair(
    case_id: str,
    *,
    should_hit: bool,
    similarity: float,
    pair_type: str = "paraphrase",
) -> PairSpec:
    """A pair whose stored A and queried B have the given cosine similarity."""
    vector_a = unit_vector([1.0, 0.0, 0.0, 0.0])
    vector_b = unit_vector(
        [similarity * vector_a[0], math.sqrt(max(0.0, 1 - similarity**2)), 0.0, 0.0]
    )
    return PairSpec(
        case_id=case_id,
        query_a=f"question a {case_id}",
        query_b=f"question b {case_id}",
        should_hit=should_hit,
        pair_type=pair_type,
        vector_a=vector_a,
        vector_b=vector_b,
    )


class FakeCache:
    """The ``Cache`` port in memory: nearest stored vector per tenant."""

    name = "fake_cache"

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.entries: dict[str, list[tuple[list[float], CachedAnswer]]] = {}
        self.fail_on = fail_on or set()
        self.calls: list[str] = []
        self.invalidated: list[str] = []

    async def key_for(self, ctx: RunContext, query: str) -> CacheKey:  # pragma: no cover
        return CacheKey(
            tenant_id=ctx.tenant.tenant_id,
            normalized_query=query,
            embedding=[1.0, 0.0, 0.0, 0.0],
            index_version=1,
        )

    async def lookup(self, key: CacheKey, *, threshold: float = 0.92) -> CacheHit | None:
        self.calls.append("lookup")
        if "lookup" in self.fail_on:
            raise RuntimeError("lookup down")
        rows = self.entries.get(key.tenant_id, [])
        if not rows:
            return None
        best_similarity = -1.0
        best: CachedAnswer | None = None
        for vector, entry in rows:
            similarity = sum(a * b for a, b in zip(vector, key.embedding, strict=True))
            if similarity > best_similarity:
                best_similarity, best = similarity, entry
        if best is None or best_similarity < threshold:
            return None
        return CacheHit(entry=best, similarity=max(0.0, min(1.0, best_similarity)))

    async def store(self, key: CacheKey, answer: CachedAnswer, *, ttl_days: int = 7) -> None:
        self.calls.append("store")
        if "store" in self.fail_on:
            raise RuntimeError("store down")
        self.entries.setdefault(key.tenant_id, []).append((list(key.embedding), answer))

    async def invalidate(self, tenant_id: str) -> int:
        self.calls.append("invalidate")
        if "invalidate" in self.fail_on:
            raise RuntimeError("invalidate down")
        removed = len(self.entries.pop(tenant_id, []))
        self.invalidated.append(tenant_id)
        return removed


async def test_the_benchmark_counts_hits_false_hits_and_invalidations() -> None:
    cache = FakeCache()
    pairs = [
        pair("p1", should_hit=True, similarity=0.99),
        pair("p2", should_hit=True, similarity=0.60),
        pair("n1", should_hit=False, similarity=0.95),
        pair("n2", should_hit=False, similarity=0.50, pair_type="near_miss"),
    ]
    measurement = await measure_adapter(
        cache,
        pairs,
        tenant_id="cache_bench",
        index_version=1,
        threshold=0.94,
        repeats=2,
        warmup=1,
    )
    summary = summarize_measurement(measurement)

    assert summary["lookups"] == 8
    assert summary["positive_lookups"] == 4
    assert summary["negative_lookups"] == 4
    assert summary["hits"] == 2, "only p1 reaches the threshold"
    assert summary["false_hits"] == 2, "n1 is a false hit, n2 is below it"
    assert summary["hit_rate"] == pytest.approx(0.5)
    assert summary["false_hit_rate"] == pytest.approx(0.5)
    assert summary["accuracy"] == pytest.approx(0.5)
    assert summary["errors"] == 0
    assert summary["invalidate_calls"] == len(pairs), "one clear before every pair"
    # Each pair stores two entries and clears what the previous pair (and the
    # warmup) left: 1, then 2, 2, 2. The final cleanup is not counted.
    assert summary["invalidated_entries"] == 7
    assert not cache.entries, "the benchmark clears the tenant once more at the end"
    assert measurement.pairs[0].similarity_probe == pytest.approx(0.99, abs=1e-6)
    assert measurement.pairs[1].similarity_probe == pytest.approx(0.60, abs=1e-6)
    assert measurement.lookup_ms and measurement.store_ms and measurement.invalidate_ms


async def test_the_benchmark_records_an_error_instead_of_losing_the_run() -> None:
    cache = FakeCache(fail_on={"lookup"})
    pairs = [pair("p1", should_hit=True, similarity=0.99)]
    measurement = await measure_adapter(
        cache,
        pairs,
        tenant_id="cache_bench",
        index_version=1,
        threshold=0.94,
        repeats=2,
        warmup=0,
    )
    summary = summarize_measurement(measurement)
    assert summary["lookups"] == 0
    assert summary["errors"] >= 2
    assert "lookup down" in summary["error_samples"][0]


async def test_summarize_an_empty_measurement_is_zero_not_a_crash() -> None:
    cache = FakeCache()
    measurement = await measure_adapter(
        cache,
        [],
        tenant_id="cache_bench",
        index_version=1,
        threshold=0.94,
        repeats=1,
        warmup=0,
    )
    summary = summarize_measurement(measurement)
    assert summary["lookups"] == 0
    assert summary["hit_rate"] == 0.0
    assert summary["lookup_latency_ms"]["p50"] == 0.0


def test_build_pairs_requires_a_vector_for_every_question() -> None:
    class Case:
        id = "c001"
        split = "test"
        payload: ClassVar[dict[str, Any]] = {
            "query_a": "a",
            "query_b": "b",
            "should_hit": True,
            "pair_type": "paraphrase",
        }

    with pytest.raises(ConfigError, match="c001"):
        build_pairs([Case()], {"a": [1.0]})


def test_the_frozen_dataset_loads_into_pairs() -> None:
    cases, split, dataset_count = load_pair_cases("v5", REPO / "evals" / "datasets")
    assert split == "test"
    assert dataset_count == 50
    assert len(cases) == 50
    positives = [case for case in cases if case.payload["should_hit"]]
    assert len(positives) == 25


async def test_the_report_renders_counts_versions_and_notes(tmp_path: Path) -> None:
    fake = FakeCache()
    pairs = [
        pair("p1", should_hit=True, similarity=0.99),
        pair("n1", should_hit=False, similarity=0.95, pair_type="near_miss"),
    ]
    measurement = await measure_adapter(
        fake,
        pairs,
        tenant_id="cache_bench",
        index_version=1,
        threshold=0.94,
        repeats=3,
        warmup=0,
    )
    report = build_report(
        adapted=[
            {
                "name": "fake_cache",
                "summary": summarize_measurement(measurement),
                "pairs": [
                    {
                        "case_id": row.case_id,
                        "pair_type": row.pair_type,
                        "should_hit": row.should_hit,
                        "hits": row.hits,
                        "false_hits": row.false_hits,
                        "lookups": row.lookups,
                        "similarity_probe": row.similarity_probe,
                    }
                    for row in measurement.pairs
                ],
                "describe": {"server": "fake"},
            }
        ],
        pairs=pairs,
        dataset_version="v5",
        dataset_path="evals/datasets/v5/cache.jsonl",
        split="test",
        dataset_case_count=50,
        threshold=0.94,
        repeats=3,
        warmup=0,
        tenant_id="cache_bench",
        index_version=1,
        embedder_source="mock (deterministic sha256, dimension 1024)",
        embedder_dimension=1024,
        embedding_ms=12.5,
        embedding_vectors=4,
        provider="mock",
        model="deterministic mock embedder",
        environment={"valkey_image": "valkey/valkey-bundle:9.0-alpine"},
        generated_at=datetime.now(UTC),
        wall_time_s=1.5,
        transport_control={
            "samples": 2,
            "repr_literal_bytes": 20061,
            "compact_literal_bytes": 9738,
            "repr_latency_ms": {"p50": 45.0, "p95": 48.0},
            "compact_latency_ms": {"p50": 2.0, "p95": 2.6},
        },
    )
    markdown = render_markdown(report)
    assert "| fake_cache | 3 | 3 | 1.0000 | 3 | 3 | 1.0000 | 0.5000 | 0 |" in markdown
    assert "valkey/valkey-bundle:9.0-alpine" in markdown
    assert "Where the PostgreSQL latency comes from" in markdown
    assert "None. Every store" in markdown
    assert "\u2014" not in markdown, "no em dashes in a committed document"

    markdown_path, json_path = write_report(report, tmp_path, name="cache-adapters-test")
    assert markdown_path.name == "cache-adapters-test.md"
    payload: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["pair_count"] == 2
    assert payload["adapters"][0]["hits"] == 3
    assert payload["transport_control"]["compact_literal_bytes"] == 9738


def test_the_benchmark_tenant_is_never_a_configured_tenant() -> None:
    from switchboard.config import load_settings

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
    assert "cache_bench" not in settings.tenants.tenants
    assert set(settings.tenants.tenants) == {"default", "seller_a"}
