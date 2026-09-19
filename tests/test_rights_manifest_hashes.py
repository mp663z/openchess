"""T2229/T0003: every evidence snapshot hash recomputes over its BYTES and
the cited statement appears verbatim inside the snapshot."""

from pathlib import Path

import yaml

from tools import rights_audit

ROOT = Path(__file__).resolve().parent.parent
DOC = yaml.safe_load(rights_audit.RIGHTS.read_text())


def test_all_evidence_hashes_recompute_over_snapshot_bytes():
    import hashlib

    checked = 0
    for source in DOC["sources"]:
        ev_path = source.get("evidence_path")
        if ev_path is None:
            assert source.get("statement") is None, source["id"]
            continue
        blob = (ROOT / ev_path).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == source["evidence_sha256"], source["id"]
        checked += 1
    assert checked >= 10, "manifest should carry a dozen captured evidence files"


def test_statement_contained_in_evidence_bytes():
    for source in DOC["sources"]:
        if source.get("statement"):
            text = (ROOT / source["evidence_path"]).read_text()
            assert source["statement"] in text, source["id"]


def test_full_audit_is_green():
    assert rights_audit.validate(DOC) == []
