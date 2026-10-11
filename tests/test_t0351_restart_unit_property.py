"""T0351: unit and seeded property checks of the production worker restart.

An independent model (plain predicates and a hand-built digest, no shared code
with server.jobs_restart) is driven with seeded random queue states and
requests, valid and defective. Every call must agree with the model on the
receipt, the new state or the failure class. A rejected call must leave state
and request unchanged and an accepted one must change only the unexpired
leases of the named worker. Unit rows pin the strict expiry boundary, the kept
attempts, the corrupt_queue battery, the payload walk, hostile values and a
lowered interpreter int-digit limit. Seeded, so a failure replays.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import sys

import pytest

from server.jobs_restart import RestartError, restart

MAX_NOW = 2**53 - 1
CODES = {"malformed_restart_request": "malformed_request", "corrupt_queue": "corrupt_queue"}
JOB_KEYS = [
    "job_id",
    "seq",
    "priority",
    "payload",
    "status",
    "attempts",
    "lease_owner",
    "lease_expires_at",
]
REQ_KEYS = ["op", "worker", "now"]
RECEIPT_ORDER = [
    "op",
    "worker",
    "restarted_at",
    "released",
    "prior_state_id",
    "state_id",
    "restart_id",
]
ALPHA = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")


def jid(n):
    return "job1:" + format(n, "064x")


def job(n=0, **kw):
    base = {
        "job_id": jid(n),
        "seq": n,
        "priority": 1,
        "payload": {"n": n},
        "status": "ready",
        "attempts": 0,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    base.update(kw)
    return base


def leased(n, owner="w1", expires=100, attempts=1, **kw):
    return job(
        n, status="leased", attempts=attempts, lease_owner=owner, lease_expires_at=expires, **kw
    )


def state(*jobs, next_seq=None):
    jobs = list(jobs)
    ns = next_seq if next_seq is not None else (max((j["seq"] for j in jobs), default=-1) + 1)
    return {"jobs": jobs, "next_seq": ns}


def req(**kw):
    base = {"op": "restart", "worker": "w1", "now": 50}
    base.update(kw)
    return base


class S(str):
    pass


class I(int):  # noqa: E742
    pass


class F(float):
    pass


class D(dict):
    pass


class L(list):
    pass


def _snap(obj):
    """Iterative, cycle-safe structural snapshot (no recursion, no caller code)."""
    out, seen, stack = [], {}, [obj]
    while stack:
        n = stack.pop()
        t = type(n)
        if t is list or t is dict:
            if id(n) in seen:
                out.append(("ref", seen[id(n)]))
                continue
            seen[id(n)] = len(seen)
            if t is dict:
                items = list(dict.items(n))
                out.append(("d", len(items)))
                for k, v in reversed(items):
                    stack.append(v)
                    stack.append(k)
            else:
                items = list(list.__iter__(n))
                out.append(("l", len(items)))
                stack.extend(reversed(items))
        elif t is int:
            out.append(("int", hex(n)))
        else:
            out.append((t.__name__, repr(n)))
    return repr(out)


def _int(v, lo, hi):
    return type(v) is int and lo <= v <= hi


def _scalar(v):
    if v is None or type(v) is bool:
        return True
    if type(v) is int:
        try:
            return len(str(abs(v))) <= 4000
        except ValueError:
            return False
    if type(v) is float:
        return math.isfinite(v)
    if type(v) is str:
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    return False


def _payload(root, seen):
    def walk(n, depth):
        if type(n) is list or type(n) is dict:
            if depth > 64 or id(n) in seen:
                return False
            seen.add(id(n))
            if type(n) is dict:
                return all(
                    type(k) is str and _scalar(k) and walk(v, depth + 1) for k, v in n.items()
                )
            return all(walk(v, depth + 1) for v in n)
        return _scalar(n)

    return walk(root, 1)


def _keys(m, names):
    return type(m) is dict and all(type(k) is str for k in m) and set(m) == set(names)


def _worker(w):
    return type(w) is str and 1 <= len(w) <= 64 and set(w) <= ALPHA


def _job_ok(j, prior, next_seq, seen):
    if not _keys(j, JOB_KEYS):
        return False
    st, at = j["status"], j["attempts"]
    o, e = j["lease_owner"], j["lease_expires_at"]
    jd = j["job_id"]
    if not (
        type(jd) is str
        and len(jd) == 69
        and jd.startswith("job1:")
        and all(c in "0123456789abcdef" for c in jd[5:])
        and _int(j["seq"], prior + 1, next_seq - 1)
        and _int(j["priority"], 0, 9)
        and type(st) is str
        and st in ("ready", "leased", "done", "dead")
        and _int(at, 0, 5)
    ):
        return False
    if st == "leased":
        if not (_worker(o) and _int(e, 1, MAX_NOW) and at >= 1):
            return False
    elif o is not None or e is not None:
        return False
    if st == "dead" and at != 5:
        return False
    if st == "done" and at < 1:
        return False
    return _payload(j["payload"], seen)


def _state_ok(s):
    if not _keys(s, ["jobs", "next_seq"]):
        return False
    if type(s["jobs"]) is not list or not _int(s["next_seq"], 0, MAX_NOW) or len(s["jobs"]) > 10000:
        return False
    ids, prior, seen = set(), -1, set()
    for j in s["jobs"]:
        if not _job_ok(j, prior, s["next_seq"], seen):
            return False
        if j["job_id"] in ids:
            return False
        ids.add(j["job_id"])
        prior = j["seq"]
    return True


def _qid(s):
    body = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "qs1:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def model(s, r):
    """('ok', receipt, new_state) or ('fail', class)."""
    if not (
        _keys(r, REQ_KEYS)
        and type(r["op"]) is str
        and r["op"] == "restart"
        and _worker(r["worker"])
        and _int(r["now"], 0, MAX_NOW)
    ):
        return ("fail", "malformed_restart_request")
    if not _state_ok(s):
        return ("fail", "corrupt_queue")
    new = json.loads(json.dumps(s, ensure_ascii=False))
    released = []
    for j in new["jobs"]:
        if (
            j["status"] == "leased"
            and j["lease_owner"] == r["worker"]
            and r["now"] < j["lease_expires_at"]
        ):
            j["status"], j["lease_owner"], j["lease_expires_at"] = "ready", None, None
            released.append(j["job_id"])
    rec = {
        "op": "restart",
        "worker": r["worker"],
        "restarted_at": r["now"],
        "released": released,
        "prior_state_id": _qid(s),
        "state_id": _qid(new),
    }
    body = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    rec["restart_id"] = "rst1:" + hashlib.sha256(b"rst1\x00" + body).hexdigest()
    return ("ok", rec, new)


def run(s, r):
    """Run restart; returns ('ok', receipt) or ('fail', class). Checks rollback."""
    sb, rb = _snap(s), _snap(r)
    try:
        out = ("ok", restart(s, r))
    except RestartError as error:
        assert type(error) is RestartError
        assert error.__cause__ is None and error.__context__ is None
        assert error.code == CODES[error.failure_class]
        assert error.retryable is False
        out = ("fail", error.failure_class)
        assert _snap(s) == sb, "state changed on rejection"
    assert _snap(r) == rb, "request was changed"
    return out


def check(s, r):
    """Model-compare on a pristine copy; returns the engine outcome."""
    want = model(s, r)
    got = run(s, r)
    assert got[0] == want[0]
    if want[0] == "fail":
        assert got == want
    else:
        assert got[1] == want[1]
        assert s == want[2]
        assert list(got[1]) == RECEIPT_ORDER
    return got


# ---------- happy ----------


def test_restart_releases_only_unexpired_leases_of_the_worker():
    s = state(
        leased(0, "w1", 100, attempts=2),
        leased(1, "w2", 100),
        job(2),
        job(3, status="done", attempts=1),
        job(4, status="dead", attempts=5),
        leased(5, "w1", 51, attempts=5),
    )
    before = copy.deepcopy(s)
    status, rec = check(s, req(now=50))
    assert status == "ok"
    assert rec["released"] == [jid(0), jid(5)]
    assert rec["worker"] == "w1" and rec["restarted_at"] == 50
    assert rec["prior_state_id"] == _qid(before) and rec["state_id"] == _qid(s)
    j0, j5 = s["jobs"][0], s["jobs"][5]
    assert j0["status"] == "ready" and j0["lease_owner"] is None and j0["lease_expires_at"] is None
    assert j0["attempts"] == 2 and j5["attempts"] == 5 and j5["status"] == "ready"
    assert s["jobs"][1] == before["jobs"][1]
    assert s["next_seq"] == before["next_seq"]
    for k in (2, 3, 4):
        assert s["jobs"][k] == before["jobs"][k]


def test_expiry_boundary_is_strict():
    for now, released in [(99, True), (100, False), (101, False), (0, True)]:
        s = state(leased(0, "w1", 100))
        rec = check(s, req(now=now))[1]
        assert (rec["released"] == [jid(0)]) is released, now
        assert (s["jobs"][0]["status"] == "ready") is released
    s = state(leased(0, "w1", 1))
    assert check(s, req(now=0))[1]["released"] == [jid(0)]
    s = state(leased(0, "w1", MAX_NOW))
    assert check(s, req(now=MAX_NOW - 1))[1]["released"] == [jid(0)]
    s = state(leased(0, "w1", MAX_NOW))
    assert check(s, req(now=MAX_NOW))[1]["released"] == []


def test_empty_release_is_a_successful_receipt_with_unchanged_state():
    for s in (state(), state(job(0)), state(leased(0, "w2", 100)), state(leased(0, "w1", 10))):
        before = copy.deepcopy(s)
        rec = check(s, req(now=50))[1]
        assert rec["released"] == [] and s == before
        assert rec["prior_state_id"] == rec["state_id"] == _qid(before)


def test_second_restart_at_the_same_now_releases_nothing():
    s = state(leased(0), leased(1, "w2"), leased(2))
    first = check(s, req())[1]
    assert first["released"] == [jid(0), jid(2)]
    second = check(s, req())[1]
    assert second["released"] == [] and second["prior_state_id"] == first["state_id"]
    assert second["restart_id"] != first["restart_id"]


def test_attempts_are_never_refunded_or_charged_and_max_attempts_returns_to_ready():
    for at in range(1, 6):
        s = state(leased(0, attempts=at))
        check(s, req())
        assert s["jobs"][0]["attempts"] == at and s["jobs"][0]["status"] == "ready"


def test_released_ids_are_in_seq_order_with_gaps_in_seq_numbers():
    s = state(
        leased(3, expires=100),
        leased(7, expires=100),
        leased(9, expires=100),
        next_seq=12,
    )
    assert check(s, req())[1]["released"] == [jid(3), jid(7), jid(9)]


def test_restart_id_binds_worker_clock_and_states():
    base = state(leased(0, "w1", 100), leased(1, "w2", 100))
    ids = set()
    for worker, now in [("w1", 10), ("w1", 11), ("w2", 10), ("w3", 10)]:
        s = copy.deepcopy(base)
        ids.add(check(s, req(worker=worker, now=now))[1]["restart_id"])
    assert len(ids) == 4
    s = copy.deepcopy(base)
    s["jobs"][0]["payload"] = {"n": "other"}
    assert check(s, req(worker="w3", now=10))[1]["restart_id"] not in ids


def _container_ids(obj):
    """Identity of every list and dict reachable from obj (iterative)."""
    ids, stack = set(), [obj]
    while stack:
        n = stack.pop()
        if type(n) is dict:
            ids.add(id(n))
            stack.extend(dict.values(n))
        elif type(n) is list:
            ids.add(id(n))
            stack.extend(list.__iter__(n))
    return ids


def test_state_id_keeps_non_ascii():
    s = state(leased(0, payload={"\u00e9": "\U0001f600"}))
    rec = check(s, req())[1]
    assert rec["state_id"] == _qid(s)


def test_receipt_and_new_state_share_no_container_with_each_other_or_the_old_state():
    payloads = [{"n": 0, "l": [1, [2]]}, [1, {"a": [3]}], {"n": 2}]
    s = state(
        leased(0, payload=payloads[0]), leased(1, payload=payloads[1]), job(2, payload=payloads[2])
    )
    old_jobs = s["jobs"]  # keep the old containers alive so no id is reused
    old_job_dicts = list(old_jobs)
    old = _container_ids(s) - {id(s)}
    old_payload_refs = [s["jobs"][i]["payload"] for i in range(3)]
    rec = restart(s, req())
    assert rec["released"] == [jid(0), jid(1)]
    new = _container_ids(s) - {id(s)}
    assert old_jobs is not s["jobs"] and all(
        a is not b for a, b in zip(old_job_dicts, s["jobs"], strict=True)
    )
    receipt = _container_ids(rec)
    assert receipt and not (receipt & new), "receipt aliases the new state"
    assert not (receipt & old), "receipt aliases the old state"
    assert not (new & old), "new state reuses a container of the old state"
    # the caller's old payload objects are untouched and detached from the state
    for i in range(3):
        assert s["jobs"][i]["payload"] == old_payload_refs[i]
        assert s["jobs"][i]["payload"] is not old_payload_refs[i]
    rec["released"].append("x")
    assert s["jobs"][0]["status"] == "ready" and len(s["jobs"]) == 3
    old_payload_refs[0]["n"] = 99
    assert s["jobs"][0]["payload"]["n"] == 0


def test_state_is_updated_in_place():
    s = state(leased(0))
    ident = id(s)
    keep = s
    restart(s, req())
    assert s is keep and id(s) == ident and s["jobs"][0]["status"] == "ready"


# ---------- request boundaries and malformed ----------


def test_request_bounds_are_exact():
    for now, ok in [(0, True), (MAX_NOW, True), (-1, False), (MAX_NOW + 1, False)]:
        assert (check(state(), req(now=now))[0] == "ok") is ok
    assert check(state(), req(now=-1)) == ("fail", "malformed_restart_request")
    for op in ["Restart", "restart ", "", "restarts"]:
        assert check(state(), req(op=op)) == ("fail", "malformed_restart_request")


def test_worker_grammar_is_exact():
    for w in ["a", "A" * 64, "w1.x:y-z_9", "0"]:
        assert check(state(), req(worker=w))[0] == "ok"
    for w in ["", "a" * 65, "w 1", "w/1", "w1\n", "é", "w,1", " w", "w\x00"]:
        assert check(state(), req(worker=w)) == ("fail", "malformed_restart_request"), w


def _hostiles():
    return [
        None,
        True,
        False,
        0,
        1.0,
        math.nan,
        "restart",
        b"w",
        [],
        {},
        (),
        S("w1"),
        I(1),
        F(1.0),
        D(),
        L(),
    ]


def test_every_request_field_rejects_hostile_values():
    for key in REQ_KEYS:
        for v in _hostiles():
            r = req()
            r[key] = v
            got = run(state(leased(0)), r)
            if got[0] == "ok":
                assert type(v) is type(req()[key])
            else:
                assert got == ("fail", "malformed_restart_request"), (key, v)
    assert check(state(), req(now=I(5))) == ("fail", "malformed_restart_request")
    assert check(state(), req(now=True)) == ("fail", "malformed_restart_request")
    assert check(state(), req(worker=S("w1"))) == ("fail", "malformed_restart_request")
    assert check(state(), req(op=S("restart"))) == ("fail", "malformed_restart_request")


def test_missing_extra_and_non_str_request_keys_are_refused():
    for k in REQ_KEYS:
        r = req()
        del r[k]
        assert check(state(), r) == ("fail", "malformed_restart_request")
    for extra in ({"extra": 1}, {1: 1}, {None: 1}):
        r = req()
        r.update(extra)
        assert check(state(), r) == ("fail", "malformed_restart_request")
    r = req()
    r[S("now")] = r.pop("now")
    assert check(state(), r) == ("fail", "malformed_restart_request")


@pytest.mark.parametrize(
    "bad", [None, 1, "restart", [], (), b"x", S("x"), D(req()), math.nan, True]
)
def test_non_dict_requests_are_malformed(bad):
    assert run(state(leased(0)), bad) == ("fail", "malformed_restart_request")


def test_inert_str_subclass_keys_are_refused():
    class Evil(str):
        def __eq__(self, other):
            raise AssertionError("eq ran")

        __hash__ = str.__hash__

    r = {}
    for k in REQ_KEYS:
        dict.__setitem__(r, Evil(k) if k == "op" else k, req()[k])
    assert run(state(), r) == ("fail", "malformed_restart_request")
    j = {}
    for k in JOB_KEYS:
        dict.__setitem__(j, Evil(k) if k == "seq" else k, job(0)[k])
    assert run({"jobs": [j], "next_seq": 1}, req()) == ("fail", "corrupt_queue")
    s = {}
    for k in ("jobs", "next_seq"):
        dict.__setitem__(s, Evil(k) if k == "jobs" else k, state()[k])
    assert run(s, req()) == ("fail", "corrupt_queue")


# ---------- corrupt_queue battery ----------


def test_state_shape_defects_are_corrupt():
    good = state(leased(0))
    for bad in [None, 1, "s", [], (), D(good), S("x"), {}, {"jobs": []}, {"next_seq": 0}]:
        assert run(bad, req()) == ("fail", "corrupt_queue")
    s = copy.deepcopy(good)
    s["extra"] = 1
    assert run(s, req()) == ("fail", "corrupt_queue")
    s = copy.deepcopy(good)
    s[1] = 1
    assert run(s, req()) == ("fail", "corrupt_queue")
    for jobs in [(), None, "x", {}, L(good["jobs"]), 1]:
        assert run({"jobs": jobs, "next_seq": 1}, req()) == ("fail", "corrupt_queue")
    for ns in [None, True, 1.0, "1", I(1), -1, MAX_NOW + 1, 0]:
        assert run({"jobs": copy.deepcopy(good["jobs"]), "next_seq": ns}, req()) == (
            "fail",
            "corrupt_queue",
        )
    assert check({"jobs": [], "next_seq": 0}, req())[0] == "ok"
    assert check({"jobs": [], "next_seq": MAX_NOW}, req())[0] == "ok"
    assert run({"jobs": [], "next_seq": MAX_NOW + 1}, req()) == ("fail", "corrupt_queue")


def test_job_count_bound_is_exact():
    def many(n):
        return {"jobs": [job(i, payload=None) for i in range(n)], "next_seq": n}

    assert run(many(10000), req())[0] == "ok"
    assert run(many(10001), req()) == ("fail", "corrupt_queue")


def test_seq_ordering_and_next_seq_bound_are_exact():
    assert check(state(job(0), job(1), next_seq=2), req())[0] == "ok"
    assert run(state(job(0), job(1), next_seq=1), req()) == ("fail", "corrupt_queue")
    assert check(state(job(5), next_seq=6), req())[0] == "ok"
    assert run(state(job(5), next_seq=5), req()) == ("fail", "corrupt_queue")
    assert run(state(job(1), job(0), next_seq=2), req()) == ("fail", "corrupt_queue")
    assert run(state(job(0), job(0, job_id=jid(9)), next_seq=3), req()) == ("fail", "corrupt_queue")
    assert run(state(job(0), job(1, job_id=jid(0)), next_seq=2), req()) == ("fail", "corrupt_queue")
    assert run(state(job(-1), next_seq=1), req()) == ("fail", "corrupt_queue")


def test_job_field_defects_are_corrupt():
    cases = [
        {"job_id": "job1:" + "AB" * 32},
        {"job_id": "job1:" + "ab" * 31},
        {"priority": -1},
        {"priority": 10},
        {"priority": True},
        {"attempts": -1},
        {"attempts": 6},
        {"status": "unknown"},
        {"status": "Ready"},
        {"status": "ready", "lease_owner": "w1"},
        {"status": "ready", "lease_expires_at": 5},
        {"status": "done", "attempts": 0},
        {"status": "dead", "attempts": 4},
        {"status": "dead", "attempts": 5, "lease_owner": "w1"},
        {"status": "leased", "attempts": 0, "lease_owner": "w1", "lease_expires_at": 5},
        {"status": "leased", "attempts": 1, "lease_owner": None, "lease_expires_at": 5},
        {"status": "leased", "attempts": 1, "lease_owner": "w1", "lease_expires_at": None},
        {"status": "leased", "attempts": 1, "lease_owner": "w1", "lease_expires_at": 0},
        {"status": "leased", "attempts": 1, "lease_owner": "w1", "lease_expires_at": MAX_NOW + 1},
        {"status": "leased", "attempts": 1, "lease_owner": "", "lease_expires_at": 5},
        {"status": "leased", "attempts": 1, "lease_owner": "a" * 65, "lease_expires_at": 5},
        {"status": "leased", "attempts": 1, "lease_owner": "w 1", "lease_expires_at": 5},
    ]
    for fields in cases:
        assert run(state(job(0, **fields)), req()) == ("fail", "corrupt_queue"), fields
    for key in JOB_KEYS:
        if key == "payload":
            continue
        for v in _hostiles() + ["x", -1, 10**30]:
            j = job(0)
            j[key] = v
            got = run({"jobs": [j], "next_seq": 1}, req())
            if got[0] == "ok":
                assert type(v) is type(job(0)[key])
            else:
                assert got == ("fail", "corrupt_queue"), (key, v)
    for key in JOB_KEYS:
        j = job(0)
        del j[key]
        assert run({"jobs": [j], "next_seq": 1}, req()) == ("fail", "corrupt_queue")
    j = job(0)
    j["extra"] = 1
    assert run({"jobs": [j], "next_seq": 1}, req()) == ("fail", "corrupt_queue")
    j = job(0)
    j[7] = 1
    assert run({"jobs": [j], "next_seq": 1}, req()) == ("fail", "corrupt_queue")
    for bad in [None, [], "x", D(job(0)), 1]:
        assert run({"jobs": [bad], "next_seq": 1}, req()) == ("fail", "corrupt_queue")


def test_one_corrupt_job_among_many_rejects_the_whole_state_unchanged():
    s = state(leased(0), leased(1), job(2, priority=99), leased(3))
    assert run(s, req()) == ("fail", "corrupt_queue")
    assert s["jobs"][0]["status"] == "leased" and s["jobs"][3]["status"] == "leased"


def nested(depth):
    node = []
    for _ in range(depth - 1):
        node = [node]
    return node


def test_payload_walk_depth_alias_cycle_and_scalars():
    def one(p):
        return run(state(leased(0, payload=p)), req())

    assert one(nested(64))[0] == "ok"
    assert one(nested(65)) == ("fail", "corrupt_queue")
    assert one(nested(5000)) == ("fail", "corrupt_queue")
    d = {}
    for _ in range(63):
        d = {"k": d}
    assert one(d)[0] == "ok"
    assert one({"k": d}) == ("fail", "corrupt_queue")
    mixed = [{"k": nested(62)}]
    assert one(mixed)[0] == "ok"
    assert one([{"k": nested(63)}]) == ("fail", "corrupt_queue")
    shared = [1]
    assert one([shared, shared]) == ("fail", "corrupt_queue")
    cyc = []
    cyc.append(cyc)
    assert one(cyc) == ("fail", "corrupt_queue")
    dcyc = {}
    dcyc["me"] = dcyc
    assert one(dcyc) == ("fail", "corrupt_queue")
    assert one([[1], [1]])[0] == "ok"
    good = [None, True, 0, -1, 1.5, -0.0, 1e300, "", "é", "\U0001f600", [], {}, {"": 1}]
    for v in good:
        assert one(v)[0] == "ok", v
    bad = [
        math.nan,
        math.inf,
        -math.inf,
        "\ud800",
        b"x",
        (1,),
        {1},
        object(),
        S("x"),
        I(1),
        F(1.5),
        L(),
        D(),
    ]
    for v in bad:
        for wrap in (lambda x: x, lambda x: [x], lambda x: {"k": x}):
            assert one(wrap(v)) == ("fail", "corrupt_queue"), v
    for key in [1, None, True, S("k"), "\ud800"]:
        assert one({key: 1}) == ("fail", "corrupt_queue")


def test_alias_across_two_jobs_payloads_is_corrupt():
    shared = {"x": 1}
    s = state(job(0, payload=shared), job(1, payload=shared))
    assert run(s, req()) == ("fail", "corrupt_queue")
    assert run(state(job(0, payload={"x": 1}), job(1, payload={"x": 1})), req())[0] == "ok"
    inner = [1]
    assert run(state(job(0, payload=[inner]), job(1, payload={"k": inner})), req()) == (
        "fail",
        "corrupt_queue",
    )


def test_payload_int_digit_limit_is_exact():
    def one(p):
        return run(state(leased(0, payload=p)), req())

    assert one(10**3999)[0] == "ok"
    assert one(-(10**3999))[0] == "ok"
    assert one(10**4000 - 1)[0] == "ok"
    assert one(10**4000) == ("fail", "corrupt_queue")
    assert one(-(10**4000)) == ("fail", "corrupt_queue")
    assert one([10**4000]) == ("fail", "corrupt_queue")
    assert one({"k": 10**4000}) == ("fail", "corrupt_queue")
    assert one(2**13287)[0] == "ok"
    assert one(2**13290) == ("fail", "corrupt_queue")


def test_payload_int_digit_limit_under_a_lowered_interpreter_limit():
    old = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        s = state(leased(0, payload=10**639))
        assert run(s, req())[0] == "ok"
        assert run(state(leased(0, payload=10**640)), req()) == ("fail", "corrupt_queue")
        assert run(state(leased(0, payload=[-(10**1000)])), req()) == ("fail", "corrupt_queue")
        assert run(state(leased(0, payload={"k": [10**639]})), req())[0] == "ok"
    finally:
        sys.set_int_max_str_digits(old)
    assert sys.get_int_max_str_digits() == old
    assert run(state(leased(0, payload=10**1000)), req())[0] == "ok"


def test_payload_walk_runs_no_caller_code():
    class Bad(list):
        def __iter__(self):
            raise AssertionError("iter ran")

    class BadD(dict):
        def items(self):
            raise AssertionError("items ran")

    assert run(state(leased(0, payload=Bad([1]))), req()) == ("fail", "corrupt_queue")
    assert run(state(leased(0, payload=BadD(a=1))), req()) == ("fail", "corrupt_queue")
    d = {}
    dict.__setitem__(d, S("k"), 1)
    assert run(state(leased(0, payload=d)), req()) == ("fail", "corrupt_queue")


def test_precedence_request_before_state():
    s = state(job(0, priority=99))
    assert run(s, req(now=-1)) == ("fail", "malformed_restart_request")
    assert run(None, req(worker="")) == ("fail", "malformed_restart_request")
    assert run(s, req()) == ("fail", "corrupt_queue")
    assert run(None, req()) == ("fail", "corrupt_queue")


def test_rejected_calls_change_nothing_and_the_valid_follow_up_restarts():
    s = state(leased(0), leased(1, "w2"))
    ref = copy.deepcopy(s)
    for bad in [req(now=-1), req(worker=""), None]:
        run(s, bad)
    assert s == ref
    assert run(s, req())[1]["released"] == [jid(0)]


# ---------- seeded properties ----------


def _rand_state(rng):
    n = rng.randint(0, 8)
    jobs, seq = [], rng.randint(0, 3)
    for _ in range(n):
        st = rng.choice(["ready", "leased", "leased", "done", "dead"])
        j = job(
            seq,
            priority=rng.randint(0, 9),
            payload=rng.choice([None, 1, "é", [1, [2]], {"a": {"b": 1.5}}]),
        )
        j["job_id"] = "job1:" + "".join(rng.choice("0123456789abcdef") for _ in range(64))
        if st == "leased":
            j.update(
                status="leased",
                attempts=rng.randint(1, 5),
                lease_owner=rng.choice(["w1", "w2", "w3"]),
                lease_expires_at=rng.choice([1, 49, 50, 51, 100, MAX_NOW]),
            )
        elif st == "done":
            j.update(status="done", attempts=rng.randint(1, 5))
        elif st == "dead":
            j.update(status="dead", attempts=5)
        else:
            j.update(attempts=rng.randint(0, 5))
        jobs.append(j)
        seq += rng.randint(1, 3)
    s = {"jobs": jobs, "next_seq": seq + rng.randint(0, 2)}
    if rng.random() < 0.3 and jobs:
        j = rng.choice(jobs)
        key = rng.choice([k for k in JOB_KEYS if k != "payload"])
        j[key] = rng.choice([None, -1, 2**60, "zz", 1.5, True, [], {}, S("x"), MAX_NOW + 1])
    return s


def _rand_req(rng):
    r = req(
        worker=rng.choice(["w1", "w2", "w3", "w4"]),
        now=rng.choice([0, 1, 49, 50, 51, 99, 100, MAX_NOW]),
    )
    if rng.random() < 0.15:
        key = rng.choice(REQ_KEYS)
        r[key] = rng.choice([None, -1, "", "x y", 1.5, True, MAX_NOW + 1])
    return r


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_states(seed):
    rng = random.Random(seed)
    for _ in range(40):
        check(_rand_state(rng), _rand_req(rng))


def test_random_corpus_covers_every_outcome_and_both_release_shapes():
    rng = random.Random(11)
    seen = set()
    for _ in range(1500):
        s, r = _rand_state(rng), _rand_req(rng)
        got = check(s, r)
        seen.add(
            "ok-release"
            if got[0] == "ok" and got[1]["released"]
            else ("ok-empty" if got[0] == "ok" else got[1])
        )
    assert seen == {"ok-release", "ok-empty", "malformed_restart_request", "corrupt_queue"}


@pytest.mark.parametrize("seed", range(10))
def test_restart_is_idempotent_and_never_touches_other_workers(seed):
    rng = random.Random(500 + seed)
    for _ in range(40):
        s = _rand_state(rng)
        r = _rand_req(rng)
        if model(s, r)[0] != "ok":
            continue
        before = copy.deepcopy(s)
        first = restart(s, r)
        for a, b in zip(before["jobs"], s["jobs"], strict=True):
            assert (
                a["job_id"] == b["job_id"]
                and a["seq"] == b["seq"]
                and a["attempts"] == b["attempts"]
            )
            if (
                b["status"] == "leased"
                or a["lease_owner"] != r["worker"]
                or a["status"] != "leased"
            ):
                assert a == b
        assert s["next_seq"] == before["next_seq"]
        assert [j["job_id"] for j in before["jobs"] if j["job_id"] in first["released"]] == first[
            "released"
        ]
        again = restart(s, r)
        assert again["released"] == [] and again["prior_state_id"] == first["state_id"]
