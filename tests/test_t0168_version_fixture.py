"""T0168 graph-version pinned conformance fixture."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.test_t0167_version_contract import (  # noqa: E402
    VersionError,
    VersionStore,
    _real_hasher,
    _valid_timestamp,
)
from tools.version_contract_lint import FAILURE_MAPPING  # noqa: E402

CASES = json.loads((Path(__file__).parent / "fixtures/version/cases.json").read_text())
TOP = {
    "schema",
    "contract",
    "contract_base_path",
    "notes",
    "happy",
    "boundary",
    "malformed",
    "rollback",
}
MANIFESTS = {
    "happy": {
        "insert-root",
        "insert-linear-child",
        "dedup-byte-equal",
        "insert-four-version-chain",
    },
    "boundary": {
        "equal-parent-timestamp",
        "duplicate-parents-canonicalized",
        "leap-day-valid",
        "parent-order-canonicalized",
    },
    "malformed": {
        "missing-label",
        "empty-label",
        "underived-id",
        "unknown-parent",
        "nonmonotonic-version",
        "second-root",
        "metadata-conflict",
    },
    "rollback": {
        "unknown-parent-then-valid",
        "second-root-then-valid",
        "metadata-conflict-then-dedup",
    },
}
FAILURES = {
    "missing-label": "malformed_version_record",
    "empty-label": "malformed_version_record",
    "underived-id": "malformed_version_record",
    "unknown-parent": "unknown_parent",
    "nonmonotonic-version": "nonmonotonic_version",
    "second-root": "root_violation",
    "metadata-conflict": "conflicting_version",
    "unknown-parent-then-valid": "unknown_parent",
    "second-root-then-valid": "root_violation",
    "metadata-conflict-then-dedup": "conflicting_version",
}

PAYLOAD_DIGESTS = {
    "happy": {
        "insert-root": "50a9d02654fd286f67221a9b45eb57d7f5b84240fed3579771849885e271947b",
        "insert-linear-child": "14e682b55268933c557c4b062ee66e681c2330d8535149d039451f727f7eedcf",
        "dedup-byte-equal": "1d48b65a4a43774a4482968bfd3212aea831e53e2c9df6b5d2d472b22cbe8577",
        "insert-four-version-chain": "036d3418864ea208a3ddbb020e00ca271cae12f6cb78bffa434b32b5c7636143",  # noqa: E501
    },
    "boundary": {
        "equal-parent-timestamp": "e8603bc75c4fd7042df7addeee0b45d1553a98e512ab71be751e9beb3461a8ba",  # noqa: E501
        "duplicate-parents-canonicalized": "af6ba4c874ea306f7939ac3b0185503a7f765a57a05d6d601e300919c73f7e26",  # noqa: E501
        "leap-day-valid": "22d1a636fd0a7d80403a240b627a61ae06568bcc4376763d878db33547c645c3",
        "parent-order-canonicalized": "b92d1cdde7c332478a1604a9c48711713bcf998f9de7641c9bd1270cbf51de2e",  # noqa: E501
    },
    "malformed": {
        "missing-label": "c3cca66d02f28b45d365b0118c6fa2df865dceefb3f61cb63c20f96bc9f00bab",
        "empty-label": "ef8d54ac499a71da2c4e0cd8cb21f0c6039075758a912266b2afbb096833520c",
        "underived-id": "cab2a20139a054428e2d68506404fce9e4aa6c6190f6e4da3c5b67e7dcc6c86e",
        "unknown-parent": "5d54b981043aaf3ccf09dcaf109b95440133a73fcde20f0a96ab6ea6227d4ff1",
        "nonmonotonic-version": "c7528bbf0cd6c78ba61ebf64772eec42cd411f4cc53cfba6131838890ae0ccbf",
        "second-root": "3e71e7d5be854d530b2b5a40c479c1f053e50c00b1b4fc09c8a9048162cb3d96",
        "metadata-conflict": "40fed58e933f1e7e6c4fde8d7487732d06e4b9aa2ebb7ce50d860f52685280eb",
    },
    "rollback": {
        "unknown-parent-then-valid": "f389741555bff866d96974560bdac519922fdebfde4195c5f2f0514745b4bb4e",  # noqa: E501
        "second-root-then-valid": "364c6ef872553895ce6b5867f38b3d575b7293f6b834151f64cd2d2211bc46ab",  # noqa: E501
        "metadata-conflict-then-dedup": "510dd9049c8c635f18df400164165eea9011ab7f815534851afbdaf9736d12e4",  # noqa: E501
    },
}


def _payload_digest(row):
    payload = {key: value for key, value in row.items() if key != "name"}
    packed = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()


def _rows(sec):
    return {r["name"]: r for r in CASES[sec]}


def _load(records):
    s = VersionStore()
    for r in records:
        s.insert(copy.deepcopy(r))
    return s


def test_structure_and_closed_manifests():
    assert set(CASES) == TOP and CASES["schema"] == 1 and CASES["contract"] == "graph-version"
    for sec, names in MANIFESTS.items():
        rows = _rows(sec)
        assert set(rows) == names
        assert len(rows) == len(CASES[sec])
        assert {name: _payload_digest(row) for name, row in rows.items()} == PAYLOAD_DIGESTS[sec]
    for sec in ("malformed", "rollback"):
        assert {n: r["expect_failure"] for n, r in _rows(sec).items()} == {
            n: FAILURES[n] for n in MANIFESTS[sec]
        }


def test_happy():
    for r in CASES["happy"]:
        s = _load(r["initial"])
        s.insert(copy.deepcopy(r["record"]))
        assert s.canonical_view() == r["expect_ids"]


def test_boundary():
    for r in CASES["boundary"]:
        s = _load(r["initial"])
        got = s.insert(copy.deepcopy(r["record"]))
        assert got["version_id"] in s.records
    rows = _rows("boundary")
    eq = rows["equal-parent-timestamp"]
    assert eq["record"]["created_at"] == eq["initial"][0]["created_at"]
    duplicate_row = rows["duplicate-parents-canonicalized"]
    supplied_dup = copy.deepcopy(duplicate_row["record"]["parent_ids"])
    assert supplied_dup != sorted(set(supplied_dup))
    original_row = copy.deepcopy(duplicate_row)
    dup_store = _load(duplicate_row["initial"])
    dup_stored = dup_store.insert(copy.deepcopy(duplicate_row["record"]))
    canonical = sorted(set(supplied_dup))
    assert dup_stored["parent_ids"] == canonical
    assert dup_store.records[dup_stored["version_id"]]["parent_ids"] == canonical
    assert len(canonical) < len(supplied_dup)
    assert dup_stored["version_id"] == _real_hasher(canonical, dup_stored["graph_digest"])
    assert duplicate_row == original_row
    assert _valid_timestamp(rows["leap-day-valid"]["record"]["created_at"])
    order_row = rows["parent-order-canonicalized"]
    supplied = order_row["record"]["parent_ids"]
    assert supplied != sorted(supplied)
    store = _load(order_row["initial"])
    stored = store.insert(copy.deepcopy(order_row["record"]))
    assert stored["parent_ids"] == sorted(supplied)
    assert stored["parent_ids"] != supplied
    assert stored["version_id"] == _real_hasher(sorted(supplied), stored["graph_digest"])


def test_malformed_and_minimal_repairs():
    for r in CASES["malformed"]:
        s = _load(r["initial"])
        before = copy.deepcopy((s.records, s.root_id))
        with pytest.raises(VersionError) as exc:
            s.insert(copy.deepcopy(r["record"]))
        assert exc.value.failure_class == r["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[r["expect_failure"]]
        assert (s.records, s.root_id) == before
        bad = r["record"]
        repair = r["repair"]
        fields = set(bad) | set(repair)
        changed = {key for key in fields if bad.get(key) != repair.get(key)}
        assert changed == set(r["defect_fields"])
        if "version_id" in changed:
            assert repair["version_id"] == _real_hasher(
                sorted(set(repair["parent_ids"])), repair["graph_digest"]
            )
        _load(r["initial"]).insert(copy.deepcopy(repair))


def test_rollback_reject_then_accept():
    for r in CASES["rollback"]:
        s = _load(r["initial"])
        before = copy.deepcopy((s.records, s.root_id))
        with pytest.raises(VersionError) as exc:
            s.insert(copy.deepcopy(r["bad"]))
        assert exc.value.failure_class == r["expect_failure"]
        assert (s.records, s.root_id) == before
        s.insert(copy.deepcopy(r["good"]))


def test_every_row_payload_is_unique_within_section():
    for sec in MANIFESTS:
        payloads = []
        for row in CASES[sec]:
            payload = {k: v for k, v in row.items() if k != "name"}
            payloads.append(json.dumps(payload, sort_keys=True))
        assert len(payloads) == len(set(payloads)), sec


def test_parent_order_row_kills_store_order_mutant():
    row = _rows("boundary")["parent-order-canonicalized"]
    supplied = row["record"]["parent_ids"]
    assert supplied != sorted(supplied)
    mutant_result = copy.deepcopy(row["record"])
    mutant_result["parent_ids"] = list(supplied)
    honest = _load(row["initial"]).insert(copy.deepcopy(row["record"]))
    assert mutant_result["parent_ids"] != honest["parent_ids"]


def test_duplicate_parent_row_kills_non_deduplicating_mutants():
    row = _rows("boundary")["duplicate-parents-canonicalized"]
    supplied = copy.deepcopy(row["record"]["parent_ids"])
    canonical = sorted(set(supplied))
    honest = _load(row["initial"]).insert(copy.deepcopy(row["record"]))
    for mutant_parents in (list(supplied), sorted(supplied)):
        mutant = copy.deepcopy(row["record"])
        mutant["version_id"] = _real_hasher(canonical, mutant["graph_digest"])
        mutant["parent_ids"] = mutant_parents
        assert mutant["parent_ids"] != honest["parent_ids"]
