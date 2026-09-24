"""T0205 permanent red battery for store atomic-edit engines.

The battery drives EVERY row of the T0204 conformance fixture
(tests/fixtures/atomic_edit/cases.json), a closed set of totality
probes and pinned accept probes through an engine class:

- happy/boundary: the pinned receipt exactly (field order and exact
  built-in types at every level); base and request untouched by value
  AND identity; nothing of the receipt aliased with any input;
  determinism by value over fresh copies on the SAME instance (never
  the same object); and a no-op follow-up (empty operations against
  the receipt's own target) returns the same state without touching
  it;
- malformed: the original input rejects with the pinned failure class
  and its mapped code, base and request untouched, and the declared
  single-locus repair commits with the receipt the local derivation
  gives, on the SAME instance;
- rollback: a rejection leaves base and request untouched by value
  AND identity, then the valid follow-up over the very same base
  object on the SAME instance returns the pinned receipt, and the base
  is still untouched;
- totality: hostile requests, base ids, operation lists, operations,
  kinds, identities and records (None, bool, int, bytes, str/dict/list
  subclasses, str-subclass and colliding-hash keys whose comparisons
  raise, renamed keys at the same arity, extra and missing keys,
  grammar near-misses including a trailing newline and a leading
  space, surrogates), hostile bases, failure-precedence probes and
  accept probes (key order, record key order, operation order) each
  reject with the pinned failure class or, for accept probes, return
  the locally derived receipt and, on the SAME instance, still reject
  a bad request typed and still accept the good edit - any other
  BaseException escaping is a failure.

Standalone-red convention (T0151, T0178, T0187, T0196, T0232, T0241,
T0250, T0259, T0268, T0277): the battery is permanently GREEN against
the contract-derived reference engine from
tests.test_t0203_atomic_edit_contract and every mutant below is RED on
its exact target. The production task switches the binding by
replacing ONLY the two binding lines below with the production
AtomicEditEngine / AtomicEditError names; no assertion changes.
Reference-source mutants raise the BOUND error class (see
_source_mutant), guarded by an identity mutant that must pass.

Fixture closure: the T0204 ordered per-section manifests, per-row
semantic checks over the ORIGINAL row data (an unknown name raises)
and ROW_DIGESTS whole-row sha256 table (key set equal to the
manifests) are enforced here through the T0204 validator.
test_closure_kills_substitution_mutants proves every cross-row payload
substitution, rename, reorder, drop, duplicate and single-edge erasure
is killed with the digest table live AND with digests neutralized."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0203_atomic_edit_contract as _reference  # noqa: E402
from tests import test_t0204_atomic_edit_fixture as _fixture  # noqa: E402
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    LEGAL_EP,
    STARTPOS,
)
from tools.atomic_edit_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
)

# -- binding (the production task replaces ONLY these two lines) ---------------
AtomicEditEngine = _reference.AtomicEditEngine
AtomicEditError = _reference.AtomicEditError

FIXTURE = (Path(__file__).parent / "fixtures" / "atomic_edit"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECEIPT_FIELDS = ("edit_id", "base_id", "target_id", "state")
RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
MER = "malformed_edit_record"
CB = "conflicting_base"
UI = "unknown_identity"
MANIFESTS = _fixture.MANIFESTS
ROW_DIGESTS = _fixture.ROW_DIGESTS

REC_A, REC_B, REC_C, REC_D = (_reference._node(f) for f in
                              (STARTPOS, AFTER_E4, KINGS, LEGAL_EP))
ID_A, ID_B, ID_C, ID_D = (_reference._identity(r) for r in
                          (REC_A, REC_B, REC_C, REC_D))
RECORDS = {ID_A: REC_A, ID_B: REC_B, ID_C: REC_C, ID_D: REC_D}


# -- local derivation (independent of the engine under test) -------------------
def _sid(state):
    """gs1 state id from data/contracts/atomic_edit.yaml."""
    parts = []
    for key in sorted(state):
        rec = state[key]
        body = "|".join(f"{f}={rec[f]}" for f in sorted(rec))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256("".join(parts).encode()).hexdigest()


def _derive(request, base):
    """The receipt a correct engine returns for a VALID edit."""
    staged = {k: dict(v) for k, v in base.items()}
    ops = sorted(request["operations"], key=lambda op: op["identity"])
    for op in ops:
        if op["kind"] == "delete":
            del staged[op["identity"]]
        else:
            staged[op["identity"]] = dict(op["record"])
    target = _sid(staged)
    canonical = ";".join(f"{op['kind']}:{op['identity']}" for op in ops)
    edit_id = "ae1:" + hashlib.sha256(
        f"{request['base_id']}\n{target}\n{canonical}".encode()).hexdigest()
    return {"edit_id": edit_id, "base_id": request["base_id"],
            "target_id": target, "state": staged}


def _put(rec):
    return {"kind": "put", "identity": _reference._identity(rec),
            "record": dict(rec)}


def _delete(identity):
    return {"kind": "delete", "identity": identity, "record": None}


def _good():
    """The shared valid edit: base {A, B}; put C, delete A."""
    base = {ID_A: dict(REC_A), ID_B: dict(REC_B)}
    return ({"base_id": _sid(base),
             "operations": [_put(REC_C), _delete(ID_A)]}, base)


GOOD_EXPECT = _derive(*_good())


# -- exact snapshot / aliasing -------------------------------------------------
class _KeyMark:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key


def _snap(obj):
    """Exact flat snapshot: type, id, length and key order of every
    container, key identity (text for exact str keys, id otherwise),
    scalar type+value. Iterative, cycle-safe, never hashes, compares
    or reprs a caller-owned key or subclass instance."""
    out, seen, stack = [], set(), [obj]
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is dict or kind is list or kind is tuple:
            if id(node) in seen:
                out.append(("seen", id(node)))
                continue
            seen.add(id(node))
            if kind is dict:
                items = list(dict.items(node))
                out.append(("dict", id(node), len(items)))
                for key, value in reversed(items):
                    stack.append(value)
                    stack.append(_KeyMark(key))
            else:
                values = list(kind.__iter__(node))
                out.append((kind.__name__, id(node), len(values)))
                stack.extend(reversed(values))
        elif kind is _KeyMark:
            key = node.key
            out.append(("key", key if type(key) is str
                        else (type(key).__name__, id(key))))
        elif kind is int:
            out.append(("int", node.bit_length(), node & 0xFFFF,
                        node < 0))
        elif kind in (str, bool, float, type(None), bytes):
            out.append((kind.__name__, node))
        else:
            out.append(("object", kind.__name__, id(node)))
    return out


def _containers(obj):
    return {e[1] for e in _snap(obj) if e[0] in ("dict", "list", "tuple")}


def _not_aliased(result, inputs):
    return not (_containers(result) & _containers(inputs))


# -- hostile values --------------------------------------------------------------
_ARMED = [False]


class SK(str):
    """str subclass whose comparisons raise while armed."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        if _ARMED[0]:
            raise RuntimeError("hostile str __eq__")
        return str.__eq__(self, other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __lt__(self, other):
        if _ARMED[0]:
            raise RuntimeError("hostile str __lt__")
        return str.__lt__(self, other)

    def __gt__(self, other):
        if _ARMED[0]:
            raise RuntimeError("hostile str __gt__")
        return str.__gt__(self, other)

    def __le__(self, other):
        if _ARMED[0]:
            raise RuntimeError("hostile str __le__")
        return str.__le__(self, other)

    def __ge__(self, other):
        if _ARMED[0]:
            raise RuntimeError("hostile str __ge__")
        return str.__ge__(self, other)


class HK:
    """Non-str key whose hash collides with a real key and whose __eq__
    raises while armed."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if _ARMED[0]:
            raise RuntimeError("hostile __eq__")
        return other is self

    def __deepcopy__(self, memo):
        return self


class _DictSub(dict):
    """dict subclass whose accessors raise."""

    def items(self):
        raise RuntimeError("hostile items")

    def keys(self):
        raise RuntimeError("hostile keys")

    def __getitem__(self, key):
        raise RuntimeError("hostile __getitem__")


class _PlainDictSub(dict):
    pass


class _ListSub(list):
    pass


class _IntSub(int):
    pass


@contextlib.contextmanager
def _armed():
    _ARMED[0] = True
    try:
        yield
    finally:
        _ARMED[0] = False


# -- engine runners ---------------------------------------------------------------
def _receipt_ok(receipt, expect):
    """Exact receipt: field order and exact built-in types at every
    level, equal to EXPECT."""
    if type(receipt) is not dict or tuple(receipt) != RECEIPT_FIELDS:
        return False
    if any(type(receipt[f]) is not str for f in RECEIPT_FIELDS[:3]):
        return False
    state = receipt["state"]
    if type(state) is not dict:
        return False
    for key, rec in dict.items(state):
        if type(key) is not str or type(rec) is not dict:
            return False
        if any(type(k) is not str or type(v) is not str
               for k, v in dict.items(rec)):
            return False
    return receipt == expect


def _accepts(engine, request, base, expect):
    """ENGINE commits REQUEST over BASE as EXPECT: inputs untouched by
    value and identity, the receipt aliases nothing of the inputs."""
    inputs = (request, base)
    before = _snap(inputs)
    with _armed():
        receipt = engine.apply(request, base)
    return (_receipt_ok(receipt, expect) and _snap(inputs) == before
            and _not_aliased(receipt, inputs))


def _rejects(engine, request, base, failure):
    """ENGINE rejects with FAILURE typed (the BOUND class, its mapped
    code), inputs untouched by value and identity."""
    inputs = (request, base)
    before = _snap(inputs)
    try:
        with _armed():
            engine.apply(request, base)
    except AtomicEditError as error:
        return (type(error) is AtomicEditError
                and error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and _snap(inputs) == before)
    return False


def _ok_row(cls, case):
    engine = cls()
    request, base = copy.deepcopy(case["request"]), copy.deepcopy(case["base"])
    expect = case["expect"]
    if not _accepts(engine, request, base, expect):
        return False
    # determinism by value on the SAME instance, never the same object
    first = engine.apply(copy.deepcopy(case["request"]),
                         copy.deepcopy(case["base"]))
    second = engine.apply(copy.deepcopy(case["request"]),
                          copy.deepcopy(case["base"]))
    if first != expect or second != expect or first is second or \
            first["state"] is second["state"]:
        return False
    # a no-op follow-up against the receipt's own target
    noop = {"base_id": first["target_id"], "operations": []}
    return _accepts(engine, noop, first["state"], _derive(noop, first["state"]))


def _repaired(case):
    (locus, value), = case["minimal_repair"].items()
    parts = {"request": case["request"], "base": case["base"], locus: value}
    return copy.deepcopy(parts["request"]), copy.deepcopy(parts["base"])


def _malformed_row(cls, case):
    engine = cls()
    if not _rejects(engine, copy.deepcopy(case["request"]),
                    copy.deepcopy(case["base"]), case["expect_failure"]):
        return False
    request, base = _repaired(case)
    return _accepts(engine, request, base, _derive(request, base))


def _rollback_row(cls, case):
    engine = cls()
    base = copy.deepcopy(case["base"])
    before = _snap(base)
    if not _rejects(engine, copy.deepcopy(case["request"]), base,
                    case["expect_failure"]):
        return False
    return (_accepts(engine, copy.deepcopy(case["follow_up"]), base,
                     case["expect"]) and _snap(base) == before)


_RUNNERS = {"happy": _ok_row, "boundary": _ok_row,
            "malformed": _malformed_row, "rollback": _rollback_row}


# -- totality probes ------------------------------------------------------------
ACCEPT = "accept"


def _req(fn):
    def build():
        request, base = _good()
        return fn(request), base
    return build


def _op(index, fn):
    def build():
        request, base = _good()
        request["operations"][index] = fn(request["operations"][index])
        return request, base
    return build


def _record(fn, index=0):
    """Edit the put record (operation 0 is put C)."""
    def edit(op):
        op["record"] = fn(op["record"])
        return op
    return _op(index, edit)


def _base(fn, rebind=False):
    def build():
        request, base = _good()
        base = fn(base)
        if rebind:
            request["base_id"] = _sid(base)
        return request, base
    return build


def _rekey(mapping, key, new):
    out = {}
    for k, v in mapping.items():
        out[new if k == key else k] = v
    return out


def _set(key, value):
    def fn(obj):
        obj[key] = value
        return obj
    return fn


def _pop(key):
    def fn(obj):
        del obj[key]
        return obj
    return fn


def _const(value):
    return lambda _: value


def _both(request_fn, base_fn):
    def build():
        request, base = _good()
        return request_fn(request), base_fn(base)
    return build


def _ops(*ops, base=None):
    def build():
        b = copy.deepcopy(base) if base is not None else \
            {ID_A: dict(REC_A), ID_B: dict(REC_B)}
        return {"base_id": _sid(b), "operations": [copy.deepcopy(o) for o in ops]}, b
    return build


_GOOD_ID = _sid({ID_A: REC_A, ID_B: REC_B})
_OTHER_ID = _sid({ID_A: REC_A})
_WRONG_DIGEST = "pdv1:" + "0" * 64


def _probe_builders():
    p = {}

    def add(name, build, failure):
        assert name not in p, name
        p[name] = (build, failure)

    # request container
    for name, value in (("none", None), ("list", ["x", []]), ("str", "req"),
                        ("int", 0), ("tuple", (_GOOD_ID, []))):
        add(f"request-{name}", _req(_const(value)), MER)
    add("request-dict-subclass", _req(lambda r: _DictSub(r)), MER)
    add("request-plain-dict-subclass", _req(lambda r: _PlainDictSub(r)), MER)
    add("request-empty", _req(_const({})), MER)
    add("request-missing-base-id", _req(_pop("base_id")), MER)
    add("request-missing-operations", _req(_pop("operations")), MER)
    add("request-extra-key", _req(_set("force", True)), MER)
    add("request-extra-non-ascii-key", _req(_set("\u00e9", 1)), MER)
    add("request-renamed-base-id-same-arity",
        _req(lambda r: _rekey(r, "base_id", "base_ix")), MER)
    add("request-renamed-operations-same-arity",
        _req(lambda r: _rekey(r, "operations", "operationz")), MER)
    add("request-str-subclass-key",
        _req(lambda r: _rekey(r, "base_id", SK("base_id"))), MER)
    add("request-colliding-key",
        _req(lambda r: _rekey(r, "base_id", HK("base_id"))), MER)
    add("request-colliding-extra-key", _req(_set(HK("zz"), 1)), MER)
    # base_id
    for name, value in (("int", 1), ("bool", True), ("none", None),
                        ("bytes", _GOOD_ID.encode()), ("str-subclass", SK(_GOOD_ID)),
                        ("upper", _GOOD_ID.upper()), ("trailing-newline", _GOOD_ID + "\n"),
                        ("leading-space", " " + _GOOD_ID), ("short", _GOOD_ID[:-1]),
                        ("long", _GOOD_ID + "0"), ("other-prefix", "gs2:" + _GOOD_ID[4:]),
                        ("edit-prefix", "ae1:" + _GOOD_ID[4:]), ("empty", ""),
                        ("surrogate", _GOOD_ID[:-1] + "\ud800"),
                        ("list", [_GOOD_ID])):
        add(f"base-id-{name}", _req(_set("base_id", value)), MER)
    # operations container
    for name, value in (("none", None), ("dict", {}), ("empty-str", ""),
                        ("tuple", ()), ("int", 0)):
        add(f"operations-{name}", _req(_set("operations", value)), MER)
    add("operations-list-subclass",
        _req(lambda r: _set("operations", _ListSub(r["operations"]))(r)), MER)
    # one operation
    for index, where in ((0, "put"), (1, "delete")):
        for name, value in (("none", None), ("list", ["put"]), ("str", "put")):
            add(f"{where}-op-{name}", _op(index, _const(value)), MER)
        add(f"{where}-op-dict-subclass", _op(index, lambda o: _DictSub(o)), MER)
        add(f"{where}-op-plain-dict-subclass",
            _op(index, lambda o: _PlainDictSub(o)), MER)
        add(f"{where}-op-extra-key", _op(index, _set("force", True)), MER)
        add(f"{where}-op-missing-record", _op(index, _pop("record")), MER)
        add(f"{where}-op-missing-kind", _op(index, _pop("kind")), MER)
        add(f"{where}-op-renamed-record-same-arity",
            _op(index, lambda o: _rekey(o, "record", "recorx")), MER)
        add(f"{where}-op-renamed-identity-same-arity",
            _op(index, lambda o: _rekey(o, "identity", "identitx")), MER)
        add(f"{where}-op-str-subclass-key",
            _op(index, lambda o: _rekey(o, "kind", SK("kind"))), MER)
        add(f"{where}-op-colliding-key",
            _op(index, lambda o: _rekey(o, "kind", HK("kind"))), MER)
        for name, fn in (("str-subclass", lambda o: _set("kind", SK(o["kind"]))(o)),
                         ("upper", lambda o: _set("kind", o["kind"].upper())(o)),
                         ("padded", lambda o: _set("kind", o["kind"] + " ")(o)),
                         ("none", _set("kind", None)), ("int", _set("kind", 0)),
                         ("upsert", _set("kind", "upsert")), ("empty", _set("kind", ""))):
            add(f"{where}-kind-{name}", _op(index, fn), MER)
        add(f"{where}-kind-colliding-non-str",
            _op(index, lambda o: _set("kind", HK(o["kind"]))(o)), MER)
        for name, fn in (("none", _set("identity", None)),
                         ("int", _set("identity", 7)),
                         ("bytes", lambda o: _set("identity", o["identity"].encode())(o)),
                         ("str-subclass", lambda o: _set("identity", SK(o["identity"]))(o)),
                         ("list", _set("identity", [])), ("dict", _set("identity", {}))):
            add(f"{where}-identity-{name}", _op(index, fn), MER)
        # colliding non-str identity hashing like the OTHER operation's
        # identity (op 0 puts C, op 1 deletes A); __eq__ raises while armed
        other = ID_A if index == 0 else ID_C
        add(f"{where}-identity-colliding-non-str",
            _op(index, lambda o, other=other: _set("identity", HK(other))(o)), MER)
    # delete record must be None exactly
    for name, value in (("empty-dict", {}), ("false", False), ("zero", 0),
                        ("empty-str", ""), ("record", dict(REC_A))):
        add(f"delete-record-{name}", _op(1, _set("record", value)), MER)
    add("delete-absent-identity", _op(1, _set("identity", ID_D)), UI)
    add("delete-arbitrary-identity", _op(1, _set("identity", "no-such")), UI)
    add("delete-surrogate-identity", _op(1, _set("identity", "\ud800")), UI)
    # put record
    for name, value in (("none", None), ("list", list(REC_C.values())),
                        ("str", "record"), ("empty-dict", {})):
        add(f"put-record-{name}", _record(_const(value)), MER)
    add("put-record-dict-subclass", _record(lambda r: _DictSub(r)), MER)
    add("put-record-plain-dict-subclass", _record(lambda r: _PlainDictSub(r)), MER)
    add("put-record-extra-field", _record(_set("zz", "1")), MER)
    for field in RECORD_FIELDS:
        add(f"put-record-missing-{field}", _record(_pop(field)), MER)
        add(f"put-record-{field}-str-subclass",
            _record(lambda r, f=field: _set(f, SK(r[f]))(r)), MER)
        add(f"put-record-{field}-int", _record(_set(field, 1)), MER)
        add(f"put-record-{field}-none", _record(_set(field, None)), MER)
        add(f"put-record-{field}-bytes",
            _record(lambda r, f=field: _set(f, r[f].encode())(r)), MER)
        add(f"put-record-renamed-{field}-same-arity",
            _record(lambda r, f=field: _rekey(r, f, f[:-1] + "x")), MER)
        add(f"put-record-{field}-str-subclass-key",
            _record(lambda r, f=field: _rekey(r, f, SK(f))), MER)
        add(f"put-record-{field}-colliding-key",
            _record(lambda r, f=field: _rekey(r, f, HK(f))), MER)
    add("put-record-wrong-digest", _record(_set("digest", _WRONG_DIGEST)), MER)
    add("put-record-other-digest", _record(_set("digest", REC_A["digest"])), MER)
    add("put-record-digest-upper",
        _record(lambda r: _set("digest", "pdv1:" + r["digest"][5:].upper())(r)), MER)
    add("put-record-digest-trailing-newline",
        _record(lambda r: _set("digest", r["digest"] + "\n")(r)), MER)
    add("put-record-digest-leading-space",
        _record(lambda r: _set("digest", " " + r["digest"])(r)), MER)
    add("put-record-fen-non-canonical-clocks",
        _record(lambda r: _set("snapshot_fen", r["snapshot_fen"].replace(" 0 1", " 5 9"))(r)),
        MER)
    add("put-record-fen-trailing-space",
        _record(lambda r: _set("snapshot_fen", r["snapshot_fen"] + " ")(r)), MER)
    add("put-record-fen-garbage", _record(_set("snapshot_fen", "not a fen")), MER)
    add("put-record-fen-other-position",
        _record(_set("snapshot_fen", REC_D["snapshot_fen"])), MER)
    add("put-record-variant-unknown", _record(_set("variant", "atomic")), MER)
    add("put-record-variant-upper", _record(_set("variant", "STANDARD")), MER)
    add("put-record-surrogate-fen",
        _record(lambda r: _set("snapshot_fen", r["snapshot_fen"] + "\ud800")(r)), MER)
    add("put-identity-of-other-record", _op(0, _set("identity", ID_D)), MER)
    add("put-identity-arbitrary", _op(0, _set("identity", "no-such")), MER)
    # uniqueness
    add("duplicate-put-and-delete", _ops(_put(REC_B), _delete(ID_B)), MER)
    add("duplicate-put", _ops(_put(REC_C), _put(REC_C)), MER)
    add("duplicate-delete", _ops(_delete(ID_A), _delete(ID_A)), MER)
    # base
    for name, value in (("none", None), ("list", [dict(REC_A), dict(REC_B)]),
                        ("str", "base"), ("tuple", ())):
        add(f"base-{name}", _base(_const(value)), MER)
    add("base-dict-subclass", _base(lambda b: _DictSub(b)), MER)
    add("base-plain-dict-subclass", _base(lambda b: _PlainDictSub(b)), MER)
    add("base-str-subclass-key", _base(lambda b: _rekey(b, ID_A, SK(ID_A))), MER)
    add("base-colliding-key", _base(lambda b: _rekey(b, ID_A, HK(ID_A))), MER)
    add("base-int-key", _base(lambda b: _rekey(b, ID_A, 1)), MER)
    add("base-key-swapped", _base(lambda b: {ID_A: b[ID_B], ID_B: b[ID_A]}), MER)
    add("base-key-other-identity", _base(lambda b: _rekey(b, ID_B, ID_D)), MER)
    add("base-record-none", _base(_set(ID_B, None)), MER)
    add("base-record-dict-subclass",
        _base(lambda b: _set(ID_B, _PlainDictSub(b[ID_B]))(b)), MER)
    add("base-record-extra-field",
        _base(lambda b: _set(ID_B, {**b[ID_B], "zz": "1"})(b)), MER)
    add("base-record-wrong-digest",
        _base(lambda b: _set(ID_B, {**b[ID_B], "digest": _WRONG_DIGEST})(b)), MER)
    add("base-record-digest-str-subclass",
        _base(lambda b: _set(ID_B, {**b[ID_B], "digest": SK(b[ID_B]["digest"])})(b)), MER)
    add("base-record-renamed-field-same-arity",
        _base(lambda b: _set(ID_B, _rekey(b[ID_B], "digest", "digesx"))(b)), MER)
    add("base-record-colliding-key",
        _base(lambda b: _set(ID_B, _rekey(b[ID_B], "digest", HK("digest")))(b)), MER)
    # conflicting base
    add("base-id-of-another-state", _req(_set("base_id", _OTHER_ID)), CB)
    add("base-id-of-empty-state", _req(_set("base_id", _sid({}))), CB)
    add("base-id-of-target-state", _req(_set("base_id", GOOD_EXPECT["target_id"])), CB)
    add("base-missing-a-record", _base(_pop(ID_B)), CB)
    add("base-extra-record", _base(_set(ID_D, dict(REC_D))), CB)
    add("base-id-well-formed-unknown", _req(_set("base_id", "gs1:" + "0" * 64)), CB)
    # failure precedence
    add("precedence-malformed-op-over-conflicting-base",
        _both(lambda r: _set("base_id", _OTHER_ID)(r)
              and _set("operations", [_put(REC_C), {"kind": "zap", "identity": "x",
                                                    "record": None}])(r),
              lambda b: b), MER)
    add("precedence-malformed-base-over-conflicting-base",
        _base(lambda b: _set(ID_B, {**b[ID_B], "digest": _WRONG_DIGEST})(b)), MER)
    add("precedence-malformed-late-op-over-unknown-delete",
        _ops(_delete(ID_D), {"kind": "put", "identity": ID_C, "record": None}), MER)
    add("precedence-conflicting-base-over-unknown-delete",
        _both(lambda r: _set("base_id", _OTHER_ID)(r)
              and _set("operations", [_delete(ID_D)])(r), lambda b: b), CB)
    add("precedence-malformed-request-over-malformed-base",
        _both(_set("force", True), _const(None)), MER)
    # unknown identity at each sorted position
    for position in ("first", "middle", "last"):
        ids = sorted([ID_A, ID_B, ID_C, ID_D])
        target = {"first": ids[0], "middle": ids[1], "last": ids[3]}[position]
        present = {i: RECORDS[i] for i in ids if i != target}
        ops = [_delete(i) if i == target else _put(RECORDS[i]) for i in ids]
        add(f"unknown-delete-{position}-in-canonical-order",
            _ops(*ops, base=present), UI)
    # accept probes
    add("accept-good-edit", _ops(_put(REC_C), _delete(ID_A)), ACCEPT)
    add("accept-operations-reversed", _ops(_delete(ID_A), _put(REC_C)), ACCEPT)
    add("accept-base-keys-reversed",
        _base(lambda b: dict(reversed(list(b.items())))), ACCEPT)
    add("accept-base-record-keys-reversed",
        _base(lambda b: {k: dict(reversed(list(v.items()))) for k, v in b.items()}),
        ACCEPT)
    add("accept-put-record-keys-reversed",
        _record(lambda r: dict(reversed(list(r.items())))), ACCEPT)
    add("accept-op-keys-reversed",
        _op(0, lambda o: dict(reversed(list(o.items())))), ACCEPT)
    add("accept-request-keys-reversed",
        _req(lambda r: dict(reversed(list(r.items())))), ACCEPT)
    add("accept-put-all-onto-empty",
        _ops(*(_put(r) for r in (REC_D, REC_C, REC_B, REC_A)), base={}), ACCEPT)
    add("accept-delete-all",
        _ops(_delete(ID_B), _delete(ID_A)), ACCEPT)
    add("accept-replace-with-identical-record", _ops(_put(REC_A)), ACCEPT)
    add("accept-noop-on-empty", _ops(base={}), ACCEPT)
    add("accept-four-operations-mixed",
        _ops(_delete(ID_A), _put(REC_B), _put(REC_C), _put(REC_D)), ACCEPT)
    return p


PROBES = _probe_builders()
PROBE_EXPECT = {name: _derive(*build()) for name, (build, failure)
                in PROBES.items() if failure == ACCEPT}
PROBE_MANIFEST = tuple(PROBES)
PROBE_COUNT = 192  # GENERATED


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    request, base = build()
    engine = cls()
    if failure == ACCEPT:
        # SAME INSTANCE: accepts, then still rejects a bad request typed
        # and still accepts the good edit
        try:
            return (_accepts(engine, request, base, PROBE_EXPECT[name])
                    and _rejects(engine, {"base_id": 1}, {}, MER)
                    and _accepts(engine, *_good(), GOOD_EXPECT))
        except BaseException:  # noqa: BLE001 - any rejection fails
            return False
    try:
        return _rejects(engine, request, base, failure)
    except BaseException:  # noqa: BLE001 - a raw escape is the defect
        return False


def _probe(cls, executed=None, first_only=False, only=None):
    """Every manifest row and every totality probe through CLS; returns
    failing labels (only the first when FIRST_ONLY). Untrusted-component
    boundary: BaseException is caught."""
    failures = []
    jobs = []
    for section, manifest in MANIFESTS.items():
        rows = {r["name"]: r for r in CASES[section]}
        for name in manifest:
            jobs.append((f"{section}:{name}",
                         lambda s=section, r=rows[name]: _RUNNERS[s](cls, r)))
    for name in PROBE_MANIFEST:
        jobs.append((f"totality:{name}", lambda n=name: _totality_ok(cls, n)))
    for label, job in jobs:
        if only is not None and label not in only:
            continue
        try:
            ok = job()
        except BaseException as error:  # noqa: BLE001
            failures.append(f"{label}:{type(error).__name__}")
        else:
            if executed is not None:
                executed.append(label)
            if not ok:
                failures.append(label)
        if first_only and failures:
            return failures
    return failures


# -- engine mutants ----------------------------------------------------------------
class AcceptsAll(AtomicEditEngine):
    def apply(self, request, base):
        try:
            return super().apply(request, base)
        except AtomicEditError:
            return copy.deepcopy(GOOD_EXPECT)


class WrongCode(AtomicEditEngine):
    def apply(self, request, base):
        try:
            return super().apply(request, base)
        except AtomicEditError as error:
            raise AtomicEditError(error.failure_class, "internal") from None


def _remap(frm, to):
    class Remap(AtomicEditEngine):
        def apply(self, request, base):
            try:
                return super().apply(request, base)
            except AtomicEditError as error:
                if error.failure_class == frm:
                    raise AtomicEditError(to, FAILURE_MAPPING[to]) from None
                raise
    Remap.__name__ = f"Remap_{frm}_to_{to}"
    return Remap


class ForeignError(AtomicEditEngine):
    """Rejects with a look-alike error class, not the bound one."""

    class _Lookalike(Exception):
        def __init__(self, failure_class, code):
            super().__init__(failure_class)
            self.failure_class, self.code = failure_class, code

    def apply(self, request, base):
        try:
            return super().apply(request, base)
        except AtomicEditError as error:
            raise self._Lookalike(error.failure_class, error.code) from None


class CommitsIntoBase(AtomicEditEngine):
    """Commits the result into the caller's base in place."""

    def apply(self, request, base):
        receipt = super().apply(request, base)
        base.clear()
        base.update(copy.deepcopy(receipt["state"]))
        return receipt


class PartialStageOnReject(AtomicEditEngine):
    """Applies puts to the caller's base before a later failure."""

    def apply(self, request, base):
        try:
            return super().apply(request, base)
        except AtomicEditError:
            if type(base) is dict and type(request) is dict and \
                    type(request.get("operations")) is list:
                for op in request["operations"]:
                    if type(op) is dict and op.get("kind") == "put" and \
                            type(op.get("identity")) is str:
                        base[op["identity"]] = op.get("record")
            raise


class StateSharesBaseRecords(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        for key in receipt["state"]:
            if key in base:
                receipt["state"][key] = base[key]
        return receipt


class StateSharesPutRecords(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        for op in request["operations"]:
            if op["kind"] == "put":
                receipt["state"][op["identity"]] = op["record"]
        return receipt


class MutatesRequestOnSuccess(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        request["operations"].sort(key=lambda op: op["identity"])
        return receipt


class NormalizesBaseKeyOrder(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        items = sorted(base.items())
        base.clear()
        base.update(items)
        return receipt


class CachedResult(AtomicEditEngine):
    """Class-level cache: a repeated edit returns the SAME receipt."""

    _cache = {}

    def apply(self, request, base):
        receipt = super().apply(request, base)
        key = json.dumps([request, base], sort_keys=True, default=str)
        return self._cache.setdefault(key, receipt)


class StaleEditId(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        receipt["edit_id"] = "ae1:" + hashlib.sha256(
            receipt["base_id"].encode()).hexdigest()
        return receipt


class ReceiptFieldOrder(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        return dict(reversed(list(receipt.items())))


class StrSubclassEditId(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        receipt["edit_id"] = SK(receipt["edit_id"])
        return receipt


class TargetFromBase(AtomicEditEngine):
    def apply(self, request, base):
        receipt = super().apply(request, base)
        receipt["target_id"] = receipt["base_id"]
        return receipt


class RawRequestPeek(AtomicEditEngine):
    """Compares request keys before the exact-str guard."""

    def apply(self, request, base):
        if type(request) is dict:
            "base_id" in request  # noqa: B015 - the hash/== probe is the point
        return super().apply(request, base)


class RawKindPeek(AtomicEditEngine):
    """Tests kind membership before the exact-str type check."""

    def apply(self, request, base):
        if type(request) is dict and type(request.get("operations")) is list:
            for op in request["operations"]:
                if type(op) is dict and "kind" in dict.keys(op):
                    op["kind"] in ("put", "delete")  # noqa: B015
        return super().apply(request, base)


class RawBaseKeySort(AtomicEditEngine):
    """Sorts base keys before the exact-str key check."""

    def apply(self, request, base):
        if type(base) is dict:
            with contextlib.suppress(TypeError):
                sorted(dict.keys(base))
        return super().apply(request, base)


# Behaviorally EQUIVALENT one-guard edits, documented and deliberately not
# listed as mutants: a lower guard subsumes each, so no black-box probe can
# separate them from the reference.
#   record-key-len / record-key-subset: the record fields are exactly
#     variant, digest, snapshot_fen, and the exact-str guards on those three
#     reject any renamed or missing field.
#   variant-isinstance / fen-isinstance / no-variant-equality: the linked
#     node machinery rejects str subclasses and every non-canonical variant
#     (NodeError -> malformed) before the local check is reached.
#   digest-grammar-match: the derived-digest equality rejects any digest the
#     loosened grammar lets through.
EQUIVALENT_MUTANTS = ("record-key-len", "record-key-subset", "variant-isinstance",
                      "fen-isinstance", "no-variant-equality", "digest-grammar-match")


_SOURCES = ("_exact_dict", "_validate_record", "state_id", "AtomicEditEngine")


def _source_mutant(name, edits):
    """A one-guard edit of the reference engine: the reference record
    validation, state id and engine sources are concatenated; each OLD
    must occur exactly once. The exec namespace raises the BOUND error
    class."""
    src = "\n\n".join(inspect.getsource(getattr(_reference, n))
                      for n in _SOURCES)
    for old, new in edits:
        if src.count(old) != 1:
            raise AssertionError(f"{name}: edit site count {src.count(old)}: {old!r}")
        src = src.replace(old, new)
    namespace = dict(vars(_reference))
    namespace["AtomicEditError"] = AtomicEditError

    def _bound_fail(cls):
        raise AtomicEditError(cls, FAILURE_MAPPING[cls])

    namespace["_fail"] = _bound_fail
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return namespace["AtomicEditEngine"]


_SM = (
    ("exact-dict-isinstance",
     [("return type(obj) is dict and", "return isinstance(obj, dict) and")]),
    ("no-key-guard",
     [("and all(type(k) is str for k in dict.keys(obj))", "")]),
    ("key-guard-isinstance",
     [("all(type(k) is str for k in dict.keys(obj))",
       "all(isinstance(k, str) for k in dict.keys(obj))")]),
    ("request-key-len",
     [('set(request.keys()) != {"base_id", "operations"}', "len(request) != 2")]),
    ("request-key-subset",
     [('set(request.keys()) != {"base_id", "operations"}',
       'not set(request.keys()) <= {"base_id", "operations"}')]),
    ("request-key-superset",
     [('set(request.keys()) != {"base_id", "operations"}',
       'not set(request.keys()) >= {"base_id", "operations"}')]),
    ("base-id-isinstance",
     [('type(request["base_id"]) is not str', 'not isinstance(request["base_id"], str)')]),
    ("base-id-match",
     [('_ID_RE.fullmatch(request["base_id"])', '_ID_RE.match(request["base_id"])')]),
    ("base-id-search",
     [('_ID_RE.fullmatch(request["base_id"])', '_ID_RE.search(request["base_id"])')]),
    ("no-base-id-grammar",
     [(' or \\\n                _ID_RE.fullmatch(request["base_id"]) is None', "")]),
    ("operations-isinstance",
     [("if type(operations) is not list:", "if not isinstance(operations, list):")]),
    ("no-operations-type",
     [("if type(operations) is not list:", "if False:")]),
    ("op-key-len",
     [("if not _exact_dict(op) or set(op.keys()) != \\\n                    _OP_FIELDS:",
       "if not _exact_dict(op) or len(op) != len(_OP_FIELDS):")]),
    ("op-key-subset",
     [("if not _exact_dict(op) or set(op.keys()) != \\\n                    _OP_FIELDS:",
       "if not _exact_dict(op) or not set(op.keys()) <= _OP_FIELDS:")]),
    ("op-key-superset",
     [("if not _exact_dict(op) or set(op.keys()) != \\\n                    _OP_FIELDS:",
       "if not _exact_dict(op) or not set(op.keys()) >= _OP_FIELDS:")]),
    ("kind-isinstance",
     [('if type(op["kind"]) is not str or', 'if not isinstance(op["kind"], str) or')]),
    ("no-kind-type",
     [('if type(op["kind"]) is not str or \\\n                    op["kind"] not in _OP_KINDS:',
       'if op["kind"] not in _OP_KINDS:')]),
    ("no-kind-membership",
     [('if type(op["kind"]) is not str or \\\n                    op["kind"] not in _OP_KINDS:',
       'if type(op["kind"]) is not str:')]),
    ("identity-isinstance",
     [('if type(op["identity"]) is not str:', 'if not isinstance(op["identity"], str):')]),
    ("no-identity-type",
     [('if type(op["identity"]) is not str:', "if False:")]),
    ("no-uniqueness",
     [('            if op["identity"] in seen:', "            if False:")]),
    ("uniqueness-before-identity-type",
     [('            if type(op["identity"]) is not str:\n'
       '                _fail("malformed_edit_record")\n'
       '            if op["identity"] in seen:\n',
       '            if op["identity"] in seen:\n'
       '                _fail("malformed_edit_record")\n'
       '            if type(op["identity"]) is not str:\n')]),
    ("delete-record-falsy",
     [('if op["record"] is not None:', 'if op["record"]:')]),
    ("no-delete-record-check",
     [('if op["record"] is not None:', "if False:")]),
    ("no-put-identity-binding",
     [('if _identity(op["record"]) != op["identity"]:', "if False:")]),
    ("no-put-record-validation",
     [('                _validate_record(op["record"])\n', "")]),
    ("record-key-superset",
     [('if set(rec.keys()) != set(_NDOCS[0]["record"]["fields"]):',
       'if not set(rec.keys()) >= set(_NDOCS[0]["record"]["fields"]):')]),
    ("digest-isinstance",
     [('type(rec.get("digest")) is not str', 'not isinstance(rec.get("digest"), str)')]),
    ("no-fen-equality",
     [('rec["snapshot_fen"] != derived["snapshot_fen"]', "False")]),
    ("no-digest-equality",
     [('if rec["digest"] != derived["digest"]:', "if False:")]),
    ("base-key-isinstance",
     [("        if not _exact_dict(base):\n", "        if type(base) is not dict:\n"),
      ("            if type(key) is not str:\n", "            if not isinstance(key, str):\n")]),
    ("no-base-record-validation",
     [("            _validate_record(rec)\n", "")]),
    ("no-base-identity-binding",
     [("            if _identity(rec) != key:", "            if False:")]),
    ("no-base-check",
     [('if state_id(base) != request["base_id"]:', "if False:")]),
    ("staged-is-base",
     [("staged = copy.deepcopy(base)", "staged = base")]),
    ("staged-shallow",
     [("staged = copy.deepcopy(base)", "staged = dict(base)")]),
    ("put-not-copied",
     [('staged[op["identity"]] = copy.deepcopy(\n                    op["record"])',
       'staged[op["identity"]] = op["record"]')]),
    ("unknown-delete-allowed",
     [('                if op["identity"] not in staged:\n'
       '                    _fail("unknown_identity")\n'
       '                del staged[op["identity"]]',
       '                staged.pop(op["identity"], None)')]),
    ("state-id-keys-unsorted",
     [("    for key in sorted(state):", "    for key in state:")]),
    ("state-id-fields-unsorted",
     [("for field in sorted(rec))", "for field in rec)")]),
    ("edit-id-ops-unsorted",
     [("            for op in sorted(operations,\n"
       "                             key=lambda op: op[\"identity\"]))",
       "            for op in operations)")]),
    ("edit-id-drops-target",
     [('f"{request[\'base_id\']}\\n{target_id}\\n"', 'f"{request[\'base_id\']}\\n"')]),
    ("edit-id-drops-kind",
     [("f\"{op['kind']}:{op['identity']}\"", "f\"{op['identity']}\"")]),
)
_SOURCE_MUTANTS = {name: _source_mutant(name, edits) for name, edits in _SM}

MUTANTS = {
    "accepts-all": AcceptsAll,
    "wrong-code": WrongCode,
    "malformed-as-conflicting": _remap(MER, CB),
    "conflicting-as-malformed": _remap(CB, MER),
    "unknown-as-malformed": _remap(UI, MER),
    "unknown-as-conflicting": _remap(UI, CB),
    "foreign-error-class": ForeignError,
    "commits-into-base": CommitsIntoBase,
    "partial-stage-on-reject": PartialStageOnReject,
    "state-shares-base-records": StateSharesBaseRecords,
    "state-shares-put-records": StateSharesPutRecords,
    "mutates-request-on-success": MutatesRequestOnSuccess,
    "normalizes-base-key-order": NormalizesBaseKeyOrder,
    "cached-result": CachedResult,
    "stale-edit-id": StaleEditId,
    "receipt-field-order": ReceiptFieldOrder,
    "str-subclass-edit-id": StrSubclassEditId,
    "target-from-base": TargetFromBase,
    "raw-request-peek": RawRequestPeek,
    "raw-kind-peek": RawKindPeek,
    "raw-base-key-sort": RawBaseKeySort,
    **_SOURCE_MUTANTS,
}

MUTANT_TARGETS = {
    "accepts-all": "malformed:request_not_a_dict",
    "wrong-code": "malformed:request_not_a_dict",
    "malformed-as-conflicting": "malformed:request_not_a_dict",
    "conflicting-as-malformed": "malformed:base_id_names_another_state",
    "unknown-as-malformed": "malformed:delete_absent_identity",
    "unknown-as-conflicting": "malformed:delete_absent_identity",
    "foreign-error-class": "malformed:request_not_a_dict",
    "commits-into-base": "happy:put_inserts_into_nonempty_base",
    "partial-stage-on-reject": "rollback:last_operation_unknown_identity",
    "state-shares-base-records": "happy:put_inserts_into_nonempty_base",
    "state-shares-put-records": "happy:put_inserts_into_nonempty_base",
    "mutates-request-on-success": "happy:mixed_multi_operation_edit",
    "normalizes-base-key-order": "totality:accept-base-keys-reversed",
    "cached-result": "happy:put_inserts_into_nonempty_base",
    "stale-edit-id": "happy:put_inserts_into_nonempty_base",
    "receipt-field-order": "happy:put_inserts_into_nonempty_base",
    "str-subclass-edit-id": "happy:put_inserts_into_nonempty_base",
    "target-from-base": "happy:put_inserts_into_nonempty_base",
    "raw-request-peek": "totality:request-colliding-key",
    "raw-kind-peek": "totality:put-kind-str-subclass",
    "raw-base-key-sort": "totality:base-str-subclass-key",
    "exact-dict-isinstance": "totality:request-plain-dict-subclass",
    "no-key-guard": "totality:request-colliding-key",
    "key-guard-isinstance": "totality:request-str-subclass-key",
    "request-key-len": "totality:request-renamed-operations-same-arity",
    "request-key-subset": "totality:request-missing-operations",
    "request-key-superset": "totality:request-extra-key",
    "base-id-isinstance": "totality:base-id-str-subclass",
    "base-id-match": "totality:base-id-trailing-newline",
    "base-id-search": "totality:base-id-trailing-newline",
    "no-base-id-grammar": "totality:base-id-upper",
    "operations-isinstance": "totality:operations-list-subclass",
    "no-operations-type": "totality:operations-empty-str",
    "op-key-len": "totality:put-op-renamed-record-same-arity",
    "op-key-subset": "totality:put-op-missing-record",
    "op-key-superset": "totality:put-op-extra-key",
    "kind-isinstance": "totality:put-kind-str-subclass",
    "no-kind-type": "totality:put-kind-colliding-non-str",
    "no-kind-membership": "totality:put-kind-upsert",
    "identity-isinstance": "totality:delete-identity-str-subclass",
    "no-identity-type": "totality:delete-identity-int",
    "no-uniqueness": "totality:duplicate-put",
    "uniqueness-before-identity-type": "totality:delete-identity-colliding-non-str",
    "delete-record-falsy": "totality:delete-record-empty-dict",
    "no-delete-record-check": "totality:delete-record-record",
    "no-put-identity-binding": "totality:put-identity-of-other-record",
    "no-put-record-validation": "totality:put-record-wrong-digest",
    "record-key-superset": "totality:put-record-extra-field",
    "digest-isinstance": "totality:put-record-digest-str-subclass",
    "no-fen-equality": "totality:put-record-fen-non-canonical-clocks",
    "no-digest-equality": "totality:put-record-wrong-digest",
    "base-key-isinstance": "totality:base-str-subclass-key",
    "no-base-record-validation": "totality:base-record-wrong-digest",
    "no-base-identity-binding": "totality:base-key-swapped",
    "no-base-check": "totality:base-id-of-another-state",
    "staged-is-base": "happy:put_inserts_into_nonempty_base",
    "staged-shallow": "happy:put_inserts_into_nonempty_base",
    "put-not-copied": "happy:put_inserts_into_nonempty_base",
    "unknown-delete-allowed": "totality:delete-absent-identity",
    "state-id-keys-unsorted": "totality:accept-base-keys-reversed",
    "state-id-fields-unsorted": "totality:accept-base-record-keys-reversed",
    "edit-id-ops-unsorted": "boundary:operation_order_reversed",
    "edit-id-drops-target": "happy:put_inserts_into_nonempty_base",
    "edit-id-drops-kind": "happy:put_inserts_into_nonempty_base",
}


# -- fixture closure kill-proof ----------------------------------------------------
def _section_of(row):
    for section, manifest in MANIFESTS.items():
        if row["name"] in manifest:
            return section
    raise AssertionError(f"row {row.get('name')!r} has no section")


def _canon(row):
    """Type-exact row identity (True never equals 1 here)."""
    return json.dumps(row, sort_keys=True)


def _erasures(section, row):
    """Single-edge erasures of ROW (pinned receipts regenerated where
    the edit still commits, so only the semantic checks can kill it).
    An erasure equal to its row is dropped."""
    out = []

    def regen(m):
        with contextlib.suppress(_reference.AtomicEditError):
            m["expect"] = _reference.AtomicEditEngine().apply(
                copy.deepcopy(m["request"]), copy.deepcopy(m["base"]))
        return m

    if section in ("happy", "boundary"):
        if row["request"]["operations"]:
            dropped = copy.deepcopy(row)
            dropped["request"]["operations"].pop()
            out.append(("last-op-dropped", regen(dropped)))
            rev = copy.deepcopy(row)
            rev["request"]["operations"].reverse()
            out.append(("ops-reversed", regen(rev)))
        grown = copy.deepcopy(row)
        present = set(grown["base"]) | {op["identity"] for op in
                                        grown["request"]["operations"]}
        extra = next((i for i in sorted(RECORDS) if i not in present), None)
        if extra is not None:
            grown["request"]["operations"].append(_put(RECORDS[extra]))
            out.append(("op-added", regen(grown)))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        (locus, value), = row["minimal_repair"].items()
        fixed[locus] = copy.deepcopy(value)
        out.append(("defect-repaired", fixed))
        other = copy.deepcopy(row)
        other["minimal_repair"] = {"base" if locus == "request" else "request":
                                   copy.deepcopy(value)}
        out.append(("repair-locus-flipped", other))
    else:
        repeated = copy.deepcopy(row)
        repeated["follow_up"] = copy.deepcopy(row["request"])
        out.append(("follow-up-repeats-rejected-request", repeated))
        tame = copy.deepcopy(row)
        tame["request"] = copy.deepcopy(row["follow_up"])
        out.append(("rejected-request-tamed", tame))
    return [(label, m) for label, m in out if _canon(m) != _canon(row)]


def _substitution_mutants():
    out = []
    labels = [(s, r["name"]) for s in MANIFESTS for r in CASES[s]]
    for sa, na in labels:
        for sb, nb in labels:
            if (sa, na) == (sb, nb):
                continue
            m = copy.deepcopy(CASES)
            i = [r["name"] for r in m[sa]].index(na)
            src = next(r for r in CASES[sb] if r["name"] == nb)
            m[sa][i] = dict(copy.deepcopy(src), name=na)
            out.append((f"payload:{sa}:{na}<-{sb}:{nb}", m))
    for section in MANIFESTS:
        rows = CASES[section]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                m = copy.deepcopy(CASES)
                m[section][i]["name"], m[section][j]["name"] = \
                    rows[j]["name"], rows[i]["name"]
                out.append((f"name-swap:{section}:{i}:{j}", m))
        for label, fn in (("reversed", lambda r: r.reverse()),
                          ("dropped", lambda r: r.pop()),
                          ("duplicated", lambda r: r.append(copy.deepcopy(r[0])))):
            m = copy.deepcopy(CASES)
            fn(m[section])
            out.append((f"{label}:{section}", m))
        for i, row in enumerate(CASES[section]):
            for label, mutant in _erasures(section, row):
                m = copy.deepcopy(CASES)
                m[section][i] = mutant
                out.append((f"erased:{section}:{row['name']}:{label}", m))
    return out


def _check_pins(cases):
    """Local semantic pins over the ORIGINAL rows: every success row's
    request names its own base and its pinned receipt is the local
    derivation; every rollback follow-up names the same base and
    commits as pinned."""
    for section in ("happy", "boundary"):
        for row in cases[section]:
            assert row["request"]["base_id"] == _sid(row["base"]), row["name"]
            assert _derive(row["request"], row["base"]) == row["expect"], row["name"]
    for row in cases["rollback"]:
        assert row["follow_up"]["base_id"] == _sid(row["base"]), row["name"]
        assert _derive(row["follow_up"], row["base"]) == row["expect"], row["name"]


def _closure(cases):
    """T0204 structure, ordered manifests, whole-row digests and per-name
    semantic checks, plus the local pins above."""
    _fixture._validate_structure(cases)
    _fixture._validate_closure(cases)
    _check_pins(cases)


# -- tests -----------------------------------------------------------------------
def test_closure():
    _closure(CASES)
    assert set(ROW_DIGESTS) == {f"{s}:{r['name']}" for s in MANIFESTS
                                for r in CASES[s]}


def test_failure_mapping_is_the_closed_contract_enum():
    assert set(FAILURE_MAPPING) == {MER, CB, UI}
    assert set(FAILURE_MAPPING.values()) <= set(ERROR_ENUM)


def test_no_equivalent_payload_pairs():
    rows = [{k: v for k, v in r.items() if k != "name"}
            for s in MANIFESTS for r in CASES[s]]
    assert all(a != b for i, a in enumerate(rows) for b in rows[i + 1:])


def test_probe_manifest_closed_and_ordered():
    assert tuple(PROBES) == PROBE_MANIFEST
    assert len(PROBE_MANIFEST) == PROBE_COUNT
    assert {f for _, f in PROBES.values()} == {MER, CB, UI, ACCEPT}


def test_probe_expects_are_closed_and_locally_derived():
    """PROBE_EXPECT covers exactly the accept probes; every pinned
    receipt is the LOCAL derivation, its ids are in grammar and its
    state holds only known records."""
    accepts = [n for n, (_, f) in PROBES.items() if f == ACCEPT]
    assert list(PROBE_EXPECT) == accepts
    for name in accepts:
        receipt = PROBE_EXPECT[name]
        assert _reference._EID_RE.fullmatch(receipt["edit_id"]), name
        assert _reference._ID_RE.fullmatch(receipt["target_id"]), name
        assert all(RECORDS[k] == v for k, v in receipt["state"].items()), name
    # order-insensitive probes share the good edit's receipt
    for name in ("accept-good-edit", "accept-operations-reversed",
                 "accept-base-keys-reversed", "accept-base-record-keys-reversed",
                 "accept-put-record-keys-reversed", "accept-op-keys-reversed",
                 "accept-request-keys-reversed"):
        assert PROBE_EXPECT[name] == GOOD_EXPECT, name


def test_identity_source_mutant_is_green_under_current_binding():
    """An unedited reference-source mutant passes the whole battery, so a
    source mutant dies only for its edit."""
    assert _probe(_source_mutant("identity", [])) == []


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(AtomicEditEngine, executed=executed) == []
    assert executed == [f"{s}:{n}" for s, man in MANIFESTS.items()
                        for n in man] + [f"totality:{n}" for n in PROBE_MANIFEST]


def test_mutant_targets_closed():
    assert set(MUTANT_TARGETS) == set(MUTANTS)
    labels = {f"{s}:{n}" for s, man in MANIFESTS.items() for n in man} | {
        f"totality:{n}" for n in PROBE_MANIFEST}
    assert set(MUTANT_TARGETS.values()) <= labels


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_red_on_its_exact_target(name):
    label = MUTANT_TARGETS[name]
    failures = _probe(MUTANTS[name], only={label})
    assert [":".join(f.split(":")[:2]) for f in failures] == [label], \
        (name, failures)


def test_reference_green_on_every_mutant_target():
    assert _probe(AtomicEditEngine, only=set(MUTANT_TARGETS.values())) == []


def test_every_row_has_a_real_erasure():
    for section in MANIFESTS:
        for row in CASES[section]:
            erasures = _erasures(section, row)
            assert erasures, (section, row["name"])


@pytest.mark.parametrize("with_digests", [True, False],
                         ids=["with-digests", "digests-neutralized"])
def test_closure_kills_substitution_mutants(with_digests, monkeypatch):
    """Every cross-row payload substitution, rename, reorder, drop,
    duplicate and single-edge erasure is killed by the T0204 closure
    validator with the digest table live AND with its _row_digest
    monkeypatched to return the table value (digests neutralized)."""
    if not with_digests:
        monkeypatch.setattr(
            _fixture, "_row_digest",
            lambda row: ROW_DIGESTS.get(f"{_section_of(row)}:{row['name']}", ""))
    mutants = _substitution_mutants()
    assert len(mutants) > 1000
    survivors = []
    for label, m in mutants:
        try:
            _closure(m)
        except (AssertionError, ValueError, KeyError, TypeError,
                AttributeError, IndexError):
            continue
        survivors.append(label)
    assert survivors == [], survivors


def test_equivalent_mutants_documented_not_listed():
    assert not set(EQUIVALENT_MUTANTS) & set(MUTANTS)
    assert set(MUTANT_TARGETS) == set(MUTANTS)
