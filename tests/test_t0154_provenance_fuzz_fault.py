"""T0154 provenance fuzz/fault campaign against the production runtime
(graph.provenance, T0152).

Where T0153 pins properties over seeded records, this task runs a
larger classified campaign with four duties:

1. CLASSIFICATION: every campaign case (insert, or merge of a source
   batch) ends exactly one of accept - the table equals an independent
   union model: key = (target_kind, target identity), sources = the set
   union ordered by (source_id, game_id, first_observed_at) - or reject
   - ProvenanceError with the failure class the case's single
   corruption predicts, its mapped code, retryable False, and no
   __cause__/__context__. Any other exception is a CRASH and fails the
   suite with the case recorded.
2. ROLLBACK: every reject leaves the table bit-identical; a merge batch
   with one bad record anywhere commits nothing.
3. FAULT INJECTION: one-edit production mutants run through the same
   campaign; each must be caught by its own check.
4. DETERMINISM: the classified log's SHA-256 is pinned.
5. DETACHMENT: after every accept, scribbling on each record the caller
   still holds (inputs, returned records, a records() snapshot) leaves
   the table unchanged, and serialize() is always sorted.

A deterministic sweep runs before the seeded cases so no draw decides
coverage: every (snapshot field x non-canonical form), every (level x
field) for str-subclass and same-arity renamed keys, str-subclass
game_id/timestamp values, the move grammar (edge move and context path
element: from == to, non-enum promotion, str subclass, each off-board
component, lengths 0-3 and 6) - each as an insert and inside a merge batch -
linked node runtime faults for every declared except class, and merge
sources whose records() raises (Exception and BaseException-only), is
not callable, is missing, or returns a tuple or list subclass.

Every corruption generator GUARANTEES invalidity: an independent
predicate re-checks the corrupted record against the model's validity
rules and the campaign fails if a "corruption" is actually valid.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import random
import types
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from graph import provenance as pv
from graph.position_digest import DigestError
from tools.variant_runtime import VariantError

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
EDGES = [
    ("e2e4", START, AFTER_E4),
    ("e7e5", AFTER_E4, AFTER_E4_E5),
    ("e2e3", START, AFTER_E4),
    ("e2e4", START, AFTER_E4_E5),
]
FILES, RANKS, PROMOS = "abcdefgh", "12345678", "qrbn"
CASES = 700
MUTANT_CASES = 350  # the deterministic sweep always runs in full first
MPR, UTK, MTI, US = (
    "malformed_provenance_record",
    "unknown_target_kind",
    "malformed_target_identity",
    "unknown_source",
)
CLASSES = {MPR, UTK, MTI, US}


class _S(str):
    pass


class _L(list):
    pass


class _D(dict):
    pass


class _Boom(BaseException):
    """A BaseException-only raiser (not Exception)."""


# -- generators and the independent model -------------------------------------------


def _move(rng):
    while True:
        a = rng.choice(FILES) + rng.choice(RANKS)
        b = rng.choice(FILES) + rng.choice(RANKS)
        if a != b:
            return a + b + rng.choice(["", "", "", *PROMOS])


def _stamp(rng):
    return (
        f"20{rng.randint(0, 99):02d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        f"T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"
    )


def _source(rng):
    return {
        "source_id": rng.choice(SOURCE_IDS),
        "game_id": "".join(chr(rng.randint(0x21, 0x7E)) for _ in range(rng.randint(1, 8))),
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
    return kind, {
        "variant": "standard",
        "path_moves": [_move(rng) for _ in range(rng.randint(0, 3))],
    }


def _record(rng, pool):
    kind, target = rng.choice(pool) if pool and rng.random() < 0.7 else _target(rng)
    srcs = [_source(rng) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.3:
        srcs.append(dict(srcs[0]))
    if rng.random() < 0.2:  # same source_id + game_id, another stamp
        srcs.append({**srcs[0], "first_observed_at": _stamp(rng)})
    return {"target_kind": kind, "target": copy.deepcopy(target), "sources": srcs}


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


def _model_add(model, rec):
    k = _model_key(rec)
    entry = model.setdefault(
        k, {"target_kind": rec["target_kind"], "target": rec["target"], "srcs": {}}
    )
    for s in rec["sources"]:
        entry["srcs"][_skey(s)] = dict(s)
    return k


def _model_record(model, k):
    e = model[k]
    return {
        "target_kind": e["target_kind"],
        "target": e["target"],
        "sources": [e["srcs"][x] for x in sorted(e["srcs"])],
    }


def _model_rows(model):
    return sorted((e["target_kind"], tuple(sorted(e["srcs"]))) for e in model.values())


# -- corruption generators (each guarantees invalidity) -------------------------------


def _bad_move(rng):
    good = _move(rng)
    return rng.choice(
        [
            "z" + good[1:],
            good[0] + "9" + good[2:],
            good[:2] + "i" + good[3:],
            good[:3] + rng.choice("09") + good[4:],
            good[:2] + good[:2],
            good[:4] + rng.choice("kKQx"),
            good[: rng.choice([0, 1, 2, 3])],
            good[:4] + "qq",
            _S(good),
            None,
        ]
    )


def _corrupt(rng, rec):
    """(record, expected class) with exactly one corruption."""
    rec = copy.deepcopy(rec)
    kind = rng.randrange(19)
    if kind == 16:  # a str-subclass key at the record, target or source level
        return _subclass_key(rng.choice(_LEVELS), rng.choice, rec)
    if kind == 17:  # same-arity renamed key at the record, target or source level
        return _renamed_key(rng.choice(_LEVELS), rng.choice, rec)
    if kind == 18:  # a str-subclass game_id or timestamp
        s = rec["sources"][0]
        field = rng.choice(["game_id", "first_observed_at"])
        return {**rec, "sources": [{**s, field: _S(s[field])}]}, MPR
    if kind == 0:
        return _D(rec), MPR
    if kind == 1:
        rec.pop(rng.choice(sorted(rec)))
        return rec, MPR
    if kind == 2:
        return {**rec, "extra": 1}, MPR
    if kind == 3:
        return {**rec, "target_kind": rng.choice(["game", "", _S(rec["target_kind"]), None])}, UTK
    if kind == 4:
        return {
            **rec,
            "sources": rng.choice([[], tuple(rec["sources"]), _L(rec["sources"]), None]),
        }, MPR
    s = rec["sources"][0]
    if kind == 5:
        return {**rec, "sources": [{**s, "source_id": rng.choice(["fics", "PGN-FILE", "x"])}]}, US
    if kind == 6:
        return {
            **rec,
            "sources": [{**s, "source_id": rng.choice([_S(SOURCE_IDS[0]), 1, None])}],
        }, MPR
    if kind == 7:
        return {
            **rec,
            "sources": [{**s, "game_id": rng.choice(["", "a b", "a\x7f", "g\u00e9", 5])}],
        }, MPR
    if kind == 8:
        bad = rng.choice(
            [
                "2026-02-29T00:00:00Z",
                "2026-09-01T24:00:00Z",
                "2026-09-01T00:00:00z",
                "2026-09-01T00:00:00+00:00",
                "0000-01-01T00:00:00Z",
                "2026-9-01T00:00:00Z",
            ]
        )
        return {**rec, "sources": [{**s, "first_observed_at": bad}]}, MPR
    if kind == 9:
        return {**rec, "sources": [{**s, "extra": "x"}]}, MPR
    if kind == 10:
        return {**rec, "sources": [dict(s), "not-a-dict"]}, MPR
    # target corruptions (target identity)
    t = rec["target"]
    if kind == 11:
        return {**rec, "target": {**t, "variant": rng.choice(["zz", _S("standard"), None])}}, MTI
    if kind == 12:
        return {**rec, "target": {**t, "extra": 1}}, MTI
    if rec["target_kind"] == "opening_context":
        if kind == 13:
            return {
                **rec,
                "target": {**t, "path_moves": list(t["path_moves"]) + [_bad_move(rng)]},
            }, MTI
        return {
            **rec,
            "target": {
                **t,
                "path_moves": rng.choice([tuple(t["path_moves"]), _L(t["path_moves"]), None]),
            },
        }, MTI
    if rec["target_kind"] == "route_edge":
        if kind == 13:
            return {**rec, "target": {**t, "move": _bad_move(rng)}}, MTI
        field = rng.choice(["from_snapshot_fen", "to_snapshot_fen"])
        return {**rec, "target": {**t, field: _noncanonical(rng, t[field])}}, MTI
    return {**rec, "target": {**t, "snapshot_fen": _noncanonical(rng, t["snapshot_fen"])}}, MTI


NONCANONICAL = [
    lambda fen: fen[:-3] + "5 9",
    lambda fen: fen[:-1] + "2",
    lambda fen: "x",
    lambda fen: fen[:-3] + "0 " + "9" * 5000,  # a 5000-digit clock (int() ValueError)
]


def _noncanonical(rng, fen):
    return rng.choice(NONCANONICAL)(fen)


_LEVELS = ("record", "target", "source")


def _rekey(d, field, new):
    return {(new if k == field else k): v for k, v in d.items()}


def _at_level(level, rec, fn):
    """Apply fn to the dict at `level`; returns (record, expected class)."""
    if level == "record":
        return fn(rec), MPR
    if level == "target":
        return {**rec, "target": fn(rec["target"])}, MTI
    return {**rec, "sources": [fn(rec["sources"][0]), *rec["sources"][1:]]}, MPR


def _subclass_key(level, choose, rec):
    return _at_level(level, rec, lambda d: _rekey(d, (f := choose(sorted(d))), _S(f)))


def _renamed_key(level, choose, rec):
    return _at_level(level, rec, lambda d: _rekey(d, (f := choose(sorted(d))), f + "_x"))


def _is_valid(rec):
    """Independent validity of a record under the model's rules."""
    try:
        dicts = [rec, rec["target"], *rec["sources"]]
        if not all(type(k) is str for d in dicts for k in dict.keys(d)):
            return False
        if type(rec) is not dict or set(rec) != {"target_kind", "target", "sources"}:
            return False
        if (
            rec["target_kind"] not in ("transposition_node", "route_edge", "opening_context")
            or type(rec["target_kind"]) is not str
        ):
            return False
        srcs = rec["sources"]
        if type(srcs) is not list or not srcs:
            return False
        for s in srcs:
            if type(s) is not dict or set(s) != {"source_id", "game_id", "first_observed_at"}:
                return False
            if type(s["source_id"]) is not str or s["source_id"] not in SOURCE_IDS:
                return False
            g = s["game_id"]
            if type(g) is not str or not g or not all(0x21 <= ord(c) <= 0x7E for c in g):
                return False
            ts = s["first_observed_at"]
            if type(ts) is not str or ts not in {x for x in [ts] if _stamp_ok(x)}:
                return False
        t = rec["target"]
        if type(t) is not dict or type(t.get("variant")) is not str or t["variant"] != "standard":
            return False
        if rec["target_kind"] == "opening_context":
            return (
                set(t) == {"variant", "path_moves"}
                and type(t["path_moves"]) is list
                and all(_move_ok(m) for m in t["path_moves"])
            )
        if rec["target_kind"] == "route_edge":
            return (
                set(t) == {"variant", "move", "from_snapshot_fen", "to_snapshot_fen"}
                and _move_ok(t["move"])
                and t["from_snapshot_fen"] in FENS
                and t["to_snapshot_fen"] in FENS
            )
        return set(t) == {"variant", "snapshot_fen"} and t["snapshot_fen"] in FENS
    except Exception:  # noqa: BLE001 - any oddity is invalid
        return False


