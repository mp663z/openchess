"""T0007 v3 - evidence contract lint (strict, frozen grandfathering).

v3 hardenings (verifier findings on v2):
- The grandfather allowlist is pinned: ALLOWLIST_SHA256 is the sha256 of the
  allowlist file's exact bytes, hardcoded in this enforcement module. A commit
  that edits data/evidence-pre-contract.yaml (replace an entry, keep the
  count, retarget a digest) fails closed: every marker file then errors as
  non-allowlisted. Changing the allowlist requires changing trusted code.
- The pre-contract marker must close the file (only whitespace after it).
- An evidence file for a task missing from tasks/dag.json is an error.
- Commands must contain a nonempty backticked command and a nonempty
  Environment value.

Rules (docs/governance/evidence-contract.md):
- A file is grandfathered ONLY when it carries an exact `pre-contract: true`
  footer line AND its path is in data/evidence-pre-contract.yaml AND the
  recorded sha256 of its bytes matches. Prose mentions mean nothing; any edit
  invalidates grandfathering and the file must meet the full contract.
- Full-contract files declare strict fields, each on its own line:
    Status: done | in-progress | pending
    Verification: <exact dag verification mode> - <verdict detail>
    Commands: `<reproducing command>` ... Environment: <environment>
    Recorded merge SHA on main: <40-hex>   (required iff Status: done)
- Cross-field checks against tasks/dag.json: a done task's evidence Status
  must be done with the SHA equal to the board's done_sha; independent-review
  / independent-verifier / human modes require a "PASS at <sha>" verdict
  naming the same SHA; a task not done on the board must not claim done.
UNVERIFIED is never done.

Judgment-substituted human checkpoints (owner-delegated, 2026-09-19):
a task whose board verification mode is EXACTLY "human checkpoint" AND
whose board checkpoint is nonempty may complete on a judgment-substituted
verdict instead of a human PASS. Eligibility also requires a pinned
grant in data/judgment-grants.yaml (sha256 pinned below as
GRANTS_SHA256 - the registry is trusted-code-adjacent: editing it
without changing the pin fails closed and every substitution is
refused). The verdict must match the grant exactly (owner wamid, date,
substitution record path, re-verify hooks); the record must exist, name
the task, carry the grant wamid, and contain every required field
(Task, What the human would have done, Why no human pass happened,
Provisional substitute decision, Re-verify hook); every hook must be a
real dag task distinct from the source task, not done, tagged
reverify:<task>, and carry a mode from the pinned ACCEPTED_HOOK_MODES
set (exact governance vocabulary, never substrings).
Auto+human, human/external, human/legal-audit and every other
human-like mode are NOT substitutable. Repo content never authorizes
itself: the grant content is verified against trusted owner-channel
evidence before the pin is set.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
DAG = ROOT / "tasks" / "dag.json"
ALLOWLIST = ROOT / "data" / "evidence-pre-contract.yaml"

# sha256 of data/evidence-pre-contract.yaml bytes - the frozen grandfather list
ALLOWLIST_SHA256 = "1475352ad9092f9e676096948476c2eb1b650a0d513f4021aa0a53fa17fd5a42"
GRANTS = ROOT / "data" / "judgment-grants.yaml"
# sha256 of data/judgment-grants.yaml bytes - the pinned grant registry
GRANTS_SHA256 = "f1f74738f148ad26a30c0291f59d1fa16d9eb325a230f3a6dc442b948948b993"
GRANT_KEY_RE = re.compile(r"^T\d{4}$")
GRANT_RECORD_RE = re.compile(r"^evidence/substitutions/(T\d{4})\.md$")
WAMID_RE = re.compile(r"^wamid\.[A-Za-z0-9+/=]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _real_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


SUBSTITUTABLE_MODES = frozenset({"human checkpoint"})
# Re-verify hooks must carry a human/independent gate from the board's
# EXACT governance vocabulary - substring checks authorize lookalikes
# ("inhuman automated", "not human", "independently automatic").
ACCEPTED_HOOK_MODES = frozenset(
    {
        "human",
        "human checkpoint",
        "human research",
        "human/external",
        "human/legal audit",
        "human/legal/expert review",
        "human/architecture review",
        "human/financial review",
        "human/independent",
        "human/independent review",
        "human/independent verifier",
        "independent verifier",
        "independent visual verifier",
        "independent review",
        "independent analyst",
        "independent finance/security verifier",
        "independent product/legal review",
        "independent AI verifier",
        "independent ML verifier",
        "independent benchmark",
        "auto + human",
        "auto + independent review",
    }
)
REQUIRED_RECORD_FIELDS = (
    "Task:",
    "What the human would have done:",
    "Why no human pass happened:",
    "Provisional substitute decision:",
    "Re-verify hook:",
)
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWLIST_KEY_RE = re.compile(r"^evidence/T\d{4}\.md$")
CMD_RE = re.compile(r"`[^`\s][^`]*`")
ENV_RE = re.compile(r"Environment:\s*\S")
TASK_RE = re.compile(r"^T\d{4}$")
MARKER_RE = re.compile(r"pre-contract: true( - [^\n]*)?\s*\Z")
INDEPENDENT = ("independent", "human")

# Judgment-substituted human checkpoints (owner-delegated, 2026-09-19):
# a HUMAN gate (never an independent-verifier task) may complete when the
# owner has delegated the decision, recorded as a provisional substitute
# with re-verify hooks. The substitution is only as strong as its record:
# the record file must exist, name the task, carry the owner wamid, and
# name at least one real re-verify hook task.
SUBST_RE = re.compile(
    r"\Ajudgment-substituted per owner (wamid\.[A-Za-z0-9+/=]+) "
    r"\((\d{4}-\d{2}-\d{2})\); provisional decision: "
    r"(evidence/substitutions/(T\d{4})\.md); "
    r"re-verify hooks: (T\d{4}(?:, T\d{4})*)\Z"
)


def _tasks(dag_path: Path) -> dict[str, dict]:
    data = json.loads(dag_path.read_text())
    items = data["tasks"] if isinstance(data, dict) and "tasks" in data else data
    return {t["id"]: t for t in items}


def _field(text: str, name: str) -> str | None:
    m = re.search(rf"^{re.escape(name)}: (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def _check_substitution(
    task: str,
    board: dict,
    mode: str,
    groups: tuple,
    tasks: dict[str, dict],
    grants: dict[str, dict],
    rel: str,
) -> list[str]:
    """Grant-pinned judgment-substitution checks (fail closed)."""
    errors: list[str] = []
    wamid, date, record, record_task, hooks_s = groups
    if mode not in SUBSTITUTABLE_MODES:
        errors.append(
            f"{rel}: mode {mode!r} is not substitutable - only "
            f"{sorted(SUBSTITUTABLE_MODES)} with a nonempty checkpoint"
        )
    if not str(board.get("checkpoint") or "").strip():
        errors.append(f"{rel}: substitution requires a nonempty board checkpoint")
    grant = grants.get(task)
    if grant is None:
        errors.append(
            f"{rel}: no pinned judgment grant for {task} - repo content "
            "cannot authorize its own substitution"
        )
        return errors
    if record_task != task:
        errors.append(f"{rel}: substitution record {record} is for {record_task}, not {task}")
    if wamid != grant["wamid"]:
        errors.append(f"{rel}: verdict wamid != pinned grant wamid")
    if date != grant["date"]:
        errors.append(f"{rel}: verdict date != pinned grant date")
    if record != grant["record"]:
        errors.append(f"{rel}: verdict record != pinned grant record")
    hooks = hooks_s.split(", ")
    if hooks != grant["hooks"]:
        errors.append(f"{rel}: verdict hooks != pinned grant hooks")
    record_path = ROOT / record
    if not record_path.is_file():
        errors.append(f"{rel}: substitution record {record} missing")
    else:
        body = record_path.read_text()
        lines = body.splitlines()
        if not lines or task not in lines[0]:
            errors.append(f"{rel}: substitution record {record} title must name {task}")
        if grant["wamid"] not in body:
            errors.append(
                f"{rel}: substitution record {record} does not carry the pinned grant wamid"
            )
        field_values: dict[str, str] = {}
        for field in REQUIRED_RECORD_FIELDS:
            hits = re.findall(rf"^- {re.escape(field)}\s*(\S.*)$", body, re.M)
            if len(hits) != 1:
                errors.append(
                    f"{rel}: substitution record {record} must carry "
                    f"{field!r} exactly once with a nonempty value "
                    f"({len(hits)} found)"
                )
            else:
                field_values[field] = hits[0]
        hook_field = field_values.get("Re-verify hook:")
        if hook_field is not None:
            if not re.fullmatch(r"T\d{4}(?:, T\d{4})*", hook_field):
                errors.append(
                    f"{rel}: substitution record {record} re-verify hook "
                    "field must be exactly the ordered grant hook ids "
                    "(comma-space separated, no prose)"
                )
            elif hook_field.split(", ") != grant["hooks"]:
                errors.append(
                    f"{rel}: substitution record {record} re-verify hook "
                    f"field {hook_field.split(', ')!r} != pinned grant "
                    f"hooks {grant['hooks']!r} (exact ordered ids required)"
                )
    for hook in hooks:
        if hook == task:
            errors.append(f"{rel}: re-verify hook cannot be {task} itself")
            continue
        hook_task = tasks.get(hook)
        if hook_task is None:
            errors.append(f"{rel}: re-verify hook {hook} is not a dag task")
            continue
        if hook_task.get("status") != "todo":
            errors.append(
                f"{rel}: re-verify hook {hook} status "
                f"{hook_task.get('status')!r} - must stay todo (an active "
                "or done hook means the gate is being worked or was "
                "completed: the substitution is superseded, not "
                "re-verifiable)"
            )
        tags = hook_task.get("tags")
        if tags is not None and (type(tags) is not list or any(type(t) is not str for t in tags)):
            errors.append(f"{rel}: re-verify hook {hook} tags malformed (list of strings required)")
            continue
        if f"reverify:{task}" not in (tags or []):
            errors.append(
                f"{rel}: re-verify hook {hook} is not linked back (missing reverify:{task} tag)"
            )
        hook_mode = hook_task.get("verification")
        if hook_mode not in ACCEPTED_HOOK_MODES:
            errors.append(
                f"{rel}: re-verify hook {hook} mode {hook_mode!r} is not in "
                "the pinned accepted hook-mode set (exact governance "
                "vocabulary required, no substrings)"
            )
    return errors


GRANT_FIELDS = ("wamid", "date", "record", "hooks")


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys instead of
    silently last-wins (PyYAML default)."""


