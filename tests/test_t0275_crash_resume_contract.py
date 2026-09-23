"""T0275: store crash-resume contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/crash_resume.yaml:
verified torn-tail recovery of a WAL-logged store after a crash. The
surviving log is the LONGEST prefix that passes the LINKED WAL
validation and replay (imported, never restated). Every entry at or
before the durable checkpoint must lie in that prefix, otherwise the
crash destroyed acknowledged data (corrupt_source). Everything from
the first invalid entry onward is the torn tail, even entries that
look valid again later. The tail goes EXACTLY ONCE to the UNTRUSTED
quarantine sink (detached copy, frozen log and request), whose token
must equal the LOCAL canonical tail serialization byte-for-byte.
Only then is the torn tail removed. A rejected resume leaves every
input bit-identical, reference-preserving.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
)
from tests.test_t0194_migration_contract import (  # noqa: E402
    state_id,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    WalEngine,
    WalError,
    _log_of,
    canonical_payload,
)
from tools.crash_resume_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_DIGITS,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_REQUEST_FIELDS = _CC["request"]["fields"]
_RESUME_RE = re.compile(_CC["identifiers"]["resume_id"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["head"]["grammar"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
_TOKEN_RE = re.compile(_CC["identifiers"]["quarantine_token"]["grammar"])

_WAL = WalEngine(canonical_payload)  # linked, trusted


class ResumeError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise ResumeError(cls, FAILURE_MAPPING[cls])


def _is_canonical_json_recursive(obj):
    """The ORIGINAL recursive checker - kept only so the mutant test
    can show it escapes raw on hostile input."""
    kind = type(obj)
    if obj is None or kind in (bool, int):
        return True
    if kind is float:
        return math.isfinite(obj)
    if kind is str:
        try:
            obj.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    if kind is list:
        return all(_is_canonical_json_recursive(item) for item in obj)
    if kind is dict:
        return all(
            type(k) is str and _is_canonical_json_recursive(k) and _is_canonical_json_recursive(v)
            for k, v in obj.items()
        )
    return False


# every int below 2**_MAX_INT_BITS has at most MAX_INT_DIGITS + 1 decimal
# digits (13288 bits -> <= 4001 digits), safely under the interpreter's
# str() limit; the exact digit count below then decides inclusively
_MAX_INT_BITS = math.ceil(MAX_INT_DIGITS * math.log2(10))


def _scalar_ok(obj):
    kind = type(obj)
    if obj is None or kind is bool:
        return True
    if kind is int:
        # bounded decimal form: never hits the interpreter's int/str
        # conversion limit (a raw ValueError) - bit_length bound first
        # a pre-screen only: the exact decimal count decides
        if obj.bit_length() > _MAX_INT_BITS:
            return False
        try:
            return len(str(abs(obj))) <= MAX_INT_DIGITS
        except ValueError:
            return False
    if kind is float:
        return math.isfinite(obj)
    if kind is str:
        try:
            obj.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    return False


def _is_canonical_json(obj):
    """TOTAL admission of one discarded entry, walked ITERATIVELY
    with an explicit stack: exact built-in JSON values only (str
    UTF-8 encodable, bounded int, finite float, bool, None, list,
    dict with exact-str keys); any container met twice (a cycle or
    an alias) or nesting deeper than MAX_DEPTH is rejected.
    Never recurses, never raises."""
    seen = set()
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        kind = type(node)
        if kind is list or kind is dict:
            # ALIAS-FREE: any container met twice anywhere (a cycle or
            # a shared sub-tree) is inadmissible - keeps the walk and
            # the canonical encoding linear in the object count
            if depth > MAX_DEPTH or id(node) in seen:
                return False
            seen.add(id(node))
            if kind is dict:
                for key, value in node.items():
                    if type(key) is not str or not _scalar_ok(key):
                        return False
                    stack.append((value, depth + 1))
            else:
                for value in node:
                    stack.append((value, depth + 1))
        elif not _scalar_ok(node):
            return False
    return True


def _canon(entry):
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def quarantine_tail(tail):
    """THE pinned canonical quarantine token: sha256 over the
    DOMAIN-SEPARATED, LENGTH-FRAMED canonical JSON of every discarded
    entry, in order - deterministic in the tail and only the tail."""
    parts = ["qtn1"]
    for entry in tail:
        text = _canon(entry)
        parts.append(f"{len(text)}:{text}")
    return "qtn1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _snapshot(obj, acc, seen):
    """Reference-preserving snapshot of every nested container."""
    if id(obj) in seen:
        return
    if type(obj) is dict:
        seen.add(id(obj))
        acc.append((obj, dict(obj)))
        for value in list(obj.values()):
            _snapshot(value, acc, seen)
    elif type(obj) is list:
        seen.add(id(obj))
        acc.append((obj, list(obj)))
        for value in list(obj):
            _snapshot(value, acc, seen)


def _restore(acc):
    for obj, saved in acc:
        if type(obj) is dict:
            obj.clear()
            obj.update(saved)
        else:
            obj[:] = saved


def _valid_prefix_length(log):
    """Length of the longest prefix passing linked WAL replay. Every
    entry is first PRE-SCREENED by the total admission walk (exact-str
    keys, alias-free, bounded): the prefix is capped at the first
    inadmissible entry, so the linked WAL never sees a hostile object."""
    cap = len(log)
    for index, entry in enumerate(log):
        if not _is_canonical_json(entry):
            cap = index
            break
    for k in range(cap, -1, -1):
        try:
            _WAL.replay(log[:k])
        except WalError:
            continue
        return k
    raise AssertionError("the empty prefix always replays")


class ResumeEngine:
    """The contract's pinned crash resume."""

    def __init__(self, quarantine_sink):
        self.sink = quarantine_sink  # UNTRUSTED

    def _quarantine(self, frozen_tail):
        """THE sink boundary: raising ANY BaseException, non-exact-str
        or wrong-grammar output fails closed as divergent_quarantine."""
        try:
            out = self.sink(copy.deepcopy(frozen_tail))
        except BaseException:
            # fail closed against the FULL BaseException surface
            _fail("divergent_quarantine")
        if type(out) is not str or _TOKEN_RE.fullmatch(out) is None:
            _fail("divergent_quarantine")
        return out

    @staticmethod
    def _derive_resume_id(head, sid, resumed, discarded, token):
        return (
            "rsm1:"
            + hashlib.sha256(f"{head}\n{sid}\n{resumed}\n{discarded}\n{token}".encode()).hexdigest()
        )

    def resume(self, log, request):
        if type(log) is not list:
            _fail("malformed_resume_record")
        if type(request) is not dict:
            _fail("malformed_resume_record")
        # KEY-TYPE GUARD before any set/hash comparison: a hostile key
        # whose __hash__ collides with a field name and whose __eq__
        # raises must fail closed typed, never escape raw
        if not all(type(key) is str for key in dict.keys(request)):
            _fail("malformed_resume_record")
        if set(request.keys()) != set(_REQUEST_FIELDS):
            _fail("malformed_resume_record")
        checkpoint = request["checkpoint_sequence"]
        if type(checkpoint) is not int:
            _fail("malformed_resume_record")
        if checkpoint < 0:
            _fail("unknown_checkpoint")
        if checkpoint > len(log):
            # acknowledged entries are MISSING: the crash truncated
            # durable data - never a resumable torn tail
            _fail("corrupt_source")
        k = _valid_prefix_length(log)
        if k < checkpoint:
            # the crash damaged an acknowledged (durable) entry
            _fail("corrupt_source")
        tail = log[k:]
        if not all(_is_canonical_json(entry) for entry in tail):
            _fail("malformed_resume_record")
        replayed = _WAL.replay(log[:k])
        # FREEZE (detached canonical copies) BEFORE the snapshot and
        # the single sink call; any canonicalization failure is typed.
        try:
            frozen_tail = [json.loads(_canon(entry)) for entry in tail]
        except (ValueError, TypeError, RecursionError):
            _fail("malformed_resume_record")
        acc = []
        _snapshot(log, acc, set())
        _snapshot(request, acc, set())
        try:
            token = self._quarantine(frozen_tail)
            # TAIL-BOUND QUARANTINE: byte-exact to the local derivation
            if token != quarantine_tail(frozen_tail):
                _fail("divergent_quarantine")
        finally:
            _restore(acc)
        discarded = len(frozen_tail)
        # COMMIT LAST: remove exactly the torn tail
        del log[k:]
        return {
            "resume_id": self._derive_resume_id(
                replayed["head"], replayed["state_id"], k, discarded, token
            ),
            "head": replayed["head"],
            "state_id": replayed["state_id"],
            "resumed_count": k,
            "discarded_count": discarded,
            "quarantine_token": token,
        }


