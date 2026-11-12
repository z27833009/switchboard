"""OpenAI-compatible model provider (LM Studio, DeepSeek, gateways).

Design.md sections 8.1 to 8.3. The provider wraps the official ``openai`` SDK,
normalises messages, tool calls, reasoning output, usage and latency, and
implements the three structured-output strategies with one repair round.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any, Literal

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from switchboard.adapters._openai_errors import map_error
from switchboard.core.config import PriceEntry, ProviderConfig
from switchboard.core.errors import (
    ConfigError,
    ProviderRequestError,
    StructuredOutputError,
    ToolValidationError,
)
from switchboard.core.models import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderCapabilities,
    ToolCall,
    ToolSpec,
    Usage,
)
from switchboard.core.pricing import compute_cost
from switchboard.tracing.spans import (
    apply_request_metadata,
    apply_usage,
    chat_span_name,
    record_error,
)

Strategy = Literal["native_json_schema", "tool_call", "prompted_json"]
STRUCTURED_TOOL_NAME = "submit_structured_response"


def to_openai_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.name:
            item["name"] = message.name
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ]
        converted.append(item)
    return converted


def to_openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.wire_name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def _extract_reasoning(message: Any) -> str | None:
    extra = getattr(message, "model_extra", None) or {}
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = extra.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _normalise_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    cached: Any = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None) or 0
    if not cached:
        extra = getattr(usage, "model_extra", None) or {}
        cached = extra.get("prompt_cache_hit_tokens") or extra.get("cached_tokens") or 0
    return Usage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        cached_input_tokens=int(cached or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    if start < 0:
        raise StructuredOutputError("structured output contains no JSON object")
    try:
        value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"invalid JSON from the model: {exc}") from exc
    if not isinstance(value, dict):
        raise StructuredOutputError("structured output must be a JSON object")
    return value


def _normalise_tool_calls(message: Any, *, skip_structured: bool) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for call in getattr(message, "tool_calls", None) or []:
        function = call.function
        if skip_structured and function.name == STRUCTURED_TOOL_NAME:
            continue
        try:
            arguments = json.loads(function.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ToolValidationError(
                f"tool {function.name!r} returned invalid JSON arguments: {exc}",
                details={"tool": function.name},
            ) from exc
        if not isinstance(arguments, dict):
            raise ToolValidationError(f"tool {function.name!r} arguments must be a JSON object")
        calls.append(ToolCall(name=function.name, arguments=arguments, call_id=call.id))
    return calls


class OpenAICompatibleProvider:
    name: str

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str,
        capabilities: ProviderCapabilities,
        timeout_s: float = 120.0,
        extra_body: dict[str, Any] | None = None,
        price: PriceEntry | None = None,
        client: Any | None = None,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self._capabilities = capabilities
        self._extra_body = dict(extra_body or {})
        self._price = price
        self._tracer = tracer or trace.get_tracer("switchboard.provider.openai_compatible")
        self._client: Any = client or AsyncOpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s
        )

    @classmethod
    def from_config(
        cls, name: str, config: ProviderConfig, price: PriceEntry | None = None
    ) -> OpenAICompatibleProvider:
        if not config.base_url or not config.model or config.capabilities is None:
            raise ConfigError(f"provider {name!r} needs base_url, model and capabilities")
        # A literal placeholder is fine (LM Studio ignores the key), but an
        # empty value means the environment variable behind it is unset. Fail
        # here, before a request, instead of sending an unauthenticated call.
        if not config.api_key:
            raise ConfigError(
                f"provider {name!r} needs an api_key; set the environment variable "
                "named in config/providers.yaml"
            )
        return cls(
            name=name,
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            capabilities=config.capabilities,
            timeout_s=config.timeout_s,
            extra_body=config.extra_body,
            price=price,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _strategy(self, request: CompletionRequest) -> Strategy | None:
        if request.response_schema is None:
            return None
        if self._capabilities.json_schema:
            return "native_json_schema"
        if self._capabilities.tools and not request.tools:
            return "tool_call"
        return "prompted_json"

    def _messages(
        self,
        request: CompletionRequest,
        strategy: Strategy | None,
        repair: str | None,
    ) -> list[dict[str, Any]]:
        messages = to_openai_messages(request.messages)
        if strategy == "prompted_json" and request.response_schema is not None:
            instruction = (
                "Return only a JSON object matching this JSON schema. "
                "Do not add markdown fences or prose.\n" + json.dumps(request.response_schema)
            )
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = str(messages[0]["content"]) + "\n\n" + instruction
            else:
                messages.insert(0, {"role": "system", "content": instruction})
        if repair is not None:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid for the required schema: "
                        f"{repair}. Return only the JSON object."
                    ),
                }
            )
        return messages

    def _kwargs(
        self,
        request: CompletionRequest,
        messages: list[dict[str, Any]],
        strategy: Strategy | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.tools:
            kwargs["tools"] = to_openai_tools(request.tools)
        for key, value in self._extra_body.items():
            kwargs.setdefault(key, value)

        schema = request.response_schema
        if strategy == "native_json_schema" and schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        elif strategy == "tool_call" and schema is not None:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": STRUCTURED_TOOL_NAME,
                        "description": "Return the structured response.",
                        "parameters": schema,
                    },
                }
            ]
            kwargs["tool_choice"] = "required"
        return kwargs

    def _parsed(self, strategy: Strategy, message: Any) -> dict[str, Any]:
        if strategy == "tool_call":
            for call in getattr(message, "tool_calls", None) or []:
                if call.function.name == STRUCTURED_TOOL_NAME:
                    return parse_json_object(call.function.arguments or "")
            raise StructuredOutputError("the model did not call the structured tool")
        native = getattr(message, "parsed", None)
        if isinstance(native, dict):
            return native
        text = getattr(message, "content", None)
        if not isinstance(text, str) or not text.strip():
            raise StructuredOutputError("structured output was empty")
        return parse_json_object(text)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if request.stream:
            raise ProviderRequestError("streaming is not implemented until T2.08")
        strategy = self._strategy(request)
        started = time.perf_counter()
        with self._tracer.start_as_current_span(
            chat_span_name(self._model),
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.system", self.name)
            span.set_attribute("gen_ai.request.model", self._model)
            span.set_attribute("switchboard.structured_output.strategy", strategy or "none")
            apply_request_metadata(span, request.metadata)
            try:
                response = await self._complete(request, strategy, started)
            except Exception as exc:
                record_error(span, exc)
                raise
            apply_usage(span, response.usage)
            # The attribute is absent, not zero, when the provider has no price
            # entry: an unpriced paid call must not look free (design 12.4).
            if self._price is not None:
                span.set_attribute(
                    "switchboard.cost_usd", compute_cost(response.usage, self._price)
                )
            span.set_attribute("switchboard.latency_ms", response.latency_ms)
            return response

    async def _complete(
        self,
        request: CompletionRequest,
        strategy: Strategy | None,
        started: float,
    ) -> CompletionResponse:
        usage = Usage(input_tokens=0, output_tokens=0)
        repair: str | None = None

        for attempt in range(2):
            messages = self._messages(request, strategy, repair)
            kwargs = self._kwargs(request, messages, strategy)
            try:
                response: ChatCompletion = await self._client.chat.completions.create(**kwargs)
            except openai.OpenAIError as exc:
                raise map_error(exc) from exc

            usage = usage.plus(_normalise_usage(getattr(response, "usage", None)))
            message = response.choices[0].message
            try:
                parsed = self._parsed(strategy, message) if strategy is not None else None
            except StructuredOutputError as exc:
                if attempt == 1:
                    raise
                repair = str(exc)
                continue

            tool_calls = _normalise_tool_calls(message, skip_structured=strategy == "tool_call")
            latency_ms = int((time.perf_counter() - started) * 1000)
            return CompletionResponse(
                text=getattr(message, "content", None) or None,
                reasoning=_extract_reasoning(message),
                tool_calls=tool_calls,
                parsed=parsed,
                structured_output_strategy=strategy,
                usage=usage,
                model=getattr(response, "model", None) or self._model,
                provider=self.name,
                latency_ms=latency_ms,
                # The response carries the same figure as the span so a run
                # that mixes providers can sum the exact per-call costs.
                cost_usd=compute_cost(usage, self._price) if self._price is not None else None,
            )

        raise StructuredOutputError("structured output failed after one repair")
