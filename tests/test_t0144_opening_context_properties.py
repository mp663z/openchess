"""T0144 deterministic unit/property battery for the production
opening-context runtime (graph.opening_context, T0143).

Seeded properties over graph.opening_context only: no tests.* helpers.
Paths are generated from the shipped opening registry plus random
move-model texts, and every result is checked against an independent
longest-prefix model built from data/openings/registry.yaml.
"""

from __future__ import annotations

import ast
import copy
import random
from pathlib import Path

import pytest
import yaml

from graph import opening_context as oc

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = yaml.safe_load((ROOT / "data" / "openings" / "registry.yaml").read_text())["registry"]
SENTINEL = REGISTRY["none_sentinel"]
ENTRIES = REGISTRY["entries"]
FILES, RANKS = "abcdefgh", "12345678"
SEEDS = range(40)
MCR, MP, UV, UOC, CC = (
    "malformed_context_record",
    "malformed_path",
    "unknown_variant",
    "unknown_opening_code",
    "conflicting_context",
)


class _S(str):
    pass


class _L(list):
    pass


def _model(path):
    """Independent resolution: the entry whose whole move list is a
    prefix of the path, longest wins; none -> sentinel pair."""
    best = None
    for entry in ENTRIES:
        n = len(entry["moves"])
        if path[:n] == entry["moves"] and (best is None or n > len(best["moves"])):
            best = entry
    return (SENTINEL, SENTINEL) if best is None else (best["code"], best["name"])


def _move(rng):
    while True:
        a = rng.choice(FILES) + rng.choice(RANKS)
        b = rng.choice(FILES) + rng.choice(RANKS)
        if a != b:
            return a + b + (rng.choice(["", "", "", "q", "r", "b", "n"]))


def _path(rng):
    """A registry prefix (possibly empty, possibly partial) extended by
    random move-model texts."""
    base = list(rng.choice(ENTRIES)["moves"]) if rng.random() < 0.8 else []
    base = base[: rng.randint(0, len(base))] if rng.random() < 0.4 else base
    return base + [_move(rng) for _ in range(rng.randint(0, 4))]


def _paths(seed, n=12):
    rng = random.Random(seed)
    return [_path(rng) for _ in range(n)]


def _record(path):
    code, name = _model(path)
    return {
        "variant": "standard",
        "path_moves": list(path),
        "opening_code": code,
        "opening_name": name,
    }


def _err(call):
    try:
        call()
    except oc.ContextError as exc:
        return exc.failure_class, exc.code
    return None


# -- R1: production-only battery -----------------------------------------------


def test_r1_property_file_uses_no_test_helpers():
    modules = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)
    assert modules <= {"__future__", "ast", "copy", "random", "pathlib", "pytest", "yaml", "graph"}


# -- R2: happy - insert resolves by the longest registry prefix ----------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_insert_matches_independent_model(seed):
    t = oc.ContextTable()
    want = {}
    for path in _paths(seed):
        got = t.insert("standard", list(path))
        assert got == _record(path)
        want[tuple(path)] = _record(path)
    assert t.serialize() == sorted(
        ("standard", " ".join(r["path_moves"]), r["opening_code"], r["opening_name"])
        for r in want.values()
    )
    assert sorted(map(repr, t.records)) == sorted(map(repr, want.values()))


def test_r2_every_registry_entry_resolves_to_itself():
    t = oc.ContextTable()
    for entry in ENTRIES:
        rec = t.insert("standard", list(entry["moves"]))
        assert (rec["opening_code"], rec["opening_name"]) == (entry["code"], entry["name"])


# -- R3: boundary - empty path, extension refinement, path attribution ---------


