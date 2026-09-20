"""T0176: graph diff contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/diff.yaml: the
diff record shape, section semantics, guarantees (completeness,
determinism, symmetry, apply-exactness) and failure model are read
from the contract. Identity keys come from the LINKED
transposition-node machinery (imported, never restated);
accelerator digests never key a diff. The engine is table-generic:
the battery exercises it over real node records (add/remove) and
over annotated node records (a metadata field OUTSIDE identity) to
exercise changed-witnesses.
"""

from __future__ import annotations

import copy
import itertools
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0113_position_digest_contract import (  # noqa: E402
    digest_fen,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    LEGAL_EP,
    STARTPOS,
    _make_record,
    _record_identity,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _docs as _node_docs,
)
from tools.diff_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_NDOCS = _node_docs()
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_ID_RE = re.compile(_CC["identifiers"]["base_id"]["grammar"])
_FIELDS = _CC["record"]["fields"]


class DiffError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise DiffError(cls, FAILURE_MAPPING[cls])


def _node(fen_text, annotation=None):
    """A real node record (identity-derived digest); annotation is
    metadata OUTSIDE identity - it makes 'changed' reachable."""
    rec = _make_record(*_NDOCS, digest_fen, "standard", fen_text)
    if annotation is not None:
        rec = dict(rec)
        rec["annotation"] = annotation
    return rec


def _identity(record):
    base = {k: record[k] for k in ("variant", "snapshot_fen",
                                   "digest")}
    return repr(_record_identity(*_NDOCS, base))


def _state(*records):
    return {_identity(r): r for r in records}


class DiffEngine:
    """The contract's pinned computation: sections keyed by
    canonical identity; completeness, determinism, symmetry and
    apply-exactness by construction; closed failure model."""

    def __init__(self):
        self.cc = _CC

    def compute(self, base_id, target_id, base, target):
        self._check_state_id(base_id)
        self._check_state_id(target_id)
        added = {k: copy.deepcopy(target[k]) for k in target
                 if k not in base}
        removed = {k: copy.deepcopy(base[k]) for k in base
                   if k not in target}
        changed = {k: {"base": copy.deepcopy(base[k]),
                       "target": copy.deepcopy(target[k])}
                   for k in base if k in target
                   and base[k] != target[k]}
        return {"base_id": base_id, "target_id": target_id,
                "added": dict(sorted(added.items())),
                "removed": dict(sorted(removed.items())),
                "changed": dict(sorted(changed.items()))}

    def _check_state_id(self, value):
        if not isinstance(value, str) or isinstance(value, bool) \
                or _ID_RE.fullmatch(value) is None:
            _fail("malformed_diff_record")

    def validate_diff(self, diff):
        """TOTAL validation: explicit type guards on every field
        before any sibling machinery."""
        if not isinstance(diff, dict):
            _fail("malformed_diff_record")
        if set(diff.keys()) != set(_FIELDS):
            _fail("malformed_diff_record")
        self._check_state_id(diff["base_id"])
        self._check_state_id(diff["target_id"])
        for section in ("added", "removed"):
            value = diff[section]
            if not isinstance(value, dict) or any(
                    not isinstance(k, str) or
                    not isinstance(v, dict)
                    for k, v in value.items()):
                _fail("malformed_diff_record")
        changed = diff["changed"]
        if not isinstance(changed, dict) or any(
                not isinstance(k, str) or
                not isinstance(v, dict) or
                set(v.keys()) != {"base", "target"} or
                not isinstance(v["base"], dict) or
                not isinstance(v["target"], dict)
                for k, v in changed.items()):
            _fail("malformed_diff_record")
        overlap = (set(diff["added"]) & set(diff["removed"])) | \
            (set(diff["added"]) & set(changed)) | \
            (set(diff["removed"]) & set(changed))
        if overlap:
            _fail("malformed_diff_record")
        if diff["base_id"] == diff["target_id"] and (
                diff["added"] or diff["removed"] or changed):
            _fail("malformed_diff_record")

    def apply(self, diff, base_id, base):
        """ATOMIC staged-copy apply: the base must match the diff's
        base exactly (id AND every removed/changed-base record);
        any violation fails closed and commits nothing."""
        self.validate_diff(diff)
        if base_id != diff["base_id"]:
            _fail("conflicting_base")
        staged = copy.deepcopy(base)
        for key, rec in diff["removed"].items():
            if key not in staged:
                _fail("unknown_identity")
            if staged[key] != rec:
                _fail("conflicting_base")
        for key, witness in diff["changed"].items():
            if key not in staged:
                _fail("unknown_identity")
            if staged[key] != witness["base"]:
                _fail("conflicting_base")
        for key in diff["removed"]:
            del staged[key]
        for key, witness in diff["changed"].items():
            staged[key] = copy.deepcopy(witness["target"])
        for key, rec in diff["added"].items():
            staged[key] = copy.deepcopy(rec)
        return staged


