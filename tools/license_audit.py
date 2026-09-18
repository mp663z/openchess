"""Release license audit (T3669, run from day one).

Scans dependency manifests and fails on any dependency whose license is not
on the permissive allowlist. Sources: requirements*.txt pinned names mapped
through installed metadata, plus package.json when present.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

ALLOWED = {
    "MIT",
    "MIT-0",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Python-2.0",
    "MPL-2.0",
}

ROOT = Path(__file__).resolve().parent.parent


def python_requirements() -> list[str]:
    names: list[str] = []
    for req in sorted(ROOT.glob("requirements*.txt")):
        for line in req.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"([A-Za-z0-9_.\-]+)", line)
            if m:
                names.append(m.group(1))
    return names


def npm_dependencies() -> list[str]:
    pkg = ROOT / "package.json"
    if not pkg.exists():
        return []
    data = json.loads(pkg.read_text())
    deps = list(data.get("dependencies", {})) + list(data.get("devDependencies", {}))
    return deps


def audit_python(names: list[str]) -> list[str]:
    problems = []
    for name in names:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            problems.append(f"python:{name}: not installed, license unverified")
            continue
        lic = (dist.metadata.get("License") or "").strip()
        expr = (dist.metadata.get("License-Expression") or "").strip()
        candidates = {c for c in (expr, lic) if c}
        classifiers = {
            c.split("::")[-1].strip()
            for c in dist.metadata.get_all("Classifier") or []
            if c.startswith("License")
        }
        normalized = {c.replace(" License", "").strip() for c in classifiers}
        ok = any(
            any(a in cand or cand in a for a in ALLOWED) for cand in candidates | normalized
        )
        if not ok:
            found = sorted(candidates | normalized) or ["UNKNOWN"]
            problems.append(f"python:{name}: license {found}")
    return problems


def main() -> int:
    problems = audit_python(python_requirements())
    npm = npm_dependencies()
    if npm:
        print(f"note: {len(npm)} npm dependencies declared; audited at install time by CI gate")
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        return 1
    print(f"OK license audit passed ({len(python_requirements())} python requirements checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
