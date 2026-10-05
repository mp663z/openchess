"""T0090: FEN unit and metamorphic property checks of the shipped graph.fen runtime.

Independent of the T0086 reference: properties are stated over the runtime alone.
1. Canonical round trip: every accepted FEN emits back to itself.
2. Colour mirror: swapping colours (reverse ranks, swap case, flip side to move,
   castling letters and en-passant rank) preserves the verdict class.
3. Counter edit: changing the fullmove number within range preserves the verdict.
4. Single-character edits never crash: accept (and round trip) or a typed FenError
   with a closed failure class and its mapped code.
5. Failures are atomic: the same input yields the same verdict before and after refusals.
"""

# ruff: noqa: E501
from __future__ import annotations

import random

import pytest

import graph.fen as fen
from tests import test_t0087_fen_fixture as fix

C = fix.C
CLASSES = set(C["failure_mapping"])
SEEDS = sorted(
    {r["input_fen"] for s in ("happy", "boundary") for r in fix.CASES[s] if "input_fen" in r}
    | {
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
        "r3k2r/8/8/8/8/8/8/R3K2R b Kq - 12 40",
        "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
        "8/8/8/8/8/8/8/8 w - - 0 1",
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 3",
    }
)
CASTLE_ORDER = "KQkq"

# Acceptance oracle: positions that are legal by construction (hand-checked)
# MUST be accepted and round trip exactly. A typed refusal is a failure here,
# so a runtime that refuses valid chess cannot pass on refusal parity alone.
KNOWN_VALID = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2",
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R b Kq - 12 40",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 3",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
    "4k3/P7/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/p7/4K3 b - - 0 1",
    "4k3/8/8/8/8/8/4R3/4K3 b - - 0 1",
    "rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3",
    "4k3/8/8/8/8/8/PPPPPPP1/QQ2K3 w - - 0 1",
    "4k3/8/4K3/8/8/8/8/8 w - - 0 1",
    "4k3/8/8/8/8/8/8/R3K3 w Q - 0 1",
    "4k2r/8/8/8/8/8/8/4K3 b k - 0 1",
    "r3k3/8/8/8/8/8/8/4K3 b q - 0 1",
    "4k3/p7/8/8/8/8/P7/4K3 w - - 0 1",
)
ATTACK_ROWS = (
    "8/8/8/3k4/2P5/8/8/7K w - - 0 1",
    "8/8/8/3k4/4P3/8/8/7K w - - 0 1",
    "7k/8/8/2p5/3K4/8/8/8 b - - 0 1",
    "7k/8/8/4p3/3K4/8/8/8 b - - 0 1",
    "8/8/8/3k4/1N6/8/8/7K w - - 0 1",
    "8/8/8/3k4/5N2/8/8/7K w - - 0 1",
    "8/8/1N6/3k4/8/8/8/7K w - - 0 1",
    "8/8/5N2/3k4/8/8/8/7K w - - 0 1",
    "8/8/8/3k4/8/2N5/8/7K w - - 0 1",
    "8/8/8/3k4/8/4N3/8/7K w - - 0 1",
    "8/2N5/8/3k4/8/8/8/7K w - - 0 1",
    "8/4N3/8/3k4/8/8/8/7K w - - 0 1",
    "3R4/8/7K/8/3k4/8/8/8 w - - 0 1",
    "8/8/7K/8/3k4/8/8/3R4 w - - 0 1",
    "8/8/7K/8/R2k4/8/8/8 w - - 0 1",
    "8/8/7K/8/3k3R/8/8/8 w - - 0 1",
    "8/8/7K/8/3k4/8/8/B7 w - - 0 1",
    "8/8/7K/8/3k4/8/8/6B1 w - - 0 1",
    "8/B7/7K/8/3k4/8/8/8 w - - 0 1",
    "8/6B1/7K/8/3k4/8/8/8 w - - 0 1",
    "3Q4/8/7K/8/3k4/8/8/8 w - - 0 1",
    "8/6Q1/7K/8/3k4/8/8/8 w - - 0 1",
    "8/8/7K/8/Q2k4/8/8/8 w - - 0 1",
    "8/8/8/4K3/k6R/8/8/8 w - - 0 1",
    "8/8/8/4K3/R6k/8/8/8 w - - 0 1",
    "R7/8/8/4K3/8/8/8/k7 w - - 0 1",
    "k7/8/8/4K3/8/8/8/R7 w - - 0 1",
    "7B/8/8/8/8/8/8/k1K5 w - - 0 1",
    "7k/8/8/8/8/8/8/B1K5 w - - 0 1",
    "k7/8/8/8/8/8/8/2K4B w - - 0 1",
    "B7/8/8/8/8/8/8/2K4k w - - 0 1",
    "7k/8/8/1n6/3K4/8/8/8 b - - 0 1",
    "7k/8/8/8/3K4/5n2/8/8 b - - 0 1",
    "7k/8/8/8/r2K4/8/8/8 b - - 0 1",
    "7k/8/8/8/3K3r/8/8/8 b - - 0 1",
    "7k/8/8/8/3K4/8/8/b7 b - - 0 1",
    "7k/6b1/8/8/3K4/8/8/8 b - - 0 1",
)
KING_ROWS = (
    "8/8/8/8/3k4/2K5/8/8 w - - 0 1",
    "8/8/8/8/2Kk4/8/8/8 w - - 0 1",
    "8/8/8/2K5/3k4/8/8/8 w - - 0 1",
    "8/8/8/8/3k4/3K4/8/8 w - - 0 1",
    "8/8/8/3K4/3k4/8/8/8 w - - 0 1",
    "8/8/8/8/3k4/4K3/8/8 w - - 0 1",
    "8/8/8/8/3kK3/8/8/8 w - - 0 1",
    "8/8/8/4K3/3k4/8/8/8 w - - 0 1",
)
# Known-invalid positions with the exact refusal class they must raise.
KNOWN_INVALID = (
    ("4k3/8/8/8/8/8/8/4K03 w - - 0 1", "malformed_fen"),  # zero digit
    ("4k3/8/8/8/8/8/8/31K3 w - - 0 1", "malformed_fen"),  # adjacent digits
    ("4k3/8/8/8/8/8/8/4K3 w  - 0 1", "malformed_fen"),  # empty castling field
    *((text, "impossible_position") for text in ATTACK_ROWS),
    *((text, "impossible_position") for text in KING_ROWS),
    (
        "4k3/8/8/8/8/8/4R3/4K3 w - - 0 1",
        "impossible_position",
    ),  # black king in check, white to move
    (
        "4k3/4r3/8/8/8/8/8/4K3 b - - 0 1",
        "impossible_position",
    ),  # white king in check, black to move
    ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq e3 0 1", "impossible_position"),
    ("8/8/8/8/8/8/8/4K3 w - - 0 1", "impossible_position"),  # no black king
    ("4k3/8/8/8/8/8/8/4K4 w - - 0 1", "malformed_fen"),  # rank sums to 9 files
    ("4kK2/8/8/8/8/8/8/8 w - - 0 1", "impossible_position"),  # kings adjacent
    ("P3k3/8/8/8/8/8/8/4K3 w - - 0 1", "impossible_position"),  # pawn on back rank
    ("4k3/8/8/8/8/8/4K3 w - - 0 1", "malformed_fen"),
    ("4k3/8/8/8/8/8/8/8/4K3 w - - 0 1", "malformed_fen"),
    ("r3k2r/8/8/8/8/8/8/R3K2R w KKkq - 0 1", "malformed_fen"),
    ("r3k2r/8/8/8/8/8/8/R3K2R w QKkq - 0 1", "malformed_fen"),
    ("r3k2r/8/8/8/8/8/8/R3K2R w KQkz - 0 1", "malformed_fen"),
    ("4k3/8/8/8/4P3/8/8/4K3 b - e4 0 1", "malformed_fen"),
    ("4k3/8/8/8/4P3/8/8/4K3 b - z3 0 1", "malformed_fen"),
    ("4k3/8/8/8/8/8/8/8 w - - 0 1", "impossible_position"),
    ("8/8/8/8/8/8/8/4K3 b - - 0 1", "impossible_position"),
    ("4k3/8/8/8/8/8/8/3KK3 w - - 0 1", "impossible_position"),
    ("3kk3/8/8/8/8/8/8/4K3 w - - 0 1", "impossible_position"),
    ("4k3/4K3/8/8/8/8/8/8 w - - 0 1", "impossible_position"),
    ("8/8/8/8/8/8/3k4/4K3 w - - 0 1", "impossible_position"),
    ("4k3/8/8/8/8/8/8/P3K3 w - - 0 1", "impossible_position"),
    ("p3k3/8/8/8/8/8/8/4K3 w - - 0 1", "impossible_position"),
    ("4k3/8/8/8/8/8/8/p3K3 w - - 0 1", "impossible_position"),
    ("4k3/8/8/8/8/8/PPPPPPP1/QQQ1K3 w - - 0 1", "impossible_position"),
    ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNQ w kq - 0 1", "impossible_position"),
    ("4k3/8/8/8/8/8/8/4K3 w K - 0 1", "impossible_position"),
    ("4k3/8/8/8/8/8/8/R4K2 w Q - 0 1", "impossible_position"),
    ("r2k4/8/8/8/8/8/8/4K3 b q - 0 1", "impossible_position"),
    ("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e3 0 1", "impossible_position"),
    ("rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR b KQkq c6 0 2", "impossible_position"),
    ("rnbqkbnr/pppppppp/8/8/8/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1", "impossible_position"),
    ("rnbqkbnr/pppppppp/8/8/4N3/8/PPPP1PPP/RNBQKB1R b KQkq e3 0 1", "impossible_position"),
    ("rnbqkbnr/pppppppp/8/8/4P3/4N3/PPPP1PPP/RNBQKB1R b KQkq e3 0 1", "impossible_position"),
    ("rnbqkbnr/pppppppp/8/8/4P3/8/1PPPPPPP/RNBQKBNR b KQkq e3 0 1", "impossible_position"),
    ("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 5 1", "impossible_position"),
    ("rnbqkbnr/pp1ppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2", "impossible_position"),
)


