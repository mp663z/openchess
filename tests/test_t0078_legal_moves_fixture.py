"""T0078: legal-moves conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0077 legal-moves
contract. All semantics are derived from the parsed contract document
(data/contracts/legal_moves.yaml), never hardcoded in the test: move
shape (closed keys, distinct squares, square grammar, promotion enum and
presence), promotion expansion, per-piece movement (leaper deltas,
slider directions, pawn forward/double/capture geometry per side,
promotion ranks, never-backward), occupancy rules, the attack relation
(occupancy-irrelevant, attacker king safety ignored), the king-safety
legality filter, the move-set terminal classification, failure classes,
failure_mapping and the closed error enum all come from the YAML. Every
malformed case is discriminating (repairing ONLY its declared defect
makes it valid) and rollback cases assert the pre-state survives a
rejected move bit-identically. Validation order is pinned: shape ->
no_piece -> not_players_piece -> unreachable_target -> promotion rules
-> leaves_king_attacked.

DESIGN CAUTION: the reference interpreter in this file is derived from
the same contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work must
execute these same cases against a separately implemented runtime.
Position application, en-passant, castling and every non-move-set
terminal outcome are owned by their own contracts and fixtures and are
out of scope here; wrong_variant is unreachable within this fixture
because variant identity is owned by the chess-variant contract and
every state here is standard chess.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "legal_moves" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "legal_moves.yaml")
                     .read_text())
C = DOC["contract"]

SHAPE = dict(C["move_model"]["shape"])
REQUIRED = list(SHAPE["required"])
OPTIONAL = list(SHAPE["optional"])
CLOSED_KEYS = SHAPE["closed_keys"] is True
FROM_TO_DISTINCT = SHAPE["from_to_distinct"] is True
PROMO_ENUM = list(SHAPE["types"]["promotion"]["enum"])
PROMO_PRESENCE = SHAPE["types"]["promotion"]["presence"]
FILES = list(C["move_model"]["square_grammar"]["files"])
RANKS = list(C["move_model"]["square_grammar"]["ranks"])
EXPANSION = dict(C["move_model"]["promotion_expansion"])
MOVEMENT = dict(C["movement"])
PAWN = dict(MOVEMENT["pawn"])
OCCUPANCY = dict(MOVEMENT["occupancy"])
ATTACK = dict(C["attack"])
LEGALITY = dict(C["legality"])
TERMINAL = dict(C["move_set_terminal_status"])
FAILURE_CLASSES = list(C["failure_classes"])
FAILURE_MAPPING = dict(C["failure_mapping"])
ERROR_ENUM = list(C["errors"]["closed_enum"])

SIDES = {"w", "b"}
OTHER = {"w": "b", "b": "w"}
SIDE_NAME = {"w": "white", "b": "black"}
ALL_SQUARES = {f + r for f in FILES for r in RANKS}
STATE_KEYS = {"occupied", "side_to_move"}

KNIGHT_DELTAS = [tuple(d) for d in MOVEMENT["knight"]["deltas"]]
KING_DELTAS = [tuple(d) for d in MOVEMENT["king"]["deltas"]]
ROOK_DIRS = [tuple(d) for d in MOVEMENT["rook"]["directions"]]
BISHOP_DIRS = [tuple(d) for d in MOVEMENT["bishop"]["directions"]]
QUEEN_DIRS = [tuple(d) for d in MOVEMENT["queen"]["directions"]]
PROMO_RANKS = dict(PAWN["promotion_ranks"])
DOUBLE_RANKS = dict(PAWN["double"]["ranks"])
FORWARD = {s: (PAWN["forward"][SIDE_NAME[s]]["file_delta"],
               PAWN["forward"][SIDE_NAME[s]]["rank_delta"]) for s in "wb"}
CAPTURE_DELTAS = {s: [tuple(d) for d in PAWN["capture_deltas"][SIDE_NAME[s]]]
                  for s in "wb"}

TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes",
            "happy", "boundary", "terminal", "malformed", "rollback"}
KIND_KEYS = {
    "move": {"name", "kind", "state", "move"},
    "not-move": {"name", "kind", "state", "move"},
    "attack": {"name", "kind", "state", "attacker", "square",
               "expect_attacked"},
    "move-set-for-square": {"name", "kind", "state", "from_square",
                            "expect_to_squares"},
    "expansion": {"name", "kind", "state", "from_square", "to_square",
                  "expect_promotions"},
    "terminal": {"name", "kind", "state", "expect_status"},
}
MALFORMED_REQUIRED = {"name", "state", "move", "defect", "expect_failure"}
ROLLBACK_REQUIRED = MALFORMED_REQUIRED | {"expect_state_after"}

SECTION_COUNTS = {"happy": 15, "boundary": 31, "terminal": 4,
                  "malformed": 14, "rollback": 2}
TERMINAL_STATUSES = {"checkmate": 1, "stalemate": 1, "check": 1, "none": 1}
FAILURE_CLASS_COUNTS = {"malformed_move": 7, "no_piece": 1,
                        "not_players_piece": 1, "unreachable_target": 1,
                        "promotion_missing": 1, "promotion_forbidden": 2,
                        "leaves_king_attacked": 1}


class LegalMoveFailure(Exception):
    def __init__(self, failure_class: str) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.error = FAILURE_MAPPING[failure_class]["error"]


def _xy(square: str) -> tuple[int, int]:
    return FILES.index(square[0]), RANKS.index(square[1])


def _sq(x: int, y: int) -> str:
    return FILES[x] + RANKS[y]


def _on_board(x: int, y: int) -> bool:
    return 0 <= x < len(FILES) and 0 <= y < len(RANKS)


def _grammar_ok(square) -> bool:
    """Square-grammar predicate derived ONLY from the contract: exactly
    one declared file char then one declared rank char."""
    return (type(square) is str and len(square) == 2
            and square[0] in FILES and square[1] in RANKS)


def _validate_state(state: dict, where: str) -> None:
    assert type(state) is dict, f"{where}: state must be a mapping"
    assert set(state) == STATE_KEYS, (
        f"{where}: state keys {sorted(state)} != {sorted(STATE_KEYS)}")
    occ = state["occupied"]
    assert type(occ) is dict, f"{where}: occupied must be a mapping"
    for sq, tok in occ.items():
        assert type(sq) is str and sq in ALL_SQUARES, (
            f"{where}: occupied key {sq!r} is not a square")
        assert (type(tok) is str and len(tok) == 2
                and tok[0] in "wb" and tok[1] in "pnbrqk"), (
            f"{where}: bad piece token {tok!r}")
    stm = state["side_to_move"]
    assert type(stm) is str and stm in SIDES, f"{where}: bad side_to_move"
    for side in SIDES:
        kings = [sq for sq, tok in occ.items() if tok == side + "k"]
        assert len(kings) == 1, (
            f"{where}: exactly one {side} king required, found {len(kings)}")


def _pseudo_targets(occ: dict, sq: str) -> set:
    """Pseudo-legal destinations for the piece on sq, derived from the
    contract movement and occupancy sections (no king-safety filter).
    Occupancy: own-piece squares unreachable, enemy squares capture-only,
    a slider's ray ends at the first piece (included when enemy)."""
    tok = occ[sq]
    side, piece = tok[0], tok[1]
    x, y = _xy(sq)
    targets: set = set()
    leaper = {"n": KNIGHT_DELTAS, "k": KING_DELTAS}.get(piece)
    slider = {"r": ROOK_DIRS, "b": BISHOP_DIRS, "q": QUEEN_DIRS}.get(piece)
    if leaper is not None:
        for dx, dy in leaper:
            nx, ny = x + dx, y + dy
            if _on_board(nx, ny) and occ.get(_sq(nx, ny), "  ")[0] != side:
                targets.add(_sq(nx, ny))
    elif slider is not None:
        for dx, dy in slider:
            nx, ny = x + dx, y + dy
            while _on_board(nx, ny):
                hit = occ.get(_sq(nx, ny))
                if hit is None:
                    targets.add(_sq(nx, ny))
                else:
                    if hit[0] != side:
                        targets.add(_sq(nx, ny))
                    break  # any piece ends the ray before the next square
                nx, ny = nx + dx, ny + dy
    elif piece == "p":
        fx, fy = FORWARD[side]
        one = (x + fx, y + fy)
        if _on_board(*one) and _sq(*one) not in occ:
            targets.add(_sq(*one))
            two = (x + 2 * fx, y + 2 * fy)
            if (RANKS[y] == DOUBLE_RANKS[SIDE_NAME[side]]
                    and _on_board(*two) and _sq(*two) not in occ):
                targets.add(_sq(*two))
        for dx, dy in CAPTURE_DELTAS[side]:
            nx, ny = x + dx, y + dy
            hit = occ.get(_sq(nx, ny)) if _on_board(nx, ny) else None
            if hit is not None and hit[0] != side:
                targets.add(_sq(nx, ny))
    return targets


