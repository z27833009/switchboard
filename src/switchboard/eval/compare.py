"""Comparison table over per-suite eval reports (design.md sections 20.2 and 20.3).

The compare command merges the committed JSON reports into one table with
provider and orchestrator as dimensions. Metric values and latency are copied
unchanged from the report that a run produced; only the per-100 figures are
derived from the report's measured totals and case count. A report written
before the price table keeps its null cost, but a zero row for its provider
lets the table say "no API cost" without touching the file. A combination with
no report is ``not run`` and may carry a reason supplied on the command line, so
a pending paid provider is visible in the table instead of silently missing.
Smoke reports are excluded because a ``--limit`` run is not a full-suite result.

The output is deterministic: rows follow the suite, provider and orchestrator
order given on the command line, there is no generated-at timestamp, and two
runs over the same reports write the same bytes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from switchboard.core.config import PricesConfig
from switchboard.core.errors import ConfigError
from switchboard.eval.gate import read_json_object
from switchboard.eval.report import report_file_stem

DEFAULT_COMPARE_REPORTS_DIR = Path("evals/reports")
DEFAULT_PRICES_PATH = Path("config/prices.yaml")
DEFAULT_COMPARE_NAME = "compare"
DEFAULT_SUITES: tuple[str, ...] = ("routing", "toolcall", "rag")
DEFAULT_PROVIDERS: tuple[str, ...] = ("lmstudio", "deepseek")
DEFAULT_ORCHESTRATORS: tuple[str, ...] = ("langgraph", "agent_loop")

# The metric each suite is compared on (AC-13a). The rag suite measures four
# strategies; the production path is conditional rewrite plus hybrid search
# (design.md section 9.3), so that strategy is the headline and the others stay
# in the per-suite report.
PRIMARY_METRICS: dict[str, str] = {
    "routing": "plan_accuracy",
    "toolcall": "both_accuracy",
    "rag": "recall_at_5_conditional_hybrid",
}
SECONDARY_METRICS: dict[str, str] = {
    "routing": "plan_jaccard",
    "toolcall": "argument_accuracy",
}
RAG_RECALL_PREFIX = "recall_at_5_"
RAG_MRR_PREFIX = "mrr_"


@dataclass(frozen=True)
class MetricChoice:
    primary_name: str
    primary_value: float
    secondary_name: str | None
    secondary_value: float | None


def _numeric(metrics: dict[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def comparison_metrics(suite: str, metrics: dict[str, Any]) -> MetricChoice:
    """Pick the headline and secondary metric of one suite from a report.

    Routing and toolcall have one named headline. Rag prefers the production
    strategy and falls back to the first measured recall@5 strategy, so a future
    report that drops or renames the production label still compares instead of
    crashing; the metric name in the output says which one was used.
    """
    try:
        primary_name = PRIMARY_METRICS[suite]
    except KeyError as exc:
        available = ", ".join(sorted(PRIMARY_METRICS))
        raise ConfigError(
            f"the compare command does not know suite {suite!r}; known: {available}"
        ) from exc

    if suite == "rag" and _numeric(metrics, primary_name) is None:
        candidates = sorted(
            name
            for name in metrics
            if name.startswith(RAG_RECALL_PREFIX) and _numeric(metrics, name) is not None
        )
        if not candidates:
            raise ConfigError("the rag report has no numeric recall_at_5_* metric")
        primary_name = candidates[0]

    primary_value = _numeric(metrics, primary_name)
    if primary_value is None:
        raise ConfigError(f"the {suite} report has no numeric {primary_name!r} metric")

    secondary_name = SECONDARY_METRICS.get(suite)
    if secondary_name is None and suite == "rag":
        secondary_name = RAG_MRR_PREFIX + primary_name.removeprefix(RAG_RECALL_PREFIX)
    secondary_value = _numeric(metrics, secondary_name) if secondary_name else None
    if secondary_value is None:
        secondary_name = None
    return MetricChoice(
        primary_name=primary_name,
        primary_value=primary_value,
        secondary_name=secondary_name,
        secondary_value=secondary_value,
    )


def load_zero_price_providers(path: Path = DEFAULT_PRICES_PATH) -> frozenset[str]:
    """Providers whose committed price row is zero, such as a local model.

    The comparison uses this only to label a report written before the price
    table existed. Such a report keeps its null cost (nothing is backfilled into
    the file), but a zero row means the measured run's API cost is exactly zero,
    so the table can still say "no API cost" without inventing a dollar amount.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read the price table {path}: {exc}") from exc
    try:
        payload: Any = yaml.safe_load(raw)
        prices = PricesConfig.model_validate(payload)
    except Exception as exc:
        raise ConfigError(f"{path} is not a valid price table: {exc}") from exc
    return frozenset(entry.provider for entry in prices.entries if entry.is_zero)


