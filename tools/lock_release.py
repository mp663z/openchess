"""T3655 v3 - locked release dependency resolution with artifact hashes.

pip cannot resolve for a foreign target: its resolver evaluates PEP 508
environment markers against the RUNNING interpreter, so `pip download
--python-version 3.12 --platform win_amd64` still omits win32-only deps and
keeps py<3.11-only deps (verified empirically, pip 22 and pip 26). This tool
therefore resolves directly against PyPI's JSON API:

- Release targets are explicit (RELEASE_TARGETS). Markers from each
  distribution's requires_dist are evaluated per target with packaging's
  Marker against a constructed target environment, so the closure is
  demonstrably that of the defined target.
- Every artifact is an exact wheel: name, version, filename, sha256 (PyPI
  digest), URL. No null or n/a versions.
- Licenses come from PyPI metadata, SPDX-normalized; unknown licenses are
  fail-closed unless covered by the curated LICENSE_OVERRIDES map.

--check (CI): re-resolve every target and byte-compare against the committed
data/release-lock.json, so requirement edits and upstream drift fail the build.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "data" / "release-lock.json"

RELEASE_TARGETS = {
    "cp312-manylinux_x86_64": {
        "python": "3.12",
        "env": {
            "implementation_name": "cpython",
            "implementation_version": "3.12.0",
            "os_name": "posix",
            "platform_machine": "x86_64",
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_system": "Linux",
            "platform_version": "",
            "python_full_version": "3.12.0",
            "python_version": "3.12",
            "sys_platform": "linux",
        },
        "platforms": ("manylinux", "x86_64"),  # any manylinux* x86_64 tag
    },
    "cp312-win_amd64": {
        "python": "3.12",
        "env": {
            "implementation_name": "cpython",
            "implementation_version": "3.12.0",
            "os_name": "nt",
            "platform_machine": "AMD64",
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_system": "Windows",
            "platform_version": "",
            "python_full_version": "3.12.0",
            "python_version": "3.12",
            "sys_platform": "win32",
        },
        "platforms": ("win",),
    },
}

# Pre-PEP-639 packages whose PyPI metadata omits the license. Values verified
# against the project's PyPI page, or its own LICENSE file when the page is
# silent (mypy-extensions: MIT, github.com/python/mypy_extensions LICENSE).
LICENSE_OVERRIDES = {"colorama": "BSD-3-Clause", "mypy-extensions": "MIT"}

_LICENSE_NORMALIZE = {
    "mit license": "MIT",
    "apache software license": "Apache-2.0",
    "bsd license": "BSD-3-Clause",
    "the mit license (mit)": "MIT",
    "apache license 2.0": "Apache-2.0",
}

_PYPI = "https://pypi.org/pypi"


def _get_json(url: str, attempts: int = 4) -> dict:
    """Fetch JSON with retry + exponential backoff on transient failures
    (network errors, 429, 5xx). 4xx responses other than 429 are permanent
    and raise immediately."""
    delay = 1.0
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": "release-lock/3"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise
            if attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def _normalize_license(lic: str) -> str:
    return _LICENSE_NORMALIZE.get(lic.strip().lower(), lic.strip())


def _license_of(name: str, info: dict) -> str:
    key = name.lower().replace("_", "-")
    if key in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[key]
    expr = (info.get("license_expression") or "").strip()
    if expr:
        return _normalize_license(expr)
    lic = (info.get("license") or "").strip()
    if lic and lic.lower() != "unknown" and "\n" not in lic:
        return _normalize_license(lic)
    for c in info.get("classifiers") or []:
        if c.startswith("License ::"):
            return _normalize_license(c.rsplit("::", 1)[1].strip())
    return "UNVERIFIED"


def _requirements() -> dict[str, Requirement]:
    deps: dict[str, Requirement] = {}
    for req_file in sorted(ROOT.glob("requirements*.txt")):
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                r = Requirement(line)
                deps[r.name.lower().replace("_", "-")] = r
    return deps


def _tag_ok(py: str, abi: str, plat: str, target: dict) -> bool:
    if py == "cp312":
        if abi not in ("cp312", "abi3", "none"):
            return False
    elif re.fullmatch(r"py3(\d*)", py):
        minor = py[3:]
        if minor and int(minor) > 12:
            return False
        if abi != "none":
            return False
    else:
        return False
    os_kind = target["platforms"][0]
    if plat == "any":
        return True
    if os_kind == "manylinux":
        return plat.startswith("manylinux") and plat.endswith("x86_64")
    # 64-bit Windows target: win_amd64 wheels only. A win32 wheel is the wrong
    # architecture for the declared target and must never be locked for it.
    return plat == "win_amd64"


def _pick_artifact(files: list[dict], target: dict) -> dict | None:
    for f in files:
        if f.get("yanked") or not f["filename"].endswith(".whl"):
            continue
        rp = f.get("requires_python") or ""
        if rp and Version(target["python"]) not in SpecifierSet(rp):
            continue
        try:
            _, _, _, tags = parse_wheel_filename(f["filename"])
        except Exception:
            continue
        for t in tags:
            if _tag_ok(t.interpreter, t.abi, t.platform, target):
                return f
    return None


def _resolve_target(target_name: str, target: dict) -> list[dict]:
    pending: dict[str, list[Requirement]] = {
        k: [r] for k, r in _requirements().items()
    }
    resolved: dict[str, dict] = {}
    direct_keys = set(pending)
    constraints: dict[str, list[Requirement]] = {
        k: list(v) for k, v in pending.items()
    }
    while pending:
        key = min(pending)
        reqs = pending.pop(key)
        if key in resolved:
            continue
        specs = SpecifierSet()
        for r in reqs:
            specs &= r.specifier
        index = _get_json(f"{_PYPI}/{key}/json")
        candidates = []
        for ver, files in index["releases"].items():
            try:
                v = Version(ver)
            except Exception:
                continue
            if v.is_prerelease or v not in specs:
                continue
            usable = [f for f in files if not f.get("yanked")]
            if not usable:
                continue
            pkg_rp = ""
            chosen = _pick_artifact(usable, target)
            if chosen is None:
                continue
            pkg_rp = chosen.get("requires_python") or ""
            if pkg_rp and Version(target["python"]) not in SpecifierSet(pkg_rp):
                continue
            candidates.append((v, chosen))
        if not candidates:
            raise RuntimeError(f"{target_name}: no compatible artifact for {key} ({specs})")
        version, artifact = max(candidates, key=lambda c: c[0])
        release = _get_json(f"{_PYPI}/{key}/{version}/json")
        info = release["info"]
        lic = _license_of(info["name"], info)
        resolved[key] = {
            "name": info["name"],
            "version": str(version),
            "filename": artifact["filename"],
            "sha256": artifact["digests"]["sha256"],
            "url": artifact["url"],
            "license": lic,
            "origin": "PyPI",
            "direct": key in direct_keys,
            "requirement": str(_requirements()[key]) if key in direct_keys else None,
        }
        for dep_str in info.get("requires_dist") or []:
            dep = Requirement(dep_str)
            if dep.marker is not None:
                if '"extra"' in str(dep.marker):
                    continue  # optional extras are not shipped runtime deps
                try:
                    if not dep.marker.evaluate(target["env"]):
                        continue
                except Exception:
                    continue
            dkey = dep.name.lower().replace("_", "-")
            constraints.setdefault(dkey, []).append(dep)
            if dkey not in resolved:
                pending.setdefault(dkey, []).append(dep)
    # Post-closure validation: the resolved version must satisfy EVERY
    # requirement edge accumulated for it, including ones discovered after
    # the package was first resolved. Fail-closed on conflict.
    for key, reqs in constraints.items():
        if key not in resolved:
            continue
        version = Version(resolved[key]["version"])
        for r in reqs:
            if version not in r.specifier:
                raise RuntimeError(
                    f"{target_name}: resolved {key}=={version} violates {r}"
                )
    return [resolved[k] for k in sorted(resolved)]


def lock() -> dict:
    targets = {}
    for target_name, spec in RELEASE_TARGETS.items():
        artifacts = _resolve_target(target_name, spec)
        unverified = [a["name"] for a in artifacts if a["license"] == "UNVERIFIED"]
        if unverified:
            print(
                f"FAIL-CLOSED: unresolved licenses on {target_name}: " + ", ".join(unverified),
                file=sys.stderr,
            )
            sys.exit(1)
        targets[target_name] = {
            "python": spec["python"],
            "artifacts": artifacts,
        }
    return {
        "schema_version": 3,
        "generated_by": "tools/lock_release.py (PyPI JSON resolver, per-target PEP 508 markers)",
        "release_targets": targets,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="re-resolve and byte-compare against the committed lock (CI)",
    )
    args = parser.parse_args()
    data = lock()
    rendered = json.dumps(data, indent=2) + "\n"
    if args.check:
        committed = LOCK.read_text() if LOCK.exists() else ""
        if committed != rendered:
            print("release lock is stale - re-run tools/lock_release.py", file=sys.stderr)
            sys.exit(1)
        print("release lock is current")
        return
    LOCK.write_text(rendered)
    n = {t: len(s["artifacts"]) for t, s in data["release_targets"].items()}
    print("locked:", {t: f"{c} artifacts" for t, c in n.items()})


if __name__ == "__main__":
    main()
