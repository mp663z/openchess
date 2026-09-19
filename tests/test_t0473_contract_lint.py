"""T0473: contract lint - happy passes; boundary/malformed/rollback each
rejected for their own reason (whole-structure sweep, not whack-a-mole)."""

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


MUTATIONS = {
    "missing_area": lambda d: d["areas"].pop("billing"),
    "extra_area": lambda d: d["areas"].__setitem__(
        "chess_engine", {"ops": {"go": {"method": "POST", "path": "/x",
        "mutating": True, "request": "{}", "response": "{}",
        "errors": ["internal"]}}}),
    "error_outside_enum": lambda d: d["areas"]["identity"]["ops"]
        ["register"]["errors"].append("made_up_code"),
    "duplicate_enum_codes": lambda d: d["contract"]["transport"]
        ["errors"]["closed_enum"].append("internal"),
    "rollback_rule_removed": lambda d: d["contract"]["versioning"]
        .__setitem__("rule", "Clients pin MAJOR. MINOR is additive."),
    "non_semver_scheme": lambda d: d["contract"]["versioning"]
        .__setitem__("scheme", "calver"),
    "unversioned_base_path": lambda d: d["contract"]["versioning"]
        .__setitem__("base_path", "/cp"),
    "idempotency_header_unnamed": lambda d: d["contract"]["transport"]
        .__setitem__("idempotency", "mutations are idempotent somehow"),
    "mutating_op_without_errors": lambda d: d["areas"]["quota"]["ops"]
        ["reserve"].__setitem__("errors", []),
    "missing_schema_version": lambda d: d.pop("schema_version"),
    "empty_ops": lambda d: d["areas"]["identity"].__setitem__("ops", {}),
    "bad_method": lambda d: d["areas"]["identity"]["ops"]["login"]
        .__setitem__("method", "FETCH"),
    "response_not_string": lambda d: d["areas"]["identity"]["ops"]
        ["login"].__setitem__("response", {"token": "str"}),
    "missing_privacy_policy": lambda d: d["contract"].pop("privacy"),
    "bool_as_int_schema_version": lambda d: d.__setitem__(
        "schema_version", True),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_mutation_is_rejected(name):
    with pytest.raises(ContractError):
        lint(_mutate(MUTATIONS[name]))


def test_non_dict_document_is_rejected():
    with pytest.raises(ContractError):
        lint(["not", "a", "mapping"])


def test_non_dict_area_is_rejected():
    doc = _mutate(lambda d: d["areas"].__setitem__("identity", "oops"))
    with pytest.raises(ContractError):
        lint(doc)
