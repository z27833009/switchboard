"""Live check that the OpenAI-compatible adapter streams from LM Studio.

The unit and contract tests use a fake async stream. This one makes a real call
when the environment names LM Studio, and skips otherwise (CI has no model).
"""

from __future__ import annotations

import os

import pytest

from switchboard.adapters.providers.openai_compat import OpenAICompatibleProvider
from switchboard.core.models import (
    CompletionRequest,
    Message,
    ProviderCapabilities,
    Usage,
)

LMSTUDIO_URL = os.environ.get("LMSTUDIO_BASE_URL", "")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen3-8b")

pytestmark = pytest.mark.skipif(
    not LMSTUDIO_URL, reason="set LMSTUDIO_BASE_URL to run the live stream check"
)


async def test_lmstudio_streams_answer_deltas_that_rebuild_the_answer() -> None:
    provider = OpenAICompatibleProvider(
        name="lmstudio",
        model=LMSTUDIO_MODEL,
        base_url=LMSTUDIO_URL,
        api_key="lm-studio",
        capabilities=ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=True,
            reasoning=False,
            context_window=8192,
        ),
        extra_body={"reasoning_effort": "none"},
    )
    deltas: list[str] = []

    response = await provider.complete(
        CompletionRequest(
            messages=[
                Message(
                    role="user",
                    content="Reply with one short sentence about late deliveries.",
                )
            ],
            max_tokens=64,
            stream=True,
            delta_callback=deltas.append,
        )
    )

    assert deltas, "the live provider returned no answer fragments"
    assert "".join(deltas) == response.text
    assert response.usage != Usage(input_tokens=0, output_tokens=0)
    assert response.usage.output_tokens > 0
    assert response.tool_calls == []
