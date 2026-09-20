"""T0176: graph diff contract behavior battery (v2).

Reference engine FULLY DERIVED from data/contracts/diff.yaml:
state ids are CANONICAL STATE CONTENT DIGESTS over the full
identity->record map (structural whole-base evidence); sections
carry exact valid LINKED node records whose map key EQUALS the
derived canonical identity; compute is TOTAL over hostile state
inputs; apply verifies the recomputed base id BEFORE any
mutation and rejects added identities already present. The
changed section exercises cross-oracle digest twins (equal
canonical identity, unequal accelerator key) - proving digests
never substitute for identity.
"""

from __future__ import annotations

import copy
import hashlib
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
    NodeError,
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
_DIGEST = ROOT / "data/contracts/position_digest.yaml"
_DIGEST_RE = re.compile(
    yaml.safe_load(_DIGEST.read_text())["contract"]["digest"]
    ["format"]["regex"])

K1 = "pdv1:" + "1" * 64
K2 = "pdv1:" + "2" * 64


class DiffError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise DiffError(cls, FAILURE_MAPPING[cls])


def _node(fen_text, digest_override=None):
    """A real node record through the linked machinery; an
    override swaps only the accelerator digest (a cross-oracle
    twin: equal canonical identity, unequal key)."""
    rec = _make_record(*_NDOCS, digest_fen, "standard", fen_text)
    if digest_override is not None:
        rec = dict(rec)
        rec["digest"] = digest_override
    return rec


def _identity(record):
    return repr(_record_identity(*_NDOCS, record))


def _state(*records):
    return {_identity(r): r for r in records}


def _validate_record_key(key, rec):
    """Every section entry: the value must be an exact valid
    LINKED node record (canonical fields through the node
    machinery, exact built-in-str digest in the linked format)
    whose DERIVED canonical identity equals the map key."""
    if not isinstance(key, str) or type(key) is not str:
        _fail("malformed_diff_record")
    if not isinstance(rec, dict):
        _fail("malformed_diff_record")
    try:
        derived = _make_record(*_NDOCS, digest_fen,
                               rec.get("variant"),
                               rec.get("snapshot_fen"))
    except NodeError:
        _fail("malformed_diff_record")
    if set(rec.keys()) != set(derived.keys()):
        _fail("malformed_diff_record")
    if rec["variant"] != derived["variant"] or \
            rec["snapshot_fen"] != derived["snapshot_fen"]:
        _fail("malformed_diff_record")
    if type(rec["digest"]) is not str or \
            _DIGEST_RE.fullmatch(rec["digest"]) is None:
        _fail("malformed_diff_record")
    if _identity(rec) != key:
        _fail("malformed_diff_record")


def state_id(state):
    """The canonical state content digest: sha256 over the
    canonical serialization of the FULL identity->record map.
    Derived, never supplied."""
    parts = []
    for key in sorted(state):
        rec = state[key]
        body = "|".join(f"{field}={rec[field]}"
                        for field in sorted(rec))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256(
        "".join(parts).encode()).hexdigest()


