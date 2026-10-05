"""T0219 Store/WAL/instrument: behavior-neutral diagnostics for the
production WAL (store.wal) via store.wal_instrument.

WalTracer wraps WalEngine.append and WalEngine.replay. This file proves:
- neutrality: over a seeded trajectory and every failure class, a traced
  call returns what the bare call returns or raises the same typed class
  (the same exception object) and never changes an argument beyond what
  the bare call itself changes;
- diagnostics: one record per call with operation, argument snapshot,
  outcome (accept with summary / reject with failure class and mapped code
  / crash with the error type) and arguments_unchanged on failure, in seq
  order; append-only, isolated views, deterministic JSONL;
- totality: hostile error attributes, a faulting trace container and
  pathological depth never change the wrapped result; a hostile log or
  request records an opaque marker instead of dispatching;
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
from store import wal
from store import wal_instrument as wi

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "store" / "wal_instrument.py").read_text()
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


def _engine():
    return wal.WalEngine(wal.canonical_payload)


def _bare(fn, *args):
    try:
        return ("ok", fn(*args))
    except wal.WalError as error:
        return ("reject", error.failure_class, error.code)
    except BaseException as error:  # noqa: BLE001
        return ("crash", type(error).__name__)


def _script(seed, n=30):
    rng = random.Random(seed)
    return [(rng.choice(("put", "put", "delete")), rng.randrange(len(RECS))) for _ in range(n)]


# -- neutrality -------------------------------------------------------------


def test_traced_trajectory_matches_bare():
    bare_log, traced_log = [], []
    bare, tracer = _engine(), wi.WalTracer(_engine())
    for op, index in _script(7):
        a = _bare(bare.append, bare_log, _request(op, index))
        b = _bare(tracer.append, traced_log, _request(op, index))
        assert a[0] == b[0] == "ok"
        assert a[1] == b[1]
    assert bare_log == traced_log
    assert tracer.replay(traced_log) == bare.replay(bare_log)
    assert len(tracer.records) == 31


def test_result_is_the_wrapped_object():
    class Spy:
        def __init__(self):
            self.out = {"entry_id": "x", "sequence": 1, "op": "put"}

        def append(self, log, request):
            return self.out

    spy = Spy()
    assert wi.WalTracer(spy).append([], {}) is spy.out


CORRUPT = {
    "bad-request-type": lambda: ([], "nope"),
    "bad-log-type": lambda: ((), _request("put", 0)),
    "unknown-op": lambda: ([], {"op": "frobnicate", "payload": _request("put", 0)["payload"]}),
    "extra-field": lambda: ([], {**_request("put", 0), "extra": 1}),
}


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_rejections_are_the_same_typed_class(name):
    log, request = CORRUPT[name]()
    bare = _bare(_engine().append, copy.deepcopy(log), copy.deepcopy(request))
    assert bare[0] == "reject"
    tracer = wi.WalTracer(_engine())
    traced = _bare(tracer.append, copy.deepcopy(log), copy.deepcopy(request))
    assert traced == bare
    (record,) = tracer.records
    assert record["outcome"] == "reject"
    assert (record["failure_class"], record["code"]) == bare[1:]
    assert record["arguments_unchanged"] is True


def test_tampered_chain_replay_rejects_and_log_is_unchanged():
    engine, tracer = _engine(), wi.WalTracer(_engine())
    log = []
    for index in range(3):
        engine.append(log, _request("put", index))
    log[1]["entry_id"] = "wal1:" + "0" * 64
    before = copy.deepcopy(log)
    bare = _bare(engine.replay, log)
    assert bare[:2] == ("reject", "corrupt_chain")
    assert _bare(tracer.replay, log) == bare
    assert log == before
    assert tracer.records[0]["failure_class"] == "corrupt_chain"
    assert tracer.records[0]["arguments_unchanged"] is True


def test_same_exception_object_is_reraised():
    boom = wal.WalError("malformed_wal_entry", "E_MALFORMED")

    class Raiser:
        def replay(self, log):
            raise boom

    with pytest.raises(wal.WalError) as caught:
        wi.WalTracer(Raiser()).replay([])
    assert caught.value is boom


def test_crash_is_recorded_and_reraised():
    class Crasher:
        def replay(self, log):
            raise KeyError("x")

    tracer = wi.WalTracer(Crasher())
    with pytest.raises(KeyError):
        tracer.replay([])
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


# -- diagnostics ------------------------------------------------------------


def test_one_record_per_call_in_seq_order_with_summaries():
    tracer, log = wi.WalTracer(_engine()), []
    tracer.append(log, _request("put", 0))
    tracer.append(log, _request("put", 1))
    tracer.append(log, _request("delete", 0))
    replayed = tracer.replay(log)
    records = tracer.records
    assert [r["seq"] for r in records] == [0, 1, 2, 3]
    assert [r["operation"] for r in records] == ["append"] * 3 + ["replay"]
    assert [r["result"]["sequence"] for r in records[:3]] == [1, 2, 3]
    assert [r["result"]["op"] for r in records[:3]] == ["put", "put", "delete"]
    assert records[3]["result"] == {
        "head": replayed["head"],
        "applied": 3,
        "state_id": replayed["state_id"],
        "identities": 1,
    }
    assert all(r["outcome"] == "accept" for r in records)


def test_records_are_an_isolated_append_only_view():
    tracer = wi.WalTracer(_engine())
    tracer.replay([])
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "replay"
    tracer.replay([])
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer, log = wi.WalTracer(_engine()), []
        tracer.append(log, _request("put", 2))
        tracer.replay(log)
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


def test_arguments_unchanged_false_when_wrapped_mutates_then_rejects():
    class Mutator:
        def append(self, log, request):
            log.append("junk")
            raise wal.WalError("malformed_wal_entry", "E")

    tracer = wi.WalTracer(Mutator())
    with pytest.raises(wal.WalError):
        tracer.append([], {})
    assert tracer.records[0]["arguments_unchanged"] is False


# -- totality ---------------------------------------------------------------


def test_hostile_error_attributes_do_not_change_the_raise():
    class Evil(wal.WalError):
        @property
        def failure_class(self):
            raise RuntimeError("nope")

        @failure_class.setter
        def failure_class(self, value):
            pass

    err = Evil("x", "y")

    class Raiser:
        def replay(self, log):
            raise err

    tracer = wi.WalTracer(Raiser())
    with pytest.raises(Evil) as caught:
        tracer.replay([])
    assert caught.value is err
    assert tracer.records[0]["failure_class"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    tracer = wi.WalTracer(_engine())
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.replay([])["applied"] == 0
    assert tracer.records[0]["operation"] == "replay"


def test_hostile_log_and_request_record_opaque_markers():
    class LogList(list):
        def __iter__(self):
            raise RuntimeError("dispatched")

    class Req(dict):
        def keys(self):
            raise RuntimeError("dispatched")

    tracer = wi.WalTracer(_engine())
    assert _bare(tracer.append, LogList(), _request("put", 0))[0] == "reject"
    assert _bare(tracer.append, [], Req(_request("put", 0)))[0] == "reject"
    assert [r["outcome"] for r in tracer.records] == ["reject", "reject"]
    assert tracer.records[0]["arguments"][0] == {"__opaque__": "LogList"}
    assert tracer.records[1]["arguments"][1] == {"__opaque__": "Req"}


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = wi.WalTracer(_engine())
    assert _bare(tracer.replay, deep)[0] == "reject"
    assert "max-depth-exceeded" in json.dumps(tracer.records[0]["arguments"])


# -- one-edit mutants of the instrument -------------------------------------

MUTANTS = {
    "swallow-reject": (
        "            raise\n        except BaseException as error:",
        "            return None\n        except BaseException as error:",
    ),
    "drop-crash-reraise": (
        "                }\n            )\n            raise\n        self._append_total(",
        "                }\n            )\n        self._append_total(",
    ),
    "seq-constant": ('"seq": self._seq()', '"seq": 0'),
    "wrong-outcome-label": ('"outcome": "accept"', '"outcome": "ok"'),
    "unsorted-jsonl": (
        "json.dumps(record, sort_keys=True))\n            except",
        "json.dumps(record))\n            except",
    ),
    "drop-code": ('"code": _attribute(error, "code"),', ""),
}


def _mutant(label):
    before, after = MUTANTS[label]
    assert SOURCE.count(before) == 1, label
    module = types.ModuleType("mutant_" + label.replace("-", "_"))
    module.__file__ = str(ROOT / "store" / "wal_instrument.py")
    exec(compile(SOURCE.replace(before, after), module.__file__, "exec"), module.__dict__)
    return module


def _mutant_is_red(module):
    try:
        tracer = module.WalTracer(_engine())
        log = []
        tracer.append(log, _request("put", 0))
        tracer.append(log, _request("put", 1))
        if [r["seq"] for r in tracer.records] != [0, 1]:
            return True
        if any(r["outcome"] != "accept" for r in tracer.records):
            return True
        if [json.dumps(r) for r in tracer.records] != [
            json.dumps(r) for r in map(dict, tracer.records)
        ] or "\n".join(json.dumps(r, sort_keys=True) for r in tracer.records) != tracer.to_jsonl():
            return True
        try:
            tracer.append(log, "nope")
            return True
        except wal.WalError:
            pass
        last = tracer.records[-1]
        if last.get("outcome") != "reject" or "code" not in last:
            return True

        class Crasher:
            def replay(self, log):
                raise KeyError("x")

        crash = module.WalTracer(Crasher())
        try:
            crash.replay([])
            return True
        except KeyError:
            return False
    except BaseException:  # noqa: BLE001
        return True


def test_unmutated_instrument_is_green_on_the_mutant_check():
    assert not _mutant_is_red(_mutant_identity())


def _mutant_identity():
    module = types.ModuleType("identity")
    module.__file__ = str(ROOT / "store" / "wal_instrument.py")
    exec(compile(SOURCE, module.__file__, "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_mutant_is_red(label):
    assert _mutant_is_red(_mutant(label)), label
