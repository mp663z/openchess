"""T0322 permanent red battery for jobs-progress report functions.

Drives every row of the T0321 fixture (tests/fixtures/progress/cases.json),
hostile-input totality probes and pinned semantic probes through a
`report(request)` callable and its `ProgressError` class:

- happy/boundary: each request in a row returns the pinned record exactly
  (exact types), the progress_id is re-derived here from the contract
  preimage, the request is untouched and the record shares no container
  with it;
- malformed: each row rejects with the pinned class and mapped code, fresh
  (no __cause__ or __context__), request untouched, and its single-locus
  repair is accepted;
- rollback: a rejected request leaves everything untouched and the valid
  follow-up reports as pinned;
- totality: hostile types at every request and previous-record field fail
  closed with a typed error and never run caller code;
- semantics: floor percent, chain linkage, heartbeat, terminal completion,
  clock edge, total fixed by the first report, validation order.

Standalone-red convention: GREEN against the contract-derived reference in
tests.test_t0320_progress_contract; every mutant below is RED on its pinned
target check. The production task switches the binding by replacing ONLY
the two binding lines with the production report / ProgressError names.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0320_progress_contract as _reference  # noqa: E402
from tools.progress_contract_lint import FAILURE_MAPPING  # noqa: E402

report = _reference.report
ProgressError = _reference.ProgressError

CASES = json.loads((ROOT / "tests" / "fixtures" / "progress" / "cases.json").read_text())
JOB = "job1:" + "a" * 64
MAX = 2**53 - 1
RECORD_FIELDS = (
    "op",
    "job_id",
    "worker",
    "done",
    "total",
    "percent_bp",
    "reported_at",
    "previous_id",
    "progress_id",
)
CLASSES = ("malformed_progress_request", "corrupt_previous_record", "progress_conflict")


def _req(**kw):
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


def _id(record_without_id):
    body = json.dumps(record_without_id, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "pg1:" + hashlib.sha256(b"pg1" + bytes([0]) + body.encode("utf-8")).hexdigest()


def _forge(**fields):
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
    rec["progress_id"] = _id({k: rec[k] for k in RECORD_FIELDS[:-1]})
    return rec


def _walk_ids(obj, out):
    if isinstance(obj, (dict, list)):
        out.add(id(obj))
        for v in obj.values() if isinstance(obj, dict) else obj:
            _walk_ids(v, out)
    return out


def _ok(impl, request, expect):
    before = copy.deepcopy(request)
    record = impl(request)
    assert request == before
    assert type(record) is dict and record == expect
    assert set(record) == set(RECORD_FIELDS)
    for key, value in expect.items():
        assert type(record[key]) is type(value), key
    assert record["progress_id"] == _id({k: v for k, v in record.items() if k != "progress_id"})
    assert not (_walk_ids(record, set()) & _walk_ids(request, set()))
    return record


def _rejects(impl, err, request, failure):
    snap = copy.deepcopy(request)
    with pytest.raises(err) as caught:
        impl(request)
    exc = caught.value
    assert type(exc) is err
    assert exc.failure_class == failure
    assert exc.code == FAILURE_MAPPING[failure]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False
    assert request == snap


# ---- checks shared by the tests and the mutant harness ---------------------------------


def check_fixture(impl, err):
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            assert len(row["requests"]) == len(row["expect_records"]), row["name"]
            for request, expect in zip(row["requests"], row["expect_records"], strict=True):
                _ok(impl, request, expect)
    for row in CASES["malformed"]:
        _rejects(impl, err, row["request"], row["expect_failure"])
        impl(row["minimal_repair"]["request"])
    for row in CASES["rollback"]:
        _rejects(impl, err, row["rejected_request"], row["expect_failure"])
        _ok(impl, row["request"], row["expect_record"])


def check_semantics(impl, err):
    first = impl(_req(done=1, total=3, now=5))
    assert (first["percent_bp"], first["previous_id"], first["reported_at"]) == (3333, None, 5)
    assert impl(_req(done=3, total=3))["percent_bp"] == 10000  # first report may complete
    assert impl(_req(done=9999, total=10000))["percent_bp"] == 9999  # never rounds up to 10000
    second = impl(_req(done=2, total=3, now=5, previous=first))  # equal now accepted
    assert second["previous_id"] == first["progress_id"] and second["percent_bp"] == 6666
    beat = impl(_req(done=2, total=3, now=9, previous=second))  # heartbeat: equal done
    assert (
        beat["done"] == 2
        and beat["reported_at"] == 9
        and beat["progress_id"] != second["progress_id"]
    )
    last = impl(_req(done=3, total=3, now=10, previous=beat))
    assert last["percent_bp"] == 10000
    _rejects(impl, err, _req(done=3, total=3, now=11, previous=last), "progress_conflict")
    _rejects(impl, err, _req(done=1, total=3, now=11, previous=beat), "progress_conflict")
    _rejects(impl, err, _req(done=2, total=4, now=11, previous=beat), "progress_conflict")
    _rejects(impl, err, _req(done=2, total=3, now=8, previous=beat), "progress_conflict")
    _rejects(
        impl, err, _req(done=2, total=3, now=11, worker="w-2", previous=beat), "progress_conflict"
    )
    _rejects(
        impl,
        err,
        _req(done=2, total=3, now=11, job_id="job1:" + "b" * 64, previous=beat),
        "progress_conflict",
    )
    big = impl(_req(done=MAX, total=MAX, now=MAX))
    assert (big["percent_bp"], big["reported_at"]) == (10000, MAX)
    assert impl(_req(done=MAX - 1, total=MAX))["percent_bp"] == (MAX - 1) * 10000 // MAX
    a, b = impl(_req(now=7)), impl(_req(now=7))
    assert a == b and a is not b


def check_order(impl, err):
    prev = _forge()
    # shape before previous integrity
    _rejects(impl, err, _req(total=0, previous=_forge(done=3)), "malformed_progress_request")
    # previous integrity before chain consistency
    forged = _forge()
    forged["progress_id"] = "pg1:" + "0" * 64
    _rejects(impl, err, _req(done=1, total=10, now=60, previous=forged), "corrupt_previous_record")
    # a corrupt previous is never treated as a conflict, and an intact one is
    _rejects(impl, err, _req(done=1, total=10, now=60, previous=prev), "progress_conflict")
    for bad in (
        _forge(percent_bp=2001),
        _forge(done=11),
        _forge(total=0),
        _forge(reported_at=MAX + 1),
        _forge(op="x"),
        _forge(worker="bad worker"),
        _forge(previous_id="nope"),
    ):
        _rejects(impl, err, _req(done=5, total=10, now=60, previous=bad), "corrupt_previous_record")
    done_but_forged = _forge(done=10, percent_bp=9999)  # complete AND corrupt: corrupt wins
    _rejects(
        impl,
        err,
        _req(done=10, total=10, now=60, previous=done_but_forged),
        "corrupt_previous_record",
    )
    tampered = {**prev, "done": 3, "percent_bp": 3000}
    _rejects(
        impl, err, _req(done=5, total=10, now=60, previous=tampered), "corrupt_previous_record"
    )


class Boom:
    def __getattribute__(self, name):
        raise AssertionError("caller object touched: " + name)


class BoomStr(str):
    def __eq__(self, other):
        raise AssertionError("eq hook")

    __hash__ = str.__hash__


class BoomDict(dict):
    def __iter__(self):
        raise AssertionError("iter hook")

    def keys(self):
        raise AssertionError("keys hook")

    def items(self):
        raise AssertionError("items hook")


HOSTILE = (None, True, False, -1, 2**70, 1.0, float("nan"), "", "x", b"b", [], (1,), {}, Boom)


def check_totality(impl, err):
    for field in ("op", "job_id", "worker", "done", "total", "now", "previous"):
        for bad in HOSTILE:
            request = _req(done=1, total=10)
            request[field] = bad
            if (field, bad) in (("worker", "x"), ("previous", None)):
                continue  # valid values
            try:
                impl(request)
            except err as exc:
                assert exc.failure_class in CLASSES
            else:
                raise AssertionError((field, bad))
    for field in RECORD_FIELDS:
        for bad in HOSTILE:
            prev = _forge()
            prev[field] = bad
            if prev == _forge():
                continue
            try:
                impl(_req(done=5, total=10, now=60, previous=prev))
            except err as exc:
                assert exc.failure_class == "corrupt_previous_record", (field, bad)
            else:
                raise AssertionError((field, bad))
    for bad in (Boom(), BoomDict(), BoomStr("report"), [_req()], (_req(),), "report", None):
        try:
            impl(bad)
        except err as exc:
            assert exc.failure_class == "malformed_progress_request"
        else:
            raise AssertionError(bad)
    for forged in (
        BoomDict(_req()),
        _req(job_id=BoomStr(JOB)),
        _req(worker=BoomStr("w-1")),
        _req(previous=BoomDict(_forge())),
        _req(previous=_forge(job_id=BoomStr(JOB))),
    ):
        try:
            impl(forged)
        except err:
            pass
        except AssertionError as exc:
            raise AssertionError("caller code ran: " + str(exc)) from None
        else:
            raise AssertionError("subclass accepted")
    for bad in (JOB + "\n", "job1:" + "A" * 64, "job1:" + "\u0661" * 64):
        _rejects(impl, err, _req(job_id=bad), "malformed_progress_request")
    for bad in ("", "w" * 65, "w\n", "w w", "é"):
        _rejects(impl, err, _req(worker=bad), "malformed_progress_request")
    for field, bad in (("done", True), ("total", True), ("now", True), ("now", MAX + 1)):
        _rejects(impl, err, _req(**{field: bad}), "malformed_progress_request")
    _rejects(impl, err, {**_req(), "extra": 1}, "malformed_progress_request")
    missing = _req()
    del missing["previous"]
    _rejects(impl, err, missing, "malformed_progress_request")


class RawEscape(AssertionError):
    """A non-contract exception escaped the implementation (a crash, not a decision)."""


def _guard(impl, err, oracle=None):
    """Any exception that is not the typed contract error (an AssertionError
    raised inside the implementation included) is a crash and becomes RawEscape.
    With an oracle (the reference), a typed refusal of an input the reference
    accepts is an acceptance failure and surfaces as a plain AssertionError."""

    def guarded(*args):
        try:
            return impl(*args)
        except err as refusal:
            if oracle is not None:
                try:
                    oracle(*copy.deepcopy(args))
                except Exception:  # noqa: BLE001 - reference refuses too: legitimate refusal
                    raise refusal from None
                raise AssertionError(
                    f"refused an input the reference accepts: {refusal.failure_class}"
                ) from None
            raise
        except Exception as exc:  # noqa: BLE001
            raise RawEscape(f"raw {type(exc).__name__} escaped") from None

    return guarded


CHECKS = (check_fixture, check_semantics, check_order, check_totality)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_bound_implementation_passes(check):
    check(_guard(report, ProgressError), ProgressError)


def test_fixture_covers_every_failure_class():
    assert {r["expect_failure"] for r in CASES["malformed"]} == set(CLASSES)


# ---- mutants: the battery must be RED on each ------------------------------------------


def _wrap(fn):
    def impl(request):
        return fn(request, report)

    return impl


def _m_round_percent(request, real):
    out = real(request)
    if out["total"] and out["done"] != out["total"]:
        out["percent_bp"] = round(out["done"] * 10000 / out["total"])
        out["progress_id"] = _id({k: v for k, v in out.items() if k != "progress_id"})
    return out


def _m_report_after_completion(request, real):
    prev = request.get("previous") if isinstance(request, dict) else None
    if isinstance(prev, dict) and prev.get("done") == prev.get("total") and prev.get("total"):
        return real({**request, "previous": None})
    return real(request)


def _m_done_regress(request, real):
    try:
        return real(request)
    except ProgressError as exc:
        prev = request.get("previous")
        if exc.failure_class == "progress_conflict" and request["done"] < prev["done"]:
            return real({**request, "previous": None})
        raise


def _m_strict_clock(request, real):
    prev = request.get("previous") if isinstance(request, dict) else None
    if isinstance(prev, dict) and request.get("now") == prev.get("reported_at"):
        raise ProgressError("progress_conflict")
    return real(request)


def _m_total_rescale(request, real):
    try:
        return real(request)
    except ProgressError as exc:
        prev = request.get("previous")
        if exc.failure_class == "progress_conflict" and request["total"] != prev["total"]:
            return real({**request, "previous": None})
        raise


def _m_trust_previous(request, real):
    try:
        return real(request)
    except ProgressError as exc:
        if exc.failure_class == "corrupt_previous_record":
            return real({**request, "previous": None})
        raise


def _m_worker_ignored(request, real):
    try:
        return real(request)
    except ProgressError as exc:
        prev = request.get("previous")
        if exc.failure_class == "progress_conflict" and request["worker"] != prev["worker"]:
            return real({**request, "previous": None})
        raise


def _m_chain_dropped(request, real):
    out = real(request)
    if out["previous_id"] is not None:
        out["previous_id"] = None
        out["progress_id"] = _id({k: v for k, v in out.items() if k != "progress_id"})
    return out


def _m_conflict_before_corrupt(request, real):
    try:
        return real(request)
    except ProgressError as exc:
        prev = request.get("previous") if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_previous_record"
            and isinstance(prev, dict)
            and prev.get("done") == prev.get("total")
        ):
            raise ProgressError("progress_conflict") from None
        raise


def _m_raw_exception(request, real):
    if isinstance(request, dict) and request.get("now") is None:
        raise KeyError("now")
    return real(request)


def _m_bool_done(request, real):
    if isinstance(request, dict) and type(request.get("done")) is bool:
        return real({**request, "done": int(request["done"])})
    return real(request)


def _m_refuses_valid(request, real):
    if isinstance(request, dict) and request.get("previous") is not None:
        raise ProgressError("progress_conflict")
    return real(request)


def _m_refuses_everything(request, real):
    raise ProgressError("malformed_progress_request")


MUTANTS = {
    "refuses_valid": (_m_refuses_valid, "check_semantics"),
    "refuses_everything": (_m_refuses_everything, "check_fixture"),
    "round_percent": (_m_round_percent, "check_semantics"),
    "report_after_completion": (_m_report_after_completion, "check_semantics"),
    "done_regress": (_m_done_regress, "check_semantics"),
    "strict_clock": (_m_strict_clock, "check_semantics"),
    "total_rescale": (_m_total_rescale, "check_semantics"),
    "trust_previous": (_m_trust_previous, "check_order"),
    "worker_ignored": (_m_worker_ignored, "check_semantics"),
    "chain_dropped": (_m_chain_dropped, "check_semantics"),
    "conflict_before_corrupt": (_m_conflict_before_corrupt, "check_order"),
    "bool_done": (_m_bool_done, "check_totality"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_target(name):
    mutate, target = MUTANTS[name]
    check = next(c for c in CHECKS if c.__name__ == target)
    try:
        check(_guard(_wrap(mutate), ProgressError, report), ProgressError)
    except RawEscape:
        raise AssertionError(f"mutant {name} crashed, not refuted") from None
    except ProgressError:
        raise AssertionError(f"mutant {name} died of a typed error, not an assertion") from None
    except (AssertionError, pytest.fail.Exception):
        pass
    else:
        raise AssertionError(f"mutant {name} survived {target}")


def test_raw_exception_is_a_totality_failure_not_a_mutant_kill():
    """A crash is detected by the totality check as RawEscape; it is not
    counted among the semantic mutant kills above."""
    with pytest.raises(RawEscape):
        check_totality(_guard(_wrap(_m_raw_exception), ProgressError), ProgressError)
