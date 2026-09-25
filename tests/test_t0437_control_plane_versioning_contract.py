"""T0437 reference compatibility model, not a shipped implementation.

A later implement task must independently implement the contract and run these
rows against that implementation. Only behavioral rows count as mutant kills.
"""
from __future__ import annotations

import ast

# ruff: noqa: E501  (explicit behavioral rows)
import contextlib
import copy
import inspect
import re
from pathlib import Path

import pytest
import yaml

from tests.test_t0419_openapi_contract import derive as strict_source_derivation
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
        strict_source_derivation(doc)
        base = _base(doc)
    except Exception:
        failed = True
    if failed:
        raise VersionError("source invalid")
    return base


def _fields(old, new, side):
    if type(old) is not dict or type(new) is not dict:
        raise VersionError("fields")
    for key, spec in old.items():
        if type(key) is not str:
            raise VersionError("field key")
        if key not in new:
            return False
        current = new[key]
        if type(spec) is not dict or type(current) is not dict:
            raise VersionError("field spec")
        if spec.get("type") == "object" and current.get("type") == "object":
            if {k: v for k, v in spec.items() if k != "fields"} != {k: v for k, v in current.items() if k != "fields"}:
                return False
            if not _fields(spec["fields"], current["fields"], side):
                return False
        elif spec != current:
            return False
    for key, spec in new.items():
        if type(key) is not str or type(spec) is not dict:
            raise VersionError("field key or spec")
        if key not in old and spec.get("required") is not False:
            return False
    return True


def _minor(old, new):
    # Compare all existing non-field declarations; only request/response
    # additions and new operations are on the MINOR allowlist.
    old_contract = copy.deepcopy(old["contract"])
    new_contract = copy.deepcopy(new["contract"])
    old_transport = old_contract["transport"]
    new_transport = new_contract["transport"]
    new_ops = [f"{area}.{op}" for area, spec in new["areas"].items()
               for op in spec["ops"] if area not in old["areas"]
               or op not in old["areas"][area]["ops"]]
    for list_name, old_list, new_list in (
        ("read_only_operations", old_transport["read_only_operations"],
         new_transport["read_only_operations"]),
        ("public_operations", old_transport["auth"]["public_operations"],
         new_transport["auth"]["public_operations"]),
    ):
        added = [name for name in new_list if name not in old_list]
        expected = [name for name in new_ops if (new["areas"][name.split(".")[0]]["ops"][name.split(".")[1]]["mutating"] is False
                    if list_name == "read_only_operations" else new["areas"][name.split(".")[0]]["ops"][name.split(".")[1]]["auth"] == "public")]
        if (len(new_list) != len(set(new_list)) or set(new_list) != set(old_list) | set(expected)
                or len(added) != len(expected)):
            return False
    old_transport.pop("read_only_operations")
    new_transport.pop("read_only_operations")
    old_transport["auth"].pop("public_operations")
    new_transport["auth"].pop("public_operations")
    if old_contract != new_contract:
        return False
    if old["schema_version"] != new["schema_version"]:
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
    new_routes = set()
    for name, area in new["areas"].items():
        for op_name, spec in area["ops"].items():
            route = (spec["method"], spec["path"])
            if route in new_routes:
                return False
            new_routes.add(route)
            if (name not in old["areas"] or op_name not in old["areas"][name]["ops"]) and route in old_routes:
                return False
    return True


# -- reference begin
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

# -- reference end

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


def test_nested_optional_addition_is_minor():
    old, new = fresh()
    nested = new["areas"]["entitlements"]["ops"]["get"]["response"]["fields"]["cost_caps"]["fields"]
    nested["display_currency"] = {"type": "string", "required": False}
    assert compare(old, new, 1, 2) is True
    nested["display_currency"]["required"] = True
    nested["display_currency"]["example"] = "USD"
    assert compare(old, new, 1, 2) is False
    major(new)
    assert compare(old, new, 1, 0) is True


def test_hostile_nested_key_fails_typed_without_context():
    old, new = fresh()
    nested = new["areas"]["entitlements"]["ops"]["get"]["response"]["fields"]["cost_caps"]["fields"]
    nested[object()] = {"type": "string", "required": False}
    with pytest.raises(VersionError) as error:
        compare(old, new, 0, 1)
    assert error.value.__cause__ is None and error.value.__context__ is None


def test_new_operation_is_minor():
    old, new = fresh()
    op = copy.deepcopy(new["areas"]["identity"]["ops"]["refresh"])
    op["path"] = "/identity/restore"
    new["areas"]["identity"]["ops"]["restore"] = op
    assert compare(old, new, 0, 1) is True


