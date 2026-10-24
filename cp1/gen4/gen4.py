import sys
HDR = '''"""{task}: integration and restart proof for the production jobs {name} engine.

{blurb}

A restart is modelled the way a host would do it: the durable value is written
as canonical JSON under tmp_path (atomic replace), the production module is
imported anew (`importlib.reload`), the file is read back and the stream
continues. A few cut points run in a real child interpreter. Sampled cut points
(12 every-step streams, 10 single random cuts, child-interpreter steps)
must give the same outcomes and the same durable value as an
uninterrupted run and as the contract's reference implementation
(`tests.{ref}`),
which is the differential oracle. {forged}
Single process tree, no real clock, no concurrency, no production changes.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
import random
import subprocess
import sys

import pytest

import server.{mod} as PMOD
{extra_imp}from tests.{ref} import {fn} as REF

MAX = 2**53 - 1
FN = "{fn}"
OP = "{op}"
IDS = {ids}

'''

CORE = '''

_FRESH_COUNT = [0]


def _fresh_module():
    """A restarted process: the production file is executed anew in its own namespace.

    The canonical module in sys.modules is never reloaded or mutated, so other tests that
    hold its functions and error classes are unaffected.
    """
    _FRESH_COUNT[0] += 1
    name = f"_restart_copy_{_FRESH_COUNT[0]}_{PMOD.__name__.rsplit('.', 1)[-1]}"
    spec = importlib.util.spec_from_file_location(name, PMOD.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fresh():
    return getattr(_fresh_module(), FN)


def test_a_restart_never_touches_the_canonical_production_module():
    canonical = sys.modules[PMOD.__name__]
    before = {k: id(v) for k, v in vars(PMOD).items()}
    count = len(sys.modules)
    fn = _fresh()
    again = _fresh_module()
    assert sys.modules[PMOD.__name__] is canonical is PMOD
    assert {k: id(v) for k, v in vars(PMOD).items()} == before
    assert len(sys.modules) == count
    assert getattr(again, FN) is not getattr(PMOD, FN) and fn is not getattr(PMOD, FN)
    for name, value in vars(PMOD).items():
        if isinstance(value, type) and issubclass(value, Exception) and name.endswith("Error"):
            assert getattr(again, name) is not value
            assert vars(PMOD)[name] is value


def _outcome(fn, *args):
    try:
        return ("ok", fn(*args))
    except Exception as error:  # noqa: BLE001
        failure = getattr(error, "failure_class", None)
        assert failure is not None, f"FOREIGN-EXCEPTION {error!r}"
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
    "import json, sys\\n"
    "from server.{mod} import {fn} as F\\n"
    "args = json.load(sys.stdin)\\n"
    "try:\\n"
    "    out = ['ok', F(*args)]\\n"
    "except Exception as e:\\n"
    "    out = ['err', e.failure_class, e.code]\\n"
    "print(json.dumps([out, args]))\\n"
)


def _child(args):
    """One step in a brand-new interpreter, the way a restarted host would."""
    done = subprocess.run(
        [sys.executable, "-c", _CHILD.replace("\\\\n", "\\n")],
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



class _SStr(str):
    """A hook-free str subclass: only its exact type is wrong."""


class _SInt(int):
    pass


class _SFloat(float):
    pass


class _SDict(dict):
    pass


class _SList(list):
    pass


def _walk(node, prefix=()):
    """Iterative path walk over dict values, dict keys and list items."""
    out, stack = [], [(node, prefix)]
    while stack:
        cur, path = stack.pop()
        out.append(path)
        if type(cur) is dict:
            for k, v in cur.items():
                out.append((*path, ("key", k)))
                stack.append((v, (*path, k)))
        elif type(cur) is list:
            for i, v in enumerate(cur):
                stack.append((v, (*path, i)))
    return out


def _subclassed(args, path):
    new = copy.deepcopy(args)
    if path and type(path[-1]) is tuple:
        parent = new
        for step in path[:-1]:
            parent = parent[step]
        key = path[-1][1]
        value = parent.pop(key)
        parent[_SStr(key)] = value
        return new
    if not path:
        return None
    parent = new
    for step in path[:-1]:
        parent = parent[step]
    value = parent[path[-1]]
    cls = {str: _SStr, int: _SInt, float: _SFloat, dict: _SDict, list: _SList}.get(type(value))
    if cls is None:
        return None
    parent[path[-1]] = cls(value)
    return new


def _sample_args(count=8):
    rng, durable, out = random.Random(5), new_durable(), []
    for _ in range(80):
        request = make_request(rng, durable)
        args = call_args(durable, request)
        if CHILD_OK(request):
            out.append((request, copy.deepcopy(args)))
        res = _outcome(PICKER(_fresh(), request), *args)
        absorb(durable, request, args, res)
    return out[:: max(1, len(out) // count)][:count]


def test_hook_free_subclasses_at_every_path_are_refused_like_the_oracle():
    checked = refused = 0
    for request, args in _sample_args():
        for path in _walk(args):
            bad = _subclassed(args, path)
            if bad is None:
                continue
            got = _outcome(PICKER(_fresh(), request), *copy.deepcopy(bad))
            want = _outcome(PICKER(REF, request), *copy.deepcopy(bad))
            assert got == want, (path, got, want)
            checked += 1
            refused += got[0] == "err"
    assert checked > 50 and refused > 0
'''