def _stamp_ok(ts):
    if len(ts) != 20 or not ts.isascii() or not ts.endswith("Z"):
        return False
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") == ts
    except ValueError:
        return False


def _move_ok(m):
    return (
        type(m) is str
        and len(m) in (4, 5)
        and m[0] in FILES
        and m[1] in RANKS
        and m[2] in FILES
        and m[3] in RANKS
        and m[:2] != m[2:4]
        and (len(m) == 4 or m[4] in PROMOS)
    )


# -- the campaign ---------------------------------------------------------------------


def _outcome(call):
    try:
        return ("accept", call())
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # a BaseException-only escape is a crash, not an abort
        if type(exc).__name__ == "ProvenanceError" and type(exc).__module__ in (
            pv.__name__,
            "provenance_mutant",
        ):
            fresh = exc.__cause__ is None and exc.__context__ is None
            return ("reject", exc.failure_class, exc.code, exc.retryable, fresh)
        return ("crash", type(exc).__name__)


class _Bag:
    def __init__(self, records):
        self._r = records

    def records(self):
        return self._r


def _state(t):
    return t.records(), t.serialize()


def _rows(t):
    return sorted((r[0], r[2]) for r in t.serialize())


def _scribble(rec):
    """Mutate every nested level of a record the caller holds."""
    rec["sources"][0]["game_id"] += "~"
    rec["sources"].append({"source_id": "x", "game_id": "x", "first_observed_at": "x"})
    tgt = rec["target"]
    if "path_moves" in tgt:
        tgt["path_moves"].append("a1a2")
    tgt["variant"] = "zz"
    rec["target_kind"] = "zz"


