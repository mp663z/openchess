"""T0069: en-passant conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0068 en-passant
contract. All semantics are derived from the parsed contract document
(data/contracts/en_passant.yaml), never hardcoded in the test: target
grammar (none sentinel/files/ranks/empty), set_on (event, target_file,
per-side rank triples), lifetime, the storage-vs-identity split,
capture mechanics (per-side mover/captured/target ranks, adjacent-file
relation), turn linkage, failure classes, failure_mapping and the
closed error enum all come from the YAML. Every malformed case is
discriminating (repairing ONLY its declared defect makes it valid) and
rollback cases assert the pre-state survives a rejected capture
bit-identically.

DESIGN CAUTION: the reference interpreter in this file is derived from
the same contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work must
execute these same cases against a separately implemented runtime."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "en_passant" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "en_passant.yaml").read_text())
C = DOC["contract"]

GRAMMAR = dict(C["target"]["grammar"])
NONE = GRAMMAR["none_sentinel"]
FILES = list(GRAMMAR["files"])
RANKS = list(GRAMMAR["ranks"])
SET_ON = {k: v for k, v in C["target"]["set_on"].items() if k != "rule"}
LIFETIME = {k: v for k, v in C["target"]["lifetime"].items() if k != "rule"}
SVI = {k: v for k, v in C["target"]["storage_vs_identity"].items()
       if k != "rule"}
MOVER = {k: v for k, v in C["capture"]["mover"].items() if k != "rule"}
TURN = dict(C["turn_linkage"])
IDENTITY_PARTICIPATES = C["identity"]["participates"]
FAILURE_MAPPING = dict(C["failure_mapping"])
ERROR_ENUM = list(C["errors"]["closed_enum"])

SIDES = {"w", "b"}
OTHER = {"w": "b", "b": "w"}
SIDE_NAME = {"w": "white", "b": "black"}
MOVE_TYPES = {"pawn-advance", "ep-capture", "quiet"}
ALL_SQUARES = {f + r for f in "abcdefgh" for r in "12345678"}

STATE_KEYS = {"ep_target", "occupied", "side_to_move"}
MOVE_KEY_SETS = {
    "pawn-advance": {"type", "from", "to"},
    "ep-capture": {"type", "from", "to"},
    "quiet": {"type", "from", "to"},
}

TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes",
            "happy", "boundary", "malformed", "rollback"}
KIND_KEYS = {
    "set-target": {"name", "kind", "state", "move", "expect_target",
                   "expect_occupied", "expect_turn"},
    "ep-capture": {"name", "kind", "state", "move", "expect_target",
                   "expect_occupied", "expect_turn"},
    "other-move": {"name", "kind", "state", "move", "expect_target",
                   "expect_occupied", "expect_turn"},
    "identity": {"name", "kind", "state", "expect_identity_value",
                 "expect_stored_target"},
}
HAPPY_KINDS = {"set-target", "ep-capture"}
BOUNDARY_KINDS = {"ep-capture", "other-move", "identity"}
MALFORMED_REQUIRED = {"name", "kind", "state", "move", "defect",
                      "expect_failure"}
ROLLBACK_REQUIRED = MALFORMED_REQUIRED | {"expect_state_after"}
MOVE_DOMAIN = {
    "set-target": {"pawn-advance"},
    "ep-capture": {"ep-capture"},
    "other-move": {"quiet"},
}

SECTION_COUNTS = {"happy": 4, "boundary": 5, "malformed": 7, "rollback": 2}
HAPPY_KIND_COUNTS = {"set-target": 2, "ep-capture": 2}
BOUNDARY_KIND_COUNTS = {"ep-capture": 2, "other-move": 1, "identity": 2}
FAILURE_CLASS_COUNTS = {"target_malformed": 1, "target_inconsistent": 2,
                        "capture_precondition": 3, "pinned_capture": 1}


class EnPassantFailure(Exception):
    def __init__(self, failure_class: str) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.error = FAILURE_MAPPING[failure_class]["error"]


def _grammar_ok(target) -> bool:
    """Canonical-target predicate derived ONLY from the contract grammar:
    the none sentinel stands alone; empty is forbidden; otherwise exactly
    one declared file plus one declared rank."""
    if type(target) is not str:
        return False
    if target == NONE:
        return True
    if target == "":
        return False
    if len(target) != 2:
        return False
    return target[0] in FILES and target[1] in RANKS


def _validate_state(state: dict, where: str) -> None:
    assert type(state) is dict, f"{where}: state must be a mapping"
    assert set(state) == STATE_KEYS, (
        f"{where}: state keys {sorted(state)} != {sorted(STATE_KEYS)}")
    t = state["ep_target"]
    assert type(t) is str, f"{where}: ep_target must be a string"
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


def _validate_move(move: dict, where: str) -> None:
    assert type(move) is dict, f"{where}: move must be a mapping"
    t = move.get("type")
    assert type(t) is str and t in MOVE_TYPES, f"{where}: bad move type {t!r}"
    assert set(move) == MOVE_KEY_SETS[t], (
        f"{where}: move keys {sorted(move)} != {sorted(MOVE_KEY_SETS[t])}")
    for k in ("from", "to"):
        sq = move[k]
        assert type(sq) is str and sq in ALL_SQUARES, (
            f"{where}: bad square {sq!r}")


def _rank_ok(target: str, side: str) -> bool:
    """A stored target is available only to the advancing side's
    opponent (contract.lifetime.available_to): white captures targets
    left by black's advance (black target_rank), and vice versa."""
    advancer = OTHER[side]
    return target[1] == SET_ON[SIDE_NAME[advancer]]["target_rank"]


