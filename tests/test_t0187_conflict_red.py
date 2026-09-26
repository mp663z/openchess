"""T0187 permanent red battery for graph-conflict detectors.

The battery drives EVERY row of the T0186 conformance fixture
(tests/fixtures/conflict/cases.json) through a detector class and
proves happy, boundary, malformed and rollback behavior:

- happy/boundary: the pinned result exactly, deterministic over
  fresh copies, left-right symmetric, and the EXACT objects handed
  to detect are never mutated;
- malformed: the original input rejects with the pinned failure
  class and its mapped error code, the received objects stay
  byte-identical, and the declared minimal repair is accepted with
  ids derived from the repaired content;
- rollback: a rejection leaves the received objects byte-identical
  and the follow-up valid detect on the same detector instance
  returns the pinned result.

Following the repository's standalone-red convention (T0151,
T0178), the battery is permanently GREEN against the contract-
derived reference detector from tests.test_t0185_conflict_contract
and every black-box mutant below is RED. T0188 adds graph/conflict.py
and switches the binding by replacing ONLY the two binding lines
below with ``from graph.conflict import ConflictDetector,
ConflictError``; no assertion changes.

Fixture closure: the rows this battery executes are closed by
ordered per-section manifests, per-row semantic pins and closed
per-tag defect-locus/edge checks over the ORIGINAL row data (an
unknown tag raises), and a ROW_DIGESTS whole-row sha256 table
whose key set equals the manifests. test_closure_kills_
substitution_mutants proves substitution mutants are killed with
the digest table live AND with digests neutralized."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0185_conflict_contract as _reference  # noqa: E402
from tests.test_t0185_conflict_contract import (  # noqa: E402
    K1,
    K2,
    _identity,
    _node,
    state_id,
)
from tools.conflict_contract_lint import (  # noqa: E402
    FAILURE_MAPPING,
)

# -- binding switch: T0188 replaces ONLY these two lines with the
# graph.conflict production names; _identity/state_id/
# FAILURE_MAPPING stay contract-derived.
from graph.conflict import ConflictDetector, ConflictError  # noqa: E402  # isort: skip

FIXTURE = (Path(__file__).parent / "fixtures" / "conflict"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECORD_FIELDS = frozenset({"variant", "digest", "snapshot_fen"})
SIDES = ("base", "left", "right")
MCR = "malformed_conflict_record"
DB = "divergent_base"
_DIGEST_RE = re.compile(r"^pdv1:[0-9a-f]{64}$")
_VARIANT_GRAMMAR = re.compile(r"^[a-z][a-z0-9_-]*$")
REGISTERED_VARIANTS = frozenset({"standard"})

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, scenario tag, (|base|, |left|, |right|),
#                  sorted witness kinds)
HAPPY_MANIFEST = (
    ("disjoint-edits-compatible", "disjoint", (1, 2, 2),
     (1, 0, 0, 1, 0, 0), ()),
    ("identical-outcomes-compatible", "identical-outcomes",
     (1, 2, 2),
     (1, 0, 0, 1, 0, 0), ()),
    ("both-changed-differently", "both-changed", (1, 1, 1),
     (0, 0, 1, 0, 0, 1),
     ("both_changed_differently",)),
    ("changed-vs-removed-left-changed", "cvr-left", (2, 2, 1),
     (0, 0, 1, 0, 1, 0),
     ("changed_vs_removed",)),
    ("changed-vs-removed-right-changed", "cvr-right", (2, 1, 2),
     (0, 1, 0, 0, 0, 1),
     ("changed_vs_removed",)),
    ("added-differently", "added-differently", (0, 1, 1),
     (1, 0, 0, 1, 0, 0),
     ("added_differently",)),
)
BOUNDARY_MANIFEST = (
    ("both-removed-byte-identical-compatible", "both-removed",
     (2, 1, 1),
     (0, 1, 0, 0, 1, 0), ()),
    ("both-added-identically-compatible", "both-added", (0, 1, 1),
     (1, 0, 0, 1, 0, 0),
     ()),
    ("equal-outcomes-despite-base-digest-difference",
     "equal-outcomes", (1, 1, 1),
     (0, 0, 1, 0, 0, 1), ()),
    ("mixed-multi-identity-conflicts", "mixed-multi", (3, 3, 2),
     (1, 1, 1, 0, 1, 1),
     ("both_changed_differently",)),
    ("single-record-states-conflict", "single-record", (1, 1, 1),
     (0, 0, 1, 0, 0, 1),
     ("both_changed_differently",)),
)
# malformed: (name, defect-locus tag, failure class)
MALFORMED_MANIFEST = (
    ("state-not-mapping", "base-not-mapping", MCR),
    ("record-not-mapping", "left-record-not-mapping", MCR),
    ("record-extra-field", "left-record-extra-field", MCR),
    ("record-missing-field", "left-record-missing-digest", MCR),
    ("record-non-str-field", "left-variant-not-str", MCR),
    ("record-unknown-variant", "left-variant-unregistered", MCR),
    ("record-invalid-fen", "left-fen-illegal", MCR),
    ("record-bad-digest-grammar", "left-digest-grammar", MCR),
    ("record-identity-key-mismatch", "left-key-not-identity", MCR),
    ("record-noncanonical-clocks", "left-clocks-noncanonical", MCR),
    ("left-equals-base-divergent-base", "left-equals-base", DB),
)
# rollback: (name, rejection-locus tag, failure class)
ROLLBACK_MANIFEST = (
    ("rejected-malformed-then-valid-detect", "left-digest-grammar",
     MCR),
    ("rejected-divergent-base-then-valid-detect",
     "left-equals-base", DB),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:disjoint-edits-compatible":
        "033db9d59a1dd8819c36880324aec77cf38be7e37c24047279794995f4f74879",
    "happy:identical-outcomes-compatible":
        "85f3ad5a83c106de083e0df80fd6aa1ad66cecb28078d29159de91da7830f731",
    "happy:both-changed-differently":
        "57676a29e6178b5c9ed634d9e0e48527c6f0bb1c8dbbca131c34cbc41687c902",
    "happy:changed-vs-removed-left-changed":
        "fe5946922930ca1ceb5fff45dbf2bfe1f890df3ce44359297cd6f998ceb1491a",
    "happy:changed-vs-removed-right-changed":
        "3cecb8fd3570e03285deaad4da123a5ef11cda746576b4698014fd3e9c6b3650",
    "happy:added-differently":
        "19178619d6dec0d7bab9d56b3d1538548080f53421add09d21df31bdab4226fc",
    "boundary:both-removed-byte-identical-compatible":
        "26745cf9a879aa2da7a776bf30e62a8638953a702ac1f1e4cc807f4240a9cb91",
    "boundary:both-added-identically-compatible":
        "26cecfc27502718ba362b9a40b4c274cbeda238ca2f9a266de1992b2b68d6ff3",
    "boundary:equal-outcomes-despite-base-digest-difference":
        "a985d19bbe0c292dab5d17b4f7c90d6fc257c0a13fbe5822e6caafc86e828ff8",
    "boundary:mixed-multi-identity-conflicts":
        "872c5b22f7ed5aa40922708051cab97c6a49dc85bfb163b027b3a94c058da7c8",
    "boundary:single-record-states-conflict":
        "d8a18290e3c5e577b101a39d7524cb2a3319392abe896acbaca5904d161991bc",
    "malformed:state-not-mapping":
        "65a012817083ac809d68c45385c9592f6d5d022e5ef92f79d19c171548ffe0c4",
    "malformed:record-not-mapping":
        "c8d4fbb05e29b5fae0a2368851c2d30dd1b7f4a125fafbf0c2106da349d0ce58",
    "malformed:record-extra-field":
        "d948a49c69d52e8c7fefd74f9408af74303bb551f56d0467d5ab6bcdd991cd1b",
    "malformed:record-missing-field":
        "de1054a5852e231ebd8ae18e03a08833d25f24894f3cb56503020441fee12063",
    "malformed:record-non-str-field":
        "021f3ea11043e5480ebc1f11a8258788b2807c8db1c152ae3986b74bc0d89cd7",
    "malformed:record-unknown-variant":
        "d6a1f6ce61fc6a82b0e7508408d9fbf342327becb10c8983b1aaef2a61caf2e8",
    "malformed:record-invalid-fen":
        "89fcc13b9387d4b9ce4360b5137b170e0857cc25fb2344ac27eb13ef85f8eb49",
    "malformed:record-bad-digest-grammar":
        "47b453eca57d7a185635040288e42defc06465c1ee67dca207a1b4af8fee29a1",
    "malformed:record-identity-key-mismatch":
        "bdb129f58317e9ffe4e310b96b0ed8fa5d1bec1a2084c2f7b299bf9d07e15ff8",
    "malformed:record-noncanonical-clocks":
        "e8debf8eac6604a19393cbf305b236fe1628fbd6681728d6ad451a59c5dbd3b5",
    "malformed:left-equals-base-divergent-base":
        "1aa09f563e383aa6607fb2ce408aeea528a4c892d1abaa41cdb07f361b345a6c",
    "rollback:rejected-malformed-then-valid-detect":
        "ba4dd5de242c5f2d5f96aab0efe33b066f97797266446b68d37b26b9478f3f36",
    "rollback:rejected-divergent-base-then-valid-detect":
        "1b7b3ddf8a16908b4a397db1a12413001574e8834691b41ff0bc18e88994ab51",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _section_of(row, cases=None):
    for section, rows in (cases or CASES).items():
        if isinstance(rows, list) and any(r is row for r in rows):
            return section
    for section, manifest in MANIFESTS.items():
        if row["name"] in {m[0] for m in manifest}:
            return section
    raise AssertionError(f"row {row.get('name')!r} has no section")


# -- per-tag semantic checks over ORIGINAL row data ---------------------------
def _edits(base, other):
    ids_b, ids_o = set(base), set(other)
    return (ids_o - ids_b, ids_b - ids_o,
            {i for i in ids_b & ids_o if base[i] != other[i]})


def _safe_identity(record):
    """Derived canonical identity, or None when the record cannot
    be derived (total over hostile rows)."""
    try:
        key = json.dumps(record, sort_keys=True)
    except (TypeError, ValueError):
        key = None
    if key is not None and key in _IDENTITY_MEMO:
        return _IDENTITY_MEMO[key]
    try:
        derived = _identity(record)
    except Exception:  # noqa: BLE001
        derived = None
    if key is not None:
        _IDENTITY_MEMO[key] = derived
    return derived


_IDENTITY_MEMO = {}


def _is_valid_state(state):
    return isinstance(state, dict) and all(
        type(k) is str and isinstance(v, dict)
        and set(v) == RECORD_FIELDS
        and all(type(v[f]) is str for f in RECORD_FIELDS)
        and _DIGEST_RE.fullmatch(v["digest"])
        and _safe_identity(v) == k
        for k, v in state.items())


def _check_scenario(case, tag, sizes, edit_counts, kinds):
    """One closed branch per happy/boundary scenario tag; an
    unknown tag raises."""
    name = case["name"]
    base, left, right = case["base"], case["left"], case["right"]
    for side in SIDES:
        assert _is_valid_state(case[side]), (name, side)
    assert (len(base), len(left), len(right)) == sizes, name
    expect = case["expect"]
    assert set(expect) == {"base_id", "left_id", "right_id",
                           "conflicts"}, name
    assert expect["base_id"] == state_id(base), name
    assert expect["left_id"] == state_id(left), name
    assert expect["right_id"] == state_id(right), name
    conflicts = expect["conflicts"]
    assert tuple(sorted(w["kind"] for w in conflicts.values())) \
        == kinds, name
    a_l, r_l, c_l = _edits(base, left)
    a_r, r_r, c_r = _edits(base, right)
    e_l, e_r = a_l | r_l | c_l, a_r | r_r | c_r
    assert tuple(map(len, (a_l, r_l, c_l, a_r, r_r, c_r))) == \
        edit_counts, name
    if tag == "disjoint":
        assert e_l and e_r and not (e_l & e_r), name
    elif tag == "identical-outcomes":
        assert left == right != base, name
    elif tag == "both-changed":
        (key,) = conflicts
        assert key in c_l & c_r, name
        assert conflicts[key]["left"] == left[key], name
        assert conflicts[key]["right"] == right[key], name
    elif tag == "cvr-left":
        (key,) = conflicts
        assert key in c_l & r_r, name
        assert conflicts[key]["left"] == left[key], name
        assert conflicts[key]["right"] is None, name
    elif tag == "cvr-right":
        (key,) = conflicts
        assert key in r_l & c_r, name
        assert conflicts[key]["left"] is None, name
        assert conflicts[key]["right"] == right[key], name
    elif tag == "added-differently":
        (key,) = conflicts
        assert key in a_l & a_r and left[key] != right[key], name
    elif tag == "both-removed":
        assert r_l & r_r and left == right, name
    elif tag == "both-added":
        shared = a_l & a_r
        assert shared and all(left[i] == right[i]
                              for i in shared), name
    elif tag == "equal-outcomes":
        assert set(base) == set(left) == set(right), name
        assert left == right, name
        diff = [i for i in base if base[i] != left[i]]
        assert diff, name
        for i in diff:
            assert base[i]["snapshot_fen"] == \
                left[i]["snapshot_fen"], name
            assert base[i]["variant"] == left[i]["variant"], name
            assert base[i]["digest"] != left[i]["digest"], name
    elif tag == "mixed-multi":
        (key,) = conflicts
        assert key in c_l & c_r, name
        assert len(e_l | e_r) >= 3, name
    elif tag == "single-record":
        (key,) = conflicts
        assert set(base) == set(left) == set(right) == {key}, name
        assert key in c_l & c_r, name
    else:
        raise AssertionError(f"unknown scenario tag {tag!r}")


def _left_defects(case):
    """(key, record) pairs of LEFT that are not in the base."""
    left, base = case["left"], case["base"]
    assert isinstance(left, dict) and isinstance(base, dict)
    return [(k, v) for k, v in left.items()
            if k not in base or base[k] != v]


def _check_locus(case, tag):
    """One closed branch per defect-locus tag: the advertised
    defect is REALLY the only defect, located where the tag says.
    An unknown tag raises."""
    name = case["name"]
    if tag == "base-not-mapping":
        assert not isinstance(case["base"], dict), name
        assert _is_valid_state(case["left"]), name
        assert _is_valid_state(case["right"]), name
        return
    assert _is_valid_state(case["base"]), name
    assert _is_valid_state(case["right"]), name
    if tag == "left-equals-base":
        assert _is_valid_state(case["left"]), name
        assert case["left"] == case["base"], name
        assert state_id(case["left"]) == state_id(case["base"]), \
            name
        assert case["right"] != case["base"], name
        return
    (key, rec), = _left_defects(case)
    if tag == "left-record-not-mapping":
        assert not isinstance(rec, dict), name
        return
    assert isinstance(rec, dict), name
    if tag == "left-record-extra-field":
        assert set(rec) > RECORD_FIELDS, name
    elif tag == "left-record-missing-digest":
        assert set(rec) == RECORD_FIELDS - {"digest"}, name
    else:
        assert set(rec) == RECORD_FIELDS, name
        variant, fen, digest = (rec["variant"], rec["snapshot_fen"],
                                rec["digest"])
        if tag == "left-variant-not-str":
            assert type(variant) is not str, name
        elif tag == "left-variant-unregistered":
            assert type(variant) is str, name
            assert _VARIANT_GRAMMAR.fullmatch(variant), name
            assert variant not in REGISTERED_VARIANTS, name
        elif tag == "left-fen-illegal":
            assert variant in REGISTERED_VARIANTS, name
            assert len(fen.split(" ")[0].split("/")) != 8, name
        elif tag == "left-digest-grammar":
            assert variant in REGISTERED_VARIANTS, name
            assert _DIGEST_RE.fullmatch(digest) is None, name
            assert _safe_identity(rec) == key, name
        elif tag == "left-key-not-identity":
            assert variant in REGISTERED_VARIANTS, name
            assert len(fen.split(" ")[0].split("/")) == 8, name
            assert _DIGEST_RE.fullmatch(digest), name
            assert _safe_identity(rec) not in (key, None), name
        elif tag == "left-clocks-noncanonical":
            assert _DIGEST_RE.fullmatch(digest), name
            assert fen.split(" ")[4:] != ["0", "1"], name
            assert _safe_identity(rec) == key, name
        else:
            raise AssertionError(f"unknown defect-locus tag {tag!r}")


def _check_row(section, case, meta):
    if section in ("happy", "boundary"):
        _, tag, sizes, edit_counts, kinds = meta
        _check_scenario(case, tag, sizes, edit_counts, kinds)
        return
    _, tag, failure = meta
    assert case["expect_failure"] == failure, case["name"]
    assert failure in FAILURE_MAPPING, case["name"]
    _check_locus(case, tag)
    if section == "malformed":
        rep = case["minimal_repair"]
        which = next(iter(rep.values()))["which"]
        assert which == ("base" if tag == "base-not-mapping"
                         else "left"), case["name"]
        return
    # rollback: the follow-up is the same request with LEFT fixed
    assert case["then_base"] == case["base"], case["name"]
    assert case["then_right"] == case["right"], case["name"]
    assert case["then_left"] != case["left"], case["name"]
    if tag == "left-equals-base":
        # the follow-up left diverges from base with base retained
        assert set(case["base"]) < set(case["then_left"]), \
            case["name"]
        assert all(case["then_left"][k] == v
                   for k, v in case["base"].items()), case["name"]
    else:
        # the follow-up left is the left with ONLY the defect
        # record replaced (same key set minus the defect locus)
        (dkey, _), = _left_defects(case)
        kept = set(case["left"]) - {dkey}
        assert kept <= set(case["then_left"]), case["name"]
        assert len(case["then_left"]) == len(case["left"]), \
            case["name"]
        assert all(case["then_left"][k] == case["left"][k]
                   for k in kept), case["name"]
    assert _is_valid_state(case["then_left"]), case["name"]
    assert state_id(case["then_left"]) != \
        state_id(case["then_base"]), case["name"]
    assert case["expect"]["left_id"] == \
        state_id(case["then_left"]), case["name"]


def _validate_closure(cases):
    """Ordered names, whole-row digests and per-tag semantic
    checks for every executed section; the digest table's key set
    equals the manifests."""
    assert set(ROW_DIGESTS) == {
        f"{s}:{m[0]}" for s, man in MANIFESTS.items() for m in man}
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == \
            [m[0] for m in manifest], section
        for row, meta in zip(rows, manifest, strict=True):
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS[label] == _row_digest(row), label
            _check_row(section, row, meta)


# -- the battery ---------------------------------------------------------------
def _swap_expect(expect):
    return {"base_id": expect["base_id"],
            "left_id": expect["right_id"],
            "right_id": expect["left_id"],
            "conflicts": {
                k: {"kind": w["kind"],
                    "left": copy.deepcopy(w["right"]),
                    "right": copy.deepcopy(w["left"])}
                for k, w in expect["conflicts"].items()}}


def _fresh(case, prefix=""):
    return tuple(copy.deepcopy(case[prefix + s]) for s in SIDES)


def _snap(obj):
    """Exact flat snapshot: type, id, length and key order of every
    container, key identity (text for exact str keys, id otherwise),
    and scalar type+value (huge ints by bit length and low bits).
    Iterative, cycle-safe, never hashes, compares or reprs a
    caller-owned key or subclass instance."""
    out, seen, stack = [], set(), [obj]
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is dict or kind is list or kind is tuple:
            if id(node) in seen:
                out.append(("seen", id(node)))
                continue
            seen.add(id(node))
            if kind is dict:
                items = list(dict.items(node))
                out.append(("dict", id(node), len(items)))
                for key, value in reversed(items):
                    stack.append(value)
                    stack.append(_KeyMark(key))
            else:
                values = list(kind.__iter__(node))
                out.append((kind.__name__, id(node), len(values)))
                stack.extend(reversed(values))
        elif kind is _KeyMark:
            key = node.key
            out.append(("key", key if type(key) is str
                        else (type(key).__name__, id(key))))
        elif kind is int:
            out.append(("int", id(node), node.bit_length(),
                        node & 0xFFFF))
        elif kind in (str, bool, float, type(None)):
            out.append((kind.__name__, node))
        else:
            out.append(("object", kind.__name__, id(node)))
    return out


class _KeyMark:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key


def _untouched(inputs, snap):
    """Exact snapshot of every input unchanged: values, types,
    object identities, nesting and key order."""
    return _snap(inputs) == snap


def _same_ordered(result, expect):
    """Order-sensitive equality: dict equality ignores order, so the
    conflicts map is also compared as an ordered item list."""
    return (result == expect
            and isinstance(result, dict)
            and isinstance(result.get("conflicts"), dict)
            and list(result["conflicts"].items())
            == list(expect["conflicts"].items()))


def _checked_detect(detector, base, left, right):
    """(result, untouched) for one successful detect call."""
    inputs = (base, left, right)
    snap = _snap(inputs)
    result = detector.detect(base, left, right)
    return result, (_untouched(inputs, snap)
                    and _not_aliased(result, inputs))


def _not_aliased(result, inputs):
    """No container reachable from the result is shared with the
    caller's inputs: mutating a returned witness can never reach
    the inputs, nor vice versa."""
    if not isinstance(result, dict):
        return True
    inner = {entry[1] for entry in _snap(inputs)
             if entry[0] in ("dict", "list", "tuple")}
    return not any(entry[0] in ("dict", "list", "tuple")
                   and entry[1] in inner for entry in _snap(result))


def _detect_ok(cls, case, prefix="", detector=None):
    """Pinned result (order-sensitive), determinism, symmetry, and
    every input untouched by value and identity on every call."""
    expect = case["expect"]
    detector = detector or cls()
    for det, (b, lft, r), want in (
            (detector, _fresh(case, prefix), expect),
            (cls(), _fresh(case, prefix), expect),
            (cls(), _fresh(case, prefix), None)):
        if want is None:
            b, lft, r, want = b, r, lft, _swap_expect(expect)
        result, untouched = _checked_detect(det, b, lft, r)
        if not (untouched and _same_ordered(result, want)):
            return False
    return True


def _rejects(detector, case):
    inputs = _fresh(case)
    snap = _snap(inputs)
    try:
        detector.detect(*inputs)
    except ConflictError as error:
        return (error.failure_class == case["expect_failure"]
                and error.code == FAILURE_MAPPING[
                    case["expect_failure"]]
                and _untouched(inputs, snap))
    return False


def _repaired(case):
    rep = case["minimal_repair"]
    out = {s: copy.deepcopy(case[s]) for s in SIDES}
    if "replace_state" in rep:
        form = rep["replace_state"]
        out[form["which"]] = copy.deepcopy(form["state"])
    else:
        form = rep["replace_record"]
        state = out[form["which"]]
        del state[form["identity"]]
        state[_identity(form["record"])] = copy.deepcopy(
            form["record"])
    return out


def _malformed_ok(cls, case):
    if not _rejects(cls(), case):
        return False
    fixed = _repaired(case)
    result, untouched = _checked_detect(
        cls(), fixed["base"], fixed["left"], fixed["right"])
    want = _reference.ConflictDetector().detect(
        *copy.deepcopy((fixed["base"], fixed["left"], fixed["right"])))
    return untouched and _same_ordered(result, want) and (
        result["base_id"], result["left_id"],
        result["right_id"]) == tuple(state_id(fixed[s])
                                     for s in SIDES)


def _rollback_ok(cls, case):
    detector = cls()
    return (_rejects(detector, case)
            and _detect_ok(cls, case, "then_", detector=detector))


# -- in-file canonical-order probe --------------------------------------------
# The fixture has at most one conflict per row, so conflict ORDER is
# proven here: three identities, all both-changed, inserted in a
# different order in base, left and right, none of them canonical
# (sorted identity) order, and none equal to the reversed canonical
# order.
_FEN_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_FEN_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_FEN_KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
CANONICAL_ORDER = [
    "('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')",
    "('standard', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR', "
    "'b', 'KQkq', '-')",
    "('standard', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', "
    "'w', 'KQkq', '-')",
]


def _order_states():
    def state(order, digest=None):
        return {_identity(_node(f)): _node(f, digest) for f in order}
    base = state((_FEN_KINGS, _FEN_START, _FEN_E4))
    left = state((_FEN_E4, _FEN_START, _FEN_KINGS), K1)
    right = state((_FEN_START, _FEN_KINGS, _FEN_E4), K2)
    return base, left, right


ORDER_EXPECT = {
    "base_id":
        "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
    "left_id":
        "gs1:591e4ac23fd58c0306938a38efb658bc784b4e3396ee2546db5db89be6f6422c",
    "right_id":
        "gs1:15b455261e5d51d566e48d17c41159f6183994a20d98a91d8b95118f9643c37f",
    "conflicts": {
        "('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')": {
            "kind": "both_changed_differently",
            "left": {
                "digest":
                    "pdv1:1111111111111111111111111111111111111111111111111111111111111111",
                "snapshot_fen":
                    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "variant":
                    "standard",
            },
            "right": {
                "digest":
                    "pdv1:2222222222222222222222222222222222222222222222222222222222222222",
                "snapshot_fen":
                    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "variant":
                    "standard",
            },
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR', 'b', 'KQkq', '-')": {
            "kind": "both_changed_differently",
            "left": {
                "digest":
                    "pdv1:1111111111111111111111111111111111111111111111111111111111111111",
                "snapshot_fen":
                    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                "variant":
                    "standard",
            },
            "right": {
                "digest":
                    "pdv1:2222222222222222222222222222222222222222222222222222222222222222",
                "snapshot_fen":
                    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                "variant":
                    "standard",
            },
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', 'KQkq', '-')": {
            "kind": "both_changed_differently",
            "left": {
                "digest":
                    "pdv1:1111111111111111111111111111111111111111111111111111111111111111",
                "snapshot_fen":
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "variant":
                    "standard",
            },
            "right": {
                "digest":
                    "pdv1:2222222222222222222222222222222222222222222222222222222222222222",
                "snapshot_fen":
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "variant":
                    "standard",
            },
        },
    },
}


def _order_ok(cls):
    """Both argument orders return the WHOLE pinned result - ids,
    canonical conflict order and every witness's kind/left/right -
    order-sensitively, with inputs untouched."""
    for swap in (False, True):
        base, left, right = _order_states()
        want = ORDER_EXPECT
        if swap:
            left, right = right, left
            want = _swap_expect(ORDER_EXPECT)
        result, untouched = _checked_detect(cls(), base, left, right)
        if not (untouched and _same_ordered(result, want)):
            return False
    return True


ORDER_LABEL = "probe:canonical-order"


# -- totality probes (closed, ordered) ----------------------------------------
class SK(str):
    """str-subclass map key whose comparisons raise."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile str __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile str __ne__")


