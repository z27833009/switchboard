"""Markdown and JSON eval reports (design.md sections 20.2 and 20.3).

Every number in a report comes from the run that produced it. A value that was
not measured is ``null`` and the markdown says so, rather than a guessed number:
cost is null when the provider has no entry in ``config/prices.yaml``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from switchboard.core.config import PriceEntry
from switchboard.core.guardrails import PATTERN_BY_ID, PATTERN_SET_VERSION
from switchboard.core.models import Usage
from switchboard.core.pricing import compute_cost
from switchboard.eval.runner import CaseRun
from switchboard.eval.scorers.cache import sweep_thresholds, threshold_label
from switchboard.eval.scorers.routing import plan_label
from switchboard.gate import CHECK_NAMES


@dataclass(frozen=True)
class ReportMeta:
    suite: str
    dataset_version: str
    dataset_path: str
    split: str
    orchestrator: str
    provider: str
    model: str
    generated_at: datetime
    wall_time_s: float
    dataset_case_count: int
    split_case_count: int
    smoke: bool
    recording: bool = False
    recorded_with: str | None = None
    # The price row for the provider and model, or None when the table has no
    # entry. The date comes from the table so the report can cite its source.
    price: PriceEntry | None = None
    price_verified_on: date | None = None
    # The tenant's configured cache threshold, marked in the sweep table.
    chosen_threshold: float | None = None
    # The judge that produced the rag answer-quality metrics (T3.04), such as
    # "llm (lmstudio/qwen3-8b)". None in every report that ran without it, so
    # the default rag report is unchanged.
    judge_label: str | None = None


def percentile(values: list[int], percent: float) -> float:
    """Linear interpolation between the closest ranks; 0.0 for no values."""
    if not values:
        return 0.0
    if percent <= 0:
        return float(min(values))
    if percent >= 100:
        return float(max(values))
    ordered = sorted(values)
    rank = (percent / 100.0) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(ordered[lower])
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def latency_summary(values: list[int]) -> dict[str, float]:
    return {
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "min": float(min(values)) if values else 0.0,
        "max": float(max(values)) if values else 0.0,
        "mean": round(sum(values) / len(values), 3) if values else 0.0,
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _metric_values(results: list[CaseRun], name: str) -> list[float]:
    return [result.score.metrics.get(name, 0.0) for result in results]


def _routing_metrics(results: list[CaseRun]) -> dict[str, float]:
    metrics = {
        "plan_accuracy": _mean(_metric_values(results, "exact_match")),
        "plan_jaccard": _mean(_metric_values(results, "jaccard")),
        "case_pass_rate": _mean(_metric_values(results, "exact_match")),
    }
    for label, wants_ambiguous in (("unambiguous", False), ("ambiguous", True)):
        group = [
            result for result in results if ("ambiguous" in result.case.tags) == wants_ambiguous
        ]
        if group:
            metrics[f"{label}_plan_accuracy"] = _mean(_metric_values(group, "exact_match"))
    return metrics


def _toolcall_metrics(results: list[CaseRun]) -> dict[str, float]:
    call_counts = [float(len(result.outcome.tool_calls)) for result in results]
    redundant = _metric_values(results, "redundant")
    return {
        "tool_name_accuracy": _mean(_metric_values(results, "tool_name_match")),
        "argument_accuracy": _mean(_metric_values(results, "argument_match")),
        "both_accuracy": _mean(_metric_values(results, "both")),
        "tool_calls_per_case": round(sum(call_counts) / len(call_counts), 4),
        "redundant_call_rate": _mean(redundant),
        "cases_with_redundant_calls": float(sum(1 for value in redundant if value)),
        "case_pass_rate": _mean(_metric_values(results, "both")),
    }


def _gate_counts(results: list[CaseRun]) -> dict[str, Any]:
    checks: dict[str, dict[str, int]] = {}
    for name in CHECK_NAMES:
        counts = {}
        for kind in ("tp", "fp", "fn", "tn"):
            counts[kind] = int(sum(_metric_values(results, f"{name}_{kind}")))
        checks[name] = counts
    false_positives = [
        result.case.id
        for result in results
        if result.score.metrics.get("weak_predicted")
        and not result.case.payload.get("is_weak", False)
    ]
    false_negatives = [
        result.case.id
        for result in results
        if not result.score.metrics.get("weak_predicted")
        and result.case.payload.get("is_weak", False)
    ]
    return {
        "checks": checks,
        "predicted_weak_cases": int(
            sum(1 for result in results if result.score.metrics.get("weak_predicted"))
        ),
        "labeled_weak_cases": int(
            sum(1 for result in results if result.score.metrics.get("weak_labeled"))
        ),
        "false_positive_cases": false_positives,
        "false_negative_cases": false_negatives,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _gate_metrics(results: list[CaseRun]) -> dict[str, float]:
    counts = _gate_counts(results)
    metrics: dict[str, float] = {}
    for name in CHECK_NAMES:
        check = counts["checks"][name]
        precision = _ratio(check["tp"], check["tp"] + check["fp"])
        recall = _ratio(check["tp"], check["tp"] + check["fn"])
        metrics[f"{name}_precision"] = precision
        metrics[f"{name}_recall"] = recall
        denominator = precision + recall
        metrics[f"{name}_f1"] = (
            round(2 * precision * recall / denominator, 4) if denominator else 0.0
        )
    predicted = counts["predicted_weak_cases"]
    labeled = counts["labeled_weak_cases"]
    metrics["case_accuracy"] = _mean(_metric_values(results, "weak_correct"))
    metrics["gate_precision"] = _ratio(
        labeled - len(counts["false_negative_cases"]),
        predicted,
    )
    metrics["gate_recall"] = _ratio(
        labeled - len(counts["false_negative_cases"]),
        labeled,
    )
    metrics["good_case_specificity"] = _ratio(
        len(results) - labeled - len(counts["false_positive_cases"]),
        len(results) - labeled,
    )
    return metrics


def _injection_counts(results: list[CaseRun]) -> dict[str, Any]:
    patterns: dict[str, dict[str, Any]] = {}
    for pattern_id, pattern in PATTERN_BY_ID.items():
        patterns[pattern_id] = {
            "name": pattern.name,
            "family": pattern.family,
            "tp": int(sum(_metric_values(results, f"{pattern_id}_tp"))),
            "fp": int(sum(_metric_values(results, f"{pattern_id}_fp"))),
            "fn": int(sum(_metric_values(results, f"{pattern_id}_fn"))),
            "tn": int(sum(_metric_values(results, f"{pattern_id}_tn"))),
        }
    false_positives = [
        result.case.id
        for result in results
        if result.score.metrics.get("flagged")
        and not bool(result.case.payload.get("is_malicious", False))
    ]
    false_negatives = [
        result.case.id
        for result in results
        if not result.score.metrics.get("flagged")
        and bool(result.case.payload.get("is_malicious", False))
    ]
    return {
        "pattern_set_version": PATTERN_SET_VERSION,
        "patterns": patterns,
        "false_positive_cases": false_positives,
        "false_negative_cases": false_negatives,
    }


def _injection_metrics(results: list[CaseRun]) -> dict[str, float]:
    flagged = _metric_values(results, "flagged")
    labeled = _metric_values(results, "labeled_malicious")
    tp = int(sum(1 for f, m in zip(flagged, labeled, strict=True) if f and m))
    fp = int(sum(1 for f, m in zip(flagged, labeled, strict=True) if f and not m))
    fn = int(sum(1 for f, m in zip(flagged, labeled, strict=True) if not f and m))
    tn = int(sum(1 for f, m in zip(flagged, labeled, strict=True) if not f and not m))
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    denominator = precision + recall
    return {
        "detection_rate": recall,
        "false_positive_rate": _ratio(fp, fp + tn),
        "precision": precision,
        "recall": recall,
        "f1": round(2 * precision * recall / denominator, 4) if denominator else 0.0,
        "case_accuracy": _mean(_metric_values(results, "correct")),
        "benign_specificity": _ratio(tn, tn + fp),
    }


def _injection_case_row(result: CaseRun) -> dict[str, Any]:
    payload = result.case.payload
    matched = result.outcome.details.get("patterns", [])
    return {
        "case_id": result.case.id,
        "query": result.case.query,
        "content": payload.get("content"),
        "is_malicious": bool(payload.get("is_malicious", False)),
        "expected_pattern": payload.get("expected_pattern"),
        "matched_patterns": list(matched) if isinstance(matched, list) else [],
        "flagged": bool(result.outcome.details.get("flagged", False)),
        "correct": result.score.passed,
        "note": payload.get("note"),
        "error": result.outcome.error,
    }


def _rag_strategy_names(results: list[CaseRun]) -> list[str]:
    names: list[str] = []
    for result in results:
        strategies = result.outcome.details.get("strategies", {})
        if not isinstance(strategies, dict):
            continue
        for name in strategies:
            if name not in names:
                names.append(name)
    return names


def _rag_strategy_payload(result: CaseRun, name: str) -> dict[str, Any]:
    strategies = result.outcome.details.get("strategies", {})
    if not isinstance(strategies, dict):
        return {}
    payload = strategies.get(name, {})
    return payload if isinstance(payload, dict) else {}


def _rag_metrics(results: list[CaseRun]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for name in _rag_strategy_names(results):
        metrics[f"recall_at_5_{name}"] = _mean(_metric_values(results, f"recall_at_5_{name}"))
        metrics[f"mrr_{name}"] = _mean(_metric_values(results, f"mrr_{name}"))
    judged = [result for result in results if "judge_acceptable" in result.score.metrics]
    if judged:
        # Over the cases the judge actually returned a verdict for, so a judge
        # error is visible as a smaller denominator instead of a silent zero.
        metrics["judge_accept_rate"] = _mean(
            [float(result.score.metrics["judge_acceptable"]) for result in judged]
        )
    return metrics


def _rag_strategy_summary(results: list[CaseRun], name: str) -> dict[str, Any]:
    payloads = [_rag_strategy_payload(result, name) for result in results]
    latencies = [int(payload.get("latency_ms", 0)) for payload in payloads]
    needs = [
        result for result in results if bool(result.outcome.details.get("needs_rewrite", False))
    ]
    return {
        "recall_at_5": _mean(_metric_values(results, f"recall_at_5_{name}")),
        "mrr": _mean(_metric_values(results, f"mrr_{name}")),
        "cases": len(results),
        "rewrite_rounds": int(sum(int(payload.get("rewrite_rounds", 0)) for payload in payloads)),
        "retrieval_rounds": int(
            sum(int(payload.get("retrieval_rounds", 0)) for payload in payloads)
        ),
        "cases_with_rewrite": int(
            sum(1 for payload in payloads if int(payload.get("rewrite_rounds", 0)) > 0)
        ),
        "latency_ms": latency_summary(latencies),
        "errors": int(sum(1 for payload in payloads if payload.get("error"))),
        "needs_rewrite_cases": len(needs),
        "needs_rewrite_recall_at_5": _mean(_metric_values(needs, f"recall_at_5_{name}")),
        "needs_rewrite_mrr": _mean(_metric_values(needs, f"mrr_{name}")),
    }


def _rag_case_row(result: CaseRun) -> dict[str, Any]:
    usage = result.outcome.usage or Usage(input_tokens=0, output_tokens=0)
    relevant = result.outcome.details.get("relevant", [])
    strategies: dict[str, Any] = {}
    for name in _rag_strategy_names([result]):
        payload = _rag_strategy_payload(result, name)
        strategies[name] = {
            "retrieved": list(payload.get("retrieved", [])),
            "recall_at_5": payload.get("recall_at_5", 0.0),
            "mrr": payload.get("mrr", 0.0),
            "rewrite_rounds": payload.get("rewrite_rounds", 0),
            "retrieval_rounds": payload.get("retrieval_rounds", 0),
            "rewritten_queries": list(payload.get("rewritten_queries", [])),
            "latency_ms": payload.get("latency_ms", 0),
            "error": payload.get("error"),
        }
    judge = result.outcome.details.get("judge")
    row = {
        "case_id": result.case.id,
        "query": result.case.query,
        "tags": list(result.case.tags),
        "split": result.case.split,
        "needs_rewrite": bool(result.outcome.details.get("needs_rewrite", False)),
        "relevant": list(relevant) if isinstance(relevant, list) else [],
        "strategies": strategies,
        "passed": result.score.passed,
        "latency_ms": result.outcome.latency_ms,
        "runner_latency_ms": result.runner_latency_ms,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "error": result.outcome.error,
    }
    if isinstance(judge, dict):
        row["judge"] = {
            "verdict": judge.get("verdict"),
            "score": judge.get("score"),
            "reason": judge.get("reason") or "",
            "answer": judge.get("answer"),
            "generation_latency_ms": judge.get("generation_latency_ms", 0),
            "judge_latency_ms": judge.get("judge_latency_ms", 0),
            "error": judge.get("error"),
        }
    return row


def _cache_pairs(results: list[CaseRun]) -> list[tuple[float, bool]]:
    return [
        (
            float(result.outcome.details.get("similarity", 0.0)),
            bool(result.outcome.details.get("should_hit", False)),
        )
        for result in results
    ]


def _cache_sweep(results: list[CaseRun]) -> list[dict[str, Any]]:
    return [
        {
            "threshold": row.threshold,
            "hits": row.hits,
            "positive_pairs": row.positives,
            "hit_rate": row.hit_rate,
            "false_hits": row.false_hits,
            "negative_pairs": row.negatives,
            "false_hit_rate": row.false_hit_rate,
            "accuracy": row.accuracy,
        }
        for row in sweep_thresholds(_cache_pairs(results))
    ]


def _cache_metrics(results: list[CaseRun]) -> dict[str, float]:
    pairs = _cache_pairs(results)
    metrics: dict[str, float] = {
        "pair_count": float(len(pairs)),
        "positive_pairs": float(sum(1 for _similarity, label in pairs if label)),
        "negative_pairs": float(sum(1 for _similarity, label in pairs if not label)),
    }
    for row in sweep_thresholds(pairs):
        label = threshold_label(row.threshold)
        metrics[f"hit_rate_at_{label}"] = row.hit_rate
        metrics[f"false_hit_rate_at_{label}"] = row.false_hit_rate
        metrics[f"accuracy_at_{label}"] = row.accuracy
    return metrics


def _cache_case_row(result: CaseRun) -> dict[str, Any]:
    details = result.outcome.details
    return {
        "case_id": result.case.id,
        "query_a": details.get("query_a") or result.case.query,
        "query_b": details.get("query_b"),
        "should_hit": bool(details.get("should_hit", False)),
        "pair_type": details.get("pair_type"),
        "similarity": float(details.get("similarity", 0.0)),
        "predicted_hit": result.score.metrics.get("hit_at_chosen_threshold", 0.0) > 0.0,
        "correct": result.score.passed,
        "tags": list(result.case.tags),
        "note": result.case.payload.get("note"),
        "error": result.outcome.error,
    }


def _usage_totals(results: list[CaseRun]) -> Usage:
    total = Usage(input_tokens=0, output_tokens=0)
    for result in results:
        if result.outcome.usage is not None:
            total = total.plus(result.outcome.usage)
    return total


def _routing_case_row(result: CaseRun) -> dict[str, Any]:
    usage = result.outcome.usage or Usage(input_tokens=0, output_tokens=0)
    return {
        "case_id": result.case.id,
        "query": result.case.query,
        "tags": list(result.case.tags),
        "split": result.case.split,
        "expected_agents": list(result.case.expected_agents),
        "expected_plan": plan_label(result.case.expected_agents),
        "actual_agents": list(result.outcome.plan),
        "actual_plan": plan_label(result.outcome.plan),
        "exact_match": result.score.metrics.get("exact_match", 0.0),
        "jaccard": result.score.metrics.get("jaccard", 0.0),
        "passed": result.score.passed,
        "latency_ms": result.outcome.latency_ms,
        "runner_latency_ms": result.runner_latency_ms,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "reason": result.outcome.details.get("reason"),
        "tool_calls": list(result.outcome.tool_calls),
        "error": result.outcome.error,
    }


def _toolcall_case_row(result: CaseRun) -> dict[str, Any]:
    usage = result.outcome.usage or Usage(input_tokens=0, output_tokens=0)
    calls = [
        {"name": name, "arguments": arguments}
        for name, arguments in zip(
            result.outcome.tool_calls,
            result.outcome.tool_arguments,
            strict=False,
        )
    ]
    return {
        "case_id": result.case.id,
        "query": result.case.query,
        "tags": list(result.case.tags),
        "split": result.case.split,
        "expected_tool": result.case.expected_tool,
        "expected_args": dict(result.case.expected_args),
        "actual_calls": calls,
        "tool_name_match": result.score.metrics.get("tool_name_match", 0.0),
        "argument_match": result.score.metrics.get("argument_match", 0.0),
        "both": result.score.metrics.get("both", 0.0),
        "call_count": len(result.outcome.tool_calls),
        "redundant": result.score.metrics.get("redundant", 0.0),
        "passed": result.score.passed,
        "latency_ms": result.outcome.latency_ms,
        "runner_latency_ms": result.runner_latency_ms,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "error": result.outcome.error,
    }


def _gate_case_row(result: CaseRun) -> dict[str, Any]:
    expected = sorted(result.case.payload.get("weakness", []))
    actual = sorted(result.outcome.details.get("failed_checks", []))
    predicted_weak = bool(actual)
    labeled_weak = bool(result.case.payload.get("is_weak", False))
    return {
        "case_id": result.case.id,
        "query": result.case.query,
        "answer": result.outcome.answer,
        "agents": list(result.case.payload.get("agents", [])),
        "plan": list(result.case.payload.get("plan", [])),
        "expected_failed_checks": expected,
        "actual_failed_checks": actual,
        "action": result.outcome.details.get("action"),
        "reason": result.outcome.details.get("reason"),
        "feedback": result.outcome.details.get("feedback"),
        "labeled_weak": labeled_weak,
        "predicted_weak": predicted_weak,
        "correct": result.score.passed,
        "note": result.case.payload.get("note"),
        "error": result.outcome.error,
    }


def build_report(meta: ReportMeta, results: list[CaseRun]) -> dict[str, Any]:
    latencies = [result.outcome.latency_ms for result in results]
    runner_latencies = [result.runner_latency_ms for result in results]
    usage = _usage_totals(results)
    errors = [result.case.id for result in results if result.outcome.error]
    cost = compute_cost(usage, meta.price) if meta.price is not None else None
    price = (
        None
        if meta.price is None
        else {
            "provider": meta.price.provider,
            "model": meta.price.model,
            "input_per_million": meta.price.input_per_million,
            "cached_input_per_million": meta.price.cached_input_per_million,
            "output_per_million": meta.price.output_per_million,
            "verified_on": (
                meta.price_verified_on.isoformat() if meta.price_verified_on is not None else None
            ),
            "source": meta.price.source,
            "note": meta.price.note,
        }
    )

    if meta.suite == "routing":
        metrics = _routing_metrics(results)
    elif meta.suite == "gate":
        metrics = _gate_metrics(results)
    elif meta.suite == "injection":
        metrics = _injection_metrics(results)
    elif meta.suite == "rag":
        metrics = _rag_metrics(results)
    elif meta.suite == "cache":
        metrics = _cache_metrics(results)
    else:
        metrics = _toolcall_metrics(results)
    report: dict[str, Any] = {
        "suite": meta.suite,
        "dataset_version": meta.dataset_version,
        "dataset_path": meta.dataset_path,
        "split": meta.split,
        "case_count": len(results),
        "dataset_case_count": meta.dataset_case_count,
        "split_case_count": meta.split_case_count,
        "smoke": meta.smoke,
        "orchestrator": meta.orchestrator,
        "provider": meta.provider,
        "model": meta.model,
        "generated_at": meta.generated_at.isoformat(),
        "wall_time_s": round(meta.wall_time_s, 3),
        "recording": meta.recording,
        "recorded_with": meta.recorded_with,
        "metrics": metrics,
        "latency_ms": latency_summary(latencies),
        "runner_latency_ms": latency_summary(runner_latencies),
        "usage": {
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        },
        "cost_usd": cost,
        "price": price,
        "no_api_cost": bool(meta.price is not None and meta.price.is_zero),
        "error_count": len(errors),
        "error_case_ids": errors,
    }
    if meta.suite == "routing":
        matrix: dict[str, dict[str, int]] = {}
        for result in results:
            expected = plan_label(result.case.expected_agents)
            actual = plan_label(result.outcome.plan)
            row = matrix.setdefault(expected, {})
            row[actual] = row.get(actual, 0) + 1
        report["confusion_matrix"] = {
            expected: dict(sorted(row.items())) for expected, row in sorted(matrix.items())
        }
        report["plan_source"] = (
            "agent loop first decision, derived plan"
            if meta.orchestrator == "agent_loop"
            else "supervisor plan call"
        )
        report["cases"] = [_routing_case_row(result) for result in results]
    elif meta.suite == "gate":
        report["gate_counts"] = _gate_counts(results)
        report["label_source"] = (
            "constructed and audited by the implementation session, "
            "not an independent human annotator"
        )
        report["deterministic"] = True
        report["cases"] = [_gate_case_row(result) for result in results]
    elif meta.suite == "rag":
        names = _rag_strategy_names(results)
        report["strategies"] = {name: _rag_strategy_summary(results, name) for name in names}
        report["retrieval_k"] = 5
        report["rrf_k"] = 60
        judged = [
            result for result in results if isinstance(result.outcome.details.get("judge"), dict)
        ]
        if judged:
            verdicts = [
                str(result.outcome.details["judge"].get("verdict") or "error") for result in judged
            ]
            report["judge"] = {
                "judge": meta.judge_label,
                "strategy": "conditional_hybrid",
                "cases": len(judged),
                "acceptable": sum(1 for verdict in verdicts if verdict == "acceptable"),
                "weak": sum(1 for verdict in verdicts if verdict == "weak"),
                "errors": sum(1 for verdict in verdicts if verdict == "error"),
                "accept_rate": _mean(
                    [
                        float(result.score.metrics["judge_acceptable"])
                        for result in judged
                        if "judge_acceptable" in result.score.metrics
                    ]
                ),
                "weak_case_ids": [
                    result.case.id
                    for result in judged
                    if result.outcome.details["judge"].get("verdict") == "weak"
                ],
                "error_case_ids": [
                    result.case.id
                    for result in judged
                    if result.outcome.details["judge"].get("verdict") is None
                ],
                "generation_latency_ms": latency_summary(
                    [
                        int(result.outcome.details["judge"].get("generation_latency_ms", 0))
                        for result in judged
                    ]
                ),
                "judge_latency_ms": latency_summary(
                    [
                        int(result.outcome.details["judge"].get("judge_latency_ms", 0))
                        for result in judged
                    ]
                ),
            }
        report["cases"] = [_rag_case_row(result) for result in results]
    elif meta.suite == "injection":
        report["injection_counts"] = _injection_counts(results)
        report["pattern_set_version"] = PATTERN_SET_VERSION
        report["label_source"] = (
            "constructed and audited by the implementation session, "
            "not an independent human annotator"
        )
        report["deterministic"] = True
        report["cases"] = [_injection_case_row(result) for result in results]
    elif meta.suite == "cache":
        report["threshold_sweep"] = _cache_sweep(results)
        report["chosen_threshold"] = meta.chosen_threshold
        sources = {
            str(result.outcome.details.get("embedding_source", ""))
            for result in results
            if result.outcome.details.get("embedding_source")
        }
        report["embedding_source"] = ", ".join(sorted(sources)) or None
        report["deterministic_embeddings"] = meta.provider == "mock"
        report["label_source"] = (
            "pairs written and audited by the implementation session, "
            "not an independent human annotator"
        )
        report["cases"] = [_cache_case_row(result) for result in results]
    else:
        report["cases"] = [_toolcall_case_row(result) for result in results]
    return report


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _metric_table(metrics: dict[str, float]) -> list[str]:
    lines = ["| Metric | Value |", "| --- | --- |"]
    for name, value in metrics.items():
        lines.append(f"| {name} | {value:.4f} |")
    return lines


def _gate_check_table(report: dict[str, Any]) -> list[str]:
    lines = [
        "| Check | TP | FP | FN | TN | Precision | Recall |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in CHECK_NAMES:
        check = report["gate_counts"]["checks"][name]
        precision = report["metrics"][f"{name}_precision"]
        recall = report["metrics"][f"{name}_recall"]
        lines.append(
            f"| {name} | {check['tp']} | {check['fp']} | {check['fn']} | "
            f"{check['tn']} | {precision:.4f} | {recall:.4f} |"
        )
    return lines


def _injection_pattern_table(report: dict[str, Any]) -> list[str]:
    lines = [
        "| Pattern | Family | TP | FP | FN | TN | Precision | Recall |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    patterns = report["injection_counts"]["patterns"]
    for pattern_id, counts in patterns.items():
        precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
        recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
        lines.append(
            f"| {pattern_id} | {counts['family']} | {counts['tp']} | {counts['fp']} | "
            f"{counts['fn']} | {counts['tn']} | {precision:.4f} | {recall:.4f} |"
        )
    return lines


def _rag_strategy_table(report: dict[str, Any]) -> list[str]:
    lines = [
        "| Strategy | Recall@5 | MRR | Cases | Rewrite rounds | Retrieval rounds "
        "| Cases with rewrite | p50 ms | p95 ms | Errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, strategy in report["strategies"].items():
        latency = strategy["latency_ms"]
        lines.append(
            f"| {name} | {strategy['recall_at_5']:.4f} | {strategy['mrr']:.4f} | "
            f"{strategy['cases']} | {strategy['rewrite_rounds']} | "
            f"{strategy['retrieval_rounds']} | {strategy['cases_with_rewrite']} | "
            f"{latency['p50']} | {latency['p95']} | {strategy['errors']} |"
        )
    return lines


def _cache_sweep_table(report: dict[str, Any]) -> list[str]:
    lines = [
        "| Threshold | Hits | Positive pairs | Hit rate | False hits | Negative pairs "
        "| False-hit rate | Accuracy |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    chosen = report.get("chosen_threshold")
    for row in report["threshold_sweep"]:
        marker = " (chosen)" if chosen is not None and abs(row["threshold"] - chosen) < 1e-9 else ""
        lines.append(
            f"| {row['threshold']:.2f}{marker} | {row['hits']} | {row['positive_pairs']} | "
            f"{row['hit_rate']:.4f} | {row['false_hits']} | {row['negative_pairs']} | "
            f"{row['false_hit_rate']:.4f} | {row['accuracy']:.4f} |"
        )
    return lines


def _case_table(report: dict[str, Any]) -> list[str]:
    if report["suite"] == "cache":
        lines = [
            "| Case | Query A | Query B | Should hit | Pair type | Similarity "
            "| Predicted hit | Correct | Note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for case in report["cases"]:
            note = case["error"] or case["note"] or ""
            if not case["correct"] and not case["error"]:
                note = (note + "; " if note else "") + "classified wrong at the chosen threshold"
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(case["case_id"]),
                        _cell(str(case["query_a"] or "")),
                        _cell(str(case["query_b"] or "")),
                        "yes" if case["should_hit"] else "no",
                        _cell(str(case["pair_type"] or "")),
                        f"{case['similarity']:.4f}",
                        "yes" if case["predicted_hit"] else "no",
                        "yes" if case["correct"] else "no",
                        _cell(note),
                    ]
                )
                + " |"
            )
        return lines

    if report["suite"] == "rag":
        names = list(report["strategies"])
        header = (
            "| Case | Query | Needs rewrite | Relevant | "
            + " | ".join(f"{name} R@5/MRR" for name in names)
            + " | Rewrites | Note |"
        )
        lines = [
            header,
            "| --- | --- | --- | --- | " + " | ".join("---" for _ in names) + " | --- | --- |",
        ]
        for case in report["cases"]:
            cells = []
            rewrites = []
            for name in names:
                strategy = case["strategies"].get(name, {})
                cells.append(
                    f"{strategy.get('recall_at_5', 0.0):.3f}/{strategy.get('mrr', 0.0):.3f}"
                )
                rewrites.append(str(strategy.get("rewrite_rounds", 0)))
            note = case["error"] or ""
            errors = [
                f"{name}: {strategy.get('error')}"
                for name, strategy in case["strategies"].items()
                if strategy.get("error")
            ]
            if errors:
                note = "; ".join(errors)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(case["case_id"]),
                        _cell(case["query"]),
                        "yes" if case["needs_rewrite"] else "no",
                        _cell(", ".join(case["relevant"])),
                        *cells,
                        "/".join(rewrites),
                        _cell(note),
                    ]
                )
                + " |"
            )
        return lines

    if report["suite"] == "injection":
        lines = [
            "| Case | Content | Malicious | Expected pattern | Matched patterns | Correct | Note |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for case in report["cases"]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(case["case_id"]),
                        _cell(str(case["content"] or "")),
                        "yes" if case["is_malicious"] else "no",
                        _cell(case["expected_pattern"] or "none"),
                        _cell(", ".join(case["matched_patterns"]) or "none"),
                        "yes" if case["correct"] else "no",
                        _cell(case["note"] or case["error"] or ""),
                    ]
                )
                + " |"
            )
        return lines

    if report["suite"] == "gate":
        lines = [
            "| Case | Query | Expected checks | Actual checks | Action | Weak | Correct | Note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for case in report["cases"]:
            expected = ", ".join(case["expected_failed_checks"]) or "none"
            actual = ", ".join(case["actual_failed_checks"]) or "none"
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(case["case_id"]),
                        _cell(case["query"]),
                        _cell(expected),
                        _cell(actual),
                        _cell(case["action"]),
                        "yes" if case["labeled_weak"] else "no",
                        "yes" if case["correct"] else "no",
                        _cell(case["note"] or case["error"] or ""),
                    ]
                )
                + " |"
            )
        return lines

    if report["suite"] == "routing":
        lines = [
            "| Case | Query | Expected | Actual | Exact | Jaccard | Latency ms "
            "| Tokens in/cached/out | Note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for case in report["cases"]:
            note = case["error"] or case.get("reason") or ""
            tokens = f"{case['input_tokens']}/{case['cached_input_tokens']}/{case['output_tokens']}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(case["case_id"]),
                        _cell(case["query"]),
                        _cell(case["expected_plan"]),
                        _cell(case["actual_plan"]),
                        "yes" if case["exact_match"] else "no",
                        f"{case['jaccard']:.3f}",
                        str(case["latency_ms"]),
                        tokens,
                        _cell(note),
                    ]
                )
                + " |"
            )
        return lines

    lines = [
        "| Case | Query | Expected tool | Actual calls | Name | Args | Calls "
        "| Latency ms | Tokens in/cached/out | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        actual = ", ".join(
            f"{call['name']}({json.dumps(call['arguments'], ensure_ascii=False)})"
            for call in case["actual_calls"]
        )
        expected = (
            f"{case['expected_tool']} {json.dumps(case['expected_args'], ensure_ascii=False)}"
        )
        note = case["error"] or ""
        tokens = f"{case['input_tokens']}/{case['cached_input_tokens']}/{case['output_tokens']}"
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(case["case_id"]),
                    _cell(case["query"]),
                    _cell(expected),
                    _cell(actual),
                    "yes" if case["tool_name_match"] else "no",
                    "yes" if case["argument_match"] else "no",
                    str(case["call_count"]),
                    str(case["latency_ms"]),
                    tokens,
                    _cell(note),
                ]
            )
            + " |"
        )
    return lines


def _confusion_table(matrix: dict[str, dict[str, int]]) -> list[str]:
    actual_labels = sorted({actual for row in matrix.values() for actual in row})
    lines = [
        "| Expected \\ Actual | " + " | ".join(actual_labels) + " |",
        "| --- | " + " | ".join("---" for _ in actual_labels) + " |",
    ]
    for expected, row in matrix.items():
        lines.append(
            "| "
            + _cell(expected)
            + " | "
            + " | ".join(str(row.get(actual, 0)) for actual in actual_labels)
            + " |"
        )
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {str(report['suite']).title()} eval: {report['provider']}", ""]
    lines.append("## Run")
    lines.append("")
    lines.append(f"- Dataset: `{report['dataset_version']}` at `{report['dataset_path']}`")
    lines.append(
        f"- Split: `{report['split']}`, scored {report['case_count']} of "
        f"{report['split_case_count']} cases in the split "
        f"({report['dataset_case_count']} in the dataset)"
    )
    if report["smoke"]:
        lines.append(
            "- **Smoke run:** `--limit` was used, so this report is not a full-suite result."
        )
    lines.append(
        f"- Orchestrator: `{report['orchestrator']}`; provider: `{report['provider']}`; "
        f"model: `{report['model']}`"
    )
    lines.append(f"- Generated: {report['generated_at']}")
    lines.append(f"- Wall time: {report['wall_time_s']} s")
    if report["recording"]:
        lines.append(f"- Recording run: fixtures were recorded with `{report['recorded_with']}`")
    elif report.get("deterministic"):
        lines.append(
            "- Deterministic suite: no model call is made; the provider and model fields are "
            "recorded from the runner but unused."
        )
    elif report["provider"] == "mock" and report["suite"] != "cache":
        if report["suite"] == "rag":
            lines.append(
                "- Mock replay: rewrite calls come from fixtures, but retrieval still uses "
                "the configured embedding model and the database, so the latency and "
                "scores are measured retrieval results."
            )
        else:
            lines.append(
                "- Mock replay: model latency is recorded as 0 and the CI gate measures "
                "the pipeline, not the model (design.md section 18)."
            )
    if report["suite"] == "cache":
        lines.append(f"- Embeddings: {report.get('embedding_source') or 'not recorded'}")
        if report.get("deterministic_embeddings"):
            lines.append(
                "- **Deterministic mock embeddings:** this run checks the suite mechanics "
                "only. A hash-based fake has no semantic similarity, so its hit and "
                "false-hit rates are not a quality measurement."
            )
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    lines.extend(_metric_table(report["metrics"]))
    lines.append("")
    if report["suite"] == "routing" and report.get("plan_source", "").startswith("agent loop"):
        lines.append(
            "Agent loop mode: each case sends the first step of the agent loop (design.md "
            "section 6.9): the production loop prompt, the default tenant's read-only tools "
            "and the platform tools `result_page@1` and `search_docs@1`. The plan is derived "
            "from the tool calls in that decision with the same function the adapter uses. "
            "The suite does not execute tools, mirroring the supervisor mode, which does not "
            "run agents. A live loop can call more tools on later steps, so a whole run's "
            "plan is a superset of the first decision's: with exact-set scoring this number "
            "can land above or below the live loop's plan accuracy. It measures the first "
            "decision; the comparison report puts it beside the supervisor mode."
        )
        lines.append("")
    elif report["suite"] == "toolcall" and report.get("orchestrator") == "agent_loop":
        lines.append(
            "Agent loop mode: each case sends the first step of the agent loop (design.md "
            "section 6.9), the production loop prompt and the tools the adapter exposes (the "
            "default tenant's read-only tools plus `result_page@1` and `search_docs@1`), and "
            "scores the first tool call. The suite does not execute the tool or continue the "
            "loop, so a live loop can choose a different or additional tool on a later step."
        )
        lines.append("")

    if report["suite"] == "cache":
        lines.append("## Threshold sweep")
        lines.append("")
        lines.extend(_cache_sweep_table(report))
        lines.append("")
        chosen = report.get("chosen_threshold")
        chosen_text = (
            f"The configured threshold is {chosen:.2f}."
            if chosen is not None
            else "The configured threshold was not recorded."
        )
        lines.append(
            "A pair is a hit when its cosine similarity is at or above the threshold. "
            "Hit rate is the share of pairs labeled `should_hit` that hit; the "
            "false-hit rate is the share of near-miss pairs that hit anyway. "
            f"{chosen_text} The value in `config/tenants.yaml` and the comment in "
            "`config/platform.yaml` point at this report. Both questions of every pair "
            "are embedded once before the cases run, so the runner wall maximum is that "
            "batch and a case's own similarity is immediate."
        )
        lines.append("")

    if report["suite"] == "injection":
        lines.append("## Patterns")
        lines.append("")
        lines.append(f"Pattern set version: `{report['pattern_set_version']}`.")
        lines.append("")
        lines.extend(_injection_pattern_table(report))
        lines.append("")
        counts = report["injection_counts"]
        lines.append(
            "Detection rate is the share of labeled malicious samples that were "
            "flagged; the false positive rate is the share of labeled benign samples "
            "that were flagged. Per-pattern precision treats a match on any benign "
            "sample as a false positive for that pattern; per-pattern recall counts "
            "the malicious samples written for that pattern id. A malicious sample "
            "that another pattern also catches does not change a pattern's score. "
            "Labels were constructed and audited by the implementation session, not "
            "by an independent human annotator. Benign samples include near-miss "
            "policy text, and two deliberate false-positive traps."
        )
        lines.append("")
        lines.append(
            f"False positive case ids: "
            f"{', '.join(counts['false_positive_cases']) or 'none'}; "
            f"false negative case ids: "
            f"{', '.join(counts['false_negative_cases']) or 'none'}."
        )
        lines.append("")

    if report["suite"] == "rag":
        lines.append("## Strategies")
        lines.append("")
        lines.extend(_rag_strategy_table(report))
        lines.append("")
        lines.append("Cases tagged `needs_rewrite`:")
        lines.append("")
        lines.append("| Strategy | Cases | Recall@5 | MRR |")
        lines.append("| --- | --- | --- | --- |")
        for name, strategy in report["strategies"].items():
            lines.append(
                f"| {name} | {strategy['needs_rewrite_cases']} | "
                f"{strategy['needs_rewrite_recall_at_5']:.4f} | "
                f"{strategy['needs_rewrite_mrr']:.4f} |"
            )
        lines.append("")
        lines.append(
            "All four strategies run on the same cases, in the same order. Recall@5 counts "
            "the share of the labeled chunk keys in the first five results; MRR is the mean "
            "of 1/rank of the first relevant result. A chunk's score is always its cosine "
            "similarity, so the rewrite threshold and the fused order stay separate. The "
            "conditional strategies rewrite only when history is present or the best cosine "
            "is below the tenant's `retrieval.min_score`, and never make more than two "
            "retrieval rounds. The latency columns are wall time per case for that strategy, "
            "including the embedding call and any rewrite call."
        )
        lines.append("")
        lines.append(
            "Cross-encoder re-ranking is not enabled: it is optional in design.md section "
            "9.2, would add sentence-transformers and a torch stack on an 8 GB GPU shared "
            "with qwen3-8b and bge-m3, and this run has no measured evidence that it would "
            "pay for that cost."
        )
        lines.append("")
        if report.get("orchestrator") not in {None, "langgraph"}:
            lines.append(
                f"Orchestrator dimension: this report is labeled "
                f"`{report['orchestrator']}` for the provider by orchestrator comparison. "
                "Retrieval is orchestration independent (both adapters share the retriever "
                "and the rewrite helper), so the strategies and code path are identical to "
                "the langgraph report; any difference between the two runs is run-to-run "
                "timing, not a mechanism difference."
            )
            lines.append("")
        if report.get("judge"):
            judge = report["judge"]
            lines.append("## Answer judge")
            lines.append("")
            lines.append(
                f"Judge: {judge['judge'] or 'not recorded'}. Each case generated one answer "
                f"from the `{judge['strategy']}` retrieval result and the judge scored it "
                "against the evidence. A judge verdict never changes the retrieval metrics "
                "or the `passed` flag above; judge mode is opt-in with `--judge`."
            )
            lines.append("")
            lines.append("| Cases judged | Acceptable | Weak | Errors | Accept rate |")
            lines.append("| --- | --- | --- | --- | --- |")
            lines.append(
                f"| {judge['cases']} | {judge['acceptable']} | {judge['weak']} | "
                f"{judge['errors']} | {judge['accept_rate']:.4f} |"
            )
            lines.append("")
            lines.append(
                "Weak case ids: "
                + (", ".join(judge["weak_case_ids"]) or "none")
                + ". Judge error ids: "
                + (", ".join(judge["error_case_ids"]) or "none")
                + "."
            )
            lines.append("")
            generation = judge["generation_latency_ms"]
            judging = judge["judge_latency_ms"]
            lines.append(
                f"Answer generation p50 {generation['p50']} ms, judge call p50 "
                f"{judging['p50']} ms. The latency table below stays retrieval only, "
                "so it is comparable to a plain rag report. Retrieval numbers in this "
                "report come from this run: the rewrite calls are model output, so they "
                "can differ slightly from the committed rag report. The judge metric is "
                "what judge mode adds."
            )
            lines.append("")
            lines.append("| Case | Verdict | Score | Answer | Reason |")
            lines.append("| --- | --- | --- | --- | --- |")
            for case in report["cases"]:
                payload = case.get("judge") or {}
                verdict = payload.get("verdict") or "error"
                score = payload.get("score")
                score_text = "" if score is None else f"{score:.2f}"
                note = payload.get("reason") or payload.get("error") or ""
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _cell(case["case_id"]),
                            _cell(verdict),
                            score_text,
                            _cell(payload.get("answer") or ""),
                            _cell(note),
                        ]
                    )
                    + " |"
                )
            lines.append("")

    if report["suite"] == "gate":
        lines.append("## Checks")
        lines.append("")
        lines.extend(_gate_check_table(report))
        lines.append("")
        lines.append(
            "Labels: the cases and their expected failed checks were constructed and read "
            "through by the implementation session, not by an independent human annotator. "
            "Precision and recall measure the deterministic checks against those labels; "
            "they are not a claim about real-world answer quality. The derived-percentage "
            "case is labeled acceptable on purpose, so its false rejection is visible "
            "instead of hidden (ADR 0013)."
        )
        lines.append("")
        counts = report["gate_counts"]
        lines.append(
            f"Predicted weak cases: {counts['predicted_weak_cases']}; "
            f"labeled weak cases: {counts['labeled_weak_cases']}; "
            f"false positive ids: {', '.join(counts['false_positive_cases']) or 'none'}; "
            f"false negative ids: {', '.join(counts['false_negative_cases']) or 'none'}."
        )
        lines.append("")

    lines.append("## Latency")
    lines.append("")
    lines.append("| Window | p50 ms | p95 ms | min ms | max ms | mean ms |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    first_label = "Retrieval" if report["suite"] == "rag" else "Model"
    if report["suite"] == "injection":
        first_label = "Scanner"
    if report["suite"] == "cache":
        first_label = "Similarity"
    for name, key in ((first_label, "latency_ms"), ("Runner wall", "runner_latency_ms")):
        summary = report[key]
        lines.append(
            f"| {name} | {summary['p50']} | {summary['p95']} | {summary['min']} | "
            f"{summary['max']} | {summary['mean']} |"
        )
    lines.append("")

    lines.append("## Usage")
    lines.append("")
    lines.append("| Input tokens | Cached input tokens | Output tokens | Total tokens |")
    lines.append("| --- | --- | --- | --- |")
    usage = report["usage"]
    lines.append(
        f"| {usage['input_tokens']} | {usage['cached_input_tokens']} | "
        f"{usage['output_tokens']} | {usage['total_tokens']} |"
    )
    lines.append("")
    if report["cost_usd"] is None:
        lines.append(
            f"Cost is not reported: provider `{report['provider']}` has no entry in "
            "`config/prices.yaml`, so the JSON value is null rather than an invented "
            "zero. A mock replay makes no billable API call."
        )
    elif report["no_api_cost"]:
        lines.append(
            f"Cost: USD {report['cost_usd']:.6f} for this run. The provider is local "
            "(LM Studio), so the run has no API cost; the price table records zero for "
            "it on purpose."
        )
    else:
        price = report["price"] or {}
        basis = f"verified on {price.get('verified_on') or 'an unrecorded date'}"
        if price.get("source"):
            basis += f", {price['source']}"
        lines.append(
            f"Cost: USD {report['cost_usd']:.6f} for this run, from `config/prices.yaml` "
            f"({basis}). Cached input tokens are billed at the cached rate."
        )
    lines.append("")

    if "confusion_matrix" in report:
        lines.append("## Plan confusion matrix")
        lines.append("")
        lines.extend(_confusion_table(report["confusion_matrix"]))
        lines.append("")

    lines.append(f"## Cases ({report['case_count']})")
    lines.append("")
    lines.extend(_case_table(report))
    lines.append("")
    lines.append(
        f"Errors: {report['error_count']}"
        + (" (" + ", ".join(report["error_case_ids"]) + ")" if report["error_case_ids"] else "")
    )
    lines.append("")
    return "\n".join(lines)


def report_file_stem(suite: str, provider: str, orchestrator: str | None) -> str:
    """File name stem: ``<suite>-<provider>``, plus the orchestrator when set.

    The LangGraph reports keep the names committed before T2.11
    (``routing-mock``), so a second orchestrator gets its own file instead of
    overwriting the baseline report (design.md section 20.2). The compare
    command locates reports with the same rule, so a reader and a writer cannot
    disagree about a file name.
    """
    if isinstance(orchestrator, str) and orchestrator and orchestrator != "langgraph":
        return f"{suite}-{orchestrator}-{provider}"
    return f"{suite}-{provider}"


def report_stem(report: dict[str, Any]) -> str:
    """The file stem: ``<suite>-<provider>``, with ``judge`` after the suite.

    Judge mode writes ``<suite>-judge-<provider>`` so the plain report (and any
    baseline or comparison that reads it) is never overwritten (T3.04).
    """
    if report.get("judge"):
        return report_file_stem(
            f"{report['suite']}-judge",
            str(report["provider"]),
            report.get("orchestrator"),
        )
    return report_file_stem(
        str(report["suite"]),
        str(report["provider"]),
        report.get("orchestrator"),
    )


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = report_stem(report)
    markdown_path = output / f"{stem}.md"
    json_path = output / f"{stem}.json"
    markdown_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return markdown_path, json_path


__all__ = [
    "ReportMeta",
    "build_report",
    "latency_summary",
    "percentile",
    "render_markdown",
    "report_file_stem",
    "report_stem",
    "write_report",
]
