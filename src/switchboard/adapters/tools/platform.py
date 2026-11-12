"""Platform tool sources: tools the platform serves, not a data source.

Design.md section 7.5. ``result_page@1`` lives next to the run-scoped result
store in ``switchboard.agents.results``; this module adds ``search_docs@1``,
which wraps the tenant's ``Retriever`` and is used by the agent loop
orchestrator. The tools are read-only, scoped to the run context the caller
passes and never take the tenant from arguments.
"""

from __future__ import annotations

import time
from typing import Any

from switchboard.core.errors import RetrievalError, ToolExecutionError, ToolValidationError
from switchboard.core.models import RunContext, ToolResult, ToolSpec
from switchboard.core.ports import Retriever

DEFAULT_K = 5
MAX_K = 10


def search_docs_spec() -> ToolSpec:
    return ToolSpec(
        name="search_docs",
        version="1",
        description=(
            "Search the tenant's policy documents and return the best matching "
            "chunks with their source. Use it for policy rules: returns, refunds, "
            "shipping, payments and installments, late-delivery compensation and "
            "seller onboarding."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "k": {"type": "integer", "minimum": 1, "maximum": MAX_K},
            },
            "required": ["query"],
        },
        risk="read",
        tenant_scoped=False,
    )


class SearchDocsToolSource:
    """In-process platform tool source for ``search_docs@1``.

    The call is not rewritten: the loop can search again with a different query,
    which is the loop's equivalent of the conditional rewrite in design.md
    section 9.3. Each call is traced by the retriever's ``retrieve`` span.
    """

    name = "platform_docs"

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever
        self._spec = search_docs_spec()

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        return [self._spec]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        if name != "search_docs@1":
            raise ToolExecutionError(f"unknown platform tool {name!r}")
        query = args.get("query")
        k = args.get("k", DEFAULT_K)
        if not isinstance(query, str) or not query.strip():
            raise ToolValidationError("query must be a non-empty string")
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
            raise ToolValidationError(f"k must be an integer between 1 and {MAX_K}")
        started = time.perf_counter()
        try:
            chunks = await self._retriever.retrieve(query, ctx, k, round=1)
        except RetrievalError as exc:
            raise ToolExecutionError(f"document search failed: {exc}") from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(
            tool="search_docs@1",
            version="1",
            total_rows=len(chunks),
            summary={
                "query": query,
                "k": k,
                "hits": len(chunks),
                "top_score": max((chunk.score for chunk in chunks), default=0.0),
            },
            rows=[
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "source": chunk.source,
                    "title": chunk.title,
                    "text": chunk.text,
                    "score": chunk.score,
                    "ordinal": chunk.ordinal,
                }
                for chunk in chunks
            ],
            duration_ms=duration_ms,
        )


__all__ = ["DEFAULT_K", "MAX_K", "SearchDocsToolSource", "search_docs_spec"]
