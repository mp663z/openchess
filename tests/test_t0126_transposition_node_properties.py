"""T0126 deterministic unit/property battery for the production
transposition-node runtime (graph.transposition_node, T0125).

Seeded properties over graph.transposition_node only: no tests.*
helpers. Inserts are drawn from a pinned position pool with random
clocks, and every result is checked against an independent model: a
node is (variant, digest, snapshot) with the snapshot hand-pinned below
(identity en-passant value, clocks 0 1) and the digest the linked
graph.position_digest runtime gives that snapshot; one identity is one
node for any path or clocks, and the digest bucket never decides
equality.
"""

from __future__ import annotations

import ast
import copy
import random
import types
from pathlib import Path

import pytest

from graph import transposition_node as tn
from graph.position_digest import digest_fen

DOCS = tn.load_docs()
SEEDS = range(40)
MNR, MP, UV = "malformed_node_record", "malformed_position", "unknown_variant"

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
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1": "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1": (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1"
    ),
    "4k3/P7/8/8/8/8/8/4K3 w - - 0 1": "4k3/P7/8/8/8/8/8/4K3 w - - 0 1",
    "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1": "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1",
}
FENS = list(POOL)
START, AFTER_E4, AFTER_E4_E5, LEGAL_EP, KINGS, KINGS_B, START_KQ, PROMO_FROM, PROMO_TO = FENS


class _S(str):
    pass


class _D(dict):
    pass


def _clock(rng, fen):
    parts = fen.split(" ")
    parts[4] = "0" if parts[3] != "-" else str(rng.randint(0, 99))
    parts[5] = str(rng.randint(1, 300))
    return " ".join(parts)


def _canon(fen):
    head = fen.split(" ")[:4]
    return next(snap for raw, snap in POOL.items() if raw.split(" ")[:4] == head)


def _node(fen, variant="standard"):
    snap = _canon(fen)
    return {"variant": variant, "digest": digest_fen(variant, snap), "snapshot_fen": snap}


def _inserts(seed, n=16):
    rng = random.Random(seed)
    keys = rng.sample(FENS, 5)
    return [("standard", _clock(rng, rng.choice(keys))) for _ in range(n)]


def _model_run(inserts):
    table = {}
    outcomes = []
    for variant, fen in inserts:
        rec = _node(fen, variant)
        table.setdefault(rec["snapshot_fen"], rec)
        outcomes.append(table[rec["snapshot_fen"]])
    return outcomes, sorted((r["variant"], r["digest"], r["snapshot_fen"]) for r in table.values())


def _outcome(call):
    """The call's result, or the failure class of the NodeError it raised
    (production's or an in-file mutant module's own NodeError class)."""
    try:
        return call()
    except Exception as exc:
        if type(exc).__name__ != "NodeError" or type(exc).__module__ not in (
            tn.__name__,
            "transposition_node_mutant",
        ):
            raise
        return exc.failure_class


def _run(mod, inserts, digest_fn=None):
    t = mod.NodeTable(DOCS) if digest_fn is None else mod.NodeTable(DOCS, digest_fn)
    return t, [_outcome(lambda e=e: t.insert(*e)) for e in inserts]


_FIXED = digest_fen("standard", KINGS)


def _const_digest(variant, fen):
    """A valid-format digest that is the same for every position: every
    node shares one bucket."""
    return _FIXED


class _Src:
    def __init__(self, records):
        self._r = records

    def records(self):
        return self._r


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
    assert modules <= {
        "__future__",
        "ast",
        "copy",
        "random",
        "types",
        "pathlib",
        "pytest",
        "graph",
        "graph.position_digest",
    }


# -- R2: happy - the table equals the independent model -------------------------------


