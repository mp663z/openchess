"""T0086: the FEN contract is the enforced source of truth.

The normative contract (data/contracts/fen.yaml) declares the
six-field shape, placement grammar, position rules, castling and
en-passant consistency, counters, canonical serialization, and
all-or-nothing parse atomicity. This battery proves the behavior the
contract promises with an EXECUTABLE reference model DERIVED FROM the
contract data (grammar, rules, error mapping all come from the yaml,
never hardcoded), then replays mutations: contradiction, reversal,
bool/int drift, rogue keys, undeclared error codes, and linkage
mutations against the real sibling contracts. A semantic or
structural slip must fail both the lint and the model checks.
"""

from __future__ import annotations

import copy
import functools
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.fen_contract_lint import lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "fen.yaml"
DATA_DIR = ROOT / "data" / "contracts"


def _doc():
    return yaml.safe_load(CONTRACT.read_text())


def _lint():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fen_contract_lint.py")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


# -- executable reference model, fully derived from the contract ----
# Board geometry, the attack relation, castling home squares and the
# en-passant advance geometry are all read from the structured
# contract data (and its LINKED sibling contracts), never hardcoded:
# a matching defect between this model and the contract cannot
# self-certify because there is exactly one source.

SIBLING_DIR = ROOT / "data" / "contracts"


@functools.cache
def _sibling(name):
    with open(SIBLING_DIR / name) as fh:
        return yaml.safe_load(fh)["contract"]


def _castling_homes():
    return _sibling("castling.yaml")["rights"]["home_squares"]


def _ep_set_on():
    return _sibling("en_passant.yaml")["target"]["set_on"]


def _attack(board_contract, square, board, by_white, _pawn_deltas=None):
    """Attack relation derived from contract.board.attack.

    A pawn attacks along its OWN capture deltas, so the attacker of
    `square` stands at the INVERSE origin (f - df, r - dr) of each
    declared attacker delta - the only asymmetric relation here (knight
    and king deltas are sign-symmetric and the slider walk starts at
    the target, so both directions of the relation coincide)."""
    a = board_contract["attack"]
    files = board_contract["files"]
    f, r = square
    pawn_deltas = (_pawn_deltas if _pawn_deltas is not None else
                   (a["white_pawn_capture_deltas"] if by_white
                    else a["black_pawn_capture_deltas"]))
    pawn = "P" if by_white else "p"
    for df, dr in pawn_deltas:
        if board.get((f - df, r - dr)) == pawn:
            return True
    for df, dr in a["knight_deltas"]:
        if board.get((f + df, r + dr)) == ("N" if by_white else "n"):
            return True
    for df, dr in a["king_deltas"]:
        if board.get((f + df, r + dr)) == ("K" if by_white else "k"):
            return True
    sliders = ((a["rook_directions"], ("R", "Q")),
               (a["bishop_directions"], ("B", "Q")))
    max_rank = len(board_contract["ranks"])
    for directions, kinds in sliders:
        want = tuple(ch if by_white else ch.lower() for ch in kinds)
        for df, dr in directions:
            nf, nr = f + df, r + dr
            while 0 <= nf < len(files) and 1 <= nr <= max_rank:
                piece = board.get((nf, nr))
                if piece:
                    if piece in want:
                        return True
                    break
                nf, nr = nf + df, nr + dr
    return False


class FenError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


START_OFFICERS = {"q": 1, "r": 2, "b": 2, "n": 2}


