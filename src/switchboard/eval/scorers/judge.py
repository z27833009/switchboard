"""Judge agreement math: does the judge agree with a human label?

Design.md sections 6.8 and 20.2. The numbers are pure functions over pairs of
``(human_label, judge_label)``, so the same function scores the agreement run
and an eval case. Labels are ``acceptable`` or ``weak``.

Raw agreement alone is easy to over-read on a small sample, so the summary also
carries Cohen's kappa (agreement corrected for chance) and the two directional
counts that matter for a gate: false rejections (a human said acceptable, the
judge said weak) and missed weak answers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from switchboard.core.errors import ConfigError

JudgeLabel = Literal["acceptable", "weak"]
JUDGE_LABELS: tuple[JudgeLabel, ...] = ("acceptable", "weak")


def _check_label(label: str, *, field: str) -> JudgeLabel:
    if label not in JUDGE_LABELS:
        raise ConfigError(f"{field} must be 'acceptable' or 'weak', got {label!r}")
    return label


def confusion_matrix(pairs: Sequence[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Rows are the human labels, columns the judge labels."""
    matrix: dict[str, dict[str, int]] = {
        human: dict.fromkeys(JUDGE_LABELS, 0) for human in JUDGE_LABELS
    }
    for human, judged in pairs:
        human_label = _check_label(human, field="human label")
        judge_label = _check_label(judged, field="judge label")
        matrix[human_label][judge_label] += 1
    return matrix


def raw_agreement(pairs: Sequence[tuple[str, str]]) -> float:
    """Share of pairs where the judge label equals the human label."""
    if not pairs:
        return 0.0
    return round(sum(1 for human, judged in pairs if human == judged) / len(pairs), 4)


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float:
    """Cohen's kappa for two raters and two labels; 0.0 when chance is certain."""
    if not pairs:
        return 0.0
    total = len(pairs)
    matrix = confusion_matrix(pairs)
    observed = sum(matrix[label][label] for label in JUDGE_LABELS) / total
    expected = sum(
        (sum(matrix[label].values()) / total) * (sum(row[label] for row in matrix.values()) / total)
        for label in JUDGE_LABELS
    )
    if expected >= 1.0:
        return 0.0
    return round((observed - expected) / (1.0 - expected), 4)


def agreement_summary(pairs: Sequence[tuple[str, str]]) -> dict[str, float]:
    """The headline numbers, safe to place straight in a report."""
    matrix = confusion_matrix(pairs)
    compared = len(pairs)
    return {
        "cases": float(compared),
        "agreements": float(sum(matrix[label][label] for label in JUDGE_LABELS)),
        "raw_agreement": raw_agreement(pairs),
        "cohen_kappa": cohen_kappa(pairs),
        "false_rejections": float(matrix["acceptable"]["weak"]),
        "missed_weak": float(matrix["weak"]["acceptable"]),
        "judge_accept_rate": round(
            sum(1 for _human, judged in pairs if judged == "acceptable") / compared, 4
        )
        if compared
        else 0.0,
    }


def judge_metric(verdict: str | None) -> float | None:
    """The per-case metric for a rag outcome: 1.0 acceptable, 0.0 weak, None."""
    if verdict == "acceptable":
        return 1.0
    if verdict == "weak":
        return 0.0
    return None


__all__ = [
    "JUDGE_LABELS",
    "JudgeLabel",
    "agreement_summary",
    "cohen_kappa",
    "confusion_matrix",
    "judge_metric",
    "raw_agreement",
]