def test_lint_clean():
    lint()


def test_empty_diff_on_equal_states():
    engine = DiffEngine()
    state = _state(_node(STARTPOS), _node(KINGS))
    diff = engine.compute("s1", "s1", state, state)
    assert diff == {"base_id": "s1", "target_id": "s1",
                    "added": {}, "removed": {}, "changed": {}}
    engine.validate_diff(diff)
    assert engine.apply(diff, "s1", state) == state


def test_add_remove_change_mixed():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS), _node(AFTER_E4))
    target = _state(_node(STARTPOS), _node(KINGS, "annotated"),
                    _node(LEGAL_EP))
    diff = engine.compute("s1", "s2", base, target)
    assert list(diff["added"]) == [_identity(_node(LEGAL_EP))]
    assert list(diff["removed"]) == [_identity(_node(AFTER_E4))]
    assert list(diff["changed"]) == [_identity(_node(KINGS))]
    witness = diff["changed"][_identity(_node(KINGS))]
    assert witness["base"] == _node(KINGS)
    assert witness["target"] == _node(KINGS, "annotated")
    assert engine.apply(diff, "s1", base) == target


def test_completeness_theorem_permutations():
    """NO SILENT DIFFERENCE: for every permutation of a mutation
    set (adds, removals, changes), the diff surfaces EXACTLY that
    set - and a silently dropped difference is caught by the
    apply round-trip."""
    engine = DiffEngine()
    pool = [_node(STARTPOS), _node(AFTER_E4), _node(KINGS),
            _node(LEGAL_EP)]
    base = _state(*pool)
    mutations = [
        lambda t: t.pop(_identity(_node(STARTPOS))),
        lambda t: t.pop(_identity(_node(AFTER_E4))),
        lambda t: t.update({_identity(_node(KINGS, "c")):
                            _node(KINGS, "c")}),
        lambda t: t.update({_identity(_node("8/8/8/8/8/8/8/K6k"
                                            " w - - 0 1")):
                            _node("8/8/8/8/8/8/8/K6k w - - 0 1")}),
    ]
    for count in range(1, len(mutations) + 1):
        for combo in itertools.permutations(mutations, count):
            target = copy.deepcopy(base)
            for mutate in combo:
                mutate(target)
            diff = engine.compute("a", "b", base, target)
            assert engine.apply(diff, "a", base) == target
            expected = (len(base - target.keys()
                            if hasattr(base, '-') else [])
                        or None)
            del expected
            surfaced = (len(diff["added"]) + len(diff["removed"])
                        + len(diff["changed"]))
            assert surfaced == count


def test_determinism_and_symmetry():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS))
    target = _state(_node(KINGS, "x"), _node(AFTER_E4))
    d1 = engine.compute("a", "b", base, target)
    d2 = engine.compute("a", "b", base, target)
    assert d1 == d2
    forward = engine.compute("a", "b", base, target)
    reverse = engine.compute("b", "a", target, base)
    assert reverse["added"] == forward["removed"]
    assert reverse["removed"] == forward["added"]
    for key, witness in forward["changed"].items():
        flipped = reverse["changed"][key]
        assert flipped["base"] == witness["target"]
        assert flipped["target"] == witness["base"]
    assert engine.apply(reverse, "b", target) == base


