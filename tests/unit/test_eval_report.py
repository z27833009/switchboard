"""Report writer: measured values only, both files, smoke marker and usage."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from switchboard.core.config import PriceEntry
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
    assert "config/prices.yaml" in markdown
    assert "| plan_accuracy | 1.0000 |" in markdown
    assert "| r001 | query r001 | data | data | yes |" in markdown


def test_report_cost_uses_the_cached_input_rate() -> None:
    price = PriceEntry(
        provider="deepseek",
        model="deepseek-flash",
        input_per_million=0.30,
        cached_input_per_million=0.006,
        output_per_million=1.20,
        source="https://api-docs.deepseek.com/quick_start/pricing",
    )
    priced = replace(
        meta(),
        provider="deepseek",
        model="deepseek-flash",
        price=price,
        price_verified_on=date(2026, 11, 12),
    )
    report = build_report(
        priced,
        [
            routing_result("r001", ["data"], ["data"]),
            routing_result("r002", ["docs"], ["docs"]),
            routing_result("r003", ["data"], ["data"]),
        ],
    )

    # 30 input / 6 cached / 9 output tokens: 24 uncached, 6 cached, 9 output.
    expected = (24 * 0.30 + 6 * 0.006 + 9 * 1.20) / 1_000_000
    assert report["cost_usd"] == pytest.approx(expected, rel=1e-12)
    assert report["no_api_cost"] is False
    assert report["price"]["verified_on"] == "2026-11-12"
    assert report["price"]["source"] == "https://api-docs.deepseek.com/quick_start/pricing"
    markdown = render_markdown(report)
    assert "Cost: USD" in markdown
    assert "https://api-docs.deepseek.com/quick_start/pricing" in markdown
    assert "Cached input tokens are billed at the cached rate" in markdown


def test_report_zero_price_is_no_api_cost() -> None:
    price = PriceEntry(
        provider="lmstudio",
        model=None,
        input_per_million=0.0,
        cached_input_per_million=0.0,
        output_per_million=0.0,
    )
    local = replace(meta(), provider="lmstudio", price=price, price_verified_on=date(2026, 11, 12))
    report = build_report(local, [routing_result("r001", ["data"], ["data"])])

    assert report["cost_usd"] == 0.0
    assert report["no_api_cost"] is True
    markdown = render_markdown(report)
    assert "no API cost" in markdown
    assert "Cost: USD 0.000000" in markdown


def test_write_report_writes_both_files(tmp_path: Path) -> None:
    report = build_report(meta(), [routing_result("r001", ["data"], ["data"])])
    markdown_path, json_path = write_report(report, tmp_path)

    assert markdown_path == tmp_path / "routing-mock.md"
    assert json_path == tmp_path / "routing-mock.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["plan_accuracy"] == 1.0
    assert payload["cases"][0]["case_id"] == "r001"
    assert "Routing eval" in markdown_path.read_text(encoding="utf-8")


def test_a_second_orchestrator_gets_its_own_report_file(tmp_path: Path) -> None:
    """The committed langgraph reports keep their names (T2.11, design 20.2)."""
    report = build_report(
        replace(meta(), orchestrator="agent_loop"),
        [routing_result("r001", ["data"], ["data"])],
    )
    markdown_path, json_path = write_report(report, tmp_path)

    assert markdown_path == tmp_path / "routing-agent_loop-mock.md"
    assert json_path == tmp_path / "routing-agent_loop-mock.json"
    assert report["orchestrator"] == "agent_loop"
    assert report["plan_source"] == "agent loop first decision, derived plan"
    markdown = render_markdown(report)
    assert "Orchestrator: `agent_loop`" in markdown
    assert "Agent loop mode: each case sends the first step of the agent loop" in markdown


def rag_result(
    case_id: str,
    *,
    recall: dict[str, float],
    mrr: dict[str, float],
    rewrites: dict[str, int],
    latency: dict[str, int],
) -> CaseRun:
    case = EvalCase(
        id=case_id,
        query=f"query {case_id}",
        tags=["returns", "needs_rewrite"],
        split="test",
        payload={"relevant_chunk_ids": ["returns-policy.md#1"]},
    )
    strategies = {
        name: {
            "retrieved": ["returns-policy.md#1"],
            "recall_at_5": recall[name],
            "mrr": mrr[name],
            "rewrite_rounds": rewrites[name],
            "retrieval_rounds": 1 + rewrites[name],
            "latency_ms": latency[name],
            "error": None,
        }
        for name in recall
    }
    outcome = EvalOutcome(
        case_id=case_id,
        details={
            "strategies": strategies,
            "relevant": ["returns-policy.md#1"],
            "needs_rewrite": True,
        },
        usage=Usage(input_tokens=20, output_tokens=5),
        latency_ms=sum(latency.values()),
    )
    metrics: dict[str, float] = {}
    for name in recall:
        metrics[f"recall_at_5_{name}"] = recall[name]
        metrics[f"mrr_{name}"] = mrr[name]
    return CaseRun(
        case=case,
        outcome=outcome,
        score=Score(case_id=case_id, metrics=metrics, passed=True),
        runner_latency_ms=sum(latency.values()) + 1,
    )


def test_rag_report_shows_all_four_strategies_and_rounds() -> None:
    names = ["query_as_written", "always_rewrite", "conditional_rewrite", "conditional_hybrid"]
    results = [
        rag_result(
            "rag001",
            recall=dict.fromkeys(names, 1.0),
            mrr=dict.fromkeys(names, 1.0),
            rewrites={
                "query_as_written": 0,
                "always_rewrite": 1,
                "conditional_rewrite": 0,
                "conditional_hybrid": 0,
            },
            latency={
                "query_as_written": 100,
                "always_rewrite": 400,
                "conditional_rewrite": 100,
                "conditional_hybrid": 120,
            },
        ),
        rag_result(
            "rag002",
            recall=dict.fromkeys(names, 0.5),
            mrr=dict.fromkeys(names, 0.25),
            rewrites={
                "query_as_written": 0,
                "always_rewrite": 1,
                "conditional_rewrite": 1,
                "conditional_hybrid": 1,
            },
            latency={
                "query_as_written": 200,
                "always_rewrite": 800,
                "conditional_rewrite": 300,
                "conditional_hybrid": 400,
            },
        ),
    ]
    report = build_report(meta(suite="rag"), results)

    assert report["metrics"]["recall_at_5_query_as_written"] == 0.75
    assert report["metrics"]["mrr_always_rewrite"] == 0.625
    assert report["strategies"]["query_as_written"]["rewrite_rounds"] == 0
    assert report["strategies"]["always_rewrite"]["rewrite_rounds"] == 2
    assert report["strategies"]["conditional_rewrite"]["cases_with_rewrite"] == 1
    assert report["strategies"]["conditional_hybrid"]["latency_ms"]["p50"] == 260.0
    assert report["retrieval_k"] == 5
    assert report["rrf_k"] == 60
    assert report["cases"][0]["needs_rewrite"] is True
    assert report["cases"][0]["strategies"]["always_rewrite"]["rewrite_rounds"] == 1

    markdown = render_markdown(report)
    assert "# Rag eval: mock" in markdown
    assert "## Strategies" in markdown
    assert "| conditional_hybrid | 0.7500 | 0.6250 |" in markdown
    assert "Cross-encoder re-ranking is not enabled" in markdown
    assert "| Retrieval |" in markdown
