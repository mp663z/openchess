"""T0005: license boundary frozen - chess/local/BYOM AGPL; private side enumerated."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BOUNDARY = yaml.safe_load((ROOT / "licensing-boundary.yaml").read_text())

ALLOWED_PRIVATE = {
    "identity", "billing", "entitlements", "quota",
    "provider routing and provider keys", "abuse and admin", "ops",
}


def test_product_is_agpl():
    assert BOUNDARY["product_license"] == "AGPL-3.0-only"


def test_agpl_covers_chess_local_byom():
    covers = " ".join(BOUNDARY["agpl_covers"]).lower()
    for term in ("chess features", "local-first", "byom"):
        assert term in covers, term


def test_private_side_exactly_the_enumerated_set():
    assert set(BOUNDARY["private_side_only"]) == ALLOWED_PRIVATE


def test_default_is_agpl_and_every_repo_area_classified():
    assert "AGPL" in BOUNDARY["default_rule"]
    areas = BOUNDARY["repo_areas"]
    assert areas, "repo_areas must classify every top-level area"
    assert set(areas.values()) <= {"agpl", "private"}
    # Only the control plane may be private.
    private_areas = [k for k, v in areas.items() if v == "private"]
    assert private_areas == ["control-plane"]
    # Current code dirs are AGPL.
    for d in ("ingest", "backtest", "brand", "tools"):
        assert areas[d] == "agpl"
