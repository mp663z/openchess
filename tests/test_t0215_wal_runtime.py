"""T0215: production WAL runtime proof (store/wal.py).

The T0214 red battery is the behavioral proof: it runs against
store.wal with only its production-bindings import switched. This file
proves the switch is exactly that one line, that production never
imports the test package, and the runtime-specific properties the
battery cannot see (memoized linked derivation stays detached and
exact).
"""

from __future__ import annotations

import ast
import copy
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.node import make_record, record_identity  # noqa: E402
from store import wal  # noqa: E402
from store.wal import GENESIS, WalEngine, WalError, canonical_payload  # noqa: E402

RED = ROOT / "tests" / "test_t0214_wal_red.py"
PRODUCTION = ROOT / "store" / "wal.py"
ORACLE_LINE = "from tests import test_t0212_wal_contract as _ref  # noqa: E402\n"
PRODUCTION_LINE = "from store import wal as _ref  # noqa: E402\n"
# sha256 of tests/test_t0214_wal_red.py with the oracle binding line: the
# #210 battery (18adad5) plus the two huge-fullmove-FEN probes
RED_AS_MERGED_SHA256 = (
    "9f81bb7b5f0242ba3d8751d57e328f7c233ba8d6c296d238a267e7a556e78d88")
ALLOWED_IMPORTS = {"__future__", "functools", "hashlib", "re", "pathlib",
                   "yaml", "graph.diff", "graph.node",
                   "graph.position_digest", "tools.variant_runtime",
                   "tools.wal_contract_lint"}

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


def _payload(fen):
    record = make_record("standard", fen)
    return {"identity": record_identity(record), "record": record}


def test_r1_red_battery_switch_is_exactly_the_binding_line():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINE) == 1
    assert ORACLE_LINE not in source
    restored = source.replace(PRODUCTION_LINE, ORACLE_LINE)
    assert hashlib.sha256(restored.encode()).hexdigest() == \
        RED_AS_MERGED_SHA256


def test_r2_production_imports_only_shipped_runtimes():
    tree = ast.parse(PRODUCTION.read_text())
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            seen.add(node.module)
        elif isinstance(node, ast.Call) and \
                isinstance(node.func, ast.Name):
            assert node.func.id not in {"__import__", "eval", "exec",
                                        "compile", "getattr"}
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS
    assert not any(name.startswith("tests") for name in seen)


def test_r3_public_surface():
    assert set(wal.__all__) == {"FAILURE_MAPPING", "GENESIS", "WalEngine",
                                "WalError", "canonical_payload", "restore",
                                "snapshot"}
    assert not hasattr(wal, "_snapshot") and not hasattr(wal, "_restore")
    assert GENESIS == "wal0:" + "0" * 64
    assert issubclass(WalError, Exception)


def test_r3_snapshot_restore_round_trip_preserves_identity_and_value():
    log = []
    engine = WalEngine(canonical_payload)
    engine.append(log, {"op": "put", "payload": _payload(START)})
    engine.append(log, {"op": "put", "payload": _payload(KINGS)})
    objs = [(e, e["payload"], e["payload"]["record"]) for e in log]
    values = copy.deepcopy(log)
    container, saved = wal.snapshot(log)
    # an ADDED key at every level must be gone after restore too
    for e in log:
        e["x"] = 1
        e["payload"]["x"] = 1
        e["payload"]["record"]["x"] = 1
    log[0]["payload"]["record"]["digest"] = "tampered"
    log[1]["payload"].clear()
    log[0]["op"] = "delete"
    log[:] = [{"junk": 1}]
    wal.restore(log, container, saved)
    assert log == values
    assert [list(e) for e in log] == [list(e) for e in values]
    assert [list(e["payload"]) for e in log] == \
        [list(e["payload"]) for e in values]
    assert [list(e["payload"]["record"]) for e in log] == \
        [list(e["payload"]["record"]) for e in values]
    after = [(e, e["payload"], e["payload"]["record"]) for e in log]
    assert len(after) == len(objs)
    assert all(x is y for pa, pb in zip(after, objs, strict=True)
               for x, y in zip(pa, pb, strict=True))


def test_r4_memoized_linked_derivation_is_detached():
    engine = WalEngine(canonical_payload)
    log = []
    engine.append(log, {"op": "put", "payload": _payload(START)})
    first = engine.replay(log)
    # tamper with every object the engine handed out
    for record in first["state"].values():
        record["digest"] = "pdv1:" + "0" * 64
    receipt = engine.append(log, {"op": "put", "payload": _payload(KINGS)})
    receipt["payload"]["record"]["digest"] = "pdv1:" + "0" * 64
    second = engine.replay(log)
    assert all(rec == make_record("standard", rec["snapshot_fen"])
               for rec in second["state"].values())
    assert second["applied"] == 2 and second["head"] == log[-1]["entry_id"]


