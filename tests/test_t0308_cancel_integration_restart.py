"""T0308: cancel across process restarts.

Production `server.jobs_cancel.cancel` is a PURE callable: it persists
nothing and this battery claims no durability guarantee for it. What is
persisted here is the test harness's own stream of decision records (the
cd1-addressed receipts), written as canonical JSON under tmp_path with an
atomic replace. A "restart" executes the production source anew in an
isolated module namespace (the canonical module in sys.modules is never
reloaded or mutated; `test_a_restart_never_touches_the_canonical_production_module`
pins that) and continues the stream from the persisted log. Restarted and
uninterrupted streams must agree with each other and with the contract's
reference implementation (`tests.test_t0302_cancel_contract`), the
differential oracle. Replaying a seeded stream after restarts gives
identical outcomes, and decision ids are unique per distinct request.
Every call through the harness passes the actual request object and is
checked to leave that same object structurally identical (a
serialized-value snapshot before and after, not a hostile-identity
proof), accepted and refused alike;
`test_the_purity_snapshot_catches_an_outcome_preserving_scribble_on_both_paths`
pins that this snapshot is load-bearing, not incidental: a wrapper that
mutates its actual argument on the accepted path or the typed-refusal
path fails the harness. One disclosed exception: the Unicode preimage
row makes one direct production call outside `_outcome` on a
test-literal input. Forged requests (built from request fields only:
cancel carries no previous record) are refused with the oracle's class
and code, and the stream then continues: after the refusal window a
valid follow-up on the reloaded file succeeds, matches the oracle, and
advances the persisted log. A truncated durable file is a host-level
JSON error and never reaches the engine; the test restores the captured
last-good bytes by hand (no automatic recovery is claimed) and the
restored file stays usable. Decision-id uniqueness is checked over the
finite generated request domain of the replay seeds, not universally.
Seeded and single-threaded, so a failure replays.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import random
import subprocess
import sys

import pytest

import server.jobs_cancel as PMOD
from server.jobs_cancel import CancelError as PROD_ERR
from server.jobs_cancel import cancel as PROD
from tests.test_t0302_cancel_contract import CancelError as REF_ERR
from tests.test_t0302_cancel_contract import cancel as REF

MAX = 2**53 - 1
MAX_ATTEMPTS = 5
HEX = "0123456789abcdef"


def new_durable():
    return {"n": 0, "log": []}


def make_request(rng, durable):
    status = rng.choice(("ready", "ready", "leased"))
    attempts = rng.randint(1 if status == "leased" else 0, MAX_ATTEMPTS)
    return {
        "op": "cancel",
        "job": {
            "job_id": "job1:" + format(durable["n"], "064x"),
            "seq": rng.choice((0, 3, MAX - 1)),
            "priority": rng.choice((0, 4, 9)),
            "payload": {"n": durable["n"], "tags": ["a", "b"]},
            "status": status,
            "attempts": attempts,
            "lease_owner": "w-1" if status == "leased" else None,
            "lease_expires_at": rng.choice((1, 100, MAX)) if status == "leased" else None,
        },
        "requester": rng.choice(("owner-1", "w", "a" * 64)),
        "now": rng.choice((0, 1, 100, MAX)),
    }


def absorb(durable, request, out):
    if out[0] == "ok":
        durable["log"].append(copy.deepcopy(out[1]))  # the decision record itself
        durable["n"] += 1


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _save(path, durable):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_canon(durable))
    os.replace(tmp, path)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fresh_module():
    spec = importlib.util.spec_from_file_location("jobs_cancel_restarted", PMOD.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_restart_never_touches_the_canonical_production_module():
    before = PMOD.cancel
    fresh = _fresh_module()
    assert PMOD.cancel is before and fresh.cancel is not before
    assert "jobs_cancel_restarted" not in sys.modules
    assert sys.modules["server.jobs_cancel"] is PMOD


def _outcome(fn, request, err_cls):
    before = _isnap(request)
    try:
        out = ("ok", fn(request))
    except err_cls as error:
        assert type(error) is err_cls
        assert error.__cause__ is None and error.__context__ is None
        assert error.retryable is False
        out = ("fail", error.failure_class, error.code)
    assert _isnap(request) == before, "call changed its input"
    return out


def _isnap(node):
    out, seen, stack = [], {}, [node]
    while stack:
        n = stack.pop()
        t = type(n)
        if t is list or t is dict:
            if id(n) in seen:
                out.append(("ref", seen[id(n)]))
                continue
            seen[id(n)] = len(seen)
            items = list(dict.items(n)) if t is dict else list(list.__iter__(n))
            out.append((("d" if t is dict else "l"), len(items)))
            if t is dict:
                for k, v in reversed(items):
                    stack.append(v)
                    stack.append(k)
            else:
                stack.extend(reversed(items))
        elif t is int:
            out.append(("int", hex(n)))
        elif t is str or t is float or t is bool or n is None or t is bytes:
            out.append((t.__name__, repr(n)))
        else:
            out.append(("other", t.__name__))
    return out


def _typed(obj):
    kind = type(obj)
    if kind is dict:
        return ("dict", tuple(sorted((k, _typed(v)) for k, v in obj.items())))
    if kind is list:
        return ("list", tuple(_typed(v) for v in obj))
    if kind is tuple:
        return ("tuple", tuple(_typed(v) for v in obj))
    return (kind.__name__, obj)


def _drive(seed, restart_at, tmp_path, length=40):
    """A seeded stream; restarts reload the durable log into a fresh module."""
    rng = random.Random(seed)
    durable = new_durable()
    path = tmp_path / f"stream-{seed}.json"
    outcomes = []
    fn, err = PROD, PROD_ERR
    for step in range(length):
        if step in restart_at:
            path.parent.mkdir(parents=True, exist_ok=True)
            _save(path, durable)
            durable = _load(path)
            fresh = _fresh_module()
            fn, err = fresh.cancel, fresh.CancelError
        request = make_request(rng, durable)
        out = _outcome(fn, request, err)
        ref = _outcome(REF, copy.deepcopy(request), REF_ERR)
        assert _typed(out) == _typed(ref), (seed, step, out, ref)
        outcomes.append(out)
        absorb(durable, request, out)
    return outcomes, durable


@pytest.mark.parametrize("seed", range(12))
def test_restart_at_every_step_matches_an_uninterrupted_run_and_the_oracle(seed, tmp_path):
    restarted, rdurable = _drive(seed, set(range(40)), tmp_path)
    plain, pdurable = _drive(seed, set(), tmp_path / "plain")
    assert _typed(restarted) == _typed(plain)
    assert _typed(rdurable) == _typed(pdurable)


@pytest.mark.parametrize("seed", range(10))
def test_single_restart_at_a_random_cut_point(seed, tmp_path):
    cut = random.Random(10_000 + seed).randrange(40)
    restarted, _ = _drive(seed, {cut}, tmp_path)
    plain, _ = _drive(seed, set(), tmp_path / "plain")
    assert _typed(restarted) == _typed(plain)


CHILD = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("jobs_cancel_child", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
durable = json.loads(sys.argv[2])
request = json.loads(sys.argv[3])
before = json.dumps(request, sort_keys=True)
try:
    record = m.cancel(request)
    out = ("ok", record)
    durable["log"].append(record)
    durable["n"] += 1
except m.CancelError as e:
    out = ("fail", e.failure_class, e.code)
assert json.dumps(request, sort_keys=True) == before, "child input changed"
print(json.dumps((out, durable)))
"""