class HK:
    """Non-str map key whose hash collides with a real identity
    key and whose __eq__ raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


def _probe_triple():
    """A valid disjoint triple; every probe corrupts one slot."""
    start, e4, kings = (_node(_FEN_START), _node(_FEN_E4),
                        _node(_FEN_KINGS))
    return ({_identity(start): start},
            {_identity(start): start, _identity(e4): e4},
            {_identity(start): start, _identity(kings): kings})


def _target_key(slot, state):
    """The record each slot's record-level probes corrupt."""
    return sorted(state)[-1] if slot != "base" else next(iter(state))


def _rekey(state, key, new_key):
    return {(new_key if k == key else k): v for k, v in state.items()}


def _self_ref():
    loop = []
    loop.append(loop)
    return loop


def _deep():
    node = []
    for _ in range(100_000):
        node = [node]
    return node


_HOSTILE_STATES = (("none", None), ("true", True), ("zero", 0),
                   ("float", 1.5), ("text", "text"), ("list", []))
_HOSTILE_KEYS = (("none", None), ("true", True), ("zero", 0))
_HOSTILE_RECORDS = (("none", None), ("zero", 0), ("list", []),
                    ("text", "text"))
_HOSTILE_FIELD_VALUES = (("none", None), ("true", True), ("zero", 0),
                         ("float", 1.5), ("list", []), ("dict", {}),
                         ("empty", ""))
