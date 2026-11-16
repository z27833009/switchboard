# Multi-session memory candidates (V3.16)

Status: **candidate**. This file was generated as a first draft for the author to
review and edit. It is not the frozen dataset until every case has
`"review": "approved"` and the version is renamed to `memory-v1`.

## What a case means

Each case has one fact and one question that requires that fact. The fact is
deliberately not derivable from the demo corpus or from general knowledge, so a
correct answer is evidence that the fact was carried across the turns.

The runner will use two modes:

1. **history off**: the fact is said in session A, the question is asked in a new
   session B. No history is sent. This is the control.
2. **layers 1 to 4**: the fact is said in session A, then the question is asked
   in the same session. The current context policy and session history are the
   only memory available.

A separate **cross-session** probe can keep the two session ids but enable a
future tenant-scoped memory table. It is not part of the current V3.16 run.

## Author review checklist

For every case:

- the fact is a single, unambiguous statement;
- the question cannot be answered without the fact;
- `expected_contains` lists only strings that must appear in a correct answer;
- the wording is natural and does not leak `expected_contains` in the question;
- the case is not a duplicate of another case;
- the fact does not depend on a real person, employer or private dataset.

Change `"review": "pending"` to `"review": "approved"` after editing. Do not
edit a case after the report references it; create a new version instead.

## Validation

```bash
uv run python scripts/validate_memory_candidates.py evals/datasets/v3/memory-candidates.json
```

The validator checks the count, unique ids, non-empty fields, review status and
that `expected_contains` does not appear verbatim in the question.

## Running after review

The runner is intentionally not added until the dataset is approved. It will
create one session for the fact and one for the question, record both run ids
and answers, and write a report under `evals/reports/`. The report will state
whether T3.11 (long-term memory) is still needed.
