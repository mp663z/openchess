"""T0225 deterministic unit/property battery for the production backup.

Seeded properties over store.backup (the shipped T0224 runtime) only: no
tests.* helpers. Source logs are built with the shipped store.wal from
records made by the shipped graph.node runtime; receipts are checked
against the WAL replay, graph.diff, an independent bundle serialization
and an independent backup-id derivation.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import random
from pathlib import Path

import pytest

from graph import diff
from graph.node import make_record, record_identity
from store import backup, wal

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
FIELDS = ("backup_id", "head", "state_id", "entry_count", "bundle")
COUNT_MAX = 9223372036854775807
SEEDS = range(40)


class _Str(str):
    pass


class _Int(int):
    pass


class _Dict(dict):
    pass


class _Colliding:
    """Hashes like a real field name; while armed (only around the engine
    call) comparing it raises, so any comparison before the exact-str key
    guard escapes raw."""

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


def _armed(call, *args):
    _Colliding.armed = True
    try:
        return call(*args)
    finally:
        _Colliding.armed = False


def _engine(serializer=backup.serialize_bundle):
    return backup.BackupEngine(serializer)


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


def _fold(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _bundle(state):
    """Independent canonical bundle: key line then sorted field line."""
    return "".join(
        f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key]))
        + "\n" for key in sorted(state))


def _backup_id(head, sid, count, bundle):
    """Independent derivation from data/contracts/backup.yaml."""
    return "bck1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{bundle}".encode()).hexdigest()


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
        return [_Pin(value)] + [x for v in value.values() for x in _deep_ids(v)]
    if isinstance(value, list):
        return [_Pin(value)] + [x for v in value for x in _deep_ids(v)]
    return []


def _snap(value):
    return _shape(value), _deep_ids(value)


def _receipt(ops):
    return _engine().backup(_build(ops))


def _reforged(receipt, **changes):
    """A receipt with CHANGES and a self-consistent backup id."""
    out = {**receipt, **changes}
    out["backup_id"] = _backup_id(out["head"], out["state_id"],
                                  out["entry_count"], out["bundle"])
    return out


# -- R1: backup receipts ---------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_backup_receipt_matches_replay_and_derivation(seed):
    ops = _ops(seed)
    log = _build(ops)
    before = _snap(log)
    calls = []

    def serializer(state):
        calls.append(copy.deepcopy(state))
        return backup.serialize_bundle(state)

    receipt = _engine(serializer).backup(log)
    replay = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    model = _fold(ops)
    assert list(receipt) == list(FIELDS)
    assert type(receipt["entry_count"]) is int
    assert receipt["head"] == replay["head"]
    assert receipt["state_id"] == replay["state_id"] == diff.state_id(copy.deepcopy(model))
    assert receipt["entry_count"] == len(ops) == replay["applied"]
    assert receipt["bundle"] == _bundle(model) == backup.serialize_bundle(model)
    assert receipt["backup_id"] == _backup_id(
        receipt["head"], receipt["state_id"], receipt["entry_count"], receipt["bundle"])
    assert receipt["backup_id"] == backup.derive_backup_id(
        receipt["head"], receipt["state_id"], receipt["entry_count"], receipt["bundle"])
    assert calls == [model]
    assert _snap(log) == before
    assert receipt == _engine().backup(copy.deepcopy(log))
    if not ops:
        assert receipt["head"] == backup.GENESIS == wal.GENESIS
        assert receipt["state_id"] == backup.EMPTY_STATE_ID == diff.state_id({})


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_every_prefix_backs_up_and_verifies(seed):
    ops = _ops(seed)
    log = _build(ops)
    ids = set()
    for k in range(len(ops) + 1):
        receipt = _engine().backup(log[:k])
        before = _snap(receipt)
        verified = _engine().verify(receipt)
        assert verified == receipt and verified is not receipt
        assert list(verified) == list(FIELDS)
        assert _snap(receipt) == before
        assert receipt["entry_count"] == k
        ids.add(receipt["backup_id"])
    assert len(ids) == len(ops) + 1  # every head differs


@pytest.mark.parametrize("seed", range(12))
def test_r3_serializer_argument_is_detached(seed):
    """The serializer gets a by-value copy: mutating it at every level
    never reaches the log, and the receipt's state id is the replayed
    one."""
    log = _build(_ops(seed, length=1 + seed % 6))
    before = _snap(log)

    def serializer(state):
        text = backup.serialize_bundle(state)
        for record in state.values():
            record["zz"] = "1"
            record["digest"] = "tampered"
        state["zz"] = {}
        return text

    receipt = _engine(serializer).backup(log)
    assert _snap(log) == before
    assert receipt == _engine().backup(copy.deepcopy(log))


@pytest.mark.parametrize("seed", range(12))
def test_r4_non_canonical_serializer_output_is_bound_into_the_id(seed):
    """backup does not re-serialize: any exact UTF-8 str is the bundle,
    and the receipt id binds it (verify passes, a different bundle is a
    different id)."""
    log = _build(_ops(seed))
    receipt = _engine(lambda state: "custom\n" + str(len(state))).backup(log)
    state = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))["state"]
    assert receipt["bundle"] == "custom\n" + str(len(state))
    assert receipt["backup_id"] == _backup_id(
        receipt["head"], receipt["state_id"], receipt["entry_count"], receipt["bundle"])
    assert _engine().verify(receipt) == receipt
    assert receipt["backup_id"] != _engine().backup(log)["backup_id"]


# -- R5: serializer boundary -----------------------------------------------------

def _raiser(kind):
    def serializer(state):
        raise kind()
    return serializer


def _forged_oracle(error):
    """An oracle raising a pre-built (forged) typed error: it must fail as
    the boundary's own class, never surface as the forged one."""
    def oracle(state):
        raise error
    return oracle