def _no_dupes(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise ValueError(f"duplicate mapping key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dupes)


def _parse_grants(text: str) -> tuple[dict[str, dict], list[str]]:
    """Shape-validate the grant registry, failing closed on any
    structural deviation. The pinned digest authorizes the bytes; this
    schema check is what makes the bytes meaningful."""
    try:
        doc = yaml.load(text, Loader=_UniqueKeyLoader)
    except Exception:
        return {}, ["data/judgment-grants.yaml: unparseable YAML"]
    if type(doc) is not dict or set(doc) != {"grants"}:
        return {}, [
            "data/judgment-grants.yaml: top-level mapping with exactly one key 'grants' required"
        ]
    mapping = doc["grants"]
    if type(mapping) is not dict:
        return {}, ["data/judgment-grants.yaml: 'grants' must be a mapping"]
    errors: list[str] = []
    for key, value in mapping.items():
        if type(key) is not str or not GRANT_KEY_RE.match(key):
            errors.append(f"grant key {key!r} is not TNNNN")
            continue
        if type(value) is not dict:
            errors.append(f"grant {key}: mapping required")
            continue
        unknown = set(value) - set(GRANT_FIELDS)
        if unknown:
            errors.append(f"grant {key}: unknown fields {sorted(unknown)}")
        missing = [f for f in GRANT_FIELDS if f not in value]
        if missing:
            errors.append(f"grant {key}: missing fields {missing}")
            continue
        wamid = value.get("wamid")
        if type(wamid) is not str or not WAMID_RE.match(wamid):
            errors.append(f"grant {key}: wamid malformed")
        date = value.get("date")
        if type(date) is not str or not DATE_RE.match(date):
            errors.append(f"grant {key}: date must be YYYY-MM-DD")
        elif not _real_date(date):
            errors.append(f"grant {key}: date {date!r} is not a real calendar date")
        record = value.get("record")
        m = GRANT_RECORD_RE.match(record) if type(record) is str else None
        if m is None or m.group(1) != key:
            errors.append(f"grant {key}: record must be evidence/substitutions/{key}.md")
        hooks = value.get("hooks")
        if (
            type(hooks) is not list
            or not hooks
            or any(type(h) is not str or not GRANT_KEY_RE.match(h) for h in hooks)
            or len(set(hooks)) != len(hooks)
        ):
            errors.append(f"grant {key}: hooks must be unique TNNNN ids")
    return mapping, errors


def load_grants(path: Path) -> tuple[dict[str, dict], list[str]]:
    """Load the pinned judgment-grant registry, failing closed on any
    tampering (same trust model as the grandfather allowlist). The
    digest check authorizes the bytes; the schema check then decides
    what they mean - a re-pinned malformed registry still fails
    closed."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != GRANTS_SHA256:
        return {}, [
            "data/judgment-grants.yaml: digest != GRANTS_SHA256 pinned "
            "in tools/evidence_lint.py - grant registry tampered; "
            "substitutions refused (fail closed)"
        ]
    return _parse_grants(raw.decode())


def lint_file(
    path: Path,
    tasks: dict[str, dict],
    allowlist: dict[str, str],
    grants: dict[str, dict] | None = None,
    rel: str | None = None,
) -> list[str]:
    errors: list[str] = []
    grants = grants or {}
    task = path.stem
    rel = rel or f"evidence/{path.name}"
    text = path.read_text()
    if not TASK_RE.match(task):
        return [f"{rel}: filename must be TNNNN.md"]

    if MARKER_RE.search(text):
        recorded = allowlist.get(rel)
        if recorded is None:
            errors.append(f"{rel}: pre-contract marker on a non-allowlisted file")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != recorded:
            errors.append(f"{rel}: edited since grandfathering - must meet the full contract")
        else:
            return []  # frozen grandfathered file

    first = text.splitlines()[0] if text.splitlines() else ""
    if task not in first:
        errors.append(f"{rel}: title must name {task}")

    status = _field(text, "Status")
    if status not in ("done", "in-progress", "pending"):
        errors.append(f"{rel}: missing or invalid `Status:` field (done|in-progress|pending)")

    board = tasks.get(task)
    if board is None:
        errors.append(f"{rel}: task {task} missing from tasks/dag.json")
        return errors
    board_done = bool(board.get("status") == "done")
    board_sha = board.get("done_sha") or ""
    mode = board.get("verification")
    if not mode:
        errors.append(f"{rel}: board task {task} has no verification mode")

    recorded_sha = _field(text, "Recorded merge SHA on main")
    if status == "done":
        if not board_done:
            errors.append(f"{rel}: claims done but board status is not done")
        if not recorded_sha or not SHA_RE.fullmatch(recorded_sha):
            errors.append(f"{rel}: Status: done without an exact 40-hex recorded merge SHA")
        elif board_sha and recorded_sha != board_sha:
            errors.append(
                f"{rel}: recorded SHA {recorded_sha[:12]} != board done_sha {board_sha[:12]}"
            )
    elif recorded_sha and not SHA_RE.fullmatch(recorded_sha):
        errors.append(f"{rel}: recorded merge SHA is not a full 40-hex SHA")

    verification = _field(text, "Verification")
    if verification is None:
        errors.append(f"{rel}: missing `Verification:` field")
    else:
        if "UNVERIFIED" in verification:
            errors.append(f"{rel}: UNVERIFIED verdict is never done")
        if mode is not None:
            vmode, _, detail = verification.partition(" - ")
            if vmode != mode:
                errors.append(f"{rel}: Verification mode {vmode!r} != board mode {mode!r}")
            if status == "done" and any(k in mode for k in INDEPENDENT):
                m = re.search(r"\bPASS at ([0-9a-f]{40})\b", detail)
                subst = SUBST_RE.match(detail.strip())
                if m:
                    if board_sha and m.group(1) != board_sha:
                        errors.append(
                            f"{rel}: verifier PASS SHA {m.group(1)[:12]} != board done_sha"
                        )
                elif subst:
                    errors.extend(
                        _check_substitution(task, board, mode, subst.groups(), tasks, grants, rel)
                    )
                elif "human" in mode and "independent" not in mode:
                    errors.append(
                        f"{rel}: human mode requires a `PASS at <sha>` "
                        "verdict or a judgment-substituted verdict naming "
                        "owner wamid, substitution record and re-verify "
                        "hooks"
                    )
                else:
                    errors.append(
                        f"{rel}: independent/human mode requires a `PASS at <sha>` verdict"
                    )

    commands = _field(text, "Commands")
    if not commands:
        errors.append(f"{rel}: missing `Commands:` field")
    else:
        if not CMD_RE.search(commands):
            errors.append(f"{rel}: Commands must include a nonempty backticked command")
        if not ENV_RE.search(commands):
            errors.append(
                f"{rel}: Commands must record a nonempty environment (Environment: <value>)"
            )
    return errors


def load_allowlist(path: Path) -> tuple[dict[str, str], list[str]]:
    """Load the grandfather allowlist, failing closed on any tampering.

    The file's bytes must hash to ALLOWLIST_SHA256 (pinned above in trusted
    code); on mismatch nothing is grandfathered. Shape is validated too.
    """
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ALLOWLIST_SHA256:
        return {}, [
            "data/evidence-pre-contract.yaml: digest != ALLOWLIST_SHA256 pinned "
            "in tools/evidence_lint.py - allowlist tampered; grandfathering "
            "refused (fail closed)"
        ]
    errors: list[str] = []
    mapping = (yaml.safe_load(raw.decode()) or {}).get("files", {})
    for key, value in mapping.items():
        if not ALLOWLIST_KEY_RE.match(key):
            errors.append(f"allowlist key {key!r} is not evidence/TNNNN.md")
        if not isinstance(value, str) or not DIGEST_RE.match(value):
            errors.append(f"allowlist digest for {key!r} is not 64-hex sha256")
    return mapping, errors


def lint_tree(root: Path = ROOT) -> list[str]:
    evidence = root / "evidence"
    dag = root / "tasks" / "dag.json"
    allowlist_path = root / "data" / "evidence-pre-contract.yaml"
    tasks = _tasks(dag)
    allowlist, errors = load_allowlist(allowlist_path)
    grants, grant_errors = load_grants(root / "data" / "judgment-grants.yaml")
    errors.extend(grant_errors)
    files = sorted(evidence.glob("T*.md"))
    if not files:
        errors.append("no evidence files found")
    for path in files:
        errors.extend(lint_file(path, tasks, allowlist, grants))
    return errors


def main() -> int:
    errors = lint_tree()
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print(f"OK evidence contract: {len(list(EVIDENCE.glob('T*.md')))} files lint-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