# ---------------------------------------------------------------- adapters
ADAPT = {}

ADAPT['retry'] = ('''
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
        for now in (0, 1, MAX - 86_400_000, MAX - 86_400_000 + 1, MAX - 1000, MAX - 999, MAX - 1, MAX):
            for failure in ("transient", "timeout", "permanent"):
                request = {"op": "decide", "job_id": JOB, "attempts": attempts, "failure": failure,
                           "now": now, "policy": copy.deepcopy(policy)}
                assert _outcome(_fresh(), request) == _outcome(REF, copy.deepcopy(request))
''', "('decision_id',)", "A retry decision depends on nothing but its request, so the durable value of a job is its attempt count, its clock and its policy; the host persists the decision log between runs.", "")

ADAPT['progress'] = ('''
JOB = "job1:" + "ab" * 32


def new_durable():
    return {"prev": None, "done": 0, "now": 0, "total": 10, "worker": "w-1"}


def make_request(rng, durable):
    if rng.random() < 0.05 and durable["prev"] is not None:
        durable.update(prev=None, done=0, total=rng.choice((1, 10, 100000)), worker=rng.choice(("w-1", "w:2")))
    kind = rng.random()
    done = min(durable["total"], durable["done"] + rng.choice((0, 1, 3)))
    worker = durable["worker"]
    if kind < 0.10:
        done = max(0, durable["done"] - 1)  # a regression: a conflict once something was reported
    elif kind < 0.15:
        worker = "other"
    durable["now"] += rng.choice((0, 1, 10))
    return {"op": "report", "job_id": JOB, "worker": worker, "done": done, "total": durable["total"],
            "now": durable["now"], "previous": copy.deepcopy(durable["prev"])}


def call_args(durable, request):
    return [request]


def PICKER(fn, request):
    return fn


def CHILD_OK(request):
    return True


def absorb(durable, request, args, out):
    if out[0] == "ok":
        durable["prev"] = out[1]
        durable["done"] = out[1]["done"]


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
        for value in (None, -1, 0, True, "x", 1.5, MAX + 1, "progress1:" + "0" * 64):
            bad = copy.deepcopy(request)
            bad["previous"][key] = value
            yield f"previous {key}={value!r}", [bad]
    bad = copy.deepcopy(request)
    bad["previous"]["extra"] = 1
    yield "previous extra key", [bad]
    for key in ("job_id", "total", "worker"):
        bad = copy.deepcopy(request)
        bad[key] = {"job_id": "job1:" + "cd" * 32, "total": request["total"] + 1, "worker": "z"}[key]
        yield f"request {key} changed", [bad]
''', "('progress_id',)", "Progress reports chain by the previous record, so the durable value is the last good record; a restart continues the chain from the file.", "")

