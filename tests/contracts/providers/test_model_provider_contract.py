"""ModelProvider contract: OpenAI-compatible, Anthropic and mock replay.

All adapters answer the same five questions: what they support, how a plain
completion is shaped, how a tool call is normalised, how structured output is
parsed and how an error leaves the adapter. The OpenAI-compatible client is the
recorded Bionic 1.2.0 payload set loaded into the SDK model, the Anthropic
client is a scripted fake built from the SDK's own ``Message`` type, and the
mock adapter replays committed fixtures from ``evals/fixtures``. No test in this
module opens a socket. Streaming is asserted only where
``capabilities().streaming`` is true.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import openai
import pytest
from anthropic.types import Message as AnthropicMessage
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from tests.contracts.support import MOCK_FIXTURES, OPENAI_FIXTURES

from switchboard.adapters.providers.anthropic import (
    STRUCTURED_TOOL_NAME as ANTHROPIC_STRUCTURED_TOOL,
)
from switchboard.adapters.providers.anthropic import AnthropicProvider
from switchboard.adapters.providers.mock import MockProvider
from switchboard.adapters.providers.openai_compat import OpenAICompatibleProvider
from switchboard.core.errors import FixtureMissError, ProviderTransientError
from switchboard.core.models import (
    CompletionRequest,
    Message,
    ProviderCapabilities,
    ToolCall,
    ToolSpec,
    Usage,
)
from switchboard.core.ports import ModelProvider
from switchboard.registry import PROVIDERS, Registry

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "agents": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["agents", "reason"],
}


def fixture_payload(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def openai_payload(name: str) -> dict[str, Any]:
    return fixture_payload(OPENAI_FIXTURES / name)


def mock_payload(purpose: str) -> dict[str, Any]:
    for path in sorted(MOCK_FIXTURES.glob("*.json")):
        payload = fixture_payload(path)
        if payload.get("purpose") == purpose:
            return payload
    raise AssertionError(f"no committed mock fixture for purpose {purpose!r}")


def request_from_fixture(payload: dict[str, Any]) -> CompletionRequest:
    stored = payload["request"]
    messages = [
        Message(
            role=message["role"],
            content=message["content"],
            name=message.get("name"),
            tool_call_id=message.get("tool_call_id"),
            tool_calls=[
                ToolCall(
                    name=call["name"],
                    arguments=call["arguments"],
                    call_id=call["call_id"],
                )
                for call in message.get("tool_calls", [])
            ],
        )
        for message in stored["messages"]
    ]
    return CompletionRequest(
        messages=messages,
        tools=[ToolSpec(**tool) for tool in stored.get("tools", [])],
        response_schema=stored.get("response_schema"),
        metadata={"purpose": stored["purpose"]},
    )


class FakeStream:
    def __init__(self, chunks: list[ChatCompletionChunk]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> ChatCompletionChunk:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def stream_chunk(
    *,
    content: str | None = None,
    usage: dict[str, Any] | None = None,
    with_choice: bool = True,
) -> ChatCompletionChunk:
    delta = {"content": content} if content is not None else {}
    payload: dict[str, Any] = {
        "id": "chatcmpl-contract",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "qwen3-8b",
        "choices": ([{"index": 0, "delta": delta, "finish_reason": None}] if with_choice else []),
    }
    if usage is not None:
        payload["usage"] = usage
    return ChatCompletionChunk.model_validate(payload)


class FakeCompletions:
    """Dispatches on the request the adapter sends; no network is touched."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if kwargs.get("stream"):
            return FakeStream(
                [
                    stream_chunk(content="There "),
                    stream_chunk(content="were "),
                    stream_chunk(content="7262 late orders."),
                    stream_chunk(
                        usage={"prompt_tokens": 19, "completion_tokens": 6, "total_tokens": 25},
                        with_choice=False,
                    ),
                ]
            )
        if kwargs.get("response_format"):
            return ChatCompletion.model_validate(openai_payload("native_json.json"))
        if kwargs.get("tools"):
            return ChatCompletion.model_validate(openai_payload("tool_call.json"))
        return ChatCompletion.model_validate(openai_payload("plain_reply.json"))


@dataclass(frozen=True)
class ProviderContract:
    label: str
    provider: ModelProvider
    text_request: CompletionRequest
    text_expected: str
    text_usage: Usage
    tool_request: CompletionRequest
    tool_expected: str
    structured_request: CompletionRequest
    structured_expected_agent: str
    stream_request: CompletionRequest
    stream_expected: str
    error_type: type[Exception]
    raise_error: Callable[[], Awaitable[None]]


