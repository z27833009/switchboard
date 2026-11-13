"""pgvector retriever: cosine similarity, optional hybrid search with RRF.

Phase 1 uses cosine similarity over the HNSW index (design.md section 9.2).
v1 adds full-text search over the generated ``chunks.tsv`` column and combines
both rankings with reciprocal rank fusion (k = 60). The search mode comes from
the tenant's ``retrieval.search`` setting (``vector`` keeps the Phase 1 path,
``hybrid`` adds full text) or from the constructor, which the rag eval uses to
run both modes over the same corpus.

``RetrievedChunk.score`` is always the cosine similarity, also for a chunk that
only the text branch found. That keeps ``retrieval.min_score`` meaningful while
RRF decides the order. The rewrite trigger uses the best cosine in the top k.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg
from opentelemetry import trace

from switchboard.core.errors import RetrievalError
from switchboard.core.guardrails import guardrail_span_attributes, scan_many
from switchboard.core.models import RetrievedChunk, RunContext
from switchboard.core.ports import Embedder
from switchboard.tracing.spans import record_error, retrieve_span, set_attributes

MAX_K = 50
RRF_K = 60
HYBRID_CANDIDATES = 20
SearchMode = Literal["vector", "hybrid"]


@dataclass(frozen=True)
class RankedCandidate:
    """One chunk in one branch's ranking; ``score`` is the cosine similarity."""

    chunk_id: str
    score: float


@dataclass(frozen=True)
class FusedHit:
    chunk_id: str
    rrf_score: float
    score: float


def reciprocal_rank_fusion(
    vector: list[RankedCandidate],
    text: list[RankedCandidate],
    *,
    k: int = RRF_K,
    limit: int = 5,
) -> list[FusedHit]:
    """Merge two rankings by RRF (design.md section 9.2).

    Each branch contributes ``1 / (k + rank)`` with rank starting at 1. A chunk
    in both branches therefore beats a chunk in only one. Ties are broken by
    the cosine score and then the chunk id, so the order is deterministic. The
    returned ``score`` is the cosine similarity, not the RRF value.
    """
    if k < 1:
        raise RetrievalError("RRF k must be at least 1")
    if limit < 1:
        raise RetrievalError("fusion limit must be at least 1")

    fused: dict[str, float] = {}
    cosine: dict[str, float] = {}
    for ranking in (vector, text):
        seen: set[str] = set()
        for rank, candidate in enumerate(ranking, start=1):
            if candidate.chunk_id in seen:
                continue
            seen.add(candidate.chunk_id)
            fused[candidate.chunk_id] = fused.get(candidate.chunk_id, 0.0) + 1.0 / (k + rank)
            cosine.setdefault(candidate.chunk_id, candidate.score)

    ordered = sorted(
        fused.items(),
        key=lambda item: (-item[1], -cosine[item[0]], item[0]),
    )
    return [
        FusedHit(chunk_id=chunk_id, rrf_score=round(value, 8), score=cosine[chunk_id])
        for chunk_id, value in ordered[:limit]
    ]


