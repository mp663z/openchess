"""T0243 deterministic unit/property battery for the production rollback.

Seeded properties over store.rollback (the shipped T0242 runtime) only:
no tests.* helpers. Source logs are built with the shipped store.wal from
records made by the shipped graph.node runtime; receipts are checked
against the WAL chain, an independent archive-token derivation and an
independent rollback-id derivation, and committed logs against the
original prefix by value and object identity.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import random
from pathlib import Path

import pytest

from graph.node import make_record, record_identity
from store import rollback, wal

FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
IDENTITIES = tuple(record_identity(record) for record in RECORDS)
FIELDS = ("rollback_id", "from_head", "to_head", "truncated_count",
          "archive_token")
GENESIS = "wal0:" + "0" * 64
SEEDS = range(40)


class _Str(str):
    pass


class _Int(int):
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
            raise RuntimeError("hostile __eq__")
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)


def _armed(call, *args):
    _Colliding.armed = True
    try:
        return call(*args)
    finally:
        _Colliding.armed = False


def _engine(archiver=rollback.archive_tail):
    return rollback.RollbackEngine(archiver)


def _ops(seed, length=None):
    rng = random.Random(seed)
    n = rng.randrange(0, 11) if length is None else length
    return [(rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
            for _ in range(n)]


def _build(ops):
    log, engine = [], wal.WalEngine(wal.canonical_payload)
    for op, index in ops:
        engine.append(log, {"op": op, "payload": {
            "identity": IDENTITIES[index], "record": dict(RECORDS[index])}})
    return log


def _token(tail):
    """Independent archive token from data/contracts/rollback.yaml."""
    parts = ["arc1"]
    for e in tail:
        r = e["payload"]["record"]
        for field in (e["sequence"], e["op"], e["entry_id"], e["prior_entry_id"],
                      e["payload"]["identity"], r["variant"], r["digest"],
                      r["snapshot_fen"]):
            parts.append(f"{len(str(field))}:{field}")
    return "arc1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _rollback_id(from_head, to_head, count, token):
    return "rbk1:" + hashlib.sha256(
        f"{from_head}\n{to_head}\n{count}\n{token}".encode()).hexdigest()


def _shape(value):
    if type(value) is dict:
        return [(key, _shape(item)) for key, item in value.items()]
    if type(value) is list:
        return [_shape(item) for item in value]
    return value


class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _deep_ids(value):
    if isinstance(value, dict):
        return [_Pin(value)] + [x for k, v in value.items()
                              for x in (_Pin(k), *_deep_ids(v))]
    if isinstance(value, list):
        return [_Pin(value)] + [x for v in value for x in _deep_ids(v)]
    return [_Pin(value)]


def _snap(value):
    return _shape(value), _deep_ids(value)


def _fails(failure, call, *args):
    with pytest.raises(rollback.RollbackError) as exc:
        try:
            _armed(call, *args)
        except rollback.RollbackError:
            raise
        else:
            raise AssertionError("accepted")
    assert exc.value.failure_class == failure
    assert exc.value.code == rollback.FAILURE_MAPPING[failure]


def _run(log, target, archiver=rollback.archive_tail):
    """Roll LOG back to TARGET and check the receipt and the commit."""
    original = copy.deepcopy(log)
    entry_ids = [_Pin(e) for e in log]
    inner = [(_Pin(e["payload"]), _Pin(e["payload"]["record"])) for e in log]
    container = id(log)
    request = {"target_sequence": target}
    req_before = _snap(request)
    out = _engine(archiver).rollback(log, request)
    assert list(out) == list(FIELDS)
    assert out["from_head"] == (original[-1]["entry_id"] if original else GENESIS)
    assert out["to_head"] == (original[target - 1]["entry_id"] if target else GENESIS)
    assert type(out["truncated_count"]) is int
    assert out["truncated_count"] == len(original) - target
    assert type(out["archive_token"]) is str
    assert out["archive_token"] == _token(original[target:]) == \
        rollback.archive_tail(copy.deepcopy(original[target:]))
    assert out["rollback_id"] == _rollback_id(
        out["from_head"], out["to_head"], out["truncated_count"], out["archive_token"])
    assert out["rollback_id"] == rollback.derive_rollback_id(
        out["from_head"], out["to_head"], out["truncated_count"], out["archive_token"])
    # commit: same container, exactly the original prefix, same objects
    assert id(log) == container
    assert log == original[:target]
    assert [_Pin(e) for e in log] == entry_ids[:target]
    assert [(_Pin(e["payload"]), _Pin(e["payload"]["record"])) for e in log] == inner[:target]
    replay = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    assert replay["head"] == out["to_head"] and replay["applied"] == target
    assert _snap(request) == req_before
    return out


# -- R1: honest rollbacks ------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_every_target_rolls_back_exactly(seed):
    ops = _ops(seed)
    base = _build(ops)
    ids = set()
    for target in range(len(base) + 1):
        log = copy.deepcopy(base)
        calls = []

        def archiver(tail, calls=calls):
            calls.append(copy.deepcopy(tail))
            return rollback.archive_tail(tail)

        out = _run(log, target, archiver)
        assert calls == [base[target:]]
        assert all(type(e) is dict and type(e["payload"]) is dict and
                   type(e["payload"]["record"]) is dict for e in calls[0])
        ids.add(out["rollback_id"])
        again = copy.deepcopy(base)
        assert _engine().rollback(again, {"target_sequence": target}) == out
    assert len(ids) == len(base) + 1


def test_r1b_empty_log_and_boundaries():
    out = _run([], 0)
    assert out["from_head"] == out["to_head"] == GENESIS == rollback.GENESIS == wal.GENESIS
    assert out["truncated_count"] == 0 and out["archive_token"] == _token([])
    log = _build(_ops(1, length=4))
    no_op = _run(copy.deepcopy(log), len(log))
    assert no_op["truncated_count"] == 0 and no_op["from_head"] == no_op["to_head"]
    full = _run(copy.deepcopy(log), 0)
    assert full["to_head"] == GENESIS and full["truncated_count"] == len(log)


@pytest.mark.parametrize("seed", range(10))
def test_r2_surviving_prefix_keeps_its_chain_and_accepts_appends(seed):
    ops = _ops(seed, length=2 + seed % 6)
    log = _build(ops)
    target = random.Random(seed).randrange(len(log) + 1)
    out = _run(log, target)
    engine = wal.WalEngine(wal.canonical_payload)
    engine.append(log, {"op": "put", "payload": {
        "identity": IDENTITIES[0], "record": dict(RECORDS[0])}})
    assert log[-1]["prior_entry_id"] == out["to_head"]
    assert log == _build(ops[:target] + [("put", 0)])


@pytest.mark.parametrize("seed", range(12))
def test_r3_archiver_argument_is_detached(seed):
    """The archiver gets a deep copy of the frozen tail: mutating it at
    every level (after computing the honest token) never reaches the log
    or the receipt."""
    log = _build(_ops(seed, length=2 + seed % 6))
    target = random.Random(seed + 1).randrange(len(log))
    original = copy.deepcopy(log)

    def archiver(tail):
        token = rollback.archive_tail(tail)
        for entry in tail:
            entry["entry_id"] = "x"
            entry["payload"]["identity"] = "x"
            entry["payload"]["record"]["digest"] = "x"
            entry["zz"] = 1
        tail.append({"foreign": True})
        return token

    out = _engine(archiver).rollback(log, {"target_sequence": target})
    assert out == _engine().rollback(copy.deepcopy(original), {"target_sequence": target})
    assert log == original[:target]


# -- R4: archiver boundary -----------------------------------------------------------

def _raiser(kind):
    def archiver(tail):
        raise kind()
    return archiver


def _other(tail):
    return rollback.archive_tail(tail[1:] if tail else [{"sequence": 0, "op": "x",
        "entry_id": "x", "prior_entry_id": "x",
        "payload": {"identity": "x", "record": {"variant": "x", "digest": "x",
                                                "snapshot_fen": "x"}}}])


def _forged_oracle(error):
    """An oracle raising a pre-built (forged) typed error: it must fail as
    the boundary's own class, never surface as the forged one."""
    def oracle(tail):
        raise error
    return oracle


