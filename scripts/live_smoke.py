"""Live smoke for the OpenAI-compatible provider (T1.05).

Requires LM Studio (Bionic) on the host with the chat model loaded:

    uv run --env-file .env python scripts/live_smoke.py

The script prints the structured plan the supervisor would receive, the
structured-output strategy, usage and latency. It is not part of CI.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from switchboard.adapters.providers.openai_compat import OpenAICompatibleProvider
from switchboard.core.models import (
    CompletionRequest,
    Message,
    ProviderCapabilities,
)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "agents": {
            "type": "array",
            "items": {"type": "string", "enum": ["direct", "docs", "data"]},
            "minItems": 1,
        },
        "reason": {"type": "string"},
    },
    "required": ["agents", "reason"],
}

SYSTEM = (
    "You are the routing supervisor of Switchboard. Choose the agents that can "
    "answer the question. direct: small talk only. docs: shop policies. "
    "data: the orders database. Return agents and a one-sentence reason."
)


async def main() -> int:
    base_url = os.environ.get("LMSTUDIO_BASE_URL")
    model = os.environ.get("LMSTUDIO_MODEL")
    if not base_url or not model:
        print("set LMSTUDIO_BASE_URL and LMSTUDIO_MODEL, for example with --env-file .env")
        return 2

    provider = OpenAICompatibleProvider(
        name="lmstudio",
        model=model,
        base_url=base_url,
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
    response = await provider.complete(
        CompletionRequest(
            messages=[
                Message(role="system", content=SYSTEM),
                Message(
                    role="user",
                    content="How many orders were delivered late in March 2018?",
                ),
            ],
            response_schema=PLAN_SCHEMA,
            max_tokens=256,
            temperature=0,
        )
    )

    print(
        json.dumps(
            {
                "plan": response.parsed,
                "strategy": response.structured_output_strategy,
                "usage": response.usage.model_dump(),
                "latency_ms": response.latency_ms,
                "reasoning_chars": len(response.reasoning or ""),
            },
            indent=2,
        )
    )
    return 0 if response.parsed is not None else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