class PgVectorRetriever:
    name: str

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        embedder: Embedder,
        name: str = "pgvector",
        search: SearchMode | None = None,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self.name = name
        self._pool = pool
        self._embedder = embedder
        # None means the tenant's retrieval.search setting decides per call.
        self._search = search
        self._tracer = tracer or trace.get_tracer("switchboard.retriever.pgvector")

    async def retrieve(
        self,
        query: str,
        ctx: RunContext,
        k: int = 5,
        *,
        round: int = 1,
        embedding: list[float] | None = None,
    ) -> list[RetrievedChunk]:
        if k < 1 or k > MAX_K:
            raise RetrievalError(f"k must be between 1 and {MAX_K}")
        mode: SearchMode = self._search or ctx.tenant.retrieval.search
        with retrieve_span(self._tracer, k=k, strategy=mode, round=round) as span:
            try:
                chunks = await self._retrieve(query, ctx, k, mode, embedding)
            except RetrievalError as exc:
                record_error(span, exc)
                raise
            span.set_attribute("switchboard.retrieval.hits", len(chunks))
            span.set_attribute(
                "switchboard.retrieval.top_score",
                max((chunk.score for chunk in chunks), default=0.0),
            )
            # The chunks are untrusted content; record only pattern ids and
            # counts on the span, never the text (design.md section 13.1).
            set_attributes(span, guardrail_span_attributes(scan_many(c.text for c in chunks)))
            return chunks

    async def _retrieve(
        self,
        query: str,
        ctx: RunContext,
        k: int,
        mode: SearchMode,
        embedding: list[float] | None = None,
    ) -> list[RetrievedChunk]:
        vector = await self._query_vector(query, embedding)
        if mode == "hybrid":
            return await self._hybrid(query, vector, ctx, k)
        return await self._vector_only(vector, ctx, k)

    async def _query_vector(self, query: str, embedding: list[float] | None) -> list[float]:
        """The cache's embedding when it is usable, otherwise a new one (10.1)."""
        if embedding:
            if len(embedding) != self._embedder.dimension:
                raise RetrievalError(
                    f"reused embedding has dimension {len(embedding)}, "
                    f"expected {self._embedder.dimension}"
                )
            return list(embedding)
        return await self._embed_query(query)

    async def _embed_query(self, query: str) -> list[float]:
        try:
            vectors = await self._embedder.embed([query])
        except Exception as exc:
            raise RetrievalError(f"query embedding failed: {type(exc).__name__}") from exc
        if len(vectors) != 1:
            raise RetrievalError("embedder returned no query vector")
        vector = vectors[0]
        if len(vector) != self._embedder.dimension:
            raise RetrievalError(
                f"embedder returned dimension {len(vector)}, expected {self._embedder.dimension}"
            )
        return vector

    async def _vector_only(
        self, vector: list[float], ctx: RunContext, k: int
    ) -> list[RetrievedChunk]:
        try:
            rows: list[Any] = await self._pool.fetch(
                """
                SELECT c.id AS chunk_id, c.document_id, d.source, d.title,
                       c.text, c.ordinal,
                       1 - (c.embedding <=> $2::vector) AS score
                FROM public.chunks c
                JOIN public.documents d ON d.id = c.document_id
                WHERE c.tenant_id = $1
                ORDER BY c.embedding <=> $2::vector
                LIMIT $3
                """,
                ctx.tenant.tenant_id,
                json.dumps(vector, separators=(",", ":")),
                k,
            )
        except Exception as exc:
            raise RetrievalError(f"vector search failed: {type(exc).__name__}") from exc

        return [self._chunk(row) for row in rows]

    async def _hybrid(
        self,
        query: str,
        vector: list[float],
        ctx: RunContext,
        k: int,
    ) -> list[RetrievedChunk]:
        candidate_limit = max(k, HYBRID_CANDIDATES)
        literal = json.dumps(vector, separators=(",", ":"))
        try:
            vector_rows: list[Any] = await self._pool.fetch(
                """
                SELECT c.id AS chunk_id, c.document_id, d.source, d.title,
                       c.text, c.ordinal,
                       1 - (c.embedding <=> $2::vector) AS score
                FROM public.chunks c
                JOIN public.documents d ON d.id = c.document_id
                WHERE c.tenant_id = $1
                ORDER BY c.embedding <=> $2::vector, c.id
                LIMIT $3
                """,
                ctx.tenant.tenant_id,
                literal,
                candidate_limit,
            )
            text_rows: list[Any] = await self._pool.fetch(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', $2) AS query)
                SELECT c.id AS chunk_id, c.document_id, d.source, d.title,
                       c.text, c.ordinal,
                       1 - (c.embedding <=> $3::vector) AS score
                FROM public.chunks c
                JOIN public.documents d ON d.id = c.document_id
                CROSS JOIN q
                WHERE c.tenant_id = $1 AND c.tsv @@ q.query
                ORDER BY ts_rank(c.tsv, q.query) DESC, c.id
                LIMIT $4
                """,
                ctx.tenant.tenant_id,
                query,
                literal,
                candidate_limit,
            )
        except Exception as exc:
            raise RetrievalError(f"hybrid search failed: {type(exc).__name__}") from exc

        rows: dict[str, Any] = {}
        vector_ranking: list[RankedCandidate] = []
        text_ranking: list[RankedCandidate] = []
        for row in vector_rows:
            chunk_id = str(row["chunk_id"])
            rows[chunk_id] = row
            vector_ranking.append(RankedCandidate(chunk_id=chunk_id, score=float(row["score"])))
        for row in text_rows:
            chunk_id = str(row["chunk_id"])
            rows.setdefault(chunk_id, row)
            text_ranking.append(RankedCandidate(chunk_id=chunk_id, score=float(row["score"])))

        hits = reciprocal_rank_fusion(vector_ranking, text_ranking, k=RRF_K, limit=k)
        return [self._chunk(rows[hit.chunk_id], score=hit.score) for hit in hits]

    @staticmethod
    def _chunk(row: Any, *, score: float | None = None) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            source=row["source"],
            title=row["title"],
            text=row["text"],
            score=float(row["score"] if score is None else score),
            ordinal=int(row["ordinal"]),
        )


__all__ = [
    "HYBRID_CANDIDATES",
    "MAX_K",
    "RRF_K",
    "FusedHit",
    "PgVectorRetriever",
    "RankedCandidate",
    "SearchMode",
    "reciprocal_rank_fusion",
]
