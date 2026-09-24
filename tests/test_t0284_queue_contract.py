"""T0284: jobs queue contract - the contract document
(data/contracts/queue.yaml) is normative; this battery holds the
contract-derived REFERENCE engine and proves happy, boundary,
malformed and rollback behavior against it.

The queue is a pure state machine over an explicit state
{jobs, next_seq} and an explicit logical clock (`now` on every
time-dependent request): enqueue (idempotent by dedupe key),
claim (lowest priority number, then lowest seq, among ready or
lease-expired jobs; leases and counts the attempt; a job that has
used max_attempts becomes dead instead), ack and nack (only by the
lease owner while the lease is live). Every transition validates
the full state first, works on a detached canonical copy and
commits LAST in place: a rejection leaves state and request
bit-identical.

DESIGN CAUTION: the reference engine is derived from the same
contract document, so this battery proves contract CONSISTENCY,
not production behavior; the later implement task must run the
same cases against a separately built runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0275_crash_resume_contract import (  # noqa: E402
    _scalar_ok,
)
from tools import crash_resume_contract_lint as _resume_lint  # noqa: E402
from tools.queue_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_DIGITS,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_STATE = _CC["state"]
_RECORD_FIELDS = _CC["record"]["fields"]
_STATE_FIELDS = _STATE["fields"]
_JOB_FIELDS = _STATE["job_fields"]
_STATUSES = tuple(_STATE["statuses"])
MAX_JOBS = _STATE["max_jobs"]
MAX_ATTEMPTS = _STATE["max_attempts"]
_OPS = {spec["op"]: spec["fields"] for spec in _CC["request"]["operations"]}
_IDS = _CC["identifiers"]
_JOB_RE = re.compile(_IDS["job_id"]["grammar"], re.ASCII)
_KEY_RE = re.compile(_IDS["dedupe_key"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_IDS["worker"]["grammar"], re.ASCII)
_STATE_RE = re.compile(_IDS["state_id"]["grammar"], re.ASCII)
MAX_NOW = 2**53 - 1
MAX_LEASE_MS = 3_600_000
MAX_PRIORITY = 9


class QueueError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise QueueError(cls, FAILURE_MAPPING[cls])


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def job_id_for(dedupe_key):
    return "job1:" + hashlib.sha256(f"job1|{dedupe_key}".encode()).hexdigest()


def state_id_for(state):
    return "qs1:" + hashlib.sha256(_canon(state).encode()).hexdigest()


def empty_state():
    return {"jobs": [], "next_seq": 0}


def _admissible(roots, seen):
    """TOTAL iterative admission of payload values, sharing `seen`
    across roots: exact built-in JSON only, bounded ints, depth at
    most MAX_DEPTH, no container met twice (cycle OR alias, also
    ACROSS payloads). Never recurses, never raises."""
    stack = [(root, 1) for root in roots]
    while stack:
        node, depth = stack.pop()
        kind = type(node)
        if kind is list or kind is dict:
            if depth > MAX_DEPTH or id(node) in seen:
                return False
            seen.add(id(node))
            if kind is dict:
                for key, value in dict.items(node):
                    if type(key) is not str or not _scalar_ok(key):
                        return False
                    stack.append((value, depth + 1))
            else:
                for value in list.__iter__(node):
                    stack.append((value, depth + 1))
        elif not _scalar_ok(node):
            return False
    return True


def _exact_keys(mapping, fields):
    """KEY-TYPE GUARD first (hostile colliding keys never reach a
    set/hash comparison), then exact key-set equality."""
    keys = list(dict.keys(mapping))
    if not all(type(key) is str for key in keys):
        return False
    return set(keys) == set(fields)


def _int_in(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _grammar(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _validate_state(state):
    """corrupt_queue on ANY shape, type, grammar or invariant
    violation; returns nothing, never raises raw."""
    if type(state) is not dict or not _exact_keys(state, _STATE_FIELDS):
        _fail("corrupt_queue")
    jobs, next_seq = state["jobs"], state["next_seq"]
    if type(jobs) is not list or not _int_in(next_seq, 0, MAX_NOW):
        _fail("corrupt_queue")
    if len(jobs) > MAX_JOBS:
        _fail("corrupt_queue")
    seen_ids, prior_seq, containers = set(), -1, set()
    for job in list.__iter__(jobs):
        if type(job) is not dict or not _exact_keys(job, _JOB_FIELDS):
            _fail("corrupt_queue")
        jid, seq = job["job_id"], job["seq"]
        if not _grammar(jid, _JOB_RE) or jid in seen_ids:
            _fail("corrupt_queue")
        seen_ids.add(jid)
        # strictly increasing seq, all below next_seq
        if not _int_in(seq, prior_seq + 1, next_seq - 1):
            _fail("corrupt_queue")
        prior_seq = seq
        if not _int_in(job["priority"], 0, MAX_PRIORITY):
            _fail("corrupt_queue")
        status, attempts = job["status"], job["attempts"]
        if type(status) is not str or status not in _STATUSES:
            _fail("corrupt_queue")
        if not _int_in(attempts, 0, MAX_ATTEMPTS):
            _fail("corrupt_queue")
        owner, expires = job["lease_owner"], job["lease_expires_at"]
        if status == "leased":
            if not _grammar(owner, _WORKER_RE) or not _int_in(expires, 1, MAX_NOW):
                _fail("corrupt_queue")
            if attempts < 1:
                _fail("corrupt_queue")
        elif owner is not None or expires is not None:
            _fail("corrupt_queue")
        if status == "dead" and attempts != MAX_ATTEMPTS:
            _fail("corrupt_queue")
        if status == "done" and attempts < 1:
            _fail("corrupt_queue")
        if not _admissible([job["payload"]], containers):
            _fail("corrupt_queue")


def _validate_request(request, clock_limit=MAX_NOW):
    """malformed_queue_request on ANY shape/type/grammar/bound
    violation; returns a FROZEN detached copy of the exact values."""
    if type(request) is not dict:
        _fail("malformed_queue_request")
    keys = list(dict.keys(request))
    if not all(type(key) is str for key in keys) or "op" not in keys:
        _fail("malformed_queue_request")
    op = request["op"]
    if type(op) is not str or op not in _OPS:
        _fail("malformed_queue_request")
    if set(keys) != set(_OPS[op]):
        _fail("malformed_queue_request")
    frozen = {"op": op}
    if op == "enqueue":
        if not _grammar(request["dedupe_key"], _KEY_RE):
            _fail("malformed_queue_request")
        if not _int_in(request["priority"], 0, MAX_PRIORITY):
            _fail("malformed_queue_request")
        if not _admissible([request["payload"]], set()):
            _fail("malformed_queue_request")
        frozen["dedupe_key"] = request["dedupe_key"]
        frozen["priority"] = request["priority"]
        frozen["payload"] = json.loads(_canon(request["payload"]))
        return frozen
    if not _grammar(request["worker"], _WORKER_RE):
        _fail("malformed_queue_request")
    if not _int_in(request["now"], 0, MAX_NOW):
        _fail("malformed_queue_request")
    frozen["worker"] = request["worker"]
    frozen["now"] = request["now"]
    if op == "claim":
        if not _int_in(request["lease_ms"], 1, MAX_LEASE_MS):
            _fail("malformed_queue_request")
        if request["now"] + request["lease_ms"] > clock_limit:
            _fail("malformed_queue_request")
        frozen["lease_ms"] = request["lease_ms"]
    else:
        if not _grammar(request["job_id"], _JOB_RE):
            _fail("malformed_queue_request")
        frozen["job_id"] = request["job_id"]
    return frozen


def _claimable(job, now):
    return job["status"] == "ready" or (
        job["status"] == "leased" and job["lease_expires_at"] <= now
    )


class QueueEngine:
    """The contract's pinned queue transitions."""

    SEQ_LIMIT = MAX_NOW  # a new job needs next_seq below this
    CLOCK_LIMIT = MAX_NOW  # now + lease_ms may not pass this

    def apply(self, state, request):
        req = _validate_request(request, self.CLOCK_LIMIT)
        _validate_state(state)
        work = json.loads(_canon(state))  # detached canonical copy
        job = self._transition(work, req)
        receipt = {
            "op": req["op"],
            "job_id": None if job is None else job["job_id"],
            "status": None if job is None else job["status"],
            "attempts": None if job is None else job["attempts"],
            "lease_expires_at": None if job is None else job["lease_expires_at"],
            "state_id": state_id_for(work),
        }
        # COMMIT LAST, in place
        state.clear()
        state.update(work)
        return receipt

    @staticmethod
    def _find(work, jid):
        for job in work["jobs"]:
            if job["job_id"] == jid:
                return job
        return None

    def _transition(self, work, req):
        op = req["op"]
        if op == "enqueue":
            jid = job_id_for(req["dedupe_key"])
            existing = self._find(work, jid)
            if existing is not None:
                if existing["priority"] != req["priority"] or _canon(existing["payload"]) != _canon(
                    req["payload"]
                ):
                    _fail("dedupe_conflict")  # never overwrite, never drop silently
                return existing  # identical re-enqueue: nothing changes
            if len(work["jobs"]) >= MAX_JOBS:
                _fail("capacity_exceeded")
            if work["next_seq"] >= self.SEQ_LIMIT:
                _fail("capacity_exceeded")  # seq space exhausted
            job = {
                "job_id": jid,
                "seq": work["next_seq"],
                "priority": req["priority"],
                "payload": req["payload"],
                "status": "ready",
                "attempts": 0,
                "lease_owner": None,
                "lease_expires_at": None,
            }
            work["jobs"].append(job)
            work["next_seq"] += 1
            return job
        if op == "claim":
            now = req["now"]
            order = sorted(
                (j for j in work["jobs"] if _claimable(j, now)),
                key=lambda j: (j["priority"], j["seq"]),
            )
            for job in order:
                if job["attempts"] >= MAX_ATTEMPTS:
                    job.update(status="dead", lease_owner=None, lease_expires_at=None)
                    continue
                job.update(
                    status="leased",
                    attempts=job["attempts"] + 1,
                    lease_owner=req["worker"],
                    lease_expires_at=now + req["lease_ms"],
                )
                return job
            return None
        job = self._find(work, req["job_id"])
        if job is None:
            _fail("unknown_job")
        if (
            job["status"] != "leased"
            or job["lease_owner"] != req["worker"]
            or not req["now"] < job["lease_expires_at"]
        ):
            _fail("lease_conflict")
        job.update(
            status="done" if op == "ack" else "ready",
            lease_owner=None,
            lease_expires_at=None,
        )
        return job