def parse_fen(contract, text):
    """Contract-derived parser: returns a position tuple or raises
    FenError carrying the contract's failure class and error code.
    All-or-nothing per contract.atomicity: no partial state."""
    mapping = contract["failure_mapping"]
    files = contract["board"]["files"]
    ranks = contract["board"]["ranks"]

    def fail(cls):
        raise FenError(cls, mapping[cls]["error"])

    parts = text.split(" ")
    if (len(parts) != len(contract["fields"]["order"])
            or any(part == "" for part in parts)):
        fail("malformed_fen")
    placement, color, castling, ep, half, full = parts

    # placement grammar
    g = contract["placement"]
    rank_list = placement.split(g["rank_separator"])
    if len(rank_list) != g["rank_count"]:
        fail("malformed_fen")
    letters = set(g["piece_letters"])
    digits = set(g["empty_run_digits"])
    board = {}
    top_rank = len(ranks)  # rank_order rank8-to-rank1
    for ri, rank in enumerate(rank_list):
        rank_no = top_rank - ri
        if not rank:
            fail("malformed_fen")
        f = 0
        prev_digit = False
        for ch in rank:
            if ch in digits:
                if prev_digit and g["adjacent_digits"] == "forbidden":
                    fail("malformed_fen")
                f += int(ch)
                prev_digit = True
            elif ch == "0" and g["zero_digit"] == "forbidden":
                fail("malformed_fen")
            elif ch in letters:
                board[(f, rank_no)] = ch
                f += 1
                prev_digit = False
            else:
                fail("malformed_fen")
        if f != g["rank_sum"]:
            fail("malformed_fen")

    # active color
    if color not in contract["active_color"]["values"]:
        fail("malformed_fen")
    white_to_move = color == "w"

    # counters: ASCII digits only, no leading zeros, declared bounds
    for value, spec in ((half, contract["counters"]["halfmove_clock"]),
                        (full, contract["counters"]["fullmove_number"])):
        if spec["grammar"] == "ascii-digits-0-9-only" and not re.fullmatch(
                r"[0-9]+", value):
            fail("malformed_fen")
        if (spec["leading_zeros"] == "forbidden"
                and value != str(int(value))):
            fail("malformed_fen")
        if int(value) < spec["min"]:
            fail("malformed_fen")
    half_i, full_i = int(half), int(full)

    # castling grammar
    cs = contract["castling"]
    if castling == cs["none_sentinel"]:
        rights = ""
    else:
        rights = castling
        if not rights or any(ch not in cs["letters"] for ch in rights):
            fail("malformed_fen")
        if len(set(rights)) != len(rights):
            fail("malformed_fen")
        if "".join(ch for ch in cs["order"] if ch in rights) != rights:
            fail("malformed_fen")

    # en passant grammar
    es = contract["en_passant"]
    if ep == es["none_sentinel"]:
        ep_square = None
    else:
        if (len(ep) != 2 or ep[0] not in files
                or ep[1] not in es["ranks"]):
            fail("malformed_fen")
        ep_square = ep

    # position rules
    pr = contract["position_rules"]
    wk = [sq for sq, pce in board.items() if pce == "K"]
    bk = [sq for sq, pce in board.items() if pce == "k"]
    if (len(wk) != 1 and pr["white_kings"] == "exactly-1") or (
        len(bk) != 1 and pr["black_kings"] == "exactly-1"
    ):
        fail("impossible_position")
    if pr["kings_adjacent"] == "forbidden":
        (wf, wr), (bf, br) = wk[0], bk[0]
        if max(abs(wf - bf), abs(wr - br)) <= 1:
            fail("impossible_position")
    if pr["pawns_on_back_ranks"] == "forbidden":
        back = (ranks[0], ranks[-1])
        for (_f, r), pce in board.items():
            if pce in "Pp" and str(r) in back:
                fail("impossible_position")
    # material feasibility: counts and promotion budget
    for side_letter, pmax, tmax in (
            ("white", pr["white_pawns_max"], pr["white_pieces_max"]),
            ("black", pr["black_pawns_max"], pr["black_pieces_max"])):
        mine = [pce for pce in board.values()
                if (pce.isupper() == (side_letter == "white"))]
        pawns = sum(1 for pce in mine if pce.lower() == "p")
        if pawns > pmax or len(mine) > tmax:
            fail("impossible_position")
        if pr["promotion_budget"] == (
                "excess-officers-over-start-set-covered-by-missing-"
                "pawns"):
            excess = 0
            for kind, start in START_OFFICERS.items():
                have = sum(1 for pce in mine if pce.lower() == kind)
                excess += max(0, have - start)
            if excess > pmax - pawns:
                fail("impossible_position")
    if pr["non_mover_king_attacked"] == "forbidden":
        mover_white = white_to_move
        non_mover = bk[0] if mover_white else wk[0]
        if _attack(contract["board"], non_mover, board,
                   by_white=mover_white):
            fail("impossible_position")

    # castling consistency: right requires king + rook on the home
    # squares OF THE LINKED CASTLING CONTRACT
    homes = _castling_homes()
    for right in rights:
        home = homes[right]
        kf = files.index(home["king"][0])
        rf = files.index(home["rook"][0])
        king_letter = "K" if right.isupper() else "k"
        rook_letter = "R" if right.isupper() else "r"
        if (board.get((kf, int(home["king"][1]))) != king_letter
                or board.get((rf, int(home["rook"][1]))) != rook_letter):
            fail("impossible_position")

    # en passant consistency, geometry from the LINKED contract's
    # set_on: target empty, origin empty, halfmove clock zero
    if ep_square is not None:
        set_on = _ep_set_on()
        f, r = files.index(ep_square[0]), int(ep_square[1])
        white_advanced = str(r) == set_on["white"]["target_rank"]
        side = set_on["white" if white_advanced else "black"]
        requires = (es["rank3_requires"] if white_advanced
                    else es["rank6_requires"])
        if ((requires == "black-to-move" and white_to_move)
                or (requires == "white-to-move" and not white_to_move)):
            fail("impossible_position")
        pawn = "P" if white_advanced else "p"
        if board.get((f, int(side["to_rank"]))) != pawn:
            fail("impossible_position")
        if es["target_square"] == "empty" and (f, r) in board:
            fail("impossible_position")
        if es["origin_square"] == "empty" and (
                f, int(side["from_rank"])) in board:
            fail("impossible_position")
        if es["halfmove_clock"] == "must-be-zero" and half_i != 0:
            fail("impossible_position")

    return (board, color, rights, ep_square, half_i, full_i)


