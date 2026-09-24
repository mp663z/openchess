"""T0135 deterministic unit/property battery for the production
route-edge runtime (graph.route_edge, T0134).

Seeded properties over graph.route_edge only: no tests.* helpers. Edges
are drawn from a pinned position pool with random clocks and random
move-model texts, and every result is checked against an independent
model: a record is (variant, move, from snapshot, to snapshot) with the
snapshots hand-pinned below (identity en-passant value, clocks 0 1);
the table maps (variant, move, from snapshot) to exactly one to
snapshot, a same-target reinsert returns the stored record and a
different target is conflicting_edge with nothing changed.
"""

from __future__ import annotations

import ast
import copy
import random
import types
from pathlib import Path

import pytest

from graph import route_edge as re_

DOCS = re_.load_docs()
SEEDS = range(40)
MER, MP, UV, CE = (
    "malformed_edge_record",
    "malformed_position",
    "unknown_variant",
    "conflicting_edge",
)
FILES, RANKS = "abcdefgh", "12345678"

# input FEN -> hand-pinned canonical snapshot (identity en passant, clocks 0 1)
POOL = {
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1": (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    ),
    # phantom e3 (no black pawn can capture) collapses to "-"
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1": (
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    ),
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2": (
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
    ),
    # legal e3 (black d4 pawn can capture) is kept
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1": (
        "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    ),
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1": (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1"
    ),
    "4k3/P7/8/8/8/8/8/4K3 w - - 0 1": "4k3/P7/8/8/8/8/8/4K3 w - - 0 1",
    "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1": "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1",
}
FENS = list(POOL)
START, AFTER_E4, AFTER_E4_E5, LEGAL_EP, KINGS, START_KQ, PROMO_FROM, PROMO_TO = FENS
MOVES = ["e2e4", "e1e2", "a7a8q", "g1f3", "h7h8n", "a1h8"]


class _S(str):
    pass


class _D(dict):
    pass


def _clock(rng, fen):
    parts = fen.split(" ")
    parts[4] = "0" if parts[3] != "-" else str(rng.randint(0, 99))
    parts[5] = str(rng.randint(1, 300))
    return " ".join(parts)


def _move(rng):
    while True:
        a = rng.choice(FILES) + rng.choice(RANKS)
        b = rng.choice(FILES) + rng.choice(RANKS)
        if a != b:
            return a + b + rng.choice(["", "", "", "q", "r", "b", "n"])


def _edges(seed, n=16):
    """Inserts drawn from a small key pool so reinserts, clock variants
    and same-from-and-move conflicts all occur."""
    rng = random.Random(seed)
    keys = [(rng.choice(MOVES + [_move(rng)]), rng.choice(FENS)) for _ in range(5)]
    out = []
    for _ in range(n):
        move, a = rng.choice(keys)
        b = rng.choice(FENS[:3]) if rng.random() < 0.7 else rng.choice(FENS)
        out.append(("standard", move, _clock(rng, a), _clock(rng, b)))
    return out


def _canon(fen):
    parts = fen.split(" ")
    return POOL[" ".join(parts[:4] + ["0", "1"])] if parts[3] == "-" else _canon_ep(fen)


def _canon_ep(fen):
    for raw, snap in POOL.items():
        if raw.split(" ")[:4] == fen.split(" ")[:4]:
            return snap
    raise KeyError(fen)


def _model_run(edges):
    """Independent table: (variant, move, from snap) -> to snap. Returns
    (per-insert outcomes, final sorted rows)."""
    table, outcomes = {}, []
    for variant, move, a, b in edges:
        key, to = (variant, move, _canon(a)), _canon(b)
        if key in table and table[key] != to:
            outcomes.append(CE)
            continue
        table.setdefault(key, to)
        outcomes.append(
            {"variant": variant, "move": move, "from_snapshot_fen": key[2], "to_snapshot_fen": to}
        )
    return outcomes, sorted(k + (v,) for k, v in table.items())


def _outcome(call):
    """The call's result, or the failure class of the EdgeError it raised
    (production's or an in-file mutant module's own EdgeError class)."""
    try:
        return call()
    except Exception as exc:
        if type(exc).__name__ != "EdgeError" or type(exc).__module__ not in (
            re_.__name__,
            "route_edge_mutant",
        ):
            raise
        return exc.failure_class