class DiffEngine:
    """The contract's pinned computation: total validation,
    content-addressed state ids, canonical order, closed failure
    model, atomic staged apply with structural base check."""

    def _validate_state(self, state):
        if not isinstance(state, dict):
            _fail("malformed_diff_record")
        for key, rec in state.items():
            _validate_record_key(key, rec)

    def compute(self, base, target):
        self._validate_state(base)
        self._validate_state(target)
        added = {k: copy.deepcopy(target[k]) for k in target
                 if k not in base}
        removed = {k: copy.deepcopy(base[k]) for k in base
                   if k not in target}
        changed = {k: {"base": copy.deepcopy(base[k]),
                       "target": copy.deepcopy(target[k])}
                   for k in base if k in target
                   and base[k] != target[k]}
        return {"base_id": state_id(base),
                "target_id": state_id(target),
                "added": dict(sorted(added.items())),
                "removed": dict(sorted(removed.items())),
                "changed": dict(sorted(changed.items()))}

    def validate_diff(self, diff):
        """TOTAL: explicit guards on every field; every section
        entry validated through the linked machinery with
        key/identity agreement; changed witnesses carry unequal
        exact content under one derived identity."""
        if not isinstance(diff, dict):
            _fail("malformed_diff_record")
        if set(diff.keys()) != set(_FIELDS):
            _fail("malformed_diff_record")
        for field in ("base_id", "target_id"):
            value = diff[field]
            if type(value) is not str or \
                    _ID_RE.fullmatch(value) is None:
                _fail("malformed_diff_record")
        for section in ("added", "removed"):
            value = diff[section]
            if not isinstance(value, dict):
                _fail("malformed_diff_record")
            for key, rec in value.items():
                _validate_record_key(key, rec)
        changed = diff["changed"]
        if not isinstance(changed, dict):
            _fail("malformed_diff_record")
        for key, witness in changed.items():
            if not isinstance(witness, dict) or \
                    set(witness.keys()) != {"base", "target"}:
                _fail("malformed_diff_record")
            _validate_record_key(key, witness["base"])
            _validate_record_key(key, witness["target"])
            if witness["base"] == witness["target"]:
                _fail("malformed_diff_record")
        overlap = (set(diff["added"]) & set(diff["removed"])) | \
            (set(diff["added"]) & set(changed)) | \
            (set(diff["removed"]) & set(changed))
        if overlap:
            _fail("malformed_diff_record")
        # ID AGREEMENT, both directions: an empty diff must carry
        # EQUAL base and target ids (a no-op cannot lie about its
        # target); a non-empty diff must carry DISTINCT ids.
        empty = not (diff["added"] or diff["removed"] or changed)
        if empty and diff["base_id"] != diff["target_id"]:
            _fail("divergent_target")
        if not empty and diff["base_id"] == diff["target_id"]:
            _fail("malformed_diff_record")

    def apply(self, diff, base):
        """ATOMIC: validate the diff, then verify the WHOLE base
        structurally - the recomputed base state id must equal the
        diff's base_id (unchanged records, extras and tampering
        all move the digest) and every added identity must be
        absent - BEFORE any mutation."""
        self.validate_diff(diff)
        self._validate_state(base)
        if state_id(base) != diff["base_id"]:
            _fail("conflicting_base")
        for key in diff["added"]:
            if key in base:
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
        # TARGET VERIFICATION: the staged result must BE the
        # diff's claimed target - recompute the state id and
        # require exact equality with target_id before returning;
        # a corrupted or malicious diff that lies about its
        # target fails closed here (base never mutated).
        if state_id(staged) != diff["target_id"]:
            _fail("divergent_target")
        return staged


def test_lint_clean():
    lint()


def test_empty_diff_on_equal_states():
    engine = DiffEngine()
    state = _state(_node(STARTPOS), _node(KINGS))
    diff = engine.compute(state, state)
    assert diff == {"base_id": state_id(state),
                    "target_id": state_id(state),
                    "added": {}, "removed": {}, "changed": {}}
    assert diff["base_id"] == diff["target_id"]
    engine.validate_diff(diff)
    assert engine.apply(diff, state) == state


def test_add_remove_change_mixed():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS, K1),
                  _node(AFTER_E4))
    target = _state(_node(STARTPOS), _node(KINGS, K2),
                    _node(LEGAL_EP))
    diff = engine.compute(base, target)
    assert diff["base_id"] == state_id(base)
    assert diff["target_id"] == state_id(target)
    assert diff["base_id"] != diff["target_id"]
    assert list(diff["added"]) == [_identity(_node(LEGAL_EP))]
    assert list(diff["removed"]) == [_identity(_node(AFTER_E4))]
    assert list(diff["changed"]) == [_identity(_node(KINGS))]
    witness = diff["changed"][_identity(_node(KINGS))]
    assert witness["base"] == _node(KINGS, K1)
    assert witness["target"] == _node(KINGS, K2)
    # the digest twin carries ONE canonical identity - the
    # accelerator key never substitutes for identity
    assert _identity(witness["base"]) == _identity(
        witness["target"])
    engine.validate_diff(diff)
    assert engine.apply(diff, base) == target


