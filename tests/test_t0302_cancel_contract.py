"""T0302: jobs cancel contract - the contract document
(data/contracts/cancel.yaml) is normative; this battery holds the
contract-derived REFERENCE cancel function and proves happy, boundary,
malformed and rollback behavior against it.

A cancellation record is a pure function of one request
{op, job, requester, now}: job is exactly one job of the linked queue
contract. Only a job that satisfies every queue job invariant and has
status ready or leased is cancelled; the record binds the payload
through a digest and carries its own content-addressed decision_id.

Fail-closed readings (the spec is silent; each is stated in the
contract's rule text):
- a job that breaks any queue job invariant is corrupt_job, never
  repaired; a valid done or dead job is job_not_cancellable;
- a ready job has not started: cancellation withdraws it before any
  claim; a leased job is claimed: the decision is recorded against the
  live lease, and applying it to the queue or aborting the worker is
  outside this contract;
- the presented status governs: lease expiry and reclaim are the queue
  contract's concern, never recomputed here;
- a recorded cancellation is terminal; this contract never reverses it;
- the record stores only the payload digest, never a copy;
- validation order is request shape -> job integrity -> cancellable
  status.
Not covered (parked for the owner and the T0293 idempotency contract):
applying the decision to queue state, worker abort, deduplication of
repeated cancellations, and redrive of a cancelled job.

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

from tools.cancel_contract_lint import (  # noqa: E402
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
_CANCELLABLE = tuple(_CC["record"]["statuses_before"])
MAX_ATTEMPTS = _REQ["max_attempts"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
_JOB_FIELDS = _QUEUE["state"]["job_fields"]
_STATUSES = tuple(_QUEUE["state"]["statuses"])
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_REQUESTER_RE = re.compile(_CC["identifiers"]["requester"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_QUEUE["identifiers"]["worker"]["grammar"], re.ASCII)

# -- reference begin: everything down to the reference end marker is rebuilt
# from source for each reference mutant (constants, error class, checks).

_REF_MAX_NOW = 2**53 - 1
_REF_MAX_SEQ = 2**53 - 2
_REF_MAX_PRIORITY = 9
_REF_MAX_DEPTH = 64
_REF_MAX_DIGITS = 4000
_REF_MAX_BITS = 13288  # ceil(4000 * log2(10)): a pre-screen only


class CancelError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canon(obj):
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


def _payload_ok(root):
    """Iterative: exact built-in JSON only, bounded ints, finite floats,
    UTF-8 strs, depth at most the bound, no container met twice (a cycle
    or an alias). Never recurses, never raises."""
    seen = set()
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
        and request["op"] == "cancel"
        and type(request["job"]) is dict
        and _grammar(request["requester"], _REQUESTER_RE)
        and _int_in(request["now"], 0, _REF_MAX_NOW)
    )


def _job_ok(job):
    if not _exact_keys(job, _JOB_FIELDS):
        return False
    status, attempts = job["status"], job["attempts"]
    owner, expires = job["lease_owner"], job["lease_expires_at"]
    if not (
        _grammar(job["job_id"], _JOB_RE)
        and _int_in(job["seq"], 0, _REF_MAX_SEQ)
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
    return _payload_ok(job["payload"])


def _digest(prefix, value):
    return (
        prefix
        + ":"
        + hashlib.sha256(prefix.encode() + b"\x00" + _canon(value).encode()).hexdigest()
    )


def cancel(request):
    """The reference cancellation. Fresh typed errors (flag pattern)."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_cancel_request"
    elif not _job_ok(request["job"]):
        failed = "corrupt_job"
    elif request["job"]["status"] not in _CANCELLABLE:
        failed = "job_not_cancellable"
    if failed is not None:
        raise CancelError(failed)
    req = json.loads(_canon(request))  # detached exact copy
    job = req["job"]
    record = {
        "op": "cancel",
        "job_id": job["job_id"],
        "seq": job["seq"],
        "priority": job["priority"],
        "payload_digest": _digest("pd1", job["payload"]),
        "status_before": job["status"],
        "attempts": job["attempts"],
        "requester": req["requester"],
        "cancelled_at": req["now"],
    }
    record["decision_id"] = _digest("cd1", record)
    return record


