"""Bounded, timed execution of eval cases (design.md section 20.2).

One failing case does not stop the run: timeouts and provider errors become a
per-case error and a zero score. A missing mock fixture does stop the run,
because ADR 0008 requires a loud failure rather than a silent default answer.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from switchboard.core.errors import ConfigError, FixtureMissError, SwitchboardError
from switchboard.core.models import EvalCase, EvalOutcome, Score
from switchboard.core.ports import EvalDeps, EvalSuite

DEFAULT_CONCURRENCY = 4
DEFAULT_CASE_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class CaseRun:
    case: EvalCase
    outcome: EvalOutcome
    score: Score
    runner_latency_ms: int


@dataclass(frozen=True)
class Selection:
    cases: list[EvalCase]
    split: str
    dataset_case_count: int
    split_case_count: int
    limited: bool


def select_cases(
    cases: list[EvalCase],
    *,
    split: str = "auto",
    limit: int | None = None,
) -> Selection:
    """Apply the split and the optional smoke limit.

    ``auto`` scores the test split when the dataset has one, and every case when
    it does not (toolcall v1 has no split field).
    """
    available = {case.split for case in cases if case.split is not None}
    if split == "auto":
        effective = "test" if "test" in available else "all"
    elif split in {"dev", "test", "all"}:
        effective = split
    else:
        raise ConfigError(f"unknown split {split!r}; use auto, dev, test or all")

    if effective == "all":
        selected = list(cases)
    else:
        selected = [case for case in cases if case.split == effective]
    if not selected:
        raise ConfigError(f"the dataset has no cases in the {effective!r} split")
    split_case_count = len(selected)

    if limit is not None:
        if limit < 1:
            raise ConfigError("--limit must be at least 1")
        selected = selected[:limit]

    return Selection(
        cases=selected,
        split=effective,
        dataset_case_count=len(cases),
        split_case_count=split_case_count,
        limited=limit is not None,
    )


async def run_cases(
    suite: EvalSuite,
    cases: list[EvalCase],
    deps: EvalDeps,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_s: float = DEFAULT_CASE_TIMEOUT_S,
) -> list[CaseRun]:
    if concurrency < 1:
        raise ConfigError("concurrency must be at least 1")
    if timeout_s <= 0:
        raise ConfigError("the per-case timeout must be positive")

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(case: EvalCase) -> CaseRun:
        async with semaphore:
            started = time.perf_counter()
            try:
                outcome = await asyncio.wait_for(suite.execute(case, deps), timeout_s)
            except TimeoutError:
                outcome = EvalOutcome(
                    case_id=case.id,
                    error=f"case timed out after {timeout_s:g}s",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except FixtureMissError:
                raise
            except SwitchboardError as exc:
                outcome = EvalOutcome(
                    case_id=case.id,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            runner_latency_ms = int((time.perf_counter() - started) * 1000)
            score = suite.score(case, outcome)
            return CaseRun(
                case=case,
                outcome=outcome,
                score=score,
                runner_latency_ms=runner_latency_ms,
            )

    return list(await asyncio.gather(*(run_one(case) for case in cases)))


__all__ = [
    "DEFAULT_CASE_TIMEOUT_S",
    "DEFAULT_CONCURRENCY",
    "CaseRun",
    "Selection",
    "run_cases",
    "select_cases",
]
