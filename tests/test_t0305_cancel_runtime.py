"""T0305: production jobs cancel runtime proof (server/jobs_cancel.py).

The test_t0304_cancel_red red battery is the behavioral proof: it runs
against server.jobs_cancel with only its two binding lines switched. This
file proves the switch is exactly those two lines, that production never
imports the test package, that its public surface is the contract's, and
that production and the T0302 reference agree (same record, or same
failure class and code, and the request unchanged) on every fixture
request and on a grid of hostile variants of each one: every field
replaced by junk, dropped, or given an extra sibling, and every nested
job field treated the same way.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import jobs_cancel  # noqa: E402
from server.jobs_cancel import CancelError, cancel  # noqa: E402
from tests import test_t0302_cancel_contract as _reference  # noqa: E402
from tests import test_t0304_cancel_red as _red  # noqa: E402
from tools.cancel_contract_lint import FAILURE_MAPPING  # noqa: E402

RED = ROOT / "tests" / "test_t0304_cancel_red.py"
PRODUCTION = ROOT / "server" / "jobs_cancel.py"
CASES = json.loads((ROOT / "tests" / "fixtures" / "cancel" / "cases.json").read_text())
ORACLE_LINES = "cancel = _reference.cancel\nCancelError = _reference.CancelError\n"
PRODUCTION_LINES = (
    'cancel = __import__("server.jobs_cancel").jobs_cancel.cancel\n'
    'CancelError = __import__("server.jobs_cancel").jobs_cancel.CancelError\n'
)
# sha256 of the red battery as landed before this switch
RED_AS_LANDED_SHA256 = "3814204b56be156e4e82adf00eaf632f562b593f322377d43b0e6e5f72ed19bc"
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "math",
    "re",
    "pathlib",
    "yaml",
    "tools.cancel_contract_lint",
}
JUNK = (
    None,
    True,
    False,
    0,
    1,
    -1,
    2**53,
    2**64,
    1.5,
    float("nan"),
    "",
    "x",
    "job1:" + "f" * 64,
    b"x",
    [],
    {},
    [1],
    {"a": 1},
)


def test_r1_red_battery_switch_is_exactly_the_two_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    # the oracle import stays (now unused) and carries one extra F401 noqa
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES).replace(", F401", "")
    assert hashlib.sha256(restored.encode()).hexdigest() == RED_AS_LANDED_SHA256
    assert _red.cancel is cancel
    assert _red.CancelError is CancelError


def test_r2_production_imports_no_test_code():
    seen = set()
    for node in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            seen.add(node.module)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) in (
            "__import__",
            "exec",
            "eval",
        ):
            raise AssertionError(f"dynamic import or exec at line {node.lineno}")
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS
    assert not any(name == "tests" or name.startswith("tests.") for name in seen)


def test_r3_public_surface():
    assert set(jobs_cancel.__all__) == {"FAILURE_MAPPING", "CancelError", "cancel"}
    assert issubclass(CancelError, Exception) and CancelError is not _reference.CancelError
    assert jobs_cancel.FAILURE_MAPPING is FAILURE_MAPPING


def _outcome(fn, error_cls, request):
    before = json.dumps(request, sort_keys=True, default=repr)
    try:
        out = ("ok", fn(request))
    except error_cls as error:
        assert type(error) is error_cls
        assert error.__cause__ is None and error.__context__ is None
        out = ("fail", error.failure_class, error.code)
    assert json.dumps(request, sort_keys=True, default=repr) == before
    return out


def _both(request):
    prod = _outcome(cancel, CancelError, copy.deepcopy(request))
    ref = _outcome(_reference.cancel, _reference.CancelError, copy.deepcopy(request))
    assert repr(prod) == repr(ref), request
    return prod


def _requests(node, found):
    if isinstance(node, dict):
        if node.get("op") == "cancel" and len(node) > 2:
            found.append(node)
        for value in node.values():
            _requests(value, found)
    elif isinstance(node, list):
        for value in node:
            _requests(value, found)
    return found


FIXTURE_REQUESTS = _requests([CASES[s] for s in ("happy", "boundary", "malformed", "rollback")], [])


def test_r4_every_fixture_request_agrees_with_the_reference():
    assert len(FIXTURE_REQUESTS) > 40
    kinds = {_both(request)[0] for request in FIXTURE_REQUESTS}
    assert kinds == {"ok", "fail"}


def test_r4_happy_and_boundary_rows_produce_the_pinned_records():
    count = 0
    for section in ("happy", "boundary"):
        assert CASES[section]
        for row in CASES[section]:
            assert len(row["requests"]) == len(row["expect_records"]) > 0
            for request, record in zip(row["requests"], row["expect_records"], strict=True):
                assert _both(request) == ("ok", record), (row["name"], request)
                assert cancel(copy.deepcopy(request)) == record
                count += 1
    assert count >= 12


def test_r4_malformed_rows_fail_as_pinned_and_their_repair_is_accepted():
    assert CASES["malformed"]
    for row in CASES["malformed"]:
        failure = row["expect_failure"]
        assert _both(row["request"]) == ("fail", failure, FAILURE_MAPPING[failure]), row["name"]
        repair = row["minimal_repair"]
        declared = repair.get("expect_failure")
        if declared is not None:
            assert _both(repair["request"]) == (
                "fail",
                declared,
                FAILURE_MAPPING[declared],
            ), row["name"]
        else:
            assert _both(repair["request"])[0] == "ok", row["name"]


def test_r4_rollback_rows_reject_as_pinned_and_the_valid_request_still_cancels():
    assert CASES["rollback"]
    for row in CASES["rollback"]:
        failure = row["expect_failure"]
        got = _both(row["rejected_request"])
        assert got == ("fail", failure, FAILURE_MAPPING[failure]), row["name"]
        assert _both(row["request"]) == ("ok", row["expect_record"]), row["name"]


def _variants(request):
    yield dict(request, extra_field=1)
    for key in request:
        yield {k: v for k, v in request.items() if k != key}
        for junk in JUNK:
            yield dict(request, **{key: junk})
        if type(request[key]) is dict:
            nested = request[key]
            for sub in nested:
                yield dict(request, **{key: {k: v for k, v in nested.items() if k != sub}})
                for junk in JUNK:
                    yield dict(request, **{key: dict(nested, **{sub: junk})})
            yield dict(request, **{key: dict(nested, extra_field=1)})


def test_r5_production_and_reference_agree_on_hostile_variants():
    count = 0
    for request in FIXTURE_REQUESTS:
        for variant in _variants(request):
            _both(variant)
            count += 1
    assert count > 3000
    for junk in JUNK + ((), ("op",), object):
        assert _outcome(cancel, CancelError, junk)[0] == "fail"


def test_r5_hostile_subclasses_fail_closed_without_running_caller_code():
    calls = []

    class Key(str):
        def __hash__(self):
            calls.append("hash")
            return 1

        def __eq__(self, other):
            calls.append("eq")
            return False

    class Int(int):
        pass

    class Str(str):
        pass

    base = next(r for r in FIXTURE_REQUESTS if set(r) == {"op", "job", "requester", "now"})
    hostile = [{Key(k): v for k, v in base.items()}]
    for key, value in base.items():
        if type(value) is int:
            hostile.append(dict(base, **{key: Int(value)}))
        elif type(value) is str:
            hostile.append(dict(base, **{key: Str(value)}))
        elif type(value) is dict:
            hostile.append(dict(base, **{key: {Key(k): v for k, v in value.items()}}))
    calls.clear()
    for request in hostile:
        try:
            cancel(request)
        except CancelError:
            pass
        else:
            raise AssertionError("hostile request was accepted")
    assert calls == []


JOB = "job1:" + "a" * 64
MAX_NOW = 2**53 - 1


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


def _request(**kw):
    request = {"op": "cancel", "job": _job(), "requester": "owner-1", "now": 100}
    request.update(kw)
    return request


def _preimage(prefix, value):
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (
        prefix
        + ":"
        + hashlib.sha256(prefix.encode("ascii") + b"\x00" + body.encode("utf-8")).hexdigest()
    )


def test_r7_lowered_int_str_limit_fails_closed():
    # E witness: with the interpreter int->str limit lowered, str() of a
    # legal-but-large payload int raises ValueError; production must fail
    # closed with corrupt_job exactly like the reference, never leak it.
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        request = _request(job=_job(payload={"n": 10**999}))
        try:
            cancel(request)
        except CancelError as error:
            assert error.failure_class == "corrupt_job"
            assert error.__cause__ is None and error.__context__ is None
        else:
            raise AssertionError("lowered-limit payload int was accepted")
        try:
            _reference.cancel(copy.deepcopy(request))
        except _reference.CancelError as error:
            assert error.failure_class == "corrupt_job"
        else:
            raise AssertionError("reference accepted the lowered-limit payload int")
    finally:
        sys.set_int_max_str_digits(limit)


def test_r6_golden_edge_rows():
    record = cancel(_request())
    assert record == {
        "op": "cancel",
        "job_id": JOB,
        "seq": 3,
        "priority": 4,
        "payload_digest": _preimage("pd1", {"k": [1, "x", None]}),
        "status_before": "ready",
        "attempts": 0,
        "requester": "owner-1",
        "cancelled_at": 100,
        "decision_id": _preimage(
            "cd1",
            {
                "op": "cancel",
                "job_id": JOB,
                "seq": 3,
                "priority": 4,
                "payload_digest": _preimage("pd1", {"k": [1, "x", None]}),
                "status_before": "ready",
                "attempts": 0,
                "requester": "owner-1",
                "cancelled_at": 100,
            },
        ),
    }
    leased = cancel(
        _request(
            job=_job(status="leased", attempts=2, lease_owner="w-1", lease_expires_at=150),
            now=120,
        )
    )
    assert leased["status_before"] == "leased" and leased["attempts"] == 2
    assert leased["cancelled_at"] == 120
    for status, attempts in (("done", 1), ("done", 5), ("dead", 5)):
        try:
            cancel(_request(job=_job(status=status, attempts=attempts)))
        except CancelError as error:
            assert error.failure_class == "job_not_cancellable"
            assert error.code == FAILURE_MAPPING["job_not_cancellable"]
        else:
            raise AssertionError(f"{status} job was cancelled")
    # scalar payload roots are valid JSON and cancel; bytes and tuples do not
    for scalar in (True, False, None, 7, "x"):
        assert cancel(_request(job=_job(payload=scalar)))["payload_digest"] == _preimage(
            "pd1", scalar
        )
    for hostile in (b"b", (1,)):
        try:
            cancel(_request(job=_job(payload=hostile)))
        except CancelError as error:
            assert error.failure_class == "corrupt_job"
        else:
            raise AssertionError("hostile payload root was accepted")
    # boundaries: now at the ceiling cancels; past it or negative is malformed
    assert cancel(_request(now=MAX_NOW))["cancelled_at"] == MAX_NOW
    for bad_now in (MAX_NOW + 1, -1, True):
        try:
            cancel(_request(now=bad_now))
        except CancelError as error:
            assert error.failure_class == "malformed_cancel_request"
        else:
            raise AssertionError(f"now={bad_now!r} was accepted")
    # the record carries only the digest: no payload copy, no lease fields
    assert "payload" not in record and "lease_owner" not in record
    assert "lease_expires_at" not in record
