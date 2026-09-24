"""T0155 Graph/provenance/integration-restart: the production provenance
table (graph.provenance, T0152) composed end to end and across a REAL
process restart.

Durable form: a table persists as JSON - its records() list, sorted by
(target_kind, canonical target JSON). A restart is a fresh interpreter
that imports graph.provenance cold, builds a new ProvenanceTable and
loads the persisted records through the production merge (a source whose
records() returns them), so every persisted record crosses the same
validation boundary as any incoming record. Expected tables come from an
independent model restated here: one record per target, sources the
sorted set union of (source_id, game_id, first_observed_at).

Happy: an insert sequence over node, edge and opening-context targets
with overlapping sources, split at every point, matches the uninterrupted
run and the model; a three-process chain; output is independent of the
hash seed and of the persisted order.
Boundary: a single source, duplicate sources collapsing, a leap-day
timestamp, the printable-ASCII game id edges.
Malformed: tampered persisted records are rejected in the fresh process
with the class the contract gives and its mapped code, never a
traceback; the first failing record decides the class.
Rollback: a rejected load, insert or merge leaves the table unchanged,
and the persisted form after a rejection loads back identical.
Hostile types: at every input boundary - the record mapping, its keys,
target_kind, the sources list, each source entry mapping, its keys and
string values, the target mapping, its keys and string values, the
opening-context path list and its moves, and the merge batch - the typed
class, an empty hostile-call log, the argument unchanged and the table
unchanged.
Mutants: one-edit production mutants, each red on its own scenario.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from graph import provenance as pv

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "graph" / "provenance.py").read_text()
_PC = yaml.safe_load((ROOT / "data" / "contracts" / "provenance.yaml").read_text())["contract"]
MAPPING = _PC["failures"]["mapping"]
SOURCE_FIELDS = tuple(_PC["source_entry"]["fields"])
RECORD_FIELDS = tuple(_PC["record"]["fields"])
MPR, UTK, MTI, US = (
    "malformed_provenance_record",
    "unknown_target_kind",
    "malformed_target_identity",
    "unknown_source",
)
V = "standard"
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"

NODE_A = ("transposition_node", {"variant": V, "snapshot_fen": START})
NODE_B = ("transposition_node", {"variant": V, "snapshot_fen": KINGS})
EDGE_A = (
    "route_edge",
    {"variant": V, "move": "e2e4", "from_snapshot_fen": START, "to_snapshot_fen": AFTER_E4},
)
EDGE_B = (
    "route_edge",
    {"variant": V, "move": "e7e5", "from_snapshot_fen": AFTER_E4, "to_snapshot_fen": AFTER_E4_E5},
)
CTX_A = ("opening_context", {"variant": V, "path_moves": ["e2e4", "c7c5"]})
CTX_B = ("opening_context", {"variant": V, "path_moves": []})
TARGETS = (NODE_A, NODE_B, EDGE_A, EDGE_B, CTX_A, CTX_B)


def _src(sid="lichess-public", game="g1", ts="2024-01-01T00:00:00Z"):
    return {"source_id": sid, "game_id": game, "first_observed_at": ts}


SOURCES = (
    _src(),
    _src("chesscom-public", "cc-9", "2023-12-31T23:59:59Z"),
    _src("pgn-file", "!", "2024-02-29T12:00:00Z"),
    _src("pgn-folder", "~" * 3, "2000-01-01T00:00:00Z"),
    _src(game="g2"),
    _src(ts="2024-01-01T00:00:01Z"),
)


def _rec(target, sources):
    kind, tgt = target
    return {
        "target_kind": kind,
        "target": copy.deepcopy(tgt),
        "sources": [dict(s) for s in sources],
    }


# the insert sequence: every target, overlapping and duplicate sources
OPS = []
for _i, _t in enumerate(TARGETS):
    OPS.append(_rec(_t, [SOURCES[_i % len(SOURCES)]]))
for _i, _t in enumerate(TARGETS):
    OPS.append(_rec(_t, [SOURCES[(_i + 1) % len(SOURCES)], SOURCES[_i % len(SOURCES)]]))
OPS.append(_rec(NODE_A, [SOURCES[4], SOURCES[4], SOURCES[5]]))
OPS.append(_rec(CTX_A, [SOURCES[2]]))

# -- independent model ----------------------------------------------------------------


def _skey(s):
    return (s["source_id"], s["game_id"], s["first_observed_at"])


def _tkey(rec):
    return (rec["target_kind"], json.dumps(rec["target"], sort_keys=True))


def _model(ops):
    table = {}
    for rec in ops:
        k = _tkey(rec)
        srcs = {_skey(s): s for s in (table[k]["sources"] if k in table else [])}
        srcs.update({_skey(s): dict(s) for s in rec["sources"]})
        table[k] = {
            "target_kind": rec["target_kind"],
            "target": copy.deepcopy(rec["target"]),
            "sources": [srcs[x] for x in sorted(srcs)],
        }
    return [table[k] for k in sorted(table)]


def _canon(rec):
    return _model([rec])[0]


def _text(records):
    return json.dumps(records, ensure_ascii=False)


# -- hostile types (built in the running process) ------------------------------------

HOSTILE = []  # user dunder calls observed during the production call


class _S(str):
    pass


class _EqRaises(str):
    def __eq__(self, other):
        HOSTILE.append("eq")
        raise RuntimeError("eq")

    def __ne__(self, other):
        HOSTILE.append("ne")
        raise RuntimeError("ne")

    __hash__ = str.__hash__


def _colliding(target):
    class _Collides(str):
        def __hash__(self):
            HOSTILE.append("hash")
            return hash(target)

        def __eq__(self, other):
            HOSTILE.append("eq")
            return str.__eq__(self, other)

        def __ne__(self, other):
            HOSTILE.append("ne")
            return str.__ne__(self, other)

    return _Collides


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hstr(form, text, target):
    return {"plain": _S, "eq-raises": _EqRaises, "hash-collides": _colliding(target)}[form](text)


class _LogList(list):
    def __iter__(self):
        HOSTILE.append("iter")
        return list.__iter__(self)

    def __len__(self):
        HOSTILE.append("len")
        return list.__len__(self)

    def __getitem__(self, i):
        HOSTILE.append("getitem")
        return list.__getitem__(self, i)


class _LogTuple(tuple):
    def __iter__(self):
        HOSTILE.append("iter")
        return tuple.__iter__(self)

    def __eq__(self, other):
        HOSTILE.append("eq")
        return tuple.__eq__(self, other)

    def __ne__(self, other):
        HOSTILE.append("ne")
        return tuple.__ne__(self, other)

    def __hash__(self):
        HOSTILE.append("hash")
        return tuple.__hash__(self)


class _D(dict):
    pass


class _LyingDict(dict):
    def __getitem__(self, key):
        HOSTILE.append(f"getitem:{key}")
        return dict.__getitem__(self, key)

    def keys(self):
        HOSTILE.append("keys")
        return []

    def __iter__(self):
        HOSTILE.append("iter")
        return iter([])

    def items(self):
        HOSTILE.append("items")
        return []

    def __len__(self):
        HOSTILE.append("len")
        return 0


def _inert(value):
    """Bytes of a (possibly hostile) value read only through the base
    types' own methods, so no user dunder runs."""
    if isinstance(value, dict):
        pairs = sorted((_inert(k), _inert(v)) for k, v in dict.items(value))
        return f"{type(value).__name__}{{{pairs}}}"
    if isinstance(value, list):
        return f"{type(value).__name__}[" + ",".join(_inert(v) for v in list.__iter__(value)) + "]"
    return f"{type(value).__name__}:{json.dumps(value)}"


