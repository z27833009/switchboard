"""Deterministic gate checks: pure functions, no I/O (design.md 6.8)."""

from __future__ import annotations

from switchboard.core.models import AgentResult, Citation, EvidenceRef, Usage
from switchboard.gate import (
    citations_valid,
    evaluate_gate,
    evidence_present,
    merge_complete,
    number_tokens,
    numbers_grounded,
)


def doc(ref: str = "d1", text: str = "Electronics can be returned within 15 days.") -> EvidenceRef:
    return EvidenceRef(kind="document", ref=ref, source_id=f"chunk-{ref}", text=text)


def tool(ref: str = "t1", text: str = '{"total_rows": 1234, "late_orders": 1234}') -> EvidenceRef:
    return EvidenceRef(kind="tool", ref=ref, source_id=f"result-{ref}", text=text)


def answer(
    text: str,
    *,
    citations: list[Citation] | None = None,
    evidence: list[EvidenceRef] | None = None,
) -> AgentResult:
    return AgentResult(
        answer=text,
        citations=citations or [],
        evidence=evidence or [],
        usage=Usage(input_tokens=0, output_tokens=0),
    )


def citation(ref: str = "d1") -> Citation:
    return Citation(kind="document", ref=ref, source_id=f"chunk-{ref}", snippet="snippet")


def test_number_tokens_normalize_separators_decimals_and_units() -> None:
    assert number_tokens("1,234.50 orders") == [("1234.5", "1,234.50")]
    assert number_tokens("1 000 items") == [("1000", "1 000")]
    assert number_tokens("3.5 % of orders") == [("3.5%", "3.5 %")]
    assert number_tokens("3.5 percent") == [("3.5%", "3.5 percent")]
    assert number_tokens("1.5 million") == [("1500000", "1.5 million")]
    assert number_tokens("2.5M") == [("2500000", "2.5M")]
    assert number_tokens("3rd place") == [("3", "3rd")]
    assert number_tokens("-5 late") == [("-5", "-5")]


def test_number_tokens_ignore_hex_order_ids_and_attached_units() -> None:
    assert number_tokens("044bcd01474ba715e7cb11d5a70ccf0e") == []
    assert number_tokens("5kg") == []
    assert number_tokens("2018-03-01") == [("2018", "2018"), ("3", "03"), ("1", "01")]


def test_numbers_grounded_accepts_normalized_evidence_numbers() -> None:
    result = numbers_grounded("There were 1,234 late orders.", "How many?", [tool()])
    assert result.passed is True
    assert result.details["checked_numbers"] == ["1,234"]


def test_numbers_grounded_reports_unsupported_numbers() -> None:
    result = numbers_grounded("There were 1,500 late orders.", "How many?", [tool()])
    assert result.passed is False
    assert result.details["unsupported_numbers"] == ["1,500"]
    assert "1,500" in result.feedback


def test_numbers_grounded_excludes_numbers_that_appear_in_the_query() -> None:
    result = numbers_grounded(
        "The 30 day window does not apply; the window is 15 days.",
        "Does the 30 day window apply?",
        [doc()],
    )
    assert result.passed is True
    assert result.details["checked_numbers"] == ["15"]


def test_numbers_grounded_keeps_units_distinct() -> None:
    result = numbers_grounded("The share was 3.5%.", "Share?", [tool(text="late_rate: 3.5")])
    assert result.passed is False
    assert result.details["unsupported_numbers"] == ["3.5%"]


def test_numbers_grounded_matches_a_scale_word_to_plain_digits() -> None:
    result = numbers_grounded(
        "Sellers earned 1.5 million.",
        "Revenue?",
        [tool(text="gross_revenue: 1500000")],
    )
    assert result.passed is True


def test_citations_valid_requires_a_marker_for_an_evidence_answer() -> None:
    result = citations_valid("The window is 15 days.", [], [doc()], required=True)
    assert result.passed is False
    assert "no citation marker" in result.feedback


def test_citations_valid_rejects_a_marker_the_agent_never_saw() -> None:
    result = citations_valid("The window is 15 days [d9].", [], [doc()], required=True)
    assert result.passed is False
    assert "d9" in result.feedback