# -- reference end

# -- helpers ----------------------------------------------------------------------------

JOB = "job1:" + "a" * 64
JOB_B = "job1:" + "0123456789abcdef" * 4
MAX = 9007199254740991  # 2**53 - 1, written out: the contract's literal edge


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


def _independent(prefix, value):
    """The contract preimages, built by hand: ASCII prefix, a NUL byte,
    then canonical JSON (sort_keys, compact separators, ensure_ascii)."""
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (
        prefix
        + ":"
        + hashlib.sha256(prefix.encode("ascii") + bytes([0]) + body.encode("utf-8")).hexdigest()
    )


def _plain(obj):
    if type(obj) is dict:
        return all(type(k) is str and _plain(v) for k, v in obj.items())
    if type(obj) is list:
        return all(_plain(v) for v in obj)
    return type(obj) in (str, int, bool, float, type(None))


def _raises(failure_class, request):
    snap = copy.deepcopy(request) if _plain(request) else None
    with pytest.raises(CancelError) as caught:
        cancel(request)
    exc = caught.value
    assert type(exc) is CancelError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False  # retryable_true_only_for is [internal]
    if snap is not None:
        assert request == snap


# -- the contract document ------------------------------------------------------------------


def test_lint_clean():
    lint()


def _mutants():
    def op_added(cc):
        cc["request"]["operations"].append({"op": "redrive", "fields": ["op", "job_id"]})

    def max_attempts(cc):
        cc["request"]["max_attempts"] = 6

    def job_grammar(cc):
        cc["identifiers"]["job_id"]["grammar"] = "^job1:.*$"

    def requester_derived(cc):
        cc["identifiers"]["requester"]["source"] = "derived-from-the-job-id"

    def priority_bound(cc):
        cc["request"]["bounds"]["priority"] = "int-0-through-10"

    def payload_bound(cc):
        cc["request"]["bounds"]["payload"] = "exact-canonical-json-depth-at-most-128"

    def payload_copy_reading(cc):
        cc["semantics"]["payload"] = "the-record-stores-a-copy-of-the-payload"

    def cancellable_reading(cc):
        cc["semantics"]["cancellable"] = "any-status-is-cancelled"

    def lease_clock_reading(cc):
        cc["semantics"]["lease_clock"] = "an-expired-lease-is-treated-as-ready"

    def decision_derivation(cc):
        cc["identifiers"]["decision_id"]["derivation"] = "sha256-over-canonical-json-of-the-record"

    def payload_derivation(cc):
        cc["identifiers"]["payload_digest"]["derivation"] = "sha256-over-the-payload"

    def extra_failure(cc):
        cc["failures"]["classes"].append("already_cancelled")

    def mapping_drift(cc):
        cc["failures"]["mapping"]["job_not_cancellable"] = "internal"

    def retryable(cc):
        cc["errors"]["shape"]["retryable_true_only_for"] = ["internal", "job_not_cancellable"]

    def record_field(cc):
        cc["record"]["fields"].append("payload")

    def status_before_added(cc):
        cc["record"]["statuses_before"].append("done")

    def link(cc):
        cc["links"]["queue_contract"] = "data/contracts/missing.yaml"

    def extra_section(cc):
        cc["redrive"] = {}

    return [
        op_added,
        max_attempts,
        job_grammar,
        requester_derived,
        priority_bound,
        payload_bound,
        payload_copy_reading,
        cancellable_reading,
        lease_clock_reading,
        decision_derivation,
        payload_derivation,
        extra_failure,
        mapping_drift,
        retryable,
        record_field,
        status_before_added,
        link,
        extra_section,
    ]


