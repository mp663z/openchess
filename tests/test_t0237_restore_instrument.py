"""T0237 Store/restore/instrument: behavior-neutral diagnostics for the
production restore engine (store.restore) via store.restore_instrument.

RestoreTracer wraps RestoreEngine.restore. This file proves:
- neutrality: over a seeded trajectory and every failure class, a traced
  call returns what the bare call returns or raises the same typed class
  (the same exception object) and never changes an argument beyond what
  the bare call itself changes;
- diagnostics: one record per call with operation, argument snapshot,
  outcome (accept with summary / reject with failure class and mapped code
  / crash with the error type) and arguments_unchanged on failure, in seq
  order; append-only, isolated views, deterministic JSONL;
- totality: hostile error attributes, a faulting trace container, a
  hostile argument and pathological depth never change the wrapped result;
- one-edit mutants of the instrument are each red on their own check.
"""

from __future__ import annotations

import copy
import json
import random
import types
from pathlib import Path

import pytest

from graph.node import make_record, record_identity
from store import backup, restore, wal
from store import restore_instrument as ins

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "store" / "restore_instrument.py").read_text()
ERR = restore.RestoreError
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
)
RECS = tuple(make_record("standard", f) for f in FENS)


def _request(op, index):
    record = dict(RECS[index])
    return {"op": op, "payload": {"identity": record_identity(record), "record": record}}


def _log(script):
    engine, log = wal.WalEngine(wal.canonical_payload), []
    for op, index in script:
        engine.append(log, _request(op, index))
    return log


def _scripts(seed, n=12):
    rng = random.Random(seed)
    return [
        [(rng.choice(("put", "put", "delete")), rng.randrange(len(RECS))) for _ in range(k)]
        for k in range(1, n + 1)
    ]


def _bare(fn, *args):
    try:
        return ("ok", fn(*args))
    except ERR as error:
        return ("reject", error.failure_class, error.code)
    except BaseException as error:  # noqa: BLE001
        return ("crash", type(error).__name__)


def _receipt(script=(("put", 0), ("put", 1))):
    return backup.BackupEngine(backup.serialize_bundle).backup(_log(script))


def _raising(bundle):
    raise ValueError("untrusted")


def _empty(bundle):
    return {}


def _engine(parser=None):
    return restore.RestoreEngine(parser or restore.parse_bundle)


def OK_ARGS():
    return (_receipt(),)


def _tamper(field, value):
    receipt = _receipt()
    receipt[field] = value
    return receipt


REJECTS = {
    "non-dict": (lambda: ("nope",), None, "malformed_restore_record"),
    "extra-field": (lambda: ({**_receipt(), "extra": 1},), None, "malformed_restore_record"),
    "tampered-bundle": (lambda: (_tamper("bundle", "tampered"),), None, "unverified_backup"),
    "divergent-count": (lambda: (_tamper("entry_count", 5),), None, "unverified_backup"),
    "raising-parser": (lambda: (_receipt(),), _raising, "divergent_parse"),
    "empty-parser": (lambda: (_receipt(),), _empty, "divergent_state"),
}


def test_traced_trajectory_matches_bare():
    bare, tracer = _engine(), ins.RestoreTracer(_engine())
    for script in _scripts(11):
        receipt = _receipt(script)
        before = copy.deepcopy(receipt)
        a = bare.restore(copy.deepcopy(receipt))
        b = tracer.restore(receipt)
        assert a == b and receipt == before
    assert len(tracer.records) == 12


def test_summary_pins_the_result():
    tracer = ins.RestoreTracer(_engine())
    receipt = _receipt([("put", 0), ("put", 1), ("delete", 0)])
    result = tracer.restore(receipt)
    (record,) = tracer.records
    assert record["result"] == {
        "restore_id": result["restore_id"],
        "backup_id": receipt["backup_id"],
        "state_id": receipt["state_id"],
        "identities": 1,
    }


# -- neutrality -------------------------------------------------------------


def test_result_is_the_wrapped_object():
    class Spy:
        out = {"marker": 1}

        def restore(self, *args):
            return self.out

    assert ins.RestoreTracer(Spy()).restore({}) is Spy.out


@pytest.mark.parametrize("name", sorted(REJECTS))
def test_rejections_are_the_same_typed_class(name):
    make_args, oracle, failure_class = REJECTS[name]
    bare = _bare(_engine(oracle).restore, *make_args())
    assert bare[:2] == ("reject", failure_class)
    tracer = ins.RestoreTracer(_engine(oracle))
    args = make_args()
    before = copy.deepcopy(args)
    traced = _bare(tracer.restore, *args)
    assert traced == bare
    assert args == before
    (record,) = tracer.records
    assert record["outcome"] == "reject" and record["operation"] == "restore"
    assert (record["failure_class"], record["code"]) == bare[1:]
    assert record["arguments_unchanged"] is True


