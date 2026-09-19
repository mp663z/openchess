"""T0022: unit/contract check - the DAG board verifier's real contract.

Good mode: the real tasks/dag.json verifies clean via tools.dag.verify.
Violation mode: seeded board fixtures under fixtures/dag/, each breaking
exactly one contract rule (cycle, unknown dependency, duplicate id,
null/non-list dependencies, null/empty id, nonstring field, invalid
status, missing required field). Every seeded violation must be REJECTED
by the real verifier - reported as a problem, never a crash - for its own
seeded reason. verify() failing closed (problem list, no exception) is
itself part of the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import dag
from tools.install_checks import CheckError

CHECK_ID = "T0022"
REAL_BOARD = Path(__file__).resolve().parent.parent.parent / "tasks" / "dag.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dag"

VIOLATIONS = {
    "bad_cycle.json": "dependency cycle detected",
    "bad_unknown_dep.json": "unknown dependency",
    "bad_dup_id.json": "duplicate task id",
    "bad_null_deps.json": "dependencies must be a list",
    "bad_null_id.json": "non-empty string",
    "bad_empty_id.json": "non-empty string",
    "bad_nonstring_field.json": "must be a string",
    "bad_status.json": "invalid status",
    "bad_missing_field.json": "missing fields",
    "bad_board_list.json": "board must be a mapping",
    "bad_status_unhashable.json": "invalid status",
    "bad_dup_dep.json": "duplicate dependency",
    "bad_meta_field.json": "milestone must be a string",
    "bad_int_sha.json": "done_sha is not a full 40-hex SHA",
    "bad_no_schema.json": "schema_version must be exactly",
    "bad_wrong_schema.json": "schema_version must be exactly",
    "bad_empty_tasks.json": "tasks list is empty",
    "bad_padded_id.json": "surrounding whitespace",
}


def run(mode: str) -> None:
    if mode == "good":
        problems = dag.verify(dag.load(REAL_BOARD))
        if problems:
            raise CheckError(f"real board: {problems}")
        return
    uncaught = []
    for fname, expect in sorted(VIOLATIONS.items()):
        try:
            problems = dag.verify(json.loads((FIXTURES / fname).read_text()))
        except Exception as e:  # contract: fail closed, never crash
            uncaught.append(f"{fname}: verify CRASHED instead of reporting: {e!r}")
            continue
        if not any(expect in p for p in problems):
            uncaught.append(f"{fname}: expected problem containing {expect!r}, got {problems}")
    if uncaught:
        return  # harness FAILS: a seeded contract violation escaped
    raise CheckError("all seeded board contract violations rejected by the real verifier")
