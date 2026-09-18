"""T2229: rights manifest completeness and fail-closed discipline."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RIGHTS = yaml.safe_load((ROOT / "data/datasets/public-source-rights.yaml").read_text())
REQUIRED_SOURCES = {
    "lichess_standard_games",
    "lichess_puzzles",
    "lichess_evals",
    "twic",
    "lichess_broadcasts",
    "lichess_chess_openings",
    "lichess_public_studies",
    "user_own_cbh_files",
}


def test_required_sources_present():
    ids = {s["id"] for s in RIGHTS["sources"]}
    assert ids >= REQUIRED_SOURCES


def test_every_source_has_license_url_permission_checksum_field():
    for s in RIGHTS["sources"]:
        assert "license" in s and s["license"], s["id"]
        assert "url" in s, s["id"]
        assert "transformation_permission" in s, s["id"]
        assert "statement_sha256" in s, s["id"]  # null allowed, key required
        assert s["decision"] in ("allow", "allow_with_flag", "fail_closed")


def test_unknown_or_proprietary_fails_closed():
    for s in RIGHTS["sources"]:
        lic = str(s["license"]).lower()
        if "unknown" in lic or "none declared" in lic or "proprietary" in lic:
            assert s["decision"] == "fail_closed", s["id"]
            assert s["transformation_permission"] is False, s["id"]


def test_allow_decisions_have_statement_evidence():
    for s in RIGHTS["sources"]:
        if s["decision"] == "allow" and s["id"] != "user_own_cbh_files":
            assert s.get("statement_source_url"), s["id"]
