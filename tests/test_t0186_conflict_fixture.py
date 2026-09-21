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


# -- the closed scenario manifests -------------------------------------------
# Every fixture section's actual {name: metadata} map must equal
# its manifest EXACTLY - no missing, substituted, duplicated,
# renamed or extra rows, and every advertised semantic shape is
# asserted before execution.
MCR = "malformed_conflict_record"
DB = "divergent_base"

# happy/boundary: name -> (scenario tag, sorted witness-kind
# list) - the tag is asserted semantically over the INPUTS, so
# a substituted row can never satisfy the wrong scenario.
HAPPY_MANIFEST = {
    "disjoint-edits-compatible": ("disjoint", []),
    "identical-outcomes-compatible": ("identical-outcomes", []),
    "both-changed-differently": (
        "both-changed", ["both_changed_differently"]),
    "changed-vs-removed-left-changed": (
        "cvr-left", ["changed_vs_removed"]),
    "changed-vs-removed-right-changed": (
        "cvr-right", ["changed_vs_removed"]),
    "added-differently": (
        "added-differently", ["added_differently"]),
}
BOUNDARY_MANIFEST = {
    "both-removed-byte-identical-compatible": ("both-removed", []),
    "both-added-identically-compatible": ("both-added", []),
    "equal-outcomes-despite-base-digest-difference": (
        "equal-outcomes", []),
    "mixed-multi-identity-conflicts": (
        "mixed-multi", ["both_changed_differently"]),
    "single-record-states-conflict": (
        "single-record", ["both_changed_differently"]),
}
# malformed: name -> (failure class, pinned defect, repair form)
MALFORMED_MANIFEST = {
    "state-not-mapping": (MCR, "base state is not a mapping",
                          "replace_state"),
    "record-not-mapping": (MCR,
                           "a state value is not a record mapping",
                           "replace_record"),
    "record-extra-field": (MCR,
                           "record carries an undeclared extra field",
                           "replace_record"),
    "record-missing-field": (MCR,
                             "record is missing the digest field",
                             "replace_record"),
    "record-non-str-field": (MCR,
                             "record variant is not a string",
                             "replace_record"),
    "record-unknown-variant": (MCR,
                               "record variant is not a registered variant",
                               "replace_record"),
    "record-invalid-fen": (MCR,
                           "record snapshot_fen is not a legal position",
                           "replace_record"),
    "record-bad-digest-grammar": (MCR,
                                  "record digest fails the linked digest grammar",
                                  "replace_record"),
    "record-identity-key-mismatch": (MCR,
                                     "record stored under a key "
                                     "unequal to its derived "
                                     "canonical identity",
                                     "replace_record"),
    "record-noncanonical-clocks": (MCR,
                                   "record snapshot_fen clocks are not canonical",
                                   "replace_record"),
    "left-equals-base-divergent-base": (DB,
                                        "left derived id equals the base derived id",
                                        "replace_state"),
}
# rollback: name -> expected rejection class
ROLLBACK_MANIFEST = {
    "rejected-malformed-then-valid-detect": MCR,
    "rejected-divergent-base-then-valid-detect": DB,
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}


def _edits(base, other):
    """(added, removed, changed) identity sets of OTHER relative
    to BASE."""
    ids_b, ids_o = set(base), set(other)
    return (ids_o - ids_b, ids_b - ids_o,
            {i for i in ids_b & ids_o if base[i] != other[i]})


