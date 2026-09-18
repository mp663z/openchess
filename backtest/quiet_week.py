"""T2234 - Quiet-week rule.

No padding: when zero deltas clear the high-confidence threshold, the
surface shows a gain-framed prepared state - never filler items.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoredDelta:
    line_id: str
    score: float
    confidence: float


PREPARED_STATE = "No new deltas this week - your preparation is current."


def surface(
    deltas: list[ScoredDelta],
    *,
    score_threshold: float,
    confidence_threshold: float,
    max_items: int = 3,
) -> list[ScoredDelta] | str:
    """Top deltas above BOTH thresholds, or the prepared-state message.

    Below-threshold items are never shown to fill space.
    """
    strong = [
        d for d in deltas if d.score >= score_threshold and d.confidence >= confidence_threshold
    ]
    if not strong:
        return PREPARED_STATE
    strong.sort(key=lambda d: d.score, reverse=True)
    return strong[:max_items]