_FIELDS = ("variant", "digest", "snapshot_fen")


def _probe_builders():
    """name -> builder returning a fresh (base, left, right)."""
    out = {}

    def put(name, slot, fn):
        def build():
            triple = list(_probe_triple())
            i = SIDES.index(slot)
            triple[i] = fn(triple[i], slot)
            return tuple(triple)
        out[name] = build

    for slot in SIDES:
        for label, value in _HOSTILE_STATES:
            put(f"{slot}-state-{label}", slot,
                lambda st, sl, v=value: copy.deepcopy(v))
        for label, value in _HOSTILE_KEYS:
            put(f"{slot}-key-{label}", slot,
                lambda st, sl, v=value: _rekey(
                    st, _target_key(sl, st), v))
        put(f"{slot}-key-str-subclass", slot,
            lambda st, sl: _rekey(st, _target_key(sl, st),
                                  SK(_target_key(sl, st))))
        put(f"{slot}-key-colliding-hash", slot,
            lambda st, sl: _rekey(st, _target_key(sl, st),
                                  HK(_target_key(sl, st))))
        for label, value in _HOSTILE_RECORDS:
            put(f"{slot}-record-{label}", slot,
                lambda st, sl, v=value: dict(
                    st, **{_target_key(sl, st): copy.deepcopy(v)}))
        for field in _FIELDS:
            for label, value in _HOSTILE_FIELD_VALUES:
                put(f"{slot}-field-{field}-{label}", slot,
                    lambda st, sl, f=field, v=value: _with_field(
                        st, sl, f, copy.deepcopy(v)))
            put(f"{slot}-field-{field}-missing", slot,
                lambda st, sl, f=field: _without_field(st, sl, f))
        put(f"{slot}-field-self-referential", slot,
            lambda st, sl: _with_field(st, sl, "digest", _self_ref()))
        put(f"{slot}-field-deep-nested", slot,
            lambda st, sl: _with_field(st, sl, "snapshot_fen",
                                       _deep()))
        put(f"{slot}-field-huge-int", slot,
            lambda st, sl: _with_field(st, sl, "digest", 10 ** 5000))
        for field in _FIELDS:
            for label, key_cls in (("str-subclass", SK),
                                   ("colliding-hash", HK)):
                put(f"{slot}-record-key-{field}-{label}", slot,
                    lambda st, sl, f=field, c=key_cls: _rekey_field(
                        st, sl, f, c(f)))
        put(f"{slot}-record-key-none", slot,
            lambda st, sl: _with_field(st, sl, None, "x"))
        put(f"{slot}-record-extra-field", slot,
            lambda st, sl: _with_field(st, sl, "label", "x"))
        put(f"{slot}-state-dict-subclass", slot,
            lambda st, sl: _DictSub(st))
        put(f"{slot}-record-dict-subclass", slot,
            lambda st, sl: dict(st, **{_target_key(sl, st): _DictSub(
                st[_target_key(sl, st)])}))
    return out


