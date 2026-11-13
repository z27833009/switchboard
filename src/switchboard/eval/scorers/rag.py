"""RAG scores: recall@k and MRR over retrieved chunk keys (design.md 20.1).

A dataset stores portable chunk keys (``source#ordinal``) rather than database
ids, because a fresh ingest creates new ids. The suite resolves keys to ids at
eval time and turns retrieved chunks back into keys before scoring, so the
scorers work on strings and stay free of the database.
"""

from __future__ import annotations

from collections.abc import Sequence

from switchboard.core.errors import ConfigError
from switchboard.core.models import EvalCase, EvalOutcome, Score
from switchboard.eval.scorers.judge import judge_metric


def chunk_key(source: str, ordinal: int) -> str:
    """The stable key used by the rag dataset and the scorer."""
    return f"{source}#{ordinal}"


def parse_chunk_key(key: str) -> tuple[str, int]:
    source, separator, ordinal = key.rpartition("#")
    if not separator or not source or not ordinal.isdigit():
        raise ConfigError(f"chunk key {key!r} must look like 'source.md#0'")
    return source, int(ordinal)


def unique_ordered(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def recall_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int = 5) -> float:
    """Share of the relevant keys found in the first k results.

    Duplicates in either list are ignored. A case with no relevant keys is a
    dataset error, not a score of zero, because it cannot mean anything.
    """
    if k < 1:
        raise ConfigError("k must be at least 1")
    relevant_set = set(relevant)
    if not relevant_set:
        raise ConfigError("a rag case needs at least one relevant chunk key")
    top = set(unique_ordered(retrieved)[:k])
    return len(top & relevant_set) / len(relevant_set)


def reciprocal_rank(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    """1 / rank of the first relevant result, or 0.0 when none is returned."""
    relevant_set = set(relevant)
    if not relevant_set:
        raise ConfigError("a rag case needs at least one relevant chunk key")
    for rank, key in enumerate(unique_ordered(retrieved), start=1):
        if key in relevant_set:
            return 1.0 / rank
    return 0.0


def score_rag(case: EvalCase, outcome: EvalOutcome) -> Score:
    strategies = outcome.details.get("strategies")
    if not isinstance(strategies, dict) or not strategies:
        return Score(case_id=case.id, metrics={}, passed=None)
    metrics: dict[str, float] = {}
    passed = True
    for name, payload in strategies.items():
        if not isinstance(payload, dict):
            raise ConfigError(f"case {case.id!r} strategy {name!r} has no result object")
        recall = payload.get("recall_at_5")
        mrr = payload.get("mrr")
        if not isinstance(recall, int | float) or not isinstance(mrr, int | float):
            raise ConfigError(f"case {case.id!r} strategy {name!r} is missing scores")
        metrics[f"recall_at_5_{name}"] = float(recall)
        metrics[f"mrr_{name}"] = float(mrr)
        passed = passed and float(recall) > 0.0
    # Judge mode (T3.04) adds one answer-quality signal per case. It never
    # changes the retrieval scores or ``passed``: the default rag measurement
    # stays retrieval only and a judge rejection is a separate number.
    judged = outcome.details.get("judge")
    if isinstance(judged, dict):
        metric = judge_metric(judged.get("verdict"))
        if metric is not None:
            metrics["judge_acceptable"] = metric
    return Score(case_id=case.id, metrics=metrics, passed=passed)


__all__ = [
    "chunk_key",
    "parse_chunk_key",
    "recall_at_k",
    "reciprocal_rank",
    "score_rag",
    "unique_ordered",
]
