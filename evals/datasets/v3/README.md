# Eval datasets v3

Frozen once a report references it (ADR 0008). Fixes go into `v4/`.

`v1/` and `v2/` stay untouched: they back the routing, toolcall and gate
reports. This version adds only the `rag` suite, which measures retrieval
quality over the ingested policy corpus.

## Files

| File | Written by | Purpose |
| --- | --- | --- |
| `rag.jsonl` | The T2.03 implementation session | 40 questions with the policy chunks that answer them, four of them with conversation history |

## Case fields

| Field | Meaning |
| --- | --- |
| `id` | Stable case id, `rag001` to `rag040` |
| `query` | The user question the retriever receives |
| `relevant_chunk_ids` | Portable chunk keys, one or more per case. The field keeps the design 20.1 name; the values are keys, not database ids |
| `tags` | Topic, plus `needs_rewrite` on the cases where the bare wording is expected to retrieve worse than a contextualized or rewritten query |
| `split` | `test` for every case (see below) |
| `history` | Optional conversation turns, `user` and `assistant`, for follow-up cases |

### Why chunk keys, not chunk ids

`chunks.id` is a database-generated UUID. Re-ingesting a document deletes its
chunks and inserts new rows, so a frozen dataset holding UUIDs would break on a
fresh volume. A case therefore stores a key of the form `source#ordinal`, for
example `returns-policy.md#1`, where `source` is the document path used by
`documents.source` and `ordinal` is `chunks.ordinal` inside that document. The
rag suite resolves every key to the current chunk id at eval time, and turns
retrieved chunks back into keys before scoring. The key is stable across
ingests as long as the document text and the chunker do not change; if they do,
the key still has to exist and a missing key fails the run with the list of
missing keys, rather than silently scoring zero.

## How the labels were made

The labels come from reading the five synthetic documents in
`data/documents`, not from the retriever output. Each relevant key is the chunk
whose text answers the question. A question that needs two policy statements
(for example, when a refund is issued and where it is sent) lists both chunks as
relevant, so recall@5 can be below 1.0 even when one relevant chunk was found.
The retriever was used during construction only to confirm that the key labels
matched the corpus, and the rewrite prompt was revised once before the labels
were frozen. The report says what the numbers mean; it does not claim that the
set is an independent human annotation.

`needs_rewrite` marks an expectation, not a label used by the scorer: the
conditional strategies decide from `retrieval.min_score`, never from the tag.
The tag exists so the report can compare the strategies on the cases where
rewriting was expected to matter. Four cases carry `history`, which is what the
conditional strategy contextualizes before its first retrieval; the other 36
have no history and are only rewritten when the first top score falls below the
configured minimum.

## Split

Every case is `test`. The task behind this version asks for the four strategies
on the same 40 cases, and there is no prompt tuning loop here: the rewrite
prompt is a fixed instruction from design.md section 9.3 and was not fitted to
individual cases. This is weaker than the dev/test discipline used for the
routing dataset, where prompt work read only dev cases; the report and this
README state it instead of implying a larger test set than exists.

## Metrics

The suite reports recall@5 and MRR for four strategies on the same cases:

| Strategy | Search | Rewriting |
| --- | --- | --- |
| `query_as_written` | vector | never |
| `always_rewrite` | vector | every case |
| `conditional_rewrite` | vector | when history is present or the top score is below `retrieval.min_score` |
| `conditional_hybrid` | vector plus full-text, RRF k = 60 | same rule as `conditional_rewrite` |

Recall@5 is the share of relevant keys found in the first five results. MRR is
the mean of `1/rank` of the first relevant result, over the five returned
results. Each report carries `dataset_version: v3`, the split, the case count,
per-strategy latency and the rewrite and retrieval round counts.

## Command

```bash
uv run --env-file .env switchboard eval run --suite rag --dataset-version v3 \
  --provider lmstudio --concurrency 1
```

The suite needs the compose database with the five documents ingested for the
`default` tenant and the configured embedding model. The provider is used only
for rewrite calls. `--provider mock --record` records those calls through the
real provider, and a later `--provider mock` run replays them, but retrieval
still needs the real embedding model and the database, so this suite is not part
of the CI mock gate.
