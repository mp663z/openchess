"""T3655 v2: SBOM is generated from a locked release resolution, exact-compared."""

import contextlib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs/component-inventory.json"
LOCK = ROOT / "data" / "release-lock.json"


def _fresh() -> dict:
    import tools.component_inventory as ci

    return ci.generate()


def test_committed_manifest_is_current_with_inputs():
    """Staleness = inputs changed since generation (env-independent)."""
    import tools.component_inventory as ci

    committed = json.loads(MANIFEST.read_text())
    assert committed["inputs_sha256"] == ci._inputs_sha256(), (
        "manifest is stale - re-run tools/component_inventory.py "
        "(and tools/lock_release.py if requirements changed)"
    )


def test_committed_manifest_regenerates_exactly():
    """The locked resolution makes the whole manifest byte-reproducible."""
    committed = json.loads(MANIFEST.read_text())
    fresh = _fresh()
    assert committed == fresh, (
        "committed manifest differs from regeneration - re-run "
        "tools/component_inventory.py"
    )


def test_libraries_come_from_lock_not_environment():
    """Every lock entry appears in the manifest; nothing ambient leaks in."""
    lock = json.loads(LOCK.read_text())
    committed = json.loads(MANIFEST.read_text())
    lock_names = {lib["name"] for lib in lock["libraries"]}
    manifest_names = {lib["name"] for lib in committed["libraries"]}
    assert manifest_names == lock_names
    assert lock["libraries"], "lock is empty - run tools/lock_release.py"


def test_lock_is_fingerprinted_into_inputs():
    """Changing the lock without regenerating the manifest fails staleness."""
    import tools.component_inventory as ci

    h = hashlib.sha256()
    for f in sorted(ROOT.glob("requirements*.txt")) + [ci.PINS, LOCK]:
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    committed = json.loads(MANIFEST.read_text())
    assert committed["inputs_sha256"] == h.hexdigest()


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
    # Discovered (not hardcoded): lists may be empty only because no such
    # files exist under the declared asset/model dirs yet.
    assert isinstance(inv["model_weights"], list)
    assert isinstance(inv["bundled_assets"], list)


def test_discovery_picks_up_weights_and_assets(tmp_path, monkeypatch):
    """Discovery must find files under the declared dirs (fail-closed vs hardcode)."""
    import tools.component_inventory as ci

    for d, key in ((ci.MODEL_DIRS[0], "model_weights"), (ci.ASSET_DIRS[0], "bundled_assets")):
        target = ROOT / d
        target.mkdir(parents=True, exist_ok=True)
        probe = target / "probe.bin"
        probe.write_bytes(b"probe")
        try:
            found = ci.generate()[key]
            assert any(
                f["path"].endswith("probe.bin") and len(f["sha256"]) == 64 for f in found
            ), key
        finally:
            probe.unlink()
            with contextlib.suppress(OSError):
                target.rmdir()