def _model_mismatch(mod, seed, digest_fn=None):
    inserts = _inserts(seed)
    want, rows = _model_run(inserts)
    t, got = _run(mod, inserts, digest_fn)
    if digest_fn is not None:  # digests come from the injected oracle
        want = [{**w, "digest": digest_fn("standard", w["snapshot_fen"])} for w in want]
        rows = sorted((v, digest_fn(v, s), s) for v, _d, s in rows)
    return got != want or t.serialize() != rows or len(t.records()) != len(rows)


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_table_equals_model(seed):
    assert not _model_mismatch(tn, seed)


def test_r2_seeds_exercise_clock_variant_reinsert():
    hits = 0
    for seed in SEEDS:
        seen = {}
        for _v, fen in _inserts(seed):
            snap = _canon(fen)
            if snap in seen and seen[snap] != fen:
                hits += 1
            seen.setdefault(snap, fen)
    assert hits > len(SEEDS)


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_shared_bucket_is_separated_by_field_comparison(seed):
    """Every position in one digest bucket: the table still equals the
    model, so the bucket never decides equality."""
    assert not _model_mismatch(tn, seed, _const_digest)


def test_r2_insert_returns_exact_canonical_record():
    t = tn.NodeTable(DOCS)
    for fen in FENS:
        rec = t.insert("standard", fen)
        assert rec == _node(fen)
        assert list(rec) == ["variant", "digest", "snapshot_fen"]
        assert tn.validate_record(*DOCS, copy.deepcopy(rec)) == rec


# -- R3: boundary -----------------------------------------------------------------------


@pytest.mark.parametrize("clocks", ["0 1", "99 1", "100 1", "0 300", "150 400", "7 " + "9" * 40])
def test_r3_clocks_never_reach_the_record(clocks):
    fen = KINGS[: -len("0 1")] + clocks
    assert tn.NodeTable(DOCS).insert("standard", fen) == _node(KINGS)


def test_r3_en_passant_identity_value():
    t = tn.NodeTable(DOCS)
    assert t.insert("standard", AFTER_E4)["snapshot_fen"].split()[3] == "-"
    assert t.insert("standard", LEGAL_EP)["snapshot_fen"].split()[3] == "e3"
    # the phantom-ep input and its collapsed form are the same node
    t.insert("standard", _canon(AFTER_E4))
    assert len(t.records()) == 2


def test_r3_side_to_move_and_castling_separate_nodes():
    t = tn.NodeTable(DOCS, _const_digest)
    for fen in (KINGS, KINGS_B, START, START_KQ):
        t.insert("standard", fen)
    assert len(t.records()) == 4


# -- R4: malformed inputs reject typed and change nothing ------------------------------

BAD_INSERTS = [
    ("variant-unknown", ("chess960", START), UV),
    ("variant-case", ("Standard", START), UV),
    ("variant-subclass", (_S("standard"), START), UV),
    ("variant-none", (None, START), UV),
    ("fen-none", ("standard", None), MP),
    ("fen-bytes", ("standard", START.encode()), MP),
    ("fen-subclass", ("standard", _S(START)), MP),
    ("fen-garbage", ("standard", "x"), MP),
    ("fen-short-board", ("standard", "8/8/8 w - - 0 1"), MP),
    ("fen-fullmove-0", ("standard", START[:-1] + "0"), MP),
    ("fen-ep-with-halfmove", ("standard", AFTER_E4.replace(" 0 1", " 5 1")), MP),
    ("fen-no-kings", ("standard", "8/8/8/8/8/8/8/8 w - - 0 1"), MP),
    ("fen-clock-5000-digits", ("standard", START[:-1] + "9" * 5000), MP),
]


@pytest.mark.parametrize("name,args,cls", BAD_INSERTS, ids=[b[0] for b in BAD_INSERTS])
def test_r4_malformed_insert_rejects_typed_atomically(name, args, cls):
    t, _ = _run(tn, _inserts(3))
    before, ser = copy.deepcopy(t.buckets), t.serialize()
    with pytest.raises(tn.NodeError) as err:
        t.insert(*args)
    assert (err.value.failure_class, err.value.code) == (cls, tn.FAILURE_MAPPING[cls])
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert t.buckets == before and t.serialize() == ser


