"""Toolcall suite: one data-agent completion per case, scored, tools not run.

The request uses the data agent's stable prefix and exactly the five Olist tool
specs from the manifest, sorted by wire name the way ``DataAgent`` sorts them.
Only the model's tool call is inspected; no database or MCP server is touched.
"""

from __future__ import annotations

from pathlib import Path

from switchboard.agents.data import DataAgent
from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    AgentTask,
    CompletionRequest,
    EvalCase,
    EvalOutcome,
    Score,
    ToolSpec,
)
from switchboard.core.ports import EvalDeps
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.scorers.toolcall import required_arguments, score_toolcall
from switchboard.eval.suite import SuiteContext


def build_toolcall_request(query: str, tools: list[ToolSpec]) -> CompletionRequest:
    messages = DataAgent().build_messages(AgentTask(query=query))
    return CompletionRequest(
        messages=messages,
        tools=sorted(tools, key=lambda tool: tool.wire_name),
        temperature=0.0,
        max_tokens=512,
        metadata={"purpose": "agent.data"},
    )


class ToolCallSuite:
    name = "toolcall"

    def __init__(
        self,
        *,
        tools: list[ToolSpec],
        dataset_root: Path = DEFAULT_DATASET_ROOT,
    ) -> None:
        self._tools = sorted(tools, key=lambda tool: tool.wire_name)
        self._by_name = {tool.wire_name: tool for tool in self._tools}
        self._dataset_root = Path(dataset_root)

    @classmethod
    def from_context(cls, context: SuiteContext) -> ToolCallSuite:
        if not context.tools:
            raise ConfigError("the toolcall suite needs the Olist tool manifest")
        return cls(tools=context.tools, dataset_root=context.dataset_root)

    def load(self, version: str) -> list[EvalCase]:
        return load_cases(self.name, version, self._dataset_root)

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        request = build_toolcall_request(case.query, self._tools)
        response = await deps.provider.complete(request)
        return EvalOutcome(
            case_id=case.id,
            answer=response.text,
            tool_calls=[call.name for call in response.tool_calls],
            tool_arguments=[call.arguments for call in response.tool_calls],
            details={
                "raw_tool_calls": [call.model_dump(mode="json") for call in response.tool_calls]
            },
            usage=response.usage,
            latency_ms=response.latency_ms,
        )

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        expected = case.expected_tool or ""
        spec = self._by_name.get(expected)
        if spec is None:
            raise ConfigError(
                f"case {case.id!r} expects {expected!r}, which is not in the tool manifest"
            )
        return score_toolcall(
            case,
            outcome,
            required=required_arguments(spec),
        )


__all__ = ["ToolCallSuite", "build_toolcall_request"]
