"""T3655 v2 - Release SBOM + data/model manifest generator.

Env-independent: libraries come from the locked release resolution
(data/release-lock.json, produced by tools/lock_release.py from the canonical
environment), never from the ambient interpreter. Model weights and bundled
assets are discovered from packaging/asset manifests, not hardcoded.
Staleness = inputs (requirements + dataset pins + lock) changed since
generation; the staleness test also compares the exact regenerated library set.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "data/datasets/snapshot-pins.yaml"
LOCK = ROOT / "data/release-lock.json"
MANIFEST = ROOT / "docs/component-inventory.json"

# Directories whose contents ship inside the release artifact. Any file found
# under one is a bundled asset; model weights are assets under a model dir.
ASSET_DIRS = ("assets", "static")
MODEL_DIRS = ("models", "weights")


def _inputs_sha256() -> str:
    """Hash of the manifest's inputs: requirement files + dataset pins + lock."""
    h = hashlib.sha256()
    for f in sorted(ROOT.glob("requirements*.txt")) + [PINS, LOCK]:
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


def generate() -> dict:
    lock = json.loads(LOCK.read_text())
    libraries = [
        {
            "name": lib["name"],
            "requirement": lib.get("requirement"),
            "version": lib["version"] or "n/a (conditional, not installed at lock time)",
            "license": lib["license"],
            "origin": lib["origin"],
            "direct": lib["direct"],
        }
        for lib in sorted(lock["libraries"], key=lambda x: (not x["direct"], x["name"].lower()))
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
    return {
        "schema_version": 2,
        "generated_by": "tools/component_inventory.py",
        "inputs_sha256": _inputs_sha256(),
        "libraries": libraries,
        "engines": [
            {
                "name": "stockfish",
                "version": "19",
                "origin": "https://stockfishchess.org/",
                "license": "GPL-3.0",
                "bundled": False,
                "note": "external process, invoked over UCI; not linked, not distributed",
            }
        ],
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
