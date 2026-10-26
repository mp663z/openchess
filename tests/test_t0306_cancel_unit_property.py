"""T0306: unit and seeded property checks of the production cancel decision.

An independent model (hand-built grammar and bound checks, a bounded
payload walk and hand-built pd1:/cd1: digests, no shared code with
server.jobs_cancel) is driven with seeded random requests, valid and
defective. Every call must agree with the model on the record or the
failure class, must leave the request byte-identical, and must return a
record that shares no container with it. Seeded, so a failure replays.
Unit rows pin every bound at both sides, the precedence between failure
classes, and hostile values (bool, float, str and int subclasses, inert
str-subclass keys, hostile payload roots) at every request and job field.
The request-unchanged check is a serialized-value comparison (a JSON or
repr snapshot), not a hostile-identity proof; rollback is proven for a
pure callable with fresh requests, with no persistent engine state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random

import pytest

from server.jobs_cancel import CancelError, cancel

JOB = "job1:" + "ab" * 32
JOB_HEX = set("0123456789abcdef")
MAX_NOW = 2**53 - 1
MAX_SEQ = 2**53 - 2
MAX_ATTEMPTS = 5
RECORD_ORDER = [
    "op",
    "job_id",
    "seq",
    "priority",
    "payload_digest",
    "status_before",
    "attempts",
    "requester",
    "cancelled_at",
    "decision_id",
]
CODES = {
    "malformed_cancel_request": "malformed_request",
    "corrupt_job": "corrupt_job",
    "job_not_cancellable": "job_not_cancellable",
}
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")


def job(**kw):
    base = {
        "job_id": JOB,
        "seq": 3,
        "priority": 4,
        "payload": {"k": [1, "x", None]},
        "status": "ready",
        "attempts": 0,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    base.update(kw)
    return base


def req(**kw):
    base = {"op": "cancel", "job": job(), "requester": "owner-1", "now": 100}
    base.update(kw)
    return base


def _snap(obj):
    try:
        return json.dumps(obj, default=repr)
    except ValueError:
        # cyclic hostile payloads cannot be JSON-encoded; repr is stable
        return repr(obj)


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _preimage(prefix, value):
    return (
        prefix
        + ":"
        + hashlib.sha256(
            prefix.encode("ascii") + b"\x00" + _canon(value).encode("utf-8")
        ).hexdigest()
    )


def _model_scalar_ok(node):
    kind = type(node)
    if node is None or kind is bool:
        return True
    if kind is int:
        if node.bit_length() > 13288:
            return False
        digits = 1 if node == 0 else 0
        n = abs(node)
        if n:
            digits = 0
            while n:
                n //= 10
                digits += 1
        return digits <= 4000
    if kind is float:
        return math.isfinite(node)
    if kind is str:
        try:
            node.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    return False


def _model_payload_ok(root):
    seen = set()
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        kind = type(node)
        if kind is dict:
            if depth > 64 or id(node) in seen:
                return False
            seen.add(id(node))
            for key, value in dict.items(node):
                if type(key) is not str or not _model_scalar_ok(key):
                    return False
                stack.append((value, depth + 1))
        elif kind is list:
            if depth > 64 or id(node) in seen:
                return False
            seen.add(id(node))
            for value in list.__iter__(node):
                stack.append((value, depth + 1))
        elif not _model_scalar_ok(node):
            return False
    return True


def _name_ok(value):
    return type(value) is str and 1 <= len(value) <= 64 and all(c in NAME_CHARS for c in value)


def _job_id_ok(value):
    return (
        type(value) is str
        and len(value) == 69
        and value.startswith("job1:")
        and all(c in JOB_HEX for c in value[5:])
    )


def _exact_int(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _keys_exact(mapping, fields):
    return (
        type(mapping) is dict
        and all(type(k) is str for k in mapping)
        and len(mapping) == len(fields)
        and set(mapping) == set(fields)
    )


def model(request):
    """('ok', record) or ('fail', class). Written from the contract text."""
    if not _keys_exact(request, ("op", "job", "requester", "now")):
        return ("fail", "malformed_cancel_request")
    if not (
        type(request["op"]) is str
        and request["op"] == "cancel"
        and type(request["job"]) is dict
        and _name_ok(request["requester"])
        and _exact_int(request["now"], 0, MAX_NOW)
    ):
        return ("fail", "malformed_cancel_request")
    j = request["job"]
    if not _keys_exact(
        j,
        (
            "job_id",
            "seq",
            "priority",
            "payload",
            "status",
            "attempts",
            "lease_owner",
            "lease_expires_at",
        ),
    ):
        return ("fail", "corrupt_job")
    status, attempts = j["status"], j["attempts"]
    owner, expires = j["lease_owner"], j["lease_expires_at"]
    if not (
        _job_id_ok(j["job_id"])
        and _exact_int(j["seq"], 0, MAX_SEQ)
        and _exact_int(j["priority"], 0, 9)
        and type(status) is str
        and status in ("ready", "leased", "done", "dead")
        and _exact_int(attempts, 0, MAX_ATTEMPTS)
    ):
        return ("fail", "corrupt_job")
    if status == "leased":
        if not (_name_ok(owner) and _exact_int(expires, 1, MAX_NOW) and attempts >= 1):
            return ("fail", "corrupt_job")
    elif owner is not None or expires is not None:
        return ("fail", "corrupt_job")
    if status == "dead" and attempts != MAX_ATTEMPTS:
        return ("fail", "corrupt_job")
    if status == "done" and attempts < 1:
        return ("fail", "corrupt_job")
    if not _model_payload_ok(j["payload"]):
        return ("fail", "corrupt_job")
    if status not in ("ready", "leased"):
        return ("fail", "job_not_cancellable")
    record = {
        "op": "cancel",
        "job_id": j["job_id"],
        "seq": j["seq"],
        "priority": j["priority"],
        "payload_digest": _preimage("pd1", j["payload"]),
        "status_before": status,
        "attempts": attempts,
        "requester": request["requester"],
        "cancelled_at": request["now"],
    }
    record["decision_id"] = _preimage("cd1", record)
    return ("ok", record)


def run(request):
    before = _snap(request)
    try:
        out = ("ok", cancel(request))
    except CancelError as error:
        assert type(error) is CancelError
        assert error.__cause__ is None and error.__context__ is None
        assert error.code == CODES[error.failure_class]
        assert error.retryable is False
        out = ("fail", error.failure_class)
    assert _snap(request) == before, "request was changed"
    if out[0] == "ok":
        record = out[1]
        assert type(record) is dict and all(type(k) is str for k in record)
        assert set(record) == set(RECORD_ORDER), sorted(set(RECORD_ORDER) - set(record))
        for field in (
            "op",
            "job_id",
            "payload_digest",
            "status_before",
            "requester",
            "decision_id",
        ):
            assert type(record[field]) is str, field
        for field in ("seq", "priority", "attempts", "cancelled_at"):
            assert type(record[field]) is int, field
    return out


def _typed(obj):
    """Type-exact canonical form: False is not 0 and a bool is not an int,
    which a plain == comparison hides (E's bool-priority retype witness)."""
    kind = type(obj)
    if kind is dict:
        return ("dict", tuple(sorted((k, _typed(v)) for k, v in obj.items())))
    if kind is list:
        return ("list", tuple(_typed(v) for v in obj))
    if kind is tuple:
        return ("tuple", tuple(_typed(v) for v in obj))
    return (kind.__name__, obj)


def check(request):
    got, want = run(request), model(request)
    assert _typed(got) == _typed(want), (request, got, want)
    return got


# ---------- unit: happy ----------


def test_ready_record_is_exact_and_ordered():
    record = check(req())[1]
    assert list(record) == RECORD_ORDER
    assert record["op"] == "cancel" and record["job_id"] == JOB
    assert record["seq"] == 3 and record["priority"] == 4
    assert record["status_before"] == "ready" and record["attempts"] == 0
    assert record["requester"] == "owner-1" and record["cancelled_at"] == 100
    assert record["payload_digest"] == _preimage("pd1", {"k": [1, "x", None]})
    for field in ("seq", "priority", "attempts", "cancelled_at"):
        assert type(record[field]) is int


def test_leased_record_carries_no_lease_fields():
    record = check(
        req(job=job(status="leased", attempts=2, lease_owner="w-1", lease_expires_at=150), now=120)
    )[1]
    assert record["status_before"] == "leased" and record["attempts"] == 2
    assert record["cancelled_at"] == 120
    assert "lease_owner" not in record and "lease_expires_at" not in record
    assert set(record) == set(RECORD_ORDER)


def test_payload_digest_binds_the_payload_and_stores_no_copy():
    a = check(req(job=job(payload={"k": 1})))[1]
    b = check(req(job=job(payload={"k": 2})))[1]
    assert a["payload_digest"] != b["payload_digest"]
    assert a["payload_digest"] == _preimage("pd1", {"k": 1})
    assert "payload" not in a


def test_scalar_payload_roots_cancel_and_digest_exactly():
    for scalar in (True, False, None, 7, "x", 1.5, [1], {"a": 1}):
        record = check(req(job=job(payload=scalar)))[1]
        assert record["payload_digest"] == _preimage("pd1", scalar)


def test_no_jitter_and_determinism():
    first = [run(req(now=5)) for _ in range(5)]
    assert all(x == first[0] for x in first)


def test_decision_id_binds_every_request_field():
    base = cancel(req(now=7))
    variants = [
        req(now=8),
        req(now=7, requester="owner-2"),
        req(now=7, job=job(seq=4)),
        req(now=7, job=job(priority=5)),
        req(now=7, job=job(payload={"k": [1, "x", None, 2]})),
        req(now=7, job=job(job_id="job1:" + "cd" * 32)),
        req(now=7, job=job(status="leased", attempts=1, lease_owner="w-1", lease_expires_at=150)),
    ]
    ids = {cancel(v)["decision_id"] for v in variants}
    assert base["decision_id"] not in ids and len(ids) == len(variants)


def test_result_shares_no_container_with_the_request_or_other_results():
    request = req()
    one = cancel(request)
    two = cancel(request)
    assert one == two and one is not two
    one["job_id"] = "x"
    assert cancel(request) == two
    assert request["job"]["job_id"] == JOB


def test_unicode_payload_digest_pins_the_ascii_escaped_preimage():
    # a non-ASCII payload string is canonical JSON-escaped (\\u00e9); an
    # ensure_ascii flip in production's encoder changes both digests, so
    # the literal preimages below pin the escape behavior exactly
    record = check(req(job=job(payload={"greeting": chr(0xE9)})))[1]
    assert record["payload_digest"] == (
        "pd1:ef5f00c1a46ab7d8fc2128206a3e23072a6632912de4a76ca7f553d7fd9c6031"
    )
    assert record["decision_id"] == (
        "cd1:24ad6c80b0bd77532f71948c916c806bc662f3618c9e52d4f07694f5fbfc6847"
    )
    assert record["payload_digest"] == _preimage("pd1", {"greeting": chr(0xE9)})


def test_typed_comparison_catches_a_bool_retype():
    # E's witness shape: a wrapper that retypes priority 0/1 to bool while
    # leaving the digest untouched passed all 116 tests when the model
    # comparison was plain ==. The battery's comparator must see it.
    request = req(job=job(priority=1))
    record = cancel(copy.deepcopy(request))
    lying = dict(record, priority=bool(record["priority"]))
    want = model(request)
    assert ("ok", lying) == want  # why == alone is not enough
    assert _typed(("ok", lying)) != _typed(want)


# ---------- unit: boundaries ----------


@pytest.mark.parametrize(
    "field,good,bad",
    [
        ("seq", [0, MAX_SEQ], [-1, MAX_SEQ + 1]),
        ("priority", [0, 9], [-1, 10]),
        ("attempts", [0, 5], [-1, 6]),
    ],
)
def test_job_int_bounds_are_exact(field, good, bad):
    for value in good:
        assert check(req(job=job(**{field: value})))[0] == "ok"
    for value in bad:
        assert check(req(job=job(**{field: value}))) == ("fail", "corrupt_job")


@pytest.mark.parametrize(
    "value,ok",
    [(0, True), (MAX_NOW, True), (-1, False), (MAX_NOW + 1, False), (True, False)],
)
def test_now_bound_is_exact(value, ok):
    got = check(req(now=value))
    assert got[0] == ("ok" if ok else "fail")
    if not ok:
        assert got == ("fail", "malformed_cancel_request")


def test_lease_invariants_are_exact():
    base = dict(status="leased", attempts=1, lease_owner="w-1", lease_expires_at=100)
    assert check(req(job=job(**base)))[0] == "ok"
    assert check(req(job=job(**dict(base, lease_expires_at=MAX_NOW))))[0] == "ok"
    assert check(req(job=job(**dict(base, lease_expires_at=0)))) == ("fail", "corrupt_job")
    assert check(req(job=job(**dict(base, lease_expires_at=MAX_NOW + 1)))) == (
        "fail",
        "corrupt_job",
    )
    assert check(req(job=job(**dict(base, attempts=0)))) == ("fail", "corrupt_job")
    assert check(req(job=job(**dict(base, lease_owner=None)))) == ("fail", "corrupt_job")
    assert check(req(job=job(**dict(base, lease_owner="!!")))) == ("fail", "corrupt_job")
    # a ready job holds neither lease field
    assert check(req(job=job(lease_owner="w-1"))) == ("fail", "corrupt_job")
    assert check(req(job=job(lease_expires_at=150))) == ("fail", "corrupt_job")


def test_done_and_dead_invariants_are_exact():
    for attempts in (1, 5):
        assert check(req(job=job(status="done", attempts=attempts))) == (
            "fail",
            "job_not_cancellable",
        )
    assert check(req(job=job(status="dead", attempts=5))) == ("fail", "job_not_cancellable")
    assert check(req(job=job(status="done", attempts=0))) == ("fail", "corrupt_job")
    assert check(req(job=job(status="dead", attempts=4))) == ("fail", "corrupt_job")
    assert check(req(job=job(status="dead", attempts=5, lease_owner="w-1"))) == (
        "fail",
        "corrupt_job",
    )


def test_payload_depth_and_int_digits_are_exact():
    deep_ok = deep_bad = "leaf"
    for _ in range(64):
        deep_ok = [deep_ok]
        deep_bad = [deep_bad]
    deep_bad = [deep_bad]  # 64 containers deep is allowed, 65 is not
    assert check(req(job=job(payload=deep_ok)))[0] == "ok"
    assert check(req(job=job(payload=deep_bad))) == ("fail", "corrupt_job")
    assert check(req(job=job(payload={"n": 10**3999})))[0] == "ok"
    # the largest 4000-digit int has bit_length exactly 13288: both the
    # pre-screen boundary and the digit boundary are pinned
    assert check(req(job=job(payload={"n": 10**4000 - 1})))[0] == "ok"
    assert check(req(job=job(payload={"n": 10**4000}))) == ("fail", "corrupt_job")
    assert check(req(job=job(payload={"n": -(10**4000)}))) == ("fail", "corrupt_job")
    # dict nesting is bounded exactly like list nesting
    dict_ok = dict_bad = "leaf"
    for _ in range(64):
        dict_ok = {"k": dict_ok}
        dict_bad = {"k": dict_bad}
    dict_bad = {"k": dict_bad}
    assert check(req(job=job(payload=dict_ok)))[0] == "ok"
    assert check(req(job=job(payload=dict_bad))) == ("fail", "corrupt_job")


def test_lowered_int_str_limit_fails_closed():
    # the same witness as the T0305 runtime proof, kept here so this file
    # alone kills a conversion-guard mutant: under a lowered interpreter
    # limit a legal-but-large payload int must be corrupt_job, never a raw
    # ValueError. Production-only: the independent model is not run under a
    # lowered limit (its own digest encoding would hit the same raw
    # conversion fault); model agreement is pinned in the normal
    # environment only.
    import sys

    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        try:
            cancel(req(job=job(payload={"n": 10**999})))
        except CancelError as error:
            assert error.failure_class == "corrupt_job"
            assert error.__cause__ is None and error.__context__ is None
        else:
            raise AssertionError("lowered-limit payload int was accepted")
    finally:
        sys.set_int_max_str_digits(limit)


# ---------- unit: malformed / corrupt / precedence ----------

JUNK = (None, True, False, 0, 1, -1, 2**53, 2**64, 1.0, 1.5, float("nan"), "", "x", b"x", [], {})


@pytest.mark.parametrize("field", ["op", "requester", "now"])
def test_every_scalar_request_field_rejects_hostile_values(field):
    for value in JUNK:
        got = check(req(**{field: value}))
        if got[0] == "ok":
            continue  # a value that is valid for this field (the model agrees)
        assert got == ("fail", "malformed_cancel_request"), (field, value)


@pytest.mark.parametrize(
    "field", ["job_id", "seq", "priority", "status", "attempts", "lease_owner", "lease_expires_at"]
)
def test_every_scalar_job_field_rejects_hostile_values(field):
    for value in JUNK:
        got = check(req(job=job(**{field: value})))
        if got[0] == "ok":
            continue
        assert got == ("fail", "corrupt_job"), (field, value)


def test_hostile_payload_roots_are_corrupt():
    for hostile in (b"b", (1,), object(), {1: "x"}, [b"x"], {"k": (2,)}):
        assert check(req(job=job(payload=hostile))) == ("fail", "corrupt_job")


def test_payload_alias_and_cycle_are_corrupt():
    shared = [1]
    assert check(req(job=job(payload=[shared, shared]))) == ("fail", "corrupt_job")
    cyclic = []
    cyclic.append(cyclic)
    assert check(req(job=job(payload=cyclic))) == ("fail", "corrupt_job")
    shared_dict = {"a": 1}
    assert check(req(job=job(payload={"x": shared_dict, "y": shared_dict}))) == (
        "fail",
        "corrupt_job",
    )


def test_payload_hostile_scalars_are_corrupt():
    assert check(req(job=job(payload={"n": float("nan")}))) == ("fail", "corrupt_job")
    assert check(req(job=job(payload={"n": float("inf")}))) == ("fail", "corrupt_job")
    assert check(req(job=job(payload={"s": chr(0xD800)}))) == ("fail", "corrupt_job")
    assert check(req(job=job(payload={chr(0xD800): 1}))) == ("fail", "corrupt_job")


class _Str(str):
    pass


class _Int(int):
    pass


def test_subclasses_of_exact_types_are_refused():
    assert check(req(job=job(job_id=_Str(JOB)))) == ("fail", "corrupt_job")
    assert check(req(op=_Str("cancel"))) == ("fail", "malformed_cancel_request")
    assert check(req(requester=_Str("owner-1"))) == ("fail", "malformed_cancel_request")
    assert check(req(now=_Int(100))) == ("fail", "malformed_cancel_request")
    assert check(req(job=job(seq=_Int(3)))) == ("fail", "corrupt_job")
    assert check(req(job=job(attempts=True))) == ("fail", "corrupt_job")
    assert check(req(job=job(status=_Str("ready")))) == ("fail", "corrupt_job")
    assert check(req(job=job(payload=[_Int(1)]))) == ("fail", "corrupt_job")


def test_inert_str_subclass_keys_are_refused_at_both_levels():
    request = {(_Str(k) if k == "op" else k): v for k, v in req().items()}
    assert check(request) == ("fail", "malformed_cancel_request")
    hostile_job = {(_Str(k) if k == "seq" else k): v for k, v in job().items()}
    assert check(req(job=hostile_job)) == ("fail", "corrupt_job")


def test_missing_extra_and_non_str_keys_are_refused():
    for key in req():
        assert check({k: v for k, v in req().items() if k != key})[0] == "fail"
    assert check(dict(req(), extra=1)) == ("fail", "malformed_cancel_request")
    assert check({**req(), 1: 2}) == ("fail", "malformed_cancel_request")
    for key in job():
        assert check(req(job={k: v for k, v in job().items() if k != key})) == (
            "fail",
            "corrupt_job",
        )
    assert check(req(job=dict(job(), extra=1))) == ("fail", "corrupt_job")
    assert check(req(job={**job(), 1: 2})) == ("fail", "corrupt_job")


@pytest.mark.parametrize("junk", [None, [], (), "cancel", 5, object(), set()])
def test_non_dict_requests_are_malformed(junk):
    assert run(junk) == ("fail", "malformed_cancel_request")


def test_requester_and_worker_grammar_is_exact():
    assert check(req(requester="a" * 64))[0] == "ok"
    for bad in ("", "a" * 65, "has space", "tab\there", "owner-1\n"):
        assert check(req(requester=bad)) == ("fail", "malformed_cancel_request")
    leased = dict(status="leased", attempts=1, lease_owner="w-1", lease_expires_at=100)
    for bad in ("", "w" * 65, "bad owner"):
        assert check(req(job=job(**dict(leased, lease_owner=bad)))) == ("fail", "corrupt_job")


def test_job_id_grammar_is_exact():
    assert check(req(job=job(job_id="job1:" + "0123456789abcdef" * 4)))[0] == "ok"
    for bad in (
        "job1:" + "A" * 64,
        "job1:" + "a" * 63,
        "job1:" + "a" * 65,
        "job2:" + "a" * 64,
        " job1:" + "a" * 64,
        "job1:" + "a" * 64 + "\n",
        "job1:" + "a" * 63 + "g",
    ):
        assert check(req(job=job(job_id=bad))) == ("fail", "corrupt_job")


def test_precedence_request_then_job_then_status():
    # a malformed request wins over a corrupt job
    assert check(req(now=-1, job=job(attempts=9))) == ("fail", "malformed_cancel_request")
    # a corrupt job wins over a not-cancellable status
    assert check(req(job=job(status="done", attempts=9))) == ("fail", "corrupt_job")
    # a valid done or dead job is refused as not cancellable, not corrupt
    assert check(req(job=job(status="done", attempts=1))) == ("fail", "job_not_cancellable")


# ---------- unit: rollback (purity on every refusal) ----------


def test_rejected_requests_change_nothing_and_a_valid_follow_up_cancels():
    request = req(job=job(status="done", attempts=1))
    before = copy.deepcopy(request)
    assert run(request) == ("fail", "job_not_cancellable")
    assert request == before
    assert run(req())[0] == "ok"


# ---------- seeded properties ----------


def _random_payload(rng, depth=1):
    if depth >= 4 or rng.random() < 0.45:
        return rng.choice(
            [None, True, False, 0, -7, rng.randint(-(10**20), 10**20), 1.5, -0.0, "x", "", "k"]
        )
    if rng.random() < 0.5:
        return [_random_payload(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {
        rng.choice(["a", "b", "k", "zz"]): _random_payload(rng, depth + 1)
        for _ in range(rng.randint(0, 3))
    }


def _random_valid(rng):
    status = rng.choice(["ready", "leased", "done", "dead"])
    if status == "ready":
        attempts, owner, expires = 0, None, None
    elif status == "leased":
        attempts = rng.randint(1, MAX_ATTEMPTS)
        owner, expires = "w-1", rng.randint(1, MAX_NOW)
    elif status == "done":
        attempts, owner, expires = rng.randint(1, MAX_ATTEMPTS), None, None
    else:
        attempts, owner, expires = MAX_ATTEMPTS, None, None
    return req(
        job=job(
            seq=rng.choice([0, 1, rng.randint(0, MAX_SEQ), MAX_SEQ]),
            priority=rng.randint(0, 9),
            payload=_random_payload(rng),
            status=status,
            attempts=attempts,
            lease_owner=owner,
            lease_expires_at=expires,
        ),
        requester=rng.choice(["owner-1", "w", "a" * 64]),
        now=rng.choice([0, 1, rng.randint(0, MAX_NOW), MAX_NOW]),
    )


def _damage(rng, request):
    request = copy.deepcopy(request)
    target = rng.choice(["op", "requester", "now", "job", "key", "j.scalar", "j.payload", "j.key"])
    if target == "key":
        request[rng.choice(["x", "now"])] = 1
    elif target == "j.key":
        request["job"][rng.choice(["x", "seq"])] = 1
    elif target == "j.scalar":
        field = rng.choice(
            ["job_id", "seq", "priority", "status", "attempts", "lease_owner", "lease_expires_at"]
        )
        request["job"][field] = rng.choice(JUNK)
    elif target == "j.payload":
        shared = [1]
        request["job"]["payload"] = rng.choice(
            [b"b", (1,), {"n": float("nan")}, [shared, shared], {"n": 10**4001}, chr(0xD800)]
        )
    else:
        request[target] = rng.choice(JUNK)
    return request


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_requests(seed):
    rng = random.Random(seed)
    for _ in range(120):
        request = _random_valid(rng)
        if rng.random() < 0.5:
            request = _damage(rng, request)
        check(request)


@pytest.mark.parametrize("seed", range(20))
def test_record_fields_are_exact_functions_of_the_request(seed):
    rng = random.Random(1000 + seed)
    for _ in range(60):
        request = _random_valid(rng)
        out = run(request)
        want = model(request)
        assert out == want
        if out[0] == "ok":
            record = out[1]
            assert list(record) == RECORD_ORDER
            assert record["cancelled_at"] == request["now"]
            assert record["payload_digest"] == _preimage("pd1", request["job"]["payload"])
            for field in ("seq", "priority", "attempts", "cancelled_at"):
                assert type(record[field]) is int, field


@pytest.mark.parametrize("seed", range(10))
def test_replay_is_deterministic_and_ids_are_unique_per_distinct_request(seed):
    rng = random.Random(3000 + seed)
    seen = {}
    for _ in range(200):
        request = _random_valid(rng)
        out = run(request)
        assert out == run(copy.deepcopy(request))
        if out[0] == "ok":
            key = _snap(request)
            ident = out[1]["decision_id"]
            assert seen.setdefault(ident, key) == key