NEW = _rec(NODE_B, [SOURCES[3]])
NEW_EDGE = _rec(EDGE_B, [SOURCES[3]])
NEW_CTX = _rec(("opening_context", {"variant": V, "path_moves": ["d2d4", "d7d5"]}), [SOURCES[3]])


def _hostile_record(spec):
    """(call, argument) for one hostile row."""
    kind, _, form = spec.partition(":")
    field, _, f = form.partition("@")
    rec = copy.deepcopy(NEW)
    if kind == "merge-batch-list-subclass":
        return "merge", _LogList([rec])
    if kind == "record-dict-subclass":
        return "insert", _D(rec)
    if kind == "record-lying-dict":
        return "insert", _LyingDict(rec)
    if kind == "record-key":
        return "insert", {(_hstr(f, k, "sources") if k == field else k): v for k, v in rec.items()}
    if kind == "target-kind":
        return "insert", {**rec, "target_kind": _hstr(form, rec["target_kind"], "route_edge")}
    if kind == "sources-list-subclass":
        return "insert", {**rec, "sources": _LogList(rec["sources"])}
    if kind == "source-dict-subclass":
        return "insert", {**rec, "sources": [_D(rec["sources"][0])]}
    if kind == "source-lying-dict":
        return "insert", {**rec, "sources": [_LyingDict(rec["sources"][0])]}
    if kind == "source-key":
        s = {(_hstr(f, k, "game_id") if k == field else k): v for k, v in rec["sources"][0].items()}
        return "insert", {**rec, "sources": [s]}
    if kind == "source-value":
        s = rec["sources"][0]
        return "insert", {**rec, "sources": [{**s, field: _hstr(f, s[field], s[field])}]}
    base = {"node": NEW, "edge": NEW_EDGE, "ctx": NEW_CTX}[kind.split("-")[0]]
    rec = copy.deepcopy(base)
    what = kind.split("-", 1)[1]
    tgt = rec["target"]
    if what == "target-dict-subclass":
        tgt = _D(tgt)
    elif what == "target-lying-dict":
        tgt = _LyingDict(tgt)
    elif what == "target-key":
        tgt = {(_hstr(f, k, "variant") if k == field else k): v for k, v in tgt.items()}
    elif what == "target-value":
        tgt = {**tgt, field: _hstr(f, tgt[field], tgt[field])}
    elif what == "path-list-subclass":
        tgt = {**tgt, "path_moves": _LogList(tgt["path_moves"])}
    elif what == "path-move":
        tgt = {**tgt, "path_moves": [_hstr(form, tgt["path_moves"][0], "d7d5"), "d7d5"]}
    else:
        raise AssertionError(spec)
    return "insert", {**rec, "target": tgt}