class _DictSub(dict):
    """dict subclass whose accessors raise: states and records must
    be exact dicts."""

    def items(self):
        raise RuntimeError("hostile items")

    def keys(self):
        raise RuntimeError("hostile keys")

    def __getitem__(self, key):
        raise RuntimeError("hostile __getitem__")


def _with_field(state, slot, field, value):
    key = _target_key(slot, state)
    rec = dict(state[key])
    rec[field] = value
    out = dict(state)
    out[key] = rec
    return out


def _rekey_field(state, slot, field, new_key):
    key = _target_key(slot, state)
    out = dict(state)
    out[key] = _rekey(state[key], field, new_key)
    return out


def _without_field(state, slot, field):
    key = _target_key(slot, state)
    rec = {k: v for k, v in state[key].items() if k != field}
    return dict(state, **{key: rec})


PROBES = _probe_builders()
PROBE_MANIFEST = (
    "base-state-none",
    "base-state-true",
    "base-state-zero",
    "base-state-float",
    "base-state-text",
    "base-state-list",
    "base-key-none",
    "base-key-true",
    "base-key-zero",
    "base-key-str-subclass",
    "base-key-colliding-hash",
    "base-record-none",
    "base-record-zero",
    "base-record-list",
    "base-record-text",
    "base-field-variant-none",
    "base-field-variant-true",
    "base-field-variant-zero",
    "base-field-variant-float",
    "base-field-variant-list",
    "base-field-variant-dict",
    "base-field-variant-empty",
    "base-field-variant-missing",
    "base-field-digest-none",
    "base-field-digest-true",
    "base-field-digest-zero",
    "base-field-digest-float",
    "base-field-digest-list",
    "base-field-digest-dict",
    "base-field-digest-empty",
    "base-field-digest-missing",
    "base-field-snapshot_fen-none",
    "base-field-snapshot_fen-true",
    "base-field-snapshot_fen-zero",
    "base-field-snapshot_fen-float",
    "base-field-snapshot_fen-list",
    "base-field-snapshot_fen-dict",
    "base-field-snapshot_fen-empty",
    "base-field-snapshot_fen-missing",
    "base-field-self-referential",
    "base-field-deep-nested",
    "base-field-huge-int",
    "base-record-key-variant-str-subclass",
    "base-record-key-variant-colliding-hash",
    "base-record-key-digest-str-subclass",
    "base-record-key-digest-colliding-hash",
    "base-record-key-snapshot_fen-str-subclass",
    "base-record-key-snapshot_fen-colliding-hash",
    "base-record-key-none",
    "base-record-extra-field",
    "base-state-dict-subclass",
    "base-record-dict-subclass",
    "left-state-none",
    "left-state-true",
    "left-state-zero",
    "left-state-float",
    "left-state-text",
    "left-state-list",
    "left-key-none",
    "left-key-true",
    "left-key-zero",
    "left-key-str-subclass",
    "left-key-colliding-hash",
    "left-record-none",
    "left-record-zero",
    "left-record-list",
    "left-record-text",
    "left-field-variant-none",
    "left-field-variant-true",
    "left-field-variant-zero",
    "left-field-variant-float",
    "left-field-variant-list",
    "left-field-variant-dict",
    "left-field-variant-empty",
    "left-field-variant-missing",
    "left-field-digest-none",
    "left-field-digest-true",
    "left-field-digest-zero",
    "left-field-digest-float",
    "left-field-digest-list",
    "left-field-digest-dict",
    "left-field-digest-empty",
    "left-field-digest-missing",
    "left-field-snapshot_fen-none",
    "left-field-snapshot_fen-true",
    "left-field-snapshot_fen-zero",
    "left-field-snapshot_fen-float",
    "left-field-snapshot_fen-list",
    "left-field-snapshot_fen-dict",
    "left-field-snapshot_fen-empty",
    "left-field-snapshot_fen-missing",
    "left-field-self-referential",
    "left-field-deep-nested",
    "left-field-huge-int",
    "left-record-key-variant-str-subclass",
    "left-record-key-variant-colliding-hash",
    "left-record-key-digest-str-subclass",
    "left-record-key-digest-colliding-hash",
    "left-record-key-snapshot_fen-str-subclass",
    "left-record-key-snapshot_fen-colliding-hash",
    "left-record-key-none",
    "left-record-extra-field",
    "left-state-dict-subclass",
    "left-record-dict-subclass",
    "right-state-none",
    "right-state-true",
    "right-state-zero",
    "right-state-float",
    "right-state-text",
    "right-state-list",
    "right-key-none",
    "right-key-true",
    "right-key-zero",
    "right-key-str-subclass",
    "right-key-colliding-hash",
    "right-record-none",
    "right-record-zero",
    "right-record-list",
    "right-record-text",
    "right-field-variant-none",
    "right-field-variant-true",
    "right-field-variant-zero",
    "right-field-variant-float",
    "right-field-variant-list",
    "right-field-variant-dict",
    "right-field-variant-empty",
    "right-field-variant-missing",
    "right-field-digest-none",
    "right-field-digest-true",
    "right-field-digest-zero",
    "right-field-digest-float",
    "right-field-digest-list",
    "right-field-digest-dict",
    "right-field-digest-empty",
    "right-field-digest-missing",
    "right-field-snapshot_fen-none",
    "right-field-snapshot_fen-true",
    "right-field-snapshot_fen-zero",
    "right-field-snapshot_fen-float",
    "right-field-snapshot_fen-list",
    "right-field-snapshot_fen-dict",
    "right-field-snapshot_fen-empty",
    "right-field-snapshot_fen-missing",
    "right-field-self-referential",
    "right-field-deep-nested",
    "right-field-huge-int",
    "right-record-key-variant-str-subclass",
    "right-record-key-variant-colliding-hash",
    "right-record-key-digest-str-subclass",
    "right-record-key-digest-colliding-hash",
    "right-record-key-snapshot_fen-str-subclass",
    "right-record-key-snapshot_fen-colliding-hash",
    "right-record-key-none",
    "right-record-extra-field",
    "right-state-dict-subclass",
    "right-record-dict-subclass",
)


