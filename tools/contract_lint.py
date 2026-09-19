"""T0473: replaceable control-plane contract lint, schema v2.

Enforces the NORMATIVE content of data/contracts/control-plane.yaml, not
just its shape:
- structured request/response/error field schemas (types, required,
  examples that type-match - examples drive generated conformance);
- a coherent auth contract: bearer tokens issued by named public
  operations; every other operation requires auth; op-level auth flags
  must agree with transport.auth.public_operations;
- method/mutating consistency (GET is never mutating) and idempotency
  invariants (every mutating op lists idempotency_conflict);
- privacy as exact structured values (chess_content=forbidden,
  key_material=write-only, logs=no-secrets) - a weakened privacy text
  fails;
- BYOK neutrality: no vendor-specific field names anywhere, and no
  response field may carry key material;
- versioning semantics: the rule must actually state MAJOR/MINOR/
  additive/rollback behavior, not merely contain the word "Rollback".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "control-plane.yaml"

REQUIRED_AREAS = {"identity", "entitlements", "quota", "billing",
                  "provider_routing"}
METHODS = {"GET", "POST", "DELETE"}
FIELD_TYPES = {"string", "integer", "number", "boolean", "object",
               "array"}
SCALAR_TYPES = {"string", "integer", "number", "boolean"}
FIELD_SPEC_KEYS = {"type", "required", "example", "fields", "items",
                   "write_only"}
VENDOR_DENYLIST = re.compile(
    r"openai|anthropic|gemini|azure|api_key|secret_key|bearer_token",
    re.IGNORECASE)
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ContractError(Exception):
    pass


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(problem)


def _mapping(node: object, where: str) -> dict:
    _need(type(node) is dict, f"{where}: expected mapping, got "
                             f"{type(node).__name__}")
    return node  # type: ignore[return-value]


def _text(node: object, where: str) -> str:
    _need(type(node) is str and bool(node.strip()),
          f"{where}: nonempty string required")
    return node  # type: ignore[return-value]


def _type_matches(value: object, ftype: str) -> bool:
    if ftype == "string":
        return type(value) is str
    if ftype == "integer":
        return type(value) is int
    if ftype == "number":
        return type(value) in (int, float)
    if ftype == "boolean":
        return type(value) is bool
    if ftype == "object":
        return type(value) is dict
    if ftype == "array":
        return type(value) is list
    return False


def _lint_fields(fields: object, where: str, *, is_response: bool,
                 problems: list[str]) -> None:
    fields = _mapping(fields, where)
    for name, spec in sorted(fields.items()):
        fw = f"{where}.{name}"
        _need(type(name) is str and bool(FIELD_NAME.match(name)),
              f"{fw}: field names are snake_case")
        _need(not VENDOR_DENYLIST.search(name),
              f"{fw}: vendor-specific field name (BYOK violation)")
        spec = _mapping(spec, fw)
        _need(set(spec.keys()) <= FIELD_SPEC_KEYS,
              f"{fw}: unknown spec keys {sorted(set(spec) - FIELD_SPEC_KEYS)}")
        ftype = spec.get("type")
        _need(ftype in FIELD_TYPES, f"{fw}.type: one of {sorted(FIELD_TYPES)}")
        required = spec.get("required")
        _need(type(required) is bool, f"{fw}.required: boolean required")
        write_only = spec.get("write_only", False)
        _need(type(write_only) is bool, f"{fw}.write_only: boolean")
        if is_response:
            _need(not write_only and name != "key_material",
                  f"{fw}: key material may never appear in a response")
        if required and ftype != "object":
            _need("example" in spec,
                  f"{fw}.example: required leaf fields carry an example "
                  "(drives generated conformance; objects exemplify via "
                  "nested fields)")
        if "example" in spec:
            _need(_type_matches(spec["example"], ftype),
                  f"{fw}.example: does not match type {ftype}")
        if ftype == "object":
            _lint_fields(spec.get("fields"), fw + ".fields",
                         is_response=is_response, problems=problems)
        if ftype == "array":
            _need(spec.get("items") in SCALAR_TYPES,
                  f"{fw}.items: scalar item type required")


def lint(doc: object) -> None:
    top = _mapping(doc, "contract document")
    _need(type(top.get("schema_version")) is int
          and top["schema_version"] >= 2,
          "schema_version: int >= 2 required")
    contract = _mapping(top.get("contract"), "contract")
    _text(contract.get("name"), "contract.name")

    versioning = _mapping(contract.get("versioning"), "contract.versioning")
    _need(versioning.get("scheme") == "semver",
          "versioning.scheme: must be semver")
    rule = _text(versioning.get("rule"), "versioning.rule")
    for marker in ("MAJOR", "MINOR", "additive", "Rollback"):
        _need(marker in rule,
              f"versioning.rule: must state {marker} behavior")
    _need(len(rule) >= 200,
          "versioning.rule: too thin - rollback semantics required")
    base_path = _text(versioning.get("base_path"),
                      "versioning.base_path")
    _need(base_path.startswith("/") and "/v" in base_path,
          "versioning.base_path: versioned path like /cp/v1")

    transport = _mapping(contract.get("transport"), "contract.transport")
    auth = _mapping(transport.get("auth"), "transport.auth")
    _need(auth.get("scheme") == "bearer",
          "transport.auth.scheme: bearer required")
    _need(auth.get("header") == "Authorization",
          "transport.auth.header: Authorization required")
    _need(auth.get("format") == "Authorization: Bearer <opaque-token>",
          "transport.auth.format: exact bearer format required")
    auth_rule = _text(auth.get("rule"), "transport.auth.rule")
    for marker in ("REQUIRED", "401", "auth_invalid", "auth_expired"):
        _need(marker in auth_rule,
              f"transport.auth.rule: must state {marker}")
    _need(len(auth_rule) >= 120,
          "transport.auth.rule: too thin to be a rule")
    public_ops = auth.get("public_operations")
    issued_by = auth.get("issued_by")
    _need(type(public_ops) is list and public_ops,
          "transport.auth.public_operations: nonempty list")
    _need(type(issued_by) is list and issued_by,
          "transport.auth.issued_by: nonempty list")
    for label, entries in (("public_operations", public_ops),
                           ("issued_by", issued_by)):
        _need(len(set(entries)) == len(entries),
              f"transport.auth.{label}: duplicate entries rejected, "
              "not normalized")

    idem = _text(transport.get("idempotency"), "transport.idempotency")
    _need("Idempotency-Key" in idem and "idempotency_conflict" in idem,
          "transport.idempotency: must define the key header and the "
          "different-body conflict outcome")

    self_destructive = transport.get("self_destructive_operations")
    _need(type(self_destructive) is list and self_destructive,
          "transport.self_destructive_operations: nonempty list")
    _need(len(set(self_destructive)) == len(self_destructive),
          "transport.self_destructive_operations: duplicate entries "
          "rejected, not normalized")
    sd_rule = _text(transport.get("self_destructive_rule"),
                    "transport.self_destructive_rule")
    for marker in ("BEFORE", "replay", "idempotency_conflict"):
        _need(marker in sd_rule,
              f"transport.self_destructive_rule: must state {marker}")
    _need(len(sd_rule) >= 200,
          "transport.self_destructive_rule: too thin to be a rule")

    errors = _mapping(transport.get("errors"), "transport.errors")
    enum = errors.get("closed_enum")
    _need(type(enum) is list and enum, "errors.closed_enum: nonempty list")
    for i, code in enumerate(enum):
        _need(type(code) is str and code.strip() and " " not in code,
              f"errors.closed_enum[{i}]: unique nonempty code required")
    _need(len(set(enum)) == len(enum),
          "errors.closed_enum: duplicate codes")
    enum_set = set(enum)
    _need("idempotency_conflict" in enum_set,
          "errors.closed_enum: idempotency_conflict required")
    shape_fields = _mapping(_mapping(_mapping(errors.get("shape"),
        "errors.shape").get("error"), "errors.shape.error").get("fields"),
        "errors.shape.error.fields")
    _lint_fields(shape_fields, "errors.shape.error.fields",
                 is_response=True, problems=[])

    privacy = _mapping(contract.get("privacy"), "contract.privacy")
    _need(privacy.get("chess_content") == "forbidden",
          "privacy.chess_content: must be 'forbidden'")
    _need(privacy.get("key_material") == "write-only",
          "privacy.key_material: must be 'write-only'")
    _need(privacy.get("logs") == "no-secrets",
          "privacy.logs: must be 'no-secrets'")
    cost = _mapping(contract.get("cost"), "contract.cost")
    _need(cost.get("reservation_required") is True,
          "cost.reservation_required: must be exactly true")
    _need(cost.get("caps_enforced") == "server-side",
          "cost.caps_enforced: must be server-side")
    recovery = _mapping(contract.get("recovery"), "contract.recovery")
    _need(recovery.get("entitlements_cache_ttl_field")
          == "cache_ttl_seconds",
          "recovery.entitlements_cache_ttl_field: must name "
          "cache_ttl_seconds")
    _need(recovery.get("reservation_expiry_field") == "expires_at",
          "recovery.reservation_expiry_field: must name expires_at")

    areas = _mapping(top.get("areas"), "areas")
    got = set(areas.keys())
    _need(got == REQUIRED_AREAS,
          f"areas: must be exactly {sorted(REQUIRED_AREAS)}, "
          f"got {sorted(got)}")

    op_index: dict[str, dict] = {}
    flagged_public: set[str] = set()
    seen_routes: dict[tuple[str, str], str] = {}
    for area_name, area in sorted(areas.items()):
        ops = _mapping(_mapping(area, f"areas.{area_name}").get("ops"),
                       f"areas.{area_name}.ops")
        _need(bool(ops), f"areas.{area_name}.ops: at least one operation")
        for op_name, op in sorted(ops.items()):
            where = f"areas.{area_name}.ops.{op_name}"
            op = _mapping(op, where)
            op_index[f"{area_name}.{op_name}"] = op
            _need(op.get("method") in METHODS,
                  f"{where}.method: one of {sorted(METHODS)}")
            path = _text(op.get("path"), f"{where}.path")
            _need(path.startswith("/"), f"{where}.path: absolute path")
            route = (op["method"], path)
            _need(route not in seen_routes,
                  f"{where}: duplicate route {route} already used by "
                  f"{seen_routes.get(route)}")
            seen_routes[route] = where
            mutating = op.get("mutating")
            _need(type(mutating) is bool,
                  f"{where}.mutating: boolean required")
            _need(not (op["method"] == "GET" and mutating),
                  f"{where}: GET may never be mutating")
            auth_flag = op.get("auth")
            _need(auth_flag in ("required", "public"),
                  f"{where}.auth: 'required' or 'public'")
            if auth_flag == "public":
                flagged_public.add(f"{area_name}.{op_name}")
            op_errors = op.get("errors")
            _need(type(op_errors) is list and op_errors,
                  f"{where}.errors: nonempty list")
            for code in op_errors:
                _need(code in enum_set,
                      f"{where}.errors: {code!r} not in closed enum")
            _need("internal" in op_errors,
                  f"{where}.errors: must include internal fallback")
            if mutating:
                _need("idempotency_conflict" in op_errors,
                      f"{where}: mutating op must list "
                      "idempotency_conflict")
            _lint_fields(_mapping(op.get("request"),
                                  f"{where}.request").get("fields"),
                         f"{where}.request.fields", is_response=False,
                         problems=[])
            _lint_fields(_mapping(op.get("response"),
                                  f"{where}.response").get("fields"),
                         f"{where}.response.fields", is_response=True,
                         problems=[])

    read_only = transport.get("read_only_operations")
    _need(type(read_only) is list, "transport.read_only_operations: "
                                  "list required")
    _need(len(set(read_only)) == len(read_only),
          "transport.read_only_operations: duplicate entries rejected")
    read_only_rule = _text(transport.get("read_only_rule"),
                           "transport.read_only_rule")
    _need("mutating: false" in read_only_rule
          and "idempotency" in read_only_rule,
          "transport.read_only_rule: must state the mutating:false "
          "allowlist and the idempotency-bypass reason")
    read_only_set = set()
    for ref in read_only:
        ref = _text(ref, "transport.read_only_operations[]")
        _need(ref in op_index, f"transport.read_only_operations: "
                               f"{ref!r} is not a declared operation")
        read_only_set.add(ref)
    flagged_read_only = {name for name, op in op_index.items()
                         if op["mutating"] is False}
    _need(read_only_set == flagged_read_only,
          f"read-only incoherent: mutating:false ops "
          f"{sorted(flagged_read_only)} != read_only_operations "
          f"{sorted(read_only_set)} - flipping a state-changing op to "
          "mutating:false bypasses idempotency")

    public_set = set()
    for ref in public_ops:
        ref = _text(ref, "transport.auth.public_operations[]")
        _need(ref in op_index, f"transport.auth.public_operations: "
                               f"{ref!r} is not a declared operation")
        public_set.add(ref)
    _need(public_set == flagged_public,
          f"auth incoherent: ops flagged public {sorted(flagged_public)} "
          f"!= transport.auth.public_operations {sorted(public_set)}")
    for ref in issued_by:
        ref = _text(ref, "transport.auth.issued_by[]")
        _need(ref in op_index,
              f"transport.auth.issued_by: {ref!r} not a declared op")
        _need(ref in public_set,
              f"transport.auth.issued_by: {ref!r} must be public - a "
              "token cannot be minted behind auth")
    for ref in self_destructive:
        ref = _text(ref, "transport.self_destructive_operations[]")
        _need(ref in op_index,
              f"transport.self_destructive_operations: {ref!r} not a "
              "declared op")
        target = op_index[ref]
        _need(target.get("mutating") is True,
              f"transport.self_destructive_operations: {ref!r} must be "
              "mutating")
        _need(target.get("auth") == "required",
              f"transport.self_destructive_operations: {ref!r} must "
              "require auth")


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
    print("OK contract lint: replaceable control-plane contract v4 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