def _engine(sink=quarantine_tail):
    return ResumeEngine(sink)


def _req(checkpoint):
    return {"checkpoint_sequence": checkpoint}


def _three():
    return _log_of(("put", STARTPOS), ("put", KINGS), ("put", AFTER_E4))


def _four():
    return _log_of(("put", STARTPOS), ("put", KINGS), ("put", AFTER_E4), ("delete", KINGS))


def _raises(cls, fn, *args):
    with pytest.raises(ResumeError) as err:
        fn(*args)
    assert err.value.failure_class == cls
    assert err.value.code == FAILURE_MAPPING[cls]
    assert err.value.code in ERROR_ENUM


def _torn_forms():
    """Ways a crash tears the entry that follows a clean 3-entry log."""
    nxt = _four()[3]

    def missing_keys():
        return {k: v for k, v in nxt.items() if k != "payload"}

    def fragment():
        return '{"entry_id": "wal1:ab'

    def null():
        return None

    def bad_entry_id():
        e = copy.deepcopy(nxt)
        e["entry_id"] = "wal1:" + "0" * 64
        return e

    def broken_chain():
        e = copy.deepcopy(nxt)
        e["prior_entry_id"] = GENESIS
        return e

    def wrong_sequence():
        e = copy.deepcopy(nxt)
        e["sequence"] = 9
        return e

    def truncated_payload():
        e = copy.deepcopy(nxt)
        e["payload"]["record"] = {}
        return e

    def empty_dict():
        return {}

    return [
        (f.__name__, f)
        for f in (
            missing_keys,
            fragment,
            null,
            bad_entry_id,
            broken_chain,
            wrong_sequence,
            truncated_payload,
            empty_dict,
        )
    ]


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_clean_log_resumes_unchanged():
    log = _three()
    before = copy.deepcopy(log)
    calls = []

    def sink(tail):
        calls.append(tail)
        return quarantine_tail(tail)

    receipt = _engine(sink).resume(log, _req(3))
    assert list(receipt) == _FIELDS
    assert _RESUME_RE.fullmatch(receipt["resume_id"])
    assert _HEAD_RE.fullmatch(receipt["head"])
    assert _STATE_RE.fullmatch(receipt["state_id"])
    assert _TOKEN_RE.fullmatch(receipt["quarantine_token"])
    assert receipt["head"] == log[-1]["entry_id"]
    assert receipt["state_id"] == _WAL.replay(log)["state_id"]
    assert (receipt["resumed_count"], receipt["discarded_count"]) == (3, 0)
    assert receipt["quarantine_token"] == quarantine_tail([])
    assert calls == [[]]  # the sink is called even for an empty tail
    assert log == before


