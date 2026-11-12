"""Gate scores: per-check confusion counts and overall weak detection."""

from __future__ import annotations

from switchboard.core.errors import ConfigError
from switchboard.core.models import EvalCase, EvalOutcome, Score
from switchboard.gate import CHECK_NAMES


def expected_weakness(case: EvalCase) -> set[str]:
    raw = case.payload.get("weakness", [])
    if not isinstance(raw, list) or any(not isinstance(name, str) for name in raw):
        raise ConfigError(f"case {case.id!r} has a weakness that is not a list of check names")
    unknown = sorted(set(raw) - set(CHECK_NAMES))
    if unknown:
        raise ConfigError(
            f"case {case.id!r} names unknown checks: {', '.join(unknown)}; "
            f"known checks: {', '.join(CHECK_NAMES)}"
        )
    return set(raw)


def score_gate(case: EvalCase, outcome: EvalOutcome) -> Score:
    expected = expected_weakness(case)
    predicted = set(outcome.details.get("failed_checks", []))
    metrics: dict[str, float] = {}
    for name in CHECK_NAMES:
        hit = name in predicted
        label = name in expected
        metrics[f"{name}_tp"] = 1.0 if hit and label else 0.0
        metrics[f"{name}_fp"] = 1.0 if hit and not label else 0.0
        metrics[f"{name}_fn"] = 1.0 if label and not hit else 0.0
        metrics[f"{name}_tn"] = 1.0 if not hit and not label else 0.0

    labeled_weak = case.payload.get("is_weak")
    if not isinstance(labeled_weak, bool):
        raise ConfigError(f"case {case.id!r} has no boolean is_weak label")
    predicted_weak = bool(predicted)
    correct = predicted_weak == labeled_weak
    metrics["weak_correct"] = 1.0 if correct else 0.0
    metrics["weak_predicted"] = 1.0 if predicted_weak else 0.0
    metrics["weak_labeled"] = 1.0 if labeled_weak else 0.0
    return Score(case_id=case.id, metrics=metrics, passed=correct)


__all__ = ["expected_weakness", "score_gate"]
