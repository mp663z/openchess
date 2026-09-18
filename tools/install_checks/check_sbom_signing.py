"""T0034: SBOM/signing check - manifest integrity + REAL attestation wiring.

SBOM: the committed docs/component-inventory.json must regenerate EXACTLY
via tools/component_inventory.py (staleness = drift = fail), and generation
must fail closed on an unclassified library (fault injection against the
real linkage classification) and on a tampered manifest.

Signing: release.yml is parsed as YAML and must carry keyless Sigstore
attestation, not a job merely NAMED attest: OIDC permissions
(id-token: write + attestations: write), an actions/attest-* step whose
subject-path covers the SBOM, the release lock and the checksums file, and
a release step attaching the same artifacts. Seeded violations: attestation
step removed, SBOM dropped from subjects, OIDC permission downgraded,
checksums detached from the release - each must be caught.
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
RELEASE_YML = ROOT / ".github/workflows/release.yml"
REQUIRED_SUBJECTS = {
    "docs/component-inventory.json",
    "data/release-lock.json",
    "checksums.sha256",
}


def _workflow_problems(wf: dict) -> list[str]:
    problems: list[str] = []
    perms = wf.get("permissions") or {}
    for perm in ("id-token", "attestations"):
        if perms.get(perm) != "write":
            problems.append(f"release.yml: permissions.{perm} != write")
    steps = [
        s
        for job in (wf.get("jobs") or {}).values()
        for s in (job.get("steps") or [])
    ]
    attest = [
        s for s in steps
        if str(s.get("uses", "")).startswith("actions/attest")
    ]
    if not attest:
        problems.append("release.yml: no actions/attest-* step (job name is not signing)")
    else:
        subjects = set()
        for s in attest:
            subjects |= set(str((s.get("with") or {}).get("subject-path", "")).split())
        missing = REQUIRED_SUBJECTS - subjects
        if missing:
            problems.append(f"release.yml: attestation subjects missing {sorted(missing)}")
    release_runs = [
        str(s.get("run", "")) for s in steps
        if "gh release create" in str(s.get("run", ""))
    ]
    if not release_runs:
        problems.append("release.yml: no gh release create step")
    for artifact in REQUIRED_SUBJECTS:
        if not any(artifact in r for r in release_runs):
            problems.append(f"release.yml: {artifact} not attached to the release")
    return problems


def run(mode: str) -> None:
    if mode == "good":
        if ci.generate() != json.loads(ci.MANIFEST.read_text()):
            raise CheckError("committed SBOM does not regenerate exactly")
        problems = _workflow_problems(yaml.safe_load(RELEASE_YML.read_text()))
        if problems:
            raise CheckError(f"release.yml attestation: {problems}")
        return
    uncaught = []
    # SBOM fault injection against the real data
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
        patched.unlink(missing_ok=True)
    tampered = json.loads(ci.MANIFEST.read_text())
    tampered["libraries"][0]["license"] = "WARRANTY-DISCLAIMED-9.9"
    if ci.generate() == tampered:
        uncaught.append("tampered SBOM still matched regeneration")
    # signing violations: mutate the REAL parsed workflow
    wf = yaml.safe_load(RELEASE_YML.read_text())

    def _strip_attest(w):
        for job in w["jobs"].values():
            job["steps"] = [
                s for s in job["steps"]
                if not str(s.get("uses", "")).startswith("actions/attest")
            ]
        return w

    def _drop_subject(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if str(s.get("uses", "")).startswith("actions/attest"):
                    s["with"]["subject-path"] = "checksums.sha256"
        return w

    def _downgrade_perms(w):
        w["permissions"] = {"contents": "write"}
        return w

    def _detach_checksums(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if "gh release create" in str(s.get("run", "")):
                    s["run"] = 'gh release create "$GITHUB_REF_NAME" --generate-notes'
        return w

    for name, mutate in {
        "attestation step removed": _strip_attest,
        "SBOM dropped from attestation subjects": _drop_subject,
        "OIDC permissions downgraded": _downgrade_perms,
        "checksums detached from release": _detach_checksums,
    }.items():
        problems = _workflow_problems(mutate(json.loads(json.dumps(wf))))
        if not problems:
            uncaught.append(f"workflow violation NOT caught: {name}")
    if uncaught:
        return  # harness FAILS: an SBOM/signing violation escaped
    raise CheckError("all seeded SBOM/signing violations caught")
