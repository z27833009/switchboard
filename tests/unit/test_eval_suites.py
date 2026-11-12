"""Eval suites share the production prompts and score a fake provider response."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from switchboard.adapters.orchestrator.langgraph_impl.orchestrator import (
    plan_schema as orchestrator_plan_schema,
)
from switchboard.agents.data import DataAgent
from switchboard.core.errors import ConfigError
from switchboard.core.models import (
    AgentSpec,
    AgentTask,
    CompletionRequest,
    CompletionResponse,
    EvalCase,
    EvalOutcome,
    ProviderCapabilities,
    ToolCall,
    Usage,
)
from switchboard.core.ports import EvalDeps
from switchboard.eval.datasets import load_cases
from switchboard.eval.manifest import load_tool_specs
from switchboard.eval.suites.routing import RoutingSuite, build_routing_request
from switchboard.eval.suites.toolcall import ToolCallSuite, build_toolcall_request
from switchboard.routing import plan_schema

REPO_ROOT = Path(__file__).resolve().parents[2]


class ScriptedProvider:
    name = "scripted"

    def __init__(self, response: CompletionResponse) -> None:
        self.response = response
        self.requests: list[CompletionRequest] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return self.response


def response(
    *,
    parsed: dict[str, Any] | None = None,
    tool_calls: list[ToolCall] | None = None,
) -> CompletionResponse:
    return CompletionResponse(
        text=None,
        parsed=parsed,
        tool_calls=tool_calls or [],
        usage=Usage(input_tokens=100, cached_input_tokens=5, output_tokens=10),
        model="fake",
        provider="fake",
        latency_ms=7,
    )


def specs() -> list[AgentSpec]:
    return [
        AgentSpec(name="data", description="orders", cacheable=False, needs=["tools"]),
        AgentSpec(name="direct", description="small talk", cacheable=True, exclusive=True),
        AgentSpec(name="docs", description="policies", cacheable=True, needs=["retriever"]),
    ]


def test_orchestrator_and_suite_use_the_same_plan_schema() -> None:
    assert orchestrator_plan_schema is plan_schema


def test_routing_request_is_the_supervisor_request() -> None:
    request = build_routing_request("How many orders?", specs(), 3)
    assert request.messages[0].content.startswith("You are Switchboard's routing supervisor.")
    assert "Question: How many orders?" in request.messages[1].content
    assert request.response_schema == plan_schema(["data", "direct", "docs"], 3)
    assert request.metadata["purpose"] == "supervisor.plan"
    assert request.temperature == 0.0


def test_toolcall_request_uses_the_data_agent_prefix() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    request = build_toolcall_request("Count orders.", tools)
    expected = DataAgent().build_messages(AgentTask(query="Count orders."))
    assert request.messages == expected
    assert [tool.wire_name for tool in request.tools] == sorted(tool.wire_name for tool in tools)
    assert request.metadata["purpose"] == "agent.data"


def test_datasets_load_with_the_expected_sizes_and_splits() -> None:
    routing = load_cases("routing", "v1", REPO_ROOT / "evals" / "datasets")
    toolcall = load_cases("toolcall", "v1", REPO_ROOT / "evals" / "datasets")
    assert len(routing) == 60
    assert len(toolcall) == 40
    assert sum(case.split == "dev" for case in routing) == 21
    assert sum(case.split == "test" for case in routing) == 39
    assert all(case.expected_agents for case in routing)
    assert all(case.split is None for case in toolcall)
    assert all(case.expected_tool for case in toolcall)


def test_manifest_has_the_five_olist_tools() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    assert [tool.wire_name for tool in tools] == [
        "describe_schema@1",
        "order_lookup@1",
        "orders_late@1",
        "orders_summary@1",
        "top_products@1",
    ]


async def test_routing_suite_executes_and_scores_a_valid_plan() -> None:
    suite = RoutingSuite(agent_specs=specs(), max_plan_size=3)
    provider = ScriptedProvider(response(parsed={"agents": ["docs", "data"], "reason": "both"}))
    case = EvalCase(id="r001", query="later and policy?", expected_agents=["data", "docs"])
    outcome = await suite.execute(case, EvalDeps(provider=provider))
    assert outcome.plan == ["docs", "data"]
    assert outcome.error is None
    assert outcome.details["reason"] == "both"
    assert suite.score(case, outcome).passed is True
    assert len(provider.requests) == 1


async def test_routing_suite_records_an_invalid_plan() -> None:
    suite = RoutingSuite(agent_specs=specs(), max_plan_size=3)
    provider = ScriptedProvider(response(parsed={"agents": ["bogus"], "reason": "x"}))
    case = EvalCase(id="r001", query="q", expected_agents=["data"])
    outcome = await suite.execute(case, EvalDeps(provider=provider))
    assert outcome.plan == []
    assert outcome.error is not None
    assert "unknown agents" in outcome.error
    assert suite.score(case, outcome).passed is False


async def test_toolcall_suite_sends_tools_and_scores_the_first_call() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    suite = ToolCallSuite(tools=tools)
    arguments = {"date_from": "2017-04-01", "date_to": "2017-04-30", "status": "invoiced"}
    provider = ScriptedProvider(
        response(
            tool_calls=[ToolCall(name="orders_summary@1", arguments=arguments, call_id="call-1")]
        )
    )
    case = EvalCase(
        id="t001",
        query="Count the invoiced orders in April 2017.",
        expected_tool="orders_summary@1",
        expected_args=arguments,
    )
    outcome = await suite.execute(case, EvalDeps(provider=provider))
    assert outcome.tool_calls == ["orders_summary@1"]
    assert outcome.tool_arguments == [arguments]
    assert len(provider.requests[0].tools) == 5
    assert suite.score(case, outcome).passed is True


def test_toolcall_suite_rejects_an_unknown_expected_tool() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    suite = ToolCallSuite(tools=tools)
    case = EvalCase(
        id="t999",
        query="q",
        expected_tool="missing@1",
        expected_args={},
    )
    with pytest.raises(ConfigError, match="not in the tool manifest"):
        suite.score(case, EvalOutcome(case_id="t999"))