def _per_100(value: float | None, case_count: int) -> float | None:
    if value is None or case_count < 1:
        return None
    return round(value * 100 / case_count, 6)


def _latency(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    summary = payload.get("latency_ms")
    if not isinstance(summary, dict):
        return None, None
    p50 = summary.get("p50")
    p95 = summary.get("p95")
    return (
        float(p50) if isinstance(p50, int | float) else None,
        float(p95) if isinstance(p95, int | float) else None,
    )


def _empty_row(suite: str, provider: str, orchestrator: str) -> dict[str, Any]:
    return {
        "suite": suite,
        "provider": provider,
        "orchestrator": orchestrator,
        "status": "not run",
        "reason": None,
        "report": None,
        "model": None,
        "dataset_version": None,
        "split": None,
        "case_count": None,
        "primary_metric": None,
        "primary_value": None,
        "secondary_metric": None,
        "secondary_value": None,
        "p50_ms": None,
        "p95_ms": None,
        "tokens_per_100_queries": None,
        "cost_usd": None,
        "cost_per_100_queries": None,
        "cost_source": None,
        "no_api_cost": None,
    }


def _measured_row(
    suite: str,
    provider: str,
    orchestrator: str,
    path: Path,
    payload: dict[str, Any],
    *,
    zero_price_providers: frozenset[str],
) -> dict[str, Any]:
    declared_suite = payload.get("suite")
    declared_provider = payload.get("provider")
    declared_orchestrator = payload.get("orchestrator")
    if declared_suite != suite:
        raise ConfigError(f"{path} is a {declared_suite!r} report, not {suite!r}")
    if declared_provider != provider:
        raise ConfigError(f"{path} is a {declared_provider!r} report, not {provider!r}")
    if declared_orchestrator != orchestrator:
        raise ConfigError(f"{path} is an {declared_orchestrator!r} report, not {orchestrator!r}")

    row = _empty_row(suite, provider, orchestrator)
    row["report"] = path.name
    row["model"] = payload.get("model")
    row["dataset_version"] = payload.get("dataset_version")
    row["split"] = payload.get("split")
    row["case_count"] = payload.get("case_count")

    if payload.get("smoke") is True:
        row["status"] = "smoke"
        row["reason"] = "smoke run (--limit); not comparable"
        return row

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise ConfigError(f"{path} has no metrics object")
    case_count = payload.get("case_count")
    if isinstance(case_count, bool) or not isinstance(case_count, int) or case_count < 1:
        raise ConfigError(f"{path} has no positive integer case_count")

    choice = comparison_metrics(suite, metrics)
    usage = payload.get("usage")
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    tokens_per_100 = (
        _per_100(float(total_tokens), case_count)
        if isinstance(total_tokens, int | float) and not isinstance(total_tokens, bool)
        else None
    )
    cost = payload.get("cost_usd")
    reported_cost = (
        float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None
    )
    if reported_cost is not None:
        cost_value = reported_cost
        cost_source: str | None = "report"
    elif provider in zero_price_providers:
        # The report predates the price table. The committed zero row makes the
        # measured run's API cost exactly zero; the report file is not touched.
        cost_value = 0.0
        cost_source = "price_table_zero"
    else:
        cost_value = None
        cost_source = None
    p50, p95 = _latency(payload)
    reported_no_api_cost = payload.get("no_api_cost")
    if cost_source == "price_table_zero":
        no_api_cost: bool | None = True
    elif isinstance(reported_no_api_cost, bool):
        no_api_cost = reported_no_api_cost
    else:
        no_api_cost = None

    row.update(
        {
            "status": "measured",
            "primary_metric": choice.primary_name,
            "primary_value": round(choice.primary_value, 6),
            "secondary_metric": choice.secondary_name,
            "secondary_value": (
                round(choice.secondary_value, 6) if choice.secondary_value is not None else None
            ),
            "p50_ms": p50,
            "p95_ms": p95,
            "tokens_per_100_queries": tokens_per_100,
            "cost_usd": cost_value,
            "cost_per_100_queries": _per_100(cost_value, case_count),
            "cost_source": cost_source,
            "no_api_cost": no_api_cost if isinstance(no_api_cost, bool) else None,
        }
    )
    return row


def parse_not_run_reasons(values: list[str] | None) -> dict[str, str]:
    """``provider=reason`` pairs, for a combination that has no report yet."""
    reasons: dict[str, str] = {}
    for raw in values or []:
        provider, separator, reason = raw.partition("=")
        provider = provider.strip()
        reason = reason.strip()
        if not separator or not provider or not reason:
            raise ConfigError(f"--not-run-reason expects provider=reason, got {raw!r}")
        if provider in reasons:
            raise ConfigError(f"--not-run-reason names {provider!r} twice")
        reasons[provider] = reason
    return reasons


def _dataset_version_warnings(rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    suites = list(dict.fromkeys(row["suite"] for row in rows))
    for suite in suites:
        versions = sorted(
            {
                str(row["dataset_version"])
                for row in rows
                if row["suite"] == suite
                and row["status"] == "measured"
                and row["dataset_version"] is not None
            }
        )
        if len(versions) > 1:
            warnings.append(
                f"suite {suite}: measured reports use dataset versions {', '.join(versions)}; "
                "the rows are not directly comparable"
            )
    return warnings


def build_comparison(
    *,
    reports_dir: Path = DEFAULT_COMPARE_REPORTS_DIR,
    suites: tuple[str, ...] = DEFAULT_SUITES,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
    orchestrators: tuple[str, ...] = DEFAULT_ORCHESTRATORS,
    not_run_reasons: dict[str, str] | None = None,
    zero_price_providers: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Merge the expected reports into one provider by orchestrator table."""
    unsupported = sorted(set(suites) - set(PRIMARY_METRICS))
    if unsupported:
        available = ", ".join(sorted(PRIMARY_METRICS))
        raise ConfigError(
            f"the compare command supports {available}; not: {', '.join(unsupported)}"
        )
    reasons = not_run_reasons or {}
    unknown = sorted(set(reasons) - set(providers))
    if unknown:
        raise ConfigError(
            "--not-run-reason names providers outside --provider: " + ", ".join(unknown)
        )

    rows: list[dict[str, Any]] = []
    for suite in suites:
        for provider in providers:
            for orchestrator in orchestrators:
                path = Path(reports_dir) / f"{report_file_stem(suite, provider, orchestrator)}.json"
                if not path.is_file():
                    row = _empty_row(suite, provider, orchestrator)
                    row["reason"] = reasons.get(provider)
                    rows.append(row)
                    continue
                rows.append(
                    _measured_row(
                        suite,
                        provider,
                        orchestrator,
                        path,
                        read_json_object(path),
                        zero_price_providers=zero_price_providers,
                    )
                )

    measured = sum(1 for row in rows if row["status"] == "measured")
    if measured == 0:
        raise ConfigError(f"no eval reports found under {Path(reports_dir)}")
    return {
        "suites": list(suites),
        "providers": list(providers),
        "orchestrators": list(orchestrators),
        "measured": measured,
        "not_run": sum(1 for row in rows if row["status"] == "not run"),
        "smoke": sum(1 for row in rows if row["status"] == "smoke"),
        "rows": rows,
        "warnings": _dataset_version_warnings(rows),
    }


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _number(value: float | None, digits: int) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _status_cell(row: dict[str, Any]) -> str:
    if row["status"] == "measured":
        return "measured"
    reason = row["reason"] or "no report"
    return f"{row['status']}: {reason}"


def _metric_cell(row: dict[str, Any], kind: str) -> tuple[str, str]:
    name = row[f"{kind}_metric"]
    value = row[f"{kind}_value"]
    if name is None:
        return "-", "-"
    return str(name), _number(value, 4)


def _cost_cell(row: dict[str, Any]) -> str:
    if row["status"] != "measured":
        return "-"
    if row["cost_usd"] is None:
        return "not priced"
    cell = f"{row['cost_per_100_queries']:.6f}"
    if row["no_api_cost"]:
        cell += " (no API cost)"
    return cell


def _report_cell(row: dict[str, Any]) -> str:
    report = row["report"]
    if not isinstance(report, str):
        return "-"
    markdown = report.removesuffix(".json") + ".md"
    return f"[{markdown}]({markdown})"


def render_comparison_markdown(comparison: dict[str, Any]) -> str:
    lines = ["# Eval comparison: provider by orchestrator", ""]
    suites = ", ".join(f"`{suite}`" for suite in comparison["suites"])
    providers = ", ".join(f"`{provider}`" for provider in comparison["providers"])
    orchestrators = ", ".join(f"`{orchestrator}`" for orchestrator in comparison["orchestrators"])
    lines.append(
        f"Merged from the JSON reports in `{DEFAULT_COMPARE_REPORTS_DIR.as_posix()}` for "
        f"suites {suites}, providers {providers} and orchestrators {orchestrators}. "
        "Each row copies the numbers of its report; nothing is averaged or estimated. "
        "`not run` means no report exists for that combination, and the reason after the "
        "colon is the one supplied on the command line. Smoke reports (`--limit`) are not "
        "compared."
    )
    lines.append("")
    lines.append(
        f"Measured: {comparison['measured']} of {len(comparison['rows'])} combinations; "
        f"not run: {comparison['not_run']}; smoke: {comparison['smoke']}."
    )
    lines.append("")
    lines.append(
        "| Suite | Provider | Orchestrator | Model | Status | Dataset | Cases "
        "| Primary metric | Value | Secondary metric | Value | p50 ms | p95 ms "
        "| Tokens per 100 queries | Cost per 100 queries | Report |"
    )
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
        "| --- | --- | --- | --- |"
    )
    for row in comparison["rows"]:
        primary_name, primary_value = _metric_cell(row, "primary")
        secondary_name, secondary_value = _metric_cell(row, "secondary")
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["suite"]),
                    _cell(row["provider"]),
                    _cell(row["orchestrator"]),
                    _cell(row["model"] or "-"),
                    _cell(_status_cell(row)),
                    _cell(row["dataset_version"] or "-"),
                    _cell(row["case_count"] if row["case_count"] is not None else "-"),
                    _cell(primary_name),
                    _cell(primary_value),
                    _cell(secondary_name),
                    _cell(secondary_value),
                    _number(row["p50_ms"], 1),
                    _number(row["p95_ms"], 1),
                    _number(row["tokens_per_100_queries"], 1),
                    _cell(_cost_cell(row)),
                    _cell(_report_cell(row)),
                ]
            )
            + " |"
        )
    lines.append("")

    if comparison["warnings"]:
        lines.append("## Warnings")
        lines.append("")
        for warning in comparison["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Plan accuracy is the exact-set routing score of the suite. Tool-call accuracy "
        "is `both_accuracy`: the first tool name and its required arguments both match. "
        "Rag is recall@5 and MRR of `conditional_hybrid`, the production retrieval path "
        "(conditional rewrite plus hybrid search); the other three strategies stay in the "
        "rag report."
    )
    lines.append(
        "- Latency is copied from each report. Routing and toolcall p50 and p95 are one "
        "model call per case, not an end-to-end run. Rag p50 and p95 are the per-case wall "
        "time of all four retrieval strategies, including the embedding call and any "
        "rewrite call."
    )
    lines.append(
        "- Tokens and cost per 100 queries scale the suite's measured totals by its case "
        "count: `total * 100 / cases`. A local provider has an explicit zero price row, so "
        'its cost is 0 and the cell says "no API cost". A report written before the price '
        "table keeps a null cost by design; when `config/prices.yaml` has a zero row for that "
        "provider, this table applies it and the JSON says `cost_source` `price_table_zero`. "
        'A null cost with no zero row is shown as "not priced" and is never turned into an '
        "invented zero."
    )
    lines.append(
        "- Routing and toolcall have one measured row per orchestrator. Rag measures "
        "retrieval, which both adapters share: its langgraph and agent_loop rows run the "
        "same strategy code, so any difference between them is run-to-run timing, not a "
        "mechanism difference."
    )
    lines.append(
        "- The toolcall agent_loop row scores the first decision of the loop with the "
        "loop's full toolset. The suite does not execute tools, and a live loop can call "
        "other tools on later steps."
    )
    lines.append(
        "- Every row links its per-suite markdown report; this comparison never rewrites "
        "those reports."
    )
    lines.append("")
    return "\n".join(lines)


def write_comparison(
    comparison: dict[str, Any],
    output_dir: Path = DEFAULT_COMPARE_REPORTS_DIR,
    *,
    name: str = DEFAULT_COMPARE_NAME,
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    markdown_path = output / f"{name}.md"
    json_path = output / f"{name}.json"
    markdown_path.write_text(render_comparison_markdown(comparison), encoding="utf-8", newline="\n")
    json_path.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return markdown_path, json_path


__all__ = [
    "DEFAULT_COMPARE_NAME",
    "DEFAULT_COMPARE_REPORTS_DIR",
    "DEFAULT_ORCHESTRATORS",
    "DEFAULT_PRICES_PATH",
    "DEFAULT_PROVIDERS",
    "DEFAULT_SUITES",
    "PRIMARY_METRICS",
    "SECONDARY_METRICS",
    "MetricChoice",
    "build_comparison",
    "comparison_metrics",
    "load_zero_price_providers",
    "parse_not_run_reasons",
    "render_comparison_markdown",
    "write_comparison",
]