@pytest.mark.parametrize("mutate", _mutants(), ids=lambda f: f.__name__)
def test_mutations_fail_lint(mutate, tmp_path):
    doc = yaml.safe_load(CONTRACT.read_text())
    mutate(doc["contract"])
    path = tmp_path / "cancel.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


def _queue_drifts():
    def max_attempts(q):
        q["state"]["max_attempts"] = 6

    def job_grammar(q):
        q["identifiers"]["job_id"]["grammar"] = "^x$"

    def worker_grammar(q):
        q["identifiers"]["worker"]["grammar"] = "^w$"

    def priority(q):
        q["request"]["bounds"]["priority"] = "int-0-through-19-lower-is-more-urgent"

    def priority_prefix_preserving(q):
        q["request"]["bounds"]["priority"] = "int-0-through-9-higher-is-more-urgent"

    def payload(q):
        q["request"]["bounds"]["payload"] = "exact-canonical-json-depth-at-most-128"

    def payload_prefix_preserving(q):
        q["request"]["bounds"]["payload"] = (
            "exact-canonical-json-acyclic-alias-free-depth-at-most-64-int-digits-at-most-4000"
            "-or-cyclic-payloads-allowed"
        )

    def job_fields(q):
        q["state"]["job_fields"].append("tags")

    def no_leased(q):
        q["state"]["statuses"].remove("leased")

    return [
        max_attempts,
        job_grammar,
        worker_grammar,
        priority,
        priority_prefix_preserving,
        payload,
        payload_prefix_preserving,
        job_fields,
        no_leased,
    ]


@pytest.mark.parametrize("drift", _queue_drifts(), ids=lambda f: f.__name__)
def test_lint_binds_the_queue_sibling(drift, tmp_path, monkeypatch):
    from tools import cancel_contract_lint as cl

    queue = yaml.safe_load((ROOT / "data/contracts/queue.yaml").read_text())
    drift(queue["contract"])
    (tmp_path / "data/contracts").mkdir(parents=True)
    (tmp_path / "data/contracts/queue.yaml").write_text(yaml.safe_dump(queue))
    (tmp_path / "data/contracts/retry.yaml").write_text("{}")
    monkeypatch.setattr(cl, "ROOT", tmp_path)
    with pytest.raises(ContractError):
        cl.lint(CONTRACT)


def test_error_enum_matches_mapping():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)


def test_bounds_are_the_contract_literals():
    bounds = _REQ["bounds"]
    assert bounds["now"] == "int-0-through-2-pow-53-minus-1-milliseconds"
    assert bounds["seq"] == "int-0-through-2-pow-53-minus-2"
    assert bounds["priority"] == "int-0-through-9"
    assert bounds["payload"] == (
        "exact-canonical-json-acyclic-alias-free-depth-at-most-64-int-digits-at-most-4000"
    )
    assert MAX_ATTEMPTS == 5 and MAX == 2**53 - 1


# -- happy path: golden records ----------------------------------------------------------------

_P1 = {"b": [1, 2.5, "café", True, None], "a": {"n": -7}}
GOLDEN = [
    (
        req(
            job=job(job_id=JOB_B, seq=17, priority=0, payload=_P1, attempts=3),
            requester="owner-1",
            now=12,
        ),
        {
            "op": "cancel",
            "job_id": JOB_B,
            "seq": 17,
            "priority": 0,
            "payload_digest": "pd1:d38ba77da32b8f64e5fffd5613ce0bde4a94df6c9a841bb3a08f02a361c39dd0",  # noqa: E501
            "status_before": "ready",
            "attempts": 3,
            "requester": "owner-1",
            "cancelled_at": 12,
            "decision_id": "cd1:7c34d4b151bccee8d4b55fb48f34a0ee533575d8734eebaea6a9ea22c86e3b40",  # noqa: E501
        },
    ),
    (
        req(
            job=job(
                seq=0,
                priority=9,
                payload=None,
                status="leased",
                attempts=2,
                lease_owner="w-1",
                lease_expires_at=9,
            ),
            requester="ops",
            now=0,
        ),
        {
            "op": "cancel",
            "job_id": JOB,
            "seq": 0,
            "priority": 9,
            "payload_digest": "pd1:566b124f704ca6a152fee38fabb9fb8f15b1545051a19d4aec4da156aea6c076",  # noqa: E501
            "status_before": "leased",
            "attempts": 2,
            "requester": "ops",
            "cancelled_at": 0,
            "decision_id": "cd1:724167b5699006d236711a3a85b42e77a2a57fd31a01c4327e000bc49ee06a6a",  # noqa: E501
        },
    ),
]


