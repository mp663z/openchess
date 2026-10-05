"""T0313 permanent red battery for jobs-retry decision functions.

Drives every row of the T0312 fixture (tests/fixtures/retry/cases.json),
hostile-input totality probes and pinned semantic probes through a
`decide(request)` callable and its `RetryError` class:

- happy/boundary: every request returns the pinned record exactly (field
  order, exact types), the decision_id is re-derived here independently from
  the contract's digest rule, and the caller's request is unchanged;
- malformed: each row rejects with the pinned class and mapped code, fresh
  (no __cause__ or __context__), request untouched, and its declared
  single-locus repair is accepted;
- rollback: a rejected request leaves everything untouched and the valid
  follow-up decides as pinned;
- totality: hostile types at every request and policy field fail closed with
  a typed error, never a raw exception, and never run caller code;
- semantics: backoff sequence, cap, monotone delay, permanent and exhausted
  are dead, timeout equals transient, clock overflow is an error and is never
  clamped, no jitter, result shares no container with the input.

Standalone-red convention: the battery is GREEN against the contract-derived
reference in tests.test_t0311_retry_contract and every mutant below is RED.
The production task switches the binding by replacing ONLY the two binding
lines with the production decide / RetryError names; no assertion changes.
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

from tests import test_t0311_retry_contract as _reference  # noqa: E402
from tools.retry_contract_lint import FAILURE_MAPPING  # noqa: E402

decide = _reference.decide
RetryError = _reference.RetryError

CASES = json.loads((ROOT / "tests" / "fixtures" / "retry" / "cases.json").read_text())
JOB = "job1:" + "a" * 64
POLICY = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 30000}
MAX_NOW = 2**53 - 1
MAX_ATTEMPTS = 5
RECORD_ORDER = ["op", "job_id", "decision", "attempts", "delay_ms", "retry_at", "decision_id"]


def _req(**kw):
    base = {
        "op": "decide",
        "job_id": JOB,
        "attempts": 1,
        "failure": "transient",
        "now": 0,
        "policy": dict(POLICY),
    }
    base.update(kw)
    return base


def _independent_id(request, record):
    body = json.dumps(
        {"request": request, "record": {k: v for k, v in record.items() if k != "decision_id"}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "rd1:" + hashlib.sha256(b"rd1\x00" + body.encode()).hexdigest()


def _exact_record(record, expect):
    assert type(record) is dict
    assert list(record) == RECORD_ORDER or sorted(record) == sorted(RECORD_ORDER)
    assert record == expect
    for key, value in expect.items():
        assert type(record[key]) is type(value), key


def _ok(impl, request, expect):
    before = copy.deepcopy(request)
    record = impl(request)
    assert request == before
    _exact_record(record, expect)
    assert record["decision_id"] == _independent_id(request, record)
    return record


def _rejects(impl, err, request, failure):
    snap = copy.deepcopy(request)
    with _raises_ctx(err) as caught:
        impl(request)
    exc = caught.value
    assert type(exc) is err
    assert exc.failure_class == failure
    assert exc.code == FAILURE_MAPPING[failure]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False
    assert request == snap


# ---- check functions shared by the tests and the mutant harness -------------------------


def check_fixture(impl, err):
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            assert len(row["requests"]) == len(row["expect_records"]), row["name"]
            for request, expect in zip(row["requests"], row["expect_records"], strict=True):
                _ok(impl, request, expect)
    for row in CASES["malformed"]:
        _rejects(impl, err, row["request"], row["expect_failure"])
        repaired = row["minimal_repair"]["request"]
        record = impl(repaired)
        assert record["decision"] in ("retry", "dead"), row["name"]
    for row in CASES["rollback"]:
        _rejects(impl, err, row["rejected_request"], row["expect_failure"])
        _ok(impl, row["request"], row["expect_record"])


def check_semantics(impl, err):
    delays = []
    for attempt in range(1, MAX_ATTEMPTS):
        record = impl(_req(attempts=attempt))
        assert record["decision"] == "retry"
        delays.append(record["delay_ms"])
        assert record["retry_at"] == record["delay_ms"]
    assert delays == [1000, 2000, 4000, 8000]
    capped = impl(_req(attempts=4, policy={**POLICY, "max_delay_ms": 5000}))
    assert capped["delay_ms"] == 5000
    dead = impl(_req(attempts=MAX_ATTEMPTS))
    assert (dead["decision"], dead["delay_ms"], dead["retry_at"]) == ("dead", None, None)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        perm = impl(_req(attempts=attempt, failure="permanent"))
        assert (perm["decision"], perm["delay_ms"], perm["retry_at"]) == ("dead", None, None)
    t1, t2 = impl(_req(failure="timeout")), impl(_req(failure="transient"))
    assert {k: v for k, v in t1.items() if k != "decision_id"} == {
        k: v for k, v in t2.items() if k != "decision_id"
    }
    assert t1["decision_id"] != t2["decision_id"]  # the failure is bound into the id
    constant = [
        impl(_req(attempts=a, policy={**POLICY, "multiplier": 1}))["delay_ms"] for a in (1, 2, 3, 4)
    ]
    assert constant == [1000] * 4
    now = MAX_NOW - 1000
    assert impl(_req(now=now))["retry_at"] == MAX_NOW
    with _raises_ctx(err) as caught:
        impl(_req(now=now + 1))
    assert caught.value.failure_class == "clock_overflow"
    # an exhausted job never overflows: dead carries no retry_at
    assert impl(_req(now=MAX_NOW, attempts=MAX_ATTEMPTS))["decision"] == "dead"
    first, second = impl(_req(now=7)), impl(_req(now=7))
    assert first == second and first is not second


def check_precedence_and_purity(impl, err):
    # shape before policy: a bad request with a bad policy is the request failure
    _rejects(impl, err, _req(attempts=0, policy={"base_delay_ms": 0}), "malformed_retry_request")
    _rejects(impl, err, _req(policy={**POLICY, "max_delay_ms": 999}), "invalid_retry_policy")
    # policy before clock: a bad policy never reaches the overflow test
    _rejects(
        impl, err, _req(now=MAX_NOW, policy={**POLICY, "max_delay_ms": 1}), "invalid_retry_policy"
    )
    request = _req()
    record = impl(request)
    record["job_id"] = "mutated"
    request["policy"]["base_delay_ms"] = 7
    assert impl(_req())["job_id"] == JOB


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


HOSTILE = (
    None,
    True,
    False,
    -1,
    2**70,
    1.0,
    float("nan"),
    "",
    "x",
    b"b",
    [],
    (1,),
    {},
    Boom,
)


def check_totality(impl, err):
    for field in ("op", "job_id", "attempts", "failure", "now", "policy"):
        for bad in HOSTILE:
            request = _req()
            request[field] = bad
            if request == _req():
                continue
            try:
                impl(request)
            except err as exc:
                assert exc.failure_class in ("malformed_retry_request", "invalid_retry_policy")
            else:
                raise AssertionError((field, bad))
    for field in POLICY:
        for bad in HOSTILE:
            request = _req()
            request["policy"][field] = bad
            try:
                impl(request)
            except err as exc:
                assert exc.failure_class == "invalid_retry_policy"
            else:
                raise AssertionError((field, bad))
    for bad in (Boom(), BoomDict(), BoomStr("decide"), [_req()], (_req(),), "decide"):
        try:
            impl(bad)
        except err as exc:
            assert exc.failure_class == "malformed_retry_request"
        else:
            raise AssertionError(bad)
    for forged in (BoomDict(_req()), _req(job_id=BoomStr(JOB)), _req(failure=BoomStr("timeout"))):
        try:
            impl(forged)
        except err:
            pass
        except AssertionError as exc:
            raise Touched("caller code ran: " + str(exc)) from None
        else:
            raise Touched("subclass accepted")
    for bad in (JOB + "\n", "job1:" + "A" * 64, "job1:" + "a" * 63, "job1:" + "\u0661" * 64):
        _rejects(impl, err, _req(job_id=bad), "malformed_retry_request")
    for bad in (True, 1.0, 2**64):
        _rejects(impl, err, _req(attempts=bad), "malformed_retry_request")
    _rejects(impl, err, {**_req(), "extra": 1}, "malformed_retry_request")
    missing = _req()
    del missing["now"]
    _rejects(impl, err, missing, "malformed_retry_request")


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
    # exact str keys only: a valid request whose keys are a str subclass is refused
    _rejects(impl, err, _inert(_req()), "malformed_retry_request")
    _rejects(impl, err, _req(policy=_inert(POLICY)), "invalid_retry_policy")


CHECKS = (
    check_fixture,
    check_semantics,
    check_precedence_and_purity,
    check_totality,
    check_inert_keys,
)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_bound_implementation_passes(check):
    check(_guard(decide, RetryError), RetryError)


def test_fixture_is_closed_over_every_failure_class_and_decision():
    failures = {r["expect_failure"] for r in CASES["malformed"]}
    assert failures == {"malformed_retry_request", "invalid_retry_policy", "clock_overflow"}
    decisions = {
        rec["decision"]
        for s in ("happy", "boundary")
        for r in CASES[s]
        for rec in r["expect_records"]
    }
    assert decisions == {"retry", "dead"}


# ---- mutants: the battery must be RED on each ------------------------------------------


def _wrap(fn):
    def impl(request):
        return fn(request, decide)

    return impl


def _redo_id(record, request):
    record = dict(record)
    record["decision_id"] = _independent_id(request, record)
    return record


def _m_no_cap(request, real):
    out = real(request)
    if out["delay_ms"] is not None:
        p = request["policy"]
        out["delay_ms"] = p["base_delay_ms"] * p["multiplier"] ** (request["attempts"] - 1)
        out["retry_at"] = request["now"] + out["delay_ms"]
    return out


def _m_dead_one_late(request, real):
    if request["attempts"] == MAX_ATTEMPTS and request["failure"] != "permanent":
        return real({**request, "attempts": MAX_ATTEMPTS - 1})
    return real(request)


def _m_permanent_retries(request, real):
    if request["failure"] == "permanent" and request["attempts"] < MAX_ATTEMPTS:
        return real({**request, "failure": "transient"})
    return real(request)


def _m_timeout_dead(request, real):
    if request["failure"] == "timeout":
        return real({**request, "failure": "permanent"})
    return real(request)


def _m_clamp_clock(request, real):
    try:
        return real(request)
    except RetryError as exc:
        if exc.failure_class != "clock_overflow":
            raise
        out = real({**request, "now": 0})
        out["retry_at"] = MAX_NOW
        return out


def _m_jitter(request, real):
    out = real(request)
    if out["delay_ms"] is not None and request["attempts"] == 3:
        out["delay_ms"] += 1
        out["retry_at"] += 1
    return out


def _m_accepts_bool_attempts(request, real):
    if type(request.get("attempts")) is bool:
        return real({**request, "attempts": int(request["attempts"]) or 1})
    return real(request)


def _m_policy_before_shape(request, real):
    try:
        return real(request)
    except RetryError as exc:
        if exc.failure_class == "malformed_retry_request" and isinstance(request, dict):
            policy = request.get("policy")
            if isinstance(policy, dict) and policy.get("base_delay_ms") == 0:
                raise RetryError("invalid_retry_policy") from None
        raise


def _m_raw_exception(request, real):
    if isinstance(request, dict) and request.get("now") is None:
        raise KeyError("now")
    return real(request)


def _m_id_ignores_failure(request, real):
    out = real(request)
    if request["failure"] == "timeout":
        out = _redo_id(out, {**request, "failure": "transient"})
    return out


def _m_refuses_valid(request, real):
    if isinstance(request, dict) and request.get("attempts") == 2:
        raise RetryError("invalid_retry_policy")
    return real(request)


def _m_refuses_everything(request, real):
    raise RetryError("invalid_retry_policy")


MUTANTS = {
    "refuses_valid": (_m_refuses_valid, "check_semantics"),
    "refuses_everything": (_m_refuses_everything, "check_fixture"),
    "no_cap": (_m_no_cap, "check_fixture"),
    "dead_one_late": (_m_dead_one_late, "check_semantics"),
    "permanent_retries": (_m_permanent_retries, "check_semantics"),
    "timeout_dead": (_m_timeout_dead, "check_semantics"),
    "clamp_clock": (_m_clamp_clock, "check_semantics"),
    "jitter": (_m_jitter, "check_semantics"),
    "bool_attempts": (_m_accepts_bool_attempts, "check_totality"),
    "policy_before_shape": (_m_policy_before_shape, "check_precedence_and_purity"),
    "id_ignores_failure": (_m_id_ignores_failure, "check_semantics"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_target(name):
    mutate, target = MUTANTS[name]
    check = next(c for c in CHECKS if c.__name__ == target)
    try:
        check(_guard(_wrap(mutate), RetryError, decide), RetryError)
    except RawEscape:
        raise AssertionError(f"mutant {name} crashed, not refuted") from None
    except RetryError:
        raise AssertionError(f"mutant {name} died of a typed error, not an assertion") from None
    except AssertionError:
        pass
    else:
        raise AssertionError(f"mutant {name} survived {target}")


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_guard_with_oracle_is_identity_on_the_reference(check):
    check(_guard(decide, RetryError, decide), RetryError)


def test_raw_exception_is_a_totality_failure_not_a_mutant_kill():
    """A crash is detected by the totality check as RawEscape; it is not
    counted among the semantic mutant kills above."""
    with pytest.raises(RawEscape):
        check_totality(_guard(_wrap(_m_raw_exception), RetryError), RetryError)


def test_result_shares_no_container_with_later_results():
    first = decide(_req())
    second = decide(_req())
    assert first == second and first is not second