def _run(mod, edges, digest_fn=None):
    t = mod.EdgeTable(DOCS) if digest_fn is None else mod.EdgeTable(DOCS, digest_fn)
    return t, [_outcome(lambda e=e: t.insert(*e)) for e in edges]


_FIXED = re_.digest_fen("standard", START)


def _const_digest(variant, fen):
    """A valid-format digest that is the same for every position: every
    edge with the same move shares one bucket."""
    return _FIXED


class _Src:
    def __init__(self, records):
        self._r = records

    def records(self):
        return self._r


def _rec(move, a, b, variant="standard"):
    return {
        "variant": variant,
        "move": move,
        "from_snapshot_fen": POOL[a],
        "to_snapshot_fen": POOL[b],
    }


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
    assert modules <= {"__future__", "ast", "copy", "random", "types", "pathlib", "pytest", "graph"}


# -- R2: happy - the table equals the independent model -------------------------------


def _model_mismatch(mod, seed, digest_fn=None):
    edges = _edges(seed)
    want, rows = _model_run(edges)
    t, got = _run(mod, edges, digest_fn)
    return got != want or t.serialize() != rows or len(t.records()) != len(rows)


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_table_equals_model(seed):
    assert not _model_mismatch(re_, seed)


def test_r2_seeds_exercise_reinsert_clock_variant_and_conflict():
    kinds = set()
    for seed in SEEDS:
        edges = _edges(seed)
        want, _ = _model_run(edges)
        seen = {}
        for (v, m, a, b), out in zip(edges, want, strict=True):
            key = (v, m, _canon(a))
            if out == CE:
                kinds.add("conflict")
            elif key in seen:
                kinds.add("reinsert")
                if seen[key] != (a, b):
                    kinds.add("clock-variant-reinsert")
            seen.setdefault(key, (a, b))
    assert kinds == {"conflict", "reinsert", "clock-variant-reinsert"}


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_shared_bucket_is_separated_by_field_comparison(seed):
    """With every position in one digest bucket per move, the table
    still equals the model: the bucket never decides equality."""
    assert not _model_mismatch(re_, seed, _const_digest)


def test_r2_insert_returns_exact_canonical_record():
    t = re_.EdgeTable(DOCS)
    for a in FENS:
        rec = t.insert("standard", "e2e4", a, AFTER_E4)
        assert rec == _rec("e2e4", a, AFTER_E4)
        assert list(rec) == ["variant", "move", "from_snapshot_fen", "to_snapshot_fen"]
        assert re_.validate_record(*DOCS, copy.deepcopy(rec)) == rec


# -- R3: boundary -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "move",
    ["a1h8", "h8a1", "a1a2", "h1h2", "a7a8q", "a7a8r", "a7a8b", "a7a8n", "h2h1q", "b1a3"],
)
def test_r3_boundary_moves_accepted(move):
    rec = re_.EdgeTable(DOCS).insert("standard", move, KINGS, KINGS)
    assert rec["move"] == move


@pytest.mark.parametrize("clocks", ["0 1", "99 1", "100 1", "0 300", "150 400", "7 " + "9" * 40])
def test_r3_clocks_never_reach_the_record(clocks):
    fen = KINGS[: -len("0 1")] + clocks
    assert re_.EdgeTable(DOCS).insert("standard", "e1e2", fen, fen) == _rec("e1e2", KINGS, KINGS)


def test_r3_en_passant_identity_value():
    t = re_.EdgeTable(DOCS)
    assert t.insert("standard", "e2e4", START, AFTER_E4)["to_snapshot_fen"].split()[3] == "-"
    assert t.insert("standard", "d7d5", START, LEGAL_EP)["to_snapshot_fen"].split()[3] == "e3"


def test_r3_move_application_is_not_this_layer():
    """A grammatical move between unrelated positions is a valid edge."""
    assert re_.EdgeTable(DOCS).insert("standard", "a1h8", PROMO_TO, START_KQ) == _rec(
        "a1h8", PROMO_TO, START_KQ
    )


