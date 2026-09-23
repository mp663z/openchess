"""T0251: production export runtime proof (store/export.py).

The T0250 red battery is the behavioral proof: it runs against
store.export with only its two binding lines switched. This file proves
the switch is exactly those two lines, that production never imports
the test package and links the production WAL through its public
snapshot/restore helpers, and that production reproduces every pinned
fixture receipt with the honest exporter.
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

from store import export, wal  # noqa: E402
from store.export import (  # noqa: E402
    FORMATS,
    ExportEngine,
    ExportError,
    derive_export_id,
    render_document,
)

RED = ROOT / "tests" / "test_t0250_export_red.py"
PRODUCTION = ROOT / "store" / "export.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "export" / "cases.json").read_text())
ORACLE_LINES = ("ExportEngine = _reference.ExportEngine\n"
                "ExportError = _reference.ExportError\n")
PRODUCTION_LINES = (
    'ExportEngine = __import__("store.export").export.ExportEngine\n'
    'ExportError = __import__("store.export").export.ExportError\n')
# sha256 of tests/test_t0250_export_red.py as merged (7ad8b4d)
RED_AS_MERGED_SHA256 = (
    "d986a85ea2ad651a01705b82fb0abda058ebe67d54fb394827e400c2a46139af")
ALLOWED_IMPORTS = {"__future__", "hashlib", "json", "pathlib", "yaml",
                   "store", "tools.export_contract_lint"}


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
    assert wal_attrs <= set(wal.__all__), wal_attrs - set(wal.__all__)
    assert {"snapshot", "restore", "WalEngine", "WalError"} <= wal_attrs
    assert type(ExportEngine(render_document)._wal) is wal.WalEngine


def test_r3_public_surface():
    assert set(export.__all__) == {"FAILURE_MAPPING", "FORMATS",
                                   "ExportEngine", "ExportError",
                                   "derive_export_id", "render_document"}
    assert FORMATS == ("jsonl-v1",)
    assert issubclass(ExportError, Exception)
    with pytest.raises(ValueError):
        render_document({}, "csv")


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_reproduces_every_pinned_receipt(section):
    for row in CASES[section]:
        log = copy.deepcopy(row["log"])
        request = copy.deepcopy(row["request"])
        entries = list(log)
        before = (copy.deepcopy(log), copy.deepcopy(request))
        receipt = ExportEngine(render_document).export(log, request)
        assert receipt == row["expect"], row["name"]
        assert derive_export_id(receipt["head"], receipt["state_id"],
                                receipt["record_count"], receipt["format"],
                                receipt["document"]) == receipt["export_id"]
        assert (log, request) == before
        assert all(a is b for a, b in zip(log, entries, strict=True))


def test_r5_corrupt_source_surfaces_from_the_linked_wal():
    row = next(r for r in CASES["happy"] if len(r["log"]) >= 2)
    log = copy.deepcopy(row["log"])
    log[1]["sequence"] = 3
    with pytest.raises(wal.WalError):
        wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    before = copy.deepcopy(log)
    calls = []
    with pytest.raises(ExportError) as exc:
        ExportEngine(lambda state, fmt: calls.append(state) or "").export(
            log, {"format": "jsonl-v1"})
    assert exc.value.failure_class == "corrupt_source"
    assert log == before and calls == []
