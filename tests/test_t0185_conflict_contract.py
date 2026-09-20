"""T0185: graph conflict contract behavior battery.

Reference detector FULLY DERIVED from data/contracts/
conflict.yaml: three-way incompatibility detection over
(base, left, right) graph states. Change derivation per side is
the identity-keyed symmetric difference vs base; the three
incompatibility kinds, compatibility rules, witness shape and
closed failure model are read from the contract. Identity keys
come from the LINKED transposition-node machinery (imported,
never restated); accelerator digests never key a conflict.
"""

from __future__ import annotations

import copy
import itertools
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
from tools.conflict_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_NDOCS = _node_docs()
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
K1 = "pdv1:" + "1" * 64
K2 = "pdv1:" + "2" * 64
K3 = "pdv1:" + "3" * 64


class ConflictError_(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise ConflictError_(cls, FAILURE_MAPPING[cls])


def _node(fen_text, digest_override=None):
    rec = _make_record(*_NDOCS, digest_fen, "standard", fen_text)
    if digest_override is not None:
        rec = dict(rec)
        rec["digest"] = digest_override
    return rec


def _identity(record):
    return repr(_record_identity(*_NDOCS, record))


def _state(*records):
    return {_identity(r): r for r in records}


def _changes(base, derived):
    """Identity-keyed symmetric difference vs base: maps identity
    -> ("added", rec) | ("removed", rec) | ("changed", (b, t))."""
    out = {}
    for key, rec in derived.items():
        if key not in base:
            out[key] = ("added", copy.deepcopy(rec))
        elif base[key] != rec:
            out[key] = ("changed", (copy.deepcopy(base[key]),
                                    copy.deepcopy(rec)))
    for key, rec in base.items():
        if key not in derived:
            out[key] = ("removed", copy.deepcopy(rec))
    return out


class ConflictDetector:
    """The contract's pinned detection: total over inputs,
    deterministic, symmetric, complete; never mutates inputs,
    never auto-resolves."""

    def detect(self, base_id, base, left_id, left, right_id,
               right):
        for value in (base_id, left_id, right_id):
            if not isinstance(value, str) or not value:
                _fail("malformed_conflict_record")
        for state in (base, left, right):
            if not isinstance(state, dict):
                _fail("malformed_conflict_record")
        if len({base_id, left_id, right_id}) != 3:
            _fail("divergent_base")
        left_changes = _changes(base, left)
        right_changes = _changes(base, right)
        conflicts = {}
        for key in sorted(set(left_changes)
                          & set(right_changes)):
            lkind, lval = left_changes[key]
            rkind, rval = right_changes[key]
            if lkind == rkind == "added":
                if lval != rval:
                    conflicts[key] = {
                        "kind": "added_differently",
                        "left": copy.deepcopy(lval),
                        "right": copy.deepcopy(rval)}
            elif lkind == rkind == "changed":
                if lval[1] != rval[1]:
                    conflicts[key] = {
                        "kind": "both_changed_differently",
                        "left": copy.deepcopy(lval[1]),
                        "right": copy.deepcopy(rval[1])}
            elif lkind == rkind == "removed":
                pass  # both removed: byte-identical outcome
            else:
                # one side changed, the other removed
                changed_val = lval[1] if lkind == "changed" \
                    else rval[1]
                conflicts[key] = {
                    "kind": "changed_vs_removed",
                    "left": copy.deepcopy(
                        lval[1]) if lkind == "changed" else None,
                    "right": copy.deepcopy(
                        rval[1]) if rkind == "changed" else None}
                del changed_val
        return {"base_id": base_id, "left_id": left_id,
                "right_id": right_id,
                "conflicts": dict(sorted(conflicts.items()))}


def test_lint_clean():
    lint()


def test_compatible_disjoint_edits_no_conflicts():
    det = ConflictDetector()
    base = _state(_node(STARTPOS), _node(KINGS))
    left = _state(_node(STARTPOS), _node(KINGS), _node(AFTER_E4))
    right = _state(_node(STARTPOS), _node(LEGAL_EP))
    result = det.detect("b", base, "l", left, "r", right)
    assert result["conflicts"] == {}
    assert result["base_id"] == "b"


def test_identical_outcomes_compatible():
    det = ConflictDetector()
    base = _state(_node(STARTPOS), _node(KINGS))
    left = _state(_node(STARTPOS), _node(KINGS, K1))
    right = _state(_node(STARTPOS), _node(KINGS, K1))
    assert det.detect("b", base, "l", left, "r",
                      right)["conflicts"] == {}
    removed_left = _state(_node(STARTPOS))
    removed_right = _state(_node(STARTPOS))
    assert det.detect("b", base, "l", removed_left, "r",
                      removed_right)["conflicts"] == {}
    added_left = _state(_node(STARTPOS), _node(KINGS),
                        _node(AFTER_E4))
    assert det.detect("b", base, "l", added_left, "r",
                      added_left)["conflicts"] == {}


def test_both_changed_differently():
    det = ConflictDetector()
    base = _state(_node(KINGS))
    left = _state(_node(KINGS, K1))
    right = _state(_node(KINGS, K2))
    result = det.detect("b", base, "l", left, "r", right)
    key = _identity(_node(KINGS))
    witness = result["conflicts"][key]
    assert witness["kind"] == "both_changed_differently"
    assert witness["left"] == _node(KINGS, K1)
    assert witness["right"] == _node(KINGS, K2)


def test_changed_vs_removed_both_orientations():
    det = ConflictDetector()
    base = _state(_node(KINGS))
    changed = _state(_node(KINGS, K1))
    removed = _state()
    result = det.detect("b", base, "l", changed, "r", removed)
    key = _identity(_node(KINGS))
    witness = result["conflicts"][key]
    assert witness["kind"] == "changed_vs_removed"
    assert witness["left"] == _node(KINGS, K1)
    assert witness["right"] is None
    flipped = det.detect("b", base, "l", removed, "r", changed)
    witness2 = flipped["conflicts"][key]
    assert witness2["left"] is None
    assert witness2["right"] == _node(KINGS, K1)


def test_added_differently():
    det = ConflictDetector()
    base = _state(_node(STARTPOS))
    left = _state(_node(STARTPOS), _node(KINGS, K1))
    right = _state(_node(STARTPOS), _node(KINGS, K2))
    result = det.detect("b", base, "l", left, "r", right)
    key = _identity(_node(KINGS))
    assert result["conflicts"][key]["kind"] == "added_differently"
    # same identity added with byte-identical content: compatible
    both_same = _state(_node(STARTPOS), _node(KINGS, K1))
    assert det.detect("b", base, "l", both_same, "r",
                      both_same)["conflicts"] == {}


def test_completeness_over_mutation_pairs():
    """Every incompatible overlap surfaces; empty conflicts iff
    the two edit sets are compatible - across permutations of
    mutation pairs on a shared pool."""
    det = ConflictDetector()
    pool = [_node(STARTPOS), _node(AFTER_E4), _node(KINGS),
            _node(LEGAL_EP)]
    base = _state(*pool)
    extra = _node("8/8/8/8/8/8/8/K6k w - - 0 1")

    def mut_remove(fen):
        return lambda t: t.pop(_identity(_node(fen)))

    def mut_change(fen, key):
        return lambda t: t.update(
            {_identity(_node(fen)): _node(fen, key)})

    def mut_add(rec):
        return lambda t: t.update({_identity(rec): rec})

    edits = [mut_remove(STARTPOS), mut_remove(KINGS),
             mut_change(KINGS, K1), mut_change(KINGS, K2),
             mut_change(AFTER_E4, K1), mut_add(extra),
             mut_add(dict(extra, digest=K3))]
    for left_edit, right_edit in itertools.product(edits,
                                                   repeat=2):
        left = copy.deepcopy(base)
        right = copy.deepcopy(base)
        left_edit(left)
        right_edit(right)
        result = det.detect("b", base, "l", left, "r", right)
        # independent re-derivation of the expected verdict
        lc = _changes(base, left)
        rc = _changes(base, right)
        expected = set()
        for key in set(lc) & set(rc):
            lk, lv = lc[key]
            rk, rv = rc[key]
            if lk == rk == "removed":
                continue
            if lk == rk == "added" and lv == rv:
                continue
            if lk == rk == "changed" and lv[1] == rv[1]:
                continue
            expected.add(key)
        assert set(result["conflicts"]) == expected


def test_symmetry_left_right_swap():
    det = ConflictDetector()
    base = _state(_node(KINGS), _node(STARTPOS))
    left = _state(_node(KINGS, K1), _node(STARTPOS))
    right = _state(_node(KINGS, K2))
    forward = det.detect("b", base, "l", left, "r", right)
    swapped = det.detect("b", base, "r", right, "l", left)
    assert swapped["left_id"] == "r"
    assert set(swapped["conflicts"]) == set(forward["conflicts"])
    for key, witness in forward["conflicts"].items():
        flip = swapped["conflicts"][key]
        assert flip["kind"] == witness["kind"]
        assert flip["left"] == witness["right"]
        assert flip["right"] == witness["left"]


def test_determinism_and_inputs_never_mutated():
    det = ConflictDetector()
    base = _state(_node(KINGS))
    left = _state(_node(KINGS, K1))
    right = _state(_node(KINGS, K2))
    snapshot = (copy.deepcopy(base), copy.deepcopy(left),
                copy.deepcopy(right))
    first = det.detect("b", base, "l", left, "r", right)
    second = det.detect("b", base, "l", left, "r", right)
    assert first == second
    assert (base, left, right) == snapshot


def test_divergent_base_id_collision():
    det = ConflictDetector()
    base = _state(_node(KINGS))
    left = _state(_node(KINGS, K1))
    right = _state(_node(KINGS, K2))
    for ids in [("b", "b", "r"), ("b", "l", "b"),
                ("b", "l", "l"), ("x", "x", "x")]:
        before = (copy.deepcopy(base), copy.deepcopy(left),
                  copy.deepcopy(right))
        with pytest.raises(ConflictError_) as exc:
            det.detect(ids[0], base, ids[1], left, ids[2],
                       right)
        assert exc.value.failure_class == "divergent_base"
        assert exc.value.code == FAILURE_MAPPING[
            "divergent_base"]
        assert exc.value.code in ERROR_ENUM
        assert (base, left, right) == before


HOSTILE = [None, True, 0, 1.5, "text", []]


@pytest.mark.parametrize("hostile", HOSTILE)
@pytest.mark.parametrize("slot", ["base", "left", "right"])
def test_total_over_hostile_states(hostile, slot):
    det = ConflictDetector()
    good = _state(_node(KINGS))
    args = {"base": good, "left": good, "right": good}
    args[slot] = hostile
    with pytest.raises(ConflictError_) as exc:
        det.detect("b", args["base"], "l", args["left"], "r",
                   args["right"])
    assert exc.value.failure_class == \
        "malformed_conflict_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_conflict_record"]
    assert exc.value.code in ERROR_ENUM


