"""T0311: jobs retry contract - the contract document
(data/contracts/retry.yaml) is normative; this battery holds the
contract-derived REFERENCE decision function and proves happy,
boundary, malformed and rollback behavior against it.

A retry decision is a pure function of one request
{op, job_id, attempts, failure, now, policy}: a permanent failure or an
exhausted job (attempts == the queue contract's max_attempts) is dead;
otherwise the job is retried after
min(max_delay_ms, base_delay_ms * multiplier ** (attempts - 1)) ms, at
now + delay. There is no jitter.

Fail-closed readings (the spec is silent; each is stated in the
contract's rule text):
- timeout is retryable exactly like transient (no separate budget);
- a retry_at beyond 2**53 - 1 is clock_overflow, never clamped and never
  turned into dead;
- max_attempts comes from the linked queue contract, never the caller;
- no jitter: decisions are deterministic and replayable;
- validation order is request shape -> policy -> clock; a malformed
  request wins over a bad policy.
Not covered (needs the idempotency/cancel contracts T0293/T0302,
parked): retry of a cancelled job and retry deduplication by key.

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

from tools.retry_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_POLICY_FIELDS = _REQ["policy_fields"]
_FAILURES = tuple(_REQ["failures"])
_RECORD_FIELDS = _CC["record"]["fields"]
MAX_ATTEMPTS = _REQ["max_attempts"]
MAX_NOW = 2**53 - 1
MAX_BASE = 3_600_000
MAX_MULT = 10
MAX_DELAY = 86_400_000
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_DECISION_RE = re.compile(_CC["identifiers"]["decision_id"]["grammar"], re.ASCII)


class RetryError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _int_in(value, lo, hi):
    return type(value) is int and lo <= value <= hi


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
        and request["op"] == "decide"
        and type(request["job_id"]) is str
        and _JOB_RE.fullmatch(request["job_id"]) is not None
        and _int_in(request["attempts"], 1, MAX_ATTEMPTS)
        and type(request["failure"]) is str
        and request["failure"] in _FAILURES
        and _int_in(request["now"], 0, MAX_NOW)
        and type(request["policy"]) is dict
    )


def _policy_ok(policy):
    return (
        _exact_keys(policy, _POLICY_FIELDS)
        and _int_in(policy["base_delay_ms"], 1, MAX_BASE)
        and _int_in(policy["multiplier"], 1, MAX_MULT)
        and _int_in(policy["max_delay_ms"], policy["base_delay_ms"], MAX_DELAY)
    )


def decide(request):
    """The reference decision. Fresh typed errors (flag pattern)."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_retry_request"
    elif not _policy_ok(request["policy"]):
        failed = "invalid_retry_policy"
    if failed is not None:
        raise RetryError(failed)
    req = json.loads(_canon(request))  # detached exact copy
    policy = req["policy"]
    dead = req["failure"] == "permanent" or req["attempts"] >= MAX_ATTEMPTS
    if dead:
        delay = retry_at = None
    else:
        delay = min(
            policy["max_delay_ms"],
            policy["base_delay_ms"] * policy["multiplier"] ** (req["attempts"] - 1),
        )
        retry_at = req["now"] + delay
        if retry_at > MAX_NOW:
            raise RetryError("clock_overflow")
    record = {
        "op": "decide",
        "job_id": req["job_id"],
        "decision": "dead" if dead else "retry",
        "attempts": req["attempts"],
        "delay_ms": delay,
        "retry_at": retry_at,
    }
    digest = hashlib.sha256(
        b"rd1\x00" + _canon({"request": req, "record": record}).encode()
    ).hexdigest()
    record["decision_id"] = "rd1:" + digest
    return record


# -- helpers ----------------------------------------------------------------------------

JOB = "job1:" + "a" * 64
POLICY = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 30000}


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


def _raises(failure_class, request):
    snap = copy.deepcopy(request) if _plain(request) else None
    with pytest.raises(RetryError) as caught:
        decide(request)
    exc = caught.value
    assert type(exc) is RetryError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class]
    assert exc.__cause__ is None and exc.__context__ is None
    if snap is not None:
        assert request == snap


def _plain(obj):
    if type(obj) is dict:
        return all(type(k) is str and _plain(v) for k, v in obj.items())
    return type(obj) in (str, int, bool, type(None))


# -- lint -------------------------------------------------------------------------------


def test_lint_clean():
    lint()


