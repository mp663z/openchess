"""Shipped exact graph-node construction and canonical identity."""

from __future__ import annotations

from graph.position_digest import digest_fen
from tools.variant_runtime import identity, parse_position


def make_record(variant: str, snapshot_fen: str) -> dict:
    """Build the exact linked three-field node record from shipped runtimes."""
    try:
        position = parse_position(variant, snapshot_fen)
    except BaseException:
        parts = snapshot_fen.split(" ")
        if len(parts) != 6 or parts[3] == "-":
            raise
        parts[3] = "-"
        position = parse_position(variant, " ".join(parts))
    projection = identity(position)
    canonical = " ".join(
        (
            projection["board"],
            projection["side_to_move"],
            projection["castling_rights"],
            projection["en_passant"],
            "0",
            "1",
        )
    )
    # Reparse normalized output, so returned snapshots are exact-valid.
    parse_position(variant, canonical)
    return {
        "variant": projection["variant"],
        "digest": digest_fen(variant, canonical),
        "snapshot_fen": canonical,
    }


def record_identity(record: dict) -> str:
    """Return the canonical graph-state map key for an exact record."""
    if type(record) is not dict or set(record) != {"variant", "digest", "snapshot_fen"}:
        raise ValueError("exact node record required")
    expected = make_record(record["variant"], record["snapshot_fen"])
    if record != expected:
        raise ValueError("node record disagrees with linked derivation")
    projection = identity(parse_position(record["variant"], record["snapshot_fen"]))
    return repr(tuple(projection.values()))