ADAPT['limit'] = ('''
def new_durable():
    return {"prev": None, "now": 0, "policy": None, "n": 0}


def make_request(rng, durable):
    if durable["policy"] is None or rng.random() < 0.04:
        durable.update(policy=rng.choice(({"capacity": 3, "refill_ms": 1000}, {"capacity": 1, "refill_ms": 1},
                                          {"capacity": 5, "refill_ms": 86_400_000})), prev=None, n=durable["n"] + 1)
        durable["now"] = rng.choice((0, 5, MAX - 10**7))
    policy = durable["policy"]
    durable["now"] += rng.choice((0, 0, 1, policy["refill_ms"] // 2, policy["refill_ms"], 10**6))
    durable["now"] = min(durable["now"], MAX)
    cost = rng.choice((1, 1, 1, 2, policy["capacity"], policy["capacity"] + 1))
    return {"op": "admit", "key": f"k-{durable['n']}", "cost": cost, "now": durable["now"],
            "policy": copy.deepcopy(policy), "previous": copy.deepcopy(durable["prev"])}


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
''', "('limit_id',)", "A token bucket chains by the previous bucket record, so the durable value is the last good record; a restart continues the bucket from the file.", "")

ADAPT['dead_letter'] = ('''
def _job(i, status="dead", attempts=5, payload=None):
    return {"job_id": "job1:" + format(i, "064x"), "seq": i, "priority": i % 10,
            "payload": {"i": i, "t": ["é", None, 1.5]} if payload is None else payload,
            "status": status,
            "attempts": attempts, "lease_owner": None, "lease_expires_at": None}


def new_durable():
    return {"i": 0, "now": 0, "log": []}


def make_request(rng, durable):
    durable["i"] += rng.choice((0, 1, 1))
    durable["now"] = rng.choice((0, 1, 10**9, MAX))
    kind = rng.random()
    job = _job(durable["i"])
    if kind < 0.1:
        job = _job(durable["i"], status="ready", attempts=0)
    elif kind < 0.2:
        job = _job(durable["i"], status="leased", attempts=2)
        job["lease_owner"], job["lease_expires_at"] = "w1", 100
    elif kind < 0.3:
        job["seq"] = -1
    return {"op": "bury", "job": job, "reason": rng.choice(("exhausted", "permanent")), "now": durable["now"]}


def call_args(durable, request):
    return [request]


def PICKER(fn, request):
    return fn


def CHILD_OK(request):
    return True


def absorb(durable, request, args, out):
    if out[0] == "ok":
        durable["log"].append(out[1]["entry_id"])


def torn_variants(durable):
    request = make_request(random.Random(3), copy.deepcopy(durable))
    request["job"] = _job(durable["i"] + 1)
    yield "valid", [copy.deepcopy(request)]
    for key in list(request["job"]):
        bad = copy.deepcopy(request)
        del bad["job"][key]
        yield f"job drop {key}", [bad]
        for value in (None, -1, 0, True, "x", 1.5, MAX + 1, []):
            bad = copy.deepcopy(request)
            bad["job"][key] = value
            yield f"job {key}={value!r}", [bad]
    for key in list(request):
        bad = copy.deepcopy(request)
        del bad[key]
        yield f"request drop {key}", [bad]
    for status in ("ready", "leased", "done", "other"):
        bad = copy.deepcopy(request)
        bad["job"]["status"] = status
        yield f"status {status}", [bad]
''', "('entry_id',)", "Burying a dead job appends one entry to the dead-letter log, so the durable value is the log of entry ids the host persisted; a restart must reproduce the same entry for the same job and never a new one.", "")

