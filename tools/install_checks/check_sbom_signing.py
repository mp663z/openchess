"""T0034: SBOM/signing check - manifest integrity + fail-closed generation.

Good mode: tools/component_inventory.py regenerates the SBOM and it matches
the committed docs/component-inventory.json EXACTLY (staleness = drift =
fail), and release.yml carries the artifact-attestation step (signing).
Violation mode (fault injection against the real data): (a) a linkage
classification file with one library entry removed - generation must fail
closed naming the unclassified library; (b) a tampered committed manifest
(one license field flipped) must NOT match regeneration.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import yaml

from tools import component_inventory as ci
from tools.install_checks import CheckError

CHECK_ID = "T0034"
ROOT = Path(__file__).resolve().parent.parent.parent


def _regen_matches_committed() -> bool:
    committed = json.loads(ci.MANIFEST.read_text())
    return ci.generate() == committed


def run(mode: str) -> None:
    if mode == "good":
        if not _regen_matches_committed():
            raise CheckError("committed SBOM does not regenerate exactly")
        release = (ROOT / ".github/workflows/release.yml").read_text()
        if "attest" not in release:
            raise CheckError("release.yml lost its artifact-attestation (signing) step")
        return
    uncaught = []
    # (a) a library dropped from the linkage classification must fail closed
    link = yaml.safe_load(ci.LINKAGE.read_text())
    dropped = sorted(link["components"])[0]
    del link["components"][dropped]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(link, f)
        patched = Path(f.name)
    original = ci.LINKAGE
    try:
        ci.LINKAGE = patched
        try:
            ci.generate()
            uncaught.append(f"unclassified library {dropped} did NOT fail generation")
        except RuntimeError as e:
            if dropped not in str(e):
                uncaught.append(f"failure did not name the unclassified library: {e}")
    finally:
        ci.LINKAGE = original
    # (b) tampered manifest must not match regeneration
    tampered = json.loads(ci.MANIFEST.read_text())
    tampered["libraries"][0]["license"] = "WARRANTY-DISCLAIMED-9.9"
    if ci.generate() == tampered:
        uncaught.append("tampered SBOM still matched regeneration")
    if uncaught:
        return  # harness FAILS: an SBOM integrity violation escaped
    raise CheckError("all seeded SBOM violations caught")