GOOD = _node(AFTER_E4)
BAD_RECORDS = [
    ("not-dict", None, MNR),
    ("dict-subclass", _D(GOOD), MNR),
    ("missing-field", {k: v for k, v in GOOD.items() if k != "digest"}, MNR),
    ("extra-field", {**GOOD, "path": []}, MNR),
    *[
        (f"renamed-{f}", {(k + "_x" if k == f else k): v for k, v in GOOD.items()}, MNR)
        for f in ("variant", "digest", "snapshot_fen")
    ],
    (
        "renamed-snapshot",
        {"variant": "standard", "digest": GOOD["digest"], "snapshot": GOOD["snapshot_fen"]},
        MNR,
    ),
    (
        "key-subclass",
        {_S("digest"): GOOD["digest"], "variant": "standard", "snapshot_fen": GOOD["snapshot_fen"]},
        MNR,
    ),
    ("variant-none", {**GOOD, "variant": None}, MNR),
    ("variant-subclass", {**GOOD, "variant": _S("standard")}, MNR),
    ("digest-subclass", {**GOOD, "digest": _S(GOOD["digest"])}, MNR),
    ("snapshot-subclass", {**GOOD, "snapshot_fen": _S(GOOD["snapshot_fen"])}, MNR),
    ("variant-unknown", {**GOOD, "variant": "chess960"}, UV),
    ("digest-upper", {**GOOD, "digest": GOOD["digest"].upper()}, MNR),
    ("digest-short", {**GOOD, "digest": GOOD["digest"][:-1]}, MNR),
    ("digest-trailing-newline", {**GOOD, "digest": GOOD["digest"] + "\n"}, MNR),
    ("digest-of-other-node", {**GOOD, "digest": _node(START)["digest"]}, MNR),
    ("halfmove-not-0", {**GOOD, "snapshot_fen": GOOD["snapshot_fen"][:-3] + "1 1"}, MNR),
    ("fullmove-not-1", {**GOOD, "snapshot_fen": GOOD["snapshot_fen"][:-3] + "0 2"}, MNR),
    ("phantom-ep-kept", {**GOOD, "snapshot_fen": AFTER_E4}, MNR),
    ("legal-ep-dropped", {**_node(LEGAL_EP), "snapshot_fen": LEGAL_EP.replace(" e3 ", " - ")}, MNR),
    ("snapshot-garbage", {**GOOD, "snapshot_fen": "x"}, MNR),
    (
        "snapshot-clock-5000-digits",
        {**GOOD, "snapshot_fen": GOOD["snapshot_fen"][:-1] + "1" * 5000},
        MNR,
    ),
]


@pytest.mark.parametrize("name,rec,cls", BAD_RECORDS, ids=[b[0] for b in BAD_RECORDS])
def test_r4_malformed_record_rejects_on_validate_and_merge(name, rec, cls):
    snap = copy.deepcopy(rec)
    with pytest.raises(tn.NodeError) as err:
        tn.validate_record(*DOCS, rec)
    assert (err.value.failure_class, err.value.code) == (cls, tn.FAILURE_MAPPING[cls])
    assert err.value.__cause__ is None and err.value.__context__ is None
    t, _ = _run(tn, _inserts(5))
    before = copy.deepcopy(t.buckets)
    for batch in ([_node(KINGS), rec], [rec, _node(KINGS)]):
        with pytest.raises(tn.NodeError) as err:
            t.merge(_Src(copy.deepcopy(batch)))
        assert err.value.failure_class == cls
        assert t.buckets == before
    assert rec == snap


class _LS(list):
    pass


@pytest.mark.parametrize("source", ["records", (GOOD,), None, {"a": GOOD}, _LS([GOOD])])
def test_r4_merge_source_must_be_exact_list(source):
    t, _ = _run(tn, _inserts(6))
    before = copy.deepcopy(t.buckets)
    with pytest.raises(tn.NodeError) as err:
        t.merge(_Src(source))
    assert err.value.failure_class == MNR
    assert t.buckets == before