@pytest.mark.parametrize("name,torn", _torn_forms())
def test_torn_tail_is_discarded(name, torn):
    clean = _three()
    log = _three() + [torn()]
    receipt = _engine().resume(log, _req(3))
    assert log == clean, name
    assert receipt["discarded_count"] == 1, name
    assert receipt["resumed_count"] == 3, name
    assert receipt["head"] == clean[-1]["entry_id"], name
    assert receipt["state_id"] == _WAL.replay(clean)["state_id"], name
    assert receipt["quarantine_token"] == quarantine_tail([torn()]), name


def test_everything_after_first_invalid_entry_is_discarded():
    """A valid-looking entry after the torn one is still torn tail."""
    four = _four()
    log = four[:2] + [{"torn": True}] + four[2:]
    receipt = _engine().resume(log, _req(2))
    assert receipt["resumed_count"] == 2
    assert receipt["discarded_count"] == 3
    assert log == four[:2]


def test_checkpoint_zero_whole_log_torn():
    log = [{"entry_id": "wal1:zz"}, "garbage"]
    receipt = _engine().resume(log, _req(0))
    assert log == []
    assert receipt["head"] == GENESIS
    assert receipt["state_id"] == state_id({})
    assert (receipt["resumed_count"], receipt["discarded_count"]) == (0, 2)


def test_empty_log():
    log = []
    receipt = _engine().resume(log, _req(0))
    assert log == []
    assert receipt["head"] == GENESIS
    assert receipt["state_id"] == state_id({})
    assert (receipt["resumed_count"], receipt["discarded_count"]) == (0, 0)