FORGED_ERRORS = {
    **{f"forged-rollback-error-{c}": rollback.RollbackError(c, rollback.FAILURE_MAPPING[c])
       for c in sorted(rollback.FAILURE_MAPPING) if c != "divergent_archive"},
    **{f"forged-wal-error-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
}


HOSTILE_ARCHIVERS = {
    **{name: _forged_oracle(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda tail: None,
    "bytes": lambda tail: rollback.archive_tail(tail).encode(),
    "int": lambda tail: 0,
    "str-subclass": lambda tail: _Str(rollback.archive_tail(tail)),
    "trailing-newline": lambda tail: rollback.archive_tail(tail) + "\n",
    "upper-case": lambda tail: rollback.archive_tail(tail).upper(),
    "wrong-prefix": lambda tail: "arc2" + rollback.archive_tail(tail)[4:],
    "surrogate": lambda tail: rollback.archive_tail(tail)[:-1] + "\ud800",
    "grammar-valid-other-tail": _other,
    "grammar-valid-flip": lambda tail: rollback.archive_tail(tail)[:-1] + (
        "0" if rollback.archive_tail(tail)[-1] != "0" else "1"),
}


@pytest.mark.parametrize("name", sorted(HOSTILE_ARCHIVERS))
@pytest.mark.parametrize("seed", range(5))
def test_r4_hostile_archiver_fails_closed(name, seed):
    log = _build(_ops(seed, length=1 + seed % 5))
    target = seed % len(log)
    request = {"target_sequence": target}
    before, req_before = _snap(log), _snap(request)
    _fails("divergent_archive", _engine(HOSTILE_ARCHIVERS[name]).rollback, log, request)
    assert _snap(log) == before and _snap(request) == req_before


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("fail", [False, True])
def test_r5_live_input_mutation_is_undone(seed, fail):
    """An archiver closing over the caller's LIVE log and request adds keys
    at every level, changes and removes values and inserts into both
    containers; everything is put back (values, key order, identity) on
    both exits, and the commit truncates the restored log."""
    log = _build(_ops(seed, length=2 + seed % 5))
    target = seed % len(log)
    request = {"target_sequence": target}
    original = copy.deepcopy(log)
    entry_ids = [_Pin(e) for e in log]
    before, req_before = _snap(log), _snap(request)

    def archiver(tail):
        token = rollback.archive_tail(tail)
        for entry in list(log):
            entry["zz"] = 1
            entry["sequence"] = 99
            entry["payload"]["zz"] = 1
            entry["payload"]["record"]["zz"] = 1
            entry["payload"]["record"].pop("digest")
        log.insert(0, {"foreign": True})
        del log[-1]
        request["target_sequence"] = 0
        request["zz"] = 1
        request[_Str("target_sequence")] = 5
        return None if fail else token

    if fail:
        _fails("divergent_archive", _engine(archiver).rollback, log, request)
        assert _snap(log) == before
    else:
        out = _engine(archiver).rollback(log, request)
        assert out == _engine().rollback(copy.deepcopy(original), {"target_sequence": target})
        assert log == original[:target]
        assert [_Pin(e) for e in log] == entry_ids[:target]
    assert _snap(request) == req_before


# -- R6: malformed requests and logs -------------------------------------------------

def _request_tampers(target):
    return [
        ("renamed-key", {"target_sequencex": target}),
        ("missing-key", {}),
        ("extra-key", {"target_sequence": target, "zz": 1}),
        ("str-subclass-key", {_Str("target_sequence"): target}),
        ("colliding-key", {_Colliding("target_sequence"): target}),
        ("dict-subclass", _Dict({"target_sequence": target})),
        ("list", [("target_sequence", target)]),
        ("none", None),
        ("target-bool", {"target_sequence": True}),
        ("target-int-subclass", {"target_sequence": _Int(target)}),
        ("target-float", {"target_sequence": float(target)}),
        ("target-str", {"target_sequence": str(target)}),
        ("target-none", {"target_sequence": None}),
    ]


@pytest.mark.parametrize("seed", range(6))
def test_r6_malformed_request_is_rejected_untouched(seed):
    for name, request in _request_tampers(1):
        for corrupt in (False, True):
            log = _build(_ops(seed, length=2 + seed % 4))
            if corrupt:
                log[0]["sequence"] += 1
            before, req_before, calls = _snap(log), _snap(request), []
            try:
                _fails("malformed_rollback_record",
                       _engine(lambda t, calls=calls: calls.append(t)).rollback, log, request)
            except AssertionError as err:
                raise AssertionError(f"{name}: {err}") from None
            assert calls == [] and _snap(log) == before, name
            assert _snap(request) == req_before, name


@pytest.mark.parametrize("bad", [None, (), {}, "log", 0, _Dict()])
def test_r6b_non_list_log_is_malformed(bad):
    _fails("malformed_rollback_record", _engine().rollback, bad, {"target_sequence": 0})


def test_r6c_list_subclass_log_is_malformed():
    log = _List(_build(_ops(3, length=2)))
    before = _snap(list(log))
    _fails("malformed_rollback_record", _engine().rollback, log, {"target_sequence": 0})
    assert len(log) == 2 and _snap(list(log))[0] == before[0]


# -- R7: target bounds -----------------------------------------------------------------

@pytest.mark.parametrize("seed", range(8))
def test_r7_target_bounds_from_both_sides(seed):
    log = _build(_ops(seed, length=1 + seed % 6))
    n = len(log)
    for target in (-1, -n - 1, n + 1, n + 2, 2 ** 70, -(2 ** 70)):
        work = copy.deepcopy(log)
        before, calls = _snap(work), []
        _fails("unknown_target",
               _engine(lambda t, calls=calls: calls.append(t)).rollback,
               work, {"target_sequence": target})
        assert calls == [] and _snap(work) == before
    for target in (0, 1, n - 1, n):
        _run(copy.deepcopy(log), target)


# -- R8: corrupt source ----------------------------------------------------------------

def _flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


SOURCE_TAMPERS = {
    "sequence-shift": lambda e: e.__setitem__("sequence", e["sequence"] + 1),
    "sequence-bool": lambda e: e.__setitem__("sequence", True),
    "unknown-op": lambda e: e.__setitem__("op", "move"),
    "op-str-subclass": lambda e: e.__setitem__("op", _Str(e["op"])),
    "entry-id-flip": lambda e: e.__setitem__("entry_id", _flip(e["entry_id"])),
    "prior-flip": lambda e: e.__setitem__("prior_entry_id", _flip(e["prior_entry_id"])),
    "extra-key": lambda e: e.__setitem__("zz", 1),
    "str-subclass-key": lambda e: e.__setitem__(_Str("op"), e.pop("op")),
    "colliding-key": lambda e: e.__setitem__(_Colliding("op"), 1),
    "digest-swap": lambda e: e["payload"]["record"].__setitem__(
        "digest", RECORDS[(IDENTITIES.index(e["payload"]["identity"]) + 1)
                          % len(RECORDS)]["digest"]),
    "identity-str-subclass": lambda e: e["payload"].__setitem__(
        "identity", _Str(e["payload"]["identity"])),
    "record-dict-subclass": lambda e: e["payload"].__setitem__(
        "record", _Dict(e["payload"]["record"])),
    "entry-dict-subclass": lambda e: _Dict(e),
    "entry-not-dict": lambda e: [e],
}


@pytest.mark.parametrize("name", sorted(SOURCE_TAMPERS))
def test_r8_corrupt_source_fails_before_the_archiver(name):
    for seed in range(8):
        log = _build(_ops(seed, length=1 + seed % 6))
        position = random.Random(seed + 500).randrange(len(log))
        replacement = SOURCE_TAMPERS[name](log[position])
        if replacement is not None:
            log[position] = replacement
        # corrupt_source wins over an out-of-range target (validation first)
        for target in (0, len(log), len(log) + 1, -1):
            request = {"target_sequence": target}
            before, calls = _snap(log), []
            try:
                _fails("corrupt_source",
                       _engine(lambda t, calls=calls: calls.append(t)).rollback,
                       log, request)
            except AssertionError as err:
                raise AssertionError(f"{name}/{seed}/{target}: {err}") from None
            assert _snap(log) == before and calls == []


# -- R9: archive token -------------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r9_archive_tail_matches_the_independent_derivation(seed):
    log = _build(_ops(seed))
    for start in range(len(log) + 1):
        tail = log[start:]
        before = _snap(tail)
        assert rollback.archive_tail(tail) == _token(tail)
        assert _snap(tail) == before
    assert len({_token(log[s:]) for s in range(len(log) + 1)}) == len(log) + 1


def test_r10_property_file_uses_no_test_helpers():
    tree = ast.parse(Path(__file__).read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)
    assert modules <= {"__future__", "ast", "copy", "hashlib", "random",
                       "pathlib", "pytest", "graph.node", "store"}




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _snap(value)
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _snap(value) != before
