"""Contract-derived shipped FEN parser used by graph runtimes."""

from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "fen.yaml"
DATA_DIR = ROOT / "data" / "contracts"


def _doc():
    return yaml.safe_load(CONTRACT.read_text())


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
    pawn_deltas = (
        _pawn_deltas
        if _pawn_deltas is not None
        else (a["white_pawn_capture_deltas"] if by_white else a["black_pawn_capture_deltas"])
    )
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
    sliders = ((a["rook_directions"], ("R", "Q")), (a["bishop_directions"], ("B", "Q")))
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

    if type(text) is not str:
        fail("malformed_fen")
    parts = text.split(" ")
    if len(parts) != len(contract["fields"]["order"]) or any(part == "" for part in parts):
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
    for value, spec in (
        (half, contract["counters"]["halfmove_clock"]),
        (full, contract["counters"]["fullmove_number"]),
    ):
        if spec["grammar"] == "ascii-digits-0-9-only" and not re.fullmatch(r"[0-9]+", value):
            fail("malformed_fen")
        try:
            number = int(value)
        except ValueError:  # beyond the interpreter's int-string limit
            number = None
        if number is None:
            fail("malformed_fen")
        if spec["leading_zeros"] == "forbidden" and value != str(number):
            fail("malformed_fen")
        if number < spec["min"]:
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
        if len(ep) != 2 or ep[0] not in files or ep[1] not in es["ranks"]:
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
        ("black", pr["black_pawns_max"], pr["black_pieces_max"]),
    ):
        mine = [pce for pce in board.values() if (pce.isupper() == (side_letter == "white"))]
        pawns = sum(1 for pce in mine if pce.lower() == "p")
        if pawns > pmax or len(mine) > tmax:
            fail("impossible_position")
        if pr["promotion_budget"] == ("excess-officers-over-start-set-covered-by-missing-pawns"):
            excess = 0
            for kind, start in START_OFFICERS.items():
                have = sum(1 for pce in mine if pce.lower() == kind)
                excess += max(0, have - start)
            if excess > pmax - pawns:
                fail("impossible_position")
    if pr["non_mover_king_attacked"] == "forbidden":
        mover_white = white_to_move
        non_mover = bk[0] if mover_white else wk[0]
        if _attack(contract["board"], non_mover, board, by_white=mover_white):
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
        if (
            board.get((kf, int(home["king"][1]))) != king_letter
            or board.get((rf, int(home["rook"][1]))) != rook_letter
        ):
            fail("impossible_position")

    # en passant consistency, geometry from the LINKED contract's
    # set_on: target empty, origin empty, halfmove clock zero
    if ep_square is not None:
        set_on = _ep_set_on()
        f, r = files.index(ep_square[0]), int(ep_square[1])
        white_advanced = str(r) == set_on["white"]["target_rank"]
        side = set_on["white" if white_advanced else "black"]
        requires = es["rank3_requires"] if white_advanced else es["rank6_requires"]
        if (requires == "black-to-move" and white_to_move) or (
            requires == "white-to-move" and not white_to_move
        ):
            fail("impossible_position")
        pawn = "P" if white_advanced else "p"
        if board.get((f, int(side["to_rank"]))) != pawn:
            fail("impossible_position")
        if es["target_square"] == "empty" and (f, r) in board:
            fail("impossible_position")
        if es["origin_square"] == "empty" and (f, int(side["from_rank"])) in board:
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
    return " ".join(
        [
            g["rank_separator"].join(rank_list),
            color,
            rights or contract["castling"]["none_sentinel"],
            ep or contract["en_passant"]["none_sentinel"],
            str(half),
            str(full),
        ]
    )