def test_r3_empty_path_is_unclassified():
    rec = oc.ContextTable().insert("standard", [])
    assert rec == {
        "variant": "standard",
        "path_moves": [],
        "opening_code": SENTINEL,
        "opening_name": SENTINEL,
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_r3_extension_refinement_over_every_prefix(seed):
    """Extending a path keeps the same entry or moves to a strictly
    longer entry whose moves extend the prior entry's moves."""
    by_code = {e["code"]: e for e in ENTRIES}
    t = oc.ContextTable()
    for path in _paths(seed):
        prev = None
        for i in range(len(path) + 1):
            code = t.insert("standard", path[:i])["opening_code"]
            if prev not in (None, SENTINEL) and code != prev:
                assert code != SENTINEL
                old, new = by_code[prev]["moves"], by_code[code]["moves"]
                assert len(new) > len(old) and new[: len(old)] == old
            prev = code


def test_r3_path_attribution_two_orders_two_contexts():
    """Two move orders reaching the same position are two records with
    their own contexts: the key is the path, never the node."""
    t = oc.ContextTable()
    a = t.insert("standard", ["g1f3", "g8f6", "e2e4", "e7e5"])
    b = t.insert("standard", ["e2e4", "e7e5", "g1f3", "g8f6"])
    assert (a["opening_code"], b["opening_code"]) == ("A04", "C20")
    assert len(t.records) == 2


# -- R4: malformed inputs reject typed and change nothing ----------------------

BAD_PATHS = [
    (None, MP),
    ("e2e4", MP),
    (("e2e4",), MP),
    (_L(["e2e4"]), MP),
    ({"e2e4": 1}, MP),
    ([1], MP),
    ([None], MP),
    ([["e2e4"]], MP),
    ([_S("e2e4")], MP),
    (["e2e4 "], MP),
    (["E2E4"], MP),
    (["e2e9"], MP),
    (["i2e4"], MP),
    (["e2e2"], MP),
    (["e7e8k"], MP),
    (["e7e8qq"], MP),
    (["e2e"], MP),
    ([""], MP),
    (["\u04352e4"], MP),
    (["e2e4\n"], MP),
    (["e2e4", 7], MP),
]
BAD_VARIANTS = [None, 1, "Standard", "standard ", "", "c960", _S("standard"), b"standard"]


@pytest.mark.parametrize("i", range(len(BAD_PATHS)))
def test_r4_bad_paths_reject_typed_atomically(i):
    path, cls = BAD_PATHS[i]
    t = oc.ContextTable()
    t.insert("standard", ["e2e4", "c7c5"])
    before, snap = t.serialize(), copy.deepcopy(path)
    assert _err(lambda: t.insert("standard", path)) == (cls, oc.FAILURE_MAPPING[cls])
    assert t.serialize() == before
    assert type(path) is type(snap) and path == snap


@pytest.mark.parametrize("variant", BAD_VARIANTS, ids=repr)
def test_r4_bad_variants_reject_typed_atomically(variant):
    t = oc.ContextTable()
    assert _err(lambda: t.insert(variant, ["e2e4"])) == (UV, oc.FAILURE_MAPPING[UV])
    assert t.serialize() == []


def test_r4_variant_decided_before_path():
    assert _err(lambda: oc.ContextTable().insert("nope", None))[0] == UV


# -- R5: merge algebra and atomic rejection ------------------------------------


def _table(paths):
    t = oc.ContextTable()
    for p in paths:
        t.insert("standard", list(p))
    return t


@pytest.mark.parametrize("seed", SEEDS)
def test_r5_merge_idempotent_commutative_associative(seed):
    rng = random.Random(seed)
    groups = [[_path(rng) for _ in range(rng.randint(0, 5))] for _ in range(3)]
    union = _table([p for g in groups for p in g]).serialize()
    a, b, c = (_table(g) for g in groups)
    assert _table(groups[0]).merge(b).serialize() == _table(groups[1]).merge(a).serialize()
    left = _table(groups[0]).merge(_table(groups[1])).merge(c)
    right = _table(groups[0]).merge(_table(groups[1]).merge(c))
    assert left.serialize() == right.serialize() == union
    again = copy.deepcopy(left.serialize())
    left.merge(_table(groups[2]))
    left.merge(left)
    assert left.serialize() == again


def _drifted_registry(mutate):
    reg = copy.deepcopy(REGISTRY)
    mutate(reg)
    return reg


def test_r5_unknown_code_merge_rejects_atomically():
    """A source record whose code the receiver's registry does not know
    is unknown_opening_code; nothing of the batch is committed."""
    reg = _drifted_registry(
        lambda r: r["entries"].append({"code": "B99", "name": "Mystery", "moves": ["a2a3"]})
    )
    src = oc.ContextTable(registry=reg)
    src.insert("standard", ["e2e4", "c7c5"])  # valid for the receiver
    src.insert("standard", ["a2a3"])  # B99, unknown to it
    dst = _table([["d2d4", "d7d5", "c2c4"]])
    before, records = dst.serialize(), dst.records
    assert _err(lambda: dst.merge(src)) == (UOC, oc.FAILURE_MAPPING[UOC])
    assert dst.serialize() == before and dst.records == records


def _rename_c20(reg):
    for entry in reg["entries"]:
        if entry["code"] == "C20":
            entry["code"], entry["name"] = "C21", "Drifted Name"


def _rename_c20_name(reg):
    for entry in reg["entries"]:
        if entry["code"] == "C20":
            entry["name"] = "King Pawn"


@pytest.mark.parametrize(
    "mutate", [_rename_c20, _rename_c20_name], ids=["code-and-name", "name-only"]
)
def test_r5_conflicting_registry_snapshots_both_orders_reject(mutate):
    """Contract merge rule: for an existing key the exact incoming
    record is compared with the stored record, and a context mismatch
    is conflicting_context before any mutation - in both orders."""
    path = ["e2e4", "e7e5", "g1f3", "g8f6"]
    a = _table([path, ["d2d4", "d7d5", "c2c4"]])
    b = oc.ContextTable(registry=_drifted_registry(mutate))
    b.insert("standard", path)
    for x, y in ((a, b), (b, a)):
        before = x.serialize()
        assert _err(lambda x=x, y=y: x.merge(y)) == (CC, oc.FAILURE_MAPPING[CC])
        assert x.serialize() == before


def test_r5_merge_rejects_non_table_sources():
    t = _table([["e2e4"]])
    for bad in (None, [], {}, "t", object()):
        assert _err(lambda bad=bad: t.merge(bad))[0] == MCR
        assert t.serialize() == [("standard", "e2e4", SENTINEL, SENTINEL)]


# -- R6: rollback and conflicting reinsert -------------------------------------


def test_r6_registry_drift_reinsert_conflicts_and_rolls_back():
    t = _table([["e2e4", "e7e5"]])
    before = t.serialize()
    _rename_c20_name(t.registry)
    assert _err(lambda: t.insert("standard", ["e2e4", "e7e5"])) == (CC, oc.FAILURE_MAPPING[CC])
    assert t.serialize() == before
    # a following valid insert still lands
    t.insert("standard", ["d2d4"])
    assert len(t.serialize()) == 2


@pytest.mark.parametrize("seed", SEEDS)
def test_r6_reinsert_returns_existing_and_is_idempotent(seed):
    t = oc.ContextTable()
    for path in _paths(seed):
        first = t.insert("standard", list(path))
        size = len(t.serialize())
        assert t.insert("standard", list(path)) == first
        assert len(t.serialize()) == size


# -- R7: detached views and determinism ----------------------------------------


def test_r7_returned_values_are_detached():
    t = oc.ContextTable()
    path = ["e2e4", "c7c5"]
    rec = t.insert("standard", path)
    path.append("g1f3")
    rec["opening_code"] = "X"
    rec["path_moves"].append("zz")
    view = t.records
    view[0]["opening_name"] = "X"
    view.append({})
    t.map.clear()
    assert t.serialize() == [("standard", "e2e4 c7c5", "B20", "Sicilian Defense")]


@pytest.mark.parametrize("seed", SEEDS)
def test_r7_determinism_by_value(seed):
    a = _table(_paths(seed))
    b = _table(list(reversed(_paths(seed))))
    assert a.serialize() == b.serialize() == _table(_paths(seed)).serialize()
