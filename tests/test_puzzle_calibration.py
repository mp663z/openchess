"""T2721: puzzle themes, rating, popularity, provenance, solution semantics kept."""

import pytest

from ingest.puzzle_calibration import (
    calibrate_puzzle_row,
    parse_game_url,
    rating_tier,
)
from ingest.schemas import adapt_puzzle_csv_line

HEADER = [
    "PuzzleId", "FEN", "Moves", "Rating", "RatingDeviation", "Popularity",
    "NbPlays", "Themes", "GameUrl", "OpeningTags",
]
LINE = (
    "00008,r6k/pp2r2p/4Rp1Q/3p4/8/1N1P2R1/PqP2bPP/7K b - - 0 24,"
    "f2g3 e6e7 b2b1 b3c1 b1c1 h6c1,1785,75,92,3629,"
    '"crushing hangingPiece short middlegame",'
    "https://lichess.org/yyznGmXS/black#48,Italian_Game"
)


def norm(line: str = LINE):
    return adapt_puzzle_csv_line(
        HEADER, line, source_file="lichess_db_puzzle.csv.zst", license_value="CC0"
    ).row


def test_real_row_calibrates_with_original_semantics():
    c = calibrate_puzzle_row(norm())
    assert c.puzzle_id == "00008"
    assert c.themes == ["crushing", "hangingPiece", "short", "middlegame"]  # verbatim order
    assert c.rating == 1785 and c.rating_deviation == 75 and c.rating_tier == "stable"
    assert c.popularity == 92 and c.nb_plays == 3629
    assert c.game.game_id == "yyznGmXS" and c.game.color == "black" and c.game.ply == 48
    assert c.solution_uci == ["f2g3", "e6e7", "b2b1", "b3c1", "b1c1", "h6c1"]


def test_rating_tiers():
    assert rating_tier(75) == "stable"
    assert rating_tier(76) == "provisional"
    assert rating_tier(150) == "provisional"
    assert rating_tier(151) == "volatile"


def test_game_url_parsing_variants():
    assert parse_game_url("https://lichess.org/yyznGmXS/black#48").ply == 48
    assert parse_game_url("https://lichess.org/yyznGmXS").color is None
    with pytest.raises(ValueError):
        parse_game_url("https://example.com/yyznGmXS")


def test_semantics_enforced_not_repaired():
    bad_pop = norm(LINE.replace(",92,", ",150,"))
    with pytest.raises(ValueError, match="popularity"):
        calibrate_puzzle_row(bad_pop)
    bad_rd = norm(LINE.replace(",75,92,", ",-5,92,"))
    with pytest.raises(ValueError, match="rating deviation"):
        calibrate_puzzle_row(bad_rd)
    bad_move = norm(LINE.replace("f2g3 e6e7", "f2g3 zz99"))
    with pytest.raises(ValueError, match="non-UCI"):
        calibrate_puzzle_row(bad_move)
