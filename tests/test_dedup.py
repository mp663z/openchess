"""T2718: identity rules pass duplicate and conflicting-version fixtures."""

from ingest.dedup import DedupStore, identity_of
from ingest.schemas import adapt_eval_jsonl, adapt_game_pgn, adapt_puzzle_csv_line

PGN_A = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abcd1234"]
[White "alice"]
[Black "bob"]
[Result "1-0"]
[UTCDate "2025.03.01"]
[UTCTime "10:00:01"]

1. e4 e5 2. Nf3 Nc6 1-0"""

PUZZLE_HEADER = [
    "PuzzleId", "FEN", "Moves", "Rating", "RatingDeviation", "Popularity",
    "NbPlays", "Themes", "GameUrl", "OpeningTags",
]


def puzzle_line(pid: str, rating: int) -> str:
    return (
        f"{pid},r6k/pp2r2p/4Rp1Q/3p4/8/1N1P2R1/PqP2bPP/7K b - - 0 24,"
        f"f2g3 e6e7 b2b1 b3c1,{rating},75,92,3629,"
        '"crushing short",https://lichess.org/yyznGmXS/black#48,Italian_Game'
    )


def game(source_file: str):
    return adapt_game_pgn(PGN_A, source_file=source_file, license_value="CC0")


def test_game_duplicate_keeps_one_row_and_both_provenances(tmp_path):
    store = DedupStore(tmp_path / "d.db")
    assert store.add(game("2025-03.pgn.zst"), source_file="2025-03.pgn.zst") == "inserted"
    assert store.add(game("2025-04.pgn.zst"), source_file="2025-04.pgn.zst") == "duplicate"
    assert store.count_records() == 1
    assert store.provenance(game("x")) == ["2025-03.pgn.zst", "2025-04.pgn.zst"]
    assert store.conflicts() == []
    store.close()


def test_puzzle_conflicting_version_recorded_with_provenance(tmp_path):
    store = DedupStore(tmp_path / "d.db")
    v1 = adapt_puzzle_csv_line(
        PUZZLE_HEADER, puzzle_line("00008", 1785), source_file="p1.csv", license_value="CC0"
    )
    v2 = adapt_puzzle_csv_line(
        PUZZLE_HEADER, puzzle_line("00008", 1790), source_file="p2.csv", license_value="CC0"
    )
    assert store.add(v1, source_file="p1.csv") == "inserted"
    assert store.add(v2, source_file="p2.csv") == "conflict"
    assert store.count_records() == 1  # first retained, not silently overwritten
    conflicts = store.conflicts()
    assert len(conflicts) == 1 and conflicts[0][0] == "puzzle:00008"
    assert store.provenance(v1) == ["p1.csv", "p2.csv"]  # both provenances retained
    store.close()


def test_eval_identity_is_fen(tmp_path):
    line = (
        '{"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",'
        '"evals":[{"knodes":100,"depth":40,"pvs":[{"cp":15,"line":"d2d4"}]}]}'
    )
    e1 = adapt_eval_jsonl(line, source_file="e1.jsonl", license_value="CC0")
    e2 = adapt_eval_jsonl(line, source_file="e2.jsonl", license_value="CC0")
    assert identity_of(e1).startswith("eval:rnbqkbnr")
    store = DedupStore(tmp_path / "d.db")
    assert store.add(e1, source_file="e1.jsonl") == "inserted"
    assert store.add(e2, source_file="e2.jsonl") == "duplicate"
    assert store.count_records() == 1
    store.close()


def test_game_without_site_uses_player_date_movetext_identity():
    pgn = PGN_A.replace('[Site "https://lichess.org/abcd1234"]\n', "")
    n = adapt_game_pgn(pgn, source_file="x", license_value="CC0")
    assert identity_of(n).startswith("game:anon:")
