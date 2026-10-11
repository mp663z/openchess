"""T0350: production jobs restart runtime proof (server/jobs_restart.py).

The test_t0349_restart_red red battery is the behavioral proof: it runs against
server.jobs_restart with only its two binding lines switched. This file proves
the switch is exactly those two lines, that production never imports the test
package, that its public surface is the contract's, and that production and
the T0347 reference agree (same record, or same failure class and code, and
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

from server import jobs_restart  # noqa: E402
from server.jobs_restart import RestartError, restart  # noqa: E402
from tests import test_t0347_restart_contract as _reference  # noqa: E402
from tests import test_t0349_restart_red as _red  # noqa: E402
from tools.restart_contract_lint import FAILURE_MAPPING  # noqa: E402

RED = ROOT / "tests" / "test_t0349_restart_red.py"
PRODUCTION = ROOT / "server" / "jobs_restart.py"
CASES = json.loads((ROOT / "tests" / "fixtures" / "restart" / "cases.json").read_text())
ORACLE_LINES = "restart = _reference.restart\nRestartError = _reference.RestartError\n"
PRODUCTION_LINES = (
    'restart = __import__("server.jobs_restart").jobs_restart.restart\n'
    'RestartError = __import__("server.jobs_restart").jobs_restart.RestartError\n'
)
# sha256 of the red battery as landed before this switch
RED_AS_LANDED_SHA256 = "c903ab42754c536d1aa032f41e0adcec637e770f33cbe75484cd2f31921201cb"
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "math",
    "re",
    "pathlib",
    "yaml",
    "tools.restart_contract_lint",
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
    assert _red.restart is restart
    assert _red.RestartError is RestartError


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
    assert set(jobs_restart.__all__) == {"FAILURE_MAPPING", "RestartError", "restart"}
    assert issubclass(RestartError, Exception) and RestartError is not _reference.RestartError
    assert jobs_restart.FAILURE_MAPPING is FAILURE_MAPPING


def _snapshot(obj):
    return json.dumps(obj, sort_keys=True, default=repr)


def _outcome(fn, error_cls, state, request):
    before = (_snapshot(state), _snapshot(request))
    try:
        out = ("ok", fn(state, request), _snapshot(state))
    except error_cls as error:
        assert type(error) is error_cls
        assert error.__cause__ is None and error.__context__ is None
        out = ("fail", error.failure_class, error.code)
        assert _snapshot(state) == before[0]
    assert _snapshot(request) == before[1]
    return out


def _both(state, request):
    prod = _outcome(restart, RestartError, copy.deepcopy(state), copy.deepcopy(request))
    ref = _outcome(
        _reference.restart, _reference.RestartError, copy.deepcopy(state), copy.deepcopy(request)
    )
    assert repr(prod) == repr(ref), (state, request)
    return prod


def _pairs():
    pairs = []
    for section in ("happy", "boundary", "malformed", "rollback"):
        for row in CASES[section]:
            state = row["state"]
            requests = list(row.get("requests", []))
            for key in ("request", "rejected", "follow_up"):
                if key in row:
                    requests.append(row[key])
            requests += [
                v for k, v in row.items() if k.endswith("_request") and isinstance(v, dict)
            ]
            for request in requests:
                if isinstance(request, dict) and request.get("op") == "restart":
                    pairs.append((state, request))
    return pairs


PAIRS = _pairs()


def test_r4_every_fixture_pair_agrees_with_the_reference():
    assert len(PAIRS) > 40
    kinds = {_both(state, request)[0] for state, request in PAIRS}
    assert kinds == {"ok", "fail"}


def _mapping_variants(mapping):
    if type(mapping) is not dict:
        return
    yield dict(mapping, extra_field=1)
    for key in mapping:
        yield {k: v for k, v in mapping.items() if k != key}
        for junk in JUNK:
            yield dict(mapping, **{key: junk})


def _state_variants(state):
    yield from _mapping_variants(state)
    if type(state) is not dict or type(state.get("jobs")) is not list:
        return
    for index, job in enumerate(state["jobs"][:4]):
        for variant in _mapping_variants(job):
            jobs = list(state["jobs"])
            jobs[index] = variant
            yield dict(state, jobs=jobs)
    if state.get("jobs"):
        yield dict(state, jobs=state["jobs"] + [state["jobs"][0]])
        yield dict(state, jobs=list(reversed(state["jobs"])))
        yield dict(state, jobs=[None] + state["jobs"])


def test_r5_production_and_reference_agree_on_hostile_requests_and_states():
    count = 0
    for state, request in PAIRS:
        for variant in _mapping_variants(request):
            _both(state, variant)
            count += 1
        for variant in _state_variants(state):
            _both(variant, request)
            count += 1
    assert count > 3000
    for junk in JUNK + ((), ("op",), object):
        assert _outcome(restart, RestartError, {"jobs": [], "next_seq": 0}, junk)[0] == "fail"
        assert _outcome(restart, RestartError, junk, PAIRS[0][1])[0] == "fail"


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

    state, base = PAIRS[0]
    hostile = [(state, {Key(k): v for k, v in base.items()})]
    for key, value in base.items():
        if type(value) is int:
            hostile.append((state, dict(base, **{key: Int(value)})))
        elif type(value) is str:
            hostile.append((state, dict(base, **{key: Str(value)})))
    hostile.append(({Key(k): v for k, v in state.items()}, base))
    job = copy.deepcopy(state["jobs"][0])
    hostile.append((dict(state, jobs=[{Key(k): v for k, v in job.items()}]), base))
    hostile.append((dict(state, jobs=[dict(job, seq=Int(job["seq"]))]), base))
    hostile.append((dict(state, jobs=[dict(job, status=Str(job["status"]))]), base))
    calls.clear()
    for hostile_state, request in hostile:
        try:
            restart(hostile_state, request)
        except RestartError:
            pass
        else:
            raise AssertionError("hostile input was accepted")
    assert calls == []