def verdict(text):
    try:
        position = fen.parse_fen(C, text)
    except fen.FenError as exc:
        assert exc.failure_class in CLASSES
        assert exc.code == C["failure_mapping"][exc.failure_class]["error"]
        return ("err", exc.failure_class)
    assert fen.emit_fen(C, position) == text, text
    return ("ok", None)


def mirror(text):
    placement, color, castling, ep, half, full = text.split(" ")
    ranks = [r.swapcase() for r in reversed(placement.split("/"))]
    if castling != "-":
        castling = "".join(sorted(castling.swapcase(), key=CASTLE_ORDER.index))
    if ep != "-":
        ep = ep[0] + str(9 - int(ep[1]))
    return " ".join(["/".join(ranks), "b" if color == "w" else "w", castling, ep, half, full])


def _accepted_exactly(module, text):
    position = module.parse_fen(C, text)  # a refusal here is a FAILURE, not a verdict
    assert module.emit_fen(C, position) == text
    return position


@pytest.mark.parametrize("text", KNOWN_VALID)
def test_known_valid_positions_are_accepted_and_round_trip_exactly(text):
    _accepted_exactly(fen, text)


@pytest.mark.parametrize("text", KNOWN_VALID)
def test_known_valid_mirror_and_fullmove_edits_are_accepted(text):
    _accepted_exactly(fen, mirror(text))
    parts = text.split(" ")
    for number in ("1", "40", "9999"):
        parts[5] = number
        _accepted_exactly(fen, " ".join(parts))


