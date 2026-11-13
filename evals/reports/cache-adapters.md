# Cache adapter benchmark: PostgreSQL versus Valkey

## Run

- Generated: 2026-11-13T20:40:19.132009+00:00
- Dataset: `v5` at `evals/datasets/v5/cache.jsonl`, split `test`, 50 pairs (25 that should hit, 25 near misses), 50 cases in the dataset
- Threshold: 0.94 (the value in `config/tenants.yaml` and `evals/reports/cache-lmstudio.md`)
- Repeats: 20 per pair after 5 warmup cycles; lookups measured: pg_semantic 1000, valkey_semantic 1000
- Tenant: `cache_bench` at index version 1, cleared before each pair; the demo tenants and their entries are untouched
- Embeddings: lmstudio (openai_compatible, bge-m3), 1024 dimensions, 80 vectors in 3141.25 ms, computed once and shared by both adapters
- Wall time: 108.704 s
- Provider: `lmstudio` (`bge-m3`); every call is a cache call, there is no chat completion

Latency is the adapter call only: one `FT.SEARCH` for Valkey and one SQL statement plus the hit counter for PostgreSQL. The shared embedding cost is reported above and excluded from both samples, because it is the same for both adapters.

## Hit and false-hit

| Adapter | Hits | Positive lookups | Hit rate | False hits | Near misses | False-hit rate | Accuracy | Errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pg_semantic | 20 | 500 | 0.0400 | 20 | 500 | 0.0400 | 0.5000 | 0 |
| valkey_semantic | 20 | 500 | 0.0400 | 20 | 500 | 0.0400 | 0.5000 | 0 |

Difference: valkey_semantic has 0 more hits and 0 more false hits than pg_semantic over the same 500 positive and 500 near-miss lookups.

## Latency

| Adapter | Lookup p50 ms | Lookup p95 ms | Lookup mean ms | Lookup min ms | Lookup max ms | Store p50 ms | Store p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pg_semantic | 44.31 | 48.461 | 45.09 | 42.724 | 54.463 | 48.795 | 53.159 |
| valkey_semantic | 0.697 | 1.474 | 0.793 | 0.415 | 2.909 | 1.497 | 2.782 |

| Adapter | Invalidate calls | Entries removed | Invalidate p50 ms | Invalidate p95 ms |
| --- | --- | --- | --- | --- |
| pg_semantic | 50 | 985 | 2.178 | 3.698 |
| valkey_semantic | 50 | 52 | 0.841 | 1.578 |

## Versions

| Component | Value |
| --- | --- |
| postgres_cache_table | public.cache_entries (pgvector HNSW, COSINE) |
| postgres_pgvector | 0.8.7 |
| postgres_server | 16.15 (Debian 16.15-1.pgdg12+2) |
| valkey_client | valkey-py 6.2.0 |
| valkey_image | valkey/valkey-bundle:9.0-alpine |
| valkey_image_digest | valkey/valkey-bundle@sha256:508ee27b6e8b8e516c837259dac179f42188ce158279c4c0814fde019861981e |
| valkey_index | sb_cache on HASH keys sb:cache: |
| valkey_search_module | 10003 |
| valkey_server | 9.0.7 |

## Where the PostgreSQL latency comes from

The PostgreSQL adapter sends the 1024-dimension embedding as pgvector's text form with full float precision. The same `LOOKUP_SQL` with the same vectors at reduced precision isolates how much of the measured latency is that payload on this host's connection to the container, rather than the database:

| Vector literal | Bytes | Lookups | p50 ms | p95 ms |
| --- | --- | --- | --- | --- |
| full precision (what the adapter sends) | 21686 | 50 | 44.987 | 48.406 |
| reduced precision (control only) | 9738 | 50 | 1.503 | 2.392 |

The store is unchanged in both rows; only the encoded size differs. Read the PostgreSQL latency column with this in mind: on this host a lookup that carries the full text vector costs tens of milliseconds more than the same statement with a compact one, and the same payload is what its `store` writes.

## Per pair

The similarity column is one untimed lookup at threshold 0, so it shows the distance the adapter computed for the pair even when it is below the threshold. `hit` is the measured decision at the threshold above.

