"""Cache suite and threshold report (design.md sections 10.2 and 20.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from switchboard.core.config import (
    EmbedderConfig,
    PlatformConfig,
    PriceEntry,
    PricesConfig,
    ProviderConfig,
    ProvidersConfig,
    Settings,
    TenantAgentSettings,
    TenantConfig,
    TenantsConfig,
    TenantToolSettings,
)
from switchboard.core.models import EvalCase, EvalOutcome, Score, Usage
from switchboard.core.ports import EvalDeps
from switchboard.eval.report import ReportMeta, build_report, render_markdown
from switchboard.eval.runner import CaseRun
from switchboard.eval.scorers.cache import CHOSEN_THRESHOLD, sweep_thresholds
from switchboard.eval.suites.cache import CacheSuite, prepare_case

REPO = Path(__file__).resolve().parents[2]
DATASETS = REPO / "evals" / "datasets"


def settings() -> Settings:
    return Settings(
        platform=PlatformConfig(
            orchestrator="langgraph",
            retriever="none",
            cache="none",
            embedder="mock",
        ),
        providers=ProvidersConfig(
            providers={"mock": ProviderConfig(adapter="mock", model="qwen3-8b")},
            embedders={"mock": EmbedderConfig(adapter="mock", dimension=1024)},
        ),
        tenants=TenantsConfig(
            tenants={
                "default": TenantConfig(
                    provider="mock",
                    agents=TenantAgentSettings(allow=["direct"]),
                    tools=TenantToolSettings(allow=[]),
                )
            }
        ),
        prices=PricesConfig(
            verified_on=datetime.now(UTC).date(),
            entries=[
                PriceEntry(
                    provider="mock",
                    model=None,
                    input_per_million=0.0,
                    cached_input_per_million=0.0,
                    output_per_million=0.0,
                )
            ],
        ),
    )


def test_the_v5_dataset_has_fifty_labeled_pairs() -> None:
    suite = CacheSuite(settings=settings(), provider="mock", dataset_root=DATASETS)
    cases = suite.load("v5")

    assert len(cases) == 50
    labels = [prepare_case(case)[2] for case in cases]
    assert labels.count(True) == 25
    assert labels.count(False) == 25
    assert {case.split for case in cases} == {"test"}
    cross = [case for case in cases if case.payload.get("pair_type") == "cross_type"]
    assert len(cross) == 6
    assert all("cross_type" in case.tags for case in cross)


def test_prepare_case_rejects_a_missing_label() -> None:
    case = EvalCase(id="x1", query="a", payload={"query_a": "a", "query_b": "b"}, split="test")
    with pytest.raises(Exception, match="should_hit"):
        prepare_case(case)


async def test_the_mock_embedder_runs_the_suite_without_a_model() -> None:
    suite = CacheSuite(settings=settings(), provider="mock", dataset_root=DATASETS)
    cases = suite.load("v5")
    deps = EvalDeps(provider=cast(Any, object()))

    outcome = await suite.execute(cases[0], deps)

    assert 0.0 <= outcome.details["similarity"] <= 1.0
    assert outcome.details["should_hit"] is True
    assert "mock" in outcome.details["embedding_source"]
    score = suite.score(cases[0], outcome)
    assert 0.0 <= score.metrics["similarity"] <= 1.0
    assert score.passed is not None


def test_the_threshold_boundary_is_inclusive() -> None:
    rows = sweep_thresholds([(CHOSEN_THRESHOLD, True), (CHOSEN_THRESHOLD - 0.000001, False)])
    at_chosen = next(row for row in rows if row.threshold == CHOSEN_THRESHOLD)
    assert at_chosen.hits == 1
    assert at_chosen.false_hits == 0


def test_the_sweep_counts_hits_and_false_hits() -> None:
    rows = sweep_thresholds(
        [(0.95, True), (0.90, True), (0.93, False), (0.80, False)],
        thresholds=(0.92, 0.94),
    )

    low, high = rows
    assert low.threshold == 0.92
    assert (low.hits, low.positives) == (1, 2)
    assert (low.false_hits, low.negatives) == (1, 2)
    assert low.hit_rate == 0.5
    assert low.false_hit_rate == 0.5
    assert low.accuracy == 0.5
    assert high.hits == 1
    assert high.false_hits == 0
    assert high.accuracy == 0.75


def case_run(case_id: str, similarity: float, should_hit: bool) -> CaseRun:
    case = EvalCase(
        id=case_id,
        query=f"query {case_id}",
        payload={
            "query_a": f"query {case_id} a",
            "query_b": f"query {case_id} b",
            "should_hit": should_hit,
            "pair_type": "paraphrase" if should_hit else "near_miss",
        },
        split="test",
    )
    predicted = similarity >= CHOSEN_THRESHOLD
    outcome = EvalOutcome(
        case_id=case_id,
        details={
            "query_a": case.payload["query_a"],
            "query_b": case.payload["query_b"],
            "should_hit": should_hit,
            "pair_type": case.payload["pair_type"],
            "similarity": similarity,
            "embedding_source": "mock (deterministic sha256, dimension 1024)",
        },
        usage=Usage(input_tokens=0, output_tokens=0),
        latency_ms=0,
    )
    score = Score(
        case_id=case_id,
        metrics={
            "similarity": similarity,
            "hit_at_chosen_threshold": 1.0 if predicted else 0.0,
            "correct_at_chosen_threshold": 1.0 if predicted == should_hit else 0.0,
        },
        passed=predicted == should_hit,
    )
    return CaseRun(case=case, outcome=outcome, score=score, runner_latency_ms=1)


def test_the_report_carries_the_sweep_and_marks_the_chosen_threshold() -> None:
    results = [
        case_run("c001", 0.95, True),
        case_run("c002", 0.90, True),
        case_run("c003", 0.93, False),
        case_run("c004", 0.80, False),
    ]
    report = build_report(
        ReportMeta(
            suite="cache",
            dataset_version="v5",
            dataset_path="evals/datasets/v5/cache.jsonl",
            split="test",
            orchestrator="langgraph",
            provider="mock",
            model="mock (deterministic)",
            generated_at=datetime.now(UTC),
            wall_time_s=1.0,
            dataset_case_count=50,
            split_case_count=50,
            smoke=False,
            chosen_threshold=0.92,
        ),
        results,
    )

    assert report["suite"] == "cache"
    assert report["metrics"]["pair_count"] == 4.0
    assert report["metrics"]["positive_pairs"] == 2.0
    assert report["metrics"]["negative_pairs"] == 2.0
    assert report["metrics"]["hit_rate_at_0.92"] == 0.5
    assert report["metrics"]["false_hit_rate_at_0.92"] == 0.5
    assert report["metrics"]["hit_rate_at_0.94"] == 0.5
    assert report["metrics"]["false_hit_rate_at_0.94"] == 0.0
    assert len(report["threshold_sweep"]) == 8
    assert report["deterministic_embeddings"] is True
    assert report["chosen_threshold"] == 0.92

    markdown = render_markdown(report)
    assert "## Threshold sweep" in markdown
    assert "| 0.92 (chosen) |" in markdown
    assert "Deterministic mock embeddings" in markdown
    assert "c003" in markdown
