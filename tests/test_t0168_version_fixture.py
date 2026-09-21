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

PAYLOAD_DIGESTS = {  # ruff: noqa: E501
    "happy": {
        "insert-root": "50a9d02654fd286f67221a9b45eb57d7f5b84240fed3579771849885e271947b",
        "insert-linear-child": "14e682b55268933c557c4b062ee66e681c2330d8535149d039451f727f7eedcf",
        "dedup-byte-equal": "1d48b65a4a43774a4482968bfd3212aea831e53e2c9df6b5d2d472b22cbe8577",
        "insert-four-version-chain": "036d3418864ea208a3ddbb020e00ca271cae12f6cb78bffa434b32b5c7636143",  # noqa: E501
    },
    "boundary": {
        "equal-parent-timestamp": "e8603bc75c4fd7042df7addeee0b45d1553a98e512ab71be751e9beb3461a8ba",  # noqa: E501
        "duplicate-parents-canonicalized": "bd677730fb7eafb6a23237c994c395eb46104b267e9f424da899e9fcc5deea2a",  # noqa: E501
        "leap-day-valid": "22d1a636fd0a7d80403a240b627a61ae06568bcc4376763d878db33547c645c3",
        "parent-order-canonicalized": "4c48a5716306bc026a6bad3f0e742622ff76c78f4385e811d4ee6e9be2c23421",  # noqa: E501
    },
    "malformed": {
        "missing-label": "21deb1cdd98f9f68ba0e082e538b66fefa5fd8a6c2ee24b20ef21f9db5b5ba41",
        "empty-label": "38afb13af9672d483fbf50834d50b55b6e2d74adfb0d17eac3396d2cbc3190e8",
        "underived-id": "ce104ccd9cd86a8ed6c50c7e9c520290ea711e80b4a7aaaff1c8feb1525905aa",
        "unknown-parent": "7d8069329f1ba0a99ab57375af779bda8cce00fae6470b8ed6ee542b7250caa7",
        "nonmonotonic-version": "d052a2cc744e693d65307bcab8ee521c13893407a043613cc99f2354838bd7f4",
        "second-root": "ef6633923ed5105f0098c7ece0e0207b78a87ea90436aefc3748b472844bd32b",
        "metadata-conflict": "5155eabe4a4d9b67f5be0b7539fd536cea4eadb02288063bfd9547d77eff8a5c",
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
    dup = rows["duplicate-parents-canonicalized"]["record"]["parent_ids"]
    assert len(dup) > len(set(dup))
    assert _valid_timestamp(rows["leap-day-valid"]["record"]["created_at"])
    order = rows["parent-order-canonicalized"]["record"]["parent_ids"]
    assert order == sorted(order)


def test_malformed_and_minimal_repairs():
    for r in CASES["malformed"]:
        s = _load(r["initial"])
        before = copy.deepcopy((s.records, s.root_id))
        with pytest.raises(VersionError) as exc:
            s.insert(copy.deepcopy(r["record"]))
        assert exc.value.failure_class == r["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[r["expect_failure"]]
        assert (s.records, s.root_id) == before
        _load(r["initial"]).insert(copy.deepcopy(r["repair"]))


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