def _detached(t, held):
    """After an accept: scribbling on every record the caller still holds
    (inputs, returned values, a records() snapshot) never reaches the table."""
    after = _state(t)
    for rec in [*held, *t.records()]:
        _scribble(rec)
    return _state(t) == after


class _NoCall:
    records = 5


class _Raises:
    def __init__(self, exc):
        self._exc = exc

    def records(self):
        raise self._exc


LINKED_FAULTS = (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError)

BAD_SOURCES = [
    ("records-raises-exception", lambda: _Raises(ValueError("boom"))),
    ("records-raises-baseexception", lambda: _Raises(_Boom())),
    ("records-not-callable", _NoCall),
    ("records-missing", object),
    ("records-tuple", lambda: _Bag(())),
]


def _case(mod, rng, t, model, pool):
    op = rng.randrange(4)
    before = _state(t)
    if op == 0:  # valid insert (new target or union)
        rec = _record(rng, pool)
        inp = copy.deepcopy(rec)
        out = _outcome(lambda: t.insert(inp))
        k = _model_add(model, rec)
        if out != ("accept", _model_record(model, k)):
            return f"insert-valid {out[0]}", "accept-mismatch"
        if not _detached(t, [inp, out[1]]):
            return "insert-valid", "aliased"
    elif op == 1:  # corrupted insert
        bad, cls = _corrupt(rng, _record(rng, pool))
        if _is_valid(bad):
            return "generator", "corruption-was-valid"
        snap = copy.deepcopy(bad)
        out = _outcome(lambda: t.insert(bad))
        if out != ("reject", cls, mod.FAILURE_MAPPING[cls], False, True):
            return f"insert-bad {cls} {out[0]}", f"missing-rejection:{cls}:{out[:3]}"
        if _state(t) != before:
            return f"insert-bad {cls}", "rollback"
        if bad != snap:
            return f"insert-bad {cls}", "input-mutated"
    elif op == 2:  # merge of a valid batch
        batch = [_record(rng, pool) for _ in range(rng.randint(0, 4))]
        held = copy.deepcopy(batch)
        out = _outcome(lambda: t.merge(_Bag(held)))
        for rec in batch:
            _model_add(model, rec)
        if out[0] != "accept":
            return f"merge-valid {out[0]}", f"accept-mismatch:{out[:3]}"
        if not _detached(t, held):
            return "merge-valid", "aliased"
    else:  # merge of a batch with one corrupted record anywhere
        batch = [_record(rng, pool) for _ in range(rng.randint(1, 4))]
        bad, cls = _corrupt(rng, _record(rng, pool))
        if _is_valid(bad):
            return "generator", "corruption-was-valid"
        batch.insert(rng.randint(0, len(batch)), bad)
        out = _outcome(lambda: t.merge(_Bag(batch)))
        if out != ("reject", cls, mod.FAILURE_MAPPING[cls], False, True):
            return f"merge-bad {cls} {out[0]}", f"missing-rejection:{cls}:{out[:3]}"
        if _state(t) != before:
            return f"merge-bad {cls}", "rollback"
    if _rows(t) != _model_rows(model):
        return f"op{op}", "table-model-mismatch"
    if t.serialize() != sorted(t.serialize()):
        return f"op{op}", "serialize-unsorted"
    return f"op{op} {out[0]}" + (f" {out[1]}" if out[0] == "reject" else ""), None