def test_full_record_equality_golden_vectors():
    for request, expected in GOLDEN:
        got = cancel(copy.deepcopy(request))
        assert type(got) is dict and got == expected
        assert list(got) == _RECORD_FIELDS
        assert all(type(got[k]) is type(expected[k]) for k in expected)


def test_digests_match_the_contract_preimages():
    for request, expected in GOLDEN:
        assert expected["payload_digest"] == _independent("pd1", request["job"]["payload"])
        without = {k: v for k, v in expected.items() if k != "decision_id"}
        assert expected["decision_id"] == _independent("cd1", without)
    for seq, priority, requester, now in ((1, 1, "a", 5), (99, 8, "z" * 64, MAX)):
        request = req(
            job=job(seq=seq, priority=priority, payload=[seq]),
            requester=requester,
            now=now,
        )
        got = cancel(request)
        without = {k: v for k, v in got.items() if k != "decision_id"}
        assert got["decision_id"] == _independent("cd1", without)
        assert got["payload_digest"] == _independent("pd1", [seq])
        assert (got["seq"], got["priority"], got["requester"], got["cancelled_at"]) == (
            seq,
            priority,
            requester,
            now,
        )


def test_payload_is_bound_but_never_copied():
    a = cancel(req(job=job(payload={"x": 1})))
    b = cancel(req(job=job(payload={"x": 2})))
    assert a["payload_digest"] != b["payload_digest"] and a["decision_id"] != b["decision_id"]
    assert "payload" not in a


def test_determinism_and_detachment():
    request = req()
    snap = copy.deepcopy(request)
    a, b = cancel(request), cancel(request)
    assert a == b and a is not b
    assert request == snap
    a["seq"] = -1
    assert cancel(request)["seq"] == 3


# -- boundary ---------------------------------------------------------------------------------


def nest(depth):
    node = 0
    for _ in range(depth):
        node = [node]
    return node


def test_bounds_are_inclusive():
    cancel(req(now=0))
    cancel(req(now=9007199254740991))
    cancel(req(job=job(seq=0)))
    cancel(req(job=job(seq=9007199254740990)))
    cancel(req(job=job(priority=0)))
    cancel(req(job=job(priority=9)))
    cancel(req(job=job(payload=nest(64))))
    cancel(req(job=job(payload={"n": 10**4000 - 1, "m": -(10**4000 - 1)})))
    cancel(req(requester="a"))
    cancel(req(requester="z" * 64))
    for bad in (req(now=-1), req(now=9007199254740992)):
        _raises("malformed_cancel_request", bad)
    for bad in (req(requester=""), req(requester="z" * 65), req(requester="has space")):
        _raises("malformed_cancel_request", bad)
    for bad in (
        job(seq=-1),
        job(seq=9007199254740991),
        job(priority=-1),
        job(priority=10),
        job(payload=nest(65)),
        job(payload={"n": 10**4000}),
        job(payload=-(10**4000)),
    ):
        _raises("corrupt_job", req(job=bad))


# -- cancel semantics: eligibility and the live-lease ruling ------------------------------------


def test_not_cancellable_jobs_are_refused():
    for bad in (
        job(status="done", attempts=1),
        job(status="done", attempts=5),
        job(status="dead", attempts=5),
    ):
        _raises("job_not_cancellable", req(job=bad))


