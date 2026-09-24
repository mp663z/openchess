"""T0287: production jobs queue runtime proof (server/jobs_queue.py).

The T0286 red battery is the behavioral proof: it runs against
server.jobs_queue with only its two binding lines switched. This file
proves the switch is exactly those two lines, that production never
imports the test package, that its public surface is the contract's,
that production reproduces every fixture row, and that every totality
probe (hostile types at every request, payload, state and job boundary)
gets the same receipt or typed failure and the same post-state from
production and from the T0284 reference.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import jobs_queue  # noqa: E402
from server.jobs_queue import QueueEngine, QueueError  # noqa: E402
from tests import test_t0284_queue_contract as _reference  # noqa: E402
from tests import test_t0286_queue_red as _red  # noqa: E402
from tools.queue_contract_lint import FAILURE_MAPPING, MAX_DEPTH, MAX_INT_DIGITS  # noqa: E402

RED = ROOT / "tests" / "test_t0286_queue_red.py"
PRODUCTION = ROOT / "server" / "jobs_queue.py"
CASES = json.loads((ROOT / "tests" / "fixtures" / "queue" / "cases.json").read_text())
ORACLE_LINES = "QueueEngine = _reference.QueueEngine\nQueueError = _reference.QueueError\n"
PRODUCTION_LINES = (
    'QueueEngine = __import__("server.jobs_queue").jobs_queue.QueueEngine\n'
    'QueueError = __import__("server.jobs_queue").jobs_queue.QueueError\n'
)
# sha256 of tests/test_t0286_queue_red.py as amended at 30f371e
RED_AS_MERGED_SHA256 = "5581e0af4e43fe773ba4ac470c83ba5344adb04926372a142b39cfc694b92d20"
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "math",
    "re",
    "pathlib",
    "yaml",
    "tools.queue_contract_lint",
}


def test_r1_red_battery_switch_is_exactly_the_two_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES)
    assert hashlib.sha256(restored.encode()).hexdigest() == RED_AS_MERGED_SHA256
    assert _red.QueueEngine is QueueEngine
    assert _red.QueueError is QueueError


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
    assert set(jobs_queue.__all__) == {
        "FAILURE_MAPPING",
        "QueueEngine",
        "QueueError",
        "job_id_for",
        "state_id_for",
    }
    assert issubclass(QueueError, Exception) and QueueError is not _reference.QueueError
    assert jobs_queue.FAILURE_MAPPING is FAILURE_MAPPING
    assert (jobs_queue.MAX_DEPTH, jobs_queue.MAX_INT_DIGITS) == (MAX_DEPTH, MAX_INT_DIGITS)
    assert (jobs_queue.MAX_JOBS, jobs_queue.MAX_ATTEMPTS) == (
        _reference.MAX_JOBS,
        _reference.MAX_ATTEMPTS,
    )
    assert (QueueEngine.SEQ_LIMIT, QueueEngine.CLOCK_LIMIT) == (
        _reference.QueueEngine.SEQ_LIMIT,
        _reference.QueueEngine.CLOCK_LIMIT,
    )
    for key in ("a", "job-1", "x" * 64, "\u00e9"):
        assert jobs_queue.job_id_for(key) == _reference.job_id_for(key)
    for state in ({"jobs": [], "next_seq": 0}, {"jobs": [], "next_seq": 7}):
        assert jobs_queue.state_id_for(state) == _reference.state_id_for(state)


@pytest.mark.parametrize("section", ["happy", "boundary", "malformed", "rollback"])
def test_r4_reproduces_every_fixture_row(section):
    assert CASES[section]
    for row in CASES[section]:
        _red.RUNNERS[section](QueueEngine, row)


def _shape(obj, seen=None):
    """Copy-invariant, type-exact shape: containers are numbered in visit
    order and one met again is a reference to its number, so two builds
    of one probe compare equal and a re-wired alias differs."""
    seen = {} if seen is None else seen
    if type(obj) in (dict, list) or isinstance(obj, (dict, list)):
        if id(obj) in seen:
            return ("ref", seen[id(obj)])
        seen[id(obj)] = len(seen)
        if isinstance(obj, dict):
            items = [(_shape(k, seen), _shape(v, seen)) for k, v in dict.items(obj)]
        else:
            items = [_shape(v, seen) for v in list.__iter__(obj)]
        return (type(obj).__qualname__, items)
    if isinstance(obj, str):
        return (type(obj).__qualname__, str.__str__(obj))
    if isinstance(obj, float) and obj != obj:
        return (type(obj).__qualname__, "nan")
    if isinstance(obj, (int, float)) or obj is None:
        return (type(obj).__qualname__, obj)
    return (type(obj).__qualname__, "opaque")


def _outcome(engine_cls, error_cls, build):
    state, request = build()
    try:
        receipt = engine_cls().apply(state, request)
    except error_cls as exc:
        assert type(exc) is error_cls
        assert exc.__cause__ is None and exc.__context__ is None
        return ("fail", exc.failure_class, exc.code), _shape(state), _shape(request)
    return ("ok", receipt), _shape(state), _shape(request)


@pytest.mark.parametrize("name", sorted(_red.PROBES))
def test_r5_every_totality_probe_matches_the_reference(name):
    build, expect = _red.PROBES[name]
    got = _outcome(QueueEngine, QueueError, build)
    want = _outcome(_reference.QueueEngine, _reference.QueueError, build)
    assert got == want, name
    if expect == _red.ACCEPT:
        assert got[0][0] == "ok", name
    else:
        assert got[0][:2] == ("fail", expect), name
