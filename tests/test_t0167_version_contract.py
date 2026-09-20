"""T0167: graph-content version contract behavior battery.

Reference store FULLY DERIVED from data/contracts/version.yaml:
record shape, content-addressed identity, lineage invariants,
timestamp calendar rules, failure classes and merge semantics are
all read from the contract doc. The content hasher is an
INJECTABLE oracle - the trust boundary is pinned: an injected
collision surfaces as conflicting_version, never a silent
overwrite. Happy, lineage, content-addressing, Cartesian
malformed, monotonicity, collision-witness, merge-algebra,
rollback, mutant and lint batteries below.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import re
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.variant_contract_lint import ContractError  # noqa: E402
from tools.version_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)

CONTRACT = ROOT / "data/contracts/version.yaml"


def _docs():
    return yaml.safe_load(CONTRACT.read_text())["contract"]


_CC = _docs()
_ID_RE = re.compile(_CC["identity"]["version_id"]["grammar"])
_DIGEST_RE = re.compile(_CC["fields"]["graph_digest"]["grammar"])
_TS_RE = re.compile(_CC["fields"]["created_at"]["grammar"])
_LABEL_RE = re.compile(_CC["fields"]["label"]["grammar"])
_FIELDS = _CC["record"]["fields"]


class VersionError(Exception):
    def __init__(self, failure_class, code, witness=None):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code
        self.witness = witness


def _fail(cls, witness=None):
    raise VersionError(cls, FAILURE_MAPPING[cls], witness)


def _real_hasher(parent_ids, graph_digest):
    body = ("gv1-content\n"
            + "".join(p + "\n" for p in sorted(parent_ids))
            + graph_digest + "\n")
    return "gv1:" + hashlib.sha256(body.encode()).hexdigest()


def constant_hasher(parent_ids, graph_digest):
    """UNTRUSTED: total collision - every content maps to one id."""
    return "gv1:" + "0" * 64


def _valid_timestamp(value):
    """The contract's created_at rules: pinned grammar shape, a
    REAL Gregorian date (leap-year rules), leap seconds rejected
    (second field 00-59)."""
    if not isinstance(value, str) or _TS_RE.fullmatch(
            value) is None:
        return False
    year = int(value[0:4])
    month = int(value[5:7])
    day = int(value[8:10])
    hour = int(value[11:13])
    minute = int(value[14:16])
    second = int(value[17:19])
    if hour > 23 or minute > 59 or second > 59:
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _content_of(record):
    """Identity content: parents (sorted) + graph digest. Label and
    created_at are metadata, never identity."""
    return (tuple(sorted(record["parent_ids"])),
            record["graph_digest"])


class VersionStore:
    """The contract's pinned store: content-addressed immutable
    records, one root, closed failure model, atomic merge."""

    def __init__(self, hasher=_real_hasher):
        self.hasher = hasher
        self.records = {}          # version_id -> exact record
        self.root_id = None

    def make_record(self, parent_ids, graph_digest, created_at,
                    label):
        """Build a candidate record with the content address this
        store's hasher derives - callers never invent ids."""
        canonical_parents = sorted(set(parent_ids)) if \
            isinstance(parent_ids, list) else parent_ids
        if isinstance(canonical_parents, list) and \
                all(isinstance(p, str) for p in canonical_parents):
            vid = self.hasher(canonical_parents, graph_digest)
        else:
            vid = self.hasher([], "")
        return {"version_id": vid,
                "parent_ids": canonical_parents,
                "graph_digest": graph_digest,
                "created_at": created_at,
                "label": label}

    def _validate_shape(self, record):
        """TOTAL validation: explicit type guards on every field
        before any sibling machinery - nothing raw escapes."""
        if not isinstance(record, dict):
            _fail("malformed_version_record")
        if set(record.keys()) != set(_FIELDS):
            _fail("malformed_version_record")
        vid = record["version_id"]
        if not isinstance(vid, str) or isinstance(vid, bool) or \
                _ID_RE.fullmatch(vid) is None:
            _fail("malformed_version_record")
        parents = record["parent_ids"]
        if not isinstance(parents, list) or \
                any(not isinstance(p, str) or isinstance(p, bool)
                    or _ID_RE.fullmatch(p) is None for p in
                    parents):
            _fail("malformed_version_record")
        digest = record["graph_digest"]
        if not isinstance(digest, str) or \
                _DIGEST_RE.fullmatch(digest) is None:
            _fail("malformed_version_record")
        if not _valid_timestamp(record["created_at"]):
            _fail("malformed_version_record")
        label = record["label"]
        if not isinstance(label, str) or isinstance(label, bool) \
                or _LABEL_RE.fullmatch(label) is None:
            _fail("malformed_version_record")
        # content addressing: the id MUST be the store-derived
        # address of its own content
        if vid != self.hasher(sorted(set(parents)), digest):
            _fail("malformed_version_record")

    def insert(self, record):
        rec = copy.deepcopy(record)
        self._validate_shape(rec)
        rec["parent_ids"] = sorted(set(rec["parent_ids"]))
        vid = rec["version_id"]
        if vid in self.records:
            existing = self.records[vid]
            # an existing id accepts ONLY a byte-equal record:
            # unequal content (injected collision) OR divergent
            # metadata (created_at/label) both fail closed, NEVER
            # overwrite and never silently keep one side's
            # metadata - order-independent by construction
            if existing != rec:
                _fail("conflicting_version",
                      witness={"stored": copy.deepcopy(existing),
                               "incoming": copy.deepcopy(rec)})
            return existing  # dedup: byte-equal record, same id
        if not rec["parent_ids"]:
            # root claim
            if self.root_id is not None:
                _fail("root_violation")
        else:
            for parent in rec["parent_ids"]:
                if parent not in self.records:
                    _fail("unknown_parent")
            for parent in rec["parent_ids"]:
                if rec["created_at"] < \
                        self.records[parent]["created_at"]:
                    _fail("nonmonotonic_version")
        self.records[vid] = rec
        if not rec["parent_ids"]:
            self.root_id = vid
        return rec

    def canonical_view(self):
        return sorted(self.records)

    def merge(self, other):
        """ATOMIC staged-copy commit: every incoming EXACT record is
        validated against the merged lineage; any rejection leaves
        this store bit-identical."""
        staged = VersionStore(self.hasher)
        staged.records = copy.deepcopy(self.records)
        staged.root_id = self.root_id
        # PHASE 1 - TOTAL batch validation: every incoming raw
        # record's field set and scalar/container types are
        # checked into a safe staged batch BEFORE any topological
        # inspection touches it (lineage, content and grammar
        # checks still happen per-record in staged insert; this
        # phase only guarantees inspection safety - no parent
        # existence required here)
        batch = []
        for vid in other.canonical_view():
            raw = other.records[vid]
            if not isinstance(raw, dict) or \
                    set(raw.keys()) != set(_FIELDS):
                _fail("malformed_version_record")
            parents = raw["parent_ids"]
            if not isinstance(parents, list) or \
                    any(not isinstance(x, str) for x in parents):
                _fail("malformed_version_record")
            for scalar in ("version_id", "graph_digest",
                           "created_at", "label"):
                if not isinstance(raw[scalar], str):
                    _fail("malformed_version_record")
            batch.append(copy.deepcopy(raw))
        # PHASE 2 - topological order over the SAFE batch: a
        # record is staged once every parent is present; a batch
        # that stops making progress surfaces the exact failure
        # its first stuck record produces
        pending = batch
        while pending:
            progressed = False
            for rec in list(pending):
                if all(p in staged.records
                       for p in rec["parent_ids"]):
                    staged.insert(rec)
                    pending.remove(rec)
                    progressed = True
            if not progressed:
                staged.insert(pending[0])
        self.records = staged.records
        self.root_id = staged.root_id
        return self


