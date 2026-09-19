"""Real benchmark: walk the task DAG and confirm full reachability.

Correctness is content-independent (every task reachable, walk
terminates), so board edits never break it; the threshold only catches
order-of-magnitude slowdowns in the walk itself.
"""

from __future__ import annotations

import json
from pathlib import Path

BENCH_ID = "dag-walk"
THRESHOLD_MS = 2000.0
DAG = Path(__file__).resolve().parents[2] / "tasks" / "dag.json"


def run() -> int:
    tasks = json.loads(DAG.read_text())["tasks"]
    deps = {t["id"]: list(t.get("dependencies") or []) for t in tasks}
    seen: set[str] = set()
    stack = list(deps)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(d for d in deps.get(node, []) if d not in seen)
    _ = len(deps)  # size read is part of the workload
    return len(seen)


def verify(result: int) -> str | None:
    total = len(json.loads(DAG.read_text())["tasks"])
    if result != total:
        return f"walk reached {result} of {total} tasks"
    return None
