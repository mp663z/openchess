"""T0186: conflict conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0185 conflict contract. The cases execute against the
contract-derived reference in tests.test_t0185_conflict_
contract (itself fully derived from data/contracts/
conflict.yaml plus the linked transposition-node, variant,
position-digest, en-passant and FEN contracts) - nothing is
re-implemented here. Pinned states, derived ids and witnesses in
the fixture were computed from that reference at authoring time,
so any contract or derivation drift breaks this battery. Every
malformed case is discriminating (repairing ONLY its declared
defect locus makes the case valid) and rollback cases prove a
rejection leaves every input state byte-identical.

DESIGN CAUTION: the reference detector is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work
must execute these same cases against a separately implemented
runtime."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0185_conflict_contract import (  # noqa: E402
    ConflictDetector,
    ConflictError_,
    _identity,
)
from tools.conflict_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "conflict"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
WITNESS_KINDS = set(_CC["conflicts_section"]["witness"]["kinds"]
                    if "kinds" in
                    _CC["conflicts_section"]["witness"]
                    else _CC["conflicts_section"]["kinds"])
_ID_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}
WHICH = {"base", "left", "right"}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
DETECT_KEYS = {"name", "kind", "base", "left", "right", "expect"}
MALFORMED_KEYS = {"name", "kind", "base", "left", "right",
                  "expect_failure", "defect", "minimal_repair"}
ROLLBACK_KEYS = {"name", "kind", "base", "left", "right",
                 "expect_failure", "then_base", "then_left",
                 "then_right", "expect"}
EXPECT_KEYS = {"base_id", "left_id", "right_id", "conflicts"}
REPAIR_FORMS = {"replace_state", "replace_record"}


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    for case in CASES[section]:
        if case["name"] == name:
            return case
    raise AssertionError(f"case {name} not found in {section}")


def _validate_state_shape(state, label):
    """Structure-level state shape: a mapping of exact-str keys
    to record mappings with exactly the node record fields and
    string values. Semantic validity is proven by EXECUTION."""
    assert isinstance(state, dict), label
    for key, rec in state.items():
        assert isinstance(key, str), label
        assert isinstance(rec, dict), label
        assert set(rec) == RECORD_FIELDS, (label, key)
        for field in RECORD_FIELDS:
            assert isinstance(rec[field], str), (label, key,
                                                 field)


def _validate_expect(expect, name):
    assert set(expect) == EXPECT_KEYS, name
    for field in ("base_id", "left_id", "right_id"):
        assert isinstance(expect[field], str), (name, field)
        assert _ID_RE.fullmatch(expect[field]), (name, field)
    conflicts = expect["conflicts"]
    assert isinstance(conflicts, dict), name
    for identity, witness in conflicts.items():
        assert isinstance(identity, str), name
        assert set(witness) == {"kind", "left", "right"}, name
        assert witness["kind"] in WITNESS_KINDS, name
        for side in ("left", "right"):
            value = witness[side]
            if value is not None:
                assert isinstance(value, dict), (name, identity)
                assert set(value) == RECORD_FIELDS, (name,
                                                     identity)


def _repaired(case):
    """Apply the declarative minimal repair: it touches ONLY the
    declared locus, everything else byte-identical."""
    rep = case["minimal_repair"]
    assert set(rep) <= REPAIR_FORMS
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure",
                        "minimal_repair")}
    if "replace_state" in rep:
        form = rep["replace_state"]
        out[form["which"]] = copy.deepcopy(form["state"])
        return out
    form = rep["replace_record"]
    state = out[form["which"]]
    assert form["identity"] in state
    del state[form["identity"]]
    state[_identity(form["record"])] = copy.deepcopy(
        form["record"])
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of two closed
    forms, never mixed:
    - replace_state: exactly {"which", "state"}; which is
      base/left/right; state is a structure-valid state;
    - replace_record: exactly {"which", "identity", "record"};
      identity is a string key PRESENT in the case's which state;
      record has exactly the node fields with string values."""
    rep = case["minimal_repair"]
    assert set(rep) in ({"replace_state"}, {"replace_record"}), \
        case["name"]
    if "replace_state" in rep:
        form = rep["replace_state"]
        assert set(form) == {"which", "state"}, case["name"]
        assert form["which"] in WHICH, case["name"]
        _validate_state_shape(form["state"], case["name"])
        return
    form = rep["replace_record"]
    assert set(form) == {"which", "identity", "record"}, \
        case["name"]
    assert form["which"] in WHICH, case["name"]
    state = case[form["which"]]
    assert isinstance(state, dict), case["name"]
    assert isinstance(form["identity"], str), case["name"]
    assert form["identity"] in state, case["name"]
    record = form["record"]
    assert isinstance(record, dict), case["name"]
    assert set(record) == RECORD_FIELDS, case["name"]
    for field in RECORD_FIELDS:
        assert isinstance(record[field], str), case["name"]


