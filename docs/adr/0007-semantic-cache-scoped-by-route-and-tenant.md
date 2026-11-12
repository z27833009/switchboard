# ADR 0007: Semantic cache before routing, scoped by tenant and index version, with measured false-hit rate

Status: proposed, 2026-11-11 (v1 feature). Amended 2026-11-12: lookup moved before the supervisor, route removed from the key. The file name keeps its original slug so links stay stable.

## Context

A semantic cache in front of an agent pipeline saves cost and latency for repeated questions, but it is also a known source of wrong answers: two questions that embed close together can need different answers ("orders in March" versus "orders in April"), and cached answers go stale when documents change. Several products offer such caches (GPTCache, LiteLLM, Portkey, Redis LangCache); what they rarely show is the false-hit rate.

Design 0.1 looked up the cache after the supervisor and keyed it by route. A hit therefore still paid for the routing model call, contradicting the requirement that a hit costs only the embedding, and the key depended on a nondeterministic model output.

## Decision

- The lookup runs **before** the supervisor. A hit makes no model call; its cost is the query embedding, which a miss reuses for retrieval.
- Cache key: tenant id, normalized query, embedding and the tenant's document index version. A hit requires the same tenant and index version, an unexpired entry, and cosine similarity at or above a threshold. The entry records the plan label that produced it, for the trace.
- Only answers from cacheable plans are stored: every agent in the plan must declare `cacheable` (ADR 0011). Plans containing `data` are never stored.
- Lookups are skipped when the session has history, because a follow-up's meaning depends on the conversation.
- Ingestion bumps the index version, which invalidates that tenant's entries. A TTL covers `direct` answers.
- The threshold is chosen from a `cache` eval suite with paraphrase pairs and near-miss pairs, including cross-type near misses (a policy question versus a data question with similar words). The report shows hit rate and false-hit rate per threshold, and the chosen value is documented with the report path.
- Every lookup is a span with similarity and threshold; the run records the hit.
- Priority: last task of v1 and first to cut. Provider prefix caching (ADR 0006) delivers the routine savings.

## Alternatives

- **Lookup after routing, keyed by route (design 0.1).** Rejected for the reasons in the context.
- **Global cache over all plans.** Highest hit rate on paper. Rejected because it returns wrong answers for parameterized questions.
- **Exact-match cache only.** No false hits. Rejected as too weak to be interesting; it may be added as a first tier later.
- **No cache.** Honest option and the fallback if v1 runs long.

## Consequences

- Because the lookup runs before routing, a data question can in principle match a cached policy answer. The cross-type near-miss pairs measure exactly this risk; if it is real, the threshold rises or a cheap intent check is added before accepting a hit.
- Hit rate on the demo is whatever the dataset makes it, so the README reports it with that caveat.
