"""T0175 independent model/oracle verification of graph versions."""

from __future__ import annotations

import hashlib
import random

import pytest

from graph.version_store import VersionError, VersionStore, version_id

TS = "2026-09-21T12:00:00Z"


def _digest(n):
    return "gdv1:" + f"{n:064x}"


def _oracle_id(parents, digest):
    body = "gv1-content\n" + "".join(p + "\n" for p in sorted(set(parents))) + digest + "\n"
    return "gv1:" + hashlib.sha256(body.encode()).hexdigest()


def test_independent_id_oracle_cartesian():
    roots = [_oracle_id([], _digest(i)) for i in range(1, 6)]
    parent_sets = [[], [roots[0]], roots[:2], roots[::-1], roots + roots[:2]]
    for parents in parent_sets:
        for n in range(20, 40):
            assert version_id(list(parents), _digest(n)) == _oracle_id(parents, _digest(n))


def _model(seed, nodes=30):
    rng = random.Random(seed)
    records = []
    root = {
        "parent_ids": [],
        "graph_digest": _digest(seed * 1000 + 1),
        "created_at": TS,
        "label": f"root-{seed}",
    }
    root["version_id"] = _oracle_id([], root["graph_digest"])
    records.append(root)
    for i in range(1, nodes):
        pool = [r["version_id"] for r in records]
        count = 1 + rng.randrange(min(3, len(pool)))
        parents = sorted(rng.sample(pool, count))
        digest = _digest(seed * 1000 + i + 1)
        rec = {
            "version_id": _oracle_id(parents, digest),
            "parent_ids": parents,
            "graph_digest": digest,
            "created_at": TS,
            "label": f"n-{seed}-{i}",
        }
        records.append(rec)
    return records


@pytest.mark.parametrize("seed", range(20))
def test_independent_model_matches_production_and_shuffled_merge(seed):
    records = _model(seed)
    direct = VersionStore()
    for rec in records:
        direct.insert(rec)
    source = VersionStore()
    for rec in records:
        source.insert(rec)
    receiver = VersionStore()
    receiver.insert(records[0])
    # Source internal iteration is deliberately anti-topological.
    source._records = dict(reversed(list(source._records.items())))
    receiver.merge(source)
    expected = {r["version_id"]: r for r in records}
    assert direct.records == receiver.records == expected
    assert direct.root_id == receiver.root_id == records[0]["version_id"]
    assert direct.canonical_view() == sorted(expected)


def test_failure_precedence_and_atomicity_independent_matrix():
    store = VersionStore()
    root = _model(99, 1)[0]
    store.insert(root)
    unknown = "gv1:" + "f" * 64
    cases = [
        ({}, "malformed_version_record"),
        ({**root, "extra": 1}, "malformed_version_record"),
        ({**root, "version_id": "bad"}, "malformed_version_record"),
        ({**root, "parent_ids": [unknown]}, "malformed_version_record"),
        ({**root, "label": "bad\nlabel"}, "malformed_version_record"),
    ]
    for record, failure in cases:
        before_records, before_root = store.records, store.root_id
        with pytest.raises(VersionError) as caught:
            store.insert(record)
        assert caught.value.failure_class == failure
        assert store.records == before_records and store.root_id == before_root


def test_merge_algebra_against_independent_union_model():
    records = _model(7, 18)
    a = VersionStore()
    b = VersionStore()
    whole = VersionStore()
    for rec in records:
        whole.insert(rec)
    split = 9
    for rec in records[:split]:
        a.insert(rec)
    # b must contain ancestry, then adds suffix.
    for rec in records:
        b.insert(rec)
    a_before = a.records
    a.merge(b)
    assert a.records == whole.records
    once = a.records
    a.merge(b)
    assert a.records == once
    assert set(a_before) < set(a.records)


def test_adversarial_subclasses_rejected_without_dispatch():
    class Record(dict):
        def __iter__(self):
            raise AssertionError("iter")

        def __deepcopy__(self, memo):
            raise AssertionError("copy")

    store = VersionStore()
    before = store.records
    with pytest.raises(VersionError) as caught:
        store.insert(Record())
    assert caught.value.failure_class == "malformed_version_record"
    assert store.records == before
