"""Contract tests for AnthropicProvider against a fake Messages API client.

No test in this module opens a socket: the client is injected, and an autouse
fixture makes a real connection attempt fail loudly. The payloads are built
with the SDK's own models (``anthropic.types.Message`` and the raw stream
events), so the adapter is exercised against the shape the SDK really parses.
No Anthropic key is read and no live call is made anywhere in this file.
"""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from anthropic.types import Message
from anthropic.types.raw_content_block_delta_event import RawContentBlockDeltaEvent
from anthropic.types.raw_content_block_start_event import RawContentBlockStartEvent
from anthropic.types.raw_message_delta_event import RawMessageDeltaEvent
from anthropic.types.raw_message_start_event import RawMessageStartEvent
from anthropic.types.raw_message_stop_event import RawMessageStopEvent
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.contracts.support import CONFIG_DIR, CONFIG_ENV

from switchboard.adapters.providers.anthropic import (
    CACHE_CONTROL,
    DEFAULT_MAX_TOKENS,
    STRUCTURED_TOOL_NAME,
    AnthropicProvider,
    map_error,
    normalise_usage,
    to_anthropic_tool_choice,
    validate_against_schema,
)
from switchboard.config import load_settings
from switchboard.core.config import PriceEntry, ProviderConfig
from switchboard.core.errors import (
    ConfigError,
    ProviderRequestError,
    ProviderTransientError,
    StructuredOutputError,
    ToolValidationError,
)
from switchboard.core.models import (
    CompletionRequest,
    ProviderCapabilities,
    ToolCall,
    ToolSpec,
    Usage,
)
from switchboard.core.models import (
    Message as CoreMessage,
)
from switchboard.registry import PROVIDERS, Registry

CAPABILITIES = ProviderCapabilities(
    tools=True,
    json_schema=False,
    streaming=True,
    reasoning=True,
    context_window=1_000_000,
)
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "agents": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["agents", "reason"],
}
PRICE = PriceEntry(
    provider="anthropic",
    model="claude-sonnet-5-5",
    input_per_million=2.0,
    cached_input_per_million=0.1,
    cache_write_per_million=2.5,
    output_per_million=10.0,
    source="https://platform.claude.com/docs/en/about-claude/pricing",
)
ORDERS_TOOL = ToolSpec(
    name="orders_late",
    version="1",
    description="Count late deliveries.",
    input_schema={
        "type": "object",
        "properties": {"date_from": {"type": "string"}},
        "required": ["date_from"],
    },
)
TOTALS_TOOL = ToolSpec(
    name="orders_summary",
    version="1",
    description="Summarise orders.",
    input_schema={"type": "object", "properties": {}},
)