def _engine():
    return QueueEngine()


def enq(key, priority=5, payload=None):
    return {
        "op": "enqueue",
        "dedupe_key": key,
        "priority": priority,
        "payload": {"k": key} if payload is None else payload,
    }


def claim(worker="w1", now=0, lease_ms=1000):
    return {"op": "claim", "worker": worker, "now": now, "lease_ms": lease_ms}


def ack(jid, worker="w1", now=1):
    return {"op": "ack", "job_id": jid, "worker": worker, "now": now}


def nack(jid, worker="w1", now=1):
    return {"op": "nack", "job_id": jid, "worker": worker, "now": now}


def _run(state, *requests):
    """Apply in order; CLOSURE: every successful apply must leave a
    state that the engine itself accepts as valid."""
    eng = _engine()
    out = []
    for r in requests:
        out.append(eng.apply(state, r))
        try:
            _validate_state(state)
        except QueueError as exc:
            raise AssertionError(f"closure broken: {exc.failure_class}") from exc
    return out


class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _snap(obj, memo=None):
    """Identity-and-value snapshot that never hashes or compares a
    caller key (hostile keys stay inert) and terminates on cycles."""
    memo = {} if memo is None else memo
    kind = type(obj)
    if kind in (dict, list):
        if id(obj) in memo:
            return ("seen", _Pin(obj))
        memo[id(obj)] = True
        if kind is dict:
            return (
                "dict",
                _Pin(obj),
                tuple(
                    (k if type(k) is str else ("key", _Pin(k)), _snap(v, memo))
                    for k, v in dict.items(obj)
                ),
            )
        return ("list", _Pin(obj), tuple(_snap(v, memo) for v in list.__iter__(obj)))
    if kind is float and obj != obj:
        return ("nan",)
    return (kind.__name__, repr(obj) if kind in (int, float, str, bool, type(None)) else _Pin(obj))


