"""The rag dataset and its portable chunk keys (design.md section 20.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from switchboard.core.errors import ConfigError
from switchboard.core.models import EvalCase
from switchboard.eval.datasets import load_cases
from switchboard.eval.scorers.rag import parse_chunk_key
from switchboard.eval.suites.rag import (
    STRATEGIES,
    STRATEGY_NAMES,
    case_history,
    needs_rewrite,
    prepare_case,
)

REPO = Path(__file__).resolve().parents[2]
DATASET_ROOT = REPO / "evals" / "datasets"


def test_v3_has_40_test_cases_with_portable_keys() -> None:
    cases = load_cases("rag", "v3", DATASET_ROOT)
    assert len(cases) == 40
    assert {case.split for case in cases} == {"test"}
    assert {case.id for case in cases} == {f"rag{number:03d}" for number in range(1, 41)}

    keys: set[str] = set()
    for case in cases:
        relevant = prepare_case(case)
        assert relevant == case.payload["relevant_chunk_ids"]
        for key in relevant:
            parse_chunk_key(key)
            keys.add(key)
    # Every chunk of the five-document corpus appears as a label at least once.
    assert len(keys) == 29


def test_the_dataset_marks_rewrite_cases_and_history_cases() -> None:
    cases = load_cases("rag", "v3", DATASET_ROOT)
    tagged = [case.id for case in cases if needs_rewrite(case)]
    history = [case.id for case in cases if case_history(case)]
    assert len(tagged) == 9
    assert len(history) == 4
    assert set(history) <= set(tagged)


def test_the_four_strategies_cover_both_search_modes_and_all_rewrite_modes() -> None:
    assert STRATEGY_NAMES == (
        "query_as_written",
        "always_rewrite",
        "conditional_rewrite",
        "conditional_hybrid",
    )
    assert {strategy.search for strategy in STRATEGIES} == {"vector", "hybrid"}
    assert {strategy.rewrite_mode for strategy in STRATEGIES} == {
        "never",
        "always",
        "conditional",
    }


def test_prepare_case_rejects_a_case_without_relevant_keys() -> None:
    case = EvalCase(id="rag900", query="q", split="test", payload={"relevant_chunk_ids": []})
    with pytest.raises(ConfigError, match="relevant_chunk_ids"):
        prepare_case(case)


def test_prepare_case_rejects_a_bad_key_and_a_missing_split() -> None:
    bad_key = EvalCase(
        id="rag901",
        query="q",
        split="test",
        payload={"relevant_chunk_ids": ["returns-policy.md"]},
    )
    with pytest.raises(ConfigError, match="must look like"):
        prepare_case(bad_key)

    no_split = EvalCase(
        id="rag902",
        query="q",
        payload={"relevant_chunk_ids": ["returns-policy.md#0"]},
    )
    with pytest.raises(ConfigError, match="dev or test split"):
        prepare_case(no_split)


def test_prepare_case_rejects_a_malformed_history() -> None:
    case = EvalCase(
        id="rag903",
        query="q",
        split="test",
        payload={
            "relevant_chunk_ids": ["returns-policy.md#0"],
            "history": [{"role": "system", "content": "not a turn"}],
        },
    )
    with pytest.raises(ConfigError, match="history"):
        prepare_case(case)


def test_the_committed_labels_are_source_ordinal_pairs() -> None:
    cases = load_cases("rag", "v3", DATASET_ROOT)
    for case in cases:
        for key in case.payload["relevant_chunk_ids"]:
            source, ordinal = parse_chunk_key(key)
            assert source.endswith(".md")
            assert ordinal >= 0
