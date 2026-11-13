"""Eval suites share the production prompts and score a fake provider response."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from switchboard.adapters.orchestrator.langgraph_impl.orchestrator import (
    plan_schema as orchestrator_plan_schema,
)
from switchboard.agent_loop import AGENT_LOOP_SYSTEM
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
    ToolSpec,
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


def test_manifest_has_the_seven_olist_tools() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    assert [tool.wire_name for tool in tools] == [
        "add_order_note@1",
        "describe_schema@1",
        "order_lookup@1",
        "orders_late@1",
        "orders_summary@1",
        "query_readonly_sql@1",
        "top_products@1",
    ]
    by_name = {tool.wire_name: tool for tool in tools}
    assert by_name["add_order_note@1"].risk == "write"
    assert by_name["add_order_note@1"].tenant_scoped is True
    assert all(tool.risk == "read" for name, tool in by_name.items() if name != "add_order_note@1")


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


async def test_routing_suite_measures_only_the_agents_the_dataset_labels() -> None:
    """A new registered agent cannot move a frozen dataset version (T2.09)."""
    clarify = AgentSpec(
        name="clarify",
        description="asks one clarifying question",
        cacheable=False,
        exclusive=True,
    )
    suite = RoutingSuite(agent_specs=[*specs(), clarify], max_plan_size=3)
    cases = suite.load("v1")
    case = next(item for item in cases if item.id == "r003")
    provider = ScriptedProvider(response(parsed={"agents": ["data"], "reason": "orders"}))

    outcome = await suite.execute(case, EvalDeps(provider=provider))

    assert outcome.plan == ["data"]
    request = provider.requests[0]
    assert request.response_schema is not None
    assert request.response_schema["properties"]["agents"]["items"]["enum"] == [
        "data",
        "direct",
        "docs",
    ]
    assert "clarify" not in request.messages[1].content


async def test_routing_suite_records_an_invalid_plan() -> None:
    suite = RoutingSuite(agent_specs=specs(), max_plan_size=3)
    provider = ScriptedProvider(response(parsed={"agents": ["bogus"], "reason": "x"}))
    case = EvalCase(id="r001", query="q", expected_agents=["data"])
    outcome = await suite.execute(case, EvalDeps(provider=provider))
    assert outcome.plan == []
    assert outcome.error is not None
    assert "unknown agents" in outcome.error
    assert suite.score(case, outcome).passed is False


def tenant_tool_specs() -> list[ToolSpec]:
    all_tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    return [spec for spec in all_tools if spec.name in {"orders_summary", "orders_late"}]


async def test_routing_suite_agent_loop_derives_the_plan_from_the_decision() -> None:
    suite = RoutingSuite(
        agent_specs=specs(),
        max_plan_size=3,
        orchestrator="agent_loop",
        tenant_tools=tenant_tool_specs(),
    )
    provider = ScriptedProvider(
        response(tool_calls=[ToolCall(name="orders_summary@1", arguments={}, call_id="c1")])
    )
    case = EvalCase(id="r002", query="How many orders?", expected_agents=["data"])

    outcome = await suite.execute(case, EvalDeps(provider=provider))

    assert outcome.plan == ["data"]
    assert outcome.tool_calls == ["orders_summary@1"]
    assert outcome.details["mode"] == "agent_loop"
    assert outcome.details["tool_calls"] == [{"name": "orders_summary@1", "arguments": {}}]
    assert outcome.details["reason"].startswith("Derived from the tools")
    assert suite.score(case, outcome).passed is True

    request = provider.requests[0]
    assert request.messages[0].content == AGENT_LOOP_SYSTEM
    assert request.response_schema is None
    assert request.metadata["purpose"] == "agent.agent_loop"
    wire_names = [tool.wire_name for tool in request.tools]
    assert "orders_summary@1" in wire_names
    assert "result_page@1" in wire_names
    assert "search_docs@1" in wire_names
    # Sorted by wire name, like the production request.
    assert wire_names == sorted(wire_names)


async def test_routing_suite_agent_loop_scores_a_direct_decision() -> None:
    suite = RoutingSuite(
        agent_specs=specs(),
        max_plan_size=3,
        orchestrator="agent_loop",
        tenant_tools=tenant_tool_specs(),
    )
    provider = ScriptedProvider(response())
    case = EvalCase(id="r003", query="hello", expected_agents=["direct"])

    outcome = await suite.execute(case, EvalDeps(provider=provider))

    assert outcome.plan == ["direct"]
    assert outcome.tool_calls == []
    assert suite.score(case, outcome).passed is True


async def test_routing_suite_agent_loop_offers_read_only_tools_only() -> None:
    write = ToolSpec(
        name="delete_order",
        version="1",
        description="Delete an order.",
        input_schema={"type": "object"},
        risk="write",
    )
    suite = RoutingSuite(
        agent_specs=specs(),
        max_plan_size=3,
        orchestrator="agent_loop",
        tenant_tools=[*tenant_tool_specs(), write],
        retriever_configured=False,
    )
    provider = ScriptedProvider(response())
    case = EvalCase(id="r004", query="hello", expected_agents=["direct"])

    await suite.execute(case, EvalDeps(provider=provider))

    wire_names = [tool.wire_name for tool in provider.requests[0].tools]
    assert "delete_order@1" not in wire_names
    # No retriever means no search_docs@1, just as in a live run.
    assert "search_docs@1" not in wire_names
    assert "result_page@1" in wire_names


def test_routing_suite_rejects_an_unsupported_orchestrator() -> None:
    with pytest.raises(ConfigError, match="cannot score"):
        RoutingSuite(agent_specs=specs(), max_plan_size=3, orchestrator="sequential")


async def test_toolcall_suite_agent_loop_sends_the_first_loop_step() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    suite = ToolCallSuite(
        tools=tools,
        orchestrator="agent_loop",
        tenant_tools=[spec for spec in tools if spec.name in {"orders_summary", "orders_late"}],
    )
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

    assert outcome.details["mode"] == "agent_loop"
    assert outcome.tool_calls == ["orders_summary@1"]
    assert suite.score(case, outcome).passed is True
    request = provider.requests[0]
    assert request.messages[0].content == AGENT_LOOP_SYSTEM
    assert request.metadata["purpose"] == "agent.agent_loop"
    wire_names = [tool.wire_name for tool in request.tools]
    assert "orders_summary@1" in wire_names
    assert "result_page@1" in wire_names
    assert "search_docs@1" in wire_names
    assert wire_names == sorted(wire_names)


async def test_toolcall_suite_agent_loop_scores_a_docs_call_as_a_miss() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    suite = ToolCallSuite(tools=tools, orchestrator="agent_loop", tenant_tools=tools)
    provider = ScriptedProvider(
        response(tool_calls=[ToolCall(name="search_docs@1", arguments={}, call_id="c1")])
    )
    case = EvalCase(
        id="t001",
        query="Count orders.",
        expected_tool="orders_summary@1",
        expected_args={},
    )

    outcome = await suite.execute(case, EvalDeps(provider=provider))

    score = suite.score(case, outcome)
    assert score.metrics["tool_name_match"] == 0.0
    assert score.passed is False


def test_toolcall_suite_rejects_an_unsupported_orchestrator() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    with pytest.raises(ConfigError, match="cannot score"):
        ToolCallSuite(tools=tools, orchestrator="sequential")


async def test_toolcall_suite_sends_tools_and_scores_the_first_call() -> None:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    # The demo tenant does not allow the free-form SQL tool (T3.05), so a live
    # data agent never sees it and the suite must not offer it either. The write
    # tool stays out for the same reason: no model sees it without approval.
    tenant_tools = [
        tool for tool in tools if tool.wire_name not in {"query_readonly_sql@1", "add_order_note@1"}
    ]
    assert len(tenant_tools) == 5
    suite = ToolCallSuite(tools=tools, tenant_tools=tenant_tools)
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
