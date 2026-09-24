"""T0320: jobs progress contract - the contract document
(data/contracts/progress.yaml) is normative; this battery holds the
contract-derived REFERENCE report function and proves happy, boundary,
malformed and rollback behavior against it.

A progress report is a pure function of one request
{op, job_id, worker, done, total, now, previous}: previous is null on
the first report and otherwise exactly the previous progress record.
The record carries percent_bp = floor(done * 10000 / total), the report
time, and a chain link previous_id to the previous record's progress_id.

Fail-closed readings (the spec is silent; each is stated in the
contract's rule text):
- total is fixed by the first report; a changed total is a conflict,
  never a rescale;
- done never decreases; an equal done is a heartbeat and is accepted;
- completion (done == total) is terminal: no report follows it;
- now never precedes the previous reported_at; an equal now is accepted;
- job_id and worker must equal the previous record's; a new worker
  starts a new chain (previous null);
- a previous record whose shape, bounds, percent or progress_id do not
  recompute is corrupt and never trusted;
- validation order is request shape -> previous integrity -> chain.
Not covered (needs the idempotency/cancel contracts T0293/T0302,
parked): progress of a cancelled job and deduplication of replayed
reports. Lease validity stays with the queue contract.

DESIGN CAUTION: the reference is derived from the same contract
document, so this battery proves contract CONSISTENCY, not production
behavior; a later implement task must run the same cases against a
separately built runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.progress_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_RECORD_FIELDS = _CC["record"]["fields"]
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_CC["identifiers"]["worker"]["grammar"], re.ASCII)
_PROGRESS_RE = re.compile(_CC["identifiers"]["progress_id"]["grammar"], re.ASCII)

# -- reference begin: everything down to the reference end marker is rebuilt
# from source for each reference mutant (constants, error class, checks).

_REF_MAX_TOTAL = 2**53 - 1
_REF_MAX_NOW = 2**53 - 1
_REF_BP = 10000


class ProgressError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _int_in(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _grammar(value, regex):
    return type(value) is str and regex.fullmatch(value) is not None


def _exact_keys(mapping, fields):
    """Exact dict whose keys are exact strs equal to FIELDS (checked
    before any set comparison, so no user key code runs)."""
    return (
        type(mapping) is dict
        and all(type(k) is str for k in mapping)
        and len(mapping) == len(fields)
        and set(mapping) == set(fields)
    )


def _request_ok(request):
    if not _exact_keys(request, _FIELDS):
        return False
    return (
        type(request["op"]) is str
        and request["op"] == "report"
        and _grammar(request["job_id"], _JOB_RE)
        and _grammar(request["worker"], _WORKER_RE)
        and _int_in(request["total"], 1, _REF_MAX_TOTAL)
        and _int_in(request["done"], 0, request["total"])
        and _int_in(request["now"], 0, _REF_MAX_NOW)
        and (request["previous"] is None or type(request["previous"]) is dict)
    )


def _progress_id(record_without_id):
    body = _canon(record_without_id).encode()
    return "pg1:" + hashlib.sha256(b"pg1\x00" + body).hexdigest()


def _previous_ok(prev):
    if not _exact_keys(prev, _RECORD_FIELDS):
        return False
    shape = (
        type(prev["op"]) is str
        and prev["op"] == "report"
        and _grammar(prev["job_id"], _JOB_RE)
        and _grammar(prev["worker"], _WORKER_RE)
        and _int_in(prev["total"], 1, _REF_MAX_TOTAL)
        and _int_in(prev["done"], 0, prev["total"])
        and _int_in(prev["reported_at"], 0, _REF_MAX_NOW)
        and type(prev["percent_bp"]) is int
        and prev["percent_bp"] == prev["done"] * _REF_BP // prev["total"]
        and (prev["previous_id"] is None or _grammar(prev["previous_id"], _PROGRESS_RE))
        and _grammar(prev["progress_id"], _PROGRESS_RE)
    )
    if not shape:
        return False
    return prev["progress_id"] == _progress_id({k: prev[k] for k in _RECORD_FIELDS[:-1]})


def _chain_ok(request):
    prev = request["previous"]
    return (
        request["job_id"] == prev["job_id"]
        and request["worker"] == prev["worker"]
        and request["total"] == prev["total"]
        and prev["done"] < prev["total"]
        and request["done"] >= prev["done"]
        and request["now"] >= prev["reported_at"]
    )


def report(request):
    """The reference report. Fresh typed errors (flag pattern)."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_progress_request"
    elif request["previous"] is not None and not _previous_ok(request["previous"]):
        failed = "corrupt_previous_record"
    elif request["previous"] is not None and not _chain_ok(request):
        failed = "progress_conflict"
    if failed is not None:
        raise ProgressError(failed)
    req = json.loads(_canon(request))  # detached exact copy
    prev = req["previous"]
    record = {
        "op": "report",
        "job_id": req["job_id"],
        "worker": req["worker"],
        "done": req["done"],
        "total": req["total"],
        "percent_bp": req["done"] * _REF_BP // req["total"],
        "reported_at": req["now"],
        "previous_id": None if prev is None else prev["progress_id"],
    }
    record["progress_id"] = _progress_id(record)
    return record


