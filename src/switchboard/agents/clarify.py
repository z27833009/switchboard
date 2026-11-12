"""Clarify agent: ask one question instead of guessing (design.md 6.4).

The agent is registered like any other: one class with a spec plus one entry
point. Nothing in core or the orchestrator names it. The mark that ends the run
with status ``needs_clarification`` is the generic
``AgentResult.needs_clarification`` flag, set here by the agent itself.
"""

from __future__ import annotations

from switchboard.agents.base import BaseAgent
from switchboard.agents.prompts import CLARIFY_SYSTEM
from switchboard.core.models import AgentResult, AgentSpec, AgentTask
from switchboard.core.ports import AgentDeps

# Used only when the model returns no usable line; it reads as a clarification,
# never as a factual answer.
FALLBACK_QUESTION = (
    "Could you say more about which order, product, seller or policy topic you mean?"
)
MAX_QUESTION_CHARS = 300


def clean_question(text: str | None) -> str:
    """Keep the first non-empty line, strip quoting and trailing whitespace."""
    for line in (text or "").splitlines():
        candidate = line.strip().strip("\"'`").strip()
        if candidate:
            candidate = candidate[:MAX_QUESTION_CHARS].rstrip()
            if "?" not in candidate:
                candidate = candidate.rstrip(". ") + "?"
            return candidate
    return ""


class ClarifyAgent(BaseAgent):
    spec = AgentSpec(
        name="clarify",
        description=(
            "Asks one clarifying question when the request is too vague to tell "
            "which order, seller, product, time window or policy it is about; "
            "never combined with another agent."
        ),
        cacheable=False,
        exclusive=True,
    )
    system_prompt = CLARIFY_SYSTEM

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        response = await self.complete(self.build_messages(task), deps, task)
        question = clean_question(response.text) or FALLBACK_QUESTION
        return AgentResult(
            answer=question,
            usage=response.usage,
            cost_usd=response.cost_usd,
            needs_clarification=True,
        )


__all__ = ["ClarifyAgent", "clean_question"]
