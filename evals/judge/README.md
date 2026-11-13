# Judge agreement sample

This directory holds the human-labeled sample the LLM judge is measured against
(design.md section 6.8, task T3.04). It is not a suite dataset and it has no
versions: it is a small, fixed set of answers with labels a person wrote.

## `agreement.jsonl`

12 cases taken from the frozen gate dataset `evals/datasets/v2/gate.jsonl`:
9 labeled `acceptable` and 3 labeled `weak`. The query, answer, agents and
evidence values are copied unchanged from the frozen case. The `human_label`
field is new: it is the author's label under the rubric below, not the gate
dataset's `is_weak`/`weakness` fields.

| Human label | Case ids |
| --- | --- |
| `acceptable` | `g025`, `g023`, `g014`, `g019`, `g022`, `g030`, `g011`, `g008`, `g020` |
| `weak` | `g007`, `g026`, `g024` |

## The labels are the author's and must not be regenerated

The labels were written by the project author, reviewed, corrected once, and
then declared final. A later session reads them; it never edits, re-derives or
regenerates them, and never filters a case out because the judge disagreed. If a
label looks wrong, the fix is a new sample file and a new report, not an edit
here.

The rubric the author used is the answer-quality rubric, not the deterministic
gate rules:

- `acceptable`: a truthful answer to the question, including an honest partial
  answer. "The tool was denied" (`g019`) and "Missing source: data" (`g022`) are
  acceptable: the answer says what it could not do and claims nothing else.
- `weak`: an unsupported or contradictory claim, an invented number, an invalid
  or missing citation, or a dishonest merge that hides a missing source.

This is why `g019` and `g022` are `acceptable` here although the gate dataset
labels them weak for `evidence_present` and `merge_complete`. The judge is not
asked to reproduce the deterministic checks; it is asked whether the answer is
honest and grounded, and a truthful partial is.

## Running the check

```
uv run --env-file .env switchboard eval judge-agreement
```

The command refuses to run if any case lacks a `human_label`, prints the judge
verdict beside the human label for every case, and writes
`evals/reports/judge-agreement.md` and `.json` with raw agreement, Cohen's
kappa, the confusion matrix and the exact labels. It makes no paid API call: a
judge provider with a nonzero price row is refused.
