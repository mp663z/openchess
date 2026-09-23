"""T0233: production restore runtime proof (store/restore.py).

The T0232 red battery is the behavioral proof: it runs against
store.restore with only its two binding lines switched. This file
proves the switch is exactly those two lines, that production never
imports the test package and links the production backup, that its
record validation agrees with the production WAL's, and that
production reproduces every pinned fixture restore record.
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

from store import backup, restore, wal  # noqa: E402
from store.restore import (  # noqa: E402
    RestoreEngine,
    RestoreError,
    derive_restore_id,
    parse_bundle,
)

RED = ROOT / "tests" / "test_t0232_restore_red.py"
PRODUCTION = ROOT / "store" / "restore.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "restore" / "cases.json").read_text())
ORACLE_LINES = ("RestoreEngine = _reference.RestoreEngine\n"
                "RestoreError = _reference.RestoreError\n")
PRODUCTION_LINES = (
    'RestoreEngine = __import__("store.restore").restore.RestoreEngine\n'
    'RestoreError = __import__("store.restore").restore.RestoreError\n')
# sha256 of tests/test_t0232_restore_red.py as merged (32a2a96)
RED_AS_MERGED_SHA256 = (
    "a049f1be7edbde8c6df9332056841b005506ee47e060db949dfe4c314a7e3d7f")
ALLOWED_IMPORTS = {"__future__", "hashlib", "pathlib", "yaml", "graph.diff",
                   "graph.node", "graph.position_digest", "store",
                   "tools.restore_contract_lint", "tools.variant_runtime"}


def test_r1_red_battery_switch_is_exactly_the_two_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES)
    assert hashlib.sha256(restored.encode()).hexdigest() == \
        RED_AS_MERGED_SHA256


def test_r2_production_imports_only_shipped_runtimes_and_links_backup():
    tree = ast.parse(PRODUCTION.read_text())
    seen, backup_attrs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            seen.add(node.module)
            if node.module == "store":
                assert [a.name for a in node.names] == ["backup"]
        elif isinstance(node, ast.Attribute) and \
                isinstance(node.value, ast.Name) and \
                node.value.id == "_backup":
            backup_attrs.add(node.attr)
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS
    assert backup_attrs <= set(backup.__all__), \
        backup_attrs - set(backup.__all__)
    assert {"BackupEngine", "BackupError", "serialize_bundle"} <= \
        backup_attrs
    assert type(restore._BACKUP) is backup.BackupEngine
    assert restore._BACKUP.serializer is backup.serialize_bundle


def test_r3_public_surface():
    assert set(restore.__all__) == {"FAILURE_MAPPING", "RestoreEngine",
                                    "RestoreError", "derive_restore_id",
                                    "parse_bundle"}
    assert issubclass(RestoreError, Exception)


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_reproduces_every_pinned_record(section):
    for row in CASES[section]:
        receipt = copy.deepcopy(row["receipt"])
        before = copy.deepcopy(receipt)
        result = RestoreEngine(parse_bundle).restore(receipt)
        assert result == row["expect"], row["name"]
        assert list(result["state"]) == list(row["expect"]["state"])
        assert derive_restore_id(result["backup_id"], result["state_id"]) \
            == result["restore_id"]
        assert receipt == before
        assert backup.serialize_bundle(parse_bundle(receipt["bundle"])) \
            == receipt["bundle"]


def _records():
    """Every fixture record plus one mutation per rejection route."""
    base = []
    for section in ("happy", "boundary", "rollback"):
        for row in CASES[section]:
            base.extend(row["expect"]["state"].values())
    assert base
    good = base[0]

    class S(str):
        pass

    out = [dict(r) for r in base]
    out += [
        {**good, "digest": "pdv1:" + "0" * 64},
        {**good, "extra": "x"},
        {k: v for k, v in good.items() if k != "digest"},
        {**good, "variant": 1},
        {**good, "variant": S(good["variant"])},
        {**good, "variant": "not-a-variant"},
        {**good, "snapshot_fen": "garbage"},
        {**good, "snapshot_fen": good["snapshot_fen"] + " " * 300},
        {S(k): v for k, v in good.items()},
        {1: "x", **good},
    ]
    return out


def test_r5_record_validation_agrees_with_the_production_wal():
    for record in _records():
        try:
            expected = wal._validate_record(copy.copy(record))
        except wal.WalError:
            expected = None
        assert restore._record_identity(copy.copy(record)) == expected, \
            record
