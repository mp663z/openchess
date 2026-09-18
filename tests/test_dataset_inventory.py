"""Auto-verification for T2710 (inventory), T2711 (rights), T2712 (pins)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INV = yaml.safe_load((ROOT / "data/datasets/inventory.yaml").read_text())
RIGHTS = yaml.safe_load((ROOT / "data/datasets/rights-manifest.yaml").read_text())
PINS = yaml.safe_load((ROOT / "data/datasets/snapshot-pins.yaml").read_text())

REQUIRED_KINDS = {"games", "puzzles", "evaluations", "broadcasts", "elite_games"}


def test_inventory_covers_required_datasets():
    kinds = {d["kind"] for d in INV["datasets"]}
    assert kinds >= REQUIRED_KINDS


def test_inventory_fields_complete_and_https():
    for d in INV["datasets"]:
        assert d["release_cadence"], d["id"]
        assert d["compression"] is not None or d["kind"] == "broadcasts"
        assert d["checksum_mechanism"]
        for key in ("landing_url",):
            assert d[key].startswith("https://"), d[key]
        for key in ("download_url", "list_url", "api_url"):
            if key in d:
                assert d[key].startswith("https://"), d[key]


def test_every_dataset_has_rights_entry():
    inv_ids = {d["id"] for d in INV["datasets"]}
    rights_ids = {r["dataset"] for r in RIGHTS["rights"]}
    assert inv_ids == rights_ids


def test_rights_fail_closed_on_missing_terms():
    for r in RIGHTS["rights"]:
        if r["license"] in ("unknown", "") or not r["license_statement"]:
            assert r["decision"] == "fail_closed", r["dataset"]
            assert not r["transformation_permission"]
        if r["license"].startswith("CC0"):
            assert r["decision"] in ("allow", "allow_with_flag")


def test_pins_have_no_latest_aliases():
    for p in PINS["pins"]:
        assert "latest" not in p["url"]
        assert p["snapshot"] not in ("latest", "")
        assert isinstance(p["expected_bytes"], int) and p["expected_bytes"] > 0
        assert p["sha256"], "sha256 must be present (pending-measurement allowed pre-acquisition)"


def test_measured_sha256_is_wellformed_when_present():
    for p in PINS["pins"]:
        if p["sha256"] != "pending-measurement":
            assert len(p["sha256"]) == 64
            int(p["sha256"], 16)
