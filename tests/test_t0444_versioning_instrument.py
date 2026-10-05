"""T0444 Contracts/versioning/instrument: behavior-neutral diagnostics for the
shipped control-plane version comparator via
server.control_plane_versioning_instrument.

VersionTracer wraps compare(old, new, old_minor, new_minor). This file proves:
- happy: additive minor, equal snapshots and a major bump with a new base
  path are traced as accept with the exact verdict the bare call returns;
- boundary: minor edges (zero, ceiling) and a same-major breaking change
  (verdict False, still an accept record);
- malformed: hostile minors, a malformed snapshot and a backward minor raise
  the same typed VersionError object, leave every argument unchanged and
  record reject with the failure class and mapped code;
- rollback: refusals interleaved with accepts change no snapshot and no
  later verdict; records are an isolated append-only view with
  deterministic JSONL;
- no snapshot content is copied into a record (metadata only);
- totality: hostile error attributes, a faulting trace container and a
  hostile snapshot never change the wrapped result;
- one-edit mutants of the instrument are each red on a semantic assertion.
"""

from __future__ import annotations

import copy
import json
import types
from pathlib import Path

import pytest
import yaml

from server import control_plane_versioning as cpv
from server import control_plane_versioning_instrument as vi

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "server" / "control_plane_versioning_instrument.py").read_text()
BASE = yaml.safe_load((ROOT / "data" / "contracts" / "control-plane.yaml").read_text())
CEILING = 2147483647


def _pair():
    return copy.deepcopy(BASE), copy.deepcopy(BASE)


def _additive():
    old, new = _pair()
    new["areas"]["identity"]["ops"]["register"]["request"]["fields"]["new_flag"] = {
        "type": "boolean",
        "required": False,
    }
    return old, new


def _breaking():
    old, new = _pair()
    new["areas"]["identity"]["ops"]["register"]["request"]["fields"]["display_name"]["required"] = (
        True
    )
    return old, new


def _major():
    old, new = _pair()
    new["contract"]["versioning"]["base_path"] = "/cp/v2"
    return old, new


def _bare(fn, *args):
    try:
        return ("ok", fn(*args))
    except cpv.VersionError as error:
        return ("reject", error.failure_class, error.code)
    except BaseException as error:  # noqa: BLE001
        return ("crash", type(error).__name__)


# -- neutrality and happy/boundary -----------------------------------------------------

ACCEPTS = {
    "equal": (_pair, 0, 0, True),
    "additive-minor": (_additive, 0, 1, True),
    "ceiling-minors": (_additive, CEILING, CEILING, True),
    "zero-minors": (_pair, 0, 0, True),
    "major-new-base-path": (_major, 0, 0, True),
    "same-major-breaking": (_breaking, 0, 1, False),
}


@pytest.mark.parametrize("name", sorted(ACCEPTS))
def test_accept_matches_bare_and_is_traced(name):
    make, old_minor, new_minor, verdict = ACCEPTS[name]
    old, new = make()
    assert cpv.compare(*make(), old_minor, new_minor) is verdict
    tracer = vi.VersionTracer()
    before = copy.deepcopy((old, new))
    assert tracer.compare(old, new, old_minor, new_minor) is verdict
    assert (old, new) == before
    (record,) = tracer.records
    assert record["outcome"] == "accept" and record["compatible"] is verdict
    assert (record["old_minor"], record["new_minor"]) == (old_minor, new_minor)
    assert record["old_base_path"] == "/cp/v1"
    assert record["new_base_path"] == new["contract"]["versioning"]["base_path"]


def test_result_is_the_wrapped_object():
    sentinel = object()

    tracer = vi.VersionTracer(lambda *args: sentinel)
    assert tracer.compare(1, 2, 3, 4) is sentinel
    assert tracer.records[0]["compatible"] is None


# -- malformed and rollback -------------------------------------------------------------


def _hostile_doc():
    old, _ = _pair()
    del old["contract"]["versioning"]
    return old, copy.deepcopy(BASE)