def test_initial_position_is_accepted_with_the_standard_layout():
    position = _accepted_exactly(fen, KNOWN_VALID[0])
    board = position[0]
    assert len(board) == 32 and position[1:] == ("w", "KQkq", None, 0, 1)


@pytest.mark.parametrize("text,failure_class", KNOWN_INVALID)
def test_known_invalid_positions_raise_the_exact_class(text, failure_class):
    with pytest.raises(fen.FenError) as caught:
        fen.parse_fen(C, text)
    assert caught.value.failure_class == failure_class
    assert caught.value.code == C["failure_mapping"][failure_class]["error"]


@pytest.mark.parametrize("text", SEEDS)
def test_seed_roundtrip_and_mirror_parity(text):
    assert verdict(text) == verdict(mirror(text))
    assert mirror(mirror(text)) == text


@pytest.mark.parametrize("text", SEEDS)
def test_fullmove_edit_keeps_verdict_and_roundtrips(text):
    parts = text.split(" ")
    for number in ("40", "99", "9999"):
        parts[5] = number
        assert verdict(" ".join(parts))[0] == verdict(text)[0]


@pytest.mark.parametrize("seed", [90, 900, 20261005])
def test_single_character_edits_never_crash_and_accepted_ones_round_trip(seed):
    rng = random.Random(seed)
    alphabet = "pnbrqkPNBRQK12345678/ -wbcdefgh0123456789KQkq"
    accepted = rejected = 0
    for text in rng.sample(SEEDS, len(SEEDS)):
        for _ in range(60):
            i = rng.randrange(len(text))
            op = rng.choice(("sub", "del", "ins"))
            ch = rng.choice(alphabet)
            if op == "sub":
                edited = text[:i] + ch + text[i + 1 :]
            elif op == "del":
                edited = text[:i] + text[i + 1 :]
            else:
                edited = text[:i] + ch + text[i:]
            kind, _ = verdict(edited)
            accepted += kind == "ok"
            rejected += kind == "err"
    assert accepted > 0 and rejected > accepted