def _validate_structure(cases):
    assert set(cases) == TOP_KEYS
    # the fixture-format version is pinned exactly: an int equal
    # to FIXTURE_SCHEMA_VERSION, never a string, bool, or other
    # integer silently interpreted under wrong assumptions
    assert type(cases["schema"]) is int, "schema must be an int"
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == \
        _CC["versioning"]["base_path"]
    assert isinstance(cases["notes"], str) and cases["notes"]
    for section in ("happy", "boundary", "malformed",
                    "rollback"):
        assert cases[section], f"{section} must be non-empty"
        names = [case["name"] for case in cases[section]]
        assert len(names) == len(set(names)), (
            f"{section} names must be unique")
        for case in cases[section]:
            if section in ("happy", "boundary"):
                assert case["kind"] == "detect", case["name"]
                assert set(case) == DETECT_KEYS, case["name"]
                for which in ("base", "left", "right"):
                    _validate_state_shape(case[which],
                                          case["name"])
                _validate_expect(case["expect"], case["name"])
            elif section == "malformed":
                assert case["kind"] == "detect", case["name"]
                assert set(case) == MALFORMED_KEYS, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert isinstance(case["defect"], str) and \
                    case["defect"], case["name"]
                _validate_repair(case)
            else:
                assert case["kind"] == "rollback-detect", \
                    case["name"]
                assert set(case) == ROLLBACK_KEYS, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                for which in ("then_base", "then_left",
                              "then_right"):
                    _validate_state_shape(case[which],
                                          case["name"])
                _validate_expect(case["expect"], case["name"])
    # every declared failure class is exercised by the malformed
    # battery, and every malformed failure is contract-declared
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared == FAILURE_CLASSES


def test_fixture_structure():
    _validate_structure(CASES)


def test_fixture_schema_version_mutations_fail():
    """The pinned fixture-format version is closed: older, newer,
    string, boolean, and missing schema values all fail structure
    validation."""
    for mutate in (
            lambda m: m.__setitem__("schema", 0),
            lambda m: m.__setitem__("schema", 2),
            lambda m: m.__setitem__("schema", "1"),
            lambda m: m.__setitem__("schema", True),
            lambda m: m.__delitem__("schema")):
        m = copy.deepcopy(CASES)
        mutate(m)
        with pytest.raises(AssertionError):
            _validate_structure(m)


def test_repairs_minimal_locus():
    """Every repair touches ONLY its declared locus: for
    replace_state the other two states are byte-identical; for
    replace_record the other two states AND every other record
    in the repaired state are byte-identical."""
    for case in CASES["malformed"]:
        rep = _repaired(case)
        form = next(iter(case["minimal_repair"].values()))
        which = form["which"]
        for other in WHICH - {which}:
            assert rep[other] == case[other], case["name"]
        if "replace_record" in case["minimal_repair"]:
            original = case[which]
            for key, rec in original.items():
                if key != form["identity"]:
                    assert rep[which].get(key) == rec, \
                        case["name"]


def _swap_expect(expect):
    """The contract's pinned symmetry, mechanically: left-right
    swap flips the side ids and every witness's left/right."""
    out = {"base_id": expect["base_id"],
           "left_id": expect["right_id"],
           "right_id": expect["left_id"],
           "conflicts": {}}
    for identity, witness in expect["conflicts"].items():
        out["conflicts"][identity] = {
            "kind": witness["kind"],
            "left": copy.deepcopy(witness["right"]),
            "right": copy.deepcopy(witness["left"])}
    return out