def test_new_operation_with_route_collision_refused():
    old, new = fresh()
    op = copy.deepcopy(new["areas"]["identity"]["ops"]["login"])
    new["areas"]["identity"]["ops"]["new_login"] = op
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)


@pytest.mark.parametrize("change", [
    lambda n: n["areas"]["identity"]["ops"]["register"]["request"]["fields"].pop("email"),
    lambda n: n["areas"]["identity"]["ops"]["register"]["request"]["fields"]["email"].update(type="integer", example=1),
    lambda n: n["areas"]["identity"]["ops"]["refresh"].update(path="/identity/renew"),
    lambda n: n["areas"]["identity"]["ops"]["register"]["response"]["fields"]["display_name"].update(required=True, example="Ada"),
])
def test_existing_shape_changes_are_major_only(change):
    old, new = fresh()
    change(new)
    assert compare(old, new, 0, 1) is False
    major(new)
    assert compare(old, new, 0, 0) is True


def test_determinism_and_fresh_errors():
    old, new = fresh()
    assert compare(old, new, 0, 0) is True
    assert compare(old, new, 0, 0) is True
    errors = []
    for _ in range(2):
        with pytest.raises(VersionError) as error:
            compare(old, new, True, 0)
        errors.append(error.value)
    assert errors[0] is not errors[1]


def test_source_validator_is_t0419_strict_on_new_optional_nested_bad_type():
    old, new = fresh()
    nested = new["areas"]["entitlements"]["ops"]["get"]["response"]["fields"]["cost_caps"]["fields"]
    nested["display_currency"] = {"type": "string", "required": False, "example": "\ud800"}
    with pytest.raises(VersionError) as error:
        compare(old, new, 0, 1)
    assert error.value.code == "malformed_request" and error.value.__context__ is None


REFERENCE_MUTANTS = {
    "new-required-response-accepted": ("spec.get(\"required\") is not False", "side == \"request\" and spec.get(\"required\") is not False"),
    "optional-to-required-response-accepted": ("elif spec != current:\n            return False", "elif spec != current and side == \"request\":\n            return False"),
    "new-required-request-accepted": ("spec.get(\"required\") is not False", "side == \"response\" and spec.get(\"required\") is not False"),
    "optional-to-required-request-accepted": ("elif spec != current:\n            return False", "elif spec != current and side == \"response\":\n            return False"),
    "nested-optional-rejected": ("if not _fields(spec[\"fields\"], current[\"fields\"], side):", "if spec[\"fields\"] != current[\"fields\"]:"),
    "base-path-not-required-for-major": ("if newer > older:\n        return True", "if newer >= older:\n        return True"),
    "minor-missing-ceiling": ("new_minor <= 2147483647", "new_minor <= 2147483648"),
    "minor-downgrade-accepted": ("new_minor < old_minor", "new_minor > old_minor"),
    "operation-route-change-minor": ("current.get(k) != value", "False"),
    "strict-validator-removed": ("strict_source_derivation(doc)", "_base(doc)"),
    "other-contract-change-minor": ("if old_contract != new_contract:\n        return False", "if False:\n        return False"),
    "deleted-area-minor": ("if new_area is None:\n            return False", "if new_area is None:\n            continue"),
    "deleted-operation-minor": ("if current is None:\n                return False", "if current is None:\n                continue"),
    "object-metadata-ignored": ("if {k: v for k, v in spec.items() if k != \"fields\"} != {k: v for k, v in current.items() if k != \"fields\"}:\n                return False", "if False:\n                return False"),
    "major-downgrade-accepted": ("if newer < older or (newer == older and new_minor < old_minor):", "if newer == older and new_minor < old_minor:"),
    "old-minor-ceiling-off-by-one": ("old_minor <= 2147483647", "old_minor <= 2147483648"),
    "area-metadata-ignored": ("if {k: v for k, v in old_area.items() if k != \"ops\"} != {k: v for k, v in new_area.items() if k != \"ops\"}:\n            return False", "if False:\n            return False"),
    "existing-op-only-path-checked": ("current.get(k) != value", "k == 'path' and current.get(k) != value"),
    "new-area-omitted-from-new-ops": ("if area not in old[\"areas\"]\n               or op not in old[\"areas\"][area][\"ops\"]", "if op not in old[\"areas\"][area][\"ops\"]"),
    "allowlist-order-required": ("if (len(new_list) != len(set(new_list))", "if (new_list[:len(old_list)] != old_list or len(new_list) != len(set(new_list))"),
}


