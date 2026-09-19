"""DAG board tooling for the product task graph (tasks/dag.json).

Done requires acceptance, an exact SHA and an evidence manifest.
UNVERIFIED is never done.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REQUIRED_FIELDS = {
    "id",
    "phase",
    "milestone",
    "week",
    "track",
    "title",
    "dependencies",
    "acceptance",
    "verification",
    "status",
    "spine_outcome",
    "roadmap_layer",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
def require_sha(sha: str) -> None:
    if not SHA_RE.fullmatch(sha or ""):
        raise DagError(f"done requires a full lowercase 40-hex SHA, got {sha!r}")


def require_evidence(task_id: str, manifest: str) -> None:
    want = f"evidence/{task_id}.md"
    if manifest != want:
        raise DagError(f"evidence manifest must be exactly {want}, got {manifest!r}")


VALID_STATUS = {"todo", "claimed", "in_progress", "done", "blocked"}


class DagError(Exception):
    pass


def load(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def save_atomic(path: Path, board: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".json")
    with open(fd, "w") as f:
        json.dump(board, f, indent=1)
        f.write("\n")
    Path(tmp).replace(path)


def index(board: dict) -> dict[str, dict]:
    tasks = board["tasks"]
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        dupes = {i for i in ids if ids.count(i) > 1}
        raise DagError(f"duplicate task ids: {sorted(dupes)[:5]}")
    return {t["id"]: t for t in tasks}


def verify(board: dict) -> list[str]:
    """Structural integrity checks. Returns a list of problems (empty = ok).

    Fail CLOSED, never crash: malformed shapes (null/non-list dependencies,
    null/empty/nonstring ids or fields) are reported as problems.
    """
    problems: list[str] = []
    if not isinstance(board, dict):
        return [f"board must be a mapping, got {type(board).__name__}"]
    tasks = board.get("tasks")
    if not isinstance(tasks, list):
        return ["board: tasks must be a list"]
    for t in tasks:
        if not isinstance(t, dict):
            problems.append(f"task entry is not a mapping: {t!r}")
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not tid.strip():
            problems.append(f"task id must be a non-empty string, got {tid!r}")
    ids = [t.get("id") for t in tasks if isinstance(t, dict)]
    seen: set[str] = set()
    for i in ids:
        if isinstance(i, str):
            if i in seen:
                problems.append(f"duplicate task id {i}")
            seen.add(i)
    known = set(seen)
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = t.get("id", "?")
        missing = REQUIRED_FIELDS - set(t)
        if missing:
            problems.append(f"{tid}: missing fields {sorted(missing)}")
        for field in ("title", "acceptance", "verification", "milestone",
                      "phase", "roadmap_layer", "spine_outcome", "track", "week"):
            if field in t:
                v = t[field]
                if not isinstance(v, str):
                    problems.append(f"{tid}: {field} must be a string, got {type(v).__name__}")
                elif not v.strip():
                    problems.append(f"{tid}: empty {field}")
        status = t.get("status")
        if not isinstance(status, str) or status not in VALID_STATUS:
            problems.append(f"{tid}: invalid status {status!r}")
        deps = t.get("dependencies", [])
        if not isinstance(deps, list):
            problems.append(f"{tid}: dependencies must be a list, got {type(deps).__name__}")
        else:
            seen_deps: set[str] = set()
            for dep in deps:
                if not isinstance(dep, str):
                    problems.append(f"{tid}: dependency must be a string, got {dep!r}")
                elif dep in seen_deps:
                    problems.append(f"{tid}: duplicate dependency {dep}")
                elif dep not in known:
                    problems.append(f"{tid}: unknown dependency {dep}")
                else:
                    seen_deps.add(dep)
        if t.get("status") == "done":
            sha = t.get("done_sha") or ""
            if not SHA_RE.fullmatch(str(sha)):
                problems.append(f"{tid}: done_sha is not a full 40-hex SHA")
            want = f"evidence/{tid}.md"
            if t.get("evidence_manifest") != want:
                problems.append(
                    f"{tid}: evidence_manifest must be exactly {want}"
                )
    # cycle detection (Kahn) over well-formed entries only
    id_counts: dict[str, int] = {}
    for t in tasks:
        if isinstance(t, dict) and isinstance(t.get("id"), str):
            id_counts[t["id"]] = id_counts.get(t["id"], 0) + 1
    clean = [
        t for t in tasks
        if isinstance(t, dict)
        and isinstance(t.get("id"), str) and t["id"].strip()
        and id_counts.get(t["id"], 0) == 1
        and isinstance(t.get("dependencies", []), list)
        and all(isinstance(d, str) for d in t.get("dependencies", []))
    ]
    dep_sets = {t["id"]: set(t["dependencies"]) for t in clean}
    indeg = {t["id"]: 0 for t in clean}
    for t in clean:
        for dep in dep_sets[t["id"]]:
            if dep in indeg:
                indeg[t["id"]] += 1
    queue = [i for i, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for t in clean:
            if node in dep_sets[t["id"]]:
                indeg[t["id"]] -= 1
                if indeg[t["id"]] == 0:
                    queue.append(t["id"])
    if seen != len(clean):
        problems.append("dependency cycle detected")
    return problems


def claimable(board: dict) -> list[dict]:
    by_id = index(board)
    return [
        t
        for t in board["tasks"]
        if t["status"] == "todo"
        and all(by_id[d]["status"] == "done" for d in t["dependencies"])
    ]


def cmd_verify(board: dict, _args: argparse.Namespace) -> int:
    problems = verify(board)
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    print(f"OK {len(board['tasks'])} tasks, no structural problems")
    return 0


def cmd_status(board: dict, _args: argparse.Namespace) -> int:
    counts: dict[str, int] = {}
    for t in board["tasks"]:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    total = len(board["tasks"])
    done = counts.get("done", 0)
    parts = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"total={total} done={done} ({done / total:.1%}) " + parts)
    print(f"claimable_now={len(claimable(board))}")
    return 0


def cmd_claimable(board: dict, args: argparse.Namespace) -> int:
    rows = claimable(board)
    if args.track:
        rows = [t for t in rows if t["track"] == args.track]
    for t in rows[: args.limit]:
        print(f"{t['id']}\t{t['phase']}\t{t['week']}\t{t['track']}\t{t['title']}")
    print(f"# {len(rows)} claimable")
    return 0


def cmd_claim(board: dict, args: argparse.Namespace) -> int:
    by_id = index(board)
    t = by_id.get(args.task_id)
    if t is None:
        raise DagError(f"unknown task {args.task_id}")
    if t["status"] != "todo":
        raise DagError(f"{args.task_id} is {t['status']}, not todo")
    unmet = [d for d in t["dependencies"] if by_id[d]["status"] != "done"]
    if unmet:
        raise DagError(f"{args.task_id} has unmet dependencies: {unmet}")
    t["status"] = "claimed"
    t["claimed_by"] = args.claimant
    return 0


def cmd_start(board: dict, args: argparse.Namespace) -> int:
    by_id = index(board)
    t = by_id.get(args.task_id)
    if t is None:
        raise DagError(f"unknown task {args.task_id}")
    if t["status"] not in {"todo", "claimed"}:
        raise DagError(f"{args.task_id} is {t['status']}")
    unmet = [d for d in t["dependencies"] if by_id[d]["status"] != "done"]
    if unmet:
        raise DagError(f"{args.task_id} has unmet dependencies: {unmet}")
    t["status"] = "in_progress"
    t["claimed_by"] = args.claimant
    return 0


def cmd_complete(board: dict, args: argparse.Namespace) -> int:
    by_id = index(board)
    t = by_id.get(args.task_id)
    if t is None:
        raise DagError(f"unknown task {args.task_id}")
    if t["status"] in {"done"}:
        raise DagError(f"{args.task_id} already done")
    require_sha(args.sha)
    require_evidence(args.task_id, args.evidence)
    unmet = [d for d in t["dependencies"] if by_id[d]["status"] != "done"]
    if unmet:
        raise DagError(f"{args.task_id} has unmet dependencies: {unmet}")
    t["status"] = "done"
    t["done_sha"] = args.sha
    t["evidence_manifest"] = args.evidence
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    default_board = Path(__file__).resolve().parent.parent / "tasks" / "dag.json"
    ap.add_argument("--board", default=str(default_board))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    sub.add_parser("status")
    c = sub.add_parser("claimable")
    c.add_argument("--track")
    c.add_argument("--limit", type=int, default=50)
    for name in ("claim", "start"):
        p = sub.add_parser(name)
        p.add_argument("task_id")
        p.add_argument("--claimant", default="task-agent")
    p = sub.add_parser("complete")
    p.add_argument("task_id")
    p.add_argument("--sha", required=True)
    p.add_argument("--evidence", required=True)
    args = ap.parse_args()

    board_path = Path(args.board)
    board = load(board_path)
    fn = {
        "verify": cmd_verify,
        "status": cmd_status,
        "claimable": cmd_claimable,
        "claim": cmd_claim,
        "start": cmd_start,
        "complete": cmd_complete,
    }[args.cmd]
    rc = fn(board, args)
    if args.cmd in {"claim", "start", "complete"} and rc == 0:
        save_atomic(board_path, board)
        print(f"{args.cmd}: {args.task_id} ok")
    return rc


if __name__ == "__main__":
    sys.exit(main())
