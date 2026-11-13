"""Anthropic provider adapter: native tool use, structured output, prompt caching.

Design.md sections 8.1, 8.2 and 8.5. The adapter implements the same
``ModelProvider`` port as ``OpenAICompatibleProvider`` and normalises the
Messages API onto the core models: system prompt, user and assistant turns,
tool definitions, tool results, stop reasons, usage (including prompt-cache
reads and writes), request id and latency. The client is injected, so contract
tests never open a socket.

Two differences from the OpenAI-compatible adapter are deliberate and
documented in design section 8.1:

* Anthropic reports prompt tokens as three numbers (fresh input, cache read,
  cache write) and requires the caller to add them up. ``Usage.input_tokens``
  is their sum, like OpenAI's ``prompt_tokens``; ``cached_input_tokens`` is the
  read part and ``cache_write_input_tokens`` the write part, which is billed
  above the input rate.
* The Messages API of the pinned SDK exposes no ``temperature`` (or top_p)
  parameter, so ``CompletionRequest.temperature`` is not sent. The adapter
  validates itself against the SDK's typed parameters rather than guessing,
  and the plans and answers in this project all ask for the default anyway.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import Any, Literal

import anthropic
from anthropic import AsyncAnthropic
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind

from switchboard.adapters.providers.tool_names import (
    ANTHROPIC_TOOL_NAME_PATTERN,
    safe_tool_names,
)
from switchboard.core.config import PriceEntry, ProviderConfig
from switchboard.core.errors import (
    ConfigError,
    ProviderRequestError,
    ProviderTransientError,
    StructuredOutputError,
    SwitchboardError,
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

DEFAULT_BASE_URL = "https://api.anthropic.com"
# The Messages API requires a max_tokens on every request; the port leaves it
# optional, so the provider config (or this floor) supplies one.
DEFAULT_MAX_TOKENS = 4096
STRUCTURED_TOOL_NAME = "submit_structured_response"
# Ephemeral is the 5 minute cache, the only TTL the adapter asks for. Its write
# price is the one the price table's cache_write_per_million row carries.
CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}
ToolChoice = Literal["auto", "any", "tool"]
JSON_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")


def to_anthropic_tool_choice(choice: ToolChoice, tool_name: str | None = None) -> dict[str, Any]:
    """Map the tool-choice vocabulary onto the Messages API.

    The port expresses two needs: a normal tool round (``auto``) and a forced
    structured-output tool (``tool``). ``any`` is part of the mapping because
    the API supports it and a future request field could ask for it without a
    change here.
    """
    if choice == "tool":
        if not tool_name:
            raise ConfigError("a specific tool_choice needs a tool name")
        return {"type": "tool", "name": tool_name}
    return {"type": choice}


def to_anthropic_messages(
    messages: Sequence[Message], names: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Convert core messages into Messages API turns.

    System messages are handled separately (``to_system_prompt``). A tool
    message becomes a ``tool_result`` block inside a user turn, which is what
    the API expects after an assistant ``tool_use``; consecutive messages with
    the same role are merged into one turn with several blocks, so a tool
    result and the next question share a turn instead of breaking the
    alternating shape.
    """
    translated = names or {}
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        blocks: list[dict[str, Any]] = []
        if message.role == "tool":
            if not message.tool_call_id:
                raise ToolValidationError("a tool message needs the tool_call_id it answers")
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            )
        else:
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.call_id,
                        "name": translated.get(call.name, call.name),
                        "input": call.arguments,
                    }
                )
        if not blocks:
            continue
        role = "assistant" if message.role == "assistant" else "user"
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    return converted


def to_system_prompt(messages: Sequence[Message]) -> str | None:
    """Join every system message into the one system prompt the API takes."""
    parts = [message.content for message in messages if message.role == "system"]
    joined = "\n\n".join(part for part in parts if part.strip())
    return joined or None