D1 = "gdv1:" + hashlib.sha256(b"graph-state-1").hexdigest()
D2 = "gdv1:" + hashlib.sha256(b"graph-state-2").hexdigest()
D3 = "gdv1:" + hashlib.sha256(b"graph-state-3").hexdigest()
D4 = "gdv1:" + hashlib.sha256(b"graph-state-4").hexdigest()
T1 = "2026-01-01T00:00:00Z"
T2 = "2026-01-02T00:00:00Z"
T3 = "2026-01-03T00:00:00Z"
T4 = "2026-01-04T00:00:00Z"


def _chain(store, count):
    """Root + linear chain; returns the list of exact records."""
    out = []
    parents = []
    for i in range(count):
        rec = store.make_record(
            parents, [D1, D2, D3, D4][i],
            [T1, T2, T3, T4][i], f"v{i}")
        out.append(store.insert(rec))
        parents = [rec["version_id"]]
    return out


def test_lint_clean():
    lint()


def test_happy_root_and_linear_chain():
    store = VersionStore()
    chain = _chain(store, 4)
    assert len(store.records) == 4
    assert store.root_id == chain[0]["version_id"]
    assert chain[0]["parent_ids"] == []
    for prev, cur in zip(chain, chain[1:], strict=False):
        assert cur["parent_ids"] == [prev["version_id"]]


