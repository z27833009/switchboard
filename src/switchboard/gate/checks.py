"""Deterministic gate checks over an answer, its citations and its evidence.

Design.md section 6.8 and ADR 0013: the first gate layer is pure and cheap, so
it can run on every answer without a model call. The functions here take plain
domain models and return ``CheckResult`` values; ``evaluate_gate`` combines them
into the ``GateVerdict`` the orchestrator acts on.

Number normalization covers thousands separators, a decimal point, a percent
sign or word, the words thousand, million and billion, the attached multipliers
k, M and B, and an ordinal suffix. It deliberately does not compute derived
numbers: a correct derived percentage that is not written in the evidence is
reported as unsupported, and the gate eval suite measures that false positive
(ADR 0013, review R12).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from switchboard.core.models import AgentResult, Citation, EvidenceRef, GateVerdict

CheckName = Literal[
    "numbers_grounded",
    "citations_valid",
    "evidence_present",
    "merge_complete",
]

CHECK_NAMES: tuple[CheckName, ...] = (
    "numbers_grounded",
    "citations_valid",
    "evidence_present",
    "merge_complete",
)

# The marker syntax from design.md section 6.4: [d1] for documents, [t1] for
# tool results.
MARKER = re.compile(r"\[([dt]\d+)\]")

_SEPARATORS = str.maketrans("", "", ",_\u00a0 ")

_MULTIPLIERS: dict[str, int] = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
}

_NUMBER_TOKEN = re.compile(
    r"(?<![\w.])"
    r"(?P<sign>[+-]?)"
    r"(?P<digits>(?:\d{1,3}(?:[,_\u00a0 ]\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<suffix>\s?%|\s?(?:percent|thousand|million|billion|bn)\b|[kKmMbB])?"
    r"(?P<ordinal>st|nd|rd|th)?"
    r"(?![\w])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CheckResult:
    name: CheckName
    passed: bool
    feedback: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _canonical_number(match: re.Match[str]) -> str:
    digits = match.group("digits").translate(_SEPARATORS)
    try:
        value = Decimal(digits)
    except InvalidOperation:  # pragma: no cover - the regex only admits digits
        return digits
    suffix = (match.group("suffix") or "").strip().lower()
    unit = ""
    if suffix in {"%", "percent"}:
        unit = "%"
    elif suffix in _MULTIPLIERS:
        value *= _MULTIPLIERS[suffix]
    if match.group("sign") == "-":
        value = -value
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text + unit


def number_tokens(text: str) -> list[tuple[str, str]]:
    """Return ``(canonical, as written)`` for every number in ``text``.

    Duplicates are kept in first-seen order; callers that compare sets dedupe.
    """
    tokens: list[tuple[str, str]] = []
    for match in _NUMBER_TOKEN.finditer(text):
        tokens.append((_canonical_number(match), match.group(0).strip()))
    return tokens


def numbers_grounded(
    answer: str,
    query: str,
    evidence: Sequence[EvidenceRef],
) -> CheckResult:
    """Every answer number not in the query must occur in the evidence."""
    query_numbers = {canonical for canonical, _ in number_tokens(query)}
    evidence_numbers = {canonical for item in evidence for canonical, _ in number_tokens(item.text)}

    checked: list[str] = []
    unsupported: list[str] = []
    seen: set[str] = set()
    for canonical, written in number_tokens(answer):
        if canonical in query_numbers or canonical in seen:
            continue
        seen.add(canonical)
        checked.append(written)
        if canonical not in evidence_numbers:
            unsupported.append(written)

    if unsupported:
        listed = ", ".join(unsupported)
        return CheckResult(
            name="numbers_grounded",
            passed=False,
            feedback=(
                f"These numbers are not in the evidence the agent saw: {listed}. "
                "Use only numbers from the tool results or documents, or remove them."
            ),
            details={
                "unsupported_numbers": unsupported,
                "checked_numbers": checked,
            },
        )
    return CheckResult(
        name="numbers_grounded",
        passed=True,
        details={"checked_numbers": checked},
    )


def citations_valid(
    answer: str,
    citations: Sequence[Citation],
    evidence: Sequence[EvidenceRef],
    *,
    required: bool,
) -> CheckResult:
    """Every marker must refer to given evidence, and a sourced answer cites one.

    When no evidence was given at all, this check passes: ``evidence_present``
    already reports the failure, and a corrective retry for citations would be
    the wrong action.
    """
    if not required or not evidence:
        return CheckResult(
            name="citations_valid",
            passed=True,
            details={"required": required, "evidence_count": len(evidence)},
        )

    refs = {item.ref for item in evidence}
    markers = list(dict.fromkeys(MARKER.findall(answer)))
    cited_refs = {citation.ref for citation in citations}
    unknown = sorted({ref for ref in [*markers, *cited_refs] if ref not in refs})

    if unknown:
        available = ", ".join(sorted(refs))
        listed = ", ".join(f"[{ref}]" for ref in unknown)
        return CheckResult(
            name="citations_valid",
            passed=False,
            feedback=(
                f"The answer cites {listed}, which was not evidence the agent saw. "
                f"Use only these refs: {available}."
            ),
            details={"unknown_refs": unknown, "markers": markers},
        )
    if not markers:
        return CheckResult(
            name="citations_valid",
            passed=False,
            feedback=(
                "The answer from an evidence-based agent has no citation marker. "
                "Cite every fact as [dN] or [tN] using the evidence it saw."
            ),
            details={"unknown_refs": [], "markers": []},
        )
    return CheckResult(
        name="citations_valid",
        passed=True,
        details={"unknown_refs": [], "markers": markers},
    )


def evidence_present(
    evidence: Sequence[EvidenceRef],
    *,
    required: bool,
) -> CheckResult:
    """Fail when an evidence-based agent saw no usable evidence at all."""
    if not required:
        return CheckResult(
            name="evidence_present",
            passed=True,
            details={"required": False, "usable": 0},
        )

    usable = [item for item in evidence if not item.empty and item.text.strip()]
    if usable:
        return CheckResult(
            name="evidence_present",
            passed=True,
            details={
                "required": True,
                "usable": len(usable),
                "empty": sum(1 for item in evidence if item.empty),
            },
        )
    return CheckResult(
        name="evidence_present",
        passed=False,
        feedback=(
            "The agent saw no usable evidence: the retrieval or tool result was empty, "
            "or every tool was denied. Plan a different source for this question."
        ),
        details={"required": True, "usable": 0, "empty": len(evidence)},
    )


def merge_complete(
    *,
    merged: bool,
    expected_sources: Sequence[str],
    result_sources: Sequence[str],
    answer: str,
) -> CheckResult:
    """Fail when a merged answer is missing one of its planned sources."""
    if not merged:
        return CheckResult(
            name="merge_complete",
            passed=True,
            details={"merged": False},
        )

    missing = sorted(set(expected_sources) - set(result_sources))
    if not missing:
        return CheckResult(
            name="merge_complete",
            passed=True,
            details={"merged": True, "missing_sources": []},
        )

    note = "the answer does not name it as missing"
    if "missing source" in answer.lower():
        note = "the answer names it as missing, which is the honest partial output"
    listed = ", ".join(missing)
    return CheckResult(
        name="merge_complete",
        passed=False,
        feedback=f"The merged answer is missing the {listed} source; {note}.",
        details={"merged": True, "missing_sources": missing},
    )


def evaluate_gate(
    *,
    query: str,
    answer: AgentResult,
    evidence_agents: Collection[str],
    expected_sources: Sequence[str] = (),
    result_sources: Sequence[str] = (),
    merged: bool = False,
) -> GateVerdict:
    """Run the deterministic checks and recommend one corrective action.

    ``evidence_agents`` holds the agents in the plan whose specs need tools or a
    retriever, so a direct answer is not asked for citations it cannot have.
    """
    required = bool(evidence_agents)
    checks: list[CheckResult] = []
    if required:
        checks.append(numbers_grounded(answer.answer, query, answer.evidence))
        checks.append(
            citations_valid(
                answer.answer,
                answer.citations,
                answer.evidence,
                required=True,
            )
        )
        checks.append(evidence_present(answer.evidence, required=True))
    checks.append(
        merge_complete(
            merged=merged,
            expected_sources=expected_sources,
            result_sources=result_sources,
            answer=answer.answer,
        )
    )

    failed = [check for check in checks if not check.passed]
    if not failed:
        return GateVerdict(
            passed=True,
            failed_checks=[],
            action="pass",
            reason="all deterministic checks passed",
        )

    failed_names: list[str] = [check.name for check in failed]
    feedback = " ".join(check.feedback for check in failed if check.feedback).strip()
    action: Literal["retry", "replan", "none"]
    if "evidence_present" in failed_names:
        action = "replan"
        reason = "evidence_present failed: no usable evidence reached the agent"
    elif "numbers_grounded" in failed_names or "citations_valid" in failed_names:
        action = "retry"
        reason = "a grounding or citation check failed: " + ", ".join(failed_names)
    else:
        action = "none"
        reason = "merge_complete failed: the honest partial answer is the output"
    return GateVerdict(
        passed=False,
        failed_checks=failed_names,
        action=action,
        reason=reason,
        feedback=feedback,
    )


__all__ = [
    "CHECK_NAMES",
    "MARKER",
    "CheckName",
    "CheckResult",
    "citations_valid",
    "evaluate_gate",
    "evidence_present",
    "merge_complete",
    "number_tokens",
    "numbers_grounded",
]