def to_anthropic_tools(
    tools: Sequence[ToolSpec], names: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Tool definitions with one cache breakpoint on the last of them.

    Tools are the first part of the stable prompt prefix (design section 8.5).
    A ``cache_control`` on the last definition caches the whole array, which is
    the documented way to mark an explicit breakpoint; the adapter does not
    put one on anything dynamic.
    """
    translated = names or {}
    converted: list[dict[str, Any]] = [
        {
            "name": translated.get(tool.wire_name, tool.wire_name),
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]
    if converted:
        converted[-1]["cache_control"] = dict(CACHE_CONTROL)
    return converted


def _system_blocks(system: str | None) -> list[dict[str, Any]] | None:
    """The system prompt as one block with the stable-prefix cache breakpoint."""
    if not system:
        return None
    return [{"type": "text", "text": system, "cache_control": dict(CACHE_CONTROL)}]


def normalise_usage(usage: Any) -> Usage:
    """Map Messages API usage onto the core record (design.md section 8.3).

    The API reports the prompt as three parts; ``input_tokens`` is their sum
    and the two cache parts stay visible on their own fields. A missing usage
    object leaves every count at zero rather than guessing.
    """
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    fresh = int(getattr(usage, "input_tokens", 0) or 0)
    cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return Usage(
        input_tokens=fresh + cached + written,
        cached_input_tokens=cached,
        cache_write_input_tokens=written,
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def _merge_stream_usage(base: Usage, update: Any) -> Usage:
    """Merge the ``message_delta`` usage into what ``message_start`` reported.

    The delta carries the final output token count. Newer responses also repeat
    the prompt counters there; a zero means "not sent in this event" for those,
    so the start value is kept, and the output count always comes from the
    delta when it is present.
    """
    if update is None:
        return base
    cached = int(getattr(update, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(update, "cache_creation_input_tokens", 0) or 0)
    fresh = int(getattr(update, "input_tokens", 0) or 0)
    output = int(getattr(update, "output_tokens", 0) or 0)
    if fresh or cached or written:
        return Usage(
            input_tokens=fresh + cached + written,
            cached_input_tokens=cached,
            cache_write_input_tokens=written,
            output_tokens=output or base.output_tokens,
        )
    return Usage(
        input_tokens=base.input_tokens,
        cached_input_tokens=base.cached_input_tokens,
        cache_write_input_tokens=base.cache_write_input_tokens,
        output_tokens=output or base.output_tokens,
    )


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    # An unknown type keyword is not guessed at; the schema author is told.
    return False


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Check a decoded JSON value against the schema the caller asked for.

    This is the subset of JSON Schema the platform's requests use: ``type``
    (a string or a list), ``properties``, ``required``, ``additionalProperties:
    false``, ``items`` and ``enum``. Other keywords are ignored rather than
    guessed at, and only object properties are returned as readable
    ``$.field`` paths. A violation is a list of reasons, not an exception, so
    the caller can repair once before raising.
    """
    errors: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, str):
        if expected not in JSON_TYPES:
            errors.append(f"{path}: unsupported schema type {expected!r}")
        elif not _type_matches(value, expected):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return errors
    elif isinstance(expected, list):
        names = [item for item in expected if isinstance(item, str)]
        if not any(_type_matches(value, name) for name in names):
            errors.append(f"{path}: expected one of {', '.join(names)}")
            return errors

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: {value!r} is not one of {enum!r}")

    if isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        if isinstance(required, list):
            missing = [name for name in required if isinstance(name, str) and name not in value]
            if missing:
                errors.append(f"{path}: missing required {', '.join(missing)}")
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, dict):
                errors.extend(validate_against_schema(item, child, f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {name!r}")
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(validate_against_schema(item, items, f"{path}[{index}]"))
    return errors


def _text_from_blocks(blocks: Sequence[Any]) -> tuple[str | None, str | None]:
    """Split assistant content into answer text and reasoning text."""
    texts = [
        block.text
        for block in blocks
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    ]
    reasoning = [
        block.thinking
        for block in blocks
        if getattr(block, "type", None) == "thinking"
        and isinstance(getattr(block, "thinking", None), str)
    ]
    return ("".join(texts) or None, "".join(reasoning) or None)


def _structured_input(message: Any) -> dict[str, Any]:
    """The arguments of the forced structured-output tool call."""
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != STRUCTURED_TOOL_NAME:
            continue
        payload = getattr(block, "input", None)
        if not isinstance(payload, dict):
            raise StructuredOutputError("the structured tool returned no JSON object")
        return payload
    raise StructuredOutputError("the model did not call the structured tool")


def _check_schema(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    problems = validate_against_schema(payload, schema)
    if problems:
        raise StructuredOutputError(
            "structured output does not match the schema: " + "; ".join(problems[:5])
        )


def _tool_calls_from_blocks(
    blocks: Sequence[Any], *, skip_structured: bool, names: dict[str, str] | None = None
) -> list[ToolCall]:
    reverse = {safe: original for original, safe in (names or {}).items()}
    calls: list[ToolCall] = []
    for block in blocks:
        if getattr(block, "type", None) != "tool_use":
            continue
        name = getattr(block, "name", None)
        if not isinstance(name, str) or not name:
            raise ToolValidationError("a tool_use block arrived without a name")
        if skip_structured and name == STRUCTURED_TOOL_NAME:
            continue
        arguments: Any = getattr(block, "input", None)
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ToolValidationError(
                    f"tool {name!r} returned invalid JSON arguments: {exc}",
                    details={"tool": name},
                ) from exc
        if not isinstance(arguments, dict):
            raise ToolValidationError(f"tool {name!r} arguments must be a JSON object")
        call_id = getattr(block, "id", None)
        if not isinstance(call_id, str) or not call_id:
            raise ToolValidationError("a tool_use block arrived without an id")
        calls.append(ToolCall(name=reverse.get(name, name), arguments=arguments, call_id=call_id))
    return calls


def map_error(exc: Exception) -> SwitchboardError:
    """Map an SDK or transport failure onto the core error taxonomy (6.6).

    Rate limits and server errors (including 529 "overloaded") are transient,
    so the fallback decorator may retry them; auth, permission and malformed
    request errors are not retried. Everything the SDK raises that carries a
    status keeps that status in the details for the trace.
    """
    if isinstance(exc, anthropic.APITimeoutError | TimeoutError):
        return ProviderTransientError(
            "the Anthropic request timed out",
            details={"error": type(exc).__name__},
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderTransientError(
            f"could not reach the Anthropic endpoint: {exc}",
            details={"error": type(exc).__name__},
        )
    status = getattr(exc, "status_code", None)
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
    if isinstance(status, int):
        if status == 429 or status >= 500:
            return ProviderTransientError(
                f"the Anthropic request failed with status {status}: {exc}",
                details={"status": status, "error": type(exc).__name__},
            )
        return ProviderRequestError(
            f"the Anthropic request failed with status {status}: {exc}",
            details={"status": status, "error": type(exc).__name__},
        )
    if isinstance(exc, anthropic.APIError):
        return ProviderRequestError(
            f"the Anthropic SDK rejected the request: {exc}",
            details={"error": type(exc).__name__},
        )
    return ProviderRequestError(
        f"the Anthropic call failed: {exc}", details={"error": type(exc).__name__}
    )


class AnthropicProvider:
    """ModelProvider over the Anthropic Messages API (design.md section 8.1)."""

    name: str

    def __init__(
        self,
        *,
        name: str,
        model: str,
        capabilities: ProviderCapabilities,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 120.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        price: PriceEntry | None = None,
        tool_name_pattern: str | None = None,
        extra_body: dict[str, Any] | None = None,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
        tracer: trace.Tracer | None = None,
    ) -> None:
        if capabilities.json_schema:
            raise ConfigError(
                f"provider {name!r} declares json_schema support, but this adapter "
                "implements structured output with a forced tool call; set "
                "capabilities.json_schema to false (design.md section 8.2)"
            )
        self.name = name
        self._model = model
        self._capabilities = capabilities
        self._max_tokens = max_tokens
        self._price = price
        # The Messages API rejects the @major wire name, so the pattern is on
        # even when the config does not repeat it.
        self._tool_name_pattern = tool_name_pattern or ANTHROPIC_TOOL_NAME_PATTERN
        self._extra_body = dict(extra_body or {})
        self._tracer = tracer or trace.get_tracer("switchboard.provider.anthropic")
        self._client = client
        self._client_factory = client_factory
        if client is None and client_factory is None:
            # max_retries=0: retries belong to the fallback decorator, which
            # knows the configured budget (design.md section 8.4). The SDK's own
            # retries would double that budget and hide the first failure.
            self._client = AsyncAnthropic(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_s,
                max_retries=0,
            )

    @classmethod
    def from_config(
        cls, name: str, config: ProviderConfig, price: PriceEntry | None = None
    ) -> AnthropicProvider:
        if not config.model:
            raise ConfigError(f"provider {name!r} needs a model")
        if config.capabilities is None:
            raise ConfigError(f"provider {name!r} needs capabilities")
        api_key = config.api_key or ""
        if not api_key and config.api_key_env:
            api_key = os.environ.get(config.api_key_env, "")
        if not api_key:
            source = config.api_key_env or "config/providers.yaml"
            raise ConfigError(
                f"provider {name!r} needs an api_key; set the environment variable "
                f"named by {source}"
            )
        return cls(
            name=name,
            model=config.model,
            capabilities=config.capabilities,
            api_key=api_key,
            base_url=config.base_url or DEFAULT_BASE_URL,
            timeout_s=config.timeout_s,
            max_tokens=config.max_tokens or DEFAULT_MAX_TOKENS,
            price=price,
            tool_name_pattern=config.tool_name_pattern,
            extra_body=config.extra_body,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _client_or_raise(self) -> Any:
        if self._client is None:
            if self._client_factory is None:
                raise ConfigError(f"provider {self.name!r} has no client")
            self._client = self._client_factory()
        return self._client

    def _tool_names(self, request: CompletionRequest) -> dict[str, str]:
        names = [tool.wire_name for tool in request.tools]
        for message in request.messages:
            names.extend(call.name for call in message.tool_calls)
        return safe_tool_names(names, self._tool_name_pattern)

    def _strategy(self, request: CompletionRequest) -> Literal["tool_call"] | None:
        return "tool_call" if request.response_schema is not None else None

    def _kwargs(
        self,
        request: CompletionRequest,
        messages: list[dict[str, Any]],
        names: dict[str, str],
        *,
        strategy: Literal["tool_call"] | None,
    ) -> dict[str, Any]:
        system = to_system_prompt(request.messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens or self._max_tokens,
            "messages": messages,
        }
        blocks = _system_blocks(system)
        if blocks is not None:
            kwargs["system"] = blocks
        if strategy == "tool_call" and request.response_schema is not None:
            kwargs["tools"] = [
                {
                    "name": STRUCTURED_TOOL_NAME,
                    "description": "Return the structured response.",
                    "input_schema": request.response_schema,
                }
            ]
            kwargs["tool_choice"] = to_anthropic_tool_choice("tool", STRUCTURED_TOOL_NAME)
        elif request.tools:
            kwargs["tools"] = to_anthropic_tools(request.tools, names)
            kwargs["tool_choice"] = to_anthropic_tool_choice("auto")
        for key, value in self._extra_body.items():
            kwargs.setdefault(key, value)
        return kwargs

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        strategy = self._strategy(request)
        if request.stream and not self._capabilities.streaming:
            raise ProviderRequestError(f"provider {self.name!r} does not support streaming")
        if request.stream and strategy is not None:
            raise ProviderRequestError(
                "structured output calls are completed without streaming (design.md section 8.2)"
            )
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
                if request.stream:
                    response = await self._stream(request, started, span)
                else:
                    response = await self._complete(request, strategy, started, span)
            except Exception as exc:
                if isinstance(exc, SwitchboardError):
                    record_error(span, exc)
                    raise
                # Only SDK and transport failures belong to the provider error
                # taxonomy; a bug in the adapter (or in a fake client in a
                # test) must surface as itself, like the OpenAI adapter.
                if not isinstance(exc, anthropic.AnthropicError | TimeoutError):
                    record_error(span, exc)
                    raise
                mapped = map_error(exc)
                record_error(span, mapped)
                raise mapped from exc
            apply_usage(span, response.usage)
            if self._price is not None:
                span.set_attribute(
                    "switchboard.cost_usd", compute_cost(response.usage, self._price)
                )
            span.set_attribute("switchboard.latency_ms", response.latency_ms)
            return response

    async def _complete(
        self,
        request: CompletionRequest,
        strategy: Literal["tool_call"] | None,
        started: float,
        span: Span,
    ) -> CompletionResponse:
        names = self._tool_names(request)
        messages = to_anthropic_messages(request.messages, names)
        usage = Usage(input_tokens=0, output_tokens=0)
        repair: str | None = None

        for attempt in range(2):
            kwargs = self._kwargs(request, messages, names, strategy=strategy)
            if repair is not None:
                kwargs["messages"] = [
                    *messages,
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Your previous reply was not valid for the required "
                                    f"schema: {repair}. Call the tool with a valid object."
                                ),
                            }
                        ],
                    },
                ]
            message = await self._client_or_raise().messages.create(**kwargs)
            usage = usage.plus(normalise_usage(getattr(message, "usage", None)))
            request_id = getattr(message, "id", None)
            if isinstance(request_id, str) and request_id:
                span.set_attribute("gen_ai.response.id", request_id)
            stop_reason = getattr(message, "stop_reason", None)
            if isinstance(stop_reason, str) and stop_reason:
                span.set_attribute("switchboard.provider.stop_reason", stop_reason)
            blocks = list(getattr(message, "content", None) or [])
            try:
                parsed: dict[str, Any] | None = None
                if strategy == "tool_call" and request.response_schema is not None:
                    parsed = _structured_input(message)
                    _check_schema(parsed, request.response_schema)
            except StructuredOutputError as exc:
                if attempt == 1:
                    raise
                repair = str(exc)
                continue

            text, reasoning = _text_from_blocks(blocks)
            tool_calls = _tool_calls_from_blocks(
                blocks, skip_structured=strategy == "tool_call", names=names
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            return CompletionResponse(
                text=text,
                reasoning=reasoning,
                tool_calls=tool_calls,
                parsed=parsed,
                structured_output_strategy=strategy,
                usage=usage,
                model=str(getattr(message, "model", None) or self._model),
                provider=self.name,
                latency_ms=latency_ms,
                cost_usd=compute_cost(usage, self._price) if self._price is not None else None,
            )

        raise StructuredOutputError("structured output failed after one repair")

    async def _stream(
        self, request: CompletionRequest, started: float, span: Span
    ) -> CompletionResponse:
        """Consume the event stream and rebuild the non-streamed response shape.

        Text deltas go to ``request.delta_callback``; thinking deltas are kept
        on the side and never forwarded; ``input_json_delta`` fragments are
        joined per content-block index and parsed exactly as in the
        non-streamed path. Cancelling the awaiting task cancels the SDK request
        and the HTTP stream, so ``CancelledError`` is never converted into a
        provider error.
        """
        names = self._tool_names(request)
        messages = to_anthropic_messages(request.messages, names)
        kwargs = self._kwargs(request, messages, names, strategy=None)
        kwargs["stream"] = True

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_slots: dict[int, dict[str, Any]] = {}
        usage = Usage(input_tokens=0, output_tokens=0)
        model = self._model
        stop_reason: str | None = None

        stream = await self._client_or_raise().messages.create(**kwargs)
        try:
            async for event in stream:
                kind = getattr(event, "type", None)
                if kind == "message_start":
                    message = event.message
                    request_id = getattr(message, "id", None)
                    if isinstance(request_id, str) and request_id:
                        span.set_attribute("gen_ai.response.id", request_id)
                    model = str(getattr(message, "model", None) or model)
                    usage = normalise_usage(getattr(message, "usage", None))
                elif kind == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "tool_use":
                        initial = getattr(block, "input", None)
                        tool_slots[int(event.index)] = {
                            "id": str(getattr(block, "id", "")),
                            "name": str(getattr(block, "name", "")),
                            "json": "",
                            "input": initial if isinstance(initial, dict) else {},
                        }
                    elif getattr(block, "type", None) == "thinking":
                        thinking = getattr(block, "thinking", None)
                        if isinstance(thinking, str):
                            reasoning_parts.append(thinking)
                elif kind == "content_block_delta":
                    delta = event.delta
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "text_delta":
                        text = getattr(delta, "text", None)
                        if isinstance(text, str) and text:
                            text_parts.append(text)
                            if request.delta_callback is not None:
                                request.delta_callback(text)
                    elif delta_type == "input_json_delta":
                        slot = tool_slots.setdefault(
                            int(event.index), {"id": "", "name": "", "json": "", "input": {}}
                        )
                        partial = getattr(delta, "partial_json", None)
                        if isinstance(partial, str):
                            slot["json"] += partial
                    elif delta_type == "thinking_delta":
                        thinking = getattr(delta, "thinking", None)
                        if isinstance(thinking, str):
                            reasoning_parts.append(thinking)
                elif kind == "message_delta":
                    update = getattr(event, "delta", None)
                    new_reason = getattr(update, "stop_reason", None)
                    if isinstance(new_reason, str) and new_reason:
                        stop_reason = new_reason
                    usage = _merge_stream_usage(usage, getattr(event, "usage", None))
            if stop_reason:
                span.set_attribute("switchboard.provider.stop_reason", stop_reason)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                # Closing releases the HTTP stream; a close failure must not
                # replace the response or the cancellation being raised.
                with suppress(Exception):
                    await close()

        calls: list[ToolCall] = []
        reverse = {safe: original for original, safe in names.items()}
        for index in sorted(tool_slots):
            slot = tool_slots[index]
            name = slot["name"]
            if not name:
                raise ToolValidationError("a streamed tool call arrived without a name")
            if slot["json"]:
                try:
                    arguments = json.loads(slot["json"])
                except json.JSONDecodeError as exc:
                    raise ToolValidationError(
                        f"tool {name!r} returned invalid JSON arguments: {exc}",
                        details={"tool": name},
                    ) from exc
            else:
                arguments = slot["input"]
            if not isinstance(arguments, dict):
                raise ToolValidationError(f"tool {name!r} arguments must be a JSON object")
            calls.append(
                ToolCall(
                    name=reverse.get(name, name),
                    arguments=arguments,
                    call_id=slot["id"] or str(index),
                )
            )

        return CompletionResponse(
            text="".join(text_parts) or None,
            reasoning="".join(reasoning_parts) or None,
            tool_calls=calls,
            parsed=None,
            structured_output_strategy=None,
            usage=usage,
            model=model,
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=compute_cost(usage, self._price) if self._price is not None else None,
        )


__all__ = [
    "CACHE_CONTROL",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_TOKENS",
    "STRUCTURED_TOOL_NAME",
    "AnthropicProvider",
    "map_error",
    "normalise_usage",
    "to_anthropic_messages",
    "to_anthropic_tool_choice",
    "to_anthropic_tools",
    "to_system_prompt",
    "validate_against_schema",
]