def _attacked(occ: dict, square: str, by_side: str) -> bool:
    """Minimal slider/knight/pawn/king attack probe over the partial
    occupied map, used only for the pinned-capture precondition (the
    contract evaluates legality on the RESULTING position)."""
    f0, r0 = ord(square[0]) - 97, int(square[1])
    for sq, tok in occ.items():
        if tok[0] != by_side:
            continue
        f1, r1 = ord(sq[0]) - 97, int(sq[1])
        df, dr = f1 - f0, r1 - r0
        piece = tok[1]
        if piece == "p":
            pawn_dr = 1 if by_side == "w" else -1
            if dr == pawn_dr and abs(df) == 1:
                return True
        elif piece == "n":
            if (abs(df), abs(dr)) in ((1, 2), (2, 1)):
                return True
        elif piece == "k":
            if max(abs(df), abs(dr)) == 1 and (df, dr) != (0, 0):
                return True
        elif (piece in "rq" and (df == 0 or dr == 0) and (df, dr) != (0, 0)) or (
                piece in "bq" and abs(df) == abs(dr) and df != 0):
            step_f = (df > 0) - (df < 0)
            step_r = (dr > 0) - (dr < 0)
            if _clear(occ, square, sq, step_f, step_r):
                return True
    return False


def _clear(occ: dict, a: str, b: str, sf: int, sr: int) -> bool:
    f, r = ord(a[0]) - 97 + sf, int(a[1]) + sr
    bf, br = ord(b[0]) - 97, int(b[1])
    while (f, r) != (bf, br):
        if chr(97 + f) + str(r) in occ:
            return False
        f += sf
        r += sr
    return True