def test_ready_cancellation_withdraws_before_any_claim():
    got = cancel(req(job=job(status="ready", attempts=0)))
    assert (got["status_before"], got["attempts"]) == ("ready", 0)


def test_leased_cancellation_records_the_live_lease_state():
    """A leased job is claimed; the decision is recorded against the live
    lease (status_before leased, attempts kept). The record copies no
    lease fields; applying the decision to the queue or aborting the
    worker is outside this contract."""
    got = cancel(req(job=job(status="leased", attempts=2, lease_owner="w-1", lease_expires_at=MAX)))
    assert (got["status_before"], got["attempts"]) == ("leased", 2)
    assert "lease_owner" not in got and "lease_expires_at" not in got


def test_presented_status_governs_over_lease_expiry():
    """An expired lease is still a leased job here: expiry and reclaim
    are the queue contract's concern, never recomputed by cancel."""
    got = cancel(
        req(
            job=job(status="leased", attempts=1, lease_owner="w-1", lease_expires_at=5),
            now=100,
        )
    )
    assert got["status_before"] == "leased"


def test_leased_lease_field_bounds_are_inclusive():
    cancel(req(job=job(status="leased", attempts=1, lease_owner="w-1", lease_expires_at=1)))
    cancel(req(job=job(status="leased", attempts=5, lease_owner="w-1", lease_expires_at=MAX)))
    cancel(req(job=job(status="leased", attempts=1, lease_owner="w", lease_expires_at=1)))
    cancel(req(job=job(status="leased", attempts=1, lease_owner="w" * 64, lease_expires_at=1)))
    for bad in (
        job(status="leased", attempts=1, lease_owner="w-1", lease_expires_at=0),
        job(status="leased", attempts=1, lease_owner="w-1", lease_expires_at=MAX + 1),
        job(status="leased", attempts=1, lease_owner="", lease_expires_at=1),
        job(status="leased", attempts=1, lease_owner="w" * 65, lease_expires_at=1),
        job(status="leased", attempts=0, lease_owner="w-1", lease_expires_at=1),
    ):
        _raises("corrupt_job", req(job=bad))


def test_queue_job_invariants_are_enforced():
    for bad in (
        job(attempts=-1),
        job(attempts=6),
        job(lease_owner="w-1"),
        job(lease_expires_at=9),
        job(status="leased", attempts=0, lease_owner="w-1", lease_expires_at=9),
        job(status="leased", attempts=2, lease_owner=None, lease_expires_at=9),
        job(status="leased", attempts=2, lease_owner="w-1", lease_expires_at=0),
        job(status="done", attempts=0),
        job(status="done", attempts=6),
        job(status="dead", attempts=4),
        job(status="cancelled"),
    ):
        _raises("corrupt_job", req(job=bad))


# (corrupt non-cancellable job, its single-locus repair): the repair is
# still not cancellable, so it fails job_not_cancellable - job integrity
# is checked for every status before the cancellable check, at each
# invariant of _job_ok.
_NON_CANCELLABLE_INTEGRITY = {
    "done-attempts-zero": (
        job(status="done", attempts=0),
        job(status="done", attempts=1),
    ),
    "done-attempts-past-max": (
        job(status="done", attempts=MAX_ATTEMPTS + 1),
        job(status="done", attempts=MAX_ATTEMPTS),
    ),
    "dead-attempts-below-max": (
        job(status="dead", attempts=MAX_ATTEMPTS - 1),
        job(status="dead", attempts=MAX_ATTEMPTS),
    ),
    "done-lease-owner-set": (
        job(status="done", attempts=1, lease_owner="w-1"),
        job(status="done", attempts=1),
    ),
    "dead-lease-expiry-set": (
        job(status="dead", attempts=5, lease_expires_at=9),
        job(status="dead", attempts=5),
    ),
}


def test_non_cancellable_job_integrity_precedes_the_cancellable_check():
    for label, (bad, fix) in _NON_CANCELLABLE_INTEGRITY.items():
        changed = {k for k in fix if bad[k] != fix[k] or type(bad[k]) is not type(fix[k])}
        assert len(changed) == 1, label
        _raises("corrupt_job", req(job=bad))
        _raises("job_not_cancellable", req(job=fix))


