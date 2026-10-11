"""T0342: unit and seeded property checks of the production limit decision.

An independent model (token bucket arithmetic and a hand-built `lm1:` digest,
no shared code with server.jobs_limit) is driven with seeded random request
chains, valid and defective. Every call must agree with the model on the
record or the failure class, must leave the request (including the previous
record) byte-identical, and must return a result that shares no container
with it. Rate properties that do not use the model are checked as well: the
admitted cost over any window never exceeds capacity plus the refill earned,
and a deny's retry_after_ms is exact (admitted at now + retry, denied one
millisecond earlier). Seeded, so a failure replays.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re

import pytest

from server.jobs_limit import LimitError, admit

MAX = 2**53 - 1
MAX_CAP = 1_000_000
MAX_REFILL = 86_400_000
POLICY = {"capacity": 3, "refill_ms": 1000}
CODES = {
    "malformed_limit_request": "malformed_request",
    "invalid_limit_policy": "invalid_policy",
    "corrupt_previous_bucket": "corrupt_record",
    "limit_conflict": "limit_conflict",
    "clock_overflow": "clock_overflow",
}
FIELDS = [
    "op",
    "key",
    "capacity",
    "refill_ms",
    "cost",
    "decision",
    "tokens",
    "updated_at",
    "retry_after_ms",
    "previous_id",
]
RECORD_ORDER = FIELDS + ["limit_id"]
KEY_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")
LID_RE = re.compile(r"lm1:[0-9a-f]{64}")
JUNK = (None, True, False, 0, 1, -1, 2**53, 2**64, 1.0, 1.5, float("nan"), "", "x", b"x", [], {})


def req(**kw):
    base = {
        "op": "admit",
        "key": "k-1",
        "cost": 1,
        "now": 0,
        "policy": dict(POLICY),
        "previous": None,
    }
    base.update(kw)
    return base


def _snap(obj):
    return json.dumps(obj, default=repr)


def _lid(body):
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "lm1:" + hashlib.sha256(b"lm1" + bytes([0]) + text.encode()).hexdigest()


def forge(**fields):
    """A well-formed previous admit record (3 capacity, 1000 ms) with its id recomputed."""
    body = {
        "op": "admit",
        "key": "k-1",
        "capacity": 3,
        "refill_ms": 1000,
        "cost": 1,
        "decision": "admit",
        "tokens": 2,
        "updated_at": 100,
        "retry_after_ms": None,
        "previous_id": None,
    }
    body.update(fields)
    body["limit_id"] = _lid({k: body[k] for k in FIELDS})
    return body


def forge_deny(**fields):
    base = dict(
        decision="deny",
        tokens=0,
        cost=1,
        updated_at=100,
        retry_after_ms=1000,
    )
    base.update(fields)
    return forge(**base)


def _int(v, lo, hi):
    return type(v) is int and lo <= v <= hi


def _keys(mapping, names):
    return type(mapping) is dict and set(map(type, mapping)) <= {str} and set(mapping) == set(names)


def _grammar(v, rx):
    return type(v) is str and rx.fullmatch(v) is not None


def _previous_ok(prev):
    if not _keys(prev, RECORD_ORDER):
        return False
    cap, refill, cost, tokens = prev["capacity"], prev["refill_ms"], prev["cost"], prev["tokens"]
    if not (
        type(prev["op"]) is str
        and prev["op"] == "admit"
        and _grammar(prev["key"], KEY_RE)
        and _int(cap, 1, MAX_CAP)
        and _int(refill, 1, MAX_REFILL)
        and _int(cost, 1, cap if type(cap) is int else 1)
        and type(prev["decision"]) is str
        and prev["decision"] in ("admit", "deny")
        and _int(tokens, 0, cap)
        and _int(prev["updated_at"], 0, MAX)
        and (prev["previous_id"] is None or _grammar(prev["previous_id"], LID_RE))
        and _grammar(prev["limit_id"], LID_RE)
    ):
        return False
    retry = prev["retry_after_ms"]
    if prev["decision"] == "admit":
        if retry is not None or tokens > cap - cost:
            return False
    else:
        if not (
            _int(retry, 1, MAX)
            and tokens < cost
            and (cost - tokens - 1) * refill < retry <= (cost - tokens) * refill
            and prev["updated_at"] + retry <= MAX
        ):
            return False
    return prev["limit_id"] == _lid({k: prev[k] for k in FIELDS})


def model(request):
    if not _keys(request, ["op", "key", "cost", "now", "policy", "previous"]):
        return ("fail", "malformed_limit_request")
    if not (
        type(request["op"]) is str
        and request["op"] == "admit"
        and _grammar(request["key"], KEY_RE)
        and _int(request["cost"], 1, MAX_CAP)
        and _int(request["now"], 0, MAX)
        and type(request["policy"]) is dict
        and (request["previous"] is None or type(request["previous"]) is dict)
    ):
        return ("fail", "malformed_limit_request")
    pol = request["policy"]
    if not (
        _keys(pol, ["capacity", "refill_ms"])
        and _int(pol["capacity"], 1, MAX_CAP)
        and _int(pol["refill_ms"], 1, MAX_REFILL)
    ):
        return ("fail", "invalid_limit_policy")
    cap, refill, cost, now = pol["capacity"], pol["refill_ms"], request["cost"], request["now"]
    if cost > cap:
        return ("fail", "malformed_limit_request")
    prev = request["previous"]
    if prev is None:
        tokens, base = cap, now
    else:
        if not _previous_ok(prev):
            return ("fail", "corrupt_previous_bucket")
        if not (
            request["key"] == prev["key"]
            and cap == prev["capacity"]
            and refill == prev["refill_ms"]
            and now >= prev["updated_at"]
        ):
            return ("fail", "limit_conflict")
        gained = (now - prev["updated_at"]) // refill
        if prev["tokens"] + gained >= cap:
            tokens, base = cap, now
        else:
            tokens, base = prev["tokens"] + gained, prev["updated_at"] + gained * refill
    if tokens >= cost:
        decision, left, retry = "admit", tokens - cost, None
    else:
        decision, left = "deny", tokens
        retry = (cost - tokens) * refill - (now - base)
        if now + retry > MAX:
            return ("fail", "clock_overflow")
    record = {
        "op": "admit",
        "key": request["key"],
        "capacity": cap,
        "refill_ms": refill,
        "cost": cost,
        "decision": decision,
        "tokens": left,
        "updated_at": base,
        "retry_after_ms": retry,
        "previous_id": None if prev is None else prev["limit_id"],
    }
    record["limit_id"] = _lid(record)
    return ("ok", record)


def run(request):
    before = _snap(request)
    try:
        out = ("ok", admit(request))
    except LimitError as error:
        assert type(error) is LimitError
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


def test_fresh_bucket_is_full_and_admits_with_exact_shape():
    record = check(req(cost=2, now=5))[1]
    assert list(record) == RECORD_ORDER
    assert (record["decision"], record["tokens"], record["updated_at"]) == ("admit", 1, 5)
    assert record["retry_after_ms"] is None and record["previous_id"] is None
    assert all(type(record[k]) is int for k in ("capacity", "refill_ms", "cost", "tokens"))


def test_drain_then_deny_with_exact_retry_then_refill():
    prev, seen = None, []
    for now in (0, 0, 0, 0):
        out = check(req(now=now, previous=prev))[1]
        seen.append((out["decision"], out["tokens"], out["retry_after_ms"]))
        prev = out
    assert seen == [("admit", 2, None), ("admit", 1, None), ("admit", 0, None), ("deny", 0, 1000)]
    assert check(req(now=999, previous=prev))[1]["decision"] == "deny"
    assert check(req(now=999, previous=prev))[1]["retry_after_ms"] == 1
    again = check(req(now=1000, previous=prev))[1]
    assert (again["decision"], again["tokens"], again["updated_at"]) == ("admit", 0, 1000)


def test_partial_refill_is_carried_below_capacity():
    drained = forge(tokens=0, updated_at=100)
    out = check(req(now=2650, previous=drained, cost=1))[1]
    assert out["updated_at"] == 100 + 2 * 1000 and out["tokens"] == 1  # 2 gained, 1 spent
    out = check(req(now=1100, previous=drained, cost=1))[1]
    assert out["updated_at"] == 1100 and out["tokens"] == 0


def test_at_capacity_no_partial_refill_is_banked():
    full = forge(tokens=3, cost=1, updated_at=0)
    full = dict(full, tokens=3)
    full["limit_id"] = _lid({k: full[k] for k in FIELDS})
    assert not _previous_ok(full)  # admit with tokens 3 and cost 1 would be impossible
    near = forge(tokens=2, updated_at=100)
    out = check(req(now=100 + 1999, previous=near))[1]
    assert out["updated_at"] == 100 + 1999 and out["tokens"] == 2


def test_deny_does_not_consume_tokens_and_is_chained():
    prev = check(req(cost=3, now=0))[1]
    deny = check(req(cost=1, now=10, previous=prev))[1]
    assert deny["decision"] == "deny" and deny["tokens"] == 0
    assert deny["retry_after_ms"] == 990 and deny["previous_id"] == prev["limit_id"]


def test_limit_id_binds_every_field_and_decisions_are_deterministic():
    request = req(cost=2, now=7)
    assert run(request) == run(copy.deepcopy(request))
    base = check(request)[1]["limit_id"]
    variants = [
        req(cost=1, now=7),
        req(cost=2, now=8),
        req(cost=2, now=7, key="k-2"),
        req(cost=2, now=7, policy={"capacity": 4, "refill_ms": 1000}),
        req(cost=2, now=7, policy={"capacity": 3, "refill_ms": 1001}),
        req(cost=2, now=107, previous=forge()),
    ]
    ids = {check(v)[1]["limit_id"] for v in variants}
    assert base not in ids and len(ids) == len(variants)


def test_result_shares_no_container_with_the_request():
    previous = forge()
    request = req(now=150, previous=previous)
    one, two = admit(request), admit(request)
    assert one == two and one is not two
    one["key"] = "zzz"
    assert admit(request) == two


# ---------- unit: boundaries ----------


def test_policy_and_cost_bounds_are_exact():
    assert check(req(policy={"capacity": 1, "refill_ms": 1}))[0] == "ok"
    assert (
        check(req(cost=MAX_CAP, policy={"capacity": MAX_CAP, "refill_ms": MAX_REFILL}))[0] == "ok"
    )
    for policy in (
        {"capacity": 0, "refill_ms": 1},
        {"capacity": MAX_CAP + 1, "refill_ms": 1},
        {"capacity": 1, "refill_ms": 0},
        {"capacity": 1, "refill_ms": MAX_REFILL + 1},
    ):
        assert check(req(policy=policy)) == ("fail", "invalid_limit_policy")
    assert check(req(cost=0)) == ("fail", "malformed_limit_request")
    assert check(req(cost=MAX_CAP + 1)) == ("fail", "malformed_limit_request")
    assert check(req(cost=3))[0] == "ok"
    assert check(req(cost=4)) == ("fail", "malformed_limit_request")  # above capacity, never a deny
    assert check(req(now=MAX))[0] == "ok" and check(req(now=MAX + 1))[0] == "fail"
    assert check(req(now=-1)) == ("fail", "malformed_limit_request")


def test_key_grammar_is_exact():
    for key in ("a", "A.b_c:d-9", "x" * 64):
        assert check(req(key=key))[0] == "ok"
    for key in ("", "x" * 65, "a b", "a/b", "é", "a\n", "\u0661"):
        assert check(req(key=key)) == ("fail", "malformed_limit_request")


def test_clock_overflow_boundary_is_exact_and_never_clamped():
    drained = forge(cost=3, tokens=0, updated_at=MAX - 1000)
    out = check(req(cost=1, now=MAX - 1000, previous=drained))[1]
    assert (out["decision"], out["retry_after_ms"]) == ("deny", 1000)
    for now in (MAX - 999, MAX):
        late = forge(cost=3, tokens=0, updated_at=now)
        assert check(req(cost=1, now=now, previous=late)) == ("fail", "clock_overflow")
    # an admit has no retry_after_ms, so the clock cannot overflow
    assert check(req(cost=1, now=MAX))[0] == "ok"
    part = forge(cost=3, tokens=0, updated_at=MAX - 1500)
    out = check(req(cost=1, now=MAX - 1000, previous=part))[1]
    assert (out["decision"], out["retry_after_ms"]) == ("deny", 500)


def test_equal_now_is_accepted_and_regression_conflicts():
    prev = forge()
    assert check(req(now=100, previous=prev))[0] == "ok"
    assert check(req(now=99, previous=prev)) == ("fail", "limit_conflict")


# ---------- unit: malformed ----------


@pytest.mark.parametrize("field", ["op", "key", "cost", "now", "policy", "previous"])
def test_every_request_field_rejects_hostile_values(field):
    for value in JUNK:
        got = check(req(**{field: value}))
        if got[0] == "fail":
            if field == "policy" and type(value) is dict:
                expected = "invalid_limit_policy"
            elif field == "previous" and type(value) is dict:
                expected = "corrupt_previous_bucket"
            else:
                expected = "malformed_limit_request"
            assert got == ("fail", expected), (field, value)


@pytest.mark.parametrize("field", ["capacity", "refill_ms"])
def test_every_policy_field_rejects_hostile_values(field):
    for value in JUNK:
        got = check(req(policy=dict(POLICY, **{field: value})))
        if got[0] == "fail":
            assert got == ("fail", "invalid_limit_policy"), (field, value)


class _Str(str):
    pass


class _Int(int):
    pass


class _Dict(dict):
    pass


def test_subclasses_of_exact_types_are_refused():
    for field, value in [
        ("key", _Str("k-1")),
        ("op", _Str("admit")),
        ("cost", _Int(1)),
        ("now", _Int(1)),
        ("policy", _Dict(POLICY)),
        ("previous", _Dict(forge())),
    ]:
        assert run(req(**{field: value}))[0] == "fail", field
    assert check(req(policy=dict(POLICY, capacity=_Int(3)))) == ("fail", "invalid_limit_policy")
    assert check(req(policy=dict(POLICY, refill_ms=True))) == ("fail", "invalid_limit_policy")
    assert check(req(cost=True)) == ("fail", "malformed_limit_request")


def test_inert_str_subclass_keys_are_refused_at_every_level():
    request = {(_Str("op") if k == "op" else k): v for k, v in req().items()}
    assert check(request) == ("fail", "malformed_limit_request")
    for key in POLICY:
        policy = {(_Str(k) if k == key else k): v for k, v in POLICY.items()}
        assert check(req(policy=policy)) == ("fail", "invalid_limit_policy")
    for key in RECORD_ORDER:
        prev = {(_Str(k) if k == key else k): v for k, v in forge().items()}
        assert check(req(now=150, previous=prev)) == ("fail", "corrupt_previous_bucket"), key


def test_missing_extra_and_non_str_keys_are_refused():
    for key in req():
        assert check({k: v for k, v in req().items() if k != key})[0] == "fail"
    assert check(dict(req(), extra=1)) == ("fail", "malformed_limit_request")
    assert check({**req(), 1: 2}) == ("fail", "malformed_limit_request")
    assert check(req(policy={"capacity": 3})) == ("fail", "invalid_limit_policy")
    assert check(req(policy={**POLICY, "burst": 1})) == ("fail", "invalid_limit_policy")
    assert check(req(policy={**POLICY, 1: 2})) == ("fail", "invalid_limit_policy")


@pytest.mark.parametrize("junk", [None, [], (), "admit", 5, object(), set()])
def test_non_dict_requests_are_malformed(junk):
    assert run(junk) == ("fail", "malformed_limit_request")


# ---------- unit: corrupt previous (one defect at a time) ----------


def test_previous_field_defects_are_corrupt_with_a_valid_digest():
    for field in FIELDS:
        for value in JUNK + ("lm1:" + "e" * 64, "report2", 10**7, MAX + 1):
            if type(value) is bytes:
                continue
            prev = forge(**{field: value})
            if prev == forge():
                continue
            got = check(req(now=150, previous=prev))
            if got[0] == "fail":
                assert got[1] in ("corrupt_previous_bucket", "limit_conflict"), (field, value)


def test_previous_admit_and_deny_invariants_are_exact():
    assert check(req(now=150, previous=forge(tokens=2, cost=1)))[0] == "ok"
    assert check(req(now=150, previous=forge(tokens=3, cost=1))) == (
        "fail",
        "corrupt_previous_bucket",
    )
    assert check(req(now=150, previous=forge(retry_after_ms=5))) == (
        "fail",
        "corrupt_previous_bucket",
    )
    ok = forge_deny(tokens=0, cost=1, retry_after_ms=1000)
    assert check(req(now=150, previous=ok))[0] == "ok"
    assert check(req(now=150, previous=forge_deny(retry_after_ms=1)))[0] == "ok"
    for bad in (
        forge_deny(retry_after_ms=0),
        forge_deny(retry_after_ms=1001),
        forge_deny(retry_after_ms=None),
        forge_deny(tokens=1, cost=1, retry_after_ms=1),
        forge_deny(tokens=0, cost=2, retry_after_ms=1000),
        forge_deny(tokens=0, cost=2, retry_after_ms=2001),
        forge_deny(updated_at=MAX, retry_after_ms=1),
    ):
        assert check(req(now=MAX, previous=bad, cost=1)) == ("fail", "corrupt_previous_bucket"), bad
    assert (
        check(req(now=150, previous=forge_deny(tokens=0, cost=2, retry_after_ms=1001)))[0] == "ok"
    )
    assert (
        check(req(now=150, previous=forge_deny(tokens=0, cost=2, retry_after_ms=2000)))[0] == "ok"
    )


def test_previous_digest_must_recompute():
    prev = forge()
    for bad in (
        "lm1:" + "0" * 64,
        prev["limit_id"][:-1] + ("0" if prev["limit_id"][-1] != "0" else "1"),
        "lm2:" + prev["limit_id"][4:],
        prev["limit_id"].upper(),
        None,
    ):
        assert check(req(now=150, previous=dict(prev, limit_id=bad))) == (
            "fail",
            "corrupt_previous_bucket",
        )
    assert check(req(now=150, previous=dict(prev, tokens=1))) == ("fail", "corrupt_previous_bucket")


def test_previous_missing_or_extra_keys_are_corrupt():
    prev = forge()
    for key in prev:
        assert check(req(now=150, previous={k: v for k, v in prev.items() if k != key})) == (
            "fail",
            "corrupt_previous_bucket",
        )
    assert check(req(now=150, previous=dict(prev, extra=1))) == ("fail", "corrupt_previous_bucket")
    assert check(req(now=150, previous={})) == ("fail", "corrupt_previous_bucket")


# ---------- unit: chain conflicts and precedence ----------


def test_chain_requires_same_key_capacity_and_refill():
    prev = forge()
    assert check(req(now=150, key="k-2", previous=prev)) == ("fail", "limit_conflict")
    assert check(req(now=150, policy={"capacity": 4, "refill_ms": 1000}, previous=prev)) == (
        "fail",
        "limit_conflict",
    )
    assert check(req(now=150, policy={"capacity": 3, "refill_ms": 999}, previous=prev)) == (
        "fail",
        "limit_conflict",
    )


def test_precedence_request_policy_cost_previous_chain_clock():
    bad_prev = dict(forge(), limit_id="lm1:" + "0" * 64)
    assert check(req(cost=0, policy=None, previous=bad_prev)) == ("fail", "malformed_limit_request")
    assert check(req(policy={"capacity": 0, "refill_ms": 1}, previous=bad_prev)) == (
        "fail",
        "invalid_limit_policy",
    )
    assert check(req(cost=4, previous=bad_prev)) == ("fail", "malformed_limit_request")
    assert check(req(now=0, previous=bad_prev, key="other")) == ("fail", "corrupt_previous_bucket")
    assert check(req(now=0, previous=forge(), key="other")) == ("fail", "limit_conflict")


def test_rejected_calls_change_nothing_and_the_valid_follow_up_decides():
    previous = forge()
    request = req(now=50, previous=previous)
    before = copy.deepcopy(request)
    assert run(request) == ("fail", "limit_conflict")
    assert request == before
    assert run(req(now=150, previous=previous))[0] == "ok"


# ---------- seeded properties ----------


def _random_policy(rng):
    return {
        "capacity": rng.choice([1, 2, 3, 10, 1000, MAX_CAP]),
        "refill_ms": rng.choice([1, 2, 1000, 86_400_000, rng.randint(1, 5000)]),
    }


def _damage(rng, request):
    request = copy.deepcopy(request)
    target = rng.choice(["op", "key", "cost", "now", "policy", "p", "previous", "k", "prev"])
    if target == "k":
        request[rng.choice(["x", "op"])] = 1
    elif target == "p":
        request["policy"][rng.choice(["capacity", "refill_ms"])] = rng.choice(JUNK)
    elif target == "prev":
        if request["previous"]:
            request["previous"][rng.choice(RECORD_ORDER)] = rng.choice(JUNK)
    else:
        request[target] = rng.choice(JUNK)
    return request


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_chains(seed):
    rng = random.Random(seed)
    policy, prev, now = _random_policy(rng), None, 0
    for _ in range(60):
        now += rng.choice([0, 0, 1, rng.randint(0, 3 * policy["refill_ms"])])
        request = req(
            cost=rng.randint(1, min(policy["capacity"], 4)),
            now=now,
            policy=dict(policy),
            previous=prev,
        )
        if rng.random() < 0.3:
            check(_damage(rng, request))
        out = check(request)
        if out[0] == "ok":
            prev = out[1]
        if rng.random() < 0.05:
            policy, prev = _random_policy(rng), None


@pytest.mark.parametrize("seed", range(30))
def test_admitted_cost_never_exceeds_capacity_plus_refill_earned(seed):
    rng = random.Random(1000 + seed)
    policy = {"capacity": rng.randint(1, 8), "refill_ms": rng.randint(1, 50)}
    prev, now, start, spent = None, 0, 0, 0
    for _ in range(300):
        now += rng.randint(0, 2 * policy["refill_ms"])
        cost = rng.randint(1, policy["capacity"])
        record = admit(req(cost=cost, now=now, policy=dict(policy), previous=prev))
        prev = record
        assert 0 <= record["tokens"] <= policy["capacity"]
        if record["decision"] == "admit":
            spent += cost
        assert spent <= policy["capacity"] + (now - start) // policy["refill_ms"]


@pytest.mark.parametrize("seed", range(30))
def test_deny_retry_after_is_exact(seed):
    rng = random.Random(2000 + seed)
    policy = {"capacity": rng.randint(1, 6), "refill_ms": rng.randint(1, 500)}
    prev, now = None, 0
    for _ in range(80):
        now += rng.randint(0, policy["refill_ms"])
        cost = rng.randint(1, policy["capacity"])
        record = admit(req(cost=cost, now=now, policy=dict(policy), previous=prev))
        if record["decision"] == "deny":
            retry = record["retry_after_ms"]
            later = admit(req(cost=cost, now=now + retry, policy=dict(policy), previous=record))
            assert later["decision"] == "admit"
            if retry > 1:
                sooner = admit(
                    req(cost=cost, now=now + retry - 1, policy=dict(policy), previous=record)
                )
                assert sooner["decision"] == "deny"
        prev = record


@pytest.mark.parametrize("seed", range(10))
def test_replay_is_deterministic_and_ids_are_unique_per_distinct_request(seed):
    rng = random.Random(3000 + seed)
    policy, prev, now, seen = _random_policy(rng), None, 0, {}
    for _ in range(150):
        now += rng.randint(0, policy["refill_ms"])
        request = req(
            cost=rng.randint(1, min(policy["capacity"], 3)),
            now=now,
            policy=dict(policy),
            previous=prev,
        )
        out = run(request)
        assert out == run(copy.deepcopy(request))
        if out[0] == "ok":
            key = _snap(request)
            assert seen.setdefault(out[1]["limit_id"], key) == key
            prev = out[1]


def test_forged_deny_retry_sum_boundary_is_inclusive():
    ok_prev = forge_deny(updated_at=MAX - 1000, retry_after_ms=1000)
    assert check(req(now=MAX - 1000, previous=ok_prev))[0] == "ok"
    bad_prev = forge_deny(updated_at=MAX - 999, retry_after_ms=1000)
    assert check(req(now=MAX - 999, previous=bad_prev))[0] == "fail"
