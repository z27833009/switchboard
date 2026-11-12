"""Regression gate: thresholds, provenance checks and the CLI contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from switchboard.cli import app
from switchboard.core.errors import ConfigError
from switchboard.eval.gate import evaluate_gate

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINES = REPO_ROOT / "evals" / "baselines"
REPORTS = REPO_ROOT / "evals" / "reports"


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def baseline_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suite": "routing",
        "dataset_version": "v1",
        "metrics": {"plan_accuracy": 0.5385, "plan_jaccard": 0.6667},
    }
    payload.update(overrides)
    return payload


def report_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suite": "routing",
        "dataset_version": "v1",
        "smoke": False,
        "metrics": {"plan_accuracy": 0.5385, "plan_jaccard": 0.6667},
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("suite", ["routing", "toolcall"])
def test_committed_baselines_equal_the_committed_reports(suite: str) -> None:
    result = evaluate_gate(
        suite=suite,
        baseline_path=BASELINES / f"{suite}.json",
        report_path=REPORTS / f"{suite}-mock.json",
    )

    assert result.passed
    assert result.drop == 0.0


def test_drop_equal_to_the_limit_passes(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload())
    report = write_json(tmp_path / "report.json", report_payload(metrics={"plan_accuracy": 0.4885}))

    result = evaluate_gate(suite="routing", baseline_path=baseline, report_path=report)

    assert result.drop == 0.05
    assert result.passed


def test_drop_beyond_the_limit_fails(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload())
    report = write_json(tmp_path / "report.json", report_payload(metrics={"plan_accuracy": 0.45}))

    result = evaluate_gate(suite="routing", baseline_path=baseline, report_path=report)

    assert result.drop == 0.0885
    assert not result.passed


def test_improvement_is_a_negative_drop(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload())
    report = write_json(tmp_path / "report.json", report_payload(metrics={"plan_accuracy": 0.65}))

    result = evaluate_gate(suite="routing", baseline_path=baseline, report_path=report)

    assert result.drop == -0.1115
    assert result.passed


def test_smoke_report_is_refused(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload())
    report = write_json(tmp_path / "report.json", report_payload(smoke=True))

    with pytest.raises(ConfigError, match="smoke run"):
        evaluate_gate(suite="routing", baseline_path=baseline, report_path=report)


def test_wrong_suite_is_refused(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload(suite="toolcall"))
    report = write_json(tmp_path / "report.json", report_payload())

    with pytest.raises(ConfigError, match="not a 'routing' one"):
        evaluate_gate(suite="routing", baseline_path=baseline, report_path=report)


def test_missing_primary_metric_is_refused(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload())
    report = write_json(tmp_path / "report.json", report_payload(metrics={"jaccard": 1.0}))

    with pytest.raises(ConfigError, match="plan_accuracy"):
        evaluate_gate(suite="routing", baseline_path=baseline, report_path=report)


def test_dataset_version_mismatch_is_refused(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload())
    report = write_json(tmp_path / "report.json", report_payload(dataset_version="v2"))

    with pytest.raises(ConfigError, match="dataset version mismatch"):
        evaluate_gate(suite="routing", baseline_path=baseline, report_path=report)


def test_negative_max_drop_is_refused(tmp_path: Path) -> None:
    baseline = write_json(tmp_path / "baseline.json", baseline_payload())
    report = write_json(tmp_path / "report.json", report_payload())

    with pytest.raises(ConfigError, match="cannot be negative"):
        evaluate_gate(
            suite="routing",
            baseline_path=baseline,
            report_path=report,
            max_drop=-0.01,
        )


def test_cli_gate_uses_the_default_report_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "gate",
            "--suite",
            "routing",
            "--baseline",
            str(BASELINES / "routing.json"),
            "--max-drop",
            "0.05",
        ],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert "baseline 0.5385" in result.output
    assert "current 0.5385" in result.output
    assert "drop 0.0000, allowed 0.0500" in result.output
    assert "gate passed" in result.output


def test_cli_gate_fails_on_a_worse_report(tmp_path: Path) -> None:
    report = json.loads((REPORTS / "routing-mock.json").read_text(encoding="utf-8"))
    report["metrics"]["plan_accuracy"] = 0.45
    worse = write_json(tmp_path / "routing-worse.json", report)

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "gate",
            "--suite",
            "routing",
            "--baseline",
            str(BASELINES / "routing.json"),
            "--report",
            str(worse),
            "--max-drop",
            "0.05",
        ],
    )

    assert result.exit_code == 1
    assert "drop 0.0885, allowed 0.0500" in result.output
    assert "gate failed" in result.stderr