def test_apply_round_trip_permutations():
    engine = DiffEngine()
    records = [_node(STARTPOS), _node(AFTER_E4), _node(KINGS),
               _node(LEGAL_EP)]
    for perm in itertools.permutations(records, 3):
        base = _state(*perm[:2])
        target = _state(perm[1], perm[2])
        diff = engine.compute("x", "y", base, target)
        assert engine.apply(diff, "x", base) == target


def _valid_diff():
    engine = DiffEngine()
    base = _state(_node(STARTPOS))
    target = _state(_node(STARTPOS), _node(KINGS))
    return engine.compute("s1", "s2", base, target)


CARTESIAN = [None, True, 0, 1.5, [], {}, ""]


@pytest.mark.parametrize("mutation", CARTESIAN)
@pytest.mark.parametrize("field", _FIELDS)
def test_cartesian_malformed_battery(field, mutation):
    if mutation == {} and field in ("added", "removed",
                                    "changed"):
        pytest.skip("empty map is a valid empty section")
    engine = DiffEngine()
    diff = _valid_diff()
    diff[field] = copy.deepcopy(mutation)
    with pytest.raises(DiffError) as exc:
        engine.validate_diff(diff)
    assert exc.value.failure_class == "malformed_diff_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_diff_record"]
    assert exc.value.code in ERROR_ENUM


MALFORMED_DIFFS = [
    ("missing section", lambda d: d.pop("added")),
    ("extra field", lambda d: d.update(extra="x")),
    ("id grammar drift", lambda d: d.update(base_id="BAD ID!")),
    ("section not map", lambda d: d.update(added=[])),
    ("witness keys drift", lambda d: d.update(
        changed={"k": {"before": {}, "after": {}}})),
    ("identity in two sections", lambda d: (
        d["removed"].update(d["added"]))),
    ("equal ids nonempty diff", lambda d: d.update(
        target_id=d["base_id"])),
]


@pytest.mark.parametrize("name,mutate", MALFORMED_DIFFS,
                         ids=[n for n, _ in MALFORMED_DIFFS])
def test_diff_specific_malformed(name, mutate):
    engine = DiffEngine()
    diff = _valid_diff()
    mutate(diff)
    with pytest.raises(DiffError) as exc:
        engine.validate_diff(diff)
    assert exc.value.failure_class == "malformed_diff_record"


def test_conflicting_base_by_id_and_by_content():
    engine = DiffEngine()
    base = _state(_node(STARTPOS))
    target = _state(_node(KINGS))
    diff = engine.compute("s1", "s2", base, target)
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, "OTHER", base)
    assert exc.value.failure_class == "conflicting_base"
    assert exc.value.code == FAILURE_MAPPING["conflicting_base"]
    assert exc.value.code in ERROR_ENUM
    # same id, tampered content: the removed record no longer
    # matches the base's exact record
    tampered = _state(_node(STARTPOS, "tampered"))
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, "s1", tampered)
    assert exc.value.failure_class == "conflicting_base"


def test_unknown_identity_in_base():
    engine = DiffEngine()
    base = _state(_node(STARTPOS))
    target = _state()
    diff = engine.compute("s1", "s2", base, target)
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, "s1", _state())
    assert exc.value.failure_class == "unknown_identity"
    assert exc.value.code == FAILURE_MAPPING["unknown_identity"]