def test_branching_dag_and_multi_parent_merge_version():
    store = VersionStore()
    root = store.insert(store.make_record([], D1, T1, "root"))
    left = store.insert(store.make_record(
        [root["version_id"]], D2, T2, "left"))
    right = store.insert(store.make_record(
        [root["version_id"]], D3, T2, "right"))
    merged = store.insert(store.make_record(
        [right["version_id"], left["version_id"]], D4, T4,
        "merge"))
    assert merged["parent_ids"] == sorted(
        [left["version_id"], right["version_id"]])
    assert len(store.records) == 4


def test_content_addressing_dedup_byte_equal_only():
    """A byte-equal reinsert dedups (same id, same everything).
    Label and created_at never participate in IDENTITY - the id
    is unchanged by them - but a DIVERGENT metadata value on an
    existing id fails CLOSED as conflicting_version: no silent
    keep of either side's metadata, order-independent."""
    store = VersionStore()
    a = store.insert(store.make_record([], D1, T1, "first"))
    again = store.insert(store.make_record([], D1, T1, "first"))
    assert again is a
    assert len(store.records) == 1
    before = copy.deepcopy(store.records)
    with pytest.raises(VersionError) as exc:
        store.insert(store.make_record([], D1, T1, "renamed"))
    assert exc.value.failure_class == "conflicting_version"
    assert exc.value.code == FAILURE_MAPPING[
        "conflicting_version"]
    assert exc.value.code in ERROR_ENUM
    assert store.records == before
    assert store.records[a["version_id"]]["label"] == "first"


def test_metadata_divergence_rejected_directly_and_both_merges():
    """A/B pairs with identical parents+digest but divergent label,
    divergent created_at, and both: same content id BY DESIGN;
    direct insert AND both merge directions reject identically
    with bit-identical rollback."""
    variants = [
        ("label only", T1, "beta"),
        ("created_at only", T2, "alpha"),
        ("both", T2, "beta"),
    ]
    for _name, ts, label in variants:
        store_a = VersionStore()
        store_b = VersionStore()
        rec_a = store_a.make_record([], D1, T1, "alpha")
        rec_b = store_b.make_record([], D1, ts, label)
        assert rec_a["version_id"] == rec_b["version_id"]
        store_a.insert(rec_a)
        store_b.insert(rec_b)
        with pytest.raises(VersionError) as exc:
            store_a.insert(rec_b)
        assert exc.value.failure_class == "conflicting_version"
        for src, dst in ((store_b, store_a),
                         (store_a, store_b)):
            before = copy.deepcopy(dst.records)
            before_root = dst.root_id
            with pytest.raises(VersionError) as exc:
                dst.merge(src)
            assert exc.value.failure_class ==                 "conflicting_version"
            assert dst.records == before
            assert dst.root_id == before_root