def test_checkpoint_exactly_at_valid_prefix_end():
    log = _three() + ["torn"]
    receipt = _engine().resume(log, _req(3))
    assert receipt["discarded_count"] == 1


def test_idempotent():
    log = _three() + ["torn", None]
    first = _engine().resume(log, _req(3))
    after_first = copy.deepcopy(log)
    second = _engine().resume(log, _req(3))
    assert log == after_first
    assert second["discarded_count"] == 0
    assert (second["head"], second["state_id"]) == (first["head"], first["state_id"])
    assert second["resumed_count"] == first["resumed_count"]


def test_determinism():
    a = _engine().resume(_three() + ["torn"], _req(2))
    b = _engine().resume(_three() + ["torn"], _req(2))
    assert a == b


def test_resume_id_binds_every_field():
    r = _engine().resume(_three() + ["torn"], _req(3))
    args = [
        r["head"],
        r["state_id"],
        r["resumed_count"],
        r["discarded_count"],
        r["quarantine_token"],
    ]
    base = ResumeEngine._derive_resume_id(*args)
    assert base == r["resume_id"]
    swaps = [GENESIS, state_id({}), 2, 0, quarantine_tail([])]
    for i, other in enumerate(swaps):
        mutated = list(args)
        mutated[i] = other
        assert ResumeEngine._derive_resume_id(*mutated) != base, i


def test_quarantine_token_binds_every_discarded_entry():
    base = quarantine_tail(["a", {"b": 1}])
    for other in (
        ["a"],
        [{"b": 1}, "a"],
        ["a", {"b": 2}],
        ["a", {"b": 1}, None],
        ["a", {"b": 1.0}],
        ["a", {"b": True}],
    ):
        assert quarantine_tail(other) != base, other


# -- durability, checkpoints and requests ---------------------------------------


def _damage(log, index):
    record = log[index]["payload"]["record"]
    record["snapshot_fen"] = KINGS if record["snapshot_fen"] == STARTPOS else STARTPOS


@pytest.mark.parametrize("index", [0, 1, 2])
def test_damaged_durable_entry_is_corrupt_source(index):
    log = _three()
    _damage(log, index)
    before = copy.deepcopy(log)
    _raises("corrupt_source", _engine().resume, log, _req(3))
    assert log == before


@pytest.mark.parametrize("length,checkpoint", [(2, 3), (3, 4), (0, 1), (3, 99)])
def test_truncated_durable_log_is_corrupt_source(length, checkpoint):
    """A log shorter than its checkpoint lost acknowledged entries."""
    log = _three()[:length]
    before = copy.deepcopy(log)
    _raises("corrupt_source", _engine().resume, log, _req(checkpoint))
    assert log == before


def test_damage_after_checkpoint_is_torn_tail():
    log = _three()
    _damage(log, 1)
    receipt = _engine().resume(log, _req(1))
    assert receipt["discarded_count"] == 2
    assert len(log) == 1


@pytest.mark.parametrize("checkpoint", [-1, -99])
def test_unknown_checkpoint(checkpoint):
    log = _three()
    before = copy.deepcopy(log)
    _raises("unknown_checkpoint", _engine().resume, log, _req(checkpoint))
    assert log == before


class _IntSub(int):
    pass


@pytest.mark.parametrize(
    "req",
    [
        None,
        [],
        3,
        {},
        {"checkpoint": 3},
        {"checkpoint_sequence": 3, "extra": 1},
        {"checkpoint_sequence": True},
        {"checkpoint_sequence": "3"},
        {"checkpoint_sequence": 3.0},
        {"checkpoint_sequence": None},
        {"checkpoint_sequence": _IntSub(3)},
        {"checkpoint_sequence": [3]},
    ],
)
def test_total_over_hostile_requests(req):
    _raises("malformed_resume_record", _engine().resume, _three(), req)


@pytest.mark.parametrize("log", [None, {}, "log", 3, (1, 2)])
def test_total_over_hostile_log_types(log):
    _raises("malformed_resume_record", _engine().resume, log, _req(0))


class _StrSub(str):
    pass


def _cyclic_list():
    x = []
    x.append(x)
    return x


def _cyclic_dict():
    x = {}
    x["self"] = x
    return x


def _nested_list(depth):
    x = []
    for _ in range(depth - 1):
        x = [x]
    return x


