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
# sha256 of tests/test_t0286_queue_red.py as amended at c10a450
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


# -- golden edge rows: exact outcomes pinned as literals, never recomputed ----

_MAX = 9007199254740991  # 2**53 - 1, written out
_JA = "job1:c320a4de55bab79ab85ec24089658fef29dea4beb512432f69b3b5c8c980274f"  # job_id_for("a")


def _empty():
    return {"jobs": [], "next_seq": 0}


def _enqueue(payload):
    return {"op": "enqueue", "dedupe_key": "a", "priority": 5, "payload": payload}


def _nest_dicts(depth):
    node = {"k": 0}
    for _ in range(depth - 1):
        node = {"k": node}
    return node


def _leased_at_limit():
    """One job leased by w1 until 2**53-1 (the clock limit)."""
    job = {
        "job_id": _JA,
        "seq": 0,
        "priority": 5,
        "payload": {"k": 1},
        "status": "leased",
        "attempts": 1,
        "lease_owner": "w1",
        "lease_expires_at": _MAX,
    }
    return {"jobs": [job], "next_seq": 1}


class _FloatSub(float):
    pass


def _with_job(**changes):
    def build():
        state = _leased_at_limit()
        state["jobs"][0].update(changes)
        return state

    return build


def _ack(now):
    return {"op": "ack", "job_id": _JA, "worker": "w1", "now": now}


_MQ, _LC = "malformed_queue_request", "lease_conflict"
# name -> (build state, build request, ("ok", status, state_id) | ("fail", class))
GOLDEN = {
    "payload-negative-4000-digits": (
        _empty,
        lambda: _enqueue({"n": -(10**4000 - 1)}),
        ("ok", "ready", "qs1:2164871ba2dc89cb0337228b73bcaf02485b05729bd6d968c7389f0d69276535"),
    ),
    "payload-positive-4000-digits": (
        _empty,
        lambda: _enqueue({"n": 10**4000 - 1}),
        ("ok", "ready", "qs1:cd38d0a1b6802479eeee2fff935cb6ddcc14f7a074754b0da16195af5c523401"),
    ),
    "payload-negative-10-pow-3999": (
        _empty,
        lambda: _enqueue({"n": -(10**3999)}),
        ("ok", "ready", "qs1:551e09e6494846f96b2eb1cfeb0b6cd4dfb4245a7a65cfcef3d5c72eee33e6ac"),
    ),
    "payload-float-subclass-request": (
        _empty,
        lambda: _enqueue({"x": _FloatSub(1.5)}),
        ("fail", _MQ),
    ),
    "payload-float-subclass-state": (
        _with_job(payload={"x": _FloatSub(1.5)}),
        lambda: _ack(_MAX - 1),
        ("fail", "corrupt_queue"),
    ),
    "lease-expiry-past-clock-limit": (
        _with_job(lease_expires_at=_MAX + 1),
        lambda: _ack(_MAX - 1),
        ("fail", "corrupt_queue"),
    ),
    "payload-negative-4001-digits": (_empty, lambda: _enqueue({"n": -(10**4000)}), ("fail", _MQ)),
    "payload-64-nested-dicts": (
        _empty,
        lambda: _enqueue(_nest_dicts(64)),
        ("ok", "ready", "qs1:6c36e01c62a46610a5872593d77dac3394305dc920998c4d0787a45366030354"),
    ),
    "payload-65-nested-dicts": (_empty, lambda: _enqueue(_nest_dicts(65)), ("fail", _MQ)),
    "payload-value-lone-surrogate": (_empty, lambda: _enqueue({"s": "\ud800"}), ("fail", _MQ)),
    "payload-key-lone-surrogate": (_empty, lambda: _enqueue({"\ud800": 1}), ("fail", _MQ)),
    "payload-non-ascii-state-id": (
        _empty,
        lambda: _enqueue({"x": "\u00e9"}),
        ("ok", "ready", "qs1:adc3a504209fe0aa326dd746267526c90fdff62564acb1ec79955faf2ee12a7c"),
    ),
    "ack-at-clock-limit-is-lease-conflict": (_leased_at_limit, lambda: _ack(_MAX), ("fail", _LC)),
    "ack-one-before-limit-expiry": (
        _leased_at_limit,
        lambda: _ack(_MAX - 1),
        ("ok", "done", "qs1:689b8eaa63cd646df1d9df6f5f80ef2808e32ccbe5334f4016d7cbbb8431a1ba"),
    ),
}