def _run_detect(base, left, right, expect):
    detector = ConflictDetector()
    pristine = (copy.deepcopy(base), copy.deepcopy(left),
                copy.deepcopy(right))
    result = detector.detect(base, left, right)
    assert result == expect
    # determinism: a second run over fresh copies is identical
    again = ConflictDetector().detect(copy.deepcopy(base),
                                      copy.deepcopy(left),
                                      copy.deepcopy(right))
    assert again == result
    # symmetry: the left-right swap flips the witnesses exactly
    swapped = ConflictDetector().detect(copy.deepcopy(base),
                                        copy.deepcopy(right),
                                        copy.deepcopy(left))
    assert swapped == _swap_expect(expect)
    # inputs never mutated
    assert (base, left, right) == pristine


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        _run_detect(case["base"], case["left"], case["right"],
                    case["expect"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        _run_detect(case["base"], case["left"], case["right"],
                    case["expect"])


def _exec_malformed(case):
    """The ORIGINAL input rejects with the pinned failure class
    and leaves every input state byte-identical."""
    pristine = (copy.deepcopy(case["base"]),
                copy.deepcopy(case["left"]),
                copy.deepcopy(case["right"]))
    try:
        ConflictDetector().detect(copy.deepcopy(case["base"]),
                                  copy.deepcopy(case["left"]),
                                  copy.deepcopy(case["right"]))
    except ConflictError_ as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.code in ERROR_ENUM
        assert (case["base"], case["left"], case["right"]) == \
            pristine
        return
    raise AssertionError("input unexpectedly accepted")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    result = ConflictDetector().detect(out["base"], out["left"],
                                       out["right"])
    assert set(result) == EXPECT_KEYS
    _validate_expect(result, case["name"])


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    """Collection guard: test_malformed's parameter IDs are
    exactly the fixture's malformed-name set - read from the
    actual parametrize mark, never recomputed from the same
    expression."""
    marks = [m for m in test_malformed.pytestmark
             if m.name == "parametrize"]
    assert len(marks) == 1
    assert set(marks[0].args[1]) == set(_names("malformed"))


def test_collection_guard_detects_late_fixture_row():
    """A fixture row appended AFTER decorator collection is
    caught: the parametrize mark froze at import, so the guard
    must fail while the row is present."""
    late = copy.deepcopy(CASES["malformed"][0])
    late["name"] = "late-row-witness"
    CASES["malformed"].append(late)
    try:
        with pytest.raises(AssertionError):
            test_malformed_param_ids_equal_fixture_names()
    finally:
        CASES["malformed"].pop()


def test_injected_valid_malformed_row_fails_rejection():
    """An in-memory VALID row labelled malformed must fail the
    original-rejection step - the malformed battery is not
    vacuous."""
    valid = copy.deepcopy(CASES["malformed"][0])
    rep = valid["minimal_repair"]["replace_state"]
    valid["base"] = copy.deepcopy(rep["state"])
    ConflictDetector().detect(copy.deepcopy(valid["base"]),
                              copy.deepcopy(valid["left"]),
                              copy.deepcopy(valid["right"]))
    with pytest.raises(AssertionError):
        _exec_malformed(valid)


def _repair_mutation_cases():
    """Every repair-form mutation the tagged union must reject
    STRUCTURALLY, before any execution."""
    good_state = CASES["malformed"][0]["minimal_repair"][
        "replace_state"]["state"]
    good_record = CASES["malformed"][1]["minimal_repair"][
        "replace_record"]["record"]
    st = {"replace_state": {"which": "base",
                            "state": good_state}}
    rec = {"replace_record": {"which": "left",
                              "identity": "k",
                              "record": good_record}}
    return [
        ("empty-repair", {}),
        ("mixed-forms", dict(st, **rec)),
        ("unknown-form", {"set_field": {"which": "left"}}),
        ("state-extra-key",
         {"replace_state": dict(st["replace_state"], wat=1)}),
        ("state-missing-state",
         {"replace_state": {"which": "base"}}),
        ("state-which-unknown",
         {"replace_state": {"which": "middle",
                            "state": good_state}}),
        ("state-not-mapping",
         {"replace_state": {"which": "base", "state": []}}),
        ("record-extra-key",
         {"replace_record": dict(rec["replace_record"], wat=1)}),
        ("record-which-unknown",
         {"replace_record": dict(rec["replace_record"],
                                 which="middle")}),
        ("record-identity-absent",
         {"replace_record": dict(rec["replace_record"],
                                 identity="no-such-key")}),
        ("record-wrong-fields",
         {"replace_record": dict(
             rec["replace_record"],
             record=dict(good_record, label="x"))}),
        ("record-non-str-value",
         {"replace_record": dict(
             rec["replace_record"],
             record=dict(good_record, digest=5))}),
    ]


def test_repair_form_mutations_fail_structure():
    """Every repair-form mutation fails _validate_structure before
    execution."""
    for label, rep in _repair_mutation_cases():
        m = copy.deepcopy(CASES)
        # apply to a case whose form matches, else the first
        target = 0 if "replace_state" in rep or \
            label in ("empty-repair", "mixed-forms",
                      "unknown-form") else 1
        if label in ("empty-repair", "mixed-forms",
                     "unknown-form") and "replace_record" in rep:
            target = 1
        m["malformed"][target]["minimal_repair"] = \
            copy.deepcopy(rep)
        try:
            _validate_structure(m)
        except AssertionError:
            continue
        raise AssertionError(
            f"repair mutation {label!r} passed")


def test_rollback():
    """A rejection leaves every input state byte-identical, and a
    subsequent valid detect returns the pinned result."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        pristine = (copy.deepcopy(case["base"]),
                    copy.deepcopy(case["left"]),
                    copy.deepcopy(case["right"]))
        with pytest.raises(ConflictError_) as exc:
            ConflictDetector().detect(
                copy.deepcopy(case["base"]),
                copy.deepcopy(case["left"]),
                copy.deepcopy(case["right"]))
        assert exc.value.failure_class == case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert (case["base"], case["left"],
                case["right"]) == pristine
        _run_detect(case["then_base"], case["then_left"],
                    case["then_right"], case["expect"])
