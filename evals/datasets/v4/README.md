# Eval datasets v4

Frozen once a report references it (ADR 0008). Fixes go into `v5/`.
`v1/`, `v2/` and `v3/` stay untouched: they back the routing, toolcall, gate and
rag reports.

This version adds only the `injection` suite. Each case is one piece of
untrusted content that an agent could receive, either malicious or benign. The
suite runs the deterministic scanner in `src/switchboard/core/guardrails.py`,
so it needs no model and no database.

## Files

| File | Written by | Purpose |
| --- | --- | --- |
| `injection.jsonl` | The implementation session for T2.04 | 20 malicious and 20 benign content samples |

## Case fields

| Field | Meaning |
| --- | --- |
| `id` | Stable case id, `m001` to `m020` for malicious and `b001` to `b020` for benign |
| `query` | A one-line description used to render the case; the scanner does not read it |
| `content` | The untrusted text that a document or tool result could carry |
| `is_malicious` | The label: should the scanner flag this content |
| `expected_pattern` | For malicious cases, the pattern id the sample was written to exercise; `null` for benign cases |
| `split` | `test` for every case; the scanner has no prompt to tune, so there is no dev split |
| `note` | One line explaining the sample, including the two deliberate false positives |

## How the samples were made

The 20 malicious samples were written by hand, one or two per pattern id, from
the pattern families in design.md section 13.1:

| Family | Samples | Pattern ids |
| --- | --- | --- |
| instruction override | m001 to m003 | `ignore_previous`, `new_instructions` |
| role impersonation | m004 to m007 | `you_are_now`, `act_as_privileged`, `fake_system_turn` |
| tool or action request | m008 to m011 | `call_tool`, `run_command`, `forward_secrets` |
| prompt exfiltration | m012 to m014 | `reveal_prompt`, `repeat_above` |
| encoded payload | m015 to m018 | `base64_blob`, `decode_instruction`, `escape_sequence` |
| delimiter injection | m019 and m020 | `closing_tag`, `chat_template` |

Each malicious sample is labeled with the pattern id it was written for, so the
report can show per-pattern recall. Extra matches from other patterns do not
change a pattern's score.

The 20 benign samples are near-miss policy, support and documentation text: they
use the vocabulary of injection (instructions, system prompt, password, token,
SQL, base64, HTML tags, tool names) without being an instruction to the model.
Eighteen of them must stay unflagged. Two are deliberate false-positive traps:

- `b011` is a benign seller notice that starts with "New instructions:", which
  the `new_instructions` pattern flags.
- `b012` is benign advice to "Call the tool in the seller dashboard", which the
  `call_tool` pattern flags.

They are kept on purpose so the measured false positive rate is not zero by
construction and the per-pattern precision of those two rules is visible. The
labels are constructed by the implementation session and read through, not
produced by an independent human annotator. The pattern set and the labels were
written in the same task, so the report describes this set, not real-world
detection quality; patterns are extended from later failures, not from guesses.

## Metrics

The suite reports:

- `detection_rate`: flagged malicious samples divided by all malicious samples.
- `false_positive_rate`: flagged benign samples divided by all benign samples.
- sample `precision`, `recall` and `f1`, and `case_accuracy`.
- per pattern id: TP, FP, FN, TN, precision and recall. For one pattern, TP and
  FN count only the malicious samples labeled with that id; FP and TN count only
  the benign samples. Malicious samples labeled with another id are outside that
  pattern's counts.

The report always carries `dataset_version: v4` and the pattern set version from
`switchboard.core.guardrails`.

## Command

```
uv run switchboard eval run --suite injection --dataset-version v4 --provider mock
```

The suite never calls the provider, so `--provider mock` only satisfies the
runner's configuration lookup. The report's model field is `deterministic`.
