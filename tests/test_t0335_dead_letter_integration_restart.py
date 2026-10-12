"""T0335: integration and restart proof for the production jobs dead letter engine.

Burying a dead job appends one entry to the dead-letter log, so the durable
value is the log of entry ids the host persisted; a restart must reproduce
the same entry for the same job and never a new one.

A restart is modelled the way a host would do it: the durable value is written
as canonical JSON under tmp_path (atomic replace), the production module is
executed anew in its own namespace (the canonical
module is never reloaded), the file is read back and the stream
continues. A few cut points run in a real child interpreter. Sampled cut points
(12 every-step streams, 10 single random cuts, child-interpreter steps)
must give the same outcomes and the same durable value as an
uninterrupted run and as the contract's reference implementation
(`tests.test_t0329_dead_letter_contract`),
which is the differential oracle.
Altered job and request fields are refused with the oracle class and code
and leave the input unchanged; the dead-letter log is the only durable
value.
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

import server.jobs_dead_letter as PMOD
from tests.test_t0329_dead_letter_contract import bury as REF

MAX = 2**53 - 1
FN = "bury"
OP = "bury"
IDS = ("entry_id",)


def _job(i, status="dead", attempts=5, payload=None):
    return {
        "job_id": "job1:" + format(i, "064x"),
        "seq": i,
        "priority": i % 10,
        "payload": {"i": i, "t": ["é", None, 1.5]} if payload is None else payload,
        "status": status,
        "attempts": attempts,
        "lease_owner": None,
        "lease_expires_at": None,
    }


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
    return {
        "op": "bury",
        "job": job,
        "reason": rng.choice(("exhausted", "permanent")),
        "now": durable["now"],
    }


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
    "import json, sys\n"
    "from server.jobs_dead_letter import bury as F\n"
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


def _check(request):
    out = _outcome(_fresh(), request)
    assert out == _outcome(REF, copy.deepcopy(request)), request
    return out


def _djob(i=1, payload=None, status="dead", attempts=5, owner=None, expires=None):
    job = _job(
        i, status=status, attempts=attempts, payload={"a": 1} if payload is None else payload
    )
    job["lease_owner"], job["lease_expires_at"] = owner, expires
    return job


def _breq(job, reason="exhausted", now=7):
    return {"op": "bury", "job": job, "reason": reason, "now": now}


def test_every_status_attempt_and_lease_combination_survives_a_restart_like_the_oracle():
    leases = [
        (None, None),
        ("w1", 1),
        ("w1", 0),
        ("w1", MAX),
        ("w1", MAX + 1),
        ("", 5),
        (None, 5),
        ("w1", None),
    ]
    for status in ("ready", "leased", "done", "dead", "other"):
        for attempts in range(-1, 8):
            for owner, expires in leases:
                _check(_breq(_djob(3, None, status, attempts, owner, expires)))


def test_every_request_field_edge_survives_a_restart_like_the_oracle():
    accepted = refused = 0
    values = {
        "op": ("bury", "other", "", None, 1, True),
        "reason": ("exhausted", "permanent", "other", "", "exhausted\n", None, 1, True),
        "now": (-1, 0, 1, MAX - 1, MAX, MAX + 1, 2**54, True, 1.5, "1", None),
        "job": (None, [], (), "x", 0, True, {}),
    }
    for field, vals in values.items():
        for value in vals:
            request = _breq(_djob())
            request[field] = value
            out = _check(request)
            accepted += out[0] == "ok"
            refused += out[0] == "err"
    for field in ("op", "job", "reason", "now"):
        request = _breq(_djob())
        del request[field]
        assert _check(request)[0] == "err"
    request = _breq(_djob())
    request["extra"] = 1
    assert _check(request)[0] == "err"
    assert accepted and refused


def test_every_job_field_edge_survives_a_restart_like_the_oracle():
    values = {
        "job_id": (
            "",
            "job1:",
            "job1:" + "A" * 64,
            "job1:" + "a" * 63,
            "job1:" + "0" * 64 + "\n",
            None,
            1,
        ),
        "seq": (-1, 0, 1, 2, MAX - 1, MAX, MAX + 1, True, 1.5, "1", None),
        "priority": (-1, 0, 1, 8, 9, 10, 11, True, 1.5, "1", None),
        "status": ("dead", "ready", "leased", "done", "", "DEAD", "dead\n", None, 1),
        "attempts": (-1, 0, 1, 4, 5, 6, True, 1.5, "5", None),
        "lease_owner": ("w1", "", "w\n", "a" * 64, "a" * 65, 1, True),
        "lease_expires_at": (-1, 0, 1, MAX, MAX + 1, True, 1.5, "1"),
    }
    refused = 0
    for field, vals in values.items():
        for value in vals:
            job = _djob(1)
            job[field] = value
            refused += _check(_breq(job))[0] == "err"
        job = _djob(1)
        del job[field]
        assert _check(_breq(job))[0] == "err"
    job = _djob(1)
    job["extra"] = 1
    assert _check(_breq(job))[0] == "err"
    assert refused > 0


def test_exact_bit_and_digit_edges_survive_a_restart_like_the_oracle():
    edges = [
        10**4000 - 1,
        -(10**4000 - 1),
        10**4000,
        -(10**4000),
        2**13287,
        2**13288,
        2**13289,
        -(2**13287),
        -(2**13288),
        2**13287 - 1,
        10**3999,
        10**3999 + 1,
    ]
    for value in edges:
        for wrap in (lambda v: v, lambda v: [v], lambda v: {"k": v}):
            out = _check(_breq(_djob(1, wrap(value), "dead", 5)))
            digits = len(str(abs(value))) if abs(value) < 10**4010 else 9999
            assert (out[0] == "ok") == (digits <= 4000)


def test_exact_nesting_depth_edges_survive_a_restart_like_the_oracle():
    for depth in range(58, 71):
        for wrap in (lambda v: [v], lambda v: {"k": v}):
            value = 1
            for _ in range(depth):
                value = wrap(value)
            _check(_breq(_djob(1, value)))


def test_payload_value_kinds_survive_a_restart_like_the_oracle():
    class D(dict):
        pass

    class L(list):
        pass

    for payload in (
        None,
        True,
        0,
        1.5,
        float("inf"),
        float("nan"),
        "é",
        "",
        b"x",
        (1,),
        {1, 2},
        [],
        {},
        [None, [1, {"a": [2.5]}]],
        {"a": {"b": None}},
        {1: 2},
        {None: 1},
        D(a=1),
        L([1]),
        object(),
        1 + 2j,
        "\ud800",
        {"k": float("inf")},
    ):
        _check(_breq(_djob(1, payload)))
    shared = [1]
    _check(_breq(_djob(1, [shared, shared])))
    cyc = []
    cyc.append(cyc)
    _check(_breq(_djob(1, cyc)))


def test_a_dict_subclass_request_or_job_is_refused_like_the_oracle():
    class D(dict):
        pass

    assert _check(D(_breq(_djob())))[0] == "err"
    assert _check(_breq(D(_djob())))[0] == "err"


def test_payload_dict_keys_that_are_str_subclasses_are_refused_like_the_oracle():
    plain = {"a": 1}
    for payload in (
        {_SStr("a"): 1},
        {"a": {_SStr("b"): 1}},
        {"a": [{"b": {_SStr("c"): None}}]},
        [{_SStr("a"): 1}],
        {"a": {"b": 1}, _SStr("c"): 2},
    ):
        out = _check(_breq(_djob(2, payload)))
        assert out[0] == "err"
    assert _check(_breq(_djob(2, plain)))[0] == "ok"
