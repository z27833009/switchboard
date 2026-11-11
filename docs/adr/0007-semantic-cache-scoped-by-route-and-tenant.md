# ADR 0007: Semantic cache scoped by route and tenant with measured false-hit rate

Status: proposed, 2026-11-11 (v1 feature)

## Context

A semantic cache in front of an agent pipeline saves cost and latency for repeated questions, but it is also a known source of wrong answers: two questions that embed close together can need different answers ("orders in March" versus "orders in April"), and cached answers go stale when documents change. Several products offer such caches (GPTCache, LiteLLM, Portkey, Redis LangCache); what they rarely show is the false-hit rate.

## Decision

- The supervisor's routing decision carries a `cacheable` flag. Only `direct` and `rag` routes are cacheable. `sql` and `parallel` are never cached.
- Cache key: tenant id, route, normalized query, embedding, and the tenant's document index version. A hit requires all of tenant, route and index version to match and cosine similarity above a threshold.
- Ingestion bumps the index version, which invalidates that tenant's entries. A TTL covers `direct` answers.
- The threshold is chosen from a `cache` eval suite with paraphrase pairs and near-miss pairs; the report shows hit rate and false-hit rate per threshold and the chosen value is documented with the report path.
- Every lookup is a span with similarity and threshold; the run records the hit.

## Alternatives

- **Global cache over all routes.** Highest hit rate on paper. Rejected because it returns wrong answers for parameterized questions.
- **Exact-match cache only.** No false hits. Rejected as too weak to be interesting; it may be added as a first tier later.
- **No cache.** Honest option. Rejected because the invalidation and measurement design is a good interview topic, and the feature is small on PostgreSQL (ADR 0003).

## Consequences

- Hit rate on the demo is whatever the dataset makes it, so the README reports it with that caveat.
- The feature is deliberately second priority (v1) behind measurement infrastructure.
