#!/usr/bin/env python3
"""Shared closure enforcement for domain contract lints.

A contract file is a CLOSED document: exact built-in-integer
schema_version, no undeclared top-level keys, no undeclared
contract sections, and no undeclared keys inside the failures
and errors sections. An undeclared key is hidden normative
semantics; the contract header's claim that structured fields
are the enforcement surface only holds when every key is
declared.
"""

from __future__ import annotations

from tools.variant_contract_lint import ContractError  # noqa: E402

FAILURE_KEYS = {"classes", "triggers", "mapping", "closed"}
ERROR_KEYS = {"closed_enum", "shape"}


def _key_diagnostic(keys):
    """Return a deterministic representation for arbitrary mapping keys."""
    return sorted((type(key).__name__, repr(key)) for key in keys)


def close_envelope(doc, *, contract_id, sections,
                   schema_version=1):
    """Close the file envelope and the contract section set;
    return the contract mapping."""
    if not isinstance(doc, dict):
        raise ContractError("contract document must be a mapping")
    if set(doc) != {"schema_version", "contract"}:
        raise ContractError(
            "top level must be exactly "
            f"{'schema_version', 'contract'}: {_key_diagnostic(doc)!r}")
    if type(doc["schema_version"]) is not int or \
            doc["schema_version"] != schema_version:
        raise ContractError(
            f"schema_version must be exact built-in int "
            f"{schema_version}")
    cc = doc["contract"]
    if not isinstance(cc, dict):
        raise ContractError("contract section missing")
    if cc.get("id") != contract_id:
        raise ContractError(
            f"contract id drifted: {cc.get('id')!r}")
    if set(cc) != set(sections):
        raise ContractError(
            f"contract sections drifted: "
            f"missing={_key_diagnostic(set(sections) - set(cc))} "
            f"extra={_key_diagnostic(set(cc) - set(sections))}")
    return cc


def close_failures(failures):
    if not isinstance(failures, dict):
        raise ContractError("failures must be a mapping")
    if set(failures) != FAILURE_KEYS:
        raise ContractError(
            f"failures keys must be exactly "
            f"{_key_diagnostic(FAILURE_KEYS)!r}: "
            f"{_key_diagnostic(failures)!r}")


def close_errors(errors, *, shape_keys):
    if not isinstance(errors, dict) or set(errors) != ERROR_KEYS:
        raise ContractError(
            f"errors keys must be exactly "
            f"{_key_diagnostic(ERROR_KEYS)!r}: "
            f"{_key_diagnostic(errors) if isinstance(errors, dict) else type(errors).__name__}")
    shape = errors.get("shape")
    if not isinstance(shape, dict) or set(shape) != set(shape_keys):
        raise ContractError(
            f"errors.shape keys must be exactly "
            f"{_key_diagnostic(shape_keys)!r}: "
            f"{_key_diagnostic(shape) if isinstance(shape, dict) else type(shape).__name__}")
