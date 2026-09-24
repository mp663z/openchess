"""T0153 deterministic unit/property battery for the production
provenance runtime (graph.provenance, T0152).

Seeded properties over graph.provenance only: no tests.* helpers. Records
are generated across all three sibling target kinds with random source
sets, and every result is checked against an independent model: key =
(target_kind, target identity), sources = the set union ordered by
(source_id, game_id, first_observed_at).
"""

from __future__ import annotations

import ast
import copy
import random
from pathlib import Path

import pytest
import yaml

from graph import provenance as pv

ROOT = Path(__file__).resolve().parents[1]
SOURCE_IDS = sorted(
    e["id"]
    for e in yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"][
        "sources"
    ]["entries"]
)
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
START_KQ = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1"
FENS = [START, AFTER_E4, AFTER_E4_E5, START_KQ]
EDGES = [("e2e4", START, AFTER_E4), ("e7e5", AFTER_E4, AFTER_E4_E5)]
FILES, RANKS = "abcdefgh", "12345678"
SEEDS = range(40)
MPR, UTK, MTI, US = (
    "malformed_provenance_record",
    "unknown_target_kind",
    "malformed_target_identity",
    "unknown_source",
)


class _S(str):
    pass


class _L(list):
    pass


class _D(dict):
    pass


# -- generators and the independent model -----------------------------------------


def _move(rng):
    while True:
        a = rng.choice(FILES) + rng.choice(RANKS)
        b = rng.choice(FILES) + rng.choice(RANKS)
        if a != b:
            return a + b + rng.choice(["", "", "", "q", "r", "b", "n"])


def _stamp(rng):
    return "20%02d-%02d-%02dT%02d:%02d:%02dZ" % (
        rng.randint(0, 99),
        rng.randint(1, 12),
        rng.randint(1, 28),
        rng.randint(0, 23),
        rng.randint(0, 59),
        rng.randint(0, 59),
    )


def _source(rng):
    game = "".join(chr(rng.randint(0x21, 0x7E)) for _ in range(rng.randint(1, 12)))
    return {
        "source_id": rng.choice(SOURCE_IDS),
        "game_id": game,
        "first_observed_at": _stamp(rng),
    }


def _target(rng):
    kind = rng.choice(["transposition_node", "route_edge", "opening_context"])
    if kind == "transposition_node":
        return kind, {"variant": "standard", "snapshot_fen": rng.choice(FENS)}
    if kind == "route_edge":
        move, a, b = rng.choice(EDGES)
        return kind, {
            "variant": "standard",
            "move": move,
            "from_snapshot_fen": a,
            "to_snapshot_fen": b,
        }
    path = [_move(rng) for _ in range(rng.randint(0, 3))]
    return kind, {"variant": "standard", "path_moves": path}


