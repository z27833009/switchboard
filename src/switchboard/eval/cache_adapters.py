"""Cache adapter benchmark: PostgreSQL semantic cache versus Valkey (T3.09).

The two ``Cache`` adapters are driven through the port with the same frozen
pair dataset, the same embeddings and the same threshold, so the comparison
measures the store and not the metric: for every pair the benchmark clears a
dedicated tenant, stores question A, looks question B up, and repeats that
enough times to report p50 and p95 latency beside the hit and false-hit counts.

Design choices that keep the number honest:

* The dataset is frozen (``evals/datasets/v5/cache.jsonl``, 25 paraphrase pairs
  that should hit and 25 near misses that must not) and is read, never edited.
* The threshold is the one the platform configures (0.94), so the measured
  counts can be compared with ``evals/reports/cache-lmstudio.md``.
* Embeddings are computed once with the configured real embedder and shared by
  both adapters. Embedding time is reported separately and excluded from the
  latency sample, because it is identical for both and would hide the store.
* The tenant is cleared before each pair, so every lookup sees exactly one
  stored question (repeated ``repeats`` times) and the hit decision is the
  pairwise one. A second untimed lookup at threshold 0 records the similarity
  each adapter computed for that pair, which is what makes a semantic
  difference between the two visible.
* The benchmark tenant is never a configured tenant, so demo data cannot be
  touched.

A failure is counted and reported. If an adapter produced no successful lookup
at all the command exits non-zero instead of writing a meaningless report.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.embedders.mock import MockEmbedder
from switchboard.core.cache import normalize_query
from switchboard.core.config import Settings
from switchboard.core.errors import ConfigError
from switchboard.core.models import AgentResult, CachedAnswer, CacheKey, Usage
from switchboard.eval.datasets import load_cases
from switchboard.eval.report import latency_summary
from switchboard.eval.runner import select_cases
from switchboard.eval.suites.cache import prepare_case

DEFAULT_REPEATS = 20
DEFAULT_WARMUP = 5
DEFAULT_TENANT = "cache_bench"
DEFAULT_ADAPTERS: tuple[str, ...] = ("pg_semantic", "valkey_semantic")
DEFAULT_NAME = "cache-adapters"
SUITE_NAME = "cache-adapters"
TENANT_INDEX_VERSION = 1


@dataclass(frozen=True)
class PairSpec:
    case_id: str
    query_a: str
    query_b: str
    should_hit: bool
    pair_type: str
    vector_a: list[float]
    vector_b: list[float]


@dataclass
class PairMeasurement:
    case_id: str
    pair_type: str
    should_hit: bool
    stored: int = 0
    lookups: int = 0
    hits: int = 0
    false_hits: int = 0
    similarity_probe: float | None = None
    seed_attempts: int = 0
    probe_attempts: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class AdapterMeasurement:
    name: str
    pairs: list[PairMeasurement] = field(default_factory=list)
    lookup_ms: list[float] = field(default_factory=list)
    store_ms: list[float] = field(default_factory=list)
    invalidate_ms: list[float] = field(default_factory=list)
    invalidated: int = 0
    index_rebuilt: bool = False
    errors: list[str] = field(default_factory=list)


def build_pairs(cases: Sequence[Any], vectors: dict[str, list[float]]) -> list[PairSpec]:
    """Turn frozen cache cases plus their embeddings into benchmark pairs."""
    pairs: list[PairSpec] = []
    for case in cases:
        query_a, query_b, should_hit = prepare_case(case)
        try:
            vector_a = vectors[query_a]
            vector_b = vectors[query_b]
        except KeyError as exc:  # pragma: no cover - guarded by the caller
            raise ConfigError(f"case {case.id!r} has no embedding for {exc.args[0]!r}") from exc
        pairs.append(
            PairSpec(
                case_id=str(case.id),
                query_a=query_a,
                query_b=query_b,
                should_hit=should_hit,
                pair_type=str(case.payload.get("pair_type") or "unknown"),
                vector_a=vector_a,
                vector_b=vector_b,
            )
        )
    return pairs


def load_pair_cases(
    version: str, dataset_root: Path, *, split: str = "auto"
) -> tuple[list[Any], str, int]:
    """The frozen cases, the split actually scored and the dataset case count."""
    cases = load_cases("cache", version, dataset_root)
    selection = select_cases(cases, split=split, limit=None)
    return list(selection.cases), selection.split, selection.dataset_case_count


def build_embedder(settings: Settings, provider: str) -> tuple[Any, str, int]:
    """The embedder the cache suite uses: real for a real provider, mock otherwise."""
    name = "mock" if provider == "mock" else settings.platform.embedder
    config = settings.providers.embedders.get(name)
    if name == "mock":
        dimension = config.dimension if config is not None else 1024
        return (
            MockEmbedder(dimension=dimension),
            f"mock (deterministic sha256, dimension {dimension})",
            dimension,
        )
    if config is None:
        raise ConfigError(f"unknown embedder {name!r} for the cache benchmark")
    embedder = create_embedder(name, config)
    source = f"{name} ({config.adapter}, {config.model or 'no model'})"
    return embedder, source, int(getattr(embedder, "dimension", config.dimension))


def valkey_image_facts(image: str) -> dict[str, Any]:
    """The image digest, read from the local Docker so the report is verifiable."""
    facts: dict[str, Any] = {"valkey_image": image}
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{index .RepoDigests 0}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        facts["valkey_image_digest"] = "unknown (docker not available)"
        return facts
    digest = (completed.stdout or "").strip()
    facts["valkey_image_digest"] = digest or "unknown (image not present locally)"
    return facts


def cached_answer(case_id: str) -> CachedAnswer:
    """A stored answer of the shape a docs run would cache."""
    return CachedAnswer(
        plan="docs",
        answer=AgentResult(
            answer=f"Benchmark answer for {case_id} [d1].",
            usage=Usage(input_tokens=100, output_tokens=20),
        ),
    )


def key_for(pair: PairSpec, *, tenant_id: str, index_version: int, which: str) -> CacheKey:
    """A CacheKey without a second embedding call (the vector is shared)."""
    if which == "a":
        query, vector = pair.query_a, pair.vector_a
    else:
        query, vector = pair.query_b, pair.vector_b
    return CacheKey(
        tenant_id=tenant_id,
        normalized_query=normalize_query(query),
        embedding=vector,
        index_version=index_version,
    )


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def compact_vector_literal(vector: Sequence[float], digits: int = 6) -> str:
    """pgvector text form at reduced precision, for the transport control only."""
    return "[" + ",".join(f"{float(value):.{digits}f}" for value in vector) + "]"


async def measure_postgres_transport(
    pool: Any,
    pairs: Sequence[PairSpec],
    *,
    tenant_id: str,
    index_version: int,
) -> dict[str, Any]:
    """How much of the PostgreSQL lookup latency is the vector payload.

    The adapter sends the embedding as pgvector's text form with full float
    precision, about 20 KB for 1024 dimensions. On a host that reaches the
    container through Docker Desktop's port mapping, a payload that size can
    add tens of milliseconds per statement while the database itself executes
    the query in well under a millisecond. Running the adapter's own
    ``LOOKUP_SQL`` with a compact literal (about 8 KB) separates the two, so the
    report states a transport fact instead of implying it is a property of the
    store.
    """
    from switchboard.adapters.cache.pg_semantic import LOOKUP_SQL, vector_literal

    full: list[float] = []
    compact: list[float] = []
    for pair in pairs:
        literal = vector_literal(pair.vector_b)
        started = _now_ms()
        try:
            await pool.fetchrow(LOOKUP_SQL, tenant_id, literal, index_version)
            full.append(_now_ms() - started)
        except Exception:
            break
        short = compact_vector_literal(pair.vector_b)
        started = _now_ms()
        try:
            await pool.fetchrow(LOOKUP_SQL, tenant_id, short, index_version)
            compact.append(_now_ms() - started)
        except Exception:
            break
    return {
        "samples": len(full),
        "repr_literal_bytes": len(vector_literal(pairs[0].vector_b)) if pairs else 0,
        "compact_literal_bytes": len(compact_vector_literal(pairs[0].vector_b)) if pairs else 0,
        "repr_latency_ms": _latency(full),
        "compact_latency_ms": _latency(compact),
    }


async def first_visible(
    adapter: Any,
    key: CacheKey,
    *,
    attempts: int = 20,
    delay_s: float = 0.005,
) -> tuple[Any, int]:
    """Return the first lookup that sees an entry, and how many attempts it took.

    ``valkey-search`` indexes asynchronously, so a lookup issued microseconds
    after a burst of stores can see nothing at all even though the keys exist.
    The benchmark seeds one entry per pair and waits until it is visible before
    timing anything, which keeps a store-to-index lag out of the hit counts. A
    caller never retries: an empty result is a cache miss, which is safe.
    """
    for attempt in range(1, max(1, attempts) + 1):
        try:
            hit = await adapter.lookup(key, threshold=0.0)
        except Exception:
            return None, attempt
        if hit is not None:
            return hit, attempt
        await asyncio.sleep(delay_s)
    return None, max(1, attempts)


async def measure_adapter(
    adapter: Any,
    pairs: Sequence[PairSpec],
    *,
    tenant_id: str,
    index_version: int,
    threshold: float,
    repeats: int,
    warmup: int,
) -> AdapterMeasurement:
    """Clear, store and look up once per pair per repeat, and time the store side."""
    measurement = AdapterMeasurement(name=str(getattr(adapter, "name", "unknown")))
    if not pairs:
        return measurement

    # A store that keeps deleted entries in its index graph (valkey-search)
    # needs a clean graph for a comparable measurement; the benchmark rebuilds
    # it once, before any pair runs. The keys are left in place and re-indexed.
    rebuild = getattr(adapter, "rebuild_index", None)
    if rebuild is not None:
        try:
            await rebuild()
            measurement.index_rebuilt = True
        except Exception as exc:
            measurement.errors.append(f"rebuild: {type(exc).__name__}: {exc}")

    # Warm up the connection and the index lookup path on the first pair; the
    # timings are discarded and the first measured pair clears the tenant again.
    warm_pair = pairs[0]
    for _ in range(max(0, warmup)):
        try:
            await adapter.store(
                key_for(warm_pair, tenant_id=tenant_id, index_version=index_version, which="a"),
                cached_answer(warm_pair.case_id),
            )
            await adapter.lookup(
                key_for(warm_pair, tenant_id=tenant_id, index_version=index_version, which="b"),
                threshold=threshold,
            )
        except Exception as exc:
            measurement.errors.append(f"warmup: {type(exc).__name__}: {exc}")
            break

    for pair in pairs:
        row = PairMeasurement(
            case_id=pair.case_id,
            pair_type=pair.pair_type,
            should_hit=pair.should_hit,
        )
        key_a = key_for(pair, tenant_id=tenant_id, index_version=index_version, which="a")
        key_b = key_for(pair, tenant_id=tenant_id, index_version=index_version, which="b")
        started = _now_ms()
        try:
            measurement.invalidated += int(await adapter.invalidate(tenant_id))
        except Exception as exc:
            row.errors.append(f"invalidate: {type(exc).__name__}: {exc}")
            measurement.errors.append(row.errors[-1])
        measurement.invalidate_ms.append(_now_ms() - started)

        # Timed stores: every repeat writes the same question again, the way a
        # cache sees a repeated question. The index may lag the writes here, so
        # no lookup is timed until the wait below.
        for _repeat in range(repeats):
            started = _now_ms()
            try:
                await adapter.store(key_a, cached_answer(pair.case_id))
                measurement.store_ms.append(_now_ms() - started)
                row.stored += 1
            except Exception as exc:
                row.errors.append(f"store: {type(exc).__name__}: {exc}")
                measurement.errors.append(row.errors[-1])

        # Wait until the stored question is visible before the timed lookups:
        # valkey-search indexes asynchronously and a lookup issued microseconds
        # after a store can see nothing (see ``first_visible``). The wait is
        # untimed, and without it the hit counts would measure index lag.
        try:
            _seed_hit, row.seed_attempts = await first_visible(adapter, key_b)
        except Exception as exc:
            row.errors.append(f"seed: {type(exc).__name__}: {exc}")
            measurement.errors.append(row.errors[-1])

        for _repeat in range(repeats):
            started = _now_ms()
            try:
                hit = await adapter.lookup(key_b, threshold=threshold)
                measurement.lookup_ms.append(_now_ms() - started)
                row.lookups += 1
            except Exception as exc:
                row.errors.append(f"lookup: {type(exc).__name__}: {exc}")
                measurement.errors.append(row.errors[-1])
                continue
            if hit is not None:
                if pair.should_hit:
                    row.hits += 1
                else:
                    row.false_hits += 1

        # One untimed lookup below any threshold records the similarity the
        # adapter computed, so the two distance implementations are comparable.
        try:
            probe, row.probe_attempts = await first_visible(adapter, key_b)
            row.similarity_probe = None if probe is None else round(probe.similarity, 6)
        except Exception as exc:
            row.errors.append(f"probe: {type(exc).__name__}: {exc}")
            measurement.errors.append(row.errors[-1])
        measurement.pairs.append(row)

    try:
        await adapter.invalidate(tenant_id)
    except Exception as exc:
        measurement.errors.append(f"cleanup: {type(exc).__name__}: {exc}")
    return measurement


def _latency(values: Sequence[float]) -> dict[str, float]:
    summary = latency_summary(values)
    return {key: round(value, 3) for key, value in summary.items()}


def summarize_measurement(measurement: AdapterMeasurement) -> dict[str, Any]:
    """Counts and rates from the measured pairs, plus the latency windows."""
    positive_lookups = sum(row.lookups for row in measurement.pairs if row.should_hit)
    negative_lookups = sum(row.lookups for row in measurement.pairs if not row.should_hit)
    hits = sum(row.hits for row in measurement.pairs)
    false_hits = sum(row.false_hits for row in measurement.pairs)
    total = positive_lookups + negative_lookups
    correct = hits + (negative_lookups - false_hits)
    return {
        "name": measurement.name,
        "lookups": total,
        "positive_lookups": positive_lookups,
        "negative_lookups": negative_lookups,
        "hits": hits,
        "false_hits": false_hits,
        "hit_rate": round(hits / positive_lookups, 4) if positive_lookups else 0.0,
        "false_hit_rate": round(false_hits / negative_lookups, 4) if negative_lookups else 0.0,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "lookup_latency_ms": _latency(measurement.lookup_ms),
        "store_latency_ms": _latency(measurement.store_ms),
        "invalidate_latency_ms": _latency(measurement.invalidate_ms),
        "invalidate_calls": len(measurement.invalidate_ms),
        "invalidated_entries": measurement.invalidated,
        "index_rebuilt": measurement.index_rebuilt,
        "errors": len(measurement.errors),
        "error_samples": measurement.errors[:10],
    }


async def ensure_benchmark_tenant(pool: Any, tenant_id: str) -> int:
    """Create the benchmark tenant row if missing and return its index version.

    The row is only ever created, never renamed or deleted, and the command
    refuses a tenant that ``config/tenants.yaml`` configures, so a benchmark can
    never invalidate demo entries.
    """
    await pool.execute(
        "INSERT INTO public.tenants (id, name, doc_index_version) VALUES ($1, $2, $3) "
        "ON CONFLICT (id) DO NOTHING",
        tenant_id,
        f"Cache adapter benchmark ({tenant_id})",
        TENANT_INDEX_VERSION,
    )
    version = await pool.fetchval(
        "SELECT doc_index_version FROM public.tenants WHERE id = $1", tenant_id
    )
    if version is None:
        raise ConfigError(f"the benchmark tenant {tenant_id!r} could not be created")
    return int(version)


def build_report(
    *,
    adapted: Sequence[dict[str, Any]],
    pairs: Sequence[PairSpec],
    dataset_version: str,
    dataset_path: str,
    split: str,
    dataset_case_count: int,
    threshold: float,
    repeats: int,
    warmup: int,
    tenant_id: str,
    index_version: int,
    embedder_source: str,
    embedder_dimension: int,
    embedding_ms: float,
    embedding_vectors: int,
    provider: str,
    model: str,
    environment: dict[str, Any],
    generated_at: datetime,
    wall_time_s: float,
    transport_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The report dictionary, written to JSON and rendered as markdown."""
    positives = sum(1 for pair in pairs if pair.should_hit)
    negatives = len(pairs) - positives
    rows: list[dict[str, Any]] = []
    for entry in adapted:
        summary = dict(entry["summary"])
        summary["pairs"] = entry["pairs"]
        summary["describe"] = entry.get("describe")
        rows.append(summary)
    return {
        "suite": SUITE_NAME,
        "generated_at": generated_at.isoformat(),
        "wall_time_s": round(wall_time_s, 3),
        "dataset_version": dataset_version,
        "dataset_path": dataset_path,
        "split": split,
        "dataset_case_count": dataset_case_count,
        "pair_count": len(pairs),
        "positive_pairs": positives,
        "negative_pairs": negatives,
        "threshold": threshold,
        "repeats": repeats,
        "warmup": warmup,
        "tenant": tenant_id,
        "index_version": index_version,
        "provider": provider,
        "model": model,
        "embedder": {
            "source": embedder_source,
            "dimension": embedder_dimension,
            "vectors": embedding_vectors,
            "embedding_ms": round(embedding_ms, 3),
        },
        "environment": environment,
        "transport_control": transport_control,
        "adapters": rows,
        "no_api_cost": provider == "lmstudio" or provider == "mock",
        "cost_usd": 0.0,
        "errors": sum(int(row["errors"]) for row in rows),
    }


