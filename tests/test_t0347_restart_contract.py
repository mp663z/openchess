"""T0347: jobs restart contract - the contract document
(data/contracts/restart.yaml) is normative; this battery holds the
contract-derived REFERENCE restart function and proves happy, boundary,
malformed and rollback behavior against it.

A restart is a pure transition of one queue state (the linked queue
contract's state) for one request {op, worker, now}: every job leased by
that worker whose lease is still live (now strictly before its expiry)
returns to ready with no lease owner or expiry. The receipt names the
released job ids in seq order, binds the state before and after through
the queue's own state_id, and carries its own content-addressed
restart_id. The new state is committed last, in place.

Fail-closed readings (the spec is silent; each is stated in the
contract's rule text):
- attempts are kept, as on a queue nack: a restart never refunds or
  charges an attempt;
- a lease at or past its expiry is left untouched: it is already
  reclaimable by the queue claim;
- a released job at max_attempts returns to ready; dead-marking stays
  with the queue claim;
- a state that breaks any queue state or job invariant is corrupt_queue,
  never repaired;
- validation order is request shape -> state invariants.
Not covered (needs the idempotency/cancel contracts T0293/T0302,
parked): fencing a restart against a stale worker epoch, and cancelling
jobs held by a restarted worker.

DESIGN CAUTION: the reference is derived from the same contract
document, so this battery proves contract CONSISTENCY, not production
behavior; a later implement task must run the same cases against a
separately built runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.restart_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_RECORD_FIELDS = _CC["record"]["fields"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
_STATE_FIELDS = _QUEUE["state"]["fields"]
_JOB_FIELDS = _QUEUE["state"]["job_fields"]
_STATUSES = tuple(_QUEUE["state"]["statuses"])
MAX_ATTEMPTS = _REQ["bounds"]["max_attempts"]
_JOB_RE = re.compile(_QUEUE["identifiers"]["job_id"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_CC["identifiers"]["worker"]["grammar"], re.ASCII)
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"], re.ASCII)
_RESTART_RE = re.compile(_CC["identifiers"]["restart_id"]["grammar"], re.ASCII)

# -- reference begin: everything down to the reference end marker is rebuilt
# from source for each reference mutant (constants, error class, checks).

_REF_MAX_NOW = 2**53 - 1
_REF_MAX_JOBS = 10000
_REF_MAX_PRIORITY = 9
_REF_MAX_DEPTH = 64
_REF_MAX_DIGITS = 4000
_REF_MAX_BITS = 13288  # ceil(4000 * log2(10)): a pre-screen only


class RestartError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canon_queue(obj):
    """The linked queue contract's state canon: non-ASCII kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canon_receipt(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _int_in(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _grammar(value, regex):
    return type(value) is str and regex.fullmatch(value) is not None


def _exact_keys(mapping, fields):
    """Exact dict whose keys are exact strs equal to FIELDS (checked
    before any set comparison, so no user key code runs)."""
    return (
        type(mapping) is dict
        and all(type(k) is str for k in mapping)
        and len(mapping) == len(fields)
        and set(mapping) == set(fields)
    )


def _scalar_ok(node):
    kind = type(node)
    if node is None or kind is bool:
        return True
    if kind is int:
        if node.bit_length() > _REF_MAX_BITS:
            return False
        ok = False
        try:
            ok = len(str(abs(node))) <= _REF_MAX_DIGITS
        except ValueError:
            ok = False
        return ok
    if kind is float:
        return math.isfinite(node)
    if kind is str:
        ok = True
        try:
            node.encode("utf-8")
        except UnicodeEncodeError:
            ok = False
        return ok
    return False


def _payload_ok(root, seen):
    """Iterative: exact built-in JSON only, bounded ints, finite floats,
    UTF-8 strs, depth at most the bound, no container met twice (a cycle
    or an alias, also across the payloads of one state: SEEN is shared).
    Never recurses, never raises."""
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        kind = type(node)
        if kind is list or kind is dict:
            if depth > _REF_MAX_DEPTH or id(node) in seen:
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


def _request_ok(request):
    if not _exact_keys(request, _FIELDS):
        return False
    return (
        type(request["op"]) is str
        and request["op"] == "restart"
        and _grammar(request["worker"], _WORKER_RE)
        and _int_in(request["now"], 0, _REF_MAX_NOW)
    )


def _job_ok(job, prior_seq, next_seq, containers):
    if not _exact_keys(job, _JOB_FIELDS):
        return False
    status, attempts = job["status"], job["attempts"]
    owner, expires = job["lease_owner"], job["lease_expires_at"]
    if not (
        _grammar(job["job_id"], _JOB_RE)
        and _int_in(job["seq"], prior_seq + 1, next_seq - 1)
        and _int_in(job["priority"], 0, _REF_MAX_PRIORITY)
        and type(status) is str
        and status in _STATUSES
        and _int_in(attempts, 0, MAX_ATTEMPTS)
    ):
        return False
    if status == "leased":
        if not (
            _grammar(owner, _WORKER_RE) and _int_in(expires, 1, _REF_MAX_NOW) and attempts >= 1
        ):
            return False
    elif owner is not None or expires is not None:
        return False
    if status == "dead" and attempts != MAX_ATTEMPTS:
        return False
    if status == "done" and attempts < 1:
        return False
    return _payload_ok(job["payload"], containers)


def _state_ok(state):
    if not _exact_keys(state, _STATE_FIELDS):
        return False
    jobs, next_seq = state["jobs"], state["next_seq"]
    if type(jobs) is not list or not _int_in(next_seq, 0, _REF_MAX_NOW):
        return False
    if len(jobs) > _REF_MAX_JOBS:
        return False
    seen_ids, prior_seq, containers = set(), -1, set()
    for job in list.__iter__(jobs):
        if not _job_ok(job, prior_seq, next_seq, containers):
            return False
        if job["job_id"] in seen_ids:
            return False
        seen_ids.add(job["job_id"])
        prior_seq = job["seq"]
    return True