HOSTILE_IDS = [None, True, 0, 1.5, [], ""]


@pytest.mark.parametrize("hostile", HOSTILE_IDS)
@pytest.mark.parametrize("slot", ["base_id", "left_id",
                                  "right_id"])
def test_total_over_hostile_ids(hostile, slot):
    det = ConflictDetector()
    good = _state(_node(KINGS))
    ids = {"base_id": "b", "left_id": "l", "right_id": "r"}
    ids[slot] = hostile
    with pytest.raises(ConflictError_) as exc:
        det.detect(ids["base_id"], good, ids["left_id"], good,
                   ids["right_id"], good)
    assert exc.value.failure_class in FAILURE_MAPPING


def test_digest_never_decides_compatibility():
    """Collision-oriented: left and right store one identity
    under the SAME accelerator key but with different canonical
    records (distinct identities) - no conflict (disjoint);
    and one identity re-keyed identically on both sides is
    compatible even though the accelerator moved."""
    det = ConflictDetector()
    a = dict(_node(STARTPOS), digest=K1)
    b = dict(_node(KINGS), digest=K1)  # shared key, 2 identities
    base = _state(a)
    left = _state(a, b)
    right = _state(a)
    assert det.detect("b", base, "l", left, "r",
                      right)["conflicts"] == {}
    base2 = _state(_node(STARTPOS))
    rekeyed = _state(_node(STARTPOS, K2))
    assert det.detect("b", base2, "l", rekeyed, "r",
                      rekeyed)["conflicts"] == {}


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
        "last-writer-wins-resolution")
    add("not_scope drift", ["contract", "role", "not_scope"],
        "resolution-owned-here")
    add("field dropped", ["contract", "record", "fields"],
        ["base_id", "left_id", "right_id"])
    add("extra field", ["contract", "record", "fields"],
        ["base_id", "left_id", "right_id", "conflicts",
         "resolution"])
    add("digest keys", ["contract", "identity", "digest_keys"],
        "keyed-by-accelerator-digest")
    add("derivation drift", ["contract", "identity",
                             "change_derivation"],
        "path-based-difference")
    add("witness fields drift", ["contract", "conflicts_section",
                                 "witness", "fields"],
        ["kind", "winner"])
    add("sentinel drift", ["contract", "conflicts_section",
                           "witness", "absent_sentinel"],
        "empty-string")
    add("kind dropped", ["contract", "conflicts_section",
                         "kinds"],
        ["both_changed_differently", "changed_vs_removed"])
    add("disjoint drift", ["contract", "compatibility",
                           "disjoint_identities"],
        "disjoint-conflicts")
    add("identical drift", ["contract", "compatibility",
                            "identical_outcomes"],
        "identical-outcomes-conflict")
    add("else drift", ["contract", "compatibility",
                       "everything_else"], "silently-merged")
    add("distinct ids dropped", ["contract", "base_check",
                                 "distinct_ids"],
        "shared-ids-allowed")
    add("determinism drift", ["contract", "guarantees",
                              "determinism"],
        "insertion-order")
    add("symmetry drift", ["contract", "guarantees",
                           "symmetry"], "asymmetric")
    add("completeness drift", ["contract", "guarantees",
                               "completeness"],
        "best-effort")
    add("silent resolution", ["contract", "guarantees",
                              "no_silent_resolution"],
        "auto-merge-conflicts")
    add("failure class dropped", ["contract", "failures",
                                  "classes"],
        ["malformed_conflict_record"])
    add("trigger drift", ["contract", "failures", "triggers",
                          "divergent_base"], "never-raised")
    add("triggers incomplete", ["contract", "failures",
                                "triggers"],
        {"malformed_conflict_record":
         "field-set-grammar-witness-shape-or-kind-violation"})
    add("divergent mapped away", ["contract", "failures",
                                  "mapping", "divergent_base"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"],
        False)
    add("error enum drift", ["contract", "errors",
                             "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "divergent_base"])
    add("witnesses drift", ["contract", "properties",
                            "witnesses_exact"],
        "summaries-only")
    add("mutates inputs", ["contract", "properties", "atomic"],
        "detection-may-mutate")
    add("order drift", ["contract", "properties",
                        "canonical_order"],
        "arrival-order")
    add("base path drift", ["contract", "versioning",
                            "base_path"], "/graph/conflict/v0")
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
    assert covered >= {"role", "record", "identity",
                       "conflicts_section", "compatibility",
                       "base_check", "guarantees", "failures",
                       "errors", "properties", "versioning",
                       "links"}
