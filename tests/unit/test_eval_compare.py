"""The compare command: merge, missing combinations, metric choice, determinism."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from switchboard.cli import app
from switchboard.core.errors import ConfigError
from switchboard.eval.compare import (
    build_comparison,
    comparison_metrics,
    load_zero_price_providers,
    parse_not_run_reasons,
    render_comparison_markdown,
    write_comparison,
)
from switchboard.eval.report import report_file_stem

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_report(
    *,
    suite: str,
    provider: str,
    orchestrator: str,
    metrics: dict[str, float],
    case_count: int = 10,
    total_tokens: int = 1000,
    cost_usd: float | None = 0.0,
    no_api_cost: bool = True,
    dataset_version: str = "v1",
    smoke: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    return {
        "suite": suite,
        "dataset_version": dataset_version,
        "dataset_path": f"evals/datasets/{dataset_version}/{suite}.jsonl",
        "split": "test",
        "case_count": case_count,
        "smoke": smoke,
        "orchestrator": orchestrator,
        "provider": provider,
        "model": model or ("qwen3-8b" if provider == "lmstudio" else "deepseek-flash"),
        "metrics": metrics,
        "latency_ms": {"p50": 10.0, "p95": 20.0},
        "usage": {
            "input_tokens": total_tokens,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": total_tokens,
        },
        "cost_usd": cost_usd,
        "no_api_cost": no_api_cost,
    }


def write_report(directory: Path, payload: dict[str, Any]) -> Path:
    stem = report_file_stem(
        str(payload["suite"]), str(payload["provider"]), str(payload["orchestrator"])
    )
    path = directory / f"{stem}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def row_for(
    comparison: dict[str, Any], suite: str, provider: str, orchestrator: str
) -> dict[str, Any]:
    for row in comparison["rows"]:
        if (
            row["suite"] == suite
            and row["provider"] == provider
            and row["orchestrator"] == orchestrator
        ):
            return cast(dict[str, Any], row)
    raise AssertionError(f"no row for {suite}/{provider}/{orchestrator}")


def test_comparison_merges_reports_and_marks_missing_combinations(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"plan_accuracy": 0.9231, "plan_jaccard": 0.9359},
        ),
    )
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="agent_loop",
            metrics={"plan_accuracy": 0.641, "plan_jaccard": 0.7564},
        ),
    )
    write_report(
        tmp_path,
        make_report(
            suite="toolcall",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"both_accuracy": 0.95, "argument_accuracy": 0.95},
        ),
    )
    write_report(
        tmp_path,
        make_report(
            suite="rag",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={
                "recall_at_5_conditional_hybrid": 1.0,
                "mrr_conditional_hybrid": 0.8521,
            },
        ),
    )

    comparison = build_comparison(
        reports_dir=tmp_path,
        not_run_reasons={"deepseek": "DEEPSEEK_API_KEY missing"},
    )

    assert len(comparison["rows"]) == 3 * 2 * 2
    assert comparison["measured"] == 4
    assert comparison["not_run"] == 8
    assert comparison["smoke"] == 0

    measured = row_for(comparison, "routing", "lmstudio", "agent_loop")
    assert measured["status"] == "measured"
    assert measured["primary_metric"] == "plan_accuracy"
    assert measured["primary_value"] == 0.641
    assert measured["dataset_version"] == "v1"
    assert measured["case_count"] == 10
    assert measured["report"] == "routing-agent_loop-lmstudio.json"

    missing = row_for(comparison, "toolcall", "deepseek", "agent_loop")
    assert missing["status"] == "not run"
    assert missing["reason"] == "DEEPSEEK_API_KEY missing"
    assert missing["primary_value"] is None
    assert missing["report"] is None


def test_metric_selection_per_suite() -> None:
    routing = comparison_metrics("routing", {"plan_accuracy": 0.9, "plan_jaccard": 0.8})
    assert (routing.primary_name, routing.primary_value) == ("plan_accuracy", 0.9)
    assert (routing.secondary_name, routing.secondary_value) == ("plan_jaccard", 0.8)

    toolcall = comparison_metrics("toolcall", {"both_accuracy": 0.75, "argument_accuracy": 0.8})
    assert (toolcall.primary_name, toolcall.primary_value) == ("both_accuracy", 0.75)
    assert (toolcall.secondary_name, toolcall.secondary_value) == (
        "argument_accuracy",
        0.8,
    )

    rag = comparison_metrics(
        "rag",
        {
            "recall_at_5_query_as_written": 0.9,
            "mrr_query_as_written": 0.7,
            "recall_at_5_conditional_hybrid": 1.0,
            "mrr_conditional_hybrid": 0.8521,
        },
    )
    assert rag.primary_name == "recall_at_5_conditional_hybrid"
    assert rag.primary_value == 1.0
    assert (rag.secondary_name, rag.secondary_value) == ("mrr_conditional_hybrid", 0.8521)


def test_metric_selection_falls_back_to_a_measured_rag_strategy() -> None:
    choice = comparison_metrics(
        "rag",
        {
            "recall_at_5_query_as_written": 0.9,
            "mrr_query_as_written": 0.7,
            "recall_at_5_always_rewrite": 0.8,
            "mrr_always_rewrite": 0.6,
        },
    )
    assert choice.primary_name == "recall_at_5_always_rewrite"
    assert choice.primary_value == 0.8
    assert (choice.secondary_name, choice.secondary_value) == ("mrr_always_rewrite", 0.6)


def test_metric_selection_refuses_a_report_without_the_headline() -> None:
    with pytest.raises(ConfigError, match="plan_accuracy"):
        comparison_metrics("routing", {"plan_jaccard": 0.8})
    with pytest.raises(ConfigError, match="recall_at_5"):
        comparison_metrics("rag", {"mrr_query_as_written": 0.7})


def test_tokens_and_cost_are_scaled_per_100_queries(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="toolcall",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"both_accuracy": 0.95, "argument_accuracy": 0.95},
            case_count=20,
            total_tokens=4000,
            cost_usd=0.5,
            no_api_cost=False,
        ),
    )

    row = row_for(
        build_comparison(
            reports_dir=tmp_path,
            providers=("lmstudio",),
            orchestrators=("langgraph",),
            suites=("toolcall",),
        ),
        "toolcall",
        "lmstudio",
        "langgraph",
    )

    assert row["tokens_per_100_queries"] == 20000.0
    assert row["cost_per_100_queries"] == 2.5


def test_a_local_provider_reports_zero_cost_and_says_so(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"plan_accuracy": 0.9, "plan_jaccard": 0.8},
        ),
    )

    markdown = render_comparison_markdown(
        build_comparison(
            reports_dir=tmp_path,
            providers=("lmstudio",),
            orchestrators=("langgraph",),
            suites=("routing",),
        )
    )

    assert "(no API cost)" in markdown
    assert "no API cost" in markdown


def test_the_zero_price_row_labels_an_old_report_without_backfilling_it(
    tmp_path: Path,
) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"plan_accuracy": 0.9, "plan_jaccard": 0.8},
            cost_usd=None,
            no_api_cost=False,
        ),
    )

    comparison = build_comparison(
        reports_dir=tmp_path,
        providers=("lmstudio",),
        orchestrators=("langgraph",),
        suites=("routing",),
        zero_price_providers=frozenset({"lmstudio"}),
    )
    row = row_for(comparison, "routing", "lmstudio", "langgraph")

    assert row["cost_usd"] == 0.0
    assert row["cost_per_100_queries"] == 0.0
    assert row["cost_source"] == "price_table_zero"
    assert row["no_api_cost"] is True
    assert "(no API cost)" in render_comparison_markdown(comparison)
    # The report file itself is unchanged: no backfilled cost is written.
    assert json.loads((tmp_path / "routing-lmstudio.json").read_text())["cost_usd"] is None


def test_load_zero_price_providers_reads_the_committed_table() -> None:
    providers = load_zero_price_providers(REPO_ROOT / "config" / "prices.yaml")

    assert "lmstudio" in providers
    assert "deepseek" not in providers


def test_load_zero_price_providers_names_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read the price table"):
        load_zero_price_providers(tmp_path / "prices.yaml")


def test_a_null_cost_is_never_shown_as_zero(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="mock",
            orchestrator="langgraph",
            metrics={"plan_accuracy": 0.9, "plan_jaccard": 0.8},
            cost_usd=None,
            no_api_cost=False,
        ),
    )

    row = row_for(
        build_comparison(
            reports_dir=tmp_path,
            providers=("mock",),
            orchestrators=("langgraph",),
            suites=("routing",),
        ),
        "routing",
        "mock",
        "langgraph",
    )

    assert row["cost_usd"] is None
    assert row["cost_per_100_queries"] is None


def test_output_is_deterministic(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"plan_accuracy": 0.9, "plan_jaccard": 0.8},
        ),
    )
    kwargs: dict[str, Any] = {
        "reports_dir": tmp_path,
        "providers": ("lmstudio", "deepseek"),
        "orchestrators": ("langgraph", "agent_loop"),
        "suites": ("routing", "toolcall", "rag"),
        "not_run_reasons": {"deepseek": "DEEPSEEK_API_KEY missing"},
    }
    first = build_comparison(**kwargs)
    second = build_comparison(**kwargs)

    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    assert render_comparison_markdown(first) == render_comparison_markdown(second)
    assert "generated_at" not in json.dumps(first)

    write_comparison(first, tmp_path, name="compare")
    first_markdown = (tmp_path / "compare.md").read_bytes()
    first_json = (tmp_path / "compare.json").read_bytes()
    write_comparison(second, tmp_path, name="compare")
    assert (tmp_path / "compare.md").read_bytes() == first_markdown
    assert (tmp_path / "compare.json").read_bytes() == first_json


def test_a_smoke_report_is_not_compared(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"plan_accuracy": 0.9, "plan_jaccard": 0.8},
        ),
    )
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="agent_loop",
            metrics={"plan_accuracy": 1.0, "plan_jaccard": 1.0},
            smoke=True,
        ),
    )

    comparison = build_comparison(
        reports_dir=tmp_path,
        providers=("lmstudio",),
        orchestrators=("langgraph", "agent_loop"),
        suites=("routing",),
    )

    smoke = row_for(comparison, "routing", "lmstudio", "agent_loop")
    assert smoke["status"] == "smoke"
    assert smoke["primary_value"] is None
    assert comparison["smoke"] == 1
    assert comparison["measured"] == 1


def test_missing_dataset_versions_produce_a_warning(tmp_path: Path) -> None:
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="langgraph",
            metrics={"plan_accuracy": 0.9, "plan_jaccard": 0.8},
            dataset_version="v1",
        ),
    )
    write_report(
        tmp_path,
        make_report(
            suite="routing",
            provider="lmstudio",
            orchestrator="agent_loop",
            metrics={"plan_accuracy": 0.9, "plan_jaccard": 0.8},
            dataset_version="v2",
        ),
    )

    comparison = build_comparison(
        reports_dir=tmp_path,
        providers=("lmstudio",),
        orchestrators=("langgraph", "agent_loop"),
        suites=("routing",),
    )

    assert len(comparison["warnings"]) == 1
    assert "v1, v2" in comparison["warnings"][0]


def test_an_empty_reports_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no eval reports"):
        build_comparison(reports_dir=tmp_path)


def test_an_unknown_suite_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="supports"):
        build_comparison(reports_dir=tmp_path, suites=("routing", "cache"))


def test_not_run_reasons_are_parsed_and_validated() -> None:
    assert parse_not_run_reasons(["deepseek=DEEPSEEK_API_KEY missing"]) == {
        "deepseek": "DEEPSEEK_API_KEY missing"
    }
    with pytest.raises(ConfigError, match="provider=reason"):
        parse_not_run_reasons(["deepseek"])
    with pytest.raises(ConfigError, match="twice"):
        parse_not_run_reasons(["deepseek=one", "deepseek=two"])

    with pytest.raises(ConfigError, match="outside --provider"):
        build_comparison(
            reports_dir=Path("evals/reports"),
            not_run_reasons={"openai": "missing"},
        )


def test_report_file_stem_matches_the_committed_names() -> None:
    assert report_file_stem("routing", "lmstudio", "langgraph") == "routing-lmstudio"
    assert report_file_stem("routing", "lmstudio", "agent_loop") == "routing-agent_loop-lmstudio"


def test_cli_compare_uses_committed_reports(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "compare",
            "--reports-dir",
            str(REPO_ROOT / "evals" / "reports"),
            "--output-dir",
            str(tmp_path),
            "--not-run-reason",
            "deepseek=DEEPSEEK_API_KEY missing",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "measured" in result.output
    markdown_path = tmp_path / "compare.md"
    json_path = tmp_path / "compare.json"
    assert markdown_path.is_file()
    assert json_path.is_file()

    comparison = json.loads(json_path.read_text(encoding="utf-8"))
    measured = [
        row
        for row in comparison["rows"]
        if row["provider"] == "lmstudio" and row["status"] == "measured"
    ]
    assert measured, "the committed LM Studio reports must be comparable"
    deepseek = [row for row in comparison["rows"] if row["provider"] == "deepseek"]
    assert deepseek
    assert all(row["status"] in {"measured", "not run"} for row in deepseek)
    not_run = [row for row in deepseek if row["status"] == "not run"]
    assert all(row["reason"] == "DEEPSEEK_API_KEY missing" for row in not_run)
    if not_run:
        assert "not run" in result.output
    assert "[routing-lmstudio.md](routing-lmstudio.md)" in markdown_path.read_text(encoding="utf-8")


def test_cli_compare_refuses_an_unknown_orchestrator(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "compare",
            "--reports-dir",
            str(REPO_ROOT / "evals" / "reports"),
            "--output-dir",
            str(tmp_path),
            "--orchestrator",
            "sequential",
        ],
    )

    assert result.exit_code == 1
    assert "unknown orchestrator 'sequential'" in result.output