def _raises(cls, state, request):
    before_s, before_r = _snap(state), _snap(request)
    with pytest.raises(QueueError) as err:
        _engine().apply(state, request)
    assert err.value.failure_class == cls
    assert err.value.code == FAILURE_MAPPING[cls]
    assert err.value.code in ERROR_ENUM
    # ATOMIC: rejection leaves both bit-identical, references included
    assert _snap(state) == before_s
    assert _snap(request) == before_r


def _queue(*keys, **prio):
    state = empty_state()
    for key in keys:
        _engine().apply(state, enq(key, prio.get(key, 5)))
    return state


# -- lint -----------------------------------------------------------------------


def test_lint_clean():
    lint()


def test_payload_bounds_match_the_linked_admission_bounds():
    assert (MAX_DEPTH, MAX_INT_DIGITS) == (_resume_lint.MAX_DEPTH, _resume_lint.MAX_INT_DIGITS)


def _mutants():
    def drop_op(cc):
        cc["request"]["operations"].pop()

    def swap_ordering(cc):
        cc["semantics"]["ordering"] = "claim-picks-lowest-seq"

    def extra_failure(cc):
        cc["failures"]["classes"].append("queue_paused")

    def mapping_drift(cc):
        cc["failures"]["mapping"]["lease_conflict"] = "internal"

    def max_attempts(cc):
        cc["state"]["max_attempts"] = 6

    def job_field(cc):
        cc["state"]["job_fields"].remove("lease_owner")

    def grammar(cc):
        cc["identifiers"]["job_id"]["grammar"] = "^job1:.*$"

    def open_failures(cc):
        cc["failures"]["closed"] = False

    def extra_section(cc):
        cc["oracle_boundary"] = {}

    def depth(cc):
        cc["request"]["bounds"]["payload"] = cc["request"]["bounds"]["payload"].replace("64", "65")

    def retryable(cc):
        cc["errors"]["shape"]["retryable_true_only_for"] = ["internal", "lease_conflict"]

    def link(cc):
        cc["links"]["idempotency_contract"] = "data/contracts/missing.yaml"

    return [
        drop_op,
        swap_ordering,
        extra_failure,
        mapping_drift,
        max_attempts,
        job_field,
        grammar,
        open_failures,
        extra_section,
        depth,
        retryable,
        link,
    ]