def test_completeness_theorem_permutations():
    """NO SILENT DIFFERENCE: every permutation of a mutation set
    surfaces EXACTLY; the apply round-trip catches any silently
    dropped difference."""
    engine = DiffEngine()
    pool = [_node(STARTPOS), _node(AFTER_E4), _node(KINGS),
            _node(LEGAL_EP)]
    base = _state(*pool)
    extra = _node("8/8/8/8/8/8/8/K6k w - - 0 1")
    mutations = [
        lambda t: t.pop(_identity(_node(STARTPOS))),
        lambda t: t.pop(_identity(_node(AFTER_E4))),
        lambda t: t.update({_identity(_node(KINGS)):
                            _node(KINGS, K1)}),
        lambda t: t.update({_identity(extra): extra}),
    ]
    for count in range(1, len(mutations) + 1):
        for combo in itertools.permutations(mutations, count):
            target = copy.deepcopy(base)
            for mutate in combo:
                mutate(target)
            diff = engine.compute(base, target)
            engine.validate_diff(diff)
            assert engine.apply(diff, base) == target
            surfaced = (len(diff["added"]) + len(diff["removed"])
                        + len(diff["changed"]))
            assert surfaced == count


def test_determinism_and_symmetry():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS, K1))
    target = _state(_node(KINGS, K2), _node(AFTER_E4))
    assert engine.compute(base, target) == \
        engine.compute(base, target)
    forward = engine.compute(base, target)
    reverse = engine.compute(target, base)
    assert reverse["base_id"] == forward["target_id"]
    assert reverse["target_id"] == forward["base_id"]
    assert reverse["added"] == forward["removed"]
    assert reverse["removed"] == forward["added"]
    for key, witness in forward["changed"].items():
        flipped = reverse["changed"][key]
        assert flipped["base"] == witness["target"]
        assert flipped["target"] == witness["base"]
    assert engine.apply(reverse, target) == base


def test_apply_round_trip_permutations():
    engine = DiffEngine()
    records = [_node(STARTPOS), _node(AFTER_E4), _node(KINGS),
               _node(LEGAL_EP)]
    for perm in itertools.permutations(records, 3):
        base = _state(*perm[:2])
        target = _state(perm[1], perm[2])
        diff = engine.compute(base, target)
        assert engine.apply(diff, base) == target


# -- blocker 1: structural base check ----------------------------------------


def test_base_with_extra_unrelated_record_rejected():
    engine = DiffEngine()
    base = _state(_node(STARTPOS))
    target = _state(_node(KINGS))
    diff = engine.compute(base, target)
    bloated = _state(_node(STARTPOS), _node(AFTER_E4))
    before = copy.deepcopy(bloated)
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, bloated)
    assert exc.value.failure_class == "conflicting_base"
    assert exc.value.code == FAILURE_MAPPING["conflicting_base"]
    assert exc.value.code in ERROR_ENUM
    assert bloated == before


def test_base_missing_unchanged_record_rejected():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS))
    target = _state(_node(STARTPOS), _node(AFTER_E4))
    diff = engine.compute(base, target)
    shrunk = _state(_node(KINGS))  # STARTPOS silently dropped
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, shrunk)
    assert exc.value.failure_class == "conflicting_base"


def test_base_tampered_unchanged_record_rejected():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS))
    target = _state(_node(STARTPOS), _node(AFTER_E4))
    diff = engine.compute(base, target)
    tampered = _state(_node(STARTPOS, K1), _node(KINGS))
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, tampered)
    assert exc.value.failure_class == "conflicting_base"


def test_add_only_diff_over_present_identity_rejected():
    """An add-only diff whose added identity is ALREADY in the
    base (different content) must never silently overwrite."""
    engine = DiffEngine()
    base = _state(_node(STARTPOS))
    target = _state(_node(STARTPOS), _node(KINGS, K2))
    diff = engine.compute(base, target)
    assert set(diff["added"]) == {_identity(_node(KINGS))}
    occupied = _state(_node(STARTPOS), _node(KINGS, K1))
    before = copy.deepcopy(occupied)
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, occupied)
    assert exc.value.failure_class == "conflicting_base"
    assert occupied == before