# -- reference end

# -- helpers ----------------------------------------------------------------------------

JOB = "job1:" + "a" * 64
JOB_B = "job1:" + "b" * 64
MAX = 9007199254740991  # 2**53 - 1, written out: the contract's literal edge


def req(**kw):
    base = {
        "op": "report",
        "job_id": JOB,
        "worker": "w-1",
        "done": 0,
        "total": 10,
        "now": 0,
        "previous": None,
    }
    base.update(kw)
    return base


def _independent_id(record_without_id):
    """The contract preimage, built by hand: ASCII pg1, a NUL byte, then
    canonical JSON (sort_keys, compact separators, ensure_ascii) of the
    record without progress_id."""
    body = json.dumps(record_without_id, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "pg1:" + hashlib.sha256(b"pg1" + bytes([0]) + body.encode("utf-8")).hexdigest()


def _forge(**fields):
    """A previous record with the given fields and a RECOMPUTED id, so
    only the named defect is wrong."""
    rec = {
        "op": "report",
        "job_id": JOB,
        "worker": "w-1",
        "done": 2,
        "total": 10,
        "percent_bp": 2000,
        "reported_at": 50,
        "previous_id": None,
    }
    rec.update(fields)
    rec["progress_id"] = _independent_id({k: rec[k] for k in _RECORD_FIELDS[:-1]})
    return rec


def _plain(obj):
    if type(obj) is dict:
        return all(type(k) is str and _plain(v) for k, v in obj.items())
    return type(obj) in (str, int, bool, type(None))


def _raises(failure_class, request):
    snap = copy.deepcopy(request) if _plain(request) else None
    with pytest.raises(ProgressError) as caught:
        report(request)
    exc = caught.value
    assert type(exc) is ProgressError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False  # retryable_true_only_for is [internal]
    if snap is not None:
        assert request == snap


# -- the contract document ------------------------------------------------------------------


def test_lint_clean():
    lint()


def _mutants():
    def worker_grammar(cc):
        cc["identifiers"]["worker"]["grammar"] = "^.*$"

    def total_reading(cc):
        cc["semantics"]["total"] = "a-changed-total-rescales-done"

    def heartbeat_reading(cc):
        cc["semantics"]["monotone"] = "done-strictly-increases"

    def completion_reading(cc):
        cc["semantics"]["completion"] = "reports-after-completion-are-ignored"

    def percent_rounding(cc):
        cc["semantics"]["percent"] = "percent-bp-rounds-half-up"

    def derivation_prefix_dropped(cc):
        cc["identifiers"]["progress_id"]["derivation"] = (
            "sha256-over-canonical-json-of-the-record-without-progress-id"
        )

    def extra_failure(cc):
        cc["failures"]["classes"].append("stale_progress")

    def mapping_drift(cc):
        cc["failures"]["mapping"]["progress_conflict"] = "internal"

    def retryable(cc):
        cc["errors"]["shape"]["retryable_true_only_for"] = ["internal", "progress_conflict"]

    def record_field(cc):
        cc["record"]["fields"].append("eta_ms")

    def bound(cc):
        cc["request"]["bounds"]["total"] = "int-0-through-2-pow-53-minus-1-work-units"

    def link(cc):
        cc["links"]["queue_contract"] = "data/contracts/missing.yaml"

    def extra_section(cc):
        cc["cancellation"] = {}

    return [
        worker_grammar,
        total_reading,
        heartbeat_reading,
        completion_reading,
        percent_rounding,
        derivation_prefix_dropped,
        extra_failure,
        mapping_drift,
        retryable,
        record_field,
        bound,
        link,
        extra_section,
    ]


@pytest.mark.parametrize("mutate", _mutants(), ids=lambda f: f.__name__)
def test_mutations_fail_lint(mutate, tmp_path):
    doc = yaml.safe_load(CONTRACT.read_text())
    mutate(doc["contract"])
    path = tmp_path / "progress.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


@pytest.mark.parametrize("name", ["job_id", "worker"])
def test_lint_binds_the_queue_sibling(name, tmp_path, monkeypatch):
    """The job id and worker grammars are the queue contract's: a drifted
    queue contract fails the progress lint too."""
    from tools import progress_contract_lint as pl

    queue = yaml.safe_load((ROOT / "data/contracts/queue.yaml").read_text())
    queue["contract"]["identifiers"][name]["grammar"] = "^x$"
    (tmp_path / "data/contracts").mkdir(parents=True)
    (tmp_path / "data/contracts/queue.yaml").write_text(yaml.safe_dump(queue))
    monkeypatch.setattr(pl, "ROOT", tmp_path)
    with pytest.raises(ContractError):
        pl.lint(CONTRACT)


def test_error_enum_matches_mapping():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)