def _state_id(state):
    return "qs1:" + hashlib.sha256(_canon_queue(state).encode("utf-8")).hexdigest()


def _restart_id(record_without_id):
    body = _canon_receipt(record_without_id).encode("utf-8")
    return "rst1:" + hashlib.sha256(b"rst1\x00" + body).hexdigest()


def restart(state, request):
    """The reference restart. Fresh typed errors (flag pattern); the new
    state is committed last, in place."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_restart_request"
    elif not _state_ok(state):
        failed = "corrupt_queue"
    if failed is not None:
        raise RestartError(failed)
    worker, now = request["worker"], request["now"]
    work = json.loads(_canon_queue(state))  # detached copy-on-write
    prior = _state_id(work)
    released = []
    for job in work["jobs"]:
        if (
            job["status"] == "leased"
            and job["lease_owner"] == worker
            and now < job["lease_expires_at"]
        ):
            job.update(status="ready", lease_owner=None, lease_expires_at=None)
            released.append(job["job_id"])
    record = {
        "op": "restart",
        "worker": worker,
        "restarted_at": now,
        "released": released,
        "prior_state_id": prior,
        "state_id": _state_id(work),
    }
    record["restart_id"] = _restart_id(record)
    # COMMIT LAST, in place
    state.clear()
    state.update(work)
    return record


# -- reference end

# -- helpers ----------------------------------------------------------------------------

MAX = 9007199254740991  # 2**53 - 1, written out: the contract's literal edge


def jid(key):
    """The queue contract's job id for a dedupe key (queue reference)."""
    return "job1:" + hashlib.sha256(f"job1|{key}".encode()).hexdigest()


def job(key, seq, status="ready", attempts=0, owner=None, expires=None, priority=5, payload=None):
    return {
        "job_id": jid(key),
        "seq": seq,
        "priority": priority,
        "payload": {"k": key} if payload is None else payload,
        "status": status,
        "attempts": attempts,
        "lease_owner": owner,
        "lease_expires_at": expires,
    }


def leased(key, seq, owner="w1", expires=2000, attempts=1, **kw):
    return job(key, seq, status="leased", attempts=attempts, owner=owner, expires=expires, **kw)


def state(*jobs, next_seq=None):
    jobs = list(jobs)
    return {
        "jobs": jobs,
        "next_seq": (jobs[-1]["seq"] + 1 if jobs else 0) if next_seq is None else next_seq,
    }


def rq(worker="w1", now=1000, **kw):
    base = {"op": "restart", "worker": worker, "now": now}
    base.update(kw)
    return base


