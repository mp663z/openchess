"""T0278: production crash resume runtime proof (store/crash_resume.py).

The T0277 red battery is the behavioral proof: it runs against
store.crash_resume with only its two binding lines switched. This file
proves the switch is exactly those two lines, that production never
imports the test package and links the production WAL through its
public surface only, that production reproduces every pinned fixture
outcome, that the admission walk and the quarantine token are identical
to the contract reference, that every totality probe gets the same
outcome from production and reference, and that the surviving prefix is
the linked production WAL's judgment.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import crash_resume, wal  # noqa: E402
from store.crash_resume import (  # noqa: E402
    ResumeEngine,
    ResumeError,
    quarantine_tail,
)
from tests import test_t0275_crash_resume_contract as _reference  # noqa: E402
from tests import test_t0277_crash_resume_red as _red  # noqa: E402
from tests.test_t0276_crash_resume_fixture import (  # noqa: E402
    SINKS,
    _rollback_sink,
)
from tools.crash_resume_contract_lint import (  # noqa: E402
    FAILURE_MAPPING,
    MAX_DEPTH,
)

RED = ROOT / "tests" / "test_t0277_crash_resume_red.py"
PRODUCTION = ROOT / "store" / "crash_resume.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "crash_resume" / "cases.json")
    .read_text())
ORACLE_LINES = ("ResumeEngine = _reference.ResumeEngine\n"
                "ResumeError = _reference.ResumeError\n")
PRODUCTION_LINES = (
    'ResumeEngine = __import__("store.crash_resume")'
    '.crash_resume.ResumeEngine\n'
    'ResumeError = __import__("store.crash_resume")'
    '.crash_resume.ResumeError\n')
# sha256 of tests/test_t0277_crash_resume_red.py as merged (#253)
RED_AS_MERGED_SHA256 = (
    "e28ec1e8ce7ef4b390ea270cadbf2d5a38bb2a63bda895d7c677dc4dc2c689c8")
ALLOWED_IMPORTS = {"__future__", "copy", "hashlib", "json", "math", "re",
                   "pathlib", "yaml", "store",
                   "tools.crash_resume_contract_lint"}


def test_r1_red_battery_switch_is_exactly_the_two_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES)
    assert hashlib.sha256(restored.encode()).hexdigest() == \
        RED_AS_MERGED_SHA256
    assert _red.ResumeEngine is ResumeEngine
    assert _red.ResumeError is ResumeError


def test_r2_production_imports_only_shipped_runtimes_and_links_wal():
    tree = ast.parse(PRODUCTION.read_text())
    seen, wal_attrs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            seen.add(node.module)
            if node.module == "store":
                assert [a.name for a in node.names] == ["wal"]
        elif isinstance(node, ast.Attribute) and \
                isinstance(node.value, ast.Name) and node.value.id == "_wal":
            wal_attrs.add(node.attr)
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS
    assert wal_attrs <= set(wal.__all__), wal_attrs - set(wal.__all__)
    assert {"WalEngine", "WalError", "canonical_payload"} <= wal_attrs
    engine = ResumeEngine(quarantine_tail)
    assert type(engine._wal) is wal.WalEngine
    assert engine._wal.canonicalizer is wal.canonical_payload


def test_r3_public_surface():
    assert set(crash_resume.__all__) == {
        "FAILURE_MAPPING", "ResumeEngine", "ResumeError", "quarantine_tail"}
    assert issubclass(ResumeError, Exception)
    assert crash_resume.FAILURE_MAPPING is FAILURE_MAPPING
    assert crash_resume.MAX_DEPTH == MAX_DEPTH


def _resume(sink, log, request):
    fn = SINKS[sink] if isinstance(sink, str) else sink
    return ResumeEngine(fn).resume(log, request)


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_reproduces_every_pinned_outcome(section):
    for row in CASES[section]:
        log = copy.deepcopy(row["log"])
        request = copy.deepcopy(row["request"])
        kept = list(log[:row["expect"]["resumed_count"]])
        receipt = _resume(row["sink"], log, request)
        assert receipt == row["expect"], row["name"]
        assert list(receipt) == list(_red.RECEIPT_FIELDS)
        assert log == row["expect_log"], row["name"]
        assert request == row["request"]
        assert all(a is b for a, b in zip(log, kept, strict=True))


def test_r4_malformed_rows_and_their_repairs():
    for row in CASES["malformed"]:
        log = copy.deepcopy(row["log"])
        request = copy.deepcopy(row["request"])
        with pytest.raises(ResumeError) as exc:
            _resume(row["sink"], log, request)
        assert exc.value.failure_class == row["expect_failure"], row["name"]
        assert exc.value.code == FAILURE_MAPPING[row["expect_failure"]]
        assert (log, request) == (row["log"], row["request"])
        fixed = _red._repaired(row)
        assert _resume(fixed["sink"], copy.deepcopy(fixed["log"]),
                       copy.deepcopy(fixed["request"])) == \
            _reference.ResumeEngine(SINKS[fixed["sink"]]).resume(
                copy.deepcopy(fixed["log"]), copy.deepcopy(fixed["request"]))


def test_r4_rollback_rows_restore_by_value_and_reference():
    for row in CASES["rollback"]:
        log = copy.deepcopy(row["log"])
        request = copy.deepcopy(row["request"])
        entries = list(log)
        sink = _rollback_sink(row["rejected_sink"], log, request)
        with pytest.raises(ResumeError) as exc:
            ResumeEngine(sink).resume(log, request)
        assert exc.value.failure_class == row["expect_failure"], row["name"]
        assert (log, request) == (row["log"], row["request"])
        assert all(a is b for a, b in zip(log, entries, strict=True))
        assert ResumeEngine(quarantine_tail).resume(log, request) == \
            row["expect"]
        assert log == row["expect_log"]


def _leaf_values():
    nested = []
    node = nested
    for _ in range(MAX_DEPTH - 1):
        node.append([])
        node = node[0]
    shared = [1]
    return [None, True, False, 0, -1, 10 ** 3999, 10 ** 4000, 2 ** 20000,
            0.5, float("inf"), float("nan"), "", "\u00e9", "\U0001d11e",
            "\ud800", b"x", (1,), {1: 2}, {"\ud800": 1}, {"a": [1, {"b": None}]},
            nested, [nested], [shared, shared], {"a": shared, "b": shared},
            _reference._StrSub("s"), {_reference._StrSub("k"): 1}]


def test_r5_admission_and_token_match_the_reference():
    for value in _leaf_values():
        assert crash_resume._is_canonical_json(value) is \
            _reference._is_canonical_json(value), repr(value)[:60]
    for row in CASES["happy"] + CASES["boundary"] + CASES["rollback"]:
        for tail in (row["log"], row["log"][1:], []):
            assert quarantine_tail(tail) == _reference.quarantine_tail(tail)
    tail = [{"z": "\u00e9", "a": [1, None, True, 0.25]}, "\U0001d11e", -7]
    assert quarantine_tail(tail) == _reference.quarantine_tail(tail)


def _outcome(cls, name):
    """(receipt or failure class, log after, request after) for probe
    NAME through CLS; builds fresh inputs so stateful sinks start
    identically."""
    build, _ = _red.PROBES[name]
    log, request, sink = build()
    fn = SINKS[sink] if isinstance(sink, str) else sink
    try:
        result = cls(fn).resume(log, request)
    except (_reference.ResumeError, ResumeError) as error:
        result = ("rejected", error.failure_class, error.code)
    return result, _state(log), _state(request)


def _state(value):
    """Type-exact text of VALUE, or the error its hostile contents
    raise when serialized (the battery pins restoration of those)."""
    try:
        return _red._typed(value)
    except BaseException as error:  # noqa: BLE001 - hostile containers
        return ("unserializable", type(value).__name__,
                type(error).__name__)


def test_r5_every_totality_probe_matches_the_reference():
    for name in _red.PROBE_MANIFEST:
        assert _outcome(ResumeEngine, name) == \
            _outcome(_reference.ResumeEngine, name), name


def test_r6_surviving_prefix_is_the_linked_wal_judgment():
    """The production prefix equals a LINEAR search with the production
    WAL's replay over the admissible prefix, on every fixture log and
    every wal-* / tail-* probe log."""
    engine = ResumeEngine(quarantine_tail)
    logs = [row["log"] for s in ("happy", "boundary", "rollback")
            for row in CASES[s]]
    logs += [_red.PROBES[name][0]()[0] for name in _red.PROBE_MANIFEST
             if name.startswith(("wal-", "tail-"))]
    checked = 0
    for log in logs:
        if type(log) is not list:
            continue
        cap = next((i for i, e in enumerate(log)
                    if not _reference._is_canonical_json(e)), len(log))
        count = 0
        for k in range(1, cap + 1):
            try:
                wal.WalEngine(wal.canonical_payload).replay(
                    copy.deepcopy(log[:k]))
            except wal.WalError:
                break
            count = k
        assert crash_resume._valid_prefix_length(engine._wal, log) == count
        checked += 1
    assert checked >= len(CASES["happy"]) + len(CASES["boundary"])