def emit_fen(contract, position):
    board, color, rights, ep, half, full = position
    files = contract["board"]["files"]
    g = contract["placement"]
    rank_list = []
    for r in range(len(contract["board"]["ranks"]), 0, -1):
        row, empty = "", 0
        for f in range(len(files)):
            piece = board.get((f, r))
            if piece:
                if empty:
                    row += str(empty)
                    empty = 0
                row += piece
            else:
                empty += 1
        if empty:
            row += str(empty)
        rank_list.append(row)
    return " ".join([
        g["rank_separator"].join(rank_list), color,
        rights or contract["castling"]["none_sentinel"],
        ep or contract["en_passant"]["none_sentinel"],
        str(half), str(full),
    ])


# -- happy / boundary / malformed / atomicity behavior -----------------

STARTPOS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_lint_clean():
    _lint()


def test_happy_startpos_and_roundtrip():
    doc = _doc()["contract"]
    pos = parse_fen(doc, STARTPOS)
    assert pos[1] == "w" and pos[2] == "KQkq" and pos[3] is None
    assert pos[4] == 0 and pos[5] == 1
    assert len(pos[0]) == 32
    assert emit_fen(doc, pos) == STARTPOS  # canonical round-trip identity


def test_happy_midgame_with_ep_target():
    # 1. e4: target recorded on the advance (no capture available).
    fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    pos = parse_fen(_doc()["contract"], fen)
    assert pos[3] == "e3" and pos[1] == "b"
    assert emit_fen(_doc()["contract"], pos) == fen


