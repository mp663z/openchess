"""T0344: integration and restart proof for the production jobs limit engine.

A token bucket chains by the previous bucket record, so the durable value is
the last good record; a restart continues the bucket from the file.

A restart is modelled the way a host would do it: the durable value is written
as canonical JSON under tmp_path (atomic replace), the production module is
imported anew (`importlib.reload`), the file is read back and the stream
continues. A few cut points run in a real child interpreter. Every cut point
of a seeded stream must give the same outcomes and the same durable value as an
uninterrupted run and as the contract's reference implementation
(`tests.test_t0338_limit_contract`),
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

import server.jobs_limit as PMOD
from tests.test_t0338_limit_contract import admit as REF

MAX = 2**53 - 1
FN = "admit"
OP = "admit"
IDS = ("limit_id",)


def new_durable():
    return {"prev": None, "now": 0, "policy": None, "n": 0}


def make_request(rng, durable):
    if durable["policy"] is None or rng.random() < 0.04:
        durable.update(
            policy=rng.choice(
                (
                    {"capacity": 3, "refill_ms": 1000},
                    {"capacity": 1, "refill_ms": 1},
                    {"capacity": 5, "refill_ms": 86_400_000},
                )
            ),
            prev=None,
            n=durable["n"] + 1,
        )
        durable["now"] = rng.choice((0, 5, MAX - 10**7))
    policy = durable["policy"]
    durable["now"] += rng.choice((0, 0, 1, policy["refill_ms"] // 2, policy["refill_ms"], 10**6))
    durable["now"] = min(durable["now"], MAX)
    cost = rng.choice((1, 1, 1, 2, policy["capacity"], policy["capacity"] + 1))
    return {
        "op": "admit",
        "key": f"k-{durable['n']}",
        "cost": cost,
        "now": durable["now"],
        "policy": copy.deepcopy(policy),
        "previous": copy.deepcopy(durable["prev"]),
    }


def call_args(durable, request):
    return [request]


def PICKER(fn, request):
    return fn


def CHILD_OK(request):
    return True


def absorb(durable, request, args, out):
    if out[0] == "ok":
        durable["prev"] = out[1]


def torn_variants(durable):
    request = make_request(random.Random(3), copy.deepcopy(durable))
    prev = request["previous"]
    yield "valid", [copy.deepcopy(request)]
    if prev is None:
        return
    for key in list(prev):
        bad = copy.deepcopy(request)
        del bad["previous"][key]
        yield f"previous drop {key}", [bad]
        for value in (None, -1, 0, 1, True, "x", 1.5, MAX + 1, "lim1:" + "0" * 64):
            bad = copy.deepcopy(request)
            bad["previous"][key] = value
            yield f"previous {key}={value!r}", [bad]
    for key in ("capacity", "refill_ms"):
        bad = copy.deepcopy(request)
        bad["policy"][key] += 1
        yield f"policy {key} changed", [bad]
    bad = copy.deepcopy(request)
    bad["now"] = max(0, prev["updated_at"] - 1)
    yield "clock before the previous record", [bad]


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
    "from server.jobs_limit import admit as F\n"
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