ADAPT['restart'] = ('''

def new_durable():
    return {"state": {"jobs": [], "next_seq": 0}, "now": 0, "log": []}


def make_request(rng, durable):
    durable["now"] += rng.choice((0, 1, 20, 150))
    now, pick = durable["now"], rng.random()
    if pick < 0.30:
        return {"op": "enqueue", "dedupe_key": f"k{rng.randrange(6)}", "priority": rng.randrange(3),
                "payload": {"n": rng.randrange(2)}}
    if pick < 0.55:
        return {"op": "claim", "worker": rng.choice(("w1", "w2")), "now": now, "lease_ms": rng.choice((10, 100))}
    if pick < 0.70:
        return {"op": rng.choice(("ack", "nack")), "worker": rng.choice(("w1", "w2")), "now": now,
                "job_id": job_id_for(f"k{rng.randrange(6)}")}
    return {"op": "restart", "worker": rng.choice(("w1", "w2", "w3")), "now": now}


def call_args(durable, request):
    return [durable["state"], request]


def PICKER(fn, request):
    if request["op"] == "restart":
        return fn
    return lambda state, req: QueueEngine().apply(state, req)


def CHILD_OK(request):
    return request["op"] == "restart"


def absorb(durable, request, args, out):
    durable["state"] = args[0]
    if out[0] == "ok" and request["op"] == "restart":
        durable["log"].append(out[1]["restart_id"])


def _live(state, worker, now):
    return [j for j in state["jobs"] if j["status"] == "leased" and j["lease_owner"] == worker and now < j["lease_expires_at"]]


def torn_variants(durable):
    request = {"op": "restart", "worker": "w1", "now": 5}
    state = durable["state"]
    yield "valid", [copy.deepcopy(state), dict(request)]
    for key in list(state):
        bad = copy.deepcopy(state)
        del bad[key]
        yield f"state drop {key}", [bad, dict(request)]
    for i, job in enumerate(state["jobs"]):
        for key in list(job):
            bad = copy.deepcopy(state)
            del bad["jobs"][i][key]
            yield f"job {i} drop {key}", [bad, dict(request)]
            for value in (None, -1, 0, 6, True, "x", 1.5, MAX + 1, []):
                bad = copy.deepcopy(state)
                bad["jobs"][i][key] = value
                yield f"job {i} {key}={value!r}", [bad, dict(request)]
    for value in (-1, 0, 1, 10**4, MAX + 1, True):
        bad = copy.deepcopy(state)
        bad["next_seq"] = value
        yield f"next_seq={value!r}", [bad, dict(request)]
    for key in list(request):
        bad = dict(request)
        del bad[key]
        yield f"request drop {key}", [copy.deepcopy(state), bad]
    for now in (-1, MAX + 1, True, 1.0):
        yield f"now={now!r}", [copy.deepcopy(state), {**request, "now": now}]


def test_a_restart_releases_exactly_the_unexpired_leases_of_the_restarted_worker(tmp_path):
    for seed in range(8):
        rng, durable = random.Random(400 + seed), new_durable()
        for _ in range(40):
            request = make_request(rng, durable)
            args = call_args(durable, request)
            before = copy.deepcopy(args[0])
            out = _outcome(PICKER(_fresh(), request), *args)
            absorb(durable, request, args, out)
            if request["op"] != "restart" or out[0] != "ok":
                continue
            want = [j["job_id"] for j in _live(before, request["worker"], request["now"])]
            assert out[1]["released"] == want
            assert out[1]["prior_state_id"] != out[1]["state_id"] or not want
            for job in durable["state"]["jobs"]:
                assert not (job["status"] == "leased" and job["job_id"] in want)
            again = _outcome(_fresh(), copy.deepcopy(durable["state"]), dict(request))
            assert again[0] == "ok" and again[1]["released"] == []
''', "('state_id', 'restart_id')", "The engine holds no memory between calls: its whole state is the queue state `{jobs, next_seq}`. The restart operation releases the unexpired leases of a restarted worker, so each stream mixes queue operations (enqueue, claim, ack, nack through the production queue) with restarts, and the host persists the queue state between them.", "from server.jobs_queue import QueueEngine, job_id_for\n")

