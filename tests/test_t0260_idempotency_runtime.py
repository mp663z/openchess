"""T0260: production idempotency runtime proof (store/idempotency.py).

The T0259 red battery is the behavioral proof: it runs against
store.idempotency with only its two binding lines switched. This file
proves the switch is exactly those two lines, that production never
imports the test package and links the production WAL through its
public surface only, that request validation through the WAL's public
append agrees with the WAL's own payload validation, and that
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

from store import idempotency, wal  # noqa: E402
from store.idempotency import (  # noqa: E402
    IdempotencyEngine,
    IdempotencyError,
    derive_receipt_id,
    request_fingerprint,
)

RED = ROOT / "tests" / "test_t0259_idempotency_red.py"
PRODUCTION = ROOT / "store" / "idempotency.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "idempotency" / "cases.json").read_text())
ORACLE_LINES = ("IdempotencyEngine = _reference.IdempotencyEngine\n"
                "IdempotencyError = _reference.IdempotencyError\n")
PRODUCTION_LINES = (
    'IdempotencyEngine = __import__("store.idempotency")'
    '.idempotency.IdempotencyEngine\n'
    'IdempotencyError = __import__("store.idempotency")'
    '.idempotency.IdempotencyError\n')
# sha256 of tests/test_t0259_idempotency_red.py as merged (bound-error
# source-mutant rebind)
RED_AS_MERGED_SHA256 = (
    "cac1c10e4179118dcab2305795a6e535ac8f6c2d6c5fc7c699bd7b523fb4d770")
ALLOWED_IMPORTS = {"__future__", "copy", "hashlib", "re", "pathlib", "yaml",
                   "store", "tools.idempotency_contract_lint"}


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
    engine = IdempotencyEngine(request_fingerprint)
    assert type(engine._wal) is wal.WalEngine
    assert engine._wal.canonicalizer is wal.canonical_payload


def test_r3_public_surface():
    assert set(idempotency.__all__) == {
        "FAILURE_MAPPING", "IdempotencyEngine", "IdempotencyError",
        "derive_receipt_id", "request_fingerprint"}
    assert issubclass(IdempotencyError, Exception)


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_reproduces_every_pinned_outcome(section):
    for row in CASES[section]:
        log = copy.deepcopy(row["log"])
        ledger = copy.deepcopy(row["ledger"])
        request = copy.deepcopy(row["request"])
        entries, receipts = list(log), list(ledger)
        before_request = copy.deepcopy(request)
        result = IdempotencyEngine(request_fingerprint).apply(
            log, ledger, request)
        assert result == row["expect"], row["name"]
        receipt = result["receipt"]
        assert derive_receipt_id(
            receipt["idempotency_key"], receipt["request_fingerprint"],
            receipt["entry_id"], receipt["sequence"]) == receipt["receipt_id"]
        assert request == before_request
        grew = 1 if result["outcome"] == "applied" else 0
        assert len(log) == len(entries) + grew
        assert len(ledger) == len(receipts) + grew
        assert all(a is b for a, b in zip(log, entries, strict=False))
        assert all(a is b for a, b in zip(ledger, receipts, strict=False))
        if grew:
            assert log[-1]["entry_id"] == receipt["entry_id"]
            assert ledger[-1] == receipt


def _payload_cases():
    rows = [r for s in ("happy", "boundary") for r in CASES[s]]
    good = rows[0]["request"]["payload"]
    record = good["record"]

    class S(str):
        pass

    out = [("put", copy.deepcopy(r["request"]["payload"])) for r in rows]
    out += [(op, copy.deepcopy(good)) for op in ("delete", "Put", "", "x")]
    out += [("put", p) for p in (
        None, [], {"identity": good["identity"]},
        {**good, "extra": 1},
        {**good, "identity": 1},
        {**good, "identity": "wrong"},
        {**good, "record": {**record, "digest": "pdv1:" + "0" * 64}},
        {**good, "record": {**record, "variant": S(record["variant"])}},
        {**good, "record": {k: v for k, v in record.items()
                            if k != "digest"}},
        {**good, "record": {**record, "snapshot_fen": "garbage"}},
        {**good, "record": None},
    )]
    return out


def test_r5_request_validation_agrees_with_the_wal_payload_validation():
    """The public append-on-empty-log route rejects exactly what the
    WAL's own op registry and payload validation reject, and leaves the
    request payload untouched."""
    for op, payload in _payload_cases():
        try:
            if op not in wal._OPS:
                raise wal.WalError("unknown_operation", "x")
            wal._validate_payload(op, copy.deepcopy(payload))
            expected_ok = True
        except wal.WalError:
            expected_ok = False
        request = {"idempotency_key": "k-1", "op": op, "payload": payload}
        before = copy.deepcopy(request)
        try:
            IdempotencyEngine(request_fingerprint)._validate_request(request)
            ok = True
        except IdempotencyError as err:
            assert err.failure_class == "malformed_idempotency_request"
            ok = False
        assert ok == expected_ok, (op, payload)
        assert request == before


def test_r6_corrupt_source_surfaces_from_the_linked_wal():
    row = next(r for r in CASES["happy"] + CASES["boundary"]
               if len(r["log"]) >= 2)
    log = copy.deepcopy(row["log"])
    log[1]["sequence"] = 3
    with pytest.raises(wal.WalError):
        wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log))
    before = copy.deepcopy(log)
    calls = []
    with pytest.raises(IdempotencyError) as exc:
        IdempotencyEngine(lambda op, p: calls.append(op) or "").apply(
            log, [], copy.deepcopy(row["request"]))
    assert exc.value.failure_class == "corrupt_source"
    assert log == before and calls == []