def _hostile_rows():
    rows = [
        ("merge-batch-list-subclass", MPR),
        ("record-dict-subclass", MPR),
        ("record-lying-dict", MPR),
        ("sources-list-subclass", MPR),
        ("source-dict-subclass", MPR),
        ("source-lying-dict", MPR),
        ("ctx-path-list-subclass", MTI),
    ]
    for form in KEY_FORMS:
        rows.append((f"target-kind:{form}", UTK))
        rows.append((f"ctx-path-move:{form}", MTI))
        for field in RECORD_FIELDS:
            rows.append((f"record-key:{field}@{form}", MPR))
        for field in SOURCE_FIELDS:
            rows.append((f"source-key:{field}@{form}", MPR))
            rows.append((f"source-value:{field}@{form}", MPR))
        for kind, fields in (
            ("node", ("variant", "snapshot_fen")),
            ("edge", ("variant", "move", "from_snapshot_fen", "to_snapshot_fen")),
            ("ctx", ("variant",)),
        ):
            for field in fields:
                rows.append((f"{kind}-target-key:{field}@{form}", MTI))
                rows.append((f"{kind}-target-value:{field}@{form}", MTI))
    for kind in ("node", "edge", "ctx"):
        rows.append((f"{kind}-target-dict-subclass", MTI))
        rows.append((f"{kind}-target-lying-dict", MTI))
    return rows


# -- step runner (shared by the child and the in-process run) ------------------------


class _Batch:
    def __init__(self, records):
        self._r = records

    def records(self):
        return self._r


def _outcome(fn):
    try:
        got = fn()
    except BaseException as exc:  # noqa: BLE001 - a raw escape is the defect
        if type(exc).__name__ == "ProvenanceError" and hasattr(exc, "failure_class"):
            return {
                "err": exc.failure_class,
                "code": exc.code,
                "typed": exc.code == MAPPING.get(exc.failure_class),
                "cause": exc.__cause__ is not None or exc.__context__ is not None,
            }
        return {"raw": type(exc).__name__}
    return {"ok": True, "got": got}


def _dump(table):
    return sorted(copy.deepcopy(list(table._records.values())), key=_tkey)


def _run(module, steps):
    table = module.ProvenanceTable()
    out = []
    for step in steps:
        before = _dump(table)
        do = step["do"]
        if do in ("load", "merge"):
            batch = _Batch(json.loads(step["text"]))
            res = _outcome(lambda batch=batch: table.merge(batch) is table)
        elif do == "insert":
            res = _outcome(lambda step=step: table.insert(step["record"]))
        elif do == "hostile":
            call, arg = _hostile_record(step["hostile"])
            snapshot = _inert(arg)
            HOSTILE.clear()  # construction may hash/compare; only the production call counts
            if call == "insert":
                res = _outcome(lambda arg=arg: table.insert(arg))
            else:
                res = _outcome(lambda arg=arg: table.merge(_Batch(arg)) is table)
            res["hostile"] = list(HOSTILE)
            res["arg_unchanged"] = _inert(arg) == snapshot
        else:
            raise AssertionError(do)
        if "err" in res or "raw" in res:
            res["unchanged"] = _dump(table) == before
        out.append(res)
    return {"steps": out, "table": _dump(table)}


def _source_mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"graph._t0155_mutant_{name}")
    module.__file__ = str(ROOT / "graph" / "provenance.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    return module


def _child_main():
    payload = json.load(sys.stdin)
    name = payload.get("mutant")
    module = pv if name is None else _source_mutant(name, MUTANTS[name][0])
    json.dump([_run(module, job) for job in payload["jobs"]], sys.stdout, sort_keys=True)


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0155_provenance_integration_restart import _child_main; _child_main()"
)


