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
    _DIGEST_RE,
    DiffEngine,
    DiffError,
    state_id,
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

# -- the closed scenario manifests -------------------------------------------
# Every fixture section's actual {name: metadata} map must equal
# its manifest EXACTLY - no missing, substituted, duplicated,
# renamed, re-kinded or extra rows, and every advertised
# semantic shape is asserted before execution.
MDR = "malformed_diff_record"
CB = "conflicting_base"
UI = "unknown_identity"
DT = "divergent_target"

HAPPY_MANIFEST = {
    "compute-mixed-add-remove-change": ("compute", "mixed"),
    "apply-mixed-round-trip": ("apply", "mixed"),
    "compute-add-only": ("compute", "add-only"),
    "apply-add-only": ("apply", "add-only"),
    "compute-remove-only": ("compute", "remove-only"),
    "apply-remove-only": ("apply", "remove-only"),
    "validate-honest-diff": ("diff-validate", "validate"),
}
BOUNDARY_MANIFEST = {
    "compute-empty-on-equal-states": ("compute", "no-op"),
    "apply-empty-noop": ("apply", "no-op"),
    "compute-change-only": ("compute", "change-only"),
    "compute-remove-all-to-empty": ("compute", "remove-all"),
    "apply-to-empty-base": ("apply", "empty-base"),
}
MALFORMED_MANIFEST = {
    "diff-missing-section": (MDR, "set_section",
                             "missing-section"),
    "diff-bad-id-grammar": (MDR, "set_id", "bad-id-grammar"),
    "diff-section-not-mapping": (MDR, "set_section",
                                 "section-non-mapping"),
    "diff-added-record-bad-digest": (MDR, "set_section",
                                     "bad-linked-digest"),
    "diff-changed-witness-equal-sides": (MDR, "set_section",
                                         "equal-changed-sides"),
    "diff-section-overlap": (MDR, "set_section",
                             "cross-section-overlap"),
    "diff-nonempty-equal-ids": (MDR, "set_id",
                                "nonempty-equal-ids"),
    "diff-empty-unequal-ids": (DT, "set_id",
                               "empty-unequal-ids"),
    "apply-conflicting-base-id": (CB, "replace_base",
                                  "base-id-conflict"),
    "apply-added-identity-already-present": (CB, "replace_diff",
                                             "added-identity-present"),
    "apply-removed-identity-unknown": (UI, "replace_diff",
                                       "removed-identity-absent"),
    "apply-removed-content-mismatch": (CB, "set_section",
                                       "removed-content-mismatch"),
    "apply-divergent-target-id": (DT, "set_id",
                                  "divergent-target-id"),
    "compute-state-not-mapping": (MDR, "replace_state",
                                  "state-non-mapping"),
}
ROLLBACK_MANIFEST = {
    "rejected-conflicting-base-then-valid-apply": CB,
    "rejected-divergent-target-then-valid-apply": DT,
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}


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


def _case_diff(case):
    """The diff a case advertises: expect for compute, diff for
    apply."""
    return case["expect"] if case["kind"] == "compute" \
        else case["diff"]


def _assert_scenario(case, scenario):
    """The advertised semantic shape is REALLY present - a
    scenario tag can never be satisfied by a substituted row."""
    name = case["name"]
    diff = _case_diff(case)
    nonempty = [sec for sec in ("added", "removed", "changed")
                if diff[sec]]
    if scenario == "mixed":
        assert nonempty == ["added", "removed", "changed"], name
    elif scenario == "add-only":
        assert nonempty == ["added"], name
    elif scenario == "remove-only":
        assert nonempty == ["removed"], name
    elif scenario == "change-only":
        assert nonempty == ["changed"], name
    elif scenario == "no-op":
        assert nonempty == [], name
        assert diff["base_id"] == diff["target_id"], name
    elif scenario == "remove-all":
        assert nonempty == ["removed"], name
        assert case["target"] == {}, name
        assert set(diff["removed"]) == set(case["base"]), name
    elif scenario == "empty-base":
        assert case["base"] == {}, name
        assert nonempty == ["added"], name
    elif scenario == "validate":
        assert case["kind"] == "diff-validate", name
    else:  # pragma: no cover - manifest typo guard
        raise AssertionError(f"unknown scenario {scenario!r}")


def _diff_sections(diff):
    return (diff["added"], diff["removed"], diff["changed"])


