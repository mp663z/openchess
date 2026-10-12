"""T0326: integration and restart proof for the production jobs progress engine.

Progress reports chain by the previous record, so the durable value is the
last good record; a restart continues the chain from the file.

A restart is modelled the way a host would do it: the durable value is written
as canonical JSON under tmp_path (atomic replace), the production module is
executed anew in its own namespace (the canonical
module is never reloaded), the file is read back and the stream
continues. A few cut points run in a real child interpreter. Sampled cut points
(12 every-step streams, 10 single random cuts, child-interpreter steps)
must give the same outcomes and the same durable value as an
uninterrupted run and as the contract's reference implementation
(`tests.test_t0320_progress_contract`),
which is the differential oracle.
Altered fields of the persisted previous record (digest not recomputed) and
altered request fields are refused with the oracle class and code and leave
the input unchanged. A separate row forges each previous field with the
digest recomputed by the reference.
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

import server.jobs_progress as PMOD
from tests.test_t0320_progress_contract import report as REF

MAX = 2**53 - 1
FN = "report"
OP = "report"
IDS = ("progress_id",)


JOB = "job1:" + "ab" * 32


def new_durable():
    return {"prev": None, "done": 0, "now": 0, "total": 10, "worker": "w-1"}


def make_request(rng, durable):
    if rng.random() < 0.05 and durable["prev"] is not None:
        durable.update(
            prev=None, done=0, total=rng.choice((1, 10, 100000)), worker=rng.choice(("w-1", "w:2"))
        )
    kind = rng.random()
    done = min(durable["total"], durable["done"] + rng.choice((0, 1, 3)))
    worker = durable["worker"]
    if kind < 0.10:
        done = max(0, durable["done"] - 1)  # a regression: a conflict once something was reported
    elif kind < 0.15:
        worker = "other"
    durable["now"] += rng.choice((0, 1, 10))
    return {
        "op": "report",
        "job_id": JOB,
        "worker": worker,
        "done": done,
        "total": durable["total"],
        "now": durable["now"],
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
        bad[key] = {"job_id": "job1:" + "cd" * 32, "total": request["total"] + 1, "worker": "z"}[
            key
        ]
        yield f"request {key} changed", [bad]


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
    "from server.jobs_progress import report as F\n"
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


from tests.test_t0320_progress_contract import _progress_id as _ref_progress_id  # noqa: E402

_JOB = "job1:" + "ab" * 32
_VALUES = {
    "op": ("report", "other", "", None, 1, True),
    "job_id": (
        "",
        "job1:" + "ab" * 32 + "\n",
        "job1:",
        "job1:" + "AB" * 32,
        "job1:" + "ab" * 31,
        "x",
        None,
        1,
        True,
    ),
    "worker": ("", "w-1\n", "a" * 64, "a" * 65, "w w", "w/", "é", None, 1, True),
    "total": (0, 1, 2, 10, MAX - 1, MAX, MAX + 1, MAX + 2, 2**54, -1, True, 1.5, "1", None),
    "done": (-1, 0, 1, 9, 10, 11, MAX, MAX + 1, True, 1.5, "1", None),
    "now": (-1, 0, 1, MAX - 1, MAX, MAX + 1, 2**54, True, 1.5, "1", None),
    "previous": (None, [], (), "x", 0, True, {}),
}


def _base_request(previous=None):
    return {
        "op": "report",
        "job_id": _JOB,
        "worker": "w-1",
        "done": 2,
        "total": 10,
        "now": 5,
        "previous": previous,
    }


def _first_record():
    return REF(_base_request())


def _check(request):
    out = _outcome(_fresh(), request)
    assert out == _outcome(REF, copy.deepcopy(request)), request
    return out


def test_every_request_field_edge_survives_a_restart_like_the_oracle():
    accepted = refused = 0
    for field, values in _VALUES.items():
        for value in values:
            request = _base_request()
            request[field] = value
            if field == "done" and value in (MAX, MAX + 1):
                request["total"] = MAX
            out = _check(request)
            accepted += out[0] == "ok"
            refused += out[0] == "err"
    assert accepted and refused
    for total in (1, MAX - 1, MAX):
        for done in (0, total, total - 1):
            for now in (0, MAX):
                request = _base_request()
                request.update(total=total, done=done, now=now)
                assert _check(request)[0] == "ok"
    for field in ("op", "job_id", "worker", "total", "done", "now", "previous"):
        request = _base_request()
        del request[field]
        assert _check(request)[0] == "err"
    for field in ("op", "job_id", "worker", "total", "done", "now"):
        request = _base_request(_first_record())
        request[field] = _VALUES[field][1] if field != "op" else "other"
        assert _check(request)[0] == "err"


def _forged(previous, **changes):
    bad = copy.deepcopy(previous)
    bad.update(changes)
    body = {k: bad[k] for k in bad if k != "progress_id"}
    bad["progress_id"] = _ref_progress_id(body)  # the digest is recomputed: only the field is wrong
    return bad


def test_forged_previous_records_with_a_recomputed_digest_survive_a_restart_like_the_oracle():
    previous = _first_record()
    refused = 0
    for field in (
        "op",
        "job_id",
        "worker",
        "done",
        "total",
        "percent_bp",
        "reported_at",
        "previous_id",
    ):
        for value in _VALUES.get(field, ()) + _VALUES.get("now", ()) + (4999, 5001, 2000, 2001):
            bad = _forged(previous, **{field: value})
            request = _base_request(bad)
            request["done"] = 5
            refused += _check(request)[0] == "err"
        bad = copy.deepcopy(previous)
        del bad[field]
        assert _check(_base_request(bad))[0] == "err"
    bad = copy.deepcopy(previous)
    bad["extra"] = 1
    assert _check(_base_request(bad))[0] == "err"
    for bad_id in ("pg1:" + "0" * 64, "pg1:" + "A" * 64, "pg1:" + "0" * 63, "x", None, 1):
        assert _check(_base_request({**previous, "progress_id": bad_id}))[0] == "err"
    assert refused > 0
    for field in ("previous_id",):
        for value in (None, "pg1:" + "0" * 64, "pg1:" + "A" * 64, "pg1:" + "0" * 65, "", 1, True):
            bad = _forged(previous, **{field: value})
            _check(_base_request(bad))
    chained = _base_request(previous)
    chained.update(done=3)
    second = _check(chained)[1]
    deeper = _base_request(second)
    deeper.update(done=4, now=6)
    assert _check(deeper)[0] == "ok"


def test_a_zero_total_is_refused_like_the_oracle():
    request = _base_request()
    request.update(total=0, done=0)
    assert _check(request)[0] == "err"
    previous = _first_record()
    request = _base_request(_forged(previous, total=0, done=0, percent_bp=0))
    assert _check(request)[0] == "err"


def test_trailing_newlines_in_identifiers_are_refused_like_the_oracle():
    previous = _first_record()
    request = _base_request()
    request["job_id"] = _JOB + "\n"
    assert _check(request)[0] == "err"
    request = _base_request()
    request["worker"] = "w-1\n"
    assert _check(request)[0] == "err"
    for field in ("job_id", "worker"):
        bad = _forged(previous, **{field: previous[field] + "\n"})
        assert _check(_base_request(bad))[0] == "err"
    bad = copy.deepcopy(previous)
    bad["progress_id"] += "\n"
    assert _check(_base_request(bad))[0] == "err"
    second = _check({**_base_request(previous), "done": 3})[1]
    bad = _forged(second, previous_id=previous["progress_id"] + "\n")
    assert _check(_base_request(bad))[0] == "err"