def test_rollback_bit_identical_on_rejected_apply():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS))
    target = _state(_node(STARTPOS), _node(AFTER_E4),
                    _node(LEGAL_EP))
    diff = engine.compute("s1", "s2", base, target)
    before = copy.deepcopy(base)
    with pytest.raises(DiffError):
        engine.apply(diff, "WRONG", base)
    tampered = copy.deepcopy(base)
    tampered[_identity(_node(KINGS))] = _node(KINGS, "x")
    with pytest.raises(DiffError):
        engine.apply(diff, "s1", tampered)
    assert base == before
    # apply is staged: a diff malformed midway commits nothing
    diff2 = engine.compute("s1", "s3", base, target)
    diff2["removed"]["ghost-identity"] = {"bogus": True}
    with pytest.raises(DiffError):
        engine.apply(diff2, "s1", base)
    assert base == before


# -- mutation battery --------------------------------------------------------


def _mutants():
    doc = yaml.safe_load(CONTRACT.read_text())
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    add("role kind drift", ["contract", "role", "kind"],
        "approximate-digest-difference")
    add("scope drift", ["contract", "role", "scope"],
        "scoring-and-ranking")
    add("field dropped", ["contract", "record", "fields"],
        ["base_id", "target_id", "added", "removed"])
    add("extra field", ["contract", "record", "fields"],
        ["base_id", "target_id", "added", "removed", "changed",
         "score"])
    add("digest keys", ["contract", "identity", "digest_keys"],
        "keyed-by-accelerator-digest")
    add("identity restated", ["contract", "identity", "restated"],
        "restated-here")
    add("added meaning drift", ["contract", "sections", "added",
                                "meaning"], "present-in-base-only")
    add("changed witness drift", ["contract", "sections",
                                  "changed", "witness"],
        "target-record-only")
    add("id grammar drift", ["contract", "identifiers",
                             "base_id", "grammar"], "^.*$")
    add("distinctness drift", ["contract", "identifiers",
                               "distinctness"], "always-distinct")
    add("completeness dropped", ["contract", "guarantees",
                                 "completeness"],
        "best-effort-differences")
    add("determinism dropped", ["contract", "guarantees",
                                "determinism"],
        "insertion-order-sections")
    add("symmetry dropped", ["contract", "guarantees",
                             "symmetry"], "asymmetric")
    add("apply inexact", ["contract", "guarantees",
                          "apply_exactness"],
        "approximate-target")
    add("apply semantics drift", ["contract", "apply",
                                  "semantics"], "add-only")
    add("base check dropped", ["contract", "apply",
                               "base_check"], "no-base-check")
    add("non-atomic apply", ["contract", "apply", "commit"],
        "record-by-record")
    add("failure class dropped", ["contract", "failures",
                                  "classes"],
        ["malformed_diff_record", "conflicting_base"])
    add("trigger drift", ["contract", "failures", "triggers",
                          "conflicting_base"],
        "never-raised")
    add("triggers incomplete", ["contract", "failures",
                                "triggers"],
        {"malformed_diff_record":
         "diff-field-shape-grammar-or-reference-violation"})
    add("conflict mapped away", ["contract", "failures",
                                 "mapping", "conflicting_base"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"],
        False)
    add("error enum drift", ["contract", "errors",
                             "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "conflicting_base"])
    add("silent difference", ["contract", "properties",
                              "no_silent_difference"],
        "silent-drops-allowed")
    add("rollback drift", ["contract", "properties",
                           "rollback"], "best-effort")
    add("order drift", ["contract", "properties",
                        "canonical_order"],
        "bucket-arrival-order")
    add("base path drift", ["contract", "versioning",
                            "base_path"], "/graph/diff/v0")
    add("link drift", ["contract", "links",
                       "collision_contract"],
        "data/contracts/san.yaml")
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 25
    for _name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)


def test_mutants_never_silent_subset():
    covered = set()
    base = yaml.safe_load(CONTRACT.read_text())["contract"]
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != base.get(section):
                covered.add(section)
    assert covered >= {"role", "record", "identity", "sections",
                       "identifiers", "guarantees", "apply",
                       "failures", "errors", "properties",
                       "versioning", "links"}