def test_happy_serialization_roundtrip_fixture_set():
    doc = _doc()
    fixtures = [
        STARTPOS,
        "r2qk2r/ppp2ppp/2n2n2/2b1p3/2BPP3/2N2N2/PPP2PPP/R2QK2R w KQkq - 6 7",
        "8/8/8/8/8/8/8/K6k w - - 0 1",          # bare kings boundary
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2",     # real ep capture state
        "r3k3/8/8/8/8/8/8/4K2R b Kq - 12 20",
        "8/8/8/8/8/8/8/K6k b - - 99 240",        # high counters boundary
        # 16 pieces per side boundary
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        # 8 pawns per side boundary (white castled, queens off)
        "r4rk1/pppppppp/8/8/8/8/PPPPPPPP/R4RK1 w - - 0 9",
        # promotion budget exact fit: 3 white queens, 6 pawns
        # (2 excess queens, 2 missing pawns)
        "4k3/8/8/8/8/8/PPP5/1QQQK3 w - - 0 1",
        # black 3 queens 6 pawns exact fit
        "qqq1k3/ppp5/8/8/8/8/8/4K3 b - - 0 1",
    ]
    for fen in fixtures:
        assert emit_fen(doc["contract"], parse_fen(doc["contract"], fen)) == fen


@pytest.mark.parametrize("bad", [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0",     # 5 fields
    "8/8/8/8/8/8/8/K6k w - - 0 1 extra",                          # 7 fields
    "8/8/8/8/8/8/8/K6k  w - - 0 1",                               # double space
    "8/8/8/8/8/8/8/K6k w - - 0 1 ",                               # trailing space
    "8/8/8/8/8/8/8/K6k W - - 0 1",                                # bad color
    "8/8/8/8/8/8/8/K6k x - - 0 1",                                # bad color
    "8/8/8/8/8/8/8/K6k w - - x 1",                                # bad halfmove
    "8/8/8/8/8/8/8/K6k w - - -1 1",                               # signed counter
    "8/8/8/8/8/8/8/K6k w - - \u0664 1",                          # Arabic-Indic digit
    "8/8/8/8/8/8/8/K6k w - - \uff14 1",                          # full-width digit
    "8/8/8/8/8/8/8/K6k w - - 0 \u0661",                          # unicode fullmove
    "8/8/8/8/8/8/8/K6k w - - 01 1",                               # leading zero halfmove
    "8/8/8/8/8/8/8/K6k w - - 0 01",                               # leading zero fullmove
    "8/8/8/8/8/8/8/K6k w - - 0 0",                                # fullmove < min
    "8/8/8/8/8/8/8/K6k w - - 0 -1",                               # bad fullmove
    "9/8/8/8/8/8/8/K6k w - - 0 1",                                # rank sum 9
    "7/8/8/8/8/8/8/K6k w - - 0 1",                                # rank sum 7
    "8/8/8/8/8/8/8/K6k w - - 0 1".replace("8", "44", 1),          # adjacent digits
    "8/8/8/8/8/8/8/K6k w - - 0 1".replace("8", "80", 1),          # zero digit
    "8/8/8/8/8/8/8/K6k w - - 0 1".replace("8", "7X", 1),          # bad letter
    "8/8/8/8/8/8/K6k w - - 0 1",                                  # 7 ranks
    "8/8/8/8/8/8/8/K6k w KK - 0 1",                               # duplicate right
    "8/8/8/8/8/8/8/K5Rk w qK - 0 1",                              # wrong order
    "8/8/8/8/8/8/8/K5Rk w X - 0 1",                               # bad right letter
    "8/8/8/8/8/8/8/K6k w - e4 0 1",                               # bad ep rank
    "8/8/8/8/8/8/8/K6k w - zz 0 1",                               # bad ep square
])
def test_malformed_fens_rejected_as_malformed_request(bad):
    doc = _doc()
    with pytest.raises(FenError) as exc:
        parse_fen(doc["contract"], bad)
    assert exc.value.failure_class == "malformed_fen"
    assert exc.value.code == "malformed_request"


