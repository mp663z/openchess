"""T0158: graph collision contract lint.

Enforces the normative CONTENT of data/contracts/collision.yaml by
EXACT STRUCTURED COMPARISON, never prose-marker presence: the
collision-semantics role (normal operation, never an error), the
exact collision definition (bucket key equal AND canonical identity
unequal; bucket key derived, never stored as identity), the
separation rule (canonical field comparison over exact stored
records; digest equality/inequality never decide), the guarantee
set (no-merge, no-fork, count-exact, order-insensitivity, merge
algebra preserved), the two-class failure model with its exact
mapping (no orphan classes, no undeclared codes), the error
surface shape, the behavioral properties, the link set,
versioning, and linkage VERIFIED AGAINST THE ACTUAL sibling
artifacts (variant hash_rule + id_grammar, digest format +
accelerator-only role, node identity). Anything less passes
silently and every accelerated table rots.

    python tools/collision_contract_lint.py [path]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "collision.yaml"
VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
DIGEST = ROOT / "data" / "contracts" / "position_digest.yaml"
NODE = ROOT / "data" / "contracts" / "transposition_node.yaml"

LINKS = {
    "variant_contract": "data/contracts/variant.yaml",
    "position_digest_contract": "data/contracts/position_digest.yaml",
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
}
ROLE = {
    "kind": "bucket-collision-semantics-for-accelerated-lookup",
    "collision_is": "normal-operation-never-an-error",
    "scope": "all-graph-tables-with-accelerated-bucket-lookup",
    "owns": "collision-definition-separation-and-guarantees-only",
    "links": {
        "variant": "data/contracts/variant.yaml",
        "position_digest": "data/contracts/position_digest.yaml",
        "transposition_node":
            "data/contracts/transposition_node.yaml",
    },
}
DEFINITION = {
    "collision": "bucket-key-equal-and-canonical-identity-unequal",
    "bucket_key": "derived-value-never-stored-as-identity",
    "identity":
        "from-linked-table-contract-identity-read-never-restated",
    "links": {
        "variant": "data/contracts/variant.yaml",
        "transposition_node":
            "data/contracts/transposition_node.yaml",
    },
}
SEPARATION = {
    "rule_kind":
        "canonical-field-comparison-over-exact-stored-records",
    "bucket_role": "lookup-accelerator-only-from-linked-hash_rule",
    "digest_equality": "never-decides-record-equality",
    "digest_inequality": "never-decides-record-inequality",
    "trust_boundary":
        "equal-canonical-identities-must-yield-same-"
        "valid-format-bucket-key",
    "enforcement":
        "independent-canonical-identity-index-plus-insert-"
        "consistency-validation",
    "oracle_output":
        "valid-format-bucket-key-from-linked-digest-contract-"
        "format",
    "links": {
        "variant": "data/contracts/variant.yaml",
        "position_digest": "data/contracts/position_digest.yaml",
    },
}
GUARANTEES = {
    "no_merge": "collision-never-merges-distinct-records",
    "no_fork": "collision-never-splits-one-record",
    "count_exact":
        "n-distinct-identities-in-one-bucket-yield-n-records",
    "order_insensitivity":
        "canonical-identity-order-never-arrival-order",
    "merge_algebra_preserved":
        "idempotent-commutative-associative-under-collision",
    "trust_boundary":
        "equal-identities-never-fork-misbucketed-or-stateful-oracle",
}
FAILURE_CLASSES = ["malformed_collision_record",
                   "accelerator_as_identity",
                   "accelerator_inconsistent"]
FAILURE_TRIGGERS = {
    "malformed_collision_record":
        "record-fails-linked-table-shape-or-receiver-oracle-"
        "revalidation",
    "accelerator_as_identity":
        "bucket-key-presented-as-record-identity",
    "accelerator_inconsistent":
        "oracle-raised-or-invalid-format-key-or-equal-identity-"
        "divergent-key",
}
FAILURE_MAPPING = {
    "malformed_collision_record": "malformed_request",
    "accelerator_as_identity": "accelerator_as_identity",
    "accelerator_inconsistent": "accelerator_inconsistent",
}
ERROR_ENUM = ["malformed_request", "accelerator_as_identity",
              "accelerator_inconsistent", "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        },
        "additional_fields": "forbidden",
    },
    "retryable_true_only_for": ["internal"],
}
PROPERTIES = {
    "separation_completeness":
        "every-bucket-decision-by-field-comparison",
    "collision_transparency":
        "observable-behavior-identical-under-collision",
    "no_merge": "distinct-identities-never-collapse",
    "no_fork": "one-identity-never-duplicates",
    "order_insensitivity": "permutation-invariant-table",
    "oracle_boundary":
        "single-boundary-helper-for-insert-and-merge-validation",
    "rollback": "rejected-insert-leaves-table-bit-identical",
    "trust_boundary":
        "oracle-divergence-fails-closed-no-fork-persists",
}
VERSIONING = {"base_path": "/graph/collision/v1"}
PROSE_KEYS = {"rule"}


def _load(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"{path.name}: unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContractError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"{path.name}: top level must be a mapping")
    return data


def _check_exact(section: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise ContractError(
            f"{section}: content drift\n  expected: {expected!r}\n"
            f"  actual:   {actual!r}")


def _check_closure(section: str, mapping: dict, allowed: set) -> None:
    extra = sorted(set(mapping) - allowed)
    if extra:
        raise ContractError(f"{section}: undeclared keys {extra}")


def _strip_prose(section: dict) -> dict:
    return {k: v for k, v in section.items() if k not in PROSE_KEYS}


def lint(path: Path = CONTRACT, *, variant_path: Path = VARIANT,
         digest_path: Path = DIGEST,
         node_path: Path = NODE) -> None:
    data = _load(path)
    if data.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    _check_closure("top level", data, {"schema_version", "contract"})
    contract = data["contract"]
    if not isinstance(contract, dict):
        raise ContractError("contract must be a mapping")
    if contract.get("id") != "graph-collision":
        raise ContractError(
            f"contract.id drift: {contract.get('id')!r}")
    _check_closure(
        "contract", contract,
        {"id", "role", "definition", "separation", "guarantees",
         "failures", "errors", "properties", "links", "versioning"})

    for section in ("role", "definition", "separation", "guarantees",
                    "failures", "errors", "properties", "links",
                    "versioning"):
        if section not in contract:
            raise ContractError(f"contract.{section}: missing section")

    role = contract["role"]
    _check_closure("role", role, set(ROLE) | {"rule"})
    _check_exact("role", _strip_prose(role), ROLE)

    definition = contract["definition"]
    _check_closure("definition", definition,
                   set(DEFINITION) | {"rule"})
    _check_exact("definition", _strip_prose(definition), DEFINITION)

    separation = contract["separation"]
    _check_closure("separation", separation,
                   set(SEPARATION) | {"rule"})
    _check_exact("separation", _strip_prose(separation), SEPARATION)

    guarantees = contract["guarantees"]
    _check_closure("guarantees", guarantees,
                   set(GUARANTEES) | {"rule"})
    _check_exact("guarantees", _strip_prose(guarantees),
                 GUARANTEES)

    failures = contract["failures"]
    _check_closure("failures", failures,
                   {"classes", "triggers", "mapping", "closed",
                    "rule"})
    _check_exact("failures.classes", failures.get("classes"),
                 FAILURE_CLASSES)
    _check_exact("failures.triggers", failures.get("triggers"),
                 FAILURE_TRIGGERS)
    if set(failures["triggers"]) != set(failures["classes"]):
        raise ContractError(
            "failures: triggers keys must equal declared classes")
    _check_exact("failures.mapping", failures.get("mapping"),
                 FAILURE_MAPPING)
    if failures.get("closed") is not True:
        raise ContractError("failures.closed must be true")
    if set(failures["mapping"]) != set(failures["classes"]):
        raise ContractError(
            "failures: mapping keys must equal declared classes")
    undeclared = set(failures["mapping"].values()) - set(ERROR_ENUM)
    if undeclared:
        raise ContractError(
            f"failures: undeclared error codes {sorted(undeclared)}")

    errors = contract["errors"]
    _check_closure("errors", errors, {"closed_enum", "shape", "rule"})
    _check_exact("errors.closed_enum", errors.get("closed_enum"),
                 ERROR_ENUM)
    _check_exact("errors.shape", errors.get("shape"), ERROR_SHAPE)

    properties = contract["properties"]
    _check_closure("properties", properties, set(PROPERTIES) | {"rule"})
    _check_exact("properties", _strip_prose(properties), PROPERTIES)

    links = contract["links"]
    _check_closure("links", links, set(LINKS) | {"rule"})
    _check_exact("links", _strip_prose(links), LINKS)

    versioning = contract["versioning"]
    _check_closure("versioning", versioning,
                   set(VERSIONING) | {"rule"})
    _check_exact("versioning", _strip_prose(versioning), VERSIONING)

    # Linkage: verify against the ACTUAL sibling artifacts.
    variant = _load(variant_path)["contract"]
    identity = variant.get("identity", {})
    hash_rule = identity.get("hash_rule", "")
    if not isinstance(hash_rule, str) or \
            "accelerator only" not in hash_rule or \
            "collisions MUST fall back to full canonical" \
            not in hash_rule:
        raise ContractError(
            "linkage: variant identity.hash_rule drift - the "
            "collision contract's separation rule inherits the "
            "accelerator-only role with mandatory field-comparison "
            "fallback")
    canonical = identity.get("canonical_fields")
    if not isinstance(canonical, list) or not canonical:
        raise ContractError(
            "linkage: variant canonical_fields missing - the "
            "collision contract's identity source is gone")

    digest = _load(digest_path)["contract"]
    if digest.get("role", {}).get("kind") != "lookup-accelerator-only":
        raise ContractError(
            "linkage: digest contract role kind drift - the "
            "collision contract inherits the accelerator-only role")
    _check_exact("linkage digest format",
                 digest.get("digest", {}).get("format"), {
                     "prefix": "pdv1:",
                     "total_length": 69,
                     "regex": "^pdv1:[0-9a-f]{64}$",
                 })

    node = _load(node_path)["contract"]
    node_identity = node.get("identity", {})
    if node_identity.get("equality") != "canonical-field-comparison-only":
        raise ContractError(
            "linkage: node identity equality drift - the reference "
            "table's separation rule is canonical field comparison")
    if node_identity.get("accelerator") != \
            "from-linked-position-digest-contract-digest":
        raise ContractError(
            "linkage: node identity accelerator drift - the "
            "reference table's bucket key source changed")
    if node_identity.get("excluded") != [
            "halfmove_clock", "fullmove_number", "move_order_path",
            "repertoire_context"]:
        raise ContractError(
            "linkage: node identity exclusions drift - collision "
            "separation must exclude exactly the same fields")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        lint(path)
    except ContractError as exc:
        print(f"collision contract lint FAILED: {exc}")
        return 1
    print(f"collision contract lint ok: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