@pytest.mark.parametrize("mutate", _mutants(), ids=lambda f: f.__name__)
def test_mutations_fail_lint(mutate, tmp_path):
    doc = yaml.safe_load(CONTRACT.read_text())
    mutate(doc["contract"])
    path = tmp_path / "queue.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


# -- happy path -----------------------------------------------------------------


def test_enqueue_receipt_and_state():
    state = empty_state()
    (r,) = _run(state, enq("a", 3))
    assert set(r) == set(_RECORD_FIELDS)
    assert r == {
        "op": "enqueue",
        "job_id": job_id_for("a"),
        "status": "ready",
        "attempts": 0,
        "lease_expires_at": None,
        "state_id": state_id_for(state),
    }
    assert state == {
        "jobs": [
            {
                "job_id": job_id_for("a"),
                "seq": 0,
                "priority": 3,
                "payload": {"k": "a"},
                "status": "ready",
                "attempts": 0,
                "lease_owner": None,
                "lease_expires_at": None,
            }
        ],
        "next_seq": 1,
    }
    assert _JOB_RE.fullmatch(r["job_id"]) and _STATE_RE.fullmatch(r["state_id"])


def test_full_lifecycle_claim_ack():
    state = _queue("a")
    r1, r2 = _run(state, claim("w1", now=100, lease_ms=50), ack(job_id_for("a"), "w1", 149))
    assert (r1["status"], r1["attempts"], r1["lease_expires_at"]) == ("leased", 1, 150)
    assert (r2["status"], r2["attempts"], r2["lease_expires_at"]) == ("done", 1, None)
    job = state["jobs"][0]
    assert (job["lease_owner"], job["lease_expires_at"]) == (None, None)
    # done is terminal: nothing left to claim
    (r3,) = _run(state, claim("w2", now=10**6))
    assert r3["job_id"] is None and r3["status"] is None


def test_nack_returns_to_ready_keeping_attempts():
    state = _queue("a")
    _run(state, claim(now=0), nack(job_id_for("a"), now=5))
    job = state["jobs"][0]
    assert (job["status"], job["attempts"], job["lease_owner"]) == ("ready", 1, None)
    (r,) = _run(state, claim("w2", now=6))
    assert (r["job_id"], r["attempts"]) == (job_id_for("a"), 2)


def test_ordering_priority_then_seq():
    state = _queue("low", "hi1", "mid", "hi2", low=9, hi1=0, mid=4, hi2=0)
    got = [_run(state, claim(now=0))[0]["job_id"] for _ in range(4)]
    assert got == [job_id_for(k) for k in ("hi1", "hi2", "mid", "low")]
    assert _run(state, claim(now=0))[0]["job_id"] is None


def test_enqueue_is_idempotent_by_dedupe_key():
    state = _queue("a")
    _run(state, claim(now=0))
    before = copy.deepcopy(state)
    (r,) = _run(state, enq("a"))
    assert state == before  # nothing changed
    assert (r["job_id"], r["status"], r["attempts"]) == (job_id_for("a"), "leased", 1)
    assert r["state_id"] == state_id_for(before)


def test_expired_lease_is_reclaimed_at_least_once():
    state = _queue("a")
    _run(state, claim("w1", now=0, lease_ms=10))
    # still live at 9: nothing claimable
    assert _run(state, claim("w2", now=9))[0]["job_id"] is None
    # expiry is INCLUSIVE: at now == lease_expires_at it is reclaimable
    (r,) = _run(state, claim("w2", now=10, lease_ms=10))
    assert (r["job_id"], r["attempts"], r["lease_expires_at"]) == (job_id_for("a"), 2, 20)
    assert state["jobs"][0]["lease_owner"] == "w2"
    # the old owner lost the lease
    _raises("lease_conflict", state, ack(job_id_for("a"), "w1", now=11))


def test_dead_letter_after_max_attempts():
    state = _queue("a", "b", a=0, b=1)
    for i in range(MAX_ATTEMPTS):
        (r,) = _run(state, claim(now=i * 100, lease_ms=1))
        assert (r["job_id"], r["attempts"]) == (job_id_for("a"), i + 1)
    # the next claim marks a dead and moves on to b in the same step
    (r,) = _run(state, claim(now=10**4))
    assert r["job_id"] == job_id_for("b")
    a = state["jobs"][0]
    assert (a["status"], a["attempts"], a["lease_owner"]) == ("dead", MAX_ATTEMPTS, None)


def test_dead_marking_commits_on_an_empty_claim():
    state = _queue("a")
    for i in range(MAX_ATTEMPTS):
        _run(state, claim(now=i * 10, lease_ms=1))
    (r,) = _run(state, claim(now=10**4))
    assert r == {
        "op": "claim",
        "job_id": None,
        "status": None,
        "attempts": None,
        "lease_expires_at": None,
        "state_id": state_id_for(state),
    }
    assert state["jobs"][0]["status"] == "dead"


