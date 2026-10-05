"""T0255 Store/export/instrument: behavior-neutral diagnostics for the
production export engine (store.export) via store.export_instrument.

ExportTracer wraps ExportEngine.export. This file proves:
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
from store import export, wal
from store import export_instrument as ins

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "store" / "export_instrument.py").read_text()
ERR = export.ExportError
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


def _raising(state, fmt):
    raise ValueError("untrusted")


def _wrong(state, fmt):
    return "not the canonical rendering"


def _engine(exporter=None):
    return export.ExportEngine(exporter or export.render_document)


def _request_for(fmt=None):
    return {"format": fmt or export.FORMATS[0]}


def OK_ARGS():
    return (_log([("put", 0), ("put", 1), ("delete", 0)]), _request_for())


REJECTS = {
    "log-not-list": (lambda: ((), _request_for()), None, "malformed_export_request"),
    "request-extra-field": (
        lambda: (_log([("put", 0)]), {**_request_for(), "extra": 1}),
        None,
        "malformed_export_request",
    ),
    "format-not-str": (
        lambda: (_log([("put", 0)]), {"format": 1}),
        None,
        "malformed_export_request",
    ),
    "unsupported-format": (
        lambda: (_log([("put", 0)]), {"format": "bogus"}),
        None,
        "unsupported_format",
    ),
    "corrupt-source": (
        lambda: ([{"entry_id": "wal1:" + "0" * 64}], _request_for()),
        None,
        "corrupt_source",
    ),
    "raising-exporter": (
        lambda: (_log([("put", 0)]), _request_for()),
        _raising,
        "divergent_export",
    ),
    "wrong-document": (
        lambda: (_log([("put", 0)]), _request_for()),
        _wrong,
        "divergent_export",
    ),
}


def test_traced_trajectory_matches_bare():
    bare, tracer = _engine(), ins.ExportTracer(_engine())
    for script in _scripts(9):
        for fmt in export.FORMATS:
            log = _log(script)
            before = copy.deepcopy(log)
            request = _request_for(fmt)
            a = bare.export(copy.deepcopy(log), dict(request))
            b = tracer.export(log, request)
            assert a == b and log == before and request == _request_for(fmt)
    assert tracer.records


def test_summary_pins_the_result():
    tracer = ins.ExportTracer(_engine())
    log = _log([("put", 0), ("put", 1), ("delete", 0)])
    result = tracer.export(log, _request_for())
    (record,) = tracer.records
    assert record["result"] == {
        "export_id": result["export_id"],
        "head": result["head"],
        "state_id": result["state_id"],
        "record_count": 1,
        "format": result["format"],
        "document_chars": len(result["document"]),
    }


# -- neutrality -------------------------------------------------------------


def test_result_is_the_wrapped_object():
    class Spy:
        out = {"marker": 1}

        def export(self, *args):
            return self.out

    assert ins.ExportTracer(Spy()).export([], {}) is Spy.out


@pytest.mark.parametrize("name", sorted(REJECTS))
def test_rejections_are_the_same_typed_class(name):
    make_args, oracle, failure_class = REJECTS[name]
    bare = _bare(_engine(oracle).export, *make_args())
    assert bare[:2] == ("reject", failure_class)
    tracer = ins.ExportTracer(_engine(oracle))
    args = make_args()
    before = copy.deepcopy(args)
    traced = _bare(tracer.export, *args)
    assert traced == bare
    assert args == before
    (record,) = tracer.records
    assert record["outcome"] == "reject" and record["operation"] == "export"
    assert (record["failure_class"], record["code"]) == bare[1:]
    assert record["arguments_unchanged"] is True


def test_same_exception_object_is_reraised():
    boom = ERR("malformed_export_request", "E")

    class Raiser:
        def export(self, *args):
            raise boom

    with pytest.raises(ERR) as caught:
        ins.ExportTracer(Raiser()).export([], {})
    assert caught.value is boom


def test_crash_is_recorded_and_reraised():
    class Crasher:
        def export(self, *args):
            raise KeyError("x")

    tracer = ins.ExportTracer(Crasher())
    with pytest.raises(KeyError):
        tracer.export([], {})
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


# -- diagnostics ------------------------------------------------------------


def test_one_record_per_call_in_seq_order():
    tracer = ins.ExportTracer(_engine())
    for args in (OK_ARGS(), OK_ARGS()):
        tracer.export(*args)
    assert [r["seq"] for r in tracer.records] == [0, 1]
    assert all(r["outcome"] == "accept" and r["operation"] == "export" for r in tracer.records)


def test_records_are_an_isolated_append_only_view():
    tracer = ins.ExportTracer(_engine())
    tracer.export(*OK_ARGS())
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "export"
    tracer.export(*OK_ARGS())
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer = ins.ExportTracer(_engine())
        tracer.export(*OK_ARGS())
        tracer.export(*OK_ARGS())
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


def test_arguments_unchanged_false_when_wrapped_mutates_then_rejects():
    class Mutator:
        def export(self, first, *rest):
            first.clear()
            raise ERR("malformed_export_request", "E")

    tracer = ins.ExportTracer(Mutator())
    args = OK_ARGS()
    with pytest.raises(ERR):
        tracer.export(*args)
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
        def export(self, *args):
            raise err

    tracer = ins.ExportTracer(Raiser())
    with pytest.raises(Evil) as caught:
        tracer.export([], {})
    assert caught.value is err
    assert tracer.records[0]["failure_class"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    tracer = ins.ExportTracer(_engine())
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.export(*OK_ARGS())
    assert tracer.records[0]["operation"] == "export"


def test_hostile_argument_records_an_opaque_marker():
    class Hostile(dict):
        def keys(self):
            raise RuntimeError("dispatched")

        def __iter__(self):
            raise RuntimeError("dispatched")

    tracer = ins.ExportTracer(_engine())
    args = list(OK_ARGS())
    args[1] = Hostile()
    assert _bare(tracer.export, *args)[0] == "reject"
    assert tracer.records[0]["arguments"][1] == {"__opaque__": "Hostile"}


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = ins.ExportTracer(_engine())
    args = list(OK_ARGS())
    args[0] = deep
    assert _bare(tracer.export, *args)[0] == "reject"
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
    module.__file__ = str(ROOT / "store" / "export_instrument.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _mutant(label):
    before, after = MUTANTS[label]
    assert SOURCE.count(before) == 1, label
    return _load(SOURCE.replace(before, after))


def _is_red(module):
    tracer = module.ExportTracer(_engine())
    tracer.export(*OK_ARGS())
    tracer.export(*OK_ARGS())
    records = tracer.records
    if [r["seq"] for r in records] != [0, 1]:
        return True
    if any(r["outcome"] != "accept" for r in records):
        return True
    if "\n".join(json.dumps(r, sort_keys=True) for r in records) != tracer.to_jsonl():
        return True
    make_args, oracle, _ = next(iter(REJECTS.values()))
    rejecting = module.ExportTracer(_engine(oracle))
    try:
        rejecting.export(*make_args())
        return True
    except ERR:
        pass
    last = rejecting.records[-1]
    if last.get("outcome") != "reject" or "code" not in last:
        return True

    class Crasher:
        def export(self, *args):
            raise KeyError("x")

    crash = module.ExportTracer(Crasher())
    try:
        crash.export([], {})
    except KeyError:
        return False
    except Exception as error:
        # a different exception type is a semantic deviation
        return type(error) is not KeyError
    return True


def test_unmutated_instrument_is_green_on_the_mutant_check():
    assert not _is_red(_load(SOURCE))


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_mutant_is_red(label):
    assert _is_red(_mutant(label)), label
