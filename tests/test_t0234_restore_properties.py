"""T0234 deterministic unit/property battery for the production restore.

Seeded properties over store.restore (the shipped T0233 runtime) only: no
tests.* helpers. Receipts come from the shipped store.backup over logs
built with the shipped store.wal from records made by the shipped
graph.node runtime; restores are checked against an independent put/delete
model, graph.diff, an independent state-id and restore-id derivation and
an independent bundle serialization. Forged receipts are self-consistent
(they pass the local backup verify) so every restore-side guard is
reached on its own.
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
from graph.position_digest import DigestError
from store import backup, restore, wal
from tools.variant_runtime import VariantError

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
FIELDS = ("restore_id", "backup_id", "state_id", "state")
RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
COUNT_MAX = 9223372036854775807
SEEDS = range(40)


class _Str(str):
    pass


class _Dict(dict):
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


def _engine(parser=restore.parse_bundle):
    return restore.RestoreEngine(parser)


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


def _bundle(state, order=sorted):
    """Independent canonical bundle: key line then sorted field line."""
    return "".join(
        f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key]))
        + "\n" for key in order(state))


def _state_id(state):
    """Independent state id (graph.diff canonical form), no validation, so
    it also prices invalid states for forged receipts."""
    return "gs1:" + hashlib.sha256(_bundle(state).encode()).hexdigest()


def _backup_id(head, sid, count, bundle):
    return "bck1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{bundle}".encode()).hexdigest()


def _restore_id(backup_id, sid):
    return "rst1:" + hashlib.sha256(f"{backup_id}\n{sid}".encode()).hexdigest()


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


def _receipt(ops):
    return backup.BackupEngine(backup.serialize_bundle).backup(_build(ops))


def _positive(seed=0):
    """A real receipt with entry_count > 0 and at least two records."""
    for s in range(seed, seed + 400):
        ops = _ops(s, length=3 + s % 6)
        if len(_fold(ops)) >= 2:
            return _receipt(ops), _fold(ops)
    raise AssertionError("no seed")


def _forged(receipt, state=None, bundle=None, sid=None, **changes):
    """A self-consistent receipt (it passes the local backup verify) over
    STATE: bundle and state id default to the independent canonical ones,
    and the backup id is re-derived."""
    out = {**receipt, **changes}
    if state is not None:
        out["bundle"] = _bundle(state) if bundle is None else bundle
        out["state_id"] = _state_id(state) if sid is None else sid
    out["backup_id"] = _backup_id(out["head"], out["state_id"],
                                  out["entry_count"], out["bundle"])
    assert backup.BackupEngine(backup.serialize_bundle).verify(out) == out
    return out


def _fails(failure, call, *args):
    with pytest.raises(restore.RestoreError) as exc:
        try:
            _armed(call, *args)
        except restore.RestoreError:
            raise
        else:
            raise AssertionError("accepted")
    assert exc.value.failure_class == failure
    assert exc.value.code == restore.FAILURE_MAPPING[failure]


def _check_out(out, receipt_before, model):
    assert list(out) == list(FIELDS)
    assert out["backup_id"] == receipt_before["backup_id"]
    assert out["state_id"] == receipt_before["state_id"] == _state_id(model)
    assert out["restore_id"] == _restore_id(out["backup_id"], out["state_id"])
    assert out["restore_id"] == restore.derive_restore_id(
        out["backup_id"], out["state_id"])
    assert type(out["state"]) is dict and out["state"] == model
    for key, record in out["state"].items():
        assert type(key) is str and type(record) is dict
        assert set(record) == set(RECORD_FIELDS)
        assert all(type(k) is str and type(v) is str for k, v in record.items())


# -- R1: honest restores -----------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_restore_matches_model_and_derivations(seed):
    ops = _ops(seed)
    receipt = _receipt(ops)
    model = _fold(ops)
    before, calls = _snap(receipt), []

    def parser(bundle):
        calls.append(bundle)
        return restore.parse_bundle(bundle)

    out = _engine(parser).restore(receipt)
    _check_out(out, receipt, model)
    assert out["state_id"] == diff.state_id(copy.deepcopy(model))
    assert calls == [receipt["bundle"]] and type(calls[0]) is str
    assert _snap(receipt) == before
    again = _engine().restore(copy.deepcopy(receipt))
    assert again == out and again["state"] is not out["state"]
    if not ops or not model:
        assert out["state"] == {}


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_every_prefix_restores(seed):
    ops = _ops(seed)
    seen = set()
    for n in range(len(ops) + 1):
        receipt = _receipt(ops[:n])
        out = _engine().restore(receipt)
        _check_out(out, receipt, _fold(ops[:n]))
        seen.add(out["restore_id"])
    # distinct backups give distinct restore ids (the backup id is bound)
    assert len(seen) == len(ops) + 1


def test_r2b_empty_log_restores_to_the_empty_state():
    receipt = _receipt([])
    out = _engine().restore(receipt)
    assert out["state"] == {} and out["state_id"] == diff.state_id({})
    assert out["restore_id"] == _restore_id(receipt["backup_id"], receipt["state_id"])


@pytest.mark.parametrize("seed", SEEDS)
def test_r3_parse_bundle_is_the_exact_inverse(seed):
    model = _fold(_ops(seed))
    parsed = restore.parse_bundle(_bundle(model))
    assert parsed == model
    assert restore.parse_bundle(backup.serialize_bundle(model)) == model
    assert backup.serialize_bundle(parsed) == _bundle(model)


@pytest.mark.parametrize("seed", range(12))
def test_r4_output_state_is_detached_from_the_parser_output(seed):
    receipt, model = _positive(seed)
    kept = []

    def parser(bundle):
        kept.append(restore.parse_bundle(bundle))
        return kept[-1]

    out = _engine(parser).restore(receipt)
    parsed = kept[0]
    assert out["state"] is not parsed
    parser_ids = {id(parsed)} | {id(r) for r in parsed.values()}
    out_ids = {id(out["state"])} | {id(r) for r in out["state"].values()}
    assert not parser_ids & out_ids
    for record in parsed.values():
        record["digest"] = "tampered"
        record["zz"] = "1"
    parsed["zz"] = {}
    assert out["state"] == model


# -- R5: parser boundary -------------------------------------------------------------

def _raiser(kind):
    def parser(bundle):
        raise kind()
    return parser


def _forged_oracle(error):
    """An oracle raising a pre-built (forged) typed error: it must fail as
    the boundary's own class, never surface as the forged one."""
    def oracle(bundle):
        raise error
    return oracle


