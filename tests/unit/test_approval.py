"""Human-in-the-loop approval in the LangGraph path (T3.01, AC-15a, AC-15b).

A scripted provider and a stub tool source keep the tests free of LM Studio and
PostgreSQL. The assertions are what a caller can observe: the paused status and
its pending call, whether the tool ran, the final status and the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from switchboard.adapters.orchestrator.agent_loop import AgentLoopOrchestrator
from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.agents.data import DataAgent
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ApprovalUnavailable
from switchboard.core.models import (
    ApprovalDecision,
    ChatRequest,
    CompletionResponse,
    ProviderCapabilities,
    RunContext,
    TenantContext,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)
from switchboard.registry import AGENTS, Registry

NOTE_WRITE = ToolSpec(
    name="add_order_note",
    version="1",
    description="Append a note to one order. Needs approval.",
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "string"}, "note": {"type": "string"}},
    },
    risk="write",
)

READ_TOOL = ToolSpec(
    name="orders_summary",
    version="1",
    description="Count orders.",
    input_schema={"type": "object", "properties": {}},
)

NOTE_CALL = ToolCall(
    name="add_order_note@1",
    arguments={"order_id": "abc123", "note": "Customer asked for a later delivery."},
    call_id="call-note",
)
SUMMARY_CALL = ToolCall(
    name="orders_summary@1", arguments={"status": "delivered"}, call_id="call-summary"
)


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


class ScriptedProvider:
    """Plans with one agent, calls one tool once, then answers."""

    name = "fake"

    def __init__(
        self,
        *,
        tool_calls: list[ToolCall],
        answer: str = "Done [t1].",
        answers: list[str] | None = None,
    ) -> None:
        self._tool_calls = tool_calls
        self._answers = list(answers) if answers else [answer]
        self.requests: list[Any] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: Any) -> CompletionResponse:
        self.requests.append(request)
        usage = Usage(input_tokens=10, output_tokens=2)
        if request.response_schema is not None:
            return CompletionResponse(
                text=None,
                parsed={"agents": ["data"], "reason": "scripted plan"},
                usage=usage,
                model="fake",
                provider="fake",
                latency_ms=1,
            )
        has_tool_message = any(message.role == "tool" for message in request.messages)
        if not has_tool_message and self._tool_calls:
            return CompletionResponse(
                text=None,
                tool_calls=[call.model_copy() for call in self._tool_calls],
                usage=usage,
                model="fake",
                provider="fake",
                latency_ms=1,
            )
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return CompletionResponse(
            text=answer,
            usage=usage,
            model="fake",
            provider="fake",
            latency_ms=1,
        )


class NoteToolSource:
    """Serves the write tool and records every execution."""

    name = "stub_notes"

    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs = specs if specs is not None else [NOTE_WRITE]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        self.calls.append((name, dict(args)))
        return ToolResult(
            tool=name,
            version="1",
            total_rows=1,
            summary={"inserted": True, "note_id": "note-1"},
            rows=[{"order_id": args.get("order_id"), "note_id": "note-1"}],
        )


def context(*, ask: set[str] | None = None) -> RunContext:
    return RunContext(
        run_id="run-approval",
        tenant=TenantContext(
            tenant_id="seller_a",
            allowed_agents={"data"},
            allowed_tools={"orders_summary@1", "add_order_note@1"},
            ask_tools=ask if ask is not None else {"add_order_note@1"},
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def langgraph(tool_source: Any, provider: ScriptedProvider) -> LangGraphOrchestrator:
    registry = Registry([FakeEntryPoint("data", AGENTS, DataAgent())])
    return LangGraphOrchestrator(
        provider=provider,
        registry=registry,
        context_policy=ClearOldestToolResults(),
        tool_sources=[tool_source],
        tracer=TracerProvider().get_tracer("switchboard.test.approval"),
    )


async def test_a_write_tool_pauses_before_it_runs_then_runs_once() -> None:
    source = NoteToolSource()
    orchestrator = langgraph(source, ScriptedProvider(tool_calls=[NOTE_CALL]))

    paused = await orchestrator.run(
        ChatRequest(query="Add a note to order abc123", tenant_id="seller_a"),
        context(),
    )

    assert paused.status == "waiting_approval"
    assert paused.pending_approval is not None
    assert paused.pending_approval.tool == "add_order_note@1"
    assert paused.pending_approval.arguments == NOTE_CALL.arguments
    assert "write" in paused.pending_approval.reason
    assert source.calls == []

    result = await orchestrator.resume(
        "run-approval",
        ApprovalDecision(request_id=paused.pending_approval.request_id, approved=True),
    )

    assert result.status == "ok"
    assert result.pending_approval is None
    assert source.calls == [("add_order_note@1", NOTE_CALL.arguments)]
    assert result.answer is not None and result.answer.answer == "Done [t1]."
    assert result.answer.tool_calls[0].status == "ok"

    # The thread is gone once the run finished, so a second approval cannot run
    # the tool again (the API also refuses it with 409).
    with pytest.raises(ApprovalUnavailable):
        await orchestrator.resume(
            "run-approval",
            ApprovalDecision(request_id="whatever", approved=True),
        )
    assert len(source.calls) == 1


async def test_an_ask_tool_pauses_even_when_the_manifest_is_read_only() -> None:
    source = NoteToolSource(specs=[READ_TOOL])
    orchestrator = langgraph(source, ScriptedProvider(tool_calls=[SUMMARY_CALL]))

    paused = await orchestrator.run(
        ChatRequest(query="How many delivered orders?", tenant_id="seller_a"),
        context(ask={"orders_summary@1"}),
    )

    assert paused.status == "waiting_approval"
    assert paused.pending_approval is not None
    assert paused.pending_approval.tool == "orders_summary@1"
    assert "tools.ask" in paused.pending_approval.reason
    assert source.calls == []


async def test_a_read_tool_runs_without_a_pause() -> None:
    source = NoteToolSource(specs=[READ_TOOL])
    orchestrator = langgraph(source, ScriptedProvider(tool_calls=[SUMMARY_CALL]))

    result = await orchestrator.run(
        ChatRequest(query="How many delivered orders?", tenant_id="seller_a"),
        context(ask=set()),
    )

    assert result.status == "ok"
    assert result.pending_approval is None
    assert source.calls == [("orders_summary@1", SUMMARY_CALL.arguments)]


async def test_a_gate_retry_does_not_execute_the_approved_call_twice() -> None:
    """The gate retries an uncited answer; the approved write must not repeat."""
    source = NoteToolSource()
    provider = ScriptedProvider(
        tool_calls=[NOTE_CALL],
        answers=["The note is added.", "The note is added [t1]."],
    )
    orchestrator = langgraph(source, provider)
    paused = await orchestrator.run(
        ChatRequest(query="Add a note to order abc123", tenant_id="seller_a"),
        context(),
    )
    assert paused.pending_approval is not None

    result = await orchestrator.resume(
        "run-approval",
        ApprovalDecision(request_id=paused.pending_approval.request_id, approved=True),
    )

    assert result.status == "ok", result.errors
    # One execution, one approval: the retry reuses the stored result.
    assert len(source.calls) == 1
    assert result.answer is not None and result.answer.answer == "The note is added [t1]."


async def test_reject_ends_the_run_and_executes_nothing() -> None:
    source = NoteToolSource()
    orchestrator = langgraph(source, ScriptedProvider(tool_calls=[NOTE_CALL]))
    paused = await orchestrator.run(
        ChatRequest(query="Add a note to order abc123", tenant_id="seller_a"),
        context(),
    )
    assert paused.pending_approval is not None

    result = await orchestrator.resume(
        "run-approval",
        ApprovalDecision(request_id=paused.pending_approval.request_id, approved=False),
    )

    assert result.status == "rejected"
    assert source.calls == []
    assert result.answer is not None
    assert "add_order_note@1 was rejected" in result.answer.answer
    assert [error.kind for error in result.errors] == ["ToolApprovalRejected"]
    # A rejected run is a decision, not a gate failure.
    assert result.gate is None


async def test_resume_without_a_paused_graph_is_unavailable() -> None:
    orchestrator = langgraph(NoteToolSource(), ScriptedProvider(tool_calls=[NOTE_CALL]))
    with pytest.raises(ApprovalUnavailable):
        await orchestrator.resume(
            "run-that-never-paused",
            ApprovalDecision(request_id="missing", approved=True),
        )


async def test_the_agent_loop_never_sees_write_or_ask_tools() -> None:
    source = NoteToolSource(specs=[NOTE_WRITE, READ_TOOL])
    provider = ScriptedProvider(tool_calls=[SUMMARY_CALL])
    registry = Registry([FakeEntryPoint("data", AGENTS, DataAgent())])
    orchestrator = AgentLoopOrchestrator(
        provider=provider,
        registry=registry,
        context_policy=ClearOldestToolResults(),
        tool_sources=[source],
        tracer=TracerProvider().get_tracer("switchboard.test.approval"),
    )

    result = await orchestrator.run(
        ChatRequest(query="How many delivered orders?", tenant_id="seller_a"),
        context(ask={"orders_summary@1"}),
    )

    offered = {
        spec.wire_name for request in provider.requests for spec in getattr(request, "tools", [])
    }
    assert "add_order_note@1" not in offered
    assert "orders_summary@1" not in offered
    # The loop treated the named tool as unknown and never executed it.
    assert source.calls == []
    assert result.status in {"partial", "ok"}
