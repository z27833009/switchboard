# Eval datasets v2

Frozen once a report references it (ADR 0008). Fixes go into `v3/`.

`v1/` stays untouched: it backs the routing and toolcall reports. This version
adds only the gate suite, which needs answer cases rather than supervisor or
tool-call cases. A later report may still cite `v1` for routing and toolcall.

## Files

| File | Written by | Purpose |
| --- | --- | --- |
| `gate.jsonl` | The implementation session for T2.01 | 30 answer cases, each with evidence and expected failed checks |

## Case fields

| Field | Meaning |
| --- | --- |
| `id` | Stable case id, `g001` to `g030` |
| `query` | The user question, used to exclude query numbers from grounding and to render the case |
| `answer` | The answer text as an agent would return it, including `[dN]` and `[tN]` markers |
| `agents` | Agents that produced the answer (`direct`, `docs`, `data`, or a combination) |
| `plan` | Sources the plan required; defaults to `agents` |
| `result_sources` | Sources that actually produced a result; defaults to `plan` |
| `evidence` | Evidence the agent saw: `kind`, `ref`, `source_id`, `text`, and optional `empty` |
| `is_weak` | Overall label: should the gate reject this answer |
| `weakness` | The checks that should fail, from `numbers_grounded`, `citations_valid`, `evidence_present`, `merge_complete` |
| `split` | `test` for every case; the gate suite has no prompt to tune, so there is no dev split |
| `note` | One line explaining the label |

The gate suite builds citations from the answer markers and evidence refs, so a
case can exercise an unknown marker by writing one that no evidence ref matches.
It runs the orchestrator's own `evaluate_gate` function, not a copy.

## How the labels were made

The 30 cases and their expected checks were written by the T2.01 implementation
session before the checks were run. Each label follows the prose definition in
design.md section 6.8, not the output of the code:

- `numbers_grounded`: a number in the answer that is not in the query is absent
  from the evidence text after separator and unit normalization.
- `citations_valid`: a docs or data answer has no marker, or a marker names a ref
  the agent was not given. A case with no evidence at all passes this check
  because `evidence_present` already reports the failure; a citation retry would
  be the wrong action.
- `evidence_present`: the agent saw no usable evidence. An empty tool result is
  a ref with `empty: true`, so it is present in the case but not usable.
- `merge_complete`: a plan with more than one source is missing a produced
  source. The answer may name the missing source; the check still fails because
  the merge is incomplete, while the recommended action is none.

These are constructed labels, not labels from an independent human annotator.
They were read through after writing, and the report says so. The measured
precision and recall describe the checker against this set, not real-world
answer quality.

The 30 cases include both good and weak examples for every check: 9 positive
`numbers_grounded` cases, 3 positive `citations_valid`, 4 positive
`evidence_present`, and 2 positive `merge_complete`; the rest are negatives,
including good answers that must not be rejected.

### The deliberate false positive

`g013` asks how many of 100 orders were late. The answer says 50 were late and
that this is 50 percent. The 50 percent is a correct derived number but it is
not written in the evidence, which contains 100 and 50. The case is labeled
acceptable on purpose. The check rejects it, so `numbers_grounded` precision is
below 1.0 and the false rejection is visible in the report instead of being
assumed away. ADR 0013 records this known limitation; a derived-number allowance
is future work, not part of T2.01.

## Metrics

The suite reports precision, recall and F1 per check, plus overall weak-answer
precision, recall, case accuracy and specificity for good answers. Counts (TP,
FP, FN, TN per check) are in the JSON report under `gate_counts` and in the
markdown check table. A report always carries `dataset_version: v2`.

## Command

```
uv run switchboard eval run --suite gate --dataset-version v2 --provider mock
```

The suite never calls the provider, so `--provider mock` only satisfies the
runner's configuration lookup. The report's model field is `deterministic`.