def _mutant_compare(name):
    old, replacement = REFERENCE_MUTANTS[name]
    source = inspect.getsource(_fields) + "\n" + inspect.getsource(_minor) + "\n" + inspect.getsource(compare)
    if name == "strict-validator-removed":
        source = inspect.getsource(_source) + "\n" + source
    assert source.count(old) == 1, name
    source = source.replace(old, replacement, 1)
    scope = dict(globals())
    exec(compile(ast.parse(source), f"<reference-mutant:{name}>", "exec"), scope)
    return scope["compare"]


@pytest.mark.parametrize("name", REFERENCE_MUTANTS)
def test_reference_mutant_red(name):
    mutant = _mutant_compare(name)
    old, new = fresh()
    if name == "new-required-response-accepted":
        added_field(new, "response", required=True)
        expected = False
    elif name == "optional-to-required-response-accepted":
        optional_to_required(new, "response")
        expected = False
    elif name == "new-required-request-accepted":
        added_field(new, "request", required=True)
        expected = False
    elif name == "optional-to-required-request-accepted":
        optional_to_required(new, "request")
        expected = False
    elif name == "nested-optional-rejected":
        new["areas"]["entitlements"]["ops"]["get"]["response"]["fields"]["cost_caps"]["fields"]["display_currency"] = {"type": "string", "required": False}
        expected = True
    elif name == "base-path-not-required-for-major":
        added_field(new, "response", required=True)
        expected = False
    elif name == "minor-missing-ceiling":
        with pytest.raises(VersionError):
            compare(old, new, 0, 2147483648)
        assert mutant(old, new, 0, 2147483648) is True
        return
    elif name == "minor-downgrade-accepted":
        with pytest.raises(VersionError):
            compare(old, new, 2, 1)
        assert mutant(old, new, 2, 1) is True
        return
    elif name == "operation-route-change-minor":
        new["areas"]["identity"]["ops"]["refresh"]["path"] = "/identity/renew"
        expected = False
    elif name == "other-contract-change-minor":
        new["contract"]["versioning"]["rule"] += " More text."
        expected = False
    elif name == "deleted-area-minor":
        del new["areas"]["quota"]
        assert compare(old, new, 0, 1) is False
        assert mutant(old, new, 0, 1) is True
        return
    elif name == "deleted-operation-minor":
        del new["areas"]["identity"]["ops"]["refresh"]
        expected = False
    elif name == "object-metadata-ignored":
        new["areas"]["entitlements"]["ops"]["get"]["response"]["fields"]["cost_caps"]["required"] = False
        expected = False
    elif name == "major-downgrade-accepted":
        major(old)
        with pytest.raises(VersionError):
            compare(old, new, 0, 1)
        assert mutant(old, new, 0, 1) is False
        return
    elif name == "area-metadata-ignored":
        new["areas"]["billing"]["pci_boundary"] += " New text."
        expected = False
    elif name == "existing-op-only-path-checked":
        new["areas"]["identity"]["ops"]["refresh"]["errors"].remove("idempotency_conflict")
        expected = False
    elif name == "new-area-omitted-from-new-ops":
        op = copy.deepcopy(new["areas"]["entitlements"]["ops"]["get"])
        op["path"] = "/zones"
        new["areas"]["zones"] = {"ops": {"get": op}}
        new["contract"]["transport"]["read_only_operations"].append("zones.get")
        assert compare(old, new, 0, 1) is True
        with pytest.raises(KeyError):
            mutant(old, new, 0, 1)
        return
    elif name == "allowlist-order-required":
        new["contract"]["transport"]["read_only_operations"].reverse()
        assert compare(old, new, 0, 1) is True
        assert mutant(old, new, 0, 1) is False
        return
    elif name == "old-minor-ceiling-off-by-one":
        major(new)
        with pytest.raises(VersionError):
            compare(old, new, 2147483648, 0)
        assert mutant(old, new, 2147483648, 0) is True
        return
    else:
        new["areas"]["entitlements"]["ops"]["get"]["response"]["fields"]["cost_caps"]["fields"]["display_currency"] = {"type": "string", "required": False, "example": "\ud800"}
        with pytest.raises(VersionError):
            compare(old, new, 0, 1)
        assert mutant(old, new, 0, 1) is True
        return
    assert compare(old, new, 0, 1) is expected
    assert mutant(old, new, 0, 1) is not expected