# --------------------------------------------------------------------------
# No socket may be opened by anything in this module.
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every connection except to loopback.

    Windows builds the event loop's self-pipe from a socket pair over
    127.0.0.1, so a blanket block would break asyncio itself; a loopback
    connection is not a model call, and the Anthropic endpoint is not loopback.
    """
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def host_of(address: Any) -> Any:
        if isinstance(address, tuple) and address:
            return address[0]
        return address

    def connect(self: socket.socket, address: Any) -> Any:
        if host_of(address) not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError(
                f"the Anthropic adapter opened a network connection to {address!r}"
            )
        return real_connect(self, address)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if host_of(address) not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError(
                f"the Anthropic adapter opened a network connection to {address!r}"
            )
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)


def test_the_network_guard_is_armed() -> None:
    with pytest.raises(AssertionError, match="opened a network connection"):
        socket.create_connection(("api.anthropic.com", 443))


# --------------------------------------------------------------------------
# Fake client and payload builders.
# --------------------------------------------------------------------------


class FakeStream:
    """The async iterator the SDK returns when ``stream=True``."""

    def __init__(self, events: list[Any], *, error: BaseException | None = None) -> None:
        self._events = list(events)
        self._error = error
        self.closed = False

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> Any:
        if self._events:
            return self._events.pop(0)
        if self._error is not None:
            error, self._error = self._error, None
            raise error
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = FakeMessages(responses)


def usage(
    input_tokens: int = 19,
    output_tokens: int = 3,
    *,
    cache_read: int = 0,
    cache_write: int = 0,
) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
    }


def message(
    *content: dict[str, Any],
    stop_reason: str | None = "end_turn",
    usage_payload: dict[str, Any] | None = None,
    model: str = "claude-sonnet-5-5",
    message_id: str = "msg_contract_1",
) -> Message:
    return Message.model_validate(
        {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": list(content),
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage_payload or usage(),
        }
    )


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def tool_use_block(
    name: str, arguments: dict[str, Any], call_id: str = "toolu_1"
) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


def make_provider(
    responses: list[Any],
    *,
    price: PriceEntry | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    capabilities: ProviderCapabilities = CAPABILITIES,
    exporter: InMemorySpanExporter | None = None,
) -> tuple[AnthropicProvider, FakeMessages]:
    client = FakeClient(responses)
    tracer = None
    if exporter is not None:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")
    adapter = AnthropicProvider(
        name="anthropic",
        model="claude-sonnet-5-5",
        capabilities=capabilities,
        price=price,
        max_tokens=max_tokens,
        client=client,
        tracer=tracer,
    )
    return adapter, client.messages


def text_request(**overrides: Any) -> CompletionRequest:
    options: dict[str, Any] = {
        "messages": [
            CoreMessage(role="system", content="You are a careful assistant."),
            CoreMessage(role="user", content="How many orders were late?"),
        ]
    }
    options.update(overrides)
    return CompletionRequest(**options)


def plan_request(**overrides: Any) -> CompletionRequest:
    options: dict[str, Any] = {
        "messages": [CoreMessage(role="user", content="How many orders were late?")],
        "response_schema": PLAN_SCHEMA,
        "max_tokens": 128,
    }
    options.update(overrides)
    return CompletionRequest(**options)


def chat_span(exporter: InMemorySpanExporter) -> Any:
    spans = [
        span for span in exporter.get_finished_spans() if span.name == "chat claude-sonnet-5-5"
    ]
    assert len(spans) == 1
    return spans[0]


# --------------------------------------------------------------------------
# Plain text, system prompt and request shape.
# --------------------------------------------------------------------------


async def test_plain_completion_maps_the_system_prompt_and_usage() -> None:
    adapter, calls = make_provider([message(text_block("OK."))])

    response = await adapter.complete(text_request())

    assert response.text == "OK."
    assert response.reasoning is None
    assert response.tool_calls == []
    assert response.parsed is None
    assert response.structured_output_strategy is None
    assert response.usage == Usage(input_tokens=19, output_tokens=3)
    assert response.model == "claude-sonnet-5-5"
    assert response.provider == "anthropic"
    assert response.latency_ms >= 0

    sent = calls.calls[0]
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["max_tokens"] == DEFAULT_MAX_TOKENS
    assert sent["system"] == [
        {
            "type": "text",
            "text": "You are a careful assistant.",
            "cache_control": dict(CACHE_CONTROL),
        }
    ]
    assert sent["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "How many orders were late?"}]}
    ]
    # No sampling parameter: the pinned Messages API exposes no temperature.
    assert "temperature" not in sent


async def test_max_tokens_falls_back_to_the_provider_default() -> None:
    adapter, calls = make_provider(
        [message(text_block("OK.")), message(text_block("OK again."))], max_tokens=777
    )

    await adapter.complete(text_request())
    assert calls.calls[0]["max_tokens"] == 777

    await adapter.complete(text_request(max_tokens=64))
    assert calls.calls[1]["max_tokens"] == 64


async def test_reasoning_blocks_stay_out_of_the_answer() -> None:
    adapter, _ = make_provider(
        [
            message(
                {"type": "thinking", "thinking": "The user asks for a count.", "signature": "sig"},
                text_block("7262 orders."),
            )
        ]
    )

    response = await adapter.complete(text_request())

    assert response.text == "7262 orders."
    assert response.reasoning == "The user asks for a count."


# --------------------------------------------------------------------------
# Native tool use.
# --------------------------------------------------------------------------


async def test_tool_definitions_use_input_schema_and_cache_the_last_tool() -> None:
    adapter, calls = make_provider([message(tool_use_block("orders_late_1", {"date_from": "x"}))])

    await adapter.complete(text_request(tools=[ORDERS_TOOL, TOTALS_TOOL]))

    tools = calls.calls[0]["tools"]
    assert [tool["name"] for tool in tools] == ["orders_late_1", "orders_summary_1"]
    assert tools[0]["input_schema"] == ORDERS_TOOL.input_schema
    assert tools[0]["description"] == "Count late deliveries."
    assert "cache_control" not in tools[0]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert calls.calls[0]["tool_choice"] == {"type": "auto"}


async def test_native_tool_use_maps_calls_back_to_wire_names() -> None:
    adapter, _ = make_provider([message(tool_use_block("orders_late_1", {"date_from": "2018-03"}))])

    response = await adapter.complete(text_request(tools=[ORDERS_TOOL]))

    assert response.tool_calls == [
        ToolCall(
            name="orders_late@1",
            arguments={"date_from": "2018-03"},
            call_id="toolu_1",
        )
    ]
    assert response.text is None


async def test_a_prior_tool_call_and_its_result_round_trip() -> None:
    broken = ToolCall(name="orders_late@1", arguments={"date_from": "x"}, call_id="toolu_1")
    request = CompletionRequest(
        messages=[
            CoreMessage(role="user", content="Late orders?"),
            CoreMessage(role="assistant", content="", tool_calls=[broken]),
            CoreMessage(role="tool", content="{}", tool_call_id="toolu_1"),
        ]
    )
    adapter, calls = make_provider([message(text_block("done"))])
    await adapter.complete(request)
    # A prior assistant tool call is sent as a tool_use block with the safe name.
    assert calls.calls[0]["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "orders_late_1",
        "input": {"date_from": "x"},
    }
    assert calls.calls[0]["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}"}
    ]


async def test_a_tool_message_without_an_id_is_a_tool_validation_error() -> None:
    adapter, _ = make_provider([message(text_block("done"))])
    request = CompletionRequest(messages=[CoreMessage(role="tool", content="{}")])

    with pytest.raises(ToolValidationError, match="tool_call_id"):
        await adapter.complete(request)


async def test_tool_results_merge_into_the_next_user_turn() -> None:
    adapter, calls = make_provider([message(text_block("April is similar."))])
    call = ToolCall(name="orders_late@1", arguments={"date_from": "2018-03"}, call_id="toolu_1")
    request = CompletionRequest(
        messages=[
            CoreMessage(role="user", content="March?"),
            CoreMessage(role="assistant", content="", tool_calls=[call]),
            CoreMessage(role="tool", content='{"count": 1328}', tool_call_id="toolu_1"),
            CoreMessage(role="user", content="And in April?"),
        ],
        tools=[ORDERS_TOOL],
    )

    await adapter.complete(request)

    sent = calls.calls[0]["messages"]
    assert [turn["role"] for turn in sent] == ["user", "assistant", "user"]
    assert sent[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"count": 1328}'},
        {"type": "text", "text": "And in April?"},
    ]


def test_tool_choice_covers_auto_any_and_specific() -> None:
    assert to_anthropic_tool_choice("auto") == {"type": "auto"}
    assert to_anthropic_tool_choice("any") == {"type": "any"}
    assert to_anthropic_tool_choice("tool", "orders_late_1") == {
        "type": "tool",
        "name": "orders_late_1",
    }
    with pytest.raises(ConfigError, match="needs a tool name"):
        to_anthropic_tool_choice("tool")


# --------------------------------------------------------------------------
# Structured output.
# --------------------------------------------------------------------------


async def test_structured_output_forces_a_tool_and_validates_the_result() -> None:
    adapter, calls = make_provider(
        [
            message(
                tool_use_block(
                    STRUCTURED_TOOL_NAME,
                    {"agents": ["data"], "reason": "orders"},
                    call_id="toolu_plan",
                ),
                stop_reason="tool_use",
            )
        ]
    )

    response = await adapter.complete(plan_request())

    assert response.structured_output_strategy == "tool_call"
    assert response.parsed == {"agents": ["data"], "reason": "orders"}
    assert response.tool_calls == []
    sent = calls.calls[0]
    assert sent["tools"] == [
        {
            "name": STRUCTURED_TOOL_NAME,
            "description": "Return the structured response.",
            "input_schema": PLAN_SCHEMA,
        }
    ]
    assert sent["tool_choice"] == {"type": "tool", "name": STRUCTURED_TOOL_NAME}
    assert "cache_control" not in sent["tools"][0]


async def test_structured_output_is_repaired_once() -> None:
    adapter, calls = make_provider(
        [
            message(tool_use_block(STRUCTURED_TOOL_NAME, {"agents": "data"})),
            message(tool_use_block(STRUCTURED_TOOL_NAME, {"agents": ["data"], "reason": "ok"})),
        ]
    )

    response = await adapter.complete(plan_request())

    assert response.parsed == {"agents": ["data"], "reason": "ok"}
    assert len(calls.calls) == 2
    repair_turn = calls.calls[1]["messages"][-1]
    assert repair_turn["role"] == "user"
    assert "previous reply" in repair_turn["content"][0]["text"]
    assert response.usage == Usage(input_tokens=38, output_tokens=6)


async def test_structured_output_that_never_matches_raises() -> None:
    adapter, calls = make_provider(
        [
            message(tool_use_block(STRUCTURED_TOOL_NAME, {"agents": ["data"]})),
            message(tool_use_block(STRUCTURED_TOOL_NAME, {"agents": ["data"]})),
        ]
    )

    with pytest.raises(StructuredOutputError, match="does not match the schema"):
        await adapter.complete(plan_request())
    assert len(calls.calls) == 2


async def test_structured_output_without_the_tool_call_raises() -> None:
    adapter, calls = make_provider(
        [message(text_block("Here is a plan in prose.")), message(text_block("Still prose."))]
    )

    with pytest.raises(StructuredOutputError, match="did not call the structured tool"):
        await adapter.complete(plan_request())
    assert len(calls.calls) == 2


async def test_structured_output_cannot_be_streamed() -> None:
    adapter, calls = make_provider([])

    with pytest.raises(ProviderRequestError, match="structured output"):
        await adapter.complete(plan_request(stream=True))
    assert calls.calls == []


def test_the_schema_checker_reports_readable_paths() -> None:
    errors = validate_against_schema({"agents": "data", "reason": 3}, PLAN_SCHEMA)
    assert any("$.agents: expected array" in error for error in errors)
    assert any("$.reason: expected string" in error for error in errors)
    assert validate_against_schema({"agents": ["data"], "reason": "ok"}, PLAN_SCHEMA) == []
    extra = validate_against_schema(
        {"agents": ["data"], "reason": "ok", "extra": 1},
        {**PLAN_SCHEMA, "additionalProperties": False},
    )
    assert any("unexpected property" in error for error in extra)


# --------------------------------------------------------------------------
# Prompt caching placement and cache usage.
# --------------------------------------------------------------------------


async def test_cache_control_marks_only_the_stable_prefix() -> None:
    exporter = InMemorySpanExporter()
    adapter, calls = make_provider(
        [message(text_block("OK."))],
        price=PRICE,
        exporter=exporter,
    )
    request = text_request(
        tools=[ORDERS_TOOL],
        messages=[
            CoreMessage(role="system", content="Static agent prompt."),
            CoreMessage(role="system", content="Tenant tool notes."),
            CoreMessage(role="user", content="Dynamic question."),
        ],
    )

    await adapter.complete(request)

    sent = calls.calls[0]
    assert sent["system"] == [
        {
            "type": "text",
            "text": "Static agent prompt.\n\nTenant tool notes.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    marked = sum(1 for tool in sent["tools"] if "cache_control" in tool)
    assert marked == 1
    assert "cache_control" not in sent["messages"][0]["content"][0]
    attributes = dict(chat_span(exporter).attributes or {})
    assert attributes["switchboard.usage.cache_write_input_tokens"] == 0


async def test_usage_maps_cache_reads_and_writes_into_the_cost() -> None:
    exporter = InMemorySpanExporter()
    adapter, _ = make_provider(
        [
            message(
                text_block("cached answer"),
                usage_payload=usage(
                    input_tokens=100, output_tokens=50, cache_read=800, cache_write=200
                ),
            )
        ],
        price=PRICE,
        exporter=exporter,
    )

    response = await adapter.complete(text_request())

    # The API reports the prompt as three parts; the core record sums them and
    # keeps the cache parts visible.
    assert response.usage == Usage(
        input_tokens=1100,
        cached_input_tokens=800,
        cache_write_input_tokens=200,
        output_tokens=50,
    )
    expected = (100 * 2.0 + 800 * 0.1 + 200 * 2.5 + 50 * 10.0) / 1_000_000
    assert response.cost_usd == pytest.approx(expected)
    attributes = dict(chat_span(exporter).attributes or {})
    assert attributes["switchboard.usage.cached_input_tokens"] == 800
    assert attributes["switchboard.usage.cache_write_input_tokens"] == 200
    assert attributes["switchboard.cost_usd"] == pytest.approx(expected)


async def test_without_a_price_row_the_call_is_unpriced() -> None:
    exporter = InMemorySpanExporter()
    adapter, _ = make_provider([message(text_block("OK."))], exporter=exporter)

    response = await adapter.complete(text_request())

    assert response.cost_usd is None
    assert "switchboard.cost_usd" not in (chat_span(exporter).attributes or {})


def test_normalise_usage_defaults_to_zero_without_a_usage_object() -> None:
    assert normalise_usage(None) == Usage(input_tokens=0, output_tokens=0)


# --------------------------------------------------------------------------
# Stop reason, request id and latency on the span.
# --------------------------------------------------------------------------


async def test_the_chat_span_records_request_id_stop_reason_and_latency() -> None:
    exporter = InMemorySpanExporter()
    adapter, _ = make_provider(
        [message(text_block("OK."), stop_reason="max_tokens", message_id="msg_abc123")],
        exporter=exporter,
    )

    response = await adapter.complete(text_request())

    span = chat_span(exporter)
    attributes = dict(span.attributes or {})
    assert attributes["gen_ai.operation.name"] == "chat"
    assert attributes["gen_ai.system"] == "anthropic"
    assert attributes["gen_ai.request.model"] == "claude-sonnet-5-5"
    assert attributes["gen_ai.response.id"] == "msg_abc123"
    assert attributes["switchboard.provider.stop_reason"] == "max_tokens"
    assert attributes["switchboard.structured_output.strategy"] == "none"
    assert attributes["switchboard.latency_ms"] == response.latency_ms


# --------------------------------------------------------------------------
# Errors.
# --------------------------------------------------------------------------


class FakeHTTPResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.request = None
        self.text = ""


def sdk_error(cls: type[Any], status: int) -> Any:
    return cls("boom", response=FakeHTTPResponse(status), body=None)


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (anthropic.RateLimitError, 429),
        (anthropic.OverloadedError, 529),
        (anthropic.InternalServerError, 500),
    ],
)
def test_transient_statuses_map_to_a_retryable_error(cls: type[Any], status: int) -> None:
    mapped = map_error(sdk_error(cls, status))
    assert isinstance(mapped, ProviderTransientError)
    assert mapped.details["status"] == status


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.BadRequestError, 400),
    ],
)
def test_auth_and_request_errors_are_not_retried(cls: type[Any], status: int) -> None:
    mapped = map_error(sdk_error(cls, status))
    assert isinstance(mapped, ProviderRequestError)
    assert not mapped.retryable
    assert mapped.details["status"] == status


def test_timeouts_and_connection_failures_are_transient() -> None:
    timeout = anthropic.APITimeoutError(request=SimpleNamespace())  # type: ignore[arg-type]
    assert isinstance(map_error(timeout), ProviderTransientError)
    assert isinstance(map_error(TimeoutError("slow")), ProviderTransientError)
    connection = anthropic.APIConnectionError(request=SimpleNamespace())  # type: ignore[arg-type]
    assert isinstance(map_error(connection), ProviderTransientError)


async def test_an_sdk_error_during_a_call_leaves_the_taxonomy() -> None:
    adapter, _ = make_provider([sdk_error(anthropic.RateLimitError, 429)])

    with pytest.raises(ProviderTransientError, match="status 429"):
        await adapter.complete(text_request())


# --------------------------------------------------------------------------
# Streaming and cancellation.
# --------------------------------------------------------------------------


def message_start(usage_payload: dict[str, Any] | None = None) -> RawMessageStartEvent:
    return RawMessageStartEvent.model_validate(
        {
            "type": "message_start",
            "message": {
                "id": "msg_stream_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": usage_payload or usage(input_tokens=19, output_tokens=1),
            },
        }
    )


def text_start(index: int) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent.model_validate(
        {"type": "content_block_start", "index": index, "content_block": text_block("")}
    )


def text_delta(index: int, text: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent.model_validate(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
    )


def tool_start(index: int, name: str, call_id: str = "toolu_stream_1") -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent.model_validate(
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": {},
            },
        }
    )


def json_delta(index: int, partial: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent.model_validate(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial},
        }
    )


def thinking_delta(index: int, text: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent.model_validate(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "thinking_delta", "thinking": text},
        }
    )


def message_delta(stop_reason: str, output_tokens: int) -> RawMessageDeltaEvent:
    return RawMessageDeltaEvent.model_validate(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        }
    )


def message_stop() -> RawMessageStopEvent:
    return RawMessageStopEvent.model_validate({"type": "message_stop"})


async def test_streaming_joins_text_emits_deltas_and_reads_usage() -> None:
    deltas: list[str] = []
    stream = FakeStream(
        [
            message_start(),
            text_start(0),
            text_delta(0, "There "),
            text_delta(0, "were "),
            text_delta(0, "7262 late orders."),
            message_delta("end_turn", 6),
            message_stop(),
        ]
    )
    adapter, calls = make_provider([stream])

    response = await adapter.complete(
        CompletionRequest(
            messages=[CoreMessage(role="user", content="how many?")],
            stream=True,
            delta_callback=deltas.append,
        )
    )

    assert deltas == ["There ", "were ", "7262 late orders."]
    assert response.text == "There were 7262 late orders."
    assert response.usage == Usage(input_tokens=19, output_tokens=6)
    assert response.model == "claude-sonnet-5-5"
    assert response.tool_calls == []
    assert calls.calls[0]["stream"] is True
    assert stream.closed is True


async def test_streaming_keeps_thinking_out_of_the_answer_and_the_callback() -> None:
    deltas: list[str] = []
    stream = FakeStream(
        [
            message_start(),
            text_start(0),
            thinking_delta(0, "The user asks "),
            thinking_delta(0, "for a count."),
            text_delta(0, "7262 orders."),
            message_delta("end_turn", 5),
            message_stop(),
        ]
    )
    adapter, _ = make_provider([stream])

    response = await adapter.complete(
        CompletionRequest(
            messages=[CoreMessage(role="user", content="how many?")],
            stream=True,
            delta_callback=deltas.append,
        )
    )

    assert deltas == ["7262 orders."]
    assert response.reasoning == "The user asks for a count."


async def test_streamed_tool_arguments_are_merged_by_index() -> None:
    stream = FakeStream(
        [
            message_start(),
            tool_start(1, "orders_late_1"),
            json_delta(1, '{"date_from": '),
            json_delta(1, '"2018-03-01"}'),
            message_delta("tool_use", 12),
            message_stop(),
        ]
    )
    adapter, _ = make_provider([stream])

    response = await adapter.complete(
        CompletionRequest(
            messages=[CoreMessage(role="user", content="late?")],
            tools=[ORDERS_TOOL],
            stream=True,
        )
    )

    assert response.text is None
    assert response.tool_calls == [
        ToolCall(
            name="orders_late@1",
            arguments={"date_from": "2018-03-01"},
            call_id="toolu_stream_1",
        )
    ]
    assert response.usage.output_tokens == 12


async def test_a_streamed_tool_call_without_a_name_raises_the_tool_taxonomy() -> None:
    stream = FakeStream(
        [
            message_start(),
            RawContentBlockStartEvent.model_validate(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": "toolu_1", "name": "", "input": {}},
                }
            ),
            message_delta("tool_use", 2),
            message_stop(),
        ]
    )
    adapter, _ = make_provider([stream])

    with pytest.raises(ToolValidationError, match="without a name"):
        await adapter.complete(
            CompletionRequest(messages=[CoreMessage(role="user", content="x")], stream=True)
        )


async def test_an_error_mid_stream_is_mapped_and_the_stream_is_closed() -> None:
    stream = FakeStream(
        [message_start(), text_start(0), text_delta(0, "partial")],
        error=sdk_error(anthropic.InternalServerError, 500),
    )
    adapter, _ = make_provider([stream])

    with pytest.raises(ProviderTransientError):
        await adapter.complete(
            CompletionRequest(messages=[CoreMessage(role="user", content="x")], stream=True)
        )
    assert stream.closed is True


async def test_cancellation_propagates_instead_of_becoming_a_provider_error() -> None:
    stream = FakeStream(
        [message_start(), text_start(0), text_delta(0, "partial")],
        error=asyncio.CancelledError(),
    )
    adapter, _ = make_provider([stream])

    with pytest.raises(asyncio.CancelledError):
        await adapter.complete(
            CompletionRequest(messages=[CoreMessage(role="user", content="x")], stream=True)
        )
    assert stream.closed is True


async def test_streaming_requires_the_capability() -> None:
    adapter, calls = make_provider(
        [],
        capabilities=ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=1_000_000,
        ),
    )

    with pytest.raises(ProviderRequestError, match="does not support streaming"):
        await adapter.complete(
            CompletionRequest(messages=[CoreMessage(role="user", content="x")], stream=True)
        )
    assert calls.calls == []


# --------------------------------------------------------------------------
# Configuration, registry and the committed files.
# --------------------------------------------------------------------------


def provider_config(**overrides: Any) -> ProviderConfig:
    options: dict[str, Any] = {
        "adapter": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-5-5",
        "max_tokens": 1234,
        "capabilities": CAPABILITIES,
    }
    options.update(overrides)
    return ProviderConfig(**options)


def test_from_config_reads_the_key_from_the_named_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    provider = AnthropicProvider.from_config("anthropic", provider_config())

    assert provider.name == "anthropic"
    assert provider.capabilities() == CAPABILITIES


def test_from_config_fails_before_any_request_when_the_key_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider.from_config("anthropic", provider_config())


def test_from_config_requires_a_model_and_capabilities() -> None:
    with pytest.raises(ConfigError, match="needs a model"):
        AnthropicProvider.from_config("anthropic", provider_config(model=None))
    with pytest.raises(ConfigError, match="needs capabilities"):
        AnthropicProvider.from_config("anthropic", provider_config(capabilities=None))


def test_a_json_schema_capability_is_rejected_by_this_adapter() -> None:
    with pytest.raises(ConfigError, match="json_schema"):
        AnthropicProvider(
            name="anthropic",
            model="claude-sonnet-5-5",
            capabilities=ProviderCapabilities(
                tools=True,
                json_schema=True,
                streaming=True,
                reasoning=False,
                context_window=1_000_000,
            ),
            client=FakeClient([]),
        )


def test_the_registry_resolves_the_provider_entry_point() -> None:
    assert Registry().load(PROVIDERS, "anthropic") is AnthropicProvider
    assert "anthropic" in Registry().names(PROVIDERS)


def test_the_committed_config_registers_anthropic_and_no_tenant_uses_it() -> None:
    settings = load_settings(CONFIG_DIR, CONFIG_ENV)

    config = settings.providers.providers["anthropic"]
    assert config.adapter == "anthropic"
    assert config.model == "claude-sonnet-5-5"
    assert config.base_url == "https://api.anthropic.com"
    assert config.api_key_env == "ANTHROPIC_API_KEY"
    assert config.api_key is None
    assert config.max_tokens == 4096
    assert config.capabilities is not None
    assert config.capabilities.tools is True
    assert config.capabilities.streaming is True
    assert config.capabilities.json_schema is False
    assert config.capabilities.context_window == 1_000_000

    assert {tenant.provider for tenant in settings.tenants.tenants.values()} == {"lmstudio"}

    price = settings.prices.lookup("anthropic", "claude-sonnet-5-5")
    assert price is not None
    assert price.source == "https://platform.claude.com/docs/en/about-claude/pricing"
    assert price.input_per_million == 2.0
    assert price.cached_input_per_million == 0.1
    assert price.cache_write_per_million == 2.5
    assert price.output_per_million == 10.0


async def test_a_client_factory_is_only_called_on_the_first_request() -> None:
    created: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient([message(text_block("from the factory"))])
        created.append(client)
        return client

    adapter = AnthropicProvider(
        name="anthropic",
        model="claude-sonnet-5-5",
        capabilities=CAPABILITIES,
        client_factory=factory,
    )
    assert created == []

    response = await adapter.complete(text_request())
    assert response.text == "from the factory"
    assert len(created) == 1
    assert len(created[0].messages.calls) == 1
