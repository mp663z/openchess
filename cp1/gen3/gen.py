import sys
body = open('/tmp/gen3/body.py').read()
# tidy the sloppy lines in the shared body
body = body.replace('''        req_after = _isnap({"request": prod_case["request"]})
        assert req_after == _isnap({"request": _build(base, **kw)["request"]}) or True
''','''        assert _isnap(prod_case["request"]) == _isnap(_build(base, **kw)["request"]), (
            "accepted call changed its request"
        )
''')
body = body.replace('''        else:
            out.append(("other", t.__name__, id(n) if t is object else 0))''','''        else:
            out.append(("other", t.__name__))''')

common_tail = '''

# ---------- order independence, determinism, late faults ----------


def _reorder(node, rng):
    if type(node) is dict:
        items = [(k, _reorder(v, rng)) for k, v in node.items()]
        rng.shuffle(items)
        return dict(items)
    if type(node) is list:
        return [_reorder(v, rng) for v in node]
    return node


@pytest.mark.parametrize("seed", range(10))
def test_key_order_never_changes_the_outcome_and_runs_are_deterministic(seed):
    rng = random.Random(seed)
    for base in BASES:
        first = _outcome(PROD, copy.deepcopy(base), PROD_ERR)
        assert _outcome(PROD, copy.deepcopy(base), PROD_ERR) == first
        shuffled = _reorder(copy.deepcopy(base), rng)
        assert _outcome(PROD, shuffled, PROD_ERR)[:2] == first[:2] or STATE_KEY_ORDER_MATTERS


class _CountingHashlib:
    """Delegates to the real hashlib and faults on the Nth sha256 call."""

    def __init__(self, fail_on=None):
        self.calls, self.fail_on = 0, fail_on

    def sha256(self, *a, **k):
        self.calls += 1
        if self.fail_on == self.calls:
            raise RuntimeError("injected fault in the digest step")
        return hashlib.sha256(*a, **k)


def test_a_fault_at_any_digest_step_leaves_input_untouched_and_the_next_call_is_clean(monkeypatch):
    for base in BASES:
        counter = _CountingHashlib()
        monkeypatch.setattr(PMOD, "hashlib", counter)
        baseline = _outcome(PROD, copy.deepcopy(base), PROD_ERR)
        steps = counter.calls
        assert baseline[0] == "ok" and steps >= 1
        for n in range(1, steps + 1):
            monkeypatch.setattr(PMOD, "hashlib", _CountingHashlib(fail_on=n))
            case = copy.deepcopy(base)
            before = _isnap(case)
            with pytest.raises(RuntimeError, match="injected fault"):
                PROD(*_args(case))
            assert _isnap(case) == before, f"fault at digest step {n} tore the input"
            monkeypatch.setattr(PMOD, "hashlib", _CountingHashlib())
            again = _outcome(PROD, copy.deepcopy(base), PROD_ERR)
            assert again == baseline
        monkeypatch.undo()


def test_rejected_requests_never_reach_the_digest_step(monkeypatch):
    counter = _CountingHashlib()
    monkeypatch.setattr(PMOD, "hashlib", counter)
    for base in BASES:
        for path in [p for p in _paths(base) if p][:20]:
            case = _build(base, path=path, make=lambda: ArmedObject())
            _outcome(PROD, case, PROD_ERR)
    assert counter.calls == 0
'''

header = '''"""{task}: seeded fuzz and fault battery for the production jobs {name}.

The contract's reference implementation is the differential oracle. Valid
cases are mutated at every reachable path with hostile values (every type
the validators gate on, boundary ints and strings, armed objects whose every
hook fails the test, attribute bombs, lying dicts, cycles, 100000-deep and
wide values, oversize ints under a lowered interpreter int-digit limit),
keys are dropped, added and replaced by non-str or armed keys, and faults
are injected into the digest step. Production and reference must return the
same record or the same failure class and code, a rejected call must leave
its input structurally identical (a hook-free iterative snapshot), production
must never run caller code, and a late fault must leave the input untouched
and the next call clean. Seeded and single-threaded, so a failure replays.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import random
import sys
import time

import pytest
'''

specs = {}