def test_citations_valid_accepts_a_known_marker() -> None:
    result = citations_valid("The window is 15 days [d1].", [citation()], [doc()], required=True)
    assert result.passed is True
    assert result.details["markers"] == ["d1"]


def test_citations_valid_does_not_double_count_missing_evidence() -> None:
    result = citations_valid("No documents were found.", [], [], required=True)
    assert result.passed is True


def test_citations_valid_is_skipped_for_a_direct_answer() -> None:
    result = citations_valid("I can answer questions about orders.", [], [], required=False)
    assert result.passed is True


def test_evidence_present_accepts_a_document_with_text() -> None:
    result = evidence_present([doc()], required=True)
    assert result.passed is True
    assert result.details["usable"] == 1


def test_evidence_present_rejects_no_evidence() -> None:
    result = evidence_present([], required=True)
    assert result.passed is False
    assert "no usable evidence" in result.feedback


def test_evidence_present_rejects_an_empty_tool_result() -> None:
    empty = EvidenceRef(
        kind="tool",
        ref="t1",
        source_id="result-empty",
        text='{"total_rows": 0, "rows_preview": []}',
        empty=True,
    )
    result = evidence_present([empty], required=True)
    assert result.passed is False
    assert result.details["empty"] == 1


def test_evidence_present_is_skipped_for_a_direct_answer() -> None:
    assert evidence_present([], required=False).passed is True


def test_merge_complete_passes_when_every_source_produced_a_result() -> None:
    result = merge_complete(
        merged=True,
        expected_sources=["docs", "data"],
        result_sources=["data", "docs"],
        answer="docs: ...\n\ndata: ...",
    )
    assert result.passed is True


def test_merge_complete_reports_a_missing_source() -> None:
    result = merge_complete(
        merged=True,
        expected_sources=["docs", "data"],
        result_sources=["docs"],
        answer="docs: ...",
    )
    assert result.passed is False
    assert result.details["missing_sources"] == ["data"]
    assert "does not name it" in result.feedback


def test_merge_complete_still_fails_when_the_answer_names_the_missing_source() -> None:
    result = merge_complete(
        merged=True,
        expected_sources=["docs", "data"],
        result_sources=["docs"],
        answer="docs: ...\n\nMissing source: data.",
    )
    assert result.passed is False
    assert "honest partial output" in result.feedback


def test_merge_complete_does_not_apply_to_a_single_source() -> None:
    result = merge_complete(
        merged=False,
        expected_sources=["docs"],
        result_sources=[],
        answer="...",
    )
    assert result.passed is True


def test_evaluate_gate_passes_a_direct_answer() -> None:
    verdict = evaluate_gate(
        query="What can you do?",
        answer=answer("I can answer questions about orders."),
        evidence_agents=[],
        expected_sources=["direct"],
        result_sources=["direct"],
        merged=False,
    )
    assert verdict.passed is True
    assert verdict.action == "pass"


def test_evaluate_gate_recommends_a_retry_for_an_ungrounded_number() -> None:
    verdict = evaluate_gate(
        query="How many were late?",
        answer=answer(
            "There were 1,500 late orders [t1].",
            citations=[Citation(kind="tool", ref="t1", source_id="result-t1", snippet="x")],
            evidence=[tool()],
        ),
        evidence_agents=["data"],
    )
    assert verdict.passed is False
    assert verdict.failed_checks == ["numbers_grounded"]
    assert verdict.action == "retry"
    assert "1,500" in (verdict.feedback or "")


def test_evaluate_gate_recommends_a_replan_when_evidence_is_missing() -> None:
    verdict = evaluate_gate(
        query="What is the policy?",
        answer=answer("I could not find a document."),
        evidence_agents=["docs"],
    )
    assert verdict.passed is False
    assert verdict.failed_checks == ["evidence_present"]
    assert verdict.action == "replan"


def test_evaluate_gate_recommends_no_action_for_an_incomplete_merge() -> None:
    verdict = evaluate_gate(
        query="Policy and count?",
        answer=answer(
            "docs: The window is 15 days [d1].",
            citations=[citation()],
            evidence=[doc()],
        ),
        evidence_agents=["docs", "data"],
        expected_sources=["docs", "data"],
        result_sources=["docs"],
        merged=True,
    )
    assert verdict.passed is False
    assert verdict.failed_checks == ["merge_complete"]
    assert verdict.action == "none"
