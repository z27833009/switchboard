"""The eval cost guard is arithmetic and policy, no network."""

from __future__ import annotations

import pytest

from switchboard.core.config import PriceEntry
from switchboard.eval.cost_guard import CostEstimate, estimate_eval_cost

PAID = PriceEntry(
    provider="deepseek",
    model="deepseek-flash",
    input_per_million=0.30,
    cached_input_per_million=0.006,
    output_per_million=1.20,
    source="https://api-docs.deepseek.com/quick_start/pricing",
)
LOCAL = PriceEntry(
    provider="lmstudio",
    model=None,
    input_per_million=0.0,
    cached_input_per_million=0.0,
    output_per_million=0.0,
)


def estimate(
    price: PriceEntry, cases: int = 10, cap: float = 5.0, confirmed: bool = False
) -> CostEstimate:
    return estimate_eval_cost(
        suite="routing",
        provider=price.provider,
        model=price.model,
        case_count=cases,
        price=price,
        cap_usd=cap,
        confirmed=confirmed,
    )


def test_the_estimate_counts_one_call_per_routing_case() -> None:
    result = estimate(PAID, cases=10)

    assert result.calls == 10
    assert result.input_tokens == 40_000
    assert result.output_tokens == 10_000
    assert result.cost_usd == pytest.approx(0.024)
    assert result.requires_confirmation is True
    assert result.allowed is False
    assert "--confirm-cost" in (result.refusal() or "")


def test_a_local_zero_price_needs_no_confirmation() -> None:
    result = estimate(LOCAL)

    assert result.cost_usd == 0.0
    assert result.requires_confirmation is False
    assert result.over_cap is False
    assert result.allowed is True
    assert result.refusal() is None


def test_confirmation_allows_a_run_under_the_cap() -> None:
    result = estimate(PAID, confirmed=True)
    assert result.allowed is True
    assert result.refusal() is None


def test_the_cap_refuses_even_with_confirmation() -> None:
    result = estimate(PAID, cap=0.001, confirmed=True)
    assert result.over_cap is True
    assert result.allowed is False
    assert "exceeds cost_cap_usd" in (result.refusal() or "")


def test_the_rag_suite_budgets_more_calls_than_routing() -> None:
    routing = estimate(PAID, cases=1)
    rag = estimate_eval_cost(
        suite="rag",
        provider="deepseek",
        model="deepseek-flash",
        case_count=1,
        price=PAID,
        cap_usd=5.0,
        confirmed=False,
    )
    assert rag.calls == 6
    assert rag.cost_usd > routing.cost_usd


def test_a_deterministic_suite_makes_no_calls() -> None:
    result = estimate_eval_cost(
        suite="gate",
        provider="deepseek",
        model="deepseek-flash",
        case_count=30,
        price=PAID,
        cap_usd=5.0,
        confirmed=False,
    )
    assert result.calls == 0
    assert result.cost_usd == 0.0
    assert result.allowed is True
