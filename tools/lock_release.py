"""T3655 v2 - lock the release dependency resolution.

Captures the exact resolved dependency set (direct + transitive closure) with
versions and licenses from the canonical environment into data/release-lock.json.
Fail-closed: any dependency whose license cannot be determined aborts the lock
unless it is covered by the curated LICENSE_OVERRIDES map (verified against the
project's PyPI page). Re-run this whenever requirements*.txt change; the SBOM
staleness test hashes requirements + pins + this lock.
"""

from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "data" / "release-lock.json"

# Pre-PEP-639 packages whose PyPI page states the license but whose installed
# metadata omits it. Values verified against each project's PyPI page.
LICENSE_OVERRIDES = {"colorama": "BSD-3-Clause"}

# Canonical SPDX normalization for legacy/classifier license strings.
_LICENSE_NORMALIZE = {
    "mit license": "MIT",
    "apache software license": "Apache-2.0",
    "bsd license": "BSD-3-Clause",
    "the mit license (mit)": "MIT",
    "apache license 2.0": "Apache-2.0",
}


def _normalize_license(lic: str) -> str:
    return _LICENSE_NORMALIZE.get(lic.strip().lower(), lic.strip())


def _license_of(dist) -> str:
    name = (dist.metadata.get("Name") or "").lower()
    if name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[name]
    expr = (dist.metadata.get("License-Expression") or "").strip()
    if expr:
        return _normalize_license(expr)
    lic = (dist.metadata.get("License") or "").strip()
    if lic and lic.lower() != "unknown":
        return _normalize_license(lic.splitlines()[0])
    for c in dist.metadata.get_all("Classifier") or []:
        if c.startswith("License ::"):
            return _normalize_license(c.rsplit("::", 1)[1].strip())
    return "UNVERIFIED"


def _requirements() -> dict[str, str]:
    deps = {}
    for req in sorted(ROOT.glob("requirements*.txt")):
        for line in req.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                name = line.split(">=")[0].split("==")[0].split(";")[0].strip()
                deps[name] = line
    return deps


def _closure(direct: set[str]) -> set[str]:
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
            if "extra ==" in marker:
                continue
            dep = name_part.split(" ")[0].split(">")[0].split("=")[0].split("<")[0].strip()
            dep = dep.rstrip(")]").strip()
            if dep:
                stack.append(dep)
    return seen


def lock() -> dict:
    direct = _requirements()
    closure = _closure(set(direct))
    entries = []
    unverified = []
    for name in sorted(closure, key=str.lower):
        key = name.lower().replace("_", "-")
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            # Conditional dependency for another platform (e.g. colorama win32):
            # record with the curated license, no installed version.
            lic = LICENSE_OVERRIDES.get(key, "UNVERIFIED")
            entries.append({
                "name": name,
                "version": None,
                "license": lic,
                "origin": "PyPI",
                "direct": key in {d.lower().replace("_", "-") for d in direct},
                "requirement": direct.get(name),
            })
            if lic == "UNVERIFIED":
                unverified.append(name)
            continue
        lic = _license_of(dist)
        entries.append({
            "name": dist.metadata.get("Name") or name,
            "version": dist.version,
            "license": lic,
            "origin": "PyPI",
            "direct": key in {d.lower().replace("_", "-") for d in direct},
            "requirement": direct.get(name),
        })
        if lic == "UNVERIFIED":
            unverified.append(name)
    if unverified:
        print(f"FAIL-CLOSED: unresolved licenses: {', '.join(unverified)}", file=sys.stderr)
        sys.exit(1)
    return {"schema_version": 1, "generated_by": "tools/lock_release.py", "libraries": entries}


if __name__ == "__main__":
    data = lock()
    LOCK.write_text(json.dumps(data, indent=2) + "\n")
    libs = data["libraries"]
    print(f"locked {len(libs)} libraries ({sum(1 for x in libs if x['direct'])} direct)")