@pytest.mark.parametrize("bad", [
    "8/8/8/8/8/8/8/7k w - - 0 1",            # no white king
    "8/8/8/8/8/8/8/K7 w - - 0 1",            # no black king
    "K7/8/8/8/8/8/8/K6k w - - 0 1",          # two white kings
    "8/8/8/8/8/8/8/KK5k w - - 0 1",          # two white kings, one rank
    "8/8/8/8/8/8/8/Kk6 w - - 0 1",           # adjacent kings
    "8/8/8/8/8/8/k7/K7 b - - 0 1",           # adjacent kings vertical
    "P6k/8/8/8/8/8/8/K7 w - - 0 1",          # pawn on rank 8
    "7k/8/8/8/8/8/8/p6K w - - 0 1",          # pawn on rank 1
    "4k3/8/8/8/8/8/8/K3R3 w - - 0 1",        # non-mover king attacked
    "k3r3/8/8/8/8/8/8/4K3 b - - 0 1",        # non-mover king attacked (black)
    "r3k2r/8/8/8/8/8/8/R3K3 b Kq - 0 1",    # K right, no white rook h1
    "r6k/8/8/8/8/8/8/4K3 b q - 0 1",        # q right, black king off e8
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e3 0 1",
    # ^ ep rank 3 requires black to move
    "4k3/8/8/8/3p4/8/8/4K3 b - d3 0 2",
    # ^ ep rank 3 requires the advancing WHITE pawn on rank 4
    "4k3/8/8/3pP3/8/8/8/4K3 b - d6 0 2",
    # ^ ep rank 6 requires white to move
    "4k3/8/8/8/8/8/8/4K3 w - d6 0 2",
    # ^ ep rank 6 requires the advancing black pawn on rank 5
    # ep target square OCCUPIED (the pawn passed over it, must be empty)
    "4k3/8/8/8/4P3/4N3/8/4K3 b - e3 0 1",
    "4k3/4N3/8/4p3/8/8/8/4K3 w - e6 0 1",
    # ep origin square OCCUPIED (the pawn left it, must be empty)
    "4k3/8/8/8/4P3/8/4P3/4K3 b - e3 0 1",
    "4k3/4p3/8/4p3/8/8/8/4K3 w - e6 0 1",
    # ep with a NONZERO halfmove clock (the advance was a pawn move,
    # which resets the clock per the turn contract)
    "4k3/8/8/8/4P3/8/8/4K3 b - e3 7 1",
    "4k3/8/8/4p3/8/8/8/4K3 w - e6 3 1",
    # material impossibility: 9 pawns, 17 pieces
    "4k3/8/8/8/8/8/PPPPPPPP/4K2P w - - 0 1",
    "4k3/8/8/8/8/7N/PPPPPPPP/RNBQKBNR w - - 0 1",
    "4k3/pppppppp/8/8/8/8/8/p6K w - - 0 1",
    # promotion budget exceeded: 3 white queens with all 8 pawns
    # present (2 excess queens, 0 missing pawns)
    "4k3/8/8/8/8/8/PPPPPPPP/1QQQK3 w - - 0 1",
    # 3 black queens with 7 pawns (2 excess, 1 missing)
    "qqq1k3/pp1ppppp/8/8/8/8/8/4K3 b - - 0 1",
    # non-mover king attacked by a pawn (exact adjacent geometry):
    # white pawn d4 attacks e5; black pawn d4 attacks e3
    "8/8/8/4k3/3P4/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/3p4/4K3/8/8 b - - 0 1",
    # edge files: a/h-file pawns attack their one diagonal square
    "8/8/8/1k6/P7/8/8/4K3 w - - 0 1",      # white a4 -> b5
    "8/8/8/6k1/7P/8/8/4K3 w - - 0 1",      # white h4 -> g5
    "4k3/8/8/p7/1K6/8/8/8 b - - 0 1",      # black a5 -> b4
    "4k3/8/8/7p/6K1/8/8/8 b - - 0 1",      # black h5 -> g4
])
def test_impossible_positions_rejected_as_illegal_position(bad):
    doc = _doc()
    with pytest.raises(FenError) as exc:
        parse_fen(doc["contract"], bad)
    assert exc.value.failure_class == "impossible_position"
    assert exc.value.code == "illegal_position"