def test_nack_at_max_attempts_then_claim_goes_dead():
    state = _queue("a")
    for i in range(MAX_ATTEMPTS):
        _run(state, claim(now=i * 10, lease_ms=5), nack(job_id_for("a"), now=i * 10 + 1))
    assert state["jobs"][0]["attempts"] == MAX_ATTEMPTS
    assert state["jobs"][0]["status"] == "ready"
    assert _run(state, claim(now=10**3))[0]["job_id"] is None
    assert state["jobs"][0]["status"] == "dead"


def test_determinism():
    def history():
        state = _queue("a", "b", "c", b=0)
        return state, _run(
            state,
            claim("w", 1),
            claim("w", 2),
            ack(job_id_for("b"), "w", 3),
            nack(job_id_for("a"), "w", 3),
        )

    assert history() == history()


def test_state_id_binds_the_whole_state():
    state = _queue("a")
    base = state_id_for(state)
    for path, value in (
        (("next_seq",), 5),
        (("jobs", 0, "priority"), 6),
        (("jobs", 0, "payload", "k"), "b"),
    ):
        other = copy.deepcopy(state)
        node = other
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        assert state_id_for(other) != base


def test_commit_is_in_place_and_detached():
    state = _queue("a")
    jobs_ref = state["jobs"]
    payload = {"deep": [1, 2]}
    request = enq("b", payload=payload)
    _run(state, request)
    assert state["jobs"] is not jobs_ref  # replaced by the committed copy
    payload["deep"].append(3)  # caller mutating afterwards is inert
    assert state["jobs"][1]["payload"] == {"deep": [1, 2]}
    assert request["payload"] is payload  # the request is never rewritten


# -- boundaries -----------------------------------------------------------------


def test_priority_bounds_inclusive():
    state = empty_state()
    _run(state, enq("a", 0), enq("b", MAX_PRIORITY))
    _raises("malformed_queue_request", state, enq("c", -1))
    _raises("malformed_queue_request", state, enq("c", MAX_PRIORITY + 1))


def test_lease_bounds_inclusive():
    state = _queue("a", "b")
    _run(state, claim(lease_ms=1), claim(lease_ms=MAX_LEASE_MS))
    _raises("malformed_queue_request", state, claim(lease_ms=0))
    _raises("malformed_queue_request", state, claim(lease_ms=MAX_LEASE_MS + 1))


def test_clock_bounds_inclusive():
    state = _queue("a")
    (r,) = _run(state, claim(now=MAX_NOW - 1, lease_ms=1))
    assert r["lease_expires_at"] == MAX_NOW
    _raises("malformed_queue_request", _queue("a"), claim(now=MAX_NOW, lease_ms=1))
    _raises("malformed_queue_request", _queue("a"), claim(now=-1))
    _raises("malformed_queue_request", _queue("a"), claim(now=MAX_NOW + 1))


def test_dedupe_key_and_worker_grammar_bounds():
    state = empty_state()
    _run(state, enq("k" * 128), enq("a.b_c:d-9"))
    _raises("malformed_queue_request", state, enq("k" * 129))
    _raises("malformed_queue_request", state, enq(""))
    _raises("malformed_queue_request", state, enq("a b"))
    _raises("malformed_queue_request", state, enq("a\n"))
    _raises("malformed_queue_request", state, enq("\u00e9"))
    _run(state, claim("w" * 64))
    _raises("malformed_queue_request", state, claim("w" * 65))


def test_payload_bounds():
    def nest(d):
        x = 0
        for _ in range(d - 1):
            x = [x]
        return [x] if d else 0

    state = empty_state()
    _run(state, enq("deep", payload=nest(MAX_DEPTH)), enq("big", payload={"n": 10**4000 - 1}))
    _raises("malformed_queue_request", state, enq("x", payload=nest(MAX_DEPTH + 1)))
    _raises("malformed_queue_request", state, enq("x", payload={"n": 10**4000}))
    _raises("malformed_queue_request", state, enq("x", payload={"f": float("nan")}))
    _raises("malformed_queue_request", state, enq("x", payload={"s": "\ud800"}))
    _raises("malformed_queue_request", state, enq("x", payload={1: "int-key"}))
    shared = [1]
    _raises("malformed_queue_request", state, enq("x", payload=[shared, shared]))
    cyc = []
    cyc.append(cyc)
    _raises("malformed_queue_request", state, enq("x", payload=cyc))


def test_capacity_boundary():
    state = {
        "jobs": [
            {
                "job_id": job_id_for(f"j{i}"),
                "seq": i,
                "priority": 5,
                "payload": None,
                "status": "done",
                "attempts": 1,
                "lease_owner": None,
                "lease_expires_at": None,
            }
            for i in range(MAX_JOBS - 1)
        ],
        "next_seq": MAX_JOBS - 1,
    }
    _run(state, enq("last"))
    assert len(state["jobs"]) == MAX_JOBS
    _raises("capacity_exceeded", state, enq("one-more"))
    # an EXISTING key at capacity is still idempotent, not a failure
    (r,) = _run(state, enq("last"))
    assert r["job_id"] == job_id_for("last")


