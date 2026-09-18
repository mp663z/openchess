"""T2721 - Puzzle calibration: themes, rating, popularity, provenance, solutions.

The lichess puzzle schema is preserved with original semantics:
- themes: exact tag strings, never renamed or merged; family tags (e.g.
  "mateIn2" implies "mate") are NOT inferred - the dump's own tags stand.
- rating: Glicko-2 rating with rating_deviation; quality tier from deviation.
- popularity: -100..100 score plus raw play count, kept verbatim.
- game provenance: GameUrl points at the source game + ply; parsed into
  game_id and ply so the join back to the games corpus stays exact.
- solution lines: UCI move list kept complete and ordered; truncation is
  rejected, never silently accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GAME_URL_RE = re.compile(r"^https://lichess\.org/([A-Za-z0-9]+)(?:/(white|black))?(?:#(\d+))?$")

RD_TIERS = (
    ("stable", 75),      # rating deviation <= 75: well-established
    ("provisional", 150),
    ("volatile", 10_000),
)


@dataclass(frozen=True)
class GameProvenance:
    game_id: str
    color: str | None
    ply: int | None


@dataclass(frozen=True)
class CalibratedPuzzle:
    puzzle_id: str
    rating: int
    rating_deviation: int
    rating_tier: str
    popularity: int
    nb_plays: int
    themes: list[str]
    game: GameProvenance
    solution_uci: list[str]


def parse_game_url(url: str) -> GameProvenance:
    m = GAME_URL_RE.match(url)
    if not m:
        raise ValueError(f"unparseable game URL: {url!r}")
    return GameProvenance(
        game_id=m.group(1),
        color=m.group(2),
        ply=int(m.group(3)) if m.group(3) else None,
    )


def rating_tier(rating_deviation: int) -> str:
    for name, max_rd in RD_TIERS:
        if rating_deviation <= max_rd:
            return name
    raise AssertionError("unreachable: volatile tier catches all")


def calibrate_puzzle_row(row: dict) -> CalibratedPuzzle:
    """Calibrate a normalized puzzle row (ingest.schemas.adapt_puzzle_csv_line)."""
    moves = row["moves"]
    if not moves or not all(re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", m) for m in moves):
        raise ValueError(f"solution line missing or contains non-UCI moves: {moves!r}")
    if row["rating_deviation"] < 0:
        raise ValueError("negative rating deviation")
    if not -100 <= row["popularity"] <= 100:
        raise ValueError(f"popularity out of lichess range: {row['popularity']}")
    return CalibratedPuzzle(
        puzzle_id=row["puzzle_id"],
        rating=row["rating"],
        rating_deviation=row["rating_deviation"],
        rating_tier=rating_tier(row["rating_deviation"]),
        popularity=row["popularity"],
        nb_plays=row["nb_plays"],
        themes=list(row["themes"]),  # verbatim, order preserved
        game=parse_game_url(row["game_url"]),
        solution_uci=list(moves),
    )