def _apply_capture(state: dict, move: dict) -> dict:
    side = state["side_to_move"]
    target = state["ep_target"]
    if not _grammar_ok(target):
        raise EnPassantFailure("target_malformed")
    if target == NONE:
        raise EnPassantFailure("capture_precondition")  # stale/no target
    if not _rank_ok(target, side):
        raise EnPassantFailure("target_inconsistent")
    m = MOVER[SIDE_NAME[side]]
    captured_sq = target[0] + m["captured_rank"]
    occ = state["occupied"]
    if occ.get(captured_sq) != OTHER[side] + "p":
        raise EnPassantFailure("target_inconsistent")
    frm, to = move["from"], move["to"]
    if to != target:
        raise EnPassantFailure("capture_precondition")
    if frm[1] != m["mover_rank"]:
        raise EnPassantFailure("capture_precondition")
    if abs(ord(frm[0]) - ord(target[0])) != 1:
        raise EnPassantFailure("capture_precondition")
    if occ.get(frm) != side + "p":
        raise EnPassantFailure("capture_precondition")
    new_occ = {sq: tok for sq, tok in occ.items()
               if sq not in (frm, captured_sq)}
    new_occ[to] = side + "p"
    king_sq = next((sq for sq, tok in new_occ.items() if tok == side + "k"),
                   None)
    if king_sq is not None and _attacked(new_occ, king_sq, OTHER[side]):
        raise EnPassantFailure("pinned_capture")
    out = copy.deepcopy(state)
    out["occupied"] = new_occ
    out["ep_target"] = NONE
    out["side_to_move"] = OTHER[side]
    return out


def _apply(state: dict, move: dict) -> dict:
    """Reference interpreter derived from the contract. Never mutates the
    input; a rejected capture leaves the state bit-identical (rollback)."""
    _validate_state(state, "apply")
    _validate_move(move, "apply")
    t = move["type"]
    if t == "ep-capture":
        return _apply_capture(state, move)
    side = state["side_to_move"]
    out = copy.deepcopy(state)
    out["side_to_move"] = OTHER[side]
    out["ep_target"] = NONE  # lifetime: any move clears the target
    frm, to = move["from"], move["to"]
    tok = state["occupied"].get(frm)
    assert tok is not None, f"apply: no piece on {frm} - fixture bug"
    new_occ = {sq: v for sq, v in state["occupied"].items() if sq != frm}
    new_occ[to] = tok
    out["occupied"] = new_occ
    if t == "pawn-advance" and tok[1] == "p":
        so = SET_ON[SIDE_NAME[side]]
        if (frm[1] == so["from_rank"] and to[1] == so["to_rank"]
                and frm[0] == to[0]):
            out["ep_target"] = to[0] + so["target_rank"]
    return out


def _legal_capture_exists(state: dict) -> bool:
    side = state["side_to_move"]
    target = state["ep_target"]
    if not _grammar_ok(target) or target == NONE or not _rank_ok(target, side):
        return False
    m = MOVER[SIDE_NAME[side]]
    tf = ord(target[0]) - 97
    for df in (-1, 1):
        f = tf + df
        if 0 <= f <= 7:
            frm = chr(97 + f) + m["mover_rank"]
            try:
                _apply_capture(state, {"type": "ep-capture", "from": frm,
                                       "to": target})
                return True
            except EnPassantFailure:
                continue
    return False


def _identity_value(state: dict) -> str:
    """Storage-vs-identity split: the canonical identity value is the
    target only when at least one legal capture exists against it."""
    assert IDENTITY_PARTICIPATES == "always"
    stored = state["ep_target"]
    if stored != NONE and _legal_capture_exists(state):
        return stored
    return NONE


def _expected_turn(move_type: str, side: str) -> dict:
    pawnish = move_type in ("pawn-advance", "ep-capture")
    reset = TURN["halfmove_clock"] == "reset"
    return {
        "halfmove_clock": ("reset" if (pawnish and reset) else "increment"),
        "fullmove_number": (
            "increment" if (TURN["fullmove_number"] == "increment-when-black"
                            and side == "b") else "same"),
    }


def _strict(case: dict, required: set, where: str) -> None:
    assert type(case) is dict
    unknown = set(case) - required
    assert not unknown, (
        f"{where} case {case.get('name')!r}: unknown keys {sorted(unknown)}")
    missing = required - set(case)
    assert not missing, (
        f"{where} case {case.get('name')!r}: missing keys {sorted(missing)}")


def _check_name(case: dict, where: str) -> None:
    name = case["name"]
    assert type(name) is str and name.strip(), (
        f"{where}: name must be a nonempty exact string")


