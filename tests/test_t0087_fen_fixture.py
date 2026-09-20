"""T0087: FEN conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0086 FEN
contract. The cases execute against the contract-derived reference in
tests.test_t0086_fen_contract (itself fully derived from
data/contracts/fen.yaml plus its linked variant, castling and
en-passant contracts) - nothing is re-implemented here. Every case
was traced through that reference at authoring time, so any contract
or derivation drift breaks this battery. Every malformed case is
single-defect by CONSTRUCTION: all non-target contract rules already
pass in the input, each defect is classified by contract validation
layer (grammar layers reject malformed_fen, consistency/position
layers reject impossible_position), and the declarative minimal_repair
(the single repair source) minimally removes only that violation -
generic invariants prove no repair fixes a defect by deleting the
castling or en-passant feature or by removing material. Rollback cases prove
the pure-function surface: a rejection leaves no trace.

DESIGN CAUTION: the reference interpreter is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work must
execute these same cases against a separately implemented runtime."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0086_fen_contract import (  # noqa: E402
    FenError,
    emit_fen,
    parse_fen,
)

# Pinned fixture-format version: consumers validate the loaded
# fixture against exactly this integer before interpreting any
# case under v3 semantics (single minimal_repair source).
FIXTURE_SCHEMA_VERSION = 3
FIXTURE = Path(__file__).parent / "fixtures" / "fen" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "fen.yaml").read_text())
C = DOC["contract"]
FAILURE_CLASSES = set(C["failure_mapping"])

TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes",
            "happy", "boundary", "malformed", "rollback"}
KIND_KEYS = {
    "roundtrip": {"name", "kind", "input_fen", "expect_fen"},
    "fen-parse": {"name", "kind", "input_fen", "defect", "layer",
                  "expect_failure", "minimal_repair"},
    "rollback-fen-parse": {"name", "kind", "reject_fen",
                           "expect_failure", "then_fen", "expect_fen"},
}
REPAIR_KEYS = {"find", "replace"}

# defect layer -> the contract validation layer that must catch it,
# and therefore the failure class that layer raises
GRAMMAR_LAYERS = {"field-count", "placement-grammar",
                  "active-color-grammar", "castling-grammar",
                  "ep-grammar", "counter-grammar"}
SEMANTIC_LAYERS = {"position-rules-kings", "position-rules-pawns",
                   "position-rules-check", "castling-consistency",
                   "ep-consistency"}
LAYER_CLASS = ({layer: "malformed_fen" for layer in GRAMMAR_LAYERS}
               | {layer: "impossible_position"
                  for layer in SEMANTIC_LAYERS})


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    for case in CASES[section]:
        if case["name"] == name:
            return case
    raise AssertionError(f"case {name} not found in {section}")


def _repaired(case):
    rep = case["minimal_repair"]
    assert set(rep) == REPAIR_KEYS
    text = case["input_fen"]
    assert text.count(rep["find"]) == 1, (
        f"{case['name']}: repair find not unique")
    return text.replace(rep["find"], rep["replace"])


def _validate_structure(cases):
    assert set(cases) == TOP_KEYS
    # the fixture-format version is pinned exactly: an int equal to
    # FIXTURE_SCHEMA_VERSION, never a string, bool, or older/newer
    # integer silently interpreted under v3 assumptions
    assert type(cases["schema"]) is int, "schema must be an int"
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == C["id"]
    assert cases["contract_schema_version"] == DOC["schema_version"]
    for section in ("happy", "boundary", "malformed", "rollback"):
        assert cases[section], f"{section} must be non-empty"
        names = [case["name"] for case in cases[section]]
        assert len(names) == len(set(names)), (
            f"{section} names must be unique")
        for case in cases[section]:
            assert set(case) == KIND_KEYS[case["kind"]], case["name"]
    assert {c["kind"] for c in cases["happy"]} == {"roundtrip"}
    assert {c["kind"] for c in cases["boundary"]} == {"roundtrip"}
    assert {c["kind"] for c in cases["malformed"]} == {"fen-parse"}
    assert {c["kind"] for c in cases["rollback"]} == {
        "rollback-fen-parse"}
    # every declared failure class is contract-declared, and every
    # contract failure class is exercised by the malformed battery
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared <= FAILURE_CLASSES
    assert declared == FAILURE_CLASSES
    # both failure classes also appear in the rollback battery
    rb = {c["expect_failure"] for c in cases["rollback"]}
    assert rb == FAILURE_CLASSES
    # every malformed case names a known validation layer, the layer
    # pin maps to the pinned failure class, and both layer families
    # are exercised
    layers = {c["layer"] for c in cases["malformed"]}
    assert layers <= set(LAYER_CLASS)
    for case in cases["malformed"]:
        assert LAYER_CLASS[case["layer"]] == case["expect_failure"], (
            case["name"])
    assert layers & GRAMMAR_LAYERS
    assert layers & SEMANTIC_LAYERS


def test_fixture_structure():
    _validate_structure(CASES)


def test_fixture_schema_version_mutations_fail():
    """The pinned fixture-format version is closed: older, newer,
    string, boolean, and missing schema values all fail structure
    validation (verifier #1 v4)."""
    for mutate in (
            lambda m: m.__setitem__("schema", 2),
            lambda m: m.__setitem__("schema", 4),
            lambda m: m.__setitem__("schema", "3"),
            lambda m: m.__setitem__("schema", True),
            lambda m: m.__delitem__("schema")):
        m = copy.deepcopy(CASES)
        mutate(m)
        with pytest.raises(AssertionError):
            _validate_structure(m)


_ROUNDTRIP_CASES = [(section, case["name"])
                    for section in ("happy", "boundary")
                    for case in CASES[section]]


@pytest.mark.parametrize(
    "section,name", _ROUNDTRIP_CASES,
    ids=[f"{section}/{name}" for section, name in _ROUNDTRIP_CASES])
def test_roundtrip(section, name):
    # parametrization is derived from the fixture itself - a case
    # added to the fixture is executed, never silently skipped
    case = _case(section, name)
    position = parse_fen(C, case["input_fen"])
    assert emit_fen(C, position) == case["expect_fen"]


def test_roundtrip_battery_depth():
    assert len(CASES["happy"]) >= 3
    assert len(CASES["boundary"]) >= 5


def _assert_original_rejects(case):
    """The fixture's original malformed input MUST reject with the
    pinned class and the contract-mapped error code - the evidence
    that the case isolates a real defect at its named layer."""
    with pytest.raises(FenError) as excinfo:
        parse_fen(C, case["input_fen"])
    assert excinfo.value.failure_class == case["expect_failure"]
    # the pinned error code is the contract's own mapping for the
    # pinned class - no re-declared codes in the fixture
    assert excinfo.value.code == (
        C["failure_mapping"][case["expect_failure"]]["error"])


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    # parametrization is derived from the fixture itself - a case
    # added to the fixture MUST prove its original input rejects;
    # it can never pass the generic tests while silently skipping
    # original rejection
    _assert_original_rejects(_case("malformed", name))


def test_malformed_param_ids_equal_fixture_names():
    """Collection guard: test_malformed's parameter IDs are exactly
    the fixture's malformed-name set - a manually repeated list that
    drifts from the fixture fails here."""
    marks = [m for m in test_malformed.pytestmark
             if m.name == "parametrize"]
    assert len(marks) == 1
    assert set(marks[0].args[1]) == set(_names("malformed"))


def test_injected_valid_malformed_row_fails_rejection():
    """Regression mutation: an in-memory 'malformed' row whose input
    is already VALID (with a trivially valid repair) must FAIL the
    original-rejection assertion - a valid row cannot launder into
    the fixture."""
    injected = {"name": "injected-valid", "kind": "fen-parse",
                "input_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "defect": "none - input already valid",
                "layer": "active-color-grammar",
                "expect_failure": "malformed_fen",
                "minimal_repair": {"find": " w ", "replace": " b "}}
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        _assert_original_rejects(injected)


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_repair_is_discriminating(name):
    """Repairing ONLY the declared defect (a single declarative
    find/replace span) makes the case VALID and canonical: the case
    isolates exactly one contract violation, so the failure class is
    attributable to the declared defect alone."""
    case = _case("malformed", name)
    repaired = _repaired(case)
    assert repaired != case["input_fen"]
    position = parse_fen(C, repaired)
    assert emit_fen(C, position) == repaired


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_repair_touches_only_the_declared_span(name):
    """The repair replaces exactly one contiguous span; everything
    outside the span is byte-identical."""
    case = _case("malformed", name)
    rep = case["minimal_repair"]
    text = case["input_fen"]
    idx = text.index(rep["find"])
    repaired = _repaired(case)
    assert repaired[:idx] == text[:idx]
    assert repaired[idx + len(rep["replace"]):] == (
        text[idx + len(rep["find"]):])


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback_leaves_no_trace(name):
    """The parse surface is a pure function (contract.atomicity):
    a rejection produces no partial state, and a subsequent valid
    parse behaves exactly as if the rejection never happened."""
    case = _case("rollback", name)
    with pytest.raises(FenError) as excinfo:
        parse_fen(C, case["reject_fen"])
    assert excinfo.value.failure_class == case["expect_failure"]
    # identical rejection on retry - no state leaked between calls
    with pytest.raises(FenError) as excinfo2:
        parse_fen(C, case["reject_fen"])
    assert excinfo2.value.failure_class == case["expect_failure"]
    position = parse_fen(C, case["then_fen"])
    assert emit_fen(C, position) == case["expect_fen"]


def _pieces(fen):
    return sum(ch.isalpha() for ch in fen.split(" ")[0])


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_repair_never_evasion(name):
    """Anti-evasion invariants: a repair may not fix the defect by
    DELETING the optional feature it broke - castling and en-passant
    feature presence is preserved exactly (sentinel stays sentinel,
    real value stays a real value), and material never decreases
    (nothing is fixed by removing pieces)."""
    case = _case("malformed", name)
    repaired = _repaired(case)
    in_parts = case["input_fen"].split(" ")
    re_parts = repaired.split(" ")
    in_castling = in_parts[2] if len(in_parts) > 2 else "-"
    in_ep = in_parts[3] if len(in_parts) > 3 else "-"
    assert (in_castling != "-") == (re_parts[2] != "-"), case["name"]
    assert (in_ep != "-") == (re_parts[3] != "-"), case["name"]
    assert _pieces(repaired) >= _pieces(case["input_fen"]), (
        case["name"])


def test_battery_size():
    total = sum(len(CASES[s]) for s in
                ("happy", "boundary", "malformed", "rollback"))
    assert total >= 20
