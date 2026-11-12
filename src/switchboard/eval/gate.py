"""Regression gate over committed eval baselines (design.md section 20.3).

A baseline is a small JSON file holding the metrics of a reviewed mock report,
plus the provenance needed to say which run produced them. The gate compares the
primary metric of a suite against the baseline and fails when the value drops by
more than the allowed delta. Baselines are updated by committing a new file,
never by this code (ADR 0008).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from switchboard.core.errors import ConfigError

PRIMARY_METRICS: dict[str, str] = {
    "routing": "plan_accuracy",
    "toolcall": "both_accuracy",
}

DEFAULT_REPORTS_DIR = Path("evals/reports")
DEFAULT_MAX_DROP = 0.05


@dataclass(frozen=True)
class GateResult:
    suite: str
    metric: str
    baseline: float
    current: float
    max_drop: float

    @property
    def drop(self) -> float:
        """How much the current value falls below the baseline (negative is better)."""
        return round(self.baseline - self.current, 4)

    @property
    def passed(self) -> bool:
        return self.drop <= self.max_drop


def primary_metric(suite: str) -> str:
    try:
        return PRIMARY_METRICS[suite]
    except KeyError as exc:
        available = ", ".join(sorted(PRIMARY_METRICS))
        raise ConfigError(f"no regression metric for suite {suite!r}; known: {available}") from exc


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return payload


def _metric_value(path: Path, payload: dict[str, Any], suite: str, metric: str) -> float:
    declared = payload.get("suite")
    if declared is not None and declared != suite:
        raise ConfigError(f"{path} is a {declared!r} file, not a {suite!r} one")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise ConfigError(f"{path} has no metrics object")
    value = metrics.get(metric)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{path} has no numeric {metric!r} in its metrics")
    return float(value)


def evaluate_gate(
    *,
    suite: str,
    baseline_path: Path,
    report_path: Path,
    max_drop: float = DEFAULT_MAX_DROP,
) -> GateResult:
    if max_drop < 0:
        raise ConfigError("--max-drop cannot be negative")
    metric = primary_metric(suite)

    baseline_payload = read_json_object(Path(baseline_path))
    report_payload = read_json_object(Path(report_path))
    if baseline_payload.get("smoke") is True:
        raise ConfigError(f"{baseline_path} is a smoke run and cannot be a baseline")
    if report_payload.get("smoke") is True:
        raise ConfigError(f"{report_path} is a smoke run; the gate needs a full-suite report")

    baseline_version = baseline_payload.get("dataset_version")
    report_version = report_payload.get("dataset_version")
    if (
        isinstance(baseline_version, str)
        and isinstance(report_version, str)
        and baseline_version != report_version
    ):
        raise ConfigError(
            f"dataset version mismatch: baseline {baseline_version!r}, "
            f"report {report_version!r}; commit a new baseline for the new version"
        )

    return GateResult(
        suite=suite,
        metric=metric,
        baseline=_metric_value(Path(baseline_path), baseline_payload, suite, metric),
        current=_metric_value(Path(report_path), report_payload, suite, metric),
        max_drop=max_drop,
    )


def default_report_path(suite: str, output_dir: Path = DEFAULT_REPORTS_DIR) -> Path:
    return Path(output_dir) / f"{suite}-mock.json"


__all__ = [
    "DEFAULT_MAX_DROP",
    "DEFAULT_REPORTS_DIR",
    "PRIMARY_METRICS",
    "GateResult",
    "default_report_path",
    "evaluate_gate",
    "primary_metric",
    "read_json_object",
]
