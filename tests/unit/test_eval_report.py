"""Report writer: measured values only, both files, smoke marker and usage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from switchboard.core.models import EvalCase, EvalOutcome, Score, Usage
from switchboard.eval.report import (
    ReportMeta,
    build_report,
    latency_summary,
    percentile,
    render_markdown,
    write_report,
)
from switchboard.eval.runner import CaseRun


def meta(*, suite: str = "routing", smoke: bool = False) -> ReportMeta:
    return ReportMeta(
        suite=suite,
        dataset_version="v1",
        dataset_path="evals/datasets/v1/routing.jsonl",
        split="test",
        orchestrator="langgraph",
        provider="mock",
        model="qwen3-8b",
        generated_at=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
        wall_time_s=1.25,
        dataset_case_count=60,
        split_case_count=39,
        smoke=smoke,
    )


def routing_result(
    case_id: str,
    expected: list[str],
    actual: list[str],
    *,
    tags: list[str] | None = None,
    latency_ms: int = 10,
) -> CaseRun:
    case = EvalCase(
        id=case_id,
        query=f"query {case_id}",
        expected_agents=expected,
        tags=tags or [],
        split="test",
    )
    outcome = EvalOutcome(
        case_id=case_id,
        plan=actual,
        usage=Usage(input_tokens=10, cached_input_tokens=2, output_tokens=3),
        latency_ms=latency_ms,
    )
    expected_set = set(expected)
    actual_set = set(actual)
    union = expected_set | actual_set
    exact = expected_set == actual_set
    jaccard = len(expected_set & actual_set) / len(union) if union else 1.0
    return CaseRun(
        case=case,
        outcome=outcome,
        score=Score(
            case_id=case_id,
            metrics={"exact_match": 1.0 if exact else 0.0, "jaccard": jaccard},
            passed=exact,
        ),
        runner_latency_ms=12,
    )


def tool_result(
    case_id: str,
    calls: list[str],
    arguments: list[dict[str, object]],
) -> CaseRun:
    case = EvalCase(
        id=case_id,
        query=f"query {case_id}",
        expected_tool="orders_summary@1",
        expected_args={"date_from": "2017-04-01", "date_to": "2017-04-30"},
    )
    outcome = EvalOutcome(
        case_id=case_id,
        tool_calls=calls,
        tool_arguments=arguments,
        usage=Usage(input_tokens=5, output_tokens=1),
        latency_ms=4,
    )
    name_match = bool(calls) and calls[0] == case.expected_tool
    args_match = bool(arguments) and arguments[0] == case.expected_args
    both = name_match and args_match
    return CaseRun(
        case=case,
        outcome=outcome,
        score=Score(
            case_id=case_id,
            metrics={
                "tool_name_match": 1.0 if name_match else 0.0,
                "argument_match": 1.0 if args_match else 0.0,
                "both": 1.0 if both else 0.0,
                "redundant": 1.0 if len(calls) > 1 else 0.0,
            },
            passed=both,
        ),
        runner_latency_ms=6,
    )


def test_percentile_and_latency_summary() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([7], 95) == 7.0
    assert percentile([0, 10, 20, 30], 50) == 15.0
    assert percentile([0, 10, 20, 30], 95) == pytest.approx(28.5)
    assert latency_summary([0, 10, 20]) == {
        "p50": 10.0,
        "p95": 19.0,
        "min": 0.0,
        "max": 20.0,
        "mean": 10.0,
    }


def test_routing_report_aggregates_metrics_usage_and_confusion() -> None:
    results = [
        routing_result("r001", ["data"], ["data"]),
        routing_result("r002", ["docs"], ["data", "docs"], latency_ms=20),
        routing_result("r003", ["data", "docs"], ["docs", "data"], tags=["ambiguous"]),
    ]
    report = build_report(meta(smoke=True), results)

    assert report["case_count"] == 3
    assert report["dataset_version"] == "v1"
    assert report["smoke"] is True
    assert report["metrics"]["plan_accuracy"] == 0.6667
    assert report["metrics"]["plan_jaccard"] == 0.8333
    assert report["metrics"]["unambiguous_plan_accuracy"] == 0.5
    assert report["metrics"]["ambiguous_plan_accuracy"] == 1.0
    assert report["latency_ms"]["p50"] == 10.0
    assert report["latency_ms"]["p95"] == 19.0
    assert report["usage"] == {
        "input_tokens": 30,
        "cached_input_tokens": 6,
        "output_tokens": 9,
        "total_tokens": 39,
    }
    assert report["cost_usd"] is None
    assert report["confusion_matrix"] == {
        "data": {"data": 1},
        "data+docs": {"data+docs": 1},
        "docs": {"data+docs": 1},
    }
    assert [row["case_id"] for row in report["cases"]] == ["r001", "r002", "r003"]


def test_toolcall_report_metrics_and_rows() -> None:
    results = [
        tool_result(
            "t001",
            ["orders_summary@1"],
            [{"date_from": "2017-04-01", "date_to": "2017-04-30"}],
        ),
        tool_result(
            "t002",
            ["orders_summary@1", "orders_late@1"],
            [{"date_from": "2017-04-01"}, {}],
        ),
    ]
    report = build_report(meta(suite="toolcall"), results)

    assert report["metrics"]["tool_name_accuracy"] == 1.0
    assert report["metrics"]["argument_accuracy"] == 0.5
    assert report["metrics"]["both_accuracy"] == 0.5
    assert report["metrics"]["tool_calls_per_case"] == 1.5
    assert report["metrics"]["redundant_call_rate"] == 0.5
    assert report["metrics"]["cases_with_redundant_calls"] == 1.0
    assert report["cases"][1]["call_count"] == 2
    assert report["cases"][1]["actual_calls"][0]["name"] == "orders_summary@1"


def test_markdown_states_metadata_smoke_and_missing_cost() -> None:
    report = build_report(meta(smoke=True), [routing_result("r001", ["data"], ["data"])])
    markdown = render_markdown(report)

    assert "# Routing eval: mock" in markdown
    assert "`v1`" in markdown
    assert "Orchestrator: `langgraph`" in markdown
    assert "**Smoke run:**" in markdown
    assert "Cost is not reported" in markdown
    assert "| plan_accuracy | 1.0000 |" in markdown
    assert "| r001 | query r001 | data | data | yes |" in markdown


def test_write_report_writes_both_files(tmp_path: Path) -> None:
    report = build_report(meta(), [routing_result("r001", ["data"], ["data"])])
    markdown_path, json_path = write_report(report, tmp_path)

    assert markdown_path == tmp_path / "routing-mock.md"
    assert json_path == tmp_path / "routing-mock.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["plan_accuracy"] == 1.0
    assert payload["cases"][0]["case_id"] == "r001"
    assert "Routing eval" in markdown_path.read_text(encoding="utf-8")