@pytest.mark.parametrize("seed", range(3))
def test_steps_in_a_real_child_interpreter_match_the_in_process_run(seed, tmp_path):
    rng = random.Random(seed)
    durable = new_durable()
    for _ in range(4):
        request = make_request(rng, durable)
        out = subprocess.run(
            [sys.executable, "-c", CHILD, PMOD.__file__, _canon(durable), _canon(request)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert out.returncode == 0, out.stderr
        child_out, child_durable = json.loads(out.stdout)
        prod = _outcome(PROD, request, PROD_ERR)
        assert _typed(tuple(child_out)) == _typed(prod)
        absorb(durable, request, prod)
        assert _typed(child_durable) == _typed(durable), "child durable diverged"


@pytest.mark.parametrize("seed", range(6))
def test_replaying_the_request_log_after_restarts_gives_identical_outcomes(seed, tmp_path):
    first, fdurable = _drive(seed, {5, 17, 29}, tmp_path)
    second, sdurable = _drive(seed, {5, 17, 29}, tmp_path / "again")
    assert _typed(first) == _typed(second)
    assert _typed(fdurable) == _typed(sdurable)
    ids = [out[1]["decision_id"] for out in first if out[0] == "ok"]
    assert len(set(ids)) == len(ids)


def torn_variants(request):
    yield "valid", request
    for key in list(request):
        bad = copy.deepcopy(request)
        del bad[key]
        yield f"drop {key}", bad
    for key in list(request["job"]):
        bad = copy.deepcopy(request)
        del bad["job"][key]
        yield f"drop job.{key}", bad
    for key, value in (
        ("seq", True),
        ("priority", "4"),
        ("attempts", 1.5),
        ("status", "unknown"),
        ("job_id", "job2:" + "ab" * 32),
    ):
        bad = copy.deepcopy(request)
        bad["job"][key] = value
        yield f"job.{key}={value!r}", bad
    bad = copy.deepcopy(request)
    bad["op"] = "cancel2"
    yield "op retype", bad
    bad = copy.deepcopy(request)
    bad["job"]["priority"] = bool(bad["job"]["priority"] in (1,))
    yield "priority bool retype", bad


def test_a_refused_step_changes_nothing_durable_and_the_stream_continues(tmp_path):
    rng = random.Random(7)
    durable = new_durable()
    path = tmp_path / "s.json"
    for _ in range(6):
        request = make_request(rng, durable)
        out = _outcome(PROD, request, PROD_ERR)
        absorb(durable, request, out)
    _save(path, durable)
    baseline = _load(path)
    request = make_request(rng, durable)
    for name, bad in torn_variants(request):
        out = _outcome(PROD, bad, PROD_ERR)
        ref = _outcome(REF, copy.deepcopy(bad), REF_ERR)
        assert _typed(out) == _typed(ref), (name, out, ref)
        if name != "valid":
            assert out[0] == "fail", (name, out)
        assert _load(path) == baseline, f"{name} changed the durable file"
    # a valid follow-up on the reloaded file must succeed and advance the stream
    loaded = _load(path)
    follow = make_request(rng, loaded)
    out = _outcome(PROD, follow, PROD_ERR)
    ref = _outcome(REF, copy.deepcopy(follow), REF_ERR)
    assert _typed(out) == _typed(ref) and out[0] == "ok", (out, ref)
    absorb(loaded, follow, out)
    _save(path, loaded)
    assert len(_load(path)["log"]) == len(baseline["log"]) + 1


def test_a_truncated_durable_file_is_a_host_error_and_the_last_good_value_still_works(tmp_path):
    path = tmp_path / "durable.json"
    durable = new_durable()
    rng = random.Random(11)
    for _ in range(3):
        request = make_request(rng, durable)
        absorb(durable, request, _outcome(PROD, request, PROD_ERR))
    _save(path, durable)
    good = path.read_bytes()
    path.write_bytes(good[: len(good) // 2])
    with pytest.raises(json.JSONDecodeError):
        _load(path)
    path.write_bytes(good)
    loaded = _load(path)
    assert _typed(loaded) == _typed(durable)
    request = make_request(rng, loaded)
    fresh = _fresh_module()
    out = _outcome(fresh.cancel, request, fresh.CancelError)
    assert out[0] == "ok"


def test_persisted_unicode_payload_record_round_trips_with_ascii_escaped_preimage(tmp_path):
    request = {
        "op": "cancel",
        "job": {
            "job_id": "job1:" + "ab" * 32,
            "seq": 3,
            "priority": 4,
            "payload": {"greeting": chr(0xE9)},
            "status": "ready",
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
        },
        "requester": "owner-1",
        "now": 100,
    }
    record = PROD(copy.deepcopy(request))
    assert record["payload_digest"] == (
        "pd1:ef5f00c1a46ab7d8fc2128206a3e23072a6632912de4a76ca7f553d7fd9c6031"
    )
    durable = {"n": 1, "log": [record]}
    path = tmp_path / "unicode.json"
    _save(path, durable)
    raw = path.read_text(encoding="utf-8")
    assert all(ord(c) < 128 for c in raw)  # persisted bytes are ascii-escaped
    assert _typed(_load(path)) == _typed(durable)


class _SStr(str):
    pass


class _SInt(int):
    pass


class _SDict(dict):
    pass


class _SList(list):
    pass


def _walk(node, prefix=()):
    yield prefix, node
    if type(node) is dict:
        for k, v in node.items():
            yield from _walk(v, prefix + (k,))
    elif type(node) is list:
        for i, v in enumerate(node):
            yield from _walk(v, prefix + (i,))


def test_the_purity_snapshot_catches_an_outcome_preserving_scribble_on_both_paths():
    valid = {
        "op": "cancel",
        "job": {
            "job_id": "job1:" + "ab" * 32,
            "seq": 3,
            "priority": 4,
            "payload": [],
            "status": "ready",
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
        },
        "requester": "owner-1",
        "now": 100,
    }

    def accepted_scribble(req):
        record = PROD(req)
        req["now"] += 1  # outcome-preserving mutation of the actual argument
        return record

    with pytest.raises(AssertionError, match="call changed its input"):
        _outcome(accepted_scribble, valid, PROD_ERR)

    malformed = {"op": "cancel", "requester": "owner-1", "now": 7}

    def refused_scribble(req):
        try:
            PROD(req)
        except PROD_ERR:
            req["now"] = 111  # mutation on the typed-refusal path
            raise

    with pytest.raises(AssertionError, match="call changed its input"):
        _outcome(refused_scribble, malformed, PROD_ERR)


def test_hook_free_subclasses_at_every_path_are_refused_like_the_oracle():
    rng = random.Random(23)
    durable = new_durable()
    request = make_request(rng, durable)
    swaps = {str: _SStr, int: _SInt, dict: _SDict, list: _SList}
    checked = 0
    for path, value in _walk(request):
        cls = swaps.get(type(value))
        if cls is None or type(value) is bool or not path:
            continue
        bad = copy.deepcopy(request)
        node = bad
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = cls(value)
        prod = _outcome(PROD, bad, PROD_ERR)
        ref = _outcome(REF, copy.deepcopy(bad), REF_ERR)
        assert _typed(prod) == _typed(ref), (path, prod, ref)
        checked += 1
    assert checked > 0


def test_every_bound_edge_survives_a_restart_like_the_oracle(tmp_path):
    fresh = _fresh_module()
    for status in ("ready", "leased", "done", "dead"):
        for attempts in (-1, 0, 1, MAX_ATTEMPTS, MAX_ATTEMPTS + 1):
            for now in (-1, 0, 1, MAX - 1, MAX):
                for seq in (-1, 0, MAX - 1, MAX):
                    for priority in (-1, 0, 9, 10):
                        for lease in (50, 100, 150) if status == "leased" else (None,):
                            request = {
                                "op": "cancel",
                                "job": {
                                    "job_id": "job1:" + "ab" * 32,
                                    "seq": seq,
                                    "priority": priority,
                                    "payload": [],
                                    "status": status,
                                    "attempts": attempts,
                                    "lease_owner": "w-1" if status == "leased" else None,
                                    "lease_expires_at": lease,
                                },
                                "requester": "owner-1",
                                "now": now,
                            }
                            prod = _outcome(fresh.cancel, request, fresh.CancelError)
                            ref = _outcome(REF, copy.deepcopy(request), REF_ERR)
                            assert _typed(prod) == _typed(ref), (request, prod, ref)