REJECTS = {
    "bool-minor": (_pair, True, 0),
    "negative-minor": (_pair, -1, 0),
    "over-ceiling-minor": (_pair, 0, CEILING + 1),
    "str-minor": (_pair, "0", 1),
    "backward-minor": (_pair, 2, 1),
    "malformed-snapshot": (_hostile_doc, 0, 0),
}


@pytest.mark.parametrize("name", sorted(REJECTS))
def test_rejections_are_the_same_typed_class(name):
    make, old_minor, new_minor = REJECTS[name]
    bare = _bare(cpv.compare, *make(), old_minor, new_minor)
    assert bare == ("reject", "malformed_version_request", "malformed_request")
    tracer = vi.VersionTracer()
    old, new = make()
    before = copy.deepcopy((old, new))
    assert _bare(tracer.compare, old, new, old_minor, new_minor) == bare
    assert (old, new) == before
    (record,) = tracer.records
    assert record["outcome"] == "reject"
    assert (record["failure_class"], record["code"]) == bare[1:]
    assert record["arguments_unchanged"] is True


def test_same_exception_object_is_reraised():
    boom = cpv.VersionError()

    def raiser(*args):
        raise boom

    with pytest.raises(cpv.VersionError) as caught:
        vi.VersionTracer(raiser).compare({}, {}, 0, 0)
    assert caught.value is boom


def test_crash_is_recorded_and_reraised():
    def crasher(*args):
        raise KeyError("x")

    tracer = vi.VersionTracer(crasher)
    with pytest.raises(KeyError):
        tracer.compare({}, {}, 0, 0)
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


def test_refusals_interleaved_with_accepts_change_nothing():
    tracer = vi.VersionTracer()
    verdicts = []
    for make, old_minor, new_minor in (
        (_additive, 0, 1),
        (_pair, 2, 1),
        (_breaking, 0, 1),
        (_hostile_doc, 0, 0),
        (_additive, 0, 1),
    ):
        old, new = make()
        outcome = _bare(tracer.compare, old, new, old_minor, new_minor)
        assert outcome == _bare(cpv.compare, *make(), old_minor, new_minor)
        verdicts.append(outcome[0] + ":" + str(outcome[1]))
    assert verdicts == [
        "ok:True",
        "reject:malformed_version_request",
        "ok:False",
        "reject:malformed_version_request",
        "ok:True",
    ]
    assert [r["outcome"] for r in tracer.records] == [
        "accept",
        "reject",
        "accept",
        "reject",
        "accept",
    ]


def test_arguments_unchanged_false_when_wrapped_mutates_then_rejects():
    def mutator(old, new, old_minor, new_minor):
        old["junk"] = 1
        raise cpv.VersionError()

    tracer = vi.VersionTracer(mutator)
    with pytest.raises(cpv.VersionError):
        tracer.compare({}, {}, 0, 0)
    assert tracer.records[0]["arguments_unchanged"] is False


# -- diagnostics ------------------------------------------------------------------------


def test_one_record_per_call_in_seq_order_metadata_only():
    tracer = vi.VersionTracer()
    tracer.compare(*_additive(), 0, 1)
    tracer.compare(*_major(), 0, 0)
    first, second = tracer.records
    assert (first["seq"], second["seq"]) == (0, 1)
    assert set(first) == {
        "seq",
        "operation",
        "old_base_path",
        "new_base_path",
        "old_minor",
        "new_minor",
        "outcome",
        "compatible",
    }
    assert first["operation"] == "compare"
    assert second["new_base_path"] == "/cp/v2"
    text = tracer.to_jsonl()
    for leaked in ("new_flag", "display_name", "password_hash_client", "areas"):
        assert leaked not in text


def test_records_are_an_isolated_append_only_view():
    tracer = vi.VersionTracer()
    tracer.compare(*_pair(), 0, 0)
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "compare"
    tracer.compare(*_pair(), 0, 0)
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer = vi.VersionTracer()
        tracer.compare(*_additive(), 0, 1)
        _bare(tracer.compare, *_pair(), 5, 1)
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


# -- totality ---------------------------------------------------------------------------