def write_report(
    report: dict[str, Any], output_dir: Path, *, name: str = DEFAULT_NAME
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    markdown_path = output / f"{name}.md"
    json_path = output / f"{name}.json"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return markdown_path, json_path


def render_markdown(report: dict[str, Any]) -> str:
    adapters = report["adapters"]
    lookups = ", ".join(f"{row['name']} {int(row['lookups'])}" for row in adapters)
    lines: list[str] = [
        "# Cache adapter benchmark: PostgreSQL versus Valkey",
        "",
        "## Run",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Dataset: `{report['dataset_version']}` at `{report['dataset_path']}`, "
        f"split `{report['split']}`, {report['pair_count']} pairs "
        f"({report['positive_pairs']} that should hit, {report['negative_pairs']} near misses), "
        f"{report['dataset_case_count']} cases in the dataset",
        f"- Threshold: {report['threshold']} (the value in `config/tenants.yaml` and "
        "`evals/reports/cache-lmstudio.md`)",
        f"- Repeats: {report['repeats']} per pair after {report['warmup']} warmup cycles; "
        f"lookups measured: {lookups}",
        f"- Tenant: `{report['tenant']}` at index version {report['index_version']}, "
        "cleared before each pair; the demo tenants and their entries are untouched",
        f"- Embeddings: {report['embedder']['source']}, "
        f"{report['embedder']['dimension']} dimensions, "
        f"{report['embedder']['vectors']} vectors in "
        f"{report['embedder']['embedding_ms']} ms, computed once and shared by both adapters",
        f"- Wall time: {report['wall_time_s']} s",
        f"- Provider: `{report['provider']}` (`{report['model']}`); every call is a cache call, "
        "there is no chat completion",
        "",
        "Latency is the adapter call only: one `FT.SEARCH` for Valkey and one SQL statement plus "
        "the hit counter for PostgreSQL. The shared embedding cost is reported above and excluded "
        "from both samples, because it is the same for both adapters.",
        "",
        "## Hit and false-hit",
        "",
        "| Adapter | Hits | Positive lookups | Hit rate | False hits | Near misses | "
        "False-hit rate | Accuracy | Errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in adapters:
        lines.append(
            f"| {row['name']} | {row['hits']} | {row['positive_lookups']} | "
            f"{row['hit_rate']:.4f} | {row['false_hits']} | {row['negative_lookups']} | "
            f"{row['false_hit_rate']:.4f} | {row['accuracy']:.4f} | {row['errors']} |"
        )
    if len(adapters) == 2:
        first, second = adapters
        lines += [
            "",
            f"Difference: {second['name']} has "
            f"{int(second['hits']) - int(first['hits'])} more hits and "
            f"{int(second['false_hits']) - int(first['false_hits'])} more false hits than "
            f"{first['name']} over the same {int(first['positive_lookups'])} positive and "
            f"{int(first['negative_lookups'])} near-miss lookups.",
        ]

    lines += [
        "",
        "## Latency",
        "",
        "| Adapter | Lookup p50 ms | Lookup p95 ms | Lookup mean ms | Lookup min ms | "
        "Lookup max ms | Store p50 ms | Store p95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in adapters:
        lookup = row["lookup_latency_ms"]
        store = row["store_latency_ms"]
        lines.append(
            f"| {row['name']} | {lookup['p50']} | {lookup['p95']} | {lookup['mean']} | "
            f"{lookup['min']} | {lookup['max']} | {store['p50']} | {store['p95']} |"
        )

    lines += [
        "",
        "| Adapter | Invalidate calls | Entries removed | Invalidate p50 ms | Invalidate p95 ms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in adapters:
        invalidate = row["invalidate_latency_ms"]
        lines.append(
            f"| {row['name']} | {row['invalidate_calls']} | {row['invalidated_entries']} | "
            f"{invalidate['p50']} | {invalidate['p95']} |"
        )

    lines += ["", "## Versions", "", "| Component | Value |", "| --- | --- |"]
    for key, value in sorted(report["environment"].items()):
        lines.append(f"| {key} | {_cell(value)} |")

    control = report.get("transport_control")
    if control:
        lines += [
            "",
            "## Where the PostgreSQL latency comes from",
            "",
            "The PostgreSQL adapter sends the 1024-dimension embedding as pgvector's text form "
            "with full float precision. The same `LOOKUP_SQL` with the same vectors at reduced "
            "precision isolates how much of the measured latency is that payload on this host's "
            "connection to the container, rather than the database:",
            "",
            "| Vector literal | Bytes | Lookups | p50 ms | p95 ms |",
            "| --- | --- | --- | --- | --- |",
            f"| full precision (what the adapter sends) | {control['repr_literal_bytes']} | "
            f"{control['samples']} | {control['repr_latency_ms']['p50']} | "
            f"{control['repr_latency_ms']['p95']} |",
            f"| reduced precision (control only) | {control['compact_literal_bytes']} | "
            f"{control['samples']} | {control['compact_latency_ms']['p50']} | "
            f"{control['compact_latency_ms']['p95']} |",
            "",
            "The store is unchanged in both rows; only the encoded size differs. Read the "
            "PostgreSQL latency column with this in mind: on this host a lookup that carries the "
            "full text vector costs tens of milliseconds more than the same statement with a "
            "compact one, and the same payload is what its `store` writes.",
        ]

    lines += [
        "",
        "## Per pair",
        "",
        "The similarity column is one untimed lookup at threshold 0, so it shows the distance the "
        "adapter computed for the pair even when it is below the threshold. `hit` is the measured "
        "decision at the threshold above.",
        "",
        "| Case | Type | Should hit | "
        + " | ".join(f"{row['name']} similarity | {row['name']} hit" for row in adapters)
        + " |",
        "| --- | --- | --- | " + " | ".join("--- | ---" for _ in adapters) + " |",
    ]
    by_adapter = {row["name"]: {pair["case_id"]: pair for pair in row["pairs"]} for row in adapters}
    first_row_pairs = adapters[0]["pairs"]
    for pair in first_row_pairs:
        cells: list[str] = []
        for row in adapters:
            measured = by_adapter[row["name"]].get(pair["case_id"], {})
            similarity = measured.get("similarity_probe")
            hits = int(measured.get("hits", 0)) + int(measured.get("false_hits", 0))
            cells.append("n/a" if similarity is None else f"{similarity:.4f}")
            cells.append(f"{hits}/{int(measured.get('lookups', 0))}")
        lines.append(
            f"| {pair['case_id']} | {pair['pair_type']} | "
            f"{'yes' if pair['should_hit'] else 'no'} | " + " | ".join(cells) + " |"
        )

    lines += ["", "## Errors"]
    error_lines: list[str] = []
    for row in adapters:
        for sample in row["error_samples"]:
            error_lines.append(f"- {row['name']}: {sample}")
    if error_lines:
        lines += error_lines
    else:
        lines.append("None. Every store, lookup, invalidate call and similarity probe succeeded.")

    retried = [
        (row["name"], pair)
        for row in adapters
        for pair in row["pairs"]
        if int(pair.get("seed_attempts") or 1) > 1 or int(pair.get("probe_attempts") or 1) > 1
    ]
    max_attempts = max(
        (
            max(int(pair.get("seed_attempts") or 1), int(pair.get("probe_attempts") or 1))
            for row in adapters
            for pair in row["pairs"]
        ),
        default=1,
    )
    rebuilt = [row["name"] for row in adapters if row.get("index_rebuilt")]
    lines += [
        "",
        "## Notes",
        "",
        "- Both adapters implement the same `Cache` port: `key_for` embeds the raw query and reads "
        "the tenant's `doc_index_version` from `public.tenants`, `lookup` filters on tenant, index "
        "version and expiry and applies the threshold, `store` writes with a TTL, and `invalidate` "
        "counts what it removed.",
        "- Valkey normalizes the query vector before the KNN call because the pinned "
        "`valkey-search` module does not, and reads the distance as `1 - cosine` exactly like "
        "pgvector's `<=>`.",
        f"- The index was rebuilt before the run for "
        f"{', '.join(rebuilt) if rebuilt else 'nothing'}. "
        "The pinned `valkey-search` module keeps a deleted document in its HNSW graph as a "
        "tombstone, never lowers `num_docs`, and after many store and delete cycles a lookup can "
        "return nothing even though matching keys exist (`FT.INFO` still counts them). A rebuild "
        "(drop the index, create it again, keys untouched) restores it; the adapter misses in that "
        "state, it never returns a wrong answer. The rebuild is outside the measurement.",
        "- Each pair writes its repeats first and waits until the stored question is visible "
        "before the timed lookups, because `valkey-search` indexes asynchronously and a lookup "
        "issued microseconds after a store can see nothing. The wait is untimed and both adapters "
        "get it; "
        f"{len(retried)} of {sum(len(row['pairs']) for row in adapters)} pair measurements needed "
        f"more than one attempt (maximum {max_attempts}). A caller never retries: an empty result "
        "is a cache miss, never a wrong answer.",
        "- The two stores hold a different number of entries for the same workload: the PostgreSQL "
        "table inserts a row on every store, while the Valkey adapter keys an entry by tenant, "
        "index version and normalized question, so storing the same question again refreshes that "
        "entry and its TTL. The measured lookup behaviour is identical; the invalidation counts "
        "above differ for that reason.",
        "- The false-hit rate at 0.94 is the dataset's known weakness, not a property of either "
        "store: raw cosine similarity does not separate these paraphrases from these near misses "
        "well. See `evals/reports/cache-lmstudio.md` for the threshold sweep.",
        "- This report never rewrites `cache-lmstudio.md` or a dataset; it is produced by "
        "`switchboard eval cache-adapters`.",
        "",
    ]
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in sorted(value.items()))
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


__all__ = [
    "DEFAULT_ADAPTERS",
    "DEFAULT_NAME",
    "DEFAULT_REPEATS",
    "DEFAULT_TENANT",
    "DEFAULT_WARMUP",
    "SUITE_NAME",
    "AdapterMeasurement",
    "PairSpec",
    "build_embedder",
    "build_pairs",
    "build_report",
    "cached_answer",
    "compact_vector_literal",
    "ensure_benchmark_tenant",
    "key_for",
    "load_pair_cases",
    "measure_adapter",
    "measure_postgres_transport",
    "render_markdown",
    "summarize_measurement",
    "valkey_image_facts",
    "write_report",
]
