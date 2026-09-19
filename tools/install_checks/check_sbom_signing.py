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


def _release_jobs(wf: dict) -> list[tuple[str, dict]]:
    """Jobs containing an EXECUTABLE gh release create step (a run line that
    starts with the command token - echo/comment mentions do not count)."""
    out = []
    for name, job in (wf.get("jobs") or {}).items():
        for s in job.get("steps") or []:
            lines = str(s.get("run", "")).splitlines()
            if any(line.strip().startswith("gh release create") for line in lines):
                out.append((name, job))
                break
    return out


def _workflow_problems(wf: dict) -> list[str]:
    problems: list[str] = []
    jobs = _release_jobs(wf)
    if not jobs:
        return ["release.yml: no job with an executable gh release create step"]
    for name, job in jobs:
        cond = str(job.get("if", "")).strip().lower()
        if cond in ("false", "!true", "0"):
            problems.append(f"release.yml: release job {name} is disabled (if: {cond})")
        # effective permissions: job overrides workflow
        perms = job.get("permissions") or wf.get("permissions") or {}
        for perm in ("id-token", "attestations"):
            if perms.get(perm) != "write":
                problems.append(
                    f"release.yml: job {name} permissions.{perm} != write"
                )
        steps = job.get("steps") or []

        def idx(pred, steps=steps):
            for i, s in enumerate(steps):
                if pred(s):
                    return i
            return None

        gen = idx(lambda s: "component_inventory" in str(s.get("run", "")))
        chk = idx(lambda s: "sha256sum" in str(s.get("run", ""))
                  and "checksums.sha256" in str(s.get("run", "")))
        att = idx(lambda s: str(s.get("uses", "")).startswith("actions/attest"))
        rel = idx(lambda s, steps=steps: any(
            line.strip().startswith("gh release create")
            for line in str(s.get("run", "")).splitlines()))
        for label, i in (("SBOM generation", gen), ("checksums", chk),
                         ("attestation", att), ("release", rel)):
            if i is None:
                problems.append(f"release.yml: job {name} lacks a {label} step")
        if None not in (gen, chk, att, rel) and not (gen < chk < att < rel):
            problems.append(
                f"release.yml: job {name} step order must be "
                "generate -> checksums -> attestation -> release"
            )
        if att is not None:
            subjects = set(
                str((steps[att].get("with") or {}).get("subject-path", "")).split()
            )
            missing = REQUIRED_SUBJECTS - subjects
            if missing:
                problems.append(
                    f"release.yml: attestation subjects missing {sorted(missing)}"
                )
        if rel is not None:
            run_text = str(steps[rel].get("run", ""))
            for artifact in REQUIRED_SUBJECTS:
                if artifact not in run_text:
                    problems.append(
                        f"release.yml: {artifact} not attached to the release"
                    )
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

    def _disable_job(w):
        for job in w["jobs"].values():
            job["if"] = "false"
        return w

    def _echo_release(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if "gh release create" in str(s.get("run", "")):
                    s["run"] = (
                        'echo "gh release create $GITHUB_REF_NAME'
                        " --generate-notes docs/component-inventory.json"
                        ' data/release-lock.json checksums.sha256"'
                    )
        return w

    def _split_attest(w):
        moved = []
        for job in w["jobs"].values():
            moved += [s for s in job["steps"]
                      if str(s.get("uses", "")).startswith("actions/attest")]
            job["steps"] = [s for s in job["steps"]
                            if not str(s.get("uses", "")).startswith("actions/attest")]
        w["jobs"]["other"] = {"runs-on": "ubuntu-latest", "steps": moved}
        return w

    for name, mutate in {
        "attestation step removed": _strip_attest,
        "SBOM dropped from attestation subjects": _drop_subject,
        "OIDC permissions downgraded": _downgrade_perms,
        "checksums detached from release": _detach_checksums,
        "release job disabled (if: false)": _disable_job,
        "release command echoed, not executed": _echo_release,
        "attestation split into an unrelated job": _split_attest,
    }.items():
        problems = _workflow_problems(mutate(json.loads(json.dumps(wf))))
        if not problems:
            uncaught.append(f"workflow violation NOT caught: {name}")
    if uncaught:
        return  # harness FAILS: an SBOM/signing violation escaped
    raise CheckError("all seeded SBOM/signing violations caught")
