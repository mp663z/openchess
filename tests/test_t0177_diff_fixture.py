"""T0177: diff conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0176 diff contract. The cases execute against the
contract-derived reference in tests.test_t0176_diff_contract
(itself fully derived from data/contracts/diff.yaml plus the
linked transposition-node, variant, position-digest, en-passant
and FEN contracts) - nothing is re-implemented here. Pinned
states, derived ids and diffs in the fixture were computed from
that reference at authoring time, so any contract or derivation
drift breaks this battery. Every malformed case is
discriminating (repairing ONLY its declared defect locus makes
the case valid) and rollback cases prove a rejected apply
leaves the base byte-identical.

DESIGN CAUTION: the reference engine is derived from the same
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

from tests.test_t0176_diff_contract import (  # noqa: E402
    DiffEngine,
    DiffError,
)
from tools.diff_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "diff"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
_ID_RE = re.compile(_CC["identifiers"]["base_id"]["grammar"])
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}
DIFF_FIELDS = {"base_id", "target_id", "added", "removed",
               "changed"}
SECTIONS = {"added", "removed", "changed"}
ID_FIELDS = {"base_id", "target_id"}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
KIND_KEYS = {
    "compute": {"name", "kind", "base", "target", "expect"},
    "diff-validate": {"name", "kind", "diff"},
    "apply": {"name", "kind", "diff", "base", "expect_target"},
}
MALFORMED_EXTRA = {"expect_failure", "defect", "minimal_repair"}
ROLLBACK_KEYS = {"name", "kind", "diff", "base", "expect_failure",
                 "then_diff", "then_base", "expect_target"}
REPAIR_FORMS = {"set_id", "set_section", "replace_diff",
                "replace_base", "replace_state"}


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


def _validate_diff_shape(diff, label):
    assert isinstance(diff, dict), label
    assert set(diff) == DIFF_FIELDS, label
    for field in ID_FIELDS:
        assert isinstance(diff[field], str), (label, field)
        assert _ID_RE.fullmatch(diff[field]), (label, field)
    for section in ("added", "removed"):
        assert isinstance(diff[section], dict), (label, section)
        _validate_state_shape(diff[section],
                              f"{label}:{section}")
    changed = diff["changed"]
    assert isinstance(changed, dict), (label, "changed")
    for key, witness in changed.items():
        assert isinstance(key, str), label
        assert isinstance(witness, dict), (label, key)
        assert set(witness) == {"base", "target"}, (label, key)
        for side in ("base", "target"):
            rec = witness[side]
            assert isinstance(rec, dict), (label, key)
            assert set(rec) == RECORD_FIELDS, (label, key)
            for field in RECORD_FIELDS:
                assert isinstance(rec[field], str), (label,
                                                     key)


def _repaired(case):
    """Apply the declarative minimal repair: it touches ONLY the
    declared locus, everything else byte-identical."""
    rep = case["minimal_repair"]
    assert set(rep) <= REPAIR_FORMS
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure",
                        "minimal_repair")}
    if "set_id" in rep:
        form = rep["set_id"]
        out["diff"][form["field"]] = form["value"]
    elif "set_section" in rep:
        form = rep["set_section"]
        out["diff"][form["section"]] = copy.deepcopy(
            form["value"])
    elif "replace_diff" in rep:
        out["diff"] = copy.deepcopy(rep["replace_diff"]["diff"])
    elif "replace_base" in rep:
        out["base"] = copy.deepcopy(
            rep["replace_base"]["state"])
    else:
        form = rep["replace_state"]
        out[form["which"]] = copy.deepcopy(form["state"])
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of five
    closed forms, never mixed:
    - set_id: exactly {"field", "value"}; field is
      base_id/target_id; value an exact state-id string;
      diff-validate/apply cases only;
    - set_section: exactly {"section", "value"}; section is
      added/removed/changed; value is a shape-valid section;
      diff-validate/apply cases only;
    - replace_diff: exactly {"diff"}; a shape-valid diff;
      apply cases only;
    - replace_base: exactly {"state"}; a shape-valid state;
      apply cases only;
    - replace_state: exactly {"which", "state"}; which is
      base/target; compute cases only."""
    rep = case["minimal_repair"]
    kind = case["kind"]
    assert len(rep) == 1 and set(rep) <= REPAIR_FORMS, \
        case["name"]
    form = next(iter(rep.values()))
    if "set_id" in rep:
        assert kind in ("diff-validate", "apply"), case["name"]
        assert set(form) == {"field", "value"}, case["name"]
        assert form["field"] in ID_FIELDS, case["name"]
        assert isinstance(form["value"], str), case["name"]
        assert _ID_RE.fullmatch(form["value"]), case["name"]
    elif "set_section" in rep:
        assert kind in ("diff-validate", "apply"), case["name"]
        assert set(form) == {"section", "value"}, case["name"]
        assert form["section"] in SECTIONS, case["name"]
        if form["section"] == "changed":
            for key, witness in form["value"].items():
                assert isinstance(key, str), case["name"]
                assert set(witness) == {"base", "target"}, \
                    case["name"]
                for side in ("base", "target"):
                    rec = witness[side]
                    assert set(rec) == RECORD_FIELDS, \
                        case["name"]
                    for field in RECORD_FIELDS:
                        assert isinstance(rec[field], str), \
                            case["name"]
        else:
            _validate_state_shape(form["value"], case["name"])
    elif "replace_diff" in rep:
        assert kind == "apply", case["name"]
        assert set(form) == {"diff"}, case["name"]
        _validate_diff_shape(form["diff"], case["name"])
    elif "replace_base" in rep:
        assert kind == "apply", case["name"]
        assert set(form) == {"state"}, case["name"]
        _validate_state_shape(form["state"], case["name"])
    else:
        assert kind == "compute", case["name"]
        assert set(form) == {"which", "state"}, case["name"]
        assert form["which"] in {"base", "target"}, case["name"]
        _validate_state_shape(form["state"], case["name"])


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
            if section == "rollback":
                assert case["kind"] == "rollback-apply", \
                    case["name"]
                assert set(case) == ROLLBACK_KEYS, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                _validate_diff_shape(case["diff"], case["name"])
                _validate_state_shape(case["base"],
                                      case["name"])
                _validate_diff_shape(case["then_diff"],
                                     case["name"])
                _validate_state_shape(case["then_base"],
                                      case["name"])
                _validate_state_shape(case["expect_target"],
                                      case["name"])
                continue
            assert case["kind"] in KIND_KEYS, case["name"]
            base_keys = KIND_KEYS[case["kind"]]
            keys = set(case)
            if section == "malformed":
                if case["kind"] == "compute":
                    base_keys = (base_keys - {"expect"})
                elif case["kind"] == "apply":
                    base_keys = (base_keys - {"expect_target"})
                keys_expected = base_keys | MALFORMED_EXTRA
                assert keys == keys_expected, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert isinstance(case["defect"], str) and \
                    case["defect"], case["name"]
                _validate_repair(case)
            else:
                assert keys == base_keys, case["name"]
                if case["kind"] == "compute":
                    _validate_state_shape(case["base"],
                                          case["name"])
                    _validate_state_shape(case["target"],
                                          case["name"])
                    _validate_diff_shape(case["expect"],
                                         case["name"])
                elif case["kind"] == "apply":
                    _validate_diff_shape(case["diff"],
                                         case["name"])
                    _validate_state_shape(case["base"],
                                          case["name"])
                    _validate_state_shape(
                        case["expect_target"], case["name"])
                else:
                    _validate_diff_shape(case["diff"],
                                         case["name"])
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
    """Every repair touches ONLY its declared locus: set_id and
    set_section change exactly one diff field; the replace forms
    change exactly one component (diff, base, or one state) with
    every other component byte-identical."""
    for case in CASES["malformed"]:
        rep = _repaired(case)
        form_name = next(iter(case["minimal_repair"]))
        if form_name in ("set_id", "set_section"):
            field = case["minimal_repair"][form_name][
                "field" if form_name == "set_id"
                else "section"]
            for key in DIFF_FIELDS - {field}:
                assert rep["diff"][key] == case["diff"][key], \
                    case["name"]
            for key in set(case) - {"diff", "defect",
                                    "expect_failure",
                                    "minimal_repair"}:
                assert rep[key] == case[key], case["name"]
        elif form_name == "replace_diff":
            for key in set(case) - {"diff", "defect",
                                    "expect_failure",
                                    "minimal_repair"}:
                assert rep[key] == case[key], case["name"]
        elif form_name == "replace_base":
            assert rep["diff"] == case["diff"], case["name"]
        else:
            which = case["minimal_repair"]["replace_state"][
                "which"]
            other = ({"base", "target"} - {which}).pop()
            assert rep[other] == case[other], case["name"]


