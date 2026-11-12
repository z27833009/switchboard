"""Docs agent: retrieve policy chunks and answer with document citations."""

from __future__ import annotations

from switchboard.agents.base import BaseAgent, partial_result
from switchboard.agents.citations import CitationRegistry
from switchboard.agents.prompts import DOCS_INSTRUCTIONS, DOCS_SYSTEM
from switchboard.core.errors import RetrievalError
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    Citation,
    EvidenceRef,
    Message,
    RetrievedChunk,
)
from switchboard.core.ports import AgentDeps

TOP_K = 5
SNIPPET_CHARS = 240


def document_block(chunk: RetrievedChunk, ref: str) -> str:
    return (
        f'<document ref="{ref}" id="{chunk.chunk_id}" source="{chunk.source}">\n'
        f"{chunk.text}\n"
        "</document>"
    )


class DocsAgent(BaseAgent):
    spec = AgentSpec(
        name="docs",
        description="Shop policies and other knowledge from the document index.",
        cacheable=True,
        needs=["retriever"],
    )
    system_prompt = DOCS_SYSTEM
    instructions = DOCS_INSTRUCTIONS

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        if deps.retriever is None:
            return partial_result("Document retrieval is not configured.")
        try:
            chunks = await deps.retriever.retrieve(task.query, deps.ctx, k=TOP_K)
        except RetrievalError as exc:
            return partial_result(f"I could not search the policy documents: {exc}")
        if not chunks:
            return partial_result(
                "I could not find any policy documents that answer this question."
            )

        messages = self.build_messages(task)
        messages.append(
            Message(
                role="user",
                content="Evidence:\n\n"
                + "\n\n".join(
                    document_block(chunk, f"d{index + 1}") for index, chunk in enumerate(chunks)
                ),
            )
        )
        response = await self.complete(messages, deps, task)

        registry = CitationRegistry()
        evidence: list[EvidenceRef] = []
        for index, chunk in enumerate(chunks):
            ref = f"d{index + 1}"
            registry.add(
                Citation(
                    kind="document",
                    ref=ref,
                    source_id=chunk.chunk_id,
                    snippet=chunk.text[:SNIPPET_CHARS],
                )
            )
            evidence.append(EvidenceRef(kind="document", ref=ref, source_id=chunk.chunk_id))
        answer = response.text or ""
        citations = registry.parse(answer)
        return AgentResult(
            answer=answer,
            citations=citations,
            evidence=evidence,
            usage=response.usage,
            partial=not citations,
        )
