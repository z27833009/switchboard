"""Docs agent: retrieve policy chunks and answer with document citations."""

from __future__ import annotations

from switchboard.agents.base import BaseAgent, partial_result
from switchboard.agents.citations import CitationRegistry
from switchboard.agents.prompts import DOCS_INSTRUCTIONS, DOCS_SYSTEM
from switchboard.agents.retrieval import retrieve_with_strategy, strategy_from_settings
from switchboard.core.errors import RetrievalError
from switchboard.core.guardrails import scan, wrap_data_block
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
from switchboard.core.pricing import add_cost

TOP_K = 5
SNIPPET_CHARS = 240


def document_block(chunk: RetrievedChunk, ref: str) -> str:
    """Render one retrieved chunk as a data block, flagged when suspicious."""
    return wrap_data_block(
        "document",
        chunk.text,
        attributes={"ref": ref, "id": chunk.chunk_id, "source": chunk.source},
        result=scan(chunk.text),
    )


class DocsAgent(BaseAgent):
    spec = AgentSpec(
        name="docs",
        description=(
            "Policy text from the document index: returns, refunds, shipping, "
            "payments and installments, late-delivery compensation, seller "
            "onboarding."
        ),
        cacheable=True,
        needs=["retriever"],
    )
    system_prompt = DOCS_SYSTEM
    instructions = DOCS_INSTRUCTIONS

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        if deps.retriever is None:
            return partial_result("Document retrieval is not configured.")
        strategy = strategy_from_settings("docs", deps.ctx.tenant.retrieval)
        try:
            run = await retrieve_with_strategy(
                deps.retriever,
                deps.provider,
                query=task.query,
                ctx=deps.ctx,
                strategy=strategy,
                k=TOP_K,
                history=task.history,
                embedding=task.query_embedding,
            )
        except RetrievalError as exc:
            return partial_result(f"I could not search the policy documents: {exc}")
        chunks = run.chunks
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
            evidence.append(
                EvidenceRef(
                    kind="document",
                    ref=ref,
                    source_id=chunk.chunk_id,
                    text=chunk.text,
                    empty=not chunk.text.strip(),
                )
            )
        answer = response.text or ""
        citations = registry.parse(answer)
        # The rewrite calls run on the same provider, so their tokens belong to
        # the run's accounting as well (AC-19a).
        usage = response.usage.plus(run.usage)
        cost = add_cost(response.cost_usd, run.cost_usd)
        return AgentResult(
            answer=answer,
            citations=citations,
            evidence=evidence,
            usage=usage,
            cost_usd=cost,
            partial=not citations,
        )