# Pawn-attack direction witnesses: (fen, attacking side is white,
# expected attack verdict on the non-moving king). The geometrically
# REVERSED placements are non-attacks that a direction-inverted
# evaluator flags; the rest are real attacks it misses.
PAWN_DIRECTION_WITNESSES = [
    ("8/8/8/4k3/3P4/8/8/4K3 w - - 0 1", True, True),
    ("4k3/8/8/8/3p4/4K3/8/8 b - - 0 1", False, True),
    ("8/8/3P4/4k3/8/8/8/4K3 w - - 0 1", True, False),
    ("4k3/8/8/8/8/4K3/3p4/8 b - - 0 1", False, False),
    ("8/8/8/1k6/P7/8/8/4K3 w - - 0 1", True, True),
    ("8/8/8/6k1/7P/8/8/4K3 w - - 0 1", True, True),
    ("4k3/8/8/p7/1K6/8/8/8 b - - 0 1", False, True),
    ("4k3/8/8/7p/6K1/8/8/8 b - - 0 1", False, True),
]

# Geometrically reversed non-attacks and file-wrap guards: all valid
# positions that MUST parse and roundtrip.
PAWN_NON_ATTACK_ACCEPTED = [
    "8/8/3P4/4k3/8/8/8/4K3 w - - 0 1",   # pawn d6, king e5
    "4k3/8/8/8/8/4K3/3p4/8 b - - 0 1",   # pawn d2, king e3
    "8/8/8/k7/7P/8/8/4K3 w - - 0 1",     # h4 does not wrap to a5
    "4k3/8/8/p7/7K/8/8/8 b - - 0 1",     # a5 does not wrap to h4
]


def _placement_board(contract, fen):
    """Board {(file, rank): letter} from the placement field, geometry
    derived from the contract's declared files and ranks."""
    files = contract["board"]["files"]
    ranks = contract["board"]["ranks"]
    board = {}
    for ri, row in enumerate(fen.split()[0].split("/")):
        f = 0
        for ch in row:
            if ch in "12345678":
                f += int(ch)
            else:
                board[(f, len(ranks) - ri)] = ch
                f += 1
        assert f == len(files)
    return board


def test_pawn_attack_direction_witnesses():
    doc = _doc()
    c = doc["contract"]
    for fen, by_white, expect in PAWN_DIRECTION_WITNESSES:
        board = _placement_board(c, fen)
        king = "k" if by_white else "K"
        sq = next(s for s, pce in board.items() if pce == king)
        assert _attack(c["board"], sq, board, by_white) is expect, fen


def test_pawn_non_attacks_parse_and_roundtrip():
    doc = _doc()
    for fen in PAWN_NON_ATTACK_ACCEPTED:
        assert emit_fen(doc["contract"], parse_fen(doc["contract"], fen)) \
            == fen, fen


def test_pawn_delta_sign_mutation_inverts_witnesses():
    """Replay: flipping the rank sign of the declared attacker pawn
    deltas must INVERT every direction witness verdict - proving the
    witnesses catch a direction-inverted evaluator (a real defect),
    rather than the delta list being linted against a constant only."""
    doc = _doc()
    c = doc["contract"]
    a = c["board"]["attack"]
    for fen, by_white, expect in PAWN_DIRECTION_WITNESSES:
        board = _placement_board(c, fen)
        king = "k" if by_white else "K"
        sq = next(s for s, pce in board.items() if pce == king)
        declared = (a["white_pawn_capture_deltas"] if by_white
                    else a["black_pawn_capture_deltas"])
        flipped = [[df, -dr] for df, dr in declared]
        assert _attack(c["board"], sq, board, by_white,
                       _pawn_deltas=flipped) is (not expect), fen


