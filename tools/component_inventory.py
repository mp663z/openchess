"""T3655 v3 - Release SBOM + data/model manifest generator.

Env-independent: libraries come from the locked release resolution
(data/release-lock.json, produced by tools/lock_release.py from PyPI metadata
with per-target PEP 508 marker evaluation), never from the ambient
interpreter. Every library carries exact per-target versions and artifact
hashes. Model weights and bundled assets are discovered from
packaging/asset manifests, not hardcoded. Staleness = inputs (requirements +
dataset pins + lock) changed since generation; tests byte-compare the full
regenerated manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "data/datasets/snapshot-pins.yaml"
LOCK = ROOT / "data/release-lock.json"
LINKAGE = ROOT / "data/linkage-classification.yaml"
MANIFEST = ROOT / "docs/component-inventory.json"

# T3656: allowed linkage/deployment/distribution classes.
LINKAGE_CLASSES = ("subprocess", "dynamic_link", "static_link", "source_inclusion", "data")
DEPLOYMENT_CLASSES = ("ci_only", "server_only", "cli_tooling", "browser_bundle")
DISTRIBUTION_CLASSES = ("not_distributed", "distributed_with_app")

# Directories whose contents ship inside the release artifact. Any file found
# under one is a bundled asset; model weights are assets under a model dir.
ASSET_DIRS = ("assets", "static")
MODEL_DIRS = ("models", "weights")


def _inputs_sha256() -> str:
    """Hash of the manifest's inputs: requirement files + dataset pins + lock."""
    h = hashlib.sha256()
    for f in sorted(ROOT.glob("requirements*.txt")) + [PINS, LOCK, LINKAGE]:
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _discover_files(dirs: tuple[str, ...]) -> list[dict]:
    found = []
    for d in dirs:
        base = ROOT / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                found.append(
                    {
                        "name": f.name,
                        "path": str(f.relative_to(ROOT)),
                        "sha256": _sha256_file(f),
                        "bytes": f.stat().st_size,
                        "origin": "repo",
                        "bundled": True,
                    }
                )
    return found


def _classifications() -> dict[str, dict]:
    raw = yaml.safe_load(LINKAGE.read_text())
    out = {}
    for name, c in raw["components"].items():
        for field, allowed in (
            ("linkage", LINKAGE_CLASSES),
            ("deployment", DEPLOYMENT_CLASSES),
            ("distribution", DISTRIBUTION_CLASSES),
        ):
            if c.get(field) not in allowed:
                raise RuntimeError(f"{name}: {field}={c.get(field)!r} not in {allowed}")
        out[name] = {k: c[k] for k in ("linkage", "deployment", "distribution")}
    return out


def generate() -> dict:
    lock = json.loads(LOCK.read_text())
    classes = _classifications()
    by_name: dict[str, dict] = {}
    for target_name, target in lock["release_targets"].items():
        for a in target["artifacts"]:
            entry = by_name.setdefault(
                a["name"],
                {
                    "name": a["name"],
                    "requirement": a.get("requirement"),
                    "license": a["license"],
                    "origin": a["origin"],
                    "direct": a["direct"],
                    "targets": {},
                },
            )
            if entry["license"] != a["license"]:
                raise RuntimeError(f"license differs across targets for {a['name']}")
            entry["targets"][target_name] = {
                "version": a["version"],
                "filename": a["filename"],
                "sha256": a["sha256"],
                "url": a["url"],
            }
    for entry in by_name.values():
        cls = classes.get(entry["name"])
        if cls is None:
            raise RuntimeError(
                f"no linkage classification for library {entry['name']} - "
                "add it to data/linkage-classification.yaml"
            )
        entry.update(cls)
    libraries = [
        by_name[n] for n in sorted(by_name, key=lambda n: (not by_name[n]["direct"], n.lower()))
    ]
    pins = yaml.safe_load(PINS.read_text())
    datasets = [
        {
            "name": p["dataset"],
            "snapshot": p["snapshot"],
            "version": p["snapshot"],
            "sha256": p["sha256"],
            "bytes": p["expected_bytes"],
            "origin": p["url"],
            "license": "CC0",
            "bundled": False,
        }
        for p in pins["pins"]
    ]
    for ds in datasets:
        cls = classes.get(ds["name"])
        if cls is None:
            raise RuntimeError(
                f"no linkage classification for dataset {ds['name']} - "
                "add it to data/linkage-classification.yaml"
            )
        ds.update(cls)
    engines = [
        {
            "name": "stockfish",
            "version": "19",
            "origin": "https://stockfishchess.org/",
            "license": "GPL-3.0",
            "bundled": False,
            "note": "external process, invoked over UCI; not linked, not distributed",
            **classes["stockfish"],
        }
    ]
    return {
        "schema_version": 4,
        "generated_by": "tools/component_inventory.py",
        "inputs_sha256": _inputs_sha256(),
        "libraries": libraries,
        "engines": engines,
        "model_weights": _discover_files(MODEL_DIRS),
        "datasets": datasets,
        "bundled_assets": _discover_files(ASSET_DIRS),
    }


if __name__ == "__main__":
    inv = generate()
    MANIFEST.write_text(json.dumps(inv, indent=2) + "\n")
    libs = inv["libraries"]
    n_direct = sum(1 for lib in libs if lib["direct"])
    print(f"libraries: {len(libs)} (direct: {n_direct}), datasets: {len(inv['datasets'])}, "
          f"weights: {len(inv['model_weights'])}, assets: {len(inv['bundled_assets'])}")
