"""T0171 deterministic unit/property battery for production graph versions."""

from __future__ import annotations

import copy
import itertools
import random

import pytest

from graph.version_store import VersionError, VersionStore, version_id


def digest(n):
    return "gdv1:" + format(n, "064x")


def ts(n):
    return f"2026-02-{n:02d}T00:00:00Z"


def build_chain(n):
    s = VersionStore()
    records = []
    parents = []
    for i in range(1, n + 1):
        r = s.make_record(parents, digest(i), ts(i), f"v{i}")
        records.append(s.insert(r))
        parents = [r["version_id"]]
    return s, records


@pytest.mark.parametrize("seed", range(32))
def test_parent_permutation_duplication_property(seed):
    rng = random.Random(seed)
    s, records = build_chain(4)
    parents = [records[1]["version_id"], records[2]["version_id"], records[3]["version_id"]]
    supplied = [rng.choice(parents) for _ in range(8)] + parents
    rng.shuffle(supplied)
    r = s.make_record(supplied, digest(10 + seed), ts(10 + seed % 10), f"p{seed}")
    assert r["parent_ids"] == sorted(set(supplied))
    assert r["version_id"] == version_id(sorted(set(supplied)), r["graph_digest"])


@pytest.mark.parametrize("depth", range(1, 9))
def test_merge_permutation_is_deterministic(depth):
    full, records = build_chain(depth)
    parts = []
    for end in range(1, depth + 1):
        p = VersionStore()
        for r in records[:end]:
            p.insert(copy.deepcopy(r))
        parts.append(p)
    orders = [parts, list(reversed(parts)), parts[::2] + parts[1::2]]
    views = []
    for order in orders:
        dst = VersionStore()
        for part in order:
            dst.merge(part)
        views.append(dst.records)
    assert all(v == full.records for v in views)


@pytest.mark.parametrize("seed", range(24))
def test_rejection_is_atomic_property(seed):
    rng = random.Random(seed)
    s, records = build_chain(3)
    before = copy.deepcopy((s.records, s.root_id))
    choice = rng.randrange(4)
    if choice == 0:
        bad = s.make_record([], digest(20 + seed), ts(20), "root2")
    elif choice == 1:
        bad = s.make_record(["gv1:" + "9" * 64], digest(20 + seed), ts(20), "orphan")
    elif choice == 2:
        bad = s.make_record([records[-1]["version_id"]], digest(20 + seed), ts(1), "early")
    else:
        bad = s.make_record([], digest(1), ts(1), "changed")
    with pytest.raises(VersionError):
        s.insert(bad)
    assert (s.records, s.root_id) == before


def test_merge_grouping_associativity():
    full, records = build_chain(5)
    stores = []
    for n in (1, 3, 5):
        s = VersionStore()
        for r in records[:n]:
            s.insert(copy.deepcopy(r))
        stores.append(s)
    for order in itertools.permutations(stores):
        dst = VersionStore()
        for source in order:
            dst.merge(source)
        assert dst.records == full.records
