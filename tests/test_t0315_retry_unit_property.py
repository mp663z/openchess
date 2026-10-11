"""T0315: unit and seeded property checks of the production retry decision.

An independent model (a few lines of integer arithmetic and a hand-built
digest, no shared code with server.jobs_retry) is driven with seeded random
requests, valid and defective. Every call must agree with the model on the
record or the failure class, must leave the request byte-identical, and must
return a result that shares no container with it. Seeded, so a failure
replays. Unit rows pin every bound at both sides, the precedence between
failure classes, and hostile values (bool, float, str and int subclasses,
inert str-subclass keys) at every request and policy field.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random

import pytest

from server.jobs_retry import RetryError, decide

JOB = "job1:" + "ab" * 32
MAX_NOW = 2**53 - 1
MAX_ATTEMPTS = 5
POLICY = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 30000}
RECORD_ORDER = ["op", "job_id", "decision", "attempts", "delay_ms", "retry_at", "decision_id"]
CODES = {
    "malformed_retry_request": "malformed_request",
    "invalid_retry_policy": "invalid_policy",
    "clock_overflow": "clock_overflow",
}


def req(**kw):
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


def _snap(obj):
    return json.dumps(obj, default=repr)


def _model_id(request, record):
    rec = {k: v for k, v in record.items() if k != "decision_id"}
    body = json.dumps({"request": request, "record": rec}, sort_keys=True, separators=(",", ":"))
    return "rd1:" + hashlib.sha256(b"rd1" + bytes([0]) + body.encode()).hexdigest()


def model(request):
    """('ok', record) or ('fail', class). Written from the contract text."""
    if not isinstance(request, dict) or type(request) is not dict:
        return ("fail", "malformed_retry_request")
    if set(map(type, request)) != {str} or set(request) != {
        "op",
        "job_id",
        "attempts",
        "failure",
        "now",
        "policy",
    }:
        return ("fail", "malformed_retry_request")

    def exact_int(v, lo, hi):
        return type(v) is int and lo <= v <= hi

    job = request["job_id"]
    job_ok = (
        type(job) is str
        and len(job) == 69
        and job.startswith("job1:")
        and all(c in "0123456789abcdef" for c in job[5:])
    )
    if not (
        type(request["op"]) is str
        and request["op"] == "decide"
        and job_ok
        and exact_int(request["attempts"], 1, MAX_ATTEMPTS)
        and type(request["failure"]) is str
        and request["failure"] in ("transient", "timeout", "permanent")
        and exact_int(request["now"], 0, MAX_NOW)
        and type(request["policy"]) is dict
    ):
        return ("fail", "malformed_retry_request")
    pol = request["policy"]
    if set(map(type, pol)) != {str} or set(pol) != {"base_delay_ms", "multiplier", "max_delay_ms"}:
        return ("fail", "invalid_retry_policy")
    base, mult, cap = pol["base_delay_ms"], pol["multiplier"], pol["max_delay_ms"]
    if not (
        exact_int(base, 1, 3_600_000)
        and exact_int(mult, 1, 10)
        and exact_int(cap, base if type(base) is int else 1, 86_400_000)
    ):
        return ("fail", "invalid_retry_policy")
    attempts, now = request["attempts"], request["now"]
    if request["failure"] == "permanent" or attempts == MAX_ATTEMPTS:
        decision, delay, at = "dead", None, None
    else:
        delay = base
        for _ in range(attempts - 1):
            delay *= mult
        delay = min(delay, cap)
        at = now + delay
        if at > MAX_NOW:
            return ("fail", "clock_overflow")
        decision = "retry"
    record = {
        "op": "decide",
        "job_id": job,
        "decision": decision,
        "attempts": attempts,
        "delay_ms": delay,
        "retry_at": at,
    }
    record["decision_id"] = _model_id(request, record)
    return ("ok", record)


def run(request):
    before = _snap(request)
    try:
        out = ("ok", decide(request))
    except RetryError as error:
        assert type(error) is RetryError
        assert error.__cause__ is None and error.__context__ is None
        assert error.code == CODES[error.failure_class]
        assert error.retryable is False
        out = ("fail", error.failure_class)
    assert _snap(request) == before, "request was changed"
    return out


def check(request):
    got, want = run(request), model(request)
    assert got == want, (request, got, want)
    return got


# ---------- unit: happy ----------


def test_backoff_chain_and_exhaustion():
    got = [check(req(attempts=a, now=100))[1] for a in range(1, 6)]
    assert [r["delay_ms"] for r in got] == [1000, 2000, 4000, 8000, None]
    assert [r["retry_at"] for r in got] == [1100, 2100, 4100, 8100, None]
    assert [r["decision"] for r in got] == ["retry"] * 4 + ["dead"]


def test_record_is_exact_and_ordered():
    record = check(req())[1]
    assert list(record) == RECORD_ORDER
    assert type(record["delay_ms"]) is int and type(record["retry_at"]) is int
    assert type(record["attempts"]) is int


def test_cap_applies_exactly_at_the_crossover():
    policy = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 4000}
    delays = [check(req(attempts=a, policy=policy))[1]["delay_ms"] for a in range(1, 5)]
    assert delays == [1000, 2000, 4000, 4000]
    policy = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 3999}
    assert check(req(attempts=3, policy=policy))[1]["delay_ms"] == 3999
    policy = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 4001}
    assert check(req(attempts=3, policy=policy))[1]["delay_ms"] == 4000


def test_multiplier_one_is_constant_and_cap_equal_base_is_allowed():
    policy = {"base_delay_ms": 7, "multiplier": 1, "max_delay_ms": 7}
    assert {check(req(attempts=a, policy=policy))[1]["delay_ms"] for a in range(1, 5)} == {7}


def test_permanent_is_dead_at_every_attempt_and_ignores_the_clock():
    for attempts in range(1, 6):
        record = check(req(attempts=attempts, failure="permanent", now=MAX_NOW))[1]
        assert record["decision"] == "dead"
        assert record["delay_ms"] is None and record["retry_at"] is None


def test_timeout_decides_like_transient_apart_from_the_bound_failure_name():
    for attempts in range(1, 6):
        a = check(req(attempts=attempts, failure="timeout"))[1]
        b = check(req(attempts=attempts, failure="transient"))[1]
        assert {k: v for k, v in a.items() if k != "decision_id"} == {
            k: v for k, v in b.items() if k != "decision_id"
        }
        assert a["decision_id"] != b["decision_id"]


def test_no_jitter_and_determinism():
    first = [run(req(attempts=3, now=5)) for _ in range(5)]
    assert all(x == first[0] for x in first)


def test_result_shares_no_container_with_the_request_or_other_results():
    request = req()
    one = decide(request)
    two = decide(request)
    assert one == two and one is not two
    one["job_id"] = "x"
    assert decide(request) == two


def test_decision_id_binds_every_request_field():
    base = decide(req(attempts=2, now=7))
    variants = [
        req(attempts=3, now=7),
        req(attempts=2, now=8),
        req(attempts=2, now=7, failure="timeout"),
        req(attempts=2, now=7, job_id="job1:" + "cd" * 32),
        req(attempts=2, now=7, policy=dict(POLICY, base_delay_ms=1001)),
        req(attempts=2, now=7, policy=dict(POLICY, multiplier=3)),
        req(attempts=2, now=7, policy=dict(POLICY, max_delay_ms=30001)),
    ]
    ids = {decide(v)["decision_id"] for v in variants}
    assert base["decision_id"] not in ids and len(ids) == len(variants)


# ---------- unit: boundaries ----------


@pytest.mark.parametrize(
    "field,good,bad",
    [
        ("attempts", [1, 5], [0, 6, -1]),
        ("now", [0, MAX_NOW - 30000], [-1, MAX_NOW + 1]),
    ],
)
def test_request_int_bounds_are_exact(field, good, bad):
    for value in good:
        assert check(req(**{field: value}))[0] == "ok"
    for value in bad:
        assert check(req(**{field: value})) == ("fail", "malformed_retry_request")


@pytest.mark.parametrize(
    "field,good,bad",
    [
        ("base_delay_ms", [1, 3_600_000], [0, 3_600_001, -1]),
        ("multiplier", [1, 10], [0, 11, -1]),
        ("max_delay_ms", [1000, 86_400_000], [999, 86_400_001]),
    ],
)
def test_policy_int_bounds_are_exact(field, good, bad):
    wide = dict(POLICY, max_delay_ms=86_400_000)
    for value in good:
        assert check(req(policy=dict(wide, **{field: value})))[0] == "ok"
    for value in bad:
        assert check(req(policy=dict(wide, **{field: value}))) == (
            "fail",
            "invalid_retry_policy",
        )


def test_clock_boundary_is_exact_and_never_clamped():
    assert check(req(now=MAX_NOW - 1000))[1]["retry_at"] == MAX_NOW
    assert check(req(now=MAX_NOW - 999)) == ("fail", "clock_overflow")
    assert check(req(now=MAX_NOW)) == ("fail", "clock_overflow")
    # dead decisions have no retry_at so the clock cannot overflow
    assert check(req(now=MAX_NOW, attempts=5))[1]["decision"] == "dead"
    assert check(req(now=MAX_NOW, failure="permanent"))[1]["decision"] == "dead"


def test_huge_multiplier_power_is_exact_integer_arithmetic():
    policy = {"base_delay_ms": 3_600_000, "multiplier": 10, "max_delay_ms": 86_400_000}
    assert check(req(attempts=4, policy=policy))[1]["delay_ms"] == 86_400_000


# ---------- unit: malformed / precedence ----------

JUNK = (None, True, False, 0, 1, -1, 2**53, 2**64, 1.0, 1.5, float("nan"), "", "x", b"x", [], {})


@pytest.mark.parametrize("field", ["op", "job_id", "attempts", "failure", "now", "policy"])
def test_every_request_field_rejects_hostile_values(field):
    for value in JUNK:
        got = check(req(**{field: value}))
        if got[0] == "ok":
            continue  # a value that is valid for this field (the model agrees)
        if field == "policy" and type(value) is dict:
            assert got == ("fail", "invalid_retry_policy")
        else:
            assert got == ("fail", "malformed_retry_request"), (field, value)


@pytest.mark.parametrize("field", ["base_delay_ms", "multiplier", "max_delay_ms"])
def test_every_policy_field_rejects_hostile_values(field):
    for value in JUNK:
        got = check(req(policy=dict(POLICY, **{field: value})))
        if got[0] == "fail":
            assert got == ("fail", "invalid_retry_policy"), (field, value)


class _Str(str):
    pass


class _Int(int):
    pass


def test_subclasses_of_exact_types_are_refused():
    assert check(req(job_id=_Str(JOB))) == ("fail", "malformed_retry_request")
    assert check(req(op=_Str("decide"))) == ("fail", "malformed_retry_request")
    assert check(req(failure=_Str("transient"))) == ("fail", "malformed_retry_request")
    assert check(req(attempts=_Int(1))) == ("fail", "malformed_retry_request")
    assert check(req(now=_Int(1))) == ("fail", "malformed_retry_request")
    assert check(req(policy=dict(POLICY, multiplier=_Int(2)))) == ("fail", "invalid_retry_policy")
    assert check(req(policy=dict(POLICY, base_delay_ms=True))) == ("fail", "invalid_retry_policy")


def test_inert_str_subclass_keys_are_refused_at_both_levels():
    inert = _Str("op")
    request = {(inert if k == "op" else k): v for k, v in req().items()}
    assert check(request) == ("fail", "malformed_retry_request")
    for key in POLICY:
        policy = {(_Str(k) if k == key else k): v for k, v in POLICY.items()}
        assert check(req(policy=policy)) == ("fail", "invalid_retry_policy")


def test_missing_extra_and_non_str_keys_are_refused():
    for key in req():
        assert check({k: v for k, v in req().items() if k != key})[0] == "fail"
    assert check(dict(req(), extra=1)) == ("fail", "malformed_retry_request")
    assert check({**req(), 1: 2}) == ("fail", "malformed_retry_request")
    for key in POLICY:
        assert check(req(policy={k: v for k, v in POLICY.items() if k != key}))[0] == "fail"
    assert check(req(policy=dict(POLICY, jitter=1))) == ("fail", "invalid_retry_policy")
    assert check(req(policy={**POLICY, 1: 2})) == ("fail", "invalid_retry_policy")


@pytest.mark.parametrize("junk", [None, [], (), "decide", 5, object(), set()])
def test_non_dict_requests_are_malformed(junk):
    assert run(junk) == ("fail", "malformed_retry_request")


def test_job_id_grammar_is_exact():
    good = "job1:" + "0123456789abcdef" * 4
    assert check(req(job_id=good))[0] == "ok"
    for bad in (
        "job1:" + "A" * 64,
        "job1:" + "a" * 63,
        "job1:" + "a" * 65,
        "job2:" + "a" * 64,
        " job1:" + "a" * 64,
        "job1:" + "a" * 64 + "\n",
        "job1:" + "a" * 63 + "g",
        "job1:" + "\u0661" * 64,
    ):
        assert check(req(job_id=bad)) == ("fail", "malformed_retry_request")


def test_failure_names_are_exact_and_case_sensitive():
    for name in ("transient", "timeout", "permanent"):
        assert check(req(failure=name))[0] == "ok"
    for name in ("Transient", "TIMEOUT", "retry", "", "permanent "):
        assert check(req(failure=name)) == ("fail", "malformed_retry_request")


def test_precedence_request_then_policy_then_clock():
    bad_policy = dict(POLICY, multiplier=0)
    assert check(req(attempts=0, policy=bad_policy)) == ("fail", "malformed_retry_request")
    assert check(req(policy=bad_policy, now=MAX_NOW)) == ("fail", "invalid_retry_policy")
    assert check(req(now=MAX_NOW)) == ("fail", "clock_overflow")
    assert check(req(policy=None, now=MAX_NOW)) == ("fail", "malformed_retry_request")


# ---------- unit: rollback (purity on every refusal) ----------


def test_rejected_requests_change_nothing_and_a_valid_follow_up_decides():
    request = req(now=MAX_NOW)
    before = copy.deepcopy(request)
    assert run(request) == ("fail", "clock_overflow")
    assert request == before
    assert run(req())[0] == "ok"


# ---------- seeded properties ----------


def _random_valid(rng):
    base = rng.choice([1, 2, 999, 1000, 3_600_000, rng.randint(1, 3_600_000)])
    mult = rng.randint(1, 10)
    cap = rng.choice([base, base + 1, 86_400_000, rng.randint(base, 86_400_000)])
    return req(
        attempts=rng.randint(1, 5),
        failure=rng.choice(["transient", "timeout", "permanent"]),
        now=rng.choice([0, 1, rng.randint(0, MAX_NOW), MAX_NOW - rng.randint(0, 100_000)]),
        policy={"base_delay_ms": base, "multiplier": mult, "max_delay_ms": cap},
    )


def _damage(rng, request):
    request = copy.deepcopy(request)
    target = rng.choice(
        ["attempts", "failure", "now", "job_id", "op", "policy", "p.base", "p.mult", "p.max", "key"]
    )
    if target == "key":
        request[rng.choice(["x", "op"])] = 1
    elif target.startswith("p."):
        field = {"p.base": "base_delay_ms", "p.mult": "multiplier", "p.max": "max_delay_ms"}[target]
        request["policy"][field] = rng.choice(JUNK)
    else:
        request[target] = rng.choice(JUNK)
    return request


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_requests(seed):
    rng = random.Random(seed)
    for _ in range(120):
        request = _random_valid(rng)
        if rng.random() < 0.5:
            request = _damage(rng, request)
        check(request)


@pytest.mark.parametrize("seed", range(20))
def test_delay_is_monotone_capped_and_exact_per_attempt(seed):
    rng = random.Random(1000 + seed)
    for _ in range(40):
        request = _random_valid(rng)
        request["failure"] = "transient"
        request["now"] = 0
        policy = request["policy"]
        delays = []
        for attempts in range(1, 5):
            record = decide(dict(request, attempts=attempts))
            delays.append(record["delay_ms"])
            assert record["delay_ms"] <= policy["max_delay_ms"]
            assert record["retry_at"] == record["delay_ms"]
        assert delays == sorted(delays)
        assert decide(dict(request, attempts=5))["decision"] == "dead"


@pytest.mark.parametrize("seed", range(20))
def test_retry_at_is_now_plus_delay_and_overflow_is_the_exact_boundary(seed):
    rng = random.Random(2000 + seed)
    for _ in range(40):
        request = _random_valid(rng)
        request["failure"] = "transient"
        request["attempts"] = rng.randint(1, 4)
        request["now"] = 0
        delay = decide(request)["delay_ms"]
        edge = MAX_NOW - delay
        assert decide(dict(request, now=edge))["retry_at"] == MAX_NOW
        assert run(dict(request, now=edge + 1)) == ("fail", "clock_overflow")


@pytest.mark.parametrize("seed", range(10))
def test_replay_is_deterministic_and_ids_are_unique_per_distinct_request(seed):
    rng = random.Random(3000 + seed)
    seen = {}
    for _ in range(200):
        request = _random_valid(rng)
        out = run(request)
        assert out == run(copy.deepcopy(request))
        if out[0] == "ok":
            key = _snap(request)
            ident = out[1]["decision_id"]
            assert seen.setdefault(ident, key) == key
