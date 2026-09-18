"""T2715: versioned schemas preserve source identifiers and license fields."""

import pytest

from ingest.schemas import (
    SCHEMA_VERSION,
    adapt_eval_jsonl,
    adapt_game_pgn,
    adapt_puzzle_csv_line,
)

PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abcd1234"]
[Date "2025.03.01"]
[White "alice"]
[Black "bob"]
[Result "1-0"]
[UTCDate "2025.03.01"]
[UTCTime "10:00:01"]
[WhiteElo "1500"]
[BlackElo "1520"]
[TimeControl "300+0"]
[Termination "Normal"]

1. e4 e5 2. Nf3 Nc6 1-0"""

PUZZLE_HEADER = [
    "PuzzleId", "FEN", "Moves", "Rating", "RatingDeviation", "Popularity",
    "NbPlays", "Themes", "GameUrl", "OpeningTags",
]
PUZZLE_LINE = (
    "00008,r6k/pp2r2p/4Rp1Q/3p4/8/1N1P2R1/PqP2bPP/7K b - - 0 24,"
    "f2g3 e6e7 b2b1 b3c1 b1c1 h6c1,1785,75,92,3629,"
    '"crushing hangingPiece short middlegame",'
    "https://lichess.org/yyznGmXS/black#48,Italian_Game Italian_Game_Classical_Variation"
)

EVAL_LINE = (
    '{"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",'
    '"evals":[{"knodes":3558,"depth":44,'
    '"pvs":[{"cp":15,"line":"d2d4 d7d5"}]}]}'
)


def test_game_adapter_preserves_identifiers_headers_license():
    n = adapt_game_pgn(
        PGN, source_file="lichess_db_standard_rated_2025-03.pgn.zst", license_value="CC0"
    )
    assert n.schema_name == "game" and n.schema_version == SCHEMA_VERSION
    assert n.row["identifiers"]["Site"] == "https://lichess.org/abcd1234"
    assert n.row["identifiers"]["White"] == "alice"
    assert n.row["headers"]["WhiteElo"] == "1500"  # every header preserved
    assert n.row["license"] == "CC0"
    assert n.row["movetext"].endswith("1-0")


def test_puzzle_adapter_real_format():
    n = adapt_puzzle_csv_line(
        PUZZLE_HEADER, PUZZLE_LINE, source_file="lichess_db_puzzle.csv.zst", license_value="CC0"
    )
    assert n.schema_name == "puzzle"
    assert n.row["identifiers"] == {
        "PuzzleId": "00008",
        "GameUrl": "https://lichess.org/yyznGmXS/black#48",
    }
    assert n.row["rating"] == 1785 and "crushing" in n.row["themes"]
    assert n.row["license"] == "CC0"


def test_eval_adapter_real_format():
    n = adapt_eval_jsonl(EVAL_LINE, source_file="lichess_db_eval.jsonl.zst", license_value="CC0")
    assert n.schema_name == "eval"
    assert n.row["identifiers"]["fen"].startswith("rnbqkbnr")
    assert n.row["evals"][0]["pvs"][0]["cp"] == 15
    assert n.row["license"] == "CC0"


def test_unknown_license_fail_closed():
    with pytest.raises(ValueError, match="fail-closed"):
        adapt_game_pgn(PGN, source_file="x", license_value="proprietary")
    with pytest.raises(ValueError, match="fail-closed"):
        adapt_puzzle_csv_line(
            PUZZLE_HEADER, PUZZLE_LINE, source_file="x", license_value="unknown"
        )
    with pytest.raises(ValueError, match="fail-closed"):
        adapt_eval_jsonl(EVAL_LINE, source_file="x", license_value="")


def test_malformed_records_rejected_not_repaired():
    with pytest.raises(ValueError):
        adapt_game_pgn("1. e4 e5", source_file="x", license_value="CC0")  # no headers
    with pytest.raises(ValueError):
        adapt_eval_jsonl('{"no_fen": true}', source_file="x", license_value="CC0")
