"""T2229/T0003: every captured statement hash in the rights manifest recomputes."""

import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOC = yaml.safe_load((ROOT / "data/datasets/public-source-rights.yaml").read_text())


def test_all_statement_hashes_recompute():
    checked = 0
    for source in DOC["sources"]:
        statement = source.get("statement")
        recorded = source.get("statement_sha256")
        if statement is None:
            assert recorded is None, source["id"]
            continue
        recomputed = hashlib.sha256(statement.encode()).hexdigest()
        assert recomputed == recorded, (
                f"{source['id']}: recorded {recorded} != recomputed {recomputed}"
            )
        checked += 1
    assert checked >= 10, "manifest should carry a dozen captured statements"


def test_no_null_hash_with_statement():
    for source in DOC["sources"]:
        if source.get("statement"):
            assert source.get("statement_sha256"), source["id"]
