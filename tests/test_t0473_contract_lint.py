"""T0473 v2: normative contract lint - the verifier's adversarial
mutations plus the structural sweep, each rejected for its own reason."""

from __future__ import annotations

import copy

import pytest
import yaml

from tools.contract_lint import CONTRACT, ContractError, lint

DOC = yaml.safe_load(CONTRACT.read_text())


def test_real_contract_is_clean():
    lint(copy.deepcopy(DOC))


def _mutate(fn):
    doc = copy.deepcopy(DOC)
    fn(doc)
    return doc


def _vendor_route(d):
    d["areas"]["provider_routing"]["ops"]["route"]["response"][
        "fields"]["openai_api_key"] = {"type": "string", "required": True,
                                       "example": "x"}


MUTATIONS = {
    # The verifier's five adversarial mutations:
    "auth_section_removed": lambda d: d["contract"]["transport"].pop(
        "auth"),
    "vendor_specific_route_response": _vendor_route,
    "privacy_allow_all": lambda d: d["contract"].__setitem__(
        "privacy", {"policy": "allow all secrets and chess FEN"}),
    "delete_account_not_mutating": lambda d: d["areas"]["identity"]
        ["ops"]["delete_account"].__setitem__("mutating", False),
    "rollback_thin": lambda d: d["contract"]["versioning"].__setitem__(
        "rule", "Rollback exists."),
    # Structural sweep:
    "missing_area": lambda d: d["areas"].pop("billing"),
    "extra_area": lambda d: d["areas"].__setitem__(
        "chess_engine", {"ops": {"go": {"method": "POST", "path": "/x",
        "auth": "required", "mutating": True,
        "request": {"fields": {}}, "response": {"fields": {}},
        "errors": ["internal", "idempotency_conflict"]}}}),
    "error_outside_enum": lambda d: d["areas"]["identity"]["ops"]
        ["register"]["errors"].append("made_up_code"),
    "duplicate_enum_codes": lambda d: d["contract"]["transport"]
        ["errors"]["closed_enum"].append("internal"),
    "mutating_without_conflict_error": lambda d: d["areas"]["quota"]
        ["ops"]["reserve"]["errors"].remove("idempotency_conflict"),
    "get_marked_mutating": lambda d: d["areas"]["entitlements"]["ops"]
        ["get"].__setitem__("mutating", True),
    "public_ops_disagree": lambda d: d["contract"]["transport"]["auth"]
        ["public_operations"].append("quota.reserve"),
    "issued_by_not_public": lambda d: d["contract"]["transport"]["auth"]
        .__setitem__("issued_by", ["quota.reserve"]),
    "issued_by_unknown_op": lambda d: d["contract"]["transport"]["auth"]
        .__setitem__("issued_by", ["identity.magic"]),
    "required_field_without_example": lambda d: d["areas"]["identity"]
        ["ops"]["register"]["request"]["fields"]["email"].pop("example"),
    "example_wrong_type": lambda d: d["areas"]["identity"]["ops"]
        ["register"]["request"]["fields"]["email"].__setitem__(
        "example", 42),
    "example_bool_as_int": lambda d: d["areas"]["quota"]["ops"]
        ["reserve"]["request"]["fields"]["estimated_units"].__setitem__(
        "example", True),
    "response_key_material": lambda d: d["areas"]["provider_routing"]
        ["ops"]["register_key"]["response"]["fields"].__setitem__(
        "key_material", {"type": "string", "required": True,
                         "example": "x"}),
    "unknown_spec_key": lambda d: d["areas"]["identity"]["ops"]
        ["register"]["request"]["fields"]["email"].__setitem__(
        "pattern", ".*"),
    "missing_schema_version": lambda d: d.pop("schema_version"),
    "bool_schema_version": lambda d: d.__setitem__("schema_version",
                                                   True),
    "empty_ops": lambda d: d["areas"]["identity"].__setitem__("ops", {}),
    "bad_method": lambda d: d["areas"]["identity"]["ops"]["login"]
        .__setitem__("method", "FETCH"),
    "auth_flag_invalid": lambda d: d["areas"]["identity"]["ops"]
        ["login"].__setitem__("auth", "sometimes"),
    "cost_not_server_side": lambda d: d["contract"]["cost"].__setitem__(
        "caps_enforced", "client-side"),
    "unversioned_base_path": lambda d: d["contract"]["versioning"]
        .__setitem__("base_path", "/cp"),
    "idempotency_conflict_not_in_enum": lambda d: d["contract"]
        ["transport"]["errors"]["closed_enum"].remove(
        "idempotency_conflict"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_mutation_is_rejected(name):
    with pytest.raises(ContractError):
        lint(_mutate(MUTATIONS[name]))


def test_non_dict_document_is_rejected():
    with pytest.raises(ContractError):
        lint(["not", "a", "mapping"])