@pytest.mark.parametrize("bad", [None, 5, b"x", ["a"], "", " ", "x" * 10000, "8/" * 7 + "8\n"])
def test_non_fen_inputs_are_typed_malformed(bad):
    with pytest.raises(fen.FenError) as caught:
        fen.parse_fen(C, bad)
    assert caught.value.failure_class == "malformed_fen"


def test_refusals_do_not_poison_later_parses():
    good = SEEDS[0]
    first = fen.parse_fen(C, good)
    for bad in ("", "8/8 w - - 0 1", good.replace("w", "x"), good + " "):
        with pytest.raises(fen.FenError):
            fen.parse_fen(C, bad)
    assert fen.parse_fen(C, good) == first


def test_run_splitting_and_zero_digit_are_malformed():
    for placement in ("44/8/8/8/8/8/8/8", "0/8/8/8/8/8/8/8", "9/8/8/8/8/8/8/8", "8/8/8/8/8/8/8"):
        assert verdict(f"{placement} w - - 0 1") == ("err", "malformed_fen")


# -- fault injection: one-edit mutants of the runtime must turn the properties red --

import inspect  # noqa: E402
import types  # noqa: E402

MUTANTS = [
    ('if prev_digit and g["adjacent_digits"] == "forbidden":', "if False:"),
    ("if f != g[\"rank_sum\"]:\n            fail(\"malformed_fen\")", "if f > g[\"rank_sum\"]:\n            fail(\"malformed_fen\")"),
    ('if color not in contract["active_color"]["values"]:', "if False:"),
    ('if spec["leading_zeros"] == "forbidden" and value != str(number):', "if False:"),
    ("if number < spec[\"min\"]:", "if False:"),
    ("            str(half),\n            str(full),", "            str(full),\n            str(half),"),
    ("for r in range(len(contract[\"board\"][\"ranks\"]), 0, -1):", "for r in range(1, len(contract[\"board\"][\"ranks\"]) + 1):"),
    ("if type(text) is not str:", "if False:"),
    # refuses the standard initial position (8 pawns / 16 pieces) and nothing a parity check sees
    ("if pawns > pmax or len(mine) > tmax:", "if pawns >= pmax or len(mine) >= tmax:"),
    # disables the non-mover-king-attacked gate
    ('if pr["non_mover_king_attacked"] == "forbidden":', "if False:"),
]  # fmt: skip