def _attacked_squares(occ: dict, side: str) -> set:
    """The attack relation: pseudo-legal capture targets, occupancy-
    irrelevant (empty, enemy AND own squares all attacked), attacker
    king safety ignored; pawns attack exactly their diagonal forward
    squares; kings attack their adjacent squares."""
    attacked: set = set()
    for sq, tok in occ.items():
        if tok[0] != side:
            continue
        piece = tok[1]
        x, y = _xy(sq)
        leaper = {"n": KNIGHT_DELTAS, "k": KING_DELTAS}.get(piece)
        slider = {"r": ROOK_DIRS, "b": BISHOP_DIRS,
                  "q": QUEEN_DIRS}.get(piece)
        if leaper is not None:
            for dx, dy in leaper:
                nx, ny = x + dx, y + dy
                if _on_board(nx, ny):
                    attacked.add(_sq(nx, ny))
        elif slider is not None:
            for dx, dy in slider:
                nx, ny = x + dx, y + dy
                while _on_board(nx, ny):
                    attacked.add(_sq(nx, ny))
                    if _sq(nx, ny) in occ:
                        break  # the first piece's square is attacked
                    nx, ny = nx + dx, ny + dy
        elif piece == "p":
            for dx, dy in CAPTURE_DELTAS[side]:
                nx, ny = x + dx, y + dy
                if _on_board(nx, ny):
                    attacked.add(_sq(nx, ny))
    return attacked


