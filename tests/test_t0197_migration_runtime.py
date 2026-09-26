"""T0197: production migration runtime proof (store/migration.py).

The T0196 red battery is the behavioral proof: it runs against
store.migration with only its two binding lines switched. This file
proves the switch is exactly those two lines, that production never
imports the test package and links only shipped runtimes, that its
state id is the graph-diff serialization, and that production
reproduces every pinned fixture receipt with the honest oracle.
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

from graph import diff  # noqa: E402
from graph.node import make_record, record_identity  # noqa: E402
from store import migration  # noqa: E402
from store.backup import serialize_bundle  # noqa: E402
from store.migration import (  # noqa: E402
    MigrationEngine,
    MigrationError,
    derive_migration_id,
    pdv2_digest,
    state_id,
)

RED = ROOT / "tests" / "test_t0196_migration_red.py"
PRODUCTION = ROOT / "store" / "migration.py"
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "migration" / "cases.json").read_text())
ORACLE_LINES = ("MigrationEngine = _reference.MigrationEngine\n"
                "MigrationError = _reference.MigrationError\n")
PRODUCTION_LINES = (
    'MigrationEngine = __import__("store.migration").migration'
    '.MigrationEngine\n'
    'MigrationError = __import__("store.migration").migration'
    '.MigrationError\n')
# sha256 of tests/test_t0196_migration_red.py as merged with the
# bound-error mutant fix (#246, merged at d07e7c6)
RED_AS_MERGED_SHA256 = (
    "4ba48e4c15192dba337fde82915de993d84d6972fb7198292f508479030ae394")

# sha256 of the same oracle-binding reconstruction after the reviewed
# test-infrastructure hygiene edit (PR #374): the red battery's
# _substitution_mutants streams mutants instead of materializing the
# full list; mutant order, labels, contents and counts are unchanged in
# both digest modes (ordered label/content SHA-256 verified against the
# pre-hygiene baseline), so the binding proof above carries over as-is.
# RED_AS_MERGED_SHA256 stays as the historical record of the
# pre-hygiene merged bytes.
RED_AS_MERGED_AFTER_HYGIENE_SHA256 = (
    "a1e7ab1d00d209ee20e325f37351c25c256bb1f5bb4ad44b08887c35614b7302")
ALLOWED_IMPORTS = {"__future__", "hashlib", "re", "pathlib", "yaml",
                   "graph.node", "graph.position_digest",
                   "tools.migration_contract_lint", "tools.variant_runtime"}


def test_r1_red_battery_switch_is_exactly_the_two_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES)
    assert hashlib.sha256(restored.encode()).hexdigest() == \
        RED_AS_MERGED_AFTER_HYGIENE_SHA256


def test_r2_production_imports_only_shipped_runtimes():
    tree = ast.parse(PRODUCTION.read_text())
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            seen.add(node.module)
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS


def test_r3_public_surface():
    assert set(migration.__all__) == {
        "FAILURE_MAPPING", "MigrationEngine", "MigrationError",
        "derive_migration_id", "pdv2_digest", "state_id"}
    assert issubclass(MigrationError, Exception)


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_r4_reproduces_every_pinned_receipt(section):
    for row in CASES[section]:
        request = copy.deepcopy(row["request"])
        source = copy.deepcopy(row["source"])
        records = list(source.values())
        before = (copy.deepcopy(request), copy.deepcopy(source))
        receipt = MigrationEngine(pdv2_digest).migrate(request, source)
        assert receipt == row["expect"], row["name"]
        assert list(receipt["state"]) == list(row["expect"]["state"])
        assert derive_migration_id(
            receipt["from_schema"], receipt["to_schema"],
            receipt["source_id"], receipt["target_id"]) == \
            receipt["migration_id"]
        assert state_id(receipt["state"]) == receipt["target_id"]
        assert (request, source) == before
        assert all(a is b for a, b in
                   zip(source.values(), records, strict=True))


_FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w Kq - 0 1",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",
    "8/8/8/8/8/8/8/K1k5 w - - 0 1",
)


def _valid_v1_states():
    """Every fixture source plus every subset (in two insertion orders)
    of records built by the shipped graph.node runtime."""
    records = [make_record("standard", fen) for fen in _FENS]
    for row in [r for s in ("happy", "boundary", "malformed", "rollback")
                for r in CASES.get(s, [])]:
        source = row.get("source")
        if type(source) is dict and all(
                type(r) is dict for r in source.values()):
            records.extend(dict(r) for r in source.values())
    unique = {}
    for record in records:
        try:
            key = record_identity(record)
        except Exception:  # noqa: BLE001 - malformed fixture records
            continue
        unique[key] = record
    items = sorted(unique.items())
    assert len(items) >= len(_FENS)
    states = [{}]
    for mask in range(1, 1 << len(items)) if len(items) <= 10 else \
            range(1, 1 << 10):
        chosen = [items[i] for i in range(min(len(items), 10))
                  if mask >> i & 1]
        states.append(dict(chosen))
        states.append(dict(reversed(chosen)))
    return states


def test_r5_state_id_is_the_graph_diff_serialization():
    """On every valid store-v1 state (fixture sources plus every subset
    of shipped-runtime records, both insertion orders) the local state
    id equals the shipped graph.diff.state_id; every fixture request's
    source_id is reproduced."""
    states = _valid_v1_states()
    assert len(states) > 500
    for state in states:
        assert state_id(state) == diff.state_id(copy.deepcopy(state))
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            assert state_id(row["source"]) == row["request"]["source_id"]
            assert diff.state_id(copy.deepcopy(row["source"])) == \
                row["request"]["source_id"]


def test_r6_target_state_id_is_the_canonical_serialization():
    """graph.diff does not model store-v2 records, so on migrated states
    the local id is checked against the shipped canonical serialization
    (store.backup.serialize_bundle) instead."""
    for state in _valid_v1_states()[:200]:
        request = {"from_schema": "store-v1", "to_schema": "store-v2",
                   "source_id": state_id(state)}
        receipt = MigrationEngine(pdv2_digest).migrate(
            request, copy.deepcopy(state))
        assert receipt["target_id"] == "gs1:" + hashlib.sha256(
            serialize_bundle(receipt["state"]).encode()).hexdigest()
        assert set(receipt["state"]) == set(state)


KINGS_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


def _kings_source():
    record = make_record("standard", KINGS_FEN)
    return {record_identity(record): record}


def _request_for(source):
    return {"from_schema": "store-v1", "to_schema": "store-v2",
            "source_id": state_id(source)}


def _assert_rejects(source, request, failure, oracle=pdv2_digest):
    before = (copy.deepcopy(request), copy.deepcopy(source))
    records = list(source.values())
    with pytest.raises(MigrationError) as exc:
        MigrationEngine(oracle).migrate(request, source)
    assert exc.value.failure_class == failure
    assert exc.value.code == migration.FAILURE_MAPPING[failure]
    assert (request, source) == before
    assert list(request) == list(before[0])
    assert all(a is b for a, b in zip(source.values(), records, strict=True))


def test_r7_mis_keyed_record_with_consistent_source_id_is_malformed():
    """A valid record under the wrong identity key, with the source id
    recomputed over that exact state, is still malformed."""
    (record,) = _kings_source().values()
    for wrong in ("('standard', 'wrong')",
                  record_identity(make_record("standard", _FENS[0])),
                  record_identity(record) + " "):
        source = {wrong: dict(record)}
        _assert_rejects(source, _request_for(source),
                        "malformed_migration_record")


def test_r8_non_canonical_snapshot_with_consistent_source_id_is_malformed():
    """Non-canonical clocks keep the identity and digest but are not the
    canonical snapshot: malformed, never carried into the target."""
    (key, record), = _kings_source().items()
    for fen in ("4k3/8/8/8/8/8/8/4K3 w - - 5 9",
                "4k3/8/8/8/8/8/8/4K3 w - - 0 2"):
        source = {key: {**record, "snapshot_fen": fen}}
        _assert_rejects(source, _request_for(source),
                        "malformed_migration_record")


def _mutating_oracle(request, source, fail_on_call):
    """Adds keys to the caller's request, a live source record and the
    source container, then answers honestly or (on FAIL_ON_CALL)
    with an off-grammar value."""
    calls = []

    def oracle(variant, fen):
        calls.append(fen)
        request["zz"] = 1
        request["source_id"] = "gs1:" + "0" * 64
        for rec in list(source.values()):
            rec["zz"] = 1
        source["zz"] = {"variant": "x"}
        if len(calls) == fail_on_call:
            return None
        return pdv2_digest(variant, fen)
    return oracle


def _two_record_source():
    records = [make_record("standard", fen) for fen in _FENS[:2]]
    return {record_identity(r): r for r in records}


@pytest.mark.parametrize("fail_on_call", [0, 1, 2])
def test_r9_added_keys_are_undone_on_every_exit(fail_on_call):
    """Keys the oracle ADDS to the request, to a live source record and
    to the source container are removed on the ok path (0) and on the
    divergent path at the first and last call; values, key order and
    object identity are all restored."""
    source = _two_record_source()
    request = _request_for(source)
    before = (copy.deepcopy(request), copy.deepcopy(source))
    records = list(source.values())
    engine = MigrationEngine(_mutating_oracle(request, source, fail_on_call))
    if fail_on_call:
        with pytest.raises(MigrationError) as exc:
            engine.migrate(request, source)
        assert exc.value.failure_class == "divergent_target"
    else:
        receipt = engine.migrate(request, source)
        assert receipt["source_id"] == before[0]["source_id"]
        assert set(receipt["state"]) == set(before[1])
    assert request == before[0] and list(request) == list(before[0])
    assert source == before[1] and list(source) == list(before[1])
    assert all(a is b for a, b in zip(source.values(), records, strict=True))
    for rec, want in zip(source.values(), before[1].values(), strict=True):
        assert list(rec) == list(want)
