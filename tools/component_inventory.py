"""T3655 - Release SBOM + data/model manifest generator.

Lists every direct and transitive library, external binary/engine, model
weight, dataset, and bundled asset with version/hash/origin/license.
Generated, never hand-maintained; tests regenerate and compare.
"""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "data/datasets/snapshot-pins.yaml"


def _requirements() -> dict[str, str]:
    deps = {}
    for req in sorted(ROOT.glob("requirements*.txt")):
        for line in req.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                name = line.split(">=")[0].split("==")[0].strip()
                deps[name] = line
    return deps


# Pre-PEP-639 packages whose PyPI page states the license but whose installed
# metadata omits it. Value verified against the project's PyPI page 2026-09-19.
LICENSE_OVERRIDES = {"colorama": "BSD-3-Clause"}


def _license_of(dist) -> str:
    name = (dist.metadata.get("Name") or "").lower()
    if name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[name]
    expr = (dist.metadata.get("License-Expression") or "").strip()
    if expr:
        return expr
    lic = (dist.metadata.get("License") or "").strip()
    if lic and lic.lower() != "unknown":
        return lic.splitlines()[0]
    for c in dist.metadata.get_all("Classifier") or []:
        if c.startswith("License ::"):
            return c.rsplit("::", 1)[1].strip()
    return "UNVERIFIED"


def _library_entry(name: str, requirement: str | None, direct: bool) -> dict:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        # Conditional dependency for another platform (e.g. colorama on win32):
        # not installed here; license resolved via the curated override map.
        key = name.lower().replace("_", "-")
        return {
            "name": name, "requirement": requirement,
            "version": "n/a (conditional, not installed on this platform)",
            "license": LICENSE_OVERRIDES.get(key, "UNVERIFIED"),
            "origin": "PyPI", "direct": direct,
        }
    return {
        "name": name,
        "requirement": requirement,
        "version": dist.version,
        "license": _license_of(dist),
        "origin": "PyPI",
        "direct": direct,
    }


def _transitive_closure(direct: set[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(direct)
    while stack:
        name = stack.pop().lower().replace("_", "-")
        if name in seen:
            continue
        seen.add(name)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        for req in dist.requires or []:
            name_part, _, marker = req.partition(";")
            if "extra ==" in marker:  # dev/optional extras are not shipped runtime deps
                continue
            dep = name_part.split(" ")[0].split(">")[0].split("=")[0].split("<")[0].strip()
            dep = dep.rstrip(")]").strip()
            if dep:
                stack.append(dep)
    return seen


def generate() -> dict:
    direct = _requirements()
    closure = _transitive_closure(set(direct))
    libraries = [_library_entry(n, direct.get(n), True) for n in sorted(direct)]
    libraries += [
        _library_entry(n, None, False)
        for n in sorted(closure)
        if n not in {d.lower().replace("_", "-") for d in direct}
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
        "schema_version": 1,
        "generated_by": "tools/component_inventory.py",
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
        "model_weights": [],
        "datasets": datasets,
        "bundled_assets": [],
    }


if __name__ == "__main__":
    inv = generate()
    out = ROOT / "docs/component-inventory.json"
    out.write_text(json.dumps(inv, indent=2) + "\n")
    libs = inv["libraries"]
    n_direct = sum(1 for lib in libs if lib["direct"])
    print(f"libraries: {len(libs)} (direct: {n_direct}), datasets: {len(inv['datasets'])}")