def _totality_ok(cls, name):
    inputs = PROBES[name]()
    snap = _snap(inputs)
    try:
        cls().detect(*inputs)
    except ConflictError as error:
        return (error.failure_class == MCR
                and error.code == FAILURE_MAPPING[MCR]
                and _snap(inputs) == snap)
    except BaseException:  # noqa: BLE001 - a raw escape is the defect
        return False
    return False

_RUNNERS = {"happy": _detect_ok, "boundary": _detect_ok,
            "malformed": _malformed_ok, "rollback": _rollback_ok}


def _probe(cls, cases=None, executed=None, first_only=False,
           only=None):
    """Every manifest row through CLS; returns failing labels
    (only the first when FIRST_ONLY). Untrusted-component
    boundary: BaseException is caught."""
    cases = cases or CASES
    failures = []
    for section, manifest in MANIFESTS.items():
        rows = {r["name"]: r for r in cases[section]}
        for meta in manifest:
            label = f"{section}:{meta[0]}"
            if only is not None and label not in only:
                continue
            try:
                ok = _RUNNERS[section](cls, rows[meta[0]])
            except BaseException as error:  # noqa: BLE001
                failures.append(f"{label}:{type(error).__name__}")
                continue
            if executed is not None:
                executed.append(label)
            if not ok:
                failures.append(label)
            if first_only and failures:
                return failures
    for name in PROBE_MANIFEST:
        label = f"totality:{name}"
        if only is not None and label not in only:
            continue
        if first_only and failures:
            return failures
        try:
            ok = _totality_ok(cls, name)
        except BaseException as error:  # noqa: BLE001
            failures.append(f"{label}:{type(error).__name__}")
            continue
        if executed is not None:
            executed.append(label)
        if not ok:
            failures.append(label)
    if only is None or ORDER_LABEL in only:
        try:
            ok = _order_ok(cls)
        except BaseException as error:  # noqa: BLE001
            failures.append(f"{ORDER_LABEL}:{type(error).__name__}")
        else:
            if executed is not None:
                executed.append(ORDER_LABEL)
            if not ok:
                failures.append(ORDER_LABEL)
    return failures