FORGED = {
 'retry': 'A retry request carries no previous record, so the forged and malformed inputs here are altered request fields (not persisted fields); the engine refuses them with the oracle class and code and leaves its input unchanged.',
 'progress': 'Altered fields of the persisted previous record (digest not recomputed) and altered request fields are refused with the oracle class and code and leave the input unchanged. A separate row forges each previous field with the digest recomputed by the reference.',
 'limit': 'Altered fields of the persisted previous bucket record (digest not recomputed) and altered request fields are refused with the oracle class and code and leave the input unchanged; digest-recomputed forgeries are not generated here.',
 'dead_letter': 'Altered job and request fields are refused with the oracle class and code and leave the input unchanged; the dead-letter log is the only durable value.',
 'restart': 'Altered fields of the persisted queue state and of the request are refused with the oracle class and code and leave the state unchanged. Only this battery mixes the real queue engine into its streams.',
}

OPS = {'retry':'decide','progress':'report','limit':'admit','dead_letter':'bury','restart':'restart'}


EXTRA = {}

EXTRA['retry'] = '''

def _agree_with_oracle(request):
    out = _outcome(_fresh(), request)
    assert out == _outcome(REF, copy.deepcopy(request)), request
    return out


def test_every_policy_bound_edge_survives_a_restart_like_the_oracle():
    edges = (-1, 0, 1, 2, 9, 10, 11, 1000, 3_599_999, 3_600_000, 3_600_001, 86_399_999, 86_400_000,
             86_400_001, MAX)
    ok = 0
    for base in edges:
        for multiplier in edges[:8]:
            for max_delay in (0, 1, base - 1, base, base + 1, 86_400_000, 86_400_001):
                request = {"op": "decide", "job_id": JOB, "attempts": 2, "failure": "transient",
                           "now": 5, "policy": {"base_delay_ms": base, "multiplier": multiplier,
                                              "max_delay_ms": max_delay}}
                ok += _agree_with_oracle(request)[0] == "ok"
    assert ok > 0


def test_dict_subclass_request_and_policy_are_refused_like_the_oracle():
    class D(dict):
        pass

    base = {"op": "decide", "job_id": JOB, "attempts": 2, "failure": "transient", "now": 5,
            "policy": {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100}}
    assert _agree_with_oracle(D(base))[0] == "err"
    request = copy.deepcopy(base)
    request["policy"] = D(base["policy"])
    assert _agree_with_oracle(request)[0] == "err"


def test_policy_with_all_fields_and_a_wrong_key_is_refused_like_the_oracle():
    base = {"op": "decide", "job_id": JOB, "attempts": 2, "failure": "transient", "now": 5,
            "policy": {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100}}
    for extra in ({"x": 1}, {1: 1}, {"": 0}, {None: 1}):
        request = copy.deepcopy(base)
        request["policy"].update(extra)
        assert _agree_with_oracle(request)[0] == "err", extra
    request = copy.deepcopy(base)
    request["extra"] = 1
    assert _agree_with_oracle(request)[0] == "err"
    request = copy.deepcopy(base)
    request["policy"]["other"] = request["policy"].pop("max_delay_ms")
    request["policy"]["max_delay_ms"] = 100
    assert _agree_with_oracle(request)[0] == "err"


def test_non_mapping_wrong_key_and_subclass_key_requests_and_policies_match_the_oracle():
    class S(str):
        pass

    base = {"op": "decide", "job_id": JOB, "attempts": 2, "failure": "transient", "now": 5,
            "policy": {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100}}
    for bad in (None, [], (), "x", 1, True, [base], {}):
        _agree_with_oracle(bad)
    for bad_policy in (None, [], (), "x", 1, {},
                       {"base_delay_ms": 1, "multiplier": 2},
                       {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100, "x": 1},
                       {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100, 1: 1},
                       {"base_delay_ms": 1, "multiplier": 2, "other": 100}):
        request = copy.deepcopy(base)
        request["policy"] = bad_policy
        assert _agree_with_oracle(request)[0] == "err"
    keyed = copy.deepcopy(base)
    keyed[S("op")] = keyed.pop("op")
    assert _agree_with_oracle(keyed)[0] == "err"
    keyed = copy.deepcopy(base)
    keyed["policy"][S("multiplier")] = keyed["policy"].pop("multiplier")
    assert _agree_with_oracle(keyed)[0] == "err"


def test_malformed_job_ids_are_refused_like_the_oracle():
    valid = JOB
    bad_ids = (
        "job1:" + "g" * 64, "job1:" + "a" * 63, "job1:" + "a" * 65, "job1:" + "A" * 64,
        valid + "\\n", "\\n" + valid, " " + valid, valid + " ", "job2:" + "ab" * 32, "JOB1:" + "ab" * 32,
        "job1" + "ab" * 32, "job1:", "", "x", None, 1, True, 1.5, [valid], b"job1:" + b"ab" * 32,
        "job1:" + "ab" * 31 + "é" + "a", valid[:-1] + "\\u0661",
    )
    for job_id in bad_ids:
        request = {"op": "decide", "job_id": job_id, "attempts": 2, "failure": "transient",
                   "now": 5, "policy": {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100}}
        assert _agree_with_oracle(request)[0] == "err", job_id
    good = {"op": "decide", "job_id": valid, "attempts": 2, "failure": "transient", "now": 5,
            "policy": {"base_delay_ms": 1, "multiplier": 2, "max_delay_ms": 100}}
    assert _agree_with_oracle(good)[0] == "ok"
    good["job_id"] = "job1:" + "0123456789abcdef" * 4
    assert _agree_with_oracle(good)[0] == "ok"
'''


