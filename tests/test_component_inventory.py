"""T3655 v3: SBOM from a locked, per-target release resolution with artifact hashes."""

import contextlib
import hashlib
import json
import re
from pathlib import Path

from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs/component-inventory.json"
LOCK = ROOT / "data" / "release-lock.json"
TARGETS = ("cp312-manylinux_x86_64", "cp312-win_amd64")


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
    assert committed == _fresh(), (
        "committed manifest differs from regeneration - re-run tools/component_inventory.py"
    )


def test_lock_covers_defined_release_targets():
    lock = json.loads(LOCK.read_text())
    assert set(lock["release_targets"]) == set(TARGETS)
    for name, target in lock["release_targets"].items():
        assert target["python"] == "3.12"
        assert target["artifacts"], f"empty closure for {name}"


def test_markers_evaluated_per_target():
    """The closures are demonstrably per-target, not running-env leakage."""
    lock = json.loads(LOCK.read_text())
    linux = {a["name"] for a in lock["release_targets"]["cp312-manylinux_x86_64"]["artifacts"]}
    win = {a["name"] for a in lock["release_targets"]["cp312-win_amd64"]["artifacts"]}
    assert "colorama" in win, "win32-only dep missing from the windows target"
    assert "colorama" not in linux, "win32-only dep leaked into the linux target"
    for py311_only in ("exceptiongroup", "tomli", "typing_extensions"):
        assert py311_only not in linux, f"{py311_only} is py<3.11-only; target is 3.12"
        assert py311_only not in win, f"{py311_only} is py<3.11-only; target is 3.12"


def test_every_artifact_is_exact_and_hashed():
    """No null/n/a versions anywhere; every artifact carries a real hash."""
    lock = json.loads(LOCK.read_text())
    for target_name, target in lock["release_targets"].items():
        for a in target["artifacts"]:
            assert a["version"] and "n/a" not in a["version"], (target_name, a["name"])
            assert len(a["sha256"]) == 64, (target_name, a["name"])
            assert a["filename"].endswith(".whl"), (target_name, a["name"])
            assert a["url"].startswith("https://files.pythonhosted.org/"), a["name"]
            assert a["license"] and a["license"] != "UNVERIFIED", a["name"]


def test_every_wheel_tag_matches_its_named_target():
    """Every locked wheel's parsed tags must be compatible with its target -
    independent of the tool's own matcher (a win32 wheel on a win_amd64 target
    is the class of bug this guards)."""
    lock = json.loads(LOCK.read_text())
    for target_name, target in lock["release_targets"].items():
        for a in target["artifacts"]:
            _, _, _, tags = parse_wheel_filename(a["filename"])
            assert tags, a["filename"]

            def tag_compatible(t, target_name=target_name) -> bool:
                py, abi, plat = t.interpreter, t.abi, t.platform
                if py.startswith("cp"):
                    if py != "cp312" or abi not in ("cp312", "abi3", "none"):
                        return False
                else:
                    if not re.fullmatch(r"py3(\d*)", py) or abi != "none":
                        return False
                if target_name.endswith("win_amd64"):
                    return plat in ("win_amd64", "any")  # never win32
                if "manylinux_x86_64" in target_name:
                    return plat == "any" or (
                        plat.startswith("manylinux") and plat.endswith("x86_64")
                    )
                raise AssertionError(f"untested target {target_name}")

            assert any(tag_compatible(t) for t in tags), (
                target_name,
                a["filename"],
            )
            # A 64-bit windows target must never lock a win32-only wheel.
            if target_name.endswith("win_amd64"):
                plats = {t.platform for t in tags}
                assert "win32" not in plats or "win_amd64" in plats, (
                    f"win32-only wheel on win_amd64 target: {a['filename']}"
                )


def test_libraries_come_from_lock_not_environment():
    """Every lock entry appears in the manifest; nothing ambient leaks in."""
    lock = json.loads(LOCK.read_text())
    committed = json.loads(MANIFEST.read_text())
    lock_names = {
        a["name"]
        for target in lock["release_targets"].values()
        for a in target["artifacts"]
    }
    manifest_names = {lib["name"] for lib in committed["libraries"]}
    assert manifest_names == lock_names


def test_manifest_versions_match_lock_exactly():
    lock = json.loads(LOCK.read_text())
    committed = json.loads(MANIFEST.read_text())
    libs = {lib["name"]: lib for lib in committed["libraries"]}
    for target_name, target in lock["release_targets"].items():
        for a in target["artifacts"]:
            t = libs[a["name"]]["targets"][target_name]
            assert t["version"] == a["version"], a["name"]
            assert t["sha256"] == a["sha256"], a["name"]


def test_lock_is_fingerprinted_into_inputs():
    """Changing the lock without regenerating the manifest fails staleness."""
    import tools.component_inventory as ci

    h = hashlib.sha256()
    for f in sorted(ROOT.glob("requirements*.txt")) + [ci.PINS, LOCK]:
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    committed = json.loads(MANIFEST.read_text())
    assert committed["inputs_sha256"] == h.hexdigest()


def test_every_library_has_origin_license_direct():
    inv = json.loads(MANIFEST.read_text())
    assert inv["libraries"], "no libraries listed"
    for lib in inv["libraries"]:
        for field in ("name", "license", "origin", "direct"):
            assert lib.get(field) not in (None, ""), (lib["name"], field)
        assert lib["license"] != "UNVERIFIED", lib["name"]
        assert lib["targets"], lib["name"]
        for t in lib["targets"].values():
            assert t["version"] and "n/a" not in t["version"], lib["name"]


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
    assert isinstance(inv["model_weights"], list)
    assert isinstance(inv["bundled_assets"], list)


def test_discovery_picks_up_weights_and_assets():
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
