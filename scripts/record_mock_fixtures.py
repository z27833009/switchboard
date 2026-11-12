"""Record mock-provider fixtures from a real OpenAI-compatible model.

    uv run --env-file .env python scripts/record_mock_fixtures.py
    uv run --env-file .env python scripts/record_mock_fixtures.py --force

The fixtures land in evals/fixtures/mock_provider and are reviewed and
committed by hand. A changed prompt changes the key, so the old fixture stops
matching and CI asks for a re-record (ADR 0008).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from switchboard.adapters.providers.mock import (
    MockProvider,
    fixture_key,
    normalise_request,
)
from switchboard.adapters.providers.openai_compat import OpenAICompatibleProvider
from switchboard.core.models import (
    CompletionRequest,
    Message,
    ProviderCapabilities,
)

FIXTURES = Path("evals/fixtures/mock_provider")
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


def requests() -> list[CompletionRequest]:
    return [
        CompletionRequest(
            messages=[Message(role="user", content="Reply with the single word OK.")],
            max_tokens=32,
            metadata={"purpose": "demo"},
        ),
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
            metadata={"purpose": "routing"},
        ),
    ]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="replace existing fixtures")
    args = parser.parse_args()

    base_url = os.environ.get("LMSTUDIO_BASE_URL")
    model = os.environ.get("LMSTUDIO_MODEL")
    if not base_url or not model:
        print("set LMSTUDIO_BASE_URL and LMSTUDIO_MODEL, for example with --env-file .env")
        return 2
    FIXTURES.mkdir(parents=True, exist_ok=True)

    real = OpenAICompatibleProvider(
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
    mock = MockProvider(name="mock", model=model, fixtures_dir=FIXTURES, record_with=real)

    for request in requests():
        purpose = str(request.metadata["purpose"])
        if args.force:
            key = fixture_key(normalise_request(request, model=model, purpose=purpose))
            (FIXTURES / f"{key}.json").unlink(missing_ok=True)
        response = await mock.complete(request)
        print(
            json.dumps(
                {
                    "purpose": purpose,
                    "text": response.text,
                    "parsed": response.parsed,
                    "strategy": response.structured_output_strategy,
                    "usage": response.usage.model_dump(),
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