def test_bounds_are_the_contract_literals():
    bounds = _REQ["bounds"]
    assert bounds["total"] == "int-1-through-2-pow-53-minus-1-work-units"
    assert bounds["done"] == "int-0-through-total"
    assert bounds["now"] == "int-0-through-2-pow-53-minus-1-milliseconds"
    assert MAX == 2**53 - 1


# -- happy path: golden chain ------------------------------------------------------------------

JOB_C = "job1:" + "0123456789abcdef" * 4
_G1 = {
    "op": "report",
    "job_id": JOB_C,
    "worker": "w.9:x-1",
    "done": 0,
    "total": 3,
    "percent_bp": 0,
    "reported_at": 10,
    "previous_id": None,
    "progress_id": "pg1:ac663dbddfac9711fa71d6b53de6a0b6d9e5841714b635dfc0c6ab407b6a32f0",
}
_G2 = {
    "op": "report",
    "job_id": JOB_C,
    "worker": "w.9:x-1",
    "done": 2,
    "total": 3,
    "percent_bp": 6666,
    "reported_at": 20,
    "previous_id": "pg1:ac663dbddfac9711fa71d6b53de6a0b6d9e5841714b635dfc0c6ab407b6a32f0",
    "progress_id": "pg1:1351556d4baa5a0044da9be932ff92e97aed1430de5f4002aa4891fe88271a52",
}
_G3 = {
    "op": "report",
    "job_id": JOB_C,
    "worker": "w.9:x-1",
    "done": 2,
    "total": 3,
    "percent_bp": 6666,
    "reported_at": 20,
    "previous_id": "pg1:1351556d4baa5a0044da9be932ff92e97aed1430de5f4002aa4891fe88271a52",
    "progress_id": "pg1:435991114585d72becd2dbfc9423eec4d5162f42745b901c32273b677091df20",
}
_G4 = {
    "op": "report",
    "job_id": JOB_C,
    "worker": "w.9:x-1",
    "done": 3,
    "total": 3,
    "percent_bp": 10000,
    "reported_at": 25,
    "previous_id": "pg1:435991114585d72becd2dbfc9423eec4d5162f42745b901c32273b677091df20",
    "progress_id": "pg1:5256424dc7c6a03f551c4070dd48f6933d3895fdc9780ee5d80f4f3c11e80db9",
}


