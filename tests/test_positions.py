"""T2719: canonical positions preserve turn, castling, en-passant, move-order context."""

import pytest

from ingest.positions import (
    PositionIndex,
    canonical_position_key,
    parse_fen,
    repertoire_key,
)

BASE = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"


def test_turn_is_identity():
    w = canonical_position_key(f"{BASE} w KQkq - 0 1")
    b = canonical_position_key(f"{BASE} b KQkq - 0 1")
    assert w != b


def test_castling_is_identity():
    full = canonical_position_key(f"{BASE} w KQkq - 0 1")
    none = canonical_position_key(f"{BASE} w - - 0 1")
    partial = canonical_position_key(f"{BASE} w Kq - 0 1")
    assert len({full, none, partial}) == 3


def test_en_passant_is_identity():
    ep_set = canonical_position_key(f"{BASE} w KQkq e3 0 1")
    ep_unset = canonical_position_key(f"{BASE} w KQkq - 0 1")
    assert ep_set != ep_unset


def test_counters_are_not_identity():
    a = canonical_position_key(f"{BASE} w KQkq - 0 1")
    b = canonical_position_key(f"{BASE} w KQkq - 17 42")
    assert a == b


def test_move_order_context_distinct_same_position(tmp_path):
    idx = PositionIndex(tmp_path / "p.db")
    # Same final position reached by two different move orders.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
    idx.add(fen, moves=["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"], source_file="g1.pgn")
    idx.add(fen, moves=["g1f3", "e7e5", "e2e4", "b8c6", "f1c4"], source_file="g2.pgn")
    assert idx.occurrences(fen) == 2  # one canonical position
    assert idx.repertoire_count(fen) == 2  # two move-order contexts retained
    idx.close()


def test_repertoire_key_order_sensitive():
    assert repertoire_key(["e2e4", "e7e5"]) != repertoire_key(["e7e5", "e2e4"])
    assert repertoire_key(["e2e4"]) == repertoire_key(["e2e4"])


def test_malformed_fen_rejected():
    for bad in ["", "not a fen", f"{BASE} x KQkq - 0 1", f"{BASE} w KQkq e4 0 1",
                "8/8/8/8/8/8/8 w - - 0 1", f"{BASE}/{BASE[2:]} w KQkq - 0 1"]:
        with pytest.raises(ValueError):
            parse_fen(bad)


def test_structured_fields_roundtrip():
    rec = parse_fen("r6k/pp2r2p/4Rp1Q/3p4/8/1N1P2R1/PqP2bPP/7K b - - 0 24")
    assert rec.turn == "b" and rec.castling == "-" and rec.en_passant == "-"
    assert rec.fullmove == 24