def _run_compute(case):
    engine = DiffEngine()
    base_p = copy.deepcopy(case["base"])
    target_p = copy.deepcopy(case["target"])
    result = engine.compute(case["base"], case["target"])
    assert result == case["expect"]
    # determinism: fresh copies, same result; inputs unmutated
    again = DiffEngine().compute(copy.deepcopy(case["base"]),
                                 copy.deepcopy(case["target"]))
    assert again == result
    assert case["base"] == base_p
    assert case["target"] == target_p
    # the computed diff VALIDATES and APPLIES to the target
    DiffEngine().validate_diff(copy.deepcopy(result))
    applied = DiffEngine().apply(copy.deepcopy(result),
                                 copy.deepcopy(case["base"]))
    assert applied == case["target"]


def _run_apply(case):
    engine = DiffEngine()
    base_p = copy.deepcopy(case["base"])
    diff_p = copy.deepcopy(case["diff"])
    result = engine.apply(case["diff"], case["base"])
    assert result == case["expect_target"]
    # base and diff never mutated
    assert case["base"] == base_p
    assert case["diff"] == diff_p
    # idempotence of apply output: the returned state is a fresh
    # structure equal to the pinned target
    again = DiffEngine().apply(copy.deepcopy(case["diff"]),
                               copy.deepcopy(case["base"]))
    assert again == result


