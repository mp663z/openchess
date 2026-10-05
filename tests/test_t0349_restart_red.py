"""T0349 permanent red battery for jobs-restart functions.

Drives every row of the T0348 fixture (tests/fixtures/restart/cases.json),
hostile-input totality probes and pinned semantic probes through a
`restart(state, request)` callable and its `RestartError` class:

- happy/boundary: each request in a row is applied in order to the SAME
  state object (commit in place) and returns the pinned receipt exactly
  (exact types) and ends in the pinned state; state_id and restart_id are
  re-derived here from the contract preimages; the request is untouched and
  the receipt shares no container with the state or request;
- malformed: each row rejects with the pinned class and mapped code, fresh
  (no __cause__ or __context__), state and request untouched, and its
  single-locus repair is accepted;
- rollback: a rejected restart leaves state and request untouched, then the
  valid follow-up on the SAME state returns the pinned receipt and state;
- totality: hostile requests and states fail closed with a typed error,
  leave inputs untouched and never run caller code;
- semantics: only the worker's unexpired leases are released, in ascending
  seq order, attempts are kept, expiry at now is untouched, restart is
  idempotent, jobs conserve.

Standalone-red convention: GREEN against the contract-derived reference in
tests.test_t0347_restart_contract; every mutant below is RED on its pinned
target check. The production task switches the binding by replacing ONLY
the two binding lines with the production restart / RestartError names.
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

from tests import test_t0347_restart_contract as _reference  # noqa: E402
from tools.restart_contract_lint import FAILURE_MAPPING  # noqa: E402

restart = _reference.restart
RestartError = _reference.RestartError

CASES = json.loads((ROOT / "tests" / "fixtures" / "restart" / "cases.json").read_text())
MAX = 2**53 - 1
RECORD_FIELDS = (
    "op",
    "worker",
    "restarted_at",
    "released",
    "prior_state_id",
    "state_id",
    "restart_id",
)
CLASSES = ("malformed_restart_request", "corrupt_queue")


def _jid(key):
    return "job1:" + hashlib.sha256(f"job1|{key}".encode()).hexdigest()


def _job(key, seq, status="ready", attempts=0, owner=None, expires=None, priority=5, payload=None):
    return {
        "job_id": _jid(key),
        "seq": seq,
        "priority": priority,
        "payload": {"k": key} if payload is None else payload,
        "status": status,
        "attempts": attempts,
        "lease_owner": owner,
        "lease_expires_at": expires,
    }


def _leased(key, seq, owner="w1", expires=2000, attempts=1):
    return _job(key, seq, "leased", attempts, owner, expires)


def _state(*jobs, next_seq=None):
    jobs = list(jobs)
    return {
        "jobs": jobs,
        "next_seq": (jobs[-1]["seq"] + 1 if jobs else 0) if next_seq is None else next_seq,
    }


def _rq(worker="w1", now=1000, **kw):
    base = {"op": "restart", "worker": worker, "now": now}
    base.update(kw)
    return base


def _state_id(st):
    body = json.dumps(st, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "qs1:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _restart_id(record_without_id):
    body = json.dumps(record_without_id, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "rst1:" + hashlib.sha256(b"rst1" + bytes([0]) + body.encode("utf-8")).hexdigest()


def _walk_ids(obj, out):
    if isinstance(obj, (dict, list)):
        out.add(id(obj))
        for v in obj.values() if isinstance(obj, dict) else obj:
            _walk_ids(v, out)
    return out


def _apply_ok(impl, st, request, expect_record, expect_state):
    req_before, prior = copy.deepcopy(request), copy.deepcopy(st)
    record = impl(st, request)
    assert request == req_before
    assert type(record) is dict and record == expect_record
    assert set(record) == set(RECORD_FIELDS)
    for key, value in expect_record.items():
        assert type(record[key]) is type(value), key
    assert st == expect_state
    assert record["prior_state_id"] == _state_id(prior)
    assert record["state_id"] == _state_id(st)
    assert record["restart_id"] == _restart_id(
        {k: v for k, v in record.items() if k != "restart_id"}
    )
    assert not (_walk_ids(record, set()) & (_walk_ids(request, set()) | _walk_ids(st, set())))
    return record


def _rejects(impl, err, st, request, failure):
    st_snap, rq_snap = copy.deepcopy(st), copy.deepcopy(request)
    with _raises_ctx(err) as caught:
        impl(st, request)
    exc = caught.value
    assert type(exc) is err
    assert exc.failure_class == failure
    assert exc.code == FAILURE_MAPPING[failure]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False
    assert st == st_snap and request == rq_snap


def _nested(depth):
    node = 1
    for _ in range(depth):
        node = [node]
    return node


# ---- checks shared by the tests and the mutant harness ---------------------------------


def check_fixture(impl, err):
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            st = copy.deepcopy(row["state"])
            assert len(row["requests"]) == len(row["expect_records"]) == len(row["expect_states"])
            for request, record, expect in zip(
                row["requests"], row["expect_records"], row["expect_states"], strict=True
            ):
                _apply_ok(impl, st, request, record, expect)
    for row in CASES["malformed"]:
        _rejects(impl, err, row["state"], row["request"], row["expect_failure"])
        repaired = row["minimal_repair"]
        impl(copy.deepcopy(repaired["state"]), repaired["request"])
    for row in CASES["rollback"]:
        rejected = row["rejected"]
        _rejects(impl, err, rejected["state"], rejected["request"], row["expect_failure"])
        _apply_ok(
            impl,
            copy.deepcopy(row["state"]),
            row["request"],
            row["expect_record"],
            row["expect_state"],
        )


def check_semantics(impl, err):
    # canonical identity: payload key order never changes the state ids
    ab = impl(_state(_job("a", 0, payload={"b": 1, "a": 2})), _rq())
    ba = impl(_state(_job("a", 0, payload={"a": 2, "b": 1})), _rq())
    assert ab["prior_state_id"] == ba["prior_state_id"] and ab["state_id"] == ba["state_id"]
    # the queue-size bound is inclusive: exactly 10000 jobs are accepted
    full = {"jobs": [_job(f"k{i}", i) for i in range(10000)], "next_seq": 10000}
    kept = impl(full, _rq())
    assert kept["released"] == [] and len(full["jobs"]) == 10000
    over = {"jobs": [_job(f"k{i}", i) for i in range(10001)], "next_seq": 10001}
    with _raises_ctx(err) as caught:
        impl(over, _rq())
    assert caught.value.failure_class == "corrupt_queue"
    st = _state(
        _leased("a", 0, "w1", 2000, attempts=2),
        _leased("b", 1, "w2", 2000),
        _leased("c", 2, "w1", 1000),  # expiry == now: left untouched
        _leased("d", 3, "w1", 999),  # already expired: left untouched
        _job("e", 4),
        _job("f", 5, "done", attempts=1),
        _job("g", 6, "dead", attempts=5),
        _leased("h", 7, "w1", 5000, attempts=5),  # at max attempts: released, not buried
    )
    snapshot = copy.deepcopy(st)
    record = impl(st, _rq("w1", 1000))
    assert record["released"] == [_jid("a"), _jid("h")]  # ascending seq
    by = {j["job_id"]: j for j in st["jobs"]}
    for key in ("a", "h"):
        j = by[_jid(key)]
        assert (j["status"], j["lease_owner"], j["lease_expires_at"]) == ("ready", None, None)
    assert by[_jid("a")]["attempts"] == 2 and by[_jid("h")]["attempts"] == 5  # kept
    for key in ("b", "c", "d", "e", "f", "g"):
        assert by[_jid(key)] == next(j for j in snapshot["jobs"] if j["job_id"] == _jid(key))
    assert st["next_seq"] == snapshot["next_seq"]
    assert [j["job_id"] for j in st["jobs"]] == [j["job_id"] for j in snapshot["jobs"]]
    for before, after in zip(snapshot["jobs"], st["jobs"], strict=True):
        for field in ("job_id", "seq", "priority", "payload", "attempts"):
            assert before[field] == after[field]
    # idempotent: a second restart at the same now releases nothing
    again_state = copy.deepcopy(st)
    again = impl(st, _rq("w1", 1000))
    assert again["released"] == [] and st == again_state
    assert again["prior_state_id"] == again["state_id"]
    # an empty queue and a worker with no lease are successful empty receipts
    empty = _state()
    assert impl(empty, _rq())["released"] == [] and empty == _state()
    other = _state(_leased("a", 0, "w2", 2000))
    keep = copy.deepcopy(other)
    assert impl(other, _rq("w1"))["released"] == [] and other == keep
    # a released job is claimable again by the queue and the receipt is deterministic
    s1, s2 = _state(_leased("a", 0)), _state(_leased("a", 0))
    r1, r2 = impl(s1, _rq()), impl(s2, _rq())
    assert r1 == r2 and r1 is not r2 and s1 == s2


def check_order(impl, err):
    bad_state = _state(_job("a", 0, "ready", attempts=99))
    # request shape before state integrity
    _rejects(impl, err, bad_state, _rq(now=-1), "malformed_restart_request")
    _rejects(impl, err, "not a state", _rq(worker="bad worker"), "malformed_restart_request")
    # a corrupt state is rejected even when no job would be released
    _rejects(impl, err, bad_state, _rq(), "corrupt_queue")
    for bad in (
        _state(_job("a", 0), _job("b", 0), next_seq=2),  # seq not increasing
        _state(_job("a", 0), next_seq=0),  # next_seq not past last
        _state(_job("a", 0), _job("a", 1)),  # duplicate id
        _state(_leased("a", 0, expires=0)),
        _state(_leased("a", 0, attempts=0)),
        _state(_job("a", 0, "dead", attempts=4)),
        _state(_job("a", 0, "done", attempts=0)),
        _state(_job("a", 0, owner="w1")),
        _state(_job("a", 0, priority=10)),
        _state(_job("a", 0, payload=_nested(65))),
        _state(_job("a", 0, payload="\ud800")),
    ):
        _rejects(impl, err, bad, _rq(), "corrupt_queue")


class Boom:
    def __getattribute__(self, name):
        raise Touched("caller object touched: " + name)


class BoomStr(str):
    def __eq__(self, other):
        raise Touched("eq hook")

    __hash__ = str.__hash__


class BoomDict(dict):
    def __iter__(self):
        raise Touched("iter hook")

    def keys(self):
        raise Touched("keys hook")

    def items(self):
        raise Touched("items hook")


class BoomList(list):
    def __iter__(self):
        raise Touched("iter hook")

    def __len__(self):
        raise Touched("len hook")


HOSTILE = (None, True, False, -1, 2**70, 1.0, float("nan"), "", "x", b"b", [], (1,), {}, Boom)
JOB_FIELDS = tuple(_job("a", 0))


def check_totality(impl, err):
    for field in ("op", "worker", "now"):
        for bad in HOSTILE:
            request = _rq()
            request[field] = bad
            if (field, bad) == ("worker", "x"):
                continue  # a valid worker id
            try:
                impl(_state(_leased("a", 0)), request)
            except err as exc:
                assert exc.failure_class == "malformed_restart_request"
            else:
                raise AssertionError((field, bad))
    for bad in (Boom(), BoomDict(), BoomStr("restart"), [_rq()], (_rq(),), "restart", None):
        try:
            impl(_state(), bad)
        except err as exc:
            assert exc.failure_class == "malformed_restart_request"
        else:
            raise AssertionError(bad)
    for bad in HOSTILE + (BoomDict(), BoomList()):
        try:
            impl(bad, _rq())
        except err as exc:
            assert exc.failure_class == "corrupt_queue"
        else:
            raise AssertionError(bad)
    for field in ("jobs", "next_seq"):
        for bad in HOSTILE:
            st = _state(_leased("a", 0))
            st[field] = bad
            if (field, bad) == ("jobs", []):
                continue  # an empty queue below next_seq is a valid state
            try:
                impl(st, _rq())
            except err as exc:
                assert exc.failure_class == "corrupt_queue", (field, bad)
            else:
                raise AssertionError((field, bad))
    for field in JOB_FIELDS:
        for bad in HOSTILE:
            st = _state(_leased("a", 0))
            before = copy.deepcopy(st)
            st["jobs"][0][field] = bad
            if st == before or (field, bad) == ("lease_owner", "x"):
                continue  # unchanged, or a valid worker id
            snap = copy.deepcopy(st)
            try:
                impl(st, _rq())
            except err as exc:
                assert exc.failure_class == "corrupt_queue", (field, bad)
                assert st == snap
            else:
                if field != "payload":
                    raise AssertionError((field, bad))
    for forged_state in (
        BoomDict(_state()),
        _state(BoomDict(_leased("a", 0))),
        _state(_job("a", 0, payload=Boom())),
        _state(_job("a", 0, payload=BoomDict(a=1))),
        _state(_job("a", 0, payload=BoomList([1]))),
        _state(_job("a", 0, payload={BoomStr("k"): 1})),
        _state(_leased("a", 0, owner=BoomStr("w1"))),
    ):
        try:
            impl(forged_state, _rq())
        except err:
            pass
        except AssertionError as exc:
            raise Touched("caller code ran: " + str(exc)) from None
        else:
            raise Touched("hostile state accepted")
    for forged_request in (BoomDict(_rq()), _rq(worker=BoomStr("w1"))):
        try:
            impl(_state(), forged_request)
        except err:
            pass
        except AssertionError as exc:
            raise Touched("caller code ran: " + str(exc)) from None
        else:
            raise Touched("subclass accepted")
    cyc = []
    cyc.append(cyc)
    shared = {"x": 1}
    for bad in (
        _state(_job("a", 0, payload=shared), _job("b", 1, payload=shared)),
        _state(_job("a", 0, payload=10**4001)),
        _state(_job("a", 0, payload={"a": float("inf")})),
    ):
        _rejects(impl, err, bad, _rq(), "corrupt_queue")
    cyclic = _state(_job("a", 0, payload=cyc))
    with _raises_ctx(err) as caught:
        impl(cyclic, _rq())
    assert caught.value.failure_class == "corrupt_queue"
    assert cyclic["jobs"][0]["payload"] is cyc and cyc[0] is cyc  # untouched, not unrolled
    for bad in ("w\n", "", "w" * 65, "w w", "é"):
        _rejects(impl, err, _state(), _rq(worker=bad), "malformed_restart_request")
    for field, bad in (("now", True), ("now", MAX + 1), ("now", -1)):
        _rejects(impl, err, _state(), _rq(**{field: bad}), "malformed_restart_request")
    _rejects(impl, err, _state(), {**_rq(), "extra": 1}, "malformed_restart_request")
    missing = _rq()
    del missing["now"]
    _rejects(impl, err, _state(), missing, "malformed_restart_request")


class Touched(AssertionError):
    """A caller-owned hostile object was touched by the implementation."""


class _raises_ctx:
    """pytest.raises that reports a missing refusal as a plain AssertionError."""

    def __init__(self, expected):
        self.expected = expected
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        if kind is None:
            raise AssertionError(f"expected {self.expected.__name__}, nothing was raised")
        if not issubclass(kind, self.expected):
            return False
        self.value = value
        return True


class RawEscape(AssertionError):
    """A non-contract exception escaped the implementation (a crash, not a decision)."""


def _guard(impl, err, oracle=None):
    """Any exception that is not the typed contract error is a crash and becomes
    RawEscape (an AssertionError raised inside the implementation included),
    except Touched, which is the semantic detection of a caller object being
    touched. With an oracle (the reference), a typed refusal of an input the
    reference accepts is an acceptance failure and surfaces as a plain
    AssertionError. The classification runs outside the except scope so a
    correct refusal keeps __context__ None."""

    def guarded(*args):
        refusal = None
        try:
            return impl(*args)
        except err as caught:
            refusal = caught
        except Touched:
            raise
        except Exception as exc:  # noqa: BLE001
            crash = type(exc).__name__
        else:  # pragma: no cover
            crash = None
        if refusal is None:
            raise RawEscape(f"raw {crash} escaped") from None
        if oracle is not None:
            try:
                oracle(*copy.deepcopy(args))
            except Exception:  # noqa: BLE001 - the reference refuses too
                accepted = False
            else:
                accepted = True
            if accepted:
                raise AssertionError(
                    f"refused an input the reference accepts: {refusal.failure_class}"
                )
        raise refusal

    return guarded


class _InertKey(str):
    """A str subclass with no hooks: only its type differs from a plain str."""


def _inert(mapping):
    return {_InertKey(k): v for k, v in mapping.items()}


def check_inert_keys(impl, err):
    st = _state(_leased("a", 0))
    _rejects(impl, err, st, _inert(_rq()), "malformed_restart_request")
    _rejects(impl, err, _inert(st), _rq(), "corrupt_queue")
    _rejects(impl, err, _state(_inert(_leased("a", 0))), _rq(), "corrupt_queue")


class _Opaque:
    def __deepcopy__(self, memo):
        return self


_OPAQUE = _Opaque()


def check_payload_integrity(impl, err):
    # each row is a typed corrupt_queue refusal (an assertion on the failure class)
    for bad in ("\ud800", {"k": "\udfff"}, {"k": (1, 2)}, {"k": {1, 2}}, {"k": _OPAQUE}):
        _rejects(impl, err, _state(_job("a", 0, payload=bad)), _rq(), "corrupt_queue")
    _rejects(impl, err, _state(_job("a", 0, payload={1: 2})), _rq(), "corrupt_queue")
    _rejects(impl, err, _state(_job("a", 0, payload={"k": float("inf")})), _rq(), "corrupt_queue")
    for jobs in ((), (_job("a", 0),), {"a": 1}, "jobs"):
        _rejects(impl, err, {"jobs": jobs, "next_seq": 1}, _rq(), "corrupt_queue")
    # their accepted twins
    for good in ("snow \u2603", {"k": [1, 2.5, None, True, "x"]}, {"": 0}):
        assert impl(_state(_job("a", 0, payload=good)), _rq())["op"] == "restart"


CHECKS = (
    check_fixture,
    check_semantics,
    check_order,
    check_totality,
    check_inert_keys,
    check_payload_integrity,
)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_bound_implementation_passes(check):
    check(_guard(restart, RestartError), RestartError)


def test_fixture_covers_every_failure_class():
    assert {r["expect_failure"] for r in CASES["malformed"]} == set(CLASSES)


# ---- mutants: the battery must be RED on each ------------------------------------------


def _wrap(fn):
    def impl(st, request):
        return fn(st, request, restart)

    return impl


def _redo(record):
    record["restart_id"] = _restart_id({k: v for k, v in record.items() if k != "restart_id"})
    return record


def _m_releases_expired(st, request, real):
    if isinstance(request, dict) and isinstance(st, dict):
        for job in list(st.get("jobs", [])) if isinstance(st.get("jobs"), list) else []:
            if (
                isinstance(job, dict)
                and job.get("lease_owner") == request.get("worker")
                and job.get("lease_expires_at") == request.get("now")
            ):
                job["lease_expires_at"] = request["now"] + 1  # treat equal-now as live
    return real(st, request)


def _m_refunds_attempt(st, request, real):
    before = {j["job_id"]: j["attempts"] for j in st["jobs"]} if isinstance(st, dict) else {}
    record = real(st, request)
    for job in st["jobs"]:
        if job["job_id"] in record["released"] and before.get(job["job_id"], 0) > 0:
            job["attempts"] -= 1
    record["state_id"] = _state_id(st)
    return _redo(record)


def _m_all_workers(st, request, real):
    record = real(st, request)
    for job in st["jobs"]:
        if job["status"] == "leased" and request["now"] < job["lease_expires_at"]:
            job.update(status="ready", lease_owner=None, lease_expires_at=None)
            record["released"].append(job["job_id"])
    record["state_id"] = _state_id(st)
    return _redo(record)


def _m_release_unsorted(st, request, real):
    record = real(st, request)
    record["released"] = record["released"][::-1]
    return _redo(record)


def _m_buries_at_max(st, request, real):
    record = real(st, request)
    for job in st["jobs"]:
        if job["job_id"] in record["released"] and job["attempts"] == 5:
            job.update(status="dead")
    record["state_id"] = _state_id(st)
    return _redo(record)


def _m_keeps_owner(st, request, real):
    record = real(st, request)
    for job in st["jobs"]:
        if job["job_id"] in record["released"]:
            job["lease_owner"] = request["worker"]
    record["state_id"] = _state_id(st)
    return _redo(record)


def _m_skips_integrity(st, request, real):
    try:
        return real(st, request)
    except RestartError as exc:
        if exc.failure_class == "corrupt_queue":
            return {
                "op": "restart",
                "worker": request["worker"],
                "restarted_at": request["now"],
                "released": [],
                "prior_state_id": "",
                "state_id": "",
                "restart_id": "",
            }
        raise


def _m_state_first(st, request, real):
    try:
        return real(st, request)
    except RestartError as exc:
        if exc.failure_class == "malformed_restart_request" and isinstance(st, dict):
            try:
                real(st, _rq())
            except RestartError as inner:
                if inner.failure_class == "corrupt_queue":
                    raise RestartError("corrupt_queue") from None
        raise


def _m_prior_is_new(st, request, real):
    record = real(st, request)
    record["prior_state_id"] = record["state_id"]
    return _redo(record)


def _m_raw_exception(st, request, real):
    if isinstance(request, dict) and request.get("now") is None:
        raise KeyError("now")
    return real(st, request)


def _m_torn_commit(st, request, real):
    work = copy.deepcopy(st) if isinstance(st, dict) else st
    try:
        return real(st, request)
    except RestartError:
        if isinstance(st, dict) and isinstance(work, dict) and work.get("jobs"):
            st["jobs"] = []  # a rejected call leaves a torn state
        raise


def _m_refuses_valid(st, request, real):
    if isinstance(st, dict) and any(
        isinstance(j, dict) and j.get("lease_owner") == request.get("worker")
        for j in st.get("jobs", [])
    ):
        raise RestartError("corrupt_queue")
    return real(st, request)


def _m_refuses_everything(st, request, real):
    raise RestartError("malformed_restart_request")


MUTANTS = {
    "refuses_valid": (_m_refuses_valid, "check_semantics"),
    "refuses_everything": (_m_refuses_everything, "check_fixture"),
    "releases_at_expiry": (_m_releases_expired, "check_semantics"),
    "refunds_attempt": (_m_refunds_attempt, "check_semantics"),
    "all_workers": (_m_all_workers, "check_semantics"),
    "release_unsorted": (_m_release_unsorted, "check_semantics"),
    "buries_at_max": (_m_buries_at_max, "check_semantics"),
    "keeps_owner": (_m_keeps_owner, "check_semantics"),
    "skips_integrity": (_m_skips_integrity, "check_order"),
    "state_first": (_m_state_first, "check_order"),
    "prior_is_new": (_m_prior_is_new, "check_fixture"),
    "torn_commit": (_m_torn_commit, "check_order"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_target(name):
    mutate, target = MUTANTS[name]
    check = next(c for c in CHECKS if c.__name__ == target)
    try:
        check(_guard(_wrap(mutate), RestartError, restart), RestartError)
    except RawEscape:
        raise AssertionError(f"mutant {name} crashed, not refuted") from None
    except RestartError:
        raise AssertionError(f"mutant {name} died of a typed error, not an assertion") from None
    except AssertionError:
        pass
    else:
        raise AssertionError(f"mutant {name} survived {target}")


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_guard_with_oracle_is_identity_on_the_reference(check):
    check(_guard(restart, RestartError, restart), RestartError)


def test_raw_exception_is_a_totality_failure_not_a_mutant_kill():
    """A crash is detected by the totality check as RawEscape; it is not
    counted among the semantic mutant kills above."""
    with pytest.raises(RawEscape):
        check_totality(_guard(_wrap(_m_raw_exception), RestartError), RestartError)
