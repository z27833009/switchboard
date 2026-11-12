"""Tracing helpers: row mapping, the 64 KB tool result event, usage attributes,
traceparent propagation and the flush helpers (design.md 12.1 and 12.2)."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, cast

import openai
import pytest
from openai.types.chat import ChatCompletion
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.adapters.providers.openai_compat import OpenAICompatibleProvider
from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.core.config import Limits
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.errors import ProviderTransientError
from switchboard.core.models import (
    AgentResult,
    AgentSpec,
    AgentTask,
    ChatRequest,
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderCapabilities,
    RunContext,
    TenantContext,
    Usage,
)
from switchboard.core.ports import AgentDeps
from switchboard.registry import AGENTS, Registry
from switchboard.tracing import (
    TOOL_RESULT_ATTRIBUTE,
    TOOL_RESULT_MAX_BYTES,
    add_tool_result_event,
    extract_traceparent,
    force_flush,
    force_flush_async,
    inject_traceparent,
    span_to_row,
)


@dataclass
class Collector:
    provider: TracerProvider
    exporter: InMemorySpanExporter
    tracer: trace.Tracer

    def finished(self) -> tuple[ReadableSpan, ...]:
        return self.exporter.get_finished_spans()

    def by_name(self, name: str) -> ReadableSpan:
        for span in self.finished():
            if span.name == name:
                return span
        raise AssertionError(f"no span named {name!r} in {[s.name for s in self.finished()]}")

    def names(self) -> list[str]:
        return [span.name for span in self.finished()]


@pytest.fixture
def collector() -> Collector:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Collector(provider=provider, exporter=exporter, tracer=provider.get_tracer("test"))


def event_attributes(event: Any) -> dict[str, Any]:
    return dict(event.attributes or {})


def attributes_of(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def chat_completion(
    *,
    model: str = "qwen3-8b",
    content: str = "OK.",
    tool_calls: list[dict[str, Any]] | None = None,
    cached_input_tokens: int = 0,
) -> ChatCompletion:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
        message["content"] = None
    usage: dict[str, Any] = {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
    }
    if cached_input_tokens:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_input_tokens}
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": usage,
        }
    )


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses

    async def create(self, **kwargs: Any) -> ChatCompletion:
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return cast(ChatCompletion, item)


def provider_with(collector: Collector, responses: list[Any]) -> OpenAICompatibleProvider:
    completions = FakeCompletions(responses)
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return OpenAICompatibleProvider(
        name="lmstudio",
        model="qwen3-8b",
        base_url="http://test.invalid/v1",
        api_key="test",
        capabilities=ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        ),
        client=client,
        tracer=collector.tracer,
    )


def test_span_to_row_maps_ids_times_status_and_json(collector: Collector) -> None:
    run_id = str(uuid.uuid4())
    with collector.tracer.start_as_current_span(
        "execute_tool orders_summary@1",
        kind=trace.SpanKind.CLIENT,
        attributes={"switchboard.run_id": run_id, "switchboard.tool.total_rows": 3},
    ) as span:
        add_tool_result_event(span, {"rows": [{"id": 1}], "total_rows": 1})

    row = span_to_row(collector.finished()[0])
    (
        span_id,
        trace_id,
        parent_span_id,
        row_run_id,
        name,
        kind,
        start_time,
        end_time,
        status,
        attributes,
        events,
    ) = row

    assert isinstance(span_id, str) and len(span_id) == 16
    assert isinstance(trace_id, str) and len(trace_id) == 32
    assert parent_span_id is None
    assert row_run_id == uuid.UUID(run_id)
    assert name == "execute_tool orders_summary@1"
    assert kind == "client"
    assert start_time.tzinfo is not None
    assert end_time >= start_time
    assert status == "unset"
    assert json.loads(attributes)["switchboard.tool.total_rows"] == 3
    stored_events = json.loads(events)
    assert stored_events[0]["name"] == "tool.result"
    assert TOOL_RESULT_ATTRIBUTE in stored_events[0]["attributes"]


def test_span_to_row_parent_span_id_links_a_child(collector: Collector) -> None:
    with (
        collector.tracer.start_as_current_span("parent"),
        collector.tracer.start_as_current_span("child"),
    ):
        pass

    parent_id = span_to_row(collector.by_name("parent"))[0]
    child_parent = span_to_row(collector.by_name("child"))[2]
    assert child_parent == parent_id


def test_non_uuid_run_id_is_stored_as_null(collector: Collector) -> None:
    with collector.tracer.start_as_current_span("run", attributes={"switchboard.run_id": "run-1"}):
        pass
    assert span_to_row(collector.finished()[0])[3] is None


def test_tool_result_event_is_capped_at_64_kb(collector: Collector) -> None:
    payload = {"rows": [{"note": "x" * 1000} for _ in range(100)]}
    original_size = len(json.dumps(payload).encode("utf-8"))
    with collector.tracer.start_as_current_span("execute_tool top_products@1") as span:
        add_tool_result_event(span, payload)

    event = collector.finished()[0].events[0]
    assert event.name == "tool.result"
    stored = event_attributes(event)
    text = stored[TOOL_RESULT_ATTRIBUTE]
    assert isinstance(text, str)
    assert len(text.encode("utf-8")) <= TOOL_RESULT_MAX_BYTES
    assert stored["switchboard.tool.result.truncated"] is True
    assert stored["switchboard.tool.result.bytes"] == original_size


def test_small_tool_result_is_not_truncated(collector: Collector) -> None:
    with collector.tracer.start_as_current_span("execute_tool describe_schema@1") as span:
        add_tool_result_event(span, {"summary": {"tables": 9}})

    event = collector.finished()[0].events[0]
    stored = event_attributes(event)
    assert stored["switchboard.tool.result.truncated"] is False
    assert json.loads(stored[TOOL_RESULT_ATTRIBUTE]) == {"summary": {"tables": 9}}


def test_traceparent_injection_and_extraction_continue_the_trace(collector: Collector) -> None:
    with collector.tracer.start_as_current_span("agent.data"):
        header = inject_traceparent()
    assert header.startswith("00-")
    assert len(header.split("-")) == 4

    child = collector.tracer.start_span(
        "execute_tool orders_summary@1", context=extract_traceparent(header)
    )
    child.end()
    parent_trace = format(collector.finished()[0].context.trace_id, "032x")
    child_trace = format(child.get_span_context().trace_id, "032x")
    assert child_trace == parent_trace


def test_missing_traceparent_starts_a_new_trace(collector: Collector) -> None:
    parent = extract_traceparent(None)
    child = collector.tracer.start_span("orphan", context=parent)
    child.end()
    assert child.get_span_context().trace_id != 0


def test_force_flush_wrappers_report_success_failure_and_timeout() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    assert force_flush(0.5, provider=provider) is True

    class Broken:
        def force_flush(self, timeout_millis: int) -> bool:
            raise RuntimeError("database down")

    assert force_flush(0.5, provider=Broken()) is False

    class Slow:
        def force_flush(self, timeout_millis: int) -> bool:
            import time

            time.sleep(0.2)
            return True

    async def run() -> bool:
        return await force_flush_async(0.01, provider=Slow())

    assert asyncio.run(run()) is False


async def test_chat_span_records_usage_strategy_and_context(collector: Collector) -> None:
    provider = provider_with(collector, [chat_completion(cached_input_tokens=4)])
    response = await provider.complete(
        CompletionRequest(
            messages=[Message(role="user", content="hi")],
            metadata={
                "run_id": str(uuid.uuid4()),
                "context_tokens_estimated": 120,
                "context_compacted": True,
                "context_cleared_results": 2,
            },
        )
    )
    assert response.text == "OK."

    span = collector.by_name("chat qwen3-8b")
    attributes = dict(span.attributes or {})
    assert span.kind is trace.SpanKind.CLIENT
    assert attributes["gen_ai.operation.name"] == "chat"
    assert attributes["gen_ai.system"] == "lmstudio"
    assert attributes["gen_ai.request.model"] == "qwen3-8b"
    assert attributes["gen_ai.usage.input_tokens"] == 10
    assert attributes["gen_ai.usage.output_tokens"] == 2
    assert attributes["switchboard.usage.cached_input_tokens"] == 4
    assert attributes["switchboard.structured_output.strategy"] == "none"
    assert attributes["switchboard.context.tokens_estimated"] == 120
    assert attributes["switchboard.context.compacted"] is True
    assert attributes["switchboard.context.cleared_results"] == 2
    assert attributes["switchboard.latency_ms"] == response.latency_ms


async def test_chat_span_records_an_exception_event(collector: Collector) -> None:
    error = openai.APIConnectionError(request=cast(Any, None))
    provider = provider_with(collector, [error])
    with pytest.raises(ProviderTransientError):
        await provider.complete(CompletionRequest(messages=[Message(role="user", content="hi")]))

    span = collector.by_name("chat qwen3-8b")
    assert span.status.status_code is trace.StatusCode.ERROR
    assert [event.name for event in span.events] == ["exception"]


class PlanProvider:
    name = "fake"

    def __init__(self) -> None:
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
        return CompletionResponse(
            text=None,
            parsed={"agents": ["direct"], "reason": "small talk"},
            usage=Usage(input_tokens=5, output_tokens=2),
            model="fake",
            provider="fake",
            latency_ms=1,
        )


class StubAgent:
    spec = AgentSpec(name="direct", description="small talk", cacheable=True)

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def run(self, task: AgentTask, deps: AgentDeps) -> AgentResult:
        if self.error is not None:
            raise self.error
        return AgentResult(answer="hello", usage=Usage(input_tokens=1, output_tokens=1))


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: Any

    def load(self) -> Any:
        return self.value


def run_context(run_id: str) -> RunContext:
    return RunContext(
        run_id=run_id,
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents={"direct"},
            allowed_tools=set(),
            provider="fake",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


def orchestrator_with(collector: Collector, agent: StubAgent) -> LangGraphOrchestrator:
    return LangGraphOrchestrator(
        provider=PlanProvider(),
        registry=Registry([FakeEntryPoint("direct", AGENTS, agent)]),
        context_policy=ClearOldestToolResults(),
        limits=Limits(run_timeout_s=5.0),
        tracer=collector.tracer,
    )


async def test_orchestrator_emits_run_supervisor_and_agent_spans(collector: Collector) -> None:
    run_id = str(uuid.uuid4())
    result = await orchestrator_with(collector, StubAgent()).run(
        ChatRequest(query="hello", tenant_id="default"), run_context(run_id)
    )
    assert result.status == "ok"
    assert sorted(collector.names()) == ["agent.direct", "run", "supervisor.plan"]

    run = collector.by_name("run")
    run_attributes = attributes_of(run)
    assert run.kind is trace.SpanKind.SERVER
    assert run_attributes["switchboard.tenant_id"] == "default"
    assert run_attributes["switchboard.run_id"] == run_id
    assert run_attributes["switchboard.orchestrator"] == "langgraph"
    assert run_attributes["switchboard.plan"] == "direct"
    assert run_attributes["switchboard.cache.hit"] is False
    assert run_attributes["switchboard.attempts"] == 0
    assert run_attributes["switchboard.status"] == "ok"

    supervisor = attributes_of(collector.by_name("supervisor.plan"))
    assert supervisor["switchboard.plan"] == "direct"
    assert supervisor["switchboard.plan.size"] == 1
    assert supervisor["switchboard.plan.reason"] == "small talk"
    assert supervisor["switchboard.plan.fallback"] is False

    agent = attributes_of(collector.by_name("agent.direct"))
    assert agent["switchboard.agent"] == "direct"
    assert agent["switchboard.partial"] is False
    assert agent["switchboard.citations"] == 0


async def test_agent_failure_emits_an_error_event_and_partial(collector: Collector) -> None:
    result = await orchestrator_with(
        collector, StubAgent(error=RuntimeError("tool loop broke"))
    ).run(ChatRequest(query="hello", tenant_id="default"), run_context(str(uuid.uuid4())))
    assert result.status == "partial"
    agent = collector.by_name("agent.direct")
    assert attributes_of(agent)["switchboard.partial"] is True
    assert [event.name for event in agent.events] == ["agent.error"]


def test_mcp_client_sends_the_current_span_traceparent(collector: Collector) -> None:
    source = McpToolSource(url="http://test.invalid", tracer=collector.tracer)
    ctx = run_context(str(uuid.uuid4()))
    with (
        collector.tracer.start_as_current_span("agent.data"),
        collector.tracer.start_as_current_span("execute_tool orders_summary@1"),
    ):
        headers = source.headers(ctx)
    assert headers["traceparent"].startswith("00-")
    assert headers["traceparent"] != ctx.trace_parent
    assert headers["Authorization"].startswith("Bearer ")