def _independent_state_id(st):
    """The queue contract's state_id, by hand: sha256 of the UTF-8
    canonical JSON (sort_keys, compact separators, NON-ASCII KEPT)."""
    body = json.dumps(st, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "qs1:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _independent_restart_id(record_without_id):
    """The contract preimage, by hand: ASCII rst1, a NUL byte, then
    canonical JSON (sort_keys, compact separators, ensure_ascii) of the
    record without restart_id."""
    body = json.dumps(record_without_id, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "rst1:" + hashlib.sha256(b"rst1" + bytes([0]) + body.encode("utf-8")).hexdigest()


def _raises(failure_class, st, request):
    st_snap, rq_snap = copy.deepcopy(st), copy.deepcopy(request)
    with pytest.raises(RestartError) as caught:
        restart(st, request)
    exc = caught.value
    assert type(exc) is RestartError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False  # retryable_true_only_for is [internal]
    assert st == st_snap and request == rq_snap


def _released(st, request):
    return restart(st, request)["released"]


# -- the contract document ------------------------------------------------------------------


def test_lint_clean():
    lint()


def _mutants():
    def worker_grammar(cc):
        cc["identifiers"]["worker"]["grammar"] = "^.*$"

    def state_id_grammar(cc):
        cc["identifiers"]["state_id"]["grammar"] = "^qs2:[0-9a-f]{64}$"

    def state_id_derivation(cc):
        cc["identifiers"]["state_id"]["derivation"] = "sha256-of-ascii-canonical-json"

    def restart_id_prefix_dropped(cc):
        cc["identifiers"]["restart_id"]["derivation"] = (
            "sha256-over-canonical-json-of-the-record-without-restart-id"
        )

    def release_reading(cc):
        cc["semantics"]["release"] = "every-job-leased-by-the-worker-returns-to-ready"

    def attempts_reading(cc):
        cc["semantics"]["attempts"] = "a-restart-refunds-the-attempt"

    def expired_reading(cc):
        cc["semantics"]["expired"] = "an-expired-lease-is-released-too"

    def dead_reading(cc):
        cc["semantics"]["dead"] = "a-released-job-at-max-attempts-becomes-dead"

    def extra_failure(cc):
        cc["failures"]["classes"].append("worker_unknown")

    def mapping_drift(cc):
        cc["failures"]["mapping"]["corrupt_queue"] = "internal"

    def retryable(cc):
        cc["errors"]["shape"]["retryable_true_only_for"] = ["internal", "corrupt_queue"]

    def record_field(cc):
        cc["record"]["fields"].append("epoch")

    def max_jobs_bound(cc):
        cc["request"]["bounds"]["max_jobs"] = 10001

    def max_attempts_bound(cc):
        cc["request"]["bounds"]["max_attempts"] = 6

    def extra_operation(cc):
        cc["request"]["operations"].append({"op": "fence", "fields": ["op", "worker"]})

    def link(cc):
        cc["links"]["queue_contract"] = "data/contracts/missing.yaml"

    def extra_section(cc):
        cc["epochs"] = {}

    return [
        worker_grammar,
        state_id_grammar,
        state_id_derivation,
        restart_id_prefix_dropped,
        release_reading,
        attempts_reading,
        expired_reading,
        dead_reading,
        extra_failure,
        mapping_drift,
        retryable,
        record_field,
        max_jobs_bound,
        max_attempts_bound,
        extra_operation,
        link,
        extra_section,
    ]


@pytest.mark.parametrize("mutate", _mutants(), ids=lambda f: f.__name__)
def test_mutations_fail_lint(mutate, tmp_path):
    doc = yaml.safe_load(CONTRACT.read_text())
    mutate(doc["contract"])
    path = tmp_path / "restart.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


def _drift_worker(q):
    q["identifiers"]["worker"]["grammar"] = "^x$"


def _drift_state_id(q):
    q["identifiers"]["state_id"]["grammar"] = "^qs2:[0-9a-f]{64}$"


def _drift_max_jobs(q):
    q["state"]["max_jobs"] = 9999


def _drift_max_attempts(q):
    q["state"]["max_attempts"] = 4


@pytest.mark.parametrize(
    "drift",
    [_drift_worker, _drift_state_id, _drift_max_jobs, _drift_max_attempts],
    ids=lambda f: f.__name__,
)
def test_lint_binds_the_queue_sibling(drift, tmp_path, monkeypatch):
    """Worker grammar, state_id grammar, max_jobs and max_attempts are
    the queue contract's: a drifted queue contract fails this lint too."""
    from tools import restart_contract_lint as rl

    queue = yaml.safe_load((ROOT / "data/contracts/queue.yaml").read_text())
    drift(queue["contract"])
    (tmp_path / "data/contracts").mkdir(parents=True)
    (tmp_path / "data/contracts/queue.yaml").write_text(yaml.safe_dump(queue))
    monkeypatch.setattr(rl, "ROOT", tmp_path)
    with pytest.raises(ContractError):
        rl.lint(CONTRACT)


def test_error_enum_matches_mapping():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)


def test_bounds_are_the_contract_literals():
    bounds = _REQ["bounds"]
    assert bounds["now"] == "int-0-through-2-pow-53-minus-1-milliseconds"
    assert bounds["max_jobs"] == 10000 == _QUEUE["state"]["max_jobs"]
    assert bounds["max_attempts"] == 5 == _QUEUE["state"]["max_attempts"]
    assert MAX == 2**53 - 1
    assert _CC["identifiers"]["restart_id"]["grammar"] == "^rst1:[0-9a-f]{64}$"
    assert _CC["identifiers"]["state_id"]["grammar"] == "^qs1:[0-9a-f]{64}$"


# -- happy path: golden restart --------------------------------------------------------------


def _golden_state():
    return state(
        leased("a", 0, owner="w1", expires=2000),  # live, released
        leased("b", 1, owner="w2", expires=2000),  # other worker
        leased("c", 2, owner="w1", expires=1000),  # expired at now: untouched
        job("d", 3),  # ready
        job("e", 4, status="done", attempts=1),
        job("f", 5, status="dead", attempts=5),
        leased("g", 6, owner="w1", expires=1001, attempts=5, priority=0),  # live at max
        leased("h", 7, owner="w1", expires=999),  # expired: untouched
        next_seq=9,
    )


_GOLDEN_PRIOR = "qs1:583ccf9c58c9e1514ef172f7d8229ac885d9e5b04047de998423fab593b97d74"
_GOLDEN_AFTER = "qs1:0bc9242a757a71cc4726815153614745c73a57bbba2ccebe9e5bf584251ebd0f"
_GOLDEN_RESTART = "rst1:8706f8a94add7d95a780352831718c9a83af3149783b53b847b5ea2b2f21e034"


def test_full_record_equality_golden_restart():
    st = _golden_state()
    expected_state = _golden_state()
    for j in (expected_state["jobs"][0], expected_state["jobs"][6]):
        j.update(status="ready", lease_owner=None, lease_expires_at=None)
    got = restart(st, rq(worker="w1", now=1000))
    assert got == {
        "op": "restart",
        "worker": "w1",
        "restarted_at": 1000,
        "released": [jid("a"), jid("g")],
        "prior_state_id": _GOLDEN_PRIOR,
        "state_id": _GOLDEN_AFTER,
        "restart_id": _GOLDEN_RESTART,
    }
    assert list(got) == _RECORD_FIELDS
    assert st == expected_state
    assert _independent_state_id(_golden_state()) == _GOLDEN_PRIOR
    assert _independent_state_id(expected_state) == _GOLDEN_AFTER


def test_ids_match_the_contract_preimages():
    st = _golden_state()
    got = restart(st, rq())
    assert got["prior_state_id"] == _independent_state_id(_golden_state())
    assert got["state_id"] == _independent_state_id(st)
    assert got["restart_id"] == _independent_restart_id(
        {k: got[k] for k in _RECORD_FIELDS if k != "restart_id"}
    )
    for name, regex in (("prior_state_id", _STATE_RE), ("state_id", _STATE_RE)):
        assert regex.fullmatch(got[name])
    assert _RESTART_RE.fullmatch(got["restart_id"])


def test_state_id_keeps_non_ascii_as_the_queue_does():
    st = state(leased("u", 0, payload={"name": "caf\u00e9 \u265e"}))
    before = copy.deepcopy(st)
    got = restart(st, rq())
    ascii_id = (
        "qs1:"
        + hashlib.sha256(
            json.dumps(before, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
    )
    assert got["prior_state_id"] == _independent_state_id(before) != ascii_id
    assert st["jobs"][0]["payload"] == {"name": "caf\u00e9 \u265e"}


def test_state_id_matches_the_queue_reference():
    """Bound to the sibling: the queue battery's own state_id_for gives
    the same ids, and the queue engine accepts the committed state."""
    from tests.test_t0284_queue_contract import QueueEngine, state_id_for

    st = _golden_state()
    before = copy.deepcopy(st)
    got = restart(st, rq())
    assert got["prior_state_id"] == state_id_for(before)
    assert got["state_id"] == state_id_for(st)
    receipt = QueueEngine().apply(st, {"op": "claim", "worker": "w9", "now": 1000, "lease_ms": 5})
    assert receipt["job_id"] == jid("a")  # g went dead on the claim; a is next


def test_released_job_at_max_attempts_is_dead_marked_by_the_queue_claim():
    from tests.test_t0284_queue_contract import QueueEngine

    st = state(leased("m", 0, attempts=5, expires=2000))
    assert _released(st, rq()) == [jid("m")]
    assert st["jobs"][0]["status"] == "ready" and st["jobs"][0]["attempts"] == 5
    receipt = QueueEngine().apply(st, {"op": "claim", "worker": "w2", "now": 1000, "lease_ms": 5})
    assert receipt["job_id"] is None
    assert st["jobs"][0]["status"] == "dead"


def test_attempts_are_kept_and_nothing_else_moves():
    st = _golden_state()
    before = copy.deepcopy(st)
    restart(st, rq())
    assert st["next_seq"] == before["next_seq"]
    assert len(st["jobs"]) == len(before["jobs"])
    for new, old in zip(st["jobs"], before["jobs"], strict=True):
        for field in ("job_id", "seq", "priority", "payload", "attempts"):
            assert new[field] == old[field]
    for i in (1, 2, 3, 4, 5, 7):
        assert st["jobs"][i] == before["jobs"][i]


def test_other_worker_gets_its_own_receipt():
    st = _golden_state()
    got = restart(st, rq(worker="w2", now=1500))
    assert got["worker"] == "w2" and got["restarted_at"] == 1500
    assert got["released"] == [jid("b")]
    assert got["restart_id"] == _independent_restart_id(
        {k: got[k] for k in _RECORD_FIELDS if k != "restart_id"}
    )
    assert st["jobs"][0]["lease_owner"] == "w1"  # w1's live lease untouched


def test_released_follows_seq_order():
    st = state(leased("z", 0), leased("y", 1), leased("x", 2))
    assert _released(st, rq()) == [jid("z"), jid("y"), jid("x")]


def test_empty_restart_is_a_successful_unchanged_receipt():
    for st in (state(), _golden_state()):
        before = copy.deepcopy(st)
        got = restart(st, rq(worker="w-none"))
        assert got["released"] == []
        assert got["prior_state_id"] == got["state_id"] == _independent_state_id(before)
        assert st == before


def test_restart_is_idempotent():
    st = _golden_state()
    restart(st, rq())
    after = copy.deepcopy(st)
    again = restart(st, rq())
    assert again["released"] == []
    assert again["prior_state_id"] == again["state_id"] == _independent_state_id(after)
    assert st == after


def test_determinism():
    a, b = _golden_state(), _golden_state()
    assert restart(a, rq()) == restart(b, rq())
    assert a == b


def test_commit_is_in_place_and_detached():
    st = _golden_state()
    jobs_before, payload_before = st["jobs"], st["jobs"][0]["payload"]
    request = rq()
    got = restart(st, request)
    assert type(st) is dict and st["jobs"] is not jobs_before
    assert st["jobs"][0]["payload"] is not payload_before
    got["released"].append("x")
    assert all(j["job_id"] != "x" for j in st["jobs"])
    assert request == rq()


# -- boundaries ------------------------------------------------------------------------------


def test_lease_window_is_exclusive_of_expiry():
    assert _released(state(leased("a", 0, expires=1001)), rq(now=1000)) == [jid("a")]
    assert _released(state(leased("a", 0, expires=1000)), rq(now=1000)) == []
    assert _released(state(leased("a", 0, expires=MAX)), rq(now=MAX - 1)) == [jid("a")]
    assert _released(state(leased("a", 0, expires=1)), rq(now=0)) == [jid("a")]
    assert _released(state(leased("a", 0, expires=1)), rq(now=1)) == []


def test_bounds_are_inclusive():
    assert restart(state(), rq(now=0))["restarted_at"] == 0
    assert restart(state(), rq(now=MAX))["restarted_at"] == MAX
    _raises("malformed_restart_request", state(), rq(now=MAX + 1))
    _raises("malformed_restart_request", state(), rq(now=-1))
    w64 = "w" * 64
    assert _released(state(leased("a", 0, owner=w64)), rq(worker=w64)) == [jid("a")]
    _raises("malformed_restart_request", state(), rq(worker="w" * 65))
    _raises("malformed_restart_request", state(), rq(worker=""))
    assert restart(state(next_seq=MAX), rq())["released"] == []
    _raises("corrupt_queue", state(next_seq=MAX + 1), rq())
    assert _released(state(leased("a", 0, priority=9, attempts=5)), rq()) == [jid("a")]
    _raises("corrupt_queue", state(job("a", 0, priority=10)), rq())
    _raises("corrupt_queue", state(job("a", 0, attempts=6)), rq())
    _raises("corrupt_queue", state(job("a", 0, priority=-1)), rq())


def test_max_jobs_is_inclusive():
    many = [job(f"j{i}", i) for i in range(10000)]
    many[-1] = leased("j9999", 9999)
    st = state(*many)
    assert _released(st, rq()) == [jid("j9999")]
    over = state(*[job(f"j{i}", i) for i in range(10001)])
    _raises("corrupt_queue", over, rq())


def test_seq_order_and_next_seq_edges():
    assert restart(state(job("a", 0), job("b", 5), next_seq=6), rq())["released"] == []
    _raises("corrupt_queue", state(job("a", 0), job("b", 0), next_seq=2), rq())
    _raises("corrupt_queue", state(job("a", 1), job("b", 0), next_seq=2), rq())
    _raises("corrupt_queue", state(job("a", 0), next_seq=0), rq())
    assert restart(state(job("a", 0), next_seq=1), rq())["released"] == []


def test_validation_order():
    bad_state = state(job("a", 0, attempts=6))
    _raises("malformed_restart_request", bad_state, rq(worker="w 1"))
    _raises("malformed_restart_request", None, rq(op="claim"))
    _raises("corrupt_queue", bad_state, rq())


# -- malformed requests and corrupt states ------------------------------------------------------

HOSTILE = []


class _Armed:
    on = False


def _log(name):
    if _Armed.on:
        HOSTILE.append(name)


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _LyingDict(dict):
    def __getitem__(self, key):
        _log("getitem")
        return dict.__getitem__(self, key)

    def __iter__(self):
        _log("iter")
        return dict.__iter__(self)

    def __len__(self):
        _log("len")
        return dict.__len__(self)

    def keys(self):
        _log("keys")
        return dict.keys(self)

    def items(self):
        _log("items")
        return dict.items(self)


class _EqRaises(str):
    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")

    def __hash__(self):
        _log("hash")
        return str.__hash__(self)


class _Collides:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        _log("hash")
        return hash(self.name)

    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")


class _ReprRaises(str):
    def __repr__(self):
        _log("repr")
        raise RuntimeError("hostile __repr__")

    def __str__(self):
        _log("str")
        raise RuntimeError("hostile __str__")

    def __format__(self, spec):
        _log("format")
        raise RuntimeError("hostile __format__")


KEY_FORMS = {"str-subclass": _StrSub, "eq-raises": _EqRaises, "hash-collides": _Collides}


def _rekey(mapping, field, make):
    return {(make(k) if k == field else k): v for k, v in mapping.items()}


def _good():
    return state(leased("a", 0), job("b", 1))


MALFORMED = {
    "none": None,
    "str": "restart",
    "list": list(rq().items()),
    "list-subclass": _ListSub(rq().items()),
    "dict-subclass": _DictSub(rq()),
    "lying-dict": _LyingDict(rq()),
    "missing-now": {"op": "restart", "worker": "w1"},
    "extra": {**rq(), "epoch": 1},
    "renamed-same-arity": {"op": "restart", "Worker": "w1", "now": 1000},
    "op-claim": rq(op="claim"),
    "op-nack": rq(op="nack"),
    "op-newline": rq(op="restart\n"),
    "op-str-subclass": rq(op=_StrSub("restart")),
    "op-repr-raises": rq(op=_ReprRaises("restart")),
    "worker-space": rq(worker="w 1"),
    "worker-newline": rq(worker="w1\n"),
    "worker-none": rq(worker=None),
    "worker-str-subclass": rq(worker=_StrSub("w1")),
    "worker-repr-raises": rq(worker=_ReprRaises("w1")),
    "worker-eq-raises": rq(worker=_EqRaises("w1")),
    "now-bool": rq(now=True),
    "now-float": rq(now=1000.0),
    "now-str": rq(now="1000"),
    "now-int-subclass": rq(now=_IntSub(1000)),
    "now-negative": rq(now=-1),
    "now-past-clock": rq(now=9007199254740992),
}
for _field in _FIELDS:
    for _label, _make in KEY_FORMS.items():
        MALFORMED[f"key-{_label}-{_field}"] = _rekey(rq(), _field, _make)


def _with_job(**fields):
    st = _good()
    st["jobs"][0].update(fields)
    return st


def _corrupt_rows():
    """Fresh objects on every call: a mutant that writes into a rejected
    state (commit-before-validate) cannot poison later rows."""
    _shared = {"x": 1}
    _cyclic = []
    _cyclic.append(_cyclic)
    _deep = {}
    _node = _deep
    for _ in range(64):
        _node["d"] = {}
        _node = _node["d"]

    rows = {
        "none": None,
        "list": [],
        "dict-subclass": _DictSub(_good()),
        "lying-dict": _LyingDict(_good()),
        "missing-field": {"jobs": []},
        "extra-field": {**_good(), "epoch": 0},
        "renamed-same-arity": {"Jobs": [], "next_seq": 0},
        "jobs-tuple": {"jobs": (), "next_seq": 0},
        "jobs-list-subclass": {"jobs": _ListSub(_good()["jobs"]), "next_seq": 2},
        "next-seq-bool": {"jobs": [], "next_seq": False},
        "next-seq-negative": {"jobs": [], "next_seq": -1},
        "next-seq-int-subclass": {"jobs": [], "next_seq": _IntSub(0)},
        "job-none": state(next_seq=1) | {"jobs": [None]},
        "job-dict-subclass": {"jobs": [_DictSub(job("a", 0))], "next_seq": 1},
        "job-lying-dict": {"jobs": [_LyingDict(job("a", 0))], "next_seq": 1},
        "job-list-subclass": {"jobs": [_ListSub(job("a", 0).items())], "next_seq": 1},
        "job-missing-field": state({k: v for k, v in job("a", 0).items() if k != "payload"}),
        "job-extra-field": state({**job("a", 0), "epoch": 0}),
        "job-renamed-same-arity": state(
            {("Status" if k == "status" else k): v for k, v in job("a", 0).items()}
        ),
        "job-id-bad": _with_job(job_id="job1:" + "G" * 64),
        "job-id-newline": _with_job(job_id=jid("a") + "\n"),
        "job-id-str-subclass": _with_job(job_id=_StrSub(jid("a"))),
        "job-id-duplicate": state(job("a", 0), job("a", 1)),
        "seq-bool": state(job("a", 0), next_seq=1) | {"jobs": [{**job("a", 0), "seq": False}]},
        "priority-bool": _with_job(priority=True),
        "priority-int-subclass": _with_job(priority=_IntSub(5)),
        "status-unknown": _with_job(status="held"),
        "status-unknown-idle": state(job("a", 0, status="held")),
        "status-empty-idle": state(job("a", 0, status="")),
        "status-str-subclass": _with_job(status=_StrSub("leased")),
        "status-repr-raises": _with_job(status=_ReprRaises("leased")),
        "attempts-bool": _with_job(attempts=True),
        "attempts-negative": _with_job(attempts=-1),
        "attempts-negative-idle": state(job("a", 0, attempts=-1)),
        "leased-no-owner": _with_job(lease_owner=None),
        "leased-owner-bad": _with_job(lease_owner="w 1"),
        "leased-owner-str-subclass": _with_job(lease_owner=_StrSub("w1")),
        "leased-owner-eq-raises": _with_job(lease_owner=_EqRaises("w1")),
        "leased-owner-repr-raises": _with_job(lease_owner=_ReprRaises("w1")),
        "leased-no-expiry": _with_job(lease_expires_at=None),
        "leased-expiry-zero": _with_job(lease_expires_at=0),
        "leased-expiry-past-clock": _with_job(lease_expires_at=9007199254740992),
        "leased-expiry-bool": _with_job(lease_expires_at=True),
        "leased-expiry-int-subclass": _with_job(lease_expires_at=_IntSub(2000)),
        "leased-attempts-zero": _with_job(attempts=0),
        "ready-with-owner": state(job("a", 0, owner="w1")),
        "ready-with-expiry": state(job("a", 0, expires=2000)),
        "dead-below-max": state(job("a", 0, status="dead", attempts=4)),
        "done-attempts-zero": state(job("a", 0, status="done", attempts=0)),
        "payload-nan": state(job("a", 0, payload={"x": float("nan")})),
        "payload-inf": state(job("a", 0, payload=[float("inf")])),
        "payload-cycle": state(job("a", 0, payload=_cyclic)),
        "payload-alias-across-jobs": state(
            job("a", 0, payload={"s": _shared}), job("b", 1, payload={"s": _shared})
        ),
        "payload-alias-within": state(job("a", 0, payload=[_shared, _shared])),
        "payload-depth-65": state(job("a", 0, payload=_deep)),
        "payload-int-4001-digits": state(job("a", 0, payload=10**4000)),
        "payload-surrogate": state(job("a", 0, payload="\ud800")),
        "payload-tuple": state(job("a", 0, payload=(1,))),
        "payload-str-subclass-nested": state(job("a", 0, payload={"x": [_StrSub("v")]})),
        "payload-repr-raises-nested": state(job("a", 0, payload={"x": _ReprRaises("v")})),
        "payload-key-str-subclass": state(job("a", 0, payload={_StrSub("x"): 1})),
        "payload-key-eq-raises": state(job("a", 0, payload={_EqRaises("x"): 1})),
        "payload-key-collides": state(job("a", 0, payload={_Collides("x"): 1})),
        "payload-key-int": state(job("a", 0, payload={1: 1})),
        "payload-key-surrogate": state(job("a", 0, payload={"\ud800": 1})),
        "payload-key-dict-subclass-nested": state(job("a", 0, payload=[{"x": _DictSub()}])),
        "payload-dict-subclass": state(job("a", 0, payload=_DictSub(x=1))),
        "payload-list-subclass": state(job("a", 0, payload=_ListSub([1]))),
        "payload-int-subclass": state(job("a", 0, payload=_IntSub(1))),
    }
    for _field in _STATE_FIELDS:
        for _label, _make in KEY_FORMS.items():
            rows[f"key-{_label}-state-{_field}"] = _rekey(_good(), _field, _make)
    for _field in _JOB_FIELDS:
        for _label, _make in KEY_FORMS.items():
            _st = _good()
            _st["jobs"][0] = _rekey(_st["jobs"][0], _field, _make)
            rows[f"key-{_label}-job-{_field}"] = _st
    return rows


CORRUPT = _corrupt_rows()


def _snap(obj, memo=None):
    memo = set() if memo is None else memo
    if isinstance(obj, (dict, list)):
        if id(obj) in memo:
            return ("seen", id(obj))
        memo.add(id(obj))
    if isinstance(obj, dict):
        return (
            type(obj),
            [
                (type(k), k.name if type(k) is _Collides else _key_text(k), _snap(v, memo))
                for k, v in dict.items(obj)
            ],
        )
    if isinstance(obj, (list, tuple)):
        items = list.__iter__(obj) if isinstance(obj, list) else iter(obj)
        return (type(obj), [_snap(v, memo) for v in items])
    if isinstance(obj, str):
        return (type(obj), str.__str__(obj))
    if isinstance(obj, float):
        return (type(obj), repr(float(obj)))
    return (
        type(obj),
        int.__repr__(obj) if isinstance(obj, int) and obj.bit_length() < 64 else id(obj),
    )


def _key_text(k):
    return str.__str__(k) if isinstance(k, str) else ("int", k)


def _hostile(failure_class, st, request):
    """Typed class, fresh error, no user code ran, inputs unchanged."""
    snaps = (_snap(st), _snap(request))
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            restart(st, request)
            got = "accepted"
        except RestartError as exc:
            got = (
                type(exc) is RestartError,
                exc.failure_class,
                exc.code,
                exc.retryable,
                exc.__cause__ is None and exc.__context__ is None,
            )
        except BaseException as exc:  # noqa: BLE001 - a raw escape is the failure
            got = ("raw", type(exc).__name__)
        calls = list(HOSTILE)
    finally:
        _Armed.on = False
    assert got == (True, failure_class, FAILURE_MAPPING[failure_class], False, True), got
    assert calls == [], calls
    assert (_snap(st), _snap(request)) == snaps


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_requests(name):
    _hostile("malformed_restart_request", _good(), MALFORMED[name])


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_corrupt_states(name):
    _hostile("corrupt_queue", _corrupt_rows()[name], rq())


def test_good_fixture_is_accepted():
    st = _good()
    assert _released(st, rq()) == [jid("a")]


def test_rejections_leave_inputs_bit_identical():
    for st, request in (
        (_golden_state(), rq(now=-1)),
        (state(leased("a", 0), job("b", 1, attempts=6)), rq()),
        (state(leased("a", 0), job("a", 1)), rq()),
    ):
        before = (copy.deepcopy(st), copy.deepcopy(request))
        with pytest.raises(RestartError):
            restart(st, request)
        assert (st, request) == before


# -- the reference itself is pinned: one-edit mutants fail the battery -------------------
# Anchors are matched against the reference block's own source (inspect),
# inside the builder, so an edit to this file never breaks collection.

REFERENCE_EDITS = {
    "release-expired-too": ('and now < job["lease_expires_at"]', "and True"),
    "release-window-inclusive": (
        'and now < job["lease_expires_at"]',
        'and now <= job["lease_expires_at"]',
    ),
    "release-any-owner": ('and job["lease_owner"] == worker', "and True"),
    "release-refunds-attempt": (
        'job.update(status="ready", lease_owner=None, lease_expires_at=None)',
        'job.update(status="ready", lease_owner=None, lease_expires_at=None, '
        'attempts=max(job["attempts"] - 1, 0))',
    ),
    "release-marks-dead-at-max": (
        'job.update(status="ready", lease_owner=None, lease_expires_at=None)',
        'job.update(status="dead" if job["attempts"] >= 5 else "ready", '
        "lease_owner=None, lease_expires_at=None)",
    ),
    "release-status-done": ('status="ready", lease_owner=None', 'status="done", lease_owner=None'),
    "release-keeps-owner": (
        "lease_owner=None, lease_expires_at=None)",
        'lease_owner=job["lease_owner"], lease_expires_at=None)',
    ),
    "release-keeps-expiry": ("lease_expires_at=None)", 'lease_expires_at=job["lease_expires_at"])'),
    "released-not-recorded": ('            released.append(job["job_id"])\n', ""),
    "prior-is-after": ('"prior_state_id": prior,', '"prior_state_id": _state_id(work),'),
    "restarted-at-const": ('"restarted_at": now,', '"restarted_at": 0,'),
    "worker-const": ('"worker": worker,', '"worker": "w1",'),
    "op-const": ('"op": "restart",\n        "worker"', '"op": "nack",\n        "worker"'),
    "state-id-ascii": ("ensure_ascii=False)", "ensure_ascii=True)"),
    "restart-id-prefix-dropped": ('b"rst1\\x00" + ', ""),
    "restart-id-extra-member": (
        "_canon_receipt(record_without_id)",
        '_canon_receipt({**record_without_id, "v": 1})',
    ),
    "no-commit": ("    state.clear()\n    state.update(work)\n", ""),
    "commit-before-validate": (
        "    if failed is not None:\n        raise RestartError(failed)\n    worker, now",
        "    if failed is not None:\n        state.clear()\n"
        "        raise RestartError(failed)\n    worker, now",
    ),
    "order-swap": (
        '    if not _request_ok(request):\n        failed = "malformed_restart_request"\n'
        '    elif not _state_ok(state):\n        failed = "corrupt_queue"\n',
        '    if not _state_ok(state):\n        failed = "corrupt_queue"\n'
        '    elif not _request_ok(request):\n        failed = "malformed_restart_request"\n',
    ),
    "op-check-off": ('        and request["op"] == "restart"\n', ""),
    "clock-bound-plus": ("_REF_MAX_NOW = 2**53 - 1", "_REF_MAX_NOW = 2**53"),
    "max-jobs-plus": ("_REF_MAX_JOBS = 10000", "_REF_MAX_JOBS = 10001"),
    "priority-bound-plus": ("_REF_MAX_PRIORITY = 9", "_REF_MAX_PRIORITY = 10"),
    "depth-bound-plus": ("_REF_MAX_DEPTH = 64", "_REF_MAX_DEPTH = 65"),
    "digits-bound-plus": ("_REF_MAX_DIGITS = 4000", "_REF_MAX_DIGITS = 4001"),
    "seq-not-increasing": ("prior_seq + 1, next_seq - 1", "prior_seq, next_seq - 1"),
    "seq-reaches-next": ("prior_seq + 1, next_seq - 1", "prior_seq + 1, next_seq"),
    "duplicate-id-allowed": (
        '        if job["job_id"] in seen_ids:\n            return False\n',
        "",
    ),
    "leased-attempts-off": (" and attempts >= 1\n", "\n"),
    "leased-expiry-zero-ok": (
        "_int_in(expires, 1, _REF_MAX_NOW)",
        "_int_in(expires, 0, _REF_MAX_NOW)",
    ),
    "idle-lease-fields-off": (
        "    elif owner is not None or expires is not None:\n        return False\n",
        "",
    ),
    "dead-invariant-off": (
        '    if status == "dead" and attempts != MAX_ATTEMPTS:\n        return False\n',
        "",
    ),
    "done-invariant-off": ('    if status == "done" and attempts < 1:\n        return False\n', ""),
    "status-enum-off": ("        and status in _STATUSES\n", ""),
    "alias-per-job": (
        'return _payload_ok(job["payload"], containers)',
        'return _payload_ok(job["payload"], set())',
    ),
    "float-finite-off": ("return math.isfinite(node)", "return True"),
    "surrogate-ok": ('            node.encode("utf-8")\n', "            pass\n"),
    "key-guard-off": ("        and all(type(k) is str for k in mapping)\n", ""),
    "payload-key-guard-off": ("if type(key) is not str or not _scalar_ok(key):", "if False:"),
    "grammar-type-off": ("return type(value) is str and regex.fullmatch", "return regex.fullmatch"),
    "int-type-off": ("return type(value) is int and lo <= value", "return lo <= value"),
    "worker-grammar-off": (
        'and _grammar(request["worker"], _WORKER_RE)',
        'and type(request["worker"]) is str',
    ),
    "now-lower-bound-minus": (
        '_int_in(request["now"], 0, _REF_MAX_NOW)',
        '_int_in(request["now"], -1, _REF_MAX_NOW)',
    ),
    "attempts-bound-plus": (
        "_int_in(attempts, 0, MAX_ATTEMPTS)",
        "_int_in(attempts, 0, MAX_ATTEMPTS + 1)",
    ),
    "attempts-lower-minus": (
        "_int_in(attempts, 0, MAX_ATTEMPTS)",
        "_int_in(attempts, -1, MAX_ATTEMPTS)",
    ),
    "jobs-type-off": ("if type(jobs) is not list or not", "if not"),
    "job-id-grammar-off": ('_grammar(job["job_id"], _JOB_RE)', 'type(job["job_id"]) is str'),
    "leased-owner-grammar-off": ("_grammar(owner, _WORKER_RE)", "type(owner) is str"),
    "payload-key-scalar-off": (
        "if type(key) is not str or not _scalar_ok(key):",
        "if type(key) is not str:",
    ),
    "next-seq-lower-minus": (
        "_int_in(next_seq, 0, _REF_MAX_NOW)",
        "_int_in(next_seq, -1, _REF_MAX_NOW)",
    ),
    "retryable-true": ("self.retryable = False", "self.retryable = True"),
    "chained-error": (
        "        raise RestartError(failed)",
        "        try:\n            raise KeyError(failed)\n"
        "        except KeyError:\n            raise RestartError(failed)",
    ),
}


# Edits that must NOT change behavior.
EQUIVALENT_EDITS = {
    # only a leased job carries a lease owner (state invariant), so the
    # status test is implied by the owner test
    "status-check-implied": (
        '            job["status"] == "leased"\n            and ',
        "            ",
    ),
    # jobs are stored in strictly increasing seq (state invariant), so
    # iteration order already is seq order
    "released-resorted": (
        '"released": released,',
        '"released": [j["job_id"] for j in work["jobs"] if j["job_id"] in released],',
    ),
    # the receipt holds only ASCII (worker grammar, hex ids), so the
    # receipt canon's ensure_ascii choice cannot change restart_id
    "receipt-canon-non-ascii": (
        'separators=(",", ":"), ensure_ascii=True)',
        'separators=(",", ":"), ensure_ascii=False)',
    ),
}


def _mutant_reference(name):
    """Rebuild the whole reference block with one edit applied; returns
    the rebuilt top-level names."""
    import ast
    import inspect

    old, new = {**REFERENCE_EDITS, **EQUIVALENT_EDITS}[name]
    module_source = inspect.getsource(sys.modules[__name__])
    begin = module_source.index("# -- reference begin")
    end = module_source.index("# -- reference end")
    source = module_source[begin:end]
    assert source.count(old) == 1, name
    source = source.replace(old, new)
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
    namespace = dict(globals())
    exec(compile(source, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return {n: namespace[n] for n in names}


def _install(monkeypatch, name):
    for key, value in _mutant_reference(name).items():
        monkeypatch.setitem(globals(), key, value)


def _battery():
    """Every behavior test that takes no fixture, plus the parametrized
    malformed and corrupt rows; returns the failing labels."""
    failures = []
    jobs = [
        (n, f)
        for n, f in sorted(globals().items())
        if n.startswith("test_")
        and callable(f)
        and f.__code__.co_argcount == 0
        and n
        not in (
            "test_lint_clean",
            "test_error_enum_matches_mapping",
            "test_bounds_are_the_contract_literals",
            "test_reference_edits_apply_once",
            "test_identity_battery_is_green",
        )
    ]
    jobs += [(f"malformed:{n}", lambda n=n: test_malformed_requests(n)) for n in MALFORMED]
    jobs += [(f"corrupt:{n}", lambda n=n: test_corrupt_states(n)) for n in CORRUPT]
    for label, job in jobs:
        try:
            job()
        except BaseException as exc:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(exc).__name__}")
    return failures


def test_reference_edits_apply_once():
    for name in {**REFERENCE_EDITS, **EQUIVALENT_EDITS}:
        _mutant_reference(name)


def test_identity_battery_is_green():
    assert _battery() == []


@pytest.mark.parametrize("name", sorted(REFERENCE_EDITS))
def test_reference_mutant_is_red(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() != [], name


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() == [], name