def _hostile_structures():
    return [
        ("cyclic_list", _cyclic_list),
        ("cyclic_dict", _cyclic_dict),
        ("nested_past_bound", lambda: _nested_list(MAX_DEPTH + 1)),
        ("nested_5000", lambda: _nested_list(5000)),
        ("int_10_pow_5000", lambda: 10**5000),
        ("int_past_digit_bound", lambda: 10**MAX_INT_DIGITS),
        ("int_10_pow_4001", lambda: 10**4001),
        ("int_10_pow_4301", lambda: 10**4301),
        ("int_10_pow_4500", lambda: 10**4500),
        ("negative_int_10_pow_4301", lambda: -(10**4301)),
        ("cycle_inside_entry", lambda: {"torn": [1, _cyclic_dict()]}),
    ]


@pytest.mark.parametrize("name,build", _hostile_structures())
def test_hostile_structure_torn_entry_is_typed_malformed(name, build):
    torn = build()
    log = _three() + [torn]
    container = list(log)
    _raises("malformed_resume_record", _engine().resume, log, _req(3))
    assert len(log) == 4, name
    assert all(a is b for a, b in zip(log, container, strict=True)), name
    assert log[3] is torn, name


def test_admission_bounds_are_inclusive_and_pinned():
    assert _MAX_INT_BITS == 13288 == (10**MAX_INT_DIGITS - 1).bit_length()
    assert len(str(2 ** (_MAX_INT_BITS - 1))) == MAX_INT_DIGITS
    assert len(str(2**_MAX_INT_BITS)) == MAX_INT_DIGITS + 1
    semantics = _CC["semantics"]["admission"]
    assert f"depth-at-most-{MAX_DEPTH}" in semantics
    assert f"digits-at-most-{MAX_INT_DIGITS}" in semantics
    for torn in (
        _nested_list(MAX_DEPTH),
        10 ** (MAX_INT_DIGITS - 1),
        -(10 ** (MAX_INT_DIGITS - 1)),
        10**MAX_INT_DIGITS - 1,
        -(10**MAX_INT_DIGITS - 1),
        2 ** (_MAX_INT_BITS - 1),
        -(2 ** (_MAX_INT_BITS - 1)),
    ):
        log = _three() + [torn]
        receipt = _engine().resume(log, _req(3))
        assert receipt["discarded_count"] == 1
        assert len(log) == 3


def _aliased_dag(depth):
    a = []
    for _ in range(depth):
        a = [a, a]
    return a


def test_aliased_containers_are_rejected_quickly():
    import time

    shared = {"k": 1}
    for torn in ({"a": shared, "b": [shared]}, _aliased_dag(24)):
        log = _three() + [torn]
        start = time.perf_counter()
        _raises("malformed_resume_record", _engine().resume, log, _req(3))
        assert time.perf_counter() - start < 2.0
        assert len(log) == 4 and log[3] is torn


