# ADR 0003: PostgreSQL for vectors, cache and traces; no Redis in Phase 1

Status: accepted, 2026-11-13. Proposed 2026-11-11. Shipped for vectors (HNSW plus full-text with RRF), spans, runs, audit rows, the Olist schema and the semantic cache table (`cache_entries`, T2.18), with no Redis or second vector store. The cache threshold measurement is in ADR 0007 and `evals/reports/cache-lmstudio.md`.

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
