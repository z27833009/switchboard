"""Price table math (design.md section 12.4).

A pure module: it takes a ``Usage`` and a ``PriceEntry`` and returns USD. It is
used by the provider adapter for the per-call figure on the chat span, by the
orchestrator for the run total and by the eval report writer, so all three
agree on how cached input tokens are billed. No framework imports, no clock and
no network: the price row already says when it was verified.
"""

from __future__ import annotations

from switchboard.core.config import PriceEntry, PricesConfig
from switchboard.core.models import Usage

TOKENS_PER_MILLION = 1_000_000
# Sub-cent amounts matter at these prices, so the result keeps twelve decimals:
# enough for one cached token (0.006 per million is 6e-9) and still free of
# binary floating point noise when a report prints it.
COST_DECIMALS = 12


def compute_cost(usage: Usage, price: PriceEntry) -> float:
    """USD cost of one usage record, with each cached part at its own rate.

    ``Usage.input_tokens`` counts every prompt token the provider reported,
    ``cached_input_tokens`` is the part served from its prefix cache and
    ``cache_write_input_tokens`` the part written to it. Writes are billed at
    the row's write rate, or at the input rate when the row has no separate
    write price, so a row written before that field existed bills exactly as
    before. A provider that reports more cached or written tokens than input
    tokens is clamped rather than billed negative.
    """
    cached = min(usage.cached_input_tokens, usage.input_tokens)
    written = min(usage.cache_write_input_tokens, usage.input_tokens - cached)
    uncached = usage.input_tokens - cached - written
    write_rate = (
        price.input_per_million
        if price.cache_write_per_million is None
        else price.cache_write_per_million
    )
    cost = (
        uncached * price.input_per_million
        + cached * price.cached_input_per_million
        + written * write_rate
        + usage.output_tokens * price.output_per_million
    ) / TOKENS_PER_MILLION
    return round(cost, COST_DECIMALS)


def lookup_price(
    prices: PricesConfig | None, provider: str, model: str | None = None
) -> PriceEntry | None:
    """The price row for a provider and model, or None when there is no entry.

    None means "not priced", never zero: a paid provider without a row must not
    look free (design 12.4).
    """
    if prices is None:
        return None
    return prices.lookup(provider, model)


def cost_usd(
    usage: Usage | None,
    prices: PricesConfig | None,
    provider: str,
    model: str | None = None,
) -> float | None:
    """Cost of a usage record, or None when the provider has no price entry."""
    if usage is None:
        return None
    price = lookup_price(prices, provider, model)
    if price is None:
        return None
    return compute_cost(usage, price)


def add_cost(left: float | None, right: float | None) -> float | None:
    """Add two per-call costs, keeping None as "no priced call recorded".

    A run that mixes providers is costed by summing the per-call figures the
    adapters report (design.md 12.4). None does not mean zero, so it never
    erases a known cost; an unpriced-only sequence stays None.
    """
    if left is None:
        return right
    if right is None:
        return left
    return round(left + right, COST_DECIMALS)


__all__ = [
    "COST_DECIMALS",
    "TOKENS_PER_MILLION",
    "add_cost",
    "compute_cost",
    "cost_usd",
    "lookup_price",
]
