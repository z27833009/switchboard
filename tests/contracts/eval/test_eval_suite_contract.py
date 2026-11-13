"""EvalSuite contract for the five registered suites.

Every suite is built through ``create_suite`` and the entry point registry, so
the test covers the adapter selection the CLI uses, not a direct import. The
shared cases are load (a non-empty dataset with unique case ids), execute (an
``EvalOutcome`` for the case) and score (a ``Score`` for that outcome).
Routing, toolcall, gate and injection execute with a scripted deterministic
provider and no network. The rag suite needs the corpus and the real embedder,
so its execute case is integration-marked and skips without the stack; its load
and score cases run everywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest
from tests.contracts.support import (
    CONFIG_DIR,
    DATASET_ROOT,
    REPO_ROOT,
    ScriptedProvider,
    completion,
    registered_agent_specs,
    settings,
)

from switchboard.adapters.embedders.factory import create_embedder
from switchboard.adapters.retrievers.ingest import ingest_directory
from switchboard.config import load_settings
from switchboard.core.models import EvalCase, EvalOutcome, Score, ToolCall
from switchboard.core.ports import EvalDeps, EvalSuite
from switchboard.eval.manifest import load_tool_specs
from switchboard.eval.suite import SuiteContext, create_suite
from switchboard.eval.suites.rag import STRATEGY_NAMES, RagSuite
from switchboard.registry import EVAL_SUITES, Registry

SUITE_VERSIONS = {
    "routing": "v1",
    "toolcall": "v1",
    "gate": "v2",
    "rag": "v3",
    "injection": "v4",
}

DATABASE_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
LMSTUDIO_URL = os.environ.get("LMSTUDIO_BASE_URL", "")


def suite_context() -> SuiteContext:
    tools = load_tool_specs(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml")
    allowed = settings().tenants.tenants["default"].tools.allow
    return SuiteContext(
        dataset_root=DATASET_ROOT,
        agent_specs=registered_agent_specs(),
        tools=tools,
        max_plan_size=3,
        settings=settings(),
        tenant_tools=[spec for spec in tools if spec.wire_name in set(allowed)],
        retriever_configured=True,
    )


def build_suite(name: str) -> EvalSuite:
    return create_suite(Registry(), name, suite_context())


@dataclass(frozen=True)
class SuiteContract:
    name: str
    version: str
    suite: EvalSuite


@pytest.fixture(
    params=list(SUITE_VERSIONS),
    ids=list(SUITE_VERSIONS),
)
def contract(request: pytest.FixtureRequest) -> SuiteContract:
    name = str(request.param)
    return SuiteContract(name=name, version=SUITE_VERSIONS[name], suite=build_suite(name))


def first_case(suite: EvalSuite, version: str) -> EvalCase:
    return suite.load(version)[0]


def test_the_registry_exposes_the_five_suites() -> None:
    assert Registry().names(EVAL_SUITES) == sorted(SUITE_VERSIONS)


async def test_load_returns_a_frozen_dataset(contract: SuiteContract) -> None:
    cases = contract.suite.load(contract.version)
    assert contract.suite.name == contract.name
    assert cases
    assert all(isinstance(case, EvalCase) for case in cases)
    ids = [case.id for case in cases]
    assert len(set(ids)) == len(ids)
    assert all(case.query.strip() for case in cases)


def provider_for(name: str, case: EvalCase) -> ScriptedProvider:
    if name == "routing":
        return ScriptedProvider(
            [completion(parsed={"agents": list(case.expected_agents), "reason": "contract"})]
        )
    if name == "toolcall":
        expected_tool = case.expected_tool
        if expected_tool is None:
            raise AssertionError(f"case {case.id} has no expected tool")
        call = ToolCall(
            name=expected_tool,
            arguments=dict(case.expected_args),
            call_id="contract-call",
        )
        return ScriptedProvider([completion(tool_calls=[call])])
    return ScriptedProvider([])


async def test_execute_and_score_are_the_same_loop_without_io(contract: SuiteContract) -> None:
    if contract.name == "rag":
        pytest.skip("the rag execute case is integration-backed; see below")
    case = first_case(contract.suite, contract.version)
    provider = provider_for(contract.name, case)
    outcome = await contract.suite.execute(case, EvalDeps(provider=provider))
    assert isinstance(outcome, EvalOutcome)
    assert outcome.case_id == case.id
    score = contract.suite.score(case, outcome)
    assert isinstance(score, Score)
    assert score.case_id == case.id
    assert score.metrics
    if contract.name in {"routing", "toolcall"}:
        assert score.passed is True
    if contract.name in {"gate", "injection"}:
        # Deterministic suites never call the model.
        assert provider.requests == []


async def test_rag_load_and_score_work_without_the_corpus(contract: SuiteContract) -> None:
    if contract.name != "rag":
        pytest.skip("only the rag suite has a score-only path")
    cases = contract.suite.load(contract.version)
    case = cases[0]
    strategies: dict[str, Any] = {name: {"recall_at_5": 1.0, "mrr": 1.0} for name in STRATEGY_NAMES}
    score = contract.suite.score(
        case,
        EvalOutcome(case_id=case.id, details={"strategies": strategies}),
    )
    assert score.passed is True
    assert len(score.metrics) == 2 * len(STRATEGY_NAMES)
    assert score.metrics["recall_at_5_conditional_hybrid"] == 1.0


@pytest.mark.integration
@pytest.mark.skipif(
    not (DATABASE_URL and LMSTUDIO_URL),
    reason="set SWITCHBOARD_DATABASE_URL and LMSTUDIO_BASE_URL",
)
async def test_rag_executes_all_four_strategies_on_the_real_corpus() -> None:
    real = load_settings(CONFIG_DIR)
    suite = RagSuite.from_context(
        SuiteContext(dataset_root=DATASET_ROOT, agent_specs=registered_agent_specs(), settings=real)
    )
    cases = suite.load(SUITE_VERSIONS["rag"])
    case = cases[0]
    embedder_name = real.platform.embedder
    embedder = create_embedder(embedder_name, real.providers.embedders[embedder_name])
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=2)
    try:
        await ingest_directory(pool, embedder, REPO_ROOT / "data" / "documents", "default")
        provider = ScriptedProvider(text=case.query)
        outcome = await suite.execute(case, EvalDeps(provider=provider))
        assert outcome.case_id == case.id
        strategies = outcome.details["strategies"]
        assert set(strategies) == set(STRATEGY_NAMES)
        assert all(payload["error"] is None for payload in strategies.values())
        score = suite.score(case, outcome)
        assert score.case_id == case.id
        assert score.metrics
    finally:
        await pool.close()