| Case | Type | Should hit | pg_semantic similarity | pg_semantic hit | valkey_semantic similarity | valkey_semantic hit |
| --- | --- | --- | --- | --- | --- | --- |
| c001 | paraphrase | yes | 0.8127 | 0/20 | 0.8127 | 0/20 |
| c002 | paraphrase | yes | 0.8378 | 0/20 | 0.8378 | 0/20 |
| c003 | paraphrase | yes | 0.9419 | 20/20 | 0.9419 | 20/20 |
| c004 | paraphrase | yes | 0.8602 | 0/20 | 0.8602 | 0/20 |
| c005 | paraphrase | yes | 0.8310 | 0/20 | 0.8310 | 0/20 |
| c006 | paraphrase | yes | 0.6336 | 0/20 | 0.6336 | 0/20 |
| c007 | paraphrase | yes | 0.7850 | 0/20 | 0.7850 | 0/20 |
| c008 | paraphrase | yes | 0.7357 | 0/20 | 0.7357 | 0/20 |
| c009 | paraphrase | yes | 0.8034 | 0/20 | 0.8034 | 0/20 |
| c010 | paraphrase | yes | 0.7401 | 0/20 | 0.7401 | 0/20 |
| c011 | paraphrase | yes | 0.8908 | 0/20 | 0.8908 | 0/20 |
| c012 | paraphrase | yes | 0.8373 | 0/20 | 0.8373 | 0/20 |
| c013 | paraphrase | yes | 0.5998 | 0/20 | 0.5998 | 0/20 |
| c014 | paraphrase | yes | 0.9058 | 0/20 | 0.9058 | 0/20 |
| c015 | paraphrase | yes | 0.8593 | 0/20 | 0.8593 | 0/20 |
| c016 | paraphrase | yes | 0.8494 | 0/20 | 0.8494 | 0/20 |
| c017 | paraphrase | yes | 0.7370 | 0/20 | 0.7370 | 0/20 |
| c018 | paraphrase | yes | 0.7615 | 0/20 | 0.7615 | 0/20 |
| c019 | paraphrase | yes | 0.7377 | 0/20 | 0.7377 | 0/20 |
| c020 | paraphrase | yes | 0.7907 | 0/20 | 0.7907 | 0/20 |
| c021 | paraphrase | yes | 0.8393 | 0/20 | 0.8393 | 0/20 |
| c022 | paraphrase | yes | 0.7583 | 0/20 | 0.7583 | 0/20 |
| c023 | paraphrase | yes | 0.8720 | 0/20 | 0.8720 | 0/20 |
| c024 | paraphrase | yes | 0.8609 | 0/20 | 0.8609 | 0/20 |
| c025 | paraphrase | yes | 0.9226 | 0/20 | 0.9226 | 0/20 |
| c026 | near_miss | no | 0.9112 | 0/20 | 0.9112 | 0/20 |
| c027 | near_miss | no | 0.7695 | 0/20 | 0.7695 | 0/20 |
| c028 | near_miss | no | 0.8196 | 0/20 | 0.8196 | 0/20 |
| c029 | cross_type | no | 0.7165 | 0/20 | 0.7165 | 0/20 |
| c030 | near_miss | no | 0.9373 | 0/20 | 0.9373 | 0/20 |
| c031 | near_miss | no | 0.9227 | 0/20 | 0.9227 | 0/20 |
| c032 | cross_type | no | 0.6825 | 0/20 | 0.6825 | 0/20 |
| c033 | cross_type | no | 0.6477 | 0/20 | 0.6477 | 0/20 |
| c034 | near_miss | no | 0.7487 | 0/20 | 0.7487 | 0/20 |
| c035 | near_miss | no | 0.9292 | 0/20 | 0.9292 | 0/20 |
| c036 | cross_type | no | 0.7773 | 0/20 | 0.7773 | 0/20 |
| c037 | cross_type | no | 0.7005 | 0/20 | 0.7005 | 0/20 |
| c038 | near_miss | no | 0.7104 | 0/20 | 0.7104 | 0/20 |
| c039 | near_miss | no | 0.6885 | 0/20 | 0.6885 | 0/20 |
| c040 | near_miss | no | 0.6946 | 0/20 | 0.6946 | 0/20 |
| c041 | near_miss | no | 0.9250 | 0/20 | 0.9250 | 0/20 |
| c042 | near_miss | no | 0.7218 | 0/20 | 0.7218 | 0/20 |
| c043 | near_miss | no | 0.8930 | 0/20 | 0.8930 | 0/20 |
| c044 | cross_type | no | 0.4714 | 0/20 | 0.4714 | 0/20 |
| c045 | near_miss | no | 0.8187 | 0/20 | 0.8187 | 0/20 |
| c046 | near_miss | no | 0.7053 | 0/20 | 0.7053 | 0/20 |
| c047 | near_miss | no | 0.4907 | 0/20 | 0.4907 | 0/20 |
| c048 | near_miss | no | 0.6660 | 0/20 | 0.6660 | 0/20 |
| c049 | near_miss | no | 0.9810 | 20/20 | 0.9810 | 20/20 |
| c050 | near_miss | no | 0.8603 | 0/20 | 0.8603 | 0/20 |

## Errors
None. Every store, lookup, invalidate call and similarity probe succeeded.

## Notes

- Both adapters implement the same `Cache` port: `key_for` embeds the raw query and reads the tenant's `doc_index_version` from `public.tenants`, `lookup` filters on tenant, index version and expiry and applies the threshold, `store` writes with a TTL, and `invalidate` counts what it removed.
- Valkey normalizes the query vector before the KNN call because the pinned `valkey-search` module does not, and reads the distance as `1 - cosine` exactly like pgvector's `<=>`.
- The index was rebuilt before the run for valkey_semantic. The pinned `valkey-search` module keeps a deleted document in its HNSW graph as a tombstone, never lowers `num_docs`, and after many store and delete cycles a lookup can return nothing even though matching keys exist (`FT.INFO` still counts them). A rebuild (drop the index, create it again, keys untouched) restores it; the adapter misses in that state, it never returns a wrong answer. The rebuild is outside the measurement.
- Each pair writes its repeats first and waits until the stored question is visible before the timed lookups, because `valkey-search` indexes asynchronously and a lookup issued microseconds after a store can see nothing. The wait is untimed and both adapters get it; 0 of 100 pair measurements needed more than one attempt (maximum 1). A caller never retries: an empty result is a cache miss, never a wrong answer.
- The two stores hold a different number of entries for the same workload: the PostgreSQL table inserts a row on every store, while the Valkey adapter keys an entry by tenant, index version and normalized question, so storing the same question again refreshes that entry and its TTL. The measured lookup behaviour is identical; the invalidation counts above differ for that reason.
- The false-hit rate at 0.94 is the dataset's known weakness, not a property of either store: raw cosine similarity does not separate these paraphrases from these near misses well. See `evals/reports/cache-lmstudio.md` for the threshold sweep.
- This report never rewrites `cache-lmstudio.md` or a dataset; it is produced by `switchboard eval cache-adapters`.
