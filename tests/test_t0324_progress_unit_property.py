"""T0324: unit and seeded property checks of the production progress report.

An independent model (plain arithmetic and a hand-built `pg1:` digest, no
shared code with server.jobs_progress) is driven with seeded random report
chains, valid and defective. Every call must agree with the model on the
record or the failure class, must leave the request (including the previous
record) byte-identical, and must return a result that shares no container
with it. Seeded, so a failure replays. Unit rows pin every bound on both
sides, the previous-record integrity checks one defect at a time (with the
digest recomputed so only the named defect is present), the chain rules, and
hostile values at every field.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re

import pytest

from server.jobs_progress import ProgressError, report

JOB = "job1:" + "ab" * 32
JOB2 = "job1:" + "cd" * 32
MAX = 2**53 - 1
CODES = {
    "malformed_progress_request": "malformed_request",
    "corrupt_previous_record": "corrupt_record",
    "progress_conflict": "progress_conflict",
}
FIELDS = ["op", "job_id", "worker", "done", "total", "percent_bp", "reported_at", "previous_id"]
RECORD_ORDER = FIELDS + ["progress_id"]
WORKER = re.compile(r"[A-Za-z0-9._:-]{1,64}")
JOB_RE = re.compile(r"job1:[0-9a-f]{64}")
PID_RE = re.compile(r"pg1:[0-9a-f]{64}")
JUNK = (None, True, False, 0, 1, -1, 2**53, 2**64, 1.0, 1.5, float("nan"), "", "x", b"x", [], {})


def req(**kw):
    base = {
        "op": "report",
        "job_id": JOB,
        "worker": "w-1",
        "done": 0,
        "total": 10,
        "now": 0,
        "previous": None,
    }
    base.update(kw)
    return base


def _snap(obj):
    return json.dumps(obj, default=repr)


def _pid(body):
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "pg1:" + hashlib.sha256(b"pg1" + bytes([0]) + text.encode()).hexdigest()


def forge(**fields):
    """A well-formed previous record with its digest recomputed."""
    body = {
        "op": "report",
        "job_id": JOB,
        "worker": "w-1",
        "done": 3,
        "total": 10,
        "percent_bp": 3000,
        "reported_at": 50,
        "previous_id": None,
    }
    body.update(fields)
    body["progress_id"] = _pid({k: v for k, v in body.items() if k != "progress_id"})
    return body


def _exact_int(v, lo, hi):
    return type(v) is int and lo <= v <= hi


def _keys(mapping, names):
    return type(mapping) is dict and set(map(type, mapping)) <= {str} and set(mapping) == set(names)


def _grammar(v, rx):
    return type(v) is str and rx.fullmatch(v) is not None


def model(request):
    if not _keys(request, ["op", "job_id", "worker", "done", "total", "now", "previous"]):
        return ("fail", "malformed_progress_request")
    total = request["total"]
    if not (
        type(request["op"]) is str
        and request["op"] == "report"
        and _grammar(request["job_id"], JOB_RE)
        and _grammar(request["worker"], WORKER)
        and _exact_int(total, 1, MAX)
        and _exact_int(request["done"], 0, total)
        and _exact_int(request["now"], 0, MAX)
        and (request["previous"] is None or type(request["previous"]) is dict)
    ):
        return ("fail", "malformed_progress_request")
    prev = request["previous"]
    if prev is not None:
        if not _keys(prev, RECORD_ORDER):
            return ("fail", "corrupt_previous_record")
        ptotal = prev["total"]
        if not (
            type(prev["op"]) is str
            and prev["op"] == "report"
            and _grammar(prev["job_id"], JOB_RE)
            and _grammar(prev["worker"], WORKER)
            and _exact_int(ptotal, 1, MAX)
            and _exact_int(prev["done"], 0, ptotal)
            and _exact_int(prev["reported_at"], 0, MAX)
            and type(prev["percent_bp"]) is int
            and prev["percent_bp"] == prev["done"] * 10000 // ptotal
            and (prev["previous_id"] is None or _grammar(prev["previous_id"], PID_RE))
            and _grammar(prev["progress_id"], PID_RE)
            and prev["progress_id"] == _pid({k: prev[k] for k in FIELDS})
        ):
            return ("fail", "corrupt_previous_record")
        if not (
            request["job_id"] == prev["job_id"]
            and request["worker"] == prev["worker"]
            and total == ptotal
            and prev["done"] < ptotal
            and request["done"] >= prev["done"]
            and request["now"] >= prev["reported_at"]
        ):
            return ("fail", "progress_conflict")
    record = {
        "op": "report",
        "job_id": request["job_id"],
        "worker": request["worker"],
        "done": request["done"],
        "total": total,
        "percent_bp": request["done"] * 10000 // total,
        "reported_at": request["now"],
        "previous_id": None if prev is None else prev["progress_id"],
    }
    record["progress_id"] = _pid(record)
    return ("ok", record)


def run(request):
    before = _snap(request)
    try:
        out = ("ok", report(request))
    except ProgressError as error:
        assert type(error) is ProgressError
        assert error.__cause__ is None and error.__context__ is None
        assert error.code == CODES[error.failure_class]
        assert error.retryable is False
        out = ("fail", error.failure_class)
    assert _snap(request) == before, "request was changed"
    return out


def check(request):
    got, want = run(request), model(request)
    assert got == want, (request, got, want)
    return got


# ---------- unit: happy ----------


def test_first_report_has_no_previous_link_and_exact_shape():
    record = check(req(done=3, now=7))[1]
    assert list(record) == RECORD_ORDER
    assert record["percent_bp"] == 3000 and record["previous_id"] is None
    assert record["reported_at"] == 7
    assert all(type(record[k]) is int for k in ("done", "total", "percent_bp", "reported_at"))


def test_chain_links_each_record_to_the_previous_progress_id():
    first = check(req(done=2, now=1))[1]
    second = check(req(done=5, now=2, previous=first))[1]
    third = check(req(done=10, now=3, previous=second))[1]
    assert second["previous_id"] == first["progress_id"]
    assert third["previous_id"] == second["progress_id"]
    assert len({first["progress_id"], second["progress_id"], third["progress_id"]}) == 3


def test_percent_is_floor_in_basis_points():
    cases = [(0, 3, 0), (1, 3, 3333), (2, 3, 6666), (3, 3, 10000), (1, 7, 1428), (1, 10**15, 0)]
    for done, total, bp in cases:
        assert check(req(done=done, total=total))[1]["percent_bp"] == bp
    assert check(req(done=MAX, total=MAX))[1]["percent_bp"] == 10000
    assert check(req(done=MAX - 1, total=MAX))[1]["percent_bp"] == 9999


def test_equal_done_is_a_heartbeat_and_equal_now_is_accepted():
    prev = forge()
    assert check(req(done=3, now=50, previous=prev))[0] == "ok"
    assert check(req(done=3, now=51, previous=prev))[0] == "ok"


def test_completion_is_reported_once_then_terminal():
    done = check(req(done=10, now=5))[1]
    assert done["percent_bp"] == 10000
    assert check(req(done=10, now=6, previous=done)) == ("fail", "progress_conflict")


def test_progress_id_binds_every_field():
    base = check(req(done=3, now=7))[1]["progress_id"]
    variants = [
        req(done=4, now=7),
        req(done=3, now=8),
        req(done=3, now=7, total=11),
        req(done=3, now=7, worker="w-2"),
        req(done=3, now=7, job_id=JOB2),
        req(done=3, now=60, previous=forge()),
    ]
    ids = {check(v)[1]["progress_id"] for v in variants}
    assert base not in ids and len(ids) == len(variants)


def test_determinism_and_no_shared_containers():
    previous = forge()
    request = req(done=4, now=60, previous=previous)
    one, two = report(request), report(request)
    assert one == two and one is not two
    one["worker"] = "zzz"
    assert report(request) == two


# ---------- unit: boundaries ----------


def test_request_bounds_are_exact():
    assert check(req(total=1, done=0))[0] == "ok"
    assert check(req(total=1, done=1))[0] == "ok"
    assert check(req(total=MAX, done=MAX, now=MAX))[0] == "ok"
    assert check(req(total=0, done=0)) == ("fail", "malformed_progress_request")
    assert check(req(total=MAX + 1, done=0)) == ("fail", "malformed_progress_request")
    assert check(req(done=11)) == ("fail", "malformed_progress_request")
    assert check(req(done=-1)) == ("fail", "malformed_progress_request")
    assert check(req(now=MAX + 1)) == ("fail", "malformed_progress_request")
    assert check(req(now=-1)) == ("fail", "malformed_progress_request")


def test_identifier_grammars_are_exact():
    for worker in ("a", "A.b_c:d-9", "x" * 64):
        assert check(req(worker=worker))[0] == "ok"
    for worker in ("", "x" * 65, "a b", "a/b", "é", "a\n", "\u0661"):
        assert check(req(worker=worker)) == ("fail", "malformed_progress_request")
    for job in (
        "job1:" + "A" * 64,
        "job1:" + "a" * 63,
        "job2:" + "a" * 64,
        "job1:" + "a" * 64 + "\n",
    ):
        assert check(req(job_id=job)) == ("fail", "malformed_progress_request")


def test_previous_record_bounds_are_exact_with_a_valid_digest():
    assert (
        check(req(total=1, done=1, now=60, previous=forge(done=0, total=1, percent_bp=0)))[0]
        == "ok"
    )
    big = forge(done=MAX - 1, total=MAX, percent_bp=9999, reported_at=MAX)
    assert check(req(total=MAX, done=MAX, now=MAX, previous=big))[0] == "ok"
    for bad in (
        forge(total=0, done=0, percent_bp=0),
        forge(total=MAX + 1, done=0, percent_bp=0),
        forge(done=11),
        forge(done=-1, percent_bp=0),
        forge(reported_at=MAX + 1),
        forge(reported_at=-1),
    ):
        assert check(req(previous=bad)) == ("fail", "corrupt_previous_record")


# ---------- unit: malformed ----------


@pytest.mark.parametrize("field", ["op", "job_id", "worker", "done", "total", "now", "previous"])
def test_every_request_field_rejects_hostile_values(field):
    for value in JUNK:
        got = check(req(**{field: value}))
        if got[0] == "fail":
            expected = (
                "corrupt_previous_record"
                if field == "previous" and type(value) is dict
                else "malformed_progress_request"
            )
            assert got == ("fail", expected), (field, value)


class _Str(str):
    pass


class _Int(int):
    pass


class _Dict(dict):
    pass


def test_subclasses_of_exact_types_are_refused():
    for field, value in [
        ("job_id", _Str(JOB)),
        ("worker", _Str("w-1")),
        ("op", _Str("report")),
        ("done", _Int(1)),
        ("total", _Int(10)),
        ("now", _Int(1)),
        ("previous", _Dict(forge())),
    ]:
        assert check(req(**{field: value})) == ("fail", "malformed_progress_request"), field
    assert check(req(done=True)) == ("fail", "malformed_progress_request")


def test_inert_str_subclass_keys_are_refused_in_request_and_previous():
    request = {(_Str("op") if k == "op" else k): v for k, v in req().items()}
    assert check(request) == ("fail", "malformed_progress_request")
    for key in RECORD_ORDER:
        prev = {(_Str(k) if k == key else k): v for k, v in forge().items()}
        assert check(req(previous=prev)) == ("fail", "corrupt_previous_record"), key


def test_missing_extra_and_non_str_request_keys_are_refused():
    for key in req():
        assert check({k: v for k, v in req().items() if k != key}) == (
            "fail",
            "malformed_progress_request",
        )
    assert check(dict(req(), extra=1)) == ("fail", "malformed_progress_request")
    assert check({**req(), 1: 2}) == ("fail", "malformed_progress_request")


@pytest.mark.parametrize("junk", [None, [], (), "report", 5, object(), set()])
def test_non_dict_requests_are_malformed(junk):
    assert run(junk) == ("fail", "malformed_progress_request")


# ---------- unit: corrupt previous (one defect at a time) ----------


def test_previous_field_defects_are_corrupt_even_with_a_valid_digest():
    for field in FIELDS:
        for value in JUNK + ("job1:" + "e" * 64, "pg1:" + "e" * 64, "report2"):
            if type(value) is bytes:
                continue  # not JSON; the digest cannot be recomputed for it
            if forge(**{field: value}) == forge():
                continue
            got = check(req(done=5, now=60, previous=forge(**{field: value})))
            if got[0] == "fail":
                assert got[1] in ("corrupt_previous_record", "progress_conflict"), (field, value)


def test_previous_wrong_percent_is_corrupt():
    for bp in (2999, 3001, 0, 10000):
        assert check(req(previous=forge(percent_bp=bp))) == ("fail", "corrupt_previous_record")


def test_previous_digest_must_recompute():
    prev = forge()
    for bad in (
        "pg1:" + "0" * 64,
        prev["progress_id"][:-1] + ("0" if prev["progress_id"][-1] != "0" else "1"),
        "pg2:" + prev["progress_id"][4:],
        prev["progress_id"].upper(),
        None,
    ):
        assert check(req(previous=dict(prev, progress_id=bad))) == (
            "fail",
            "corrupt_previous_record",
        )
    tampered = dict(prev, done=4, percent_bp=4000)  # digest left stale
    assert check(req(done=5, now=60, previous=tampered)) == ("fail", "corrupt_previous_record")


def test_previous_missing_or_extra_keys_are_corrupt():
    prev = forge()
    for key in prev:
        assert check(req(previous={k: v for k, v in prev.items() if k != key})) == (
            "fail",
            "corrupt_previous_record",
        )
    assert check(req(previous=dict(prev, extra=1))) == ("fail", "corrupt_previous_record")
    assert check(req(previous={})) == ("fail", "corrupt_previous_record")
    assert check(req(previous={**prev, 1: 2})) == ("fail", "corrupt_previous_record")


# ---------- unit: chain conflicts ----------


def test_chain_conflicts_are_exact():
    prev = forge()  # done 3 of 10 at 50
    assert check(req(done=2, now=60, previous=prev)) == ("fail", "progress_conflict")
    assert check(req(done=4, now=49, previous=prev)) == ("fail", "progress_conflict")
    assert check(req(done=4, now=60, total=11, previous=prev)) == ("fail", "progress_conflict")
    assert check(req(done=4, now=60, job_id=JOB2, previous=prev)) == ("fail", "progress_conflict")
    assert check(req(done=4, now=60, worker="w-2", previous=prev)) == ("fail", "progress_conflict")
    assert check(req(done=4, now=50, previous=prev))[0] == "ok"
    assert check(req(done=3, now=50, previous=prev))[0] == "ok"
    complete = forge(done=10, percent_bp=10000)
    assert check(req(done=10, now=60, previous=complete)) == ("fail", "progress_conflict")
    almost = forge(done=9, percent_bp=9000)
    assert check(req(done=10, now=60, previous=almost))[0] == "ok"


def test_precedence_request_then_previous_integrity_then_chain():
    bad_prev = dict(forge(), progress_id="pg1:" + "0" * 64)
    assert check(req(done=99, previous=bad_prev)) == ("fail", "malformed_progress_request")
    assert check(req(done=2, now=0, previous=bad_prev)) == ("fail", "corrupt_previous_record")
    assert check(req(done=2, now=0, previous=forge())) == ("fail", "progress_conflict")


# ---------- unit: rollback ----------


def test_rejected_reports_change_nothing_and_the_valid_follow_up_reports():
    previous = forge()
    request = req(done=2, now=60, previous=previous)
    before = copy.deepcopy(request)
    assert run(request) == ("fail", "progress_conflict")
    assert request == before
    assert run(req(done=4, now=60, previous=previous))[0] == "ok"


# ---------- seeded properties ----------


def _random_request(rng, prev):
    total = prev["total"] if prev and rng.random() < 0.9 else rng.choice([1, 2, 3, 7, 10, 100, MAX])
    lo = prev["done"] if prev else 0
    done = rng.choice([lo, min(total, lo + 1), total, rng.randint(0, total)])
    now = rng.choice([0, 1, rng.randint(0, 10**6), MAX])
    if prev and rng.random() < 0.8:
        now = max(now, prev["reported_at"])
    request = req(
        done=done,
        total=total,
        now=now,
        job_id=prev["job_id"] if prev and rng.random() < 0.95 else rng.choice([JOB, JOB2]),
        worker=prev["worker"] if prev and rng.random() < 0.95 else rng.choice(["w-1", "w-2"]),
        previous=prev,
    )
    return request


def _damage(rng, request):
    request = copy.deepcopy(request)
    target = rng.choice(
        ["op", "job_id", "worker", "done", "total", "now", "previous", "key", "prev"]
    )
    if target == "key":
        request[rng.choice(["x", "op"])] = 1
    elif target == "prev" and request["previous"]:
        request["previous"][rng.choice(RECORD_ORDER)] = rng.choice(JUNK)
    elif target != "prev":
        request[target] = rng.choice(JUNK)
    return request


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_chains(seed):
    rng = random.Random(seed)
    prev = None
    for _ in range(60):
        request = _random_request(rng, prev)
        if rng.random() < 0.3:
            check(_damage(rng, request))
        got = check(request)
        if got[0] == "ok":
            prev = got[1] if got[1]["done"] < got[1]["total"] or rng.random() < 0.3 else None
        elif rng.random() < 0.2:
            prev = None


@pytest.mark.parametrize("seed", range(20))
def test_a_chain_never_goes_backwards_and_ends_at_completion(seed):
    rng = random.Random(500 + seed)
    total = rng.choice([1, 5, 10, 1000])
    prev, last_done, last_at = None, 0, 0
    for _ in range(40):
        done = min(total, last_done + rng.randint(0, 3))
        now = last_at + rng.randint(0, 5)
        record = report(req(done=done, total=total, now=now, previous=prev))
        assert record["done"] >= last_done and record["reported_at"] >= last_at
        assert record["percent_bp"] == done * 10000 // total
        prev, last_done, last_at = record, done, now
        if done == total:
            assert run(req(done=total, total=total, now=now, previous=prev)) == (
                "fail",
                "progress_conflict",
            )
            break


@pytest.mark.parametrize("seed", range(10))
def test_replay_is_deterministic_and_ids_are_unique_per_distinct_request(seed):
    rng = random.Random(900 + seed)
    seen = {}
    prev = None
    for _ in range(150):
        request = _random_request(rng, prev)
        out = run(request)
        assert out == run(copy.deepcopy(request))
        if out[0] == "ok":
            key = _snap({k: v for k, v in request.items() if k != "previous"}) + (
                request["previous"]["progress_id"] if request["previous"] else ""
            )
            assert seen.setdefault(out[1]["progress_id"], key) == key
            prev = out[1] if out[1]["done"] < out[1]["total"] else None