# -- blocker 2: section validation through linked machinery ------------------


def _valid_diff():
    engine = DiffEngine()
    base = _state(_node(STARTPOS))
    target = _state(_node(STARTPOS), _node(KINGS))
    return engine.compute(base, target)


BOGUS_SECTION_ENTRIES = [
    ("bogus key dict value", "added", {"bogus": {"anything": 1}}),
    ("bogus witness pair", "changed",
     {"bogus": {"base": {}, "target": {}}}),
    ("key unrelated to value identity", "added",
     {"not-the-identity": _node(KINGS)}),
    ("equal changed witness", "changed",
     {_identity(_node(KINGS)):
      {"base": _node(KINGS), "target": _node(KINGS)}}),
    ("raw noncanonical fen", "added",
     {_identity(_node(AFTER_E4)):
      dict(_node(AFTER_E4),
           snapshot_fen=AFTER_E4.replace(" - ", " e3 "))}),
    ("bad digest format", "added",
     {_identity(_node(KINGS)): dict(_node(KINGS),
                                    digest="not-a-digest")}),
    ("extra record field", "added",
     {_identity(_node(KINGS)): dict(_node(KINGS), extra=1)}),
]


@pytest.mark.parametrize("name,section,entries",
                         BOGUS_SECTION_ENTRIES,
                         ids=[n for n, _, _ in
                              BOGUS_SECTION_ENTRIES])
def test_section_validation_rejects_bogus(name, section,
                                          entries):
    engine = DiffEngine()
    diff = _valid_diff()
    diff[section] = entries
    with pytest.raises(DiffError) as exc:
        engine.validate_diff(diff)
    assert exc.value.failure_class == "malformed_diff_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_diff_record"]


def test_digest_accelerator_never_substitutes_identity():
    """Collision-oriented witness: two records sharing an
    accelerator key but holding distinct canonical identities are
    TWO entries; one identity under two accelerator keys is ONE
    changed witness. Digest equality decides nothing."""
    engine = DiffEngine()
    a = dict(_node(STARTPOS), digest=K1)
    b = dict(_node(KINGS), digest=K1)  # forced shared key
    base = _state(a)
    target = _state(b)
    diff = engine.compute(base, target)
    # equal digests did NOT merge the distinct identities
    assert list(diff["removed"]) == [_identity(a)]
    assert list(diff["added"]) == [_identity(b)]
    # and the reverse: unequal digests did NOT fork one identity
    base2 = _state(_node(STARTPOS))
    target2 = _state(_node(STARTPOS, K2))
    diff2 = engine.compute(base2, target2)
    assert diff2["added"] == {} and diff2["removed"] == {}
    assert list(diff2["changed"]) == [_identity(_node(STARTPOS))]


# -- blocker 3: total compute over hostile inputs ----------------------------

HOSTILE_CONTAINERS = [None, True, 0, 1.5, "text", [], ()]


@pytest.mark.parametrize("hostile", HOSTILE_CONTAINERS)
@pytest.mark.parametrize("slot", ["base", "target"])
def test_compute_total_over_hostile_containers(hostile, slot):
    engine = DiffEngine()
    good = _state(_node(STARTPOS))
    args = {"base": good, "target": good}
    args[slot] = hostile
    with pytest.raises(DiffError) as exc:
        engine.compute(args["base"], args["target"])
    assert exc.value.failure_class == "malformed_diff_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_diff_record"]
    assert exc.value.code in ERROR_ENUM


HOSTILE_ENTRIES = [
    ("none value", lambda: {"somekey": None}),
    ("bool value", lambda: {"somekey": True}),
    ("int value", lambda: {"somekey": 0}),
    ("str value", lambda: {"somekey": "text"}),
    ("list value", lambda: {"somekey": []}),
    ("none key", lambda: {None: _node(STARTPOS)}),
    ("bool key", lambda: {True: _node(STARTPOS)}),
    ("int key", lambda: {0: _node(STARTPOS)}),
    ("key identity mismatch", lambda: {"wrong":
                                       _node(STARTPOS)}),
]