def test_same_exception_object_is_reraised():
    boom = ERR("malformed_restore_record", "E")

    class Raiser:
        def restore(self, *args):
            raise boom

    with pytest.raises(ERR) as caught:
        ins.RestoreTracer(Raiser()).restore({})
    assert caught.value is boom


def test_crash_is_recorded_and_reraised():
    class Crasher:
        def restore(self, *args):
            raise KeyError("x")

    tracer = ins.RestoreTracer(Crasher())
    with pytest.raises(KeyError):
        tracer.restore({})
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


# -- diagnostics ------------------------------------------------------------


def test_one_record_per_call_in_seq_order():
    tracer = ins.RestoreTracer(_engine())
    for args in (OK_ARGS(), OK_ARGS()):
        tracer.restore(*args)
    assert [r["seq"] for r in tracer.records] == [0, 1]
    assert all(r["outcome"] == "accept" and r["operation"] == "restore" for r in tracer.records)


def test_records_are_an_isolated_append_only_view():
    tracer = ins.RestoreTracer(_engine())
    tracer.restore(*OK_ARGS())
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "restore"
    tracer.restore(*OK_ARGS())
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer = ins.RestoreTracer(_engine())
        tracer.restore(*OK_ARGS())
        tracer.restore(*OK_ARGS())
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


def test_arguments_unchanged_false_when_wrapped_mutates_then_rejects():
    class Mutator:
        def restore(self, first, *rest):
            first.clear()
            raise ERR("malformed_restore_record", "E")

    tracer = ins.RestoreTracer(Mutator())
    args = OK_ARGS()
    with pytest.raises(ERR):
        tracer.restore(*args)
    assert tracer.records[0]["arguments_unchanged"] is False


# -- totality ---------------------------------------------------------------