def test_hostile_error_attributes_do_not_change_the_raise():
    class Evil(cpv.VersionError):
        @property
        def failure_class(self):
            raise RuntimeError("nope")

        @failure_class.setter
        def failure_class(self, value):
            pass

    err = Evil()

    def raiser(*args):
        raise err

    tracer = vi.VersionTracer(raiser)
    with pytest.raises(Evil) as caught:
        tracer.compare({}, {}, 0, 0)
    assert caught.value is err
    assert tracer.records[0]["failure_class"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    tracer = vi.VersionTracer()
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.compare(*_pair(), 0, 0) is True
    assert tracer.records[0]["operation"] == "compare"


def test_hostile_snapshot_records_an_opaque_marker_and_still_refuses():
    class DocDict(dict):
        def __getitem__(self, key):
            raise RuntimeError("dispatched")

        def get(self, key, default=None):
            raise RuntimeError("dispatched")

    tracer = vi.VersionTracer()
    assert _bare(tracer.compare, DocDict(), DocDict(), 0, 0)[0] == "reject"
    assert tracer.records[0]["old_base_path"] == {"__opaque__": "DocDict"}


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = vi.VersionTracer()
    assert _bare(tracer.compare, deep, deep, 0, 0)[0] == "reject"
    assert tracer.records[0]["outcome"] == "reject"


# -- one-edit mutants of the instrument -------------------------------------------------

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
    "verdict-inverted": (
        '"compatible": result if type(result) is bool else None',
        '"compatible": (not result) if type(result) is bool else None',
    ),
    "swap-minors": ('"old_minor": before[2],', '"old_minor": before[3],'),
    "copy-args": (
        "self._comparator(old, new, old_minor, new_minor)",
        "self._comparator(*__import__('copy').deepcopy((old, new, old_minor, new_minor)))",
    ),
    "crash-unchanged-constant": (
        '"error_type": _type_name(type(error)),\n'
        '                    "arguments_unchanged": '
        "[_snapshot(a) for a in (old, new, old_minor, new_minor)]\n"
        "                    == before,",
        '"error_type": _type_name(type(error)),\n                    "arguments_unchanged": True,',
    ),
    "append-fallback-empty": ('[_opaque("trace-append-failed")]', "[]"),
    "compatible-else-true": (
        '"compatible": result if type(result) is bool else None',
        '"compatible": result if type(result) is bool else True',
    ),
    "return-none": (
        "        )\n        return result\n",
        "        )\n        return None\n",
    ),
    "drop-failure-class": ('"failure_class": _attribute(error, "failure_class"),', ""),
    "reject-unchanged-negated": (
        "[_snapshot(a) for a in (old, new, old_minor, new_minor)]\n"
        "                    == before,\n                }\n            )\n            raise\n"
        "        except BaseException",
        "[_snapshot(a) for a in (old, new, old_minor, new_minor)]\n"
        "                    != before,\n                }\n            )\n            raise\n"
        "        except BaseException",
    ),
    "reject-unchanged-constant": (
        '"code": _attribute(error, "code"),\n'
        '                    "arguments_unchanged": '
        "[_snapshot(a) for a in (old, new, old_minor, new_minor)]\n"
        "                    == before,",
        '"code": _attribute(error, "code"),\n                    "arguments_unchanged": True,',
    ),
    "base-path-unchecked": (
        'return path if type(path) is str else _opaque("base_path")',
        "return path",
    ),
    "operation-constant": ('"operation": "compare",', '"operation": "cmp",'),
    "swap-base-paths": (
        '"old_base_path": _base_path(old),\n            "new_base_path": _base_path(new),',
        '"old_base_path": _base_path(new),\n            "new_base_path": _base_path(old),',
    ),
    "append-no-snapshot": ("list.append(trace, _snapshot(record))", "list.append(trace, record)"),
}