def test_r3_same_from_different_move_and_same_move_different_from_coexist():
    t = re_.EdgeTable(DOCS, _const_digest)
    t.insert("standard", "e2e4", START, AFTER_E4)
    t.insert("standard", "e2e4", START_KQ, KINGS)  # same bucket, other from
    t.insert("standard", "e2e3", START, KINGS)  # same from, other move
    assert len(t.records()) == 3


# -- R4: malformed inputs reject typed and change nothing ------------------------------

BAD_MOVES = [
    "z2e4",
    "e9e4",
    "e2z4",
    "e2e9",
    "e2e2",
    "e7e8qq",
    "e7e8k",
    "e7e8Q",
    "e2e",
    "",
    "E2E4",
    "e2e4\u00e9"[:5],
    "\uff45\uff12e4",
]

BAD_INSERTS = [(f"move-{m!r}", ("standard", m, START, AFTER_E4), MER) for m in BAD_MOVES] + [
    ("move-none", ("standard", None, START, AFTER_E4), MER),
    ("move-int", ("standard", 5, START, AFTER_E4), MER),
    ("variant-unknown", ("chess960", "e2e4", START, AFTER_E4), UV),
    ("variant-case", ("Standard", "e2e4", START, AFTER_E4), UV),
    ("variant-subclass", (_S("standard"), "e2e4", START, AFTER_E4), UV),
    ("variant-none", (None, "e2e4", START, AFTER_E4), UV),
    ("from-none", ("standard", "e2e4", None, AFTER_E4), MP),
    ("to-bytes", ("standard", "e2e4", START, AFTER_E4.encode()), MP),
    ("from-garbage", ("standard", "e2e4", "x", AFTER_E4), MP),
    ("to-garbage", ("standard", "e2e4", START, "8/8/8 w - - 0 1"), MP),
    ("from-fullmove-0", ("standard", "e2e4", START[:-1] + "0", AFTER_E4), MP),
    ("to-ep-with-halfmove", ("standard", "e2e4", START, AFTER_E4.replace(" 0 1", " 5 1")), MP),
    ("from-clock-5000-digits", ("standard", "e2e4", START[:-1] + "9" * 5000, AFTER_E4), MP),
    ("to-clock-5000-digits", ("standard", "e2e4", START, KINGS[:-1] + "9" * 5000), MP),
    ("to-garbage-fen", ("standard", "e2e4", START, "not a fen"), MP),
]


@pytest.mark.parametrize("name,args,cls", BAD_INSERTS, ids=[b[0] for b in BAD_INSERTS])
def test_r4_malformed_insert_rejects_typed_atomically(name, args, cls):
    t, _ = _run(re_, _edges(3))
    before, ser = copy.deepcopy(t.buckets), t.serialize()
    with pytest.raises(re_.EdgeError) as err:
        t.insert(*args)
    assert (err.value.failure_class, err.value.code) == (cls, re_.FAILURE_MAPPING[cls])
    assert err.value.__cause__ is None  # fresh, never raised `from` the parser error
    assert t.buckets == before and t.serialize() == ser