def _raises(variant, fen):
    raise RuntimeError("oracle down")


class _Halt(BaseException):
    """A BaseException that is not an Exception."""


def _halts(variant, fen):
    raise _Halt("stop")


ORACLE_FAULTS = [
    ("raises", _raises),
    ("raises-base-exception-only", _halts),
    ("raises-generator-exit", lambda v, f: (_ for _ in ()).throw(GeneratorExit())),
    ("returns-trailing-newline", lambda v, f: _FIXED + "\n"),
    ("raises-forged-node-error", lambda v, f: (_ for _ in ()).throw(tn.NodeError(UV, "x"))),
    ("returns-none", lambda v, f: None),
    ("returns-bytes", lambda v, f: _FIXED.encode()),
    ("returns-str-subclass", lambda v, f: _S(_FIXED)),
    ("returns-short", lambda v, f: _FIXED[:-1]),
    ("returns-upper", lambda v, f: _FIXED.upper()),
]


@pytest.mark.parametrize("name,oracle", ORACLE_FAULTS, ids=[o[0] for o in ORACLE_FAULTS])
def test_r4_untrusted_digest_oracle_fails_closed(name, oracle):
    t = tn.NodeTable(DOCS, oracle)
    with pytest.raises(tn.NodeError) as err:
        t.insert("standard", START)
    assert err.value.failure_class == MNR
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert t.buckets == {}
    with pytest.raises(tn.NodeError) as err:
        tn.validate_record(*DOCS, dict(GOOD), oracle)
    assert err.value.failure_class == MNR


def test_r4_failure_classes_are_the_closed_mapping():
    assert tn.FAILURE_MAPPING == {
        MNR: "malformed_request",
        MP: "malformed_request",
        UV: "unknown_variant",
    }


# -- R5: merge laws -----------------------------------------------------------------------


def _clean(seed):
    want, _ = _model_run(_inserts(seed))
    out = []
    for r in want:
        if r not in out:
            out.append(r)
    return out


def _table_of(mod, recs, digest_fn=digest_fen):
    t = mod.NodeTable(DOCS, digest_fn)
    for r in recs:
        t.insert(r["variant"], r["snapshot_fen"])
    return t


