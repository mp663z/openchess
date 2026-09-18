"""T2716: bounded memory, cancellation/restart safety, no duplicate rows."""

import sqlite3
import tracemalloc
from pathlib import Path

import zstandard as zstd

from ingest.streaming import Importer, game_key, iter_pgn_games

GAME = """[Event "Fixture {i}"]
[Site "https://lichess.org/fx{i}"]
[White "A{i}"]
[Black "B{i}"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
"""


def make_pgn_zst(path: Path, n_games: int) -> int:
    cctx = zstd.ZstdCompressor()
    payload = "\n\n".join(GAME.format(i=i) for i in range(n_games))
    path.write_bytes(cctx.compress(payload.encode()))
    return len(payload)


def test_iterates_all_games(tmp_path):
    src = tmp_path / "f.pgn.zst"
    make_pgn_zst(src, 50)
    games = list(iter_pgn_games(src))
    assert len(games) == 50
    assert games[0][0] == 0 and games[49][0] == 49
    assert '[Event "Fixture 0"]' in games[0][1]


def test_bounded_memory_on_multimegabyte_input(tmp_path):
    src = tmp_path / "big.pgn.zst"
    raw_size = make_pgn_zst(src, 4000)  # several MB of raw PGN
    tracemalloc.start()
    n = sum(1 for _ in iter_pgn_games(src, chunk_size=1 << 16))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert n == 4000
    assert peak < 4 * 1024 * 1024, f"peak {peak} bytes on {raw_size} raw bytes"
    assert peak < raw_size  # never holds the whole file


def test_restart_after_cancel_no_duplicate_rows(tmp_path):
    src = tmp_path / "f.pgn.zst"
    make_pgn_zst(src, 200)
    db = tmp_path / "out.db"

    imp = Importer(db)
    partial = imp.import_file(src, limit=120)  # simulated cancellation point
    imp.close()
    assert partial["games_emitted"] == 120

    imp2 = Importer(db)  # restart: re-import the whole file
    full = imp2.import_file(src)
    assert full["games_emitted"] == 200
    assert imp2.count_games() == 200  # idempotent: no duplicates
    imp2.close()

    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT count(DISTINCT game_pk) FROM games").fetchone()[0]
    assert rows == 200


def test_game_key_stable_and_normalized():
    a = game_key('[Event "X"]\n\n1. e4 1-0\n')
    b = game_key('[Event "X"]  \n\n1. e4 1-0')
    assert a == b
    assert a != game_key('[Event "Y"]\n\n1. e4 1-0')


def test_plain_pgn_supported(tmp_path):
    src = tmp_path / "f.pgn"
    src.write_text("\n\n".join(GAME.format(i=i) for i in range(3)))
    assert len(list(iter_pgn_games(src))) == 3