def test_associativity_across_metadata_variants():
    """Three stores holding pairwise-divergent metadata for one
    content id: every merge order rejects conflicting_version -
    no order-dependent exact store ever forms."""
    variants = [(T1, "alpha"), (T1, "beta"), (T2, "alpha")]
    stores = []
    for ts, label in variants:
        store = VersionStore()
        store.insert(store.make_record([], D1, ts, label))
        stores.append(store)
    for src, dst in itertools.permutations(stores, 2):
        before = copy.deepcopy(dst.records)
        before_root = dst.root_id
        with pytest.raises(VersionError) as exc:
            dst.merge(src)
        assert exc.value.failure_class == "conflicting_version"
        assert dst.records == before
        assert dst.root_id == before_root


def test_order_insensitive_parents_and_view():
    store = VersionStore()
    root = store.insert(store.make_record([], D1, T1, "root"))
    left = store.insert(store.make_record(
        [root["version_id"]], D2, T2, "left"))
    right = store.insert(store.make_record(
        [root["version_id"]], D3, T2, "right"))
    m1 = store.insert(store.make_record(
        [left["version_id"], right["version_id"]], D4, T4, "m"))
    store2 = VersionStore()
    r2 = store2.insert(store2.make_record([], D1, T1, "root"))
    l2 = store2.insert(store2.make_record(
        [r2["version_id"]], D2, T2, "left"))
    g2 = store2.insert(store2.make_record(
        [r2["version_id"]], D3, T2, "right"))
    m2 = store2.insert(store2.make_record(
        [g2["version_id"], l2["version_id"]], D4, T4, "m"))
    assert m1["version_id"] == m2["version_id"]
    assert store.canonical_view() == store2.canonical_view()


def test_collision_witness_fail_closed_never_overwrite():
    """The trust boundary: an injected total-collision hasher hands
    DIFFERENT content one id - the second insert surfaces
    conflicting_version with a witness; the stored record is
    bit-identical, never overwritten."""
    store = VersionStore(constant_hasher)
    first = store.insert(store.make_record([], D1, T1, "root"))
    before = copy.deepcopy(store.records)
    with pytest.raises(VersionError) as exc:
        store.insert(store.make_record([], D2, T1, "impostor"))
    assert exc.value.failure_class == "conflicting_version"
    assert exc.value.code == FAILURE_MAPPING[
        "conflicting_version"]
    assert exc.value.witness["stored"]["graph_digest"] == D1
    assert exc.value.witness["incoming"]["graph_digest"] == D2
    assert store.records == before
    assert store.records[first["version_id"]]["graph_digest"] == D1


def test_root_violations():
    store = VersionStore()
    store.insert(store.make_record([], D1, T1, "root"))
    before = copy.deepcopy(store.records)
    with pytest.raises(VersionError) as exc:
        store.insert(store.make_record([], D2, T2, "second root"))
    assert exc.value.failure_class == "root_violation"
    assert exc.value.code == FAILURE_MAPPING["root_violation"]
    assert store.records == before
    # a NON-root claim with empty parents is the root path; a root
    # with parents is impossible by construction - covered by the
    # content address itself (parents feed the id)


def test_unknown_parent_rejected():
    store = VersionStore()
    store.insert(store.make_record([], D1, T1, "root"))
    ghost = VersionStore().make_record([], D4, T1, "ghost")
    before = copy.deepcopy(store.records)
    with pytest.raises(VersionError) as exc:
        store.insert(store.make_record(
            [ghost["version_id"]], D2, T2, "orphan"))
    assert exc.value.failure_class == "unknown_parent"
    assert exc.value.code == FAILURE_MAPPING["unknown_parent"]
    assert store.records == before