_SRC = {"source_id": SOURCE_IDS[0], "game_id": "g1", "first_observed_at": "2026-01-01T00:00:00Z"}
_BASE = {
    "transposition_node": {"variant": "standard", "snapshot_fen": START},
    "route_edge": {
        "variant": "standard",
        "move": "e2e4",
        "from_snapshot_fen": START,
        "to_snapshot_fen": AFTER_E4,
    },
    "opening_context": {"variant": "standard", "path_moves": ["e2e4", "e7e5"]},
}


def _base(kind):
    return {"target_kind": kind, "target": copy.deepcopy(_BASE[kind]), "sources": [dict(_SRC)]}


SWEEP_BAD_MOVES = [
    "e2e2",  # from == to
    "e7e8k",  # promotion letter outside the enum
    "e7e8Q",
    "e2e4\n",
    _S("e2e4"),
    "z2e4",  # each off-board component
    "e9e4",
    "e2i4",
    "e2e0",
    "",  # lengths 0-3 and 6
    "e",
    "e2",
    "e2e",
    "e7e8qq",
    None,
]


def _sweep():
    """Deterministic (label, corrupted record, class) rows: every
    (snapshot field x non-canonical form) and every (level x field) for
    str-subclass and same-arity renamed keys, so no seeded draw decides
    whether a sub-variant is exercised."""
    rows = []
    for kind, fields in (
        ("transposition_node", ["snapshot_fen"]),
        ("route_edge", ["from_snapshot_fen", "to_snapshot_fen"]),
    ):
        for field in fields:
            for i, form in enumerate(NONCANONICAL):
                rec = _base(kind)
                rec["target"][field] = form(rec["target"][field])
                rows.append((f"noncanonical:{field}:{i}", rec, MTI))
    for kind in _BASE:
        for level in _LEVELS:
            base = _base(kind)
            names = sorted({"record": base, "target": base["target"]}.get(level, _SRC))
            for name in names:
                for tag, make in (("subclass-key", _subclass_key), ("renamed-key", _renamed_key)):
                    rec, cls = make(level, lambda _opts, n=name: n, _base(kind))
                    rows.append((f"{tag}:{kind}:{level}:{name}", rec, cls))
    # move grammar, for the edge move and a context path element alike
    for i, move in enumerate(SWEEP_BAD_MOVES):
        rec = _base("route_edge")
        rec["target"]["move"] = move
        rows.append((f"bad-move:edge:{i}", rec, MTI))
        rec = _base("opening_context")
        rec["target"]["path_moves"] = ["e2e4", move]
        rows.append((f"bad-move:context:{i}", rec, MTI))
    for field in ("game_id", "first_observed_at"):
        rec = _base("opening_context")
        rec["sources"][0][field] = _S(rec["sources"][0][field])
        rows.append((f"subclass-value:{field}", rec, MPR))
    return rows