def test_validation_order():
    _raises("malformed_cancel_request", req(requester="!!", job=job(priority=10)))
    _raises("corrupt_job", req(job=job(status="done", attempts=1, priority=10)))


# -- malformed requests and corrupt jobs --------------------------------------------------------

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


class _FloatSub(float):
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


MALFORMED = {
    "none": None,
    "str": "cancel",
    "list": list(req().items()),
    "list-subclass": _ListSub(req().items()),
    "dict-subclass": _DictSub(req()),
    "lying-dict": _LyingDict(req()),
    "missing-now": {k: v for k, v in req().items() if k != "now"},
    "extra": {**req(), "payload": 1},
    "renamed-same-arity": {("Now" if k == "now" else k): v for k, v in req().items()},
    "op-unknown": req(op="redrive"),
    "op-newline": req(op="cancel\n"),
    "op-str-subclass": req(op=_StrSub("cancel")),
    "requester-none": req(requester=None),
    "requester-int": req(requester=5),
    "requester-bool": req(requester=True),
    "requester-newline": req(requester="owner-1\n"),
    "requester-str-subclass": req(requester=_StrSub("owner-1")),
    "requester-repr-raises": req(requester=_ReprRaises("owner-1")),
    "now-bool": req(now=False),
    "now-float": req(now=1.0),
    "now-int-subclass": req(now=_IntSub(1)),
    "job-none": req(job=None),
    "job-list": req(job=list(job().items())),
    "job-dict-subclass": req(job=_DictSub(job())),
    "job-lying-dict": req(job=_LyingDict(job())),
    "job-list-subclass": req(job=_ListSub(job().items())),
}
for _field in _FIELDS:
    for _label, _make in KEY_FORMS.items():
        MALFORMED[f"key-{_label}-{_field}"] = _rekey(req(), _field, _make)

_SHARED = [1]
_CYCLE = []
_CYCLE.append(_CYCLE)
CORRUPT = {
    "missing-field": {k: v for k, v in job().items() if k != "payload"},
    "extra-field": {**job(), "tags": []},
    "renamed-same-arity": {("Seq" if k == "seq" else k): v for k, v in job().items()},
    "job-id-bad": job(job_id="job1:" + "A" * 64),
    "job-id-newline": job(job_id=JOB + "\n"),
    "job-id-str-subclass": job(job_id=_StrSub(JOB)),
    "job-id-repr-raises": job(job_id=_ReprRaises(JOB)),
    "seq-bool": job(seq=True),
    "seq-int-subclass": job(seq=_IntSub(3)),
    "priority-float": job(priority=4.0),
    "status-str-subclass": job(status=_StrSub("ready")),
    "status-newline": job(status="ready\n"),
    "status-unknown": job(status="cancelled"),
    "attempts-bool": job(attempts=True),
    "attempts-int-subclass": job(attempts=_IntSub(3)),
    "ready-lease-owner-set": job(lease_owner="w-1"),
    "ready-lease-expiry-set": job(lease_expires_at=9),
    "leased-no-owner": job(status="leased", attempts=1, lease_expires_at=9),
    "leased-owner-bad-grammar": job(
        status="leased", attempts=1, lease_owner="!!", lease_expires_at=9
    ),
    "payload-dict-subclass": job(payload=_DictSub(a=1)),
    "payload-lying-dict": job(payload=_LyingDict(a=1)),
    "payload-list-subclass": job(payload=_ListSub([1])),
    "payload-nested-dict-subclass": job(payload={"a": [_DictSub(b=1)]}),
    "payload-str-subclass": job(payload={"a": _StrSub("x")}),
    "payload-repr-raises": job(payload=[_ReprRaises("x")]),
    "payload-int-subclass": job(payload=[_IntSub(1)]),
    "payload-float-subclass": job(payload=[_FloatSub(1.5)]),
    "payload-nan": job(payload=[float("nan")]),
    "payload-inf": job(payload={"x": float("inf")}),
    "payload-tuple": job(payload=(1, 2)),
    "payload-set": job(payload={"x": {1}}),
    "payload-bytes": job(payload=b"x"),
    "payload-surrogate": job(payload="\ud800"),
    "payload-surrogate-key": job(payload={"\ud800": 1}),
    "payload-int-key": job(payload={1: 2}),
    "payload-alias": job(payload=[_SHARED, _SHARED]),
    "payload-cycle": job(payload=_CYCLE),
}
for _label, _make in KEY_FORMS.items():
    CORRUPT[f"payload-key-{_label}"] = job(payload={_make("k"): 1})
    for _field in _JOB_FIELDS:
        CORRUPT[f"key-{_label}-{_field}"] = _rekey(job(), _field, _make)


