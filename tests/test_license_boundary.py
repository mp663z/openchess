"""T0005: license boundary frozen - chess/local/BYOM AGPL; private side enumerated."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BOUNDARY = yaml.safe_load((ROOT / "licensing-boundary.yaml").read_text())

ALLOWED_PRIVATE = {
    "identity", "billing", "entitlements", "quota",
    "provider routing and provider keys", "abuse and admin", "ops",
}
PLANNED_AREAS = {"core", "app", "control-plane"}


def tracked_top_level_dirs() -> set[str]:
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return {f.split("/")[0] for f in files if "/" in f}


def test_product_is_agpl():
    assert BOUNDARY["product_license"] == "AGPL-3.0-or-later"


def test_agpl_covers_chess_local_byom():
    covers = " ".join(BOUNDARY["agpl_covers"]).lower()
    for term in ("chess features", "local-first", "byom"):
        assert term in covers, term


def test_private_side_exactly_the_enumerated_set():
    assert set(BOUNDARY["private_side_only"]) == ALLOWED_PRIVATE


def test_every_tracked_top_level_area_classified():
    areas = BOUNDARY["repo_areas"]
    tracked = tracked_top_level_dirs()
    missing = tracked - set(areas)
    assert not missing, f"tracked top-level dirs missing from manifest: {missing}"
    extra = set(areas) - tracked - PLANNED_AREAS
    assert not extra, f"manifest classifies nonexistent areas: {extra}"
    for d in tracked:
        assert str(areas[d]).startswith("agpl"), f"{d} must be AGPL (private = control-plane only)"


def test_only_control_plane_private_and_root_files_addressed():
    private_areas = [k for k, v in BOUNDARY["repo_areas"].items() if str(v).startswith("private")]
    assert private_areas == ["control-plane"]
    assert "root_files" in BOUNDARY and "agpl" in BOUNDARY["root_files"]
    assert "AGPL" in BOUNDARY["default_rule"]