@pytest.mark.parametrize("name,make", HOSTILE_ENTRIES,
                         ids=[n for n, _ in HOSTILE_ENTRIES])
def test_compute_total_over_hostile_entries(name, make):
    engine = DiffEngine()
    good = _state(_node(KINGS))
    for slot in ("base", "target"):
        args = {"base": good, "target": good}
        args[slot] = make()
        with pytest.raises(DiffError) as exc:
            engine.compute(args["base"], args["target"])
        assert exc.value.failure_class == \
            "malformed_diff_record"


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


def test_unknown_identity_in_base():
    engine = DiffEngine()
    base = _state(_node(STARTPOS))
    target = _state()
    diff = engine.compute(base, target)
    # a diff hand-consistent with an EMPTY base: removed entries
    # reference identities absent from it
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, _state())
    assert exc.value.failure_class == "conflicting_base"
    # unknown_identity: craft a diff whose base matches but whose
    # removed entry is not in the base (only reachable by
    # hand-building - a computed diff always matches)
    crafted = {"base_id": state_id(base),
               "target_id": state_id(_state()),
               "added": {},
               "removed": {_identity(_node(KINGS)):
                           _node(KINGS)},
               "changed": {}}
    with pytest.raises(DiffError) as exc:
        engine.apply(crafted, base)
    assert exc.value.failure_class == "unknown_identity"
    assert exc.value.code == FAILURE_MAPPING["unknown_identity"]
    assert exc.value.code in ERROR_ENUM


def test_rollback_bit_identical_on_rejected_apply():
    engine = DiffEngine()
    base = _state(_node(STARTPOS), _node(KINGS))
    target = _state(_node(STARTPOS), _node(AFTER_E4),
                    _node(LEGAL_EP))
    diff = engine.compute(base, target)
    before = copy.deepcopy(base)
    other = _state(_node(STARTPOS))
    with pytest.raises(DiffError):
        engine.apply(diff, other)
    tampered = _state(_node(STARTPOS), _node(KINGS, K1))
    with pytest.raises(DiffError):
        engine.apply(diff, tampered)
    assert base == before
    diff2 = engine.compute(base, target)
    diff2["removed"]["ghost"] = {"bogus": True}
    with pytest.raises(DiffError):
        engine.apply(diff2, base)
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
    add("added validation dropped", ["contract", "sections",
                                     "added", "validation"],
        "values-unvalidated")
    add("removed validation dropped", ["contract", "sections",
                                       "removed", "validation"],
        "keys-untrusted")
    add("changed witness drift", ["contract", "sections",
                                  "changed", "witness"],
        "target-record-only")
    add("changed validation dropped", ["contract", "sections",
                                       "changed", "validation"],
        "equal-witnesses-allowed")
    add("id kind drift", ["contract", "identifiers", "base_id",
                          "kind"], "opaque-state-identifier")
    add("id grammar drift", ["contract", "identifiers",
                             "base_id", "grammar"],
        "^[a-z0-9][a-z0-9._-]{0,63}$")
    add("derivation drift", ["contract", "identifiers",
                             "base_id", "derivation"], "md5-of-ids")
    add("distinctness drift", ["contract", "identifiers",
                               "distinctness"],
        "equal-ids-with-nonempty-diff-allowed")
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
                               "base_check"],
        "touched-records-only")
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
         "diff-or-state-shape-grammar-identity-or-record-"
         "violation"})
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
    add("total compute dropped", ["contract", "properties",
                                  "total_compute"],
        "raw-exceptions-escape")
    add("structural check dropped", ["contract", "properties",
                                     "structural_base_check"],
        "touched-records-only")
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


# -- v3: target-id verification ----------------------------------------------

TAMPERED_ID = "gs1:" + "f" * 64