def _mutants():
    def drop_failure_enum(cc):
        cc["request"]["failures"].remove("timeout")

    def max_attempts(cc):
        cc["request"]["max_attempts"] = 6

    def job_grammar(cc):
        cc["identifiers"]["job_id"]["grammar"] = "^job1:.*$"

    def timeout_reading(cc):
        cc["semantics"]["timeout"] = "a-timeout-is-dead"

    def clock_reading(cc):
        cc["semantics"]["clock"] = "a-retry-at-beyond-the-clock-is-clamped"

    def jitter(cc):
        cc["semantics"]["jitter"] = "full-jitter"

    def extra_failure(cc):
        cc["failures"]["classes"].append("retry_budget_exceeded")

    def mapping_drift(cc):
        cc["failures"]["mapping"]["clock_overflow"] = "internal"

    def open_failures(cc):
        cc["failures"]["closed"] = False

    def extra_section(cc):
        cc["cancellation"] = {}

    def retryable(cc):
        cc["errors"]["shape"]["retryable_true_only_for"] = ["internal", "clock_overflow"]

    def link(cc):
        cc["links"]["queue_contract"] = "data/contracts/missing.yaml"

    def policy_field(cc):
        cc["request"]["policy_fields"].append("jitter_ms")

    def bound(cc):
        cc["request"]["bounds"]["multiplier"] = "int-1-through-100"

    return [
        drop_failure_enum,
        max_attempts,
        job_grammar,
        timeout_reading,
        clock_reading,
        jitter,
        extra_failure,
        mapping_drift,
        open_failures,
        extra_section,
        retryable,
        link,
        policy_field,
        bound,
    ]


@pytest.mark.parametrize("mutate", _mutants(), ids=lambda f: f.__name__)
def test_mutations_fail_lint(mutate, tmp_path):
    doc = yaml.safe_load(CONTRACT.read_text())
    mutate(doc["contract"])
    path = tmp_path / "retry.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


def test_lint_binds_the_queue_sibling(tmp_path, monkeypatch):
    """max_attempts and the job id grammar are the queue contract's: a
    drifted queue contract fails the retry lint too."""
    from tools import retry_contract_lint as rl

    queue = yaml.safe_load((ROOT / "data/contracts/queue.yaml").read_text())
    queue["contract"]["state"]["max_attempts"] = 6
    (tmp_path / "data/contracts").mkdir(parents=True)
    (tmp_path / "data/contracts/queue.yaml").write_text(yaml.safe_dump(queue))
    monkeypatch.setattr(rl, "ROOT", tmp_path)
    with pytest.raises(ContractError):
        rl.lint(CONTRACT)


def test_error_enum_matches_mapping():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)


# -- happy path ---------------------------------------------------------------------------


def test_backoff_sequence_and_dead_at_max_attempts():
    got = [decide(req(attempts=a, now=100)) for a in range(1, MAX_ATTEMPTS + 1)]
    assert [r["decision"] for r in got] == ["retry"] * (MAX_ATTEMPTS - 1) + ["dead"]
    assert [r["delay_ms"] for r in got] == [1000, 2000, 4000, 8000, None]
    assert [r["retry_at"] for r in got] == [1100, 2100, 4100, 8100, None]
    for r in got:
        assert list(r) == _RECORD_FIELDS
        assert _DECISION_RE.fullmatch(r["decision_id"])


def test_permanent_is_dead_at_any_attempt():
    for a in range(1, MAX_ATTEMPTS + 1):
        r = decide(req(attempts=a, failure="permanent", now=MAX_NOW))
        assert (r["decision"], r["delay_ms"], r["retry_at"]) == ("dead", None, None)


def test_timeout_is_retryable_like_transient():
    for a in range(1, MAX_ATTEMPTS + 1):
        t, x = decide(req(attempts=a, failure="timeout")), decide(req(attempts=a))
        assert {k: t[k] for k in _RECORD_FIELDS[:-1]} == {k: x[k] for k in _RECORD_FIELDS[:-1]}
        assert t["decision_id"] != x["decision_id"]  # the failure kind is bound


def test_delay_is_capped_and_monotone():
    policy = {"base_delay_ms": 3000, "multiplier": 10, "max_delay_ms": 50000}
    delays = [decide(req(attempts=a, policy=dict(policy)))["delay_ms"] for a in range(1, 5)]
    assert delays == [3000, 30000, 50000, 50000]
    assert delays == sorted(delays)


def test_multiplier_one_is_constant_backoff():
    policy = {"base_delay_ms": 7, "multiplier": 1, "max_delay_ms": 7}
    assert {decide(req(attempts=a, policy=dict(policy)))["delay_ms"] for a in range(1, 5)} == {7}


def test_determinism_and_decision_id_binding():
    a, b = decide(req(attempts=2, now=5)), decide(req(attempts=2, now=5))
    assert a == b and a is not b
    ids = {
        decide(r)["decision_id"]
        for r in (
            req(attempts=2, now=5),
            req(attempts=2, now=6),
            req(attempts=3, now=5),
            req(attempts=2, now=5, job_id="job1:" + "b" * 64),
            req(attempts=2, now=5, policy={**POLICY, "max_delay_ms": 30001}),
            req(attempts=2, now=5, failure="timeout"),
        )
    }
    assert len(ids) == 6


