"""Production-only metamorphic checks independent of the closed fixture oracle."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from server.control_plane_versioning import VersionError, compare

SOURCE = Path(__file__).resolve().parents[1] / "data/contracts/control-plane.yaml"


def sample():
    return yaml.safe_load(SOURCE.read_text())


def test_acyclic_aliases_are_not_cycles():
    old, new = sample(), sample()
    shared = {"type": "boolean", "required": False}
    for side in ("request", "response"):
        new["areas"]["identity"]["ops"]["register"][side]["fields"]["extra_flag"] = shared
    assert compare(old, new, 0, 1) is True
    assert compare(new, new, 1, 1) is True


def test_new_area_and_new_operation_with_both_allowlist_classes():
    old, new = sample(), sample()
    new["areas"]["reports"] = {
        "ops": {
            "get": {
                "method": "GET", "path": "/reports", "auth": "public", "mutating": False,
                "request": {"fields": {}}, "response": {"fields": {}},
                "errors": ["internal"],
            }
        }
    }
    new["contract"]["transport"]["auth"]["public_operations"].append("reports.get")
    new["contract"]["transport"]["read_only_operations"].append("reports.get")
    assert compare(old, new, 0, 0) is True
    # A valid source can lose an existing operation only on a major transition.
    assert compare(new, old, 0, 1) is False
    old["contract"]["versioning"]["base_path"] = "/cp/v2"
    assert compare(new, old, 1, 0) is True


@pytest.mark.parametrize("side", ["request", "response"])
def test_nested_optional_then_required_is_breaking(side):
    old, new = sample(), sample()
    object_fields = next(
        spec["fields"]
        for area in new["areas"].values() for op in area["ops"].values()
        for spec in op[side]["fields"].values() if spec["type"] == "object"
    )
    name = "extra_flag"
    object_fields[name] = {"type": "boolean", "required": False}
    assert compare(old, new, 1, 2) is True
    object_fields[name]["required"] = True
    assert compare(old, new, 1, 2) is False


def test_both_sources_validated_before_major_success():
    old, new = sample(), sample()
    new["contract"]["versioning"]["base_path"] = "/cp/v2"
    old["areas"]["identity"]["ops"]["refresh"]["errors"].append("absent_code")
    original = copy.deepcopy((old, new))
    errors = []
    for _ in range(2):
        with pytest.raises(VersionError) as caught:
            compare(old, new, 0, 0)
        errors.append(caught.value)
        assert caught.value.__context__ is None
    assert errors[0] is not errors[1]
    assert (old, new) == original


def test_same_major_different_prefix_cannot_claim_minor():
    old, new = sample(), sample()
    new["contract"]["versioning"]["base_path"] = "/other/v1"
    assert compare(old, new, 0, 1) is False


def test_deep_opaque_metadata_is_valid_but_breaking():
    old, new = sample(), sample()
    nested = "opaque"
    for _ in range(65):
        nested = [nested]
    new["contract"]["privacy"]["logs"] = nested
    # The strict source derivation accepts an opaque metadata list here;
    # it is a changed non-operation declaration, not a minor addition.
    assert compare(old, new, 0, 1) is False


def test_extreme_nesting_and_metadata_cycle_remain_typed_refusals():
    old, new = sample(), sample()
    nested = []
    for _ in range(1100):
        nested = [nested]
    new["contract"]["privacy"]["logs"] = nested
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)
    cycle = []
    cycle.append(cycle)
    new["contract"]["privacy"]["logs"] = cycle
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)
