"""Contract tests for MockProvider, including loud misses and recording."""

import json
from pathlib import Path
from typing import Any

import pytest

from switchboard.adapters.providers.mock import (
    MockProvider,
    fixture_key,
    normalise_request,
)
from switchboard.core.config import ProviderConfig
from switchboard.core.errors import ConfigError, FixtureMissError
from switchboard.core.models import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderCapabilities,
    ToolCall,
    ToolSpec,
    Usage,
)
from switchboard.registry import EMBEDDERS, PROVIDERS, Registry

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "evals" / "fixtures" / "mock_provider"


def request(content: str = "Reply with OK.", purpose: str = "demo") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=content)],
        metadata={"purpose": purpose},
    )


class FakeRecorder:
    name = "lmstudio"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=True,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            text="OK.",
            usage=Usage(input_tokens=5, output_tokens=1),
            model="qwen3-8b",
            provider=self.name,
            latency_ms=12,
        )


def test_fixture_key_is_stable_and_sensitive_to_the_prompt() -> None:
    base = normalise_request(request(), model="qwen3-8b", purpose="demo")
    assert fixture_key(base) == fixture_key(
        normalise_request(request(), model="qwen3-8b", purpose="demo")
    )
    assert fixture_key(base) != fixture_key(
        normalise_request(request("changed"), model="qwen3-8b", purpose="demo")
    )
    assert fixture_key(base) != fixture_key(
        normalise_request(request(), model="qwen3-8b", purpose="routing")
    )
    assert fixture_key(base) != fixture_key(
        normalise_request(request(), model="other", purpose="demo")
    )


async def test_miss_names_the_purpose_and_says_re_record(tmp_path: Path) -> None:
    provider = MockProvider(model="qwen3-8b", fixtures_dir=tmp_path)
    with pytest.raises(FixtureMissError) as caught:
        await provider.complete(request(purpose="routing"))
    message = str(caught.value)
    assert "routing" in message
    assert "re-record" in message.lower()
    assert str(tmp_path) in message


async def test_recording_writes_a_fixture_and_then_replays(tmp_path: Path) -> None:
    recorder = FakeRecorder()
    provider = MockProvider(model="qwen3-8b", fixtures_dir=tmp_path, record_with=recorder)
    recorded = await provider.complete(request())
    assert recorded.text == "OK."
    assert recorder.calls == 1
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1

    replay = MockProvider(model="qwen3-8b", fixtures_dir=tmp_path)
    replayed = await replay.complete(request())
    assert replayed.text == "OK."
    assert replayed.provider == "mock"
    assert replayed.latency_ms == 0


async def test_a_changed_prompt_misses_after_recording(tmp_path: Path) -> None:
    recorder = FakeRecorder()
    provider = MockProvider(model="qwen3-8b", fixtures_dir=tmp_path, record_with=recorder)
    await provider.complete(request())
    replay = MockProvider(model="qwen3-8b", fixtures_dir=tmp_path)
    with pytest.raises(FixtureMissError, match=r"[Rr]e-record"):
        await replay.complete(request("a different prompt"))


def test_from_config_requires_fixtures() -> None:
    with pytest.raises(ConfigError, match="fixtures"):
        MockProvider.from_config("mock", ProviderConfig(adapter="mock"))


def test_registry_resolves_mock_and_embedder_entry_points() -> None:
    registry = Registry()
    assert registry.load(PROVIDERS, "mock") is MockProvider
    assert registry.names(EMBEDDERS) == ["mock", "openai_compatible"]


def test_committed_fixtures_match_their_keys_and_replay() -> None:
    files = sorted(FIXTURES.glob("*.json"))
    assert files, "run scripts/record_mock_fixtures.py to create fixtures"
    for path in files:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        assert path.stem == fixture_key(payload["request"])
        assert payload["purpose"] == payload["request"]["purpose"]
        CompletionResponse.model_validate(payload["response"])


async def test_demo_fixture_replays_from_the_committed_store() -> None:
    files = sorted(FIXTURES.glob("*.json"))
    demo = next(
        path for path in files if json.loads(path.read_text(encoding="utf-8"))["purpose"] == "demo"
    )
    payload: Any = json.loads(demo.read_text(encoding="utf-8"))
    stored = payload["request"]
    request_from_file = CompletionRequest(
        messages=[
            Message(
                role=message["role"],
                content=message["content"],
                name=message["name"],
                tool_call_id=message["tool_call_id"],
                tool_calls=[
                    ToolCall(
                        name=call["name"], arguments=call["arguments"], call_id=call["call_id"]
                    )
                    for call in message["tool_calls"]
                ],
            )
            for message in stored["messages"]
        ],
        tools=[ToolSpec(**tool) for tool in stored["tools"]],
        response_schema=stored["response_schema"],
        metadata={"purpose": stored["purpose"]},
    )
    provider = MockProvider(model=stored["model"], fixtures_dir=FIXTURES)
    response = await provider.complete(request_from_file)
    assert response.provider == "mock"
    assert response.text == payload["response"]["text"]
