"""T0179 production graph-diff runtime conformance."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from graph import diff as production
from tests.test_t0176_diff_contract import DiffEngine
from tests.test_t0176_diff_contract import DiffError as ReferenceError

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


@pytest.mark.parametrize("section", ["happy", "boundary"])
def test_production_matches_reference_on_valid_fixture(section):
    for case in CASES[section]:
        assert _call_prod(copy.deepcopy(case)) == _call_ref(copy.deepcopy(case)), case["name"]


def test_production_rejects_malformed_with_exact_failure_and_rollback():
    for case in CASES["malformed"]:
        before = copy.deepcopy(case)
        with pytest.raises(production.DiffError) as caught:
            _call_prod(case)
        assert caught.value.failure_class == case["expect_failure"], case["name"]
        assert case == before, case["name"]


def test_production_rollback_cases_then_valid_apply():
    for case in CASES["rollback"]:
        before = copy.deepcopy(case["base"])
        with pytest.raises(production.DiffError) as caught:
            production.apply(case["diff"], case["base"])
        assert caught.value.failure_class == case["expect_failure"]
        assert case["base"] == before
        assert production.apply(case["then_diff"], case["then_base"]) == case["expect_target"]


def test_reference_and_production_failure_classes_match_all_invalid_cases():
    for case in CASES["malformed"]:
        with pytest.raises(ReferenceError) as reference:
            _call_ref(copy.deepcopy(case))
        with pytest.raises(production.DiffError) as actual:
            _call_prod(copy.deepcopy(case))
        assert actual.value.failure_class == reference.value.failure_class
        assert actual.value.code == reference.value.code
