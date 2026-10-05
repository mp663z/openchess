"""T0156 Graph/provenance/instrument: behavior-neutral diagnostics for the
production provenance engine (graph.provenance) via graph.provenance_instrument.

ProvenanceTracer wraps ProvenanceTable.insert. This file proves:
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

from graph import provenance
from graph import provenance_instrument as ins
from graph.node import make_record

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "graph" / "provenance_instrument.py").read_text()
ERR = provenance.ProvenanceError
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
)
RECS = tuple(make_record("standard", f) for f in FENS)


def _bare(fn, *args):
    try:
        return ("ok", fn(*args))
    except ERR as error:
        return ("reject", error.failure_class, error.code)
    except BaseException as error:  # noqa: BLE001
        return ("crash", type(error).__name__)


def _mk(cls):
    return cls("malformed_provenance_record")


def _engine(oracle=None):
    return provenance.ProvenanceTable()


def _record(game="pgn:local-game-1", source="pgn-file", fen=0):
    return {
        "target_kind": "transposition_node",
        "target": {"variant": "standard", "snapshot_fen": FENS[fen]},
        "sources": [
            {"source_id": source, "game_id": game, "first_observed_at": "2026-09-02T08:30:00Z"}
        ],
    }


def OK_ARGS():
    return (_record(),)


REJECTS = {
    "record-not-dict": (lambda: ("nope",), None, "malformed_provenance_record"),
    "unknown-kind": (
        lambda: ({**_record(), "target_kind": "nonesuch"},),
        None,
        "unknown_target_kind",
    ),
    "extra-field": (
        lambda: ({**_record(), "extra": 1},),
        None,
        "malformed_provenance_record",
    ),
    "empty-sources": (
        lambda: ({**_record(), "sources": []},),
        None,
        "malformed_provenance_record",
    ),
}


def test_traced_trajectory_matches_bare():
    bare, tracer = _engine(), ins.ProvenanceTracer(_engine())
    rng = random.Random(3)
    for _ in range(30):
        record = _record(game=f"pgn:g{rng.randrange(4)}", fen=rng.randrange(len(FENS)))
        assert _bare(bare.insert, copy.deepcopy(record)) == _bare(tracer.insert, record)
    assert bare.serialize() == tracer._table.serialize()
    assert len(tracer.records) == 30


def test_merge_is_traced_and_matches_bare():
    left, right = _engine(), _engine()
    left.insert(_record(fen=0))
    right.insert(_record(fen=1))
    tracer = ins.ProvenanceTracer(left)
    assert tracer.merge(right) is left
    record = tracer.records[0]
    assert record["operation"] == "merge" and record["result"] == {"records": 2}


def test_merge_bad_source_rejects_the_same_way():
    tracer = ins.ProvenanceTracer(_engine())
    assert _bare(tracer.merge, object()) == _bare(_engine().merge, object())
    assert tracer.records[0]["outcome"] == "reject"


def test_summary_pins_the_result():
    tracer = ins.ProvenanceTracer(_engine())
    tracer.insert(_record(game="pgn:a"))
    tracer.insert(_record(game="pgn:b"))
    first, second = tracer.records
    assert first["result"] == {"target_kind": "transposition_node", "sources": 1}
    assert second["result"] == {"target_kind": "transposition_node", "sources": 2}


# -- neutrality -------------------------------------------------------------


def test_result_is_the_wrapped_object():
    class Spy:
        out = {"marker": 1}

        def insert(self, *args):
            return self.out

    assert ins.ProvenanceTracer(Spy()).insert({}) is Spy.out


@pytest.mark.parametrize("name", sorted(REJECTS))
def test_rejections_are_the_same_typed_class(name):
    make_args, oracle, failure_class = REJECTS[name]
    bare = _bare(_engine(oracle).insert, *make_args())
    assert bare[:2] == ("reject", failure_class)
    tracer = ins.ProvenanceTracer(_engine(oracle))
    args = make_args()
    before = copy.deepcopy(args)
    traced = _bare(tracer.insert, *args)
    assert traced == bare
    assert args == before
    (record,) = tracer.records
    assert record["outcome"] == "reject" and record["operation"] == "insert"
    assert (record["failure_class"], record["code"]) == bare[1:]
    assert record["arguments_unchanged"] is True


def test_same_exception_object_is_reraised():
    boom = _mk(ERR)

    class Raiser:
        def insert(self, *args):
            raise boom

    with pytest.raises(ERR) as caught:
        ins.ProvenanceTracer(Raiser()).insert({})
    assert caught.value is boom


def test_crash_is_recorded_and_reraised():
    class Crasher:
        def insert(self, *args):
            raise KeyError("x")

    tracer = ins.ProvenanceTracer(Crasher())
    with pytest.raises(KeyError):
        tracer.insert({})
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


# -- diagnostics ------------------------------------------------------------


def test_one_record_per_call_in_seq_order():
    tracer = ins.ProvenanceTracer(_engine())
    for args in (OK_ARGS(), OK_ARGS()):
        tracer.insert(*args)
    assert [r["seq"] for r in tracer.records] == [0, 1]
    assert all(r["outcome"] == "accept" and r["operation"] == "insert" for r in tracer.records)


def test_records_are_an_isolated_append_only_view():
    tracer = ins.ProvenanceTracer(_engine())
    tracer.insert(*OK_ARGS())
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "insert"
    tracer.insert(*OK_ARGS())
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer = ins.ProvenanceTracer(_engine())
        tracer.insert(*OK_ARGS())
        tracer.insert(*OK_ARGS())
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


def test_arguments_unchanged_false_when_wrapped_mutates_then_rejects():
    class Mutator:
        def insert(self, first, *rest):
            first.clear()
            raise _mk(ERR)

    tracer = ins.ProvenanceTracer(Mutator())
    args = OK_ARGS()
    with pytest.raises(ERR):
        tracer.insert(*args)
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

    err = _mk(Evil)

    class Raiser:
        def insert(self, *args):
            raise err

    tracer = ins.ProvenanceTracer(Raiser())
    with pytest.raises(Evil) as caught:
        tracer.insert({})
    assert caught.value is err
    assert tracer.records[0]["failure_class"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    tracer = ins.ProvenanceTracer(_engine())
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.insert(*OK_ARGS())
    assert tracer.records[0]["operation"] == "insert"


def test_hostile_argument_records_an_opaque_marker():
    class Hostile(dict):
        def keys(self):
            raise RuntimeError("dispatched")

        def __iter__(self):
            raise RuntimeError("dispatched")

    tracer = ins.ProvenanceTracer(_engine())
    args = list(OK_ARGS())
    args[0] = Hostile()
    assert _bare(tracer.insert, *args)[0] == "reject"
    assert tracer.records[0]["arguments"][0] == {"__opaque__": "Hostile"}


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = ins.ProvenanceTracer(_engine())
    args = list(OK_ARGS())
    args[0] = deep
    assert _bare(tracer.insert, *args)[0] == "reject"
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


def _load(source):
    module = types.ModuleType("instrument_under_test")
    module.__file__ = str(ROOT / "graph" / "provenance_instrument.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _mutant(label):
    before, after = MUTANTS[label]
    assert SOURCE.count(before) == 1, label
    return _load(SOURCE.replace(before, after))


def _is_red(module):
    try:
        tracer = module.ProvenanceTracer(_engine())
        tracer.insert(*OK_ARGS())
        tracer.insert(*OK_ARGS())
        records = tracer.records
        if [r["seq"] for r in records] != [0, 1]:
            return True
        if any(r["outcome"] != "accept" for r in records):
            return True
        if "\n".join(json.dumps(r, sort_keys=True) for r in records) != tracer.to_jsonl():
            return True
        make_args, oracle, _ = next(iter(REJECTS.values()))
        rejecting = module.ProvenanceTracer(_engine(oracle))
        try:
            rejecting.insert(*make_args())
            return True
        except ERR:
            pass
        last = rejecting.records[-1]
        if last.get("outcome") != "reject" or "code" not in last:
            return True

        class Crasher:
            def insert(self, *args):
                raise KeyError("x")

        crash = module.ProvenanceTracer(Crasher())
        try:
            crash.insert({})
            return True
        except KeyError:
            return False
    except BaseException:  # noqa: BLE001
        return True


def test_unmutated_instrument_is_green_on_the_mutant_check():
    assert not _is_red(_load(SOURCE))


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_mutant_is_red(label):
    assert _is_red(_mutant(label)), label
