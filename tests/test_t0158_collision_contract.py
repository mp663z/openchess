"""T0158: graph collision contract behavior battery.

Reference probe FULLY DERIVED from data/contracts/collision.yaml
plus the linked siblings (variant, position-digest, transposition-
node): the collision definition, separation rule and guarantees
come from the definition/separation/guarantees sections, and the
witness machinery is the REAL transposition-node table (imported,
never reimplemented) driven by an injectable digest oracle - a
constant oracle forces every record into one VALID bucket, so the
battery exercises the contract's collision semantics through
actual table behavior, never a mock. Happy, forced-collision,
transparency, order-insensitivity, merge-algebra, malformed,
rollback, lint-mutant and sibling-linkage batteries below.
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

from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    LEGAL_EP,
    STARTPOS,
    NodeError,
    NodeTable,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _docs as _node_docs,
)
from tools.collision_contract_lint import (  # noqa: E402
    CONTRACT,
    DIGEST,
    ERROR_ENUM,
    FAILURE_MAPPING,
    NODE,
    VARIANT,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402


def _docs():
    return yaml.safe_load(CONTRACT.read_text())["contract"]


class CollisionError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cc, cls):
    raise CollisionError(cls, cc["failures"]["mapping"][cls])


# A VALID-format oracle that maps everything to one bucket: the
# total-collision condition, with real digest FORMAT (the linked
# digest contract's pinned shape) so every record stays valid.
def constant_oracle(variant, fen):
    return "pdv1:" + "0" * 64


class CollisionProbe:
    """The contract's semantics riding on the REAL node-table
    machinery: bucket membership accelerates lookup ONLY, every
    equality decision inside a bucket is canonical field comparison
    over the exact stored records, and a bucket key presented AS an
    identity fails closed."""

    def __init__(self, oracle, docs=None):
        self.cc = docs if docs is not None else _docs()
        self.table = NodeTable(_node_docs(), digest_fn=oracle)

    def insert(self, variant_id, fen_text):
        """Insert through the real table; a record failing the
        linked node contract's own shape surfaces HERE as
        malformed_collision_record - collision handling never
        relaxes record validity."""
        try:
            return self.table.insert(variant_id, fen_text)
        except NodeError:
            _fail(self.cc, "malformed_collision_record")

    def lookup_by_bucket_key(self, bucket_key):
        """A bucket key presented AS a record identity: fail closed,
        never resolved by bucket key alone."""
        _fail(self.cc, "accelerator_as_identity")

    def records(self):
        return self.table.records()

    def canonical_view(self):
        """The observable table in CANONICAL-IDENTITY order - never
        bucket-arrival order."""
        return sorted(
            repr(self.table._identity(rec))
            for rec in self.table.records())

    def merge(self, other):
        for rec in other.records():
            self.insert(rec["variant"], rec["snapshot_fen"])
        return self


def _probe(oracle=constant_oracle):
    return CollisionProbe(oracle)


# -- pinned vectors: valid positions with pairwise-distinct identities ------

PROMO_FROM = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
PAWN_E2 = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
COLLIDING_SET = [STARTPOS, AFTER_E4, KINGS, LEGAL_EP, PROMO_FROM,
                 PAWN_E2]


def test_lint_clean():
    lint()


def test_happy_collision_free_baseline():
    """With the real digest oracle (no forced collisions) the probe
    behaves exactly as the linked node table."""
    from tests.test_t0113_position_digest_contract import digest_fen
    probe = _probe(digest_fen)
    for fen in COLLIDING_SET:
        probe.insert("standard", fen)
    assert len(probe.records()) == len(COLLIDING_SET)
    # same-position reinsert folds (same identity, same record)
    rec = probe.insert("standard", STARTPOS)
    assert rec is probe.insert("standard", STARTPOS)
    assert len(probe.records()) == len(COLLIDING_SET)


def test_forced_collision_count_exact_no_merge_no_fork():
    """COUNT-EXACT: N pairwise-distinct identities in ONE bucket
    yield exactly N records - NO-MERGE (no silent dedup, no
    first-writer wins) and NO-FORK (no record splits)."""
    probe = _probe()
    inserted = [probe.insert("standard", fen) for fen in
                COLLIDING_SET]
    assert len(probe.table.buckets) == 1  # all in one bucket
    assert len(probe.records()) == len(COLLIDING_SET)
    assert len({probe.table._identity(r) for r in
                probe.records()}) == len(COLLIDING_SET)
    # reinsertion finds the EXACT right record by field comparison,
    # never the bucket head - and never duplicates
    for fen, rec in zip(COLLIDING_SET, inserted, strict=True):
        assert probe.insert("standard", fen) is rec
    assert len(probe.records()) == len(COLLIDING_SET)


def test_separation_completeness_under_collision():
    """Separation: inside the single bucket, equal bucket keys never
    merge unequal identities and the path-normalization twin still
    folds into its canonical record by FIELD comparison."""
    probe = _probe()
    a = probe.insert("standard", AFTER_E4)
    # the phantom-EP collapse: AFTER_E4 with the none sentinel is
    # the SAME canonical identity - folds even under collision
    collapsed = AFTER_E4.replace(" e3 ", " - ")
    assert probe.insert("standard", collapsed) is a
    # a DIFFERENT position with a colliding bucket never folds
    b = probe.insert("standard", STARTPOS)
    assert b is not a
    assert len(probe.records()) == 2
    # clock-only differences (excluded from identity) still fold
    c = probe.insert("standard", STARTPOS.replace(" 0 1", " 7 42"))
    assert c is b
    assert len(probe.records()) == 2


def test_collision_transparency():
    """TRANSPARENCY: the canonical-identity view of the same record
    set is IDENTICAL collision-free and under total collision."""
    from tests.test_t0113_position_digest_contract import digest_fen
    free = _probe(digest_fen)
    colliding = _probe()
    for fen in COLLIDING_SET:
        free.insert("standard", fen)
        colliding.insert("standard", fen)
    assert free.canonical_view() == colliding.canonical_view()
    assert len(free.table.buckets) > 1  # genuinely different shape
    assert len(colliding.table.buckets) == 1


def test_order_insensitivity_under_collision():
    """ORDER-INSENSITIVITY: every insertion permutation of the
    colliding set yields the same canonical table - arrival order
    inside the bucket is never observable."""
    views = set()
    for perm in itertools.permutations(COLLIDING_SET):
        probe = _probe()
        for fen in perm:
            probe.insert("standard", fen)
        views.add(tuple(probe.canonical_view()))
    assert len(views) == 1


def test_merge_algebra_under_collision():
    """MERGE ALGEBRA PRESERVED: idempotent, commutative, associative
    even when every record collides into one bucket."""
    groups = [COLLIDING_SET[:2], COLLIDING_SET[2:4],
              COLLIDING_SET[4:]]

    def built(fens):
        probe = _probe()
        for fen in fens:
            probe.insert("standard", fen)
        return probe

    full = built(COLLIDING_SET)
    full.insert("standard", COLLIDING_SET[0])  # idempotent reinsert
    results = set()
    for order in itertools.permutations(groups):
        probe = _probe()
        for group in order:
            probe.merge(built(group))
        results.add(tuple(probe.canonical_view()))
    assert len(results) == 1
    assert results.pop() == tuple(full.canonical_view())


def test_accelerator_as_identity_rejected():
    """A bucket key presented AS identity fails closed - never
    resolved by bucket key alone, even when the bucket holds
    exactly one record."""
    probe = _probe()
    probe.insert("standard", STARTPOS)
    before = probe.canonical_view()
    with pytest.raises(CollisionError) as exc:
        probe.lookup_by_bucket_key("pdv1:" + "0" * 64)
    assert exc.value.failure_class == "accelerator_as_identity"
    assert exc.value.code == FAILURE_MAPPING[
        "accelerator_as_identity"]
    assert exc.value.code in ERROR_ENUM
    assert probe.canonical_view() == before


MALFORMED_INSERTS = [
    ("c960", STARTPOS),                       # unknown variant
    ("standard", "garbage w - - 0 1"),        # malformed FEN
    ("standard", "8/8/8/8/8/8/8/4K3 w - - 0 1"),  # one king
    ("standard", AFTER_E4.replace(" 0 1", " 3 9").replace(
        " e3 ", " - ")[:-2] + "x"),           # grammar drift
]


@pytest.mark.parametrize("variant,fen", MALFORMED_INSERTS)
def test_malformed_collision_record_rejected(variant, fen):
    """A record failing the linked node contract's own shape is
    malformed_collision_record HERE - under total collision exactly
    as collision-free."""
    probe = _probe()
    with pytest.raises(CollisionError) as exc:
        probe.insert(variant, fen)
    assert exc.value.failure_class == "malformed_collision_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_collision_record"]
    assert exc.value.code in ERROR_ENUM


def test_rollback_bit_identical_under_collision():
    probe = _probe()
    probe.insert("standard", STARTPOS)
    probe.insert("standard", KINGS)
    before = probe.canonical_view()
    before_buckets = copy.deepcopy(probe.table.buckets)
    for variant, fen in MALFORMED_INSERTS:
        with pytest.raises(CollisionError):
            probe.insert(variant, fen)
    with pytest.raises(CollisionError):
        probe.lookup_by_bucket_key("pdv1:" + "1" * 64)
    assert probe.canonical_view() == before
    assert probe.table.buckets == before_buckets


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
        "bucket-replacement-semantics")
    add("collision as error", ["contract", "role", "collision_is"],
        "rejected-as-conflict")
    add("scope drift", ["contract", "role", "scope"],
        "node-table-only")
    add("owns drift", ["contract", "role", "owns"],
        "identity-semantics")
    add("definition drift", ["contract", "definition", "collision"],
        "bucket-key-equal-implies-record-equal")
    add("bucket key stored", ["contract", "definition",
                              "bucket_key"],
        "stored-as-identity-field")
    add("identity restated", ["contract", "definition", "identity"],
        "restated-here")
    add("separation drift", ["contract", "separation", "rule_kind"],
        "first-in-bucket-wins")
    add("bucket role drift", ["contract", "separation",
                              "bucket_role"], "bucket-decides")
    add("digest equality decides", ["contract", "separation",
                                    "digest_equality"],
        "decides-record-equality")
    add("digest inequality decides", ["contract", "separation",
                                      "digest_inequality"],
        "decides-record-inequality")
    add("no_merge dropped", ["contract", "guarantees", "no_merge"],
        "first-writer-wins")
    add("no_fork dropped", ["contract", "guarantees", "no_fork"],
        "split-allowed")
    add("count drift", ["contract", "guarantees", "count_exact"],
        "at-most-n-records")
    add("arrival order", ["contract", "guarantees",
                          "order_insensitivity"],
        "bucket-arrival-order")
    add("algebra dropped", ["contract", "guarantees",
                            "merge_algebra_preserved"],
        "commutativity-lost-under-collision")
    add("failure class dropped", ["contract", "failures", "classes"],
        ["malformed_collision_record"])
    add("failure mapping drift", ["contract", "failures", "mapping",
                                  "accelerator_as_identity"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"], False)
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "accelerator_as_identity"])
    add("transparency drift", ["contract", "properties",
                               "collision_transparency"],
        "collision-visible")
    add("rollback drift", ["contract", "properties", "rollback"],
        "best-effort")
    add("link drift", ["contract", "links", "variant_contract"],
        "data/contracts/san.yaml")
    add("base path drift", ["contract", "versioning", "base_path"],
        "/graph/collision/v0")
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
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != yaml.safe_load(
                    CONTRACT.read_text())["contract"].get(section):
                covered.add(section)
    assert covered >= {"role", "definition", "separation",
                       "guarantees", "failures", "errors",
                       "properties", "links", "versioning"}


# -- linkage battery ---------------------------------------------------------


def _lint_doc(tmp_path, name, mutate, target="variant"):
    docs = {"variant": VARIANT, "digest": DIGEST, "node": NODE}
    paths = {}
    for key, src in docs.items():
        data = yaml.safe_load(src.read_text())
        if key == target:
            mutate(data)
        dst = tmp_path / f"{name}-{key}.yaml"
        dst.write_text(yaml.safe_dump(data))
        paths[key] = dst
    return paths


def _lint_with(paths):
    lint(CONTRACT, variant_path=paths["variant"],
         digest_path=paths["digest"], node_path=paths["node"])


def test_linkage_clean_copy_passes(tmp_path):
    paths = _lint_doc(tmp_path, "clean", lambda d: None)
    _lint_with(paths)


def test_linkage_variant_hash_rule_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["hash_rule"] = (
            "The hash decides identity.")
    paths = _lint_doc(tmp_path, "v", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_digest_format_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["digest"]["format"]["regex"] = (
            "^pdv2:[0-9a-f]{64}$")
    paths = _lint_doc(tmp_path, "d", drift, target="digest")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_digest_role_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["role"]["kind"] = "identity"
    paths = _lint_doc(tmp_path, "r", drift, target="digest")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_node_equality_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["equality"] = (
            "digest-comparison")
    paths = _lint_doc(tmp_path, "n", drift, target="node")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_node_exclusions_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["excluded"] = ["halfmove_clock"]
    paths = _lint_doc(tmp_path, "x", drift, target="node")
    with pytest.raises(ContractError):
        _lint_with(paths)
