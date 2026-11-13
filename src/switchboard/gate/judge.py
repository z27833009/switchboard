"""Layer 2 of the quality gate: one judge call after layer 1 passes.

Design.md section 6.8. Both orchestrators use this helper so the two adapters
cannot drift: the judge runs only after the deterministic checks pass, its
verdict is turned into a gate verdict with one rule, and an error never fails
the run.

Rules:

* layer 1 must pass; a failed layer 1 keeps its own corrective action and the
  judge does not see that answer;
* an answer that asks the user a clarification question is not judged, because
  there is no answer to assess yet (design.md section 6.4);
* an ``acceptable`` verdict keeps the layer 1 verdict and only adds the judge's
  usage and cost to the run;
* a ``weak`` verdict records the failed check ``judge`` with action ``none``, so
  no extra retry loop starts and the original answer plus the quality note is
  the output;
* a missing, malformed or refused judge reply adds an ``ErrorRecord`` and
  appends the error to the gate reason, and the layer 1 result stands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from switchboard.core.models import (
    AgentResult,
    ErrorRecord,
    GateVerdict,
    JudgeEvidence,
    JudgeRequest,
    Usage,
)
from switchboard.core.ports import Judge

JudgeOutcomeName = Literal["skipped", "passed", "rejected", "error"]


@dataclass(frozen=True)
class JudgeOutcome:
    """What layer 2 did: the verdict to act on, its trace marker and its cost."""

    verdict: GateVerdict
    outcome: JudgeOutcomeName = "skipped"
    score: float | None = None
    reason: str = ""
    usage: Usage | None = None
    cost_usd: float | None = None
    errors: list[ErrorRecord] = field(default_factory=list)


def _evidence(answer: AgentResult) -> list[JudgeEvidence]:
    return [JudgeEvidence(kind=item.kind, ref=item.ref, text=item.text) for item in answer.evidence]


async def run_judge(
    judge: Judge | None,
    *,
    query: str,
    answer: AgentResult | None,
    verdict: GateVerdict,
) -> JudgeOutcome:
    """Judge one answer that passed layer 1, or explain why it was skipped."""
    if judge is None or answer is None or not verdict.passed:
        return JudgeOutcome(verdict=verdict)
    if answer.needs_clarification:
        return JudgeOutcome(verdict=verdict)
    request = JudgeRequest(
        query=query,
        answer=answer.answer,
        evidence=_evidence(answer),
        citations=list(answer.citations),
    )
    try:
        judged = await judge.judge(request)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        updated = verdict.model_copy(
            update={"reason": f"{verdict.reason}; judge error, layer 1 result kept: {message}"}
        )
        return JudgeOutcome(
            verdict=updated,
            outcome="error",
            reason=message,
            errors=[
                ErrorRecord(
                    kind=type(exc).__name__,
                    message=message,
                    retryable=getattr(exc, "retryable", False),
                    stage="gate.judge",
                )
            ],
        )
    if judged.verdict == "acceptable":
        return JudgeOutcome(
            verdict=verdict,
            outcome="passed",
            score=judged.score,
            reason=judged.reason,
            usage=judged.usage,
            cost_usd=judged.cost_usd,
        )
    reason = "the answer judge marked this answer weak: " + (judged.reason or "no reason was given")
    return JudgeOutcome(
        verdict=GateVerdict(
            passed=False,
            failed_checks=["judge"],
            action="none",
            reason=reason,
            feedback=reason,
        ),
        outcome="rejected",
        score=judged.score,
        reason=judged.reason,
        usage=judged.usage,
        cost_usd=judged.cost_usd,
    )


__all__ = ["JudgeOutcome", "JudgeOutcomeName", "run_judge"]