def test_dedupe_conflict_fails_closed_never_overwrites():
    state = _queue("a")
    _raises("dedupe_conflict", state, enq("a", priority=0))
    _raises("dedupe_conflict", state, enq("a", payload={"other": 1}))
    _raises("dedupe_conflict", state, enq("a", payload={"k": "a", "x": None}))
    # canonical equality: key order and a detached equal value are identical
    s2 = empty_state()
    _run(s2, enq("m", 3, {"a": 1, "b": [1, 2]}))
    before = copy.deepcopy(s2)
    (r,) = _run(s2, enq("m", 3, {"b": [1, 2], "a": 1}))
    assert s2 == before and r["job_id"] == job_id_for("m")
    # 1 and 1.0 are different canonical JSON, so they conflict
    _run(s2, enq("f", 3, 1))
    _raises("dedupe_conflict", s2, enq("f", 3, 1.0))


def _state_at_seq(next_seq, *keys):
    state = {"jobs": [], "next_seq": next_seq}
    for i, key in enumerate(keys):
        state["jobs"].append(
            {
                "job_id": job_id_for(key),
                "seq": next_seq - len(keys) + i,
                "priority": 5,
                "payload": {"k": key},
                "status": "ready",
                "attempts": 0,
                "lease_owner": None,
                "lease_expires_at": None,
            }
        )
    return state


def test_seq_space_exhaustion_is_typed_and_never_bricks():
    # next_seq MAX_NOW: a new job is capacity_exceeded, state unchanged
    _raises("capacity_exceeded", _state_at_seq(MAX_NOW), enq("new"))
    # next_seq MAX_NOW-1: the last seq is usable, the result stays valid
    state = _state_at_seq(MAX_NOW - 1)
    (r,) = _run(state, enq("last"))
    assert state["jobs"][0]["seq"] == MAX_NOW - 1 and state["next_seq"] == MAX_NOW
    _validate_state(state)
    # the committed state keeps working: identical re-enqueue, claim, ack
    _run(state, enq("last"), claim(now=0), ack(r["job_id"], now=1))
    _raises("capacity_exceeded", state, enq("another"))
    assert state["jobs"][0]["status"] == "done"


def test_ack_lease_window_is_exclusive_of_expiry():
    state = _queue("a")
    _run(state, claim(now=0, lease_ms=10))
    _raises("lease_conflict", state, ack(job_id_for("a"), now=10))
    _run(state, ack(job_id_for("a"), now=9))


# -- malformed -----------------------------------------------------------------


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


@pytest.mark.parametrize(
    "request_",
    [
        None,
        [],
        "enqueue",
        {},
        {"op": "enqueue"},
        {"op": "purge"},
        {"op": 1},
        {"op": _StrSub("claim"), "worker": "w", "now": 0, "lease_ms": 1},
        {**claim(), "extra": 1},
        {k: v for k, v in claim().items() if k != "lease_ms"},
        {**claim(), "now": True},
        {**claim(), "now": 1.0},
        {**claim(), "now": _IntSub(1)},
        {**claim(), "worker": _StrSub("w")},
        {**claim(), "worker": None},
        {**enq("a"), "priority": False},
        {**enq("a"), "dedupe_key": 5},
        {**ack(job_id_for("a")), "job_id": "job1:" + "A" * 64},
        {**ack(job_id_for("a")), "job_id": "job1:" + "a" * 63},
        _DictSub(claim()),
        {1: "x", "op": "claim"},
    ],
    ids=lambda r: repr(r)[:40],
)
def test_malformed_requests(request_):
    _raises("malformed_queue_request", _queue("a"), request_)


def test_unknown_job():
    _raises("unknown_job", _queue("a"), ack(job_id_for("nope")))
    _raises("unknown_job", _queue("a"), nack(job_id_for("nope")))


@pytest.mark.parametrize("scenario", ["not_leased", "wrong_owner", "expired", "done"])
@pytest.mark.parametrize("make", [ack, nack], ids=["ack", "nack"])
def test_lease_conflicts(scenario, make):
    state = _queue("a")
    jid = job_id_for("a")
    if scenario != "not_leased":
        _run(state, claim("w1", now=0, lease_ms=10))
    if scenario == "done":
        _run(state, ack(jid, "w1", 1))
    worker = "w2" if scenario == "wrong_owner" else "w1"
    now = 10 if scenario == "expired" else 1
    _raises("lease_conflict", state, make(jid, worker, now))


def _good_state():
    state = _queue("a", "b", "c")
    _run(
        state,
        claim("w1", now=0, lease_ms=100),
        ack(job_id_for("a"), "w1", 1),
        claim("w1", now=2, lease_ms=100),
    )
    return state


