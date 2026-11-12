"""Markdown ingestion: chunk, embed, upsert with a content hash.

Design.md sections 9.1 and 9.4. Chunking is deterministic: headings split the
document, then a 512-word window with 64 words of overlap. Tokens are
approximated by whitespace words because core has no tokenizer dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from switchboard.core.ports import Embedder

MAX_WORDS = 512
OVERLAP_WORDS = 64
EMBED_BATCH = 32
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class IngestResult:
    added: int
    updated: int
    unchanged: int
    chunks_written: int
    index_version: int


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def document_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        match = HEADING.match(line)
        if match:
            return match.group(1).strip()
    return fallback


def split_sections(content: str) -> list[str]:
    matches = list(re.finditer(r"^#{1,6}\s+.*$", content, re.MULTILINE))
    sections: list[str] = []
    preamble = content[: matches[0].start()].strip() if matches else content.strip()
    if preamble:
        sections.append(preamble)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        section = content[match.start() : end].strip()
        if section:
            sections.append(section)
    return sections


def split_words(text: str, max_words: int = MAX_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]
    chunks: list[str] = []
    step = max(1, max_words - overlap)
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += step
    return chunks


def chunk_document(content: str) -> list[str]:
    chunks: list[str] = []
    for section in split_sections(content):
        chunks.extend(split_words(section))
    return chunks


async def embed_in_batches(
    embedder: Embedder, texts: list[str], batch_size: int = EMBED_BATCH
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(await embedder.embed(texts[start : start + batch_size]))
    return vectors


def vector_literal(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


async def ingest_directory(
    pool: asyncpg.Pool,
    embedder: Embedder,
    root: Path,
    tenant_id: str,
) -> IngestResult:
    tenant_exists = await pool.fetchval("SELECT 1 FROM public.tenants WHERE id = $1", tenant_id)
    if not tenant_exists:
        raise ValueError(f"unknown tenant {tenant_id!r}; run docker/postgres/init/03-documents.sql")

    files = sorted(
        path for path in root.glob("*.md") if path.is_file() and path.name.lower() != "readme.md"
    )
    if not files:
        raise ValueError(f"no markdown documents in {root}")

    added = updated = unchanged = chunks_written = changes = 0
    for path in files:
        content = path.read_text(encoding="utf-8")
        digest = content_hash(content)
        source = path.relative_to(root).as_posix()
        title = document_title(content, path.stem)

        existing = await pool.fetchrow(
            "SELECT id, content_hash FROM public.documents WHERE tenant_id = $1 AND source = $2",
            tenant_id,
            source,
        )
        if existing is not None and existing["content_hash"] == digest:
            unchanged += 1
            continue

        chunks = chunk_document(content)
        if not chunks:
            raise ValueError(f"{path} produced no chunks")
        vectors = await embed_in_batches(embedder, chunks)
        if len(vectors) != len(chunks):
            raise ValueError(f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks")
        for vector in vectors:
            if len(vector) != embedder.dimension:
                raise ValueError(
                    f"embedder returned dimension {len(vector)}, expected {embedder.dimension}"
                )

        async with pool.acquire() as connection, connection.transaction():
            if existing is not None:
                document_id = existing["id"]
                await connection.execute(
                    "UPDATE public.documents SET title = $2, content_hash = $3, "
                    "ingested_at = now() WHERE id = $1",
                    document_id,
                    title,
                    digest,
                )
                await connection.execute(
                    "DELETE FROM public.chunks WHERE document_id = $1", document_id
                )
                updated += 1
            else:
                document_id = await connection.fetchval(
                    "INSERT INTO public.documents (tenant_id, source, title, content_hash) "
                    "VALUES ($1, $2, $3, $4) RETURNING id",
                    tenant_id,
                    source,
                    title,
                    digest,
                )
                added += 1
            for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                await connection.execute(
                    "INSERT INTO public.chunks "
                    "(document_id, tenant_id, ordinal, text, embedding) "
                    "VALUES ($1, $2, $3, $4, $5::vector)",
                    document_id,
                    tenant_id,
                    ordinal,
                    chunk,
                    vector_literal(vector),
                )
            chunks_written += len(chunks)
            changes += 1

    if changes:
        version = await pool.fetchval(
            "UPDATE public.tenants SET doc_index_version = doc_index_version + 1 "
            "WHERE id = $1 RETURNING doc_index_version",
            tenant_id,
        )
    else:
        version = await pool.fetchval(
            "SELECT doc_index_version FROM public.tenants WHERE id = $1", tenant_id
        )
    if version is None:
        raise ValueError(f"unknown tenant {tenant_id!r}")
    return IngestResult(
        added=added,
        updated=updated,
        unchanged=unchanged,
        chunks_written=chunks_written,
        index_version=int(version),
    )
