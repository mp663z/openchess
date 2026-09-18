"""T2716 - Bounded-memory streaming PGN decompression and idempotent import.

Reads .pgn.zst (or plain .pgn) in fixed-size chunks, yielding one game at a
time; memory stays bounded regardless of file size. Import is idempotent:
rows are keyed by a content hash with INSERT OR IGNORE, so a cancel/restart
cycle re-reads bytes but never duplicates rows.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

CHUNK_SIZE = 1 << 20  # 1 MiB read chunks

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_pk TEXT PRIMARY KEY,
    seq_first_seen INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    game_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    games_emitted INTEGER NOT NULL,
    rows_inserted INTEGER NOT NULL,
    finished_at TEXT DEFAULT (datetime('now'))
);
"""


def _raw_chunks(path: Path, chunk_size: int) -> Iterator[bytes]:
    if path.suffix == ".zst":
        import zstandard as zstd

        dctx = zstd.ZstdDecompressor(max_window_size=2**27)
        with path.open("rb") as fh, dctx.stream_reader(fh) as reader:
            while chunk := reader.read(chunk_size):
                yield chunk
    else:
        with path.open("rb") as fh:
            while chunk := fh.read(chunk_size):
                yield chunk


def iter_pgn_games(path: str | Path, chunk_size: int = CHUNK_SIZE) -> Iterator[tuple[int, str]]:
    """Yield (sequence_no, game_text). Buffer holds at most one partial game
    plus one chunk; a new game starts at a '\\n\\n[' tag-block boundary."""
    path = Path(path)
    buf = ""
    seq = 0
    for chunk in _raw_chunks(path, chunk_size):
        buf += chunk.decode("utf-8", errors="replace")
        while (idx := buf.find("\n\n[", 1)) != -1:
            game, buf = buf[:idx], buf[idx + 2 :]
            if game.strip():
                yield seq, game.strip("\n")
                seq += 1
    if buf.strip():
        yield seq, buf.strip("\n")


def game_key(game_text: str) -> str:
    """Deterministic content key: sha256 of normalized game text."""
    norm = "\n".join(line.rstrip() for line in game_text.strip().splitlines())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class Importer:
    """Idempotent importer: restart-safe because rows are keyed by content hash."""

    def __init__(self, db_path: str | Path):
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(SCHEMA)

    def import_file(
        self, path: str | Path, limit: int | None = None, chunk_size: int = CHUNK_SIZE
    ) -> dict:
        source = Path(path).name
        emitted = 0
        inserted = 0
        for seq, game in iter_pgn_games(path, chunk_size):
            emitted += 1
            cur = self.db.execute(
                "INSERT OR IGNORE INTO games"
                " (game_pk, seq_first_seen, source_file, game_text) VALUES (?,?,?,?)",
                (game_key(game), seq, source, game),
            )
            inserted += cur.rowcount
            if limit is not None and emitted >= limit:
                break
            if emitted % 500 == 0:
                self.db.commit()
        self.db.commit()
        self.db.execute(
            "INSERT INTO import_runs (source_file, games_emitted, rows_inserted) VALUES (?,?,?)",
            (source, emitted, inserted),
        )
        self.db.commit()
        return {"games_emitted": emitted, "rows_inserted": inserted}

    def count_games(self) -> int:
        return self.db.execute("SELECT count(*) FROM games").fetchone()[0]

    def close(self) -> None:
        self.db.close()