def _snap(obj, active=None):
    active = set() if active is None else active
    if isinstance(obj, (dict, list)):
        if id(obj) in active:
            return ("cycle", type(obj))
        active = active | {id(obj)}
    if isinstance(obj, dict):
        return (
            type(obj),
            [
                (
                    type(k),
                    k.name
                    if type(k) is _Collides
                    else (str.__str__(k) if isinstance(k, str) else k),
                    _snap(v, active),
                )
                for k, v in dict.items(obj)
            ],
        )
    if isinstance(obj, list):
        return (type(obj), [_snap(v, active) for v in list.__iter__(obj)])
    if isinstance(obj, str):
        return (type(obj), str.__str__(obj))
    return (type(obj), obj)


def _hostile(failure_class, request):
    """Typed class, fresh error, no user code ran, input unchanged."""
    snap = _snap(request)
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            cancel(request)
            got = "accepted"
        except CancelError as exc:
            got = (
                type(exc) is CancelError,
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
    assert _snap(request) == snap


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_requests(name):
    _hostile("malformed_cancel_request", MALFORMED[name])


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_corrupt_jobs(name):
    _hostile("corrupt_job", req(job=CORRUPT[name]))


def test_rejections_leave_inputs_bit_identical():
    for bad in (
        req(now=-1),
        req(job=job(status="done", attempts=0)),
        req(job=job(status="done", attempts=1)),
    ):
        before = copy.deepcopy(bad)
        with pytest.raises(CancelError):
            cancel(bad)
        assert bad == before


# -- the reference itself is pinned: one-edit mutants fail the battery -------------------
# Anchors are matched against the reference block's own source (inspect),
# inside the builder, so an edit to this file never breaks collection.

REFERENCE_EDITS = {
    "cancellable-check-off": (
        '    elif request["job"]["status"] not in _CANCELLABLE:\n'
        '        failed = "job_not_cancellable"\n',
        "",
    ),
    "dead-attempts-off": (
        '    if status == "dead" and attempts != MAX_ATTEMPTS:\n        return False\n',
        "",
    ),
    "done-attempts-off": ('    if status == "done" and attempts < 1:\n        return False\n', ""),
    "lease-free-off": (
        "    elif owner is not None or expires is not None:\n        return False\n",
        "",
    ),
    "leased-attempts-off": (" and attempts >= 1\n", "\n"),
    "payload-check-off": ('    return _payload_ok(job["payload"])', "    return True"),
    "alias-off": (" or id(node) in seen:", ":"),
    "finite-off": ("        return math.isfinite(node)", "        return True"),
    "utf8-off": ('            node.encode("utf-8")\n', "            pass\n"),
    "payload-key-type-off": (
        "if type(key) is not str or not _scalar_ok(key):",
        "if not _scalar_ok(key):",
    ),
    "depth-plus": ("_REF_MAX_DEPTH = 64", "_REF_MAX_DEPTH = 65"),
    "digits-plus": ("_REF_MAX_DIGITS = 4000", "_REF_MAX_DIGITS = 4001"),
    "seq-plus": ("_REF_MAX_SEQ = 2**53 - 2", "_REF_MAX_SEQ = 2**53 - 1"),
    "priority-plus": ("_REF_MAX_PRIORITY = 9", "_REF_MAX_PRIORITY = 10"),
    "clock-plus": ("_REF_MAX_NOW = 2**53 - 1", "_REF_MAX_NOW = 2**53"),
    "job-type-off": ('        and type(request["job"]) is dict\n', ""),
    "requester-grammar-off": (
        '        and _grammar(request["requester"], _REQUESTER_RE)\n',
        '        and type(request["requester"]) is str\n',
    ),
    "key-guard-off": ("        and all(type(k) is str for k in mapping)\n", ""),
    "grammar-type-off": ("return type(value) is str and regex.fullmatch", "return regex.fullmatch"),
    "int-type-off": ("return type(value) is int and lo <= value", "return lo <= value"),
    "op-const": ('"op": "cancel",', '"op": "deleted",'),
    "status-before-const": ('"status_before": job["status"],', '"status_before": "ready",'),
    "attempts-const": ('"attempts": job["attempts"],', '"attempts": 0,'),
    "requester-const": ('"requester": req["requester"],', '"requester": "attacker",'),
    "cancelled-at-shift": ('"cancelled_at": req["now"],', '"cancelled_at": req["now"] + 1,'),
    "seq-const": ('"seq": job["seq"],', '"seq": 0,'),
    "priority-const": ('"priority": job["priority"],', '"priority": 9,'),
    "job-id-const": ('"job_id": job["job_id"],', '"job_id": "job1:" + "a" * 64,'),
    "payload-digest-prefix": ('_digest("pd1", job["payload"])', '_digest("cd1", job["payload"])'),
    "id-nul-dropped": ('prefix.encode() + b"\\x00" + ', "prefix.encode() + "),
    "decision-preimage-extra": (
        'record["decision_id"] = _digest("cd1", record)',
        'record["decision_id"] = _digest("cd1", {**record, "v": 1})',
    ),
    "retryable-true": ("self.retryable = False", "self.retryable = True"),
    "order-swap": (
        '    elif not _job_ok(request["job"]):\n'
        '        failed = "corrupt_job"\n'
        '    elif request["job"]["status"] not in _CANCELLABLE:\n'
        '        failed = "job_not_cancellable"\n',
        '    elif request["job"]["status"] not in _CANCELLABLE:\n'
        '        failed = "job_not_cancellable"\n'
        '    elif not _job_ok(request["job"]):\n'
        '        failed = "corrupt_job"\n',
    ),
    "attempts-bound-plus": (
        "_int_in(attempts, 0, MAX_ATTEMPTS)",
        "_int_in(attempts, 0, MAX_ATTEMPTS + 1)",
    ),
    "attempts-floor-minus": (
        "_int_in(attempts, 0, MAX_ATTEMPTS)",
        "_int_in(attempts, -1, MAX_ATTEMPTS)",
    ),
    "lease-owner-grammar-off": ("_grammar(owner, _WORKER_RE)", "type(owner) is str"),
    "lease-expiry-bound-plus": (
        "_int_in(expires, 1, _REF_MAX_NOW)",
        "_int_in(expires, 1, _REF_MAX_NOW + 1)",
    ),
    "chained-error": (
        "        raise CancelError(failed)",
        "        try:\n            raise KeyError(failed)\n"
        "        except KeyError:\n            raise CancelError(failed)",
    ),
}


# Edits that must NOT change behavior. not-detached: after validation the
# record copies only exact scalars plus the payload DIGEST, so working on
# the live request cannot alias or mutate anything the caller holds.
EQUIVALENT_EDITS = {
    "not-detached": ("req = json.loads(_canon(request))", "req = request"),
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
    jobs += [(f"corrupt:{n}", lambda n=n: test_corrupt_jobs(n)) for n in CORRUPT]
    for label, job_ in jobs:
        try:
            job_()
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
