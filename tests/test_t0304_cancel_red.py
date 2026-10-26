"""T0304 permanent red battery for the jobs cancel function.

Drives every row of the T0303 fixture (tests/fixtures/cancel/cases.json),
hostile-input totality probes and pinned semantic probes through a
`cancel(request)` callable and its `CancelError` class:

- happy/boundary: each request returns the pinned record exactly (exact
  types), the payload_digest and decision_id are re-derived here from the
  contract preimages, the record stores only the digest (never a payload
  copy) and never the lease fields, the request is provably untouched
  (T0240 atomicity machinery: identity walk, alias topology, encoded
  bytes) and shares no container with the record;
- malformed: each row rejects with the pinned class and mapped code, fresh
  (no __cause__ or __context__), the request provably untouched (same
  atomicity proof), and its single-locus repair is accepted or fails with
  the declared class as pinned;
- rollback: a rejected request leaves everything untouched and the valid
  follow-up cancels as pinned;
- totality: hostile types at every request and job field, and hostile payload
  objects, fail closed with a typed error and never run caller code;
- semantics: only ready and leased jobs are cancellable, done and dead are
  job_not_cancellable, dead needs max attempts, done needs an attempt, a
  lease needs owner, expiry and an attempt, validation order.

Standalone-red convention: GREEN against the contract-derived reference in
tests.test_t0302_cancel_contract; every mutant below is RED on its pinned
target check. The production task switches the binding by replacing ONLY the
two binding lines with the production cancel / CancelError names.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0302_cancel_contract as _reference  # noqa: E402
from tools.cancel_contract_lint import FAILURE_MAPPING  # noqa: E402

cancel = _reference.cancel
CancelError = _reference.CancelError

CASES = json.loads((ROOT / "tests" / "fixtures" / "cancel" / "cases.json").read_text())
JOB = "job1:" + "a" * 64
MAX = 2**53 - 1
MAX_ATTEMPTS = 5
RECORD_FIELDS = (
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
)
CLASSES = ("malformed_cancel_request", "corrupt_job", "job_not_cancellable")


def _job(**kw):
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


def _req(**kw):
    base = {"op": "cancel", "job": _job(), "requester": "owner-1", "now": 100}
    base.update(kw)
    return base


def _digest(prefix, value):
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return prefix + ":" + hashlib.sha256(prefix.encode() + bytes([0]) + body.encode()).hexdigest()


def _walk_ids(obj, out):
    if isinstance(obj, (dict, list)):
        out.add(id(obj))
        for v in obj.values() if isinstance(obj, dict) else obj:
            _walk_ids(v, out)
    return out


# -- input atomicity (the T0240 machinery, adapted) -------------------------
# A value-level == snapshot cannot see an equal-valued container swap or a
# bool-for-int retype. The snapshot below pins, fail-closed and in this
# order: (a) the joint identity walk over exact builtin containers via base
# operations only - a dict/list subclass is rejected before any hook can
# run; (b) the alias-topology signature - equal values with different
# sharing diverge; (c) deterministic encoded bytes - order-sensitive, NaN
# rejected. Requests that legitimately carry non-encodable leaves (the
# NaN/inf payload probes) degrade to the walk+signature layers only;
# requests that ARE hostile containers degrade to a root identity pin while
# the Touched hooks prove no caller code ran.


class _NonExactContainer(Exception):
    """A dict/list SUBCLASS reached a path the atomicity walk was about to
    traverse; raised before any user-defined hook on it can run."""


def _walk_identity(roots):
    """Identity map of the joint object graph: only exact builtin dict/list
    containers are traversed, only through base operations; every reached
    path pins the exact type and the ORIGINAL object (kept alive); an
    already-reached container records an alias edge."""
    seen = {}
    entries = {}
    keepalive = []

    def walk(obj, path):
        obj_type = type(obj)
        if obj_type is dict or obj_type is list:
            oid = id(obj)
            if oid in seen:
                entries[path] = ("alias", seen[oid])
                return
            seen[oid] = path
            keepalive.append(obj)
            entries[path] = ("obj", obj_type, obj)
            if obj_type is dict:
                for key, value in dict.items(obj):
                    walk(value, path + (("k", key),))
            else:
                for index, value in enumerate(list.__iter__(obj)):
                    walk(value, path + (("i", index),))
        elif isinstance(obj, (dict, list)):
            raise _NonExactContainer(f"non-exact container {obj_type.__name__!r} at {path!r}")
        else:
            entries[path] = ("leaf", obj_type, repr(obj))

    for rindex, root in enumerate(roots):
        walk(root, (("r", rindex),))
    return entries, keepalive


def _graph_signature(roots):
    """Canonical alias-topology signature: first-encounter container indices,
    dict entries in insertion order, leaves pinned by exact type and repr."""
    memo = {}

    def sig(obj):
        obj_type = type(obj)
        if obj_type is dict or obj_type is list:
            oid = id(obj)
            if oid in memo:
                return ("ref", memo[oid])
            idx = len(memo)
            memo[oid] = idx
            if obj_type is dict:
                return ("dict", idx, tuple((key, sig(value)) for key, value in dict.items(obj)))
            return ("list", idx, tuple(sig(value) for value in list.__iter__(obj)))
        return ("leaf", obj_type.__name__, repr(obj))

    return tuple(sig(root) for root in roots)


def _encoded_bytes(obj):
    """Insertion-order-preserving compact JSON bytes; called only after the
    graph is proven closed over exact builtin containers."""
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _snap(request):
    """Pre-call atomicity snapshot of the input, in degrade tiers."""
    try:
        walked = _walk_identity((request,))
    except _NonExactContainer:
        return ("root", (id(request), type(request)))
    signature = _graph_signature((request,))
    try:
        encoded = _encoded_bytes(request)
    except (ValueError, TypeError):
        # NaN/inf/bytes leaves cannot be encoded: the walk layers stand
        encoded = None
    return ("walk", (walked, signature, encoded))


def _verify_snap(snap, request):
    """Post-call verification: same original object and exact type at every
    path, same alias topology, same encoded bytes when the tier has them."""
    kind, payload = snap
    if kind == "root":
        want_id, want_type = payload
        assert id(request) == want_id and type(request) is want_type
        return
    (want_entries, _keep), want_signature, want_encoded = payload
    got_entries, _ = _walk_identity((request,))
    assert set(got_entries) == set(want_entries)
    for path, wanted in want_entries.items():
        got = got_entries[path]
        if wanted[0] == "alias":
            assert got == wanted
        elif wanted[0] == "obj":
            assert got[0] == "obj"
            assert got[1] is wanted[1]
            assert got[2] is wanted[2]  # the ORIGINAL object survives here
        else:
            assert got == wanted
    assert _graph_signature((request,)) == want_signature
    if want_encoded is not None:
        assert _encoded_bytes(request) == want_encoded


def _assert_record(request, record, expect):
    assert type(record) is dict and record == expect
    assert set(record) == set(RECORD_FIELDS)
    for key, value in expect.items():
        assert type(record[key]) is type(value), key
    assert record["payload_digest"] == _digest("pd1", request["job"]["payload"])
    assert record["decision_id"] == _digest(
        "cd1", {k: v for k, v in record.items() if k != "decision_id"}
    )
    assert "payload" not in record
    assert "lease_owner" not in record and "lease_expires_at" not in record
    assert not (_walk_ids(record, set()) & _walk_ids(request, set()))


def _expected_record(request):
    """The record a valid request must produce, re-derived from the contract
    preimages here (not read from any implementation)."""
    job = request["job"]
    record = {
        "op": "cancel",
        "job_id": job["job_id"],
        "seq": job["seq"],
        "priority": job["priority"],
        "payload_digest": _digest("pd1", job["payload"]),
        "status_before": job["status"],
        "attempts": job["attempts"],
        "requester": request["requester"],
        "cancelled_at": request["now"],
    }
    record["decision_id"] = _digest("cd1", record)
    return record


def _ok(impl, request, expect):
    snap = _snap(request)
    record = impl(request)
    _verify_snap(snap, request)
    _assert_record(request, record, expect)
    return record


def _rejects(impl, err, request, failure):
    snap = _snap(request)
    with _raises_ctx(err) as caught:
        impl(request)
    exc = caught.value
    assert type(exc) is err
    assert exc.failure_class == failure
    assert exc.code == FAILURE_MAPPING[failure]
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.retryable is False
    _verify_snap(snap, request)


def _nested(depth):
    node = 1
    for _ in range(depth):
        node = [node]
    return node


def _nested_dict(depth):
    node = 1
    for _ in range(depth):
        node = {"k": node}
    return node


# ---- checks shared by the tests and the mutant harness ---------------------------------


def check_fixture(impl, err):
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            assert len(row["requests"]) == len(row["expect_records"]), row["name"]
            for request, expect in zip(row["requests"], row["expect_records"], strict=True):
                _ok(impl, request, expect)
    for row in CASES["malformed"]:
        _rejects(impl, err, row["request"], row["expect_failure"])
        repaired = row["minimal_repair"]["request"]
        declared = row["minimal_repair"].get("expect_failure")
        if declared is not None:
            # a corrupt done/dead job's repair is still not cancellable;
            # only these five rows may fail, with the pinned class
            _rejects(impl, err, repaired, declared)
        else:
            # every other repair is a VALID request: it must be accepted
            # with a valid record, and the repair input stays atomic
            snap = _snap(repaired)
            try:
                record = impl(repaired)
            except err as exc:
                raise AssertionError(
                    f"{row['name']}: a valid repair was refused ({exc.failure_class})"
                ) from None
            _verify_snap(snap, repaired)
            _assert_record(repaired, record, _expected_record(repaired))
    for row in CASES["rollback"]:
        _rejects(impl, err, row["rejected_request"], row["expect_failure"])
        _ok(impl, row["request"], row["expect_record"])


def check_semantics(impl, err):
    for requester in ("owner-1", "owner-2"):
        assert impl(_req(requester=requester))["requester"] == requester
    a, b = impl(_req()), impl(_req())
    assert a == b and a is not b
    assert impl(_req(requester="owner-2"))["decision_id"] != impl(_req())["decision_id"]
    assert impl(_req(now=101))["cancelled_at"] == 101
    one, two = impl(_req(job=_job(payload={"a": 1}))), impl(_req(job=_job(payload={"a": 2})))
    assert one["payload_digest"] != two["payload_digest"]
    swapped = impl(_req(job=_job(payload={"b": 1, "a": 2})))
    assert (
        swapped["payload_digest"]
        == impl(_req(job=_job(payload={"a": 2, "b": 1})))["payload_digest"]
    )
    # a live lease cancels; the record keeps status_before, never the lease fields
    leased = _job(status="leased", attempts=2, lease_owner="w1", lease_expires_at=200)
    rec = impl(_req(job=leased))
    assert (rec["status_before"], rec["attempts"]) == ("leased", 2)
    for status, extra in (
        ("done", {"attempts": 1}),
        ("done", {"attempts": MAX_ATTEMPTS}),
        ("dead", {"attempts": MAX_ATTEMPTS}),
    ):
        _rejects(impl, err, _req(job=_job(status=status, **extra)), "job_not_cancellable")
    assert impl(_req(job=_job(attempts=4)))["op"] == "cancel"  # ready at 4 cancels
    assert impl(_req(job=_job(attempts=MAX_ATTEMPTS)))["op"] == "cancel"  # and at max
    leased_zero = _job(status="leased", attempts=0, lease_owner="w1", lease_expires_at=9)
    _rejects(impl, err, _req(job=leased_zero), "corrupt_job")  # a lease implies an attempt
    _rejects(impl, err, _req(job=_job(attempts=-1)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(attempts=MAX_ATTEMPTS + 1)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(status="dead", attempts=4)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(status="done", attempts=0)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(lease_owner="w1")), "corrupt_job")
    _rejects(impl, err, _req(job=_job(lease_expires_at=5)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload=_nested(65))), "corrupt_job")
    assert impl(_req(job=_job(payload=_nested(64))))["op"] == "cancel"
    _rejects(impl, err, _req(job=_job(payload=_nested_dict(65))), "corrupt_job")
    assert impl(_req(job=_job(payload=_nested_dict(64))))["op"] == "cancel"
    _rejects(impl, err, _req(job=_job(payload="\ud800")), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload={"a": float("nan")})), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload=10**4001)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload=10**4000)), "corrupt_job")  # 4001 digits
    assert impl(_req(job=_job(payload=10**3999)))["op"] == "cancel"  # exactly 4000
    assert impl(_req(job=_job(payload=-(10**3999))))["op"] == "cancel"
    # finite floats are valid payload scalars, anywhere in the tree
    for good in (1.5, -0.0, 5e-324, 1.7976931348623157e308):
        assert impl(_req(job=_job(payload=good)))["op"] == "cancel", good
    assert impl(_req(job=_job(payload={"a": [1.5, {"b": -2.25}]})))["op"] == "cancel"
    _rejects(impl, err, _req(job=_job(payload=float("inf"))), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload=[float("-inf")])), "corrupt_job")
    # a payload object key must be an exact str: plain int and bool keys are corrupt
    _rejects(impl, err, _req(job=_job(payload={1: "v"})), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload={True: "v"})), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload={"a": {2: "v"}})), "corrupt_job")
    assert impl(_req(job=_job(payload={"1": "v"})))["op"] == "cancel"
    shared = [1]
    _rejects(impl, err, _req(job=_job(payload={"a": shared, "b": shared})), "corrupt_job")
    # the payload ROOT is any JSON scalar; bytes and tuples are not JSON
    for good in (True, False, None, 7, "x"):
        assert impl(_req(job=_job(payload=good)))["op"] == "cancel", good
    _rejects(impl, err, _req(job=_job(payload=b"b")), "corrupt_job")
    _rejects(impl, err, _req(job=_job(payload=(1,))), "corrupt_job")


def check_order(impl, err):
    # shape before job integrity
    _rejects(impl, err, _req(job=_job(seq=-1), now=-1), "malformed_cancel_request")
    _rejects(
        impl, err, _req(job=_job(seq=-1), requester="bad requester!"), "malformed_cancel_request"
    )
    # job integrity before the cancellable check: a corrupt done/dead job is
    # corrupt, not job_not_cancellable
    _rejects(impl, err, _req(job=_job(status="done", attempts=0)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(status="dead", attempts=4)), "corrupt_job")
    _rejects(impl, err, _req(job=_job(lease_owner="w1")), "corrupt_job")
    _rejects(impl, err, _req(job=_job(status="bogus")), "corrupt_job")


class Boom:
    def __getattribute__(self, name):
        raise Touched("caller object touched: " + name)


class BoomStr(str):
    def __eq__(self, other):
        raise Touched("eq hook")

    __hash__ = str.__hash__


class BoomDict(dict):
    def __iter__(self):
        raise Touched("iter hook")

    def keys(self):
        raise Touched("keys hook")

    def items(self):
        raise Touched("items hook")


class BoomList(list):
    def __iter__(self):
        raise Touched("iter hook")

    def __len__(self):
        raise Touched("len hook")


HOSTILE = (None, True, False, -1, 2**70, 1.0, float("nan"), "", "x", b"b", [], (1,), {}, Boom)
JOB_FIELDS = tuple(_job())


# hostile values that are actually VALID for a field: "x" is a grammar-valid
# requester, so the reference accepts it there
_FIELD_VALID = {"requester": {"x"}}


def check_totality(impl, err):
    for field in ("op", "job", "requester", "now"):
        for bad in HOSTILE:
            if type(bad) is str and bad in _FIELD_VALID.get(field, ()):
                continue
            request = _req()
            request[field] = bad
            try:
                impl(request)
            except err as exc:
                assert exc.failure_class in CLASSES
            else:
                raise AssertionError((field, bad))
    for field in JOB_FIELDS:
        if field == "payload":
            continue  # the payload root is covered by scoped checks above:
            # scalars are valid JSON, only non-JSON roots reject
        for bad in HOSTILE:
            candidate = _job()
            candidate[field] = bad
            if candidate == _job():
                continue
            try:
                impl(_req(job=candidate))
            except err as exc:
                assert exc.failure_class in ("corrupt_job", "job_not_cancellable")
            else:
                raise AssertionError((field, bad))
    for bad in (Boom(), BoomDict(), BoomStr("cancel"), [_req()], (_req(),), "cancel", None):
        try:
            impl(bad)
        except err as exc:
            assert exc.failure_class == "malformed_cancel_request"
        else:
            raise AssertionError(bad)
    for forged in (
        BoomDict(_req()),
        _req(job=BoomDict(_job())),
        _req(requester=BoomStr("owner-1")),
        _req(job=_job(job_id=BoomStr(JOB))),
        _req(job=_job(status=BoomStr("ready"))),
        _req(job=_job(payload=Boom())),
        _req(job=_job(payload=BoomDict(a=1))),
        _req(job=_job(payload=BoomList([1]))),
        _req(job=_job(payload={BoomStr("k"): 1})),
    ):
        try:
            impl(forged)
        except err:
            pass
        except AssertionError as exc:
            raise Touched("caller code ran: " + str(exc)) from None
        else:
            raise Touched("subclass accepted")
    for bad in (JOB + "\n", "job1:" + "A" * 64, "job1:" + "\u0661" * 64):
        _rejects(impl, err, _req(job=_job(job_id=bad)), "corrupt_job")
    _rejects(impl, err, {**_req(), "extra": 1}, "malformed_cancel_request")
    missing = _req()
    del missing["now"]
    _rejects(impl, err, missing, "malformed_cancel_request")
    _rejects(impl, err, _req(job={**_job(), "extra": 1}), "corrupt_job")


class Touched(AssertionError):
    """A caller-owned hostile object was touched by the implementation."""


class _raises_ctx:
    """pytest.raises that reports a missing refusal as a plain AssertionError."""

    def __init__(self, expected):
        self.expected = expected
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        if kind is None:
            raise AssertionError(f"expected {self.expected.__name__}, nothing was raised")
        if not issubclass(kind, self.expected):
            return False
        self.value = value
        return True


class RawEscape(AssertionError):
    """A non-contract exception escaped the implementation (a crash, not a decision)."""


def _guard(impl, err, oracle=None, crash=None):
    """Any exception that is not the typed contract error is a crash and becomes
    RawEscape (an AssertionError raised inside the implementation included),
    except Touched, which is the semantic detection of a caller object being
    touched. With an oracle (the reference), a typed refusal of an input the
    reference accepts is an acceptance failure and surfaces as a plain
    AssertionError. The classification runs outside the except scope so a
    correct refusal keeps __context__ None."""

    def guarded(*args):
        refusal = None
        try:
            return impl(*args)
        except err as caught:
            refusal = caught
        except Touched:
            raise
        except Exception as exc:  # noqa: BLE001
            crashed = type(exc).__name__
        if refusal is None:
            raise (crash or RawEscape)(f"raw {crashed} escaped") from None
        if oracle is not None:
            try:
                oracle(*copy.deepcopy(args))
            except Exception:  # noqa: BLE001 - the reference refuses too
                accepted = False
            else:
                accepted = True
            if accepted:
                raise AssertionError(
                    f"refused an input the reference accepts: {refusal.failure_class}"
                )
        raise refusal

    return guarded


class _InertKey(str):
    """A str subclass with no hooks: only its type differs from a plain str."""


def _inert(mapping):
    return {_InertKey(k): v for k, v in mapping.items()}


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


def check_inert_values(impl, err):
    # str / int / dict subclasses and bools are not the exact built-in types
    for field, bad in (
        ("op", _StrSub("cancel")),
        ("requester", _StrSub("owner-1")),
        ("now", True),
        ("now", _IntSub(100)),
    ):
        _rejects(impl, err, _req(**{field: bad}), "malformed_cancel_request")
    _rejects(impl, err, _DictSub(_req()), "malformed_cancel_request")
    _rejects(impl, err, _req(job=_DictSub(_job())), "malformed_cancel_request")
    for field, bad in (
        ("job_id", _StrSub(JOB)),
        ("seq", True),
        ("seq", _IntSub(3)),
        ("priority", _IntSub(4)),
        ("status", _StrSub("ready")),
        ("attempts", _IntSub(0)),
        ("lease_owner", _StrSub("w1")),
        ("payload", _DictSub({"k": 1})),
        ("payload", [_StrSub("x")]),
        ("payload", [_IntSub(1)]),
    ):
        _rejects(impl, err, _req(job=_job(**{field: bad})), "corrupt_job")
    # container kinds are exact: a dict subclass or a tuple inside a payload is not JSON
    for bad in ({"k": _DictSub()}, [(1,)], {"k": [{"x": (1,)}]}, [["\ud800"]], {"k": ["\ud800"]}):
        _rejects(impl, err, _req(job=_job(payload=bad)), "corrupt_job")


def check_inert_keys(impl, err):
    _rejects(impl, err, _inert(_req()), "malformed_cancel_request")
    _rejects(impl, err, _req(job=_inert(_job())), "corrupt_job")


CHECKS = (
    check_fixture,
    check_semantics,
    check_order,
    check_totality,
    check_inert_keys,
    check_inert_values,
)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_bound_implementation_passes(check):
    check(_guard(cancel, CancelError, crash=AssertionError), CancelError)


def test_fixture_covers_every_failure_class_and_status_before():
    assert {r["expect_failure"] for r in CASES["malformed"]} == set(CLASSES)
    statuses = {
        rec["status_before"]
        for s in ("happy", "boundary")
        for r in CASES[s]
        for rec in r["expect_records"]
    }
    assert statuses == {"ready", "leased"}


# ---- mutants: the battery must be RED on each ------------------------------------------


def _wrap(fn):
    def impl(request):
        return fn(request, cancel)

    return impl


def _redo(out):
    out["decision_id"] = _digest("cd1", {k: v for k, v in out.items() if k != "decision_id"})
    return out


def _m_cancels_done(request, real):
    try:
        return real(request)
    except CancelError as exc:
        if exc.failure_class == "job_not_cancellable":
            return real({**request, "job": {**request["job"], "status": "ready"}})
        raise


def _m_stores_payload(request, real):
    out = real(request)
    out["payload"] = request["job"]["payload"]
    return out


def _m_digest_unsorted(request, real):
    out = real(request)
    body = json.dumps(request["job"]["payload"], separators=(",", ":"), ensure_ascii=True)
    out["payload_digest"] = "pd1:" + hashlib.sha256(b"pd1\x00" + body.encode()).hexdigest()
    return _redo(out)


def _m_now_ignored(request, real):
    out = real(request)
    out["cancelled_at"] = 100
    return _redo(out)


def _m_requester_not_bound(request, real):
    out = real(request)
    if out["requester"] == "owner-2":
        out["requester"] = "owner-1"
        _redo(out)
    return out


def _m_done_zero_ok(request, real):
    try:
        return real(request)
    except CancelError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_job"
            and isinstance(job, dict)
            and job.get("status") == "done"
            and job.get("attempts") == 0
        ):
            return real({**request, "job": {**job, "attempts": 1}})
        raise


def _m_lease_ignored(request, real):
    try:
        return real(request)
    except CancelError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if exc.failure_class == "corrupt_job" and isinstance(job, dict) and job.get("lease_owner"):
            return real({**request, "job": {**job, "lease_owner": None}})
        raise


def _m_not_cancellable_before_corrupt(request, real):
    try:
        return real(request)
    except CancelError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_job"
            and isinstance(job, dict)
            and job.get("status") in ("done", "dead")
        ):
            raise CancelError("job_not_cancellable") from None
        raise


def _m_raw_exception(request, real):
    if isinstance(request, dict) and request.get("now") is None:
        raise KeyError("now")
    return real(request)


def _m_payload_unbounded(request, real):
    try:
        return real(request)
    except CancelError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_job"
            and isinstance(job, dict)
            and isinstance(job.get("payload"), list)
        ):
            return real({**request, "job": {**job, "payload": []}})
        raise


def _m_replaces_payload_container(request, real):
    out = real(request)
    request["job"]["payload"] = copy.deepcopy(request["job"]["payload"])
    return out


def _m_dirties_on_refusal(request, real):
    try:
        return real(request)
    except CancelError:
        job = request.get("job") if isinstance(request, dict) else None
        if isinstance(job, dict) and job.get("attempts") == 0:
            job["attempts"] = False
        raise


def _m_refuses_valid_repair(request, real):
    """Refuses the now_bool row's valid repair (now 1, otherwise reference
    values) with corrupt_job; the battery's own acceptance assertion kills
    it in check_fixture, no oracle needed."""
    if (
        isinstance(request, dict)
        and request.get("op") == "cancel"
        and type(request.get("now")) is int
        and request["now"] == 1
    ):
        raise CancelError("corrupt_job")
    return real(request)


def _m_payload_root_untyped(request, real):
    try:
        return real(request)
    except CancelError as exc:
        job = request["job"] if isinstance(request, dict) else None
        if (
            exc.failure_class == "corrupt_job"
            and isinstance(job, dict)
            and isinstance(job.get("payload"), (bytes, tuple))
        ):
            return real({**request, "job": {**job, "payload": []}})
        raise


def _m_refuses_valid(request, real):
    if isinstance(request, dict) and request.get("requester") == "owner-2":
        raise CancelError("job_not_cancellable")
    return real(request)


def _m_refuses_everything(request, real):
    raise CancelError("malformed_cancel_request")


MUTANTS = {
    "refuses_valid": (_m_refuses_valid, "check_semantics"),
    "refuses_everything": (_m_refuses_everything, "check_fixture"),
    "cancels_done": (_m_cancels_done, "check_semantics"),
    "stores_payload": (_m_stores_payload, "check_fixture"),
    "digest_unsorted": (_m_digest_unsorted, "check_semantics"),
    "now_ignored": (_m_now_ignored, "check_semantics"),
    "requester_not_bound": (_m_requester_not_bound, "check_semantics"),
    "done_zero_ok": (_m_done_zero_ok, "check_semantics"),
    "lease_ignored": (_m_lease_ignored, "check_semantics"),
    "not_cancellable_before_corrupt": (_m_not_cancellable_before_corrupt, "check_order"),
    "payload_unbounded": (_m_payload_unbounded, "check_semantics"),
    "replaces_payload_container": (_m_replaces_payload_container, "check_fixture"),
    "dirties_on_refusal": (_m_dirties_on_refusal, "check_fixture"),
    "refuses_valid_repair": (_m_refuses_valid_repair, "check_fixture"),
    "payload_root_untyped": (_m_payload_root_untyped, "check_semantics"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_target(name):
    mutate, target = MUTANTS[name]
    check = next(c for c in CHECKS if c.__name__ == target)
    try:
        check(_guard(_wrap(mutate), CancelError, cancel), CancelError)
    except RawEscape:
        raise AssertionError(f"mutant {name} crashed, not refuted") from None
    except CancelError:
        raise AssertionError(f"mutant {name} died of a typed error, not an assertion") from None
    except AssertionError:
        pass
    else:
        raise AssertionError(f"mutant {name} survived {target}")


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_guard_with_oracle_is_identity_on_the_reference(check):
    check(_guard(cancel, CancelError, cancel, crash=AssertionError), CancelError)


def test_raw_exception_is_a_totality_failure_not_a_mutant_kill():
    """A crash is detected by the totality check as RawEscape; it is not
    counted among the semantic mutant kills above."""
    with pytest.raises(RawEscape):
        check_totality(_guard(_wrap(_m_raw_exception), CancelError), CancelError)
