"""Gate suite: run the deterministic checks over labeled answer cases.

The suite makes no model call. Each case supplies an answer, the evidence the
agent saw and the expected failed checks; the suite runs the same functions the
orchestrator runs and scores per-check precision and recall. Labels are
constructed and audited, not produced by an independent human annotator; the
report says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    Citation,
    EvalCase,
    EvalOutcome,
    EvidenceRef,
    Score,
    Usage,
)
from switchboard.core.ports import EvalDeps
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.scorers.gate import expected_weakness, score_gate
from switchboard.eval.suite import SuiteContext
from switchboard.gate import CHECK_NAMES, evaluate_gate


@dataclass(frozen=True)
class GateInputs:
    query: str
    answer: AgentResult
    agents: list[str]
    evidence_agents: list[str]
    plan: list[str]
    result_sources: list[str]
    merged: bool
    labeled_weak: bool
    weakness: set[str]


def _string_list(value: Any, field: str, case_id: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"case {case_id!r} field {field!r} must be a list of strings")
    return list(value)


def prepare_case(case: EvalCase, needs: dict[str, list[str]]) -> GateInputs:
    payload = case.payload
    for field in ("query", "answer", "agents", "evidence"):
        if field not in payload:
            raise ConfigError(f"case {case.id!r} is missing field {field!r}")
    query = payload["query"]
    answer_text = payload["answer"]
    if not isinstance(query, str) or not isinstance(answer_text, str):
        raise ConfigError(f"case {case.id!r} query and answer must be strings")
    agents = _string_list(payload["agents"], "agents", case.id)
    if not agents:
        raise ConfigError(f"case {case.id!r} lists no agents")
    unknown_agents = sorted(set(agents) - set(needs))
    if unknown_agents:
        raise ConfigError(
            f"case {case.id!r} names unregistered agents: {', '.join(unknown_agents)}"
        )
    plan = _string_list(payload.get("plan", agents), "plan", case.id)
    result_sources = _string_list(payload.get("result_sources", plan), "result_sources", case.id)
    raw_evidence = payload["evidence"]
    if not isinstance(raw_evidence, list):
        raise ConfigError(f"case {case.id!r} evidence must be a list")
    try:
        evidence = [EvidenceRef.model_validate(item) for item in raw_evidence]
    except ValidationError as exc:
        raise ConfigError(f"case {case.id!r} has invalid evidence: {exc}") from exc

    citations: list[Citation] = []
    for item in evidence:
        if f"[{item.ref}]" not in answer_text:
            continue
        citations.append(
            Citation(
                kind=item.kind,
                ref=item.ref,
                source_id=item.source_id,
                snippet=item.text[:240] or item.ref,
            )
        )

    labeled_weak = payload.get("is_weak")
    if not isinstance(labeled_weak, bool):
        raise ConfigError(f"case {case.id!r} needs a boolean is_weak label")
    weakness = expected_weakness(case)

    return GateInputs(
        query=query,
        answer=AgentResult(
            answer=answer_text,
            citations=citations,
            evidence=evidence,
            usage=Usage(input_tokens=0, output_tokens=0),
        ),
        agents=agents,
        evidence_agents=[name for name in agents if needs[name]],
        plan=plan,
        result_sources=result_sources,
        merged=len(plan) > 1,
        labeled_weak=labeled_weak,
        weakness=weakness,
    )


class GateSuite:
    name = "gate"

    def __init__(
        self,
        *,
        agent_specs: list[AgentSpec],
        dataset_root: Path = DEFAULT_DATASET_ROOT,
    ) -> None:
        self._needs: dict[str, list[str]] = {
            spec.name: [str(need) for need in spec.needs] for spec in agent_specs
        }
        self._dataset_root = Path(dataset_root)

    @classmethod
    def from_context(cls, context: SuiteContext) -> GateSuite:
        if not context.agent_specs:
            raise ConfigError("the gate suite needs the registered agent specs")
        return cls(agent_specs=context.agent_specs, dataset_root=context.dataset_root)

    def load(self, version: str) -> list[EvalCase]:
        cases = load_cases(self.name, version, self._dataset_root)
        for case in cases:
            prepare_case(case, self._needs)
        return cases

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        del deps  # The gate is deterministic and never calls a model.
        inputs = prepare_case(case, self._needs)
        verdict = evaluate_gate(
            query=inputs.query,
            answer=inputs.answer,
            evidence_agents=inputs.evidence_agents,
            expected_sources=inputs.plan,
            result_sources=inputs.result_sources,
            merged=inputs.merged,
        )
        return EvalOutcome(
            case_id=case.id,
            answer=inputs.answer.answer,
            details={
                "failed_checks": list(verdict.failed_checks),
                "expected_failed_checks": sorted(inputs.weakness),
                "action": verdict.action,
                "reason": verdict.reason,
                "feedback": verdict.feedback,
                "labels_are_constructed": True,
            },
            usage=Usage(input_tokens=0, output_tokens=0),
            latency_ms=0,
        )

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        unknown = set(outcome.details.get("failed_checks", [])) - set(CHECK_NAMES)
        if unknown:
            raise ConfigError(
                f"case {case.id!r} produced unknown check results: {', '.join(sorted(unknown))}"
            )
        return score_gate(case, outcome)


__all__ = ["GateInputs", "GateSuite", "prepare_case"]
