"""T0199 Store/migration/fuzz-fault: deterministic fuzz/fault battery for the
production store migration (store/migration.py, T0197).

Seeded generators build store-v1 source states and migration requests from
an independent model of data/contracts/migration.yaml (state-id, pdv2
re-digest and migration-id derivations are restated here, never taken from
production) and then damage them:

- request: one edit to the request (every leaf type, str-subclass values,
  near-miss and unregistered schema names, no-op and downgrade pairs, bad
  source ids, dropped, added and same-arity renamed keys, str-subclass,
  eq-raising and hash-colliding keys, dict subclasses and a lying dict),
  over a clean or a corrupt source;
- container: non-dict, dict-subclass, lying-dict and list-subclass sources;
- source: one source tamper (key not the identity, record dict subclass,
  lying record, list-subclass record, missing, extra and renamed fields,
  str-subclass, eq-raising and colliding keys at both levels, non-str and
  str-subclass values, v2 digests in a v1 source, non-canonical FENs,
  unknown variants, a source id of another state);
- oracle: a target-oracle fault over an empty, a one-record and a random
  source (every BaseException kind, a forged typed error of every class
  store/migration.py raises or names in its own except clauses, non-str,
  str-subclass, v1-grammar, upper-case, trailing-newline and constant
  output, an honest answer, an honest answer that then scribbles on the
  caller's source and request);
- live: an oracle that edits the caller's live source and request and then
  answers honestly, raises or diverges.

Every case must satisfy, against production: no raw escape (only a typed
MigrationError from the closed class set with its mapped code, never a
forged object, never chained); the same outcome as the contract reference
engine (tests.test_t0194_migration_contract) and, where the outcome is
independently known, that outcome; request and source identical by value,
exact type, key order and object identity at every level on every exit; no
oracle call on a request or source rejection; on success exactly one
oracle call per record, in sorted key order, with exact str arguments, and
the model receipt; no poisoning; determinism.

Hostile types: every input boundary (request, source container, record)
gets list-subclass, dict-subclass and lying-dict rows, str-subclass keys in
3 forms (plain, eq-raises, hash-collides) and str-subclass values; each row
pins the typed class, an empty hostile-call log and unchanged input.

Mutation check: _probe runs a fixed slice of every generator against a
source mutant of store/migration.py. Every entry in MUTANTS must turn the
probe red; every entry in EQUIVALENT_EDITS must keep it green.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import functools
import hashlib
import random
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph import position_digest as _digest  # noqa: E402
from graph.node import make_record, record_identity  # noqa: E402
from store import migration  # noqa: E402
from tests import test_t0194_migration_contract as _reference  # noqa: E402
from tools import variant_runtime as _variant  # noqa: E402
from tools.migration_contract_lint import FAILURE_MAPPING  # noqa: E402

SOURCE_TEXT = (ROOT / "store" / "migration.py").read_text()
V1, V2 = "store-v1", "store-v2"
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w Kq - 0 1",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",
    "8/8/8/8/8/8/8/K1k5 w - - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
KEYS = tuple(record_identity(record) for record in RECORDS)
CLASSES = frozenset(FAILURE_MAPPING)
MMR, UM, CS, DT = (
    "malformed_migration_record",
    "unknown_migration",
    "conflicting_source",
    "divergent_target",
)
GENERATORS = ("request", "container", "source", "oracle", "live")
FUZZ_SEEDS = {"request": 240, "container": 24, "source": 240, "oracle": 0, "live": 96}
PROBE_SEEDS = 48  # per generator; the oracle slice covers every fault once
HOSTILE = []  # user dunder calls observed while armed


class _Str(str):
    pass


class _Dict(dict):
    pass


class _List(list):
    pass


class _Colliding:
    """Hashes like a real name; while armed (only around the engine call)
    comparing it raises, so any comparison before an exact-str key guard
    escapes raw."""

    armed = False

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __deepcopy__(self, memo):
        return self

    def __eq__(self, other):
        if _Colliding.armed:
            HOSTILE.append("colliding-eq")
            raise RuntimeError("hostile __eq__")
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)


class _EqRaises(str):
    """A str subclass whose comparison and hashing are logged while armed."""

    def __eq__(self, other):
        if _Colliding.armed:
            HOSTILE.append("eqraises-eq")
            raise RuntimeError("hostile __eq__")
        return str.__eq__(self, other)

    def __hash__(self):
        if _Colliding.armed:
            HOSTILE.append("eqraises-hash")
        return str.__hash__(self)

    def __deepcopy__(self, memo):
        return self


class _Lying(dict):
    """A dict subclass whose every view lies while armed."""

    def _log(self, name):
        if _Colliding.armed:
            HOSTILE.append(f"lying-{name}")

    def keys(self):
        self._log("keys")
        return dict.keys(self)

    def items(self):
        self._log("items")
        return dict.items(self)

    def values(self):
        self._log("values")
        return dict.values(self)

    def __iter__(self):
        self._log("iter")
        return dict.__iter__(self)

    def __getitem__(self, key):
        self._log("getitem")
        return dict.__getitem__(self, key)

    def __len__(self):
        self._log("len")
        return dict.__len__(self)

    def __deepcopy__(self, memo):
        return _Lying({k: copy.deepcopy(v, memo) for k, v in dict.items(self)})


class _LogList(list):
    def __iter__(self):
        if _Colliding.armed:
            HOSTILE.append("list-iter")
        return list.__iter__(self)

    def __len__(self):
        if _Colliding.armed:
            HOSTILE.append("list-len")
        return list.__len__(self)


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hkey(form, text):
    if form == "plain":
        return _Str(text)
    if form == "eq-raises":
        return _EqRaises(text)
    return _Colliding(text)


# -- the independent model --------------------------------------------------------


def _state_id(state):
    text = "".join(
        f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key])) + "\n"
        for key in sorted(state)
    )
    return "gs1:" + hashlib.sha256(text.encode()).hexdigest()


def _pdv2(variant, fen):
    return "pdv2:" + hashlib.sha256(f"{variant}\n{fen}".encode()).hexdigest()


def _mid(frm, to, source_id, target_id):
    return "mg1:" + hashlib.sha256(f"{frm}\n{to}\n{source_id}\n{target_id}".encode()).hexdigest()


def _source(rng, low=0, high=None):
    high = len(RECORDS) if high is None else high
    chosen = sorted(rng.sample(range(len(RECORDS)), rng.randint(low, high)))
    return {KEYS[i]: dict(RECORDS[i]) for i in chosen}


def _request(source, frm=V1, to=V2):
    return {"from_schema": frm, "to_schema": to, "source_id": _state_id(source)}


def _model_state(source):
    return {
        key: {
            "variant": r["variant"],
            "digest": _pdv2(r["variant"], r["snapshot_fen"]),
            "snapshot_fen": r["snapshot_fen"],
        }
        for key, r in source.items()
    }


def _model_receipt(source):
    staged = _model_state(source)
    sid, tid = _state_id(source), _state_id(staged)
    return {
        "migration_id": _mid(V1, V2, sid, tid),
        "from_schema": V1,
        "to_schema": V2,
        "source_id": sid,
        "target_id": tid,
        "state": staged,
    }


def _case_of(label, request, source, oracle=None, expect=None, model=None):
    return {
        "label": label,
        "request": request,
        "source": source,
        "oracle": oracle,
        "expect": expect,
        "model": model,
    }


# -- generators -------------------------------------------------------------------

LEAVES = (None, True, 1, 0.0, b"store-v1", [V1], {V1: 1}, (V1,), frozenset({V1}))
NEAR_SCHEMAS = ("store-v1 ", "Store-v1", "store-v", "store-v01", "store-v3", "store-v0", "")
PAIRS = ((V1, V1), (V2, V2), (V2, V1), (V1, "store-v3"), ("store-v3", V2))
REQ_FIELDS = ("from_schema", "to_schema", "source_id")


def _corrupt_source(rng):
    source = _source(rng, 1)
    key = sorted(source)[0]
    source[key] = {**source[key], "digest": _pdv2("standard", source[key]["snapshot_fen"])}
    return source


def _gen_request(seed):
    rng = random.Random(f"request-{seed}")
    corrupt = seed % 4 == 3
    source = _corrupt_source(rng) if corrupt else _source(rng)
    request = _request(source)
    tail = "/corrupt-source" if corrupt else ""
    kind = seed % 12
    field = REQ_FIELDS[(seed // 12) % 3]
    if kind == 0:
        leaf = LEAVES[(seed // 36) % len(LEAVES)]
        request[field] = copy.deepcopy(leaf)
        return _case_of(f"request:leaf:{field}{tail}", request, source, expect=("err", MMR))
    if kind == 1:
        request[field] = _Str(request[field])
        return _case_of(f"request:str-subclass:{field}{tail}", request, source, expect=("err", MMR))
    if kind == 2:
        near = NEAR_SCHEMAS[(seed // 12) % len(NEAR_SCHEMAS)]
        request[REQ_FIELDS[seed % 2]] = near
        return _case_of(f"request:near-schema{tail}", request, source, expect=("err", MMR))
    if kind == 3:
        frm, to = PAIRS[(seed // 12) % len(PAIRS)]
        request["from_schema"], request["to_schema"] = frm, to
        registered = {V1, V2}
        want = ("err", UM) if {frm, to} <= registered else ("err", MMR)
        return _case_of(f"request:pair:{frm}>{to}{tail}", request, source, expect=want)
    if kind == 4:
        sid = request["source_id"]
        bad = rng.choice(
            [
                sid.upper(),
                sid[:-1],
                sid + "0",
                "gs2:" + sid[4:],
                sid.replace("gs1:", ""),
                sid + "\n",
            ]
        )
        request["source_id"] = bad
        return _case_of(f"request:source-id-grammar{tail}", request, source, expect=("err", MMR))
    if kind == 5:
        del request[field]
        return _case_of(f"request:dropped:{field}{tail}", request, source, expect=("err", MMR))
    if kind == 6:
        request["extra"] = V1
        return _case_of(f"request:added{tail}", request, source, expect=("err", MMR))
    if kind == 7:
        value = request.pop(field)
        request[field + "_"] = value
        return _case_of(f"request:renamed:{field}{tail}", request, source, expect=("err", MMR))
    if kind == 8:
        form = KEY_FORMS[(seed // 12) % 3]
        value = request.pop(field)
        request[_hkey(form, field)] = value
        return _case_of(f"request:key-{form}:{field}{tail}", request, source, expect=("err", MMR))
    if kind == 9:
        wrapper = (_Dict, _Lying)[(seed // 12) % 2]
        return _case_of(
            f"request:{wrapper.__name__}{tail}", wrapper(request), source, expect=("err", MMR)
        )
    if kind == 10:
        other = _source(rng, 1)
        if _state_id(other) == _state_id(source):
            other = {}
            if not source:
                other = {KEYS[0]: dict(RECORDS[0])}
        request["source_id"] = _state_id(other)
        want = ("err", MMR) if corrupt else ("err", CS)
        return _case_of(f"request:other-source-id{tail}", request, source, expect=want)
    return _case_of(
        f"request:clean{tail}",
        request,
        source,
        expect=("err", MMR) if corrupt else ("ok",),
        model=None if corrupt else source,
    )


def _gen_container(seed):
    rng = random.Random(f"container-{seed}")
    source = _source(rng)
    request = _request(source)
    kind = seed % 6
    if kind == 0:
        bad = [None, "state", 1, list(source.items()), tuple(source), True][seed // 6 % 6]
        return _case_of("container:non-dict", request, copy.deepcopy(bad), expect=("err", MMR))
    if kind == 1:
        return _case_of("container:dict-subclass", request, _Dict(source), expect=("err", MMR))
    if kind == 2:
        return _case_of("container:lying-dict", request, _Lying(source), expect=("err", MMR))
    if kind == 3:
        return _case_of(
            "container:list-subclass", request, _LogList(source.items()), expect=("err", MMR)
        )
    if kind == 4:
        return _case_of("container:empty", _request({}), {}, expect=("ok",), model={})
    return _case_of("container:clean", request, source, expect=("ok",), model=source)


SOURCE_TAMPERS = (
    "key-not-identity",
    "key-swapped",
    "record-dict-subclass",
    "record-lying",
    "record-list-subclass",
    "field-missing",
    "field-extra",
    "field-renamed",
    "key-plain",
    "key-eq-raises",
    "key-hash-collides",
    "field-key-plain",
    "field-key-eq-raises",
    "field-key-hash-collides",
    "value-non-str",
    "value-str-subclass-variant",
    "value-str-subclass-digest",
    "value-str-subclass-snapshot_fen",
    "value-eq-raises-variant",
    "value-eq-raises-digest",
    "value-eq-raises-snapshot_fen",
    "digest-v2-in-v1",
    "digest-upper",
    "digest-trailing-newline",
    "digest-trailing-junk",
    "fen-non-canonical",
    "variant-unknown",
    "outer-key-non-str",
)


def _tamper(source, kind, rng):
    """One damaged copy of SOURCE (non-empty) and the expected class."""
    keys = sorted(source)
    key = rng.choice(keys)
    rec = source[key]
    out = dict(source)
    field = rng.choice(("variant", "digest", "snapshot_fen"))
    if kind == "key-not-identity":
        out[key + " "] = out.pop(key)
    elif kind == "key-swapped":
        if len(keys) < 2:
            out[KEYS[0] if key != KEYS[0] else KEYS[1]] = out.pop(key)
        else:
            a, b = keys[0], keys[1]
            out[a], out[b] = out[b], out[a]
    elif kind == "record-dict-subclass":
        out[key] = _Dict(rec)
    elif kind == "record-lying":
        out[key] = _Lying(rec)
    elif kind == "record-list-subclass":
        out[key] = _LogList(rec.items())
    elif kind == "field-missing":
        out[key] = {f: v for f, v in rec.items() if f != field}
    elif kind == "field-extra":
        out[key] = {**rec, "extra": "x"}
    elif kind == "field-renamed":
        out[key] = {(f + "_" if f == field else f): v for f, v in rec.items()}
    elif kind.startswith("key-"):
        out = {(_hkey(kind[4:], k) if k == key else k): v for k, v in out.items()}
    elif kind.startswith("field-key-"):
        out[key] = {(_hkey(kind[10:], f) if f == field else f): v for f, v in rec.items()}
    elif kind == "value-non-str":
        out[key] = {**rec, field: rng.choice([None, 1, b"x", [rec[field]], True])}
    elif kind.startswith("value-str-subclass-"):
        name = kind[len("value-str-subclass-") :]
        out[key] = {**rec, name: _Str(rec[name])}
    elif kind.startswith("value-eq-raises-"):
        name = kind[len("value-eq-raises-") :]
        out[key] = {**rec, name: _EqRaises(rec[name])}
    elif kind == "digest-v2-in-v1":
        out[key] = {**rec, "digest": _pdv2(rec["variant"], rec["snapshot_fen"])}
    elif kind == "digest-upper":
        out[key] = {**rec, "digest": rec["digest"].upper()}
    elif kind == "digest-trailing-newline":
        out[key] = {**rec, "digest": rec["digest"] + "\n"}
    elif kind == "digest-trailing-junk":
        out[key] = {**rec, "digest": rec["digest"] + "x"}
    elif kind == "fen-non-canonical":
        out[key] = {**rec, "snapshot_fen": rec["snapshot_fen"].replace(" 0 1", " 3 9")}
    elif kind == "variant-unknown":
        out[key] = {**rec, "variant": rng.choice(["chess960x", "Standard", ""])}
    elif kind == "outer-key-non-str":
        out[rng.choice([1, None, (key,), b"k"])] = out.pop(key)
    return out


def _gen_source(seed):
    rng = random.Random(f"source-{seed}")
    kind = SOURCE_TAMPERS[seed % len(SOURCE_TAMPERS)]
    source = _source(rng, 1)
    request = _request(source)  # the id of the clean source
    bad = _tamper(source, kind, rng)
    if kind.startswith("digest-trailing-") or (seed % 3 == 2 and kind != "key-hash-collides"):
        with contextlib.suppress(BaseException):  # some damage is not serializable
            request = _request(bad)  # re-derived for the damaged source
    return _case_of(f"source:{kind}", request, bad, expect=("err", MMR))


def _raise(kind):
    def oracle(variant, fen):
        raise kind()

    return oracle


def _forged(error):
    def oracle(variant, fen):
        raise error

    return oracle


_DIGEST_CLASSES = yaml.safe_load(_digest.CONTRACT.read_text())["contract"]["failures"]["mapping"]
FORGED = {
    **{
        f"forged-migration-{c}": migration.MigrationError(c, FAILURE_MAPPING[c])
        for c in sorted(FAILURE_MAPPING)
    },
    **{
        f"forged-digest-{c}": _digest.DigestError(c, _DIGEST_CLASSES[c])
        for c in sorted(_DIGEST_CLASSES)
    },
    **{
        f"forged-variant-{c}": _variant.VariantError(
            "illegal_position" if c == "illegal_position" else "malformed_request", c, c
        )
        for c in sorted(_variant.FAILURE_CLASSES)
    },
    "forged-builtin-ValueError": ValueError("forged"),
    "forged-builtin-TypeError": TypeError("forged"),
}
_KINDS = {
    f"raise-{k.__name__}": k
    for k in (
        ValueError,
        KeyError,
        RuntimeError,
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
        MemoryError,
        RecursionError,
    )
}
_V1_OUT = RECORDS[0]["digest"]


def _scribble(source, request):
    def oracle(variant, fen):
        for rec in list(dict.values(source)):
            if type(rec) is dict:
                rec["digest"] = "scribbled"
        source["scribbled"] = {}
        request["source_id"] = "gs1:" + "f" * 64
        return _pdv2(variant, fen)

    return oracle


OUTPUTS = {
    "out-none": lambda v, f: None,
    "out-bytes": lambda v, f: _pdv2(v, f).encode(),
    "out-str-subclass": lambda v, f: _Str(_pdv2(v, f)),
    "out-v1-grammar": lambda v, f: _V1_OUT,
    "out-upper": lambda v, f: _pdv2(v, f).upper(),
    "out-trailing-newline": lambda v, f: _pdv2(v, f) + "\n",
    "out-short": lambda v, f: _pdv2(v, f)[:-1],
    "out-int": lambda v, f: 7,
    "out-constant": lambda v, f: "pdv2:" + "0" * 64,
    "out-honest": lambda v, f: _pdv2(v, f),
}
ORACLE_FAULTS = (*_KINDS, *sorted(FORGED), *OUTPUTS, "scribble")
SPOTS = ("empty", "one", "random")


def _oracle_for(fault):
    if fault in _KINDS:
        return lambda source, request: _raise(_KINDS[fault])
    if fault in FORGED:
        return lambda source, request: _forged(FORGED[fault])
    if fault == "scribble":
        return _scribble
    return lambda source, request: OUTPUTS[fault]


def _gen_oracle(seed):
    fault = ORACLE_FAULTS[seed // len(SPOTS)]
    spot = SPOTS[seed % len(SPOTS)]
    rng = random.Random(f"oracle-{seed}")
    source = {} if spot == "empty" else _source(rng, 1, 1 if spot == "one" else len(RECORDS))
    if spot == "empty" or fault in ("out-honest", "out-constant", "scribble"):
        # empty: the oracle is never called; constant pdv2 output is a valid
        # digest (identity is never the digest)
        model = source
        if fault == "out-constant" and source:
            model = None
        expect = ("ok",)
    else:
        model, expect = None, ("err", DT)
    return _case_of(
        f"oracle:{fault}@{spot}",
        _request(source),
        source,
        oracle=_oracle_for(fault),
        expect=expect,
        model=model,
    )


FUZZ_SEEDS["oracle"] = len(ORACLE_FAULTS) * len(SPOTS)
SCRAMBLES = (
    "source-add",
    "source-delete",
    "source-clear",
    "record-edit",
    "record-clear",
    "request-edit",
    "request-clear",
    "request-add",
)
ACTIONS = ("honest", "raise", "divergent")


def _scramble(kind, source, request):
    if kind == "source-add":
        source["extra"] = {"variant": "standard"}
    elif kind == "source-delete":
        for key in list(dict.keys(source))[:1]:
            del source[key]
    elif kind == "source-clear":
        source.clear()
    elif kind == "record-edit":
        for rec in list(dict.values(source))[:1]:
            rec["snapshot_fen"] = "8/8/8/8/8/8/8/8 w - - 0 1"
    elif kind == "record-clear":
        for rec in list(dict.values(source)):
            rec.clear()
    elif kind == "request-edit":
        request["to_schema"] = V1
    elif kind == "request-clear":
        request.clear()
    else:
        request["extra"] = "x"


def _gen_live(seed):
    kind = SCRAMBLES[seed % len(SCRAMBLES)]
    action = ACTIONS[(seed // len(SCRAMBLES)) % len(ACTIONS)]
    rng = random.Random(f"live-{seed}")
    source = _source(rng, 1)

    def build(src, req, kind=kind, action=action):
        done = []

        def oracle(variant, fen):
            if not done:
                done.append(True)
                _scramble(kind, src, req)
            if action == "raise":
                raise RuntimeError("after scramble")
            if action == "divergent":
                return _V1_OUT
            return _pdv2(variant, fen)

        return oracle

    ok = action == "honest"
    return _case_of(
        f"live:{kind}:{action}",
        _request(source),
        source,
        oracle=build,
        expect=("ok",) if ok else ("err", DT),
        model=source if ok else None,
    )


GEN = {
    "request": _gen_request,
    "container": _gen_container,
    "source": _gen_source,
    "oracle": _gen_oracle,
    "live": _gen_live,
}
PROBE = {g: min(PROBE_SEEDS, FUZZ_SEEDS[g]) if g != "oracle" else FUZZ_SEEDS[g] for g in GENERATORS}


@functools.cache
def _case(gen, seed):
    return GEN[gen](seed)


# -- running and checking ---------------------------------------------------------


def _deep(value):
    """Value, exact type, key order and object identity at every level."""
    if isinstance(value, dict):
        return (
            "d",
            type(value).__name__,
            id(value),
            tuple(
                (type(k).__name__, id(k) if type(k) is not str else k, _deep(v))
                for k, v in dict.items(value)
            ),
        )
    if isinstance(value, list):
        return ("l", type(value).__name__, id(value), tuple(_deep(v) for v in list.__iter__(value)))
    if isinstance(value, tuple):
        return ("t", id(value), tuple(_deep(v) for v in value))
    if isinstance(value, str) and type(value) is not str:
        return ("s", type(value).__name__, id(value))
    return ("v", type(value).__name__, value if type(value) is not frozenset else id(value))


def _run(module, case):
    """(outcome, before, after, source, request, calls, raised, hostile)."""
    source = copy.deepcopy(case["source"])
    request = copy.deepcopy(case["request"])
    inner = (
        (lambda v, f: _pdv2(v, f)) if case["oracle"] is None else case["oracle"](source, request)
    )
    calls = []

    def oracle(variant, fen):
        calls.append((variant, fen))
        return inner(variant, fen)

    engine = module.MigrationEngine(oracle)
    before = (_deep(request), _deep(source))
    raised = None
    HOSTILE.clear()
    _Colliding.armed = True
    try:
        outcome = ("ok", engine.migrate(request, source))
    except (migration.MigrationError, _reference.MigrationError) as error:
        outcome = ("err", error.failure_class, error.code)
        raised = error
    except BaseException as error:  # noqa: BLE001 - raw escape is a defect
        outcome = ("raw", type(error).__name__)
    finally:
        _Colliding.armed = False
    hostile = list(HOSTILE)
    after = (_deep(request), _deep(source))
    return outcome, before, after, source, request, calls, raised, hostile


@functools.cache
def _reference_outcome(gen, seed):
    return _run(_reference, _case(gen, seed))[0]


def _check(module, gen, seed):
    """Failing reasons for one case through MODULE (empty when it holds)."""
    case = _case(gen, seed)
    outcome, before, after, source, request, calls, raised, hostile = _run(module, case)
    if outcome[0] == "raw":
        return [f"raw {outcome[1]}"]
    why = []
    if outcome[0] == "err" and (
        outcome[1] not in CLASSES or outcome[2] != FAILURE_MAPPING.get(outcome[1])
    ):
        why.append(f"untyped {outcome[1:]}")
    if any(raised is forged for forged in FORGED.values()):
        why.append("forged error object passed through")
    if raised is not None and (raised.__cause__ is not None or raised.__suppress_context__):
        why.append("typed error chained")
    if outcome[:2] != _reference_outcome(gen, seed)[:2]:
        why.append(f"reference disagrees {outcome[:2]!r}"[:160])
    expect = case["expect"]
    if expect is not None and (
        expect[0] != outcome[0] or (expect[0] == "err" and outcome[1] != expect[1])
    ):
        why.append(f"expected {expect}, got {outcome[:2]}")
    if hostile:
        why.append(f"hostile calls {sorted(set(hostile))}")
    if after != before:
        why.append("inputs not restored")
    if outcome[0] == "err" and outcome[1] != DT and calls:
        why.append("oracle called before a request or source rejection")
    if outcome[0] == "ok":
        model_source = case["model"] if case["model"] is not None else case["source"]
        if case["model"] is not None and outcome[1] != _model_receipt(model_source):
            why.append("receipt differs from the model")
        if list(outcome[1]) != [
            "migration_id",
            "from_schema",
            "to_schema",
            "source_id",
            "target_id",
            "state",
        ]:
            why.append("receipt fields out of order")
        want_calls = [
            (model_source[k]["variant"], model_source[k]["snapshot_fen"])
            for k in sorted(model_source)
        ]
        if calls != want_calls or any(type(v) is not str or type(f) is not str for v, f in calls):
            why.append("oracle not called once per record in key order")
        state = outcome[1]["state"]
        if any(rec is x for rec in state.values() for x in dict.values(source)):
            why.append("receipt state shares records with the source")
    if outcome[0] == "err" and not _unpoisoned(module):
        why.append("poisoned after rejection")
    return why


def _unpoisoned(module):
    source = {KEYS[i]: dict(RECORDS[i]) for i in (0, 2, 5)}
    try:
        out = module.MigrationEngine(_pdv2).migrate(_request(source), source)
    except BaseException:  # noqa: BLE001 - any failure is poisoning
        return False
    return out == _model_receipt(source)


# -- fuzz tests -------------------------------------------------------------------


def _chunks(gen, size=48):
    total = FUZZ_SEEDS[gen]
    return [(gen, start, min(start + size, total)) for start in range(0, total, size)]


@pytest.mark.parametrize("gen,start,stop", [c for g in GENERATORS for c in _chunks(g)])
def test_fuzz_cases_hold(gen, start, stop):
    failures = []
    for seed in range(start, stop):
        why = _check(migration, gen, seed)
        if why:
            failures.append((_case(gen, seed)["label"], why))
    assert failures == []


@pytest.mark.parametrize("seed", range(40))
def test_happy_migrations_match_the_model(seed):
    source = _source(random.Random(f"happy-{seed}"))
    request = _request(source)
    snap_req, snap_src = copy.deepcopy(request), copy.deepcopy(source)
    records = list(source.values())
    engine = migration.MigrationEngine(migration.pdv2_digest)
    out = engine.migrate(request, source)
    assert out == _model_receipt(snap_src)
    assert request == snap_req and source == snap_src
    assert all(a is b for a, b in zip(source.values(), records, strict=True))
    assert engine.migrate(request, source) == out


def test_boundaries():
    engine = migration.MigrationEngine(migration.pdv2_digest)
    assert engine.migrate(_request({}), {}) == _model_receipt({})
    every = {k: dict(r) for k, r in zip(KEYS, RECORDS, strict=True)}
    assert engine.migrate(_request(every), every) == _model_receipt(every)
    # the registry has exactly one step; no-op and downgrade are unknown
    for frm, to in ((V1, V1), (V2, V2), (V2, V1)):
        with pytest.raises(migration.MigrationError) as exc:
            engine.migrate(_request({}, frm, to), {})
        assert exc.value.failure_class == UM and exc.value.__cause__ is None


@pytest.mark.parametrize("gen", GENERATORS)
def test_determinism_by_value(gen):
    for seed in range(0, FUZZ_SEEDS[gen], 5):
        first = _run(migration, GEN[gen](seed))[0]
        again = _run(migration, GEN[gen](seed))[0]
        assert first == again, _case(gen, seed)["label"]


def test_corpus_reaches_every_class_and_every_edit():
    classes, oks = set(), 0
    for gen in GENERATORS:
        for seed in range(FUZZ_SEEDS[gen]):
            outcome = _reference_outcome(gen, seed)
            if outcome[0] == "err":
                classes.add(outcome[1])
            else:
                oks += 1
    assert classes == CLASSES and oks >= 60
    labels = {_case(g, s)["label"] for g in GENERATORS for s in range(FUZZ_SEEDS[g])}
    assert {f"source:{t}" for t in SOURCE_TAMPERS} <= labels
    assert {f"oracle:{f}@{s}" for f in ORACLE_FAULTS for s in SPOTS} <= labels
    heads = {label.rsplit(":", 1)[0] for label in labels if label.startswith("live:")}
    assert {f"live:{k}" for k in SCRAMBLES} <= heads
    probe = {
        _reference_outcome(g, s)[1]
        for g in GENERATORS
        for s in range(PROBE[g])
        if _reference_outcome(g, s)[0] == "err"
    }
    assert probe == CLASSES


def test_forges_cover_every_class_the_module_raises_or_catches():
    caught = set()
    for node in ast.walk(ast.parse(SOURCE_TEXT)):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            elts = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            caught.update(ast.unparse(e) for e in elts)
    assert caught == {"BaseException", "_LINKED_REJECTIONS"}
    linked = {cls.__name__ for cls in migration._LINKED_REJECTIONS}
    assert linked == {"VariantError", "DigestError", "ValueError"}
    forged = {type(e).__name__ for e in FORGED.values()}
    assert linked | {"MigrationError"} <= forged
    assert {f"forged-migration-{c}" for c in CLASSES} <= set(FORGED)


# -- mutation check ---------------------------------------------------------------


def _source_mutant(name, edits):
    """store/migration.py with EDITS (old, new) applied, each matching exactly
    once, executed as a fresh module; MigrationError and _fail are rebound to
    the production class so typed failures stay comparable."""
    text = SOURCE_TEXT
    for old, new in edits:
        assert text.count(old) == 1, (name, old)
        text = text.replace(old, new)
    module = types.ModuleType("store._t0199_mutant")
    module.__file__ = str(ROOT / "store" / "migration.py")
    exec(compile(text, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.MigrationError = migration.MigrationError

    def _fail(failure_class):
        raise migration.MigrationError(failure_class, FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _first_bad_output_problems(module):
    """A rejected oracle output stops the migration at that call: a 4-record
    source whose first oracle output is bad sees exactly one oracle call and
    divergent_target (fail-closed reading; the contract pins the call count
    on success only)."""
    source = {KEYS[i]: dict(RECORDS[i]) for i in range(4)}
    bad = []
    for label, make in (
        ("garbage", lambda v, f: "garbage"),
        ("trailing-junk", lambda v, f: _pdv2(v, f) + "x"),
        ("trailing-newline", lambda v, f: _pdv2(v, f) + "\n"),
        ("str-subclass", lambda v, f: _Str(_pdv2(v, f))),
    ):
        calls = []

        def oracle(variant, fen, make=make, calls=calls):
            calls.append(fen)
            return make(variant, fen) if len(calls) == 1 else _pdv2(variant, fen)

        request, state = _request(source), copy.deepcopy(source)
        before = (_deep(request), _deep(state))
        fresh = False
        try:
            module.MigrationEngine(oracle).migrate(request, state)
            got = "ok"
        except BaseException as error:  # noqa: BLE001 - class is the check
            got = getattr(error, "failure_class", type(error).__name__)
            fresh = error.__cause__ is None and error.__context__ is None
        if got != "divergent_target" or len(calls) != 1:
            bad.append((label, got, len(calls)))
        elif not fresh:
            bad.append((label, "chained"))
        elif (_deep(request), _deep(state)) != before:
            bad.append((label, "inputs changed"))
    return bad


def test_rejected_oracle_output_stops_at_the_first_call():
    assert _first_bad_output_problems(migration) == []


def _source_digest_suffix_problems(module):
    """A source record digest with a trailing newline or trailing junk is
    malformed_migration_record, at the first and at the last record, with
    the request's source_id derived over the TAMPERED state (the original
    id would mask the check as conflicting_source): fresh error, no oracle
    call, request and source unchanged."""
    bad = []
    for suffix in ("\n", "x"):
        for position in (0, -1):
            source = {KEYS[i]: dict(RECORDS[i]) for i in (0, 2, 5)}
            key = sorted(source)[position]
            source[key] = {**source[key], "digest": source[key]["digest"] + suffix}
            request = _request(source)
            before = (_deep(request), _deep(source))
            calls = []

            def oracle(variant, fen, calls=calls):
                calls.append(fen)
                return _pdv2(variant, fen)

            fresh = False
            try:
                module.MigrationEngine(oracle).migrate(request, source)
                got = "ok"
            except BaseException as error:  # noqa: BLE001 - class is the check
                got = getattr(error, "failure_class", type(error).__name__)
                fresh = error.__cause__ is None and error.__context__ is None
            row = (repr(suffix), position)
            if got != MMR:
                bad.append((*row, got))
            elif not fresh:
                bad.append((*row, "chained"))
            elif calls:
                bad.append((*row, "oracle called"))
            elif (_deep(request), _deep(source)) != before:
                bad.append((*row, "inputs changed"))
    return bad


def test_source_digest_suffix_is_malformed():
    assert _source_digest_suffix_problems(migration) == []


def _probe(module):
    failures = [f"first-bad-output {row}" for row in _first_bad_output_problems(module)]
    failures += [f"digest-suffix {row}" for row in _source_digest_suffix_problems(module)]
    for gen in GENERATORS:
        for seed in range(PROBE[gen]):
            try:
                why = _check(module, gen, seed)
            except BaseException as error:  # noqa: BLE001
                why = [f"check raised {type(error).__name__}"]
            if why:
                failures.append(_case(gen, seed)["label"])
    return failures


_BE = '        except BaseException:\n            _fail("divergent_target")'
_OUT = "        if type(out) is not str or digest_re.fullmatch(out) is None:"
_REQ_KEYS = (
    "        if not all(type(key) is str for key in request_keys) or \\\n"
    "                set(request_keys) != _REQUEST_KEYS:"
)
_SCHEMA_VAL = "            if type(value) is not str or \\\n                    _SCHEMA_RE"
_SID_VAL = "        if type(value) is not str or _STATE_RE.fullmatch(value) is None:"
_STEP = (
    '        if (frozen_req["from_schema"], frozen_req["to_schema"]) not in _STEPS:\n'
    '            _fail("unknown_migration")\n'
)
_SRC_TYPE = "        if type(source_state) is not dict:\n"
_REC_TYPE = "    if type(key) is not str or type(record) is not dict:"
_FIELD_KEYS = (
    "    if not all(type(field) is str for field in fields) or \\\n"
    "            set(fields) != _RECORD_FIELDS:"
)
_CANON = (
    '    if record["variant"] != derived["variant"] or \\\n'
    '            record["snapshot_fen"] != derived["snapshot_fen"]:\n'
    "        return False\n"
)
_RESTORE_RECS = "            for rec, content in saved_records.values():\n"


def _passthrough(expr):
    return [(_BE, f"        except {expr}:\n            raise\n" + _BE)]


MUTANTS = {
    "boundary-passthrough": _passthrough("MigrationError"),
    "boundary-narrow": [(_BE, _BE.replace("BaseException", "Exception"))],
    **{
        f"boundary-passes-{name}": _passthrough(name)
        for name in ("VariantError", "DigestError", "ValueError", "TypeError")
    },
    "boundary-cause-chain": [
        (
            _BE,
            "        except BaseException as e:\n"
            '            raise MigrationError("divergent_target", '
            'FAILURE_MAPPING["divergent_target"]) from e',
        )
    ],
    "request-isinstance": [
        ("        if type(request) is not dict:", "        if not isinstance(request, dict):")
    ],
    "request-key-guard-off": [(_REQ_KEYS, "        if set(request_keys) != _REQUEST_KEYS:")],
    "request-key-len": [
        (_REQ_KEYS, _REQ_KEYS.replace("set(request_keys) != _REQUEST_KEYS", "len(request) != 3"))
    ],
    "request-key-superset": [
        (
            _REQ_KEYS,
            _REQ_KEYS.replace(
                "set(request_keys) != _REQUEST_KEYS", "not set(request_keys) >= _REQUEST_KEYS"
            ),
        )
    ],
    "schema-isinstance": [
        (_SCHEMA_VAL, _SCHEMA_VAL.replace("type(value) is not str", "not isinstance(value, str)"))
    ],
    "source-id-isinstance": [
        (_SID_VAL, _SID_VAL.replace("type(value) is not str", "not isinstance(value, str)"))
    ],
    "source-id-match": [
        (_SID_VAL, _SID_VAL.replace("_STATE_RE.fullmatch(value)", "_STATE_RE.match(value)"))
    ],
    "step-after-source": [
        (
            _STEP + "        source_re",
            "        source_re",
        ),
        (
            "        saved_container = dict(source_state)\n",
            _STEP + "        saved_container = dict(source_state)\n",
        ),
    ],
    "source-isinstance": [(_SRC_TYPE, "        if not isinstance(source_state, dict):\n")],
    "record-key-isinstance": [
        (_REC_TYPE, "    if not isinstance(key, str) or type(record) is not dict:")
    ],
    "record-isinstance": [
        (_REC_TYPE, "    if type(key) is not str or not isinstance(record, dict):")
    ],
    "field-key-guard-off": [(_FIELD_KEYS, "    if set(fields) != _RECORD_FIELDS:")],
    "field-values-isinstance": [
        (
            "    if any(type(record[field]) is not str for field in _RECORD_FIELDS):",
            "    if any(not isinstance(record[field], str) for field in _RECORD_FIELDS):",
        )
    ],
    "canonical-check-off": [(_CANON, "")],
    "digest-grammar-off": [
        ('    if digest_re.fullmatch(record["digest"]) is None:\n        return False\n', "")
    ],
    "identity-check-off": [("    return identity == key", "    return True")],
    "conflicting-source-off": [
        (
            '            if state_id(frozen) != frozen_req["source_id"]:\n'
            '                _fail("conflicting_source")\n',
            "",
        )
    ],
    "unfrozen-source": [
        (
            "        frozen = {key: dict(rec) for key, rec in source_state.items()}",
            "        frozen = source_state",
        )
    ],
    "shallow-frozen-source": [
        (
            "        frozen = {key: dict(rec) for key, rec in source_state.items()}",
            "        frozen = dict(source_state)",
        )
    ],
    "no-record-restore": [
        (
            _RESTORE_RECS + "                rec.clear()\n                rec.update(content)\n",
            _RESTORE_RECS + "                pass\n",
        )
    ],
    "no-container-restore": [
        (
            "            source_state.clear()\n            source_state.update(saved_container)\n",
            "",
        )
    ],
    "no-request-restore": [
        (
            "            dict.clear(request)\n            dict.update(request, saved_request)",
            "            pass",
        )
    ],
    "request-restore-no-clear": [("            dict.clear(request)\n", "")],
    "restore-only-on-failure": [
        (
            "        finally:\n            for rec",
            "        except BaseException:\n            for rec",
        )
    ],
    "migration-id-swap": [
        (
            '                    frozen_req["source_id"], target_id),',
            '                    target_id, frozen_req["source_id"]),',
        )
    ],
    "receipt-target-from-source": [
        (
            '                "target_id": target_id,',
            '                "target_id": frozen_req["source_id"],',
        )
    ],
    "state-id-unsorted": [("    for key in sorted(state):", "    for key in state:")],
    "oracle-args-swapped": [
        (
            '                        record["variant"], record["snapshot_fen"], target_re),',
            '                        record["snapshot_fen"], record["variant"], target_re),',
        )
    ],
}

MUTANTS.update(
    {
        "digest-search": [
            (
                '    if digest_re.fullmatch(record["digest"]) is None:',
                '    if digest_re.search(record["digest"]) is None:',
            )
        ],
        "output-isinstance": [
            (_OUT, _OUT.replace("type(out) is not str", "not isinstance(out, str)"))
        ],
        "output-match": [(_OUT, _OUT.replace("digest_re.fullmatch(out)", "digest_re.match(out)"))],
        "output-grammar-off": [(_OUT, "        if type(out) is not str:")],
    }
)

# Edits that must NOT change behavior; each is asserted green.
# The output checks at the oracle boundary are repeated by post_transform_
# validation, so a weakened boundary check still ends in divergent_target;
# it is NOT equivalent, though: it keeps calling the oracle for the remaining
# records before rejecting. _first_bad_output_problems pins fail-at-first-bad-
# output (one call), so those edits are killed mutants above, not entries here.
# The receipt is built after the finally block restores the caller's request,
# so reading the live request there reads the restored, validated value.
EQUIVALENT_EDITS = {
    "receipt-reads-restored-request": [
        (
            '                "to_schema": frozen_req["to_schema"],',
            '                "to_schema": request["to_schema"],',
        )
    ],
}


def test_identity_source_mutant_is_green():
    module = _source_mutant("identity", [])
    assert module.MigrationEngine is not migration.MigrationEngine
    assert _probe(module) == []


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edits_stay_green(name):
    assert _probe(_source_mutant(name, EQUIVALENT_EDITS[name])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(_source_mutant(name, MUTANTS[name])) != [], name
