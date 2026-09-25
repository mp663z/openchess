"""T0437 reference compatibility model, not a shipped implementation.

A later implement task must independently implement the contract and run these
rows against that implementation. Only behavioral rows count as mutant kills.
"""
from __future__ import annotations

# ruff: noqa: E501  (explicit behavioral rows)
import copy
import re
from pathlib import Path

import pytest
import yaml

from tools.contract_lint import lint as source_lint
from tools.control_plane_versioning_contract_lint import CONTRACT, lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
BASE = yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text())


class VersionError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.failure_class = "malformed_version_request"
        self.code = "malformed_request"
        self.retryable = False


def _base(doc):
    base = doc["contract"]["versioning"]["base_path"]
    match = re.fullmatch(r"/cp/v([1-9][0-9]{0,2})", base) if type(base) is str else None
    if match is None:
        raise VersionError("base path")
    return int(match.group(1))


def _source(doc):
    if type(doc) is not dict or type(doc.get("schema_version")) is not int or doc["schema_version"] != 2:
        raise VersionError("source")
    failed = False
    try:
        source_lint(doc)
        base = _base(doc)
    except BaseException:
        failed = True
    if failed:
        raise VersionError("source invalid")
    return base


def _fields(old, new, side):
    if type(old) is not dict or type(new) is not dict:
        raise VersionError("fields")
    for key, spec in old.items():
        if type(key) is not str or key not in new:
            return False
        if spec != new[key]:
            return False
    for key, spec in new.items():
        if type(key) is not str:
            raise VersionError("field key")
        if key not in old and spec.get("required") is not False:
            return False
    return True


def _minor(old, new):
    # Compare all existing non-field declarations; only request/response
    # additions and new operations are on the MINOR allowlist.
    for key, value in old["contract"].items():
        if key == "versioning":
            if value != new["contract"].get(key):
                return False
        elif value != new["contract"].get(key):
            return False
    for area, old_area in old["areas"].items():
        new_area = new["areas"].get(area)
        if new_area is None:
            return False
        if {k: v for k, v in old_area.items() if k != "ops"} != {k: v for k, v in new_area.items() if k != "ops"}:
            return False
        for name, prior in old_area["ops"].items():
            current = new_area["ops"].get(name)
            if current is None:
                return False
            for k, value in prior.items():
                if k not in ("request", "response") and current.get(k) != value:
                    return False
            if not all(_fields(prior[side]["fields"], current[side]["fields"], side)
                       for side in ("request", "response")):
                return False
    old_routes = {(spec["method"], spec["path"]) for area in old["areas"].values() for spec in area["ops"].values()}
    for name, area in new["areas"].items():
        for op_name, spec in area["ops"].items():
            if (name not in old["areas"] or op_name not in old["areas"][name]["ops"]) and (spec["method"], spec["path"]) in old_routes:
                return False
    return True


def compare(old, new, old_minor, new_minor):
    """Pure reference; result is exact bool; errors are typed and fresh."""
    if (type(old_minor) is not int or type(new_minor) is not int
            or not 0 <= old_minor <= 2147483647 or not 0 <= new_minor <= 2147483647):
        raise VersionError("minor")
    older, newer = _source(old), _source(new)
    if newer < older or (newer == older and new_minor < old_minor):
        raise VersionError("version order")
    if newer > older:
        return True  # New major/base path admits breaking declarations.
    return _minor(old, new)


def fresh():
    return copy.deepcopy(BASE), copy.deepcopy(BASE)


def added_field(new, side, *, required=False):
    fields = new["areas"]["identity"]["ops"]["register"][side]["fields"]
    fields["new_flag"] = {"type": "boolean", "required": required}
    if required:
        fields["new_flag"]["example"] = True


def optional_to_required(new, side):
    fields = new["areas"]["identity"]["ops"]["register"][side]["fields"]
    key = "display_name"
    fields[key]["required"] = True
    if side == "response":
        fields[key]["example"] = "Ada"


def major(new):
    new["contract"]["versioning"]["base_path"] = "/cp/v2"


@pytest.mark.parametrize("side,change", [("request", added_field), ("response", added_field),
                                          ("request", optional_to_required), ("response", optional_to_required)])
def test_minor_and_major_edge(side, change):
    old, new = fresh()
    if change is added_field:
        change(new, side, required=True)
    else:
        change(new, side)
    before = copy.deepcopy((old, new))
    assert compare(old, new, 3, 4) is False
    assert (old, new) == before
    major(new)
    assert compare(old, new, 3, 0) is True


@pytest.mark.parametrize("side", ["request", "response"])
def test_optional_additions_are_minor_and_old_client_rolls_back(side):
    old, new = fresh()
    added_field(new, side)
    assert compare(old, new, 3, 4) is True
    assert compare(old, new, 0, 2147483647) is True


def test_new_required_request_field_refused_minor_accepted_major():
    old, new = fresh()
    added_field(new, "request", required=True)
    assert compare(old, new, 3, 4) is False
    major(new)
    assert compare(old, new, 3, 0) is True


def test_no_base_change_cannot_claim_major():
    old, new = fresh()
    added_field(new, "response", required=True)
    assert compare(old, new, 0, 1) is False
    major(new)
    assert compare(old, new, 0, 0) is True


@pytest.mark.parametrize("old_minor,new_minor", [(-1, 0), (0, -1), (0, 2147483648),
                                                   (True, 1), (0, 1.0), (2, 1)])
def test_bad_minors_are_typed_and_atomic(old_minor, new_minor):
    old, new = fresh()
    before = copy.deepcopy((old, new))
    with pytest.raises(VersionError) as error:
        compare(old, new, old_minor, new_minor)
    assert error.value.code == "malformed_request" and error.value.retryable is False
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert (old, new) == before


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version=True),
    lambda d: d["contract"]["versioning"].update(base_path="/cp/v1junk"),
    lambda d: d["areas"]["identity"]["ops"]["register"]["request"]["fields"].update({1: {"type": "string", "required": False}}),
    lambda d: d["areas"]["identity"]["ops"]["register"]["errors"].append([]),
])
def test_hostile_source_is_typed_and_atomic(mutation):
    old, new = fresh()
    mutation(new)
    before = copy.deepcopy((old, new))
    with pytest.raises(VersionError) as error:
        compare(old, new, 0, 1)
    assert error.value.code == "malformed_request" and error.value.__cause__ is None
    assert error.value.__context__ is None and (old, new) == before


def test_contract_lint_and_sibling_source():
    lint()
    assert yaml.safe_load(CONTRACT.read_text())["contract"]["source"]["contract"] == "data/contracts/control-plane.yaml"


@pytest.mark.parametrize("section,key,value", [
    ("compatibility", "response", "new-fields-may-be-required"),
    ("compatibility", "minor", "all-changes-additive"),
    ("compatibility", "request", "new-fields-may-be-required"),
    ("compatibility", "major", "same-base-path-allowed"),
])
def test_structured_rule_mutants_are_refused(tmp_path, section, key, value):
    doc = yaml.safe_load(CONTRACT.read_text())
    doc["contract"][section][key] = value
    path = tmp_path / "mutant.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)