FORGED_ERRORS = {
    **{f"forged-restore-error-{c}": restore.RestoreError(c, restore.FAILURE_MAPPING[c])
       for c in sorted(restore.FAILURE_MAPPING) if c != "divergent_parse"},
    **{f"forged-backup-error-{c}": backup.BackupError(c, backup.FAILURE_MAPPING[c])
       for c in sorted(backup.FAILURE_MAPPING)},
    **{f"forged-wal-error-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
}


HOSTILE_PARSERS = {
    **{name: _forged_oracle(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "index-error": _raiser(IndexError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda bundle: None,
    "list": lambda bundle: list(restore.parse_bundle(bundle).items()),
    "str": lambda bundle: bundle,
    "dict-subclass": lambda bundle: _Dict(restore.parse_bundle(bundle)),
    "canonical-other-state": lambda bundle: {IDENTITIES[0]: dict(RECORDS[0])}
    if restore.parse_bundle(bundle) != {IDENTITIES[0]: dict(RECORDS[0])}
    else {IDENTITIES[1]: dict(RECORDS[1])},
}
HOSTILE_CLASS = {"canonical-other-state": "divergent_state"}


@pytest.mark.parametrize("name", sorted(HOSTILE_PARSERS))
@pytest.mark.parametrize("seed", range(5))
def test_r5_hostile_parser_fails_closed(name, seed):
    receipt, _model = _positive(seed)
    before = _snap(receipt)
    _fails(HOSTILE_CLASS.get(name, "divergent_parse"),
           _engine(HOSTILE_PARSERS[name]).restore, receipt)
    assert _snap(receipt) == before


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("fail", [False, True])
def test_r6_live_receipt_mutation_is_undone(seed, fail):
    """A parser closing over the caller's LIVE receipt changes, removes
    and adds keys; the receipt comes back with the same values, key
    order and identity on both exits, and the output uses the values
    the receipt had before the parser ran."""
    receipt, model = _positive(seed)
    original = dict(receipt)
    before = _snap(receipt)

    def parser(bundle):
        parsed = restore.parse_bundle(bundle)
        receipt["state_id"] = "gs1:" + "0" * 64
        receipt["backup_id"] = "bck1:" + "0" * 64
        del receipt["bundle"]
        receipt["zz"] = 1
        receipt[_Str("head")] = "x"
        return None if fail else parsed

    if fail:
        _fails("divergent_parse", _engine(parser).restore, receipt)
    else:
        out = _engine(parser).restore(receipt)
        _check_out(out, original, model)
    assert _snap(receipt) == before and receipt == original


def test_r6b_parser_clearing_the_receipt_is_undone():
    receipt, model = _positive(3)
    before = _snap(receipt)

    def parser(bundle):
        receipt.clear()
        return restore.parse_bundle(bundle)

    out = _engine(parser).restore(receipt)
    assert out["state"] == model
    assert _snap(receipt) == before


# -- R7: verify first ----------------------------------------------------------------

def _flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


def _receipt_tampers(r):
    """(name, receipt, restore failure) - all rejected by the linked
    backup verify before any parse."""
    fields = list(r)
    return [
        *[(f"renamed-key-{f}", {(f + "x" if k == f else k): v for k, v in r.items()},
           "malformed_restore_record") for f in fields],
        *[(f"missing-key-{f}", {k: v for k, v in r.items() if k != f},
           "malformed_restore_record") for f in fields],
        ("extra-key", {**r, "zz": 1}, "malformed_restore_record"),
        ("str-subclass-key", {**{k: v for k, v in r.items() if k != "head"},
                              _Str("head"): r["head"]}, "malformed_restore_record"),
        ("colliding-key", {**{k: v for k, v in r.items() if k != "head"},
                           _Colliding("head"): r["head"]}, "malformed_restore_record"),
        ("dict-subclass", _Dict(r), "malformed_restore_record"),
        ("not-dict-list", [r], "malformed_restore_record"),
        ("not-dict-none", None, "malformed_restore_record"),
        ("not-dict-str", "receipt", "malformed_restore_record"),
        ("count-bool", {**r, "entry_count": True}, "malformed_restore_record"),
        ("count-over-max", {**r, "entry_count": COUNT_MAX + 1}, "malformed_restore_record"),
        ("backup-id-grammar", {**r, "backup_id": r["backup_id"].upper()},
         "malformed_restore_record"),
        ("state-id-str-subclass", {**r, "state_id": _Str(r["state_id"])},
         "malformed_restore_record"),
        ("bundle-non-str", {**r, "bundle": b"x"}, "malformed_restore_record"),
        ("backup-id-flip", {**r, "backup_id": _flip(r["backup_id"])}, "unverified_backup"),
        ("state-id-flip", {**r, "state_id": _flip(r["state_id"])}, "unverified_backup"),
        ("head-flip", {**r, "head": _flip(r["head"])}, "unverified_backup"),
        ("bundle-change", {**r, "bundle": r["bundle"] + " "}, "unverified_backup"),
        ("count-change", {**r, "entry_count": r["entry_count"] + 1}, "unverified_backup"),
        ("genesis-head", {**r, "head": backup.GENESIS}, "unverified_backup"),
    ]


@pytest.mark.parametrize("seed", range(6))
def test_r7_unverified_receipts_fail_before_the_parser(seed):
    receipt, _model = _positive(seed)
    for name, bad, failure in _receipt_tampers(receipt):
        before, calls = _snap(bad), []
        try:
            _fails(failure, _engine(lambda b, calls=calls: calls.append(b) or {}).restore, bad)
        except AssertionError as err:
            raise AssertionError(f"{name}: {err}") from None
        assert calls == [], name
        assert _snap(bad) == before, name


# -- R8: forged but verifiable receipts --------------------------------------------

def _state_tampers(model):
    """(name, state or (state, bundle, sid), failure) for forged receipts
    over a model with >= 2 records; honest parser."""
    keys = sorted(model)
    a, b = keys[0], keys[1]
    wrong_digest = copy.deepcopy(model)
    other = next(r for r in RECORDS if r["digest"] != model[a]["digest"])
    wrong_digest[a]["digest"] = other["digest"]
    clocks = copy.deepcopy(model)
    clocks[a]["snapshot_fen"] = clocks[a]["snapshot_fen"][:-3] + "5 9"
    swapped = {**model, a: model[b], b: model[a]}
    bad_variant = copy.deepcopy(model)
    bad_variant[a]["variant"] = "chess960"
    bad_fen = copy.deepcopy(model)
    bad_fen[a]["snapshot_fen"] = "8/8/8/8/8/8/8/8 w - - 0 1"
    foreign_key = {("x" + a): model[a], **{k: model[k] for k in keys[1:]}}
    return [
        ("wrong-digest", wrong_digest, None, None, "divergent_state"),
        ("non-canonical-clocks", clocks, None, None, "divergent_state"),
        ("swapped-keys", swapped, None, None, "divergent_state"),
        ("unknown-variant", bad_variant, None, None, "divergent_state"),
        ("illegal-position", bad_fen, None, None, "divergent_state"),
        ("foreign-key", foreign_key, None, None, "divergent_state"),
        ("state-id-of-other-state", model, None,
         _state_id({k: model[k] for k in keys[1:]}), "divergent_state"),
        ("reversed-bundle", model, _bundle(model, order=lambda s: sorted(s, reverse=True)),
         None, "divergent_parse"),
        ("field-order-bundle", model, "".join(
            f"{k}\n" + "|".join(f"{f}={model[k][f]}" for f in reversed(sorted(model[k])))
            + "\n" for k in sorted(model)), None, "divergent_parse"),
        ("no-trailing-newline", model, _bundle(model)[:-1], None, "divergent_parse"),
        ("unparseable", model, _bundle(model) + "dangling", None, "divergent_parse"),
    ]


@pytest.mark.parametrize("seed", range(8))
def test_r8_forged_receipts_fail_closed_typed(seed):
    receipt, model = _positive(seed)
    for name, state, bundle, sid, failure in _state_tampers(model):
        forged = _forged(receipt, state=state, bundle=bundle, sid=sid)
        before = _snap(forged)
        try:
            _fails(failure, _engine().restore, forged)
        except AssertionError as err:
            raise AssertionError(f"{name}: {err}") from None
        assert _snap(forged) == before, name


def test_r8b_restore_is_local_count_bound_from_both_sides():
    receipt, model = _positive(1)
    at_max = _forged(receipt, entry_count=COUNT_MAX)
    out = _engine().restore(at_max)
    assert out["state"] == model and out["backup_id"] == at_max["backup_id"]
    _fails("malformed_restore_record", _engine().restore,
           {**at_max, "entry_count": COUNT_MAX + 1})
    # a smaller forged state that verifies restores to exactly that state
    small = {k: model[k] for k in sorted(model)[:1]}
    assert _engine().restore(_forged(receipt, state=small))["state"] == small


# -- R9: parser-output tampers (honest receipt) ------------------------------------

def _rename(record, field, new):
    return {(new if k == field else k): v for k, v in record.items()}


def _output_tampers(parsed):
    keys = sorted(parsed)
    a, b = keys[0], keys[1]
    rec = parsed[a]

    def at(value):
        return {**parsed, a: value}

    extra = next(i for i in IDENTITIES if i not in parsed)
    return [
        ("key-str-subclass", {**{k: v for k, v in parsed.items() if k != a},
                              _Str(a): rec}),
        ("key-colliding", {**{k: v for k, v in parsed.items() if k != a},
                           _Colliding(a): rec}),
        ("key-non-str", {**{k: v for k, v in parsed.items() if k != a}, 1: rec}),
        ("key-swapped", {**parsed, a: parsed[b], b: parsed[a]}),
        ("record-dict-subclass", at(_Dict(rec))),
        ("record-list", at(list(rec.items()))),
        ("record-none", at(None)),
        ("record-str", at(a)),
        *[(f"record-renamed-{f}", at(_rename(rec, f, f + "x"))) for f in RECORD_FIELDS],
        *[(f"record-str-subclass-key-{f}", at(_rename(rec, f, _Str(f))))
          for f in RECORD_FIELDS],
        *[(f"record-colliding-key-{f}", at(_rename(rec, f, _Colliding(f))))
          for f in RECORD_FIELDS],
        *[(f"record-missing-{f}", at({k: v for k, v in rec.items() if k != f}))
          for f in RECORD_FIELDS],
        ("record-extra-field", at({**rec, "zz": "1"})),
        *[(f"record-str-subclass-value-{f}", at({**rec, f: _Str(rec[f])}))
          for f in RECORD_FIELDS],
        *[(f"record-{kind}-value-{f}", at({**rec, f: value}))
          for f in RECORD_FIELDS for kind, value in
          (("int", 1), ("none", None), ("bytes", rec[f].encode()))],
        ("record-wrong-digest", at({**rec, "digest": next(
            r["digest"] for r in RECORDS if r["digest"] != rec["digest"])})),
        ("record-clocks", at({**rec, "snapshot_fen": rec["snapshot_fen"][:-3] + "5 9"})),
        ("record-missing", {k: v for k, v in parsed.items() if k != a}),
        ("record-extra", {**parsed, extra: dict(RECORDS[IDENTITIES.index(extra)])}),
        ("empty", {}),
    ]


@pytest.mark.parametrize("seed", range(8))
def test_r9_invalid_parser_output_is_divergent_state(seed):
    receipt, _model = _positive(seed)
    parsed = restore.parse_bundle(receipt["bundle"])
    if len(parsed) == len(IDENTITIES):
        pytest.skip("no free identity")
    for name, output in _output_tampers(parsed):
        before = _snap(receipt)
        try:
            _fails("divergent_state", _engine(lambda b, o=output: o).restore, receipt)
        except AssertionError as err:
            raise AssertionError(f"{name}: {err}") from None
        assert _snap(receipt) == before, name


# -- R10: linked record rejections ---------------------------------------------------

@pytest.mark.parametrize("target", ["make_record", "record_identity"])
@pytest.mark.parametrize("error", [
    lambda: VariantError(code="malformed_request", message="x"),
    lambda: DigestError("malformed_position", "malformed_request"),
    lambda: ValueError("x"),
], ids=["variant", "digest", "value"])
def test_r10_linked_rejections_are_divergent_state(monkeypatch, target, error):
    receipt, _model = _positive(2)

    def boom(*args, **kwargs):
        raise error()

    monkeypatch.setattr(restore, target, boom)
    _fails("divergent_state", _engine().restore, receipt)


@pytest.mark.parametrize("target", ["make_record", "record_identity"])
def test_r10b_other_exceptions_propagate(monkeypatch, target):
    receipt, _model = _positive(2)

    class Unlinked(Exception):
        pass

    def boom(*args, **kwargs):
        raise Unlinked()

    monkeypatch.setattr(restore, target, boom)
    with pytest.raises(Unlinked):
        _engine().restore(receipt)


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
                       "pathlib", "pytest", "graph", "graph.node",
                       "graph.position_digest", "store", "tools.variant_runtime"}




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _snap(value)
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _snap(value) != before
