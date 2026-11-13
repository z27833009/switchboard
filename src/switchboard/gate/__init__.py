"""Quality gate: deterministic layer 1 and the optional judge layer 2.

Design.md section 6.8 and ADR 0013. Layer 1 is the pure ``checks`` module and
runs on every answer; layer 2 is one judge call that ``run_judge`` makes only
after layer 1 passes.
"""

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
from switchboard.gate.judge import JudgeOutcome, run_judge

__all__ = [
    "CHECK_NAMES",
    "CheckResult",
    "JudgeOutcome",
    "citations_valid",
    "evaluate_gate",
    "evidence_present",
    "merge_complete",
    "number_tokens",
    "numbers_grounded",
    "run_judge",
]
