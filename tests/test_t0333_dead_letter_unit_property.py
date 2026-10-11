"""T0333: unit and seeded property checks of the production dead-letter burial.

An independent model (plain predicates and a hand-built digest, no shared code
with server.jobs_dead_letter) is driven with seeded random requests, valid and
defective. Every call must agree with the model on the record or the failure
class, must leave the request byte-identical, and must return a result that
shares no container with it. Unit rows pin every bound at both sides, the
precedence between failure classes, the payload walk (depth, alias, cycle,
int digits, floats, strings, key types), hostile values and subclasses, and a
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

from server.jobs_dead_letter import DeadLetterError, bury

JOB = "job1:" + "ab" * 32
MAX_NOW = 2**53 - 1
MAX_SEQ = 2**53 - 2
CODES = {
    "malformed_bury_request": "malformed_request",
    "corrupt_job": "corrupt_job",
    "job_not_dead": "job_not_dead",
}
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
REQ_KEYS = ["op", "job", "reason", "now"]
RECORD_ORDER = [
    "op",
    "job_id",
    "seq",
    "priority",
    "payload_digest",
    "attempts",
    "reason",
    "buried_at",
    "entry_id",
]


def job(**kw):
    base = {
        "job_id": JOB,
        "seq": 3,
        "priority": 2,
        "payload": {"a": [1, 2.5, None, True, "x"]},
        "status": "dead",
        "attempts": 5,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    base.update(kw)
    return base


def req(**kw):
    base = {"op": "bury", "job": job(), "reason": "exhausted", "now": 10}
    base.update(kw)
    return base


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


def _canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _dig(prefix, v):
    return prefix + ":" + hashlib.sha256(prefix.encode() + b"\x00" + _canon(v).encode()).hexdigest()


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


def _payload(root):
    seen = set()

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


def _job_ok(j):
    if not _keys(j, JOB_KEYS):
        return False
    st, at = j["status"], j["attempts"]
    o, e = j["lease_owner"], j["lease_expires_at"]
    jid = j["job_id"]
    if not (
        type(jid) is str
        and len(jid) == 69
        and jid.startswith("job1:")
        and all(c in "0123456789abcdef" for c in jid[5:])
        and _int(j["seq"], 0, MAX_SEQ)
        and _int(j["priority"], 0, 9)
        and type(st) is str
        and st in ("ready", "leased", "done", "dead")
        and _int(at, 0, 5)
    ):
        return False
    if st == "leased":
        alpha = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")
        if not (
            type(o) is str
            and 1 <= len(o) <= 64
            and set(o) <= alpha
            and _int(e, 1, MAX_NOW)
            and at >= 1
        ):
            return False
    elif o is not None or e is not None:
        return False
    if st == "dead" and at != 5:
        return False
    if st == "done" and at < 1:
        return False
    return _payload(j["payload"])


def model(r):
    if not _keys(r, REQ_KEYS):
        return ("fail", "malformed_bury_request")
    if not (
        type(r["op"]) is str
        and r["op"] == "bury"
        and type(r["job"]) is dict
        and type(r["reason"]) is str
        and r["reason"] in ("exhausted", "permanent")
        and _int(r["now"], 0, MAX_NOW)
    ):
        return ("fail", "malformed_bury_request")
    if not _job_ok(r["job"]):
        return ("fail", "corrupt_job")
    j = r["job"]
    if j["status"] != "dead":
        return ("fail", "job_not_dead")
    rec = {
        "op": "bury",
        "job_id": j["job_id"],
        "seq": j["seq"],
        "priority": j["priority"],
        "payload_digest": _dig("pd1", j["payload"]),
        "attempts": j["attempts"],
        "reason": r["reason"],
        "buried_at": r["now"],
    }
    rec["entry_id"] = _dig("dl1", rec)
    return ("ok", rec)


def run(r):
    before = _snap(r)
    try:
        out = ("ok", bury(r))
    except DeadLetterError as error:
        assert type(error) is DeadLetterError
        assert error.__cause__ is None and error.__context__ is None
        assert error.code == CODES[error.failure_class]
        assert error.retryable is False
        out = ("fail", error.failure_class)
    assert _snap(r) == before, "request was changed"
    return out


def check(r):
    got = run(r)
    assert got == model(r)
    return got


def jreq(**kw):
    return req(job=job(**kw))


def nested(depth):
    """A list nested so that the innermost list is at the given depth."""
    node = []
    for _ in range(depth - 1):
        node = [node]
    return node


class S(str):
    pass


class I(int):  # noqa: E742
    pass


class F(float):
    pass


class L(list):
    pass


class D(dict):
    pass


# ---------- happy ----------


def test_burial_has_exact_shape_and_binds_the_payload_by_digest():
    status, rec = check(req())
    assert status == "ok"
    assert list(rec) == RECORD_ORDER
    assert rec["op"] == "bury" and rec["job_id"] == JOB and rec["seq"] == 3
    assert rec["priority"] == 2 and rec["attempts"] == 5
    assert rec["reason"] == "exhausted" and rec["buried_at"] == 10
    assert rec["payload_digest"] == _dig("pd1", job()["payload"])
    assert rec["entry_id"] == _dig("dl1", {k: v for k, v in rec.items() if k != "entry_id"})
    assert "payload" not in rec


@pytest.mark.parametrize("reason", ["exhausted", "permanent"])
def test_both_reasons_bury_a_dead_job(reason):
    assert check(req(reason=reason))[1]["reason"] == reason


def test_entry_id_binds_every_field_and_burial_is_deterministic():
    base = run(req())[1]
    assert run(req())[1] == base
    variants = [
        req(reason="permanent"),
        req(now=11),
        jreq(seq=4),
        jreq(priority=3),
        jreq(job_id="job1:" + "cd" * 32),
        jreq(payload={"a": [1, 2.5, None, True, "y"]}),
    ]
    ids = {run(v)[1]["entry_id"] for v in variants} | {base["entry_id"]}
    assert len(ids) == len(variants) + 1
    # the payload binds only through the digest
    assert run(jreq(payload=1))[1]["payload_digest"] != run(jreq(payload=True))[1]["payload_digest"]


def test_result_shares_no_container_with_the_request():
    r = req()
    rec = bury(r)
    assert rec is not r and all(type(v) in (str, int) for v in rec.values())
    rec["seq"] = 99
    assert r["job"]["seq"] == 3


# ---------- boundaries ----------


def test_request_bounds_are_exact():
    for now, ok in [(0, True), (MAX_NOW, True), (-1, False), (MAX_NOW + 1, False)]:
        assert (check(req(now=now))[0] == "ok") is ok
    assert check(req(now=-1)) == ("fail", "malformed_bury_request")
    assert check(req(reason="Exhausted")) == ("fail", "malformed_bury_request")
    assert check(req(reason="")) == ("fail", "malformed_bury_request")
    assert check(req(op="Bury")) == ("fail", "malformed_bury_request")
    assert check(req(op="bury ")) == ("fail", "malformed_bury_request")


def test_job_bounds_are_exact():
    for key, lo, hi in [("seq", 0, MAX_SEQ), ("priority", 0, 9)]:
        for v in (lo, hi):
            assert check(req(job=job(**{key: v})))[0] == "ok"
        for v in (lo - 1, hi + 1):
            assert check(req(job=job(**{key: v}))) == ("fail", "corrupt_job")
    assert check(jreq(attempts=4)) == ("fail", "corrupt_job")
    assert check(jreq(attempts=6)) == ("fail", "corrupt_job")
    assert check(jreq(attempts=5))[0] == "ok"


def test_job_id_grammar_is_exact():
    good = "job1:" + "0123456789abcdef" * 4
    assert check(jreq(job_id=good))[0] == "ok"
    for bad in [
        good[:-1],
        good + "0",
        "job2:" + "ab" * 32,
        "Job1:" + "ab" * 32,
        "job1:" + "AB" * 32,
        "job1:" + "ab" * 31 + "g0",
        "job1:" + "ab" * 31 + "a\n",
        " " + good,
        "",
    ]:
        assert check(jreq(job_id=bad)) == ("fail", "corrupt_job")


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "ready", "attempts": 0},
        {"status": "ready", "attempts": 4},
        {"status": "done", "attempts": 1},
        {"status": "done", "attempts": 5},
        {"status": "leased", "attempts": 1, "lease_owner": "w1", "lease_expires_at": 5},
        {"status": "leased", "attempts": 5, "lease_owner": "w1", "lease_expires_at": 5},
    ],
)
def test_valid_non_dead_jobs_are_not_dead(fields):
    assert check(jreq(**fields)) == ("fail", "job_not_dead")


def test_queue_job_invariants_make_a_job_corrupt():
    cases = [
        {"status": "unknown"},
        {"status": "Dead"},
        {"status": 1},
        {"status": "ready", "attempts": 0, "lease_owner": "w", "lease_expires_at": None},
        {"status": "ready", "attempts": 0, "lease_owner": None, "lease_expires_at": 5},
        {"status": "dead", "lease_owner": "w1"},
        {"status": "dead", "lease_expires_at": 5},
        {"status": "done", "attempts": 0},
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
        assert check(jreq(**fields)) == ("fail", "corrupt_job"), fields
    ok_owner = {"status": "leased", "attempts": 1, "lease_expires_at": MAX_NOW}
    for owner in ("a", "A" * 64, "w1.x:y-z_9"):
        assert check(jreq(lease_owner=owner, **ok_owner)) == ("fail", "job_not_dead")


# ---------- payload walk ----------


def test_payload_depth_is_exact():
    assert check(jreq(payload=nested(64)))[0] == "ok"
    assert check(jreq(payload=nested(65))) == ("fail", "corrupt_job")
    d = {}
    for _ in range(63):
        d = {"k": d}
    assert check(jreq(payload=d))[0] == "ok"
    assert check(jreq(payload={"k": d})) == ("fail", "corrupt_job")
    assert check(jreq(payload=nested(1)))[0] == "ok"
    deep = nested(5000)
    assert run(jreq(payload=deep)) == ("fail", "corrupt_job")


def test_payload_alias_and_cycle_are_refused():
    shared = [1]
    assert check(jreq(payload=[shared, shared])) == ("fail", "corrupt_job")
    assert check(jreq(payload={"a": shared, "b": shared})) == ("fail", "corrupt_job")
    cyc = []
    cyc.append(cyc)
    assert run(jreq(payload=cyc)) == ("fail", "corrupt_job")
    dcyc = {}
    dcyc["me"] = dcyc
    assert run(jreq(payload=dcyc)) == ("fail", "corrupt_job")
    assert check(jreq(payload=[[1], [1]]))[0] == "ok"
    assert check(jreq(payload=[[], []]))[0] == "ok"


def test_payload_int_digit_limit_is_exact():
    assert check(jreq(payload=10**3999))[0] == "ok"
    assert check(jreq(payload=-(10**3999)))[0] == "ok"
    assert check(jreq(payload=10**4000 - 1))[0] == "ok"
    assert check(jreq(payload=10**4000)) == ("fail", "corrupt_job")
    assert check(jreq(payload=-(10**4000))) == ("fail", "corrupt_job")
    assert check(jreq(payload=[10**4000])) == ("fail", "corrupt_job")
    assert check(jreq(payload={"k": 10**4000})) == ("fail", "corrupt_job")
    assert check(jreq(payload=2**13287))[0] == "ok"
    assert check(jreq(payload=2**13289)) == ("fail", "corrupt_job")


def test_payload_int_digit_limit_under_a_lowered_interpreter_limit():
    old = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        assert run(jreq(payload=10**639))[0] == "ok"
        assert run(jreq(payload=10**640)) == ("fail", "corrupt_job")
        assert run(jreq(payload=[-(10**1000)])) == ("fail", "corrupt_job")
        assert run(jreq(payload={"k": [10**639]}))[0] == "ok"
    finally:
        sys.set_int_max_str_digits(old)
    assert sys.get_int_max_str_digits() == old
    assert check(jreq(payload=10**1000))[0] == "ok"


def test_payload_scalars_are_exact_json_values():
    good = [
        None,
        True,
        False,
        0,
        -1,
        1.5,
        -0.0,
        1e300,
        "",
        "é",
        "\U0001f600",
        [],
        {},
        [None],
        {"": 1},
    ]
    for v in good:
        assert check(jreq(payload=v))[0] == "ok", v
    bad = [
        math.nan,
        math.inf,
        -math.inf,
        "\ud800",
        "a\udfffb",
        b"x",
        bytearray(b"x"),
        (1, 2),
        {1, 2},
        frozenset(),
        object(),
        complex(1, 2),
        range(3),
        S("x"),
        I(1),
        F(1.5),
        L([1]),
        D(a=1),
    ]
    for v in bad:
        for wrap in (lambda x: x, lambda x: [x], lambda x: {"k": x}):
            assert check(jreq(payload=wrap(v))) == ("fail", "corrupt_job"), v


def test_payload_keys_must_be_exact_utf8_strs():
    assert check(jreq(payload={"k": 1, "é": 2}))[0] == "ok"
    for key in [1, None, True, 1.5, (1,), S("k"), "\ud800"]:
        assert check(jreq(payload={key: 1})) == ("fail", "corrupt_job")
    assert check(jreq(payload={"a": 1, 2: 1})) == ("fail", "corrupt_job")


def test_payload_str_subclass_keys_are_inert():
    class Evil(str):
        def __hash__(self):
            raise AssertionError("key hash ran")

        def __eq__(self, other):
            raise AssertionError("key eq ran")

        def __lt__(self, other):
            raise AssertionError("key lt ran")

    d = {}
    dict.__setitem__(d, S("k"), 1)
    assert run(jreq(payload=d)) == ("fail", "corrupt_job")

    class Boom(str):
        def encode(self, *a, **k):
            raise AssertionError("encode ran")

    d = {}
    dict.__setitem__(d, Boom("k"), 1)
    assert run(jreq(payload=d)) == ("fail", "corrupt_job")
    del Evil


def test_payload_walk_runs_no_caller_code():
    class Bad(list):
        def __iter__(self):
            raise AssertionError("iter ran")

        def __len__(self):
            raise AssertionError("len ran")

    class BadD(dict):
        def items(self):
            raise AssertionError("items ran")

        def __iter__(self):
            raise AssertionError("iter ran")

    assert run(jreq(payload=Bad([1]))) == ("fail", "corrupt_job")
    assert run(jreq(payload=BadD(a=1))) == ("fail", "corrupt_job")
    assert run(jreq(payload=[Bad([1])])) == ("fail", "corrupt_job")


# ---------- malformed ----------


def _hostiles():
    return [
        None,
        True,
        False,
        0,
        1.0,
        math.nan,
        "bury",
        b"bury",
        [],
        {},
        (),
        S("bury"),
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
            got = run(r)
            if got[0] == "ok":
                assert type(v) is type(req()[key])
            elif key == "job" and type(v) is dict:
                assert got == ("fail", "corrupt_job")
            else:
                assert got == ("fail", "malformed_bury_request"), (key, v)
    assert check(req(now=I(5))) == ("fail", "malformed_bury_request")
    assert check(req(now=True)) == ("fail", "malformed_bury_request")
    assert check(req(now=5.0)) == ("fail", "malformed_bury_request")
    assert check(req(op=S("bury"))) == ("fail", "malformed_bury_request")
    assert check(req(reason=S("exhausted"))) == ("fail", "malformed_bury_request")
    assert check(req(job=D(job()))) == ("fail", "malformed_bury_request")


def test_every_job_field_rejects_hostile_values():
    for key in JOB_KEYS:
        if key == "payload":
            continue
        for v in _hostiles() + ["x", -1, 10**30]:
            j = job()
            j[key] = v
            got = run(req(job=j))
            if got[0] == "ok":
                assert type(v) is type(job()[key])
                assert key in (
                    "lease_owner",
                    "lease_expires_at",
                    "seq",
                    "attempts",
                    "priority",
                    "status",
                    "job_id",
                )
            else:
                assert got == ("fail", "corrupt_job"), (key, v)
    for key, v in [
        ("seq", I(3)),
        ("priority", I(2)),
        ("attempts", I(5)),
        ("seq", True),
        ("priority", 2.0),
    ]:
        assert check(jreq(**{key: v})) == ("fail", "corrupt_job")
    assert check(jreq(status=S("dead"))) == ("fail", "corrupt_job")
    assert check(jreq(job_id=S(JOB))) == ("fail", "corrupt_job")
    assert check(jreq(lease_owner=S("w"), status="leased", attempts=1, lease_expires_at=5)) == (
        "fail",
        "corrupt_job",
    )


def test_missing_extra_and_non_str_keys_are_refused():
    for k in REQ_KEYS:
        r = req()
        del r[k]
        assert check(r) == ("fail", "malformed_bury_request")
    r = req()
    r["extra"] = 1
    assert check(r) == ("fail", "malformed_bury_request")
    r = req()
    r[1] = 1
    assert check(r) == ("fail", "malformed_bury_request")
    r = req()
    r[S("now")] = r.pop("now")
    assert check(r) == ("fail", "malformed_bury_request")
    for k in JOB_KEYS:
        j = job()
        del j[k]
        assert check(req(job=j)) == ("fail", "corrupt_job")
    j = job()
    j["extra"] = 1
    assert check(req(job=j)) == ("fail", "corrupt_job")
    j = job()
    j[1] = 1
    assert check(req(job=j)) == ("fail", "corrupt_job")
    j = job()
    j[S("seq")] = j.pop("seq")
    assert check(req(job=j)) == ("fail", "corrupt_job")
    r = req()
    r[None] = 1
    assert check(r) == ("fail", "malformed_bury_request")


def test_inert_str_subclass_keys_are_refused_in_request_and_job():
    class Evil(str):
        def __eq__(self, other):
            raise AssertionError("eq ran")

        __hash__ = str.__hash__

    r = {}
    for k in REQ_KEYS:
        dict.__setitem__(r, Evil(k) if k == "op" else k, req()[k])
    assert run(r) == ("fail", "malformed_bury_request")
    j = {}
    for k in JOB_KEYS:
        dict.__setitem__(j, Evil(k) if k == "seq" else k, job()[k])
    assert run(req(job=j)) == ("fail", "corrupt_job")


@pytest.mark.parametrize("bad", [None, 1, "bury", [], (), b"x", S("x"), D(req()), math.nan, True])
def test_non_dict_requests_are_malformed(bad):
    assert check(bad) == ("fail", "malformed_bury_request")


def test_precedence_request_then_integrity_then_status():
    # malformed request beats a corrupt job, a corrupt job beats not-dead
    assert check(req(now=-1, job=job(seq=-1))) == ("fail", "malformed_bury_request")
    assert check(req(reason="x", job=job(status="ready", attempts=0, payload=math.nan))) == (
        "fail",
        "malformed_bury_request",
    )
    assert check(req(job=job(status="ready", attempts=0, payload=math.nan))) == (
        "fail",
        "corrupt_job",
    )
    assert check(req(job=job(status="ready", attempts=9))) == ("fail", "corrupt_job")
    assert check(req(job=job(status="ready", attempts=0))) == ("fail", "job_not_dead")
    # a non-dict job is a request-shape fault, a dict job with defects is corrupt
    assert check(req(job=[])) == ("fail", "malformed_bury_request")
    assert check(req(job={})) == ("fail", "corrupt_job")


def test_rejected_calls_change_nothing_and_the_valid_follow_up_buries():
    r = req()
    first = run(r)
    for bad in [req(now=-1), jreq(seq=-1), jreq(status="ready", attempts=0), None]:
        run(bad)
    assert run(r) == first
    assert r == req()


# ---------- seeded properties ----------


def _rand_payload(rng, depth=0):
    kind = rng.choice(
        ["i", "f", "s", "n", "b", "l", "d"] if depth < 4 else ["i", "f", "s", "n", "b"]
    )
    if kind == "i":
        return rng.choice([0, 1, -7, 2**40, 10 ** rng.randint(0, 30)])
    if kind == "f":
        return rng.choice([0.0, -0.0, 1.5, 1e-9, 123456.789])
    if kind == "s":
        return rng.choice(["", "a", "é", "\U0001f600", "x" * 40])
    if kind == "n":
        return None
    if kind == "b":
        return rng.random() < 0.5
    if kind == "l":
        return [_rand_payload(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {
        rng.choice("abcdef") + str(i): _rand_payload(rng, depth + 1)
        for i in range(rng.randint(0, 3))
    }


def _rand_req(rng):
    r = req(
        job=job(
            job_id="job1:" + "".join(rng.choice("0123456789abcdef") for _ in range(64)),
            seq=rng.choice([0, 1, 77, MAX_SEQ]),
            priority=rng.randint(0, 9),
            payload=_rand_payload(rng),
            status=rng.choice(["dead"] * 6 + ["ready", "done"]),
            attempts=5,
        ),
        reason=rng.choice(["exhausted", "permanent"]),
        now=rng.choice([0, 1, 12345, MAX_NOW]),
    )
    j = r["job"]
    if j["status"] == "ready":
        j["attempts"] = rng.randint(0, 4)
    if j["status"] == "done":
        j["attempts"] = rng.randint(1, 5)
    if rng.random() < 0.35:
        target = rng.choice(["req", "job"])
        tgt = r if target == "req" else j
        key = rng.choice(list(tgt))
        if key != "payload":
            tgt[key] = rng.choice(
                [None, -1, 2**60, "zz", 1.5, True, [], {}, S("dead"), MAX_NOW + 1]
            )
        else:
            tgt[key] = rng.choice([math.nan, (1,), b"x", {1: 1}, "\ud800", 10**4001])
    return r


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_requests(seed):
    rng = random.Random(seed)
    outcomes = set()
    for _ in range(60):
        r = _rand_req(rng)
        outcomes.add(check(r)[0] == "ok" or check(r)[1])
    assert outcomes


def test_random_corpus_covers_every_outcome():
    rng = random.Random(7)
    seen = set()
    for _ in range(1500):
        got = check(_rand_req(rng))
        seen.add("ok" if got[0] == "ok" else got[1])
    assert seen == {"ok", "malformed_bury_request", "corrupt_job", "job_not_dead"}


@pytest.mark.parametrize("seed", range(10))
def test_burial_is_deterministic_and_ids_are_unique_per_distinct_request(seed):
    rng = random.Random(1000 + seed)
    seen = {}
    for _ in range(80):
        r = _rand_req(rng)
        a, b = run(r), run(copy.deepcopy(r)) if a_safe(r) else run(r)
        assert a == b
        if a[0] == "ok":
            key = _canon(r)
            assert seen.setdefault(a[1]["entry_id"], key) == key


def a_safe(r):
    return _payload(r["job"].get("payload")) if type(r.get("job")) is dict else False


def test_lease_expiry_lower_bound_is_exact():
    ok = {"status": "leased", "attempts": 1, "lease_owner": "w1"}
    assert check(jreq(lease_expires_at=1, **ok)) == ("fail", "job_not_dead")
    assert check(jreq(lease_expires_at=MAX_NOW, **ok)) == ("fail", "job_not_dead")
    assert check(jreq(lease_expires_at=0, **ok)) == ("fail", "corrupt_job")
    assert check(jreq(lease_expires_at=MAX_NOW + 1, **ok)) == ("fail", "corrupt_job")
