"""Toolcall suite: one data-agent completion per case, scored, tools not run.

The langgraph mode (the default, unchanged) uses the data agent's stable prefix
and exactly the five Olist tool specs from the manifest, sorted by wire name the
way ``DataAgent`` sorts them. The agent_loop mode sends the first step of the
agent loop instead: the production loop prompt and the tools the adapter exposes
(the tenant's read-only tools plus ``result_page@1`` and ``search_docs@1``), and
scores the first tool call. Only the model's tool call is inspected; no database
or MCP server is touched, and the loop is not continued.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from switchboard.agent_loop import loop_request
from switchboard.agents.data import DataAgent
from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    AgentTask,
    CompletionRequest,
    CompletionResponse,
    EvalCase,
    EvalOutcome,
    Score,
    ToolSpec,
)
from switchboard.core.ports import EvalDeps
from switchboard.eval.datasets import DEFAULT_DATASET_ROOT, load_cases
from switchboard.eval.scorers.toolcall import required_arguments, score_toolcall
from switchboard.eval.suite import SuiteContext
from switchboard.eval.suites.loop_tools import agent_loop_tool_specs

SUPERVISOR_MODE = "langgraph"
AGENT_LOOP_MODE = "agent_loop"
MODES = (SUPERVISOR_MODE, AGENT_LOOP_MODE)


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
        orchestrator: str = SUPERVISOR_MODE,
        tenant_tools: list[ToolSpec] | None = None,
        retriever_configured: bool = True,
    ) -> None:
        if orchestrator not in MODES:
            raise ConfigError(
                f"the toolcall suite cannot score the {orchestrator!r} orchestrator; "
                f"supported: {', '.join(MODES)}"
            )
        self._tools = sorted(tools, key=lambda tool: tool.wire_name)
        self._by_name = {tool.wire_name: tool for tool in self._tools}
        self._dataset_root = Path(dataset_root)
        self._orchestrator = orchestrator
        self._tenant_tools = list(tenant_tools or [])
        self._retriever_configured = retriever_configured

    @classmethod
    def from_context(cls, context: SuiteContext) -> ToolCallSuite:
        if not context.tools:
            raise ConfigError("the toolcall suite needs the Olist tool manifest")
        return cls(
            tools=context.tools,
            dataset_root=context.dataset_root,
            orchestrator=context.orchestrator,
            tenant_tools=context.tenant_tools,
            retriever_configured=context.retriever_configured,
        )

    def load(self, version: str) -> list[EvalCase]:
        return load_cases(self.name, version, self._dataset_root)

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        if self._orchestrator == AGENT_LOOP_MODE:
            return await self._execute_agent_loop(case, deps)
        response = await deps.provider.complete(build_toolcall_request(case.query, self._tools))
        return _outcome(case, response, {"mode": SUPERVISOR_MODE})

    async def _execute_agent_loop(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        tools = agent_loop_tool_specs(
            self._tenant_tools,
            retriever_configured=self._retriever_configured,
        )
        request = loop_request(
            case.query,
            tools,
            max_tokens=min(4096, deps.provider.capabilities().context_window // 2),
        )
        response = await deps.provider.complete(request)
        details: dict[str, Any] = {
            "mode": AGENT_LOOP_MODE,
            "tools": [spec.wire_name for spec in tools],
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments} for call in response.tool_calls
            ],
        }
        return _outcome(case, response, details)

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


def _outcome(
    case: EvalCase,
    response: CompletionResponse,
    details: dict[str, Any],
) -> EvalOutcome:
    return EvalOutcome(
        case_id=case.id,
        answer=response.text,
        tool_calls=[call.name for call in response.tool_calls],
        tool_arguments=[call.arguments for call in response.tool_calls],
        details={
            **details,
            "raw_tool_calls": [call.model_dump(mode="json") for call in response.tool_calls],
        },
        usage=response.usage,
        latency_ms=response.latency_ms,
    )


__all__ = [
    "AGENT_LOOP_MODE",
    "MODES",
    "SUPERVISOR_MODE",
    "ToolCallSuite",
    "build_toolcall_request",
]
