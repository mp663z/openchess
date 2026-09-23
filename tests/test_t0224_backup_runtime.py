"""T0224: production backup runtime proof (store/backup.py).

The T0223 red battery is the behavioral proof: it runs against
store.backup with only its production-bindings block switched. This file
proves the switch touches only that block, that production never imports
the test package and links the production WAL, and that the public
derive_backup_id reproduces every pinned fixture receipt.
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

from store import backup, wal  # noqa: E402
from store.backup import (  # noqa: E402
    GENESIS,
    BackupEngine,
    BackupError,
    derive_backup_id,
    serialize_bundle,
)

RED = ROOT / "tests" / "test_t0223_backup_red.py"
PRODUCTION = ROOT / "store" / "backup.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "backup" / "cases.json").read_text())
BLOCK_START = "# -- production bindings (the implementation task swaps ONLY these) --------\n"
BLOCK_END = "# ---------------------------------------------------------------------------\n"
ORACLE_BLOCK = '''from tests import test_t0221_backup_contract as _ref  # noqa: E402

BackupEngine = _ref.BackupEngine
BackupError = _ref.BackupError
serialize_bundle = _ref.serialize_bundle
FAILURE_MAPPING = _ref.FAILURE_MAPPING
GENESIS = _ref.GENESIS
EMPTY_STATE_ID = _ref.state_id({})


def derive_backup_id(head, state_id, entry_count, bundle):
    """The bound canonical backup-id derivation, used only to build
    self-consistent forgeries. The implementation task must bind an
    equivalent that REPRODUCES THE PINNED RECEIPTS' backup ids (enforced
    by test_forgery_derivation_is_faithful) - not merely whatever
    derivation production happens to expose."""
    return BackupEngine._derive_backup_id(
        head, state_id, entry_count, bundle, "malformed_backup_record")
'''
# sha256 of tests/test_t0223_backup_red.py as merged in #217 (bf9062e)
RED_AS_MERGED_SHA256 = (
    "cf3ad614fe88be82f2a9fb839c230617e53de06de2dbf1f221e4714f717b9121")
ALLOWED_IMPORTS = {"__future__", "hashlib", "re", "pathlib", "yaml",
                   "graph.diff", "store", "tools.backup_contract_lint"}


def _block(source):
    start = source.index(BLOCK_START) + len(BLOCK_START)
    return start, source.index(BLOCK_END, start)


def test_r1_red_battery_switch_is_only_the_bindings_block():
    source = RED.read_text()
    assert source.count(BLOCK_START) == 1
    start, end = _block(source)
    block = source[start:end]
    assert "from store import backup as _ref" in block
    assert "tests" not in block.split("\n", 1)[0]
    restored = source[:start] + ORACLE_BLOCK + source[end:]
    assert hashlib.sha256(restored.encode()).hexdigest() == \
        RED_AS_MERGED_SHA256


def test_r2_production_imports_only_shipped_runtimes_and_links_wal():
    tree = ast.parse(PRODUCTION.read_text())
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            seen.add(node.module)
            if node.module == "store":
                assert [a.name for a in node.names] == ["wal"]
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS
    assert type(BackupEngine(serialize_bundle)._wal) is wal.WalEngine
    assert GENESIS == wal.GENESIS


def test_r3_public_surface():
    assert set(backup.__all__) == {"EMPTY_STATE_ID", "FAILURE_MAPPING", "GENESIS",
                                   "BackupEngine", "BackupError",
                                   "derive_backup_id", "serialize_bundle"}
    assert issubclass(BackupError, Exception)


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_derive_backup_id_reproduces_every_pinned_receipt(section):
    for row in CASES[section]:
        receipt = row["expect"]
        assert derive_backup_id(receipt["head"], receipt["state_id"],
                                receipt["entry_count"],
                                receipt["bundle"]) == receipt["backup_id"]
        got = BackupEngine(serialize_bundle).backup(copy.deepcopy(row["log"]))
        assert got == receipt, row["name"]


def test_r5_corrupt_source_surfaces_from_the_linked_wal():
    row = CASES["happy"][0]
    log = copy.deepcopy(row["log"])
    log[0]["sequence"] = 2
    with pytest.raises(wal.WalError):
        wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    before = copy.deepcopy(log)
    with pytest.raises(BackupError) as exc:
        BackupEngine(serialize_bundle).backup(log)
    assert exc.value.failure_class == "corrupt_source"
    assert log == before
