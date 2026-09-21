"""T0141: closed, executable opening-context fixture."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from pathlib import Path

import pytest
import yaml

from tests.test_t0140_opening_context_contract import ContextError, _table

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/opening_context/cases.json").read_text())
DOC = yaml.safe_load((ROOT / "data/contracts/opening_context.yaml").read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP = {"schema_version", "contract", "contract_schema_version", "section_manifests", *SECTIONS}
RESOLVE_KEYS = {"name", "scenario_tag", "variant", "path", "expect_code", "expect_name"}
MALFORMED_KEYS = {"name", "scenario_tag", "variant", "path", "expect_failure", "repair"}
ROLLBACK_KEYS = {
    "name",
    "scenario_tag",
    "setup_paths",
    "variant",
    "rejected_path",
    "expect_failure",
    "then_path",
    "expect_serialized",
}
FAILURES = set(DOC["contract"]["failures"]["classes"])


def _typed_keys(value):
    return sorted((type(key).__name__, repr(key)) for key in value)


def _keys(value, expected, where):
    assert type(value) is dict, f"{where}: object required"
    assert set(value) == expected, f"{where}: {_typed_keys(value)}"


def _digest(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _validate(cases):
    _keys(cases, TOP, "root")
    assert type(cases["schema_version"]) is int and cases["schema_version"] == 1
    assert type(cases["contract_schema_version"]) is int
    assert cases["contract_schema_version"] == DOC["schema_version"]
    assert cases["contract"] == "data/contracts/opening_context.yaml"
    _keys(cases["section_manifests"], set(SECTIONS), "manifests")
    names = []
    tags = []
    for section in SECTIONS:
        rows = cases[section]
        assert type(rows) is list and rows
        expected = (
            ROLLBACK_KEYS
            if section == "rollback"
            else MALFORMED_KEYS
            if section == "malformed"
            else RESOLVE_KEYS
        )
        for index, row in enumerate(rows):
            _keys(row, expected, f"{section}[{index}]")
            assert type(row["name"]) is str and row["name"]
            assert type(row["scenario_tag"]) is str and ":" in row["scenario_tag"]
            if "expect_failure" in row:
                assert row["expect_failure"] in FAILURES
            names.append(row["name"])
            tags.append(row["scenario_tag"])
        manifest = cases["section_manifests"][section]
        assert type(manifest) is dict
        assert list(manifest) == [row["name"] for row in rows]
        assert manifest == {row["name"]: _digest(row) for row in rows}
    assert len(names) == len(set(names))
    assert len(tags) == len(set(tags))


def _execute_resolve(row):
    record = _table().insert(row["variant"], row["path"])
    assert record["opening_code"] == row["expect_code"]
    assert record["opening_name"] == row["expect_name"]
    return record


def test_fixture_structure_and_semantic_manifests():
    _validate(CASES)


@pytest.mark.parametrize("section", ("happy", "boundary"))
def test_resolution_cases_execute(section):
    for row in CASES[section]:
        _execute_resolve(row)


def test_malformed_cases_reject_and_repairs_succeed():
    for row in CASES["malformed"]:
        table = _table()
        before = copy.deepcopy(table.map)
        with pytest.raises(ContextError) as error:
            table.insert(row["variant"], row["path"])
        assert error.value.failure_class == row["expect_failure"]
        assert table.map == before
        repaired = {"variant": row["variant"], "path": row["path"], **row["repair"]}
        table.insert(repaired["variant"], repaired["path"])


def test_rollback_rejections_are_bit_identical_and_recover():
    for row in CASES["rollback"]:
        table = _table()
        for path in row["setup_paths"]:
            table.insert("standard", path)
        before = copy.deepcopy(table.map)
        with pytest.raises(ContextError) as error:
            table.insert(row["variant"], row["rejected_path"])
        assert error.value.failure_class == row["expect_failure"]
        assert table.map == before
        table.insert("standard", row["then_path"])
        assert [list(record) for record in table.serialize()] == row["expect_serialized"]


def test_exhaustive_compatible_payload_substitution_closure():
    for section in SECTIONS:
        rows = CASES[section]
        for target_index, donor_index in itertools.permutations(range(len(rows)), 2):
            target, donor = rows[target_index], rows[donor_index]
            mutant = copy.deepcopy(CASES)
            replacement = copy.deepcopy(donor)
            replacement["name"] = target["name"]
            replacement["scenario_tag"] = target["scenario_tag"]
            mutant[section][target_index] = replacement
            with pytest.raises(AssertionError):
                _validate(mutant)


def test_each_shared_payload_field_is_manifest_bound():
    for section in SECTIONS:
        rows = CASES[section]
        for target_index, donor_index in itertools.permutations(range(len(rows)), 2):
            for field in (set(rows[target_index]) & set(rows[donor_index])) - {"name"}:
                if rows[target_index][field] == rows[donor_index][field]:
                    continue
                mutant = copy.deepcopy(CASES)
                mutant[section][target_index][field] = copy.deepcopy(rows[donor_index][field])
                with pytest.raises(AssertionError):
                    _validate(mutant)


def test_manifest_guard_nonvacuously_rejects_executable_substitution():
    mutant = copy.deepcopy(CASES)
    target = mutant["happy"][0]
    donor = copy.deepcopy(mutant["happy"][1])
    donor["name"] = target["name"]
    donor["scenario_tag"] = target["scenario_tag"]
    mutant["happy"][0] = donor
    _execute_resolve(donor)
    with pytest.raises(AssertionError):
        _validate(mutant)


def test_schema_and_recursive_key_sets_are_closed():
    for bad in (True, 1.0, "1", 0, 2):
        mutant = copy.deepcopy(CASES)
        mutant["schema_version"] = bad
        with pytest.raises(AssertionError):
            _validate(mutant)
    for section in SECTIONS:
        mutant = copy.deepcopy(CASES)
        mutant[section][0]["rogue"] = None
        with pytest.raises(AssertionError):
            _validate(mutant)