def _king_square(occ: dict, side: str) -> str:
    return next(sq for sq, tok in occ.items() if tok == side + "k")


def _apply(occ: dict, move: dict) -> dict:
    new = dict(occ)
    tok = new.pop(move["from_square"])
    new.pop(move["to_square"], None)  # capture removes the target piece
    if "promotion" in move:
        tok = tok[0] + move["promotion"]
    new[move["to_square"]] = tok
    return new


def _legal_moves(state: dict) -> list:
    """The legal move set: pseudo-legal moves, promotion-rank pawn moves
    expanded to exactly one move per promotion value (no unpromoted
    move to the promotion rank exists), filtered to moves whose
    resulting position leaves the mover's own king unattacked."""
    occ = state["occupied"]
    side = state["side_to_move"]
    promo_rank = PROMO_RANKS[SIDE_NAME[side]]
    moves: list = []
    for sq, tok in sorted(occ.items()):
        if tok[0] != side:
            continue
        for t in sorted(_pseudo_targets(occ, sq)):
            promos = PROMO_ENUM \
                if tok[1] == "p" and t[1] == promo_rank else [None]
            for pr in promos:
                mv = {"from_square": sq, "to_square": t}
                if pr is not None:
                    mv["promotion"] = pr
                new = _apply(occ, mv)
                if _king_square(new, side) not in \
                        _attacked_squares(new, OTHER[side]):
                    moves.append(mv)
    return moves