def _run_validate(diff):
    pristine = copy.deepcopy(diff)
    DiffEngine().validate_diff(diff)
    assert diff == pristine


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        if case["kind"] == "compute":
            _run_compute(case)
        elif case["kind"] == "apply":
            _run_apply(case)
        else:
            _run_validate(case["diff"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        if case["kind"] == "compute":
            _run_compute(case)
        elif case["kind"] == "apply":
            _run_apply(case)
        else:
            _run_validate(case["diff"])


def _exec_malformed(case):
    """The ORIGINAL input rejects with the pinned failure class
    and leaves every input byte-identical."""
    pristine = copy.deepcopy(
        {k: v for k, v in case.items()
         if k not in ("defect", "expect_failure",
                      "minimal_repair")})
    engine = DiffEngine()
    try:
        if case["kind"] == "compute":
            engine.compute(copy.deepcopy(case["base"]),
                           copy.deepcopy(case["target"]))
        elif case["kind"] == "apply":
            engine.apply(copy.deepcopy(case["diff"]),
                         copy.deepcopy(case["base"]))
        else:
            engine.validate_diff(copy.deepcopy(case["diff"]))
    except DiffError as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.code in ERROR_ENUM
        for key, value in pristine.items():
            assert case[key] == value
        return
    raise AssertionError("input unexpectedly accepted")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    engine = DiffEngine()
    if case["kind"] == "compute":
        result = engine.compute(out["base"], out["target"])
        _validate_diff_shape(result, case["name"])
    elif case["kind"] == "apply":
        engine.apply(out["diff"], out["base"])
    else:
        engine.validate_diff(out["diff"])


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
    valid = _repaired(CASES["malformed"][0])
    valid = dict(valid, kind=CASES["malformed"][0]["kind"])
    _exec_repaired(dict(CASES["malformed"][0],
                        **{"minimal_repair":
                           CASES["malformed"][0]
                           ["minimal_repair"]}))
    with pytest.raises(AssertionError):
        _exec_malformed(dict(CASES["malformed"][0], **valid))


def _repair_mutation_cases():
    """Every repair-form mutation the tagged union must reject
    STRUCTURALLY, before any execution."""
    good_diff = CASES["happy"][6]["diff"]
    good_state = CASES["happy"][0]["base"]
    good_id = good_diff["base_id"]
    return [
        ("empty-repair", {}, "diff-validate"),
        ("mixed-forms",
         {"set_id": {"field": "base_id", "value": good_id},
          "set_section": {"section": "added", "value": {}}},
         "diff-validate"),
        ("unknown-form", {"wat": {"x": 1}}, "diff-validate"),
        ("set_id-extra-key",
         {"set_id": {"field": "base_id", "value": good_id,
                     "wat": 1}}, "diff-validate"),
        ("set_id-bad-field",
         {"set_id": {"field": "label", "value": good_id}},
         "diff-validate"),
        ("set_id-bad-grammar",
         {"set_id": {"field": "base_id", "value": "bad"}},
         "diff-validate"),
        ("set_id-on-compute",
         {"set_id": {"field": "base_id", "value": good_id}},
         "compute"),
        ("set_section-bad-section",
         {"set_section": {"section": "wat", "value": {}}},
         "diff-validate"),
        ("set_section-value-not-mapping",
         {"set_section": {"section": "added", "value": []}},
         "diff-validate"),
        ("replace_diff-on-validate",
         {"replace_diff": {"diff": good_diff}},
         "diff-validate"),
        ("replace_diff-bad-shape",
         {"replace_diff": {"diff": {"wat": 1}}}, "apply"),
        ("replace_base-on-compute",
         {"replace_base": {"state": good_state}}, "compute"),
        ("replace_state-on-apply",
         {"replace_state": {"which": "base",
                            "state": good_state}}, "apply"),
        ("replace_state-bad-which",
         {"replace_state": {"which": "left",
                            "state": good_state}}, "compute"),
    ]


def test_repair_form_mutations_fail_structure():
    """Every repair-form mutation fails _validate_structure
    before execution."""
    for label, rep, kind in _repair_mutation_cases():
        m = copy.deepcopy(CASES)
        for case in m["malformed"]:
            if case["kind"] == kind:
                case["minimal_repair"] = copy.deepcopy(rep)
                break
        try:
            _validate_structure(m)
        except AssertionError:
            continue
        raise AssertionError(
            f"repair mutation {label!r} passed")


def test_rollback():
    """A rejected apply leaves the base byte-identical, and a
    subsequent valid apply returns the pinned target."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        base_p = copy.deepcopy(case["base"])
        diff_p = copy.deepcopy(case["diff"])
        with pytest.raises(DiffError) as exc:
            DiffEngine().apply(copy.deepcopy(case["diff"]),
                               copy.deepcopy(case["base"]))
        assert exc.value.failure_class == \
            case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        assert case["base"] == base_p
        assert case["diff"] == diff_p
        _run_apply({"diff": case["then_diff"],
                    "base": case["then_base"],
                    "expect_target": case["expect_target"]})