def _golden_requests():
    base = {"op": "report", "job_id": JOB_C, "worker": "w.9:x-1", "total": 3}
    return [
        ({**base, "done": 0, "now": 10, "previous": None}, _G1),
        ({**base, "done": 2, "now": 20, "previous": copy.deepcopy(_G1)}, _G2),
        ({**base, "done": 2, "now": 20, "previous": copy.deepcopy(_G2)}, _G3),
        ({**base, "done": 3, "now": 25, "previous": copy.deepcopy(_G3)}, _G4),
    ]


def test_full_record_equality_golden_chain():
    for request, expected in _golden_requests():
        got = report(request)
        assert type(got) is dict and got == expected
        assert list(got) == _RECORD_FIELDS
        assert all(type(got[k]) is type(expected[k]) for k in expected)


def test_progress_id_matches_the_contract_preimage():
    for _, expected in _golden_requests():
        without = {k: v for k, v in expected.items() if k != "progress_id"}
        assert _independent_id(without) == expected["progress_id"]
    prev = None
    for done, now in ((1, 5), (4, 5), (4, 9), (7, 9), (10, 11)):
        got = report(req(done=done, now=now, previous=prev, job_id=JOB_B, worker="z"))
        without = {k: v for k, v in got.items() if k != "progress_id"}
        assert got["progress_id"] == _independent_id(without)
        assert (got["op"], got["job_id"], got["worker"], got["done"], got["reported_at"]) == (
            "report",
            JOB_B,
            "z",
            done,
            now,
        )
        assert got["previous_id"] == (None if prev is None else prev["progress_id"])
        prev = got


def test_chain_is_monotone_and_terminal():
    prev, seen = None, []
    for done, now in ((0, 0), (0, 0), (3, 1), (3, 7), (9, 7), (10, 8)):
        prev = report(req(done=done, now=now, previous=prev))
        seen.append((prev["done"], prev["percent_bp"], prev["reported_at"]))
    assert seen == sorted(seen)
    assert seen[-1] == (10, 10000, 8)
    _raises("progress_conflict", req(done=10, now=8, previous=prev))


def test_first_report_may_complete_at_once():
    got = report(req(done=10, now=3))
    assert (got["percent_bp"], got["previous_id"]) == (10000, None)


def test_determinism_and_detachment():
    request = req(done=3, now=60, previous=_forge())
    snap = copy.deepcopy(request)
    a, b = report(request), report(request)
    assert a == b and a is not b
    assert request == snap
    a["done"] = -1
    assert report(request)["done"] == 3


# -- boundary ---------------------------------------------------------------------------------


def test_percent_is_floored_exactly():
    rows = [
        (0, 1, 0),
        (1, 1, 10000),
        (1, 3, 3333),
        (2, 3, 6666),
        (9999, 10000, 9999),
        (1, 10001, 0),
        (MAX - 1, MAX, 9999),
        (MAX, MAX, 10000),
    ]
    for done, total, bp in rows:
        got = report(req(done=done, total=total))
        assert got["percent_bp"] == bp and type(got["percent_bp"]) is int, (done, total)


def test_bounds_are_inclusive():
    report(req(done=0, total=1, now=0))
    report(req(done=9007199254740991, total=9007199254740991, now=9007199254740991))
    for bad in (
        req(total=0, done=0),
        req(total=9007199254740992, done=0),
        req(done=-1),
        req(done=11, total=10),
        req(now=-1),
        req(now=9007199254740992),
    ):
        _raises("malformed_progress_request", bad)


