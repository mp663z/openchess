"""T2723: audit sample tool reconciles raw-to-normalized deterministically."""

import json
from pathlib import Path

import pytest

from tools.corpus_audit_sample import run_sample

FIXTURE = Path("data/raw/lichess_db_puzzle.csv.zst")

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="puzzle snapshot not downloaded (CI)")


def test_sample_manifest_reconciles():
    m = run_sample(FIXTURE, 5000)
    assert m["rows_read"] == 5000
    assert m["normalized_count"] + m["quarantined_count"] == m["rows_read"]
    assert sum(m["license_tally"].values()) == m["normalized_count"]
    assert set(m["license_tally"]) <= {"CC0"}
    assert len(m["raw_sample_sha256"]) == 64
    assert len(m["normalized_id_multiset_sha256"]) == 64


def test_sample_is_deterministic():
    a = run_sample(FIXTURE, 2000)
    b = run_sample(FIXTURE, 2000)
    assert a == b  # same raw checksum, same counts, same normalized multiset


def test_committed_manifest_matches_tool_output():
    committed = json.loads(Path("data/datasets/corpus-audit-sample.json").read_text())
    rerun = run_sample(FIXTURE, committed["sample_rows_requested"])
    assert committed == rerun


def test_evidence_matches_manifest():
    """Guard against stale evidence: the evidence file must quote the exact
    checksums from the committed manifest."""
    committed = json.loads(Path("data/datasets/corpus-audit-sample.json").read_text())
    evidence = Path("evidence/T2723.md").read_text()
    assert committed["normalized_id_multiset_sha256"] in evidence
    assert committed["raw_sample_sha256"] in evidence
    assert "READ WINDOW" in evidence  # raw checksum described honestly
