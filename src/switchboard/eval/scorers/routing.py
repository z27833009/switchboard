"""Routing scores: exact set match, Jaccard and the plan confusion matrix.

Design.md section 20.1 and ADR 0011. A plan is compared as a set of agent names,
so the order the model returns does not matter.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from switchboard.core.models import EvalCase, EvalOutcome, Score


def plan_label(agents: Sequence[str] | set[str]) -> str:
    """The label used in reports and spans: ``data+docs`` or ``none``."""
    return "+".join(sorted(set(agents))) or "none"


def exact_set_match(expected: Sequence[str], actual: Sequence[str]) -> bool:
    return set(expected) == set(actual)


def jaccard(expected: Sequence[str], actual: Sequence[str]) -> float:
    """Intersection over union; two empty sets are identical, so 1.0."""
    expected_set = set(expected)
    actual_set = set(actual)
    union = expected_set | actual_set
    if not union:
        return 1.0
    return len(expected_set & actual_set) / len(union)


def score_routing(case: EvalCase, outcome: EvalOutcome) -> Score:
    match = exact_set_match(case.expected_agents, outcome.plan)
    return Score(
        case_id=case.id,
        metrics={
            "exact_match": 1.0 if match else 0.0,
            "jaccard": jaccard(case.expected_agents, outcome.plan),
        },
        passed=match,
    )


def confusion_matrix(
    pairs: Iterable[tuple[str, str]],
) -> dict[str, dict[str, int]]:
    """Count expected label to actual label transitions.

    ``pairs`` is already labelled (``plan_label``), which keeps this function
    independent of the case model and easy to test.
    """
    matrix: dict[str, dict[str, int]] = {}
    for expected, actual in pairs:
        row = matrix.setdefault(expected, {})
        row[actual] = row.get(actual, 0) + 1
    return {expected: dict(sorted(row.items())) for expected, row in sorted(matrix.items())}


__all__ = [
    "confusion_matrix",
    "exact_set_match",
    "jaccard",
    "plan_label",
    "score_routing",
]
