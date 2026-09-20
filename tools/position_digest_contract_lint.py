"""T0113: chess position-digest contract lint.

Enforces the normative CONTENT of data/contracts/position_digest.yaml
by EXACT STRUCTURED COMPARISON, never prose-marker presence: the
accelerator-only role with its linked equality source, the identity
tuple sourced from (never restating) the variant contract's
canonical_fields with the exact exclusion list, the canonical byte
encoding (field order sentinel, single-space separator, per-field
serialization forms, ASCII alphabet, no trailing bytes), the digest
definition (SHA-256, untruncated 32 bytes, 64 lowercase hex, the
exact pdv1: format with self-consistent length and regex, prefix-bump
version policy), the exact parse rejection rules, the three-class
failure model with its exact mapping (no orphan classes, no
undeclared codes), the behavioral properties, and linkage VERIFIED
AGAINST THE ACTUAL sibling artifacts (variant canonical_fields and
registry, en-passant storage-vs-identity value, FEN placement /
castling / active-color grammars). Anything less passes silently and
every dedup, memory and scoring join rots.

    python tools/position_digest_contract_lint.py [path]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "position_digest.yaml"
VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
EN_PASSANT = ROOT / "data" / "contracts" / "en_passant.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"

ROLE = {
    "kind": "lookup-accelerator-only",
    "equality_source": "from-linked-variant-contract-identity",
    "collision_rule": "from-linked-variant-contract-identity-hash_rule",
    "link": "data/contracts/variant.yaml",
}
IDENTITY = {
    "source": "from-linked-variant-contract-identity-canonical_fields",
    "en_passant_value": "from-linked-en-passant-contract-identity_value",
    "board_value": "from-linked-fen-contract-placement",
    "castling_value": "from-linked-fen-contract-castling",
    "side_value": "from-linked-fen-contract-active_color",
    "excluded": ["halfmove_clock", "fullmove_number",
                 "move_order_path", "repertoire_context"],
}
ENCODING = {
    "field_order": "from-linked-variant-contract-identity-canonical_fields",
    "separator": "single-ascii-space",
    "variant_form": "registry-id-verbatim",
    "board_form": "canonical-fen-placement-serialization",
    "side_to_move_form": {"white": "w", "black": "b"},
    "castling_form": "canonical-fen-castling-serialization",
    "en_passant_form": "identity-square-or-none-sentinel",
    "alphabet": "ascii-only",
    "trailing_bytes": "forbidden",
}
DIGEST = {
    "algorithm": "sha-256",
    "input": "utf-8-bytes-of-canonical-encoding",
    "output_bytes": 32,
    "truncated": "forbidden",
    "hex_case": "lowercase",
    "hex_length": 64,
    "version_policy": "prefix-bump-on-any-change",
}
DIGEST_FORMAT = {
    "prefix": "pdv1:",
    "total_length": 69,
    "regex": "^pdv1:[0-9a-f]{64}$",
}
PARSE = {
    "unknown_prefix": "malformed_digest",
    "wrong_length": "malformed_digest",
    "uppercase_hex": "malformed_digest",
    "non_hex": "malformed_digest",
    "empty_input": "malformed_digest",
    "surrounding_whitespace": "malformed_digest",
}
FAILURE_CLASSES = ["malformed_digest", "malformed_position",
                   "unknown_variant"]
FAILURE_MAPPING = {
    "malformed_digest": "malformed_request",
    "malformed_position": "malformed_request",
    "unknown_variant": "malformed_request",
}
PROPERTIES = {
    "determinism": "same-identity-tuple-same-digest-always",
    "clock_invariance": "clocks-never-change-the-digest",
    "phantom_ep_invariance":
        "uncapturable-target-never-changes-the-digest",
    "legal_ep_sensitivity":
        "capturable-target-always-changes-the-digest",
    "field_sensitivity":
        "every-identity-field-change-changes-the-digest",
    "statelessness": "pure-function-no-state-no-rollback-surface",
}
PROSE_KEYS = {"rule", "links"}


def _load(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
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


def _check_closure(section: str, mapping: dict, allowed: set[str]) -> None:
    extra = sorted(set(mapping) - allowed)
    if extra:
        raise ContractError(f"{section}: undeclared keys {extra}")


def _strip_prose(section: dict) -> dict:
    return {k: v for k, v in section.items() if k not in PROSE_KEYS}


def lint(path: Path = CONTRACT, *, variant_path: Path = VARIANT,
         en_passant_path: Path = EN_PASSANT,
         fen_path: Path = FEN) -> None:
    data = _load(path)
    if data.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    _check_closure("top level", data, {"schema_version", "contract"})
    contract = data["contract"]
    if not isinstance(contract, dict):
        raise ContractError("contract must be a mapping")
    if contract.get("id") != "chess-position-digest":
        raise ContractError(
            f"contract.id drift: {contract.get('id')!r}")
    _check_closure(
        "contract", contract,
        {"id", "role", "identity", "encoding", "digest", "parse",
         "failures", "properties"})

    role = contract["role"]
    _check_closure("role", role, set(ROLE) | {"rule"})
    _check_exact("role", _strip_prose(role), ROLE)

    identity = contract["identity"]
    _check_closure("identity", identity, set(IDENTITY) | {"rule", "links"})
    _check_exact("identity", _strip_prose(identity), IDENTITY)
    links = identity.get("links")
    _check_exact("identity.links", links, {
        "variant": "data/contracts/variant.yaml",
        "en_passant": "data/contracts/en_passant.yaml",
        "fen": "data/contracts/fen.yaml",
    })

    encoding = contract["encoding"]
    _check_closure("encoding", encoding, set(ENCODING) | {"rule"})
    _check_exact("encoding", _strip_prose(encoding), ENCODING)

    digest = contract["digest"]
    _check_closure("digest", digest, set(DIGEST) | {"format", "rule"})
    _check_exact("digest", {k: v for k, v in digest.items()
                            if k not in PROSE_KEYS | {"format"}}, DIGEST)
    _check_exact("digest.format", digest.get("format"), DIGEST_FORMAT)
    fmt = digest["format"]
    if fmt["total_length"] != len(fmt["prefix"]) + DIGEST["hex_length"]:
        raise ContractError(
            "digest.format.total_length inconsistent with prefix + "
            "hex_length")
    if re.fullmatch(fmt["regex"], fmt["prefix"]
                    + "0" * DIGEST["hex_length"]) is None:
        raise ContractError(
            "digest.format.regex rejects the canonical zero digest")
    if re.fullmatch(fmt["regex"], fmt["prefix"]
                    + "A" * DIGEST["hex_length"]) is not None:
        raise ContractError("digest.format.regex accepts uppercase hex")

    parse = contract["parse"]
    _check_closure("parse", parse, set(PARSE) | {"rule"})
    _check_exact("parse", _strip_prose(parse), PARSE)

    failures = contract["failures"]
    _check_closure("failures", failures,
                   {"classes", "mapping", "closed", "rule"})
    _check_exact("failures.classes", failures.get("classes"),
                 FAILURE_CLASSES)
    _check_exact("failures.mapping", failures.get("mapping"),
                 FAILURE_MAPPING)
    if failures.get("closed") is not True:
        raise ContractError("failures.closed must be true")
    if set(failures["mapping"]) != set(failures["classes"]):
        raise ContractError(
            "failures: mapping keys must equal declared classes")
    undeclared = set(failures["mapping"].values()) - {
        "malformed_request", "unknown_command", "illegal_state",
        "internal"}
    if undeclared:
        raise ContractError(
            f"failures: undeclared error codes {sorted(undeclared)}")

    properties = contract["properties"]
    _check_closure("properties", properties, set(PROPERTIES) | {"rule"})
    _check_exact("properties", _strip_prose(properties), PROPERTIES)

    # Linkage: verify against the ACTUAL sibling artifacts.
    variant = _load(variant_path)["contract"]
    fields = variant.get("identity", {}).get("canonical_fields")
    if not isinstance(fields, list) or len(fields) != 5:
        raise ContractError(
            "linkage: variant contract identity.canonical_fields "
            "missing or not five fields")
    if fields != ["variant", "board", "side_to_move", "castling_rights",
                  "en_passant"]:
        raise ContractError(
            f"linkage: variant canonical_fields drift: {fields!r}")
    registry = variant.get("variants", {}).get("entries")
    if not isinstance(registry, list) or not registry:
        raise ContractError("linkage: variant registry missing or empty")
    for entry in registry:
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ContractError(
                "linkage: variant registry entry without id")
        if " " in entry["id"]:
            raise ContractError(
                f"linkage: variant id {entry['id']!r} contains the "
                "encoding separator")

    ep = _load(en_passant_path)["contract"]
    svi = ep.get("target", {}).get("storage_vs_identity", {})
    _check_exact("linkage en_passant identity_value",
                 svi.get("identity_value"),
                 "target-when-legal-capture-else-none")
    if ep.get("target", {}).get("grammar", {}).get("none_sentinel") != "-":
        raise ContractError(
            "linkage: en-passant none sentinel drift")

    fen = _load(fen_path)["contract"]
    placement = fen.get("placement", {})
    if placement.get("rank_count") != 8 or placement.get("rank_sum") != 8:
        raise ContractError("linkage: FEN placement geometry drift")
    if fen.get("active_color", {}).get("values") != ["w", "b"]:
        raise ContractError("linkage: FEN active_color values drift")
    castling = fen.get("castling", {})
    if castling.get("order") != "KQkq" or \
            castling.get("none_sentinel") != "-":
        raise ContractError("linkage: FEN castling serialization drift")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        lint(path)
    except ContractError as exc:
        print(f"position-digest contract lint FAILED: {exc}")
        return 1
    print(f"position-digest contract lint ok: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