def _sweep_cases(mod, t):
    """The deterministic rows, each as an insert and as a one-bad merge
    batch, plus merge sources that fail or return a non-list."""
    for label, bad, cls in _sweep():
        if _is_valid(bad):
            yield f"sweep {label}", "corruption-was-valid"
            continue
        for how, call in (
            ("insert", lambda bad=bad: t.insert(copy.deepcopy(bad))),
            ("merge", lambda bad=bad: t.merge(_Bag([_base("route_edge"), copy.deepcopy(bad)]))),
        ):
            before = _state(t)
            out = _outcome(call)
            if out != ("reject", cls, mod.FAILURE_MAPPING[cls], False, True):
                yield f"sweep {how} {label}", f"missing-rejection:{cls}:{label}:{out[:3]}"
            elif _state(t) != before:
                yield f"sweep {how} {label}", "rollback"
            else:
                yield f"sweep {how} {label} reject {cls}", None
    # the linked node runtime is a lower layer: every exception class the
    # boundary declares, raised from it, maps to a fresh typed rejection
    for kind in ("transposition_node", "route_edge"):
        for exc in LINKED_FAULTS:
            label = f"linked-raises:{exc.__name__}:{kind}"
            before = _state(t)
            real = mod.make_node_record

            def raiser(*_a, exc=exc):
                raise exc("linked fault")

            mod.make_node_record = raiser
            try:
                out = _outcome(lambda kind=kind: t.insert(_base(kind)))
            finally:
                mod.make_node_record = real
            if out != ("reject", MTI, mod.FAILURE_MAPPING[MTI], False, True):
                yield f"sweep {label}", f"missing-rejection:{MTI}:{label}:{out[:5]}"
            elif _state(t) != before:
                yield f"sweep {label}", "rollback"
            else:
                yield f"sweep {label} reject {MTI}", None
    sources = [(label, make) for label, make in BAD_SOURCES]
    sources.append(("records-list-subclass", lambda: _Bag(_L([_base("route_edge")]))))
    for label, make in sources:
        before = _state(t)
        out = _outcome(lambda make=make: t.merge(make()))
        if out != ("reject", MPR, mod.FAILURE_MAPPING[MPR], False, True):
            yield f"sweep merge-source {label}", f"missing-rejection:{MPR}:src:{label}:{out[:5]}"
        elif _state(t) != before:
            yield f"sweep merge-source {label}", "rollback"
        else:
            yield f"sweep merge-source {label} reject {MPR}", None