def test_mutant_path_only_set_accepts_aliases():
    """The path-only checker (cycles only) admits an aliased DAG whose
    encoding is exponential in its object count."""

    def path_only(obj):
        path, stack = set(), [(obj, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                path.discard(id(node))
                continue
            if type(node) is list:
                if id(node) in path:
                    return False
                path.add(id(node))
                stack.append((node, True))
                stack.extend((v, False) for v in node)
        return True

    dag = _aliased_dag(12)
    assert path_only(dag)
    assert len(_canon(dag)) > 2**12  # 13 objects, exponential encoding
    assert not _is_canonical_json(dag)


def test_equal_but_distinct_containers_are_admitted():
    torn = {"a": {"k": 1}, "b": [{"k": 1}, {"k": 1}]}
    log = _three() + [torn]
    assert _engine().resume(log, _req(3))["discarded_count"] == 1


def test_mutant_recursive_checker_escapes_raw():
    """The old recursive admission escapes raw on hostile structures;
    the pinned engine returns a typed failure for every one."""
    escaped = 0
    for _name, build in _hostile_structures():
        torn = build()
        try:
            ok = _is_canonical_json_recursive(torn)
            if ok:
                _canon(torn)
        except (RecursionError, ValueError):
            escaped += 1
        log = _three() + [build()]
        _raises("malformed_resume_record", _engine().resume, log, _req(3))
    assert escaped >= 4


@pytest.mark.parametrize(
    "torn",
    [
        object(),
        float("nan"),
        float("inf"),
        {1: "x"},
        b"bytes",
        {1, 2},
        (1, 2),
        _StrSub("x"),
        "\ud800",
        {"k": [object()]},
        _IntSub(1),
    ],
    ids=lambda t: type(t).__name__,
)
def test_non_canonical_torn_entry_is_malformed(torn):
    log = _three() + [torn]
    container = list(log)
    _raises("malformed_resume_record", _engine().resume, log, _req(3))
    assert len(log) == 4
    assert all(a is b for a, b in zip(log, container, strict=True))


# -- hostile sinks ----------------------------------------------------------------


def _hostile_sinks():
    def raising(tail):
        raise RuntimeError("boom")

    def raising_keyboard_interrupt(tail):
        raise KeyboardInterrupt

    def raising_system_exit(tail):
        raise SystemExit(1)

    def raising_generator_exit(tail):
        raise GeneratorExit

    def none(tail):
        return None

    def as_bytes(tail):
        return quarantine_tail(tail).encode()

    def str_subclass(tail):
        return _StrSub(quarantine_tail(tail))

    def bad_grammar(tail):
        return "qtn1:zz"

    def upper_hex(tail):
        return quarantine_tail(tail).upper()

    def constant_valid_token(tail):
        return "qtn1:" + "f" * 64

    def empty_tail_token(tail):
        return quarantine_tail([])

    def partial_tail_token(tail):
        return quarantine_tail(tail[:-1])

    def reordered_tail_token(tail):
        return quarantine_tail(list(reversed(tail)))

    return [
        (f.__name__, f)
        for f in (
            raising,
            raising_keyboard_interrupt,
            raising_system_exit,
            raising_generator_exit,
            none,
            as_bytes,
            str_subclass,
            bad_grammar,
            upper_hex,
            constant_valid_token,
            empty_tail_token,
            partial_tail_token,
            reordered_tail_token,
        )
    ]


@pytest.mark.parametrize("name,sink", _hostile_sinks())
def test_hostile_sink(name, sink):
    log = _three() + ["torn-a", {"torn": "b"}]
    req = _req(3)
    before_log, before_req = copy.deepcopy(log), copy.deepcopy(req)
    _raises("divergent_quarantine", _engine(sink).resume, log, req)
    assert log == before_log, name
    assert req == before_req, name


def test_stateful_alternating_sink_never_commits_divergence():
    calls = {"n": 0}

    def alternating(tail):
        calls["n"] += 1
        return quarantine_tail(tail) if calls["n"] % 2 else "qtn1:" + "0" * 64

    engine = _engine(alternating)
    log = _three() + ["torn"]
    engine.resume(log, _req(3))
    assert len(log) == 3
    log = _three() + ["torn"]
    _raises("divergent_quarantine", engine.resume, log, _req(3))
    assert len(log) == 4


def test_one_sink_call_per_resume_and_none_on_early_rejection():
    calls = []

    def counting(tail):
        calls.append(len(tail))
        return quarantine_tail(tail)

    _engine(counting).resume(_three() + ["t"], _req(3))
    assert calls == [1]
    calls.clear()
    damaged = _three()
    _damage(damaged, 0)
    for log, req in (
        (_three(), _req(9)),
        (_three(), {"x": 1}),
        (damaged, _req(3)),
        (_three() + [object()], _req(3)),
    ):
        with pytest.raises(ResumeError):
            _engine(counting).resume(log, req)
    assert calls == []


def test_sink_mutating_its_argument_is_inert():
    def mutating(tail):
        token = quarantine_tail(tail)
        tail[0]["torn"] = "evil"
        tail.clear()
        return token

    log = _three() + [{"torn": "x"}]
    receipt = _engine(mutating).resume(log, _req(3))
    assert receipt["quarantine_token"] == quarantine_tail([{"torn": "x"}])
    assert len(log) == 3


def test_sink_mutating_log_and_request_during_call():
    log = _three() + ["torn"]
    req = _req(3)
    clean = _three()

    def meddling(tail):
        token = quarantine_tail(tail)
        log[0]["payload"]["record"]["snapshot_fen"] = "evil"
        log.insert(0, "junk")
        req["checkpoint_sequence"] = 0
        req["extra"] = 1
        return token

    receipt = _engine(meddling).resume(log, req)
    assert log == clean
    assert req == _req(3)
    assert receipt["discarded_count"] == 1


def test_rejected_resume_is_reference_preserving():
    log = _three() + [{"torn": {"deep": [1]}}]
    req = _req(3)
    refs = [id(e) for e in log]
    payload_refs = [id(e["payload"]) for e in log[:3]]
    deep = log[3]["torn"]
    before_log, before_req = copy.deepcopy(log), copy.deepcopy(req)

    def meddle_then_raise(tail):
        log[0]["payload"] = {}
        log[3]["torn"]["deep"].append(2)
        log.clear()
        req.clear()
        raise ValueError("late")

    _raises("divergent_quarantine", _engine(meddle_then_raise).resume, log, req)
    assert log == before_log
    assert req == before_req
    assert [id(e) for e in log] == refs
    assert [id(e["payload"]) for e in log[:3]] == payload_refs
    assert log[3]["torn"] is deep


# -- mutants the battery must kill ---------------------------------------------


def test_mutant_commit_before_quarantine():
    def mutant_resume(engine, log, request):
        k = _valid_prefix_length(log)
        tail = log[k:]
        del log[k:]  # commit FIRST
        return engine._quarantine(tail)

    def raising(tail):
        raise RuntimeError

    log = _three() + ["torn"]
    with pytest.raises(ResumeError):
        mutant_resume(_engine(raising), log, _req(3))
    assert len(log) == 3  # the mutant lost the tail without quarantine
    log = _three() + ["torn"]
    _raises("divergent_quarantine", _engine(raising).resume, log, _req(3))
    assert len(log) == 4


def test_mutant_skipping_durability_check():
    def mutant_resume(log, request):
        k = _valid_prefix_length(log)
        del log[k:]
        return k

    log = _three()
    _damage(log, 1)
    assert mutant_resume(copy.deepcopy(log), _req(3)) == 1  # durable data lost
    _raises("corrupt_source", _engine().resume, log, _req(3))


def test_mutant_discarding_only_invalid_entries():
    """A mutant that keeps valid-looking entries after the tear
    breaks the chain; the pinned engine discards the whole suffix."""
    four = _four()
    log = four[:2] + ["torn"] + four[2:]
    kept = [e for e in log if isinstance(e, dict)]
    assert kept == four  # the mutant's survivor looks clean...
    _engine().resume(log, _req(2))
    assert log == four[:2]  # ...but the engine never splices over a tear


def test_mutant_grammar_only_token_validation():
    def mutant_quarantine(engine, tail):
        return engine._quarantine(tail)

    def constant(tail):
        return "qtn1:" + "a" * 64

    assert mutant_quarantine(_engine(constant), ["torn"]) == "qtn1:" + "a" * 64
    _raises("divergent_quarantine", _engine(constant).resume, _three() + ["torn"], _req(3))


def test_mutant_live_request_reread_after_sink():
    req = _req(3)

    def switching(tail):
        req["checkpoint_sequence"] = 0
        return quarantine_tail(tail)

    log = _three() + ["torn"]
    receipt = _engine(switching).resume(log, req)
    assert req == _req(3)
    assert receipt["resumed_count"] == 3
    assert len(log) == 3


# -- lint mutants ---------------------------------------------------------------


def _mutants():
    doc = yaml.safe_load(CONTRACT.read_text())
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    c = ["contract"]
    add("role kind drift", c + ["role", "kind"], "best-effort-recovery")
    add("not_scope drift", c + ["role", "not_scope"], "restore-owned-here")
    add("record fields drift", c + ["record", "fields"], ["resume_id"])
    add("record exact drift", c + ["record", "exact"], False)
    add("request fields drift", c + ["request", "fields"], ["checkpoint_sequence", "force"])
    add("request exact drift", c + ["request", "exact"], False)
    add("resume id grammar drift", c + ["identifiers", "resume_id", "grammar"], "^.*$")
    add("resume id supplied", c + ["identifiers", "resume_id", "source"], "caller-supplied")
    add("head grammar drift", c + ["identifiers", "head", "grammar"], "^.*$")
    add("state id grammar drift", c + ["identifiers", "state_id", "grammar"], "^.*$")
    add("token grammar drift", c + ["identifiers", "quarantine_token", "grammar"], "^.*$")
    add("token trusted", c + ["identifiers", "quarantine_token", "source"], "sink-always-honest")
    add("prefix resolution drift", c + ["semantics", "prefix_resolution"], "first-valid-entries")
    add("checkpoint drift", c + ["semantics", "checkpoint_resolution"], "nearest-position")
    add("durability dropped", c + ["semantics", "durability"], "checkpoint-advisory")
    add("torn tail drift", c + ["semantics", "torn_tail"], "only-invalid-entries-dropped")
    add("quarantine drift", c + ["semantics", "quarantine"], "tail-dropped-silently")
    add("commit drift", c + ["semantics", "commit"], "truncate-then-validate")
    add("oracle trusted", c + ["oracle_boundary", "role"], "sink-always-honest")
    add("single evaluation dropped", c + ["oracle_boundary", "single_evaluation"], "retry")
    add("frozen dropped", c + ["oracle_boundary", "frozen_snapshots"], "live-log-re-read")
    add("output validation dropped", c + ["oracle_boundary", "output_validation"], "any")
    add("failure class dropped", c + ["failures", "classes"], ["corrupt_source"])
    add("failure trigger drift", c + ["failures", "triggers", "corrupt_source"], "never")
    add("failure mapping drift", c + ["failures", "mapping", "divergent_quarantine"], "internal")
    add("failures open", c + ["failures", "closed"], False)
    add("enum drift", c + ["errors", "closed_enum"], ["internal"])
    add(
        "retryable widened",
        c + ["errors", "shape", "retryable_true_only_for"],
        ["internal", "corrupt_source"],
    )
    add("property drift", c + ["properties", "idempotent"], "best-effort")
    add("base path drift", c + ["versioning", "base_path"], "/store/crash-resume/v0")
    add("link drift", c + ["links", "wal_contract"], "data/contracts/san.yaml")
    add("link missing file", c + ["links", "rollback_contract"], "data/contracts/nope.yaml")
    add("extra section", c + ["notes"], "hidden-semantics")
    add("schema version drift", ["schema_version"], 2)
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 30
    for _name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)


def test_mutants_never_silent_subset():
    base = yaml.safe_load(CONTRACT.read_text())["contract"]
    covered = set()
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != base.get(section):
                covered.add(section)
    assert covered >= set(base) - {"id"}


def test_engine_error_codes_are_the_closed_enum():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert list(FAILURE_MAPPING) == _CC["failures"]["classes"]


# -- hostile dict keys ------------------------------------------------------------


class _CollidingKey:
    """Hashes like a real field name; comparing it raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


def _colliding_requests():
    return [
        ("only_colliding", lambda: {_CollidingKey("checkpoint_sequence"): 3}),
        (
            "real_plus_colliding_other",
            lambda: {"checkpoint_sequence": 3, _CollidingKey("extra"): 3},
        ),
        ("int_key", lambda: {1: 3}),
    ]


@pytest.mark.parametrize("name,build", _colliding_requests())
def test_hostile_request_keys_fail_closed_typed(name, build):
    request = build()
    keys = list(dict.keys(request))
    log = _three()
    before_log = copy.deepcopy(log)
    _raises("malformed_resume_record", _engine().resume, log, request)
    assert list(dict.keys(request)) == keys, name
    assert log == before_log, name


def test_mutant_without_key_type_guard_escapes_raw():
    request = {_CollidingKey("checkpoint_sequence"): 3}
    with pytest.raises(RuntimeError):
        set(request.keys()) != set(_REQUEST_FIELDS)  # noqa: B015 - the unguarded comparison
    _raises("malformed_resume_record", _engine().resume, _three(), request)


def test_hostile_key_in_durable_entry_is_corrupt_source():
    log = _three()
    log[1] = {
        _CollidingKey("entry_id"): "x",
        **{k: v for k, v in log[1].items() if k != "entry_id"},
    }
    container = list(log)
    _raises("corrupt_source", _engine().resume, log, _req(3))
    assert all(a is b for a, b in zip(log, container, strict=True))


def test_hostile_key_in_torn_entry_is_malformed():
    torn = {_CollidingKey("sequence"): 4}
    log = _three() + [torn]
    _raises("malformed_resume_record", _engine().resume, log, _req(3))
    assert log[3] is torn and len(log) == 4


def test_hostile_key_after_checkpoint_caps_the_prefix():
    log = _three()
    log[2] = {_CollidingKey("op"): "put"}
    _raises("malformed_resume_record", _engine().resume, log, _req(2))
    assert len(log) == 3
