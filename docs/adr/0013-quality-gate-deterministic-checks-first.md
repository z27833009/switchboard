# ADR 0013: Quality gate with deterministic checks first and one corrective action

Status: proposed, 2026-11-12 (v1 feature). Replaces the LLM-scored gate of design 0.1.

## Context

Design 0.1 planned a gate that asked a model to score groundedness and completeness and re-routed weak answers along a fixed escalation path. A model judging a model without calibration produces a number nobody should trust. The research behind self-correction is consistent on one point: Reflexion (Shinn et al., 2023) works because the agent reflects on an external signal such as a failed test, while self-critique without an external signal often fails to improve answers and can make them worse (Huang et al., 2023, "Large Language Models Cannot Self-Correct Reasoning Yet").

Switchboard has an external signal available for free: every answer is supposed to cite the evidence it used (design 6.4), and the evidence is in the run.

## Decision

- **Layer 1, deterministic checks** (v1): `numbers_grounded` (every number in the answer that is not in the query occurs in evidence the agent saw), `citations_valid` (answers from evidence-based agents have citations, and every marker refers to evidence actually given), `evidence_present` (the agent saw some evidence), `merge_complete` (a merged answer names any missing source).
- **Corrective actions** are chosen by the failed check: grounding or citation failures retry the same agent once with feedback naming the problem (Reflexion-style, external signal); missing evidence triggers one re-plan with a different plan (ADR 0011); a missing merge source needs no action because the honest note is correct.
- At most one corrective action per run.
- **Layer 2, LLM judge** (v2): runs only after layer 1 passes, and only after agreement with a human-labeled sample has been measured and reported.
- The `gate` suite reports precision and recall per check.

## Alternatives

- **LLM judge only (design 0.1).** Rejected as the first layer for the calibration reason above; kept as a second layer once measured.
- **Self-refine: the agent critiques and revises its own answer.** Rejected: no external signal, an extra call on every run, and unreliable gains.
- **No gate.** Honest, but loses the strongest guarantee the platform can make ("every number is in the evidence"), which costs almost nothing to check.

## Consequences

- The gate is cheap (no model call unless a corrective action fires) and its verdicts are explainable in the trace (`switchboard.gate.failed_checks`).
- The `gate` suite labels are constructed and read through by the implementation session, not by an independent human annotator; the report states this, so the precision and recall are read as a regression measurement rather than a human agreement study.
- `numbers_grounded` will reject some correct answers with derived numbers (percentages, differences). This is measured, and the planned fix is an allowance for numbers computable from evidence numbers, not removing the check (review R12).
- Small local models that ignore citation markers will fail `citations_valid` often; the rate is reported per model (review R11).
