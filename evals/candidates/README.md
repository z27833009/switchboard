# Candidate cases harvested from feedback

`switchboard eval harvest --since <date> [--tenant <id>]` writes the negatively
rated runs of this database here as unlabeled JSONL. Candidates are inputs for
the next dataset version, never edits to a frozen one.

```bash
uv run --env-file .env switchboard eval harvest --since 2026-11-01
uv run --env-file .env switchboard eval harvest --since 2026-11-01 --tenant seller_a
```

## Selection

- Only rating `-1` is exported: `1` is a positive rating and is not a candidate.
- A run's verdict is its newest feedback row, so a run rated down and then up is
  not a candidate and a run rated up and then down is.
- `--since` filters on when the feedback was written, in UTC, not on when the run
  started.
- `--tenant` keeps one tenant's feedback; without it the file covers every
  tenant and each case carries its `tenant_id`.

## File name and repeat runs

One file per since date and tenant:
`feedback-<since>-<tenant|all>.jsonl`. The command rewrites that file from the
current rows, so running it twice produces the same bytes and never appends a
duplicate case. An empty result writes an empty file rather than deleting the
previous one.

Generated files are ignored by git. Commit the labeled dataset that comes out of
a labeling session, not the raw harvest.

## Case format

One JSON object per line, keys sorted:

```json
{
  "answer": "There were 1328 orders in March 2018 (t1).",
  "candidate_id": "candidate-4c0f...",
  "note": "the count is wrong",
  "plan": "data",
  "query": "How many orders were placed in March 2018?",
  "rated_at": "2026-11-13T09:30:00+00:00",
  "rating": -1,
  "run_ended_at": "2026-11-13T09:29:58+00:00",
  "run_id": "4c0f...",
  "run_started_at": "2026-11-13T09:29:50+00:00",
  "status": "ok",
  "tenant_id": "default"
}
```

There is no expected label: no `expected_agents`, no `expected_tool`, no
`expected_args`, no split. The human labeling pass adds those fields and the
labeled cases go into a new dataset version, for example
`evals/datasets/v5/<suite>.jsonl`, with the README for that version stating how
they were labeled.
