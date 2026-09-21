"""T0096: closed, executable SAN conformance fixture."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from pathlib import Path

import pytest
import yaml

from tests.test_t0095_san_contract import SanError, emit_san, resolve_san

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "san" / "cases.json"
CONTRACT = ROOT / "data" / "contracts" / "san.yaml"
CASES = json.loads(FIXTURE.read_text())
DOC = yaml.safe_load(CONTRACT.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema_version", "contract", "contract_schema_version", "section_manifests", *SECTIONS}
STATE_KEYS = {"occupied", "side_to_move", "castling_rights"}
MOVE_KEYS = {"from_square", "to_square", "promotion"}
COMMON = {"name", "scenario_tag", "san", "state"}
CASE_KEYS = {
    "happy": COMMON | {"expect_move"},
    "boundary": COMMON | {"expect_move"},
    "malformed": COMMON | {"repair_san", "expect_failure"},
    "rollback": COMMON | {"expect_failure"},
}
FAILURES = set(DOC["contract"]["failure_classes"])


def _typed_keys(value):
    return sorted((type(key).__name__, repr(key)) for key in value)


def _strict_keys(value, expected, where):
    assert type(value) is dict, f"{where}: expected object"
    assert set(value) == expected, (
        f"{where}: keys {_typed_keys(value)} != {_typed_keys({key: None for key in expected})}"
    )


def _validate_state(state, where):
    _strict_keys(state, STATE_KEYS, where)
    assert state["side_to_move"] in {"w", "b"}
    assert type(state["castling_rights"]) is str
    assert type(state["occupied"]) is dict
    for square, piece in state["occupied"].items():
        assert type(square) is str and type(piece) is str


def _validate(cases):
    _strict_keys(cases, TOP_KEYS, "root")
    assert type(cases["schema_version"]) is int
    assert cases["schema_version"] == 1
    assert type(cases["contract_schema_version"]) is int
    assert cases["contract_schema_version"] == DOC["schema_version"]
    assert cases["contract"] == "data/contracts/san.yaml"
    _strict_keys(cases["section_manifests"], set(SECTIONS), "manifests")
    all_names = []
    all_tags = []
    for section in SECTIONS:
        rows = cases[section]
        assert type(rows) is list and rows
        names = []
        for index, case in enumerate(rows):
            where = f"{section}[{index}]"
            _strict_keys(case, CASE_KEYS[section], where)
            assert type(case["name"]) is str and case["name"]
            assert type(case["scenario_tag"]) is str and ":" in case["scenario_tag"]
            assert type(case["san"]) is str and case["san"]
            _validate_state(case["state"], f"{where}.state")
            if "expect_move" in case:
                assert type(case["expect_move"]) is dict
                assert set(case["expect_move"]) <= MOVE_KEYS
                assert {"from_square", "to_square"} <= set(case["expect_move"])
            if "expect_failure" in case:
                assert case["expect_failure"] in FAILURES
            if "repair_san" in case:
                assert type(case["repair_san"]) is str and case["repair_san"]
                assert case["repair_san"] != case["san"]
            names.append(case["name"])
            all_names.append(case["name"])
            all_tags.append(case["scenario_tag"])
        manifest = cases["section_manifests"][section]
        assert type(manifest) is dict
        assert set(manifest) == set(names)
        computed = {
            case["name"]: hashlib.sha256(
                json.dumps(
                    case,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            for case in rows
        }
        assert manifest == computed
    assert len(all_names) == len(set(all_names))
    assert len(all_tags) == len(set(all_tags))


def _run_success(case):
    move = resolve_san(DOC["contract"], case["san"], case["state"])
    assert move == case["expect_move"]
    assert emit_san(DOC["contract"], move, case["state"]) == case["san"]


def _failure(case, san=None):
    with pytest.raises(SanError) as error:
        resolve_san(DOC["contract"], case["san"] if san is None else san, case["state"])
    return error.value.failure_class


def test_fixture_structure_and_contract_pin():
    _validate(CASES)


@pytest.mark.parametrize("section", SECTIONS)
def test_closed_manifest_detects_reorder_drop_and_extra(section):
    manifest = CASES["section_manifests"][section]
    names = [case["name"] for case in CASES[section]]
    assert list(manifest) == names
    assert len(names) == len(set(names))


@pytest.mark.parametrize("section", ("happy", "boundary"))
def test_success_cases_execute(section):
    for case in CASES[section]:
        _run_success(case)


def test_malformed_cases_reject_and_repairs_succeed():
    for case in CASES["malformed"]:
        assert _failure(case) == case["expect_failure"]
        repaired = copy.deepcopy(case)
        repaired["san"] = case["repair_san"]
        move = resolve_san(DOC["contract"], repaired["san"], repaired["state"])
        assert emit_san(DOC["contract"], move, repaired["state"]) == repaired["san"]


def test_rollback_rejections_are_bit_identical():
    for case in CASES["rollback"]:
        before = copy.deepcopy(case["state"])
        assert _failure(case) == case["expect_failure"]
        assert case["state"] == before
        assert json.dumps(case["state"], sort_keys=True) == json.dumps(before, sort_keys=True)


def test_exhaustive_ordered_pairwise_scenario_substitution_closure():
    """Every compatible donor payload is bound to its exact case identity."""
    for section in SECTIONS:
        rows = CASES[section]
        for target_index, donor_index in itertools.permutations(range(len(rows)), 2):
            target = rows[target_index]
            donor = rows[donor_index]
            mutant = copy.deepcopy(CASES)
            replacement = copy.deepcopy(donor)
            replacement["name"] = target["name"]
            replacement["scenario_tag"] = target["scenario_tag"]
            mutant[section][target_index] = replacement
            with pytest.raises(AssertionError):
                _validate(mutant)


def test_each_payload_field_and_scenario_tag_is_manifest_bound():
    for section in SECTIONS:
        rows = CASES[section]
        for target_index, donor_index in itertools.permutations(range(len(rows)), 2):
            shared = (set(rows[target_index]) & set(rows[donor_index])) - {"name"}
            for field in shared:
                if rows[target_index][field] == rows[donor_index][field]:
                    continue
                mutant = copy.deepcopy(CASES)
                mutant[section][target_index][field] = copy.deepcopy(rows[donor_index][field])
                with pytest.raises(AssertionError):
                    _validate(mutant)


def test_manifest_guard_is_non_vacuous_for_executable_substitution():
    mutant = copy.deepcopy(CASES)
    target = mutant["happy"][0]
    donor = copy.deepcopy(mutant["happy"][1])
    donor["name"] = target["name"]
    donor["scenario_tag"] = target["scenario_tag"]
    mutant["happy"][0] = donor
    _run_success(donor)  # substituted payload is valid on its own
    with pytest.raises(AssertionError):
        _validate(mutant)  # only the closed semantic manifest catches it


def test_schema_version_and_recursive_shape_mutations_fail():
    for bad in (True, 1.0, "1", 0, 2):
        mutant = copy.deepcopy(CASES)
        mutant["schema_version"] = bad
        with pytest.raises(AssertionError):
            _validate(mutant)
    for path, key in [(("happy", 0), "rogue"), (("happy", 0, "state"), "rogue")]:
        mutant = copy.deepcopy(CASES)
        node = mutant
        for part in path:
            node = node[part]
        node[key] = None
        with pytest.raises(AssertionError):
            _validate(mutant)