def _terminal_status(state: dict) -> str:
    """Move-set terminal classification, derived ONLY from the legal
    move set and the check state: check (king attacked), checkmate
    (check with zero legal moves), stalemate (no check with zero legal
    moves); a game with at least one legal move is never
    move-set-terminal."""
    occ = state["occupied"]
    side = state["side_to_move"]
    check = _king_square(occ, side) in _attacked_squares(occ, OTHER[side])
    zero = len(_legal_moves(state)) == 0
    if check and zero:
        return "checkmate"
    if zero:
        return "stalemate"
    if check:
        return "check"
    return "none"


def _validate_move(state: dict, move: dict) -> None:
    """Pinned validation order: shape -> no_piece -> not_players_piece
    -> unreachable_target -> promotion rules -> leaves_king_attacked.
    Each failure raises with its contract failure class."""
    occ = state["occupied"]
    side = state["side_to_move"]
    # shape: closed keys, required members, distinct squares, grammar,
    # promotion enum - any violation is malformed_move
    if type(move) is not dict:
        raise LegalMoveFailure("malformed_move")
    allowed = set(REQUIRED) | set(OPTIONAL) if CLOSED_KEYS else set(move)
    if set(move) != (set(REQUIRED) | (set(move) & set(OPTIONAL))):
        raise LegalMoveFailure("malformed_move")
    if not set(move) <= allowed or not set(REQUIRED) <= set(move):
        raise LegalMoveFailure("malformed_move")
    fr, to = move["from_square"], move["to_square"]
    if not _grammar_ok(fr) or not _grammar_ok(to):
        raise LegalMoveFailure("malformed_move")
    if FROM_TO_DISTINCT and fr == to:
        raise LegalMoveFailure("malformed_move")
    if "promotion" in move and type(move["promotion"]) is not str:
        raise LegalMoveFailure("malformed_move")
    # a syntactically valid string outside the enum is NOT a shape
    # violation: the contract maps it to promotion_forbidden at the
    # promotion-rules stage (trigger:
    # promotion-member-on-non-promotion-move-or-outside-enum)
    # piece presence and ownership
    if fr not in occ:
        raise LegalMoveFailure("no_piece")
    if occ[fr][0] != side:
        raise LegalMoveFailure("not_players_piece")
    # reachability
    if to not in _pseudo_targets(occ, fr):
        raise LegalMoveFailure("unreachable_target")
    # promotion rules: an out-of-enum string promotion is
    # promotion_forbidden per the contract trigger; promotion is
    # required exactly when a pawn reaches its promotion rank and
    # forbidden on every other move
    if "promotion" in move and move["promotion"] not in PROMO_ENUM:
        raise LegalMoveFailure("promotion_forbidden")
    promoting = occ[fr][1] == "p" and to[1] == PROMO_RANKS[SIDE_NAME[side]]
    if promoting and "promotion" not in move:
        raise LegalMoveFailure("promotion_missing")
    if not promoting and "promotion" in move:
        raise LegalMoveFailure("promotion_forbidden")
    # king safety of the resulting position
    new = _apply(occ, move)
    if _king_square(new, side) in _attacked_squares(new, OTHER[side]):
        raise LegalMoveFailure("leaves_king_attacked")


def _check_name(case: dict, where: str) -> None:
    assert type(case.get("name")) is str and case["name"], (
        f"{where}: case needs a name")


def _check_case_common(case: dict, where: str) -> None:
    _check_name(case, where)
    _validate_state(case["state"], f"{where} {case['name']}")


