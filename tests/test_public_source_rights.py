"""T2229: rights manifest completeness and fail-closed discipline.

The rules live in tools/rights_audit.py (single source of truth, wrapped by
install check T0033); these tests pin the tool against the real manifest.
"""

import yaml

from tools import rights_audit
from tools.install_checks import check_data_model_rights as t0033

DOC = yaml.safe_load(rights_audit.RIGHTS.read_text())


def test_real_manifest_passes_full_audit():
    assert rights_audit.validate(DOC) == []


def test_required_sources_present():
    ids = {s["id"] for s in DOC["sources"]}
    assert ids >= rights_audit.REQUIRED_SOURCES


def test_every_allow_decision_has_policy_license_and_grant():
    for s in DOC["sources"]:
        if s["decision"] == "allow":
            assert s["transformation_permission"] is True, s["id"]
            assert (
                s["license"] in rights_audit.ALLOW_DATA_LICENSES
                or s["id"] == rights_audit.USER_OWNED_ID
            ), s["id"]


def test_removing_required_source_coverage_breaks_full_audit(monkeypatch):
    """The install-check fixtures exercise validate_sources; the required-
    source coverage must independently bite in the full audit."""
    monkeypatch.setattr(
        rights_audit, "REQUIRED_SOURCES", rights_audit.REQUIRED_SOURCES | {"nonexistent"}
    )
    problems = rights_audit.validate(DOC)
    assert any("missing required sources" in p for p in problems)


def test_t0033_fixtures_each_rejected_for_their_own_reason():
    for fname, expect in sorted(t0033.VIOLATIONS.items()):
        problems = rights_audit.validate_sources(
            yaml.safe_load((t0033.FIXTURES / fname).read_text()), t0033.FIXTURES
        )
        assert any(expect in p for p in problems), f"{fname}: {problems}"


def test_verifier_attack_fabricated_grant_is_rejected():
    """The verifier's v1 bypass: allow + fake license + javascript: URL +
    self-authored statement with a self-consistent hash must fail."""
    import hashlib

    stmt = "I hereby grant myself all rights."
    blob = f"fetched_from: javascript:evil\nfetched_at: 2026-09-19\n---\n{stmt}\n".encode()
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path

        base = Path(d)
        (base / "ev.txt").write_bytes(blob)
        doc = {"sources": [{
            "id": "attack",
            "url": "javascript:evil",
            "license": "totally fake",
            "transformation_permission": True,
            "decision": "allow",
            "statement": stmt,
            "statement_source_url": "javascript:evil",
            "evidence_path": "ev.txt",
            "evidence_sha256": hashlib.sha256(blob).hexdigest(),
        }]}
        problems = rights_audit.validate_sources(doc, base)
    assert any("url is not an https URL" in p for p in problems)
    assert any("outside the policy set" in p for p in problems)
    assert any("statement_source_url is not https" in p for p in problems)