def _assert_malformed_scenario(case, tag):
    """The case data REALLY realizes the named defect locus -
    a same-tuple substituted row can never satisfy another
    row's scenario."""
    name = case["name"]
    diff = case.get("diff")
    if tag == "missing-section":
        missing = [sec for sec in ("added", "removed", "changed")
                   if sec not in diff]
        assert len(missing) == 1, name
    elif tag == "bad-id-grammar":
        assert _ID_RE.fullmatch(diff["base_id"]) is None or \
            _ID_RE.fullmatch(diff["target_id"]) is None, name
    elif tag == "section-non-mapping":
        assert any(not isinstance(diff[sec], dict)
                   for sec in ("added", "removed", "changed")), \
            name
    elif tag == "bad-linked-digest":
        assert any(_DIGEST_RE.fullmatch(rec["digest"]) is None
                   for rec in diff["added"].values()), name
    elif tag == "equal-changed-sides":
        assert any(wit["base"] == wit["target"]
                   for wit in diff["changed"].values()), name
    elif tag == "cross-section-overlap":
        added, removed, changed = _diff_sections(diff)
        assert (set(added) & set(removed)) or \
            (set(added) & set(changed)) or \
            (set(removed) & set(changed)), name
        # ... and ONLY the overlap is wrong: every record digest
        # is grammar-valid and every changed witness diverges, so
        # a bad-digest or equal-sides row cannot fill this slot
        for sec in (added, removed):
            for rec in sec.values():
                assert _DIGEST_RE.fullmatch(rec["digest"]), name
        for wit in changed.values():
            assert wit["base"] != wit["target"], name
    elif tag == "nonempty-equal-ids":
        added, removed, changed = _diff_sections(diff)
        assert (added or removed or changed), name
        assert diff["base_id"] == diff["target_id"], name
    elif tag == "empty-unequal-ids":
        added, removed, changed = _diff_sections(diff)
        assert not (added or removed or changed), name
        assert diff["base_id"] != diff["target_id"], name
    elif tag == "base-id-conflict":
        assert state_id(case["base"]) != diff["base_id"], name
    elif tag == "added-identity-present":
        assert set(diff["added"]) & set(case["base"]), name
    elif tag == "removed-identity-absent":
        assert set(diff["removed"]) - set(case["base"]), name
    elif tag == "removed-content-mismatch":
        assert any(case["base"][i] != rec
                   for i, rec in diff["removed"].items()
                   if i in case["base"]), name
    elif tag == "divergent-target-id":
        staged = {k: v for k, v in case["base"].items()
                  if k not in diff["removed"]}
        staged.update(diff["added"])
        for i, wit in diff["changed"].items():
            staged[i] = wit["target"]
        assert state_id(staged) != diff["target_id"], name
    elif tag == "state-non-mapping":
        assert not isinstance(case["base"], dict) or \
            not isinstance(case["target"], dict), name
    else:  # pragma: no cover - manifest typo guard
        raise AssertionError(f"unknown scenario {tag!r}")


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
        # CLOSED SCENARIO MANIFEST: the section's actual
        # {name: metadata} map must equal the manifest EXACTLY -
        # a missing, substituted, duplicated, renamed,
        # re-kinded or extra row fails HERE, before execution.
        manifest = MANIFESTS[section]
        assert set(names) == set(manifest), (
            f"{section} scenario set drifted: "
            f"missing={set(manifest) - set(names)} "
            f"extra={set(names) - set(manifest)}")
        for case in cases[section]:
            if section in ("happy", "boundary"):
                kind, scenario = manifest[case["name"]]
                assert case["kind"] == kind, case["name"]
                _assert_scenario(case, scenario)
            elif section == "malformed":
                failure, repair_form, tag = manifest[case["name"]]
                assert case["expect_failure"] == failure, case["name"]
                assert set(case["minimal_repair"]) == {repair_form}, (
                    case["name"])
                # fail closed: a substituted row lacking the
                # scenario's expected data shape is a STRUCTURAL
                # failure, never a raw escape
                try:
                    _assert_malformed_scenario(case, tag)
                except AssertionError:
                    raise
                except Exception as exc:
                    raise AssertionError(
                        f"{case['name']}: scenario {tag!r} not "
                        f"realizable ({exc!r})") from exc
            else:
                assert case["expect_failure"] == manifest[case["name"]], (
                    case["name"])
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


# -- v2: closed scenario coverage ---------------------------------------------


def test_dispatch_sections_match_manifest():
    """Every iterated or parametrized section dispatches EXACTLY
    the manifest's scenario names - a late, replaced or renamed
    row cannot evade or sneak into execution."""
    for section, manifest in MANIFESTS.items():
        assert set(_names(section)) == set(manifest), section


