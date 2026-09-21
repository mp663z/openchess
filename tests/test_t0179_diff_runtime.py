"""T0179 production graph-diff runtime conformance."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from graph import diff as production
from tests.test_t0176_diff_contract import DiffEngine

CASES = json.loads((Path(__file__).parent / "fixtures" / "diff" / "cases.json").read_text())


def _call_prod(case):
    if case["kind"] == "compute":
        return production.compute(case["base"], case["target"])
    if case["kind"] == "apply":
        return production.apply(case["diff"], case["base"])
    return production.validate_diff(case["diff"])


def _call_ref(case):
    engine = DiffEngine()
    if case["kind"] == "compute":
        return engine.compute(case["base"], case["target"])
    if case["kind"] == "apply":
        return engine.apply(case["diff"], case["base"])
    return engine.validate_diff(case["diff"])


def _valid_records():
    from tests.test_t0113_position_digest_contract import AFTER_E4, STARTPOS, digest_fen
    from tests.test_t0122_transposition_node_contract import KINGS, _docs, _make_record

    docs = _docs()
    return [_make_record(*docs, digest_fen, "standard", fen) for fen in (STARTPOS, AFTER_E4, KINGS)]


def _key(record):
    from tests.test_t0122_transposition_node_contract import _docs, _record_identity

    return repr(_record_identity(*_docs(), record))


def _assert_malformed_unchanged(call, *objects):
    pristine = copy.deepcopy(objects)
    with pytest.raises(production.DiffError) as caught:
        call()
    assert caught.value.failure_class == "malformed_diff_record"
    assert objects == pristine


def test_well_formed_sibling_digest_substitution_rejected_on_every_path():
    first, sibling, third = _valid_records()
    forged = dict(first, digest=sibling["digest"])
    assert forged["digest"].startswith("pdv1:") and len(forged["digest"]) == 69
    good_state = {_key(first): first}
    forged_state = {_key(first): forged}
    _assert_malformed_unchanged(lambda: production.compute(forged_state, {}), forged_state)
    _assert_malformed_unchanged(lambda: production.compute({}, forged_state), forged_state)

    added = production.compute({}, {_key(first): first})
    added["added"][_key(first)] = copy.deepcopy(forged)
    removed = production.compute({_key(first): first}, {})
    removed["removed"][_key(first)] = copy.deepcopy(forged)
    for diff, base in ((added, {}), (removed, {_key(first): first})):
        _assert_malformed_unchanged(lambda d=diff: production.validate_diff(d), diff)
        _assert_malformed_unchanged(lambda d=diff, b=base: production.apply(d, b), diff, base)

    # changed is reserved/future-facing for the current exact three-field node.
    # Both witness sides are tested as hostile executable mutants, never valid fixtures.
    for slot in ("base", "target"):
        witness = {"base": copy.deepcopy(first), "target": copy.deepcopy(third)}
        witness[slot] = copy.deepcopy(forged)
        diff = {
            "base_id": "gs1:" + "1" * 64,
            "target_id": "gs1:" + "2" * 64,
            "added": {},
            "removed": {},
            "changed": {_key(first): witness},
        }
        _assert_malformed_unchanged(lambda d=diff: production.validate_diff(d), diff)
        _assert_malformed_unchanged(
            lambda d=diff, b=good_state: production.apply(d, b), diff, good_state
        )


def test_reference_strict_linked_record_oracle_rejects_sibling_digest():
    """Prerequisite repair oracle: legacy fake changed fixtures remain executable
    mutants, while this strict linked-record oracle is the authority for runtime work."""
    from tests.test_t0113_position_digest_contract import digest_fen

    first, sibling, _ = _valid_records()
    forged = dict(first, digest=sibling["digest"])
    assert forged["digest"] != digest_fen(forged["variant"], forged["snapshot_fen"])
    with pytest.raises(production.DiffError) as caught:
        production.compute({_key(forged): forged}, {})
    assert caught.value.failure_class == "malformed_diff_record"


def test_shipped_position_digest_matches_contract_vectors():
    from graph.position_digest import digest_fen
    from tests.test_t0113_position_digest_contract import VECTORS

    for fen, expected in VECTORS.items():
        assert digest_fen("standard", fen) == expected


def test_production_import_has_no_tests_dependency_and_works_without_tests_package():
    import ast
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for module in (
        root / "graph" / "diff.py",
        root / "graph" / "position_digest.py",
        root / "graph" / "fen.py",
    ):
        tree = ast.parse(module.read_text())
        assert not any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tests"))
                or (
                    isinstance(node, ast.Import)
                    and any(alias.name.startswith("tests") for alias in node.names)
                )
            )
            for node in ast.walk(tree)
        ), module
    script = """
import sys
sys.modules['tests'] = None
from graph.diff import compute
print(compute({}, {}))
"""
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(root)},
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert "'added': {}" in run.stdout
