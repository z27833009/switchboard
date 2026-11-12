"""Reciprocal rank fusion (design.md section 9.2)."""

from __future__ import annotations

import pytest

from switchboard.adapters.retrievers.pgvector import (
    RankedCandidate,
    reciprocal_rank_fusion,
)
from switchboard.core.errors import RetrievalError


def candidate(chunk_id: str, score: float = 0.5) -> RankedCandidate:
    return RankedCandidate(chunk_id=chunk_id, score=score)


def test_a_chunk_in_both_rankings_beats_a_chunk_in_one() -> None:
    fused = reciprocal_rank_fusion(
        [candidate("a"), candidate("b"), candidate("c")],
        [candidate("c"), candidate("a")],
        k=60,
        limit=5,
    )
    assert [hit.chunk_id for hit in fused] == ["a", "c", "b"]
    assert fused[0].rrf_score == pytest.approx(1 / 61 + 1 / 62, abs=1e-8)
    assert fused[1].rrf_score == pytest.approx(1 / 63 + 1 / 61, abs=1e-8)


def test_text_only_chunks_keep_their_cosine_score() -> None:
    fused = reciprocal_rank_fusion(
        [candidate("a", 0.8)],
        [candidate("b", 0.42), candidate("a", 0.1)],
        limit=5,
    )
    scores = {hit.chunk_id: hit.score for hit in fused}
    assert scores["b"] == 0.42
    # The vector branch is the first source of the cosine value.
    assert scores["a"] == 0.8
    assert [hit.chunk_id for hit in fused] == ["a", "b"]


def test_duplicates_inside_one_ranking_count_once() -> None:
    fused = reciprocal_rank_fusion(
        [candidate("a"), candidate("a"), candidate("b")],
        [],
        limit=5,
    )
    assert [hit.chunk_id for hit in fused] == ["a", "b"]
    assert fused[0].rrf_score == pytest.approx(1 / 61, abs=1e-8)


def test_ties_break_by_cosine_then_chunk_id() -> None:
    # Both chunks are rank 1 in their own branch, so the RRF value is equal.
    by_id = reciprocal_rank_fusion(
        [candidate("b", 0.9)],
        [candidate("a", 0.9)],
        limit=5,
    )
    assert [hit.chunk_id for hit in by_id] == ["a", "b"]

    by_cosine = reciprocal_rank_fusion(
        [candidate("b", 0.9)],
        [candidate("a", 0.95)],
        limit=5,
    )
    assert [hit.chunk_id for hit in by_cosine] == ["a", "b"]


def test_limit_and_empty_inputs() -> None:
    assert reciprocal_rank_fusion([], [], limit=5) == []
    fused = reciprocal_rank_fusion([candidate("a")], [candidate("b")], limit=1)
    assert len(fused) == 1


def test_invalid_parameters_are_retrieval_errors() -> None:
    with pytest.raises(RetrievalError, match="RRF k"):
        reciprocal_rank_fusion([], [], k=0, limit=5)
    with pytest.raises(RetrievalError, match="fusion limit"):
        reciprocal_rank_fusion([], [], k=60, limit=0)
