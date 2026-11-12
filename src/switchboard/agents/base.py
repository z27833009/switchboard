"""Shared agent behaviour: stable prefix, context policy and the model call."""

from __future__ import annotations

from typing import ClassVar

from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    CompletionRequest,
    CompletionResponse,
    ContextBudget,
    Message,
    ToolSpec,
    Usage,
)
from switchboard.core.ports import AgentDeps


class BaseAgent:
    spec: AgentSpec
    system_prompt: ClassVar[str]
    instructions: ClassVar[str] = ""

    def build_messages(self, task: AgentTask) -> list[Message]:
        """Static system prompt, static instructions, history, query.

        Nothing dynamic before the history, so the provider prefix stays
        byte-identical across queries (design.md section 8.5).
        """
        messages = [Message(role="system", content=self.system_prompt)]
        if self.instructions:
            messages.append(Message(role="system", content=self.instructions))
        for turn in task.history:
            messages.append(Message(role=turn.role, content=turn.content))
        if task.feedback:
            messages.append(
                Message(
                    role="user",
                    content=(
                        "The previous answer was rejected for this reason: "
                        f"{task.feedback}. Answer again, fixing that problem."
                    ),
                )
            )
        messages.append(Message(role="user", content=task.query))
        return messages

    def context_budget(self, task: AgentTask, deps: AgentDeps) -> ContextBudget:
        return ContextBudget(
            context_window=deps.provider.capabilities().context_window,
            max_tokens=task.budget.max_tokens,
            fill_ratio=deps.context_fill,
        )

    async def complete(
        self,
        messages: list[Message],
        deps: AgentDeps,
        task: AgentTask,
        tools: list[ToolSpec] | None = None,
    ) -> CompletionResponse:
        fitted_result = deps.context_policy.fit(messages, self.context_budget(task, deps))
        fitted = fitted_result.messages
        request = CompletionRequest(
            messages=fitted,
            tools=sorted(tools or [], key=lambda tool: tool.wire_name),
            temperature=0.0,
            max_tokens=task.budget.max_tokens,
            metadata={
                "purpose": f"agent.{self.spec.name}",
                "run_id": deps.ctx.run_id,
                "context_tokens_estimated": fitted_result.estimated_tokens,
                "context_compacted": fitted_result.compacted,
                "context_cleared_results": fitted_result.cleared_results,
            },
        )
        return await deps.provider.complete(request)


def zero_usage() -> Usage:
    return Usage(input_tokens=0, output_tokens=0)


def partial_result(answer: str) -> AgentResult:
    return AgentResult(answer=answer, usage=zero_usage(), partial=True)