def _load(source):
    module = types.ModuleType("instrument_under_test")
    module.__file__ = str(ROOT / "server" / "control_plane_versioning_instrument.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _mutant(label):
    before, after = MUTANTS[label]
    assert SOURCE.count(before) == 1, label
    return _load(SOURCE.replace(before, after))


class _Spy:
    """A comparator or client that records the exact argument objects it receives."""

    def __init__(self):
        self.seen = []

    def __call__(self, *args):
        self.seen.append(args)

    def call(self, *args):
        self.seen.append(args)


class _MutatingCrash:
    """Mutates its first argument, then crashes with a fixed exception object."""

    def __init__(self, boom):
        self.boom = boom

    def __call__(self, first, *rest):
        first.append("junk")
        raise self.boom

    def call(self, name, body=None):
        body.append("junk")
        raise self.boom


def _first_deviation_raw(module):
    """The name of the first semantic check the module fails, or None.

    An unexpected exception from the instrument propagates and errors the
    test instead of counting as a kill."""
    """True only on a semantic deviation; an unexpected exception errors."""
    tracer = module.VersionTracer()
    tracer.compare(*_additive(), 0, 3)
    tracer.compare(*_breaking(), 1, 4)
    records = tracer.records
    if [r["seq"] for r in records] != [0, 1]:
        return "check-1"
    if [r["outcome"] for r in records] != ["accept", "accept"]:
        return "check-2"
    if [r["compatible"] for r in records] != [True, False]:
        return "check-3"
    if [(r["old_minor"], r["new_minor"]) for r in records] != [(0, 3), (1, 4)]:
        return "check-4"
    if "\n".join(json.dumps(r, sort_keys=True) for r in records) != tracer.to_jsonl():
        return "check-5"
    try:
        tracer.compare(*_pair(), 2, 1)
        return "check-6"
    except cpv.VersionError:
        pass
    last = tracer.records[-1]
    if last.get("outcome") != "reject" or last.get("code") != "malformed_request":
        return "check-7"
    spy = _Spy()
    probe = module.VersionTracer(spy)
    given = ([0], [1], 2, 3)
    probe.compare(*given)
    if len(spy.seen) != 1 or len(spy.seen[0]) != 4:
        return "args-identity"
    if spy.seen[0][0] is not given[0] or spy.seen[0][1] is not given[1]:
        return "args-identity"
    if (probe.records[0]["old_minor"], probe.records[0]["new_minor"]) != (2, 3):
        return "minors-recorded"
    lost = module.VersionTracer(spy)
    del lost._trace
    lost.compare(*given)
    if lost.records != ({"__opaque__": "trace-append-failed"},):
        return "append-fallback"
    boom = KeyError("x")
    crash = module.VersionTracer(_MutatingCrash(boom))
    victim = [0]
    try:
        crash.compare(victim, {}, 0, 0)
    except KeyError as caught:
        if caught is not boom:
            return "crash-reraise-identity"
    else:
        return "crash-swallowed"
    record = crash.records[-1]
    if record.get("outcome") != "crash" or record.get("error_type") != "KeyError":
        return "crash-record"
    if record.get("arguments_unchanged") is not False:
        return "crash-arguments-unchanged"
    return _extra_deviation(module)


class _Verdict:
    """A comparator returning one fixed object."""

    def __init__(self, out):
        self.out = out

    def __call__(self, *args):
        return self.out


class _RejectingComparator:
    """Optionally mutates its first argument, then raises one fixed VersionError."""

    def __init__(self, err, mutate):
        self.err = err
        self.mutate = mutate

    def __call__(self, first, *rest):
        if self.mutate:
            first.append("junk")
        raise self.err


def _extra_deviation(module):
    for verdict in (True, False):
        returned = module.VersionTracer(_Verdict(verdict))
        if returned.compare([0], [1], 0, 0) is not verdict:
            return "result-identity"
        if returned.records[-1].get("compatible") is not verdict:
            return "compatible-recorded"
    based = module.VersionTracer(_Verdict(True))
    good = {"contract": {"versioning": {"base_path": "/cp/v1"}}}
    other = {"contract": {"versioning": {"base_path": "/cp/v2"}}}
    based.compare(good, other, 0, 0)
    rec = based.records[-1]
    if rec.get("operation") != "compare":
        return "operation-field"
    if (rec.get("old_base_path"), rec.get("new_base_path")) != ("/cp/v1", "/cp/v2"):
        return "base-path-recorded"
    for bad in (7, None, ["/cp/v1"]):
        doc = {"contract": {"versioning": {"base_path": bad}}}
        based.compare(doc, doc, 0, 0)
        if based.records[-1].get("old_base_path") != {"__opaque__": "base_path"}:
            return "base-path-non-str"
    odd = module.VersionTracer(_Verdict(5))
    if odd.compare([0], [1], 0, 0) != 5:
        return "result-identity"
    if odd.records[-1].get("compatible") is not None:
        return "compatible-non-bool"
    for mutate, expected in ((True, False), (False, True)):
        err = cpv.VersionError()
        tracer = module.VersionTracer(_RejectingComparator(err, mutate))
        try:
            tracer.compare([0], [1], 0, 0)
        except cpv.VersionError as caught:
            if caught is not err:
                return "reject-reraise-identity"
        else:
            return "reject-swallowed"
        rec = tracer.records[-1]
        if rec.get("outcome") != "reject":
            return "reject-record"
        if rec.get("arguments_unchanged") is not expected:
            return "reject-arguments-unchanged"
        if rec.get("failure_class") != "malformed_version_request":
            return "reject-record-fields"
        if rec.get("code") != "malformed_request":
            return "reject-record-fields"
    held = module.VersionTracer(_Verdict(True))
    held.compare([0], [1], 0, 0)
    stored = held._trace[-1]
    held._trace[-1]["seq"] = 99
    if stored is held._trace[-1] and held.records[-1]["seq"] != 99:
        return "append-aliasing"
    probe = module.VersionTracer(_Verdict(True))
    sample = {"seq": 7}
    probe._append_total(sample)
    if probe._trace[-1] is sample:
        return "append-aliasing"
    return None


def _first_deviation(module):
    """The first failing check; a valid call the instrument refuses is a semantic failure."""
    try:
        return _first_deviation_raw(module)
    except cpv.VersionError:
        return "valid-refused"


def _is_red(module):
    return _first_deviation(module) is not None


def test_unmutated_instrument_is_green_on_the_mutant_check():
    assert _first_deviation(_load(SOURCE)) is None


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_mutant_is_red(label):
    assert _is_red(_mutant(label)) is True, label
    assert isinstance(_first_deviation(_mutant(label)), str), label


# the check that must catch each of these, so a kill is never an accident
EXPECTED_KILL = {
    "drop-crash-reraise": "crash-swallowed",
    "copy-args": "args-identity",
    "crash-unchanged-constant": "crash-arguments-unchanged",
    "append-fallback-empty": "append-fallback",
    "compatible-else-true": "compatible-non-bool",
    "return-none": "result-identity",
    "drop-failure-class": "reject-record-fields",
    "reject-unchanged-negated": "reject-arguments-unchanged",
    "reject-unchanged-constant": "reject-arguments-unchanged",
    "append-no-snapshot": "append-aliasing",
    "base-path-unchecked": "base-path-non-str",
    "operation-constant": "operation-field",
    "swap-base-paths": "base-path-recorded",
}


@pytest.mark.parametrize("label", sorted(EXPECTED_KILL))
def test_mutant_dies_on_its_own_check(label):
    assert _first_deviation(_mutant(label)) == EXPECTED_KILL[label]


def test_a_crashing_instrument_is_an_error_not_a_kill():
    broken = SOURCE.replace('"outcome": "crash",', '"outcome": undefined_name,')
    assert broken != SOURCE
    with pytest.raises(NameError):
        _first_deviation(_load(broken))


def test_a_valid_call_the_instrument_refuses_is_an_assertion_failure():
    refusing = SOURCE.replace(
        "result = self._comparator(old, new, old_minor, new_minor)",
        "result = self._comparator(old, new, old_minor, new_minor)\n"
        "            raise VersionError()",
    )
    assert refusing != SOURCE
    assert _first_deviation(_load(refusing)) == "valid-refused"
