"""Tool-call scores: tool name match and argument match.

Design.md section 20.1: required arguments must be exact, optional arguments are
compared only when the label has them. Extra arguments the model invents are
ignored for the argument score and show up in the per-case JSON instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from switchboard.core.models import EvalCase, EvalOutcome, Score, ToolSpec


def values_equal(expected: Any, actual: Any) -> bool:
    """JSON value equality, with bools kept separate from numbers.

    Python treats ``True == 1``; a tool argument of the wrong JSON type is a
    miss, so booleans compare only with booleans. Integer and float forms of the
    same number are equal because JSON does not distinguish them.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return float(expected) == float(actual)
    if isinstance(expected, str) or isinstance(actual, str):
        return isinstance(expected, str) and isinstance(actual, str) and expected == actual
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            values_equal(left, right) for left, right in zip(expected, actual, strict=True)
        )
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(
            values_equal(value, actual[key]) for key, value in expected.items()
        )
    return expected == actual and type(expected) is type(actual)


def required_arguments(spec: ToolSpec) -> set[str]:
    required = spec.input_schema.get("required", [])
    if not isinstance(required, list):
        return set()
    return {str(name) for name in required}


def score_arguments(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    required: set[str],
) -> bool:
    for name in required:
        if name not in actual:
            return False
    for name, value in expected.items():
        if name not in actual:
            return False
        if not values_equal(value, actual[name]):
            return False
    return True


def score_toolcall(
    case: EvalCase,
    outcome: EvalOutcome,
    *,
    required: set[str],
) -> Score:
    first_name = outcome.tool_calls[0] if outcome.tool_calls else None
    first_arguments = outcome.tool_arguments[0] if outcome.tool_arguments else {}
    name_match = first_name == case.expected_tool
    # A missing call cannot have matching arguments, even when the label has no
    # expected arguments (describe_schema).
    arguments_match = bool(outcome.tool_calls) and score_arguments(
        case.expected_args, first_arguments, required
    )
    both = name_match and arguments_match
    return Score(
        case_id=case.id,
        metrics={
            "tool_name_match": 1.0 if name_match else 0.0,
            "argument_match": 1.0 if arguments_match else 0.0,
            "both": 1.0 if both else 0.0,
            "redundant": 1.0 if len(outcome.tool_calls) > 1 else 0.0,
        },
        passed=both,
    )


__all__ = [
    "required_arguments",
    "score_arguments",
    "score_toolcall",
    "values_equal",
]