def build_openai_contract(*, error: bool = False) -> ProviderContract:
    capabilities = ProviderCapabilities(
        tools=True,
        json_schema=True,
        streaming=True,
        reasoning=False,
        context_window=8192,
    )
    fake = FakeCompletions(error=openai.APITimeoutError(request=cast(Any, None)) if error else None)
    provider = OpenAICompatibleProvider(
        name="lmstudio",
        model="qwen3-8b",
        base_url="http://test.invalid/v1",
        api_key="test",
        capabilities=capabilities,
        client=SimpleNamespace(chat=SimpleNamespace(completions=fake)),
    )
    text_request = CompletionRequest(messages=[Message(role="user", content="hi")])
    tool_request = CompletionRequest(
        messages=[Message(role="user", content="weather?")],
        tools=[
            ToolSpec(
                name="get_weather",
                version="1",
                description="Weather for a city.",
                input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
            )
        ],
    )
    structured_request = CompletionRequest(
        messages=[Message(role="user", content="How many orders were late?")],
        response_schema=PLAN_SCHEMA,
        max_tokens=128,
    )
    stream_request = CompletionRequest(
        messages=[Message(role="user", content="how many?")], stream=True
    )

    async def raise_error() -> None:
        await provider.complete(text_request)

    return ProviderContract(
        label="openai_compatible",
        provider=provider,
        text_request=text_request,
        text_expected="OK.",
        text_usage=Usage(input_tokens=19, output_tokens=3),
        tool_request=tool_request,
        tool_expected="get_weather",
        structured_request=structured_request,
        structured_expected_agent="data",
        stream_request=stream_request,
        stream_expected="There were 7262 late orders.",
        error_type=ProviderTransientError,
        raise_error=raise_error,
    )


ANTHROPIC_STREAM_EVENTS: tuple[dict[str, Any], ...] = (
    {
        "type": "message_start",
        "message": {
            "id": "msg_contract_stream",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 19, "output_tokens": 1},
        },
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "There "},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "were "},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "7262 late orders."},
    },
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 6},
    },
    {"type": "message_stop"},
)


ANTHROPIC_EVENT_CLASSES: dict[str, Any] = {
    "message_start": anthropic.types.RawMessageStartEvent,
    "content_block_start": anthropic.types.RawContentBlockStartEvent,
    "content_block_delta": anthropic.types.RawContentBlockDeltaEvent,
    "message_delta": anthropic.types.RawMessageDeltaEvent,
    "message_stop": anthropic.types.RawMessageStopEvent,
}


def anthropic_stream_events() -> list[Any]:
    return [
        ANTHROPIC_EVENT_CLASSES[payload["type"]].model_validate(payload)
        for payload in ANTHROPIC_STREAM_EVENTS
    ]


class FakeAnthropicStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = list(events)

    def __aiter__(self) -> FakeAnthropicStream:
        return self

    async def __anext__(self) -> Any:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def close(self) -> None:
        self._events.clear()


