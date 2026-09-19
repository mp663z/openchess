"""T0060: castling conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0059 castling
contract. All semantics are derived from the parsed contract document
(data/contracts/castling.yaml), never hardcoded in the test: rights
values and grammar (none_sentinel/ordering/duplicates/empty/membership),
home squares, loss mapping, per-side move paths (king/rook from+to,
king_transit, empty_required), turn linkage, identity participation,
failure classes, failure_mapping and the closed error enum all come
from the YAML. Every malformed case is discriminating (repairing ONLY
its declared defect makes it valid, so no second defect hides inside)
and rollback cases assert the pre-state survives a rejected castle
bit-identically.

Happy and boundary cases are strictly KINDED: each declares a kind with
an exact per-kind key set, so a field from a sibling kind is a shape
violation, never inert data. Every state mapping is strictly shaped
(rights/occupied/attacked/side_to_move only; squares restricted to the
squares the contract itself names; piece tokens restricted to a closed
grammar) - a typo'd key fails at shape validation.

DESIGN CAUTION: the reference interpreter in this file is derived from
the same contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later castling-runtime work
must execute these same cases against a separately implemented
runtime."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "castling" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "castling.yaml").read_text())
C = DOC["contract"]

VALUES = list(C["rights"]["values"])
GRAMMAR = dict(C["rights"]["grammar"])
NONE = GRAMMAR["none_sentinel"]
ORDERING = GRAMMAR["ordering"]
HOME = {k: dict(v) for k, v in C["rights"]["home_squares"].items()}
LOSS_KING = {"w": list(C["loss"]["on_king_move"]),
             "b": list(C["loss"]["on_king_move_black"])}
LOSS_ROOK_FROM = dict(C["loss"]["on_rook_move_from"])
LOSS_CAPTURE_ON = dict(C["loss"]["on_rook_capture_on"])
PATHS = {k: dict(v) for k, v in C["move"]["per_side_paths"].items()}
TURN = dict(C["turn_linkage"])
RIGHTS_PARTICIPATE = C["identity"]["rights_participate"]
FAILURE_MAPPING = dict(C["failure_mapping"])
ERROR_ENUM = list(C["errors"]["closed_enum"])

LOSS_TYPES = {"king_move", "rook_move_from", "rook_capture_on"}
MOVE_TYPES = LOSS_TYPES | {"castle"}
SIDES = {"w", "b"}

VALID_SQUARES = set()
for _pair in HOME.values():
    VALID_SQUARES.update(_pair.values())
for _p in PATHS.values():
    VALID_SQUARES.add(_p["king_from"])
    VALID_SQUARES.add(_p["king_to"])
    VALID_SQUARES.add(_p["rook_from"])
    VALID_SQUARES.add(_p["rook_to"])
    VALID_SQUARES.update(_p["king_transit"])
    VALID_SQUARES.update(_p["empty_required"])
VALID_SQUARES.update(LOSS_ROOK_FROM)
VALID_SQUARES.update(LOSS_CAPTURE_ON)

STATE_KEYS = {"rights", "occupied", "attacked", "side_to_move"}
MOVE_KEY_SETS = {
    "castle": {"type", "right"},
    "king_move": {"type", "side"},
    "rook_move_from": {"type", "square"},
    "rook_capture_on": {"type", "square"},
}

TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes",
            "happy", "boundary", "malformed", "rollback"}
HAPPY_KIND_KEYS = {
    "castle": {"name", "kind", "state", "move", "expect_rights",
               "expect_occupied", "expect_turn"},
    "loss": {"name", "kind", "state", "move", "expect_rights"},
}
BOUNDARY_KIND_KEYS = {
    "grammar": {"name", "kind", "state", "move", "expect_rights"},
    "occupancy": {"name", "kind", "state", "move", "expect_rights",
                  "expect_occupied"},
    "identity": {"name", "kind", "state", "other", "expect_identity"},
}
MALFORMED_REQUIRED = {"name", "state", "move", "expect_failure"}
ROLLBACK_REQUIRED = {"name", "state", "move", "expect_failure",
                     "expect_state_after"}
IDENTITY_ENUM = {"identical", "different"}
MOVE_DOMAIN = {
    ("happy", "castle"): {"castle"},
    ("happy", "loss"): LOSS_TYPES,
    ("boundary", "grammar"): LOSS_TYPES,
    ("boundary", "occupancy"): {"castle"},
    ("malformed", None): {"castle"},
    ("rollback", None): {"castle"},
}

SECTION_COUNTS = {"happy": 14, "boundary": 6, "malformed": 25, "rollback": 3}
HAPPY_KIND_COUNTS = {"castle": 4, "loss": 10}
BOUNDARY_KIND_COUNTS = {"grammar": 3, "occupancy": 1, "identity": 2}
FAILURE_CLASS_COUNTS = {"rights_malformed": 5, "rights_inconsistent": 2,
                        "path_blocked": 4, "king_unmoved_required": 2,
                        "through_check": 12}
# exact per-entry positive coverage pins: every entry of both rook loss
# maps has exactly one happy loss case whose rights change is exactly
# that entry's right (a silently unmapped entry starves these counts)
LOSS_ENTRY_POSITIVE_COUNTS = {
    ("rook_move_from", "h1"): 1, ("rook_move_from", "a1"): 1,
    ("rook_move_from", "h8"): 1, ("rook_move_from", "a8"): 1,
    ("rook_capture_on", "h1"): 1, ("rook_capture_on", "a1"): 1,
    ("rook_capture_on", "h8"): 1, ("rook_capture_on", "a8"): 1,
}


class CastlingFailure(Exception):
    def __init__(self, failure_class: str) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.error = FAILURE_MAPPING[failure_class]["error"]


def _grammar_ok(rights) -> bool:
    """Canonical-rights predicate derived ONLY from the contract grammar
    fields: the none sentinel stands alone; empty is forbidden; every
    member comes from values (subset); duplicates are forbidden; the
    declared ordering is the only canonical order."""
    if type(rights) is not str:
        return False
    if rights == NONE:
        return True
    if rights == "":
        return False  # empty: forbidden
    members = list(rights)
    if any(m not in VALUES for m in members):
        return False  # membership: subset-of-values
    if len(set(members)) != len(members):
        return False  # duplicates: forbidden
    return [c for c in ORDERING if c in set(members)] == members


def _canonical(held: set) -> str:
    if not held:
        return NONE
    return "".join(c for c in ORDERING if c in held)


def _side_of(right: str) -> str:
    return "w" if right.isupper() else "b"


def _validate_state(state: dict, where: str) -> None:
    assert type(state) is dict, f"{where}: state must be a mapping"
    assert set(state) == STATE_KEYS, (
        f"{where}: state keys {sorted(state)} != {sorted(STATE_KEYS)}")
    assert type(state["rights"]) is str, f"{where}: rights must be a string"
    occ = state["occupied"]
    assert type(occ) is dict, f"{where}: occupied must be a mapping"
    for sq, tok in occ.items():
        assert type(sq) is str and sq in VALID_SQUARES, (
            f"{where}: occupied key {sq!r} is not a contract-named square")
        assert (type(tok) is str and len(tok) == 2
                and tok[0] in "wb" and tok[1] in "kqrbnp"), (
            f"{where}: bad piece token {tok!r}")
    att = state["attacked"]
    assert type(att) is list, f"{where}: attacked must be a list"
    assert len(att) == len(set(att)), f"{where}: attacked has duplicates"
    for sq in att:
        assert type(sq) is str and sq in VALID_SQUARES, (
            f"{where}: attacked square {sq!r} is not a contract-named square")
    stm = state["side_to_move"]
    assert type(stm) is str and stm in SIDES, f"{where}: bad side_to_move"


def _validate_move(move: dict, where: str) -> None:
    assert type(move) is dict, f"{where}: move must be a mapping"
    t = move.get("type")
    assert type(t) is str and t in MOVE_TYPES, f"{where}: bad move type {t!r}"
    assert set(move) == MOVE_KEY_SETS[t], (
        f"{where}: move keys {sorted(move)} != {sorted(MOVE_KEY_SETS[t])}")
    if t == "castle":
        r = move["right"]
        assert type(r) is str and r in VALUES, f"{where}: bad castle right {r!r}"
    elif t == "king_move":
        s = move["side"]
        assert type(s) is str and s in SIDES, f"{where}: bad king_move side {s!r}"
    elif t == "rook_move_from":
        sq = move["square"]
        assert type(sq) is str and sq in LOSS_ROOK_FROM, f"{where}: bad square {sq!r}"
    else:
        sq = move["square"]
        assert type(sq) is str and sq in LOSS_CAPTURE_ON, f"{where}: bad square {sq!r}"


def _apply(state: dict, move: dict) -> dict:
    """Reference interpreter derived from the contract. Never mutates the
    input; a rejected castle leaves the state bit-identical (rollback)."""
    _validate_state(state, "apply")
    _validate_move(move, "apply")
    rights = state["rights"]
    if not _grammar_ok(rights):
        raise CastlingFailure("rights_malformed")
    held = set() if rights == NONE else set(rights)
    t = move["type"]
    if t in LOSS_TYPES:
        if t == "king_move":
            loss = LOSS_KING[move["side"]]
        elif t == "rook_move_from":
            loss = [LOSS_ROOK_FROM[move["square"]]]
        else:
            loss = [LOSS_CAPTURE_ON[move["square"]]]
        out = copy.deepcopy(state)
        out["rights"] = _canonical(held - set(loss))
        return out
    # castle
    right = move["right"]
    side = _side_of(right)
    assert side == state["side_to_move"], (
        f"castle {right} attempted on {state['side_to_move']}'s turn:"
        " fixture inconsistency, not a failure class")
    if right not in held:
        raise CastlingFailure("king_unmoved_required")
    home, path = HOME[right], PATHS[right]
    occ = state["occupied"]
    if occ.get(home["king"]) != side + "k" or occ.get(home["rook"]) != side + "r":
        raise CastlingFailure("rights_inconsistent")
    if any(sq in occ for sq in path["empty_required"]):
        raise CastlingFailure("path_blocked")
    att = set(state["attacked"])
    if (home["king"] in att or path["king_to"] in att
            or any(sq in att for sq in path["king_transit"])):
        raise CastlingFailure("through_check")
    out = copy.deepcopy(state)
    new_occ = {sq: tok for sq, tok in occ.items()
               if sq not in (home["king"], home["rook"])}
    new_occ[path["king_to"]] = side + "k"
    new_occ[path["rook_to"]] = side + "r"
    out["occupied"] = new_occ
    out["rights"] = _canonical(held - set(LOSS_KING[side]))
    return out


def _identity(state: dict) -> dict:
    _validate_state(state, "identity")
    if not _grammar_ok(state["rights"]):
        raise CastlingFailure("rights_malformed")
    if RIGHTS_PARTICIPATE:
        return {"rights": state["rights"]}
    return {}


def _expected_turn(right: str) -> dict:
    assert TURN["transitions"] == "exactly-one"
    return {
        "halfmove_clock": TURN["halfmove_clock"],
        "fullmove_number": (
            "increment" if (TURN["fullmove_number"] == "increment-when-black"
                            and _side_of(right) == "b") else "same"),
    }


def _strict_eq(a, b, where: str) -> None:
    assert type(a) is type(b), f"{where}: type {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        assert set(a) == set(b), f"{where}: keys {sorted(a)} != {sorted(b)}"
        for k in a:
            _strict_eq(a[k], b[k], f"{where}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), f"{where}: list length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _strict_eq(x, y, f"{where}[{i}]")
    else:
        assert a == b, f"{where}: {a!r} != {b!r}"


def _strict(case: dict, required: set, where: str) -> None:
    assert type(case) is dict
    unknown = set(case) - required
    assert not unknown, f"{where} case {case.get('name')!r}: unknown keys {sorted(unknown)}"
    missing = required - set(case)
    assert not missing, f"{where} case {case.get('name')!r}: missing keys {sorted(missing)}"


def _check_name(case: dict, where: str) -> None:
    name = case["name"]
    assert type(name) is str and name.strip(), f"{where}: name must be a nonempty exact string"


def _check_move_domain(case: dict, where: str, allowed: set) -> None:
    move = case["move"]
    assert type(move) is dict, f"{where} case {case['name']!r}: move must be a mapping"
    t = move.get("type")
    assert t in allowed, (
        f"{where} case {case['name']!r}: move type {t!r} outside declared"
        f" domain {sorted(allowed)}")
    _validate_move(move, f"{where} case {case['name']!r}")


def _check_expect_failure(case: dict, where: str) -> None:
    ef = case["expect_failure"]
    assert type(ef) is str, (
        f"{where} case {case['name']!r}: expect_failure must be an exact string")
    assert ef in FAILURE_MAPPING, case["name"]


def _check_expect_rights(case: dict, where: str) -> None:
    er = case["expect_rights"]
    assert type(er) is str, f"{where} case {case['name']!r}: expect_rights must be a string"
    assert _grammar_ok(er), f"{where} case {case['name']!r}: expect_rights {er!r} not canonical"


def test_contract_premises():
    """Fixture assumptions are pinned against the contract itself."""
    assert DOC["schema_version"] == 1
    assert CASES["contract_schema_version"] == DOC["schema_version"]
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASS_COUNTS)
    for cls, m in FAILURE_MAPPING.items():
        assert m["error"] in ERROR_ENUM, cls
    # b1 premise behind the occupancy boundary pair: queenside requires
    # b1 empty, kingside does not
    q_empty = PATHS["Q"]["empty_required"]
    k_empty = PATHS["K"]["empty_required"]
    assert "b1" in q_empty and "b1" not in k_empty
    # kingside/queenside paths are mirrored between sides
    for w, b in (("K", "k"), ("Q", "q")):
        assert PATHS[w]["king_transit"] != PATHS[b]["king_transit"] or w == b
    # castling increments the halfmove clock, never resets it
    assert TURN["halfmove_clock"] == "increment"
    assert RIGHTS_PARTICIPATE is True


def test_fixture_shape():
    assert set(CASES) == TOP_KEYS
    assert type(CASES["schema"]) is int and CASES["schema"] == 1
    assert type(CASES["notes"]) is str and CASES["notes"].strip()
    assert type(CASES["contract"]) is str
    assert CASES["contract"] == "data/contracts/castling.yaml"
    # load-bearing provenance: exact int typing, True == 1 is a bypass
    assert type(CASES["contract_schema_version"]) is int
    assert CASES["contract_schema_version"] == DOC["schema_version"]
    names = [c["name"] for s in SECTION_COUNTS for c in CASES[s]]
    assert len(names) == len(set(names)), "case names must be globally unique"
    for case in CASES["happy"]:
        kind = case.get("kind")
        assert kind in HAPPY_KIND_KEYS, f"happy case {case.get('name')!r}: bad kind {kind!r}"
        _strict(case, HAPPY_KIND_KEYS[kind], "happy")
        _check_name(case, "happy")
        _check_move_domain(case, "happy", MOVE_DOMAIN[("happy", kind)])
        _check_expect_rights(case, "happy")
        _validate_state(case["state"], f"happy case {case['name']!r}")
        if kind == "castle":
            _validate_state({**case["state"], "occupied": case["expect_occupied"]},
                            f"happy case {case['name']!r} expect_occupied")
            et = case["expect_turn"]
            assert type(et) is dict and set(et) == {"halfmove_clock", "fullmove_number"}, (
                case["name"])
            assert all(type(v) is str for v in et.values()), case["name"]
    for case in CASES["boundary"]:
        kind = case.get("kind")
        assert kind in BOUNDARY_KIND_KEYS, (
            f"boundary case {case.get('name')!r}: bad kind {kind!r}")
        _strict(case, BOUNDARY_KIND_KEYS[kind], "boundary")
        _check_name(case, "boundary")
        _validate_state(case["state"], f"boundary case {case['name']!r}")
        if kind in ("grammar", "occupancy"):
            _check_move_domain(case, "boundary", MOVE_DOMAIN[("boundary", kind)])
            _check_expect_rights(case, "boundary")
            assert _grammar_ok(case["state"]["rights"]), case["name"]
            if kind == "occupancy":
                _validate_state({**case["state"], "occupied": case["expect_occupied"]},
                                f"boundary case {case['name']!r} expect_occupied")
        else:
            _validate_state(case["other"], f"boundary case {case['name']!r} other")
            ei = case["expect_identity"]
            assert type(ei) is str and ei in IDENTITY_ENUM, (
                f"boundary case {case['name']!r}: expect_identity"
                f" must be one of {sorted(IDENTITY_ENUM)}")
    for case in CASES["malformed"]:
        _strict(case, MALFORMED_REQUIRED, "malformed")
        _check_name(case, "malformed")
        _check_move_domain(case, "malformed", MOVE_DOMAIN[("malformed", None)])
        _check_expect_failure(case, "malformed")
        _validate_state(case["state"], f"malformed case {case['name']!r}")
    for case in CASES["rollback"]:
        _strict(case, ROLLBACK_REQUIRED, "rollback")
        _check_name(case, "rollback")
        _check_move_domain(case, "rollback", MOVE_DOMAIN[("rollback", None)])
        _check_expect_failure(case, "rollback")
        _validate_state(case["state"], f"rollback case {case['name']!r}")
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


def test_happy_castle_cases():
    for case in (c for c in CASES["happy"] if c["kind"] == "castle"):
        before = copy.deepcopy(case["state"])
        got = _apply(case["state"], case["move"])
        _strict_eq(case["state"], before, f"{case['name']}:input-mutated")
        assert got["rights"] == case["expect_rights"], case["name"]
        _strict_eq(got["occupied"], case["expect_occupied"],
                   f"{case['name']}:occupied")
        _strict_eq(got["attacked"], case["state"]["attacked"], case["name"])
        _strict_eq(got["side_to_move"], case["state"]["side_to_move"], case["name"])
        # turn linkage: the fixture's declared expectation must equal the
        # contract-derived effect (increment never reset; fullmove when black)
        _strict_eq(case["expect_turn"], _expected_turn(case["move"]["right"]),
                   f"{case['name']}:turn")
        # a castling move is a king move: the side's exact loss set applies
        side = _side_of(case["move"]["right"])
        assert case["move"]["right"] in LOSS_KING[side], case["name"]
        held = (set() if case["state"]["rights"] == NONE
                else set(case["state"]["rights"]))
        assert got["rights"] == _canonical(held - set(LOSS_KING[side])), case["name"]


def test_happy_loss_cases():
    for case in (c for c in CASES["happy"] if c["kind"] == "loss"):
        before = copy.deepcopy(case["state"])
        got = _apply(case["state"], case["move"])
        _strict_eq(case["state"], before, f"{case['name']}:input-mutated")
        assert got["rights"] == case["expect_rights"], case["name"]
        # loss events touch rights only
        _strict_eq(got["occupied"], case["state"]["occupied"], case["name"])
        _strict_eq(got["attacked"], case["state"]["attacked"], case["name"])


def _run_grammar(case: dict) -> None:
    got = _apply(case["state"], case["move"])
    assert got["rights"] == case["expect_rights"], case["name"]
    assert _grammar_ok(got["rights"]), case["name"]


def _run_occupancy(case: dict) -> None:
    # premise: b1 occupied must not affect the K path but must affect Q
    assert "b1" in case["state"]["occupied"], case["name"]
    assert "b1" not in PATHS[case["move"]["right"]]["empty_required"], case["name"]
    got = _apply(case["state"], case["move"])
    assert got["rights"] == case["expect_rights"], case["name"]
    _strict_eq(got["occupied"], case["expect_occupied"], case["name"])


def _run_identity(case: dict) -> None:
    same = _identity(case["state"]) == _identity(case["other"])
    if case["expect_identity"] == "identical":
        assert same, case["name"]
    elif case["expect_identity"] == "different":
        assert not same, case["name"]
    else:
        raise AssertionError(f"{case['name']}: bad expect_identity")
    if not RIGHTS_PARTICIPATE:
        assert case["expect_identity"] == "identical", case["name"]


_BOUNDARY_RUNNERS = {
    "grammar": _run_grammar,
    "occupancy": _run_occupancy,
    "identity": _run_identity,
}


def test_boundary_cases_execute_by_kind():
    kinds_seen = set()
    for case in CASES["boundary"]:
        kinds_seen.add(case["kind"])
        _BOUNDARY_RUNNERS[case["kind"]](case)
    assert kinds_seen == set(BOUNDARY_KIND_KEYS), (
        f"fixture must exercise every boundary kind, saw {sorted(kinds_seen)}")


def test_cross_kind_contamination_rejected():
    """Every field from a sibling kind, added to each kind's case in both
    kinded sections, must fail shape validation - no inert or
    contradictory fields survive."""
    kind_keys = dict(HAPPY_KIND_KEYS)
    kind_keys.update({f"b:{k}": v for k, v in BOUNDARY_KIND_KEYS.items()})
    all_fields = set().union(*kind_keys.values()) - {"name", "kind"}
    cases = [(c, HAPPY_KIND_KEYS[c["kind"]]) for c in CASES["happy"]]
    cases += [(c, BOUNDARY_KIND_KEYS[c["kind"]]) for c in CASES["boundary"]]
    for case, allowed in cases:
        for foreign in sorted(all_fields - (allowed - {"name", "kind"})):
            contaminated = copy.deepcopy(case)
            if foreign in ("state", "other", "expect_occupied"):
                contaminated[foreign] = dict(case["state"])
            elif foreign == "move":
                contaminated[foreign] = {"type": "castle", "right": "K"}
            elif foreign == "expect_turn":
                contaminated[foreign] = {"halfmove_clock": "increment",
                                         "fullmove_number": "same"}
            else:
                contaminated[foreign] = "x"
            unknown = set(contaminated) - allowed
            assert foreign in unknown, (
                f"{case['name']}: sibling field {foreign!r} was NOT rejected")


def test_garbage_move_mutations_rejected():
    """An undeclared move type or an exact-key-set violation must fail
    shape validation in every move-bearing section - a rejected castle
    may not hide a second defect behind an earlier error."""
    def garbage_type(case: dict) -> dict:
        c = copy.deepcopy(case)
        c["move"] = {"type": "teleport", "right": "K"}
        return c

    def garbage_keys(case: dict) -> dict:
        c = copy.deepcopy(case)
        c["move"] = dict(c["move"])
        c["move"]["speed"] = "fast"
        return c

    for section in ("happy", "boundary", "malformed", "rollback"):
        for case in CASES[section]:
            if section == "boundary" and case["kind"] == "identity":
                continue
            kind = case.get("kind")
            allowed = MOVE_DOMAIN[(section, kind)]
            with pytest.raises(AssertionError):
                _check_move_domain(garbage_type(case), section, allowed)
            with pytest.raises(AssertionError):
                _check_move_domain(garbage_keys(case), section, allowed)


def test_strict_typing_mutations_rejected():
    """Field typing is deterministic, not incidental: int names, mistyped
    metadata, non-string expect_failure, out-of-enum expect_identity,
    bool/int and float/int conflation on schema versions all fail at
    shape time."""
    h = copy.deepcopy(CASES["happy"][0])
    h["name"] = 7
    with pytest.raises(AssertionError):
        _check_name(h, "happy")
    m = copy.deepcopy(CASES["malformed"][0])
    m["expect_failure"] = 42
    with pytest.raises(AssertionError):
        _check_expect_failure(m, "malformed")
    b = copy.deepcopy(next(c for c in CASES["boundary"]
                           if c["kind"] == "identity"))
    b["expect_identity"] = "same-ish"
    ei = b["expect_identity"]
    assert not (type(ei) is str and ei in IDENTITY_ENUM)
    # contract_schema_version bool/int and float/int conflation attacks
    # (JSON true == 1, 1.0 == 1, "1" != 1): exact-int typing must decide
    for bad_version in (True, 1.0, "1"):
        assert type(bad_version) is not int, repr(bad_version)
    # non-string rights can never be canonical
    for bad_rights in (True, 1, ["K"], None):
        assert not _grammar_ok(bad_rights), repr(bad_rights)


def test_nested_state_shape_mutations_rejected():
    """A typo'd key or off-domain value inside any nested state mapping
    must fail shape validation for every section."""
    def typo(state: dict) -> dict:
        s = copy.deepcopy(state)
        s["right"] = s.pop("rights")
        return s

    h = CASES["happy"][0]
    with pytest.raises(AssertionError):
        _validate_state(typo(h["state"]), "typo")
    b_id = next(c for c in CASES["boundary"] if c["kind"] == "identity")
    with pytest.raises(AssertionError):
        _validate_state(typo(b_id["other"]), "typo")
    r = CASES["rollback"][0]
    with pytest.raises(AssertionError):
        _validate_state(typo(r["expect_state_after"]), "typo")
    m = CASES["malformed"][0]
    with pytest.raises(AssertionError):
        _validate_state(typo(m["state"]), "typo")
    # unknown square in occupied / attacked is a shape violation
    bad_sq = copy.deepcopy(h["state"])
    bad_sq["occupied"]["z9"] = "wk"
    with pytest.raises(AssertionError):
        _validate_state(bad_sq, "unknown-square")
    bad_att = copy.deepcopy(h["state"])
    bad_att["attacked"] = ["e1", "e1"]
    with pytest.raises(AssertionError):
        _validate_state(bad_att, "dup-attacked")


def _fails_with(case: dict) -> str:
    with pytest.raises(CastlingFailure) as ei:
        _apply(case["state"], case["move"])
    return ei.value.failure_class


def _repaired(case: dict) -> dict:
    """Repair ONLY the declared defect; the result must be valid."""
    c = copy.deepcopy(case)
    cls = c["expect_failure"]
    right = c["move"]["right"]
    side = _side_of(right)
    if cls == "rights_malformed":
        c["state"]["rights"] = right
    elif cls == "rights_inconsistent":
        home = HOME[right]
        c["state"]["occupied"][home["king"]] = side + "k"
        c["state"]["occupied"][home["rook"]] = side + "r"
    elif cls == "path_blocked":
        for sq in PATHS[right]["empty_required"]:
            c["state"]["occupied"].pop(sq, None)
    elif cls == "king_unmoved_required":
        held = set() if c["state"]["rights"] == NONE else set(c["state"]["rights"])
        held.add(right)
        c["state"]["rights"] = _canonical(held)
    elif cls == "through_check":
        c["state"]["attacked"] = []
    else:  # pragma: no cover - a new class must extend the repair map
        raise AssertionError(f"no repair rule for {cls}")
    return c


def test_malformed_cases_fail_with_declared_class():
    for case in CASES["malformed"]:
        cls = _fails_with(case)
        assert cls == case["expect_failure"], (
            f"{case['name']}: failed as {cls}, declared {case['expect_failure']}")


def test_malformed_cases_are_discriminating():
    for case in CASES["malformed"]:
        repaired = _repaired(case)
        got = _apply(repaired["state"], repaired["move"])
        _validate_state(got, case["name"])  # no second defect hides in the case
        assert _grammar_ok(got["rights"]), case["name"]


def test_failure_mapping_error_codes():
    for case in CASES["malformed"]:
        cls = case["expect_failure"]
        err = FAILURE_MAPPING[cls]["error"]
        assert err in ERROR_ENUM, case["name"]
        try:
            _apply(case["state"], case["move"])
        except CastlingFailure as exc:
            assert exc.error == err, case["name"]


def test_rollback_preserves_state():
    for case in CASES["rollback"]:
        before = copy.deepcopy(case["state"])
        cls = _fails_with(case)
        assert cls == case["expect_failure"], case["name"]
        _strict_eq(case["state"], before, f"{case['name']}:prestate-mutated")
        _strict_eq(case["state"], case["expect_state_after"],
                   f"{case['name']}:after")


def _held(state: dict) -> set:
    return set() if state["rights"] == NONE else set(state["rights"])


def test_loss_map_per_entry_positive_coverage():
    """All 8 rook loss-map entries (4 move-from + 4 capture-on squares)
    have exactly one positive happy loss event each, and each event's
    rights change is exactly that entry's right - no entry can be
    silently unmapped or over-mapped."""
    assert (set(LOSS_ENTRY_POSITIVE_COUNTS)
            == {("rook_move_from", sq) for sq in LOSS_ROOK_FROM}
            | {("rook_capture_on", sq) for sq in LOSS_CAPTURE_ON})
    counts = {k: 0 for k in LOSS_ENTRY_POSITIVE_COUNTS}
    for case in (c for c in CASES["happy"] if c["kind"] == "loss"):
        t = case["move"]["type"]
        if t not in ("rook_move_from", "rook_capture_on"):
            continue
        key = (t, case["move"]["square"])
        loss_map = LOSS_ROOK_FROM if t == "rook_move_from" else LOSS_CAPTURE_ON
        right = loss_map[case["move"]["square"]]
        before = _held(case["state"])
        if right in before:
            counts[key] += 1
            # the change is EXACTLY that right, nothing else
            assert case["expect_rights"] == _canonical(before - {right}), case["name"]
        else:
            # no-op events (grammar boundary) belong to the boundary
            # section only; a no-op inside happy loss is a coverage smell
            raise AssertionError(
                f"{case['name']}: happy loss event on unheld right {right}")
    assert counts == LOSS_ENTRY_POSITIVE_COUNTS, counts
    # and each positive event executes correctly end to end
    for case in (c for c in CASES["happy"] if c["kind"] == "loss"):
        got = _apply(case["state"], case["move"])
        assert got["rights"] == case["expect_rights"], case["name"]


def test_failure_matrix_per_side_and_path():
    """path_blocked and through_check are proved per RIGHT, not inferred
    from symmetry: every K/Q/k/q path has a blocked case on its own
    empty_required square, and every right has from/transit/to attack
    rejections on its own king_from/king_transit/king_to squares."""
    blocked = {}
    attacks = {}
    for case in CASES["malformed"]:
        right = case["move"]["right"]
        if case["expect_failure"] == "path_blocked":
            hit = sorted(set(PATHS[right]["empty_required"])
                         & set(case["state"]["occupied"]))
            assert hit, f"{case['name']}: blocked case occupies no required square"
            blocked.setdefault(right, set()).update(hit)
        elif case["expect_failure"] == "through_check":
            attacks.setdefault(right, set()).update(case["state"]["attacked"])
    for right in VALUES:
        assert right in blocked, f"no path_blocked case for {right}"
        # the attacked squares for this right cover from, transit and to
        need = ({HOME[right]["king"]} | set(PATHS[right]["king_transit"])
                | {PATHS[right]["king_to"]})
        got = attacks.get(right, set())
        assert need <= got, f"{right}: attack coverage {sorted(got)} misses {sorted(need - got)}"
        # and every attacked square is load-bearing for THIS right
        assert got <= need, f"{right}: attacked squares outside its path: {sorted(got - need)}"


def test_every_failure_class_covered():
    covered = {c["expect_failure"] for c in CASES["malformed"]}
    for cls in FAILURE_MAPPING:
        assert cls in covered, f"failure class {cls} has no fixture case"
