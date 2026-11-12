"""pgvector retriever (Phase 1: cosine similarity over an HNSW index)."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from switchboard.core.errors import RetrievalError
from switchboard.core.models import RetrievedChunk, RunContext
from switchboard.core.ports import Embedder

MAX_K = 50


class PgVectorRetriever:
    name: str

    def __init__(self, *, pool: asyncpg.Pool, embedder: Embedder, name: str = "pgvector") -> None:
        self.name = name
        self._pool = pool
        self._embedder = embedder

    async def retrieve(self, query: str, ctx: RunContext, k: int = 5) -> list[RetrievedChunk]:
        if k < 1 or k > MAX_K:
            raise RetrievalError(f"k must be between 1 and {MAX_K}")
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

        return [
            RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                document_id=str(row["document_id"]),
                source=row["source"],
                title=row["title"],
                text=row["text"],
                score=float(row["score"]),
                ordinal=int(row["ordinal"]),
            )
            for row in rows
        ]
