"""T2720 - Evaluation calibration for lichess eval records.

Normalizes engine evaluation semantics:
- orientation: cp/mate scores are from the side-to-move's perspective in the
  lichess dump; we keep that perspective explicitly and expose
  white_perspective_cp for consumers that want a single orientation.
- mate encoding: mate N means the side to move mates in N moves; mate -N
  means the side to move gets mated in N moves. Converted deterministically
  to a large-magnitude cp on a fixed scale (mate_cp_base - |N|), never mixed
  with cp values.
- depth/knodes: kept verbatim; quality tiers derived from them.
- engine/version: lichess evals come from Stockfish; the version string is
  carried through when present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MATE_CP_BASE = 100_000  # mate-in-1 -> 99_999, far above any real cp

QUALITY_TIERS = (
    ("deep", 30, 1_000),      # depth >= 30 and knodes >= 1000
    ("standard", 20, 100),    # depth >= 20 and knodes >= 100
    ("shallow", 0, 0),        # everything else
)


@dataclass(frozen=True)
class CalibratedEval:
    fen: str
    side_to_move: str  # "w" or "b" from the FEN
    depth: int
    knodes: int
    cp: int | None        # side-to-move perspective, None when mate
    mate: int | None      # signed, side-to-move perspective
    white_perspective_cp: int  # single-orientation score, mate folded onto cp scale
    quality_tier: str
    engine: str | None


def mate_to_cp(mate: int) -> int:
    if mate == 0:
        raise ValueError("mate 0 is not a valid mate encoding")
    magnitude = MATE_CP_BASE - abs(mate)
    return magnitude if mate > 0 else -magnitude


def to_white_perspective(side_to_move: str, *, cp: int | None, mate: int | None) -> int:
    if (cp is None) == (mate is None):
        raise ValueError("exactly one of cp/mate must be set")
    stm_cp = cp if cp is not None else mate_to_cp(mate)  # type: ignore[arg-type]
    return stm_cp if side_to_move == "w" else -stm_cp


def quality_tier(depth: int, knodes: int) -> str:
    for name, min_depth, min_knodes in QUALITY_TIERS:
        if depth >= min_depth and knodes >= min_knodes:
            return name
    raise AssertionError("unreachable: shallow tier catches all")


def calibrate_eval_row(row: dict[str, Any]) -> CalibratedEval:
    """Calibrate the best eval (deepest pvs entry) of a normalized eval row
    (the shape produced by ingest.schemas.adapt_eval_jsonl)."""
    fen = row["fen"]
    parts = fen.split()
    if len(parts) < 2 or parts[1] not in ("w", "b"):
        raise ValueError(f"cannot determine side to move from FEN: {fen!r}")
    side_to_move = parts[1]
    engine = row.get("engine")
    best = max(row["evals"], key=lambda e: e["depth"])
    pv = best["pvs"][0]
    cp, mate = pv.get("cp"), pv.get("mate")
    return CalibratedEval(
        fen=fen,
        side_to_move=side_to_move,
        depth=best["depth"],
        knodes=best["knodes"],
        cp=cp,
        mate=mate,
        white_perspective_cp=to_white_perspective(side_to_move, cp=cp, mate=mate),
        quality_tier=quality_tier(best["depth"], best["knodes"]),
        engine=engine,
    )
