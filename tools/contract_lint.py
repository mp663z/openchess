"""T0473: replaceable control-plane contract lint.

Structural + behavior rules for data/contracts/control-plane.yaml:
- happy: a well-formed contract passes clean;
- boundary: the area set must be EXACTLY the five private-side areas
  (identity, entitlements, quota, billing, provider_routing) - an extra or
  missing area means control-plane scope drifted;
- malformed: wrong shapes/types are rejected with named problems, never
  silently coerced (exact builtin type checks throughout);
- rollback: the versioning policy must pin semver with an explicit
  rollback rule and a versioned base path - replaceability without a
  downgrade story is not replaceability.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "control-plane.yaml"

REQUIRED_AREAS = {"identity", "entitlements", "quota", "billing",
                  "provider_routing"}
METHODS = {"GET", "POST", "DELETE"}


class ContractError(Exception):
    pass


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(problem)


def _mapping(node: object, where: str) -> dict:
    _need(type(node) is dict, f"{where}: expected mapping, got "
                             f"{type(node).__name__}")
    return node  # type: ignore[return-value]


def _text(node: object, where: str, *, nonempty: bool = True) -> str:
    _need(type(node) is str, f"{where}: expected string, got "
                             f"{type(node).__name__}")
    if nonempty:
        _need(bool(node.strip()), f"{where}: empty string")
    return node  # type: ignore[return-value]


def lint(doc: object) -> None:
    top = _mapping(doc, "contract document")
    _need(type(top.get("schema_version")) is int
          and top["schema_version"] >= 1,
          "schema_version: positive int required")
    contract = _mapping(top.get("contract"), "contract")
    _text(contract.get("name"), "contract.name")

    versioning = _mapping(contract.get("versioning"), "contract.versioning")
    _need(versioning.get("scheme") == "semver",
          "versioning.scheme: must be semver")
    rule = _text(versioning.get("rule"), "versioning.rule")
    _need("Rollback" in rule or "rollback" in rule,
          "versioning.rule: rollback behavior must be defined")
    base_path = _text(versioning.get("base_path"),
                      "versioning.base_path")
    _need(base_path.startswith("/") and "/v" in base_path,
          "versioning.base_path: must be a versioned path like /cp/v1")

    transport = _mapping(contract.get("transport"), "contract.transport")
    errors = _mapping(transport.get("errors"), "transport.errors")
    enum = errors.get("closed_enum")
    _need(type(enum) is list and enum, "errors.closed_enum: nonempty list")
    for i, code in enumerate(enum):
        _need(type(code) is str and code.strip()
              and " " not in code,
              f"errors.closed_enum[{i}]: unique nonempty code required")
    _need(len(set(enum)) == len(enum),
          "errors.closed_enum: duplicate codes")
    enum_set = set(enum)
    _text(errors.get("shape"), "errors.shape")
    idem = _text(transport.get("idempotency"), "transport.idempotency")
    _need("Idempotency-Key" in idem,
          "transport.idempotency: must name the Idempotency-Key header")
    for section in ("privacy", "cost", "recovery"):
        value = contract.get(section)
        _need(type(value) is list and value,
              f"contract.{section}: nonempty policy list required")

    areas = _mapping(top.get("areas"), "areas")
    got = set(areas.keys())
    _need(got == REQUIRED_AREAS,
          f"areas: must be exactly {sorted(REQUIRED_AREAS)}, "
          f"got {sorted(got)}")
    for area_name, area in sorted(areas.items()):
        ops = _mapping(_mapping(area, f"areas.{area_name}").get("ops"),
                       f"areas.{area_name}.ops")
        _need(bool(ops), f"areas.{area_name}.ops: at least one operation")
        for op_name, op in sorted(ops.items()):
            where = f"areas.{area_name}.ops.{op_name}"
            op = _mapping(op, where)
            _need(op.get("method") in METHODS,
                  f"{where}.method: one of {sorted(METHODS)}")
            path = _text(op.get("path"), f"{where}.path")
            _need(path.startswith("/"), f"{where}.path: absolute path")
            mutating = op.get("mutating")
            _need(type(mutating) is bool,
                  f"{where}.mutating: boolean required")
            _text(op.get("request"), f"{where}.request")
            _text(op.get("response"), f"{where}.response")
            op_errors = op.get("errors")
            _need(type(op_errors) is list and op_errors,
                  f"{where}.errors: nonempty list")
            for code in op_errors:
                _need(code in enum_set,
                      f"{where}.errors: {code!r} not in closed enum")
            _need("internal" in op_errors,
                  f"{where}.errors: must include internal fallback")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        print(f"FAIL contract lint: malformed YAML: {exc}")
        return 1
    try:
        lint(doc)
    except ContractError as exc:
        print(f"FAIL contract lint: {exc}")
        return 1
    print("OK contract lint: replaceable control-plane contract clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
