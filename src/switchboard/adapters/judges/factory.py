"""Build the configured judge through the registry.

The judge is a registered component (``switchboard.judges``), like providers,
retrievers and caches (ADR 0011). ``llm`` is the only adapter in this build: one
injected provider, one structured-output call. A future judge with a different
prompt or model is one class plus one entry point.
"""

from __future__ import annotations

from typing import Any, cast

from switchboard.core.config import JudgeSettings, PriceEntry
from switchboard.core.errors import ConfigError
from switchboard.core.ports import Judge, ModelProvider
from switchboard.registry import JUDGES, Registry

# The registered adapter this build ships. It is named here rather than in the
# settings because the judge has no other adapter to choose between; adding one
# means adding a settings key at the same time.
JUDGE_ADAPTER = "llm"


def create_judge(
    registry: Registry,
    *,
    provider: ModelProvider,
    settings: JudgeSettings,
) -> Judge:
    component: Any = registry.load(JUDGES, JUDGE_ADAPTER)
    return cast(
        Judge,
        component(
            provider=provider,
            threshold=settings.threshold,
            max_evidence_chars=settings.max_evidence_chars,
        ),
    )


def require_unpriced_provider(name: str, price: PriceEntry | None) -> None:
    """Refuse a judge run against a paid provider.

    The judge agreement run and the rag judge mode are measured on the local
    model only. A paid judge would silently add cost to every run and to every
    agreement check, so it is refused here instead of being confirmed with a
    flag; a later task can add an explicit opt-in with a cost cap.
    """
    if price is not None and not price.is_zero:
        raise ConfigError(
            f"the judge must run on a provider with no API cost; {name!r} has a "
            "nonzero price row, so this run is refused rather than billed"
        )


__all__ = ["JUDGE_ADAPTER", "create_judge", "require_unpriced_provider"]
