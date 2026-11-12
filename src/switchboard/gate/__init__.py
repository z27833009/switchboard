"""Deterministic quality gate (design.md section 6.8 and ADR 0013)."""

from switchboard.gate.checks import (
    CHECK_NAMES,
    CheckResult,
    citations_valid,
    evaluate_gate,
    evidence_present,
    merge_complete,
    number_tokens,
    numbers_grounded,
)

__all__ = [
    "CHECK_NAMES",
    "CheckResult",
    "citations_valid",
    "evaluate_gate",
    "evidence_present",
    "merge_complete",
    "number_tokens",
    "numbers_grounded",
]
