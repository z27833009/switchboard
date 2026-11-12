"""Contract tests for OpenAICompatibleProvider against recorded LM Studio payloads.

The fixtures were recorded from Bionic 1.2.0 (LM Studio 1.2.0) with qwen3-8b on
2026-11-12. They are raw HTTP bodies, loaded into the SDK's ChatCompletion model
so the adapter is exercised without a network call.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import openai
import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from switchboard.adapters._openai_errors import map_error
from switchboard.adapters.providers.openai_compat import (
    STRUCTURED_TOOL_NAME,
    OpenAICompatibleProvider,
    parse_json_object,
)
from switchboard.core.config import ProviderConfig
from switchboard.core.errors import (
    ConfigError,
    ProviderRequestError,
    ProviderTransientError,
    StructuredOutputError,
    ToolValidationError,
)
from switchboard.core.models import (
    CompletionRequest,
    Message,
    ProviderCapabilities,
    ToolCall,
    Usage,
)
from switchboard.registry import PROVIDERS, Registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "openai_compatible"
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "agents": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["agents", "reason"],
}


def payload(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def completion(name: str) -> ChatCompletion:
    return ChatCompletion.model_validate(payload(name))


def plan_request(**overrides: Any) -> CompletionRequest:
    options: dict[str, Any] = {
        "messages": [Message(role="user", content="How many orders were late?")],
        "response_schema": PLAN_SCHEMA,
        "max_tokens": 128,
        "temperature": 0,
    }
    options.update(overrides)
    return CompletionRequest(**options)


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, openai.OpenAIError):
            raise item
        if isinstance(item, list):
            return FakeStream(item)
        return item


class FakeStream:
    """The async iterator the SDK returns when ``stream=True``."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> Any:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def make_provider(
    responses: list[Any],
    *,
    json_schema: bool = False,
    tools: bool = True,
    streaming: bool = True,
) -> tuple[OpenAICompatibleProvider, FakeCompletions]:
    completions = FakeCompletions(responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = OpenAICompatibleProvider(
        name="lmstudio",
        model="qwen3-8b",
        base_url="http://test.invalid/v1",
        api_key="test",
        capabilities=ProviderCapabilities(
            tools=tools,
            json_schema=json_schema,
            streaming=streaming,
            reasoning=False,
            context_window=8192,
        ),
        client=client,
    )
    return provider, completions


def chunk(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    with_choice: bool = True,
) -> ChatCompletionChunk:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    payload: dict[str, Any] = {
        "id": "chatcmpl-stream-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "qwen3-8b",
        "choices": ([{"index": 0, "delta": delta, "finish_reason": None}] if with_choice else []),
    }
    if usage is not None:
        payload["usage"] = usage
    return ChatCompletionChunk.model_validate(payload)


async def test_plain_completion_normalises_text_and_usage() -> None:
    provider, calls = make_provider([completion("plain_reply.json")])
    response = await provider.complete(
        CompletionRequest(messages=[Message(role="user", content="hi")])
    )

    assert response.text == "OK."
    assert response.reasoning is None
    assert response.usage == Usage(input_tokens=19, output_tokens=3)
    assert response.model == "qwen3-8b"
    assert response.provider == "lmstudio"
    assert response.latency_ms >= 0
    assert response.structured_output_strategy is None
    assert calls.calls[0]["messages"] == [{"role": "user", "content": "hi"}]


async def test_reasoning_is_separated_from_the_answer() -> None:
    provider, _ = make_provider([completion("reasoning_reply.json")])
    response = await provider.complete(
        CompletionRequest(messages=[Message(role="user", content="is 17 prime?")])
    )

    assert response.text is None
    assert response.reasoning is not None
    assert response.reasoning.startswith("Okay, so I need to figure out")
    assert response.usage.output_tokens == 96


async def test_tool_call_arguments_are_parsed_from_json() -> None:
    provider, _ = make_provider([completion("tool_call.json")])
    response = await provider.complete(
        CompletionRequest(messages=[Message(role="user", content="weather?")])
    )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_weather"
    assert response.tool_calls[0].arguments == {"city": "Paris"}
    assert response.tool_calls[0].call_id


async def test_invalid_tool_arguments_raise_tool_validation_error() -> None:
    raw = payload("tool_call.json")
    raw["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
    provider, _ = make_provider([ChatCompletion.model_validate(raw)])

    with pytest.raises(ToolValidationError, match="invalid JSON arguments"):
        await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="weather?")])
        )


