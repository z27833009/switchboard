"""RAG scorers and portable chunk keys (design.md section 20.1)."""

from __future__ import annotations

import pytest

from switchboard.core.errors import ConfigError
from switchboard.core.models import EvalCase, EvalOutcome, Score
from switchboard.eval.scorers.rag import (
    chunk_key,
    parse_chunk_key,
    recall_at_k,
    reciprocal_rank,
    score_rag,
    unique_ordered,
)


def test_chunk_keys_round_trip() -> None:
    assert chunk_key("returns-policy.md", 2) == "returns-policy.md#2"
    assert parse_chunk_key("returns-policy.md#2") == ("returns-policy.md", 2)
    assert parse_chunk_key("a#b.md#10") == ("a#b.md", 10)


@pytest.mark.parametrize("key", ["returns-policy.md", "#2", "returns-policy.md#x", "#"])
def test_invalid_chunk_keys_are_config_errors(key: str) -> None:
    with pytest.raises(ConfigError, match="must look like"):
        parse_chunk_key(key)


def test_unique_ordered_keeps_the_first_seen_order() -> None:
    assert unique_ordered(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_at_k_counts_the_share_of_relevant_keys() -> None:
    assert recall_at_k(["a", "b", "c"], ["b"], k=5) == 1.0
    assert recall_at_k(["a", "b", "c"], ["b", "d"], k=5) == 0.5
    assert recall_at_k(["a", "b", "c"], ["c"], k=2) == 0.0
    assert recall_at_k(["a", "a", "b"], ["a"], k=1) == 1.0


def test_recall_rejects_an_empty_relevant_list() -> None:
    with pytest.raises(ConfigError, match="at least one relevant"):
        recall_at_k(["a"], [], k=5)


def test_reciprocal_rank_uses_the_first_relevant_position() -> None:
    assert reciprocal_rank(["x", "b", "a"], ["a", "b"]) == 0.5
    assert reciprocal_rank(["a"], ["a"]) == 1.0
    assert reciprocal_rank(["x", "y"], ["a"]) == 0.0


def outcome(strategies: dict[str, dict[str, object]]) -> EvalOutcome:
    return EvalOutcome(case_id="rag001", details={"strategies": strategies})


def test_score_rag_exposes_one_metric_pair_per_strategy() -> None:
    case = EvalCase(id="rag001", query="q", split="test")
    scored: Score = score_rag(
        case,
        outcome(
            {
                "query_as_written": {"recall_at_5": 0.5, "mrr": 1.0},
                "conditional_hybrid": {"recall_at_5": 1.0, "mrr": 0.5},
            }
        ),
    )

    assert scored.metrics == {
        "recall_at_5_query_as_written": 0.5,
        "mrr_query_as_written": 1.0,
        "recall_at_5_conditional_hybrid": 1.0,
        "mrr_conditional_hybrid": 0.5,
    }
    assert scored.passed is True


def test_score_rag_fails_a_case_when_every_strategy_misses() -> None:
    case = EvalCase(id="rag002", query="q", split="test")
    scored = score_rag(
        case,
        outcome({"query_as_written": {"recall_at_5": 0.0, "mrr": 0.0}}),
    )
    assert scored.passed is False


def test_score_rag_requires_scores() -> None:
    case = EvalCase(id="rag003", query="q", split="test")
    with pytest.raises(ConfigError, match="missing scores"):
        score_rag(case, outcome({"query_as_written": {}}))