def _restart(jobs, mutant=None, hash_seed="0", raw=False):
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    run = subprocess.run(
        [sys.executable, "-c", _CHILD, str(ROOT)],
        input=json.dumps({"mutant": mutant, "jobs": jobs}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=100,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout if raw else json.loads(run.stdout)


def _local(jobs, module=pv):
    return json.loads(json.dumps([_run(module, job) for job in jobs], sort_keys=True))


_OK_LOAD = {"ok": True, "got": True}


def _ok_insert(model_after, rec):
    return {"ok": True, "got": next(r for r in model_after if _tkey(r) == _tkey(rec))}


def _rej(cls):
    return {"err": cls, "code": MAPPING[cls], "typed": True, "cause": False, "unchanged": True}


# -- scenarios: (jobs, want, labels) --------------------------------------------------


def _split_jobs():
    """Parent inserts OPS[:k] and persists; the fresh process loads the
    persisted table and inserts the rest (reinserting OPS[k-1] too)."""
    jobs, want, labels = [], [], []
    for k in range(len(OPS) + 1):
        steps = [{"do": "load", "text": _text(_model(OPS[:k]))}]
        outs = [_OK_LOAD]
        rest = OPS[k:] + (OPS[k - 1 : k] if k else [])
        done = list(OPS[:k])
        for rec in rest:
            done.append(rec)
            steps.append({"do": "insert", "record": rec})
            outs.append(_ok_insert(_model(done), rec))
        jobs.append(steps)
        want.append({"steps": outs, "table": _model(OPS)})
        labels.append(f"split@{k}")
    return jobs, want, labels


def _boundary_jobs():
    rows = {
        "empty-table": [],
        "single-source": [_rec(NODE_A, [SOURCES[0]])],
        "duplicate-sources-collapse": [_rec(NODE_A, [SOURCES[0], SOURCES[0], SOURCES[0]])],
        "leap-day-timestamp": [_rec(EDGE_A, [SOURCES[2]])],
        "game-id-edges": [_rec(CTX_A, [_src(game="!"), _src(game="~")])],
        "empty-context-path": [_rec(CTX_B, [SOURCES[1]])],
        "same-target-other-source-unions": [_rec(NODE_A, [SOURCES[0]]), _rec(NODE_A, [SOURCES[4]])],
    }
    jobs, want, labels = [], [], []
    for name, ops in rows.items():
        jobs.append([{"do": "load", "text": _text(ops)}])
        want.append({"steps": [_OK_LOAD], "table": _model(ops)})
        labels.append(name)
    return jobs, want, labels


def _tampers():
    good = _model(OPS[:4])
    n = copy.deepcopy(NEW)

    def with_rec(**over):
        return [*good, {**n, **over}]

    def with_src(**over):
        return [*good, {**n, "sources": [{**n["sources"][0], **over}]}]

    def with_tgt(base, **over):
        b = copy.deepcopy(base)
        return [*good, {**b, "target": {**b["target"], **over}}]

    return [
        ("record-missing-field", [*good, {k: v for k, v in n.items() if k != "sources"}], MPR),
        ("record-extra-field", with_rec(note="x"), MPR),
        ("record-not-dict", [*good, [n["target_kind"], n["target"], n["sources"]]], MPR),
        ("kind-unknown", with_rec(target_kind="opening_line"), UTK),
        ("kind-non-str", with_rec(target_kind=1), UTK),
        ("sources-empty", with_rec(sources=[]), MPR),
        ("sources-not-list", with_rec(sources=n["sources"][0]), MPR),
        (
            "source-missing-field",
            with_rec(sources=[{"source_id": "pgn-file", "game_id": "g"}]),
            MPR,
        ),
        ("source-id-non-str", with_src(source_id=["pgn-file"]), MPR),
        ("source-unknown", with_src(source_id="fide-archive"), US),
        ("game-id-empty", with_src(game_id=""), MPR),
        ("game-id-space", with_src(game_id="g 1"), MPR),
        ("game-id-non-ascii", with_src(game_id="g\u00e9"), MPR),
        ("timestamp-offset", with_src(first_observed_at="2024-01-01T00:00:00+00:00"), MPR),
        ("timestamp-impossible-date", with_src(first_observed_at="2023-02-29T00:00:00Z"), MPR),
        ("timestamp-fullwidth-digit", with_src(first_observed_at="\uff12024-01-01T00:00:00Z"), MPR),
        ("node-target-extra-key", with_tgt(NEW, note="x"), MTI),
        (
            "node-non-canonical-clocks",
            with_tgt(NEW, snapshot_fen=KINGS.replace(" 0 1", " 3 9")),
            MTI,
        ),
        ("node-bad-fen", with_tgt(NEW, snapshot_fen="x"), MTI),
        ("node-unknown-variant", with_tgt(NEW, variant="atomic"), MTI),
        ("edge-bad-move", with_tgt(NEW_EDGE, move="e7e9"), MTI),
        ("edge-null-move", with_tgt(NEW_EDGE, move="e7e7"), MTI),
        ("edge-bad-snapshot", with_tgt(NEW_EDGE, to_snapshot_fen="x"), MTI),
        ("ctx-bad-move", with_tgt(NEW_CTX, path_moves=["d2d9"]), MTI),
        ("ctx-path-str", with_tgt(NEW_CTX, path_moves="d2d4"), MTI),
        ("ctx-unknown-variant", with_tgt(NEW_CTX, variant="atomic"), MTI),
        # the first failing record decides; within a record, kind > sources > target
        (
            "us-then-mti",
            [*with_src(source_id="fide-archive"), with_tgt(NEW_CTX, variant="x")[-1]],
            US,
        ),
        (
            "mti-then-us",
            [*with_tgt(NEW_CTX, variant="x"), with_src(source_id="fide-archive")[-1]],
            MTI,
        ),
        ("kind-before-sources", with_rec(target_kind="x", sources=[]), UTK),
        ("sources-before-target", [*good, {**n, "sources": [], "target": {"variant": V}}], MPR),
    ]


def _tampered_jobs():
    jobs, want, labels = [], [], []
    clean = _model(OPS[:4])
    for name, recs, cls in _tampers():
        jobs.append(
            [
                {"do": "load", "text": _text(recs)},
                {"do": "load", "text": _text(clean)},
                {"do": "insert", "record": NEW},
            ]
        )
        after = _model([*OPS[:4], NEW])
        want.append({"steps": [_rej(cls), _OK_LOAD, _ok_insert(after, NEW)], "table": after})
        labels.append(name)
    return jobs, want, labels


def _rollback_jobs():
    base = _model(OPS[:6])
    good_new = _rec(CTX_A, [SOURCES[5]])
    bad = {**copy.deepcopy(NEW), "sources": [_src("fide-archive")]}
    rows = [
        ("merge-union-then-bad", [good_new, bad], US),
        (
            "merge-new-then-bad",
            [copy.deepcopy(NEW), {**copy.deepcopy(NEW), "target_kind": "x"}],
            UTK,
        ),
    ]
    jobs, want, labels = [], [], []
    for name, recs, cls in rows:
        jobs.append(
            [
                {"do": "load", "text": _text(base)},
                {"do": "merge", "text": _text(recs)},
            ]
        )
        want.append({"steps": [_OK_LOAD, _rej(cls)], "table": base})
        labels.append(name)
    for name, rec, cls in (
        ("insert-unknown-source", bad, US),
        (
            "insert-bad-target",
            {**copy.deepcopy(NEW), "target": {"variant": V, "snapshot_fen": "x"}},
            MTI,
        ),
        ("insert-union-bad-timestamp", {**_rec(NODE_A, [_src(ts="2024-13-01T00:00:00Z")])}, MPR),
    ):
        jobs.append([{"do": "load", "text": _text(base)}, {"do": "insert", "record": rec}])
        want.append({"steps": [_OK_LOAD, _rej(cls)], "table": base})
        labels.append(name)
    # an identical reload is a no-op accept
    jobs.append([{"do": "load", "text": _text(base)}, {"do": "merge", "text": _text(base)}])
    want.append({"steps": [_OK_LOAD, _OK_LOAD], "table": base})
    labels.append("identical-reload")
    return jobs, want, labels


def _hostile_jobs():
    jobs, want, labels = [], [], []
    base = _model(OPS[:6])
    for spec, cls in _hostile_rows():
        jobs.append([{"do": "load", "text": _text(base)}, {"do": "hostile", "hostile": spec}])
        want.append(
            {
                "steps": [_OK_LOAD, {**_rej(cls), "hostile": [], "arg_unchanged": True}],
                "table": base,
            }
        )
        labels.append(spec)
    return jobs, want, labels


def _bad(builder, got):
    _jobs, want, labels = builder()
    return [label for label, g, w in zip(labels, got, want, strict=True) if g != w]


# -- tests ----------------------------------------------------------------------------


def test_restart_at_every_split_matches_the_model_and_the_uninterrupted_run():
    jobs, _want, _labels = _split_jobs()
    got = _restart(jobs)
    assert _bad(_split_jobs, got) == []
    assert got == _local(jobs)
    whole = _local([[{"do": "insert", "record": r} for r in OPS]])[0]
    assert whole["table"] == _model(OPS)


def test_three_process_chain_persists_exactly():
    third = len(OPS) // 3
    t1 = _local([[{"do": "insert", "record": r} for r in OPS[:third]]])[0]["table"]
    t2 = _restart(
        [
            [{"do": "load", "text": _text(t1)}]
            + [{"do": "insert", "record": r} for r in OPS[third : 2 * third]]
        ]
    )[0]["table"]
    t3 = _restart(
        [
            [{"do": "load", "text": _text(t2)}]
            + [{"do": "insert", "record": r} for r in OPS[2 * third :]]
        ]
    )[0]["table"]
    assert t3 == _model(OPS)
    assert _restart([[{"do": "load", "text": _text(t3)}]])[0] == {
        "steps": [_OK_LOAD],
        "table": _model(OPS),
    }


def test_output_is_deterministic_across_hash_seeds_and_persisted_order():
    jobs = _split_jobs()[0][::4]
    assert _restart(jobs, hash_seed="1", raw=True) == _restart(jobs, hash_seed="777", raw=True)
    reordered = [dict(reversed(list(r.items()))) for r in reversed(_model(OPS))]
    got = _restart([[{"do": "load", "text": _text(reordered)}]])[0]
    assert got["table"] == _model(OPS)


def test_boundaries_after_restart():
    jobs, _want, _labels = _boundary_jobs()
    got = _restart(jobs)
    assert _bad(_boundary_jobs, got) == []
    assert got == _local(jobs)


def test_tampered_persisted_tables_are_rejected_typed_in_the_fresh_process():
    jobs, _want, _labels = _tampered_jobs()
    got = _restart(jobs)
    assert _bad(_tampered_jobs, got) == []
    assert got == _local(jobs)
    assert {cls for _n, _r, cls in _tampers()} == set(MAPPING)


def test_rejections_roll_back_across_the_restart():
    jobs, _want, _labels = _rollback_jobs()
    got = _restart(jobs)
    assert _bad(_rollback_jobs, got) == []
    assert got == _local(jobs)


def test_rollback_survives_the_restart():
    base = _model(OPS[:6])
    bad = [_rec(CTX_A, [SOURCES[5]]), {**copy.deepcopy(NEW), "target_kind": "x"}]
    parent = _local([[{"do": "load", "text": _text(base)}, {"do": "merge", "text": _text(bad)}]])[0]
    assert parent["steps"][1] == _rej(UTK)
    child = _restart([[{"do": "load", "text": _text(parent["table"])}]])[0]
    assert child == {"steps": [_OK_LOAD], "table": base}


def test_hostile_types_at_every_input_boundary_are_rejected_typed_and_inert():
    jobs, _want, _labels = _hostile_jobs()
    got = _restart(jobs)
    assert _bad(_hostile_jobs, got) == []
    assert got == _local(jobs)


def test_returned_and_listed_records_are_detached():
    table = pv.ProvenanceTable()
    table.merge(_Batch(_model(OPS[:6])))
    got = table.insert(OPS[6])
    got["sources"].append(_src(game="poison"))
    for rec in table.records():
        rec["target_kind"] = "poison"
    assert _dump(table) == _model(OPS[:7])


# -- in-file mutants: each is RED on its own scenario ---------------------------------


# -- direct rows (in-process) ------------------------------------------------------------


class _Halt(BaseException):
    pass


def _renamed(mapping, old):
    return {(old + "_x" if k == old else k): v for k, v in mapping.items()}


def _direct_problems(module=pv):
    """Rows the restart scenarios do not reach: same-arity renamed keys at
    every mapping, detachment of stored records from arguments and returns,
    move grammar, edge canonicality and identity, the merge reader boundary
    and persisted-order independence; [] when all hold."""
    bad = []

    def expect(label, table, fn, cls, forged=None):
        before = _dump(table)
        res = _outcome(fn)
        if res.get("err") != cls or not res.get("typed") or res.get("cause"):
            bad.append((label, {k: v for k, v in res.items() if k != "got"}))
        elif forged is not None and res.get("forged"):
            bad.append((label, "forged"))
        elif _dump(table) != before:
            bad.append((label, "table-changed"))

    node, edge, ctx = (
        _rec(NODE_A, [SOURCES[0]]),
        _rec(EDGE_A, [SOURCES[0]]),
        _rec(CTX_A, [SOURCES[0]]),
    )
    renamed = [
        ("source-entry", {**node, "sources": [_renamed(SOURCES[0], "source_id")]}, MPR),
        ("record", _renamed(node, "target_kind"), MPR),
        ("node-target", {**node, "target": _renamed(node["target"], "snapshot_fen")}, MTI),
        ("edge-target", {**edge, "target": _renamed(edge["target"], "move")}, MTI),
        ("context-target", {**ctx, "target": _renamed(ctx["target"], "path_moves")}, MTI),
    ]
    bad_moves = []
    for move in ("e7e8qq", "e7e8k"):
        bad_moves.append(
            (f"edge-move-{move}", {**edge, "target": {**edge["target"], "move": move}}, MTI)
        )
        bad_moves.append(
            (
                f"context-move-{move}",
                {**ctx, "target": {**ctx["target"], "path_moves": ["e2e4", move]}},
                MTI,
            )
        )
    noncanon = {**edge, "target": {**edge["target"], "to_snapshot_fen": AFTER_E4[:-3] + "3 9"}}
    rows = [*renamed, *bad_moves, ("edge-to-non-canonical", noncanon, MTI)]
    for label, rec, cls in rows:
        table = module.ProvenanceTable()
        table.insert(_rec(NODE_B, [SOURCES[1]]))
        expect(f"insert:{label}", table, lambda t=table, r=rec: t.insert(copy.deepcopy(r)), cls)
        expect(
            f"load:{label}",
            table,
            lambda t=table, r=rec: t.merge(_Batch([copy.deepcopy(r)])),
            cls,
        )
    # two edges with equal snapshots and different moves stay two records
    table = module.ProvenanceTable()
    other = {**edge, "target": {**edge["target"], "move": "d2d4"}}
    for rec in (edge, other):
        table.insert(copy.deepcopy(rec))
    if len(table.records()) != 2:
        bad.append(("edge-identity-keeps-move", len(table.records())))
    # detachment: the stored record is independent of the argument and the return
    table = module.ProvenanceTable()
    arg = _rec(EDGE_B, [SOURCES[2], SOURCES[3]])
    got = table.insert(arg)
    want_dump, want_json = _dump(table), json.dumps(table.records(), sort_keys=True)
    arg["sources"][0]["game_id"] = "poison"
    arg["target"]["move"] = "a2a3"
    got["sources"][1]["game_id"] = "poison"
    got["target"]["variant"] = "poison"
    again = table.insert(_rec(EDGE_B, [SOURCES[2]]))
    again["sources"][0]["source_id"] = "poison"
    if _dump(table) != want_dump or json.dumps(table.records(), sort_keys=True) != want_json:
        bad.append(("detached", "table-changed"))
    # the merge reader boundary: any records() failure is a fresh typed rejection
    for label, make in (
        ("forged", lambda: module.ProvenanceError(MPR)),
        ("base-exception", lambda: _Halt("x")),
        ("keyboard-interrupt", lambda: KeyboardInterrupt()),
    ):
        holder = []

        class _Raiser:
            def records(self, make=make, holder=holder):
                holder.append(make())
                raise holder[-1]

        table = module.ProvenanceTable()
        table.insert(copy.deepcopy(node))
        before = _dump(table)
        try:
            table.merge(_Raiser())
            bad.append((f"reader:{label}", "no-raise"))
        except BaseException as exc:  # noqa: BLE001 - class and identity are the check
            if (
                type(exc).__name__ != "ProvenanceError"
                or getattr(exc, "failure_class", None) != MPR
            ):
                bad.append((f"reader:{label}", "class", type(exc).__name__))
            elif holder and exc is holder[-1]:
                bad.append((f"reader:{label}", "forged"))
            elif exc.__cause__ is not None or exc.__context__ is not None:
                bad.append((f"reader:{label}", "chained"))
        if _dump(table) != before:
            bad.append((f"reader:{label}", "table-changed"))
    # persisted order never changes serialize()
    recs = [_rec(t, [SOURCES[i % len(SOURCES)]]) for i, t in enumerate(TARGETS)]
    forward, backward = module.ProvenanceTable(), module.ProvenanceTable()
    forward.merge(_Batch(copy.deepcopy(recs)))
    backward.merge(_Batch(copy.deepcopy(recs[::-1])))
    if forward.serialize() != backward.serialize():
        bad.append(("serialize-order", "differs"))
    return bad


def test_direct_rows():
    assert _direct_problems() == []


def _red(builder):
    def red(module):
        jobs, want, _labels = builder()
        return _local(jobs, module) != want

    return red


MUTANTS = {
    "source-entry-set-by-length": (
        [
            (
                "        if not _exact_dict(entry) or set(entry) != _SOURCE_FIELDS:",
                "        if not _exact_dict(entry) or len(entry) != len(_SOURCE_FIELDS):",
            )
        ],
        _direct_problems,
    ),
    "record-set-by-length": (
        [
            (
                "    if not _exact_dict(record) or set(record) != _RECORD_FIELDS:",
                "    if not _exact_dict(record) or len(record) != len(_RECORD_FIELDS):",
            )
        ],
        _direct_problems,
    ),
    "node-target-set-by-length": (
        [
            (
                '    if not _exact_dict(target) or set(target) != {"variant", "snapshot_fen"}:',
                "    if not _exact_dict(target) or len(target) != 2:",
            )
        ],
        _direct_problems,
    ),
    "context-target-set-by-length": (
        [
            (
                '    if not _exact_dict(target) or set(target) != {"variant", "path_moves"}:',
                "    if not _exact_dict(target) or len(target) != 2:",
            )
        ],
        _direct_problems,
    ),
    "source-entry-stored-live": (
        [
            (
                "        unique[key] = {field: entry[field] for field in "
                '_PROVENANCE["source_entry"]["fields"]}',
                "        unique[key] = entry",
            )
        ],
        _direct_problems,
    ),
    "target-stored-live": (
        [
            (
                '        "target": copy.deepcopy(record["target"]),',
                '        "target": record["target"],',
            )
        ],
        _direct_problems,
    ),
    "insert-returns-stored": (
        [
            (
                "            self._records[key] = canonical\n"
                "            return copy.deepcopy(canonical)",
                "            self._records[key] = canonical\n            return canonical",
            )
        ],
        _direct_problems,
    ),
    "move-length-unbounded": (
        [("len(move) not in (4, 5)", "len(move) < 4")],
        _direct_problems,
    ),
    "promotion-letter-unchecked": (
        [
            (
                '    return len(move) == 4 or move[4] in shape["types"]["promotion"]["enum"]',
                "    return True",
            )
        ],
        _direct_problems,
    ),
    "edge-to-snapshot-canonical-off": (
        [
            (
                '        if (before["snapshot_fen"] != target["from_snapshot_fen"]\n'
                '                or after["snapshot_fen"] != target["to_snapshot_fen"]):',
                '        if before["snapshot_fen"] != target["from_snapshot_fen"]:',
            )
        ],
        _direct_problems,
    ),
    "edge-identity-drops-move": (
        [
            (
                '            target["variant"],\n            target["move"],\n',
                '            target["variant"],\n',
            )
        ],
        _direct_problems,
    ),
    "reader-boundary-narrow": (
        [
            (
                "            incoming = reader() if callable(reader) else None\n"
                "        except BaseException:",
                "            incoming = reader() if callable(reader) else None\n"
                "        except Exception:",
            )
        ],
        _direct_problems,
    ),
    "reader-boundary-chained": (
        [
            (
                "        except BaseException:\n            failed = True\n        if failed:\n"
                '            _fail("malformed_provenance_record", "merge source records() failed")',
                "        except BaseException as exc:\n"
                '            raise ProvenanceError("malformed_provenance_record") from exc',
            )
        ],
        _direct_problems,
    ),
    "reader-forged-passes-through": (
        [
            (
                "            incoming = reader() if callable(reader) else None\n"
                "        except BaseException:",
                "            incoming = reader() if callable(reader) else None\n"
                "        except ProvenanceError:\n            raise\n        except BaseException:",
            )
        ],
        _direct_problems,
    ),
    "serialize-unsorted": (
        [("        return sorted(out)", "        return out")],
        _direct_problems,
    ),
    "merge-commits-in-place": (
        [("        candidate._records = staged\n", "        candidate._records = self._records\n")],
        _red(_rollback_jobs),
    ),
    "union-keeps-existing-sources": (
        [
            (
                '        if sources != existing["sources"]:\n',
                "        if False:\n",
            )
        ],
        _red(_split_jobs),
    ),
    "unknown-source-as-malformed": (
        [
            (
                '            _fail("unknown_source", ',
                '            _fail("malformed_provenance_record", ',
            )
        ],
        _red(_tampered_jobs),
    ),
    "timestamp-regex-only": (
        [('        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")\n', "        pass\n")],
        _red(_tampered_jobs),
    ),
    "exact-dict-isinstance": (
        [
            (
                "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
                "    return isinstance(obj, dict) and all(type(k) is str for k in dict.keys(obj))",
            )
        ],
        _red(_hostile_jobs),
    ),
    "exact-dict-drops-key-types": (
        [
            (
                "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
                "    return type(obj) is dict",
            )
        ],
        _red(_hostile_jobs),
    ),
    "kind-membership-without-type-check": (
        [
            (
                "    if type(kind) is not str or kind not in _TARGET_KINDS:",
                "    if kind not in _TARGET_KINDS:",
            )
        ],
        _red(_hostile_jobs),
    ),
    "source-id-membership-without-type-check": (
        [
            (
                '        if type(entry["source_id"]) is not str:\n'
                '            _fail("malformed_provenance_record", "source_id must be a str")\n',
                "",
            )
        ],
        _red(_hostile_jobs),
    ),
    "sources-list-isinstance": (
        [
            (
                "    if type(sources) is not list or not sources:",
                "    if not isinstance(sources, list) or not sources:",
            )
        ],
        _red(_hostile_jobs),
    ),
    "batch-list-isinstance": (
        [("        if type(incoming) is not list:", "        if not isinstance(incoming, list):")],
        _red(_hostile_jobs),
    ),
    "context-path-list-isinstance": (
        [
            (
                "    if type(path) is not list or not all(",
                "    if not isinstance(path, list) or not all(",
            )
        ],
        _red(_hostile_jobs),
    ),
}


def test_identity_mutant_is_green():
    module = _source_mutant("identity", [])
    for builder in (_split_jobs, _boundary_jobs, _tampered_jobs, _rollback_jobs, _hostile_jobs):
        jobs, want, _labels = builder()
        assert _local(jobs, module) == want, builder.__name__
    assert _direct_problems(module) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_scenario(name):
    edits, red = MUTANTS[name]
    assert red(_source_mutant(name, edits)), name


def test_a_mutant_is_red_in_a_fresh_process_too():
    jobs, want, _labels = _tampered_jobs()
    assert _restart(jobs, mutant="timestamp-regex-only") != want
