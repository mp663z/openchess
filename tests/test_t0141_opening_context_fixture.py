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
TOP = {
    "schema_version",
    "contract",
    "contract_schema_version",
    "section_manifests",
    *SECTIONS,
}
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

PINNED_MANIFEST = {
    "happy": {
        "sicilian-exact": "37253e0e355bc5ce0daab1f57eb8fe76483fbc8ed3d967983f0a4b5cbc1bc985",
        "najdorf-longest-prefix": (
            "9811b08fb4d2f6733e4813b60f25b2a394c37da26f6c16b916324a6c5a03db84"
        ),
        "italian-deeper-prefix": "99a6a30be0b58458daf21b2f37b7b0b727979ba57a5ca720de902dd2b990954b",
    },
    "boundary": {
        "empty-unclassified": "c620003fb09c445ac034cbd3b4c6ee077742a1f1f7ee10a4833e46820782b763",
        "unmatched-unclassified": (
            "9fbe2e977517662222f0002a74f634203f56d322dc99b0c8beb638dd1c86d46a"
        ),
        "extension-stable": "d55287d1009659afe65dba7409dd3462a4d6a8291e755ca9f4ec75360c68a38b",
    },
    "malformed": {
        "unknown-variant": "45170848b1a95a4490b7b295b52d09844076a1153487d39226cd1f8f445b4472",
        "path-not-list": "0c415a57d7a3b53578d07dcb065d55c6ad7f0ac87d5fd27f2a16d83b1c119fb9",
        "same-square-move": "e721ea6d42962e56261b061bef8e5aa205361d1640f3efd1fcf248fc6cb18203",
        "bad-promotion": "da2cbdaacf10115269ddcd9ff59c1b3430e16bec64d31326b6818ba55bef7a88",
    },
    "rollback": {
        "malformed-after-sicilian": (
            "7503ab2e237e82aa7116b4a3d4f434b5d0fb0df5c4bc6787366d0ff5a017e541"
        ),
        "unknown-after-empty": "3eadae0b75accddd742033c882fd8c4ddbe4441cbd46237c0514666167b93bb5",
    },
}
REPAIR_KEYS = {
    "unknown-variant": {"variant"},
    "path-not-list": {"path"},
    "same-square-move": {"path"},
    "bad-promotion": {"path"},
}


def _typed_keys(value):
    return sorted((type(key).__name__, repr(key)) for key in value)


def _keys(value, expected, where):
    assert type(value) is dict, f"{where}: object required"
    assert set(value) == expected, f"{where}: {_typed_keys(value)}"


def _digest(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _path(value, where):
    assert type(value) is list, f"{where}: list required"
    for index, move in enumerate(value):
        assert type(move) is str, f"{where}[{index}]: string required"


def _serialized(value, where):
    assert type(value) is list
    for index, record in enumerate(value):
        assert type(record) is list and len(record) == 4, f"{where}[{index}]"
        assert all(type(field) is str for field in record)


def _rehash(mutant, section, index):
    row = mutant[section][index]
    mutant["section_manifests"][section][row["name"]] = _digest(row)


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
            if section in ("happy", "boundary"):
                assert type(row["variant"]) is str
                _path(row["path"], f"{section}[{index}].path")
                assert type(row["expect_code"]) is str
                assert type(row["expect_name"]) is str
            elif section == "malformed":
                assert type(row["variant"]) is str
                if row["name"] != "path-not-list":
                    _path(row["path"], f"malformed[{index}].path")
                else:
                    assert type(row["path"]) is str
                assert row["expect_failure"] in FAILURES
                _keys(row["repair"], REPAIR_KEYS[row["name"]], f"repair {row['name']}")
                if "path" in row["repair"]:
                    _path(row["repair"]["path"], f"repair {row['name']}.path")
                if "variant" in row["repair"]:
                    assert type(row["repair"]["variant"]) is str
            else:
                assert type(row["setup_paths"]) is list
                for path_index, path in enumerate(row["setup_paths"]):
                    _path(path, f"rollback[{index}].setup_paths[{path_index}]")
                assert type(row["variant"]) is str
                _path(row["rejected_path"], f"rollback[{index}].rejected_path")
                _path(row["then_path"], f"rollback[{index}].then_path")
                _serialized(row["expect_serialized"], f"rollback[{index}].expect_serialized")
                assert row["expect_failure"] in FAILURES
            names.append(row["name"])
            tags.append(row["scenario_tag"])
        manifest = cases["section_manifests"][section]
        assert type(manifest) is dict
        assert list(manifest) == [row["name"] for row in rows]
        computed = {row["name"]: _digest(row) for row in rows}
        assert manifest == computed
        assert manifest == PINNED_MANIFEST[section]
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
            _rehash(mutant, section, target_index)
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
                _rehash(mutant, section, target_index)
                with pytest.raises(AssertionError):
                    _validate(mutant)


def test_manifest_guard_nonvacuously_rejects_executable_substitution():
    mutant = copy.deepcopy(CASES)
    target = mutant["happy"][0]
    donor = copy.deepcopy(mutant["happy"][1])
    donor["name"] = target["name"]
    donor["scenario_tag"] = target["scenario_tag"]
    mutant["happy"][0] = donor
    _rehash(mutant, "happy", 0)
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
        _rehash(mutant, section, 0)
        with pytest.raises(AssertionError):
            _validate(mutant)


def test_nested_rogue_missing_and_wrong_types_fail_even_after_rehash():
    mutations = []
    rogue = copy.deepcopy(CASES)
    rogue["malformed"][0]["repair"]["rogue"] = "ignored"
    mutations.append((rogue, "malformed", 0))
    missing = copy.deepcopy(CASES)
    missing["malformed"][0]["repair"].pop("variant")
    mutations.append((missing, "malformed", 0))
    wrong_path = copy.deepcopy(CASES)
    wrong_path["happy"][0]["path"][0] = 23
    mutations.append((wrong_path, "happy", 0))
    wrong_setup = copy.deepcopy(CASES)
    wrong_setup["rollback"][0]["setup_paths"][0] = "e2e4"
    mutations.append((wrong_setup, "rollback", 0))
    wrong_serialized = copy.deepcopy(CASES)
    wrong_serialized["rollback"][0]["expect_serialized"][0].append("rogue")
    mutations.append((wrong_serialized, "rollback", 0))
    bad_manifest = copy.deepcopy(CASES)
    bad_manifest["section_manifests"]["happy"]["sicilian-exact"] = 23
    mutations.append((bad_manifest, None, None))
    for mutant, section, index in mutations:
        if section is not None:
            _rehash(mutant, section, index)
        with pytest.raises(AssertionError):
            _validate(mutant)