def test_hostile_error_attributes_do_not_change_the_raise():
    class Evil(ERR):
        @property
        def failure_class(self):
            raise RuntimeError("nope")

        @failure_class.setter
        def failure_class(self, value):
            pass

    err = Evil("x", "y")

    class Raiser:
        def restore(self, *args):
            raise err

    tracer = ins.RestoreTracer(Raiser())
    with pytest.raises(Evil) as caught:
        tracer.restore({})
    assert caught.value is err
    assert tracer.records[0]["failure_class"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    tracer = ins.RestoreTracer(_engine())
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.restore(*OK_ARGS())
    assert tracer.records[0]["operation"] == "restore"


def test_hostile_argument_records_an_opaque_marker():
    class Hostile(dict):
        def keys(self):
            raise RuntimeError("dispatched")

        def __iter__(self):
            raise RuntimeError("dispatched")

    tracer = ins.RestoreTracer(_engine())
    args = list(OK_ARGS())
    args[0] = Hostile()
    assert _bare(tracer.restore, *args)[0] == "reject"
    assert tracer.records[0]["arguments"][0] == {"__opaque__": "Hostile"}


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = ins.RestoreTracer(_engine())
    args = list(OK_ARGS())
    args[0] = deep
    assert _bare(tracer.restore, *args)[0] == "reject"
    assert "max-depth-exceeded" in json.dumps(tracer.records[0]["arguments"])


# -- one-edit mutants of the instrument -------------------------------------

MUTANTS = {
    "swallow-reject": (
        "            raise\n        except BaseException as error:",
        "            return None\n        except BaseException as error:",
    ),
    "drop-crash-reraise": (
        "                }\n            )\n            raise\n        self._append_total(",
        "                }\n            )\n            return None\n        self._append_total(",
    ),
    "seq-constant": ('"seq": self._seq()', '"seq": 0'),
    "wrong-outcome-label": ('"outcome": "accept"', '"outcome": "ok"'),
    "unsorted-jsonl": (
        "json.dumps(record, sort_keys=True))\n            except",
        "json.dumps(record))\n            except",
    ),
    "drop-code": ('"code": _attribute(error, "code"),', ""),
    "copy-args": (
        "getattr(self._engine, operation)(*args)",
        'getattr(self._engine, operation)(*__import__("copy").deepcopy(args))',
    ),
    "truncate-arguments": (
        '"arguments": before}',
        '"arguments": before[:1]}',
    ),
    "crash-unchanged-constant": (
        '"error_type": _type_name(type(error)),\n'
        '                    "arguments_unchanged": [_snapshot(a) for a in args] == before,',
        '"error_type": _type_name(type(error)),\n                    "arguments_unchanged": True,',
    ),
    "append-fallback-empty": (
        '[_opaque("trace-append-failed")]',
        "[]",
    ),
}


def _load(source):
    module = types.ModuleType("instrument_under_test")
    module.__file__ = str(ROOT / "store" / "restore_instrument.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _mutant(label):
    before, after = MUTANTS[label]
    assert SOURCE.count(before) == 1, label
    return _load(SOURCE.replace(before, after))


class _Spy:
    """An engine that records the exact argument objects it receives."""

    def __init__(self):
        self.seen = []

    def __getattr__(self, name):
        def method(*args):
            self.seen.append(args)

        return method


class _MutatingCrash:
    """An engine whose every operation mutates its first argument, then crashes."""

    def __init__(self, boom):
        self.boom = boom

    def __getattr__(self, name):
        def method(*args):
            args[0].append("junk")
            raise self.boom

        return method


def _first_deviation(module):
    """The name of the first semantic check the module fails, or None.

    Only assertion-style deviations count: an unexpected exception from the
    instrument propagates and errors the test instead of counting as a kill."""
    tracer = module.RestoreTracer(_engine())
    tracer.restore(*OK_ARGS())
    tracer.restore(*OK_ARGS())
    records = tracer.records
    if [r["seq"] for r in records] != [0, 1]:
        return "check-1"
    if any(r["outcome"] != "accept" for r in records):
        return "check-2"
    if "\n".join(json.dumps(r, sort_keys=True) for r in records) != tracer.to_jsonl():
        return "check-3"
    make_args, oracle, _ = next(iter(REJECTS.values()))
    rejecting = module.RestoreTracer(_engine(oracle))
    try:
        rejecting.restore(*make_args())
        return "check-4"
    except ERR:
        pass
    last = rejecting.records[-1]
    if last.get("outcome") != "reject" or "code" not in last:
        return "check-5"
    spy = _Spy()
    probe = module.RestoreTracer(spy)
    given = ([0], [1], [2])
    probe._call("probe", given)
    if len(spy.seen) != 1 or len(spy.seen[0]) != 3:
        return "args-identity"
    if any(a is not b for a, b in zip(spy.seen[0], given, strict=True)):
        return "args-identity"
    if probe.records[0]["arguments"] != [[0], [1], [2]]:
        return "arguments-full"
    lost = module.RestoreTracer(spy)
    del lost._trace
    lost._call("probe", given)
    if lost.records != ({"__opaque__": "trace-append-failed"},):
        return "append-fallback"
    boom = KeyError("x")

    class Crasher:
        def restore(self, *args):
            raise boom

    crash = module.RestoreTracer(Crasher())
    try:
        crash.restore({})
    except KeyError as caught:
        if caught is not boom:
            return "crash-reraise-identity"
    else:
        return "crash-swallowed"
    if (
        crash.records[-1].get("outcome") != "crash"
        or crash.records[-1].get("error_type") != "KeyError"
    ):
        return "crash-record"
    mutating = module.RestoreTracer(_MutatingCrash(boom))
    try:
        mutating._call("probe", ([0],))
    except KeyError:
        pass
    else:
        return "crash-swallowed"
    if mutating.records[-1].get("arguments_unchanged") is not False:
        return "crash-arguments-unchanged"
    return None


def _is_red(module):
    return _first_deviation(module) is not None


def _from_source(source):
    module = types.ModuleType("instrument_under_test")
    module.__file__ = str(ROOT / "store" / "restore_instrument.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _edited(label):
    before, after = MUTANTS[label]
    assert SOURCE.count(before) == 1, label
    return _from_source(SOURCE.replace(before, after))


def test_unmutated_instrument_is_green_on_the_mutant_check():
    assert _first_deviation(_from_source(SOURCE)) is None


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_mutant_is_red(label):
    assert _is_red(_edited(label)) is True, label
    assert isinstance(_first_deviation(_edited(label)), str), label


# the check that must catch each of these, so a kill is never an accident
EXPECTED_KILL = {
    "drop-crash-reraise": "crash-swallowed",
    "copy-args": "args-identity",
    "truncate-arguments": "arguments-full",
    "crash-unchanged-constant": "crash-arguments-unchanged",
    "append-fallback-empty": "append-fallback",
}


@pytest.mark.parametrize("label", sorted(EXPECTED_KILL))
def test_mutant_dies_on_its_own_check(label):
    assert _first_deviation(_edited(label)) == EXPECTED_KILL[label]


def test_a_crashing_instrument_is_an_error_not_a_kill():
    broken = SOURCE.replace('"outcome": "crash",', '"outcome": undefined_name,')
    assert broken != SOURCE
    with pytest.raises(NameError):
        _first_deviation(_from_source(broken))