def test_contract_premises():
    """The contract structures this fixture derives from say what the
    fixture design assumes - pinned exactly so a contract edit that
    changes semantics breaks here, not silently."""
    assert REQUIRED == ["from_square", "to_square"]
    assert OPTIONAL == ["promotion"]
    assert CLOSED_KEYS and FROM_TO_DISTINCT
    assert PROMO_ENUM == ["q", "r", "b", "n"]
    assert "k" not in PROMO_ENUM and "p" not in PROMO_ENUM
    assert PROMO_PRESENCE == "required-exactly-when-promoting"
    assert EXPANSION["expands_to"] == 4
    assert EXPANSION["values"] == PROMO_ENUM
    assert EXPANSION["unpromoted_last_rank_move"] == "none"
    assert len(KNIGHT_DELTAS) == 8 and MOVEMENT["knight"]["jumps"] is True
    assert len(KING_DELTAS) == 8 and MOVEMENT["king"]["jumps"] is False
    for p in ("rook", "bishop", "queen"):
        assert MOVEMENT[p]["slides"] is True
    assert PAWN["never_backward"] is True
    assert DOUBLE_RANKS == {"white": "2", "black": "7"}
    assert PROMO_RANKS == {"white": "8", "black": "1"}
    assert OCCUPANCY == {
        "own_piece_square": "unreachable",
        "enemy_piece_square": "capture-only",
        "sliding_block": "any-piece-ends-the-ray-before-it"}
    assert ATTACK["attacker_king_safety"] == "ignored"
    assert set(ATTACK["target_occupancy"].values()) == {"attacked"}
    assert ATTACK["king_attacks"] == "adjacent-squares"
    assert ATTACK["pawn_attacks"] == "diagonal-forward-squares"
    assert LEGALITY["filter"] == \
        "resulting-position-leaves-own-king-unattacked"
    assert TERMINAL["yields"] == ["check", "checkmate", "stalemate"]
    assert TERMINAL["other_outcomes"]["owner"] == "chess-turn-contract"
    assert set(FAILURE_CLASSES) == set(FAILURE_MAPPING)
    assert set(FAILURE_CLASSES) == {
        "malformed_move", "no_piece", "not_players_piece",
        "unreachable_target", "promotion_missing", "promotion_forbidden",
        "leaves_king_attacked", "wrong_variant"}
    for mapping in FAILURE_MAPPING.values():
        assert mapping["error"] in ERROR_ENUM


def test_fixture_shape():
    assert set(CASES) == TOP_KEYS
    assert CASES["schema"] == 1
    assert CASES["contract"] == "data/contracts/legal_moves.yaml"
    assert CASES["contract_schema_version"] == DOC["schema_version"]
    for section in ("happy", "boundary", "terminal"):
        for case in CASES[section]:
            _check_case_common(case, section)
            assert set(case) == KIND_KEYS[case["kind"]], (
                f"{section} {case['name']}: keys {sorted(case)}")
    for case in CASES["malformed"]:
        _check_name(case, "malformed")
        assert set(case) == MALFORMED_REQUIRED, case["name"]
        assert type(case["defect"]) is str and case["defect"]
        assert case["expect_failure"] in FAILURE_CLASSES, case["name"]
    for case in CASES["rollback"]:
        _check_name(case, "rollback")
        assert set(case) == ROLLBACK_REQUIRED, case["name"]
        _validate_state(case["state"], f"rollback {case['name']}")
        assert case["expect_failure"] in FAILURE_CLASSES, case["name"]


def test_count_pins():
    assert {s: len(CASES[s]) for s in SECTION_COUNTS} == SECTION_COUNTS
    got: dict = {}
    for case in CASES["terminal"]:
        got[case["expect_status"]] = got.get(case["expect_status"], 0) + 1
    assert got == TERMINAL_STATUSES
    got = {}
    for case in CASES["malformed"]:
        fc = case["expect_failure"]
        got[fc] = got.get(fc, 0) + 1
    assert got == FAILURE_CLASS_COUNTS
    # every failure class reachable in a standard-variant fixture fires
    covered = {c["expect_failure"] for c in CASES["malformed"]} | \
        {c["expect_failure"] for c in CASES["rollback"]}
    assert covered == set(FAILURE_CLASSES) - {"wrong_variant"}


def test_happy():
    for case in CASES["happy"]:
        if case["kind"] != "move":
            raise AssertionError(f"happy case {case['name']} kind "
                                 f"{case['kind']}")
        _validate_move(case["state"], case["move"])  # must not raise
        assert case["move"] in _legal_moves(case["state"]), case["name"]