def test_canonical_input_roundtrips_byte_for_byte():
    doc = _doc()["contract"]
    fixtures = [
        STARTPOS,
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2",
        "r3k3/8/8/8/8/8/8/4K2R b Kq - 12 20",
        "8/8/8/8/8/8/8/K6k b - - 99 240",
        "4k3/8/8/8/8/8/PPP5/1QQQK3 w - - 0 1",
    ]
    for fen in fixtures:
        assert emit_fen(doc, parse_fen(doc, fen)) == fen, fen


def test_rejected_parse_yields_no_partial_state():
    # Atomicity: parse returns either the complete position or an
    # error; there is no observable partial state from a rejected FEN.
    doc = _doc()
    result = None
    try:
        result = parse_fen(
            doc["contract"],
            "8/8/8/8/8/8/8/Kk6 w KQkq - 0 1",  # adjacent kings
        )
    except FenError as exc:
        result = ("error", exc.code)
    assert result == ("error", "illegal_position")


def test_error_shape_and_closed_enum():
    contract = _doc()["contract"]
    assert set(contract["errors"]["closed_enum"]) == {
        "malformed_request", "illegal_position", "internal"}
    fields = contract["errors"]["shape"]["error"]["fields"]
    for name in ("code", "message", "retryable"):
        assert fields[name]["required"] is True
    for cls, m in contract["failure_mapping"].items():
        assert m["error"] in contract["errors"]["closed_enum"], cls


# -- mutation replay ---------------------------------------------------