def test_new_get_operation_requires_and_preserves_readonly_allowlist():
    old, new = fresh()
    op = copy.deepcopy(new["areas"]["entitlements"]["ops"]["get"])
    op["path"] = "/entitlements/refresh-status"
    new["areas"]["entitlements"]["ops"]["refresh_status"] = op
    new["contract"]["transport"]["read_only_operations"].append("entitlements.refresh_status")
    assert compare(old, new, 0, 1) is True
    new["contract"]["transport"]["read_only_operations"].pop()
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)


def test_new_public_operation_requires_and_preserves_public_allowlist():
    old, new = fresh()
    op = copy.deepcopy(new["areas"]["identity"]["ops"]["login"])
    op["path"] = "/identity/recover"
    new["areas"]["identity"]["ops"]["recover"] = op
    new["contract"]["transport"]["auth"]["public_operations"].append("identity.recover")
    assert compare(old, new, 0, 1) is True
    new["contract"]["transport"]["auth"]["public_operations"].pop()
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)


@pytest.mark.parametrize("list_name", ["read_only_operations", "public_operations"])
def test_existing_operation_added_or_removed_from_allowlist_is_not_minor(list_name):
    old, new = fresh()
    parent = new["contract"]["transport"]
    names = parent["read_only_operations"] if list_name == "read_only_operations" else parent["auth"]["public_operations"]
    names.pop()
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)


def test_other_contract_change_is_not_minor():
    old, new = fresh()
    new["contract"]["versioning"]["rule"] += " More text."
    assert compare(old, new, 0, 1) is False


def test_deleted_area_is_not_minor():
    old, new = fresh()
    del new["areas"]["quota"]
    assert compare(old, new, 0, 1) is False


def test_deleted_operation_is_not_minor():
    old, new = fresh()
    del new["areas"]["identity"]["ops"]["refresh"]
    assert compare(old, new, 0, 1) is False


def test_existing_object_field_metadata_is_immutable_in_minor():
    old, new = fresh()
    new["areas"]["entitlements"]["ops"]["get"]["response"]["fields"]["cost_caps"]["required"] = False
    assert compare(old, new, 0, 1) is False


def test_major_downgrade_raises_typed_error():
    old, new = fresh()
    major(old)
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)


def test_old_minor_ceiling_even_for_major_upgrade():
    old, new = fresh()
    major(new)
    with pytest.raises(VersionError):
        compare(old, new, 2147483648, 0)


def test_allowlist_order_is_insignificant():
    old, new = fresh()
    new["contract"]["transport"]["read_only_operations"].reverse()
    new["contract"]["transport"]["auth"]["public_operations"].reverse()
    assert compare(old, new, 0, 1) is True


def test_multiple_new_ops_can_append_in_reverse_order():
    old, new = fresh()
    template = new["areas"]["entitlements"]["ops"]["get"]
    for name in ("summary", "totals"):
        op = copy.deepcopy(template)
        op["path"] = f"/entitlements/{name}"
        new["areas"]["entitlements"]["ops"][name] = op
    names = new["contract"]["transport"]["read_only_operations"]
    names.extend(["entitlements.totals", "entitlements.summary"])
    assert compare(old, new, 0, 1) is True
    names.append("entitlements.summary")
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)
    names.pop()
    names.remove("entitlements.totals")
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)


def test_area_metadata_change_is_not_minor():
    old, new = fresh()
    new["areas"]["billing"]["pci_boundary"] += " New text."
    assert compare(old, new, 0, 1) is False


@pytest.mark.parametrize("change", [
    lambda op: op["errors"].remove("idempotency_conflict"),
    lambda op: op.update(method="GET", mutating=False),
    lambda op: op.update(auth="public"),
])
def test_existing_operation_non_field_change_is_not_minor(change):
    old, new = fresh()
    op = new["areas"]["identity"]["ops"]["refresh"]
    change(op)
    if op["auth"] == "public":
        new["contract"]["transport"]["auth"]["public_operations"].append("identity.refresh")
    if op["mutating"] is False:
        new["contract"]["transport"]["read_only_operations"].append("identity.refresh")
    with contextlib.suppress(VersionError):
        assert compare(old, new, 0, 1) is False


def test_new_area_and_new_operation_is_minor():
    old, new = fresh()
    op = copy.deepcopy(new["areas"]["entitlements"]["ops"]["get"])
    op["path"] = "/zones"
    new["areas"]["zones"] = {"ops": {"get": op}}
    new["contract"]["transport"]["read_only_operations"].append("zones.get")
    assert compare(old, new, 0, 1) is True
