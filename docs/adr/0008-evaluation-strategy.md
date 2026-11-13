# ADR 0008: Evaluation strategy: versioned datasets, mock provider in CI, honest reporting

Status: accepted, 2026-11-13. Proposed 2026-11-11, amended 2026-11-12 (fixture keys and loud misses, what the CI gate measures, plan labels, feedback harvest, orchestrator as a report dimension). The mock CI gate, datasets v1 to v4, committed reports and the comparison report ship.

## Context

The project's value depends on numbers that can be trusted: plan accuracy, tool-call accuracy, retrieval recall, cache false-hit rate, latency and cost per provider and per orchestrator. CI cannot call paid APIs or a developer's local model, and the author writes both the prompts and the datasets.

## Decision

- Datasets are JSONL files under `evals/datasets/<version>/`. A version is frozen once a report references it; fixes go into a new version.
- Each suite implements the `EvalSuite` port (load, execute, score) and registers through the same mechanism as other adapters.
- The routing suite is labeled with expected agent sets and scored by exact set match, with Jaccard as partial credit (ADR 0011).
- The runner writes a markdown report and a JSON report with dataset version, orchestrator, provider, model, case count, metrics, p50 and p95 latency, input, cached input and output tokens, and cost. Reports that back README numbers are committed.
- CI runs the suites with a `MockProvider` replaying recorded fixtures and compares against committed baselines with a maximum allowed drop.
- **Fixture keys.** A fixture is keyed by a hash of purpose, model, normalized messages, tool specs and response schema. A request without a matching fixture fails with a message to re-record; it never falls back to a default answer.
- **What the CI gate measures.** Replayed model output means the gate measures the pipeline (parsing, validation, normalization, scoring), not the model. A prompt change alters fixture keys and so forces a re-record against a real model, which produces a new report. This mechanically enforces the rule that a prompt change needs a new report. The README says this next to the CI badge.
- Paid providers require an explicit confirmation flag and respect a cost cap.
- Datasets for a suite are finished before the prompts for that suite. Routing questions are written by a separate session from a brief and labeled blind by the author; where the two labels disagree the case is tagged `ambiguous` and reported separately, with the agreement rate. Toolcall cases come from templates with arguments computed by code. Confusion matrices are included for routing.
- Every case has a deterministic dev or test split. Prompt work may read dev cases only; reports score the test split.
- **Feedback harvest** (v1): negatively rated runs are exported as unlabeled candidates; after human labeling they enter the next dataset version, never the current one.
- The metrics table in requirements marks every value as `target` until a report exists.

## Alternatives

- **Hosted eval products.** Convenient. Rejected for local-first and for owning the numbers.
- **Only live evals, no CI gate.** Simpler. Rejected because a regression gate is part of the platform story.
- **Fixture replay with fuzzy matching.** Would let small prompt edits pass CI without re-recording. Rejected because it hides exactly the changes that should produce a new report.
- **LLM-as-judge from the start.** Needed for answer quality. Deferred to v2 with an agreement check against human labels, because a judge without calibration produces numbers nobody should trust (ADR 0013).

## Consequences

- Fixture recording (`--record`) is part of the harness, and prompt work has a visible cost: re-record and review.
- Reports contain enough metadata to be reproduced.
- Changing a scorer or a prompt that improves numbers requires a new report, not an edit.
