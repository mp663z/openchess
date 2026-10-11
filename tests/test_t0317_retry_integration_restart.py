"""T0317: integration and restart proof for the production jobs retry engine.

A retry decision depends on nothing but its request, so the durable value of
a job is its attempt count, its clock and its policy; the host persists the
decision log between runs.

A restart is modelled the way a host would do it: the durable value is written
as canonical JSON under tmp_path (atomic replace), the production module is
imported anew (`importlib.reload`), the file is read back and the stream
continues. A few cut points run in a real child interpreter. Every cut point
of a seeded stream must give the same outcomes and the same durable value as an
uninterrupted run and as the contract's reference implementation
(`tests.test_t0311_retry_contract`),
which is the differential oracle. A torn or forged durable
value is refused with the oracle's class and code and never half-applied.
Single process tree, no real clock, no concurrency, no production changes.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import random
import subprocess
import sys

import pytest

import server.jobs_retry as PMOD
from tests.test_t0311_retry_contract import decide as REF

MAX = 2**53 - 1
FN = "decide"
OP = "decide"
IDS = ("decision_id",)


JOB = "job1:" + "ab" * 32


def new_durable():
    return {"n": 0, "attempts": 1, "now": 0, "policy": None, "log": []}


def make_request(rng, durable):
    if durable["policy"] is None:
        base = rng.choice((1, 7, 1000))
        durable["policy"] = {
            "base_delay_ms": base,
            "multiplier": rng.choice((1, 2, 3, 10)),
            "max_delay_ms": max(base, rng.choice((1000, 30_000, 86_400_000))),
        }
        durable["now"] = rng.choice((0, 5, 10**9, MAX - 10**6, MAX - 1))
    kind = rng.random()
    attempts = durable["attempts"] if kind > 0.15 else rng.choice((0, 6, 1, 5))
    return {
        "op": "decide",
        "job_id": "job1:" + format(durable["n"], "064x"),
        "attempts": attempts,
        "failure": rng.choice(("transient", "timeout", "transient", "permanent")),
        "now": durable["now"],
        "policy": copy.deepcopy(durable["policy"]),
    }


def call_args(durable, request):
    return [request]


def PICKER(fn, request):
    return fn


def CHILD_OK(request):
    return True


def absorb(durable, request, args, out):
    if out[0] == "err":
        if out[1] == "clock_overflow":
            durable.update(n=durable["n"] + 1, attempts=1, policy=None)
        return
    record = out[1]
    durable["log"].append(record["decision_id"])
    if record["decision"] == "dead":
        durable.update(n=durable["n"] + 1, attempts=1, policy=None)
    else:
        durable["attempts"] = request["attempts"] + 1
        durable["now"] = record["retry_at"] + (record["attempts"] % 3)


def torn_variants(durable):
    request = make_request(random.Random(3), copy.deepcopy(durable))
    yield "valid", [copy.deepcopy(request)]
    for key in list(request):
        bad = copy.deepcopy(request)
        del bad[key]
        yield f"drop {key}", [bad]
    for key in ("base_delay_ms", "multiplier", "max_delay_ms"):
        for value in (0, -1, True, 1.5, None, MAX + 1):
            bad = copy.deepcopy(request)
            bad["policy"][key] = value
            yield f"policy {key}={value!r}", [bad]
    for attempts in (0, 6, True, "1", -1):
        bad = copy.deepcopy(request)
        bad["attempts"] = attempts
        yield f"attempts={attempts!r}", [bad]
    for now in (-1, MAX, MAX + 1, True, 1.0):
        bad = copy.deepcopy(request)
        bad["now"] = now
        yield f"now={now!r}", [bad]


def test_every_attempt_count_and_clock_edge_survives_a_restart_like_the_oracle():
    policy = {"base_delay_ms": 1000, "multiplier": 10, "max_delay_ms": 86_400_000}
    for attempts in range(0, 7):
        for now in (
            0,
            1,
            MAX - 86_400_000,
            MAX - 86_400_000 + 1,
            MAX - 1000,
            MAX - 999,
            MAX - 1,
            MAX,
        ):
            for failure in ("transient", "timeout", "permanent"):
                request = {
                    "op": "decide",
                    "job_id": JOB,
                    "attempts": attempts,
                    "failure": failure,
                    "now": now,
                    "policy": copy.deepcopy(policy),
                }
                assert _outcome(_fresh(), request) == _outcome(REF, copy.deepcopy(request))


def _fresh():
    """A restarted process: the production module is imported anew."""
    return getattr(importlib.reload(PMOD), FN)


def _outcome(fn, *args):
    try:
        return ("ok", fn(*args))
    except Exception as error:  # noqa: BLE001
        failure = getattr(error, "failure_class", None)
        assert failure is not None, repr(error)
        assert getattr(error, "retryable", False) is False
        assert error.__cause__ is None
        return ("err", failure, error.code)


def _save(path, durable):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(durable, sort_keys=True, separators=(",", ":")))
    os.replace(tmp, path)


def _load(path):
    return json.loads(path.read_text())


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _drive(seed, restart, tmp_path, cut=None, length=60, procs=()):
    """Run one seeded stream. restart: 'never' | 'every' | 'cut'."""
    rng = random.Random(seed)
    durable, outcomes = new_durable(), []
    path = tmp_path / "durable.json"
    _save(path, durable)
    fn = _fresh()
    for step in range(length):
        if restart == "every" or (restart == "cut" and step == cut):
            fn = _fresh()
            durable = _load(path)
        request = make_request(rng, durable)
        args = call_args(durable, request)
        ref_args = copy.deepcopy(args)
        if step in procs and CHILD_OK(request):
            out = _child(args)
        else:
            out = _outcome(PICKER(fn, request), *args)
        ref = _outcome(PICKER(REF, request), *ref_args)
        assert out == ref, (step, request, out, ref)
        absorb(durable, request, args, out)
        outcomes.append((request, out))
        _save(path, durable)
    return outcomes, durable


_CHILD = (
    "import json, sys\n"
    "from server.jobs_retry import decide as F\n"
    "args = json.load(sys.stdin)\n"
    "try:\n"
    "    out = ['ok', F(*args)]\n"
    "except Exception as e:\n"
    "    out = ['err', e.failure_class, e.code]\n"
    "print(json.dumps([out, args]))\n"
)


def _child(args):
    """One step in a brand-new interpreter, the way a restarted host would."""
    done = subprocess.run(
        [sys.executable, "-c", _CHILD.replace("\\n", "\n")],
        input=json.dumps(args),
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    out, after = json.loads(done.stdout)
    args[:] = after  # in-place changes (restart commits its state in place)
    return (out[0], out[1]) if out[0] == "ok" else tuple(out)


@pytest.mark.parametrize("seed", range(12))
def test_restart_at_every_step_matches_an_uninterrupted_run_and_the_oracle(seed, tmp_path):
    want, want_durable = _drive(seed, "never", tmp_path / "a" if False else tmp_path)
    got, got_durable = _drive(seed, "every", tmp_path)
    assert got == want
    assert got_durable == want_durable
    assert any(o[0] == "ok" for _, o in want)


@pytest.mark.parametrize("seed", range(10))
def test_single_restart_at_a_random_cut_point(seed, tmp_path):
    want, want_durable = _drive(100 + seed, "never", tmp_path)
    cut = random.Random(seed).randrange(1, 59)
    got, got_durable = _drive(100 + seed, "cut", tmp_path, cut=cut)
    assert got == want and got_durable == want_durable


@pytest.mark.parametrize("seed", range(3))
def test_steps_in_a_real_child_interpreter_match_the_in_process_run(seed, tmp_path):
    want, want_durable = _drive(200 + seed, "never", tmp_path, length=24)
    procs = {3, 9, 15, 21}
    got, got_durable = _drive(200 + seed, "every", tmp_path, length=24, procs=procs)
    assert got == want and got_durable == want_durable


@pytest.mark.parametrize("seed", range(6))
def test_replaying_the_request_log_after_restarts_gives_identical_outcomes(seed, tmp_path):
    first, _ = _drive(300 + seed, "never", tmp_path, length=40)
    second, _ = _drive(300 + seed, "every", tmp_path, length=40)
    assert [o for _, o in first] == [o for _, o in second]
    ok = [o[1] for _, o in first if o[0] == "ok" and o[1].get("op") == OP]
    distinct = {_canon(r) for r in ok}
    for field in IDS:
        values = {r[field] for r in ok if field in r}
        # one identifier per distinct receipt: equal receipts share it, others never collide
        assert len(values) == len({_canon(r) for r in ok if field in r}) or not values
    assert len(distinct) >= 1


def test_a_refused_step_changes_nothing_durable_and_the_stream_continues(tmp_path):
    rng = random.Random(7)
    durable = new_durable()
    for _ in range(25):
        request = make_request(rng, durable)
        args = call_args(durable, request)
        out = _outcome(PICKER(_fresh(), request), *args)
        absorb(durable, request, args, out)
    good = copy.deepcopy(durable)
    refused = 0
    for label, bad_args in torn_variants(durable):
        before = _canon(bad_args)
        out = _outcome(_fresh(), *copy.deepcopy(bad_args))
        ref = _outcome(REF, *copy.deepcopy(bad_args))
        assert out == ref, (label, out, ref)
        if out[0] == "err":
            refused += 1
            probe = copy.deepcopy(bad_args)
            _outcome(_fresh(), *probe)
            assert _canon(probe) == before, label
    assert refused > 0
    assert durable == good
    request = make_request(rng, durable)
    args = call_args(durable, request)
    assert _outcome(PICKER(_fresh(), request), *copy.deepcopy(args)) == _outcome(
        PICKER(REF, request), *copy.deepcopy(args)
    )


def test_a_truncated_durable_file_is_a_host_error_and_the_last_good_value_still_works(tmp_path):
    rng = random.Random(11)
    durable = new_durable()
    path = tmp_path / "durable.json"
    for _ in range(20):
        request = make_request(rng, durable)
        args = call_args(durable, request)
        out = _outcome(PICKER(_fresh(), request), *args)
        absorb(durable, request, args, out)
    _save(path, durable)
    good = path.read_text()
    for cut in (0, 1, len(good) // 2, len(good) - 1):
        (tmp_path / "torn.json").write_text(good[:cut])
        with pytest.raises(json.JSONDecodeError):
            json.loads((tmp_path / "torn.json").read_text())
    assert path.read_text() == good
    request = make_request(rng, _load(path))
    args = call_args(_load(path), request)
    assert _outcome(PICKER(_fresh(), request), *copy.deepcopy(args)) == _outcome(
        PICKER(REF, request), *copy.deepcopy(args)
    )


def _agree_with_oracle(request):
    out = _outcome(_fresh(), request)
    assert out == _outcome(REF, copy.deepcopy(request)), request
    return out


def test_every_policy_bound_edge_survives_a_restart_like_the_oracle():
    edges = (
        -1,
        0,
        1,
        2,
        9,
        10,
        11,
        1000,
        3_599_999,
        3_600_000,
        3_600_001,
        86_399_999,
        86_400_000,
        86_400_001,
        MAX,
    )
    ok = 0
    for base in edges:
        for multiplier in edges[:8]:
            for max_delay in (0, 1, base - 1, base, base + 1, 86_400_000, 86_400_001):
                request = {
                    "op": "decide",
                    "job_id": JOB,
                    "attempts": 2,
                    "failure": "transient",
                    "now": 5,
                    "policy": {
                        "base_delay_ms": base,
                        "multiplier": multiplier,
                        "max_delay_ms": max_delay,
                    },
                }
                ok += _agree_with_oracle(request)[0] == "ok"
    assert ok > 0


def test_non_mapping_wrong_key_and_subclass_requests_and_policies_match_the_oracle():
    class D(dict):
        pass

    class S(str):
        pass

    base = {
        "op": "decide",
        "job_id": JOB,
        "attempts": 2,
        "failure": "transient",
        "now": 5,
        "policy": {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100},
    }
    for bad in (None, [], (), "x", 1, True, D(base), [base], {}):
        _agree_with_oracle(bad)
    for bad_policy in (
        None,
        [],
        (),
        "x",
        1,
        {},
        D(base["policy"]),
        {"base_delay_ms": 1, "multiplier": 2},
        {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100, "x": 1},
        {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100, 1: 1},
        {"base_delay_ms": 1, "multiplier": 2, "other": 100},
    ):
        request = copy.deepcopy(base)
        request["policy"] = bad_policy
        assert _agree_with_oracle(request)[0] == "err"
    keyed = copy.deepcopy(base)
    keyed[S("op")] = keyed.pop("op")
    assert _agree_with_oracle(keyed)[0] == "err"
    keyed = copy.deepcopy(base)
    keyed["policy"][S("multiplier")] = keyed["policy"].pop("multiplier")
    assert _agree_with_oracle(keyed)[0] == "err"
