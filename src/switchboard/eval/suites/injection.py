"""Injection suite: run the deterministic scanner over labeled content.

The suite makes no model call. Each case is one piece of untrusted content,
either malicious or benign, and the suite runs the production ``scan`` function
from ``switchboard.core.guardrails``. The report measures detection rate, false
positive rate and per-pattern precision and recall against constructed labels.
"""

from __future__ import annotations

from pathlib import Path

from switchboard.core.errors import ConfigError
from switchboard.core.guardrails import PATTERN_SET_VERSION, scan
from switchboard.core.models import EvalCase, EvalOutcome, Score, Usage
from switchboard.core.ports import EvalDeps
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.scorers.injection import case_label, score_injection
from switchboard.eval.suite import SuiteContext


class InjectionSuite:
    name = "injection"

    def __init__(self, *, dataset_root: Path = DEFAULT_DATASET_ROOT) -> None:
        self._dataset_root = Path(dataset_root)

    @classmethod
    def from_context(cls, context: SuiteContext) -> InjectionSuite:
        return cls(dataset_root=context.dataset_root)

    def load(self, version: str) -> list[EvalCase]:
        cases = load_cases(self.name, version, self._dataset_root)
        for case in cases:
            content = case.payload.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ConfigError(f"case {case.id!r} needs non-empty string content")
            case_label(case)
        return cases

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        del deps  # The scanner is deterministic and never calls a model.
        content = str(case.payload["content"])
        verdict = scan(content)
        return EvalOutcome(
            case_id=case.id,
            details={
                "flagged": verdict.flagged,
                "patterns": list(verdict.pattern_ids),
                "pattern_set_version": PATTERN_SET_VERSION,
            },
            usage=Usage(input_tokens=0, output_tokens=0),
            latency_ms=0,
        )

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        return score_injection(case, outcome)


__all__ = ["InjectionSuite"]