# one-gate mutants: each must be killed by the one-violation row paired with it
GATE_MUTANTS = (
    (
        "4k3/8/8/8/8/8/8/4K03 w - - 0 1",
        ('elif ch == "0" and', 'else:\n                fail("malformed_fen")\n        if f !='),
        ('elif ch == "1" and', "else:\n                pass\n        if f !="),
    ),
    (
        "4k3/8/8/8/8/8/8/4K03 w - - 0 1",
        (
            'g["zero_digit"] == "forbidden"',
            'else:\n                fail("malformed_fen")\n        if f !=',
        ),
        ('g["zero_digit"] != "forbidden"', "else:\n                pass\n        if f !="),
    ),
    (
        "4k3/8/8/8/8/8/8/31K3 w - - 0 1",
        'g["adjacent_digits"] == "forbidden"',
        'g["adjacent_digits"] != "forbidden"',
    ),
    ("4k3/8/8/8/8/8/4K3 w - - 0 1", 'if len(rank_list) != g["rank_count"]:', "if False:"),
    (
        "r3k2r/8/8/8/8/8/8/R3K2R w KKkq - 0 1",
        (
            "if len(set(rights)) != len(rights):",
            'if "".join(ch for ch in cs["order"] if ch in rights) != rights:',
        ),
        ("if False:", "if False:"),
    ),
    (
        "r3k2r/8/8/8/8/8/8/R3K2R w QKkq - 0 1",
        'if "".join(ch for ch in cs["order"] if ch in rights) != rights:',
        "if False:",
    ),
    ("4k3/8/8/8/4P3/8/8/4K3 b - e4 0 1", 'or ep[1] not in es["ranks"]:', "or False:"),
    (
        "4k3/8/8/8/8/8/8/3KK3 w - - 0 1",
        'len(wk) != 1 and pr["white_kings"] == "exactly-1"',
        "False",
    ),
    (
        "3kk3/8/8/8/8/8/8/4K3 w - - 0 1",
        'len(bk) != 1 and pr["black_kings"] == "exactly-1"',
        "False",
    ),
    (
        "4k3/4K3/8/8/8/8/8/8 w - - 0 1",
        (
            "if max(abs(wf - bf), abs(wr - br)) <= 1:",
            'if pr["non_mover_king_attacked"] == "forbidden":',
        ),
        ("if False:", "if False:"),
    ),
    ("4k3/8/8/8/8/8/8/P3K3 w - - 0 1", "back = (ranks[0], ranks[-1])", "back = (ranks[-1],)"),
    ("p3k3/8/8/8/8/8/8/4K3 w - - 0 1", "back = (ranks[0], ranks[-1])", "back = (ranks[0],)"),
    (
        "4k3/8/8/8/8/8/8/p3K3 w - - 0 1",
        'if pce in "Pp" and str(r) in back:',
        'if pce in "P" and str(r) in back:',
    ),
    ("4k3/8/8/8/8/8/PPPPPPP1/QQQ1K3 w - - 0 1", "if excess > pmax - pawns:", "if False:"),
    (
        "4k3/8/8/8/8/8/8/4K3 w K - 0 1",
        'board.get((rf, int(home["rook"][1]))) != rook_letter',
        "False",
    ),
    (
        "4k3/8/8/8/8/8/8/R4K2 w Q - 0 1",
        'board.get((kf, int(home["king"][1]))) != king_letter',
        "False",
    ),
    (
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e3 0 1",
        '(requires == "black-to-move" and white_to_move)',
        "(False and white_to_move)",
    ),
    (
        "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR b KQkq c6 0 2",
        'requires == "white-to-move" and not white_to_move',
        "False",
    ),
    (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        'if board.get((f, int(side["to_rank"]))) != pawn:',
        "if False:",
    ),
    (
        "rnbqkbnr/pppppppp/8/8/4P3/4N3/PPPP1PPP/RNBQKB1R b KQkq e3 0 1",
        'if es["target_square"] == "empty" and (f, r) in board:',
        "if False:",
    ),
    (
        "rnbqkbnr/pppppppp/8/8/4P3/8/1PPPPPPP/RNBQKBNR b KQkq e3 0 1",
        'if es["origin_square"] == "empty" and (f, int(side["from_rank"])) in board:',
        "if False:",
    ),
    (
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 5 1",
        'if es["halfmove_clock"] == "must-be-zero" and half_i != 0:',
        "if False:",
    ),
)


