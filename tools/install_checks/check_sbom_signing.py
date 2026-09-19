"""T0034: SBOM/signing check - manifest integrity + REAL attestation wiring.

SBOM: the committed docs/component-inventory.json must regenerate EXACTLY
via tools/component_inventory.py (staleness = drift = fail), and generation
must fail closed on an unclassified library (fault injection against the
real linkage classification) and on a tampered manifest.

Signing: release.yml is parsed as YAML and must carry keyless Sigstore
attestation, not a job merely NAMED attest: OIDC permissions
(id-token: write + attestations: write), the EXACT action
actions/attest-build-provenance@v2 (lookalikes like attest-not-real fail)
whose subject-path covers the SBOM, the release lock and the checksums
file, and a release step running the EXACT pinned gh release create
command attaching the same artifacts. Mandatory run/uses values are
enforced by exact structural equality (whitespace-normalized), not shell
semantics: echo prefixes, renamed commands (createx) and altered commands
all fail. Fail closed on conditionals:
ANY job-level if on the release job and ANY if on a mandatory
generation/checksum/attestation/release step is a violation (GitHub
expression syntax is not parsed), and the release step must be a single
command line (multi-line shell like "exit 0" before gh release create is a
bypass). Seeded violations: attestation step removed, SBOM dropped from
subjects, OIDC permission downgraded, checksums detached from the release,
job disabled via if:false and if:${{ false }}, attestation step gated via
if:${{ false }}, release command after exit 0 - each must be caught.
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


PINNED_GENERATION_RUN = "python tools/component_inventory.py"
PINNED_CHECKSUMS_RUN = (
    "sha256sum docs/component-inventory.json data/release-lock.json > checksums.sha256"
)
PINNED_ATTEST_USES = "actions/attest-build-provenance@v2"
PINNED_RELEASE_RUN = (
    'gh release create "$GITHUB_REF_NAME" --generate-notes '
    "docs/component-inventory.json data/release-lock.json checksums.sha256"
)


def _norm(value: object) -> str:
    """Whitespace-insensitive exact match (yaml block scalars add newlines)."""
    return " ".join(str(value).split())


def _step_idx(steps: list, key: str, exact: str) -> int | None:
    for i, s in enumerate(steps):
        if _norm(s.get(key, "")) == exact:
            return i
    return None


def _release_jobs(wf: dict) -> list[tuple[str, dict]]:
    """Jobs containing the EXACT pinned release command (no shell inference:
    echo prefixes, lookalike commands and multi-line wrappers do not match)."""
    return [
        (name, job)
        for name, job in (wf.get("jobs") or {}).items()
        if _step_idx(job.get("steps") or [], "run", PINNED_RELEASE_RUN) is not None
    ]


def _workflow_problems(wf: dict) -> list[str]:
    problems: list[str] = []
    jobs = _release_jobs(wf)
    if not jobs:
        return [
            "release.yml: no job running the exact pinned release command "
            "(echoed, renamed or altered commands do not count)"
        ]
    for name, job in jobs:
        if job.get("if") not in (None, ""):
            problems.append(
                f"release.yml: release job {name} carries a job-level if "
                f"({job.get('if')!r}) - fail closed: mandatory release jobs "
                "must be unconditional"
            )
        # effective permissions: job overrides workflow
        perms = job.get("permissions") or wf.get("permissions") or {}
        for perm in ("id-token", "attestations"):
            if perms.get(perm) != "write":
                problems.append(
                    f"release.yml: job {name} permissions.{perm} != write"
                )
        steps = job.get("steps") or []
        gen = _step_idx(steps, "run", PINNED_GENERATION_RUN)
        chk = _step_idx(steps, "run", PINNED_CHECKSUMS_RUN)
        att = _step_idx(steps, "uses", PINNED_ATTEST_USES)
        rel = _step_idx(steps, "run", PINNED_RELEASE_RUN)
        for label, i in (("SBOM generation", gen), ("checksums", chk),
                         ("attestation", att), ("release", rel)):
            if i is None:
                problems.append(
                    f"release.yml: job {name} lacks the exact pinned {label} step"
                )
            elif steps[i].get("if") not in (None, ""):
                problems.append(
                    f"release.yml: job {name} {label} step carries an if "
                    f"({steps[i].get('if')!r}) - fail closed: mandatory steps "
                    "must be unconditional"
                )
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

    def _disable_job_expr(w):
        for job in w["jobs"].values():
            job["if"] = "${{ false }}"
        return w

    def _gate_attest_step(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if str(s.get("uses", "")).startswith("actions/attest"):
                    s["if"] = "${{ false }}"
        return w

    def _release_after_exit(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if "gh release create" in str(s.get("run", "")):
                    s["run"] = (
                        "exit 0\ngh release create \"$GITHUB_REF_NAME\""
                        " --generate-notes docs/component-inventory.json"
                        " data/release-lock.json checksums.sha256"
                    )
        return w

    def _echo_generation(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if _norm(s.get("run", "")) == PINNED_GENERATION_RUN:
                    s["run"] = "echo " + PINNED_GENERATION_RUN
        return w

    def _echo_checksums(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if _norm(s.get("run", "")) == PINNED_CHECKSUMS_RUN:
                    s["run"] = "echo " + PINNED_CHECKSUMS_RUN
        return w

    def _fake_attest_action(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if str(s.get("uses", "")).startswith("actions/attest"):
                    s["uses"] = "actions/attest-not-real@v2"
        return w

    def _release_createx(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if _norm(s.get("run", "")) == PINNED_RELEASE_RUN:
                    s["run"] = PINNED_RELEASE_RUN.replace(
                        "gh release create", "gh release createx", 1)
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
        "release job disabled (if: ${{ false }})": _disable_job_expr,
        "attestation step gated (if: ${{ false }})": _gate_attest_step,
        "release command after exit 0": _release_after_exit,
        "generation command echoed, not executed": _echo_generation,
        "checksums command echoed, not executed": _echo_checksums,
        "attestation action is a lookalike (attest-not-real)": _fake_attest_action,
        "release command is a lookalike (createx)": _release_createx,
        "release command echoed, not executed": _echo_release,
        "attestation split into an unrelated job": _split_attest,
    }.items():
        problems = _workflow_problems(mutate(json.loads(json.dumps(wf))))
        if not problems:
            uncaught.append(f"workflow violation NOT caught: {name}")
    if uncaught:
        return  # harness FAILS: an SBOM/signing violation escaped
    raise CheckError("all seeded SBOM/signing violations caught")