def _assert_conflict_scenario(case, scenario, kinds):
    """The advertised scenario is REALLY present in the inputs
    and the pinned expectation - a substituted row can never
    satisfy the wrong scenario."""
    name = case["name"]
    base, left, right = case["base"], case["left"], case["right"]
    conflicts = case["expect"]["conflicts"]
    actual = sorted({w["kind"] for w in conflicts.values()})
    assert actual == kinds, name
    if not kinds:
        assert conflicts == {}, name
    a_l, r_l, c_l = _edits(base, left)
    a_r, r_r, c_r = _edits(base, right)
    edits_l, edits_r = a_l | r_l | c_l, a_r | r_r | c_r
    if scenario == "disjoint":
        assert edits_l and edits_r, name
        assert not (edits_l & edits_r), name
    elif scenario == "identical-outcomes":
        assert left == right and left != base, name
    elif scenario == "both-changed":
        assert c_l & c_r, name
    elif scenario == "cvr-left":
        assert c_l & r_r, name
    elif scenario == "cvr-right":
        assert r_l & c_r, name
    elif scenario == "added-differently":
        shared = a_l & a_r
        assert shared, name
        assert any(left[i] != right[i] for i in shared), name
    elif scenario == "both-removed":
        assert r_l & r_r, name
    elif scenario == "both-added":
        shared = a_l & a_r
        assert shared, name
        assert all(left[i] == right[i] for i in shared), name
    elif scenario == "equal-outcomes":
        # the NAMED boundary: equal outcomes DESPITE A BASE
        # DIGEST DIFFERENCE - same canonical identity set on all
        # three states, left and right byte-identical, at least
        # one retained record differs from base, and every
        # differing record keeps its canonical identity fields
        # with only the (valid) accelerator digest changed. An
        # add/remove substitution can never satisfy this.
        assert set(base) == set(left) == set(right), name
        assert left == right, name
        differing = [i for i in base if base[i] != left[i]]
        assert differing, name
        for i in differing:
            b, lft = base[i], left[i]
            assert _identity(b) == _identity(lft), name
            assert b["digest"] != lft["digest"], name
        expect = case["expect"]
        assert expect["left_id"] == expect["right_id"], name
        assert expect["base_id"] != expect["left_id"], name
    elif scenario == "mixed-multi":
        assert len(base) >= 2, name
        assert len(conflicts) == 1, name
        touched = edits_l | edits_r
        assert len(touched) >= 3, name
    elif scenario == "single-record":
        assert len(base) == len(left) == len(right) == 1, name
        assert len(conflicts) == 1, name
    else:  # pragma: no cover - manifest typo guard
        raise AssertionError(f"unknown scenario {scenario!r}")


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
        # a missing, substituted, duplicated, renamed or extra
        # row fails HERE, before execution.
        manifest = MANIFESTS[section]
        assert set(names) == set(manifest), (
            f"{section} scenario set drifted: "
            f"missing={set(manifest) - set(names)} "
            f"extra={set(names) - set(manifest)}")
        for case in cases[section]:
            want = manifest[case["name"]]
            if section in ("happy", "boundary"):
                scenario, kinds = want
                _assert_conflict_scenario(case, scenario, kinds)
            elif section == "malformed":
                failure, defect, repair = want
                assert case["expect_failure"] == failure, (
                    case["name"])
                assert case["defect"] == defect, case["name"]
                assert set(case["minimal_repair"]) == {repair}, (
                    case["name"])
            else:
                assert case["expect_failure"] == want, (
                    case["name"])
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


def _exec_malformed(case, detector=None):
    """The ORIGINAL input rejects with the pinned failure class
    and leaves every input state byte-identical. The EXACT
    working objects handed to detect are inspected after the
    rejection - mutation of the received objects is observed."""
    base, left, right = (copy.deepcopy(case["base"]),
                         copy.deepcopy(case["left"]),
                         copy.deepcopy(case["right"]))
    pristine = (copy.deepcopy(base), copy.deepcopy(left),
                copy.deepcopy(right))
    detect = (detector or ConflictDetector()).detect
    try:
        detect(base, left, right)
    except ConflictError_ as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.code in ERROR_ENUM
        assert (base, left, right) == pristine
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


def _exec_rollback(case, detector=None):
    """A rejection leaves every input state byte-identical, and a
    subsequent valid detect returns the pinned result. The EXACT
    working objects handed to the rejected detect are inspected
    after the rejection."""
    base, left, right = (copy.deepcopy(case["base"]),
                         copy.deepcopy(case["left"]),
                         copy.deepcopy(case["right"]))
    pristine = (copy.deepcopy(base), copy.deepcopy(left),
                copy.deepcopy(right))
    detect = (detector or ConflictDetector()).detect
    with pytest.raises(ConflictError_) as exc:
        detect(base, left, right)
    assert exc.value.failure_class == case["expect_failure"]
    assert exc.value.code == FAILURE_MAPPING[
        case["expect_failure"]]
    assert (base, left, right) == pristine
    _run_detect(case["then_base"], case["then_left"],
                case["then_right"], case["expect"])


def test_rollback():
    for name in _names("rollback"):
        _exec_rollback(_case("rollback", name))


# -- v2: discriminating input-mutation mutant + closed scenario
# coverage --


class _MutatingDetector:
    """Discriminating mutant: corrupts every received input
    state, then raises the expected failure class. A vacuous
    immutability witness (disposable copies) PASSES against
    this detector; a real witness fails."""

    def __init__(self, failure_class):
        self.failure_class = failure_class

    def detect(self, base, left, right):
        for state in (base, left, right):
            if isinstance(state, dict):
                state.clear()
        raise ConflictError_(self.failure_class,
                             FAILURE_MAPPING[self.failure_class])


def test_mutating_detector_discriminates():
    """Both rejection helpers FAIL against a detector that
    mutates the inputs it received before rejecting - covering
    both declared rollback failure classes."""
    for section in ("malformed", "rollback"):
        for case in CASES[section]:
            detector = _MutatingDetector(case["expect_failure"])
            helper = (_exec_malformed if section == "malformed"
                      else _exec_rollback)
            try:
                helper(case, detector=detector)
            except AssertionError:
                continue
            raise AssertionError(
                f"{helper.__name__} passed against a mutating "
                f"detector for {case['name']!r}")


