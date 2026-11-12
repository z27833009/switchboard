"""Recording one routing fixture from the real provider.

Opt in with both the LM Studio environment and ``SWITCHBOARD_EVAL_RECORD=1``:

    uv run --env-file .env pytest tests/integration/test_eval_record.py -q

It writes into a temporary fixture directory, so the committed fixtures are
never touched by a test run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from switchboard.adapters.providers.factory import create_provider
from switchboard.adapters.providers.mock import MockProvider
from switchboard.config import load_settings
from switchboard.core.models import AgentSpec
from switchboard.core.ports import EvalDeps
from switchboard.eval.datasets import load_cases
from switchboard.eval.suites.routing import RoutingSuite
from switchboard.registry import Registry

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SWITCHBOARD_EVAL_RECORD") != "1" or not os.environ.get("LMSTUDIO_BASE_URL"),
        reason="recording needs LM Studio in .env and SWITCHBOARD_EVAL_RECORD=1",
    ),
]


async def test_record_one_dev_routing_fixture(tmp_path: Path) -> None:
    settings = load_settings(REPO_ROOT / "config")
    registry = Registry()
    recorder = create_provider(registry, "lmstudio", settings.providers.providers["lmstudio"])
    mock = MockProvider(
        name="mock",
        model=settings.providers.providers["lmstudio"].model or "qwen3-8b",
        fixtures_dir=tmp_path,
        record_with=recorder,
    )
    specs: list[AgentSpec] = []
    for value in registry.agents().values():
        agent = value() if isinstance(value, type) else value
        specs.append(agent.spec)

    suite = RoutingSuite(agent_specs=specs, max_plan_size=3)
    case = next(
        item
        for item in load_cases("routing", "v1", REPO_ROOT / "evals" / "datasets")
        if item.split == "dev"
    )
    outcome = await suite.execute(case, EvalDeps(provider=mock))

    assert outcome.usage is not None
    assert outcome.latency_ms > 0
    assert len(list(tmp_path.glob("*.json"))) == 1
