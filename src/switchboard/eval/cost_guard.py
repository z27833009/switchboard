"""Cost estimate and confirmation guard for eval runs (design.md section 20.2).

A paid provider is never called by surprise. Before a run the guard prints the
provider, model, case count and a deliberately conservative estimate, then
refuses unless the caller passed ``--confirm-cost`` and the estimate is at or
below the cap in ``platform.yaml``.

The estimate assumes no cached input tokens (cached input is cheaper, so this
is an upper bound) and a fixed per-call token budget. It is a planning figure,
not a measurement: the report records the usage the provider reports.
"""

from __future__ import annotations

from dataclasses import dataclass

from switchboard.core.config import PriceEntry

# Conservative planning figures per model call. The routing and toolcall suites
# make one call per case; the rag suite makes up to six (four strategies plus a
# contextual rewrite and a conditional rewrite).
INPUT_TOKENS_PER_CALL = 4000
OUTPUT_TOKENS_PER_CALL = 1000
CALLS_PER_CASE = {
    "routing": 1,
    "toolcall": 1,
    "rag": 6,
    "gate": 0,
    "injection": 0,
}
TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class CostEstimate:
    suite: str
    provider: str
    model: str | None
    case_count: int
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cap_usd: float
    confirmed: bool

    @property
    def requires_confirmation(self) -> bool:
        return self.cost_usd > 0 and not self.confirmed

    @property
    def over_cap(self) -> bool:
        return self.cost_usd > self.cap_usd

    @property
    def allowed(self) -> bool:
        return not self.requires_confirmation and not self.over_cap

    def describe(self) -> str:
        model = self.model or "unknown"
        cases = "case" if self.case_count == 1 else "cases"
        calls = "call" if self.calls == 1 else "calls"
        return (
            f"cost estimate: provider {self.provider}, model {model}, "
            f"{self.case_count} {cases}, {self.calls} model {calls}, "
            f"{self.input_tokens} input and {self.output_tokens} output tokens, "
            f"estimated {self.cost_usd:.6f} USD (cap {self.cap_usd:.4f} USD)"
        )

    def refusal(self) -> str | None:
        """Why the run may not proceed, or None when it may."""
        if self.over_cap:
            return (
                f"the estimate {self.cost_usd:.6f} USD exceeds cost_cap_usd "
                f"{self.cap_usd:.4f}; lower --limit or raise the cap deliberately"
            )
        if self.requires_confirmation:
            return "a paid provider needs --confirm-cost after reviewing the estimate"
        return None


def estimate_eval_cost(
    *,
    suite: str,
    provider: str,
    model: str | None,
    case_count: int,
    price: PriceEntry,
    cap_usd: float,
    confirmed: bool,
) -> CostEstimate:
    """Estimate one eval run from the price row and a conservative token budget."""
    calls_per_case = CALLS_PER_CASE[suite]
    calls = case_count * calls_per_case
    input_tokens = calls * INPUT_TOKENS_PER_CALL
    output_tokens = calls * OUTPUT_TOKENS_PER_CALL
    cost = (
        input_tokens * price.input_per_million + output_tokens * price.output_per_million
    ) / TOKENS_PER_MILLION
    return CostEstimate(
        suite=suite,
        provider=provider,
        model=model,
        case_count=case_count,
        calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost, 12),
        cap_usd=cap_usd,
        confirmed=confirmed,
    )


__all__ = [
    "CALLS_PER_CASE",
    "INPUT_TOKENS_PER_CALL",
    "OUTPUT_TOKENS_PER_CALL",
    "CostEstimate",
    "estimate_eval_cost",
]