def _merge_laws_red(mod, seed):
    recs = _clean(seed)
    rng = random.Random(seed)
    groups = [[], [], []]
    for r in recs:
        groups[rng.randrange(3)].append(r)
    union = _table_of(mod, recs).serialize()

    def fresh(g):
        return _table_of(mod, groups[g])

    ab = fresh(0).merge(fresh(1)).serialize()
    ba = fresh(1).merge(fresh(0)).serialize()
    left = fresh(0).merge(fresh(1)).merge(fresh(2)).serialize()
    right = fresh(0).merge(fresh(1).merge(fresh(2))).serialize()
    again = _table_of(mod, recs).merge(_table_of(mod, recs))
    return (
        ab != ba
        or left != right
        or left != union
        or again.serialize() != union
        or len(again.records()) != len(union)
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_r5_merge_idempotent_commutative_associative(seed):
    assert not _merge_laws_red(tn, seed)


def _failing_after(n):
    """A digest oracle that answers correctly n times, then raises."""
    calls = {"n": 0}

    def oracle(variant, fen):
        calls["n"] += 1
        if calls["n"] > n:
            raise RuntimeError("oracle failed midway")
        return digest_fen(variant, fen)

    return oracle


def _midway_fault_red(mod):
    """An oracle failing at every point of a merge - during validation or
    part-way through the staged inserts - rejects the WHOLE merge and
    leaves the destination bit-identical."""
    batch = [_node(START), _node(KINGS), _node(PROMO_TO)]
    for n in range(0, 8):
        dst = _table_of(mod, [_node(AFTER_E4)])
        dst.digest_fn = _failing_after(n)
        before = copy.deepcopy(dst.buckets)
        try:
            dst.merge(_Src(copy.deepcopy(batch)))
        except Exception as exc:  # noqa: BLE001 - class checked below
            if type(exc).__name__ != "NodeError" or exc.failure_class != MNR:
                return True
            if dst.buckets != before:
                return True
            continue
        if len(dst.records()) != 4:
            return True
    return False


def test_r5_merge_is_atomic_under_midway_oracle_fault():
    assert not _midway_fault_red(tn)


def _live_rewrite_red(mod):
    """An oracle that rewrites the caller's live source records during
    the merge cannot change what is inserted."""
    live = [_node(START)]

    state = {"n": 0}

    def oracle(variant, fen):
        state["n"] += 1
        live[0]["snapshot_fen"] = _canon(KINGS)
        live[0]["digest"] = digest_fen(variant, _canon(KINGS))
        if state["n"] == 1:
            live.append({**GOOD, "digest": "bad"})
        return digest_fen(variant, fen)

    dst = mod.NodeTable(DOCS, oracle)
    try:
        dst.merge(_Src(live))
    except Exception:  # noqa: BLE001 - the appended malformed record was read
        return True
    return [r["snapshot_fen"] for r in dst.records()] != [_canon(START)]


def _shared_bucket_rollback_red(mod):
    """Constant-digest oracle (one bucket): the batch's first record
    appends into the destination's existing bucket, then a later record
    fails - malformed, or the oracle faulting midway. Buckets and every
    per-bucket list stay bit-identical."""
    for tail, fault_after in ((dict(GOOD, digest="bad"), None), (_node(PROMO_TO), 3)):
        dst = mod.NodeTable(DOCS, _const_digest)
        dst.insert("standard", START)
        if fault_after is not None:
            calls = {"n": 0}

            def oracle(v, f, calls=calls, limit=fault_after):
                calls["n"] += 1
                if calls["n"] > limit:
                    raise RuntimeError("midway")
                return _FIXED

            dst.digest_fn = oracle
        before = copy.deepcopy(dst.buckets)
        lists = {k: list(v) for k, v in dst.buckets.items()}
        batch = [
            {**_node(KINGS), "digest": _FIXED},
            {**tail, "digest": _FIXED} if fault_after else tail,
        ]
        out = _outcome(lambda b=batch, d=dst: d.merge(_Src(b)))
        if out != MNR:
            return True
        if dst.buckets != before or {k: list(v) for k, v in dst.buckets.items()} != lists:
            return True
    return False


def test_r5_failed_merge_leaves_shared_bucket_lists_untouched():
    assert not _shared_bucket_rollback_red(tn)


def test_r5_merge_inserts_the_frozen_source():
    assert not _live_rewrite_red(tn)


# -- R6: rollback -------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r6_reinsert_returns_the_stored_record(seed):
    """insert-or-return-existing: a reinsert (any clocks) returns the
    stored record object and adds nothing."""
    rng = random.Random(seed)
    t = tn.NodeTable(DOCS)
    for v, fen in _inserts(seed):
        got = t.insert(v, fen)
        n = len(t.records())
        assert t.insert(v, _clock(rng, _canon(fen))) is got and len(t.records()) == n


# -- R7: determinism ------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_r7_order_never_changes_the_table(seed):
    inserts = _inserts(seed)
    shuffled = list(inserts)
    random.Random(seed + 1000).shuffle(shuffled)
    assert _run(tn, inserts)[0].serialize() == _run(tn, shuffled)[0].serialize()


# -- in-file mutants: each pinned property is RED on a one-edit production mutant ------


def _bucket_red(mod):
    return any(_model_mismatch(mod, s, _const_digest) for s in SEEDS)


def _model_red(mod):
    return any(_model_mismatch(mod, s) for s in SEEDS)


def _record_red(mod):
    for _name, rec, cls in BAD_RECORDS:
        record = copy.deepcopy(rec)
        try:  # a raw non-NodeError (e.g. KeyError) is a crash: red
            if _outcome(lambda r=record: mod.validate_record(*DOCS, r)) != cls:
                return True
        except Exception:  # noqa: BLE001
            return True
    return False


def _merge_record_red(mod):
    for _name, rec, cls in BAD_RECORDS:
        t = mod.NodeTable(DOCS)
        batch = [copy.deepcopy(rec)]
        if _outcome(lambda b=batch, t=t: t.merge(_Src(b))) != cls or t.records():
            return True
    return False


def _list_subclass_red(mod):
    t = mod.NodeTable(DOCS)
    return _outcome(lambda: t.merge(_Src(_LS([dict(GOOD)])))) != MNR or bool(t.records())


def _oracle_red(mod):
    for _name, oracle in ORACLE_FAULTS:
        t = mod.NodeTable(DOCS, oracle)
        try:
            if _outcome(lambda t=t: t.insert("standard", START)) != MNR or t.buckets:
                return True
        except BaseException:  # noqa: BLE001 - a leaked oracle raise is red
            return True
    return False


MUTANTS = [
    (
        "equality-by-bucket-only",
        "            if self._identity(existing) == new_identity:",
        "            if True:",
        _bucket_red,
    ),
    (
        "validate-halfmove-or-to-and",
        '    if fen_named["halfmove_clock"] != "0" or \\',
        '    if fen_named["halfmove_clock"] != "0" and \\',
        _record_red,
    ),
    (
        "validate-ep-identity-skipped",
        '    if fen_named["en_passant"] != named["en_passant"]:',
        "    if False:",
        _record_red,
    ),
    (
        "validate-digest-match-skipped",
        "    if digest != _oracle_digest(nc, dc, digest_fn, variant, snapshot):",
        "    if False:",
        _record_red,
    ),
    (
        "exact-dict-any-str-key",
        "return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "return type(obj) is dict and any(type(k) is str for k in dict.keys(obj))",
        _record_red,
    ),
    (
        "oracle-output-type-check-dropped",
        "    if type(out) is not str or re.fullmatch(",
        "    if False and re.fullmatch(",
        _oracle_red,
    ),
    (
        "merge-skips-validation",
        "        for rec in frozen:\n            validate_record(",
        "        for rec in []:\n            validate_record(",
        _merge_record_red,
    ),
    (
        "oracle-boundary-exception-only",
        "    except BaseException:  # noqa: BLE001 - untrusted oracle boundary",
        "    except Exception:  # noqa: BLE001 - untrusted oracle boundary",
        _oracle_red,
    ),
    (
        "oracle-output-search-not-fullmatch",
        "    if type(out) is not str or re.fullmatch(",
        "    if type(out) is not str or re.search(",
        _oracle_red,
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
        "merge-stages-in-place",
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}",
        "        staged.buckets = self.buckets",
        _midway_fault_red,
    ),
    (
        "merge-does-not-freeze",
        "            frozen.append(dict(rec))",
        "            frozen.append(rec)",
        _live_rewrite_red,
    ),
    (
        "snapshot-keeps-clocks",
        "            f\"{named['castling_rights']} {named['en_passant']} 0 1\")",
        "            f\"{named['castling_rights']} {named['en_passant']} 0 2\")",
        _model_red,
    ),
    (
        "serialize-drops-digest",
        '        return sorted((r["variant"], r["digest"], r["snapshot_fen"])',
        '        return sorted((r["variant"], r["snapshot_fen"])',
        _model_red,
    ),
]


def _mutant(old, new):
    src = Path(tn.__file__).read_text()
    assert src.count(old) == 1, old
    mod = types.ModuleType("transposition_node_mutant")
    mod.__file__ = tn.__file__
    exec(compile(src.replace(old, new), tn.__file__, "exec"), mod.__dict__)  # noqa: S102
    return mod


@pytest.mark.parametrize("name,old,new,red", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutant_property_green_on_production(name, old, new, red):
    assert not red(tn)


@pytest.mark.parametrize("name,old,new,red", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutant_is_red(name, old, new, red):
    assert red(_mutant(old, new))
