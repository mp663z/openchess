"""T0172 deterministic fuzz/fault battery for the production version store."""

from __future__ import annotations

import copy
import random

import pytest

from graph.version_store import VersionError, VersionStore


def digest(n):
    return "gdv1:" + format(n, "064x")


def ts(n):
    return f"2026-03-{n:02d}T00:00:00Z"


def snapshot(s):
    return copy.deepcopy((s.records, s.root_id))


def build(seed, count=8):
    rng = random.Random(seed)
    s = VersionStore()
    records = []
    for i in range(1, count + 1):
        parents = (
            []
            if i == 1
            else rng.sample(
                [r["version_id"] for r in records], k=rng.randint(1, min(3, len(records)))
            )
        )
        r = s.make_record(parents, digest(seed * 100 + i), ts(i), f"s{seed}-{i}")
        records.append(s.insert(r))
    return s, records


@pytest.mark.parametrize("seed", range(40))
def test_random_dag_roundtrip_and_merge(seed):
    source, records = build(seed)
    shuffled = list(records)
    random.Random(seed + 9000).shuffle(shuffled)
    # Source batches may arrive out of order; merge owns topological staging.
    raw = VersionStore()
    raw._records = {r["version_id"]: copy.deepcopy(r) for r in shuffled}
    raw._root_id = records[0]["version_id"]
    target = VersionStore()
    target.merge(raw)
    assert target.records == source.records


@pytest.mark.parametrize("seed", range(40))
def test_fault_injection_is_atomic(seed):
    store, records = build(seed, 5)
    rng = random.Random(seed)
    before = snapshot(store)
    bad = copy.deepcopy(rng.choice(records))
    mode = seed % 5
    if mode == 0:
        bad["label"] = ""
    elif mode == 1:
        bad["version_id"] = "gv1:" + "f" * 64
    elif mode == 2:
        bad["parent_ids"] = ["gv1:" + "9" * 64]
    elif mode == 3:
        bad["created_at"] = "2020-01-01T00:00:00Z"
    else:
        bad["surprise"] = True
    with pytest.raises(VersionError):
        store.insert(bad)
    assert snapshot(store) == before


class BombDict(dict):
    def __deepcopy__(self, memo):
        raise RuntimeError("copy bomb")


class BombList(list):
    def __iter__(self):
        raise RuntimeError("iteration bomb")


@pytest.mark.parametrize("seed", range(20))
def test_hostile_container_fuzz_never_escapes(seed):
    store = VersionStore()
    before = snapshot(store)
    rng = random.Random(seed)
    parent = [[[]], [{}], [None], [True], [7], BombList()][rng.randrange(6)]
    calls = [
        lambda: store.insert(BombDict()),
        lambda: store.make_record(parent, digest(seed + 1), ts(1), "x"),
    ]
    for call in calls:
        with pytest.raises(VersionError) as exc:
            call()
        assert exc.value.failure_class == "malformed_version_record"
        assert snapshot(store) == before


@pytest.mark.parametrize("seed", range(20))
def test_corrupt_merge_source_is_atomic(seed):
    dst, _ = build(seed, 4)
    src, records = build(seed + 100, 4)
    before = snapshot(dst)
    victim = records[-1]["version_id"]
    mode = seed % 4
    if mode == 0:
        src._records[victim]["label"] = ""
    elif mode == 1:
        src._records[victim]["parent_ids"] = BombList()
    elif mode == 2:
        src._records[victim]["version_id"] = "gv1:" + "f" * 64
    else:
        src._records[victim]["graph_digest"] = "bad"
    with pytest.raises(VersionError):
        dst.merge(src)
    assert snapshot(dst) == before