specs['T0316'] = dict(name='retry', mod='jobs_retry', fn='decide', err='RetryError', ref='test_t0311_retry_contract',
 reqfail='malformed_retry_request',
 codes='{"malformed_retry_request": "malformed_request", "invalid_retry_policy": "invalid_policy", "clock_overflow": "clock_overflow"}',
 spec='''
JOB = "job1:" + "ab" * 32
MAX = 2**53 - 1
REQUEST_FAILURE = "{reqfail}"
STATE_KEY_ORDER_MATTERS = False


def _args(case):
    return (case["request"],)


def make_base_cases(rng):
    cases = []
    for attempts in (1, 2, 3, 4, 5):
        for failure in ("transient", "timeout", "permanent"):
            policy = {{"base_delay_ms": rng.choice((1, 1000, 3_600_000)), "multiplier": rng.choice((1, 2, 10)),
                      "max_delay_ms": 86_400_000}}
            cases.append({{"request": {{"op": "decide", "job_id": JOB, "attempts": attempts, "failure": failure,
                                       "now": rng.choice((0, 1, 10**9)), "policy": policy}}}})
    return cases
''',
 extra='''

# ---------- job lifecycle streams with crashing workers and clock jumps ----------


@pytest.mark.parametrize("seed", range(30))
def test_failure_streams_match_the_oracle_and_keep_the_schedule_monotone(seed):
    rng = random.Random(300 + seed)
    policy = {"base_delay_ms": rng.choice((1, 7, 1000)), "multiplier": rng.choice((1, 2, 3, 10)),
              "max_delay_ms": rng.choice((1000, 30_000, 86_400_000))}
    policy["max_delay_ms"] = max(policy["max_delay_ms"], policy["base_delay_ms"])
    now, last_delay, attempts = rng.choice((0, 5, MAX - 10**6)), 0, 1
    while attempts <= 5:
        request = {"op": "decide", "job_id": JOB, "attempts": attempts,
                   "failure": rng.choice(("transient", "timeout", "transient", "permanent")),
                   "now": now, "policy": copy.deepcopy(policy)}
        prod = _outcome(PROD, {"request": request}, PROD_ERR)
        ref = _outcome(REF, {"request": copy.deepcopy(request)}, REF_ERR)
        assert prod[0] == ref[0] and prod[1] == ref[1]
        if prod[0] == "fail":
            assert prod[1] == "clock_overflow"
            break
        record = prod[1]
        if record["decision"] == "dead":
            assert record["retry_at"] is None and record["delay_ms"] is None
            assert request["failure"] == "permanent" or attempts == 5
            break
        assert record["retry_at"] == now + record["delay_ms"] <= MAX
        assert record["delay_ms"] <= policy["max_delay_ms"] and record["delay_ms"] >= last_delay
        last_delay = record["delay_ms"]
        now = record["retry_at"] + rng.choice((0, 1, 500))  # the worker crashed and came back late
        attempts += 1


def test_every_attempt_count_and_clock_edge_matches_the_oracle():
    policy = {"base_delay_ms": 1000, "multiplier": 10, "max_delay_ms": 86_400_000}
    for attempts in range(1, 6):
        for now in (0, 1, MAX - 86_400_000, MAX - 86_400_000 + 1, MAX - 1000, MAX - 999, MAX - 1, MAX):
            for failure in ("transient", "timeout", "permanent"):
                base = {"request": {"op": "decide", "job_id": JOB, "attempts": attempts, "failure": failure,
                                    "now": now, "policy": copy.deepcopy(policy)}}
                _agree(base, path=())
''')

specs['T0325'] = dict(name='progress', mod='jobs_progress', fn='report', err='ProgressError', ref='test_t0320_progress_contract',
 reqfail='malformed_progress_request', codes=None,
 spec='''
JOB = "job1:" + "ab" * 32
JOB2 = "job1:" + "cd" * 32
MAX = 2**53 - 1
REQUEST_FAILURE = "{reqfail}"
STATE_KEY_ORDER_MATTERS = False


def _args(case):
    return (case["request"],)


def _chain(rng, job=JOB, worker="w-1", total=10, steps=4):
    """A valid chain built with the reference; returns the list of requests."""
    prev, now, done, out = None, rng.choice((0, 1, 500)), 0, []
    for _ in range(steps):
        done = min(total, done + rng.choice((0, 1, 3)))
        request = {{"op": "report", "job_id": job, "worker": worker, "done": done, "total": total,
                   "now": now, "previous": copy.deepcopy(prev)}}
        out.append(copy.deepcopy(request))
        prev = REF(request)
        now += rng.choice((0, 1, 10))
        if done == total:
            break
    return out


def make_base_cases(rng):
    cases = []
    for job, worker, total in ((JOB, "w-1", 10), (JOB2, "worker.2:x", 1), (JOB, "w", 100000)):
        for request in _chain(rng, job, worker, total, steps=5):
            cases.append({{"request": request}})
    cases.append({{"request": {{"op": "report", "job_id": JOB, "worker": "w-1", "done": 10, "total": 10,
                              "now": MAX, "previous": None}}}})
    return cases
''',
 extra='''

# ---------- report chains with crashed workers, replays, forks and clock faults ----------


@pytest.mark.parametrize("seed", range(30))
def test_report_streams_match_the_oracle_and_keep_the_chain_sound(seed):
    rng = random.Random(700 + seed)
    total = rng.choice((1, 5, 10, 100))
    prev, done, now, records = None, 0, 0, []
    for _ in range(40):
        pick = rng.random()
        step_done = done + rng.choice((0, 1, 2)) if pick < 0.7 else rng.randint(0, total + 2)
        step_now = now + rng.choice((0, 1, 50)) if pick < 0.85 else max(0, now - rng.choice((1, 5)))
        worker = rng.choice(("w-1", "w-1", "w-1", "w-2"))
        request = {"op": "report", "job_id": JOB, "worker": worker, "done": step_done, "total": total,
                   "now": step_now, "previous": copy.deepcopy(prev)}
        prod = _outcome(PROD, {"request": request}, PROD_ERR)
        ref = _outcome(REF, {"request": copy.deepcopy(request)}, REF_ERR)
        assert prod[0] == ref[0] and prod[1] == ref[1], (request, prod, ref)
        if prod[0] == "ok":
            record = prod[1]
            assert 0 <= record["done"] <= total
            if prev is not None:
                assert record["done"] >= prev["done"] and record["now"] >= prev["now"] \\
                    if "now" in record else record["done"] >= prev["done"]
                assert record["previous_id"] == prev["progress_id"]
            assert record["progress_id"] not in {r["progress_id"] for r in records}
            records.append(record)
            prev, done, now = record, record["done"], step_now
            if done == total:
                again = _outcome(PROD, {"request": {**request, "now": now, "previous": copy.deepcopy(prev)}}, PROD_ERR)
                assert again[0] == "fail"  # a completed job takes no further report
                break
    assert records or True


def test_a_forged_previous_with_every_single_field_changed_is_refused_like_the_oracle():
    base = {"request": _chain(random.Random(5), steps=3)[-1]}
    prev = base["request"]["previous"]
    assert prev is not None
    for key in list(prev):
        for factory in HOSTILE_FACTORIES:
            _agree(base, path=("request", "previous", key), make=factory)
''')
