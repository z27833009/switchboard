"""Eval scorers: set and Jaccard edge cases, required and optional tool args."""

from __future__ import annotations

import pytest

from switchboard.core.models import EvalCase, EvalOutcome, ToolSpec
from switchboard.eval.scorers.routing import (
    confusion_matrix,
    exact_set_match,
    jaccard,
    plan_label,
    score_routing,
)
from switchboard.eval.scorers.toolcall import (
    required_arguments,
    score_arguments,
    score_toolcall,
    values_equal,
)


def routing_case(case_id: str = "r001", expected: list[str] | None = None) -> EvalCase:
    return EvalCase(
        id=case_id,
        query="question",
        expected_agents=expected if expected is not None else ["data"],
    )


def toolcase() -> EvalCase:
    return EvalCase(
        id="t001",
        query="Count invoiced orders in April 2017.",
        expected_tool="orders_summary@1",
        expected_args={
            "date_from": "2017-04-01",
            "date_to": "2017-04-30",
            "status": "invoiced",
        },
    )


def summary_spec() -> ToolSpec:
    return ToolSpec(
        name="orders_summary",
        version="1",
        description="summary",
        input_schema={
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "status": {"type": "string"},
            },
            "required": ["date_from", "date_to"],
        },
    )


def test_exact_set_match_ignores_order_and_duplicates() -> None:
    assert exact_set_match(["data", "docs"], ["docs", "data"])
    assert exact_set_match(["data", "data"], ["data"])
    assert not exact_set_match(["data"], ["data", "docs"])
    assert exact_set_match([], [])


def test_jaccard_full_partial_and_empty() -> None:
    assert jaccard(["data"], ["data"]) == 1.0
    assert jaccard(["data", "docs"], ["data"]) == 0.5
    assert jaccard(["data"], ["docs"]) == 0.0
    assert jaccard([], []) == 1.0
    assert jaccard([], ["docs"]) == 0.0


def test_plan_label_sorts_and_names_the_empty_plan() -> None:
    assert plan_label(["docs", "data"]) == "data+docs"
    assert plan_label(["data", "data"]) == "data"
    assert plan_label([]) == "none"


def test_score_routing_pass_and_partial_credit() -> None:
    scored = score_routing(
        routing_case(expected=["data", "docs"]), EvalOutcome(case_id="r001", plan=["docs", "data"])
    )
    assert scored.passed is True
    assert scored.metrics == {"exact_match": 1.0, "jaccard": 1.0}

    partial = score_routing(
        routing_case(expected=["data", "docs"]), EvalOutcome(case_id="r001", plan=["data"])
    )
    assert partial.passed is False
    assert partial.metrics["exact_match"] == 0.0
    assert partial.metrics["jaccard"] == 0.5

    empty = score_routing(routing_case(expected=["data"]), EvalOutcome(case_id="r001", plan=[]))
    assert empty.metrics["jaccard"] == 0.0


def test_confusion_matrix_counts_cells() -> None:
    matrix = confusion_matrix(
        [
            ("data", "data"),
            ("data", "docs"),
            ("data+docs", "data+docs"),
            ("docs", "docs"),
        ]
    )
    assert matrix == {
        "data": {"data": 1, "docs": 1},
        "data+docs": {"data+docs": 1},
        "docs": {"docs": 1},
    }


@pytest.mark.parametrize(
    ("expected", "actual", "equal"),
    [
        (1, 1.0, True),
        (1, True, False),
        ("1", 1, False),
        (None, None, True),
        (["a", "b"], ["a", "b"], True),
        ({"a": 1}, {"a": 1.0}, True),
        ({"a": 1}, {"a": 1, "b": 2}, False),
    ],
)
def test_values_equal_keeps_json_types_honest(
    expected: object, actual: object, equal: bool
) -> None:
    assert values_equal(expected, actual) is equal


def test_required_arguments_reads_the_schema() -> None:
    assert required_arguments(summary_spec()) == {"date_from", "date_to"}


def test_arguments_required_exact_and_optional_only_when_labeled() -> None:
    required = {"date_from", "date_to"}
    expected = {"date_from": "2017-04-01", "date_to": "2017-04-30"}
    assert score_arguments(
        expected,
        {"date_from": "2017-04-01", "date_to": "2017-04-30"},
        required,
    )
    assert score_arguments(
        expected,
        {"date_from": "2017-04-01", "date_to": "2017-04-30", "status": "shipped"},
        required,
    )
    assert not score_arguments(expected, {"date_from": "2017-04-01"}, required)
    assert not score_arguments(
        expected,
        {"date_from": "2017-04-01", "date_to": "2017-04-29"},
        required,
    )
    assert not score_arguments(
        expected,
        {"date_from": "2017-04-01", "date_to": 20170430},
        required,
    )


def test_optional_argument_in_the_label_must_be_present() -> None:
    required = {"date_from", "date_to"}
    expected = {
        "date_from": "2017-04-01",
        "date_to": "2017-04-30",
        "status": "invoiced",
    }
    assert not score_arguments(
        expected, {"date_from": "2017-04-01", "date_to": "2017-04-30"}, required
    )
    assert score_arguments(
        expected,
        {
            "date_from": "2017-04-01",
            "date_to": "2017-04-30",
            "status": "invoiced",
        },
        required,
    )
    assert not score_arguments(
        expected,
        {
            "date_from": "2017-04-01",
            "date_to": "2017-04-30",
            "status": "shipped",
        },
        required,
    )


def test_score_toolcall_name_args_both_and_redundancy() -> None:
    case = toolcase()
    required = {"date_from", "date_to"}
    scored = score_toolcall(
        case,
        EvalOutcome(
            case_id="t001",
            tool_calls=["orders_summary@1"],
            tool_arguments=[case.expected_args],
        ),
        required=required,
    )
    assert scored.passed is True
    assert scored.metrics == {
        "tool_name_match": 1.0,
        "argument_match": 1.0,
        "both": 1.0,
        "redundant": 0.0,
    }

    wrong_tool = score_toolcall(
        case,
        EvalOutcome(case_id="t001", tool_calls=["orders_late@1"], tool_arguments=[{}]),
        required=required,
    )
    assert wrong_tool.metrics["tool_name_match"] == 0.0
    assert wrong_tool.metrics["argument_match"] == 0.0
    assert wrong_tool.passed is False

    redundant = score_toolcall(
        case,
        EvalOutcome(
            case_id="t001",
            tool_calls=["orders_summary@1", "orders_summary@1"],
            tool_arguments=[case.expected_args, case.expected_args],
        ),
        required=required,
    )
    assert redundant.metrics["redundant"] == 1.0
    assert redundant.metrics["both"] == 1.0

    no_calls = score_toolcall(case, EvalOutcome(case_id="t001"), required=required)
    assert no_calls.metrics["tool_name_match"] == 0.0
    assert no_calls.metrics["argument_match"] == 0.0


def test_no_call_fails_the_argument_check_even_without_expected_args() -> None:
    case = EvalCase(
        id="t037",
        query="What data can you query?",
        expected_tool="describe_schema@1",
        expected_args={},
    )
    scored = score_toolcall(case, EvalOutcome(case_id="t037"), required=set())
    assert scored.metrics["tool_name_match"] == 0.0
    assert scored.metrics["argument_match"] == 0.0
    assert scored.metrics["both"] == 0.0