def _tampered_pairs():
    """Real computed diffs across add/remove/change/mixed shapes,
    each with target_id swapped to a grammar-valid lie."""
    engine = DiffEngine()
    cases = []
    b1 = _state()
    t1 = _state(_node(STARTPOS))
    cases.append(("add-only", b1, engine.compute(b1, t1)))
    b2 = _state(_node(STARTPOS), _node(KINGS))
    t2 = _state(_node(STARTPOS))
    cases.append(("remove-only", b2, engine.compute(b2, t2)))
    b3 = _state(_node(STARTPOS), _node(KINGS, K1))
    t3 = _state(_node(STARTPOS), _node(KINGS, K2))
    cases.append(("change-only", b3, engine.compute(b3, t3)))
    b4 = _state(_node(STARTPOS), _node(KINGS, K1), _node(AFTER_E4))
    t4 = _state(_node(STARTPOS), _node(KINGS, K2), _node(LEGAL_EP))
    cases.append(("mixed", b4, engine.compute(b4, t4)))
    return cases


@pytest.mark.parametrize("name,base,diff", _tampered_pairs(),
                         ids=[c[0] for c in _tampered_pairs()])
def test_apply_rejects_target_id_tampering(name, base, diff):
    """A corrupted/malicious diff that passes every check but lies
    about its target is rejected AFTER staging, BEFORE return:
    typed divergent_target, base bit-identical."""
    engine = DiffEngine()
    tampered = copy.deepcopy(diff)
    tampered["target_id"] = TAMPERED_ID
    engine.validate_diff(tampered)  # grammar-valid lie passes
    before = copy.deepcopy(base)
    with pytest.raises(DiffError) as exc:
        engine.apply(tampered, base)
    assert exc.value.failure_class == "divergent_target"
    assert exc.value.code == FAILURE_MAPPING["divergent_target"]
    assert exc.value.code in ERROR_ENUM
    assert base == before


def test_noop_diff_with_unequal_ids_rejected():
    """An empty diff claiming distinct base/target ids is a lie
    about the target: rejected by validate_diff itself."""
    engine = DiffEngine()
    state = _state(_node(STARTPOS))
    diff = {"base_id": state_id(state), "target_id": TAMPERED_ID,
            "added": {}, "removed": {}, "changed": {}}
    before = copy.deepcopy(state)
    with pytest.raises(DiffError) as exc:
        engine.apply(diff, state)
    assert exc.value.failure_class == "divergent_target"
    assert state == before


def test_computed_diffs_land_on_target_and_reverse_on_base():
    """Converse positives: every computed diff's APPLIED result
    digest equals its target_id, and every reversed diff applied
    to the target lands exactly on the forward diff's base_id."""
    engine = DiffEngine()
    records = [_node(STARTPOS), _node(AFTER_E4), _node(KINGS),
               _node(LEGAL_EP)]
    for perm in itertools.permutations(records, 3):
        base = _state(*perm[:2])
        target = _state(perm[1], perm[2])
        forward = engine.compute(base, target)
        applied = engine.apply(forward, base)
        assert state_id(applied) == forward["target_id"]
        reverse = engine.compute(target, base)
        landed = engine.apply(reverse, target)
        assert landed == base
        assert state_id(landed) == reverse["target_id"] == \
            forward["base_id"]


def test_mutant_apply_skipping_target_check_accepts_lies():
    """Behavioral mutant: the v2 apply returned the staged copy
    WITHOUT target verification - a target_id lie passed every
    gate and applied. Pinned to prove target verification is
    load-bearing. Counter-test: the real apply rejects."""
    def mutant_apply(engine, diff, base):
        engine.validate_diff(diff)
        staged = copy.deepcopy(base)
        for key in diff["removed"]:
            del staged[key]
        for key, witness in diff["changed"].items():
            staged[key] = copy.deepcopy(witness["target"])
        for key, rec in diff["added"].items():
            staged[key] = copy.deepcopy(rec)
        return staged  # mutant: no target check

    engine = DiffEngine()
    base = _state()
    target = _state(_node(STARTPOS))
    diff = engine.compute(base, target)
    tampered = copy.deepcopy(diff)
    tampered["target_id"] = TAMPERED_ID
    applied = mutant_apply(engine, tampered, base)
    assert state_id(applied) != tampered["target_id"]  # the lie
    with pytest.raises(DiffError) as exc:
        engine.apply(tampered, base)
    assert exc.value.failure_class == "divergent_target"