async def test_cached_input_tokens_are_normalised() -> None:
    raw = payload("plain_reply.json")
    raw["usage"]["prompt_tokens_details"] = {"cached_tokens": 12}
    provider, _ = make_provider([ChatCompletion.model_validate(raw)])

    response = await provider.complete(
        CompletionRequest(messages=[Message(role="user", content="hi")])
    )
    assert response.usage.cached_input_tokens == 12


async def test_native_json_schema_strategy() -> None:
    provider, calls = make_provider([completion("native_json.json")], json_schema=True)
    response = await provider.complete(plan_request())

    assert response.structured_output_strategy == "native_json_schema"
    assert response.parsed is not None
    assert response.parsed["agents"] == ["data"]
    assert calls.calls[0]["response_format"]["type"] == "json_schema"


async def test_prompted_json_strategy() -> None:
    provider, calls = make_provider([completion("prompted_json.json")], tools=False)
    response = await provider.complete(plan_request())

    assert response.structured_output_strategy == "prompted_json"
    assert response.parsed is not None
    assert set(response.parsed) == {"agents", "reason"}
    assert calls.calls[0]["messages"][0]["role"] == "system"
    assert "JSON schema" in calls.calls[0]["messages"][0]["content"]


async def test_structured_output_is_repaired_once() -> None:
    provider, calls = make_provider(
        [completion("plain_reply.json"), completion("prompted_json.json")],
        tools=False,
    )
    response = await provider.complete(plan_request())

    assert response.parsed is not None
    assert len(calls.calls) == 2
    assert "previous reply" in calls.calls[1]["messages"][-1]["content"]
    assert response.usage == Usage(input_tokens=77, output_tokens=36)


async def test_structured_output_fails_after_one_repair() -> None:
    provider, calls = make_provider(
        [completion("plain_reply.json"), completion("plain_reply.json")]
    )
    with pytest.raises(StructuredOutputError):
        await provider.complete(plan_request())
    assert len(calls.calls) == 2


async def test_tool_call_strategy_for_structured_output() -> None:
    raw = payload("tool_call.json")
    tool_call = raw["choices"][0]["message"]["tool_calls"][0]
    tool_call["function"]["name"] = STRUCTURED_TOOL_NAME
    tool_call["function"]["arguments"] = json.dumps({"agents": ["data"], "reason": "orders"})
    provider, calls = make_provider([ChatCompletion.model_validate(raw)])

    response = await provider.complete(plan_request())
    assert response.structured_output_strategy == "tool_call"
    assert response.parsed == {"agents": ["data"], "reason": "orders"}
    assert response.tool_calls == []
    assert calls.calls[0]["tool_choice"] == "required"


async def test_streaming_accumulates_text_and_usage_and_emits_deltas() -> None:
    deltas: list[str] = []
    stream = [
        chunk(content="There "),
        chunk(content="were "),
        chunk(content="7262 late orders."),
        chunk(
            usage={"prompt_tokens": 19, "completion_tokens": 6, "total_tokens": 25},
            with_choice=False,
        ),
    ]
    provider, calls = make_provider([stream])

    response = await provider.complete(
        CompletionRequest(
            messages=[Message(role="user", content="how many?")],
            stream=True,
            delta_callback=deltas.append,
        )
    )

    assert deltas == ["There ", "were ", "7262 late orders."]
    assert response.text == "There were 7262 late orders."
    assert response.reasoning is None
    assert response.usage == Usage(input_tokens=19, output_tokens=6)
    assert response.model == "qwen3-8b"
    assert response.tool_calls == []
    assert calls.calls[0]["stream"] is True
    assert calls.calls[0]["stream_options"] == {"include_usage": True}


