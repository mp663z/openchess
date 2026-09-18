"""T2233 - False-positive cost model.

Review minutes, dismissal, mute/unsubscribe and next-open effect form a
precision-recall-cost frontier. A surfaced item that teaches the user to
stop opening the inbox is the named death of this product (report s9/s13).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SurfacedOutcome:
    item_id: str
    decision_relevant: bool
    review_minutes: float
    dismissed: bool = False
    muted: bool = False
    unsubscribed: bool = False
    next_open_delay_days: float = 0.0  # observed delay of the next inbox open


@dataclass(frozen=True)
class FrontierPoint:
    threshold: float
    precision: float
    recall: float
    minutes_per_relevant: float
    mute_rate: float
    mean_next_open_delay: float


def frontier_point(threshold: float, scored: list[tuple[float, SurfacedOutcome]]) -> FrontierPoint:
    """Precision/recall/cost over items at or above a threshold.

    `scored` pairs each item's score with its outcome. Recall is over all
    decision-relevant items in the population passed in.
    """
    shown = [o for s, o in scored if s >= threshold]
    relevant_total = sum(1 for _, o in scored if o.decision_relevant)
    relevant_shown = sum(1 for o in shown if o.decision_relevant)
    precision = relevant_shown / len(shown) if shown else 1.0
    recall = relevant_shown / relevant_total if relevant_total else 0.0
    minutes = sum(o.review_minutes for o in shown)
    mutes = sum(1 for o in shown if o.muted or o.unsubscribed)
    delays = [o.next_open_delay_days for o in shown]
    return FrontierPoint(
        threshold=threshold,
        precision=precision,
        recall=recall,
        minutes_per_relevant=minutes / relevant_shown if relevant_shown else float("inf"),
        mute_rate=mutes / len(shown) if shown else 0.0,
        mean_next_open_delay=sum(delays) / len(delays) if delays else 0.0,
    )


def frontier(
    thresholds: list[float], scored: list[tuple[float, SurfacedOutcome]]
) -> list[FrontierPoint]:
    return [frontier_point(t, scored) for t in sorted(thresholds)]