def test_dispatch_sections_match_manifest():
    """Every iterated or parametrized section dispatches EXACTLY
    the manifest's scenario names - a late, replaced or renamed
    row cannot evade or sneak into execution."""
    for section, manifest in MANIFESTS.items():
        assert set(_names(section)) == set(manifest), section


def _pop_first(m, section):
    m[section].pop(0)


def _rename_first(m, section):
    row = copy.deepcopy(m[section][0])
    row["name"] = row["name"] + "-renamed"
    m[section][0] = row


def _add_extra(m, section):
    row = copy.deepcopy(m[section][0])
    row["name"] = "extra-row-witness"
    m[section].append(row)


def _substitute_first(m, section):
    row = copy.deepcopy(m[section][1])
    row["name"] = m[section][0]["name"]
    m[section][0] = row


def _move_row(m, section):
    other = ({"happy", "boundary", "malformed", "rollback"}
             - {section})
    row = copy.deepcopy(m[sorted(other)[0]][0])
    m[section][0] = row


def _swap_failure(m, section):
    a, b = m["malformed"][0], m["malformed"][10]
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _swap_repair(m, section):
    a, b = m["malformed"][0], m["malformed"][1]
    a["minimal_repair"], b["minimal_repair"] = (
        b["minimal_repair"], a["minimal_repair"])


def _swap_defect(m, section):
    a, b = m["malformed"][1], m["malformed"][2]
    a["defect"], b["defect"] = b["defect"], a["defect"]


def _swap_rollback_failure(m, section):
    a, b = m["rollback"][0], m["rollback"][1]
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _section_mutations():
    out = []
    for section in MANIFESTS:
        out.append((f"{section}-delete-row", _pop_first))
        out.append((f"{section}-rename-row", _rename_first))
        out.append((f"{section}-add-row", _add_extra))
        out.append((f"{section}-substitute-row",
                    _substitute_first))
        out.append((f"{section}-moved-row", _move_row))
    out.append(("malformed-swap-failure", _swap_failure))
    out.append(("malformed-swap-repair", _swap_repair))
    out.append(("malformed-swap-defect", _swap_defect))
    out.append(("rollback-swap-failure", _swap_rollback_failure))
    return out


def test_section_mutations_fail_structure():
    for label, mutate in _section_mutations():
        for section in MANIFESTS:
            m = copy.deepcopy(CASES)
            mutate(m, section)
            try:
                _validate_structure(m)
            except AssertionError:
                continue
            raise AssertionError(
                f"mutation {label!r} on {section!r} passed")


def test_mutant_whole_section_scenario_substitution():
    """Every happy row replaced by a uniquely-named VALID
    disjoint-edits copy, every boundary row by a VALID
    both-removed copy, every rollback row by a VALID
    rejected-malformed copy - must fail structure validation
    BEFORE execution."""
    m = copy.deepcopy(CASES)
    disjoint = copy.deepcopy(
        _case("happy", "disjoint-edits-compatible"))
    both_removed = copy.deepcopy(
        _case("boundary",
              "both-removed-byte-identical-compatible"))
    rejected = copy.deepcopy(
        _case("rollback",
              "rejected-malformed-then-valid-detect"))
    m["happy"] = [dict(copy.deepcopy(disjoint),
                       name=f"disjoint-copy-{i}")
                  for i in range(len(HAPPY_MANIFEST))]
    m["boundary"] = [dict(copy.deepcopy(both_removed),
                          name=f"both-removed-copy-{i}")
                     for i in range(len(BOUNDARY_MANIFEST))]
    m["rollback"] = [dict(copy.deepcopy(rejected),
                          name=f"rejected-copy-{i}")
                     for i in range(len(ROLLBACK_MANIFEST))]
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_mutant_intra_failure_class_malformed_substitution():
    """Malformed rows substituted WITHIN one failure class AND
    one repair form must fail the manifest - the pinned defect
    text discriminates."""
    m = copy.deepcopy(CASES)
    donor = copy.deepcopy(
        _case("malformed", "record-missing-field"))
    for i, case in enumerate(m["malformed"]):
        if (case["expect_failure"] == donor["expect_failure"]
                and set(case["minimal_repair"])
                == set(donor["minimal_repair"])
                and case["name"] != donor["name"]):
            m["malformed"][i] = dict(copy.deepcopy(donor),
                                     name=case["name"])
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_exhaustive_pairwise_intra_section_substitution():
    """The standing scan: EVERY ordered donor/recipient pair in
    EVERY section, donor content under the recipient's name,
    must fail structure validation."""
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