def test_heartbeat_and_equal_clock_are_accepted():
    prev = _forge(done=2, reported_at=50)
    got = report(req(done=2, now=50, previous=prev))
    assert (got["done"], got["reported_at"], got["previous_id"]) == (2, 50, prev["progress_id"])


def test_conflicts():
    prev = _forge(done=2, reported_at=50)
    for bad in (
        req(done=3, now=51, previous=prev, job_id=JOB_B),
        req(done=3, now=51, previous=prev, worker="w-2"),
        req(done=3, now=51, previous=prev, total=11),
        req(done=3, now=51, previous=prev, total=9),
        req(done=1, now=51, previous=prev),
        req(done=3, now=49, previous=prev),
        req(done=10, now=51, previous=_forge(done=10, percent_bp=10000)),
        req(done=10, now=50, previous=_forge(done=10, percent_bp=10000, reported_at=50)),
    ):
        _raises("progress_conflict", bad)


def test_validation_order():
    corrupt_and_conflicting = {**_forge(), "done": 3}  # stale id, and worker differs
    _raises(
        "corrupt_previous_record",
        req(done=1, now=0, worker="w-2", previous=corrupt_and_conflicting),
    )
    _raises("malformed_progress_request", req(op="x", previous=corrupt_and_conflicting))
    _raises("malformed_progress_request", req(done=-1, previous=_forge()))


# -- malformed requests -----------------------------------------------------------------------

HOSTILE = []


class _Armed:
    on = False


def _log(name):
    if _Armed.on:
        HOSTILE.append(name)


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _LyingDict(dict):
    def __getitem__(self, key):
        _log("getitem")
        return dict.__getitem__(self, key)

    def __iter__(self):
        _log("iter")
        return dict.__iter__(self)

    def __len__(self):
        _log("len")
        return dict.__len__(self)

    def keys(self):
        _log("keys")
        return dict.keys(self)

    def items(self):
        _log("items")
        return dict.items(self)


class _EqRaises(str):
    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")

    def __hash__(self):
        _log("hash")
        return str.__hash__(self)


class _Collides:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        _log("hash")
        return hash(self.name)

    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")


class _ReprRaises(str):
    def __repr__(self):
        _log("repr")
        raise RuntimeError("hostile __repr__")

    def __str__(self):
        _log("str")
        raise RuntimeError("hostile __str__")

    def __format__(self, spec):
        _log("format")
        raise RuntimeError("hostile __format__")


KEY_FORMS = {"str-subclass": _StrSub, "eq-raises": _EqRaises, "hash-collides": _Collides}


def _rekey(mapping, field, make):
    return {(make(k) if k == field else k): v for k, v in mapping.items()}


MALFORMED = {
    "none": None,
    "str": "report",
    "list": list(req().items()),
    "list-subclass": _ListSub(req().items()),
    "dict-subclass": _DictSub(req()),
    "lying-dict": _LyingDict(req()),
    "missing-now": {k: v for k, v in req().items() if k != "now"},
    "extra": {**req(), "eta_ms": 1},
    "renamed-same-arity": {("Now" if k == "now" else k): v for k, v in req().items()},
    "op-unknown": req(op="progress"),
    "op-str-subclass": req(op=_StrSub("report")),
    "job-bad": req(job_id="job1:" + "A" * 64),
    "job-newline": req(job_id=JOB + "\n"),
    "job-str-subclass": req(job_id=_StrSub(JOB)),
    "worker-empty": req(worker=""),
    "worker-long": req(worker="w" * 65),
    "worker-newline": req(worker="w-1\n"),
    "worker-space": req(worker="w 1"),
    "worker-str-subclass": req(worker=_StrSub("w-1")),
    "worker-repr-raises": req(worker=_ReprRaises("w-1")),
    "done-bool": req(done=False),
    "done-float": req(done=1.0),
    "done-int-subclass": req(done=_IntSub(1)),
    "total-bool": req(total=True, done=0),
    "total-str": req(total="10"),
    "now-bool": req(now=False),
    "now-int-subclass": req(now=_IntSub(0)),
    "previous-list": req(previous=[]),
    "previous-str": req(previous="pg1:" + "0" * 64),
    "previous-false": req(previous=False),
    "previous-dict-subclass": req(previous=_DictSub(_forge())),
    "previous-lying-dict": req(previous=_LyingDict(_forge())),
    "previous-list-subclass": req(previous=_ListSub(_forge().items())),
}
for _field in _FIELDS:
    for _label, _make in KEY_FORMS.items():
        MALFORMED[f"key-{_label}-{_field}"] = _rekey(req(), _field, _make)