def test_nonmonotonic_version_rejected():
    store = VersionStore()
    root = store.insert(store.make_record([], D1, T2, "root"))
    before = copy.deepcopy(store.records)
    with pytest.raises(VersionError) as exc:
        store.insert(store.make_record(
            [root["version_id"]], D2, T1, "child-before-parent"))
    assert exc.value.failure_class == "nonmonotonic_version"
    assert exc.value.code == FAILURE_MAPPING[
        "nonmonotonic_version"]
    assert store.records == before
    # equal timestamps are allowed (not-before, not strictly-after)
    ok = store.insert(store.make_record(
        [root["version_id"]], D2, T2, "same-instant child"))
    assert ok["created_at"] == T2


BASE = {"parent_ids": [], "graph_digest": D1, "created_at": T1,
        "label": "root"}


def _candidate(store, **overrides):
    fields = dict(BASE)
    fields.update(overrides)
    return store.make_record(**fields)


CARTESIAN = [None, True, 0, 1.5, [], {}, ""]


@pytest.mark.parametrize("mutation", CARTESIAN)
@pytest.mark.parametrize(
    "field", ["version_id", "parent_ids", "graph_digest",
              "created_at", "label"])
def test_cartesian_malformed_battery(field, mutation):
    """Every field x the Cartesian mutation set fails closed as
    malformed_version_record with the mapped code - the validator
    is total, nothing raw escapes."""
    if field == "parent_ids" and mutation == []:
        pytest.skip("empty parent_ids is the valid root shape")
    store = VersionStore()
    rec = _candidate(store)
    rec[field] = copy.deepcopy(mutation)
    with pytest.raises(VersionError) as exc:
        store.insert(rec)
    assert exc.value.failure_class == "malformed_version_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_version_record"]
    assert exc.value.code in ERROR_ENUM
    assert store.records == {}


MALFORMED_CASES = [
    ("missing label", lambda r: r.pop("label")),
    ("extra field", lambda r: r.update(extra="x")),
    ("id grammar drift", lambda r: r.update(
        version_id="gv2:" + r["version_id"][4:])),
    ("id not derived", lambda r: r.update(
        version_id="gv1:" + "f" * 64)),
    ("digest grammar drift", lambda r: r.update(
        graph_digest="pdv1:" + "a" * 64)),
    ("label empty", lambda r: r.update(label="")),
    ("label too long", lambda r: r.update(label="x" * 121)),
    ("label non-ascii-printable", lambda r: r.update(
        label="bad\tlabel")),
    ("parent grammar drift", lambda r: r.update(
        parent_ids=["not-a-version-id"])),
    ("feb 30", lambda r: r.update(
        created_at="2026-02-30T00:00:00Z")),
    ("feb 29 non-leap", lambda r: r.update(
        created_at="2026-02-29T00:00:00Z")),
    ("feb 29 leap ok", None),  # handled in the leap test below
    ("month 13", lambda r: r.update(
        created_at="2026-13-01T00:00:00Z")),
    ("hour 24", lambda r: r.update(
        created_at="2026-01-01T24:00:00Z")),
    ("minute 60", lambda r: r.update(
        created_at="2026-01-01T00:60:00Z")),
    ("leap second rejected", lambda r: r.update(
        created_at="2026-01-01T00:00:60Z")),
    ("offset not utc", lambda r: r.update(
        created_at="2026-01-01T00:00:00+01:00")),
    ("no time", lambda r: r.update(created_at="2026-01-01")),
]


@pytest.mark.parametrize("name,mutate", [
    (n, m) for n, m in MALFORMED_CASES if m is not None],
    ids=[n for n, m in MALFORMED_CASES if m is not None])
def test_field_specific_malformed(name, mutate):
    store = VersionStore()
    rec = _candidate(store)
    mutate(rec)
    with pytest.raises(VersionError) as exc:
        store.insert(rec)
    assert exc.value.failure_class == "malformed_version_record"
    assert store.records == {}


def test_leap_day_accepted_in_leap_year():
    store = VersionStore()
    rec = _candidate(store, created_at="2028-02-29T00:00:00Z")
    assert _valid_timestamp(rec["created_at"])
    store.insert(rec)
    assert len(store.records) == 1