def _check_case_common(case: dict, where: str) -> None:
    _check_name(case, where)
    kind = case.get("kind")
    assert kind in KIND_KEYS, f"{where} case {case.get('name')!r}: bad kind {kind!r}"
    if where == "malformed":
        _strict(case, MALFORMED_REQUIRED, where)
    elif where == "rollback":
        _strict(case, ROLLBACK_REQUIRED, where)
    else:
        _strict(case, KIND_KEYS[kind], where)
    _validate_state(case["state"], f"{where} case {case['name']!r}")
    if kind != "identity":
        allowed = MOVE_DOMAIN[kind]
        if where in ("malformed", "rollback"):
            allowed = {"ep-capture"}
        t = case["move"].get("type")
        assert t in allowed, (
            f"{where} case {case['name']!r}: move type {t!r} outside"
            f" declared domain {sorted(allowed)}")
        _validate_move(case["move"], f"{where} case {case['name']!r}")
        et = case.get("expect_turn")
        if et is not None:
            assert type(et) is dict and set(et) == {"halfmove_clock",
                                                    "fullmove_number"}, (
                case["name"])
            assert all(type(v) is str for v in et.values()), case["name"]
        eo = case.get("expect_occupied")
        if eo is not None:
            _validate_state({**case["state"], "occupied": eo},
                            f"{where} case {case['name']!r} expect_occupied")
        xt = case.get("expect_target")
        if xt is not None:
            assert type(xt) is str and _grammar_ok(xt), (
                f"{where} case {case['name']!r}: expect_target {xt!r}"
                " not canonical")


def test_contract_premises():
    """Fixture assumptions are pinned against the contract itself."""
    assert DOC["schema_version"] == 1
    assert CASES["contract_schema_version"] == DOC["schema_version"]
    assert GRAMMAR["empty"] == "forbidden"
    assert SET_ON["event"] == "pawn-two-square-advance"
    assert SET_ON["target_file"] == "advancing-pawn-file"
    assert LIFETIME["duration"] == "exactly-one-ply"
    assert LIFETIME["cleared_by"] == "any-move"
    assert SVI["storage"] == "recorded-on-every-two-square-advance"
    assert SVI["identity_value"] == "target-when-legal-capture-else-none"
    assert MOVER["file_relation"] == "adjacent-to-target-file"
    assert TURN["transitions"] == "exactly-one"
    assert TURN["halfmove_clock"] == "reset"
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASS_COUNTS)
    for cls, m in FAILURE_MAPPING.items():
        assert m["error"] in ERROR_ENUM, cls


def test_fixture_shape():
    assert set(CASES) == TOP_KEYS
    assert type(CASES["schema"]) is int and CASES["schema"] == 1
    assert type(CASES["notes"]) is str and CASES["notes"].strip()
    assert CASES["contract"] == "data/contracts/en_passant.yaml"
    assert type(CASES["contract_schema_version"]) is int
    names = [c["name"] for s in SECTION_COUNTS for c in CASES[s]]
    assert len(names) == len(set(names)), "case names must be globally unique"
    for case in CASES["happy"]:
        assert case.get("kind") in HAPPY_KINDS, case.get("name")
        _check_case_common(case, "happy")
        assert _grammar_ok(case["state"]["ep_target"]), case["name"]
    for case in CASES["boundary"]:
        assert case.get("kind") in BOUNDARY_KINDS, case.get("name")
        _check_case_common(case, "boundary")
        assert _grammar_ok(case["state"]["ep_target"]), case["name"]
    for case in CASES["malformed"]:
        assert case.get("kind") == "ep-capture", case.get("name")
        _check_case_common(case, "malformed")
        ef = case["expect_failure"]
        assert type(ef) is str and ef in FAILURE_MAPPING, case["name"]
        defect = case["defect"]
        assert type(defect) is str and defect.strip(), case["name"]
    for case in CASES["rollback"]:
        assert case.get("kind") == "ep-capture", case.get("name")
        _check_case_common(case, "rollback")
        ef = case["expect_failure"]
        assert type(ef) is str and ef in FAILURE_MAPPING, case["name"]
        _validate_state(case["expect_state_after"],
                        f"rollback case {case['name']!r} expect_state_after")
    for section in SECTION_COUNTS:
        assert CASES[section], f"{section} section empty"