def _mutants():
    doc = _doc()
    c = doc["contract"]
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    add("five-field order", ["contract", "fields", "order"],
        c["fields"]["order"][:5])
    add("exactly_six false", ["contract", "fields", "exactly_six"], False)
    add("rank_count 7", ["contract", "placement", "rank_count"], 7)
    add("rank sum 9", ["contract", "placement", "rank_sum"], 9)
    add("zero digit allowed", ["contract", "placement", "zero_digit"],
        "allowed")
    add("adjacent digits allowed",
        ["contract", "placement", "adjacent_digits"], "allowed")
    add("two white kings", ["contract", "position_rules", "white_kings"],
        "exactly-2")
    add("adjacent kings allowed",
        ["contract", "position_rules", "kings_adjacent"], "allowed")
    add("back-rank pawns allowed",
        ["contract", "position_rules", "pawns_on_back_ranks"], "allowed")
    add("non-mover attack allowed",
        ["contract", "position_rules", "non_mover_king_attacked"],
        "allowed")
    add("castling order contradiction", ["contract", "castling", "order"],
        "KkQq")
    add("castling consistency dropped",
        ["contract", "castling", "consistency"], "none")
    add("ep storage identity contradiction",
        ["contract", "en_passant", "storage"],
        "recorded-only-when-capture-is-legal")
    add("ep ranks drift", ["contract", "en_passant", "ranks"],
        ["3", "4", "5", "6"])
    add("halfmove min -1", ["contract", "counters", "halfmove_clock"],
        {"type": "integer", "digits_only": True, "min": -1})
    add("fullmove min 0", ["contract", "counters", "fullmove_number"],
        {"type": "integer", "digits_only": True, "min": 0})
    add("counter digits_only false",
        ["contract", "counters", "halfmove_clock", "digits_only"], False)
    add("canonical false", ["contract", "serialization", "canonical"],
        False)
    add("roundtrip dropped", ["contract", "serialization", "roundtrip"],
        "best-effort")
    add("atomicity dropped", ["contract", "atomicity", "parse"],
        "partial-state-allowed")
    add("ep target_square drift", ["contract", "en_passant",
                                   "target_square"], "occupied-ok")
    add("ep origin_square dropped",
        ["contract", "en_passant", "origin_square"], "any")
    add("ep halfmove rule dropped", ["contract", "en_passant",
                                     "halfmove_clock"], "any")
    add("ep set_on source drift", ["contract", "en_passant", "set_on"],
        "hardcoded-here")
    add("pawn bound drift", ["contract", "position_rules",
                             "white_pawns_max"], 9)
    add("piece bound drift", ["contract", "position_rules",
                              "black_pieces_max"], 17)
    add("promotion budget dropped", ["contract", "position_rules",
                                     "promotion_budget"], "none")
    add("counter grammar drift", ["contract", "counters",
                                  "halfmove_clock", "grammar"],
        "any-unicode-digits")
    add("leading zeros allowed", ["contract", "counters",
                                  "fullmove_number", "leading_zeros"],
        "allowed")
    add("board files drift", ["contract", "board", "files"],
        ["a", "b", "c", "d", "e", "f", "g"])
    add("knight delta drift", ["contract", "board", "attack",
                               "knight_deltas"],
        [[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2], [-2, -1],
         [-2, 1]])
    add("pawn attack delta drift", ["contract", "board", "attack",
                                    "white_pawn_capture_deltas"],
        [[1, 2], [-1, 2]])
    add("castling home source drift", ["contract", "castling",
                                       "home_squares"],
        "hardcoded-here")
    add("emit_of_parse dropped", ["contract", "serialization",
                                  "emit_of_parse"], "best-effort")
    add("mapping contradiction", ["contract", "failure_mapping",
        "malformed_fen", "error"], "illegal_position")
    add("undeclared error code", ["contract", "failure_mapping",
        "impossible_position", "error"], "weird_error")
    add("enum narrowed", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    m = copy.deepcopy(doc)
    m["contract"]["rogue"] = {"x": 1}
    out.append(("rogue contract key", m))
    m = copy.deepcopy(doc)
    m["contract"]["placement"]["rogue"] = 1
    out.append(("rogue placement key", m))
    m = copy.deepcopy(doc)
    m["contract"]["failure_mapping"]["extra_class"] = {
        "trigger": "t", "error": "malformed_request"}
    out.append(("orphan failure-mapping class", m))
    m = copy.deepcopy(doc)
    del m["contract"]["position_rules"]
    out.append(("missing position_rules section", m))
    m = copy.deepcopy(doc)
    m["contract"]["errors"]["shape"]["error"]["fields"]["code"][
        "required"] = False
    out.append(("error shape reversal", m))
    m = copy.deepcopy(doc)
    m["contract"]["errors"]["shape"]["error"]["fields"]["retryable"][
        "type"] = "integer"
    out.append(("retryable type drift", m))
    return out


def test_mutations_fail_lint():
    for name, mutant in _mutants():
        try:
            lint(mutant)
        except ContractError:
            continue
        pytest.fail(f"mutation accepted by lint: {name}")


def test_mutants_never_silent_subset():
    assert len(_mutants()) >= 38


# -- linkage: sibling contracts drift -> FEN lint fails ----------------

def _lint_with_root(tmp_path, mutate=None):
    contracts = tmp_path / "data" / "contracts"
    shutil.copytree(DATA_DIR, contracts)
    if mutate:
        mutate(contracts)
    lint(_doc(), root=tmp_path)


def test_linkage_clean_copy_passes(tmp_path):
    _lint_with_root(tmp_path)


def test_linkage_en_passant_storage_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "en_passant.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["target"]["storage_vs_identity"]["storage"] = (
            "recorded-only-when-capture-is-legal")
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_en_passant_rank_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "en_passant.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["target"]["grammar"]["ranks"] = ["3", "4", "5", "6"]
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_turn_bound_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "turn.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["state"]["bounds"]["fullmove_number"]["min"] = 0
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_castling_values_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "castling.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["rights"]["values"] = ["K", "Q"]
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_castling_home_square_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "castling.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["rights"]["home_squares"]["K"]["rook"] = "g1"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_ep_set_on_target_rank_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "en_passant.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["target"]["set_on"]["white"]["target_rank"] = "4"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_ep_set_on_from_rank_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "en_passant.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["target"]["set_on"]["black"]["from_rank"] = "6"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_turn_reset_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "turn.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["transition"]["on_move"]["halfmove_clock"][
            "reset_when"] = ["capture"]
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_missing_sibling_fails(tmp_path):
    def mutate(contracts):
        (contracts / "en_passant.yaml").unlink()

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)
