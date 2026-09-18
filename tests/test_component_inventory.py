"""T3655: SBOM lists every library/engine/model/dataset/asset with version/hash/origin/license."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs/component-inventory.json"


def regenerate() -> dict:
    import tools.component_inventory as ci

    return ci.generate()


def test_committed_manifest_is_current_with_inputs():
    """Staleness = inputs changed since generation (env-independent). Library
    membership/versions/licenses legitimately vary with the installed
    environment (platform and python-version markers), so regenerability is
    checked against input hashes, not a byte-for-byte environment diff."""
    import tools.component_inventory as ci

    committed = json.loads(MANIFEST.read_text())
    assert committed["inputs_sha256"] == ci._inputs_sha256(), (
        "manifest is stale - re-run tools/component_inventory.py"
    )
    # The env-independent sections must regenerate identically.
    fresh = regenerate()
    for section in ("datasets", "engines", "model_weights", "bundled_assets"):
        assert committed[section] == fresh[section], section
    # Versions/licenses are environment-sourced but must be recorded, never blank.
    for lib in committed["libraries"]:
        assert lib.get("version"), lib["name"]
        assert lib.get("license") not in (None, "", "UNVERIFIED"), lib["name"]


def test_every_library_has_version_origin_license():
    inv = json.loads(MANIFEST.read_text())
    assert inv["libraries"], "no libraries listed"
    for lib in inv["libraries"]:
        for field in ("name", "version", "license", "origin", "direct"):
            assert lib.get(field) not in (None, ""), (lib["name"], field)
        assert lib["license"] != "UNVERIFIED", lib["name"]


def test_transitive_dependencies_present():
    inv = json.loads(MANIFEST.read_text())
    indirect = [lib for lib in inv["libraries"] if not lib["direct"]]
    assert indirect, "transitive closure missing"


def test_every_dataset_has_hash_origin_license():
    inv = json.loads(MANIFEST.read_text())
    assert inv["datasets"], "no datasets listed"
    for ds in inv["datasets"]:
        assert ds["sha256"] and len(ds["sha256"]) == 64, ds["name"]
        assert ds["origin"].startswith("https://"), ds["name"]
        assert ds["license"] == "CC0", ds["name"]


def test_engines_models_assets_sections_exist():
    inv = json.loads(MANIFEST.read_text())
    engines = inv["engines"]
    assert any(
        e["name"] == "stockfish" and e["license"] == "GPL-3.0" and not e["bundled"]
        for e in engines
    )
    assert inv["model_weights"] == []
    assert inv["bundled_assets"] == []
