"""Threshold sweep for the semantic cache suite (design.md section 10.2).

The cache suite labels a pair as ``should_hit`` when the two questions ask for
the same answer, and as a near miss when a similar wording needs a different
answer. For a threshold, a pair is a hit when its cosine similarity is at or
above it:

* hit rate: hits among the pairs that should hit;
* false-hit rate: hits among the pairs that must not hit.

A low threshold raises the hit rate and the false-hit rate together; the report
makes that trade-off visible so the chosen value is a measured decision rather
than a default. The suite reports every threshold in ``THRESHOLDS``.
"""

from __future__ import annotations

from dataclasses import dataclass

THRESHOLDS: tuple[float, ...] = (0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98, 0.99)
CHOSEN_THRESHOLD = 0.94


def threshold_label(threshold: float) -> str:
    """``0.9`` renders as ``0.90`` so a table column keeps two decimals."""
    return f"{threshold:.2f}"


@dataclass(frozen=True)
class ThresholdRow:
    threshold: float
    hits: int
    positives: int
    false_hits: int
    negatives: int

    @property
    def hit_rate(self) -> float:
        return round(self.hits / self.positives, 4) if self.positives else 0.0

    @property
    def false_hit_rate(self) -> float:
        return round(self.false_hits / self.negatives, 4) if self.negatives else 0.0

    @property
    def accuracy(self) -> float:
        total = self.positives + self.negatives
        correct = self.hits + (self.negatives - self.false_hits)
        return round(correct / total, 4) if total else 0.0


def sweep_thresholds(
    pairs: list[tuple[float, bool]], thresholds: tuple[float, ...] = THRESHOLDS
) -> list[ThresholdRow]:
    """Count hits and false hits for each threshold over ``(similarity, label)``."""
    rows: list[ThresholdRow] = []
    for threshold in thresholds:
        hits = sum(1 for similarity, should_hit in pairs if should_hit and similarity >= threshold)
        false_hits = sum(
            1 for similarity, should_hit in pairs if not should_hit and similarity >= threshold
        )
        positives = sum(1 for _similarity, should_hit in pairs if should_hit)
        negatives = len(pairs) - positives
        rows.append(
            ThresholdRow(
                threshold=threshold,
                hits=hits,
                positives=positives,
                false_hits=false_hits,
                negatives=negatives,
            )
        )
    return rows


__all__ = [
    "CHOSEN_THRESHOLD",
    "THRESHOLDS",
    "ThresholdRow",
    "sweep_thresholds",
    "threshold_label",
]
