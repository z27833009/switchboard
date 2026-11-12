"""Direct agent: small talk, no evidence and no lookups."""

from __future__ import annotations

from switchboard.agents.base import BaseAgent
from switchboard.agents.prompts import DIRECT_SYSTEM
from switchboard.core.models import AgentResult, AgentSpec, AgentTask
from switchboard.core.ports import AgentDeps


class DirectAgent(BaseAgent):
    spec = AgentSpec(
        name="direct",
        description=(
            "Small talk and questions about the assistant's capabilities; "
            "no lookups, never combined with another agent."
        ),
        cacheable=True,
        exclusive=True,
    )
    system_prompt = DIRECT_SYSTEM

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        response = await self.complete(self.build_messages(task), deps, task)
        return AgentResult(
            answer=response.text or "",
            usage=response.usage,
        )