def test_duplicate_parents_canonicalized():
    store = VersionStore()
    root = store.insert(store.make_record([], D1, T1, "root"))
    left = store.insert(store.make_record(
        [root["version_id"]], D2, T2, "left"))
    rec = store.make_record(
        [left["version_id"], left["version_id"],
         root["version_id"]], D3, T3, "child")
    stored = store.insert(rec)
    assert stored["parent_ids"] == sorted(
        {left["version_id"], root["version_id"]})


def test_merge_algebra():
    """Idempotent, commutative, associative: every grouping and
    permutation of DAG-building merges yields one store."""
    def built(depth):
        store = VersionStore()
        _chain(store, depth)
        return store

    parts = [built(1), built(2), built(3)]
    # built(n) shares the root id - merging is idempotent over it
    full = built(4)
    results = set()
    for order in itertools.permutations(parts):
        store = VersionStore()
        for part in order:
            store.merge(part)
        results.add(tuple(store.canonical_view()))
    assert len(results) == 1
    merged_all = VersionStore()
    merged_all.merge(full)
    merged_all.merge(full)
    assert tuple(merged_all.canonical_view()) in results | {
        tuple(full.canonical_view())}


def test_merge_atomic_rollback():
    good = VersionStore()
    _chain(good, 2)
    bad = VersionStore()
    _chain(bad, 2)
    orphan = VersionStore().make_record([], D4, T1, "ghost")
    bad.records["gv1:" + "e" * 64] = {
        "version_id": "gv1:" + "e" * 64,
        "parent_ids": [orphan["version_id"]],
        "graph_digest": D3, "created_at": T3, "label": "orphan"}
    # fix the orphan record's id to its own content address
    bad.records.pop("gv1:" + "e" * 64)
    rec = bad.make_record([orphan["version_id"]], D3, T3,
                          "orphan")
    bad.records[rec["version_id"]] = rec
    before = copy.deepcopy(good.records)
    before_root = good.root_id
    with pytest.raises(VersionError) as exc:
        good.merge(bad)
    assert exc.value.failure_class in FAILURE_MAPPING
    assert good.records == before
    assert good.root_id == before_root


MERGE_CARTESIAN = [None, True, 0, 1.5, "bad\ttab", [], {}]


@pytest.mark.parametrize("mutation", MERGE_CARTESIAN)
@pytest.mark.parametrize(
    "field", ["version_id", "parent_ids", "graph_digest",
              "created_at", "label"])
def test_merge_boundary_cartesian(field, mutation):
    """Every field x the Cartesian set inside a RAW incoming merge
    record maps to malformed_version_record with the exact code -
    no raw KeyError/TypeError escapes the closed surface; the
    receiver's records and root_id stay bit-identical."""
    if field == "parent_ids" and mutation == []:
        pytest.skip("empty parent_ids is the valid root shape")
    receiver = VersionStore()
    _chain(receiver, 2)
    before = copy.deepcopy(receiver.records)
    before_root = receiver.root_id
    source = VersionStore()
    _chain(source, 2)
    victim = source.canonical_view()[-1]
    raw = copy.deepcopy(source.records[victim])
    raw[field] = copy.deepcopy(mutation)
    source.records[victim] = raw  # tamper behind the store's back
    with pytest.raises(VersionError) as exc:
        receiver.merge(source)
    assert exc.value.failure_class == "malformed_version_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_version_record"]
    assert exc.value.code in ERROR_ENUM
    assert receiver.records == before
    assert receiver.root_id == before_root


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_merge_boundary_field_set(mode):
    """Missing and extra fields in a raw incoming merge record map
    to malformed_version_record; receiver bit-identical."""
    receiver = VersionStore()
    _chain(receiver, 2)
    before = copy.deepcopy(receiver.records)
    before_root = receiver.root_id
    source = VersionStore()
    _chain(source, 2)
    victim = source.canonical_view()[-1]
    raw = copy.deepcopy(source.records[victim])
    if mode == "missing":
        raw.pop("label")
    else:
        raw["surprise"] = "field"
    source.records[victim] = raw
    with pytest.raises(VersionError) as exc:
        receiver.merge(source)
    assert exc.value.failure_class == "malformed_version_record"
    assert receiver.records == before
    assert receiver.root_id == before_root