# -- black-box mutants ---------------------------------------------------------
class AcceptsAll(ConflictDetector):
    """Never validates, never rejects."""

    def detect(self, base, left, right):
        try:
            return super().detect(base, left, right)
        except ConflictError:
            return {"base_id": "", "left_id": "", "right_id": "",
                    "conflicts": {}}


class PartialCommit(ConflictDetector):
    """Rejects with the right class after corrupting the received
    inputs (partial state change before the failure)."""

    def detect(self, base, left, right):
        try:
            return super().detect(copy.deepcopy(base),
                                  copy.deepcopy(left),
                                  copy.deepcopy(right))
        except ConflictError:
            for state in (base, left, right):
                if isinstance(state, dict) and state:
                    state.pop(next(iter(state)))
            raise


class MutatesOnSuccess(ConflictDetector):
    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        if isinstance(left, dict):
            left["__touched__"] = {}
        return out


class WrongCode(ConflictDetector):
    def detect(self, base, left, right):
        try:
            return super().detect(base, left, right)
        except ConflictError as error:
            raise ConflictError(error.failure_class,
                                "E_WRONG") from error


class DivergentAsMalformed(ConflictDetector):
    def detect(self, base, left, right):
        try:
            return super().detect(base, left, right)
        except ConflictError as error:
            raise ConflictError(MCR, FAILURE_MAPPING[MCR]) from error


class SkipsDivergentBase(ConflictDetector):
    """Treats left == base as a trivial merge."""

    def detect(self, base, left, right):
        if isinstance(base, dict) and base == left:
            return {"base_id": state_id(base),
                    "left_id": state_id(left),
                    "right_id": state_id(right), "conflicts": {}}
        return super().detect(base, left, right)


class TrustsMapKeys(ConflictDetector):
    """Re-keys every state by derived identity before validating
    (hides the identity-key mismatch)."""

    def detect(self, base, left, right):
        def rekey(state):
            try:
                return {_identity(v): v for v in state.values()}
            except BaseException:  # noqa: BLE001
                return state
        return super().detect(rekey(base), rekey(left),
                              rekey(right))


class DropsChangedVsRemoved(ConflictDetector):
    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        out["conflicts"] = {k: w for k, w in out["conflicts"].items()
                            if w["kind"] != "changed_vs_removed"}
        return out


class AutoResolvesLeft(ConflictDetector):
    """Silently resolves both-changed conflicts toward left."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        out["conflicts"] = {
            k: w for k, w in out["conflicts"].items()
            if w["kind"] != "both_changed_differently"}
        return out


class Asymmetric(ConflictDetector):
    """Always reports witnesses in lexical order of the records,
    not by side."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        for w in out["conflicts"].values():
            if w["left"] is not None and w["right"] is not None:
                pair = sorted((w["left"], w["right"]),
                              key=lambda r: json.dumps(
                                  r, sort_keys=True))
                w["left"], w["right"] = pair
        return out


class FlagsIdenticalAdds(ConflictDetector):
    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        for key in set(left) & set(right):
            if key not in base and key not in out["conflicts"]:
                out["conflicts"][key] = {
                    "kind": "added_differently",
                    "left": copy.deepcopy(left[key]),
                    "right": copy.deepcopy(right[key])}
        return out


class StaleIds(ConflictDetector):
    """Reports the base id for every side."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        out["right_id"] = out["base_id"]
        return out


class RejectsEmptyBase(ConflictDetector):
    """Over-strict: rejects an empty base as malformed, although
    an empty base is a valid state."""

    def detect(self, base, left, right):
        if base == {}:
            raise ConflictError(MCR, FAILURE_MAPPING[MCR])
        return super().detect(base, left, right)


class ReverseOrder(ConflictDetector):
    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        out["conflicts"] = dict(reversed(list(
            out["conflicts"].items())))
        return out


class ValueRotate(ConflictDetector):
    """Keys in canonical order, witness values rotated by one."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        keys = list(out["conflicts"])
        values = list(out["conflicts"].values())
        out["conflicts"] = dict(zip(keys, values[1:] + values[:1],
                                    strict=True))
        return out


class SwapSidesMulti(ConflictDetector):
    """left/right swapped on every conflict after the first."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        for w in list(out["conflicts"].values())[1:]:
            w["left"], w["right"] = w["right"], w["left"]
        return out


class DropExtraConflicts(ConflictDetector):
    """Witnesses after the first emptied."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        for key in list(out["conflicts"])[1:]:
            out["conflicts"][key] = {
                "kind": "both_changed_differently",
                "left": None, "right": None}
        return out


