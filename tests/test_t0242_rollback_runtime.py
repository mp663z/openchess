"""T0242: production rollback runtime proof (store/rollback.py).

The T0241 red battery is the behavioral proof: it runs against
store.rollback with only its two binding lines switched. This file
proves the switch is exactly those two lines, that production never
imports the test package and links the production WAL through its
public snapshot/restore helpers, and that production reproduces every
pinned fixture receipt with the honest archiver.
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

from store import rollback, wal  # noqa: E402
from store.rollback import (  # noqa: E402
    GENESIS,
    RollbackEngine,
    RollbackError,
    archive_tail,
    derive_rollback_id,
)

RED = ROOT / "tests" / "test_t0241_rollback_red.py"
PRODUCTION = ROOT / "store" / "rollback.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "rollback" / "cases.json").read_text())
ORACLE_LINES = ("RollbackEngine = _reference.RollbackEngine\n"
                "RollbackError = _reference.RollbackError\n")
PRODUCTION_LINES = (
    'RollbackEngine = __import__("store.rollback").rollback.RollbackEngine\n'
    'RollbackError = __import__("store.rollback").rollback.RollbackError\n')
# sha256 of tests/test_t0241_rollback_red.py as merged (20afa72)
RED_AS_MERGED_SHA256 = (
    "b3b3f5e6b39cf6cc9a1cbee358aff1e39ccc59df7cca5e66160f8d538e6f4517")
ALLOWED_IMPORTS = {"__future__", "copy", "hashlib", "re", "pathlib", "yaml",
                   "store", "tools.rollback_contract_lint"}


def test_r1_red_battery_switch_is_exactly_the_two_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES)
    assert hashlib.sha256(restored.encode()).hexdigest() == \
        RED_AS_MERGED_SHA256


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
    # only the WAL's public surface is used
    assert wal_attrs <= set(wal.__all__), wal_attrs - set(wal.__all__)
    assert {"snapshot", "restore", "WalEngine"} <= wal_attrs
    assert type(RollbackEngine(archive_tail)._wal) is wal.WalEngine
    assert GENESIS == wal.GENESIS


def test_r3_public_surface():
    assert set(rollback.__all__) == {"FAILURE_MAPPING", "GENESIS",
                                     "RollbackEngine", "RollbackError",
                                     "archive_tail", "derive_rollback_id"}
    assert issubclass(RollbackError, Exception)


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_reproduces_every_pinned_receipt(section):
    for row in CASES[section]:
        log = copy.deepcopy(row["log"])
        prefix = log[:row["request"]["target_sequence"]]
        receipt = RollbackEngine(archive_tail).rollback(
            log, copy.deepcopy(row["request"]))
        assert receipt == row["expect"], row["name"]
        assert derive_rollback_id(receipt["from_head"], receipt["to_head"],
                                  receipt["truncated_count"],
                                  receipt["archive_token"]) == \
            receipt["rollback_id"]
        assert len(log) == len(prefix) and \
            all(a is b for a, b in zip(log, prefix, strict=True))


def test_r5_corrupt_source_surfaces_from_the_linked_wal():
    row = next(r for r in CASES["happy"] if len(r["log"]) >= 2)
    log = copy.deepcopy(row["log"])
    log[1]["sequence"] = 3
    with pytest.raises(wal.WalError):
        wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    before = copy.deepcopy(log)
    calls = []
    with pytest.raises(RollbackError) as exc:
        RollbackEngine(lambda tail: calls.append(tail) or "x").rollback(
            log, {"target_sequence": 1})
    assert exc.value.failure_class == "corrupt_source"
    assert log == before and calls == []