async def test_streaming_keeps_reasoning_out_of_the_answer_and_the_callback() -> None:
    deltas: list[str] = []
    stream = [
        chunk(reasoning="The user asks "),
        chunk(reasoning="for a count. "),
        chunk(content="7262 orders."),
        chunk(reasoning=" done"),
    ]
    provider, _ = make_provider([stream])

    response = await provider.complete(
        CompletionRequest(
            messages=[Message(role="user", content="how many?")],
            stream=True,
            delta_callback=deltas.append,
        )
    )

    assert deltas == ["7262 orders."]
    assert response.text == "7262 orders."
    assert response.reasoning == "The user asks for a count.  done"


async def test_streamed_tool_call_fragments_normalise_to_one_call() -> None:
    stream = [
        chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "orders_summary", "arguments": ""},
                }
            ]
        ),
        chunk(tool_calls=[{"index": 0, "function": {"arguments": '{"status": "del'}}]),
        chunk(tool_calls=[{"index": 0, "function": {"arguments": 'ivered"}'}}]),
        chunk(
            usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            with_choice=False,
        ),
    ]
    provider, _ = make_provider([stream])

    response = await provider.complete(
        CompletionRequest(messages=[Message(role="user", content="late?")], stream=True)
    )

    assert response.text is None
    assert response.tool_calls == [
        ToolCall(
            name="orders_summary",
            arguments={"status": "delivered"},
            call_id="call_1",
        )
    ]
    assert response.usage == Usage(input_tokens=10, output_tokens=4)


async def test_streaming_without_a_usage_chunk_leaves_usage_at_zero() -> None:
    provider, _ = make_provider([[chunk(content="OK.")]])

    response = await provider.complete(
        CompletionRequest(messages=[Message(role="user", content="hi")], stream=True)
    )

    assert response.text == "OK."
    assert response.usage == Usage(input_tokens=0, output_tokens=0)


async def test_streaming_requires_a_capability_and_a_plain_completion() -> None:
    provider, _ = make_provider([])
    with pytest.raises(ProviderRequestError, match="structured output"):
        await provider.complete(plan_request(stream=True))

    unsupported, _ = make_provider([], streaming=False)
    with pytest.raises(ProviderRequestError, match="does not support streaming"):
        await unsupported.complete(
            CompletionRequest(messages=[Message(role="user", content="hi")], stream=True)
        )


def test_parse_json_object_accepts_markdown_fences_and_prose() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Here is the plan: {"a": 2}') == {"a": 2}


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.request = None
        self.text = ""


def test_error_mapping_by_status_and_connection() -> None:
    timeout = openai.APITimeoutError(request=cast(Any, None))
    assert isinstance(map_error(timeout), ProviderTransientError)

    bad_request = openai.BadRequestError("bad", response=cast(Any, FakeResponse(400)), body=None)
    assert isinstance(map_error(bad_request), ProviderRequestError)

    server = openai.InternalServerError("boom", response=cast(Any, FakeResponse(500)), body=None)
    assert isinstance(map_error(server), ProviderTransientError)


def test_from_config_requires_model_and_capabilities() -> None:
    config = ProviderConfig(
        adapter="openai_compatible",
        base_url="http://test.invalid/v1",
        api_key="lm-studio",
        model="qwen3-8b",
        capabilities=ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=True,
            reasoning=False,
            context_window=8192,
        ),
    )
    provider = OpenAICompatibleProvider.from_config("lmstudio", config)
    assert provider.capabilities().context_window == 8192

    with pytest.raises(ConfigError, match="base_url"):
        OpenAICompatibleProvider.from_config(
            "lmstudio", ProviderConfig(adapter="openai_compatible")
        )

    with pytest.raises(ConfigError, match="needs an api_key"):
        OpenAICompatibleProvider.from_config(
            "deepseek",
            ProviderConfig(
                adapter="openai_compatible",
                base_url="https://api.deepseek.com/v1",
                api_key="",
                model="deepseek-flash",
                capabilities=config.capabilities,
            ),
        )


def test_registry_resolves_the_provider_entry_point() -> None:
    assert Registry().load(PROVIDERS, "openai_compatible") is OpenAICompatibleProvider
