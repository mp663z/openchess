"""T0341: production jobs limit runtime proof (server/jobs_limit.py).

The test_t0340_limit_red red battery is the behavioral proof: it runs against
server.jobs_limit with only its two binding lines switched. This file proves
the switch is exactly those two lines, that production never imports the test
package, that its public surface is the contract's, and that production and
the T0338 reference agree (same record, or same failure class and code, and
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

from server import jobs_limit  # noqa: E402
from server.jobs_limit import LimitError, admit  # noqa: E402
from tests import test_t0338_limit_contract as _reference  # noqa: E402
from tests import test_t0340_limit_red as _red  # noqa: E402
from tools.limit_contract_lint import FAILURE_MAPPING  # noqa: E402

RED = ROOT / "tests" / "test_t0340_limit_red.py"
PRODUCTION = ROOT / "server" / "jobs_limit.py"
CASES = json.loads((ROOT / "tests" / "fixtures" / "limit" / "cases.json").read_text())
ORACLE_LINES = "admit = _reference.admit\nLimitError = _reference.LimitError\n"
PRODUCTION_LINES = (
    'admit = __import__("server.jobs_limit").jobs_limit.admit\n'
    'LimitError = __import__("server.jobs_limit").jobs_limit.LimitError\n'
)
# sha256 of the red battery as landed before this switch
RED_AS_LANDED_SHA256 = "c8ac24ef2a92f42f20fb0bc56b25f0a8db948b764252e80752a7aebcb451824d"
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "math",
    "re",
    "pathlib",
    "yaml",
    "tools.limit_contract_lint",
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
    assert _red.admit is admit
    assert _red.LimitError is LimitError


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
    assert set(jobs_limit.__all__) == {"FAILURE_MAPPING", "LimitError", "admit"}
    assert issubclass(LimitError, Exception) and LimitError is not _reference.LimitError
    assert jobs_limit.FAILURE_MAPPING is FAILURE_MAPPING


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
    prod = _outcome(admit, LimitError, copy.deepcopy(request))
    ref = _outcome(_reference.admit, _reference.LimitError, copy.deepcopy(request))
    assert repr(prod) == repr(ref), request
    return prod


def _requests(node, found):
    if isinstance(node, dict):
        if node.get("op") == "admit" and len(node) > 2:
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
        assert _outcome(admit, LimitError, junk)[0] == "fail"


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
            admit(request)
        except LimitError:
            pass
        else:
            raise AssertionError("hostile request was accepted")
    assert calls == []