GOOD = _rec("e2e4", START, AFTER_E4)
BAD_RECORDS = [
    ("not-dict", None, MER),
    ("dict-subclass", _D(GOOD), MER),
    ("missing-field", {k: v for k, v in GOOD.items() if k != "move"}, MER),
    ("extra-field", {**GOOD, "digest": "x"}, MER),
    *[
        (f"renamed-{f}", {(k + "_x" if k == f else k): v for k, v in GOOD.items()}, MER)
        for f in ("variant", "move", "from_snapshot_fen", "to_snapshot_fen")
    ],
    (
        "renamed-from-snapshot",
        {
            **{k: v for k, v in GOOD.items() if k != "from_snapshot_fen"},
            "from_snapshot": GOOD["from_snapshot_fen"],
        },
        MER,
    ),
    ("value-subclass", {**GOOD, "move": _S("e2e4")}, MER),
    ("value-none", {**GOOD, "to_snapshot_fen": None}, MER),
    ("variant-none", {**GOOD, "variant": None}, MER),
    ("variant-subclass", {**GOOD, "variant": _S("standard")}, MER),
    ("from-subclass", {**GOOD, "from_snapshot_fen": _S(GOOD["from_snapshot_fen"])}, MER),
    ("key-subclass", {_S("move"): "e2e4", **{k: v for k, v in GOOD.items() if k != "move"}}, MER),
    ("variant-unknown", {**GOOD, "variant": "chess960"}, UV),
    ("move-bad", {**GOOD, "move": "e2e9"}, MER),
    ("from-clock-not-normalized", {**GOOD, "from_snapshot_fen": START[:-3] + "3 7"}, MER),
    ("to-clock-not-normalized", {**GOOD, "to_snapshot_fen": POOL[AFTER_E4][:-3] + "0 2"}, MER),
    ("to-phantom-ep-kept", {**GOOD, "to_snapshot_fen": AFTER_E4}, MER),
    ("from-garbage", {**GOOD, "from_snapshot_fen": "x"}, MER),
    ("to-garbage", {**GOOD, "to_snapshot_fen": "x"}, MER),
]


@pytest.mark.parametrize("name,rec,cls", BAD_RECORDS, ids=[b[0] for b in BAD_RECORDS])
def test_r4_malformed_record_rejects_on_validate_and_merge(name, rec, cls):
    snap = copy.deepcopy(rec)
    with pytest.raises(re_.EdgeError) as err:
        re_.validate_record(*DOCS, rec)
    assert (err.value.failure_class, err.value.code) == (cls, re_.FAILURE_MAPPING[cls])
    assert err.value.__cause__ is None
    t, _ = _run(re_, _edges(5))
    before = copy.deepcopy(t.buckets)
    for batch in ([_rec("e1e2", KINGS, KINGS), rec], [rec, _rec("e1e2", KINGS, KINGS)]):
        with pytest.raises(re_.EdgeError) as err:
            t.merge(_Src(copy.deepcopy(batch)))
        assert err.value.failure_class == cls
        assert err.value.__cause__ is None
        assert t.buckets == before
    assert rec == snap


def _raises(variant, fen):
    raise RuntimeError("oracle down")


ORACLE_FAULTS = [
    ("raises", _raises),
    ("returns-none", lambda v, f: None),
    ("returns-bytes", lambda v, f: _FIXED.encode()),
    ("returns-str-subclass", lambda v, f: _S(_FIXED)),
    ("returns-short", lambda v, f: _FIXED[:-1]),
    ("returns-upper", lambda v, f: _FIXED.upper() if _FIXED.upper() != _FIXED else "Z" * 64),
]


@pytest.mark.parametrize("name,oracle", ORACLE_FAULTS, ids=[o[0] for o in ORACLE_FAULTS])
def test_r4_untrusted_digest_oracle_fails_closed(name, oracle):
    t = re_.EdgeTable(DOCS, oracle)
    with pytest.raises(re_.EdgeError) as err:
        t.insert("standard", "e2e4", START, AFTER_E4)
    assert err.value.failure_class == MER
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert t.buckets == {}
    with pytest.raises(re_.EdgeError) as err:
        re_.validate_record(*DOCS, dict(GOOD), oracle)
    assert err.value.failure_class == MER


class _LS(list):
    pass


@pytest.mark.parametrize("source", ["records", (GOOD,), None, {"a": GOOD}, _LS([GOOD])])
def test_r4_merge_source_must_be_exact_list(source):
    t, _ = _run(re_, _edges(6))
    before = copy.deepcopy(t.buckets)
    with pytest.raises(re_.EdgeError) as err:
        t.merge(_Src(source))
    assert err.value.failure_class == MER
    assert t.buckets == before


def test_r4_failure_classes_are_the_closed_mapping():
    assert re_.FAILURE_MAPPING == {
        MER: "malformed_request",
        MP: "malformed_request",
        UV: "unknown_variant",
        CE: "conflicting_edge",
    }


# -- R5: merge laws -----------------------------------------------------------------------


def _clean(seed):
    """The model-accepted records of a seed: a conflict-free batch."""
    want, _ = _model_run(_edges(seed))
    out = []
    for r in want:
        if r != CE and r not in out:
            out.append(r)
    return out


