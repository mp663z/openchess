"""T0314: production jobs retry runtime proof (server/jobs_retry.py).

The T0313 red battery is the behavioral proof: it runs against
server.jobs_retry with only its two binding lines switched. This file proves
the switch is exactly those two lines, that production never imports the test
package, that its public surface is the contract's, that production
reproduces every fixture row, that production and the T0311 reference agree
on a grid of valid and hostile requests (same record or same failure class
and code), and pins golden edges as literals.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import itertools
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
# sha256 of tests/test_t0313_retry_red.py as landed before this switch
RED_AS_LANDED_SHA256 = "0604603410b173371997b8ee5208b8ad55a908d7445162675227e689a43412ce"
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "re",
    "pathlib",
    "yaml",
    "tools.retry_contract_lint",
}
JOB = "job1:" + "a" * 64
MAX_NOW = 2**53 - 1


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
    assert jobs_retry.MAX_ATTEMPTS == _reference.MAX_ATTEMPTS
    assert jobs_retry.MAX_NOW == _reference.MAX_NOW


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
    assert prod == ref, request
    return prod


def test_r4_reproduces_every_fixture_row():
    rows = [r for section in ("happy", "boundary") for r in CASES[section]]
    assert rows
    for row in rows:
        for step in row.get("steps", [row]):
            request = step.get("request", step)
            assert _both(request) is not None
    for row in CASES["malformed"]:
        got = _both(row["request"])
        assert got == ("fail", row["expect_failure"], FAILURE_MAPPING[row["expect_failure"]])


def _grid():
    base = {
        "op": "decide",
        "job_id": JOB,
        "attempts": 1,
        "failure": "transient",
        "now": 0,
        "policy": {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 30000},
    }
    for attempts, failure, now in itertools.product(
        (1, 2, 4, 5), ("transient", "timeout", "permanent"), (0, 1, MAX_NOW - 100, MAX_NOW)
    ):
        yield dict(base, attempts=attempts, failure=failure, now=now)
    for policy in (
        {"base_delay_ms": 1, "multiplier": 1, "max_delay_ms": 1},
        {"base_delay_ms": 3_600_000, "multiplier": 10, "max_delay_ms": 86_400_000},
        {"base_delay_ms": 3_600_001, "multiplier": 2, "max_delay_ms": 86_400_000},
        {"base_delay_ms": 5, "multiplier": 11, "max_delay_ms": 9},
        {"base_delay_ms": 5, "multiplier": 2, "max_delay_ms": 4},
        {"base_delay_ms": 5, "multiplier": 2, "max_delay_ms": 86_400_001},
        {"base_delay_ms": 0, "multiplier": 2, "max_delay_ms": 9},
        {"base_delay_ms": True, "multiplier": 2, "max_delay_ms": 9},
        {"base_delay_ms": 5, "multiplier": 2},
        {"base_delay_ms": 5, "multiplier": 2, "max_delay_ms": 9, "jitter": 1},
        [],
        None,
    ):
        yield dict(base, policy=policy)
    for field, value in itertools.product(
        ("op", "job_id", "attempts", "failure", "now"),
        (None, True, 0, -1, 6, 1.0, "x", "decide", [], {}, MAX_NOW + 1, 2**64),
    ):
        yield dict(base, **{field: value})
    for field in base:
        yield {k: v for k, v in base.items() if k != field}
    yield dict(base, extra=1)
    yield from (None, [], "decide", 5, ())


def test_r5_production_and_reference_agree_on_the_grid():
    outcomes = {_both(request)[0] for request in _grid()}
    assert outcomes == {"ok", "fail"}


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

    base = _reference.req()
    hostile = [
        {Key(k): v for k, v in base.items()},
        dict(base, attempts=Int(1)),
        dict(base, failure=Str("transient")),
        dict(base, job_id=Str(JOB)),
        dict(base, policy={Key(k): v for k, v in base["policy"].items()}),
        dict(base, policy=dict(base["policy"], multiplier=Int(2))),
    ]
    calls.clear()
    for request in hostile:
        try:
            decide(request)
        except RetryError as error:
            assert error.failure_class in {"malformed_retry_request", "invalid_retry_policy"}
        else:
            raise AssertionError("hostile request was accepted")
    assert calls == []


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