def _campaign(mod, seed=0, cases=CASES):
    rng = random.Random(seed)
    pool = [_target(rng) for _ in range(8)]
    t, model = mod.ProvenanceTable(), {}
    log, problems = [], []
    for line, problem in _sweep_cases(mod, t):
        log.append(f"s {line}")
        if problem:
            problems.append((-1, problem))
    for i in range(cases):
        line, problem = _case(mod, rng, t, model, pool)
        log.append(f"{i} {line}")
        if problem:
            problems.append((i, problem))
    return log, problems


_CLEAN = {}


def _clean_campaign():
    if not _CLEAN:
        _CLEAN["run"] = _campaign(pv)
    return _CLEAN["run"]


def test_r1_campaign_uses_no_test_helpers():
    modules = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)


def test_campaign_clean_run():
    log, problems = _clean_campaign()
    assert problems == []
    kinds = {line.split(" ", 2)[1] for line in log}
    assert {"op0", "op1", "op2", "op3"} <= kinds
    sweep = [line for line in log if line.startswith("s ")]
    assert len(sweep) == 2 * len(_sweep()) + 2 * len(LINKED_FAULTS) + len(BAD_SOURCES) + 1
    assert all(" reject " in line for line in sweep)
    rejected = {line.rsplit(" ", 1)[-1] for line in log if " reject " in line}
    assert rejected >= CLASSES