FORGED_ERRORS = {
    **{f"forged-backup-error-{c}": backup.BackupError(c, backup.FAILURE_MAPPING[c])
       for c in sorted(backup.FAILURE_MAPPING) if c != "divergent_snapshot"},
    **{f"forged-wal-error-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
}


HOSTILE_SERIALIZERS = {
    **{name: _forged_oracle(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda state: None,
    "bytes": lambda state: b"x",
    "int": lambda state: 0,
    "str-subclass": lambda state: _Str(backup.serialize_bundle(state)),
    "surrogate": lambda state: "\ud800",
    "surrogate-tail": lambda state: backup.serialize_bundle(state) + "\udfff",
}


@pytest.mark.parametrize("name", sorted(HOSTILE_SERIALIZERS))
@pytest.mark.parametrize("seed", range(5))
def test_r5_hostile_serializer_fails_closed(name, seed):
    log = _build(_ops(seed, length=seed % 4))
    before = _snap(log)
    with pytest.raises(backup.BackupError) as exc:
        _engine(HOSTILE_SERIALIZERS[name]).backup(log)
    assert exc.value.failure_class == "divergent_snapshot"
    assert exc.value.code == backup.FAILURE_MAPPING["divergent_snapshot"]
    assert _snap(log) == before


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("fail", [False, True])
def test_r6_live_log_mutation_is_undone(seed, fail):
    """A serializer closing over the caller's LIVE log adds keys at every
    level, changes values and inserts into the container; the log comes
    back with the same values, key order and identity on both exits."""
    log = _build(_ops(seed, length=1 + seed % 5))
    before = _snap(log)

    def serializer(state):
        for entry in list(log):
            if type(entry) is dict and "payload" in entry:
                entry["zz"] = 1
                entry["sequence"] = 99
                entry["payload"]["zz"] = 1
                entry["payload"]["record"]["zz"] = 1
                entry["payload"]["record"].pop("digest")
        log.insert(0, {"foreign": True})
        log.append(log[1])
        return None if fail else backup.serialize_bundle(state)

    if fail:
        with pytest.raises(backup.BackupError) as exc:
            _engine(serializer).backup(log)
        assert exc.value.failure_class == "divergent_snapshot"
    else:
        receipt = _engine(serializer).backup(log)
        assert receipt == _engine().backup(copy.deepcopy(log))
    assert _snap(log) == before


# -- R7: corrupt or malformed source ---------------------------------------------

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
def test_r7_corrupt_source_fails_closed_before_the_serializer(name):
    for seed in range(8):
        log = _build(_ops(seed, length=1 + seed % 6))
        position = random.Random(seed + 500).randrange(len(log))
        replacement = SOURCE_TAMPERS[name](log[position])
        if replacement is not None:
            log[position] = replacement
        before, calls = _snap(log), []
        with pytest.raises(backup.BackupError) as exc:
            _armed(_engine(calls.append).backup, log)
        assert exc.value.failure_class == "corrupt_source", (name, seed)
        assert exc.value.code == backup.FAILURE_MAPPING["corrupt_source"]
        assert _snap(log) == before and calls == []


@pytest.mark.parametrize("bad", [None, (), {}, "log", 0, _Dict()])
def test_r7b_non_list_source_is_malformed(bad):
    calls = []
    with pytest.raises(backup.BackupError) as exc:
        _engine(lambda s: calls.append(s) or "").backup(bad)
    assert exc.value.failure_class == "malformed_backup_record"
    assert calls == []


def test_r7c_list_subclass_source_is_malformed():
    class _List(list):
        pass
    log = _List(_build(_ops(3, length=2)))
    with pytest.raises(backup.BackupError) as exc:
        _engine().backup(log)
    assert exc.value.failure_class == "malformed_backup_record"


# -- R8: verify is local and total -----------------------------------------------

def _receipt_tampers(r):
    """(name, receipt, failure) over a receipt R with entry_count > 0."""
    other = next(o for o in (_receipt([("put", 6)]), _receipt([("put", 5)]))
                 if o["state_id"] != r["state_id"])
    out = [
        # Same-arity renames: one field per tamper, key count unchanged, so
        # only an exact key-set check (not a length check) rejects them.
        *[(f"renamed-key-{field}",
           {(field + "x" if k == field else k): v for k, v in r.items()},
           "malformed_backup_record") for field in list(r)],
        ("not-dict-list", [r], "malformed_backup_record"),
        ("not-dict-none", None, "malformed_backup_record"),
        ("dict-subclass", _Dict(r), "malformed_backup_record"),
        ("missing-key", {k: v for k, v in r.items() if k != "bundle"},
         "malformed_backup_record"),
        ("extra-key", {**r, "zz": 1}, "malformed_backup_record"),
        ("str-subclass-key", {**{k: v for k, v in r.items() if k != "head"},
                              _Str("head"): r["head"]}, "malformed_backup_record"),
        ("colliding-key", {**r, _Colliding("head"): 1}, "malformed_backup_record"),
        ("count-bool", {**r, "entry_count": True}, "malformed_backup_record"),
        ("count-float", {**r, "entry_count": float(r["entry_count"])},
         "malformed_backup_record"),
        ("count-str", {**r, "entry_count": str(r["entry_count"])},
         "malformed_backup_record"),
        ("count-int-subclass", {**r, "entry_count": _Int(r["entry_count"])},
         "malformed_backup_record"),
        ("count-negative", _reforged(r, entry_count=-1), "malformed_backup_record"),
        ("count-over-max", _reforged(r, entry_count=COUNT_MAX + 1),
         "malformed_backup_record"),
        ("count-huge", {**r, "entry_count": 10 ** 5000}, "malformed_backup_record"),
        ("bundle-non-str", {**r, "bundle": b"x"}, "malformed_backup_record"),
        ("bundle-str-subclass", {**r, "bundle": _Str(r["bundle"])},
         "malformed_backup_record"),
        ("bundle-surrogate", {**r, "bundle": r["bundle"] + "\ud800"},
         "malformed_backup_record"),
        ("count-reforged-change", _reforged(r, entry_count=r["entry_count"] + 1), None),
        ("bundle-change", {**r, "bundle": r["bundle"] + " "}, "divergent_backup"),
        ("count-change", {**r, "entry_count": r["entry_count"] + 1}, "divergent_backup"),
        ("backup-id-flip", {**r, "backup_id": _flip(r["backup_id"])},
         "divergent_backup"),
        ("head-flip", {**r, "head": _flip(r["head"])}, "divergent_backup"),
        ("state-id-flip", {**r, "state_id": _flip(r["state_id"])}, "divergent_backup"),
        ("swapped-state", {**r, "state_id": other["state_id"]}, "divergent_backup"),
        ("genesis-head-positive-count", _reforged(r, head=wal.GENESIS),
         "divergent_backup"),
        ("zero-count-non-genesis", _reforged(r, entry_count=0), "divergent_backup"),
    ]
    if r["state_id"] != backup.EMPTY_STATE_ID:
        out.append(("zero-count-state", _reforged(r, entry_count=0, head=wal.GENESIS),
                    "divergent_backup"))
    for field in ("backup_id", "head", "state_id"):
        out += [
            (f"{field}-str-subclass", {**r, field: _Str(r[field])},
             "malformed_backup_record"),
            (f"{field}-non-str", {**r, field: None}, "malformed_backup_record"),
            (f"{field}-grammar", {**r, field: r[field] + "0"}, "malformed_backup_record"),
            (f"{field}-upper", {**r, field: r[field].upper()}, "malformed_backup_record"),
            (f"{field}-newline", {**r, field: r[field] + "\n"},
             "malformed_backup_record"),
        ]
    return out


@pytest.mark.parametrize("seed", range(8))
def test_r8_verify_rejects_every_tamper_typed_and_untouched(seed):
    r = _receipt(_ops(seed, length=1 + seed % 6))
    assert r["entry_count"] > 0
    for name, receipt, failure in _receipt_tampers(r):
        before = _snap(receipt)
        if failure is None:
            assert _armed(_engine().verify, receipt) == receipt, name
        else:
            with pytest.raises(backup.BackupError) as exc:
                try:
                    _armed(_engine().verify, receipt)
                except backup.BackupError:
                    raise
                else:
                    raise AssertionError(f"{name}: accepted")
            assert exc.value.failure_class == failure, name
            assert exc.value.code == backup.FAILURE_MAPPING[failure]
        assert _snap(receipt) == before, name


def test_r9_entry_count_bounds_from_both_sides():
    r = _receipt([("put", 0)])
    at_max = _reforged(r, entry_count=COUNT_MAX)
    assert _engine().verify(at_max) == at_max
    for count in (COUNT_MAX + 1, COUNT_MAX * 2, -1):
        with pytest.raises(backup.BackupError) as exc:
            _engine().verify(_reforged(r, entry_count=count))
        assert exc.value.failure_class == "malformed_backup_record"
    empty = _receipt([])
    assert _engine().verify(empty) == empty
    assert empty["entry_count"] == 0 and empty["bundle"] == ""


@pytest.mark.parametrize("seed", range(8))
def test_r10_verify_output_is_detached_and_ordered(seed):
    r = _receipt(_ops(seed))
    shuffled = dict(reversed(list(r.items())))
    out = _engine().verify(shuffled)
    assert list(out) == list(FIELDS) and out == r
    assert out is not shuffled
    out["bundle"] = "x"
    assert shuffled["bundle"] == r["bundle"]


def test_r11_property_file_uses_no_test_helpers():
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
                       "pathlib", "pytest", "graph", "graph.node", "store"}




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _snap(value)
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _snap(value) != before