def test_r4_memo_never_accepts_a_wrong_digest_after_a_good_one():
    engine = WalEngine(canonical_payload)
    engine.append([], {"op": "put", "payload": _payload(START)})
    bad = _payload(START)
    bad["record"]["digest"] = make_record("standard", KINGS)["digest"]
    before = copy.deepcopy(bad)
    with pytest.raises(WalError) as exc:
        engine.append([], {"op": "put", "payload": bad})
    assert exc.value.failure_class == "malformed_wal_entry"
    assert bad == before


def test_r5_linked_runtime_rejection_is_typed():
    engine = WalEngine(canonical_payload)
    for fen in ("not a fen", "8/8/8/8/8/8/8/8 w - - 0 1"):
        record = {"variant": "standard", "digest": "pdv1:" + "0" * 64,
                  "snapshot_fen": fen}
        request = {"op": "put", "payload": {"identity": "x",
                                            "record": record}}
        before = copy.deepcopy(request)
        with pytest.raises(WalError) as exc:
            engine.append([], request)
        assert exc.value.failure_class == "malformed_wal_entry"
        assert request == before


# -- memo: successes only, narrow rejection set, bounded key bytes --------
# every fixture section the T0214 battery closes over
FIXTURE_SECTIONS = ("happy", "boundary", "malformed", "rollback",
                    "malformed-append-onto-log")


def _shared(engine_holder):
    """A make() that always hands back the SAME engine instance, with its
    canonicalizer switched to the requested oracle."""
    current = [canonical_payload]
    engine = WalEngine(lambda identity, record: current[0](identity, record))
    engine_holder.append(engine)

    def make(oracle):
        current[0] = oracle
        return engine
    return make


def _closes_over_full_fixture(make):
    """Same-instance follow-up over the whole T0214 fixture manifest:
    every happy/boundary script, malformed row, rollback row and
    append-onto-corrupt-log row passes on the one instance."""
    from tests import test_t0214_wal_red as red
    ran = 0
    for section in FIXTURE_SECTIONS:
        for case in red._section_rows(section):
            red._BATTERY[section](make, copy.deepcopy(case))
            ran += 1
    assert ran == sum(len(red._section_rows(s)) for s in FIXTURE_SECTIONS)
    assert ran >= 3 + 3 + 16 + 2


def _good_request():
    return {"op": "put", "payload": _payload(KINGS)}


def test_r6_transient_failure_is_not_memoized(monkeypatch):
    holder = []
    make = _shared(holder)
    engine = make(canonical_payload)
    wal._linked.cache_clear()
    real, calls = wal.make_record, []

    def flaky(variant, fen):
        calls.append(fen)
        if len(calls) == 1:
            raise MemoryError("transient")
        return real(variant, fen)
    monkeypatch.setattr(wal, "make_record", flaky)
    log = []
    with pytest.raises(MemoryError):
        engine.append(log, _good_request())
    assert log == []
    receipt = engine.append(log, _good_request())
    assert receipt == log[0] and receipt["sequence"] == 1
    assert len(calls) == 2
    monkeypatch.setattr(wal, "make_record", real)
    _closes_over_full_fixture(make)


def test_r6_non_contract_bug_propagates_not_blamed_on_caller(monkeypatch):
    holder = []
    make = _shared(holder)
    engine = make(canonical_payload)
    wal._linked.cache_clear()

    def buggy(variant, fen):
        raise TypeError("linked runtime bug")
    monkeypatch.setattr(wal, "make_record", buggy)
    request = _good_request()
    before = copy.deepcopy(request)
    with pytest.raises(TypeError):
        engine.append([], request)
    assert request == before
    assert wal._linked.cache_info().currsize == 0
    monkeypatch.undo()
    _closes_over_full_fixture(make)


def test_r6_memo_bytes_bounded_and_rejections_never_cached():
    holder = []
    make = _shared(holder)
    engine = make(canonical_payload)
    wal._linked.cache_clear()
    for fen in ("x" * 5000, "8/8/8/8/8/8/8/8 w - - 0 1"):
        record = {"variant": "standard", "digest": "pdv1:" + "0" * 64,
                  "snapshot_fen": fen}
        with pytest.raises(WalError) as exc:
            engine.append([], {"op": "put", "payload": {
                "identity": "x", "record": record}})
        assert exc.value.failure_class == "malformed_wal_entry"
    assert wal._linked.cache_info().currsize == 0
    over = {"variant": "v" * 100, "digest": "pdv1:" + "0" * 64,
            "snapshot_fen": KINGS}
    with pytest.raises(WalError):
        engine.append([], {"op": "put", "payload": {"identity": "x",
                                                    "record": over}})
    assert wal._linked.cache_info().currsize == 0
    # non-vacuity: a valid in-bound record IS memoized
    engine.append([], _good_request())
    assert wal._linked.cache_info().currsize == 1
    assert len(KINGS) <= wal._MEMO_MAX_FEN
    _closes_over_full_fixture(make)
