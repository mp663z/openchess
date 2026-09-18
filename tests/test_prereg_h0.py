"""T2289: preregistration structure and decision-rule consistency."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PREREG = yaml.safe_load((ROOT / "validation/prereg-h0.yaml").read_text())


def test_registered_before_data():
    reg = PREREG["registration"]
    assert reg["registered_before_data_collection"] is True
    assert reg["status"] == "registered"


def test_h0_precedes_h1():
    assert "precedes H1" in PREREG["hypothesis"]["ordering"]


def test_rubric_covers_required_classes():
    ids = {c["id"] for c in PREREG["instrument"]["classification_rubric"]}
    assert ids == {"maintained", "scattered", "course-bound", "head-only", "absent"}
    importable = {c["id"] for c in PREREG["instrument"]["classification_rubric"] if c["importable"]}
    assert importable == {"maintained", "scattered", "course-bound"}


def test_decision_rule_threshold_and_paths():
    rule = PREREG["decision_rule"]
    assert "40%" in rule["threshold"]
    assert "MAINTENANCE" in rule["pass"]
    assert "BUILDING" in rule["fail"]
    assert "game history" in rule["note"]


def test_amendments_appended_not_rewritten():
    assert PREREG["amendments"] == []


def test_external_timestamp_evidence():
    ev = PREREG["registration"]["timestamp_evidence"]
    assert ev["kind"] == "external_registry_commit"
    assert len(ev["commit_sha"]) == 40
    assert ev["registry_recorded_at"]
    assert ev["h1_artifact_present_at_registration"] is False
