"""T0189 Graph/conflict/unit-property: unit rows and seeded properties
over the production three-way conflict detector (graph.conflict, T0188).

An independent model of data/contracts/conflict.yaml is restated here:
per side, identity-keyed changes against the base (added, removed,
changed - a digest-only difference under one identity is a change);
every key both sides touched is a witness unless both removed it or both
landed on the same record; changed-vs-removed carries None on the
removing side; the state id is sha256 over the sorted identity ->
sorted-field serialization. States are built from linked node records
(graph.node.make_record, keyed by graph.node.record_identity).

Properties (seeded, deterministic, every sub-case enumerated where it
decides coverage): model equality; left-right symmetry; determinism and
insertion-order independence; inputs never mutated, accepted or
rejected; results detached; witnesses in canonical order; identical
sides compatible; the base id equal to a side id is divergent_base.
Unit rows: boundaries (empty states, sixteen identities, every witness
kind), malformed entries at base, left and right, and hostile types at
every input boundary - each state mapping, its keys in 3 forms, each
record mapping, record keys in 3 forms and each record string value -
with the typed class, an empty hostile-call log and inputs unchanged.
Mutants: one-edit production mutants, each red on its own check.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import types
from pathlib import Path

import pytest

from graph import conflict as cf
from graph.node import make_record, record_identity

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "graph" / "conflict.py").read_text()
MCR, DB = "malformed_conflict_record", "divergent_base"
MAPPING = cf.FAILURE_MAPPING
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
)
BASE_RECS = tuple(make_record("standard", f) for f in FENS)
KEYS = tuple(record_identity(r) for r in BASE_RECS)
ALT_DIGESTS = ("pdv1:" + "1" * 64, "pdv1:" + "2" * 64)


def _version(i, v):
    """Record i in version v: 0 is the linked record, 1..2 differ by digest only."""
    rec = dict(BASE_RECS[i])
    if v:
        rec["digest"] = ALT_DIGESTS[v - 1]
    return rec


def _state(spec):
    """spec: {index: version}"""
    return {KEYS[i]: _version(i, v) for i, v in spec.items()}


# -- independent model ----------------------------------------------------------------


def _model_id(state):
    parts = []
    for key in sorted(state):
        rec = state[key]
        parts.append(key + "\n" + "|".join(f"{f}={rec[f]}" for f in sorted(rec)) + "\n")
    return "gs1:" + hashlib.sha256("".join(parts).encode()).hexdigest()


def _model_changes(base, side):
    out = {}
    for key in set(base) | set(side):
        if key not in base:
            out[key] = ("added", side[key])
        elif key not in side:
            out[key] = ("removed", None)
        elif base[key] != side[key]:
            out[key] = ("changed", side[key])
    return out


def _model(base, left, right):
    ids = [_model_id(s) for s in (base, left, right)]
    if ids[0] in ids[1:]:
        return {"err": DB}
    lch, rch = _model_changes(base, left), _model_changes(base, right)
    conflicts = {}
    for key in sorted(set(lch) & set(rch)):
        (lk, lr), (rk, rr) = lch[key], rch[key]
        if lk == rk and lr == rr:
            continue
        if lk == rk:
            kind = "added_differently" if lk == "added" else "both_changed_differently"
        else:
            kind = "changed_vs_removed"
        conflicts[key] = {"kind": kind, "left": lr, "right": rr}
    return {"base_id": ids[0], "left_id": ids[1], "right_id": ids[2], "conflicts": conflicts}


def _detect(module, base, left, right):
    try:
        return module.ConflictDetector().detect(base, left, right)
    except BaseException as exc:  # noqa: BLE001 - a raw escape is the defect
        if type(exc).__name__ == "ConflictError":
            if exc.__cause__ is not None or exc.__context__ is not None:
                return {"chained": exc.failure_class}
            if exc.code != MAPPING.get(exc.failure_class):
                return {"wrong-code": exc.failure_class}
            return {"err": exc.failure_class}
        return {"raw": type(exc).__name__}


def _triples(seed=0, n=300):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        specs = []
        for _side in range(3):
            specs.append(
                {i: rng.choice((0, 0, 1, 2)) for i in range(len(KEYS)) if rng.random() < 0.6}
            )
        out.append(tuple(specs))
    return out


def _enumerated():
    """Every (base, left, right) version triple of one identity, where a
    version is absent (None) or 0..2: all 27 witness shapes."""
    out = []
    opts = (None, 0, 1)
    for b in opts:
        for lv in opts:
            for r in opts:
                spec = [{} if v is None else {0: v} for v in (b, lv, r)]
                for s in spec:
                    s[1] = 0  # a shared untouched identity
                out.append(tuple(spec))
    return out


# -- hostile types --------------------------------------------------------------------

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
    if isinstance(value, dict):
        pairs = sorted((_inert(k), _inert(v)) for k, v in dict.items(value))
        return f"{type(value).__name__}{{{pairs}}}"
    return f"{type(value).__name__}:{json.dumps(value)}"


FIELDS = ("variant", "digest", "snapshot_fen")
SIDES = ("base", "left", "right")


def _hostile_triple(spec, where):
    """(base, left, right) with one hostile type in the WHERE state; the
    clean triple is a valid one-conflict triple."""
    states = [_state({0: 0, 1: 0}), _state({0: 1, 1: 0}), _state({0: 2, 1: 0})]
    p = SIDES.index(where)
    s = states[p]
    kind, _, form = spec.partition(":")
    field, _, f = form.partition("@")
    key = KEYS[0]
    if kind == "state-dict-subclass":
        s = _D(s)
    elif kind == "state-lying-dict":
        s = _LyingDict(s)
    elif kind == "state-key":
        s = {(_hstr(form, k, KEYS[1]) if k == key else k): v for k, v in s.items()}
    elif kind == "record-dict-subclass":
        s = {**s, key: _D(s[key])}
    elif kind == "record-lying-dict":
        s = {**s, key: _LyingDict(s[key])}
    elif kind == "record-key":
        s = {**s, key: {(_hstr(f, k, "digest") if k == field else k): v for k, v in s[key].items()}}
    elif kind == "record-value":
        s = {**s, key: {**s[key], field: _hstr(f, s[key][field], s[key][field])}}
    else:
        raise AssertionError(spec)
    states[p] = s
    return states


def _hostile_specs():
    specs = ["state-dict-subclass", "state-lying-dict", "record-dict-subclass", "record-lying-dict"]
    for form in KEY_FORMS:
        specs.append(f"state-key:{form}")
        for field in FIELDS:
            specs.append(f"record-key:{field}@{form}")
            specs.append(f"record-value:{field}@{form}")
    return specs


def _hostile_problems(module):
    bad = []
    for spec in _hostile_specs():
        for where in SIDES:
            states = _hostile_triple(spec, where)
            before = [_inert(s) for s in states]
            HOSTILE.clear()  # construction may hash/compare; only detect counts
            got = _detect(module, *states)
            calls = list(HOSTILE)
            if got != {"err": MCR} or calls or [_inert(s) for s in states] != before:
                bad.append((f"{spec}@{where}", got if "err" not in got else got, calls))
    return bad


def _one_upper(digest):
    head, hexpart = digest.split(":", 1)
    i = next((i for i, c in enumerate(hexpart) if c in "abcdef"), None)
    if i is None:  # an all-digit digest: one upper-case hex letter at the end
        return f"{head}:{hexpart[:-1]}A"
    return f"{head}:{hexpart[:i]}{hexpart[i].upper()}{hexpart[i + 1 :]}"


def _malformed_problems(module):
    """Every malformed row in every position through MODULE; [] when all hold."""
    bad = []
    for where in SIDES:
        for name, mutate, cls in _malformed_rows():
            states = [_state({0: 0, 1: 0}), _state({0: 1, 1: 0}), _state({0: 2, 1: 0})]
            p = SIDES.index(where)
            states[p] = mutate(states[p])
            frozen = copy.deepcopy(states)
            try:
                got = _detect(module, *states)
            except BaseException as error:  # noqa: BLE001 - a raw escape is the defect
                got = ("raw", type(error).__name__)
            if got != {"err": cls}:
                bad.append((where, name, got))
            elif states != frozen:
                bad.append((where, name, "mutated"))
    return bad


def _malformed_rows():
    """(name, mutate(state) -> state, class) applied in each position."""
    k0 = KEYS[0]
    r = _version(0, 1)

    def put(rec):
        return lambda s: {**s, k0: rec}

    return [
        ("state-list", lambda s: list(s.items()), MCR),
        ("state-none", lambda s: None, MCR),
        ("key-bytes", lambda s: {**{k: v for k, v in s.items() if k != k0}, k0.encode(): r}, MCR),
        (
            "key-other-identity",
            lambda s: {**{k: v for k, v in s.items() if k != k0}, KEYS[2]: r},
            MCR,
        ),
        ("record-list", put(list(r.items())), MCR),
        ("record-missing", put({k: v for k, v in r.items() if k != "digest"}), MCR),
        ("record-extra", put({**r, "note": "x"}), MCR),
        (
            "record-renamed",
            put({("varient" if k == "variant" else k): v for k, v in r.items()}),
            MCR,
        ),
        ("digest-trailing-newline", put({**r, "digest": r["digest"] + "\n"}), MCR),
        ("digest-leading-space", put({**r, "digest": " " + r["digest"]}), MCR),
        ("digest-trailing-space", put({**r, "digest": r["digest"] + " "}), MCR),
        ("digest-one-upper-hex", put({**r, "digest": _one_upper(r["digest"])}), MCR),
        ("variant-none", put({**r, "variant": None}), MCR),
        ("digest-int", put({**r, "digest": 1}), MCR),
        ("digest-format", put({**r, "digest": "pdv1:" + "G" * 64}), MCR),
        ("digest-uppercase", put({**r, "digest": r["digest"].upper()}), MCR),
        ("snapshot-clocks", put({**r, "snapshot_fen": FENS[0].replace(" 0 1", " 4 9")}), MCR),
        ("snapshot-bad", put({**r, "snapshot_fen": "x"}), MCR),
        ("variant-unknown", put({**r, "variant": "atomic"}), MCR),
        ("snapshot-huge-clock", put({**r, "snapshot_fen": FENS[0][:-1] + "9" * 5000}), MCR),
    ]


# -- tests: properties ----------------------------------------------------------------


def _problems(module, triples):
    bad = []
    for specs in triples:
        states = [_state(s) for s in specs]
        frozen = copy.deepcopy(states)
        got = _detect(module, *states)
        if got != _model(*frozen):
            bad.append(("model", specs, got))
            continue
        if states != frozen:
            bad.append(("mutated", specs))
        if "err" in got:
            continue
        swapped = _detect(module, frozen[0], frozen[2], frozen[1])
        want = {
            "base_id": got["base_id"],
            "left_id": got["right_id"],
            "right_id": got["left_id"],
            "conflicts": {
                k: {"kind": w["kind"], "left": w["right"], "right": w["left"]}
                for k, w in got["conflicts"].items()
            },
        }
        if swapped != want:
            bad.append(("symmetry", specs))
        if list(got["conflicts"]) != sorted(got["conflicts"]):
            bad.append(("order", specs))
        reordered = [dict(reversed(list(s.items()))) for s in frozen]
        if _detect(module, *reordered) != got:
            bad.append(("insertion-order", specs))
    return bad


def test_model_matches_on_seeded_and_enumerated_triples():
    triples = _triples() + _enumerated()
    assert _problems(cf, triples) == []
    kinds = set()
    for specs in triples:
        got = _model(*[_state(s) for s in specs])
        kinds.update(w["kind"] for w in got.get("conflicts", {}).values())
        if "err" in got:
            kinds.add(DB)
    assert kinds == {"added_differently", "both_changed_differently", "changed_vs_removed", DB}


def test_determinism_and_state_id_is_the_model_derivation():
    for specs in _triples(seed=3, n=60):
        s = _state(specs[0])
        assert cf.state_id(s) == _model_id(s) == cf.state_id(copy.deepcopy(s))
    a = [_detect(cf, *[_state(s) for s in t]) for t in _triples(seed=5, n=40)]
    b = [_detect(cf, *[_state(s) for s in t]) for t in _triples(seed=5, n=40)]
    assert a == b


def test_results_are_detached_from_inputs_and_from_each_other():
    states = [_state({0: 0}), _state({0: 1}), _state({0: 2})]
    first = cf.ConflictDetector().detect(*states)
    first["conflicts"][KEYS[0]]["left"]["digest"] = "poison"
    assert states[1][KEYS[0]]["digest"] == ALT_DIGESTS[0]
    assert cf.ConflictDetector().detect(*states) == _model(*states)


# -- tests: unit boundaries -----------------------------------------------------------


def test_boundaries():
    empty = {}
    full = _state({i: 0 for i in range(len(KEYS))})
    rows = [
        (empty, _state({0: 0}), _state({0: 1})),  # added differently from empty
        (empty, _state({0: 0}), _state({0: 0})),  # both added identically
        (full, empty, empty),  # both removed everything: identical outcome
        (full, empty, _state({i: 1 for i in range(len(KEYS))})),  # changed vs removed, all
        (full, _state({0: 1, 1: 0}), _state({2: 0, 3: 0})),  # disjoint edits
        (empty, empty, empty),  # divergent base
        (full, full, _state({0: 1})),  # divergent base, one side unchanged
    ]
    for base, left, right in rows:
        assert _detect(cf, base, left, right) == _model(base, left, right)


def test_sixteen_identities_witnesses_in_canonical_order():
    fens = [
        f"{k}/8/8/8/8/8/8/K7 w - - 0 1"
        for k in ("k7", "1k6", "2k5", "3k4", "4k3", "5k2", "6k1", "7k")
    ]
    fens += [f.replace(" w ", " b ") for f in fens]
    recs = [make_record("standard", f) for f in fens]
    base = {record_identity(r): r for r in recs}
    left = {k: {**r, "digest": ALT_DIGESTS[0]} for k, r in base.items()}
    right = {k: {**r, "digest": ALT_DIGESTS[1]} for k, r in base.items()}
    got = cf.ConflictDetector().detect(base, left, right)
    assert got == _model(base, left, right)
    assert list(got["conflicts"]) == sorted(base) and len(base) == 16


# -- tests: malformed and hostile -----------------------------------------------------


@pytest.mark.parametrize("where", SIDES)
def test_malformed_entries_are_typed_in_every_position(where):
    for name, mutate, cls in _malformed_rows():
        states = [_state({0: 0, 1: 0}), _state({0: 1, 1: 0}), _state({0: 2, 1: 0})]
        p = SIDES.index(where)
        states[p] = mutate(states[p])
        frozen = copy.deepcopy(states)
        assert _detect(cf, *states) == {"err": cls}, name
        assert states == frozen, name


def test_malformed_rows_hold_through_the_checker():
    assert _malformed_problems(cf) == []


def test_first_invalid_state_in_base_left_right_order_decides():
    ok = _state({0: 0})
    bad = {KEYS[0]: {**_version(0, 0), "digest": 1}}
    assert _detect(cf, bad, ok, _state({0: 1})) == {"err": MCR}
    assert _detect(cf, ok, ok, bad) == {"err": MCR}  # malformed beats divergent_base


def test_hostile_types_at_every_input_boundary_are_typed_and_inert():
    assert _hostile_problems(cf) == []
    assert len(_hostile_specs()) * len(SIDES) == 75


# -- mutants: each is red on its own check --------------------------------------------


MUTANTS = {
    "record-field-set-by-length": (
        [
            (
                "    if len(rec_keys) != len(_RECORD_FIELDS) or \\\n"
                "            set(rec_keys) != _RECORD_FIELDS:",
                "    if len(rec_keys) != len(_RECORD_FIELDS):",
            )
        ],
        _malformed_problems,
    ),
    "digest-search": (
        [
            (
                "    if _DIGEST_RE.fullmatch(digest) is None:",
                "    if _DIGEST_RE.search(digest) is None:",
            )
        ],
        _malformed_problems,
    ),
    "digest-ignored-in-changes": (
        [
            (
                "        elif base[key] != rec:",
                '        elif base[key]["snapshot_fen"] != rec["snapshot_fen"]:',
            )
        ],
        lambda m: _problems(m, _enumerated()),
    ),
    # dropping the both-removed skip alone is equivalent (both removed carry the same
    # base record, so the same-kind branch skips them too); the identical-outcome
    # compare is the live check
    "identical-outcome-reported": (
        [("                if lrec != rrec:", "                if True:")],
        lambda m: _problems(m, _enumerated()),
    ),
    "divergent-base-dropped": (
        [('            _fail("divergent_base")', "            pass")],
        lambda m: _problems(m, _enumerated()),
    ),
    "removed-side-carries-record": (
        [
            (
                '                "right": dict(rrec) if rkind == "changed" else None}',
                '                "right": dict(rrec) if rrec else None}',
            )
        ],
        lambda m: _problems(m, _enumerated()),
    ),
    "state-dict-isinstance": (
        [("    if type(state) is not dict:", "    if not isinstance(state, dict):")],
        _hostile_problems,
    ),
    "record-dict-isinstance": (
        [
            (
                "    if type(key) is not str or type(rec) is not dict:",
                "    if type(key) is not str or not isinstance(rec, dict):",
            )
        ],
        _hostile_problems,
    ),
    "state-key-type-dropped": (
        [
            (
                "    if type(key) is not str or type(rec) is not dict:",
                "    if type(rec) is not dict:",
            )
        ],
        _hostile_problems,
    ),
    "record-key-types-dropped": (
        [("    if not all(type(k) is str for k in rec_keys):\n        _fail(_MCR)\n", "")],
        _hostile_problems,
    ),
    "record-value-types-dropped": (
        [
            (
                "    if type(variant) is not str or type(digest) is not str or \\\n"
                "            type(snapshot) is not str:\n        _fail(_MCR)\n",
                "",
            )
        ],
        _hostile_problems,
    ),
}


def _mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"graph._t0189_mutant_{name}")
    module.__file__ = str(ROOT / "graph" / "conflict.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    return module


def test_identity_mutant_is_green():
    module = _mutant("identity", [])
    assert _problems(module, _enumerated() + _triples(n=60)) == []
    assert _hostile_problems(module) == []
    assert _malformed_problems(module) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_own_check(name):
    edits, check = MUTANTS[name]
    assert check(_mutant(name, edits)), name
