"""T0353: integration and restart proof for the production jobs restart engine.

The engine holds no memory between calls: its whole state is the queue state
`{jobs, next_seq}`. The restart operation releases the unexpired leases of a
restarted worker, so each stream mixes queue operations (enqueue, claim,
ack, nack through the production queue) with restarts, and the host persists
the queue state between them.

A restart is modelled the way a host would do it: the durable value is written
as canonical JSON under tmp_path (atomic replace), the production module is
loaded anew into an isolated module namespace
(`importlib.util.spec_from_file_location`; a canonical production module is
never reloaded), the file is read back and the stream
continues. A few cut points run in a real child interpreter. Sampled cut
points
(12 every-step streams, 10 single random cuts, child-interpreter steps)
must give the same outcomes and the same durable value as an
uninterrupted run and as the contract's reference implementation
(`tests.test_t0347_restart_contract`),
which is the differential oracle.
Altered fields of the persisted queue state and of the request are refused
with the oracle class and code and leave the state unchanged. Only this
battery mixes the real queue engine into its streams.
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

import server.jobs_restart as PMOD
from server.jobs_queue import QueueEngine, job_id_for
from tests.test_t0347_restart_contract import restart as REF

MAX = 2**53 - 1
FN = "restart"
OP = "restart"
IDS = ("restart_id",)


def new_durable():
    return {"state": {"jobs": [], "next_seq": 0}, "now": 0, "log": []}


def make_request(rng, durable):
    durable["now"] += rng.choice((0, 1, 20, 150))
    now, pick = durable["now"], rng.random()
    if pick < 0.30:
        return {
            "op": "enqueue",
            "dedupe_key": f"k{rng.randrange(6)}",
            "priority": rng.randrange(3),
            "payload": {"n": rng.randrange(2)},
        }
    if pick < 0.55:
        return {
            "op": "claim",
            "worker": rng.choice(("w1", "w2")),
            "now": now,
            "lease_ms": rng.choice((10, 100)),
        }
    if pick < 0.70:
        return {
            "op": rng.choice(("ack", "nack")),
            "worker": rng.choice(("w1", "w2")),
            "now": now,
            "job_id": job_id_for(f"k{rng.randrange(6)}"),
        }
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
    return [
        j
        for j in state["jobs"]
        if j["status"] == "leased" and j["lease_owner"] == worker and now < j["lease_expires_at"]
    ]


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
    "from server.jobs_restart import restart as F\n"
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
    procs = frozenset(step for step, (request, _) in enumerate(want) if CHILD_OK(request))
    procs = frozenset(sorted(procs)[:4])
    assert len(procs) == 4  # four steps each run in a real child interpreter
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


def _agree(base, path=()):
    state, request = base["state"], base["request"]
    out = _outcome(_fresh(), copy.deepcopy(state), copy.deepcopy(request))
    ref = _outcome(REF, copy.deepcopy(state), copy.deepcopy(request))
    assert out == ref, (state, request, out, ref)
    return out


def _job(i, status="ready", attempts=0, owner=None, expires=None, payload=None):
    return {
        "job_id": job_id_for(f"k{i}"),
        "seq": i,
        "priority": i % 10,
        "payload": {"i": i} if payload is None else payload,
        "status": status,
        "attempts": attempts,
        "lease_owner": owner,
        "lease_expires_at": expires,
    }


def _one(state_jobs, worker="w1", now=7, next_seq=None):
    return {
        "state": {
            "jobs": state_jobs,
            "next_seq": len(state_jobs) if next_seq is None else next_seq,
        },
        "request": {"op": "restart", "worker": worker, "now": now},
    }


def test_every_status_attempt_and_lease_combination_matches_the_oracle():
    leases = [
        (None, None),
        ("w1", 1),
        ("w1", 7),
        ("w1", 8),
        ("w1", 0),
        ("w1", MAX),
        ("w1", MAX + 1),
        ("w2", 8),
        ("", 5),
        (None, 5),
        ("w1", None),
    ]
    for status in ("ready", "leased", "done", "dead", "other"):
        for attempts in range(-1, 8):
            for owner, expires in leases:
                base = _one([_job(0, status, attempts, owner, expires)])
                out = _agree(base, path=())
                if out[0] == "ok":
                    assert out[1]["restarted_at"] == 7


def test_now_around_every_expiry_edge_releases_exactly_the_unexpired_leases():
    for expiry in (1, 2, 50, MAX - 1, MAX):
        for now in {0, 1, 2, expiry - 1, expiry, expiry + 1, MAX - 1, MAX}:
            if not 0 <= now <= MAX:
                continue
            for worker in ("w1", "w2"):
                base = _one([_job(0, "leased", 1, "w1", expiry)], worker, now)
                out = _agree(base, path=())
                if out[0] == "ok":
                    released = out[1]["released"]
                    assert (released != []) == (worker == "w1" and now < expiry)


def test_seq_and_next_seq_edges_match_the_oracle():
    for seq in (-1, 0, 1, 2, 9, MAX):
        for next_seq in (-1, 0, 1, 2, 3, 10, MAX, MAX + 1):
            job = _job(0)
            job["seq"] = seq
            _agree(_one([job], next_seq=next_seq), path=())
    jobs = [_job(0), _job(1), _job(2)]
    jobs[0]["seq"], jobs[1]["seq"] = jobs[1]["seq"], jobs[0]["seq"]
    _agree(_one(jobs), path=())
    jobs = [_job(0), _job(1), _job(2)]
    jobs[2]["seq"] = 1
    _agree(_one(jobs), path=())
    jobs = [_job(0), _job(1), _job(2)]
    jobs[2]["job_id"] = jobs[0]["job_id"]
    _agree(_one(jobs), path=())


def test_valid_field_corruptions_match_the_oracle():
    for field, values in (
        ("priority", (-1, 0, 9, 10, 11, MAX, True)),
        ("attempts", (-1, 0, 5, 6, True)),
        ("status", ("READY", "", "leased ", None)),
        ("job_id", ("", "j", "J" * 64, None)),
        ("payload", (None, [], {}, "s", 1, 1.5, True)),
    ):
        for value in values:
            job = _job(0)
            job[field] = value
            _agree(_one([job]), path=())
    for n in (0, 1, 10000, 10001):
        jobs = [_job(i) for i in range(n)]
        _agree(_one(jobs, next_seq=n), path=())


def test_exact_bit_and_digit_edges_match_the_oracle():
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
            base = _one([_job(0, payload=wrap(value))])
            out = _agree(base, path=())
            digits = len(str(abs(value))) if abs(value) < 10**4010 else 9999
            assert (out[0] == "ok") == (digits <= 4000)


def test_exact_nesting_depth_edges_match_the_oracle():
    for depth in range(58, 71):
        for wrap in (lambda v: [v], lambda v: {"k": v}):
            value = 1
            for _ in range(depth):
                value = wrap(value)
            _agree(_one([_job(0, payload=value)]), path=())
            _agree(_one([_job(0, payload=value)], worker="w2"), path=())


def test_identifier_grammar_and_dict_subclasses_are_refused_like_the_oracle():
    class D(dict):
        pass

    for worker in ("w1\n", "", "a" * 65, "w w", "é", None, 1, True):
        _agree(_one([_job(0, "leased", 1, "w1", 100)], worker=worker), path=())
    base = _one([_job(0, "leased", 1, "w1", 100)])
    _agree({"state": D(base["state"]), "request": base["request"]}, path=())
    _agree({"state": base["state"], "request": D(base["request"])}, path=())
    job = _job(0, "leased", 1, "w1", 100)
    for owner in ("w1\n", "", "a" * 65, 1):
        bad = copy.deepcopy(job)
        bad["lease_owner"] = owner
        _agree(_one([bad]), path=())
    bad = copy.deepcopy(job)
    bad["job_id"] += "\n"
    _agree(_one([bad]), path=())


def _job_with_payload(payload):
    return {"job_id": job_id_for("k-reg"), "seq": 1, "priority": 1,
            "payload": payload, "status": "ready", "attempts": 0,
            "lease_owner": None, "lease_expires_at": None}


# --- L135: non-str scalar dict keys are refused (dict-key guard) ---
def test_payloads_with_non_str_scalar_keys_are_refused():
    for payload in ({5: "x"}, {1.5: "x"}, {True: "x"}, {None: "x"}, {"a": {1: 2}}):
        args = ({"jobs": [_job_with_payload(payload)], "next_seq": 2},
                {"op": "restart", "worker": "w1", "now": 5})
        out = _outcome(_fresh(), *args)
        ref = _outcome(REF, *copy.deepcopy(args))
        assert out == ref and out[0] == "err", (payload, out, ref)


# --- L113: lone-surrogate payload strings are refused (utf-8 validation) ---
def test_payloads_with_lone_surrogate_strings_are_refused():
    for bad in ("\ud800x", "x\udc00", "\ud800"):
        args = ({"jobs": [_job_with_payload({"s": bad})], "next_seq": 2},
                {"op": "restart", "worker": "w1", "now": 5})
        out = _outcome(_fresh(), *args)
        ref = _outcome(REF, *copy.deepcopy(args))
        assert out == ref and out[0] == "err", (repr(bad), out, ref)


# --- L203: queue-state id pins utf-8 canonicalization of payload content ---
def test_state_id_pinned_for_non_ascii_and_plus_payload_state():
    pins = (
        ("héllo", "qs1:0e66e276ab30ad8e39d0575135ec66231fe36e6e268d45ee04b9aeed4a08ced2"),
        ("a+b", "qs1:ece512e546a976b7b422a383d7031399b2e7320232195010fe7990eeca582b28"),
        ("abc", "qs1:860ab725bfe9c12d15e7ff2c2ab5d1fab465562a03fb8c5b9c94367214bad067"),
    )
    for text, want in pins:
        state = {"jobs": [{"job_id": "j1", "status": "queued", "attempts": 0,
                           "seq": 0, "payload": {"text": text}}]}
        assert PMOD._state_id(state) == want


# --- L74/L207: receipt canonical bytes and restart id pin ensure_ascii/utf-8 ---
def test_restart_id_pinned_for_non_ascii_and_plus_receipt_fields():
    pins = (
        ("héllo", "rst1:e5e50423dcd182dfb86dfcb62a539ffe606ccfa26e4c051ebc05d48b58489673"),
        ("a+b", "rst1:b0d997c3f6c06c86d8c642d371d17698005d967b863093cf7da99d51719f50a0"),
        ("abc", "rst1:414e59c4e5db1824b6ce80226089af5b21bfaf3baa7b28643c6015256cada6da"),
        ("\ud800", "rst1:2b119e9fcc60f013d9aafb3f1d9d6b10da8210c233a311850badd791fc19baa7"),
    )
    for note, want in pins:
        record = {"op": "restart", "worker": "w1", "note": note, "ts": 1}
        assert PMOD._restart_id(record) == want
    canon = PMOD._canon_receipt({"op": "restart", "worker": "w1", "note": "héllo", "ts": 1})
    assert canon == '{"note":"h\\u00e9llo","op":"restart","ts":1,"worker":"w1"}'
