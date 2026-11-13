# ADR 0003: PostgreSQL for vectors, cache and traces; no Redis in Phase 1

Status: accepted, 2026-11-13. Proposed 2026-11-11. Shipped for vectors (HNSW plus full-text with RRF), spans, runs, audit rows, the Olist schema and the semantic cache table (`cache_entries`, T2.18), with no Redis or second vector store. The cache threshold measurement is in ADR 0007 and `evals/reports/cache-lmstudio.md`. Amended 2026-11-13 (T3.09): the v2 comparison this decision anticipated now exists as `ValkeySemanticCache`, a second `Cache` adapter over Valkey with the valkey-search module. PostgreSQL stays the configured cache and the only stateful service the default stack needs; the comparison is in `evals/reports/cache-adapters.md`.

## Context

The platform needs vector search for RAG, a semantic cache, a span store for traces and an audit log. A common stack would add Redis (cache, rate limits) and a dedicated vector database. The project is local-first, solo, and must start with one command.

## Decision

Use PostgreSQL 16 with pgvector for all of it: chunks and embeddings, semantic cache entries, spans, runs, audit rows and the Olist business data in its own schema. No Redis, Valkey or separate vector database in Phase 1 or v1. The `Cache` port exists from the start so a Valkey adapter can be added in v2 as a comparison.

## Alternatives

- **Redis for the semantic cache.** Redis Stack has vector search and the licensing question is settled for self-hosting (AGPLv3 option since Redis 8; Valkey is BSD). Rejected for now because it adds a container and a second data model for a cache that will see demo traffic only; the semantic cache is a table with an embedding column and a tenant id.
- **Qdrant or similar for vectors.** Better at scale. Rejected because pgvector with HNSW is sufficient for a corpus of hundreds of chunks and keeps hybrid search (vector plus full text) in one query.
- **SQLite.** Simplest. Rejected because of the vector extension story and because multi-tenant row filtering and roles are part of the demo.

## Consequences

- One stateful service in Docker Compose.
- Hybrid search is a single SQL statement with reciprocal rank fusion.
- Trace volume lives in the same database as business data; acceptable for a demo, called out as a limitation.
- Adding Valkey later is a port implementation plus a benchmark, which is a better story than having it from day one without a reason.
- The port implementation arrived in v2 (T3.09) and the story is measured: at the 0.94 threshold both stores returned the same decisions on the frozen 50-pair cache dataset, 20 hits of 500 positive lookups (0.04) and 20 false hits of 500 near-miss lookups (0.04), so the adapter choice does not change what the cache answers. Lookup p50 was 44.3 ms through PostgreSQL and 0.70 ms through Valkey on the development host, with 1000 measured lookups each. That gap is mostly transport, not the database: the PostgreSQL adapter sends the 1024-dimension vector as a 21.7 kB text literal, and the same statement with a 9.7 kB literal took 1.8 ms, so the database work is microseconds and a binary or lower-precision parameter would remove most of the difference (not changed here, because it would alter a store the demo already ships).
- The comparison also found a reason to keep PostgreSQL as the default: the pinned valkey-search module never removes a deleted document from its HNSW graph (`num_docs` does not fall), and after enough store and delete cycles a filtered KNN query can return nothing at all even though matching keys exist. A degraded index only produces misses, never a wrong answer, and `FT.DROPINDEX` plus a recreate restores it, but an operator would have to notice. A cache that has to be rebuilt periodically is a worse default than a table next to the data it already needs.
- The Valkey service is pinned by tag and digest in compose, keeps no append-only file and no snapshot (a cache, not a system of record), and the API only reaches it when `config/platform.yaml` selects `cache: valkey_semantic`. The default stack is unchanged.
