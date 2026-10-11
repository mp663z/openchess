"""T0314: production jobs retry runtime proof (server/jobs_retry.py).

The test_t0313_retry_red red battery is the behavioral proof: it runs against
server.jobs_retry with only its two binding lines switched. This file proves
the switch is exactly those two lines, that production never imports the test
package, that its public surface is the contract's, and that production and
the T0311 reference agree (same record, or same failure class and code, and
the request unchanged) on every fixture request and on a grid of hostile
variants of each one: every field replaced by junk, dropped, or given an
extra sibling, and every nested mapping treated the same way.
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

from server import jobs_retry  # noqa: E402
from server.jobs_retry import RetryError, decide  # noqa: E402
from tests import test_t0311_retry_contract as _reference  # noqa: E402
from tests import test_t0313_retry_red as _red  # noqa: E402
from tools.retry_contract_lint import FAILURE_MAPPING  # noqa: E402

RED = ROOT / "tests" / "test_t0313_retry_red.py"
PRODUCTION = ROOT / "server" / "jobs_retry.py"
CASES = json.loads((ROOT / "tests" / "fixtures" / "retry" / "cases.json").read_text())
ORACLE_LINES = "decide = _reference.decide\nRetryError = _reference.RetryError\n"
PRODUCTION_LINES = (
    'decide = __import__("server.jobs_retry").jobs_retry.decide\n'
    'RetryError = __import__("server.jobs_retry").jobs_retry.RetryError\n'
)
# sha256 of the red battery as landed before this switch
RED_AS_LANDED_SHA256 = "0604603410b173371997b8ee5208b8ad55a908d7445162675227e689a43412ce"
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "math",
    "re",
    "pathlib",
    "yaml",
    "tools.retry_contract_lint",
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
    assert _red.decide is decide
    assert _red.RetryError is RetryError


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
    assert set(jobs_retry.__all__) == {"FAILURE_MAPPING", "RetryError", "decide"}
    assert issubclass(RetryError, Exception) and RetryError is not _reference.RetryError
    assert jobs_retry.FAILURE_MAPPING is FAILURE_MAPPING


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
    prod = _outcome(decide, RetryError, copy.deepcopy(request))
    ref = _outcome(_reference.decide, _reference.RetryError, copy.deepcopy(request))
    assert repr(prod) == repr(ref), request
    return prod


def _requests(node, found):
    if isinstance(node, dict):
        if node.get("op") == "decide" and len(node) > 2:
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
                assert decide(copy.deepcopy(request)) == record
                count += 1
    assert count >= 12


def test_r4_malformed_rows_fail_as_pinned_and_their_repair_is_accepted():
    assert CASES["malformed"]
    for row in CASES["malformed"]:
        failure = row["expect_failure"]
        assert _both(row["request"]) == ("fail", failure, FAILURE_MAPPING[failure]), row["name"]
        assert _both(row["minimal_repair"]["request"])[0] == "ok", row["name"]


def test_r4_rollback_rows_reject_as_pinned_and_the_valid_request_still_decides():
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
        assert _outcome(decide, RetryError, junk)[0] == "fail"


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

    base = FIXTURE_REQUESTS[0]
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
            decide(request)
        except RetryError:
            pass
        else:
            raise AssertionError("hostile request was accepted")
    assert calls == []


JOB = "job1:" + "a" * 64
MAX_NOW = 2**53 - 1


def test_r6_golden_edge_rows():
    policy = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 30000}

    def run(**kw):
        request = {
            "op": "decide",
            "job_id": JOB,
            "attempts": 1,
            "failure": "transient",
            "now": 0,
            "policy": dict(policy),
        }
        request.update(kw)
        return decide(request)

    assert [run(attempts=a)["delay_ms"] for a in (1, 2, 3, 4)] == [1000, 2000, 4000, 8000]
    assert run(attempts=5)["decision"] == "dead" and run(attempts=5)["retry_at"] is None
    big = {"base_delay_ms": 3_600_000, "multiplier": 10, "max_delay_ms": 86_400_000}
    assert run(attempts=3, policy=big)["delay_ms"] == 86_400_000
    assert run(now=MAX_NOW - 1000)["retry_at"] == MAX_NOW
    try:
        run(now=MAX_NOW - 999)
    except RetryError as error:
        assert error.failure_class == "clock_overflow"
        assert error.code == FAILURE_MAPPING["clock_overflow"]
    else:
        raise AssertionError("clock overflow was not refused")
    assert run(failure="permanent", now=MAX_NOW)["decision"] == "dead"
    assert run(failure="timeout")["delay_ms"] == run()["delay_ms"]
    dead = run(attempts=5)
    body = json.dumps(
        {
            "request": {
                "op": "decide",
                "job_id": JOB,
                "attempts": 5,
                "failure": "transient",
                "now": 0,
                "policy": policy,
            },
            "record": {k: v for k, v in dead.items() if k != "decision_id"},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert dead["decision_id"] == "rd1:" + hashlib.sha256(b"rd1\x00" + body.encode()).hexdigest()
