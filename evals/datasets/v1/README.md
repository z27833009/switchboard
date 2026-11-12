# Eval datasets v1

Frozen once a report references it (ADR 0008). Fixes go into `v2/`.

## Files

| File | Written by | Purpose |
| --- | --- | --- |
| `routing.questions.jsonl` | A separate generator session, from [the brief](../../briefs/routing-v1.md) | 60 questions, no labels |
| `routing.generator_labels.jsonl` | The same generator session | Its labels, used only to find disagreements |
| `routing.author_labels.jsonl` | The author, blind, with `label_routing.py` | The author's blind labels |
| `routing.adjudication.jsonl` | The author, after seeing the disagreements | Final labels for disagreement cases the author judged to be their own mistakes, with a note per case |
| `routing.jsonl` | `merge_routing.py` | The suite input: author labels, `ambiguous` tag where the two labelers disagree, dev or test split |
| `toolcall.jsonl` | `gen_toolcall.py` from templates, wording reviewed by the author | 40 cases; arguments computed by code |

Intermediate files stay committed so the construction can be audited.

## How the routing labels were made

1. A generator session that had seen only the brief wrote the questions and, separately, its own labels.
2. The author labeled every question without seeing the generator's labels.
3. Where the two blind labels disagree, the case is tagged `ambiguous`; reports show accuracy on unambiguous and ambiguous cases separately, plus the labeler agreement rate.
4. Adjudication: the author reviewed only the disagreement cases. Where the author judged their own blind label to be a mistake, the final label comes from `routing.adjudication.jsonl`, and the blind label is kept in the case as `author_initial_agents`. Every other case uses the author's blind label. Adjudicated cases keep the `ambiguous` tag, because the tag records that blind labelers disagreed.

Result for v1 (measured 2026-11-12): blind agreement 50 of 60 (83 percent); 10 cases tagged ambiguous; 5 of them adjudicated.

This replaces "the author writes every case", which was slower and gave the author no second opinion. The prompt writer still never sees the test cases while writing prompts (see splits).

## Splits

One in three cases (by a hash of the id, see `scripts/common.py`) is `dev`. Prompt work may look at dev cases. Reports score the `test` split; dev numbers are shown separately and never quoted as results.

## Argument conventions for toolcall

These are part of the tool contract in design.md section 7.1; the tool schemas implement them.

| Convention | Rule |
| --- | --- |
| Dates | `YYYY-MM-DD` |
| `date_to` | Inclusive. "March 2018" is `2018-03-01` to `2018-03-31` |
| Date meaning | Ranges filter on the order purchase date, for every tool |
| Relative dates | Not used. The dataset has no "today" |
| `status` | Olist `order_status` values (`delivered`, `shipped`, `canceled`, `invoiced`, ...) |
| `metric` | `revenue` (sum of item prices) or `count` (items sold) |
| Optional arguments | Omitted unless the question states them; the scorer compares optional arguments only when the label has them |

## Commands

```
python evals/scripts/gen_toolcall.py --orders-csv data/olist/raw/olist_orders_dataset.csv
python evals/scripts/validate.py toolcall
python evals/scripts/validate.py questions
python evals/scripts/label_routing.py
python evals/scripts/merge_routing.py
python evals/scripts/validate.py routing
```