def _section_mutations():
    """Structural mutations over EVERY section - each must fail
    _validate_structure before any execution."""
    out = []
    for section in MANIFESTS:
        # delete a row
        out.append((f"{section}-delete-row", section,
                    lambda m, s=section: m[s].pop(0)))
        # rename a row
        def rename(m, s=section):
            row = copy.deepcopy(m[s][0])
            row["name"] = row["name"] + "-renamed"
            m[s][0] = row
        out.append((f"{section}-rename-row", section, rename))
        # add an extra row
        def add_row(m, s=section):
            row = copy.deepcopy(m[s][0])
            row["name"] = "extra-row-witness"
            m[s].append(row)
        out.append((f"{section}-add-row", section, add_row))
        # substitute: replace a row with a renamed copy of
        # ANOTHER valid row in the same section
        def substitute(m, s=section):
            row = copy.deepcopy(m[s][1])
            row["name"] = m[s][0]["name"]
            m[s][0] = row
        out.append((f"{section}-substitute-row", section,
                    substitute))
        # move a row across sections
        def move(m, s=section):
            other = ({"happy", "boundary", "malformed",
                      "rollback"} - {s}).pop()
            row = copy.deepcopy(m[other][0])
            m[s][0] = row
        out.append((f"{section}-moved-row", section, move))
    # swap kind metadata between two happy rows
    def swap_kind(m):
        m["happy"][0]["kind"], m["happy"][1]["kind"] = \
            m["happy"][1]["kind"], m["happy"][0]["kind"]
    out.append(("happy-swap-kind", "happy", swap_kind))
    # swap failure classes between two malformed rows of
    # DIFFERENT classes
    def swap_failure(m):
        m["malformed"][0]["expect_failure"], \
            m["malformed"][7]["expect_failure"] = \
            m["malformed"][7]["expect_failure"], \
            m["malformed"][0]["expect_failure"]
    out.append(("malformed-swap-failure", "malformed",
                swap_failure))
    # swap repair forms between two malformed rows
    def swap_repair(m):
        m["malformed"][0]["minimal_repair"], \
            m["malformed"][1]["minimal_repair"] = \
            m["malformed"][1]["minimal_repair"], \
            m["malformed"][0]["minimal_repair"]
    out.append(("malformed-swap-repair", "malformed",
                swap_repair))
    # swap rollback rejection classes
    def swap_rollback(m):
        m["rollback"][0]["expect_failure"], \
            m["rollback"][1]["expect_failure"] = \
            m["rollback"][1]["expect_failure"], \
            m["rollback"][0]["expect_failure"]
    out.append(("rollback-swap-failure", "rollback",
                swap_rollback))
    return out


def test_section_mutations_fail_structure():
    for label, _section, mutate in _section_mutations():
        m = copy.deepcopy(CASES)
        mutate(m)
        try:
            _validate_structure(m)
        except AssertionError:
            continue
        raise AssertionError(f"mutation {label!r} passed")


def test_mutant_happy_boundary_scenario_substitution():
    """The verifier's replay: every happy row replaced by a
    uniquely-named VALID add-only copy, every boundary row by a
    VALID no-op copy - must fail structure validation BEFORE
    execution."""
    m = copy.deepcopy(CASES)
    add_only = copy.deepcopy(_case("happy", "compute-add-only"))
    noop = copy.deepcopy(
        _case("boundary", "compute-empty-on-equal-states"))
    m["happy"] = [dict(copy.deepcopy(add_only),
                       name=f"add-only-copy-{i}")
                  for i in range(len(HAPPY_MANIFEST))]
    m["boundary"] = [dict(copy.deepcopy(noop),
                          name=f"noop-copy-{i}")
                     for i in range(len(BOUNDARY_MANIFEST))]
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_mutant_intra_failure_class_malformed_substitution():
    """Malformed rows substituted WITHIN one failure class (all
    four classes still represented) must fail the manifest -
    class-set coverage alone is not coverage."""
    m = copy.deepcopy(CASES)
    donor = copy.deepcopy(_case("malformed",
                                "diff-bad-id-grammar"))
    for i, case in enumerate(m["malformed"]):
        if case["expect_failure"] == \
                donor["expect_failure"] and \
                case["name"] != donor["name"]:
            m["malformed"][i] = dict(copy.deepcopy(donor),
                                     name=case["name"])
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_exhaustive_pairwise_intra_section_substitution():
    """The standing scan: EVERY ordered donor/recipient pair in
    EVERY section, donor content under the recipient's name,
    must fail structure validation. Subsumes the verifier's
    same-tuple pairs (diff-missing-section ->
    diff-section-not-mapping and every other (MDR,set_section)
    pair): the scenario tags discriminate them."""
    for section in MANIFESTS:
        rows = CASES[section]
        for i, recipient in enumerate(rows):
            for j, donor in enumerate(rows):
                if i == j:
                    continue
                m = copy.deepcopy(CASES)
                m[section][i] = dict(copy.deepcopy(donor),
                                     name=recipient["name"])
                try:
                    _validate_structure(m)
                except AssertionError:
                    continue
                raise AssertionError(
                    f"{section}: {donor['name']!r} substitutes "
                    f"for {recipient['name']!r} undetected")
