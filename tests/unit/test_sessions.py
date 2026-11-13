"""Sessions, follow-ups and the generic clarification status (T2.09).

The HTTP tests use the memory store and a fake orchestrator, so no LM Studio,
PostgreSQL or MCP server is needed. The LangGraph tests use a scripted provider
and a recording agent; the model is never called. The April resolution itself is
the model's job and is checked live against LM Studio (see progress.md T2.09);
these tests prove the plumbing that makes it possible.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from tests.fakes import (
    AppHarness,
    FakeOrchestrator,
    MemoryRunStore,
    build_harness,
    make_provider,
    tenants_config,
)

from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.agents.clarify import ClarifyAgent
from switchboard.agents.data import DataAgent
from switchboard.agents.direct import DirectAgent
from switchboard.agents.docs import DocsAgent
from switchboard.api.step_events import RUN_FINISHED, StepEventPublisher
from switchboard.api.store import session_turns
from switchboard.core.config import Limits
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    ChatRequest,
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
    RunContext,
    TenantContext,
    Turn,
    Usage,
)
from switchboard.core.ports import AgentDeps
from switchboard.registry import AGENTS, Registry
from switchboard.routing import supervisor_prompt

MARCH = "How many orders were delivered late in March 2018?"
FAKE_ANSWER = "There were 7262 orders in March 2018 (t1)."


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


def run_context(allowed: set[str]) -> RunContext:
    return RunContext(
        run_id="run-1",
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents=allowed,
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


class ScriptedProvider:
    """Plans with the agents it was given; answers calls are questions."""

    name = "fake"

    def __init__(self, plan: list[str], question: str = "Which month do you mean?") -> None:
        self.plan = list(plan)
        self.question = question
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
        if request.response_schema is not None:
            return CompletionResponse(
                text=None,
                parsed={"agents": self.plan, "reason": "one source"},
                usage=Usage(input_tokens=5, output_tokens=2),
                model="fake",
                provider="fake",
                latency_ms=1,
            )
        return CompletionResponse(
            text=self.question,
            usage=Usage(input_tokens=7, output_tokens=3),
            model="fake",
            provider="fake",
            latency_ms=1,
        )


class RecordingAgent:
    """Records the task it was given and answers deterministically."""

    def __init__(self, name: str, *, needs_clarification: bool = False) -> None:
        self.spec = AgentSpec(
            name=name,
            description=f"{name} test agent",
            cacheable=False,
            exclusive=needs_clarification,
        )
        self._needs_clarification = needs_clarification
        self.tasks: list[AgentTask] = []

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        self.tasks.append(task)
        if self._needs_clarification:
            return AgentResult(
                answer="Which order do you mean?",
                usage=Usage(input_tokens=2, output_tokens=1),
                needs_clarification=True,
            )
        return AgentResult(
            answer=f"{self.spec.name} answer for {task.query}",
            usage=Usage(input_tokens=2, output_tokens=1),
        )


def make_orchestrator(provider: ScriptedProvider, agents: list[Any]) -> LangGraphOrchestrator:
    registry = Registry([FakeEntryPoint(agent.spec.name, AGENTS, agent) for agent in agents])
    return LangGraphOrchestrator(
        provider=provider,
        registry=registry,
        context_policy=ClearOldestToolResults(),
        limits=Limits(run_timeout_s=5.0),
    )


def test_supervisor_prompt_is_unchanged_when_history_is_absent() -> None:
    available = sorted(
        [DirectAgent.spec, DocsAgent.spec, DataAgent.spec], key=lambda spec: spec.name
    )
    plain = supervisor_prompt(available, "How many orders?")
    assert supervisor_prompt(available, "How many orders?", []) == plain
    assert supervisor_prompt(available, "How many orders?", None) == plain
    assert "Recent turns" not in plain
    assert plain.endswith("Question: How many orders?")


def test_supervisor_prompt_puts_history_before_the_question() -> None:
    history = [
        Turn(role="user", content=MARCH),
        Turn(role="assistant", content=FAKE_ANSWER),
    ]
    prompt = supervisor_prompt([DataAgent.spec], "And in April?", history)
    lines = prompt.splitlines()

    assert f"user: {MARCH}" in lines
    assert f"assistant: {FAKE_ANSWER}" in lines
    assert lines[-1] == "Question: And in April?"
    assert lines.index(f"assistant: {FAKE_ANSWER}") < len(lines) - 1
    assert "Resolve the new question" in prompt


async def test_and_in_april_follow_up_reaches_the_supervisor_and_the_agent() -> None:
    provider = ScriptedProvider(["data"])
    data = RecordingAgent("data")
    orchestrator = make_orchestrator(provider, [data])
    ctx = run_context({"data"})

    first = await orchestrator.run(ChatRequest(query=MARCH, tenant_id="default"), ctx)
    assert first.answer is not None
    assert "Recent turns" not in provider.requests[0].messages[1].content

    history = [
        Turn(role="user", content=MARCH),
        Turn(role="assistant", content=first.answer.answer),
    ]
    second = await orchestrator.run(
        ChatRequest(
            query="And in April?",
            tenant_id="default",
            session_id="s-1",
            history=history,
        ),
        ctx,
    )

    supervisor_text = provider.requests[1].messages[1].content
    assert f"user: {MARCH}" in supervisor_text
    assert f"assistant: {first.answer.answer}" in supervisor_text
    assert supervisor_text.endswith("Question: And in April?")
    assert second.plan is not None
    assert second.plan.agents == ["data"]
    assert data.tasks[0].history == []
    assert data.tasks[1].history == history
    assert second.status == "ok"


async def test_any_agent_can_end_a_run_as_needs_clarification() -> None:
    # No agent name is known to the orchestrator: a differently named agent
    # carrying the generic AgentResult marker gets the same status (AC-23c).
    provider = ScriptedProvider(["ask_more"])
    orchestrator = make_orchestrator(
        provider, [RecordingAgent("ask_more", needs_clarification=True)]
    )

    result = await orchestrator.run(
        ChatRequest(query="Which one?", tenant_id="default"), run_context({"ask_more"})
    )

    assert result.status == "needs_clarification"
    assert result.answer is not None
    assert result.answer.needs_clarification is True
    assert result.answer.answer == "Which order do you mean?"


async def test_the_clarify_agent_ends_the_run_as_needs_clarification() -> None:
    provider = ScriptedProvider(["clarify"], question="Which order or seller do you mean?")
    orchestrator = make_orchestrator(provider, [ClarifyAgent()])

    result = await orchestrator.run(
        ChatRequest(query="How many were late?", tenant_id="default"), run_context({"clarify"})
    )

    assert result.plan is not None
    assert result.plan.agents == ["clarify"]
    assert result.status == "needs_clarification"
    assert result.answer is not None
    assert result.answer.answer == "Which order or seller do you mean?"
    assert result.answer.citations == []
    assert result.attempts == 0
    assert result.gate is not None and result.gate.passed is True


def test_session_turns_are_user_then_assistant_oldest_first() -> None:
    rows = [
        {"query": "first", "answer": "first answer"},
        {"query": "second", "answer": "second answer"},
        {"query": "empty answer", "answer": ""},
    ]
    assert session_turns(rows) == [
        Turn(role="user", content="first"),
        Turn(role="assistant", content="first answer"),
        Turn(role="user", content="second"),
        Turn(role="assistant", content="second answer"),
    ]


async def post_chat(harness: AppHarness, query: str, **extra: Any) -> dict[str, Any]:
    response = await harness.client.post(
        "/chat", json={"query": query, "tenant_id": "default", **extra}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_the_second_request_in_a_session_receives_the_first_turn(
    harness: AppHarness,
) -> None:
    session = str(uuid.uuid4())
    first = await post_chat(harness, MARCH, session_id=session)
    second = await post_chat(harness, "And in April?", session_id=session)

    assert first["session_id"] == session
    assert second["session_id"] == session
    assert harness.orchestrator.histories[0] == []
    assert harness.orchestrator.histories[1] == [
        Turn(role="user", content=MARCH),
        Turn(role="assistant", content=first["answer"]),
    ]
    assert {row["session_id"] for row in harness.store.runs.values()} == {session}


async def test_turns_never_cross_a_tenant_or_a_session(harness: AppHarness) -> None:
    shared = str(uuid.uuid4())
    other = str(uuid.uuid4())

    first = await post_chat(harness, "first default question", session_id=shared)
    seller = await harness.client.post(
        "/chat",
        json={"query": "first seller question", "tenant_id": "seller_a", "session_id": shared},
    )
    assert seller.status_code == 200, seller.text
    await post_chat(harness, "second default question", session_id=shared)
    await post_chat(harness, "a different session", session_id=other)

    # Same session id under another tenant sees nothing from default.
    assert harness.orchestrator.histories[1] == []
    # The tenant's own earlier turn is there, oldest first.
    assert harness.orchestrator.histories[2] == [
        Turn(role="user", content="first default question"),
        Turn(role="assistant", content=first["answer"]),
    ]
    # A different session starts empty.
    assert harness.orchestrator.histories[3] == []


async def test_stored_history_is_authoritative_over_client_history(
    harness: AppHarness,
) -> None:
    session = str(uuid.uuid4())
    first = await post_chat(harness, MARCH, session_id=session)
    client_turns = [{"role": "user", "content": "a client invented turn"}]

    await post_chat(harness, "And in April?", session_id=session, history=client_turns)
    await post_chat(harness, "no session follow-up", history=client_turns)

    assert harness.orchestrator.histories[1] == [
        Turn(role="user", content=MARCH),
        Turn(role="assistant", content=first["answer"]),
    ]
    assert harness.orchestrator.histories[2] == [
        Turn(role="user", content="a client invented turn")
    ]


async def test_history_is_limited_to_the_configured_turn_count(
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    tracer_provider: TracerProvider,
    orchestrator: FakeOrchestrator,
) -> None:
    harness = build_harness(
        tenants=tenants_config(),
        store=store,
        publisher=publisher,
        provider=tracer_provider,
        orchestrator=orchestrator,
        history_turns=1,
    )
    try:
        session = str(uuid.uuid4())
        for index in range(3):
            await post_chat(harness, f"question {index}", session_id=session)
        await post_chat(harness, "final question", session_id=session)
    finally:
        await harness.client.aclose()

    assert harness.orchestrator.histories[3] == [
        Turn(role="user", content="question 2"),
        Turn(role="assistant", content=FAKE_ANSWER),
    ]


async def test_the_run_list_can_be_narrowed_to_one_session(harness: AppHarness) -> None:
    """The chat transcript reads stored runs of one conversation (T3.03)."""
    session = str(uuid.uuid4())
    first = await post_chat(harness, "first stored question", session_id=session)
    await post_chat(harness, "second stored question", session_id=session)
    await post_chat(harness, "a different session", session_id=str(uuid.uuid4()))
    unsessioned = await post_chat(harness, "no session at all")

    response = await harness.client.get(
        "/runs", params={"tenant_id": "default", "session_id": session, "limit": 50}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    # Newest first, like the unfiltered list; the page reverses it.
    assert [run["query"] for run in body["runs"]] == [
        "second stored question",
        "first stored question",
    ]
    assert {run["session_id"] for run in body["runs"]} == {session}
    assert body["runs"][-1]["run_id"] == first["run_id"]
    assert body["runs"][-1]["answer"] == first["answer"]

    # Another tenant never sees this conversation.
    cross = await harness.client.get(
        "/runs", params={"tenant_id": "seller_a", "session_id": session}
    )
    assert cross.status_code == 200
    assert cross.json()["count"] == 0

    # Without the filter the list is unchanged, including run rows that carry
    # no session id at all.
    everything = await harness.client.get("/runs", params={"tenant_id": "default"})
    listed = {run["run_id"] for run in everything.json()["runs"]}
    assert unsessioned["run_id"] in listed
    assert len(listed) == 4


async def test_a_clarify_run_finished_event_carries_the_status() -> None:
    store = MemoryRunStore()
    publisher = StepEventPublisher()
    provider = make_provider(publisher, store)
    orchestrator = FakeOrchestrator(
        tracer=provider.get_tracer("switchboard.test.clarify"),
        agents=("clarify",),
        status="needs_clarification",
        answer_text="Which month do you mean?",
    )
    harness = build_harness(
        tenants=tenants_config(),
        store=store,
        publisher=publisher,
        provider=provider,
        orchestrator=orchestrator,
    )
    try:
        response = await harness.client.post(
            "/chat/stream",
            json={"query": "How many were late?", "tenant_id": "default", "session_id": "s-1"},
        )
    finally:
        await harness.client.aclose()
        provider.shutdown()

    assert response.status_code == 200, response.text
    blocks = [block for block in response.text.strip().split("\n\n") if block.strip()]
    types = [
        next(line[len("event: ") :] for line in block.splitlines() if line.startswith("event: "))
        for block in blocks
    ]
    assert types[0] == "run.started"
    assert types[-1] == RUN_FINISHED
    final_line = next(line for line in blocks[-1].splitlines() if line.startswith("data: "))
    final = json.loads(final_line[len("data: ") :])
    assert final["status"] == "needs_clarification"
    assert final["answer"] == "Which month do you mean?"
    assert final["session_id"] == "s-1"
