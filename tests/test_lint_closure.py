"""Contract lint closure battery: every domain contract lint
rejects envelope, section and nested-section mutations.

An undeclared key is hidden normative semantics; the contract
header's claim that structured fields are the enforcement
surface only holds when every key is declared. One parametrized
family covers every closed lint - a new contract lint opts in
by adding its row to LINTS.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import (  # noqa: E402
    conflict_contract_lint,
    diff_contract_lint,
    migration_contract_lint,
    version_contract_lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

LINTS = [
    ("conflict", conflict_contract_lint,
     "data/contracts/conflict.yaml"),
    ("diff", diff_contract_lint,
     "data/contracts/diff.yaml"),
    ("migration", migration_contract_lint,
     "data/contracts/migration.yaml"),
    ("version", version_contract_lint,
     "data/contracts/version.yaml"),
]


def _closure_mutants(doc):
    """The closure attack family over one contract document."""
    out = []

    def add(name, mutate):
        m = copy.deepcopy(doc)
        mutate(m)
        out.append((name, m))

    add("schema-version-older", lambda m: m.__setitem__(
        "schema_version", 0))
    add("schema-version-newer", lambda m: m.__setitem__(
        "schema_version", 2))
    add("schema-version-string", lambda m: m.__setitem__(
        "schema_version", "1"))
    add("schema-version-bool", lambda m: m.__setitem__(
        "schema_version", True))
    add("schema-version-missing", lambda m: m.__delitem__(
        "schema_version"))
    add("undeclared-top-level-key", lambda m: m.__setitem__(
        "unexpected", {}))
    add("undeclared-contract-key", lambda m: m[
        "contract"].__setitem__("new_semantics", {}))
    add("contract-id-drift", lambda m: m["contract"].__setitem__(
        "id", "drifted-id"))
    add("undeclared-failures-key", lambda m: m["contract"][
        "failures"].__setitem__("x", {}))
    add("undeclared-errors-key", lambda m: m["contract"][
        "errors"].__setitem__("x", {}))
    add("undeclared-errors-shape-key", lambda m: m["contract"][
        "errors"]["shape"].__setitem__("x", {}))
    return out


def _boundary_mutants(doc):
    """Type-hostile keys and non-mapping closed sections."""
    out = []

    def add(name, mutate):
        m = copy.deepcopy(doc)
        mutate(m)
        out.append((name, m))

    hostile = [("int", 7), ("bool", False), ("null", None)]
    locations = [
        ("top", lambda m: m),
        ("contract", lambda m: m["contract"]),
        ("failures", lambda m: m["contract"]["failures"]),
        ("errors", lambda m: m["contract"]["errors"]),
        ("errors-shape", lambda m: m["contract"]["errors"]["shape"]),
    ]
    for key_name, key in hostile:
        for location_name, select in locations:
            add(f"{location_name}-{key_name}-key",
                lambda m, k=key, pick=select: pick(m).__setitem__(k, {}))
    # YAML sequence keys are unhashable and rejected by safe_load, so no
    # parseable sequence-key mutant exists at this API boundary.
    for location_name, select in locations[1:]:
        add(f"{location_name}-non-mapping",
            lambda m, pick=select: _replace_selected(m, pick, []))
    return out


def _replace_selected(doc, select, value):
    target = select(doc)
    # Locate by identity so the same selector table drives key and value cases.
    def walk(node):
        if not isinstance(node, dict):
            return False
        for key, child in list(node.items()):
            if child is target:
                node[key] = value
                return True
            if walk(child):
                return True
        return False
    assert walk(doc)


@pytest.mark.parametrize("name,module,contract_path", LINTS,
                         ids=[n for n, _m, _c in LINTS])
def test_lint_closure(name, module, contract_path, tmp_path):
    doc = yaml.safe_load(
        (ROOT / contract_path).read_text())
    mutants = _closure_mutants(doc) + _boundary_mutants(doc)
    for label, m in mutants:
        # never silent: every mutant genuinely alters the
        # document (serialized comparison is type-aware)
        # Python considers bool equal to int; serialization preserves types.
        assert yaml.safe_dump(m) != yaml.safe_dump(doc), label
        path = tmp_path / f"{name}-{label}.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            module.lint(path)
