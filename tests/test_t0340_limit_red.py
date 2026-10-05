"""T0340 permanent red battery for jobs-limit token-bucket functions.

Drives every row of the T0339 fixture (tests/fixtures/limit/cases.json),
hostile-input totality probes, a seeded differential against an independent
integer model of the bucket, and pinned semantic probes through an
`admit(request)` callable and its `LimitError` class:

- happy/boundary: each request returns the pinned record exactly (exact
  types), the limit_id is re-derived here from the contract preimage, the
  request is untouched and shares no container with the record;
- malformed: each row rejects with the pinned class and mapped code, fresh
  (no __cause__ or __context__), request untouched, and its single-locus
  repair is accepted or reaches a later stage;
- rollback: a rejected request leaves everything untouched and the valid
  follow-up admits as pinned;
- totality: hostile types at every request, policy and previous field fail
  closed with a typed error and never run caller code;
- semantics: fresh full bucket, floor refill, cap drops the remainder,
  partial refill carry, exact retry_after, overflow is an error and is
  never clamped, validation order, chain identity and clock edge.

Standalone-red convention: GREEN against the contract-derived reference in
tests.test_t0338_limit_contract; every mutant below is RED on its pinned
target check. The production task switches the binding by replacing ONLY
the two binding lines with the production admit / LimitError names.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0338_limit_contract as _reference  # noqa: E402
from tools.limit_contract_lint import FAILURE_MAPPING  # noqa: E402

admit = _reference.admit
LimitError = _reference.LimitError

CASES = json.loads((ROOT / "tests" / "fixtures" / "limit" / "cases.json").read_text())
POLICY = {"capacity": 3, "refill_ms": 1000}
MAX = 2**53 - 1
RECORD_FIELDS = (
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
    "limit_id",
)
CLASSES = (
    "malformed_limit_request",
    "invalid_limit_policy",
    "corrupt_previous_bucket",
    "limit_conflict",
    "clock_overflow",
)


def _req(**kw):
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


def _id(record_without_id):
    body = json.dumps(record_without_id, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "lm1:" + hashlib.sha256(b"lm1" + bytes([0]) + body.encode("utf-8")).hexdigest()


def _forge(**fields):
    rec = {
        "op": "admit",
        "key": "k-1",
        "capacity": 3,
        "refill_ms": 1000,
        "cost": 1,
        "decision": "admit",
        "tokens": 1,
        "updated_at": 50,
        "retry_after_ms": None,
        "previous_id": None,
    }
    rec.update(fields)
    rec["limit_id"] = _id({k: rec[k] for k in RECORD_FIELDS[:-1]})
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
    assert record["limit_id"] == _id({k: v for k, v in record.items() if k != "limit_id"})
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


def _model(prev, cost, now, capacity, refill):
    """Independent integer model of one admit step. prev is (tokens, updated_at) or None."""
    if prev is None:
        tokens, base = capacity, now
    else:
        gained = (now - prev[1]) // refill
        if prev[0] + gained >= capacity:
            tokens, base = capacity, now
        else:
            tokens, base = prev[0] + gained, prev[1] + gained * refill
    if tokens >= cost:
        return "admit", tokens - cost, base, None
    return "deny", tokens, base, (cost - tokens) * refill - (now - base)


# ---- checks shared by the tests and the mutant harness ---------------------------------


def check_fixture(impl, err):
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            assert len(row["requests"]) == len(row["expect_records"]), row["name"]
            for request, expect in zip(row["requests"], row["expect_records"], strict=True):
                _ok(impl, request, expect)
    for row in CASES["malformed"]:
        _rejects(impl, err, row["request"], row["expect_failure"])
        try:
            impl(row["minimal_repair"]["request"])
        except err as exc:
            assert exc.failure_class != row["expect_failure"], row["name"]
    for row in CASES["rollback"]:
        _rejects(impl, err, row["rejected_request"], row["expect_failure"])
        _ok(impl, row["request"], row["expect_record"])


def check_differential(impl, err):
    for seed in range(25):
        rng = random.Random(seed)
        capacity, refill = rng.choice((1, 3, 7)), rng.choice((1, 10, 1000))
        policy = {"capacity": capacity, "refill_ms": refill}
        prev_record, prev_state, now = None, None, rng.choice((0, 5, 10**6))
        for _ in range(40):
            now += rng.choice((0, 1, refill - 1 if refill > 1 else 0, refill, 3 * refill + 1))
            cost = rng.randrange(1, capacity + 1)
            decision, tokens, base, retry = _model(prev_state, cost, now, capacity, refill)
            record = impl(_req(cost=cost, now=now, policy=dict(policy), previous=prev_record))
            assert (record["decision"], record["tokens"], record["updated_at"]) == (
                decision,
                tokens,
                base,
            )
            assert record["retry_after_ms"] == retry
            assert 0 <= record["tokens"] <= capacity
            prev_record, prev_state = record, (tokens, base)


def check_semantics(impl, err):
    fresh = impl(_req())
    assert (fresh["decision"], fresh["tokens"], fresh["updated_at"], fresh["previous_id"]) == (
        "admit",
        2,
        0,
        None,
    )
    drained = impl(_req(cost=3, now=0))
    assert drained["tokens"] == 0
    deny = impl(_req(now=10, previous=drained))
    assert (deny["decision"], deny["tokens"], deny["retry_after_ms"]) == ("deny", 0, 990)
    assert deny["updated_at"] == 0 and deny["previous_id"] == drained["limit_id"]
    # a partial refill is carried: 1500ms is one token and 500ms kept
    carried = impl(_req(now=1500, previous=drained))
    assert (carried["decision"], carried["tokens"], carried["updated_at"]) == ("admit", 0, 1000)
    waiting = impl(_req(now=1600, previous=carried))
    assert waiting["decision"] == "deny" and waiting["retry_after_ms"] == 400
    # reaching capacity drops the remainder
    full = impl(_req(now=50 + 5999, previous=_forge()))
    assert (full["tokens"], full["updated_at"]) == (2, 50 + 5999)
    assert impl(_req(now=50 + 2000, previous=_forge()))["updated_at"] == 50 + 2000
    # equal clock accepted, regression is a conflict
    assert impl(_req(now=50, previous=_forge()))["tokens"] == 0
    _rejects(impl, err, _req(now=49, previous=_forge()), "limit_conflict")
    # identity: key, capacity and refill_ms must match the previous record
    _rejects(impl, err, _req(key="k-2", now=60, previous=_forge()), "limit_conflict")
    _rejects(
        impl,
        err,
        _req(now=60, policy={"capacity": 4, "refill_ms": 1000}, previous=_forge()),
        "limit_conflict",
    )
    _rejects(
        impl,
        err,
        _req(now=60, policy={"capacity": 3, "refill_ms": 999}, previous=_forge()),
        "limit_conflict",
    )
    # cost above capacity is malformed, never a deny
    _rejects(impl, err, _req(cost=4), "malformed_limit_request")
    assert impl(_req(cost=3))["decision"] == "admit"
    # overflow: now + retry past the clock is an error and is never clamped
    edge = _req(cost=1, now=MAX - 1000, previous=_forge(tokens=0, updated_at=MAX - 1000))
    assert impl(edge)["retry_after_ms"] == 1000  # lands exactly on the clock limit
    big = _req(cost=1, now=MAX, previous=_forge(tokens=0, updated_at=MAX))
    _rejects(impl, err, big, "clock_overflow")
    a, b = impl(_req(now=7)), impl(_req(now=7))
    assert a == b and a is not b


def check_order(impl, err):
    # shape before policy
    _rejects(impl, err, _req(cost=0, policy={"capacity": 0}), "malformed_limit_request")
    # policy before cost-within-capacity
    _rejects(
        impl, err, _req(cost=1, policy={"capacity": 0, "refill_ms": 1}), "invalid_limit_policy"
    )
    # cost-within-capacity before previous integrity
    forged = _forge()
    forged["limit_id"] = "lm1:" + "0" * 64
    _rejects(impl, err, _req(cost=4, previous=forged), "malformed_limit_request")
    # previous integrity before chain consistency
    _rejects(impl, err, _req(key="k-2", now=60, previous=forged), "corrupt_previous_bucket")
    # chain consistency before clock overflow
    _rejects(impl, err, _req(key="k-2", now=MAX, previous=_forge(tokens=0)), "limit_conflict")
    for bad in (
        _forge(tokens=4),
        _forge(tokens=-1),
        _forge(cost=0),
        _forge(cost=4),
        _forge(decision="maybe"),
        _forge(retry_after_ms=5),
        _forge(tokens=3),  # admit leaving more than capacity - cost
        _forge(decision="deny", tokens=1, retry_after_ms=1000),  # tokens not below cost
        _forge(decision="deny", tokens=0, retry_after_ms=None),
        _forge(decision="deny", tokens=0, retry_after_ms=1001),
        _forge(decision="deny", tokens=0, retry_after_ms=0),
        _forge(op="x"),
        _forge(previous_id="bad"),
        _forge(updated_at=-1),
        _forge(updated_at=MAX + 1),
    ):
        _rejects(impl, err, _req(now=100, previous=bad), "corrupt_previous_bucket")
    tampered = {**_forge(), "tokens": 0}
    _rejects(impl, err, _req(now=100, previous=tampered), "corrupt_previous_bucket")


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
    for field in ("op", "key", "cost", "now", "policy", "previous"):
        for bad in HOSTILE:
            request = _req()
            request[field] = bad
            if request == _req():
                continue
            if (field, bad) == ("key", "x"):
                continue  # a valid key
            try:
                impl(request)
            except err as exc:
                assert exc.failure_class in CLASSES
            else:
                raise AssertionError((field, bad))
    for field in POLICY:
        for bad in HOSTILE:
            request = _req()
            request["policy"][field] = bad
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
                impl(_req(now=100, previous=prev))
            except err as exc:
                assert exc.failure_class == "corrupt_previous_bucket", (field, bad)
            else:
                raise AssertionError((field, bad))
    for bad in (Boom(), BoomDict(), BoomStr("admit"), [_req()], (_req(),), "admit", None):
        try:
            impl(bad)
        except err as exc:
            assert exc.failure_class == "malformed_limit_request"
        else:
            raise AssertionError(bad)
    for forged in (
        BoomDict(_req()),
        _req(key=BoomStr("k-1")),
        _req(policy=BoomDict(POLICY)),
        _req(previous=BoomDict(_forge())),
        _req(previous=_forge(key=BoomStr("k-1"))),
        _req(previous=_forge(decision=BoomStr("admit"))),
    ):
        try:
            impl(forged)
        except err:
            pass
        except AssertionError as exc:
            raise AssertionError("caller code ran: " + str(exc)) from None
        else:
            raise AssertionError("subclass accepted")
    for bad in ("k\n", "", "k" * 65, "k k", "é"):
        _rejects(impl, err, _req(key=bad), "malformed_limit_request")
    for field, bad in (("cost", True), ("now", True), ("now", MAX + 1), ("now", -1)):
        _rejects(impl, err, _req(**{field: bad}), "malformed_limit_request")
    _rejects(impl, err, {**_req(), "extra": 1}, "malformed_limit_request")
    missing = _req()
    del missing["previous"]
    _rejects(impl, err, missing, "malformed_limit_request")


class RawEscape(AssertionError):
    """A non-contract exception escaped the implementation (a crash, not a decision)."""


def _guard(impl, err):
    """Typed contract errors and assertion failures pass through; any other
    exception (an AssertionError raised inside the implementation included)
    is a crash and becomes RawEscape: a totality failure that is
    never counted as a semantic mutant kill."""

    def guarded(*args):
        try:
            return impl(*args)
        except err:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RawEscape(f"raw {type(exc).__name__} escaped") from None

    return guarded


CHECKS = (check_fixture, check_differential, check_semantics, check_order, check_totality)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_bound_implementation_passes(check):
    check(_guard(admit, LimitError), LimitError)


def test_fixture_covers_every_failure_class_and_decision():
    assert {r["expect_failure"] for r in CASES["malformed"]} == set(CLASSES)
    decisions = {
        rec["decision"]
        for s in ("happy", "boundary")
        for r in CASES[s]
        for rec in r["expect_records"]
    }
    assert decisions == {"admit", "deny"}


# ---- mutants: the battery must be RED on each ------------------------------------------


def _wrap(fn):
    def impl(request):
        return fn(request, admit)

    return impl


def _redo(out):
    out["limit_id"] = _id({k: v for k, v in out.items() if k != "limit_id"})
    return out


def _m_banks_partial_at_cap(request, real):
    out = real(request)
    prev = request["previous"] if isinstance(request, dict) else None
    if (
        isinstance(prev, dict)
        and out["tokens"] + out["cost"] == out["capacity"]
        and out["decision"] == "admit"
    ):
        out["updated_at"] = prev["updated_at"]
        _redo(out)
    return out


def _m_drops_partial(request, real):
    out = real(request)
    if out["decision"] == "admit" and out["updated_at"] % out["refill_ms"]:
        out["updated_at"] = request["now"]
        _redo(out)
    return out


def _m_ceil_gain(request, real):
    prev = request.get("previous") if isinstance(request, dict) else None
    if (
        isinstance(prev, dict)
        and (request["now"] - prev["updated_at"]) % request["policy"]["refill_ms"]
    ):
        return real({**request, "now": request["now"] + request["policy"]["refill_ms"]})
    return real(request)


def _m_retry_from_full_window(request, real):
    out = real(request)
    if out["decision"] == "deny":
        out["retry_after_ms"] = (out["cost"] - out["tokens"]) * out["refill_ms"]
        _redo(out)
    return out


def _m_deny_over_capacity(request, real):
    try:
        return real(request)
    except LimitError as exc:
        if (
            exc.failure_class == "malformed_limit_request"
            and request["cost"] > request["policy"]["capacity"]
        ):
            return real(
                {
                    **request,
                    "cost": request["policy"]["capacity"] + 0,
                    "previous": request["previous"],
                }
            )
        raise


def _m_clamp_overflow(request, real):
    try:
        return real(request)
    except LimitError as exc:
        if exc.failure_class != "clock_overflow":
            raise
        out = real({**request, "now": request["previous"]["updated_at"]})
        out["retry_after_ms"] = MAX - request["now"]
        return _redo(out)


def _m_regress_ok(request, real):
    try:
        return real(request)
    except LimitError as exc:
        prev = request.get("previous") if isinstance(request, dict) else None
        if (
            exc.failure_class == "limit_conflict"
            and isinstance(prev, dict)
            and request["now"] < prev["updated_at"]
        ):
            return real({**request, "now": prev["updated_at"]})
        raise


def _m_policy_change_ignored(request, real):
    try:
        return real(request)
    except LimitError as exc:
        prev = request.get("previous") if isinstance(request, dict) else None
        if (
            exc.failure_class == "limit_conflict"
            and isinstance(prev, dict)
            and request["policy"]["capacity"] != prev["capacity"]
        ):
            return real({**request, "previous": None})
        raise


def _m_trust_previous(request, real):
    try:
        return real(request)
    except LimitError as exc:
        if exc.failure_class == "corrupt_previous_bucket":
            return real({**request, "previous": None})
        raise


def _m_chain_before_integrity(request, real):
    try:
        return real(request)
    except LimitError as exc:
        prev = request.get("previous") if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_previous_bucket"
            and isinstance(prev, dict)
            and request.get("key") != prev.get("key")
        ):
            raise LimitError("limit_conflict") from None
        raise


def _m_raw_exception(request, real):
    if isinstance(request, dict) and request.get("now") is None:
        raise KeyError("now")
    return real(request)


def _m_refuses_valid(request, real):
    if isinstance(request, dict) and request.get("previous") is not None:
        raise LimitError("limit_conflict")
    return real(request)


def _m_refuses_everything(request, real):
    raise LimitError("malformed_limit_request")


RAW_MUTANTS = {"raw_exception"}
REFUSAL_MUTANTS = {"refuses_valid", "refuses_everything"}

MUTANTS = {
    "refuses_valid": (_m_refuses_valid, "check_semantics"),
    "refuses_everything": (_m_refuses_everything, "check_fixture"),
    "banks_partial_at_cap": (_m_banks_partial_at_cap, "check_differential"),
    "drops_partial": (_m_drops_partial, "check_differential"),
    "ceil_gain": (_m_ceil_gain, "check_differential"),
    "retry_from_full_window": (_m_retry_from_full_window, "check_differential"),
    "clamp_overflow": (_m_clamp_overflow, "check_semantics"),
    "regress_ok": (_m_regress_ok, "check_semantics"),
    "policy_change_ignored": (_m_policy_change_ignored, "check_semantics"),
    "trust_previous": (_m_trust_previous, "check_order"),
    "chain_before_integrity": (_m_chain_before_integrity, "check_order"),
    "raw_exception": (_m_raw_exception, "check_totality"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_target(name):
    mutate, target = MUTANTS[name]
    check = next(c for c in CHECKS if c.__name__ == target)
    try:
        check(_guard(_wrap(mutate), LimitError), LimitError)
    except RawEscape:
        # only the mutant that raises a raw exception on purpose may die this way
        assert name in RAW_MUTANTS, f"mutant {name} crashed instead of being refuted"
    except LimitError:
        # a typed refusal of valid input is a kill only for the acceptance-oracle mutants
        assert name in REFUSAL_MUTANTS, f"mutant {name} died of a typed error, not an assertion"
    except (AssertionError, pytest.fail.Exception):
        assert name not in RAW_MUTANTS, f"mutant {name} was not refuted by the raw-exception probe"
    else:
        raise AssertionError(f"mutant {name} survived {target}")
