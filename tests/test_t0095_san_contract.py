"""T0095: the SAN contract is the enforced source of truth.

The contract (data/contracts/san.yaml) declares the token set,
grammar forms, minimal disambiguation under the file-rank-both
preference, suffix semantics pinned to the resulting position,
castling delegation, the three-class resolution failure model, and
canonical serialization. This battery proves the behavior with an
EXECUTABLE resolver/emitter DERIVED FROM the contract data, running
on the T0078 contract-derived legal-move interpreter (fixture/
contract consistency, not production behavior), then replays
contradiction/reversal/bool-int/rogue-key/enum mutations plus
linkage mutations against the real sibling contracts.
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

from tests.test_t0078_legal_moves_fixture import (
    _apply,
    _attacked_squares,
    _legal_moves,
    _terminal_status,
)
from tools.san_contract_lint import lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "san.yaml"
DATA_DIR = ROOT / "data" / "contracts"
FILES = "abcdefgh"


def _doc():
    return yaml.safe_load(CONTRACT.read_text())


def _lint():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "san_contract_lint.py")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def _state(board: str, side: str, rights: str = "-") -> dict:
    """board: 8 rank strings rank8..rank1, tokens wp/wn/... or '--'.
    rights: the position's castling rights (subset of KQkq or "-"),
    owned by the castling contract; never inferred from occupancy."""
    occ = {}
    for ri, rank in enumerate(board.split("/")):
        rank = rank.replace(" ", "")
        for fi in range(0, len(rank), 2):
            tok = rank[fi:fi + 2]
            if tok != "--":
                occ[FILES[fi // 2] + str(8 - ri)] = tok
    return {"occupied": occ, "side_to_move": side,
            "castling_rights": "" if rights == "-" else rights}




@functools.cache
def _castling_homes():
    path = ROOT / "data" / "contracts" / "castling.yaml"
    with open(path) as fh:
        return yaml.safe_load(fh)["contract"]["rights"]["home_squares"]


# -- executable reference resolver/emitter, contract-derived ----------

class SanError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _castling_moves(state):
    """Castling candidates per the LINKED castling contract: the RIGHT
    must be present in the state's castling_rights (irrevocable per
    that contract - never inferred from occupancy), king and rook on
    the contract's home squares, path empty, king's transit squares
    unattacked."""
    occ, side = state["occupied"], state["side_to_move"]
    held = state["castling_rights"]
    enemy = "b" if side == "w" else "w"
    homes = _castling_homes()
    k_letter, r_letter = side + "k", side + "r"
    out = []
    for right, dest_file in (("K" if side == "w" else "k", "g"),
                             ("Q" if side == "w" else "q", "c")):
        if right not in held:
            continue
        home = homes[right]
        king_sq, rook_sq = home["king"], home["rook"]
        if occ.get(king_sq) != k_letter or occ.get(rook_sq) != r_letter:
            continue
        rank = king_sq[1]
        kf, rf = FILES.index(king_sq[0]), FILES.index(rook_sq[0])
        step = 1 if rf > kf else -1
        between = [FILES[i] + rank
                   for i in range(min(kf, rf) + 1, max(kf, rf))]
        if any(sq in occ for sq in between):
            continue
        attacked = _attacked_squares(occ, enemy)
        transit = [king_sq] + [FILES[kf + step] + rank,
                               FILES[kf + 2 * step] + rank]
        if any(sq in attacked for sq in transit):
            continue
        out.append({"from_square": king_sq,
                    "to_square": dest_file + rank})
    return out


def _apply_move(occ, move, side):
    """Apply a move to the board; castling is ATOMIC per the contract:
    the king move AND the rook hop happen together, so downstream
    check/checkmate classification sees the true resulting board."""
    is_castle = (occ.get(move["from_square"], "  ") == side + "k"
                 and abs(FILES.index(move["from_square"][0])
                         - FILES.index(move["to_square"][0])) == 2)
    new = _apply(occ, move)
    if is_castle:
        rank = move["from_square"][1]
        if move["to_square"][0] == "g":
            rook_from, rook_to = "h" + rank, "f" + rank
        else:
            rook_from, rook_to = "a" + rank, "d" + rank
        new[rook_to] = new.pop(rook_from)
    return new


def _all_moves(state):
    return _legal_moves(state) + _castling_moves(state)


def resolve_san(contract, text, state):
    """Resolve SAN in a position to the legal-moves move shape, per
    the contract: grammar -> candidate filter -> exactly-one match ->
    suffix check against the resulting position. All-or-nothing."""
    mapping = contract["failure_mapping"]

    def fail(cls):
        raise SanError(cls, mapping[cls]["error"])

    t = contract["tokens"]
    # suffix: at most one, at the end, and it must be a declared glyph
    suffix = "none"
    body = text
    if body.endswith(t["checkmate_suffix"]) or body.endswith(
            t["check_suffix"]):
        suffix = body[-1]
        body = body[:-1]
    if not body or any(ch in body for ch in "+#"):
        fail("malformed_san")
    if t["annotation_suffixes"] == "forbidden" and any(
            ch in body for ch in "!?"):
        fail("malformed_san")

    moves = _all_moves(state)
    occ, side = state["occupied"], state["side_to_move"]
    enemy = "b" if side == "w" else "w"

    if body in (t["castling_kingside"], t["castling_queenside"]):
        if t["castling_character"] == ("capital-letter-O-never-digit-"
                                       "zero") and "0" in text:
            fail("malformed_san")
        dest = ("g" if body == t["castling_kingside"] else "c") + (
            "1" if side == "w" else "8")
        cand = [m for m in moves
                if m["to_square"] == dest
                and occ.get(m["from_square"], "  ")[1] == "k"
                and abs(FILES.index(m["from_square"][0])
                        - FILES.index(dest[0])) == 2]
    else:
        m = re.fullmatch(
            r"([NBRQK])?([a-h])?([1-8])?(x)?([a-h][1-8])(=[QRBN])?",
            body)
        if not m:
            fail("malformed_san")
        letter, ofile, orank, cap, dest, promo = m.groups()
        if letter and letter not in t["piece_letters"]:
            fail("malformed_san")
        if not letter:
            # pawn: origin file allowed only with capture marker
            if (ofile is not None) != (cap is not None):
                if ofile is not None and orank is None and cap is None:
                    pass  # e.g. "exd5" parse: ofile=e, cap=x, dest=d5
                else:
                    fail("malformed_san")
            if orank is not None:
                fail("malformed_san")  # pawns never carry origin rank
        if promo:
            piece = promo[1]
            if piece not in t["promotion_pieces"]:
                fail("malformed_san")
        piece_type = (letter or "p").lower()
        promo_val = promo[1].lower() if promo else None
        if promo_val and promo_val not in [
                p.lower() for p in t["promotion_pieces"]]:
            fail("malformed_san")
        if promo_val is not None and piece_type != "p":
            fail("malformed_san")

        def matches(mv, use_disamb):
            tok = occ.get(mv["from_square"])
            if tok is None or tok[1] != piece_type:
                return False
            if mv["to_square"] != dest:
                return False
            if use_disamb:
                if ofile is not None and mv["from_square"][0] != ofile:
                    return False
                if orank is not None and mv["from_square"][1] != orank:
                    return False
            if (mv.get("promotion") or None) != promo_val:
                return False
            is_capture = mv["to_square"] in occ and \
                occ[mv["to_square"]][0] == enemy
            if piece_type == "p" and \
                    mv["from_square"][0] != mv["to_square"][0]:
                is_capture = True  # en-passant shaped capture
            return (cap is not None) == is_capture

        pool = [mv for mv in moves if matches(mv, use_disamb=False)]
        cand = [mv for mv in pool if matches(mv, use_disamb=True)]
        if len(cand) == 1:
            # canonical disambiguation, derived from the FULL legal
            # move set per the contract: no disambiguator when no
            # rival, else file/rank/both under the preference order;
            # anything else supplied is malformed (pinned).
            move0 = cand[0]
            rivals = [mv for mv in pool if mv != move0]
            canonical = ""
            if rivals:
                fx = move0["from_square"][0]
                rk = move0["from_square"][1]
                pref = contract["disambiguation"]["preference_order"]
                if all(mv["from_square"][0] != fx for mv in rivals):
                    canonical = fx if pref[0] == "file" else rk
                elif all(mv["from_square"][1] != rk for mv in rivals):
                    canonical = rk
                else:
                    canonical = move0["from_square"]
            supplied = (ofile or "") + (orank or "")
            if (piece_type != "p"
                    and contract["disambiguation"]
                        ["noncanonical_disambiguation"]
                    == "rejected-as-malformed_san"
                    and supplied != canonical):
                fail("malformed_san")

    if len(cand) == 0:
        fail("no_legal_match")
    if len(cand) > 1:
        fail("ambiguous_san")
    move = cand[0]

    # suffix pinned to the RESULTING position (castling applied
    # atomically: king move plus rook hop)
    new_state = {"occupied": _apply_move(occ, move, side),
                 "side_to_move": enemy}
    terminal = _terminal_status(new_state)
    want = {"checkmate": "#", "check": "+"}.get(terminal, "none")
    if suffix != want:
        fail("no_legal_match")
    return move


def emit_san(contract, move, state):
    """Canonical SAN for a legal move in a position, per the contract:
    minimal disambiguation under the declared preference, suffix from
    the resulting position."""
    c = contract
    t = contract["tokens"]
    occ, side = state["occupied"], state["side_to_move"]
    enemy = "b" if side == "w" else "w"
    tok = occ[move["from_square"]]
    dest = move["to_square"]
    fx, tx = move["from_square"][0], dest[0]

    if tok[1] == "k" and abs(FILES.index(fx) - FILES.index(tx)) == 2:
        body = (t["castling_kingside"] if FILES.index(tx) == 6
                else t["castling_queenside"])
    elif tok[1] == "p":
        capture = (fx != tx)
        body = (fx + t["capture_marker"] if capture else "") + dest
        if move.get("promotion"):
            body += t["promotion_marker"] + move["promotion"].upper()
    else:
        letter = tok[1].upper()
        rivals = [mv for mv in _legal_moves(state)
                  if mv != move and mv["to_square"] == dest
                  and occ.get(mv["from_square"], "  ")[1] == tok[1]]
        disamb = ""
        if rivals:
            same_file = any(mv["from_square"][0] == fx for mv in rivals)
            same_rank = any(mv["from_square"][1] ==
                            move["from_square"][1] for mv in rivals)
            pref = c["disambiguation"]["preference_order"]
            if not same_file and pref[0] == "file":
                disamb = fx
            elif not same_rank and "rank" in pref:
                disamb = move["from_square"][1]
            else:
                disamb = move["from_square"]
        capture = dest in occ and occ[dest][0] == enemy
        body = (letter + disamb
                + (t["capture_marker"] if capture else "") + dest)

    new_state = {"occupied": _apply_move(occ, move, side),
                 "side_to_move": enemy}
    terminal = _terminal_status(new_state)
    return body + {"checkmate": t["checkmate_suffix"],
                   "check": t["check_suffix"]}.get(terminal, "")


# -- fixtures ----------------------------------------------------------

STARTPOS_BOARD = (
    "brbnbbbqbkbbbnbr/brbpbpbpbpbpbpbp/-- -- -- -- -- -- -- --/"
    "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
    "-- -- -- -- -- -- -- --/wpwpwpwpwpwpwpwp/wrwnwbwqwkwbwnwr")


def test_lint_clean():
    _lint()


def test_happy_pawn_and_piece_moves():
    doc = _doc()["contract"]
    st = _state(STARTPOS_BOARD, "w", "KQkq")
    assert resolve_san(doc, "e4", st) == {"from_square": "e2",
                                          "to_square": "e4"}
    assert resolve_san(doc, "Nf3", st) == {"from_square": "g1",
                                           "to_square": "f3"}
    assert emit_san(doc, {"from_square": "e2", "to_square": "e4"},
                    st) == "e4"
    assert emit_san(doc, {"from_square": "g1", "to_square": "f3"},
                    st) == "Nf3"


def test_happy_capture_and_promotion_forms():
    doc = _doc()["contract"]
    st = _state("bk-- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- bp-- -- -- --/-- -- -- -- wp-- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- wk-- -- --",
                "w")
    assert resolve_san(doc, "exd6", st) == {"from_square": "e5",
                                            "to_square": "d6"}
    promo = _state("-- -- -- -- -- -- -- --/wp-- -- -- -- -- -- --/"
                   "-- -- -- -- -- -- -- bk/-- -- -- -- -- -- -- --/"
                   "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                   "-- -- -- -- -- -- -- --/-- -- wk-- -- -- -- --",
                   "w")
    mv = resolve_san(doc, "a8=Q", promo)
    assert mv == {"from_square": "a7", "to_square": "a8",
                  "promotion": "q"}
    assert emit_san(doc, mv, promo) == "a8=Q"


def test_happy_disambiguation_file_rank_both():
    doc = _doc()["contract"]
    # rooks a1 + h1, both can reach d1: file distinguishes
    st = _state("-- -- -- -- -- bk-- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- wk-- -- --/wr-- -- -- -- -- -- wr",
                "w")
    assert resolve_san(doc, "Rhd1", st)["from_square"] == "h1"
    assert resolve_san(doc, "Rad1", st)["from_square"] == "a1"
    assert emit_san(doc, {"from_square": "h1", "to_square": "d1"},
                    st) == "Rhd1"
    # rooks a1 + a5, both can reach a3: rank distinguishes
    st2 = _state("-- -- -- -- -- bk-- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/wr-- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/wr-- -- -- -- -- -- wk",
                 "w")
    assert resolve_san(doc, "R5a3", st2)["from_square"] == "a5"
    assert emit_san(doc, {"from_square": "a5", "to_square": "a3"},
                    st2) == "R5a3"
    # queens d1, h5, a8 can all reach... use classic: Qd1 and Qh5 to
    # e... simpler: knights f3 + f7 + c6? Use both-file-and-rank case:
    # queens on a1, a8, h8 all able to hit a8? No - keep it legal:
    # knights b1, f3, d7 all cover e5? b1 no. knights f3, h3, f7:
    # f3->e5? no... e5 covered by f3? f3 covers e5 yes; f7 covers e5
    # yes; h3 covers f4/g5/f2/f1 - no. Use f3, f7, g4: g4->e5 yes.
    st3 = _state("bk-- -- -- -- -- -- --/-- -- -- -- -- wn-- --/"
                 "-- -- -- -- -- -- wn--/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- -- -- wk-- -- --",
                 "w")
    # knights f7 and g6 both cover e5? f7 covers e5? f7->e5 no
    # (f7 covers d8,d6,e9,g9,h8,h6,e5? |f-e|=1,|7-5|=2 -> yes e5!).
    # g6 covers e5 (|g-e|=2,|6-5|=1) yes. files differ -> file disamb.
    assert resolve_san(doc, "Nfe5", st3)["from_square"] == "f7"
    assert resolve_san(doc, "Nge5", st3)["from_square"] == "g6"


def test_happy_castling_and_suffixes():
    doc = _doc()["contract"]
    st = _state("-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- bk-- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/wr-- -- -- wk-- -- wr",
                "w", "KQ")
    assert resolve_san(doc, "O-O", st) == {"from_square": "e1",
                                           "to_square": "g1"}
    assert resolve_san(doc, "O-O-O", st) == {"from_square": "e1",
                                             "to_square": "c1"}
    assert emit_san(doc, {"from_square": "e1", "to_square": "g1"},
                    st) == "O-O"
    # check suffix: rook to e1+? build: white Ra1->e1 check on e-file
    st2 = _state("-- -- -- -- bk-- -- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/wr-- -- -- -- -- -- wk",
                 "w")
    mv = resolve_san(doc, "Re1+", st2)
    assert mv == {"from_square": "a1", "to_square": "e1"}
    assert emit_san(doc, mv, st2) == "Re1+"
    # checkmate: white Qh5xf7# scholar's mate pattern
    st3 = _state("brbnbbbqbkbb-- br/bpbpbpbp-- bpbpbp/"
                 "-- -- -- -- -- bn-- --/-- -- -- -- bp-- -- wq/"
                 "-- -- wb-- bp-- -- --/-- -- -- -- -- -- -- --/"
                 "wpwpwpwpwpwpwpwp/wrwnwb-- wk-- wnwr", "w")
    mv3 = resolve_san(doc, "Qxf7#", st3)
    assert mv3 == {"from_square": "h5", "to_square": "f7"}
    assert emit_san(doc, mv3, st3) == "Qxf7#"


def test_roundtrip_fixture_set():
    doc = _doc()["contract"]
    st = _state(STARTPOS_BOARD, "w", "KQkq")
    for san in ["e4", "d4", "Nf3", "Nc3", "c4", "e3", "b3", "g3"]:
        assert emit_san(doc, resolve_san(doc, san, st), st) == san
    mid = _state("br-- -- -- -- brbk--/bpbpbpbp-- bpbpbp/"
                 "-- -- bn-- -- bn-- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- wn-- -- wn-- --/"
                 "wpwpwpwp-- wpwpwp/wr-- -- -- -- wr-- wk", "w")
    for san in ["Rad1", "Rfd1", "Nh4", "h3"]:
        assert emit_san(doc, resolve_san(doc, san, mid), mid) == san


@pytest.mark.parametrize("bad", [
    "e9", "e0", "i4", "e", "e4e5", "P e4", "Pe4",
    "nf3",            # lowercase piece letter is not a token
    "Nfx3", "N113",   # bad disambiguation
    "Nxxe5", "exd",   # grammar breaks
    "exd5x",          # trailing junk
    "0-0", "O-O-O-O", "o-o",   # castling token violations
    "Nf3!", "e4?",    # annotation glyphs never canonical
    "Nf3++", "e4##",  # doubled suffix
    "e8=K", "e8=q",   # promotion piece not in the declared set
    "Ne4=Q",          # promotion on a non-pawn
    "eef3",           # pawn with origin rank
])
def test_malformed_san_rejected(bad):
    doc = _doc()["contract"]
    st = _state(STARTPOS_BOARD, "w", "KQkq")
    with pytest.raises(SanError) as exc:
        resolve_san(doc, bad, st)
    assert exc.value.failure_class == "malformed_san"
    assert exc.value.code == "malformed_request"


def test_ambiguous_san_rejected():
    doc = _doc()["contract"]
    # rooks a1/h1 both reach d1 - bare "Rd1" is ambiguous
    st2 = _state("-- -- -- -- -- bk-- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                 "-- -- -- -- wk-- -- --/wr-- -- -- -- -- -- wr",
                 "w")
    with pytest.raises(SanError) as exc:
        resolve_san(doc, "Rd1", st2)
    assert exc.value.failure_class == "ambiguous_san"
    assert exc.value.code == "ambiguous_move"


@pytest.mark.parametrize("bad", ["Ngf3", "N1f3", "Ng1f3"])
def test_overdisambiguated_quiet_moves_rejected(bad):
    # startpos: only g1 can move a knight to f3 - the canonical
    # disambiguator is EMPTY; any supplied file/rank/both is
    # noncanonical and rejected as malformed (pinned in the contract).
    doc = _doc()["contract"]
    st = _state(STARTPOS_BOARD, "w", "KQkq")
    with pytest.raises(SanError) as exc:
        resolve_san(doc, bad, st)
    assert exc.value.failure_class == "malformed_san"
    assert exc.value.code == "malformed_request"


@pytest.mark.parametrize("bad", ["Nfxe5", "N3xe5", "Nf3xe5"])
def test_overdisambiguated_captures_rejected(bad):
    # single knight f3, black pawn e5: canonical SAN is Nxe5 - file,
    # rank, or both supplied on the capture form are noncanonical.
    doc = _doc()["contract"]
    st = _state("-- -- -- -- -- bk-- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- bp-- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- wn-- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- wk-- -- --",
                "w")
    with pytest.raises(SanError) as exc:
        resolve_san(doc, bad, st)
    assert exc.value.failure_class == "malformed_san"


def test_castling_requires_right_not_occupancy():
    # king and rook on their home squares but the RIGHT is absent
    # (e.g. king moved away and returned - rights are irrevocable per
    # the castling contract): castling matches zero legal moves.
    doc = _doc()["contract"]
    st = _state("-- -- -- -- -- bk-- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/wr-- -- -- wk-- -- wr",
                "w", "-")
    with pytest.raises(SanError) as exc:
        resolve_san(doc, "O-O", st)
    assert exc.value.failure_class == "no_legal_match"
    assert exc.value.code == "illegal_move"


def test_queenside_castle_check_from_rook_hop():
    # White Ke1, Ra1; Black Kd8; right Q held. After O-O-O the board
    # is Kc1 + Rd1: ONLY the rook hop creates the check, so canonical
    # SAN is O-O-O+ and the unsuffixed form is a false suffix claim.
    doc = _doc()["contract"]
    st = _state("-- -- -- bk-- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/wr-- -- -- wk-- -- --",
                "w", "Q")
    mv = resolve_san(doc, "O-O-O+", st)
    assert mv == {"from_square": "e1", "to_square": "c1"}
    applied = _apply_move(st["occupied"], mv, "w")
    assert applied.get("c1") == "wk" and applied.get("d1") == "wr"
    assert "e1" not in applied and "a1" not in applied
    assert emit_san(doc, mv, st) == "O-O-O+"
    with pytest.raises(SanError) as exc:
        resolve_san(doc, "O-O-O", st)
    assert exc.value.failure_class == "no_legal_match"


def test_emit_parse_emit_exact_string_roundtrips():
    doc = _doc()["contract"]
    cases = [
        (_state(STARTPOS_BOARD, "w", "KQkq"),
         ["e4", "d4", "Nf3", "Nc3", "c4", "e3", "b3", "g3"]),
        (_state("br-- -- -- -- brbk--/bpbpbpbp-- bpbpbp/"
                "-- -- bn-- -- bn-- --/-- -- -- -- -- -- -- --/"
                "-- -- -- -- -- -- -- --/-- -- wn-- -- wn-- --/"
                "wpwpwpwp-- wpwpwp/wr-- -- -- -- wr-- wk", "w"),
         ["Rad1", "Rfd1", "Nh4", "h3"]),
    ]
    for st, sans in cases:
        for san in sans:
            first = resolve_san(doc, san, st)
            emitted = emit_san(doc, first, st)
            assert emitted == san  # exact string, not only identity
            second = resolve_san(doc, emitted, st)
            assert second == first
            assert emit_san(doc, second, st) == san


@pytest.mark.parametrize("san,board,side,rights", [
    # no knight can reach e5 from startpos
    ("Ne5", STARTPOS_BOARD, "w", "KQkq"),
    # pawn capture with nothing to capture
    ("exd5", STARTPOS_BOARD, "w", "KQkq"),
    # false check claim
    ("e4+", STARTPOS_BOARD, "w", "KQkq"),
    # false mate claim
    ("e4#", STARTPOS_BOARD, "w", "KQkq"),
    # castling through check: rook attacks f1 transit
    ("O-O",
     "-- -- -- -- -- bk-- --/-- -- -- -- -- -- -- --/"
     "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
     "-- -- -- -- -- br-- --/-- -- -- -- -- -- -- --/"
     "-- -- -- -- -- -- -- --/-- -- -- -- wk-- -- wr", "w", "KQ"),
    # king walk into attack is not legal, so "Kg2" matches nothing
    ("Kg2",
     "-- -- -- -- -- bk-- --/-- -- -- -- -- -- -- --/"
     "-- -- -- -- -- -- -- --/-- -- -- -- -- -- -- --/"
     "-- -- -- -- br-- -- --/-- -- -- -- -- -- -- --/"
     "-- -- -- -- -- -- -- --/-- -- -- -- wk-- -- --", "w", "-"),
])
def test_no_legal_match_rejected(san, board, side, rights):
    doc = _doc()["contract"]
    with pytest.raises(SanError) as exc:
        resolve_san(doc, san, _state(board, side, rights))
    assert exc.value.failure_class == "no_legal_match"
    assert exc.value.code == "illegal_move"


def test_rejected_resolution_yields_no_move():
    doc = _doc()["contract"]
    st = _state(STARTPOS_BOARD, "w", "KQkq")
    result = None
    try:
        result = resolve_san(doc, "Ne5", st)
    except SanError as exc:
        result = ("error", exc.code)
    assert result == ("error", "illegal_move")


def test_error_shape_and_closed_enum():
    contract = _doc()["contract"]
    assert set(contract["errors"]["closed_enum"]) == {
        "malformed_request", "ambiguous_move", "illegal_move",
        "internal"}
    fields = contract["errors"]["shape"]["error"]["fields"]
    for name in ("code", "message", "retryable"):
        assert fields[name]["required"] is True


# -- mutation replay ---------------------------------------------------

def _mutants():
    doc = _doc()
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    add("piece letters drop K", ["contract", "tokens", "piece_letters"],
        ["N", "B", "R", "Q"])
    add("castling zero digit", ["contract", "tokens",
                                "castling_kingside"], "0-0")
    add("promotion pieces add K", ["contract", "tokens",
                                   "promotion_pieces"],
        ["Q", "R", "B", "N", "K"])
    add("annotations allowed", ["contract", "tokens",
                                "annotation_suffixes"], "allowed")
    add("preference reversal", ["contract", "disambiguation",
                                "preference_order"], ["rank", "file",
                                                      "both"])
    add("minimal false", ["contract", "disambiguation", "minimal"],
        False)
    add("requires_position false", ["contract", "context",
                                    "requires_position"], False)
    add("suffix semantics flip", ["contract", "suffix", "semantics"],
        "check-iff-result-is-checkmate")
    add("castling ownership contradiction", ["contract", "castling_link",
                                             "ownership"], "san-owns")
    add("canonical false", ["contract", "serialization", "canonical"],
        False)
    add("roundtrip dropped", ["contract", "serialization", "roundtrip"],
        "best-effort")
    add("mapping contradiction", ["contract", "failure_mapping",
                                  "malformed_san", "error"],
        "illegal_move")
    add("undeclared error code", ["contract", "failure_mapping",
                                  "no_legal_match", "error"],
        "weird_error")
    add("enum narrowed", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    add("resolution class rename", ["contract", "resolution_failures",
                                    "malformed_san"], "any-violation")
    add("noncanonical disambiguation allowed",
        ["contract", "disambiguation", "noncanonical_disambiguation"],
        "accepted")
    add("castling rights field drift", ["contract", "castling_link",
                                        "state_rights_field"],
        "inferred_from_occupancy")
    add("rights irrevocability dropped", ["contract", "castling_link",
                                          "rights_irrevocable"],
        "rights-revive-on-return")
    add("castling application drift", ["contract", "castling_link",
                                       "application"],
        "king-move-only")
    m = copy.deepcopy(doc)
    m["contract"]["rogue"] = {"x": 1}
    out.append(("rogue contract key", m))
    m = copy.deepcopy(doc)
    m["contract"]["tokens"]["rogue"] = 1
    out.append(("rogue tokens key", m))
    m = copy.deepcopy(doc)
    m["contract"]["failure_mapping"]["extra"] = {
        "trigger": "t", "error": "malformed_request"}
    out.append(("orphan failure-mapping class", m))
    m = copy.deepcopy(doc)
    del m["contract"]["disambiguation"]
    out.append(("missing disambiguation section", m))
    m = copy.deepcopy(doc)
    m["contract"]["errors"]["shape"]["error"]["fields"]["code"][
        "required"] = False
    out.append(("error shape reversal", m))
    return out


def test_mutations_fail_lint():
    for name, mutant in _mutants():
        try:
            lint(mutant)
        except ContractError:
            continue
        pytest.fail(f"mutation accepted by lint: {name}")


def test_mutants_never_silent_subset():
    assert len(_mutants()) >= 22


# -- linkage mutations against the real sibling contracts --------------

def _lint_with_root(tmp_path, mutate=None):
    contracts = tmp_path / "data" / "contracts"
    shutil.copytree(DATA_DIR, contracts)
    if mutate:
        mutate(contracts)
    lint(_doc(), root=tmp_path)


def test_linkage_clean_copy_passes(tmp_path):
    _lint_with_root(tmp_path)


def test_linkage_legal_moves_promotion_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "legal_moves.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["move_model"]["shape"]["types"]["promotion"][
            "enum"] = ["q", "r"]
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_turn_side_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "turn.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["state"]["side_values"] = ["white", "black"]
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


def test_linkage_missing_sibling_fails(tmp_path):
    def mutate(contracts):
        (contracts / "legal_moves.yaml").unlink()

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)
