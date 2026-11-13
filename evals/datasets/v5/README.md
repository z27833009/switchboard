# Eval dataset v5: semantic cache pairs

Frozen once a report references it (ADR 0008). Fixes go into `v6/`.
`v1/` to `v4/` stay untouched: they back the routing, toolcall, gate, rag and
injection reports.

This version adds only the `cache` suite: 50 pairs of questions with one label
each, used to choose the similarity threshold from a measured sweep.

## Files

| File | Written by | Purpose |
| --- | --- | --- |
| `cache.jsonl` | The implementation session for T2.18 | 25 paraphrase pairs and 25 near misses |

## Case fields

| Field | Meaning |
| --- | --- |
| `id` | Stable case id, `c001` to `c050` |
| `query` | The first question, used to render the case row |
| `query_a` | The stored question (the one with a cache entry in a live run) |
| `query_b` | The incoming question that is compared with it |
| `should_hit` | `true` when both questions need the same answer, `false` when the wording is close but the answer differs |
| `pair_type` | `paraphrase`, `near_miss` or `cross_type` (one query needs a record or number and the other asks for policy) |
| `tags` | Topic tags for slicing the report |
| `split` | `test` for every case; the threshold is chosen from this whole set, not tuned on a separate dev split |
| `note` | One line explaining why the label is what it is |

## How the pairs were made

The 25 `should_hit` pairs are paraphrases of one policy question each: same
intent, different wording, and the same document passage would answer both.

The 25 near misses are close in wording but need a different answer. They fall
into three groups:

| Group | Pairs | Example |
| --- | --- | --- |
| date, region, metric or bound | c026, c030, c031, c040, c041, c048, c049, c050 | March 2018 versus April 2018 |
| different rule or service | c027, c028, c034, c035, c038, c039, c042, c043, c045, c046, c047 | electronics return window versus standard return window |
| cross-type (policy versus data) | c029, c032, c033, c036, c037, c044 | late-delivery compensation policy versus late deliveries in March 2018 |

`c029` is the example in design.md section 10.2 and ADR 0007: "late delivery
compensation policy" versus "late deliveries in March". A cache that hits there
would answer a policy question with a count, so this pair is exactly the
false-hit risk the report measures.

The labels were written and read through by the implementation session, not by
an independent human annotator, and the pairs were written in the same task as
the suite. The report describes this set, not real-world cache quality.

## Measurement

The suite embeds both questions of every pair once and reports the cosine
similarity. The report sweeps thresholds 0.85, 0.88, 0.90, 0.92, 0.94, 0.96 and
0.98 and shows, per threshold, the hit rate among the pairs labeled `should_hit`
and the false-hit rate among the near misses. The configured threshold in
`config/tenants.yaml` must be one of these values and the report path is cited
next to it.

## Command

```
uv run --env-file .env switchboard eval run --suite cache --dataset-version v5 --provider lmstudio
```

`--provider mock` uses the deterministic mock embedder and is labeled as a
mechanics check only in the report.
