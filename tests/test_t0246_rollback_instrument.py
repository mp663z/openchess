"""T0246 Store/rollback/instrument: behavior-neutral diagnostics for the
production rollback engine (store.rollback) via store.rollback_instrument.

RollbackTracer wraps RollbackEngine.rollback. This file proves:
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
from store import rollback, wal
from store import rollback_instrument as ins

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "store" / "rollback_instrument.py").read_text()
ERR = rollback.RollbackError
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


def _raising(tail):
    raise ValueError("untrusted")


def _bad_token(tail):
    return "arc1:" + "0" * 64


def _engine(archiver=None):
    return rollback.RollbackEngine(archiver or rollback.archive_tail)


def OK_ARGS():
    return (_log([("put", 0), ("put", 1), ("delete", 0)]), {"target_sequence": 1})


REJECTS = {
    "log-not-list": (lambda: ((), {"target_sequence": 0}), None, "malformed_rollback_record"),
    "request-extra-field": (
        lambda: (_log([("put", 0)]), {"target_sequence": 0, "extra": 1}),
        None,
        "malformed_rollback_record",
    ),
    "target-not-int": (
        lambda: (_log([("put", 0)]), {"target_sequence": "0"}),
        None,
        "malformed_rollback_record",
    ),
    "target-beyond-log": (
        lambda: (_log([("put", 0)]), {"target_sequence": 2}),
        None,
        "unknown_target",
    ),
    "corrupt-source": (
        lambda: ([{"entry_id": "wal1:" + "0" * 64}], {"target_sequence": 0}),
        None,
        "corrupt_source",
    ),
    "raising-archiver": (
        lambda: (_log([("put", 0)]), {"target_sequence": 0}),
        _raising,
        "divergent_archive",
    ),
    "wrong-token": (
        lambda: (_log([("put", 0)]), {"target_sequence": 0}),
        _bad_token,
        "divergent_archive",
    ),
}


def test_traced_trajectory_matches_bare():
    bare, tracer = _engine(), ins.RollbackTracer(_engine())
    for script in _scripts(5):
        for target in range(len(script) + 1):
            bare_log, traced_log = _log(script), _log(script)
            a = bare.rollback(bare_log, {"target_sequence": target})
            b = tracer.rollback(traced_log, {"target_sequence": target})
            assert a == b and bare_log == traced_log
    assert tracer.records


def test_summary_pins_the_result():
    tracer = ins.RollbackTracer(_engine())
    log = _log([("put", 0), ("put", 1), ("delete", 0)])
    result = tracer.rollback(log, {"target_sequence": 1})
    (record,) = tracer.records
    assert record["result"] == result and result["truncated_count"] == 2
    assert len(log) == 1


# -- neutrality -------------------------------------------------------------


def test_result_is_the_wrapped_object():
    class Spy:
        out = {"marker": 1}

        def rollback(self, *args):
            return self.out

    assert ins.RollbackTracer(Spy()).rollback([], {}) is Spy.out


@pytest.mark.parametrize("name", sorted(REJECTS))
def test_rejections_are_the_same_typed_class(name):
    make_args, oracle, failure_class = REJECTS[name]
    bare = _bare(_engine(oracle).rollback, *make_args())
    assert bare[:2] == ("reject", failure_class)
    tracer = ins.RollbackTracer(_engine(oracle))
    args = make_args()
    before = copy.deepcopy(args)
    traced = _bare(tracer.rollback, *args)
    assert traced == bare
    assert args == before
    (record,) = tracer.records
    assert record["outcome"] == "reject" and record["operation"] == "rollback"
    assert (record["failure_class"], record["code"]) == bare[1:]
    assert record["arguments_unchanged"] is True


def test_same_exception_object_is_reraised():
    boom = ERR("malformed_rollback_record", "E")

    class Raiser:
        def rollback(self, *args):
            raise boom

    with pytest.raises(ERR) as caught:
        ins.RollbackTracer(Raiser()).rollback([], {})
    assert caught.value is boom


def test_crash_is_recorded_and_reraised():
    class Crasher:
        def rollback(self, *args):
            raise KeyError("x")

    tracer = ins.RollbackTracer(Crasher())
    with pytest.raises(KeyError):
        tracer.rollback([], {})
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


# -- diagnostics ------------------------------------------------------------


def test_one_record_per_call_in_seq_order():
    tracer = ins.RollbackTracer(_engine())
    for args in (OK_ARGS(), OK_ARGS()):
        tracer.rollback(*args)
    assert [r["seq"] for r in tracer.records] == [0, 1]
    assert all(r["outcome"] == "accept" and r["operation"] == "rollback" for r in tracer.records)


def test_records_are_an_isolated_append_only_view():
    tracer = ins.RollbackTracer(_engine())
    tracer.rollback(*OK_ARGS())
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "rollback"
    tracer.rollback(*OK_ARGS())
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer = ins.RollbackTracer(_engine())
        tracer.rollback(*OK_ARGS())
        tracer.rollback(*OK_ARGS())
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


def test_arguments_unchanged_false_when_wrapped_mutates_then_rejects():
    class Mutator:
        def rollback(self, first, *rest):
            first.clear()
            raise ERR("malformed_rollback_record", "E")

    tracer = ins.RollbackTracer(Mutator())
    args = OK_ARGS()
    with pytest.raises(ERR):
        tracer.rollback(*args)
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
        def rollback(self, *args):
            raise err

    tracer = ins.RollbackTracer(Raiser())
    with pytest.raises(Evil) as caught:
        tracer.rollback([], {})
    assert caught.value is err
    assert tracer.records[0]["failure_class"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    tracer = ins.RollbackTracer(_engine())
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.rollback(*OK_ARGS())
    assert tracer.records[0]["operation"] == "rollback"


def test_hostile_argument_records_an_opaque_marker():
    class Hostile(dict):
        def keys(self):
            raise RuntimeError("dispatched")

        def __iter__(self):
            raise RuntimeError("dispatched")

    tracer = ins.RollbackTracer(_engine())
    args = list(OK_ARGS())
    args[1] = Hostile()
    assert _bare(tracer.rollback, *args)[0] == "reject"
    assert tracer.records[0]["arguments"][1] == {"__opaque__": "Hostile"}


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = ins.RollbackTracer(_engine())
    args = list(OK_ARGS())
    args[0] = deep
    assert _bare(tracer.rollback, *args)[0] == "reject"
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
    "reject-unchanged-constant": (
        '"code": _attribute(error, "code"),\n                    "arguments_unchanged": [_snapshot(a) for a in args] == before,',  # noqa: E501
        '"code": _attribute(error, "code"),\n                    "arguments_unchanged": True,',
    ),
    "reject-unchanged-negated": (
        '"code": _attribute(error, "code"),\n                    "arguments_unchanged": [_snapshot(a) for a in args] == before,',  # noqa: E501
        '"code": _attribute(error, "code"),\n                    "arguments_unchanged": [_snapshot(a) for a in args] != before,',  # noqa: E501
    ),
    "drop-accept-result": (
        '"result": _summary(operation, result),',
        "",
    ),
    "return-none": (
        "        return result\n\n    def",
        "        return None\n\n    def",
    ),
    "drop-failure-class": (
        '"failure_class": _attribute(error, "failure_class"),',
        "",
    ),
    "append-no-snapshot": (
        "list.append(trace, _snapshot(record))",
        "list.append(trace, record)",
    ),
}


def _load(source):
    module = types.ModuleType("instrument_under_test")
    module.__file__ = str(ROOT / "store" / "rollback_instrument.py")
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


def _first_deviation_raw(module):
    """The name of the first semantic check the module fails, or None.

    Only assertion-style deviations count: an unexpected exception from the
    instrument propagates and errors the test instead of counting as a kill."""
    tracer = module.RollbackTracer(_engine())
    tracer.rollback(*OK_ARGS())
    tracer.rollback(*OK_ARGS())
    records = tracer.records
    if [r["seq"] for r in records] != [0, 1]:
        return "check-1"
    if any(r["outcome"] != "accept" for r in records):
        return "check-2"
    if "\n".join(json.dumps(r, sort_keys=True) for r in records) != tracer.to_jsonl():
        return "check-3"
    make_args, oracle, _ = next(iter(REJECTS.values()))
    rejecting = module.RollbackTracer(_engine(oracle))
    try:
        rejecting.rollback(*make_args())
        return "check-4"
    except ERR:
        pass
    last = rejecting.records[-1]
    if last.get("outcome") != "reject" or "code" not in last:
        return "check-5"
    spy = _Spy()
    probe = module.RollbackTracer(spy)
    given = ([0], [1], [2])
    probe._call("probe", given)
    if len(spy.seen) != 1 or len(spy.seen[0]) != 3:
        return "args-identity"
    if any(a is not b for a, b in zip(spy.seen[0], given, strict=True)):
        return "args-identity"
    if probe.records[0]["arguments"] != [[0], [1], [2]]:
        return "arguments-full"
    lost = module.RollbackTracer(spy)
    del lost._trace
    lost._call("probe", given)
    if lost.records != ({"__opaque__": "trace-append-failed"},):
        return "append-fallback"
    boom = KeyError("x")

    class Crasher:
        def rollback(self, *args):
            raise boom

    crash = module.RollbackTracer(Crasher())
    try:
        crash.rollback([], {})
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
    mutating = module.RollbackTracer(_MutatingCrash(boom))
    try:
        mutating._call("probe", ([0],))
    except KeyError:
        pass
    else:
        return "crash-swallowed"
    if mutating.records[-1].get("arguments_unchanged") is not False:
        return "crash-arguments-unchanged"
    return _extra_deviation(module)


def _error_class(module):
    found = [
        v
        for v in vars(module).values()
        if isinstance(v, type) and issubclass(v, Exception) and v.__name__.endswith("Error")
    ]
    assert found, "no typed error class in the instrument module"
    return found[0]


def _typed_error(cls):
    for args in (("malformed", "malformed_request"), ("malformed",), ("malformed_request", "m")):
        try:
            return cls(*args)
        except Exception:
            continue
    raise AssertionError("cannot build a typed error")


def _tracer_class(module):
    found = [
        v
        for v in vars(module).values()
        if isinstance(v, type) and v.__name__.endswith("Tracer") and hasattr(v, "_call")
    ]
    assert found, "no tracer class"
    return found[0]


class _Ret:
    """An engine whose every operation returns one fixed dict."""

    def __init__(self, out):
        self.out = out

    def __getattr__(self, name):
        def method(*args):
            return self.out

        return method


class _Rejecting:
    """An engine whose operations optionally mutate their first argument, then raise `err`."""

    def __init__(self, err, mutate):
        self.err, self.mutate = err, mutate

    def __getattr__(self, name):
        def method(*args):
            if self.mutate:
                args[0].append("junk")
            raise self.err

        return method


def _extra_deviation(module):
    tracer_cls, err_cls = _tracer_class(module), _error_class(module)
    out = {"field": 1}
    ret = tracer_cls(_Ret(out))
    if ret._call("probe", ([0],)) is not out:
        return "result-identity"
    record = ret.records[0]
    if "result" not in record or record["result"] != module._summary("probe", out):
        return "accept-summary"
    err = _typed_error(err_cls)
    for mutate, label in ((True, "reject-arguments-unchanged"), (False, "reject-unchanged-clean")):
        rej = tracer_cls(_Rejecting(err, mutate))
        try:
            rej._call("probe", ([0],))
        except err_cls as caught:
            if caught is not err:
                return "reject-reraise-identity"
        else:
            return "reject-swallowed"
        rec = rej.records[0]
        if rec.get("outcome") != "reject":
            return "reject-record-fields"
        want = module._attribute(err, "failure_class")
        if "failure_class" not in rec or rec["failure_class"] != want:
            return "reject-record-fields"
        want = module._attribute(err, "code")
        if "code" not in rec or rec["code"] != want:
            return "reject-record-fields"
        if rec.get("arguments_unchanged") is not (not mutate):
            return label
    probe = tracer_cls(_Ret(out))
    raw = {"a": [1]}
    probe._append_total(raw)
    raw["a"].append(2)
    if object.__getattribute__(probe, "_trace")[-1] != {"a": [1]}:
        return "append-aliasing"
    return None


def test_a_valid_call_the_instrument_refuses_is_an_assertion_failure():
    """Acceptance oracle: a valid call the instrument turns into a typed refusal is a
    semantic failure (AssertionError), never a raw typed error."""
    module = _from_source(SOURCE)
    err_cls = _error_class(module)
    try:
        assert _first_deviation(module) is None
    except err_cls as error:
        raise AssertionError("the instrument refused a valid call") from error


def _first_deviation(module):
    """Tag of the first failed semantic check, or None; a typed refusal of a valid call is the
    named deviation "valid-refused", any other exception propagates (an error, not a kill)."""
    try:
        return _first_deviation_raw(module)
    except _error_class(module):
        return "valid-refused"


def _is_red(module):
    return _first_deviation(module) is not None


def _from_source(source):
    module = types.ModuleType("instrument_under_test")
    module.__file__ = str(ROOT / "store" / "rollback_instrument.py")
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
    "reject-unchanged-constant": "reject-arguments-unchanged",
    "reject-unchanged-negated": "reject-arguments-unchanged",
    "drop-accept-result": "accept-summary",
    "return-none": "result-identity",
    "drop-failure-class": "reject-record-fields",
    "append-no-snapshot": "append-aliasing",
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