_PREV_FIELDS = [f for f in _RECORD_FIELDS if f != "progress_id"]
_GOOD_PREV = _forge()
CORRUPT = {
    "missing-field": {k: v for k, v in _GOOD_PREV.items() if k != "previous_id"},
    "extra-field": {**_GOOD_PREV, "eta_ms": 0},
    "renamed-same-arity": {("Done" if k == "done" else k): v for k, v in _GOOD_PREV.items()},
    "op-wrong": _forge(op="decide"),
    "job-bad": _forge(job_id="job1:xyz"),
    "job-newline": _forge(job_id=JOB + "\n"),
    "worker-bad": _forge(worker="w 1"),
    "worker-newline": _forge(worker="w-1\n"),
    "total-zero": _forge(total=0, done=0, percent_bp=0),
    "done-over-total": _forge(done=11, percent_bp=11000),
    "done-negative": _forge(done=-1, percent_bp=-1000),
    "reported-at-past-clock": _forge(reported_at=9007199254740992),
    "percent-off-by-one": _forge(percent_bp=2001),
    "percent-rounded": _forge(done=2, total=3, percent_bp=6667),
    "percent-bool": _forge(done=0, percent_bp=False),
    "percent-float": _forge(percent_bp=2000.0),
    "previous-id-bad": _forge(previous_id="pg1:" + "G" * 64),
    "previous-id-newline": _forge(previous_id="pg1:" + "0" * 64 + "\n"),
    "progress-id-stale": {**_GOOD_PREV, "done": 3, "percent_bp": 3000},
    "progress-id-grammar": {**_GOOD_PREV, "progress_id": "pg1:" + "0" * 63},
    "progress-id-prefix": {
        **_GOOD_PREV,
        "progress_id": "pg1:"
        + hashlib.sha256(
            json.dumps(
                {k: _GOOD_PREV[k] for k in _PREV_FIELDS}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    },
    "job-str-subclass-nested": {**_GOOD_PREV, "job_id": _StrSub(JOB)},
    "worker-str-subclass-nested": {**_GOOD_PREV, "worker": _StrSub("w-1")},
    "progress-id-str-subclass": {**_GOOD_PREV, "progress_id": _StrSub(_GOOD_PREV["progress_id"])},
    "worker-repr-raises-nested": {**_GOOD_PREV, "worker": _ReprRaises("w-1")},
    "done-int-subclass-nested": {**_GOOD_PREV, "done": _IntSub(2)},
}
for _field in _RECORD_FIELDS:
    for _label, _make in KEY_FORMS.items():
        CORRUPT[f"key-{_label}-{_field}"] = _rekey(_GOOD_PREV, _field, _make)


def _snap(obj):
    if isinstance(obj, dict):
        return (
            type(obj),
            [
                (type(k), k.name if type(k) is _Collides else str.__str__(k), _snap(v))
                for k, v in dict.items(obj)
            ],
        )
    if isinstance(obj, list):
        return (type(obj), [_snap(v) for v in list.__iter__(obj)])
    if isinstance(obj, str):
        return (type(obj), str.__str__(obj))
    return (type(obj), obj)


def _hostile(failure_class, request):
    """Typed class, fresh error, no user code ran, input unchanged."""
    snap = _snap(request)
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            report(request)
            got = "accepted"
        except ProgressError as exc:
            got = (
                type(exc) is ProgressError,
                exc.failure_class,
                exc.code,
                exc.retryable,
                exc.__cause__ is None and exc.__context__ is None,
            )
        except BaseException as exc:  # noqa: BLE001 - a raw escape is the failure
            got = ("raw", type(exc).__name__)
        calls = list(HOSTILE)
    finally:
        _Armed.on = False
    assert got == (True, failure_class, FAILURE_MAPPING[failure_class], False, True), got
    assert calls == [], calls
    assert _snap(request) == snap


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_requests(name):
    _hostile("malformed_progress_request", MALFORMED[name])


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_corrupt_previous_records(name):
    _hostile("corrupt_previous_record", req(done=3, now=60, previous=CORRUPT[name]))


def test_forged_previous_is_otherwise_valid():
    """The CORRUPT rows differ from an accepted previous only by their
    defect: the unmodified forge is accepted."""
    assert (
        report(req(done=3, now=60, previous=_forge()))["previous_id"] == _GOOD_PREV["progress_id"]
    )


def test_rejections_leave_inputs_bit_identical():
    for bad in (
        req(done=-1),
        req(done=1, now=60, previous=_forge()),
        req(previous=CORRUPT["percent-off-by-one"]),
    ):
        before = copy.deepcopy(bad)
        with pytest.raises(ProgressError):
            report(bad)
        assert bad == before


# -- the reference itself is pinned: one-edit mutants fail the battery -------------------
# Anchors are matched against the reference block's own source (inspect),
# inside the builder, so an edit to this file never breaks collection.

REFERENCE_EDITS = {
    "percent-round": (
        '"percent_bp": req["done"] * _REF_BP // req["total"],',
        '"percent_bp": (req["done"] * _REF_BP + req["total"] // 2) // req["total"],',
    ),
    "prev-percent-unchecked": (
        'and prev["percent_bp"] == prev["done"] * _REF_BP // prev["total"]',
        "and True",
    ),
    "prev-id-unchecked": (
        'return prev["progress_id"] == _progress_id(',
        'return True or prev["progress_id"] == _progress_id(',
    ),
    "prev-type-unchecked": (
        '        and (request["previous"] is None or type(request["previous"]) is dict)\n',
        "",
    ),
    "job-mismatch": ('request["job_id"] == prev["job_id"]\n        and ', ""),
    "worker-mismatch": ('request["worker"] == prev["worker"]', "True"),
    "total-change": ('request["total"] == prev["total"]', "True"),
    "after-completion": ('prev["done"] < prev["total"]', "True"),
    "done-regression": ('request["done"] >= prev["done"]', "True"),
    "heartbeat-rejected": ('request["done"] >= prev["done"]', 'request["done"] > prev["done"]'),
    "clock-regression": ('request["now"] >= prev["reported_at"]', "True"),
    "clock-strict": (
        'request["now"] >= prev["reported_at"]',
        'request["now"] > prev["reported_at"]',
    ),
    "chain-wrong-link": (
        '"previous_id": None if prev is None else prev["progress_id"],',
        '"previous_id": None if prev is None else prev["previous_id"],',
    ),
    "id-prefix-dropped": ('b"pg1\\x00" + ', ""),
    "id-preimage-extra-member": (
        "_canon(record_without_id)",
        '_canon({**record_without_id, "v": 1})',
    ),
    "op-const": ('"op": "report",', '"op": "progress",'),
    "worker-const": ('"worker": req["worker"],', '"worker": "w-1",'),
    "job-const": ('"job_id": req["job_id"],', '"job_id": "job1:" + "a" * 64,'),
    "reported-at-shift": ('"reported_at": req["now"],', '"reported_at": req["now"] + 1,'),
    "done-is-total": ('"done": req["done"],', '"done": req["total"],'),
    "total-bound-plus": ("_REF_MAX_TOTAL = 2**53 - 1", "_REF_MAX_TOTAL = 2**53"),
    "clock-bound-plus": ("_REF_MAX_NOW = 2**53 - 1", "_REF_MAX_NOW = 2**53"),
    "done-lower-bound": ('_int_in(request["done"], 0,', '_int_in(request["done"], -1,'),
    "retryable-true": ("self.retryable = False", "self.retryable = True"),
    "order-swap": (
        '    elif request["previous"] is not None and not _previous_ok(request["previous"]):\n'
        '        failed = "corrupt_previous_record"\n'
        '    elif request["previous"] is not None and not _chain_ok(request):\n'
        '        failed = "progress_conflict"\n',
        '    elif request["previous"] is not None and not _chain_ok(request):\n'
        '        failed = "progress_conflict"\n'
        '    elif request["previous"] is not None and not _previous_ok(request["previous"]):\n'
        '        failed = "corrupt_previous_record"\n',
    ),
    "key-guard-off": ("        and all(type(k) is str for k in mapping)\n", ""),
    "grammar-type-off": ("return type(value) is str and regex.fullmatch", "return regex.fullmatch"),
    "int-type-off": ("return type(value) is int and lo <= value", "return lo <= value"),
    "chained-error": (
        "        raise ProgressError(failed)",
        "        try:\n            raise KeyError(failed)\n"
        "        except KeyError:\n            raise ProgressError(failed)",
    ),
}


# Edits that must NOT change behavior. not-detached: after validation the
# request holds only exact scalars plus (optionally) an exact previous
# dict of exact scalars, and the record copies scalars only, so working
# on the live request cannot alias or mutate anything the caller holds.
EQUIVALENT_EDITS = {
    "not-detached": ("req = json.loads(_canon(request))", "req = request"),
}


def _mutant_reference(name):
    """Rebuild the whole reference block with one edit applied; returns
    the rebuilt top-level names."""
    import ast
    import inspect

    old, new = {**REFERENCE_EDITS, **EQUIVALENT_EDITS}[name]
    module_source = inspect.getsource(sys.modules[__name__])
    begin = module_source.index("# -- reference begin")
    end = module_source.index("# -- reference end")
    source = module_source[begin:end]
    assert source.count(old) == 1, name
    source = source.replace(old, new)
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
    namespace = dict(globals())
    exec(compile(source, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return {n: namespace[n] for n in names}


def _install(monkeypatch, name):
    for key, value in _mutant_reference(name).items():
        monkeypatch.setitem(globals(), key, value)


def _battery():
    """Every behavior test that takes no fixture, plus the parametrized
    malformed and corrupt rows; returns the failing labels."""
    failures = []
    jobs = [
        (n, f)
        for n, f in sorted(globals().items())
        if n.startswith("test_")
        and callable(f)
        and f.__code__.co_argcount == 0
        and n
        not in (
            "test_lint_clean",
            "test_error_enum_matches_mapping",
            "test_bounds_are_the_contract_literals",
            "test_reference_edits_apply_once",
            "test_identity_battery_is_green",
        )
    ]
    jobs += [(f"malformed:{n}", lambda n=n: test_malformed_requests(n)) for n in MALFORMED]
    jobs += [(f"corrupt:{n}", lambda n=n: test_corrupt_previous_records(n)) for n in CORRUPT]
    for label, job in jobs:
        try:
            job()
        except BaseException as exc:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(exc).__name__}")
    return failures


def test_reference_edits_apply_once():
    for name in {**REFERENCE_EDITS, **EQUIVALENT_EDITS}:
        _mutant_reference(name)


def test_identity_battery_is_green():
    assert _battery() == []


@pytest.mark.parametrize("name", sorted(REFERENCE_EDITS))
def test_reference_mutant_is_red(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() != [], name


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() == [], name