def _record(rng, pool=None):
    kind, target = rng.choice(pool) if pool else _target(rng)
    srcs = [_source(rng) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.3:
        srcs.append(dict(srcs[0]))  # duplicate entry
    return {"target_kind": kind, "target": copy.deepcopy(target), "sources": srcs}


def _records(seed, n=14):
    rng = random.Random(seed)
    pool = [_target(rng) for _ in range(5)]  # shared targets force unions
    return [_record(rng, pool) for _ in range(n)]


def _model_key(rec):
    t = rec["target"]
    if rec["target_kind"] == "transposition_node":
        return ("node", t["variant"], tuple(t["snapshot_fen"].split(" ")[:4]))
    if rec["target_kind"] == "route_edge":
        return (
            "edge",
            t["variant"],
            t["move"],
            tuple(t["from_snapshot_fen"].split(" ")[:4]),
            tuple(t["to_snapshot_fen"].split(" ")[:4]),
        )
    return ("ctx", t["variant"], tuple(t["path_moves"]))


def _skey(s):
    return (s["source_id"], s["game_id"], s["first_observed_at"])


def _model(records):
    out = {}
    for rec in records:
        k = _model_key(rec)
        if k not in out:
            out[k] = {"target_kind": rec["target_kind"], "target": rec["target"], "srcs": {}}
        for s in rec["sources"]:
            out[k]["srcs"][_skey(s)] = s
    return {
        k: {
            "target_kind": v["target_kind"],
            "target": v["target"],
            "sources": [v["srcs"][x] for x in sorted(v["srcs"])],
        }
        for k, v in out.items()
    }


def _by_model_key(records):
    return {_model_key(r): r for r in records}


def _table(records):
    t = pv.ProvenanceTable()
    for r in records:
        t.insert(copy.deepcopy(r))
    return t


def _err(call):
    try:
        call()
    except pv.ProvenanceError as exc:
        return exc.failure_class, exc.code
    return None


# -- R1: production-only battery ----------------------------------------------------


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


# -- R2: happy - insert-or-union equals the independent model -----------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_table_equals_union_model(seed):
    recs = _records(seed)
    t = pv.ProvenanceTable()
    seen = []
    for r in recs:
        seen.append(r)
        got = t.insert(copy.deepcopy(r))
        assert got == _model(seen)[_model_key(r)]
    assert _by_model_key(t.records()) == _model(recs)
    assert len(t.serialize()) == len(_model(recs))


def test_r2_every_kind_round_trips():
    rng = random.Random(7)
    for kind, target in [
        ("transposition_node", {"variant": "standard", "snapshot_fen": START}),
        (
            "route_edge",
            {
                "variant": "standard",
                "move": "e2e4",
                "from_snapshot_fen": START,
                "to_snapshot_fen": AFTER_E4,
            },
        ),
        ("opening_context", {"variant": "standard", "path_moves": ["e2e4", "c7c5"]}),
    ]:
        src = _source(rng)
        rec = {"target_kind": kind, "target": target, "sources": [src]}
        assert pv.validate_record(rec) == rec
        assert pv.ProvenanceTable().insert(rec) == rec


# -- R3: boundary ---------------------------------------------------------------------


def _ctx(path=("e2e4",), **src_edit):
    src = {"source_id": "pgn-file", "game_id": "g", "first_observed_at": "2026-09-01T00:00:00Z"}
    src.update(src_edit)
    return {
        "target_kind": "opening_context",
        "target": {"variant": "standard", "path_moves": list(path)},
        "sources": [src],
    }


@pytest.mark.parametrize(
    "edit",
    [
        {"game_id": "!"},
        {"game_id": "~"},
        {"game_id": "!" + "~" * 200},
        {"first_observed_at": "2024-02-29T23:59:59Z"},
        {"first_observed_at": "0000-01-01T00:00:00Z"},
        {"first_observed_at": "9999-12-31T23:59:59Z"},
    ],
    ids=repr,
)
def test_r3_boundary_values_accepted(edit):
    assert pv.validate_record(_ctx(**edit))["sources"][0] == {**_ctx()["sources"][0], **edit}


def test_r3_empty_path_and_promotion_targets():
    assert pv.validate_record(_ctx(path=()))["target"]["path_moves"] == []
    assert pv.validate_record(_ctx(path=("e7e8q", "a2a1n")))["target"]["path_moves"] == [
        "e7e8q",
        "a2a1n",
    ]


def test_r3_node_identity_ignores_nothing_but_clocks_and_is_key():
    """Two records for the same node collapse into one union record; a
    different castling field is a different node."""
    rng = random.Random(1)
    a = {
        "target_kind": "transposition_node",
        "target": {"variant": "standard", "snapshot_fen": START},
    }
    b = {**a, "target": {"variant": "standard", "snapshot_fen": START_KQ}}
    t = pv.ProvenanceTable()
    t.insert({**a, "sources": [_source(rng)]})
    t.insert({**a, "sources": [_source(rng)]})
    t.insert({**b, "sources": [_source(rng)]})
    assert sorted(len(r["sources"]) for r in t.records()) == [1, 2]


def test_r3_duplicate_sources_collapse_and_sort():
    s1 = {"source_id": "pgn-file", "game_id": "b", "first_observed_at": "2026-01-01T00:00:00Z"}
    s0 = {"source_id": "pgn-file", "game_id": "a", "first_observed_at": "2026-01-01T00:00:00Z"}
    rec = {**_ctx(), "sources": [s1, s0, dict(s1)]}
    assert pv.validate_record(rec)["sources"] == [s0, s1]


# -- R4: malformed inputs reject typed and change nothing --------------------------

BAD = [
    ("not-dict", None, MPR),
    ("dict-subclass", _D(_ctx()), MPR),
    ("missing-field", {"target_kind": "opening_context", "target": {}}, MPR),
    ("extra-field", {**_ctx(), "x": 1}, MPR),
    ("kind-unknown", {**_ctx(), "target_kind": "game"}, UTK),
    ("kind-subclass", {**_ctx(), "target_kind": _S("opening_context")}, UTK),
    ("kind-none", {**_ctx(), "target_kind": None}, UTK),
    ("sources-empty", {**_ctx(), "sources": []}, MPR),
    ("sources-tuple", {**_ctx(), "sources": tuple(_ctx()["sources"])}, MPR),
    ("sources-subclass", {**_ctx(), "sources": _L(_ctx()["sources"])}, MPR),
    ("source-not-dict", {**_ctx(), "sources": ["x"]}, MPR),
    ("source-extra", {**_ctx(), "sources": [{**_ctx()["sources"][0], "x": 1}]}, MPR),
    ("source-unknown", _ctx(source_id="fics"), US),
    ("source-case", _ctx(source_id="PGN-FILE"), US),
    ("game-empty", _ctx(game_id=""), MPR),
    ("game-space", _ctx(game_id="a b"), MPR),
    ("game-del", _ctx(game_id="a\x7f"), MPR),
    ("game-unicode", _ctx(game_id="g\u00e9"), MPR),
    ("game-subclass", _ctx(game_id=_S("g")), MPR),
    ("game-int", _ctx(game_id=5), MPR),
    ("ts-feb29", _ctx(first_observed_at="2026-02-29T00:00:00Z"), MPR),
    ("ts-offset", _ctx(first_observed_at="2026-09-01T00:00:00+00:00"), MPR),
    ("ts-fraction", _ctx(first_observed_at="2026-09-01T00:00:00.1Z"), MPR),
    ("ts-lower-z", _ctx(first_observed_at="2026-09-01T00:00:00z"), MPR),
    ("ts-hour-24", _ctx(first_observed_at="2026-09-01T24:00:00Z"), MPR),
    ("ts-digits", _ctx(first_observed_at="\u0662026-09-01T00:00:00Z"), MPR),
    ("ts-subclass", _ctx(first_observed_at=_S("2026-09-01T00:00:00Z")), MPR),
    (
        "ctx-target-extra",
        {**_ctx(), "target": {"variant": "standard", "path_moves": [], "x": 1}},
        MTI,
    ),
    ("ctx-variant", {**_ctx(), "target": {"variant": "nope", "path_moves": []}}, MTI),
    (
        "ctx-variant-subclass",
        {**_ctx(), "target": {"variant": _S("standard"), "path_moves": []}},
        MTI,
    ),
    ("ctx-path-tuple", {**_ctx(), "target": {"variant": "standard", "path_moves": ("e2e4",)}}, MTI),
    (
        "ctx-path-subclass",
        {**_ctx(), "target": {"variant": "standard", "path_moves": _L(["e2e4"])}},
        MTI,
    ),
    ("ctx-move-bad", _ctx(path=("e2e2",)), MTI),
    ("ctx-move-upper", _ctx(path=("e7e8Q",)), MTI),
    ("ctx-move-king", _ctx(path=("e7e8k",)), MTI),
    ("ctx-move-subclass", _ctx(path=(_S("e2e4"),)), MTI),
    (
        "node-noncanonical-clock",
        {
            **_ctx(),
            "target_kind": "transposition_node",
            "target": {"variant": "standard", "snapshot_fen": START[:-3] + "5 9"},
        },
        MTI,
    ),
    (
        "node-noncanonical-ep",
        {
            **_ctx(),
            "target_kind": "transposition_node",
            "target": {"variant": "standard", "snapshot_fen": AFTER_E4.replace(" - ", " e3 ")},
        },
        MTI,
    ),
    (
        "node-huge-clock",
        {
            **_ctx(),
            "target_kind": "transposition_node",
            "target": {"variant": "standard", "snapshot_fen": START[:-3] + "0 " + "9" * 5000},
        },
        MTI,
    ),
    (
        "node-garbage",
        {
            **_ctx(),
            "target_kind": "transposition_node",
            "target": {"variant": "standard", "snapshot_fen": "x"},
        },
        MTI,
    ),
    (
        "node-variant",
        {
            **_ctx(),
            "target_kind": "transposition_node",
            "target": {"variant": "zz", "snapshot_fen": START},
        },
        MTI,
    ),
    ("node-wrong-shape", {**_ctx(), "target_kind": "transposition_node"}, MTI),
    (
        "edge-bad-move",
        {
            **_ctx(),
            "target_kind": "route_edge",
            "target": {
                "variant": "standard",
                "move": "e2e2",
                "from_snapshot_fen": START,
                "to_snapshot_fen": AFTER_E4,
            },
        },
        MTI,
    ),
    (
        "edge-noncanonical",
        {
            **_ctx(),
            "target_kind": "route_edge",
            "target": {
                "variant": "standard",
                "move": "e2e4",
                "from_snapshot_fen": START,
                "to_snapshot_fen": AFTER_E4.replace(" - ", " e3 "),
            },
        },
        MTI,
    ),
    ("edge-wrong-shape", {**_ctx(), "target_kind": "route_edge"}, MTI),
]


@pytest.mark.parametrize("name, rec, cls", BAD, ids=[b[0] for b in BAD])
def test_r4_malformed_rejects_typed_atomically(name, rec, cls):
    t = _table(_records(3))
    before, snap = t.serialize(), copy.deepcopy(rec)
    assert _err(lambda: pv.validate_record(rec)) == (cls, pv.FAILURE_MAPPING[cls])
    assert _err(lambda: t.insert(rec)) == (cls, pv.FAILURE_MAPPING[cls])
    assert t.serialize() == before
    assert rec == snap and type(rec) is type(snap)


def test_r4_errors_are_closed_and_non_retryable():
    for cls, code in pv.FAILURE_MAPPING.items():
        e = pv.ProvenanceError(cls)
        assert (e.failure_class, e.code, e.retryable) == (cls, code, False)
    with pytest.raises(ValueError):
        pv.ProvenanceError("conflicting_provenance")


# -- R5: merge algebra and atomic rejection -----------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r5_merge_idempotent_commutative_associative(seed):
    recs = _records(seed, 15)
    g = [recs[:5], recs[5:10], recs[10:]]
    union = _table(recs).serialize()
    assert (
        _table(g[0]).merge(_table(g[1])).serialize() == _table(g[1]).merge(_table(g[0])).serialize()
    )
    left = _table(g[0]).merge(_table(g[1])).merge(_table(g[2]))
    right = _table(g[0]).merge(_table(g[1]).merge(_table(g[2])))
    assert left.serialize() == right.serialize() == union
    assert _by_model_key(left.records()) == _model(recs)
    left.merge(_table(g[2]))
    left.merge(left)
    assert left.serialize() == union


class _Bag:
    def __init__(self, records):
        self._r = records

    def records(self):
        return self._r


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize(
    "name, bad, cls", BAD[:6] + BAD[12:14], ids=[b[0] for b in BAD[:6] + BAD[12:14]]
)
def test_r5_failing_batch_rejects_whole_merge_both_orders(seed, name, bad, cls):
    recs = _records(seed)
    dst = _table(recs[:6])
    batch = copy.deepcopy(recs[6:]) + [bad]
    before = copy.deepcopy(dst._records)
    assert _err(lambda: dst.merge(_Bag(batch))) == (cls, pv.FAILURE_MAPPING[cls])
    assert dst._records == before
    batch_rev = [bad] + copy.deepcopy(recs[6:])
    assert _err(lambda: dst.merge(_Bag(batch_rev))) == (cls, pv.FAILURE_MAPPING[cls])
    assert dst._records == before


def test_r5_merge_source_without_records_rejects():
    t = _table(_records(1))
    before = t.serialize()
    for bad in (None, [], {}, object(), type("X", (), {"records": 5})()):
        assert _err(lambda bad=bad: t.merge(bad))[0] == MPR
    assert t.serialize() == before


# -- R6: rollback and detachment --------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r6_reinsert_is_noop_and_returns_union(seed):
    t = pv.ProvenanceTable()
    for r in _records(seed):
        got = t.insert(copy.deepcopy(r))
        size = t.serialize()
        assert t.insert(copy.deepcopy(r)) == got
        assert t.serialize() == size


def test_r6_views_and_inputs_are_detached():
    rec = _ctx()
    t = pv.ProvenanceTable()
    out = t.insert(rec)
    rec["sources"][0]["game_id"] = "POISON"
    rec["target"]["path_moves"].append("a2a3")
    out["sources"].clear()
    view = t.records()
    view[0]["target"]["variant"] = "POISON"
    view.append({})
    assert t.records() == [_ctx()]


def test_r6_merge_does_not_alias_source_records():
    src = _table([_ctx()])
    dst = pv.ProvenanceTable().merge(src)
    next(iter(src._records.values()))["sources"][0]["game_id"] = "POISON"
    assert dst.records() == [_ctx()]


# -- R7: determinism ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r7_determinism_by_value_and_order(seed):
    recs = _records(seed)
    rng = random.Random(seed + 1000)
    shuffled = copy.deepcopy(recs)
    rng.shuffle(shuffled)
    assert _table(recs).serialize() == _table(shuffled).serialize() == _table(recs).serialize()
