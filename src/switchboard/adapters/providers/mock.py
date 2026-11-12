"""Replay recorded provider responses in CI (ADR 0008).

A fixture is keyed by a hash of purpose, model, normalised messages, tool specs
and response schema. A miss raises ``FixtureMissError`` with the purpose and
the expected path; it never falls back to a default answer. Recording is
enabled by passing the real provider as ``record_with``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from switchboard.core.config import PriceEntry, ProviderConfig
from switchboard.core.errors import ConfigError, FixtureMissError
from switchboard.core.models import (
    CompletionRequest,
    CompletionResponse,
    DeltaCallback,
    Message,
    ProviderCapabilities,
)
from switchboard.core.ports import ModelProvider

DEFAULT_CAPABILITIES = ProviderCapabilities(
    tools=True,
    json_schema=True,
    streaming=True,
    reasoning=False,
    context_window=8192,
)

# A replayed answer is cut into fixed-size fragments so a streamed test run sees
# the same deltas every time, without a network call (design.md section 8.1).
REPLAY_DELTA_CHARS = 12


def replay_answer_deltas(response: CompletionResponse, callback: DeltaCallback | None) -> None:
    """Emit the recorded answer text as deterministic fragments."""
    if callback is None or not response.text:
        return
    text = response.text
    for start in range(0, len(text), REPLAY_DELTA_CHARS):
        callback(text[start : start + REPLAY_DELTA_CHARS])


def normalise_request(request: CompletionRequest, *, model: str, purpose: str) -> dict[str, Any]:
    """Only the fields the model actually sees belong in the fixture key."""

    def message_payload(message: Message) -> dict[str, Any]:
        return {
            "role": message.role,
            "content": message.content,
            "name": message.name,
            "tool_call_id": message.tool_call_id,
            "tool_calls": [
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "call_id": call.call_id,
                }
                for call in message.tool_calls
            ],
        }

    return {
        "purpose": purpose,
        "model": model,
        "messages": [message_payload(message) for message in request.messages],
        "tools": [
            {
                "name": tool.name,
                "version": tool.version,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "risk": tool.risk,
                "tenant_scoped": tool.tenant_scoped,
                "sensitive_args": tool.sensitive_args,
            }
            for tool in request.tools
        ],
        "response_schema": request.response_schema,
    }


def fixture_key(normalised: dict[str, Any]) -> str:
    encoded = json.dumps(
        normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class MockProvider:
    name: str

    def __init__(
        self,
        *,
        name: str = "mock",
        model: str,
        fixtures_dir: Path,
        capabilities: ProviderCapabilities | None = None,
        record_with: ModelProvider | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self._fixtures_dir = Path(fixtures_dir)
        self._capabilities = capabilities or DEFAULT_CAPABILITIES
        self._recorder = record_with

    @classmethod
    def from_config(
        cls, name: str, config: ProviderConfig, price: PriceEntry | None = None
    ) -> MockProvider:
        # ``price`` is accepted so the provider factory has one signature; a
        # replay makes no billable call, so the mock never records a cost.
        if not config.fixtures:
            raise ConfigError(f"provider {name!r} needs a fixtures directory")
        return cls(
            name=name,
            model=config.model or "mock-model",
            fixtures_dir=Path(config.fixtures),
            capabilities=config.capabilities or DEFAULT_CAPABILITIES,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        purpose = str(request.metadata.get("purpose", "completion"))
        normalised = normalise_request(request, model=self._model, purpose=purpose)
        key = fixture_key(normalised)
        path = self._fixtures_dir / f"{key}.json"

        if path.is_file():
            response = self._replay(path, key, normalised, purpose)
            if request.stream and self._capabilities.streaming:
                replay_answer_deltas(response, request.delta_callback)
            return response

        if self._recorder is None:
            raise FixtureMissError(
                f"no fixture for purpose {purpose!r} and model {self._model!r} "
                f"(key {key}). Expected {path}. Re-record with --record and review "
                "the new file before committing it."
            )

        # The recorder receives the same request, so a streaming recording has
        # already delivered its deltas through the callback. Do not replay them
        # a second time.
        response = await self._recorder.complete(request)
        self._save(path, key, normalised, purpose, response)
        return response

    def _replay(
        self,
        path: Path,
        key: str,
        normalised: dict[str, Any],
        purpose: str,
    ) -> CompletionResponse:
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FixtureMissError(f"fixture {path} is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise FixtureMissError(f"fixture {path} is not a JSON object")
        if payload.get("key") != key or payload.get("request") != normalised:
            raise FixtureMissError(
                f"fixture {path} does not match purpose {purpose!r}; re-record it"
            )
        try:
            response = CompletionResponse.model_validate(payload.get("response"))
        except ValidationError as exc:
            raise FixtureMissError(f"fixture {path} has an invalid response: {exc}") from exc
        return response.model_copy(update={"provider": self.name, "latency_ms": 0})

    def _save(
        self,
        path: Path,
        key: str,
        normalised: dict[str, Any],
        purpose: str,
        response: CompletionResponse,
    ) -> None:
        self._fixtures_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": key,
            "purpose": purpose,
            "model": self._model,
            "request": normalised,
            "response": response.model_dump(mode="json"),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )


__all__ = [
    "DEFAULT_CAPABILITIES",
    "MockProvider",
    "fixture_key",
    "normalise_request",
    "replay_answer_deltas",
]
