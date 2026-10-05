"""T0331 permanent red battery for jobs dead-letter functions.

Drives every row of the T0330 fixture (tests/fixtures/dead_letter/cases.json),
hostile-input totality probes and pinned semantic probes through a
`bury(request)` callable and its `DeadLetterError` class:

- happy/boundary: each request returns the pinned record exactly (exact
  types), the payload_digest and entry_id are re-derived here from the
  contract preimages, the entry stores only the digest (never a payload
  copy), the request is untouched and shares no container with the record;
- malformed: each row rejects with the pinned class and mapped code, fresh
  (no __cause__ or __context__), request untouched, and its single-locus
  repair is accepted or reaches the next stage as pinned;
- rollback: a rejected request leaves everything untouched and the valid
  follow-up buries as pinned;
- totality: hostile types at every request and job field, and hostile payload
  objects, fail closed with a typed error and never run caller code;
- semantics: only dead jobs are buried, every other valid status is
  job_not_dead, dead needs max attempts and no lease, validation order.

Standalone-red convention: GREEN against the contract-derived reference in
tests.test_t0329_dead_letter_contract; every mutant below is RED on its
pinned target check. The production task switches the binding by replacing
ONLY the two binding lines with the production bury / DeadLetterError names.
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

from tests import test_t0329_dead_letter_contract as _reference  # noqa: E402
from tools.dead_letter_contract_lint import FAILURE_MAPPING  # noqa: E402

bury = _reference.bury
DeadLetterError = _reference.DeadLetterError

CASES = json.loads((ROOT / "tests" / "fixtures" / "dead_letter" / "cases.json").read_text())
JOB = "job1:" + "a" * 64
MAX = 2**53 - 1
MAX_ATTEMPTS = 5
RECORD_FIELDS = (
    "op",
    "job_id",
    "seq",
    "priority",
    "payload_digest",
    "attempts",
    "reason",
    "buried_at",
    "entry_id",
)
CLASSES = ("malformed_bury_request", "corrupt_job", "job_not_dead")


def _job(**kw):
    base = {
        "job_id": JOB,
        "seq": 3,
        "priority": 4,
        "payload": {"k": [1, "x", None]},
        "status": "dead",
        "attempts": 5,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    base.update(kw)
    return base


def _req(**kw):
    base = {"op": "bury", "job": _job(), "reason": "exhausted", "now": 100}
    base.update(kw)
    return base


def _digest(prefix, value):
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return prefix + ":" + hashlib.sha256(prefix.encode() + bytes([0]) + body.encode()).hexdigest()


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
    assert record["payload_digest"] == _digest("pd1", request["job"]["payload"])
    assert record["entry_id"] == _digest(
        "dl1", {k: v for k, v in record.items() if k != "entry_id"}
    )
    assert "payload" not in record
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


def _nested(depth):
    node = 1
    for _ in range(depth):
        node = [node]
    return node


# ---- checks shared by the tests and the mutant harness ---------------------------------


def check_fixture(impl, err):
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            assert len(row["requests"]) == len(row["expect_records"]), row["name"]
            for request, expect in zip(row["requests"], row["expect_records"], strict=True):
                _ok(impl, request, expect)
    for row in CASES["malformed"]:
        _rejects(impl, err, row["request"], row["expect_failure"])
        repaired = row["minimal_repair"]["request"]
        try:
            impl(repaired)
        except err as exc:
            assert exc.failure_class != row["expect_failure"], row["name"]
    for row in CASES["rollback"]:
        _rejects(impl, err, row["rejected_request"], row["expect_failure"])
        _ok(impl, row["request"], row["expect_record"])


def check_semantics(impl, err):
    for reason in ("exhausted", "permanent"):
        assert impl(_req(reason=reason))["reason"] == reason
    a, b = impl(_req()), impl(_req())
    assert a == b and a is not b
    assert impl(_req(reason="permanent"))["entry_id"] != impl(_req())["entry_id"]
    assert impl(_req(now=101))["buried_at"] == 101
    one, two = impl(_req(job=_job(payload={"a": 1}))), impl(_req(job=_job(payload={"a": 2})))
    assert one["payload_digest"] != two["payload_digest"]
    swapped = impl(_req(job=_job(payload={"b": 1, "a": 2})))
    assert (
        swapped["payload_digest"]
        == impl(_req(job=_job(payload={"a": 2, "b": 1})))["payload_digest"]
    )
    for status, extra in (
        ("ready", {"attempts": 0}),
        ("ready", {"attempts": 3}),
        ("done", {"attempts": 2}),
        ("leased", {"attempts": 1, "lease_owner": "w1", "lease_expires_at": 9}),
    ):
        _rejects(impl, err, _req(job=_job(status=status, **extra)), "job_not_dead")
    _rejects(impl, err, _req(job=_job(attempts=4)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(attempts=4), reason="permanent"), "corrupt_job")
    _rejects(impl, err, _req(job=_job(lease_owner="w1")), "corrupt_job")
    _rejects(impl, err, _req(job=_job(lease_expires_at=5)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload=_nested(65))), "corrupt_job")
    assert impl(_req(job=_job(payload=_nested(64))))["op"] == "bury"
    _rejects(impl, err, _req(job=_job(payload="\ud800")), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload={"a": float("nan")})), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload=10**4001)), "corrupt_job")
    shared = [1]
    _rejects(impl, err, _req(job=_job(payload={"a": shared, "b": shared})), "corrupt_job")


def check_order(impl, err):
    # shape before job integrity
    _rejects(impl, err, _req(job=_job(seq=-1), now=-1), "malformed_bury_request")
    _rejects(impl, err, _req(job=_job(seq=-1), reason="nope"), "malformed_bury_request")
    # job integrity before dead status: a corrupt non-dead job is corrupt, not job_not_dead
    _rejects(impl, err, _req(job=_job(status="ready", attempts=99)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(status="done", attempts=0)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(status="ready", lease_owner="w1", attempts=0)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(status="bogus")), "corrupt_job")


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


class BoomList(list):
    def __iter__(self):
        raise AssertionError("iter hook")

    def __len__(self):
        raise AssertionError("len hook")


HOSTILE = (None, True, False, -1, 2**70, 1.0, float("nan"), "", "x", b"b", [], (1,), {}, Boom)
JOB_FIELDS = tuple(_job())


def check_totality(impl, err):
    for field in ("op", "job", "reason", "now"):
        for bad in HOSTILE:
            request = _req()
            request[field] = bad
            try:
                impl(request)
            except err as exc:
                assert exc.failure_class in CLASSES
            else:
                raise AssertionError((field, bad))
    for field in JOB_FIELDS:
        if field == "payload":
            continue
        for bad in HOSTILE:
            candidate = _job()
            candidate[field] = bad
            if candidate == _job():
                continue
            try:
                impl(_req(job=candidate))
            except err as exc:
                assert exc.failure_class in ("corrupt_job", "job_not_dead")
            else:
                raise AssertionError((field, bad))
    for bad in (Boom(), BoomDict(), BoomStr("bury"), [_req()], (_req(),), "bury", None):
        try:
            impl(bad)
        except err as exc:
            assert exc.failure_class == "malformed_bury_request"
        else:
            raise AssertionError(bad)
    for forged in (
        BoomDict(_req()),
        _req(job=BoomDict(_job())),
        _req(reason=BoomStr("exhausted")),
        _req(job=_job(job_id=BoomStr(JOB))),
        _req(job=_job(status=BoomStr("dead"))),
        _req(job=_job(payload=Boom())),
        _req(job=_job(payload=BoomDict(a=1))),
        _req(job=_job(payload=BoomList([1]))),
        _req(job=_job(payload={BoomStr("k"): 1})),
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
        _rejects(impl, err, _req(job=_job(job_id=bad)), "corrupt_job")
    _rejects(impl, err, {**_req(), "extra": 1}, "malformed_bury_request")
    missing = _req()
    del missing["now"]
    _rejects(impl, err, missing, "malformed_bury_request")
    _rejects(impl, err, _req(job={**_job(), "extra": 1}), "corrupt_job")


class RawEscape(AssertionError):
    """A non-contract exception escaped the implementation (a crash, not a decision)."""


def _guard(impl, err):
    """Typed contract errors and assertion failures pass through; any other
    exception is a crash and becomes RawEscape: a totality failure that is
    never counted as a semantic mutant kill."""

    def guarded(*args):
        try:
            return impl(*args)
        except (err, AssertionError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise RawEscape(f"raw {type(exc).__name__} escaped") from None

    return guarded


CHECKS = (check_fixture, check_semantics, check_order, check_totality)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_bound_implementation_passes(check):
    check(_guard(bury, DeadLetterError), DeadLetterError)


def test_fixture_covers_every_failure_class_and_reason():
    assert {r["expect_failure"] for r in CASES["malformed"]} == set(CLASSES)
    reasons = {
        req["reason"] for s in ("happy", "boundary") for r in CASES[s] for req in r["requests"]
    }
    assert reasons == {"exhausted", "permanent"}


# ---- mutants: the battery must be RED on each ------------------------------------------


def _wrap(fn):
    def impl(request):
        return fn(request, bury)

    return impl


def _redo(out):
    out["entry_id"] = _digest("dl1", {k: v for k, v in out.items() if k != "entry_id"})
    return out


def _m_buries_ready(request, real):
    try:
        return real(request)
    except DeadLetterError as exc:
        if exc.failure_class == "job_not_dead":
            return real({**request, "job": {**request["job"], "status": "dead", "attempts": 5}})
        raise


def _m_stores_payload(request, real):
    out = real(request)
    out["payload"] = request["job"]["payload"]
    return out


def _m_digest_unsorted(request, real):
    out = real(request)
    body = json.dumps(request["job"]["payload"], separators=(",", ":"), ensure_ascii=True)
    out["payload_digest"] = "pd1:" + hashlib.sha256(b"pd1\x00" + body.encode()).hexdigest()
    return _redo(out)


def _m_now_ignored(request, real):
    out = real(request)
    out["buried_at"] = 100
    return _redo(out)


def _m_reason_not_bound(request, real):
    out = real(request)
    if out["reason"] == "permanent":
        out["reason"] = "exhausted"
        _redo(out)
    return out


def _m_dead_below_max_ok(request, real):
    try:
        return real(request)
    except DeadLetterError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_job"
            and isinstance(job, dict)
            and job.get("status") == "dead"
            and job.get("attempts") == 4
        ):
            return real({**request, "job": {**job, "attempts": 5}})
        raise


def _m_lease_ignored(request, real):
    try:
        return real(request)
    except DeadLetterError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if exc.failure_class == "corrupt_job" and isinstance(job, dict) and job.get("lease_owner"):
            return real({**request, "job": {**job, "lease_owner": None}})
        raise


def _m_not_dead_before_corrupt(request, real):
    try:
        return real(request)
    except DeadLetterError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_job"
            and isinstance(job, dict)
            and job.get("status") in ("ready", "done")
        ):
            raise DeadLetterError("job_not_dead") from None
        raise


def _m_raw_exception(request, real):
    if isinstance(request, dict) and request.get("now") is None:
        raise KeyError("now")
    return real(request)


def _m_payload_unbounded(request, real):
    try:
        return real(request)
    except DeadLetterError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_job"
            and isinstance(job, dict)
            and isinstance(job.get("payload"), list)
        ):
            return real({**request, "job": {**job, "payload": []}})
        raise


def _m_refuses_valid(request, real):
    if isinstance(request, dict) and request.get("reason") == "permanent":
        raise DeadLetterError("job_not_dead")
    return real(request)


def _m_refuses_everything(request, real):
    raise DeadLetterError("malformed_bury_request")


RAW_MUTANTS = {"raw_exception"}

MUTANTS = {
    "refuses_valid": (_m_refuses_valid, "check_semantics"),
    "refuses_everything": (_m_refuses_everything, "check_fixture"),
    "buries_ready": (_m_buries_ready, "check_semantics"),
    "stores_payload": (_m_stores_payload, "check_fixture"),
    "digest_unsorted": (_m_digest_unsorted, "check_semantics"),
    "now_ignored": (_m_now_ignored, "check_semantics"),
    "reason_not_bound": (_m_reason_not_bound, "check_semantics"),
    "dead_below_max_ok": (_m_dead_below_max_ok, "check_semantics"),
    "lease_ignored": (_m_lease_ignored, "check_semantics"),
    "not_dead_before_corrupt": (_m_not_dead_before_corrupt, "check_order"),
    "raw_exception": (_m_raw_exception, "check_totality"),
    "payload_unbounded": (_m_payload_unbounded, "check_semantics"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_target(name):
    mutate, target = MUTANTS[name]
    check = next(c for c in CHECKS if c.__name__ == target)
    try:
        check(_guard(_wrap(mutate), DeadLetterError), DeadLetterError)
    except RawEscape:
        # only the mutant that raises a raw exception on purpose may die this way
        assert name in RAW_MUTANTS, f"mutant {name} crashed instead of being refuted"
    except (AssertionError, DeadLetterError, pytest.fail.Exception):
        assert name not in RAW_MUTANTS, f"mutant {name} was not refuted by the raw-exception probe"
    else:
        raise AssertionError(f"mutant {name} survived {target}")