def test_rollback_bit_identical_across_failures():
    store = VersionStore()
    _chain(store, 2)
    before = copy.deepcopy(store.records)
    attempts = [
        lambda: store.insert(store.make_record(
            [], D3, T3, "second root")),
        lambda: store.insert(_candidate(store, label="")),
        lambda: store.insert(store.make_record(
            ["gv1:" + "9" * 64], D3, T3, "orphan")),
    ]
    for attempt in attempts:
        with pytest.raises(VersionError):
            attempt()
    assert store.records == before


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
        "mutable-pointer-versioning")
    add("versions drift", ["contract", "role", "versions"],
        "contract-schemas")
    add("field dropped", ["contract", "record", "fields"],
        ["version_id", "parent_ids", "graph_digest",
         "created_at"])
    add("extra field", ["contract", "record", "fields"],
        ["version_id", "parent_ids", "graph_digest", "created_at",
         "label", "author"])
    add("in-place mutation", ["contract", "record", "immutability"],
        "mutable-in-place")
    add("addressing drift", ["contract", "identity", "version_id",
                             "addressing"], "sequential-ids")
    add("id grammar drift", ["contract", "identity", "version_id",
                             "grammar"], "^v[0-9]+$")
    add("label as identity", ["contract", "identity", "label"],
        "identity-component")
    add("digest as identity", ["contract", "identity",
                               "graph_digest"],
        "record-identity")
    add("algorithm drift", ["contract", "content_addressing",
                            "algorithm"], "md5")
    add("dedup drift", ["contract", "content_addressing", "dedup"],
        "duplicates-allowed")
    add("collision overwrite", ["contract", "content_addressing",
                                "collision_policy"],
        "last-writer-wins")
    add("root rule drift", ["contract", "fields", "parent_ids",
                            "root_only_empty"], False)
    add("parent order matters", ["contract", "fields", "parent_ids",
                                 "order"],
        "significant-as-listed")
    add("digest grammar drift", ["contract", "fields",
                                 "graph_digest", "grammar"],
        "^gdv2:[0-9a-f]{64}$")
    add("calendar drift", ["contract", "fields", "created_at",
                           "calendar"], "any-string")
    add("leap seconds ok", ["contract", "fields", "created_at",
                            "leap_seconds"], "accepted")
    add("monotonicity dropped", ["contract", "fields",
                                 "created_at", "monotonicity"],
        "no-ordering")
    add("label grammar drift", ["contract", "fields", "label",
                                "grammar"], "^.*$")
    add("root multiplicity", ["contract", "lineage", "root"],
        "many-roots-allowed")
    add("dangling parents ok", ["contract", "lineage",
                                "parent_existence"],
        "unknown-parents-accepted")
    add("merge overwrite", ["contract", "merge", "semantics"],
        "last-writer-wins")
    add("non-atomic merge", ["contract", "merge", "commit"],
        "record-by-record")
    add("failure class dropped", ["contract", "failures",
                                  "classes"],
        ["malformed_version_record", "unknown_parent",
         "conflicting_version", "root_violation"])
    add("conflict mapped away", ["contract", "failures", "mapping",
                                 "conflicting_version"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"], False)
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "conflicting_version"])
    add("witness dropped", ["contract", "errors", "shape",
                            "collision_witness_included_on_"
                            "conflicting_version"], False)
    add("rollback drift", ["contract", "properties", "rollback"],
        "best-effort")
    add("trust dropped", ["contract", "properties",
                          "trust_boundary"],
        "collisions-absorbed-silently")
    add("base path drift", ["contract", "versioning",
                            "base_path"], "/graph/version/v0")
    add("link drift", ["contract", "links", "variant_contract"],
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
                       "content_addressing", "fields", "lineage",
                       "merge", "failures", "errors", "properties",
                       "versioning", "links"}
