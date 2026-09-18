"""T2232 - Behavioral labels.

'useful' means: the accepted repertoire change is still present in the
repertoire snapshot 30 days later. Proxy labels (engine re-analysis,
human panel) are separate label kinds and never mixed into 'useful'.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class AcceptedChange:
    change_id: str
    line_id: str
    accepted_at: date
    change_payload_hash: str  # content hash of the accepted edit


@dataclass(frozen=True)
class RepertoireSnapshot:
    taken_at: date
    present_change_hashes: frozenset[str]


def useful_label(
    change: AcceptedChange,
    snapshots: list[RepertoireSnapshot],
    *,
    window_days: int = 30,
) -> bool | None:
    """True if the accepted change persists at >= accepted_at + window_days.

    Returns None when no snapshot at or beyond the window exists
    (label unknown - never guessed).
    """
    deadline = change.accepted_at + timedelta(days=window_days)
    later = [s for s in snapshots if s.taken_at >= deadline]
    if not later:
        return None
    latest = max(later, key=lambda s: s.taken_at)
    return change.change_payload_hash in latest.present_change_hashes


@dataclass(frozen=True)
class ProxyLabel:
    """Proxy signals stay separate from the behavioral 'useful' label."""

    kind: str  # "engine_reanalysis" or "human_panel"
    item_id: str
    decision_relevant: bool