def _mutant_module(old, new):
    """Apply one edit, or several when old/new are tuples (a gate that another gate
    also covers can only be exposed by disabling both)."""
    source = inspect.getsource(fen)
    for before, after in zip(
        old if type(old) is tuple else (old,), new if type(new) is tuple else (new,), strict=True
    ):
        assert source.count(before) == 1, before
        source = source.replace(before, after)
    module = types.ModuleType("mutant_fen")
    module.__file__ = fen.__file__
    exec(compile(source, "mutant_fen", "exec"), module.__dict__)  # noqa: S102
    return module


class _StrSubclass(str):
    pass


def _failures(module):
    """Run every property against `module`; return (kind, detail) pairs.

    kind is "semantic" when a property's assertion fails (the runtime gave a
    wrong verdict) and "crash" when the runtime raised something other than a
    typed FenError. A mutant only counts as killed by a semantic failure, so a
    crash can never stand in for a missing assertion.
    """
    found = []

    def run(label, fn):
        try:
            fn()
        except AssertionError as exc:
            found.append(("semantic", f"{label}: {exc}"))
        except Exception as exc:  # noqa: BLE001
            found.append(("crash", f"{label}: {type(exc).__name__}"))

    def check(text):
        try:
            position = module.parse_fen(C, text)
        except module.FenError as exc:
            return ("err", exc.failure_class)
        assert module.emit_fen(C, position) == text
        return ("ok", None)

    def parity(text):
        assert check(text) == check(mirror(text)), text

    def accepted(text):
        assert check(text) == ("ok", None), text

    def refused(text, failure_class):
        assert check(text) == ("err", failure_class), text

    def refuses_non_text(value):
        try:
            module.parse_fen(C, value)
        except module.FenError as exc:
            assert exc.failure_class == "malformed_fen"
        else:
            raise AssertionError(f"accepted non-text {type(value).__name__}")

    for text in SEEDS:
        run(f"parity {text}", lambda t=text: parity(t))
    for text in KNOWN_VALID:
        for variant in (text, mirror(text)):
            run(f"accept {variant}", lambda t=variant: accepted(t))
    for text, failure_class in KNOWN_INVALID:
        run(f"refuse {text}", lambda t=text, c=failure_class: refused(t, c))
    for bad in (
        "44/8/8/8/8/8/8/8 w - - 0 1",
        "4k3/8/8/8/8/8/8/4K3 x - - 0 1",
        "4k3/8/8/8/8/8/8/4K3 w - - 00 1",
        "4k3/8/8/8/8/8/8/4K3 w - - 0 0",
        "7/8/8/8/8/8/8/8 w - - 0 1",
    ):
        run(f"malformed {bad}", lambda t=bad: refused(t, "malformed_fen"))
    for value in (None, 5, _StrSubclass(KNOWN_VALID[0])):
        run(f"non-text {type(value).__name__}", lambda v=value: refuses_non_text(v))
    return found