class InsertionOrder(ConflictDetector):
    """Conflicts in left-then-right arrival order, not canonical."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        arrival = list(dict.fromkeys([*left, *right]))
        out["conflicts"] = {k: out["conflicts"][k] for k in arrival
                            if k in out["conflicts"]}
        return out


def _swap_in_equal_copies(state):
    if isinstance(state, dict):
        for key in list(state):
            state[key] = copy.deepcopy(state[key])


class EqualCopySwapOnReject(ConflictDetector):
    """Writes equal-value copies into the caller's left state, then
    raises the correct class - value-equal, identity-broken."""

    def detect(self, base, left, right):
        try:
            return super().detect(copy.deepcopy(base),
                                  copy.deepcopy(left),
                                  copy.deepcopy(right))
        except ConflictError:
            _swap_in_equal_copies(left)
            raise


class EqualCopySwapOnSuccess(ConflictDetector):
    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        _swap_in_equal_copies(right)
        return out


class NoExactStrKeyGuard(ConflictDetector):
    """M1: compares map keys before the exact-str guard."""

    def detect(self, base, left, right):
        for state in (base, left, right):
            if isinstance(state, dict):
                for key in state:
                    if key == "":  # raw comparison on caller keys
                        pass
        return super().detect(base, left, right)


class RawOnNonDictState(ConflictDetector):
    """M2: iterates states before checking they are mappings."""

    def detect(self, base, left, right):
        for state in (base, left, right):
            len(state.items())
        return super().detect(base, left, right)


class RawOnNonStrVariant(ConflictDetector):
    """M3b: string-operates on variant before the type guard."""

    def detect(self, base, left, right):
        for state in (base, left, right):
            if isinstance(state, dict):
                for rec in state.values():
                    if isinstance(rec, dict) and "variant" in rec \
                            and rec["variant"] is not None:
                        rec["variant"].lower()
        return super().detect(base, left, right)


class RawOnNonStrKey(ConflictDetector):
    """M4: looks records up by re-derived str key before the key
    guard."""

    def detect(self, base, left, right):
        for state in (base, left, right):
            if isinstance(state, dict):
                for key in list(dict.keys(state)):
                    if type(key) is not str:
                        {}[key]
        return super().detect(base, left, right)


class RawRecordFieldSet(ConflictDetector):
    """Compares a record's raw key set before the exact-str key
    guard (the pre-fix T0185 reference shape)."""

    def detect(self, base, left, right):
        for state in (base, left, right):
            if isinstance(state, dict):
                for rec in state.values():
                    if isinstance(rec, dict):
                        set(rec.keys()) == set(_FIELDS)  # noqa: B015
        return super().detect(base, left, right)


class ReordersAndCopiesAfterDetect(ConflictDetector):
    """M6: after a successful detect, reverses the caller's dicts
    and replaces nested records with equal copies."""

    def detect(self, base, left, right):
        out = super().detect(base, left, right)
        for state in (base, left, right):
            items = list(state.items())
            state.clear()
            for key, value in reversed(items):
                state[key] = copy.deepcopy(value)
        return out


MUTANTS = {
    "value-rotate": ValueRotate,
    "swap-sides-multi": SwapSidesMulti,
    "drop-extra-conflicts": DropExtraConflicts,
    "m1-no-exact-str-key-guard": NoExactStrKeyGuard,
    "m2-raw-on-non-dict-state": RawOnNonDictState,
    "m3b-raw-on-non-str-variant": RawOnNonStrVariant,
    "m4-raw-on-non-str-key": RawOnNonStrKey,
    "m6-reorders-and-copies": ReordersAndCopiesAfterDetect,
    "raw-record-field-set": RawRecordFieldSet,
    "reverse-order": ReverseOrder,
    "insertion-order": InsertionOrder,
    "equal-copy-swap-on-reject": EqualCopySwapOnReject,
    "equal-copy-swap-on-success": EqualCopySwapOnSuccess,
    "accepts-all": AcceptsAll,
    "partial-commit": PartialCommit,
    "mutates-on-success": MutatesOnSuccess,
    "wrong-code": WrongCode,
    "divergent-as-malformed": DivergentAsMalformed,
    "skips-divergent-base": SkipsDivergentBase,
    "trusts-map-keys": TrustsMapKeys,
    "drops-changed-vs-removed": DropsChangedVsRemoved,
    "auto-resolves-left": AutoResolvesLeft,
    "asymmetric": Asymmetric,
    "flags-identical-adds": FlagsIdenticalAdds,
    "stale-ids": StaleIds,
    "rejects-empty-base": RejectsEmptyBase,
}


def test_closure():
    _validate_closure(CASES)


def test_row_digest_table_is_closed():
    assert set(ROW_DIGESTS) == {
        f"{s}:{m[0]}" for s, man in MANIFESTS.items() for m in man}
    for section in MANIFESTS:
        for row in CASES[section]:
            assert ROW_DIGESTS[f"{section}:{row['name']}"] == \
                _row_digest(row)


def test_reference_detector_passes_battery():
    executed = []
    assert _probe(ConflictDetector, executed=executed) == []
    assert executed == [f"{s}:{m[0]}" for s, man in MANIFESTS.items()
                        for m in man] + [
        f"totality:{n}" for n in PROBE_MANIFEST] + [ORDER_LABEL]


def test_probe_manifest_closed_and_ordered():
    assert list(PROBES) == list(PROBE_MANIFEST)
    assert len(PROBE_MANIFEST) == 156
    for slot in SIDES:
        assert sum(n.startswith(f"{slot}-") for n in PROBE_MANIFEST) \
            == 52


def test_order_probe_is_discriminating():
    """The probe's insertion orders all differ from the canonical
    order and from its reverse, the pinned order is the sorted
    identity order, and every conflict is a both-changed edit."""
    base, left, right = _order_states()
    assert sorted(CANONICAL_ORDER) == CANONICAL_ORDER
    assert list(ORDER_EXPECT["conflicts"]) == CANONICAL_ORDER
    assert ORDER_EXPECT["base_id"] == state_id(base)
    assert ORDER_EXPECT["left_id"] == state_id(left)
    assert ORDER_EXPECT["right_id"] == state_id(right)
    for key, w in ORDER_EXPECT["conflicts"].items():
        assert w["left"] == left[key] and w["right"] == right[key]
    for state in (base, left, right):
        assert set(state) == set(CANONICAL_ORDER)
        order = list(state)
        assert order != CANONICAL_ORDER
        assert order != CANONICAL_ORDER[::-1]
    assert list(left) != list(right) != list(base) != list(left)
    result = ConflictDetector().detect(base, left, right)
    assert {w["kind"] for w in result["conflicts"].values()} == {
        "both_changed_differently"}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_red(name):
    assert _probe(MUTANTS[name], first_only=True), \
        f"{name}: mutant passed battery"


def test_mutants_fail_on_their_target_rows():
    """Each mutant is red for its OWN defect: it fails exactly the
    rows that exercise the behavior it breaks."""
    targets = {
        "trusts-map-keys": "malformed:record-identity-key-mismatch",
        "skips-divergent-base":
            "malformed:left-equals-base-divergent-base",
        "drops-changed-vs-removed":
            "happy:changed-vs-removed-left-changed",
        "flags-identical-adds":
            "boundary:both-added-identically-compatible",
        "rejects-empty-base": "happy:added-differently",
        "m1-no-exact-str-key-guard":
            "totality:left-key-str-subclass",
        "m2-raw-on-non-dict-state": "totality:right-state-none",
        "m3b-raw-on-non-str-variant":
            "totality:base-field-variant-zero",
        "m4-raw-on-non-str-key": "totality:left-key-none",
        "m6-reorders-and-copies": "happy:disjoint-edits-compatible",
        "raw-record-field-set":
            "totality:left-record-key-digest-str-subclass",
        "reverse-order": ORDER_LABEL,
        "value-rotate": ORDER_LABEL,
        "swap-sides-multi": ORDER_LABEL,
        "drop-extra-conflicts": ORDER_LABEL,
        "insertion-order": ORDER_LABEL,
        "equal-copy-swap-on-reject":
            "malformed:record-bad-digest-grammar",
        "equal-copy-swap-on-success":
            "happy:disjoint-edits-compatible",
    }
    for name, label in targets.items():
        failures = _probe(MUTANTS[name], only={label})
        assert [f.split(":")[0] + ":" + f.split(":")[1]
                for f in failures] == [label], (name, failures)


# -- kill-proof: substitution mutants vs the closure ---------------------------
# The one pair of rows whose payloads are byte-identical apart from
# the name: substituting one for the other is an EQUIVALENT mutant
# (the resulting row is unchanged), so it is excluded - and the set
# is itself closed.
EQUIVALENT_PAYLOAD_PAIRS = frozenset({
    frozenset({"happy:both-changed-differently",
               "boundary:single-record-states-conflict"}),
})


def _payload(row):
    return {k: v for k, v in row.items() if k != "name"}


def _regen(row):
    row["expect"] = ConflictDetector().detect(
        copy.deepcopy(row["base"]), copy.deepcopy(row["left"]),
        copy.deepcopy(row["right"]))


def _erasures(section, row):
    """Single-edge erasures of ROW that stay executable, with the
    pinned result regenerated from the reference so only the
    closure can kill them."""
    out = []
    if section in ("happy", "boundary"):
        for label, fn in (
                ("right-is-left", lambda r: r.__setitem__(
                    "right", copy.deepcopy(r["left"]))),
                ("left-is-right", lambda r: r.__setitem__(
                    "left", copy.deepcopy(r["right"]))),
                ("sides-swapped", lambda r: r.update(
                    left=r["right"], right=r["left"])),
                *((f"{side}-drops-{n}",
                   lambda r, side=side, k=k: r[side].pop(k))
                  for side in ("left", "right")
                  for n, k in enumerate(sorted(row[side]))),
                *((f"{side}-redigests-{n}",
                   lambda r, side=side, k=k: r[side][k].__setitem__(
                       "digest", "pdv1:" + "a" * 64))
                  for side in ("left", "right")
                  for n, k in enumerate(sorted(row[side])))):
            mutant = copy.deepcopy(row)
            fn(mutant)
            try:
                _regen(mutant)
            except ConflictError:
                continue
            if mutant != row:
                out.append((label, mutant))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        fixed.update(_repaired(row))
        out.append(("defect-repaired", fixed))
        moved = copy.deepcopy(row)
        if isinstance(row["base"], dict):
            moved["left"], moved["right"] = row["right"], row["left"]
        else:
            moved["base"], moved["left"] = row["left"], row["base"]
        out.append(("defect-moved", moved))
    else:
        unfixed = copy.deepcopy(row)
        unfixed["then_left"] = copy.deepcopy(row["left"])
        out.append(("follow-up-not-fixed", unfixed))
        drift = copy.deepcopy(row)
        drift["then_right"] = copy.deepcopy(row["then_left"])
        out.append(("follow-up-right-drift", drift))
    return out


def _signature(row):
    """Scenario signature of a detect row: side sizes, witness
    kinds, per-side (added, removed, changed) counts, and whether
    the outcomes coincide. An erasure that changes it erases the
    row's edge; one that keeps it is a scenario-preserving
    perturbation (caught by the whole-row digest)."""
    a_l, r_l, c_l = _edits(row["base"], row["left"])
    a_r, r_r, c_r = _edits(row["base"], row["right"])
    return (tuple(len(row[s]) for s in SIDES),
            tuple(sorted(w["kind"]
                         for w in row["expect"]["conflicts"].values())),
            tuple(map(len, (a_l, r_l, c_l, a_r, r_r, c_r))),
            row["left"] == row["right"])


def _is_edge_erasure(section, original, mutant):
    if section not in ("happy", "boundary"):
        return True
    return _signature(mutant) != _signature(original)


def _iter_substitution_mutants(edge_only=False):
    """(label, mutated cases). EDGE_ONLY drops the scenario-
    preserving perturbations, which only the digest can kill."""
    labels = [(s, r["name"]) for s in MANIFESTS for r in CASES[s]]
    # payload substitution: every ordered pair across all sections
    for sa, na in labels:
        for sb, nb in labels:
            if (sa, na) == (sb, nb) or frozenset(
                    {f"{sa}:{na}", f"{sb}:{nb}"}) in \
                    EQUIVALENT_PAYLOAD_PAIRS:
                continue
            m = copy.deepcopy(CASES)
            i = [r["name"] for r in m[sa]].index(na)
            src = next(r for r in CASES[sb] if r["name"] == nb)
            m[sa][i] = dict(copy.deepcopy(src), name=na)
            yield (f"payload:{sa}:{na}<-{sb}:{nb}", m)
    # name swaps within each section
    for section in MANIFESTS:
        rows = CASES[section]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                m = copy.deepcopy(CASES)
                m[section][i]["name"], m[section][j]["name"] = \
                    rows[j]["name"], rows[i]["name"]
                yield (f"name-swap:{section}:{i}:{j}", m)
    # reversed rows, dropped row, duplicated row
    for section in MANIFESTS:
        m = copy.deepcopy(CASES)
        m[section].reverse()
        yield (f"reversed:{section}", m)
        m = copy.deepcopy(CASES)
        m[section].pop()
        yield (f"dropped:{section}", m)
        m = copy.deepcopy(CASES)
        m[section].append(copy.deepcopy(m[section][0]))
        yield (f"duplicated:{section}", m)
    # per-row single-edge erasures
    for section in MANIFESTS:
        for i, row in enumerate(CASES[section]):
            for label, mutant in _erasures(section, row):
                if edge_only and not _is_edge_erasure(
                        section, row, mutant):
                    continue
                m = copy.deepcopy(CASES)
                m[section][i] = mutant
                yield (f"erased:{section}:{row['name']}:"
                            f"{label}", m)
    return


def test_equivalent_payload_pairs_are_closed():
    """The excluded pairs are exactly the rows whose payloads are
    identical - no real substitution hides behind the exclusion."""
    labels = [(f"{s}:{r['name']}", r) for s in MANIFESTS
              for r in CASES[s]]
    found = {frozenset({a, b})
             for i, (a, ra) in enumerate(labels)
             for b, rb in labels[i + 1:]
             if _payload(ra) == _payload(rb)}
    assert found == EQUIVALENT_PAYLOAD_PAIRS


def test_every_row_has_an_edge_erasure():
    """Every row's edge is actually erased by at least one mutant
    the digest-neutralized closure must kill."""
    for section in MANIFESTS:
        for row in CASES[section]:
            assert any(_is_edge_erasure(section, row, mutant)
                       for _, mutant in _erasures(section, row)), \
                (section, row["name"])


@pytest.mark.parametrize("with_digests", [True, False],
                         ids=["with-digests", "digests-neutralized"])
def test_closure_kills_substitution_mutants(with_digests,
                                            monkeypatch):
    """Every substitution, rename, reorder, drop, duplicate and
    single-edge erasure is killed by _validate_closure with the
    digest table live AND with _row_digest monkeypatched to return
    the table value (digests neutralized): the ordered manifests
    and per-tag semantic checks close every section on their own.
    Scenario-preserving perturbations (e.g. a re-digested record
    that is still a both-changed edit) are required only with the
    digest table live - that is exactly what the whole-row digest
    is for."""
    if not with_digests:
        monkeypatch.setattr(
            sys.modules[__name__], "_row_digest",
            lambda row: ROW_DIGESTS.get(
                f"{_section_of(row)}:{row['name']}", ""))
    count = 0
    survivors = []
    for label, m in _iter_substitution_mutants(edge_only=not with_digests):
        count += 1
        try:
            _validate_closure(m)
        except (AssertionError, ValueError, KeyError, TypeError):
            continue
        survivors.append(label)
    assert count > 600
    assert survivors == [], survivors
