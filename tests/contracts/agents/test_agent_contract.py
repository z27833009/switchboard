"""Agent contract: direct, docs, data and clarify through the registry.

Each agent is loaded from the ``switchboard.agents`` entry points and run against
scripted model responses and in-memory evidence sources. The shared cases assert
the shape every agent returns (a non-empty answer, well-formed citations and
evidence, usage and the partial flag); the per-agent cases pin what makes that
agent different: no evidence for direct, document citations and text for docs,
tool citations and audit ids for data, one question and the clarification marker
for clarify.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import pytest
from tests.contracts.support import (
    InMemoryRetriever,
    ScriptedProvider,
    chunk,
    completion,
    policy_under_test,
    registered_agents,
    run_context,
)

from switchboard.core.errors import ToolPermissionError
from switchboard.core.models import (
    AgentResult,
    AgentTask,
    Budget,
    Citation,
    RunContext,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from switchboard.core.ports import Agent, AgentDeps, ToolSource

TOOL_SPEC = ToolSpec(
    name="orders_late",
    version="1",
    description="Late orders.",
    input_schema={"type": "object"},
)
TOOL_CALL = ToolCall(name="orders_late@1", arguments={}, call_id="call-1")


class StaticToolSource:
    name = "static_tools"

    def __init__(self, *, denied: bool = False) -> None:
        self.denied = denied
        self.calls: list[str] = []

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        del ctx
        return [TOOL_SPEC]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        del args, ctx
        self.calls.append(name)
        if self.denied:
            raise ToolPermissionError(f"{name} is not allowed for this tenant")
        return ToolResult(
            tool="orders_late@1",
            version="1",
            total_rows=500,
            summary={"late_orders": 500},
            rows=[{"order_id": f"o{index}"} for index in range(500)],
            audit_id="audit-1",
        )


def task(query: str) -> AgentTask:
    return AgentTask(query=query, budget=Budget(max_tool_steps=4, max_tokens=1024))


def deps(
    provider: ScriptedProvider,
    *,
    retriever: InMemoryRetriever | None = None,
    tool_sources: list[ToolSource] | None = None,
) -> AgentDeps:
    return AgentDeps(
        provider=provider,
        context_policy=policy_under_test(),
        ctx=run_context(allowed_tools={"orders_late@1", "result_page@1"}),
        retriever=retriever,
        tool_sources=list(tool_sources or []),
    )


@dataclass(frozen=True)
class AgentContract:
    name: str
    agent: Agent
    task: AgentTask
    deps: AgentDeps
    expected_partial: bool
    expected_citations: int
    expected_evidence: bool
    expected_needs_clarification: bool
    expected_answer: str


def build_direct() -> AgentContract:
    provider = ScriptedProvider([completion("Hello. I can answer order and policy questions.")])
    return AgentContract(
        name="direct",
        agent=registered_agents()["direct"](),
        task=task("hello"),
        deps=deps(provider),
        expected_partial=False,
        expected_citations=0,
        expected_evidence=False,
        expected_needs_clarification=False,
        expected_answer="Hello. I can answer order and policy questions.",
    )


def build_docs() -> AgentContract:
    provider = ScriptedProvider([completion("Returns take 15 calendar days [d1].")])
    return AgentContract(
        name="docs",
        agent=registered_agents()["docs"](),
        task=task("What is the return window?"),
        deps=deps(provider, retriever=InMemoryRetriever([chunk(0)])),
        expected_partial=False,
        expected_citations=1,
        expected_evidence=True,
        expected_needs_clarification=False,
        expected_answer="Returns take 15 calendar days [d1].",
    )


def build_data() -> AgentContract:
    provider = ScriptedProvider(
        [completion(tool_calls=[TOOL_CALL]), completion("There were 500 late orders [t1].")]
    )
    return AgentContract(
        name="data",
        agent=registered_agents()["data"](),
        task=task("How many orders were late?"),
        deps=deps(provider, tool_sources=[StaticToolSource()]),
        expected_partial=False,
        expected_citations=1,
        expected_evidence=True,
        expected_needs_clarification=False,
        expected_answer="There were 500 late orders [t1].",
    )


def build_clarify() -> AgentContract:
    provider = ScriptedProvider([completion("Which month do you mean?\nMarch or April?")])
    return AgentContract(
        name="clarify",
        agent=registered_agents()["clarify"](),
        task=task("How many were late?"),
        deps=deps(provider),
        expected_partial=False,
        expected_citations=0,
        expected_evidence=False,
        expected_needs_clarification=True,
        expected_answer="Which month do you mean?",
    )


BUILDERS = (build_direct, build_docs, build_data, build_clarify)


@pytest.fixture(params=BUILDERS, ids=["direct", "docs", "data", "clarify"])
def contract(request: pytest.FixtureRequest) -> AgentContract:
    builder = cast(Callable[[], AgentContract], request.param)
    return builder()


async def test_every_registered_agent_has_a_contract_case() -> None:
    assert set(registered_agents()) == {"direct", "docs", "data", "clarify"}


async def test_the_spec_matches_the_registered_name(contract: AgentContract) -> None:
    spec = contract.agent.spec
    assert spec.name == contract.name
    assert spec.description
    assert "\n" not in spec.description
    assert isinstance(spec.cacheable, bool)
    assert isinstance(spec.exclusive, bool)


async def test_the_result_has_the_agent_shape(contract: AgentContract) -> None:
    result = await contract.agent.run(contract.task, contract.deps)
    assert isinstance(result, AgentResult)
    assert result.answer == contract.expected_answer
    assert len(result.citations) == contract.expected_citations
    assert bool(result.evidence) is contract.expected_evidence
    assert result.partial is contract.expected_partial
    assert result.needs_clarification is contract.expected_needs_clarification
    assert result.usage.input_tokens >= 0
    assert result.usage.output_tokens >= 0
    for citation in result.citations:
        assert isinstance(citation, Citation)
        assert citation.kind in {"document", "tool"}
        assert citation.ref
        assert citation.source_id
        assert citation.snippet or citation.ref


def test_the_agent_specific_flags_follow_the_design() -> None:
    agents = registered_agents()
    assert agents["direct"].spec.cacheable is True
    assert agents["direct"].spec.exclusive is True
    assert agents["docs"].spec.needs == ["retriever"]
    assert agents["data"].spec.needs == ["tools"]
    assert agents["data"].spec.cacheable is False
    assert agents["clarify"].spec.exclusive is True
    assert agents["clarify"].spec.cacheable is False


async def test_docs_cites_the_retrieved_chunk_and_keeps_its_text() -> None:
    contract = build_docs()
    result = await contract.agent.run(contract.task, contract.deps)
    assert result.citations[0].kind == "document"
    assert result.citations[0].source_id == "chunk-0"
    assert result.evidence[0].kind == "document"
    assert result.evidence[0].text == chunk(0).text
    assert result.evidence[0].empty is False


async def test_docs_is_partial_when_nothing_is_retrieved() -> None:
    provider = ScriptedProvider([])
    result = await registered_agents()["docs"]().run(
        task("What is the return window?"),
        deps(provider, retriever=InMemoryRetriever([])),
    )
    assert result.partial is True
    assert result.citations == []
    # The conditional rewrite may run before the empty second round; no answer
    # call is made because there is no evidence to answer from.
    assert all(
        request.metadata.get("purpose") == "retriever.rewrite" for request in provider.requests
    )


async def test_data_records_the_tool_call_audit_id_and_evidence() -> None:
    contract = build_data()
    result = await contract.agent.run(contract.task, contract.deps)
    assert len(result.tool_calls) == 1
    record = result.tool_calls[0]
    assert record.tool == "orders_late@1"
    assert record.status == "ok"
    assert record.audit_id == "audit-1"
    assert result.citations[0].kind == "tool"
    assert result.citations[0].audit_id == "audit-1"
    assert result.evidence[0].kind == "tool"
    assert result.evidence[0].source_id == record.result_id


async def test_data_marks_a_denied_tool_partial() -> None:
    provider = ScriptedProvider(
        [completion(tool_calls=[TOOL_CALL]), completion("I cannot access that source.")]
    )
    result = await registered_agents()["data"]().run(
        task("How many orders were late?"),
        deps(provider, tool_sources=[StaticToolSource(denied=True)]),
    )
    assert result.tool_calls[0].status == "denied"
    assert result.partial is True
    assert result.citations == []


async def test_clarify_asks_one_question_and_sets_the_marker() -> None:
    contract = build_clarify()
    result = await contract.agent.run(contract.task, contract.deps)
    assert result.needs_clarification is True
    assert result.answer.count("?") == 1
    assert result.citations == []
    assert result.partial is False