def anthropic_message(
    content: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 19,
    output_tokens: int = 3,
) -> AnthropicMessage:
    return AnthropicMessage.model_validate(
        {
            "id": "msg_contract",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


class FakeAnthropicMessages:
    """Dispatches on the request the adapter sends; no network is touched."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if kwargs.get("stream"):
            return FakeAnthropicStream(anthropic_stream_events())
        if kwargs.get("tool_choice", {}).get("type") == "tool":
            return anthropic_message(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_plan",
                        "name": ANTHROPIC_STRUCTURED_TOOL,
                        "input": {"agents": ["data"], "reason": "orders"},
                    }
                ],
                stop_reason="tool_use",
            )
        if kwargs.get("tools"):
            return anthropic_message(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_weather",
                        "name": "get_weather_1",
                        "input": {"city": "Paris"},
                    }
                ],
                stop_reason="tool_use",
            )
        return anthropic_message([{"type": "text", "text": "OK."}])


def build_anthropic_contract(*, error: bool = False) -> ProviderContract:
    capabilities = ProviderCapabilities(
        tools=True,
        json_schema=False,
        streaming=True,
        reasoning=True,
        context_window=1_000_000,
    )
    fake = FakeAnthropicMessages(
        error=anthropic.APITimeoutError(request=cast(Any, None)) if error else None
    )
    provider = AnthropicProvider(
        name="anthropic",
        model="claude-sonnet-5-5",
        capabilities=capabilities,
        client=SimpleNamespace(messages=fake),
    )
    text_request = CompletionRequest(
        messages=[
            Message(role="system", content="Answer briefly."),
            Message(role="user", content="hi"),
        ]
    )
    tool_request = CompletionRequest(
        messages=[Message(role="user", content="weather?")],
        tools=[
            ToolSpec(
                name="get_weather",
                version="1",
                description="Weather for a city.",
                input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
            )
        ],
    )
    structured_request = CompletionRequest(
        messages=[Message(role="user", content="How many orders were late?")],
        response_schema=PLAN_SCHEMA,
        max_tokens=128,
    )
    stream_request = CompletionRequest(
        messages=[Message(role="user", content="how many?")], stream=True
    )

    async def raise_error() -> None:
        await provider.complete(text_request)

    return ProviderContract(
        label="anthropic",
        provider=provider,
        text_request=text_request,
        text_expected="OK.",
        text_usage=Usage(input_tokens=19, output_tokens=3),
        # The adapter sends the safe name and maps the answer back to the wire
        # name, so the contract sees the versioned name.
        tool_request=tool_request,
        tool_expected="get_weather@1",
        structured_request=structured_request,
        structured_expected_agent="data",
        stream_request=stream_request,
        stream_expected="There were 7262 late orders.",
        error_type=ProviderTransientError,
        raise_error=raise_error,
    )


def build_mock_contract() -> ProviderContract:
    provider = MockProvider(model="qwen3-8b", fixtures_dir=MOCK_FIXTURES)
    text_payload = mock_payload("demo")
    tool_payload = mock_payload("agent.data")
    plan_payload = mock_payload("supervisor.plan")
    text_request = request_from_fixture(text_payload)
    text_usage = Usage(
        input_tokens=text_payload["response"]["usage"]["input_tokens"],
        output_tokens=text_payload["response"]["usage"]["output_tokens"],
    )
    tool_calls = tool_payload["response"]["tool_calls"]
    if not tool_calls:
        raise AssertionError("the agent.data fixture records no tool call")
    no_fixture = CompletionRequest(
        messages=[Message(role="user", content="contract prompt without a fixture 8c1e2a")],
        metadata={"purpose": "demo"},
    )

    async def raise_error() -> None:
        await provider.complete(no_fixture)

    return ProviderContract(
        label="mock",
        provider=provider,
        text_request=text_request,
        text_expected=text_payload["response"]["text"],
        text_usage=text_usage,
        tool_request=request_from_fixture(tool_payload),
        tool_expected=tool_calls[0]["name"],
        structured_request=request_from_fixture(plan_payload),
        structured_expected_agent=plan_payload["response"]["parsed"]["agents"][0],
        stream_request=text_request.model_copy(update={"stream": True}),
        stream_expected=text_payload["response"]["text"],
        error_type=FixtureMissError,
        raise_error=raise_error,
    )


BUILDERS: tuple[Callable[[], ProviderContract], ...] = (
    build_openai_contract,
    build_anthropic_contract,
    build_mock_contract,
)

ERROR_BUILDERS: tuple[Callable[[], ProviderContract], ...] = (
    lambda: build_openai_contract(error=True),
    lambda: build_anthropic_contract(error=True),
    build_mock_contract,
)


@pytest.fixture(params=BUILDERS, ids=["openai_compatible", "anthropic", "mock"])
def contract(request: pytest.FixtureRequest) -> ProviderContract:
    builder = cast(Callable[[], ProviderContract], request.param)
    return builder()


@pytest.fixture(params=ERROR_BUILDERS, ids=["openai_compatible", "anthropic", "mock"])
def error_contract(request: pytest.FixtureRequest) -> ProviderContract:
    builder = cast(Callable[[], ProviderContract], request.param)
    return builder()


def test_registered_provider_adapters_have_a_contract_case() -> None:
    assert Registry().names(PROVIDERS) == ["anthropic", "mock", "openai_compatible"]


async def test_capabilities_describe_the_adapter(contract: ProviderContract) -> None:
    capabilities = contract.provider.capabilities()
    assert isinstance(capabilities, ProviderCapabilities)
    assert capabilities.tools is True
    assert capabilities.context_window > 0
    assert contract.provider.name


async def test_a_plain_completion_returns_text_and_usage(contract: ProviderContract) -> None:
    response = await contract.provider.complete(contract.text_request)
    assert response.text == contract.text_expected
    assert response.usage == contract.text_usage
    assert response.model
    assert response.provider == contract.provider.name
    assert response.latency_ms >= 0
    assert response.tool_calls == []
    assert response.parsed is None


async def test_tool_calls_are_normalised(contract: ProviderContract) -> None:
    response = await contract.provider.complete(contract.tool_request)
    assert [call.name for call in response.tool_calls] == [contract.tool_expected]
    call = response.tool_calls[0]
    assert isinstance(call.arguments, dict)
    assert call.call_id
    assert response.usage.input_tokens > 0


async def test_structured_output_is_parsed(contract: ProviderContract) -> None:
    response = await contract.provider.complete(contract.structured_request)
    assert response.parsed is not None
    assert response.parsed["agents"] == [contract.structured_expected_agent]
    assert response.structured_output_strategy is not None


async def test_streaming_deltas_join_to_the_answer(contract: ProviderContract) -> None:
    if not contract.provider.capabilities().streaming:
        pytest.skip(f"{contract.label} does not support streaming")
    deltas: list[str] = []
    request = contract.stream_request.model_copy(update={"delta_callback": deltas.append})
    response = await contract.provider.complete(request)
    assert deltas
    assert "".join(deltas) == response.text == contract.stream_expected
    assert response.usage.output_tokens > 0


async def test_errors_use_the_core_taxonomy(error_contract: ProviderContract) -> None:
    with pytest.raises(error_contract.error_type):
        await error_contract.raise_error()
