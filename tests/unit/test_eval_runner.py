"""Runner: split and limit selection, bounded concurrency, timeout, fixture miss."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import pytest

from switchboard.core.errors import ConfigError, FixtureMissError, ProviderRequestError
from switchboard.core.models import (
    EvalCase,
    EvalOutcome,
    ProviderCapabilities,
    Score,
    Usage,
)
from switchboard.core.ports import EvalDeps, EvalSuite
from switchboard.eval.runner import run_cases, select_cases


class FakeProvider:
    name = "fake"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: Any) -> Any:
        raise AssertionError("the runner test suite does not call the provider")


class ProbeSuite:
    name = "probe"

    def __init__(
        self,
        *,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.delay = delay
        self.error = error
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    def load(self, version: str) -> list[EvalCase]:
        return []

    async def execute(self, case: EvalCase, deps: EvalDeps) -> EvalOutcome:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(case.id)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        if self.error is not None:
            raise self.error
        return EvalOutcome(
            case_id=case.id,
            usage=Usage(input_tokens=2, output_tokens=1),
            latency_ms=1,
        )

    def score(self, case: EvalCase, outcome: EvalOutcome) -> Score:
        return Score(case_id=case.id, metrics={"ok": 1.0}, passed=True)


def case(case_id: str, split: Literal["dev", "test"] | None = None) -> EvalCase:
    return EvalCase(id=case_id, query=f"query {case_id}", split=split)


def cases() -> list[EvalCase]:
    return [
        case("r001", "dev"),
        case("r002", "test"),
        case("r003", "test"),
        case("r004", "dev"),
    ]


def test_select_cases_auto_uses_test_split() -> None:
    selected = select_cases(cases())
    assert [item.id for item in selected.cases] == ["r002", "r003"]
    assert selected.split == "test"
    assert selected.dataset_case_count == 4
    assert selected.split_case_count == 2
    assert selected.limited is False


def test_select_cases_all_and_limit_marks_smoke() -> None:
    selected = select_cases(cases(), split="all", limit=3)
    assert [item.id for item in selected.cases] == ["r001", "r002", "r003"]
    assert selected.split == "all"
    assert selected.split_case_count == 4
    assert selected.limited is True


def test_select_cases_without_split_uses_every_case() -> None:
    selected = select_cases([case("t001"), case("t002")])
    assert selected.split == "all"
    assert len(selected.cases) == 2


def test_select_cases_rejects_unknown_split_and_empty_split() -> None:
    with pytest.raises(ConfigError, match="unknown split"):
        select_cases(cases(), split="holdout")
    with pytest.raises(ConfigError, match="no cases"):
        select_cases([case("t001")], split="test")
    with pytest.raises(ConfigError, match="at least 1"):
        select_cases(cases(), split="all", limit=0)


async def test_run_cases_is_bounded_and_keeps_order() -> None:
    suite: EvalSuite = ProbeSuite(delay=0.03)
    results = await run_cases(
        suite,
        [case(f"c{index}") for index in range(6)],
        EvalDeps(provider=FakeProvider()),
        concurrency=2,
    )
    assert isinstance(suite, ProbeSuite)
    assert suite.max_active == 2
    assert [result.case.id for result in results] == [f"c{index}" for index in range(6)]
    assert all(result.outcome.usage is not None for result in results)


async def test_run_cases_turns_a_timeout_into_a_case_error() -> None:
    results = await run_cases(
        ProbeSuite(delay=0.2),
        [case("slow")],
        EvalDeps(provider=FakeProvider()),
        timeout_s=0.01,
    )
    assert results[0].outcome.error is not None
    assert "timed out" in results[0].outcome.error
    assert results[0].outcome.usage is None


async def test_run_cases_turns_provider_errors_into_case_errors() -> None:
    results = await run_cases(
        ProbeSuite(error=ProviderRequestError("upstream refused")),
        [case("e001")],
        EvalDeps(provider=FakeProvider()),
    )
    assert results[0].outcome.error == "ProviderRequestError: upstream refused"


async def test_run_cases_lets_a_fixture_miss_stop_the_run() -> None:
    with pytest.raises(FixtureMissError, match="re-record"):
        await run_cases(
            ProbeSuite(error=FixtureMissError("no fixture, re-record with --record")),
            [case("m001")],
            EvalDeps(provider=FakeProvider()),
        )


def test_eval_deps_only_needs_a_provider() -> None:
    deps = EvalDeps(provider=FakeProvider())
    assert deps.provider.name == "fake"
    assert deps.orchestrator is None