def _table_of(mod, recs):
    t = mod.EdgeTable(DOCS)
    for r in recs:
        t.insert(r["variant"], r["move"], r["from_snapshot_fen"], r["to_snapshot_fen"])
    return t


def _merge_laws_red(mod, seed):
    recs = _clean(seed)
    rng = random.Random(seed)
    groups = [[], [], []]
    for r in recs:
        groups[rng.randrange(3)].append(r)
    a, b, c = (_table_of(mod, g) for g in groups)
    union = _table_of(mod, recs).serialize()

    def fresh(g):
        return _table_of(mod, groups[g])

    ab = fresh(0).merge(fresh(1)).serialize()
    ba = fresh(1).merge(fresh(0)).serialize()
    left = fresh(0).merge(fresh(1)).merge(fresh(2)).serialize()
    right = fresh(0).merge(fresh(1).merge(fresh(2))).serialize()
    again = _table_of(mod, recs).merge(_table_of(mod, recs)).serialize()
    self_merge = a.merge(b).merge(c).merge(_table_of(mod, recs))
    return (
        ab != ba
        or left != right
        or left != union
        or again != union
        or self_merge.serialize() != union
        or len(self_merge.records()) != len(union)
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_r5_merge_idempotent_commutative_associative(seed):
    assert not _merge_laws_red(re_, seed)


def _conflict_merge_red(mod):
    """Tables that conflict on one from+move reject in BOTH orders and
    both destinations stay bit-identical; a batch-internal conflict
    rejects the whole merge."""
    x = _table_of(mod, [_rec("e1e2", KINGS, KINGS), _rec("e2e4", START, AFTER_E4)])
    y = _table_of(mod, [_rec("e2e4", START, KINGS), _rec("g1f3", START, START)])
    for dst, src in ((x, y), (y, x)):
        before = copy.deepcopy(dst.buckets)
        try:
            dst.merge(src)
            return True
        except mod.EdgeError as exc:
            if exc.failure_class != CE or dst.buckets != before:
                return True
    z = _table_of(mod, [_rec("e1e2", KINGS, KINGS)])
    before = copy.deepcopy(z.buckets)
    batch = [_rec("g1f3", START, START), _rec("e2e4", START, AFTER_E4), _rec("e2e4", START, KINGS)]
    try:
        z.merge(_Src(batch))
        return True
    except mod.EdgeError as exc:
        return exc.failure_class != CE or z.buckets != before


def test_r5_conflicting_merge_rejects_whole_both_orders():
    assert not _conflict_merge_red(re_)


def _shared_bucket_rollback_red(mod):
    """With every position in one bucket per move, a merge whose first
    record appends into an EXISTING shared bucket and whose later record
    fails leaves every bucket list bit-identical."""
    for tail, cls in ((_rec("e2e4", START, KINGS), CE), ({**GOOD, "move": "e2e9"}, MER)):
        dst = mod.EdgeTable(DOCS, _const_digest)
        dst.insert("standard", "e2e4", START, AFTER_E4)
        before = copy.deepcopy(dst.buckets)
        lists = {k: list(v) for k, v in dst.buckets.items()}
        batch = [_rec("e2e4", START_KQ, KINGS), tail]  # same move -> same bucket
        if _outcome(lambda b=batch, d=dst: d.merge(_Src(b))) != cls:
            return True
        if dst.buckets != before or {k: list(v) for k, v in dst.buckets.items()} != lists:
            return True
    return False


def test_r6_failed_merge_leaves_shared_bucket_lists_untouched():
    assert not _shared_bucket_rollback_red(re_)


def _frozen_source_red(mod):
    """An oracle that, on its first call during a merge, rewrites the live
    source record and appends a malformed record to the live source list
    cannot change what is validated or stored; a later rewrite of the
    source record never reaches the table."""
    live = [_rec("e1e2", KINGS, KINGS)]
    pre = copy.deepcopy(live)
    state = {"n": 0}

    def oracle(variant, fen):
        state["n"] += 1
        if state["n"] == 1:
            live[0]["to_snapshot_fen"] = POOL[START]
            live.append({**GOOD, "move": "e2e9"})
        return re_.digest_fen(variant, fen)

    dst = mod.EdgeTable(DOCS, oracle)
    if _outcome(lambda: dst.merge(_Src(live))) is not dst:
        return True
    if dst.records() != pre:
        return True
    live[0]["move"] = "a1a2"
    return dst.records() != pre


def test_r6_merge_validates_and_stores_the_frozen_source():
    assert not _frozen_source_red(re_)


# -- R6: rollback -------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r6_rejected_insert_leaves_table_bit_identical(seed):
    t = re_.EdgeTable(DOCS)
    for e in _edges(seed):
        before, ser = copy.deepcopy(t.buckets), t.serialize()
        if _outcome(lambda e=e: t.insert(*e)) == CE:
            assert t.buckets == before and t.serialize() == ser


@pytest.mark.parametrize("seed", SEEDS)
def test_r6_reinsert_returns_the_stored_record(seed):
    """insert-or-return-existing: a reinsert (any clocks) returns the
    stored record and adds nothing (T0134 pins the stored object)."""
    rng = random.Random(seed)
    t = re_.EdgeTable(DOCS)
    for e in _edges(seed):
        got = _outcome(lambda e=e: t.insert(*e))
        if got == CE:
            continue
        n = len(t.records())
        again = t.insert(e[0], e[1], _clock(rng, _canon(e[2])), _clock(rng, _canon(e[3])))
        assert again is got and len(t.records()) == n


# -- R7: determinism ------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r7_order_never_changes_a_conflict_free_table(seed):
    recs = _clean(seed)
    shuffled = list(recs)
    random.Random(seed + 1000).shuffle(shuffled)
    assert _table_of(re_, recs).serialize() == _table_of(re_, shuffled).serialize()


# -- in-file mutants: each pinned property is RED on a one-edit production mutant ------


def _move_grammar_red(mod):
    t = mod.EdgeTable(DOCS)
    for m in BAD_MOVES:
        if _outcome(lambda m=m: t.insert("standard", m, START, AFTER_E4)) != MER:
            return True
        rec = {**GOOD, "move": m}
        if _outcome(lambda r=rec: mod.validate_record(*DOCS, r)) != MER:
            return True
    return bool(t.records())


def _bucket_red(mod):
    return any(_model_mismatch(mod, s, _const_digest) for s in SEEDS)


def _model_red(mod):
    return any(_model_mismatch(mod, s) for s in SEEDS)


def _record_red(mod):
    for _name, rec, cls in BAD_RECORDS:
        record = copy.deepcopy(rec)
        try:  # a raw non-EdgeError (e.g. KeyError) is a crash: red
            if _outcome(lambda r=record: mod.validate_record(*DOCS, r)) != cls:
                return True
        except Exception:  # noqa: BLE001
            return True
    return False


def _fresh_insert_red(mod):
    for _name, args, cls in BAD_INSERTS:
        t = mod.EdgeTable(DOCS)
        try:
            t.insert(*args)
            return True
        except Exception as exc:  # noqa: BLE001 - checked below
            if getattr(exc, "failure_class", None) != cls or exc.__cause__ is not None:
                return True
    return False


def _list_subclass_red(mod):
    t = mod.EdgeTable(DOCS)
    return _outcome(lambda: t.merge(_Src(_LS([dict(GOOD)])))) != MER or bool(t.records())


def _atomic_red(mod):
    return _conflict_merge_red(mod)


MUTANTS = [
    (
        "move-square-or-to-and",
        'if square[0] not in grammar["files"] or square[1] not in grammar["ranks"]:',
        'if square[0] not in grammar["files"] and square[1] not in grammar["ranks"]:',
        _move_grammar_red,
    ),
    (
        "move-length-allows-6",
        "if len(move) not in (squares_len, squares_len + 1):",
        "if len(move) not in (squares_len, squares_len + 1, squares_len + 2):",
        _move_grammar_red,
    ),
    (
        "move-length-drops-5",
        "if len(move) not in (squares_len, squares_len + 1):",
        "if len(move) < squares_len:",
        _move_grammar_red,
    ),
    (
        "move-distinct-skipped",
        'if shape["from_to_distinct"] and squares[0] == squares[1]:',
        "if False:",
        _move_grammar_red,
    ),
    (
        "move-promotion-any-letter",
        'move[4] in shape["types"]["promotion"]["enum"])',
        "True)",
        _move_grammar_red,
    ),
    (
        "conflict-prefix-drops-from",
        "if existing_identity[:3] == new_identity[:3]:",
        "if existing_identity[:2] == new_identity[:2]:",
        _bucket_red,
    ),
    (
        "conflict-never-detected",
        '                _fail(self.ec, "conflicting_edge")',
        "                pass",
        _model_red,
    ),
    (
        "identity-drops-to",
        '                          record["variant"], record["to_snapshot_fen"]))',
        '                          record["variant"], record["from_snapshot_fen"]))',
        _model_red,
    ),
    (
        "identity-drops-from",
        '                          record["variant"], record["from_snapshot_fen"]),',
        '                          record["variant"], record["to_snapshot_fen"]),',
        _bucket_red,
    ),
    (
        "equality-by-bucket-only",
        "            if existing_identity == new_identity:",
        "            if True:",
        _bucket_red,
    ),
    (
        "insert-parser-error-chained",
        "    except (FenError, ValueError):  # ValueError: clock over int-str limit\n"
        "        failed = True",
        "    except (FenError, ValueError) as exc:  # ValueError: clock over int-str limit\n"
        '        raise EdgeError("malformed_position", "malformed_request") from exc',
        _fresh_insert_red,
    ),
    (
        "validate-key-set-by-length",
        "    if not _exact_dict(record) or set(dict.keys(record)) != set(",
        "    if not _exact_dict(record) or len(dict.keys(record)) != len(",
        _record_red,
    ),
    (
        "merge-source-list-subclass",
        "        if type(source) is not list:",
        "        if not isinstance(source, list):",
        _list_subclass_red,
    ),
    (
        "merge-stages-shared-lists",
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}",
        "        staged.buckets = dict(self.buckets)",
        _shared_bucket_rollback_red,
    ),
    (
        "merge-reads-live-source-list",
        "        source = list(source)  # the batch is read once",
        "        pass",
        _frozen_source_red,
    ),
    (
        "merge-validates-live-record",
        "            rec = {f: dict.__getitem__(rec, f) for f in _FIELDS}",
        "            pass",
        _frozen_source_red,
    ),
    (
        "merge-stages-in-place",
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}",
        "        staged.buckets = self.buckets",
        _atomic_red,
    ),
    (
        "validate-skips-to-snapshot",
        '    for key in ("from_snapshot_fen", "to_snapshot_fen"):',
        '    for key in ("from_snapshot_fen",):',
        _record_red,
    ),
    (
        "exact-dict-any-str-key",
        "return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "return type(obj) is dict and any(type(k) is str for k in dict.keys(obj))",
        _record_red,
    ),
    (
        "record-values-any-exact-str",
        "if not all(type(record[f]) is str for f in _FIELDS):",
        "if not any(type(record[f]) is str for f in _FIELDS):",
        _record_red,
    ),
    (
        "serialize-drops-to",
        '        return sorted((r["variant"], r["move"], r["from_snapshot_fen"],\n'
        '                       r["to_snapshot_fen"]) for r in self.records())',
        '        return sorted((r["variant"], r["move"], r["from_snapshot_fen"])\n'
        "                      for r in self.records())",
        _model_red,
    ),
]


def _mutant(old, new):
    src = Path(re_.__file__).read_text()
    assert src.count(old) == 1, old
    mod = types.ModuleType("route_edge_mutant")
    mod.__file__ = re_.__file__
    exec(compile(src.replace(old, new), re_.__file__, "exec"), mod.__dict__)  # noqa: S102
    return mod


@pytest.mark.parametrize("name,old,new,red", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutant_property_green_on_production(name, old, new, red):
    assert not red(re_)


@pytest.mark.parametrize("name,old,new,red", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutant_is_red(name, old, new, red):
    assert red(_mutant(old, new))
