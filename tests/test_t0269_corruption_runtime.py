"""T0269: production corruption scan runtime proof (store/corruption.py).

The T0268 red battery is the behavioral proof: it runs against
store.corruption with only its two binding lines switched. This file
proves the switch is exactly those two lines, that production never
imports the test package and links the production WAL through its
public surface only, that the production canonical encoding and
quarantine token are byte-identical to the contract reference, that the
verified prefix is the linked production WAL's judgment, and that
production reproduces every pinned fixture outcome.
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

from store import corruption, wal  # noqa: E402
from store.corruption import (  # noqa: E402
    CorruptionEngine,
    CorruptionError,
    canonical_encoding,
    derive_scan_id,
    quarantine_suffix,
)
from tests import test_t0266_corruption_contract as _reference  # noqa: E402
from tests import test_t0268_corruption_red as _red  # noqa: E402
from tests.test_t0267_corruption_fixture import (  # noqa: E402
    ORACLES,
    _repaired,
)
from tools.corruption_contract_lint import FAILURE_MAPPING  # noqa: E402

RED = ROOT / "tests" / "test_t0268_corruption_red.py"
PRODUCTION = ROOT / "store" / "corruption.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "corruption" / "cases.json").read_text())
ORACLE_LINES = ("CorruptionEngine = _reference.CorruptionEngine\n"
                "CorruptionError = _reference.CorruptionError\n")
PRODUCTION_LINES = (
    'CorruptionEngine = __import__("store.corruption")'
    '.corruption.CorruptionEngine\n'
    'CorruptionError = __import__("store.corruption")'
    '.corruption.CorruptionError\n')
# sha256 of tests/test_t0268_corruption_red.py as merged (#250)
RED_AS_MERGED_SHA256 = (
    "1d8025b933683c39a778dc9a20169b3e3fcd74763385f6a8cf8aa6208cd9bf90")

# sha256 of the same oracle-binding reconstruction after the reviewed
# test-infrastructure hygiene edit (PR #374): the red battery's
# _substitution_mutants streams mutants instead of materializing the
# full list; mutant order, labels, contents and counts are unchanged in
# both digest modes (ordered label/content SHA-256 verified against the
# pre-hygiene baseline), so the binding proof above carries over as-is.
# RED_AS_MERGED_SHA256 stays as the historical record of the
# pre-hygiene merged bytes.
RED_AS_MERGED_AFTER_HYGIENE_SHA256 = (
    "417cf85285bd05d2a7934bd50d94b09a4d5402f2fd2be99f070341cf520778c0")
ALLOWED_IMPORTS = {"__future__", "copy", "hashlib", "re", "pathlib", "yaml",
                   "store", "tools.corruption_contract_lint"}


def test_r1_red_battery_switch_is_exactly_the_two_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES)
    assert hashlib.sha256(restored.encode()).hexdigest() == \
        RED_AS_MERGED_AFTER_HYGIENE_SHA256
    assert _red.CorruptionEngine is CorruptionEngine
    assert _red.CorruptionError is CorruptionError


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
    assert {"WalEngine", "WalError", "GENESIS",
            "canonical_payload"} <= wal_attrs
    engine = CorruptionEngine(quarantine_suffix)
    assert type(engine._wal) is wal.WalEngine
    assert engine._wal.canonicalizer is wal.canonical_payload


def test_r3_public_surface():
    assert set(corruption.__all__) == {
        "FAILURE_MAPPING", "CorruptionEngine", "CorruptionError",
        "canonical_encoding", "derive_scan_id", "quarantine_suffix"}
    assert issubclass(CorruptionError, Exception)
    assert corruption.FAILURE_MAPPING is FAILURE_MAPPING


def _run(oracle, log, request):
    return CorruptionEngine(ORACLES[oracle]).scan(log, request)


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_reproduces_every_pinned_outcome(section):
    for row in CASES[section]:
        log = copy.deepcopy(row["log"])
        request = copy.deepcopy(row["request"])
        kept = list(log[:row["expect"]["verified_count"]])
        receipt = _run(row["oracle"], log, request)
        assert receipt == row["expect"], row["name"]
        assert receipt["scan_id"] == derive_scan_id(
            receipt["verdict"], receipt["verified_head"],
            receipt["verified_count"], receipt["quarantined_count"],
            receipt["quarantine_token"])
        assert request == row["request"]
        assert len(log) == len(kept)
        assert all(a is b for a, b in zip(log, kept, strict=True))


def test_r4_malformed_and_rollback_rows():
    for row in CASES["malformed"]:
        log, request = copy.deepcopy(row["log"]), copy.deepcopy(
            row["request"])
        with pytest.raises(CorruptionError) as exc:
            _run(row["oracle"], log, request)
        assert exc.value.failure_class == row["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[row["expect_failure"]]
        assert (log, request) == (row["log"], row["request"])
        fixed = _repaired(row)
        assert _run(fixed["oracle"], copy.deepcopy(fixed["log"]),
                    copy.deepcopy(fixed["request"])) == \
            _reference.CorruptionEngine(ORACLES[fixed["oracle"]]).scan(
                copy.deepcopy(fixed["log"]), copy.deepcopy(fixed["request"]))
    for row in CASES["rollback"]:
        log, request = copy.deepcopy(row["log"]), copy.deepcopy(
            row["request"])
        with pytest.raises(CorruptionError) as exc:
            _run(row["oracle"], log, request)
        assert exc.value.failure_class == row["expect_failure"]
        assert (log, request) == (row["log"], row["request"])
        assert _run(row["then_oracle"], copy.deepcopy(row["then_log"]),
                    copy.deepcopy(row["then_request"])) == row["expect"]


def _encoding_inputs():
    out = [row["log"] for s in ("happy", "boundary", "rollback")
           for row in CASES[s]]
    out += [log for name, (build, _) in _red.PROBES.items()
            if name.startswith("wal-")
            for log in [build()[0]]]
    out += [[], [None, True, False, 0, -1, 2 ** 255, "", "\u00e9",
                 "\U0001d11e", [], {}, {"b": [1], "a": {"c": None}}]]
    return [log for log in out if _in_domain(log)]


def _in_domain(log):
    try:
        _reference.canonical_encoding(log)
    except _reference._OutOfDomain:
        return False
    return True


def test_r5_out_of_domain_logs_are_rejected_alike():
    for row in CASES["rollback"] + CASES["malformed"]:
        log = row["log"]
        if type(log) is list and not _in_domain(log):
            with pytest.raises(corruption._OutOfDomain):
                canonical_encoding(log)


def test_r5_canonical_encoding_and_token_match_the_reference():
    """Byte-identical canonical encoding, detached copy and token on
    every fixture log, every wal-* probe log and a leaf-edge log."""
    for log in _encoding_inputs():
        copied, raw = canonical_encoding(log)
        ref_copied, ref_raw = _reference.canonical_encoding(log)
        assert raw == ref_raw
        assert copied == ref_copied and copied is not log
        assert quarantine_suffix(log) == _reference.quarantine_suffix(log)


def test_r6_verified_prefix_is_the_linked_wal_judgment():
    """The production verified prefix equals a LINEAR search with the
    production WAL's replay on every salvaging fixture row and wal-*
    probe."""
    engine = CorruptionEngine(quarantine_suffix)
    for log in _encoding_inputs():
        frozen, _ = canonical_encoding(log)
        count, head = 0, wal.GENESIS
        for k in range(1, len(frozen) + 1):
            try:
                result = wal.WalEngine(wal.canonical_payload).replay(
                    copy.deepcopy(frozen[:k]))
            except wal.WalError:
                break
            count, head = k, result["head"]
        assert engine._verified_prefix(frozen) == (count, head)