def test_boundary():
    for case in CASES["boundary"]:
        state = case["state"]
        kind = case["kind"]
        if kind == "move":
            _validate_move(state, case["move"])
            assert case["move"] in _legal_moves(state), case["name"]
        elif kind == "not-move":
            assert case["move"] not in _legal_moves(state), case["name"]
        elif kind == "attack":
            got = case["square"] in _attacked_squares(
                state["occupied"], case["attacker"])
            assert got == case["expect_attacked"], case["name"]
        elif kind == "move-set-for-square":
            got = {m["to_square"] for m in _legal_moves(state)
                   if m["from_square"] == case["from_square"]
                   and "promotion" not in m}
            assert got == set(case["expect_to_squares"]), case["name"]
        elif kind == "expansion":
            got = sorted(m["promotion"] for m in _legal_moves(state)
                         if m["from_square"] == case["from_square"]
                         and m["to_square"] == case["to_square"])
            assert got == sorted(case["expect_promotions"]), case["name"]
            assert len(got) == EXPANSION["expands_to"], case["name"]
            # no unpromoted move to the promotion rank exists
            assert {"from_square": case["from_square"],
                    "to_square": case["to_square"]} not in \
                _legal_moves(state), case["name"]
        else:
            raise AssertionError(f"{case['name']}: bad kind {kind}")


def test_terminal():
    for case in CASES["terminal"]:
        assert case["expect_status"] in set(TERMINAL["yields"]) | {"none"}
        assert _terminal_status(case["state"]) == case["expect_status"], (
            case["name"])


def test_malformed():
    for case in CASES["malformed"]:
        try:
            _validate_move(case["state"], case["move"])
        except LegalMoveFailure as e:
            assert e.failure_class == case["expect_failure"], (
                f"{case['name']}: {e.failure_class} != "
                f"{case['expect_failure']}")
            assert e.error in ERROR_ENUM, case["name"]
        else:
            raise AssertionError(f"{case['name']}: defect did not fire")


def test_malformed_discriminating():
    """Every malformed case names exactly one defect: repairing ONLY
    that defect makes the case valid, so no second defect hides."""
    for case in CASES["malformed"]:
        state = copy.deepcopy(case["state"])
        move = copy.deepcopy(case["move"])
        fc = case["expect_failure"]
        name = case["name"]
        if name == "missing-to-square":
            move["to_square"] = "e4"
        elif name == "extra-key":
            del move["kind"]
        elif name == "from-equals-to":
            move["to_square"] = "e3"
        elif name in ("square-wrong-case", "square-rank-out-of-range"):
            move["to_square"] = "e4"
        elif name in ("promotion-outside-enum", "promotion-wrong-type-int",
                      "promotion-wrong-type-list"):
            move["promotion"] = "q"
        elif name == "from-square-empty":
            state["occupied"]["e2"] = "wp"
        elif name == "enemy-piece-on-from":
            state["side_to_move"] = "b"
        elif name == "target-not-reachable":
            move["to_square"] = "f5"
        elif name == "promotion-missing":
            move["promotion"] = "n"
        elif name == "promotion-on-non-promotion-move":
            del move["promotion"]
        elif name == "pinned-rook-leaves-king-attacked":
            move["to_square"] = "e8"
        else:
            raise AssertionError(f"no repair rule for {name}")
        _validate_state(state, f"repaired {name}")
        try:
            _validate_move(state, move)
        except LegalMoveFailure as e:
            raise AssertionError(
                f"{name}: repairing only the declared defect still "
                f"fails with {e.failure_class}") from e
        assert move in _legal_moves(state), f"repaired {name} not legal"
        assert fc in FAILURE_MAPPING


def test_rollback():
    for case in CASES["rollback"]:
        pre = copy.deepcopy(case["state"])
        try:
            _validate_move(case["state"], case["move"])
        except LegalMoveFailure as e:
            assert e.failure_class == case["expect_failure"], case["name"]
            assert e.error in ERROR_ENUM, case["name"]
        else:
            raise AssertionError(f"{case['name']}: defect did not fire")
        assert case["state"] == pre, (
            f"{case['name']}: state mutated by rejected move")
        assert case["state"] == case["expect_state_after"], (
            f"{case['name']}: pre-state does not survive bit-identically")