# -- boundary -------------------------------------------------------------------------------


def test_bounds_are_inclusive():
    decide(req(attempts=1, now=0))
    decide(req(attempts=MAX_ATTEMPTS, now=MAX_NOW))
    decide(req(policy={"base_delay_ms": 1, "multiplier": 1, "max_delay_ms": 1}))
    decide(
        req(policy={"base_delay_ms": MAX_BASE, "multiplier": MAX_MULT, "max_delay_ms": MAX_DELAY})
    )
    for bad in (
        req(attempts=0),
        req(attempts=MAX_ATTEMPTS + 1),
        req(now=-1),
        req(now=MAX_NOW + 1),
    ):
        _raises("malformed_retry_request", bad)
    for policy in (
        {"base_delay_ms": 0, "multiplier": 2, "max_delay_ms": 10},
        {"base_delay_ms": MAX_BASE + 1, "multiplier": 2, "max_delay_ms": MAX_DELAY},
        {"base_delay_ms": 1, "multiplier": 0, "max_delay_ms": 10},
        {"base_delay_ms": 1, "multiplier": MAX_MULT + 1, "max_delay_ms": 10},
        {"base_delay_ms": 10, "multiplier": 2, "max_delay_ms": 9},
        {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": MAX_DELAY + 1},
    ):
        _raises("invalid_retry_policy", req(policy=policy))


def test_clock_edge_is_exact_and_fails_closed_past_it():
    r = decide(req(now=MAX_NOW - 1000))
    assert r["retry_at"] == MAX_NOW
    _raises("clock_overflow", req(now=MAX_NOW - 999))
    # dead decisions carry no retry_at, so they never overflow
    assert decide(req(now=MAX_NOW, attempts=MAX_ATTEMPTS))["decision"] == "dead"
    assert decide(req(now=MAX_NOW, failure="permanent"))["decision"] == "dead"


def test_exact_integer_arithmetic_at_the_largest_policy():
    policy = {"base_delay_ms": MAX_BASE, "multiplier": MAX_MULT, "max_delay_ms": MAX_DELAY}
    got = [decide(req(attempts=a, policy=dict(policy)))["delay_ms"] for a in range(1, 5)]
    assert got == [MAX_BASE, MAX_BASE * MAX_MULT, MAX_DELAY, MAX_DELAY]
    assert all(type(d) is int for d in got)


# -- malformed -------------------------------------------------------------------------------


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _EqRaises(str):
    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    def __hash__(self):
        return str.__hash__(self)


class _Collides:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


def _rekey(mapping, field, make):
    return {(make(k) if k == field else k): v for k, v in mapping.items()}


MALFORMED = {
    "none": None,
    "list": list(req().items()),
    "dict-subclass": _DictSub(req()),
    "missing-now": {k: v for k, v in req().items() if k != "now"},
    "extra": {**req(), "x": 1},
    "renamed-same-arity": {("Now" if k == "now" else k): v for k, v in req().items()},
    "op-unknown": req(op="retry"),
    "op-str-subclass": req(op=_StrSub("decide")),
    "job-bad": req(job_id="job1:" + "A" * 64),
    "job-newline": req(job_id=JOB + "\n"),
    "job-str-subclass": req(job_id=_StrSub(JOB)),
    "attempts-bool": req(attempts=True),
    "attempts-float": req(attempts=1.0),
    "attempts-int-subclass": req(attempts=_IntSub(1)),
    "failure-unknown": req(failure="fatal"),
    "failure-newline": req(failure="transient\n"),
    "failure-str-subclass": req(failure=_StrSub("transient")),
    "now-bool": req(now=False),
    "policy-none": req(policy=None),
    "policy-list": req(policy=list(POLICY.items())),
    "policy-dict-subclass": req(policy=_DictSub(POLICY)),
    # a malformed request wins over a bad policy
    "bad-op-and-bad-policy": req(op="x", policy={"base_delay_ms": 0}),
}
for _field in _FIELDS:
    for _label, _make in (
        ("str-subclass", _StrSub),
        ("eq-raises", _EqRaises),
        ("hash-collides", _Collides),
    ):
        MALFORMED[f"key-{_label}-{_field}"] = _rekey(req(), _field, _make)

BAD_POLICY = {
    "missing": {"base_delay_ms": 1, "multiplier": 2},
    "extra": {**POLICY, "jitter_ms": 0},
    "renamed-same-arity": {"base_delay_ms": 1, "Multiplier": 2, "max_delay_ms": 3},
    "bool": {**POLICY, "multiplier": True},
    "float": {**POLICY, "base_delay_ms": 1000.0},
    "int-subclass": {**POLICY, "max_delay_ms": _IntSub(30000)},
    "str": {**POLICY, "multiplier": "2"},
    "max-below-base": {**POLICY, "max_delay_ms": 999},
}
for _field in _POLICY_FIELDS:
    for _label, _make in (
        ("str-subclass", _StrSub),
        ("eq-raises", _EqRaises),
        ("hash-collides", _Collides),
    ):
        BAD_POLICY[f"key-{_label}-{_field}"] = _rekey(POLICY, _field, _make)


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_requests(name):
    _raises("malformed_retry_request", MALFORMED[name])


@pytest.mark.parametrize("name", sorted(BAD_POLICY))
def test_invalid_policies(name):
    _raises("invalid_retry_policy", req(policy=BAD_POLICY[name]))


def test_policy_error_wins_over_clock_overflow():
    _raises("invalid_retry_policy", req(now=MAX_NOW, policy={**POLICY, "multiplier": 0}))


# -- rollback / purity ---------------------------------------------------------------------


def test_inputs_are_never_mutated_and_records_are_detached():
    request = req(attempts=2, now=10)
    snap = copy.deepcopy(request)
    record = decide(request)
    assert request == snap
    record["delay_ms"] = -1
    assert decide(request)["delay_ms"] == 2000
    for bad in (req(attempts=0), req(policy={**POLICY, "multiplier": 0}), req(now=MAX_NOW - 1)):
        before = copy.deepcopy(bad)
        with pytest.raises(RetryError):
            decide(bad)
        assert bad == before


def test_every_job_reaches_dead_by_max_attempts():
    for failure in _FAILURES:
        decisions = [
            decide(req(attempts=a, failure=failure))["decision"] for a in range(1, MAX_ATTEMPTS + 1)
        ]
        assert decisions[-1] == "dead"
        assert "retry" not in decisions[decisions.index("dead") :]


# -- the reference itself is pinned: one-edit mutants of decide fail the battery ------
# Anchors are matched against decide's own source (inspect), inside the
# builder, so an edit to this file never breaks collection.

REFERENCE_EDITS = {
    "timeout-dead": ('req["failure"] == "permanent"', 'req["failure"] != "transient"'),
    "exhausted-off-by-one": (
        'req["attempts"] >= MAX_ATTEMPTS',
        'req["attempts"] > MAX_ATTEMPTS',
    ),
    "clamp-clock": ('raise RetryError("clock_overflow")', "retry_at = MAX_NOW"),
    "exponent-off": ('** (req["attempts"] - 1)', '** req["attempts"]'),
    "no-cap": ("delay = min(", "delay = max(0, 0 * "),
    "policy-first": ("    if not _request_ok(request):", "    if False:"),
    "chained-error": (
        "        raise RetryError(failed)",
        "        try:\n            raise KeyError(failed)\n"
        "        except KeyError:\n            raise RetryError(failed)",
    ),
}


# Edits that must NOT change behavior. not-detached: after validation the
# request holds only exact scalars plus an exact policy dict of exact
# ints, and the record copies scalars only, so working on the live
# request cannot alias or mutate anything the caller holds.
EQUIVALENT_EDITS = {
    "not-detached": ("req = json.loads(_canon(request))", "req = request"),
}


def _mutant_decide(name):
    import inspect

    old, new = {**REFERENCE_EDITS, **EQUIVALENT_EDITS}[name]
    source = inspect.getsource(decide)
    assert source.count(old) == 1, name
    namespace = dict(globals())
    exec(compile(source.replace(old, new), f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return namespace["decide"]


def _battery():
    """Every behavior test that takes no fixture, plus the parametrized
    malformed and policy rows; returns the failing labels."""
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
            "test_reference_edits_apply_once",
            "test_identity_battery_is_green",
        )
    ]
    jobs += [(f"malformed:{n}", lambda n=n: test_malformed_requests(n)) for n in MALFORMED]
    jobs += [(f"policy:{n}", lambda n=n: test_invalid_policies(n)) for n in BAD_POLICY]
    for label, job in jobs:
        try:
            job()
        except BaseException as exc:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(exc).__name__}")
    return failures


def test_reference_edits_apply_once():
    for name in {**REFERENCE_EDITS, **EQUIVALENT_EDITS}:
        _mutant_decide(name)


def test_identity_battery_is_green():
    assert _battery() == []


@pytest.mark.parametrize("name", sorted(REFERENCE_EDITS))
def test_reference_mutant_is_red(name, monkeypatch):
    monkeypatch.setitem(globals(), "decide", _mutant_decide(name))
    assert _battery() != [], name


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name, monkeypatch):
    monkeypatch.setitem(globals(), "decide", _mutant_decide(name))
    assert _battery() == [], name
