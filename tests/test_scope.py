"""T0001: scope freeze matches v5 section 11 (five product capabilities)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCOPE = yaml.safe_load((ROOT / "scope/v0.1.yaml").read_text())


def test_five_product_capabilities():
    names = [c["name"] for c in SCOPE["capabilities"]]
    assert len(names) == 5
    assert names == [
        "Import",
        "Search",
        "Personal memory",
        "Repertoire + Delta + Diagnosis",
        "Review + Training on the web/mobile habit surface",
    ]


def test_casual_one_connect_sync_in_scope():
    imp = SCOPE["capabilities"][0]
    assert "one-connect" in imp["detail"]
    assert "Lichess/Chess.com" in imp["detail"]


def test_explicit_deferrals_named():
    names = " ".join(d["name"] for d in SCOPE["deferrals"]).lower()
    for expected in (
        "team collaboration",
        "remote workers",
        "plugin marketplace",
        "cql",
        "provider marketplace",
        "visual agent canvas",
        "air-gapped packages",
        "native mobile",
        "beyond the casual one-connect flow",
        "cbh import",
    ):
        assert expected in names, expected


def test_deferrals_have_targets():
    for d in SCOPE["deferrals"]:
        assert d["deferred_to"], d["id"]
