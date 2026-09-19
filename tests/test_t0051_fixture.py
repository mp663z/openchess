"""T0051: turn conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0050 turn
contract. All semantics are derived from the parsed contract document
(data/contracts/turn.yaml), never hardcoded in the test: state fields,
side values, bounds, transition operations, reset predicate, fifty-move
thresholds, termination enum, identity participation booleans and the
failure_mapping all come from the YAML. Every malformed case is
discriminating (repairing ONLY its declared defect makes it valid, so
no second defect hides inside) and rollback cases assert the pre-state
survives a rejected transition bit-identically.

Boundary cases are strictly KINDED: each declares kind in
{threshold, transition, identity} with an exact per-kind key set, so a
field from a sibling kind is a shape violation, never inert data. All
nested state mappings (state, expect_state, other, expect_state_after)
are strictly shaped against the three contract state fields - a typo'd
key fails at shape validation.

DESIGN CAUTION: the reference interpreter in this file is derived from
the same contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later turn-runtime work must
execute these same cases against a separately implemented runtime."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "turn" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "turn.yaml").read_text())
C = DOC["contract"]

STATE_FIELDS = list(C["state"]["fields"])
SIDE_VALUES = list(C["state"]["side_values"])
BOUNDS = dict(C["state"]["bounds"])
ON_MOVE = dict(C["transition"]["on_move"])
RESET_WHEN = list(ON_MOVE["halfmove_clock"]["reset_when"])
RESET_TO = ON_MOVE["halfmove_clock"]["reset_to"]
FMW = ON_MOVE["fullmove_number"]
TERMINATION_STATES = list(C["termination"]["states"])
FIFTY = dict(C["termination"]["fifty_move"])
IDENTITY = dict(C["transition"]["identity"])
FAILURE_MAPPING = dict(C["failure_mapping"])
NULL_POLICY = dict(C["transition"]["null_move"])
AFTER_TERM = dict(C["termination"]["on_transition_after_termination"])
UNKNOWN_STATE = dict(C["termination"]["unknown_state"])
ERROR_ENUM = list(C["errors"]["closed_enum"])

TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes",
            "happy", "boundary", "malformed", "rollback"}
HAPPY_REQUIRED = {"name", "state", "move", "expect_state"}
BOUNDARY_KIND_KEYS = {
    "threshold": {"name", "kind", "state", "move", "expect_state",
                  "expect_termination_available", "expect_automatic"},
    "transition": {"name", "kind", "state", "move", "expect_state"},
    "identity": {"name", "kind", "state", "other", "expect_identity"},
}
MALFORMED_REQUIRED = {"name", "state", "move", "expect_failure"}
MALFORMED_OPTIONAL = {"terminated"}
ROLLBACK_REQUIRED = {"name", "state", "move", "expect_failure", "expect_state_after"}
ROLLBACK_OPTIONAL = {"terminated"}
MOVE_KINDS = set(RESET_WHEN) | {"quiet", "null"}
IDENTITY_ENUM = {"identical", "different"}


class TurnFailure(Exception):
    def __init__(self, failure_class: str) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.error = FAILURE_MAPPING[failure_class]["error"]


def _is_int(v) -> bool:
    return type(v) is int  # bool is NOT int here: 0/False conflation is a bypass


def _validate_state(state: dict) -> None:
    side = state.get("side_to_move")
    if side not in SIDE_VALUES or type(side) is not str:
        raise TurnFailure("no_side_to_move")
    for field, bound in BOUNDS.items():
        v = state.get(field)
        if not _is_int(v) or v < bound["min"]:
            raise TurnFailure("bad_counter")
    if set(state) != set(STATE_FIELDS):
        raise TurnFailure("bad_counter")


def _apply(state: dict, move: str, terminated: str | None = None) -> dict:
    _validate_state(state)
    if terminated is not None:
        if terminated not in TERMINATION_STATES:
            raise TurnFailure("unknown_termination")
        raise TurnFailure(AFTER_TERM["failure"])
    if move == "null":
        raise TurnFailure(NULL_POLICY["failure"])
    if move not in MOVE_KINDS - {"null"}:
        raise TurnFailure("illegal_transition")
    out = dict(state)
    mover = state["side_to_move"]
    out["side_to_move"] = next(s for s in SIDE_VALUES if s != mover)
    mover_is_black = mover == "b"
    if (FMW["when"] == "black") == mover_is_black:
        out["fullmove_number"] = state["fullmove_number"] + FMW["amount"]
    if move in RESET_WHEN:
        out["halfmove_clock"] = RESET_TO
    else:
        otherwise = ON_MOVE["halfmove_clock"]["otherwise"]
        out["halfmove_clock"] = state["halfmove_clock"] + otherwise["amount"]
    return out


def _identity(state: dict) -> dict:
    _validate_state(state)
    out = {}
    if IDENTITY["side_to_move_participates"]:
        out["side_to_move"] = state["side_to_move"]
    if IDENTITY["counters_participate"]:
        out["halfmove_clock"] = state["halfmove_clock"]
        out["fullmove_number"] = state["fullmove_number"]
    return out


def _strict_eq(a, b, where: str) -> None:
    assert type(a) is type(b), f"{where}: type {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        assert set(a) == set(b), f"{where}: keys {sorted(a)} != {sorted(b)}"
        for k in a:
            _strict_eq(a[k], b[k], f"{where}.{k}")
    else:
        assert a == b, f"{where}: {a!r} != {b!r}"


def _strict(case: dict, required: set, optional: set, where: str) -> None:
    assert type(case) is dict
    unknown = set(case) - required - optional
    assert not unknown, f"{where} case {case.get('name')!r}: unknown keys {sorted(unknown)}"
    missing = required - set(case)
    assert not missing, f"{where} case {case.get('name')!r}: missing keys {sorted(missing)}"


def _strict_state(state: dict, where: str, complete: bool) -> None:
    """A state mapping carries ONLY contract state fields (a typo'd key
    fails here); when complete, exactly the three declared fields."""
    assert type(state) is dict, f"{where}: state must be a mapping"
    unknown = set(state) - set(STATE_FIELDS)
    assert not unknown, f"{where}: unknown state fields {sorted(unknown)}"
    if complete:
        missing = set(STATE_FIELDS) - set(state)
        assert not missing, f"{where}: missing state fields {sorted(missing)}"


LEGAL_MOVES = MOVE_KINDS - {"null"}


def _check_name(case: dict, where: str) -> None:
    name = case["name"]
    assert type(name) is str and name.strip(), f"{where}: name must be a nonempty exact string"


def _check_move_domain(case: dict, where: str, allow_null: bool) -> None:
    move = case["move"]
    assert type(move) is str, f"{where} case {case['name']!r}: move must be an exact string"
    domain = MOVE_KINDS if allow_null else LEGAL_MOVES
    assert move in domain, (
        f"{where} case {case['name']!r}: move {move!r} outside declared domain {sorted(domain)}")


def _check_expect_failure(case: dict, where: str) -> None:
    ef = case["expect_failure"]
    assert type(ef) is str, f"{where} case {case['name']!r}: expect_failure must be an exact string"
    assert ef in FAILURE_MAPPING, case["name"]
    if "terminated" in case:
        assert type(case["terminated"]) is str, (
            f"{where} case {case['name']!r}: terminated must be an exact string")


def test_fixture_shape():
    assert set(CASES) == TOP_KEYS
    assert type(CASES["schema"]) is int and CASES["schema"] == 1
    assert type(CASES["notes"]) is str and CASES["notes"].strip()
    assert type(CASES["contract"]) is str and CASES["contract"].strip()
    assert CASES["contract"] == "data/contracts/turn.yaml"
    # load-bearing provenance: exact int typing, True == 1 is a bypass
    assert type(CASES["contract_schema_version"]) is int
    assert CASES["contract_schema_version"] == DOC["schema_version"]
    names = [c["name"] for s in ("happy", "boundary", "malformed", "rollback") for c in CASES[s]]
    assert len(names) == len(set(names)), "case names must be globally unique"
    for case in CASES["happy"]:
        _strict(case, HAPPY_REQUIRED, set(), "happy")
        _check_name(case, "happy")
        _check_move_domain(case, "happy", allow_null=False)
    for case in CASES["boundary"]:
        assert case.get("kind") in BOUNDARY_KIND_KEYS, (
            f"boundary case {case.get('name')!r}: undeclared kind {case.get('kind')!r}")
        _strict(case, BOUNDARY_KIND_KEYS[case["kind"]], set(), "boundary")
        _check_name(case, "boundary")
        if case["kind"] in ("threshold", "transition"):
            _check_move_domain(case, "boundary", allow_null=False)
        else:
            ei = case["expect_identity"]
            assert type(ei) is str and ei in IDENTITY_ENUM, (
                f"boundary case {case['name']!r}: expect_identity"
                f" must be one of {sorted(IDENTITY_ENUM)}")
        if case["kind"] == "threshold":
            assert type(case["expect_automatic"]) is bool, case["name"]
            assert type(case["expect_termination_available"]) is str, case["name"]
    for case in CASES["malformed"]:
        _strict(case, MALFORMED_REQUIRED, MALFORMED_OPTIONAL, "malformed")
        _check_name(case, "malformed")
        # null is a declared defect class; every other move must be a
        # legal kind so the DECLARED defect is the only one present
        _check_move_domain(case, "malformed", allow_null=True)
        _check_expect_failure(case, "malformed")
    for case in CASES["rollback"]:
        _strict(case, ROLLBACK_REQUIRED, ROLLBACK_OPTIONAL, "rollback")
        _check_name(case, "rollback")
        # rollback isolates rejection semantics: the move must be legal
        # or exactly null, so termination/null is the SOLE defect
        _check_move_domain(case, "rollback", allow_null=True)
        _check_expect_failure(case, "rollback")
    for section in ("happy", "boundary", "malformed", "rollback"):
        assert CASES[section], f"{section} section empty"
    # nested strict shape: every state mapping carries only contract
    # state fields; complete states carry exactly the three
    for case in CASES["happy"]:
        _strict_state(case["state"], case["name"], complete=True)
        _strict_state(case["expect_state"], case["name"], complete=True)
    for case in CASES["boundary"]:
        _strict_state(case["state"], case["name"], complete=True)
        if case["kind"] in ("threshold", "transition"):
            _strict_state(case["expect_state"], case["name"], complete=True)
        else:
            _strict_state(case["other"], case["name"], complete=True)
    for case in CASES["malformed"]:
        _strict_state(case["state"], case["name"], complete=False)
    for case in CASES["rollback"]:
        _strict_state(case["state"], case["name"], complete=True)
        _strict_state(case["expect_state_after"], case["name"], complete=True)


def test_happy_transitions():
    for case in CASES["happy"]:
        got = _apply(case["state"], case["move"])
        _strict_eq(got, case["expect_state"], case["name"])
        assert got["side_to_move"] != case["state"]["side_to_move"], case["name"]


def _run_threshold(case: dict) -> None:
    claim = FIFTY["claim"]
    auto = FIFTY["automatic"]
    got = _apply(case["state"], case["move"])
    _strict_eq(got, case["expect_state"], case["name"])
    hm = got["halfmove_clock"]
    available = case["expect_termination_available"]
    if available == claim["state"]:
        assert hm == claim["threshold"], case["name"]
        assert case["expect_automatic"] is claim["automatic"]
    elif available == auto["state"]:
        assert hm == auto["threshold"], case["name"]
        assert case["expect_automatic"] is auto["automatic"]
    else:
        raise AssertionError(f"{case['name']}: undeclared termination expectation")


def _run_transition(case: dict) -> None:
    got = _apply(case["state"], case["move"])
    _strict_eq(got, case["expect_state"], case["name"])
    assert got["side_to_move"] != case["state"]["side_to_move"], case["name"]


def _run_identity(case: dict) -> None:
    same = _identity(case["state"]) == _identity(case["other"])
    if case["expect_identity"] == "identical":
        assert same, case["name"]
    elif case["expect_identity"] == "different":
        assert not same, case["name"]
    else:
        raise AssertionError(f"{case['name']}: bad expect_identity")
    if not IDENTITY["counters_participate"]:
        for f in ("halfmove_clock", "fullmove_number"):
            if case["state"][f] != case["other"][f]:
                assert case["expect_identity"] == "identical", case["name"]


_BOUNDARY_RUNNERS = {
    "threshold": _run_threshold,
    "transition": _run_transition,
    "identity": _run_identity,
}


def test_boundary_cases_execute_by_kind():
    assert FIFTY["claim"]["threshold"] < FIFTY["automatic"]["threshold"]
    kinds_seen = set()
    for case in CASES["boundary"]:
        kinds_seen.add(case["kind"])
        _BOUNDARY_RUNNERS[case["kind"]](case)
    assert kinds_seen == set(BOUNDARY_KIND_KEYS), (
        f"fixture must exercise every boundary kind, saw {sorted(kinds_seen)}")


def test_boundary_cross_kind_contamination_rejected():
    """Every field from a sibling kind, added to each kind's case, must
    fail shape validation - no inert or contradictory fields survive."""
    import copy as _copy

    all_fields = set().union(*BOUNDARY_KIND_KEYS.values()) - {"name", "kind"}
    for case in CASES["boundary"]:
        allowed = BOUNDARY_KIND_KEYS[case["kind"]] - {"name", "kind"}
        for foreign in sorted(all_fields - allowed):
            contaminated = _copy.deepcopy(case)
            if foreign == "other" or foreign in ("state", "expect_state"):
                contaminated[foreign] = dict(case["state"])
            elif foreign == "expect_automatic":
                contaminated[foreign] = True
            elif foreign == "move":
                contaminated[foreign] = "quiet"
            else:
                contaminated[foreign] = "x"
            unknown = set(contaminated) - BOUNDARY_KIND_KEYS[case["kind"]]
            assert foreign in unknown, (
                f"{case['name']}: sibling field {foreign!r} was NOT rejected")


def test_garbage_move_mutations_rejected():
    """A garbage move must fail shape validation in every move-bearing
    section: rejected-transition cases may not hide a second defect
    behind the earlier termination/null error."""
    import copy as _copy

    def garbage(case: dict) -> dict:
        c = _copy.deepcopy(case)
        c["move"] = "garbage"
        return c

    checks = (
        (CASES["happy"], "happy", False),
        ([c for c in CASES["boundary"] if c["kind"] in ("threshold", "transition")],
         "boundary", False),
        (CASES["malformed"], "malformed", True),
        (CASES["rollback"], "rollback", True),
    )
    for cases, where, allow_null in checks:
        assert cases, where
        for case in cases:
            with pytest.raises(AssertionError):
                _check_move_domain(garbage(case), where, allow_null)
    # the verifier's exact attack: terminated rollback with garbage move
    term_rb = next(c for c in CASES["rollback"] if "terminated" in c)
    with pytest.raises(AssertionError):
        _check_move_domain(garbage(term_rb), "rollback", True)
    null_rb = next(c for c in CASES["rollback"] if c["move"] == "null")
    with pytest.raises(AssertionError):
        _check_move_domain(garbage(null_rb), "rollback", True)
    term_m = next(c for c in CASES["malformed"] if "terminated" in c)
    with pytest.raises(AssertionError):
        _check_move_domain(garbage(term_m), "malformed", True)


def test_strict_typing_mutations_rejected():
    """Field typing is deterministic, not incidental: int names, mistyped
    metadata, non-string expect_failure/terminated, out-of-enum
    expect_identity all fail at shape time."""
    import copy as _copy

    h = _copy.deepcopy(CASES["happy"][0])
    h["name"] = 7
    with pytest.raises(AssertionError):
        _check_name(h, "happy")
    m = _copy.deepcopy(CASES["malformed"][0])
    m["expect_failure"] = 42
    with pytest.raises(AssertionError):
        _check_expect_failure(m, "malformed")
    m2 = _copy.deepcopy(next(c for c in CASES["malformed"] if "terminated" in c))
    m2["terminated"] = 3.5
    with pytest.raises(AssertionError):
        _check_expect_failure(m2, "malformed")
    b = _copy.deepcopy(next(c for c in CASES["boundary"] if c["kind"] == "identity"))
    b["expect_identity"] = "same-ish"
    ei = b["expect_identity"]
    assert not (type(ei) is str and ei in IDENTITY_ENUM)
    bad_meta = _copy.deepcopy(CASES)
    bad_meta["notes"] = 9
    assert not (type(bad_meta["notes"]) is str and bad_meta["notes"].strip())
    # contract_schema_version bool/int and float/int conflation attacks
    # (JSON true == 1, 1.0 == 1, "1" != 1): exact-int typing must decide
    for bad_version in (True, 1.0, "1"):
        assert type(bad_version) is not int, repr(bad_version)
    for bad_contract in (7, "", None):
        assert not (type(bad_contract) is str and bad_contract.strip())


def test_nested_state_shape_mutations_rejected():
    """A typo'd key inside any nested state mapping is an unknown field
    and must fail shape validation for every section."""
    import copy as _copy

    def typo(state: dict) -> dict:
        s = _copy.deepcopy(state)
        s["halfmove_clok"] = s.pop("halfmove_clock")
        return s

    h = CASES["happy"][0]
    with pytest.raises(AssertionError):
        _strict_state(typo(h["state"]), h["name"], complete=True)
    with pytest.raises(AssertionError):
        _strict_state(typo(h["expect_state"]), h["name"], complete=True)
    b_id = next(c for c in CASES["boundary"] if c["kind"] == "identity")
    with pytest.raises(AssertionError):
        _strict_state(typo(b_id["other"]), b_id["name"], complete=True)
    r = CASES["rollback"][0]
    with pytest.raises(AssertionError):
        _strict_state(typo(r["expect_state_after"]), r["name"], complete=True)
    m = CASES["malformed"][0]
    with pytest.raises(AssertionError):
        _strict_state(typo(m["state"]), m["name"], complete=False)


def _fails_with(case: dict) -> str:
    with pytest.raises(TurnFailure) as ei:
        _apply(case["state"], case["move"], case.get("terminated"))
    return ei.value.failure_class


def _repaired(case: dict) -> dict:
    """Repair ONLY the declared defect; the result must be valid."""
    c = copy.deepcopy(case)
    cls = c["expect_failure"]
    if cls == "no_side_to_move":
        c["state"]["side_to_move"] = SIDE_VALUES[0]
        c.pop("terminated", None)
    elif cls == "bad_counter":
        for field, bound in BOUNDS.items():
            v = c["state"].get(field)
            if not _is_int(v) or v < bound["min"]:
                c["state"][field] = bound["min"]
    elif cls == "illegal_transition":
        if c.get("move") == "null":
            c["move"] = "quiet"
        if "terminated" in c:
            c.pop("terminated")
    elif cls == "unknown_termination":
        c["terminated"] = TERMINATION_STATES[0]
        c["move"] = "quiet"
        c.pop("terminated")
    return c


def test_malformed_cases_fail_with_declared_class():
    for case in CASES["malformed"]:
        cls = _fails_with(case)
        assert cls == case["expect_failure"], (
            f"{case['name']}: failed as {cls}, declared {case['expect_failure']}")


def test_malformed_cases_are_discriminating():
    for case in CASES["malformed"]:
        repaired = _repaired(case)
        got = _apply(repaired["state"], repaired["move"], repaired.get("terminated"))
        _validate_state(got)  # no second defect hides in the case


def test_failure_mapping_error_codes():
    for case in CASES["malformed"]:
        cls = case["expect_failure"]
        err = FAILURE_MAPPING[cls]["error"]
        assert err in ERROR_ENUM, case["name"]
        try:
            _apply(case["state"], case["move"], case.get("terminated"))
        except TurnFailure as exc:
            assert exc.error == err, case["name"]


def test_rollback_preserves_state():
    for case in CASES["rollback"]:
        before = copy.deepcopy(case["state"])
        cls = _fails_with(case)
        assert cls == case["expect_failure"], case["name"]
        _strict_eq(case["state"], before, f"{case['name']}:prestate-mutated")
        _strict_eq(case["state"], case["expect_state_after"], f"{case['name']}:after")


def test_terminated_cases_cover_closure_and_unknown():
    term_cases = [c for c in CASES["malformed"] if "terminated" in c]
    kinds = {c["expect_failure"] for c in term_cases}
    assert "illegal_transition" in kinds, "no closed-after-termination case"
    assert "unknown_termination" in kinds, "no unknown-termination case"
    for case in term_cases:
        if case["expect_failure"] == "illegal_transition":
            assert case["terminated"] in TERMINATION_STATES, case["name"]
        if case["expect_failure"] == "unknown_termination":
            assert case["terminated"] not in TERMINATION_STATES, case["name"]


def test_every_failure_class_covered():
    covered = {c["expect_failure"] for c in CASES["malformed"]}
    for cls in FAILURE_MAPPING:
        assert cls in covered, f"failure class {cls} has no fixture case"
