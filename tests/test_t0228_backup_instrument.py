"""T0228 Store/backup/instrument: behavior-neutral diagnostics for the
production backup engine (store.backup) via store.backup_instrument.

BackupTracer wraps BackupEngine.backup and BackupEngine.verify. This file
proves:
- neutrality: over a seeded trajectory and every failure class, a traced
  call returns what the bare call returns or raises the same typed class
  (the same exception object) and never changes an argument;
- diagnostics: one record per call with operation, argument snapshot,
  outcome (accept with summary / reject with failure class and mapped code
  / crash with the error type) and arguments_unchanged on failure, in seq
  order; append-only, isolated views, deterministic JSONL;
- totality: hostile error attributes, a faulting trace container, a
  hostile log and pathological depth never change the wrapped result;
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
from store import backup, wal
from store import backup_instrument as bi

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "store" / "backup_instrument.py").read_text()
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


def _engine():
    return backup.BackupEngine(backup.serialize_bundle)


def _bare(fn, *args):
    try:
        return ("ok", fn(*args))
    except backup.BackupError as error:
        return ("reject", error.failure_class, error.code)
    except BaseException as error:  # noqa: BLE001
        return ("crash", type(error).__name__)


def _scripts(seed, n=20):
    rng = random.Random(seed)
    return [
        [(rng.choice(("put", "put", "delete")), rng.randrange(len(RECS))) for _ in range(k)]
        for k in range(n)
    ]


# -- neutrality -------------------------------------------------------------


def test_traced_trajectory_matches_bare():
    bare, tracer = _engine(), bi.BackupTracer(_engine())
    for script in _scripts(11):
        log = _log(script)
        before = copy.deepcopy(log)
        a = bare.backup(log)
        b = tracer.backup(log)
        assert a == b and log == before
        assert tracer.verify(b) == bare.verify(a)
    assert len(tracer.records) == 40


def test_result_is_the_wrapped_object():
    class Spy:
        out = {"backup_id": "x"}

        def backup(self, log):
            return self.out

    assert bi.BackupTracer(Spy()).backup([]) is Spy.out


def _receipt():
    return _engine().backup(_log([("put", 0), ("put", 1)]))


def _tampered(field, value):
    receipt = _receipt()
    receipt[field] = value
    return receipt


REJECTS = {
    "backup-non-list": ("backup", lambda: ("nope",), "malformed_backup_record"),
    "backup-corrupt-source": (
        "backup",
        lambda: ([{"entry_id": "wal1:" + "0" * 64}],),
        "corrupt_source",
    ),
    "verify-non-dict": ("verify", lambda: ("nope",), "malformed_backup_record"),
    "verify-extra-field": (
        "verify",
        lambda: ({**_receipt(), "extra": 1},),
        "malformed_backup_record",
    ),
    "verify-divergent-bundle": (
        "verify",
        lambda: (_tampered("bundle", "tampered"),),
        "divergent_backup",
    ),
    "verify-divergent-count": (
        "verify",
        lambda: (_tampered("entry_count", 5),),
        "divergent_backup",
    ),
}


@pytest.mark.parametrize("name", sorted(REJECTS))
def test_rejections_are_the_same_typed_class(name):
    operation, make_args, failure_class = REJECTS[name]
    bare = _bare(getattr(_engine(), operation), *copy.deepcopy(make_args()))
    assert bare[:2] == ("reject", failure_class)
    tracer = bi.BackupTracer(_engine())
    args = make_args()
    before = copy.deepcopy(args)
    traced = _bare(getattr(tracer, operation), *args)
    assert traced == bare
    assert args == before
    (record,) = tracer.records
    assert record["outcome"] == "reject" and record["operation"] == operation
    assert (record["failure_class"], record["code"]) == bare[1:]
    assert record["arguments_unchanged"] is True


def test_divergent_serializer_rejects_the_same_way():
    def bad(state):
        raise ValueError("untrusted")

    bare = backup.BackupEngine(bad)
    tracer = bi.BackupTracer(backup.BackupEngine(bad))
    log = _log([("put", 0)])
    expected = _bare(bare.backup, log)
    assert expected[:2] == ("reject", "divergent_snapshot")
    assert _bare(tracer.backup, log) == expected
    assert tracer.records[0]["failure_class"] == "divergent_snapshot"


def test_same_exception_object_is_reraised():
    boom = backup.BackupError("malformed_backup_record", "E")

    class Raiser:
        def verify(self, receipt):
            raise boom

    with pytest.raises(backup.BackupError) as caught:
        bi.BackupTracer(Raiser()).verify({})
    assert caught.value is boom


def test_crash_is_recorded_and_reraised():
    class Crasher:
        def verify(self, receipt):
            raise KeyError("x")

    tracer = bi.BackupTracer(Crasher())
    with pytest.raises(KeyError):
        tracer.verify({})
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


# -- diagnostics ------------------------------------------------------------


def test_one_record_per_call_in_seq_order_with_summaries():
    tracer = bi.BackupTracer(_engine())
    receipt = tracer.backup(_log([("put", 0), ("put", 1), ("delete", 0)]))
    tracer.verify(receipt)
    first, second = tracer.records
    assert (first["seq"], first["operation"]) == (0, "backup")
    assert (second["seq"], second["operation"]) == (1, "verify")
    assert first["result"] == {
        "backup_id": receipt["backup_id"],
        "head": receipt["head"],
        "state_id": receipt["state_id"],
        "entry_count": 3,
        "bundle_chars": len(receipt["bundle"]),
    }
    assert second["result"] == first["result"]
    assert first["outcome"] == second["outcome"] == "accept"


def test_records_are_an_isolated_append_only_view():
    tracer = bi.BackupTracer(_engine())
    tracer.backup([])
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "backup"
    tracer.backup([])
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer = bi.BackupTracer(_engine())
        tracer.verify(tracer.backup(_log([("put", 2)])))
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


def test_arguments_unchanged_false_when_wrapped_mutates_then_rejects():
    class Mutator:
        def backup(self, log):
            log.append("junk")
            raise backup.BackupError("corrupt_source", "E")

    tracer = bi.BackupTracer(Mutator())
    with pytest.raises(backup.BackupError):
        tracer.backup([])
    assert tracer.records[0]["arguments_unchanged"] is False


# -- totality ---------------------------------------------------------------


def test_hostile_error_attributes_do_not_change_the_raise():
    class Evil(backup.BackupError):
        @property
        def failure_class(self):
            raise RuntimeError("nope")

        @failure_class.setter
        def failure_class(self, value):
            pass

    err = Evil("x", "y")

    class Raiser:
        def verify(self, receipt):
            raise err

    tracer = bi.BackupTracer(Raiser())
    with pytest.raises(Evil) as caught:
        tracer.verify({})
    assert caught.value is err
    assert tracer.records[0]["failure_class"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    tracer = bi.BackupTracer(_engine())
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.backup([])["entry_count"] == 0
    assert tracer.records[0]["operation"] == "backup"


def test_hostile_log_records_an_opaque_marker():
    class LogList(list):
        def __iter__(self):
            raise RuntimeError("dispatched")

    tracer = bi.BackupTracer(_engine())
    assert _bare(tracer.backup, LogList())[0] == "reject"
    assert tracer.records[0]["arguments"][0] == {"__opaque__": "LogList"}


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = bi.BackupTracer(_engine())
    assert _bare(tracer.backup, deep)[0] == "reject"
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
    module.__file__ = str(ROOT / "store" / "backup_instrument.py")
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
    tracer = module.BackupTracer(_engine())
    receipt = tracer.backup(_log([("put", 0)]))
    tracer.verify(receipt)
    records = tracer.records
    if [r["seq"] for r in records] != [0, 1]:
        return "check-1"
    if any(r["outcome"] != "accept" for r in records):
        return "check-2"
    if "\n".join(json.dumps(r, sort_keys=True) for r in records) != tracer.to_jsonl():
        return "check-3"
    try:
        tracer.backup("nope")
        return "check-4"
    except backup.BackupError:
        pass
    last = tracer.records[-1]
    if last.get("outcome") != "reject" or "code" not in last:
        return "check-5"
    spy = _Spy()
    probe = module.BackupTracer(spy)
    given = ([0], [1], [2])
    probe._call("probe", given)
    if len(spy.seen) != 1 or len(spy.seen[0]) != 3:
        return "args-identity"
    if any(a is not b for a, b in zip(spy.seen[0], given, strict=True)):
        return "args-identity"
    if probe.records[0]["arguments"] != [[0], [1], [2]]:
        return "arguments-full"
    lost = module.BackupTracer(spy)
    del lost._trace
    lost._call("probe", given)
    if lost.records != ({"__opaque__": "trace-append-failed"},):
        return "append-fallback"
    boom = KeyError("x")

    class Crasher:
        def verify(self, *args):
            raise boom

    crash = module.BackupTracer(Crasher())
    try:
        crash.verify({})
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
    mutating = module.BackupTracer(_MutatingCrash(boom))
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
    module.__file__ = str(ROOT / "store" / "backup_instrument.py")
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