def test_properties_hold_for_the_shipped_runtime():
    assert _failures(fen) == []


@pytest.mark.parametrize("old,new", MUTANTS, ids=[f"m{i}" for i in range(len(MUTANTS))])
def test_every_mutant_is_killed_by_a_semantic_failure(old, new):
    failures = _failures(_mutant_module(old, new))
    assert any(kind == "semantic" for kind, _ in failures), failures


@pytest.mark.parametrize(
    "row,old,new", GATE_MUTANTS, ids=[f"gate{i}" for i in range(len(GATE_MUTANTS))]
)
def test_each_refusal_gate_mutant_is_killed_by_its_own_row(row, old, new):
    failures = _failures(_mutant_module(old, new))
    own = [d for kind, d in failures if kind == "semantic" and d.startswith(f"refuse {row}")]
    assert own, failures


def test_known_valid_boundary_rows_are_accepted():
    for text in KNOWN_VALID[-6:]:
        assert verdict(text) == ("ok", None), text


ATTACK_MUTANTS = (
    ("board.get((f - df, r - dr)) == pawn", "board.get((f + df, r + dr)) == pawn"),
    ("board.get((f - df, r - dr)) == pawn", "board.get((f - df, r + dr)) == pawn"),
    ('for df, dr in a["knight_deltas"]:', 'for df, dr in a["knight_deltas"][1:]:'),
    ('for df, dr in a["knight_deltas"]:', 'for df, dr in a["knight_deltas"][:-1]:'),
    (
        'board.get((f + df, r + dr)) == ("N" if by_white else "n")',
        'board.get((f, r + dr)) == ("N" if by_white else "n")',
    ),
    (
        'board.get((f + df, r + dr)) == ("N" if by_white else "n")',
        'board.get((f + df, r)) == ("N" if by_white else "n")',
    ),
    ("        for df, dr in directions:", "        for df, dr in directions[1:]:"),
    ("        for df, dr in directions:", "        for df, dr in directions[:-1]:"),
    ("while 0 <= nf < len(files)", "while 1 <= nf < len(files)"),
    ("while 0 <= nf < len(files)", "while 0 <= nf < len(files) - 1"),
    ("and 1 <= nr <= max_rank:", "and 2 <= nr <= max_rank:"),
    ("and 1 <= nr <= max_rank:", "and 1 <= nr < max_rank:"),
    ("want = tuple(ch if by_white else ch.lower() for ch in kinds)", "want = tuple(kinds)"),
    ('(("R", "Q")', '(("R",') if False else ('("R", "Q")', '("R",)'),
    ('("B", "Q")', '("B",)'),
)


@pytest.mark.parametrize(
    "old,new", ATTACK_MUTANTS, ids=[f"attack{i}" for i in range(len(ATTACK_MUTANTS))]
)
def test_attack_geometry_mutants_are_killed_by_an_asymmetric_attacked_king_row(old, new):
    failures = _failures(_mutant_module(old, new))
    own = [
        d
        for kind, d in failures
        if kind == "semantic" and any(d.startswith(f"refuse {row}") for row in ATTACK_ROWS)
    ]
    assert own, failures


def test_king_attack_deltas_are_live_once_the_adjacency_gate_is_off():
    off = "if max(abs(wf - bf), abs(wr - br)) <= 1:"
    for old, new in (
        ('for df, dr in a["king_deltas"]:', 'for df, dr in a["king_deltas"][1:]:'),
        ('for df, dr in a["king_deltas"]:', 'for df, dr in a["king_deltas"][:-1]:'),
        ('board.get((f + df, r + dr)) == ("K" if by_white else "k")', "False"),
    ):
        failures = _failures(_mutant_module((off, old), ("if False:", new)))
        own = [
            d
            for kind, d in failures
            if kind == "semantic" and any(d.startswith(f"refuse {row}") for row in KING_ROWS)
        ]
        assert own, (old, new, failures)
