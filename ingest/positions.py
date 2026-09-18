"""T2719 - Canonical position indexing.

A canonical position key preserves the four fields that define a chess
position: board placement, side to move (turn), castling rights, and the
en-passant square. Half-move and full-move counters are metadata, not
position identity, and are stored but excluded from the key.

Move-order / repertoire context is preserved separately: repertoire_key
hashes the exact move sequence, so two games reaching the same position by
different move orders share a position key but have distinct repertoire keys.

No chess-library dependency: FEN parsing is pure python (the corpus is
trusted for move legality; malformed FEN is rejected, not repaired).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_FEN_RE = re.compile(
    r"^(?P<board>[1-8KQRBNPkqrbnp/]+) (?P<turn>[wb]) "
    r"(?P<castling>K?Q?k?q?|-) (?P<ep>-|[a-h][36]) "
    r"(?P<halfmove>\d+) (?P<fullmove>\d+)$"
)


@dataclass(frozen=True)
class PositionRecord:
    board: str
    turn: str
    castling: str
    en_passant: str
    halfmove: int
    fullmove: int

    @property
    def canonical_key(self) -> str:
        return f"{self.board}|{self.turn}|{self.castling}|{self.en_passant}"


def parse_fen(fen: str) -> PositionRecord:
    m = _FEN_RE.match(fen.strip())
    if not m:
        raise ValueError(f"malformed FEN: {fen!r}")
    board = m.group("board")
    ranks = board.split("/")
    if len(ranks) != 8:
        raise ValueError(f"FEN has {len(ranks)} ranks, expected 8")
    for rank in ranks:
        width = sum(int(c) if c.isdigit() else 1 for c in rank)
        if width != 8:
            raise ValueError(f"FEN rank {rank!r} has width {width}, expected 8")
    return PositionRecord(
        board=board,
        turn=m.group("turn"),
        castling=m.group("castling"),
        en_passant=m.group("ep"),
        halfmove=int(m.group("halfmove")),
        fullmove=int(m.group("fullmove")),
    )


def canonical_position_key(fen: str) -> str:
    return parse_fen(fen).canonical_key


def repertoire_key(moves: list[str]) -> str:
    """Move-order-sensitive key: the exact sequence defines the repertoire path."""
    return hashlib.sha256(" ".join(moves).encode()).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    position_pk TEXT PRIMARY KEY,
    board TEXT NOT NULL,
    turn TEXT NOT NULL,
    castling TEXT NOT NULL,
    en_passant TEXT NOT NULL,
    first_seen_fen TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS repertoire_paths (
    repertoire_pk TEXT PRIMARY KEY,
    position_pk TEXT NOT NULL,
    move_count INTEGER NOT NULL,
    source_file TEXT NOT NULL
);
"""


class PositionIndex:
    def __init__(self, db_path: str | Path):
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(SCHEMA)

    def add(self, fen: str, *, moves: list[str], source_file: str) -> str:
        rec = parse_fen(fen)
        self.db.execute(
            "INSERT INTO positions (position_pk, board, turn, castling, en_passant, first_seen_fen)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(position_pk) DO UPDATE SET occurrences = occurrences + 1",
            (rec.canonical_key, rec.board, rec.turn, rec.castling, rec.en_passant, fen.strip()),
        )
        if moves:
            self.db.execute(
                "INSERT OR IGNORE INTO repertoire_paths"
                " (repertoire_pk, position_pk, move_count, source_file) VALUES (?,?,?,?)",
                (repertoire_key(moves), rec.canonical_key, len(moves), source_file),
            )
        self.db.commit()
        return rec.canonical_key

    def occurrences(self, fen: str) -> int:
        row = self.db.execute(
            "SELECT occurrences FROM positions WHERE position_pk=?",
            (canonical_position_key(fen),),
        ).fetchone()
        return row[0] if row else 0

    def repertoire_count(self, fen: str) -> int:
        return self.db.execute(
            "SELECT count(*) FROM repertoire_paths WHERE position_pk=?",
            (canonical_position_key(fen),),
        ).fetchone()[0]

    def close(self) -> None:
        self.db.close()