def _corruptions():
    def job(i, **kw):
        def f(s):
            s["jobs"][i].update(kw)

        return f

    def swap_seq(s):
        s["jobs"][0]["seq"], s["jobs"][1]["seq"] = s["jobs"][1]["seq"], s["jobs"][0]["seq"]

    def dup_id(s):
        s["jobs"][1]["job_id"] = s["jobs"][0]["job_id"]

    def shared_payload(s):
        s["jobs"][1]["payload"] = s["jobs"][0]["payload"]

    def extra_key(s):
        s["jobs"][0]["note"] = 1

    def missing_key(s):
        del s["jobs"][0]["priority"]

    def too_many(s):
        s["jobs"] = s["jobs"] * (MAX_JOBS // 3 + 1)

    return {
        "not_dict": lambda s: s.clear() or s.update(x=1),
        "extra_top": lambda s: s.update(extra=1),
        "jobs_not_list": lambda s: s.update(jobs={}),
        "next_seq_bool": lambda s: s.update(next_seq=True),
        "next_seq_too_small": lambda s: s.update(next_seq=2),
        "seq_order": swap_seq,
        "dup_id": dup_id,
        "bad_id": job(0, job_id="job1:zz"),
        "bad_status": job(0, status="running"),
        "priority_out": job(0, priority=10),
        "attempts_out": job(0, attempts=MAX_ATTEMPTS + 1),
        "leased_no_owner": job(1, lease_owner=None),
        "leased_zero_attempts": job(1, attempts=0),
        "ready_with_owner": job(2, lease_owner="w1"),
        "ready_with_expiry": job(2, lease_expires_at=5),
        "dead_short": job(2, status="dead", attempts=1),
        "done_zero": job(0, attempts=0),
        "payload_nan": job(0, payload=float("nan")),
        "shared_payload": shared_payload,
        "extra_key": extra_key,
        "missing_key": missing_key,
        "too_many": too_many,
    }


@pytest.mark.parametrize("name", list(_corruptions()))
def test_corrupt_states(name):
    state = _good_state()
    _corruptions()[name](state)
    _raises("corrupt_queue", state, claim(now=3))


def test_corrupt_state_types():
    for bad in (None, [], "state", _DictSub(empty_state())):
        _raises("corrupt_queue", bad, claim())


def test_request_is_validated_before_state():
    _raises("malformed_queue_request", None, {"op": "purge"})


# -- hostile keys -----------------------------------------------------------------


class _CollidingKey:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile key")


def _collide(mapping, field):
    out = {k: v for k, v in mapping.items() if k != field}
    out[_CollidingKey(field)] = mapping[field]
    return out


@pytest.mark.parametrize("field", ["op", "worker", "now", "lease_ms"])
def test_hostile_request_keys(field):
    _raises("malformed_queue_request", _queue("a"), _collide(claim(), field))


@pytest.mark.parametrize("where", ["state", "job", "payload"])
def test_hostile_state_keys(where):
    state = _good_state()
    if where == "state":
        state = _collide(state, "jobs")
    elif where == "job":
        state["jobs"][0] = _collide(state["jobs"][0], "status")
    else:
        state["jobs"][0]["payload"] = _collide({"k": 1}, "k")
    _raises("corrupt_queue", state, claim(now=3))


def test_mutant_without_key_guard_escapes_raw():
    request = _collide(claim(), "op")
    with pytest.raises(RuntimeError):
        set(request.keys()) != set(_OPS["claim"])  # noqa: B015
    _raises("malformed_queue_request", _queue("a"), request)


# -- engine mutants: each is killed by the battery ----------------------------------


def _semantic_battery(engine_cls):
    """A compact re-run of the ordering/lease/dead/ack rules that each
    engine mutant below breaks."""
    raw = engine_cls()

    class eng:  # noqa: N801 - closure-checked view of the engine
        @staticmethod
        def apply(state, request):
            out = raw.apply(state, request)
            try:
                _validate_state(state)
            except QueueError as exc:  # a committed state must stay valid
                raise AssertionError(f"closure broken: {exc.failure_class}") from exc
            return out

    s = empty_state()
    for key, pr in (("low", 9), ("hi", 0), ("hi2", 0)):
        eng.apply(s, enq(key, pr))
    with pytest.raises(QueueError) as err:
        eng.apply(s, enq("hi", 5))  # dedupe conflict
    assert err.value.failure_class == "dedupe_conflict"
    with pytest.raises(QueueError) as err:
        eng.apply(_state_at_seq(MAX_NOW), enq("x"))  # seq overflow
    assert err.value.failure_class == "capacity_exceeded"
    with pytest.raises(QueueError) as err:
        eng.apply(_queue("c"), claim(now=MAX_NOW - 1, lease_ms=2))  # clock overflow
    assert err.value.failure_class == "malformed_queue_request"
    order = [eng.apply(s, claim(now=0, lease_ms=10))["job_id"] for _ in range(3)]
    assert order == [job_id_for(k) for k in ("hi", "hi2", "low")]
    assert eng.apply(s, claim(now=10, lease_ms=10))["attempts"] == 2
    with pytest.raises(QueueError):
        eng.apply(s, ack(job_id_for("hi"), "w2", now=11))
    with pytest.raises(QueueError):
        eng.apply(s, ack(job_id_for("hi"), "w1", now=20))
    eng.apply(s, nack(job_id_for("hi"), "w1", now=19))
    assert s["jobs"][1]["attempts"] == 2
    t = _queue("a")
    for i in range(MAX_ATTEMPTS):
        eng.apply(t, claim(now=i * 10, lease_ms=1))
    assert eng.apply(t, claim(now=10**4))["job_id"] is None
    assert t["jobs"][0]["status"] == "dead"


def _engine_mutants():
    class SeqOnly(QueueEngine):
        def _transition(self, work, req):
            if req["op"] == "claim":
                for j in work["jobs"]:
                    j["_p"], j["priority"] = j["priority"], 0
                try:
                    return super()._transition(work, req)
                finally:
                    for j in work["jobs"]:
                        j["priority"] = j.pop("_p")
            return super()._transition(work, req)

    class StrictExpiry(QueueEngine):
        def _transition(self, work, req):
            if req["op"] == "claim":
                req = {**req, "now": req["now"] - 1}
            return super()._transition(work, req)

    class NoOwnerCheck(QueueEngine):
        def _transition(self, work, req):
            if req["op"] in ("ack", "nack"):
                job = self._find(work, req["job_id"])
                if job is not None and job["lease_owner"]:
                    req = {**req, "worker": job["lease_owner"]}
            return super()._transition(work, req)

    class NoExpiryCheck(QueueEngine):
        def _transition(self, work, req):
            if req["op"] in ("ack", "nack"):
                req = {**req, "now": 0}
            return super()._transition(work, req)

    class NackCounts(QueueEngine):
        def _transition(self, work, req):
            job = super()._transition(work, req)
            if req["op"] == "nack":
                job["attempts"] += 1
            return job

    class NoDeadLetter(QueueEngine):
        def _transition(self, work, req):
            if req["op"] == "claim":
                for j in work["jobs"]:
                    if j["attempts"] >= MAX_ATTEMPTS and j["status"] != "dead":
                        j["attempts"] = MAX_ATTEMPTS - 1
            return super()._transition(work, req)

    class NoDedupeConflict(QueueEngine):
        def _transition(self, work, req):
            if req["op"] == "enqueue":
                job = self._find(work, job_id_for(req["dedupe_key"]))
                if job is not None:
                    return job
            return super()._transition(work, req)

    class SeqOverflow(QueueEngine):
        SEQ_LIMIT = MAX_NOW + 1

    class NoClockBound(QueueEngine):
        CLOCK_LIMIT = 2**64

    return [
        SeqOnly,
        StrictExpiry,
        NoOwnerCheck,
        NoExpiryCheck,
        NackCounts,
        NoDeadLetter,
        NoDedupeConflict,
        SeqOverflow,
        NoClockBound,
    ]


def test_reference_passes_semantic_battery():
    _semantic_battery(QueueEngine)


@pytest.mark.parametrize("mutant", _engine_mutants(), ids=lambda c: c.__name__)
def test_engine_mutants_are_killed(mutant):
    with pytest.raises((AssertionError, QueueError, pytest.fail.Exception)):
        _semantic_battery(mutant)


def test_mutant_non_atomic_commit_is_detected():
    class EarlyCommit(QueueEngine):
        def apply(self, state, request):
            req = _validate_request(request)
            _validate_state(state)
            if req["op"] == "enqueue":
                state["next_seq"] += 1  # mutates BEFORE a possible rejection
            return super().apply(state, request)

    state = {
        "jobs": [
            {
                "job_id": job_id_for(f"j{i}"),
                "seq": i,
                "priority": 5,
                "payload": None,
                "status": "done",
                "attempts": 1,
                "lease_owner": None,
                "lease_expires_at": None,
            }
            for i in range(MAX_JOBS)
        ],
        "next_seq": MAX_JOBS,
    }
    before = copy.deepcopy(state)
    with pytest.raises(QueueError):
        EarlyCommit().apply(state, enq("x"))
    assert state != before  # the mutant leaked a partial write
    with pytest.raises(QueueError):
        QueueEngine().apply(state := copy.deepcopy(before), enq("x"))
    assert state == before


def test_engine_error_codes_are_the_closed_enum():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert set(FAILURE_MAPPING) == set(_CC["failures"]["classes"])


class _PinProbeLeaf:
    pass


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": _PinProbeLeaf()}
    before = _snap(value)
    value["k"] = _PinProbeLeaf()
    value["k"] = _PinProbeLeaf()
    assert _snap(value) != before
