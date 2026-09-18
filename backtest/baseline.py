"""T2231 - Naive baseline ranker.

Exact rule (nothing else): any new 2500+ game in a repertoire ECO,
sorted by recency, top-k. Tests prove no hidden feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BaselineGame:
    game_id: str
    eco: str
    white_elo: int | None
    black_elo: int | None
    played_at: date


def naive_rank(
    games: list[BaselineGame],
    repertoire_ecos: set[str],
    *,
    min_elo: int = 2500,
    top_k: int = 5,
) -> list[BaselineGame]:
    """The entire baseline: ECO membership, 2500+ on either side, recency."""
    def qualifies(g: BaselineGame) -> bool:
        if g.eco not in repertoire_ecos:
            return False
        return (g.white_elo or 0) >= min_elo or (g.black_elo or 0) >= min_elo

    hits = [g for g in games if qualifies(g)]
    hits.sort(key=lambda g: (g.played_at, g.game_id), reverse=True)
    return hits[:top_k]
