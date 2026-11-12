"""Markdown and JSON eval reports (design.md sections 20.2 and 20.3).

Every number in a report comes from the run that produced it. A value that was
not measured (cost, before the price table in T2.06) is ``null`` and the
markdown says so, rather than a guessed number.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from switchboard.core.models import Usage
from switchboard.eval.runner import CaseRun
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
    return {
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

    if meta.suite == "routing":
        metrics = _routing_metrics(results)
    elif meta.suite == "gate":
        metrics = _gate_metrics(results)
    elif meta.suite == "rag":
        metrics = _rag_metrics(results)
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
        "cost_usd": None,
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
        report["cases"] = [_rag_case_row(result) for result in results]
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


def _case_table(report: dict[str, Any]) -> list[str]:
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
    elif report["provider"] == "mock":
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
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    lines.extend(_metric_table(report["metrics"]))
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
    lines.append(
        "Cost is not reported as USD: no price table exists until T2.06, so the JSON value is "
        "null. A local model such as LM Studio has no API cost."
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


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{report['suite']}-{report['provider']}"
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
    "write_report",
]
