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
all fail. Beyond the mandatory steps, the COMPLETE release-job pipeline
is pinned step-for-step in order (checkout -> setup -> install -> lock
check -> license audit -> generation -> checksums -> attestation ->
release): inserted, removed, reordered or altered steps fail closed, so
released bytes are always the attested bytes; conditionals are rejected
on every pipeline step. Step metadata is fail-closed: only
uses/run/name/with/env are allowed (continue-on-error, working-directory,
shell and any other execution-affecting key is a violation), and with/env
blocks must match their exact pinned contents. Fail closed on conditionals:
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


# The COMPLETE release-job pipeline, pinned step for step: identity
# (uses/run, whitespace-normalized) and order. Any inserted, removed,
# reordered or altered step is a violation - bytes attested and bytes
# released cannot drift apart between pinned steps.
PINNED_JOB_STEPS: list[tuple[str, str]] = [
    ("uses", "actions/checkout@v4"),
    ("uses", "actions/setup-python@v5"),
    ("run", "pip install -r requirements-dev.txt"),
    ("run", "python tools/lock_release.py --check"),
    ("run", "python tools/license_audit.py"),
    ("run", PINNED_GENERATION_RUN),
    ("run", PINNED_CHECKSUMS_RUN),
    ("uses", PINNED_ATTEST_USES),
    ("run", PINNED_RELEASE_RUN),
]


# Fail closed on every execution-affecting step key: only identity
# (uses/run), display name, and the exact pinned with/env blocks survive.
ALLOWED_STEP_KEYS = {"uses", "run", "name", "with", "env"}


def _step_identity(s: dict) -> tuple[str, str] | None:
    has_uses = "uses" in s
    has_run = "run" in s
    if has_uses and has_run:
        return None  # invalid: both
    if has_uses:
        return ("uses", _norm(s["uses"]))
    if has_run:
        return ("run", _norm(s["run"]))
    return None


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
        for i, s in enumerate(steps):
            if s.get("if") not in (None, ""):
                problems.append(
                    f"release.yml: job {name} step {i} carries an if "
                    f"({s.get('if')!r}) - fail closed: pipeline steps must "
                    "be unconditional"
                )
            extra = sorted(set(s) - ALLOWED_STEP_KEYS)
            if extra:
                problems.append(
                    f"release.yml: job {name} step {i} carries "
                    f"execution-affecting keys {extra} - only "
                    f"{sorted(ALLOWED_STEP_KEYS)} are allowed (fail closed "
                    "on continue-on-error, working-directory, shell, ...)"
                )
            ident = _step_identity(s)
            if ident == ("uses", "actions/setup-python@v5"):
                if s.get("with") != {"python-version": "3.12"}:
                    problems.append(
                        f"release.yml: job {name} setup-python step with "
                        f"block must be exactly python-version 3.12"
                    )
            elif ident == ("uses", PINNED_ATTEST_USES):
                if set(s.get("with") or {}) != {"subject-path"}:
                    problems.append(
                        f"release.yml: job {name} attestation step with "
                        "block must contain only subject-path"
                    )
            elif "with" in s:
                problems.append(
                    f"release.yml: job {name} step {i} carries an "
                    "unexpected with block"
                )
            if ident == ("run", PINNED_RELEASE_RUN):
                if s.get("env") != {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}:
                    problems.append(
                        f"release.yml: job {name} release step env must be "
                        "exactly GH_TOKEN from secrets.GITHUB_TOKEN"
                    )
            elif "env" in s:
                problems.append(
                    f"release.yml: job {name} step {i} carries an "
                    "unexpected env block"
                )
        actual = [_step_identity(s) for s in steps]
        if any(a is None for a in actual):
            problems.append(
                f"release.yml: job {name} has a step with both/neither "
                "run/uses - pipeline shape unrecognized, failing closed"
            )
            continue
        if actual != PINNED_JOB_STEPS:
            padded = actual + [None] * max(0, len(PINNED_JOB_STEPS) - len(actual))
            for i, (exp, got) in enumerate(
                zip(PINNED_JOB_STEPS, padded, strict=False)
            ):
                if exp != got:
                    problems.append(
                        f"release.yml: job {name} step {i} must be exactly "
                        f"{exp!r}, got {got!r} - the complete pinned pipeline "
                        "may not be inserted into, reordered or altered"
                    )
                    break
            if len(actual) != len(PINNED_JOB_STEPS) and len(actual) > len(PINNED_JOB_STEPS):
                problems.append(
                    f"release.yml: job {name} has {len(actual) - len(PINNED_JOB_STEPS)} "
                    "extra step(s) beyond the pinned pipeline"
                )
        att = _step_idx(steps, "uses", PINNED_ATTEST_USES)
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

    def _attest_continue_on_error(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if str(s.get("uses", "")).startswith("actions/attest"):
                    s["continue-on-error"] = True
        return w

    def _release_custom_shell(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if _norm(s.get("run", "")) == PINNED_RELEASE_RUN:
                    s["shell"] = "echo {0}"
        return w

    def _generation_working_directory(w):
        for job in w["jobs"].values():
            for s in job["steps"]:
                if _norm(s.get("run", "")) == PINNED_GENERATION_RUN:
                    s["working-directory"] = "/tmp"
        return w

    def _tamper(after_uses=None, after_run=None):
        def m(w):
            for job in w["jobs"].values():
                steps = job["steps"]
                for i, s in enumerate(steps):
                    if after_uses and str(s.get("uses", "")).startswith("actions/attest"):
                        steps.insert(i + 1, {"run": "echo MALICE >> docs/component-inventory.json"})
                        return w
                    if after_run and _norm(s.get("run", "")) == after_run:
                        steps.insert(i + 1, {"run": "echo MALICE >> docs/component-inventory.json"})
                        return w
            return w
        return m

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
        "tamper between checksums and attestation": _tamper(after_run=PINNED_CHECKSUMS_RUN),
        "tamper between attestation and release": _tamper(after_uses=True),
        "attestation continue-on-error: true": _attest_continue_on_error,
        "release runs under shell 'echo {0}'": _release_custom_shell,
        "generation in working-directory /tmp": _generation_working_directory,
        "release command echoed, not executed": _echo_release,
        "attestation split into an unrelated job": _split_attest,
    }.items():
        problems = _workflow_problems(mutate(json.loads(json.dumps(wf))))
        if not problems:
            uncaught.append(f"workflow violation NOT caught: {name}")
    if uncaught:
        return  # harness FAILS: an SBOM/signing violation escaped
    raise CheckError("all seeded SBOM/signing violations caught")