def test_determinism_pinned_log():
    a, _ = _clean_campaign()
    b, _ = _campaign(pv)
    assert a == b
    assert hashlib.sha256("\n".join(a).encode()).hexdigest() == PINNED_LOG_SHA256


PINNED_LOG_SHA256 = "d2e885620312996d0d5806a4db03507f8b7123125cf5658bfcf9026ff07e4dc3"


# -- fault injection: one-edit production mutants, each caught by its own check -------

MUTANTS = [
    (
        "source-key-drops-stamp",
        '    return entry["source_id"], entry["game_id"], entry["first_observed_at"]',
        '    return entry["source_id"], entry["game_id"]',
        "accept-mismatch",
    ),
    (
        "move-square-or-to-and",
        '    if any(square[0] not in grammar["files"] or square[1] not in grammar["ranks"]',
        '    if any(square[0] not in grammar["files"] and square[1] not in grammar["ranks"]',
        f"missing-rejection:{MTI}",
    ),
    (
        "move-length-allows-6",
        "    if type(move) is not str or not move.isascii() or len(move) not in (4, 5):",
        "    if type(move) is not str or not move.isascii() or len(move) not in (4, 5, 6):",
        f"missing-rejection:{MTI}",
    ),
    (
        "merge-stages-in-place",
        "        staged = copy.deepcopy(self._records)",
        "        staged = self._records",
        "rollback",
    ),
    (
        "game-id-allows-space",
        "all(0x21 <= ord(char) <= 0x7E for char in value)",
        "all(0x20 <= ord(char) <= 0x7E for char in value)",
        f"missing-rejection:{MPR}",
    ),
    (
        "timestamp-calendar-check-dropped",
        '        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")',
        "        pass",
        f"missing-rejection:{MPR}",
    ),
    (
        "source-id-exact-str-dropped",
        '        if type(entry["source_id"]) is not str:',
        "        if False:",
        f"missing-rejection:{MPR}",
    ),
    (
        "kind-exact-str-dropped",
        "    if type(kind) is not str or kind not in _TARGET_KINDS:",
        "    if kind not in _TARGET_KINDS:",
        f"missing-rejection:{UTK}",
    ),
    (
        "union-never-grows",
        '        if sources != existing["sources"]:',
        "        if False:",
        "accept-mismatch",
    ),
    (
        "exact-dict-drops-key-check",
        "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "    return type(obj) is dict",
        "missing-rejection:",
    ),
    (
        "move-from-to-distinct-dropped",
        '    if shape["from_to_distinct"] and start == end:\n        return False\n',
        "",
        f"missing-rejection:{MTI}:bad-move:",
    ),
    (
        "move-promotion-isalpha",
        '    return len(move) == 4 or move[4] in shape["types"]["promotion"]["enum"]',
        "    return len(move) == 4 or move[4].isalpha()",
        f"missing-rejection:{MTI}:bad-move:",
    ),
    (
        "move-isinstance",
        "    if type(move) is not str or not move.isascii() or len(move) not in (4, 5):",
        "    if not isinstance(move, str) or not move.isascii() or len(move) not in (4, 5):",
        f"missing-rejection:{MTI}:bad-move:context:4",
    ),
    (
        "timestamp-isinstance",
        "    if type(value) is not str or not value.isascii() or _TIMESTAMP",
        "    if not isinstance(value, str) or not value.isascii() or _TIMESTAMP",
        f"missing-rejection:{MPR}:subclass-value:first_observed_at",
    ),
    (
        "game-id-isinstance",
        "    return type(value) is str and bool(value)",
        "    return isinstance(value, str) and bool(value)",
        f"missing-rejection:{MPR}:subclass-value:game_id",
    ),
    (
        "merge-batch-isinstance",
        "        if type(incoming) is not list:",
        "        if not isinstance(incoming, list):",
        f"missing-rejection:{MPR}:src:records-list-subclass",
    ),
    (
        "source-entry-shape-by-length",
        "        if not _exact_dict(entry) or set(entry) != _SOURCE_FIELDS:",
        "        if not _exact_dict(entry) or len(entry) != len(_SOURCE_FIELDS):",
        f"missing-rejection:{MPR}:renamed-key:",
    ),
    (
        "node-target-shape-by-length",
        '    if not _exact_dict(target) or set(target) != {"variant", "snapshot_fen"}:',
        "    if not _exact_dict(target) or len(target) != 2:",
        f"missing-rejection:{MTI}:renamed-key:transposition_node:target:",
    ),
    (
        "edge-target-shape-by-length",
        "    if not _exact_dict(target) or set(target) != fields:",
        "    if not _exact_dict(target) or len(target) != len(fields):",
        f"missing-rejection:{MTI}:renamed-key:route_edge:target:",
    ),
    (
        "context-target-shape-by-length",
        '    if not _exact_dict(target) or set(target) != {"variant", "path_moves"}:',
        "    if not _exact_dict(target) or len(target) != 2:",
        f"missing-rejection:{MTI}:renamed-key:opening_context:target:",
    ),
    (
        "record-shape-by-length",
        "    if not _exact_dict(record) or set(record) != _RECORD_FIELDS:",
        "    if not _exact_dict(record) or len(record) != len(_RECORD_FIELDS):",
        f"missing-rejection:{MPR}:renamed-key:",
    ),
    (
        "edge-to-snapshot-canonical-dropped",
        '                or after["snapshot_fen"] != target["to_snapshot_fen"]):',
        "                or False):",
        f"missing-rejection:{MTI}:noncanonical:to_snapshot_fen:",
    ),
    (
        "node-except-drops-valueerror",
        '        return "transposition_node", target["variant"], identity\n'
        "    except (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):",
        '        return "transposition_node", target["variant"], identity\n'
        "    except (VariantError, DigestError, TypeError, KeyError, IndexError):",
        f"missing-rejection:{MTI}:linked-raises:ValueError:transposition_node",
    ),
    (
        "source-entry-stored-by-reference",
        "        unique[key] = {field: entry[field] for field in "
        '_PROVENANCE["source_entry"]["fields"]}',
        "        unique[key] = entry",
        "aliased",
    ),
    (
        "target-stored-by-reference",
        '        "target": copy.deepcopy(record["target"]),',
        '        "target": record["target"],',
        "aliased",
    ),
    (
        "insert-new-returns-stored",
        "            return copy.deepcopy(canonical)",
        "            return canonical",
        "aliased",
    ),
    (
        "insert-existing-returns-stored",
        "        return copy.deepcopy(self._records[key])",
        "        return self._records[key]",
        "aliased",
    ),
    (
        "merge-source-failure-chained",
        "        except BaseException:\n            failed = True",
        "        except BaseException as exc:\n"
        '            raise ProvenanceError("malformed_provenance_record") from exc',
        f"missing-rejection:{MPR}:src:records-raises",
    ),
    (
        "merge-source-except-narrowed",
        "        except BaseException:",
        "        except Exception:",
        f"missing-rejection:{MPR}:src:records-raises-baseexception",
    ),
    (
        "serialize-unsorted",
        "        return sorted(out)",
        "        return out",
        "serialize-unsorted",
    ),
]


def _mutant(old, new):
    src = Path(pv.__file__).read_text()
    assert src.count(old) == 1, old
    mod = types.ModuleType("provenance_mutant")
    mod.__file__ = pv.__file__
    exec(compile(src.replace(old, new), pv.__file__, "exec"), mod.__dict__)  # noqa: S102
    return mod


@pytest.mark.parametrize("name,old,new,own", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_injected_fault_detected_by_its_own_check(name, old, new, own):
    _, problems = _campaign(_mutant(old, new), cases=MUTANT_CASES)
    assert problems, name
    assert any(p.startswith(own) for _i, p in problems), (name, problems[:3])
