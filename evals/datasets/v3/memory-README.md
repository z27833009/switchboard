# Multi-session memory dataset v1 (V3.16)

Status: **approved by the author on 2026-11-16**. The author reviewed the 20
candidate cases and approved them as written. The file is frozen once a report
references it; a later change creates `memory-v2`.

## What a case means

Each case has one fact and one question that requires that fact. The fact is
deliberately not derivable from the demo corpus or from general knowledge, so a
correct answer is evidence that the fact was carried across the turns.

The runner uses these modes:

1. **history off**: the fact is said in session A, the question is asked in a new
   session B. No history is sent. This is the control.
2. **layers 1 to 4, cross-session**: the same fact session, a second new question
   session, with the current context policy. The current design has no
   cross-session memory table, so this measures whether the existing layers carry
   the fact across a session boundary.
3. **layers 1 to 4, same session**: the fact and question share one session. This
   is the within-session probe, so the report can separate "the model cannot
   remember at all" from "the layers work inside one conversation but not across
   conversations".

A separate future **tenant memory** probe can use the same cases after a
tenant-scoped memory table exists.

## Author review

The author reviewed the candidates and approved them as written. The validator
must report `20 approved` before the runner starts.

## Validation

```bash
uv run python scripts/validate_memory_candidates.py evals/datasets/v3/memory-v1.json
```

The validator checks the count, unique ids, non-empty fields, review status and
that `expected_contains` does not appear verbatim in the question.

## Running

The runner is `scripts/run_memory_cases.py`. It needs the local API and LM Studio:

```bash
uv run --env-file .env python scripts/run_memory_cases.py \
  --dataset evals/datasets/v3/memory-v1.json \
  --out evals/reports/memory-v1.json
```

It writes the JSON results and a markdown report beside them. The report records
the pass count for each mode and whether T3.11 (long-term memory) resumes.