EXTRA['progress'] = open('/tmp/gen4/extra_progress.txt').read()


EXTRA['dead_letter'] = open('/tmp/gen4/extra_dead.txt').read()


EXTRA['restart'] = open('/tmp/gen4/extra_restart.txt').read()


def build(task, name, mod, fn, ref, key):
    adapt, ids, blurb, extra = ADAPT[key]
    import textwrap
    blurb = textwrap.fill(blurb, 76)
    hdr = HDR.format(forged=textwrap.fill(FORGED[key], 76), op=OPS[key], task=task, name=name, blurb=blurb, ref=ref, mod=mod, fn=fn, ids=ids, extra_imp=extra)
    core = CORE.replace('{mod}', mod).replace('{fn}', fn)
    return hdr + adapt + core + EXTRA.get(key, '')

if __name__ == '__main__':
    pass

if __name__ == '__main__':
    import subprocess as sp
    TASKS = {
     'T0317': ('retry','jobs_retry','decide','test_t0311_retry_contract','retry','retry'),
     'T0326': ('progress','jobs_progress','report','test_t0320_progress_contract','progress','progress'),
     'T0335': ('dead letter','jobs_dead_letter','bury','test_t0329_dead_letter_contract','dead_letter','dead_letter'),
     'T0344': ('limit','jobs_limit','admit','test_t0338_limit_contract','limit','limit'),
     'T0353': ('restart','jobs_restart','restart','test_t0347_restart_contract','restart','restart'),
    }
    for task,(name,mod,fn,ref,fname,key) in TASKS.items():
        p=f'/home/sandbox/openchess/tests/test_{task.lower()}_{fname}_integration_restart.py'
        open(p,'w').write(build(task,name,mod,fn,ref,key)); print(p)
