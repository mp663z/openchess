"""T0146 Graph/opening context/integration-restart: the production
opening-context store (graph.opening_context, T0143) composed end to end
and across a REAL process restart.

Durable form: a table persists as JSON - a list of [key, record] pairs,
key = [variant, path_moves], record = the four contract fields - sorted
by (variant, path). A restart is a fresh interpreter that imports
graph.opening_context cold, builds a new ContextTable and loads the
persisted pairs through the production merge (a source table holding the
pairs), so every persisted record crosses the same validation boundary
as any incoming record. The expected tables come from an independent
longest-prefix model of data/openings/registry.yaml, restated here.

Happy: every enumerated path set, split at every point, inserts before
and after a restart and matches the uninterrupted run and the model; a
three-process chain; reinsertion after a restart returns the stored
record; output is independent of the hash seed.
Boundary: empty table, empty path (the "-" sentinel), each registry
entry exactly, one move past it, longest prefix over a shorter one.
Malformed: tampered persisted records are rejected in the fresh process
with the class the contract gives and its mapped code, never a
traceback, and the first failing record decides the class.
Rollback: a rejected load, insert or merge leaves the table unchanged,
and the persisted form after a rejection loads back identical.
Hostile types: at every input boundary -
insert(variant, path), merge(other), the source store mapping, its keys
and the moves in them, each record mapping, record keys in 3 forms and
each record string value - the typed class, an empty hostile-call log,
the argument unchanged and the table unchanged.
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

from graph import opening_context as oc

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "graph" / "opening_context.py").read_text()
REGISTRY = yaml.safe_load((ROOT / "data" / "openings" / "registry.yaml").read_text())["registry"]
_CC = yaml.safe_load((ROOT / "data" / "contracts" / "opening_context.yaml").read_text())["contract"]
MAPPING = _CC["failures"]["mapping"]
FIELDS = tuple(_CC["record"]["fields"])
SENTINEL = REGISTRY["none_sentinel"]
ENTRIES = REGISTRY["entries"]
MCR, MP, UV, UOC, CC = (
    "malformed_context_record",
    "malformed_path",
    "unknown_variant",
    "unknown_opening_code",
    "conflicting_context",
)
V = "standard"

# -- independent model ----------------------------------------------------------------


def _model(path):
    best = None
    for entry in ENTRIES:
        n = len(entry["moves"])
        if list(path[:n]) == entry["moves"] and (best is None or n > len(best["moves"])):
            best = entry
    return (SENTINEL, SENTINEL) if best is None else (best["code"], best["name"])


def _record(path, variant=V):
    code, name = _model(path)
    return {
        "variant": variant,
        "path_moves": list(path),
        "opening_code": code,
        "opening_name": name,
    }


def _pairs(paths):
    """The durable form the model predicts for a set of paths."""
    uniq = {tuple(p): None for p in paths}
    return [[[V, list(p)], _record(p)] for p in sorted(uniq)]


def _text(pairs):
    return json.dumps(pairs, ensure_ascii=False)


# every registry prefix, each entry plus one move, unclassified and empty paths
PATHS = [[]]
for _e in ENTRIES:
    for _n in range(1, len(_e["moves"]) + 1):
        PATHS.append(list(_e["moves"][:_n]))
    PATHS.append([*_e["moves"], "h2h3"])
PATHS += [
    ["a2a3"],
    ["a2a3", "a7a6"],
    ["e2e4", "c7c5", "g1f3"],
    ["a2a4", "b7b5", "a4b5", "h7h5", "b5b6", "h5h4", "b6a7", "h4h3", "a7b8q"],
]
PATHS = [list(p) for p in dict.fromkeys(tuple(p) for p in PATHS)]

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
    if isinstance(value, tuple):
        return f"{type(value).__name__}(" + ",".join(_inert(v) for v in tuple.__iter__(value)) + ")"
    if isinstance(value, oc.ContextTable) or type(value).__name__.endswith("ContextTable"):
        return f"{type(value).__name__}<{_inert(value._records)}>"
    return f"{type(value).__name__}:{json.dumps(value)}"


NEW_PATH = ["d2d4", "d7d5", "c2c4"]  # D20, never in the hostile base table


def _hostile_call(module, spec, table):
    """(call, argument) for one hostile row against TABLE."""
    kind, _, form = spec.partition(":")
    if kind == "ins-variant":
        return "insert", (_hstr(form, V, V), list(NEW_PATH))
    if kind == "ins-path-list-subclass":
        return "insert", (V, _LogList(NEW_PATH))
    if kind == "ins-path-move":
        return "insert", (V, [_hstr(form, NEW_PATH[0], NEW_PATH[1]), *NEW_PATH[1:]])
    rec = _record(NEW_PATH)
    key = (V, tuple(NEW_PATH))
    store = {key: rec}
    other_cls = module.ContextTable
    if kind == "merge-other-subclass":
        other_cls = type("SubContextTable", (module.ContextTable,), {})
    elif kind == "merge-store-dict-subclass":
        store = _D(store)
    elif kind == "merge-store-lying-dict":
        store = _LyingDict(store)
    elif kind == "merge-key-tuple-subclass":
        store = {_LogTuple(key): rec}
    elif kind == "merge-key-variant":
        store = {(_hstr(form, V, V), key[1]): rec}
    elif kind == "merge-key-move":
        store = {(V, (_hstr(form, NEW_PATH[0], NEW_PATH[1]), *NEW_PATH[1:])): rec}
    elif kind == "merge-record-dict-subclass":
        store = {key: _D(rec)}
    elif kind == "merge-record-lying-dict":
        store = {key: _LyingDict(rec)}
    elif kind == "merge-record-path-list-subclass":
        store = {key: {**rec, "path_moves": _LogList(NEW_PATH)}}
    elif kind == "merge-record-key":
        field, _, f = form.partition("@")
        store = {key: {(_hstr(f, k, "variant") if k == field else k): v for k, v in rec.items()}}
    elif kind == "merge-record-value":
        field, _, f = form.partition("@")
        store = {key: {**rec, field: _hstr(f, rec[field], rec[field])}}
    elif kind == "merge-record-move":
        store = {key: {**rec, "path_moves": [_hstr(form, NEW_PATH[0], NEW_PATH[1]), *NEW_PATH[1:]]}}
    else:
        raise AssertionError(spec)
    other = other_cls()
    other._records = store
    return "merge", other


def _hostile_rows():
    rows = []
    for form in KEY_FORMS:
        rows.append((f"ins-variant:{form}", UV))
        rows.append((f"ins-path-move:{form}", MP))
    rows.append(("ins-path-list-subclass", MP))
    for spec in (
        "merge-other-subclass",
        "merge-store-dict-subclass",
        "merge-store-lying-dict",
        "merge-key-tuple-subclass",
        "merge-record-dict-subclass",
        "merge-record-lying-dict",
        "merge-record-path-list-subclass",
    ):
        rows.append((spec, MCR))
    for form in KEY_FORMS:
        rows.append((f"merge-key-variant:{form}", MCR))
        rows.append((f"merge-key-move:{form}", MCR))
        rows.append((f"merge-record-move:{form}", MCR))
        for field in FIELDS:
            rows.append((f"merge-record-key:{field}@{form}", MCR))
        for field in ("variant", "opening_code", "opening_name"):
            rows.append((f"merge-record-value:{field}@{form}", MCR))
    return rows


# -- step runner (shared by the child and the in-process run) ------------------------


def _outcome(fn):
    try:
        got = fn()
    except BaseException as exc:  # noqa: BLE001 - a raw escape is the defect
        if type(exc).__name__ == "ContextError" and hasattr(exc, "failure_class"):
            return {
                "err": exc.failure_class,
                "code": exc.code,
                "typed": exc.code == MAPPING.get(exc.failure_class),
                "cause": exc.__cause__ is not None or exc.__context__ is not None,
            }
        return {"raw": type(exc).__name__}
    return {"ok": True, "got": got}


def _dump(table):
    return [[[k[0], list(k[1])], r] for k, r in sorted(table._records.items())]


def _source(module, pairs):
    """A source table holding persisted pairs verbatim."""
    src = module.ContextTable()
    store = {}
    for key, rec in pairs:
        k = key
        if type(key) is list and len(key) == 2:
            k = (key[0], tuple(key[1]) if type(key[1]) is list else key[1])
        elif type(key) is list:
            k = tuple(key)
        store[k] = rec
    src._records = store
    return src


def _run(module, steps):
    table = module.ContextTable()
    out = []
    for step in steps:
        before = _dump(table)
        do = step["do"]
        if do == "load" or do == "merge":
            res = _outcome(
                lambda step=step: table.merge(_source(module, json.loads(step["text"]))) is table
            )
        elif do == "insert":
            res = _outcome(lambda step=step: table.insert(step.get("variant", V), step["path"]))
        elif do == "hostile":
            call, arg = _hostile_call(module, step["hostile"], table)
            snapshot = _inert(list(arg) if call == "insert" else arg)
            HOSTILE.clear()  # construction may hash/compare; only the production call counts
            if call == "insert":
                res = _outcome(lambda arg=arg: table.insert(*arg))
            else:
                res = _outcome(lambda arg=arg: table.merge(arg) is table)
            res["hostile"] = list(HOSTILE)
            res["arg_unchanged"] = _inert(list(arg) if call == "insert" else arg) == snapshot
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
    module = types.ModuleType(f"graph._t0146_mutant_{name}")
    module.__file__ = str(ROOT / "graph" / "opening_context.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    return module


def _child_main():
    payload = json.load(sys.stdin)
    name = payload.get("mutant")
    module = oc if name is None else _source_mutant(name, MUTANTS[name][0])
    json.dump([_run(module, job) for job in payload["jobs"]], sys.stdout, sort_keys=True)


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0146_opening_context_integration_restart import _child_main; _child_main()"
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


def _local(jobs, module=oc):
    return json.loads(json.dumps([_run(module, job) for job in jobs], sort_keys=True))


# -- expected outcomes ----------------------------------------------------------------


def _ok_insert(path):
    return {"ok": True, "got": _record(path)}


_OK_LOAD = {"ok": True, "got": True}


def _rej(cls):
    return {"err": cls, "code": MAPPING[cls], "typed": True, "cause": False, "unchanged": True}


# -- scenarios: (jobs, want, labels) --------------------------------------------------


def _split_jobs():
    """Parent inserts PATHS[:k] and persists; the fresh process loads the
    persisted table and inserts the rest (reinserting PATHS[k-1] too)."""
    jobs, want, labels = [], [], []
    full = _pairs(PATHS)
    for k in range(len(PATHS) + 1):
        text = _text(_pairs(PATHS[:k]))
        steps = [{"do": "load", "text": text}]
        rest = PATHS[k:] + (PATHS[k - 1 : k] if k else [])
        steps += [{"do": "insert", "path": p} for p in rest]
        jobs.append(steps)
        want.append({"steps": [_OK_LOAD] + [_ok_insert(p) for p in rest], "table": full})
        labels.append(f"split@{k}")
    return jobs, want, labels


def _boundary_jobs():
    jobs, want, labels = [], [], []
    rows = [
        ("empty-table", [{"do": "load", "text": "[]"}], [_OK_LOAD], []),
        (
            "empty-path-sentinel",
            [{"do": "load", "text": "[]"}, {"do": "insert", "path": []}],
            [_OK_LOAD, _ok_insert([])],
            [[]],
        ),
        (
            "longest-prefix-wins-after-restart",
            [
                {"do": "load", "text": _text(_pairs([["e2e4", "c7c5"]]))},
                {"do": "insert", "path": ENTRIES[2]["moves"]},
            ],
            [_OK_LOAD, _ok_insert(ENTRIES[2]["moves"])],
            [["e2e4", "c7c5"], ENTRIES[2]["moves"]],
        ),
    ]
    for entry in ENTRIES:
        for path in (entry["moves"], entry["moves"][:-1], [*entry["moves"], "h2h3"]):
            rows.append(
                (
                    f"entry-{entry['code']}-{len(path)}",
                    [{"do": "load", "text": _text(_pairs([path]))}, {"do": "insert", "path": path}],
                    [_OK_LOAD, _ok_insert(path)],
                    [path],
                )
            )
    for label, steps, outs, paths in rows:
        jobs.append(steps)
        want.append({"steps": outs, "table": _pairs(paths)})
        labels.append(label)
    return jobs, want, labels


def _tampers():
    """(name, pairs, class): persisted pairs with one corruption each."""
    good = _pairs([["e2e4", "c7c5"], ["a2a3"], []])
    d20 = _record(NEW_PATH)
    k20 = [V, list(NEW_PATH)]

    def with_record(**over):
        return [*good, [k20, {**d20, **over}]]

    rows = [
        (
            "missing-field",
            [*good, [k20, {k: v for k, v in d20.items() if k != "opening_name"}]],
            MCR,
        ),
        ("extra-field", with_record(note="x"), MCR),
        ("variant-non-str", [*good, [[1, list(NEW_PATH)], {**d20, "variant": 1}]], MCR),
        ("code-non-str", with_record(opening_code=20), MCR),
        ("name-none", with_record(opening_name=None), MCR),
        ("path-str", [*good, [[V, "d2d4"], {**d20, "path_moves": "d2d4"}]], MCR),
        ("path-move-int", [*good, [[V, [5]], {**d20, "path_moves": [5]}]], MCR),
        (
            "unknown-variant",
            [*good, [["atomic", list(NEW_PATH)], {**d20, "variant": "atomic"}]],
            UV,
        ),
        ("move-grammar", [*good, [[V, ["e2e9"]], _record(["e2e9"])]], MP),
        ("move-null", [*good, [[V, ["e2e2"]], _record(["e2e2"])]], MP),
        ("move-promo-letter", [*good, [[V, ["a7a8k"]], _record(["a7a8k"])]], MP),
        ("code-unregistered", with_record(opening_code="E99"), UOC),
        ("code-grammar", with_record(opening_code="Z1"), MCR),
        ("code-lowercase", with_record(opening_code="d20"), MCR),
        ("sentinel-code-only", with_record(opening_code=SENTINEL), MCR),
        ("sentinel-name-only", with_record(opening_name=SENTINEL), MCR),
        ("name-mismatch", with_record(opening_name="Queen's Gambit Accepted"), MCR),
        (
            "shorter-prefix-claimed",
            [
                *good,
                [
                    [V, ENTRIES[2]["moves"]],
                    {
                        **_record(ENTRIES[2]["moves"]),
                        "opening_code": "B20",
                        "opening_name": "Sicilian Defense",
                    },
                ],
            ],
            MCR,
        ),
        (
            "unclassified-claims-entry",
            [
                *good,
                [
                    [V, ["a2a4"]],
                    {
                        **_record(["a2a4"]),
                        "opening_code": "A04",
                        "opening_name": "Zukertort Opening",
                    },
                ],
            ],
            MCR,
        ),
        (
            "classified-claims-sentinel",
            with_record(opening_code=SENTINEL, opening_name=SENTINEL),
            MCR,
        ),
        ("key-path-drift", [*good, [[V, ["d2d4", "d7d5"]], d20]], MCR),
        ("key-variant-drift", [*good, [["atomic", list(NEW_PATH)], d20]], MCR),
        ("key-path-str", [*good, [[V, "d2d4"], d20]], MCR),
        ("key-short", [*good, [[V], d20]], MCR),
        # the first failing record decides the class
        (
            "mp-then-uoc",
            [*good, [[V, ["e2e9"]], _record(["e2e9"])], [k20, {**d20, "opening_code": "E99"}]],
            MP,
        ),
        (
            "uoc-then-mp",
            [*good, [k20, {**d20, "opening_code": "E99"}], [[V, ["e2e9"]], _record(["e2e9"])]],
            UOC,
        ),
        (
            "uv-then-mcr",
            [
                [["atomic", list(NEW_PATH)], {**d20, "variant": "atomic"}],
                *good,
                [k20, {**d20, "note": 1}],
            ],
            UV,
        ),
    ]
    return rows


def _tampered_jobs():
    """A tampered persisted table is rejected in the fresh process with no
    trace; the same process then loads the clean table and inserts."""
    jobs, want, labels = [], [], []
    clean = _pairs([["e2e4", "c7c5"], ["a2a3"], []])
    for name, pairs, cls in _tampers():
        jobs.append(
            [
                {"do": "load", "text": _text(pairs)},
                {"do": "load", "text": _text(clean)},
                {"do": "insert", "path": NEW_PATH},
            ]
        )
        want.append(
            {
                "steps": [_rej(cls), _OK_LOAD, _ok_insert(NEW_PATH)],
                "table": _pairs([["e2e4", "c7c5"], ["a2a3"], [], NEW_PATH]),
            }
        )
        labels.append(name)
    return jobs, want, labels


def _conflict_rollback_jobs():
    """Same-key disagreement across a restart is conflicting_context; a
    rejected merge or insert after a load leaves the loaded table as is."""
    base = _pairs([["e2e4", "c7c5"], ["a2a3"]])
    sic = [V, ["e2e4", "c7c5"]]
    rows = [
        (
            "same-key-other-entry",
            [
                [
                    sic,
                    {
                        **_record(["e2e4", "c7c5"]),
                        "opening_code": "C20",
                        "opening_name": "King's Pawn Game",
                    },
                ]
            ],
            CC,
        ),
        (
            "same-key-sentinel",
            [
                [
                    sic,
                    {
                        **_record(["e2e4", "c7c5"]),
                        "opening_code": SENTINEL,
                        "opening_name": SENTINEL,
                    },
                ]
            ],
            CC,
        ),
        # a valid new record, then a conflict: nothing of the batch commits
        (
            "new-then-conflict",
            [
                [[V, list(NEW_PATH)], _record(NEW_PATH)],
                [
                    sic,
                    {
                        **_record(["e2e4", "c7c5"]),
                        "opening_code": "C20",
                        "opening_name": "King's Pawn Game",
                    },
                ],
            ],
            CC,
        ),
        (
            "new-then-malformed",
            [
                [[V, list(NEW_PATH)], _record(NEW_PATH)],
                [[V, ["g1f3"]], {**_record(["g1f3"]), "opening_code": "E99"}],
            ],
            UOC,
        ),
    ]
    jobs, want, labels = [], [], []
    for name, pairs, cls in rows:
        jobs.append(
            [
                {"do": "load", "text": _text(base)},
                {"do": "merge", "text": _text(pairs)},
                {"do": "insert", "path": ["e2e4", "c7c5"]},
            ]
        )
        want.append(
            {
                "steps": [_OK_LOAD, _rej(cls), _ok_insert(["e2e4", "c7c5"])],
                "table": base,
            }
        )
        labels.append(name)
    # identical same-key incoming is a no-op accept
    jobs.append([{"do": "load", "text": _text(base)}, {"do": "merge", "text": _text(base)}])
    want.append({"steps": [_OK_LOAD, _OK_LOAD], "table": base})
    labels.append("same-key-identical")
    # rejected inserts after a load
    for name, step, cls in (
        ("insert-bad-move", {"do": "insert", "path": ["e2e9"]}, MP),
        ("insert-unknown-variant", {"do": "insert", "variant": "atomic", "path": ["e2e4"]}, UV),
        ("insert-path-str", {"do": "insert", "path": "e2e4"}, MP),
    ):
        jobs.append([{"do": "load", "text": _text(base)}, step])
        want.append({"steps": [_OK_LOAD, _rej(cls)], "table": base})
        labels.append(name)
    return jobs, want, labels


def _hostile_jobs():
    jobs, want, labels = [], [], []
    base = _text(_pairs([["e2e4", "c7c5"], ["a2a3"], []]))
    for spec, cls in _hostile_rows():
        jobs.append([{"do": "load", "text": base}, {"do": "hostile", "hostile": spec}])
        want.append(
            {
                "steps": [_OK_LOAD, {**_rej(cls), "hostile": [], "arg_unchanged": True}],
                "table": json.loads(base),
            }
        )
        labels.append(spec)
    return jobs, want, labels


def _bad(builder, got):
    _jobs, want, labels = builder()
    return [label for label, g, w in zip(labels, got, want, strict=True) if g != w]


# -- tests: happy and boundary --------------------------------------------------------


def test_model_matches_the_registry_on_its_own_entries():
    for entry in ENTRIES:
        assert _model(entry["moves"]) == (entry["code"], entry["name"])
    assert _model([]) == (SENTINEL, SENTINEL)
    assert SENTINEL == "-"


def test_restart_at_every_split_matches_the_model_and_the_uninterrupted_run():
    jobs, want, _labels = _split_jobs()
    got = _restart(jobs)
    assert _bad(_split_jobs, got) == []
    assert got == _local(jobs)
    uninterrupted = _local([[{"do": "insert", "path": p} for p in PATHS]])[0]
    assert uninterrupted["table"] == _pairs(PATHS)


def test_three_process_chain_persists_exactly():
    third = len(PATHS) // 3
    text = _text(_local([[{"do": "insert", "path": p} for p in PATHS[:third]]])[0]["table"])
    first = _restart(
        [
            [{"do": "load", "text": text}]
            + [{"do": "insert", "path": p} for p in PATHS[third : 2 * third]]
        ]
    )[0]
    second = _restart(
        [
            [{"do": "load", "text": _text(first["table"])}]
            + [{"do": "insert", "path": p} for p in PATHS[2 * third :]]
        ]
    )[0]
    assert second["table"] == _pairs(PATHS)
    reloaded = _restart([[{"do": "load", "text": _text(second["table"])}]])[0]
    assert reloaded == {"steps": [_OK_LOAD], "table": _pairs(PATHS)}


def test_output_is_deterministic_across_hash_seeds():
    jobs = _split_jobs()[0][::5]
    assert _restart(jobs, hash_seed="1", raw=True) == _restart(jobs, hash_seed="4242", raw=True)


def test_boundaries_after_restart():
    jobs, want, _labels = _boundary_jobs()
    got = _restart(jobs)
    assert _bad(_boundary_jobs, got) == []
    assert got == _local(jobs)


def test_persisted_key_order_never_changes_the_table():
    pairs = _pairs(PATHS)
    got = _restart([[{"do": "load", "text": _text(list(reversed(pairs)))}]])[0]
    assert got["table"] == pairs


# -- tests: malformed, rollback, hostile ----------------------------------------------


def test_tampered_persisted_tables_are_rejected_typed_in_the_fresh_process():
    jobs, want, _labels = _tampered_jobs()
    got = _restart(jobs)
    assert _bad(_tampered_jobs, got) == []
    assert got == _local(jobs)
    assert {cls for _n, _p, cls in _tampers()} == {MCR, MP, UV, UOC}


def test_conflicts_and_rejections_roll_back_across_the_restart():
    jobs, want, _labels = _conflict_rollback_jobs()
    got = _restart(jobs)
    assert _bad(_conflict_rollback_jobs, got) == []
    assert got == _local(jobs)


def test_rollback_survives_the_restart():
    base = _pairs([["e2e4", "c7c5"], ["a2a3"]])
    bad = [[[V, list(NEW_PATH)], _record(NEW_PATH)], [[V, ["e2e9"]], _record(["e2e9"])]]
    parent = _local([[{"do": "load", "text": _text(base)}, {"do": "merge", "text": _text(bad)}]])[0]
    assert parent["steps"][1] == _rej(MP)
    child = _restart([[{"do": "load", "text": _text(parent["table"])}]])[0]
    assert child == {"steps": [_OK_LOAD], "table": base}


def test_hostile_types_at_every_input_boundary_are_rejected_typed_and_inert():
    jobs, want, _labels = _hostile_jobs()
    got = _restart(jobs)
    assert _bad(_hostile_jobs, got) == []
    assert got == _local(jobs)


def test_returned_records_are_detached_after_restart():
    table = oc.ContextTable()
    table.merge(_source(oc, _pairs(PATHS[:5])))
    got = table.insert(V, PATHS[1])
    got["path_moves"].append("h7h6")
    got["opening_code"] = "X"
    for rec in table.records:
        rec["variant"] = "poison"
    assert _dump(table) == _pairs(PATHS[:5])


# -- stored-table and direct rows (in-process) ----------------------------------------


def _loaded(module):
    table = module.ContextTable()
    table.merge(_source(module, _pairs([["e2e4", "c7c5"], ["a2a3"], []])))
    return table


def _stored_problems(module=oc):
    """Rows the restart scenarios cannot express (hostile objects inside the
    STORED table, the returned record, same-arity renames); [] when all hold."""
    bad = []
    key0 = (V, ("e2e4", "c7c5"))

    def expect(label, table, fn, cls, before=None):
        before = _dump(table) if before is None else before
        HOSTILE.clear()
        res = _outcome(fn)
        calls = list(HOSTILE)
        if res.get("err") != cls or not res.get("typed") or res.get("cause"):
            bad.append((label, "class", {k: v for k, v in res.items() if k != "got"}))
        elif calls:
            bad.append((label, "hostile", calls))
        elif _dump(table) != before:
            bad.append((label, "table-changed"))

    # C9: a tuple-subclass path tuple in a persisted key
    rec = _record(["e2e4"])
    src = module.ContextTable()
    src._records = {(V, _LogTuple(("e2e4",))): rec}
    table = _loaded(module)
    expect("key-path-tuple-subclass", table, lambda: table.merge(src), MCR)
    # C13, C14, C32: a tampered STORED record, caught by the current pass of merge
    stored = []
    for form in ("plain", "eq-raises"):
        stored.append((f"stored-variant-{form}", "variant", form, UV))
        stored.append((f"stored-code-{form}", "opening_code", form, MCR))
        stored.append((f"stored-name-{form}", "opening_name", form, MCR))
    for label, field, form, cls in stored:
        table = _loaded(module)
        good = table._records[key0]
        table._records[key0] = {**good, field: _hstr(form, good[field], good[field])}
        before = _dump(table)
        expect(label, table, lambda t=table: t.merge(module.ContextTable()), cls, before)
    for label, change in (
        ("stored-extra-field", lambda r: {**r, "note": "x"}),
        ("stored-missing-field", lambda r: {k: v for k, v in r.items() if k != "opening_name"}),
        (
            "stored-renamed-field",
            lambda r: {("varient" if k == "variant" else k): v for k, v in r.items()},
        ),
    ):
        table = _loaded(module)
        table._records[key0] = change(table._records[key0])
        before = _dump(table)
        expect(label, table, lambda t=table: t.merge(module.ContextTable()), MCR, before)
    # C33: a same-arity renamed field in an incoming record
    renamed = {("varient" if k == "variant" else k): v for k, v in _record(NEW_PATH).items()}
    table = _loaded(module)
    src = module.ContextTable()
    src._records = {(V, tuple(NEW_PATH)): renamed}
    expect("incoming-renamed-field", table, lambda: table.merge(src), MCR)
    # C19: a stored record that disagrees with a re-insert of its key
    table = _loaded(module)
    table._records[key0] = {**table._records[key0], "opening_name": "Tampered"}
    before = _dump(table)
    expect("insert-conflict-with-stored", table, lambda: table.insert(V, list(key0[1])), CC, before)
    # C29: mutating what insert, records and map return never reaches the table
    table = _loaded(module)
    want = _dump(table)
    got = table.insert(V, NEW_PATH)
    want = _dump(table)
    again = table.insert(V, ["a2a3"])
    for rec in (got, again):
        rec["opening_code"] = "X99"
        rec["path_moves"].append("h7h6")
    for rec in table.records:
        rec["variant"] = "poison"
    for rec in table.map.values():
        rec["opening_name"] = "poison"
    if _dump(table) != want:
        bad.append(("returned-record-detached", "table-changed"))
    res = _outcome(lambda: table.merge(module.ContextTable()) is table)
    if res != {"ok": True, "got": True} or _dump(table) != want:
        bad.append(("returned-record-detached", "merge", res.get("err")))
    return bad


def test_stored_table_and_direct_rows():
    assert _stored_problems() == []


# -- in-file mutants: each is RED on its own scenario ---------------------------------


def _red(builder):
    def red(module):
        jobs, want, _labels = builder()
        return _local(jobs, module) != want

    return red


MUTANTS = {
    "exact-key-inner-tuple-isinstance-off": (
        [("        and type(key[1]) is tuple\n", "")],
        _stored_problems,
    ),
    "validate-variant-type-off": (
        [
            (
                '        if type(record["variant"]) is not str or '
                'record["variant"] not in self.variants:',
                '        if record["variant"] not in self.variants:',
            )
        ],
        _stored_problems,
    ),
    "validate-code-name-type-off": (
        [
            (
                "        if type(code) is not str or type(name) is not str:\n"
                '            _fail("malformed_context_record")\n',
                "",
            )
        ],
        _stored_problems,
    ),
    "insert-conflict-as-malformed": (
        [
            (
                '                _fail("conflicting_context")\n'
                "            return copy.deepcopy(existing)",
                '                _fail("malformed_context_record")\n'
                "            return copy.deepcopy(existing)",
            )
        ],
        _stored_problems,
    ),
    "insert-returns-live-record": (
        [
            (
                "        self._records[key] = copy.deepcopy(record)\n"
                "        return copy.deepcopy(record)",
                "        self._records[key] = record\n        return record",
            )
        ],
        _stored_problems,
    ),
    "validate-field-superset": (
        [
            (
                "    def _validate(self, record):\n"
                "        if not _exact_dict(record) or set(record) != _FIELDS:",
                "    def _validate(self, record):\n"
                "        if not _exact_dict(record) or not set(record) >= _FIELDS:",
            )
        ],
        _stored_problems,
    ),
    "typed-field-set-by-length": (
        [
            (
                'str fields."""\n        if not _exact_dict(record) or set(record) != _FIELDS:',
                'str fields."""\n'
                "        if not _exact_dict(record) or len(record) != len(_FIELDS):",
            )
        ],
        _stored_problems,
    ),
    "merge-skips-new-key-validation": (
        [
            (
                "            staged._records[key] = copy.deepcopy(self._validate(record))",
                "            staged._records[key] = copy.deepcopy(record)",
            )
        ],
        _red(_tampered_jobs),
    ),
    "merge-stages-in-place": (
        [
            (
                "        staged = ContextTable(registry=self.registry, variants=self.variants)\n"
                "        staged._records = {key: copy.deepcopy(record)"
                " for key, record in current}\n",
                "        staged = self\n",
            )
        ],
        _red(_conflict_rollback_jobs),
    ),
    "conflict-reported-as-malformed": (
        [
            (
                '                ):\n                    _fail("conflicting_context")',
                '                ):\n                    _fail("malformed_context_record")',
            )
        ],
        _red(_conflict_rollback_jobs),
    ),
    "resolve-shortest-prefix": (
        [('len(prefix) > len(best["moves"])', 'len(prefix) < len(best["moves"])')],
        _red(_split_jobs),
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
    "exact-key-drops-move-types": (
        [("        and all(type(move) is str for move in key[1])\n", "")],
        _red(_hostile_jobs),
    ),
    "exact-key-tuple-isinstance": (
        [("        type(key) is tuple\n", "        isinstance(key, tuple)\n")],
        _red(_hostile_jobs),
    ),
    "path-list-isinstance": (
        [("    if type(value) is not list:", "    if not isinstance(value, list):")],
        _red(_hostile_jobs),
    ),
    "variant-membership-without-type-check": (
        [
            (
                "        if type(variant) is not str or variant not in self.variants:",
                "        if variant not in self.variants:",
            )
        ],
        _red(_hostile_jobs),
    ),
    "merge-other-isinstance": (
        [
            (
                "        if type(other) is not ContextTable:",
                "        if not isinstance(other, ContextTable):",
            )
        ],
        _red(_hostile_jobs),
    ),
    "store-dict-isinstance": (
        [("        if type(records) is not dict:", "        if not isinstance(records, dict):")],
        _red(_hostile_jobs),
    ),
}


def test_identity_mutant_is_green():
    module = _source_mutant("identity", [])
    for builder in (
        _split_jobs,
        _boundary_jobs,
        _tampered_jobs,
        _conflict_rollback_jobs,
        _hostile_jobs,
    ):
        jobs, want, _labels = builder()
        assert _local(jobs, module) == want, builder.__name__
    assert _stored_problems(module) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_scenario(name):
    edits, red = MUTANTS[name]
    assert red(_source_mutant(name, edits)), name


def test_a_mutant_is_red_in_a_fresh_process_too():
    jobs, want, _labels = _tampered_jobs()
    assert _restart(jobs, mutant="merge-skips-new-key-validation") != want


def test_scenarios_do_not_share_state_with_the_expected_tables():
    jobs, want, _labels = _split_jobs()
    frozen = copy.deepcopy(want)
    _local(jobs)
    assert want == frozen
