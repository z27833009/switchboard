# ADR 0008: Evaluation strategy: versioned datasets, mock provider in CI, honest reporting

Status: proposed, 2026-11-11

## Context

The project's value depends on numbers that can be trusted: routing accuracy, tool-call accuracy, retrieval recall, cache false-hit rate, latency and cost per provider. CI cannot call paid APIs or a developer's local model, and the author writes both the prompts and the datasets.

## Decision

- Datasets are JSONL files under `evals/datasets/<version>/`. A version is frozen once a report references it; fixes go into a new version.
- Each suite implements the `EvalSuite` port (load, execute, score) and registers through the same mechanism as other adapters.
- The runner writes a markdown report and a JSON report with dataset version, provider, model, case count, metrics, p50 and p95 latency and cost. Reports that back README numbers are committed.
- CI runs the suites with a `MockProvider` replaying recorded fixtures and compares against committed baselines with a maximum allowed drop. The README states that these CI numbers measure the pipeline, not the model.
- Paid providers require an explicit confirmation flag and respect a cost cap.
- Datasets for a suite are written before the prompts for that suite; ambiguous cases are tagged and reported separately; confusion matrices are included for routing.
- The metrics table in requirements marks every value as `target` until a report exists.

## Alternatives

- **Hosted eval products.** Convenient. Rejected for local-first and for owning the numbers.
- **Only live evals, no CI gate.** Simpler. Rejected because a regression gate is part of the platform story.
- **LLM-as-judge from the start.** Needed for answer quality. Deferred to v2 with an agreement check against human labels, because a judge without calibration produces numbers nobody should trust.

## Consequences

- Fixture recording (`--record`) is part of the harness.
- Reports contain enough metadata to be reproduced.
- Changing a scorer or a prompt that improves numbers requires a new report, not an edit.