def test_count_pins():
    for section, n in SECTION_COUNTS.items():
        assert len(CASES[section]) == n, (
            f"{section}: {len(CASES[section])} cases, pinned {n}")
    hk = {}
    for c in CASES["happy"]:
        hk[c["kind"]] = hk.get(c["kind"], 0) + 1
    assert hk == HAPPY_KIND_COUNTS, hk
    bk = {}
    for c in CASES["boundary"]:
        bk[c["kind"]] = bk.get(c["kind"], 0) + 1
    assert bk == BOUNDARY_KIND_COUNTS, bk
    fc = {}
    for c in CASES["malformed"]:
        fc[c["expect_failure"]] = fc.get(c["expect_failure"], 0) + 1
    assert fc == FAILURE_CLASS_COUNTS, fc


def test_happy():
    for case in CASES["happy"]:
        out = _apply(case["state"], case["move"])
        side = case["state"]["side_to_move"]
        assert out["ep_target"] == case["expect_target"], case["name"]
        assert out["occupied"] == case["expect_occupied"], case["name"]
        assert out["side_to_move"] == OTHER[side], case["name"]
        assert _expected_turn(case["move"]["type"], side) == case["expect_turn"], (
            case["name"])


def test_boundary():
    for case in CASES["boundary"]:
        if case["kind"] == "identity":
            assert _identity_value(case["state"]) == case["expect_identity_value"], (
                case["name"])
            assert case["state"]["ep_target"] == case["expect_stored_target"], (
                case["name"])
        else:
            out = _apply(case["state"], case["move"])
            side = case["state"]["side_to_move"]
            assert out["ep_target"] == case["expect_target"], case["name"]
            assert out["occupied"] == case["expect_occupied"], case["name"]
            assert _expected_turn(case["move"]["type"], side) == case["expect_turn"], (
                case["name"])


def test_malformed():
    for case in CASES["malformed"]:
        try:
            _apply(case["state"], case["move"])
        except EnPassantFailure as e:
            assert e.failure_class == case["expect_failure"], (
                f"{case['name']}: {e.failure_class} != {case['expect_failure']}")
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
        if case["name"] == "target-not-canonical":
            state["ep_target"] = state["ep_target"].lower()
        elif case["name"] == "target-wrong-rank-white":
            state["ep_target"] = "e6"
            state["occupied"] = {"e5": "bp", "d5": "wp", "e1": "wk",
                                 "e8": "bk"}
            move = {"type": "ep-capture", "from": "d5", "to": "e6"}
        elif case["name"] == "no-enemy-pawn-on-captured-square":
            state["occupied"]["e5"] = "bp"
        elif case["name"] == "no-adjacent-mover":
            state["occupied"]["d5"] = state["occupied"].pop("c5")
            move["from"] = "d5"
        elif case["name"] == "stale-target":
            state["ep_target"] = "e6"
        elif case["name"] == "pinned-capture-horizontal":
            state["occupied"]["h5"] = state["occupied"].pop("a5")
        elif case["name"] == "mover-wrong-rank":
            state["occupied"]["d5"] = state["occupied"].pop("d4")
            move["from"] = "d5"
        else:
            raise AssertionError(f"no repair rule for {case['name']}")
        try:
            _apply(state, move)
        except EnPassantFailure as e:
            raise AssertionError(
                f"{case['name']}: repairing only the declared defect"
                f" still fails with {e.failure_class}") from e
        assert fc in FAILURE_MAPPING


def test_rollback():
    for case in CASES["rollback"]:
        pre = copy.deepcopy(case["state"])
        try:
            _apply(case["state"], case["move"])
        except EnPassantFailure as e:
            assert e.failure_class == case["expect_failure"], case["name"]
        else:
            raise AssertionError(f"{case['name']}: defect did not fire")
        assert case["state"] == pre, f"{case['name']}: input mutated"
        assert case["state"] == case["expect_state_after"], case["name"]