def _golden_problem(module, name):
    """None when MODULE's engine gives exactly the pinned outcome."""
    build_state, build_request, expect = GOLDEN[name]
    state, request = build_state(), build_request()
    request_shape = _shape(request)
    try:
        receipt = module.QueueEngine().apply(state, request)
    except module.QueueError as exc:
        if expect[0] != "fail" or type(exc) is not module.QueueError:
            return f"rejected {exc.failure_class}"
        if exc.failure_class != expect[1] or exc.code != FAILURE_MAPPING[expect[1]]:
            return f"class {exc.failure_class}"
        if exc.__cause__ is not None or exc.__context__ is not None:
            return "typed error chained"
        if _shape(state) != _shape(build_state()) or _shape(request) != request_shape:
            return "rejected apply changed state or request"
        return None
    except BaseException as exc:  # noqa: BLE001 - a raw escape is a defect
        return f"raw {type(exc).__name__}"
    if expect[0] != "ok":
        return "accepted"
    if (receipt["status"], receipt["state_id"]) != expect[1:]:
        return f"receipt {receipt['status']} {receipt['state_id']}"
    if (
        receipt["state_id"]
        != "qs1:"
        + hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    ):
        return "committed state does not hash to the receipt's state_id"
    return None


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_r6_golden_edge_rows(name):
    assert _golden_problem(jobs_queue, name) is None


# -- production mutants beyond the T0286 table: each must turn a golden row red

PRODUCTION_EDITS = {
    "digits-signed": (
        "len(str(abs(node))) <= MAX_INT_DIGITS",
        "len(str(node)) <= MAX_INT_DIGITS",
    ),
    "dict-depth-flat": (
        "                pending.append((value, depth + 1))",
        "                pending.append((value, depth))",
    ),
    "key-scalar-unchecked": (
        "if type(key) is not str or not _scalar(key):",
        "if type(key) is not str:",
    ),
    "now-ceiling-short": (
        "_int(now, 0, MAX_NOW))",
        "_int(now, 0, MAX_NOW - 1))",
    ),
    "canon-ascii": ("ensure_ascii=False)", "ensure_ascii=True)"),
    "utf8-surrogatepass": ('node.encode("utf-8")', 'node.encode("utf-8", "surrogatepass")'),
    "float-isinstance": ("if kind is float:", "if isinstance(node, float):"),
    "lease-expiry-ceiling-plus": (
        "_int(expires, 1, MAX_NOW)",
        "_int(expires, 1, MAX_NOW + 1)",
    ),
}


def _mutant_module(name):
    import types

    old, new = PRODUCTION_EDITS[name]
    source = PRODUCTION.read_text()
    assert source.count(old) == 1, name
    module = types.ModuleType(f"server.jobs_queue_mutant_{name}")
    module.__file__ = str(PRODUCTION)
    exec(compile(source.replace(old, new), f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    return module


def _red_rows(module):
    return [name for name in GOLDEN if _golden_problem(module, name) is not None]


def test_r6_production_is_green_on_every_golden_row():
    assert _red_rows(jobs_queue) == []


@pytest.mark.parametrize("name", sorted(PRODUCTION_EDITS))
def test_r6_production_mutants_turn_a_golden_row_red(name):
    assert _red_rows(_mutant_module(name)) != [], name


def test_r6_production_edits_are_new():
    assert not set(PRODUCTION_EDITS) & set(_red.MUTANTS)
