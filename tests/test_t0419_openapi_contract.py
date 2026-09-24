"""T0419: control-plane OpenAPI contract - the contract document
(data/contracts/openapi.yaml) is normative; this battery holds the
contract-derived REFERENCE derive() and proves happy, boundary, malformed
and hostile-type behavior against it.

derive(source) maps one control-plane contract document (schema_version
2, data/contracts/control-plane.yaml) to exactly one OpenAPI 3.1.0
document: one path per op in source order, bearer security except on the
public operations, an Idempotency-Key header on every mutating op and on
no read-only op, write_only fields as writeOnly (request side only), the
error shape with code closed to the control-plane enum, and one error
response per status reading. canonical_bytes pins the byte output
(insertion key order, compact separators, UTF-8).

Grounded readings (cited in the contract's rule text):
- additionalProperties true on every object: control-plane.yaml says
  servers MUST ignore unknown request fields and payloads MAY carry
  extra fields;
- 401 and 409 statuses come from the contract; every other status is the
  merged mock's executable reading (path + sha256 pinned);
- entitlement_missing, provider_unavailable, cost_cap_exceeded,
  rate_limited and internal have no reading: they stay unmapped
  (x-unmapped-errors / x-unmapped-error-codes), never an invented status.
Fail-closed readings (the spec is silent):
- any malformed or hostile-typed source is malformed_request with no
  partial document and the source untouched;
- an integer or number example lies within 2**53 - 1 in magnitude and a
  float example is finite, so the bytes stay interoperable JSON.

DESIGN CAUTION: the reference is derived from the same contract
document, so this battery proves contract CONSISTENCY, not production
behavior; a later implement task must run the same cases against a
separately built generator."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.openapi_contract_lint import (  # noqa: E402
    CHESS_TOKENS,
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    ContractError,
    lint,
)

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_SRC = _CC["source"]
_GR = _SRC["grammars"]
_STAT = _CC["error_responses"]["status"]
CONTROL_PLANE = ROOT / _SRC["contract"]
BASE_RE = re.compile(_GR["base_path"], re.ASCII)
AREA_RE = re.compile(_GR["area"], re.ASCII)
OP_RE = re.compile(_GR["op"], re.ASCII)
PATH_RE = re.compile(_GR["path"], re.ASCII)
FIELD_RE = re.compile(_GR["field"], re.ASCII)
OP_KEYS = tuple(_SRC["op_keys"])
METHODS = frozenset(_SRC["methods"])
AUTH_VALUES = frozenset(_SRC["auth_values"])
FIELD_KEYS = frozenset(_SRC["field_keys"])
FIELD_TYPES = frozenset(_SRC["field_types"])
ITEM_TYPES = frozenset(_SRC["item_types"])
STATUS = {**_STAT["contract_fixed"], **_STAT["mock_reading"]}
UNMAPPED = frozenset(_STAT["unmapped"])

# -- reference begin: everything down to the reference end marker is rebuilt
# from source for each reference mutant (constants, error class, checks).

MAX_DEPTH = 8
MAX_AREAS = 64
MAX_OPS = 64
MAX_FIELDS = 256
MAX_EXAMPLE_INT = 2**53 - 1


class OpenApiError(Exception):
    """Typed rejection: failure_class, code, retryable."""

    def __init__(self, failure_class, code, message):
        Exception.__init__(self, message)
        self.failure_class = failure_class
        self.code = code
        self.retryable = False


def _fail(message):
    raise OpenApiError("malformed_control_plane", "malformed_request", message)


def _need(ok, message):
    if not ok:
        _fail(message)


def _dict(value, where):
    _need(type(value) is dict, f"{where}: not a dict")
    for key in dict.keys(value):
        _need(type(key) is str, f"{where}: non-str key")
    return value


def _list_of_str(value, where):
    _need(type(value) is list, f"{where}: not a list")
    for item in list.__iter__(value):
        _need(type(item) is str, f"{where}: non-str item")
    _need(len(set(value)) == len(value), f"{where}: duplicate item")
    return value


def _match(regex, value, where):
    _need(type(value) is str and regex.fullmatch(value) is not None, f"{where}: grammar")
    return value


def _exact(mapping, keys, where):
    _need(set(dict.keys(mapping)) == set(keys), f"{where}: keys")


def _example_ok(kind, item, value):
    if kind == "string":
        return type(value) is str
    if kind == "integer":
        return type(value) is int and -MAX_EXAMPLE_INT <= value <= MAX_EXAMPLE_INT
    if kind == "number":
        if type(value) is float:
            return math.isfinite(value)
        return type(value) is int and -MAX_EXAMPLE_INT <= value <= MAX_EXAMPLE_INT
    if kind == "boolean":
        return type(value) is bool
    if kind == "array":
        return type(value) is list and all(_example_ok(item, None, v) for v in list.__iter__(value))
    return False


def _copy_example(value):
    if type(value) is list:
        return [_copy_example(v) for v in list.__iter__(value)]
    return value


def _schema(fields, where, depth, side):
    _dict(fields, where)
    _need(len(fields) <= MAX_FIELDS, f"{where}: too many fields")
    props, required = {}, []
    for name, spec in dict.items(fields):
        at = f"{where}.{name}"
        _match(FIELD_RE, name, at)
        _need(not (CHESS_TOKENS & set(name.split("_"))), f"{at}: chess content")
        _dict(spec, at)
        _need(set(dict.keys(spec)) <= FIELD_KEYS, f"{at}: keys")
        _need("type" in spec and "required" in spec, f"{at}: type and required")
        kind = spec["type"]
        _need(type(kind) is str and kind in FIELD_TYPES, f"{at}: type")
        _need(type(spec["required"]) is bool, f"{at}: required")
        out = {"type": kind}
        if kind == "array":
            item = spec.get("items")
            _need(type(item) is str and item in ITEM_TYPES, f"{at}: items")
            out["items"] = {"type": item}
        else:
            _need("items" not in spec, f"{at}: items on a non-array")
            item = None
        if kind == "object":
            _need(depth < MAX_DEPTH, f"{at}: too deep")
            _need("example" not in spec, f"{at}: example on an object")
            _need("fields" in spec, f"{at}: object without fields")
            out = _schema(spec["fields"], at, depth + 1, side)
        else:
            _need("fields" not in spec, f"{at}: fields on a non-object")
        if "write_only" in spec:
            _need(spec["write_only"] is True, f"{at}: write_only")
            _need(side == "request", f"{at}: write_only outside a request")
            out["writeOnly"] = True
        if "example" in spec:
            _need(_example_ok(kind, item, spec["example"]), f"{at}: example type")
            out["examples"] = [_copy_example(spec["example"])]
        props[name] = out
        if spec["required"]:
            required.append(name)
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": True,
    }


def _error_schema(shape, closed):
    _dict(shape, "errors.shape")
    _exact(shape, ["error"], "errors.shape")
    inner = _dict(shape["error"], "errors.shape.error")
    _exact(inner, ["fields"], "errors.shape.error")
    fields = _dict(inner["fields"], "errors.shape.error.fields")
    _need(list(dict.keys(fields)) == ["code", "message", "retryable"], "error fields")
    body = _schema(fields, "errors.shape.error.fields", 1, "response")
    for name, kind in (("code", "string"), ("message", "string"), ("retryable", "boolean")):
        _need(body["properties"][name]["type"] == kind, f"error {name} type")
    _need(body["required"] == ["code", "message", "retryable"], "error fields required")
    code = body["properties"]["code"]
    body["properties"]["code"] = {
        "type": "string",
        "enum": list(closed),
        **({"examples": code["examples"]} if "examples" in code else {}),
    }
    return {
        "type": "object",
        "properties": {"error": body},
        "required": ["error"],
        "additionalProperties": True,
    }


def derive(source):
    """The one OpenAPI 3.1.0 document of a control-plane contract."""
    _dict(source, "source")
    _exact(source, ["schema_version", "contract", "areas"], "source")
    _need(type(source["schema_version"]) is int and source["schema_version"] == 2, "schema_version")
    cc = _dict(source["contract"], "contract")
    _need("versioning" in cc and "transport" in cc, "contract sections")
    versioning = _dict(cc["versioning"], "versioning")
    _need("base_path" in versioning, "base_path")
    base = _match(BASE_RE, versioning["base_path"], "base_path")
    major = BASE_RE.fullmatch(base).group(1)
    transport = _dict(cc["transport"], "transport")
    for key in ("auth", "read_only_operations", "errors"):
        _need(key in transport, f"transport.{key}")
    auth = _dict(transport["auth"], "auth")
    _need("public_operations" in auth, "public_operations")
    public = _list_of_str(auth["public_operations"], "public_operations")
    read_only = _list_of_str(transport["read_only_operations"], "read_only_operations")
    errors = _dict(transport["errors"], "errors")
    _need("shape" in errors and "closed_enum" in errors, "errors sections")
    closed = _list_of_str(errors["closed_enum"], "closed_enum")
    _need(len(closed) >= 1, "closed_enum empty")
    for code in closed:
        _need(code in STATUS or code in UNMAPPED, f"{code}: no status reading")
    error_schema = _error_schema(errors["shape"], closed)
    areas = _dict(source["areas"], "areas")
    _need(1 <= len(areas) <= MAX_AREAS, "areas count")
    paths, seen_routes, ids = {}, set(), []
    publics, read_onlys = [], []
    for area, aspec in dict.items(areas):
        _match(AREA_RE, area, "area")
        _dict(aspec, area)
        _need("ops" in aspec, f"{area}: ops")
        for key in dict.keys(aspec):
            _need(key == "ops" or type(aspec[key]) is str, f"{area}.{key}: extra")
        ops = _dict(aspec["ops"], f"{area}.ops")
        _need(1 <= len(ops) <= MAX_OPS, f"{area}: ops count")
        for op, spec in dict.items(ops):
            _match(OP_RE, op, area)
            name = f"{area}.{op}"
            _dict(spec, name)
            _exact(spec, OP_KEYS, name)
            method = spec["method"]
            _need(type(method) is str and method in METHODS, f"{name}: method")
            path = _match(PATH_RE, spec["path"], f"{name}.path")
            _need(path not in seen_routes, f"{name}: duplicate path")
            seen_routes.add(path)
            _need(type(spec["auth"]) is str and spec["auth"] in AUTH_VALUES, f"{name}: auth")
            _need(type(spec["mutating"]) is bool, f"{name}: mutating")
            if spec["auth"] == "public":
                publics.append(name)
            if not spec["mutating"]:
                read_onlys.append(name)
            request = _dict(spec["request"], f"{name}.request")
            response = _dict(spec["response"], f"{name}.response")
            _exact(request, ["fields"], f"{name}.request")
            _exact(response, ["fields"], f"{name}.response")
            req = _schema(request["fields"], f"{name}.request", 1, "request")
            resp = _schema(response["fields"], f"{name}.response", 1, "response")
            if method == "GET":
                _need(not spec["mutating"], f"{name}: mutating GET")
                _need(req["properties"] == {}, f"{name}: GET with request fields")
            codes = _list_of_str(spec["errors"], f"{name}.errors")
            _need(len(codes) >= 1, f"{name}: no errors")
            for code in codes:
                _need(code in closed, f"{name}: {code} outside closed_enum")
            by_status = {}
            for code in codes:
                if code in STATUS:
                    by_status.setdefault(STATUS[code], []).append(code)
            responses = {
                "200": {"description": "ok", "content": {"application/json": {"schema": resp}}}
            }
            for status in sorted(by_status):
                responses[str(status)] = {
                    "description": "error",
                    "x-error-codes": by_status[status],
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                    },
                }
            operation = {
                "operationId": name,
                "security": [] if spec["auth"] == "public" else [{"bearerAuth": []}],
                "parameters": (
                    [{"$ref": "#/components/parameters/IdempotencyKey"}] if spec["mutating"] else []
                ),
            }
            if method == "POST":
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": req}},
                }
            operation["responses"] = responses
            operation["x-unmapped-errors"] = [c for c in codes if c not in STATUS]
            paths[path] = {method.lower(): operation}
            ids.append(name)
    _need(sorted(publics) == sorted(public), "public_operations mismatch")
    _need(sorted(read_onlys) == sorted(read_only), "read_only_operations mismatch")
    return {
        "openapi": "3.1.0",
        "info": {"title": "replaceable-control-plane", "version": major},
        "servers": [{"url": base}],
        "paths": paths,
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "parameters": {
                "IdempotencyKey": {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }
            },
            "schemas": {"Error": error_schema},
        },
        "x-unmapped-error-codes": [c for c in closed if c not in STATUS],
    }


def canonical_bytes(document):
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# -- reference end


GOLDEN_SHA256 = "6f84e469fb40b97231dc0d4d4c2a54ebdc43482d00b5ba94ca685233c7526db3"
GOLDEN_LENGTH = 16995


def src():
    return yaml.safe_load(CONTROL_PLANE.read_text())


def op_of(source, name):
    area, op = name.split(".")
    return source["areas"][area]["ops"][op]


def _refused(source):
    """derive rejects source typed and fresh, no partial document, the
    source bit-identical."""
    before = copy.deepcopy(source)
    try:
        derive(source)
    except OpenApiError as exc:
        assert exc.failure_class == "malformed_control_plane"
        assert exc.code == FAILURE_MAPPING["malformed_control_plane"]
        assert exc.retryable is False
        assert exc.__cause__ is None and exc.__context__ is None
        assert source == before
        return True
    return False


def _accepted(source):
    before = copy.deepcopy(source)
    doc = derive(source)
    assert source == before
    json.loads(canonical_bytes(doc))
    return doc


def _walk(schema, out):
    """Every schema node reachable through properties/items."""
    out.append(schema)
    for sub in schema.get("properties", {}).values():
        _walk(sub, out)
    if "items" in schema:
        _walk(schema["items"], out)
    return out


def _ops(doc):
    for path, item in doc["paths"].items():
        for method, operation in item.items():
            yield path, method, operation


# -- R1: the contract document and its sibling bindings --------------------------


def test_lint_clean():
    lint()


def _mutants():
    def drop_unmapped(cc):
        cc["error_responses"]["status"]["unmapped"].pop()

    def invent_status(cc):
        st = cc["error_responses"]["status"]
        st["unmapped"].remove("internal")
        st["mock_reading"]["internal"] = 500

    def reorder_unmapped(cc):
        u = cc["error_responses"]["status"]["unmapped"]
        u[0], u[1] = u[1], u[0]

    def flip_fixed(cc):
        cc["error_responses"]["status"]["contract_fixed"]["auth_invalid"] = 403

    def mock_sha(cc):
        cc["error_responses"]["status"]["mock_source"]["sha256"] = "0" * 64

    def extra_section(cc):
        cc["notes"] = "x"

    def closed_errors(cc):
        cc["errors"]["closed_enum"].append("corrupt")

    def depth_bound(cc):
        cc["source"]["bounds"]["object_depth"] = 9

    def example_bound(cc):
        cc["source"]["bounds"]["example_integer_magnitude"] = 2**63 - 1

    return {
        f.__name__: f
        for f in (
            drop_unmapped,
            invent_status,
            reorder_unmapped,
            flip_fixed,
            mock_sha,
            extra_section,
            closed_errors,
            depth_bound,
            example_bound,
        )
    }


@pytest.mark.parametrize("name", sorted(_mutants()))
def test_mutations_fail_lint(name, tmp_path):
    doc = yaml.safe_load(CONTRACT.read_text())
    _mutants()[name](doc["contract"])
    path = tmp_path / "openapi.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    with pytest.raises(ContractError):
        lint(path)


def _lint_siblings(monkeypatch, tmp_path, *, mock_text=None, cp_edit=None):
    """Lint against edited copies of the linked siblings (the real files
    are never touched). The pinned mock sha is recomputed for an edited
    mock so the reading check itself is what must fail."""
    import tools.openapi_contract_lint as mod

    root = tmp_path / "root"
    for rel in _CC["links"].values():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    mock = root / _CC["links"]["control_plane_mock"]
    if mock_text is not None:
        mock.write_text(mock_text)
    if cp_edit is not None:
        cp_path = root / _CC["links"]["control_plane_contract"]
        cp = yaml.safe_load(cp_path.read_text())
        cp_edit(cp)
        cp_path.write_text(yaml.safe_dump(cp, sort_keys=False))
    sha = hashlib.sha256(mock.read_bytes()).hexdigest()
    doc = yaml.safe_load(CONTRACT.read_text())
    doc["contract"]["error_responses"]["status"]["mock_source"]["sha256"] = sha
    path = tmp_path / "openapi.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    pinned = copy.deepcopy(mod.ERROR_RESPONSES)
    pinned["status"]["mock_source"]["sha256"] = sha
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "ERROR_RESPONSES", pinned)
    mod.lint(path)


def test_sibling_harness_accepts_unedited_copies(monkeypatch, tmp_path):
    _lint_siblings(monkeypatch, tmp_path)


def _mock_edit(label):
    text = (ROOT / _CC["links"]["control_plane_mock"]).read_text()
    if label == "one-site-status-drift":
        i = text.index('"not_found", "unknown reservation"')
        j = text.index("status=404", i)
        return text[:j] + "status=410" + text[j + len("status=404") :]
    if label == "every-site-status-drift":
        out, i = text, 0
        while True:
            i = out.find('"not_found",', i)
            if i < 0:
                return out
            j = out.index("status=404", i)
            out = out[:j] + "status=410" + out[j + len("status=404") :]
            i = j
    if label == "code-outside-enum":
        return text.replace('"conflict", "account exists"', '"account_taken", "account exists"')
    if label == "contract-status-contradicted":
        i = text.index('"auth_invalid", "bad credentials"')
        j = text.index("status=401", i)
        return text[:j] + "status=403" + text[j + len("status=401") :]
    raise AssertionError(label)


MOCK_EDITS = (
    "code-outside-enum",
    "contract-status-contradicted",
    "every-site-status-drift",
    "one-site-status-drift",
)


@pytest.mark.parametrize("label", MOCK_EDITS)
def test_lint_binds_the_mock_reading(label, monkeypatch, tmp_path):
    edited = _mock_edit(label)
    assert edited != (ROOT / _CC["links"]["control_plane_mock"]).read_text()
    with pytest.raises(ContractError):
        _lint_siblings(monkeypatch, tmp_path, mock_text=edited)


def _cp_chess_field(cp):
    fields = cp["areas"]["quota"]["ops"]["reserve"]["request"]["fields"]
    fields["fen_hint"] = {"type": "string", "required": False}


def _cp_nested_chess(cp):
    policy = cp["areas"]["provider_routing"]["ops"]["route"]["request"]["fields"]["policy"]
    policy["fields"]["game_id"] = {"type": "string", "required": False}


def _cp_write_only_response(cp):
    fields = cp["areas"]["provider_routing"]["ops"]["register_key"]["response"]["fields"]
    fields["key_ref"]["write_only"] = True


def _cp_error_shape(cp):
    cp["contract"]["transport"]["errors"]["shape"]["error"]["fields"]["retryable"]["type"] = (
        "string"
    )


def _cp_enum_grows(cp):
    cp["contract"]["transport"]["errors"]["closed_enum"].append("gone")


@pytest.mark.parametrize(
    "edit",
    [_cp_chess_field, _cp_nested_chess, _cp_write_only_response, _cp_error_shape, _cp_enum_grows],
    ids=lambda f: f.__name__,
)
def test_lint_binds_the_control_plane(edit, monkeypatch, tmp_path):
    with pytest.raises(ContractError):
        _lint_siblings(monkeypatch, tmp_path, cp_edit=edit)


def test_error_enum_matches_mapping():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)


def test_bounds_are_the_contract_literals():
    bounds = _SRC["bounds"]
    literals = (MAX_DEPTH, MAX_AREAS, MAX_OPS, MAX_FIELDS, MAX_EXAMPLE_INT)
    contract = (
        bounds["object_depth"],
        bounds["areas"],
        bounds["ops_per_area"],
        bounds["fields_per_object"],
        bounds["example_integer_magnitude"],
    )
    assert literals == contract


# -- R2: the derived document over the real control-plane contract ---------------


def test_golden_document_bytes():
    data = canonical_bytes(derive(src()))
    assert len(data) == GOLDEN_LENGTH
    assert hashlib.sha256(data).hexdigest() == GOLDEN_SHA256


def test_top_level_shape():
    doc = derive(src())
    assert list(doc) == [
        "openapi",
        "info",
        "servers",
        "paths",
        "components",
        "x-unmapped-error-codes",
    ]
    assert doc["openapi"] == "3.1.0"
    assert doc["info"] == {"title": "replaceable-control-plane", "version": "1"}
    assert doc["servers"] == [{"url": src()["contract"]["versioning"]["base_path"]}]


def test_one_path_per_op_in_source_order():
    source = src()
    expected = [
        (op["path"], op["method"].lower(), f"{a}.{o}")
        for a, spec in source["areas"].items()
        for o, op in spec["ops"].items()
    ]
    got = [(p, m, operation["operationId"]) for p, m, operation in _ops(derive(source))]
    assert got == expected
    assert len(got) == 14


def test_security_follows_public_operations():
    source = src()
    public = source["contract"]["transport"]["auth"]["public_operations"]
    doc = derive(source)
    assert doc["components"]["securitySchemes"] == {
        "bearerAuth": {"type": "http", "scheme": "bearer"}
    }
    for _p, _m, operation in _ops(doc):
        want = [] if operation["operationId"] in public else [{"bearerAuth": []}]
        assert operation["security"] == want, operation["operationId"]


def test_idempotency_key_exactly_on_mutating_ops():
    source = src()
    read_only = source["contract"]["transport"]["read_only_operations"]
    doc = derive(source)
    assert doc["components"]["parameters"] == {
        "IdempotencyKey": {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
        }
    }
    seen_read_only = []
    for _p, _m, operation in _ops(doc):
        name = operation["operationId"]
        if op_of(source, name)["mutating"]:
            assert operation["parameters"] == [{"$ref": "#/components/parameters/IdempotencyKey"}]
        else:
            assert operation["parameters"] == []
            seen_read_only.append(name)
    assert sorted(seen_read_only) == sorted(read_only)


def _request_schema(operation):
    return operation["requestBody"]["content"]["application/json"]["schema"]


def _response_schemas(doc):
    out = [doc["components"]["schemas"]["Error"]]
    for _p, _m, operation in _ops(doc):
        out.append(operation["responses"]["200"]["content"]["application/json"]["schema"])
    return out


def test_write_only_only_on_key_material_and_never_in_a_response():
    doc = derive(src())
    register = doc["paths"]["/provider-keys/register"]["post"]
    assert _request_schema(register)["properties"]["key_material"] == {
        "type": "string",
        "writeOnly": True,
        "examples": ["12345678"],
    }
    flagged = []
    for _p, _m, operation in _ops(doc):
        if "requestBody" in operation:
            for node in _walk(_request_schema(operation), []):
                if "writeOnly" in node:
                    flagged.append((operation["operationId"], node))
    assert len(flagged) == 1
    for schema in _response_schemas(doc):
        assert all("writeOnly" not in node for node in _walk(schema, []))


def test_error_schema_is_the_control_plane_shape_exactly():
    source = src()
    closed = source["contract"]["transport"]["errors"]["closed_enum"]
    assert derive(source)["components"]["schemas"]["Error"] == {
        "type": "object",
        "properties": {
            "error": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "enum": closed, "examples": ["not_found"]},
                    "message": {"type": "string", "examples": ["human-readable detail"]},
                    "retryable": {"type": "boolean", "examples": [False]},
                },
                "required": ["code", "message", "retryable"],
                "additionalProperties": True,
            }
        },
        "required": ["error"],
        "additionalProperties": True,
    }


def test_error_responses_follow_the_status_readings():
    source = src()
    fixed, mock = _STAT["contract_fixed"], _STAT["mock_reading"]
    doc = derive(source)
    for _p, _m, operation in _ops(doc):
        codes = op_of(source, operation["operationId"])["errors"]
        want = {}
        for code in codes:
            status = fixed.get(code, mock.get(code))
            if status is not None:
                want.setdefault(str(status), []).append(code)
        got = {k: v["x-error-codes"] for k, v in operation["responses"].items() if k != "200"}
        assert got == want, operation["operationId"]
        assert list(operation["responses"]) == ["200", *sorted(want)]
        for key in want:
            assert operation["responses"][key]["content"] == {
                "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
            }
        assert operation["x-unmapped-errors"] == [c for c in codes if c in _STAT["unmapped"]]


def test_unmapped_codes_listed_never_given_a_status():
    doc = derive(src())
    assert doc["x-unmapped-error-codes"] == [
        "entitlement_missing",
        "provider_unavailable",
        "cost_cap_exceeded",
        "rate_limited",
        "internal",
    ]
    route = doc["paths"]["/provider-routing/route"]["post"]
    assert route["x-unmapped-errors"] == [
        "entitlement_missing",
        "cost_cap_exceeded",
        "provider_unavailable",
        "internal",
    ]
    assert set(route["responses"]) == {"200", "400", "401"}
    assert all(
        set(operation["responses"]) <= {"200", "400", "401", "404", "409", "429"}
        for _p, _m, operation in _ops(doc)
    )


def test_every_object_allows_additional_properties():
    doc = derive(src())
    schemas = list(_response_schemas(doc))
    for _p, _m, operation in _ops(doc):
        if "requestBody" in operation:
            schemas.append(_request_schema(operation))
    objects = [n for s in schemas for n in _walk(s, []) if n.get("type") == "object"]
    assert len(objects) >= 30
    assert all(n["additionalProperties"] is True for n in objects)


def test_nested_object_and_examples_are_faithful():
    route = derive(src())["paths"]["/provider-routing/route"]["post"]
    assert _request_schema(route) == {
        "type": "object",
        "properties": {
            "capability": {"type": "string", "examples": ["analysis-fast"]},
            "policy": {
                "type": "object",
                "properties": {
                    "prefer": {"type": "string", "examples": ["own-key"]},
                    "max_cost_usd": {"type": "number", "examples": [0.05]},
                },
                "required": [],
                "additionalProperties": True,
            },
        },
        "required": ["capability"],
        "additionalProperties": True,
    }


def test_get_ops_have_no_request_body():
    doc = derive(src())
    gets = [o for _p, m, o in _ops(doc) if m == "get"]
    assert [o["operationId"] for o in gets] == ["entitlements.get", "billing.get_subscription"]
    assert all("requestBody" not in o for o in gets)
    posts = [o for _p, m, o in _ops(doc) if m == "post"]
    assert all(o["requestBody"]["required"] is True for o in posts)


def test_deterministic_and_source_untouched():
    source = src()
    before = copy.deepcopy(source)
    first = canonical_bytes(derive(source))
    assert canonical_bytes(derive(source)) == first
    assert canonical_bytes(derive(copy.deepcopy(source))) == first
    assert source == before


def _containers(value, out):
    if type(value) in (dict, list):
        out.add(id(value))
        for v in value.values() if type(value) is dict else value:
            _containers(v, out)
    return out


def with_field(name="extra", **spec):
    source = src()
    fields = op_of(source, "quota.reserve")["request"]["fields"]
    fields[name] = {"type": "string", "required": False, **spec}
    return source


def test_document_is_detached_from_the_source():
    source = with_field("tags", type="array", items="string", example=["a", "b"])
    doc = _accepted(source)
    assert not (_containers(source, set()) & _containers(doc, set()))
    op_of(source, "quota.reserve")["request"]["fields"]["tags"]["example"].append("c")
    schema = _request_schema(doc["paths"]["/quota/reserve"]["post"])
    assert schema["properties"]["tags"] == {
        "type": "array",
        "items": {"type": "string"},
        "examples": [["a", "b"]],
    }


def test_canonical_bytes_pinned():
    assert canonical_bytes({"b": 1, "a": ["é", 1.5, True, None]}) == (
        '{"b":1,"a":["é",1.5,true,null]}'.encode()
    )


# -- R3: boundaries (refused one past each bound, accepted on it) ---------------


def nest(levels):
    """quota.reserve gains one object field nested `levels` deep."""
    inner = {"leaf": {"type": "string", "required": False}}
    for _ in range(levels):
        inner = {"wrap": {"type": "object", "required": False, "fields": inner}}
    source = src()
    op_of(source, "quota.reserve")["request"]["fields"]["box"] = {
        "type": "object",
        "required": False,
        "fields": inner,
    }
    return source


def test_depth_bound():
    # box's fields are depth 2; each wrap adds one: MAX_DEPTH-2 wraps put
    # the leaf table at exactly MAX_DEPTH
    _accepted(nest(MAX_DEPTH - 2))
    assert _refused(nest(MAX_DEPTH - 1))


def many_fields(n):
    source = src()
    op_of(source, "quota.reserve")["request"]["fields"] = {
        f"f{i}": {"type": "integer", "required": False} for i in range(n)
    }
    return source


def test_fields_bound():
    _accepted(many_fields(MAX_FIELDS))
    assert _refused(many_fields(MAX_FIELDS + 1))


def test_fields_bound_nested():
    source = src()
    policy = op_of(source, "provider_routing.route")["request"]["fields"]["policy"]
    policy["fields"] = {f"f{i}": {"type": "boolean", "required": False} for i in range(MAX_FIELDS)}
    _accepted(source)
    policy["fields"]["over"] = {"type": "boolean", "required": False}
    assert _refused(source)


def _new_op(path):
    return {
        "method": "POST",
        "path": path,
        "auth": "required",
        "mutating": True,
        "request": {"fields": {}},
        "response": {"fields": {}},
        "errors": ["malformed_request"],
    }


def many_areas(n):
    source = src()
    for i in range(n - len(source["areas"])):
        source["areas"][f"extra_{chr(97 + i // 26)}{chr(97 + i % 26)}"] = {
            "ops": {"do": _new_op(f"/extra/a{i}")}
        }
    return source


def test_areas_bound():
    assert len(_accepted(many_areas(MAX_AREAS))["paths"]) == 14 + MAX_AREAS - 5
    assert _refused(many_areas(MAX_AREAS + 1))


def many_ops(n):
    source = src()
    ops = source["areas"]["quota"]["ops"]
    for i in range(n - len(ops)):
        ops[f"op_{chr(97 + i // 26)}{chr(97 + i % 26)}"] = _new_op(f"/quota/x{i}")
    return source


def test_ops_bound():
    _accepted(many_ops(MAX_OPS))
    assert _refused(many_ops(MAX_OPS + 1))


def test_areas_and_ops_lower_bound():
    source = src()
    source["areas"]["quota"]["ops"] = {}
    assert _refused(source)
    source = src()
    source["areas"] = {}
    assert _refused(source)


def renamed_area(name):
    source = src()
    source["areas"][name] = {"ops": {"do": _new_op("/renamed/do")}}
    return source


def renamed_op(name):
    source = src()
    source["areas"]["quota"]["ops"][name] = _new_op("/quota/renamed")
    return source


def test_area_and_op_grammar_edges():
    _accepted(renamed_area("a" * 32))
    assert _refused(renamed_area("a" * 33))
    _accepted(renamed_op("o" * 32))
    assert _refused(renamed_op("o" * 33))
    assert _refused(renamed_area("quota_x\n"))
    assert _refused(renamed_op("do\n"))


def pathed(path):
    source = src()
    op_of(source, "quota.reserve")["path"] = path
    return source


def test_path_grammar_edges():
    assert _accepted(pathed("/a" * 8))["paths"]["/a" * 8]
    assert _refused(pathed("/a" * 9))
    _accepted(pathed("/" + "b" * 32))
    assert _refused(pathed("/" + "b" * 33))
    assert _refused(pathed("/quota/reserve\n"))
    assert _refused(pathed("/Quota"))


def based(base):
    source = src()
    source["contract"]["versioning"]["base_path"] = base
    return source


def test_base_path_edges():
    assert _accepted(based("/control/v999"))["info"]["version"] == "999"
    assert _refused(based("/control/v1000"))
    assert _refused(based("/control/v0"))
    assert _refused(based("/control/v1\n"))


def test_field_name_edges():
    _accepted(with_field("f" * 64))
    assert _refused(with_field("f" * 65))
    assert _refused(with_field("extra\n"))


def test_integer_example_bounds():
    for value in (MAX_EXAMPLE_INT, -MAX_EXAMPLE_INT):
        _accepted(with_field(type="integer", example=value))
        _accepted(with_field(type="number", example=value))
    for value in (MAX_EXAMPLE_INT + 1, -MAX_EXAMPLE_INT - 1, 10**5000):
        assert _refused(with_field(type="integer", example=value))
        assert _refused(with_field(type="number", example=value))
    _accepted(with_field(type="array", items="integer", example=[MAX_EXAMPLE_INT]))
    assert _refused(with_field(type="array", items="integer", example=[MAX_EXAMPLE_INT + 1]))


def test_float_example_must_be_finite():
    _accepted(with_field(type="number", example=1.5e308))
    for value in (math.inf, -math.inf, math.nan):
        assert _refused(with_field(type="number", example=value))
    assert _refused(with_field(type="array", items="number", example=[math.nan]))
    assert _refused(with_field(type="integer", example=1.0))


def test_bool_is_never_a_number():
    assert _refused(with_field(type="integer", example=True))
    assert _refused(with_field(type="number", example=False))
    assert _refused(with_field(type="array", items="integer", example=[True]))
    _accepted(with_field(type="boolean", example=True))


# -- R4: malformed sources ------------------------------------------------------


def _edit(path_fn):
    def build():
        source = src()
        path_fn(source)
        return source

    return build


MALFORMED = {
    "not-a-dict": lambda: [],
    "extra-top-key": _edit(lambda s: s.update(extra=1)),
    "missing-areas": _edit(lambda s: s.pop("areas")),
    "schema-version-1": _edit(lambda s: s.update(schema_version=1)),
    "missing-transport": _edit(lambda s: s["contract"].pop("transport")),
    "missing-public": _edit(lambda s: s["contract"]["transport"]["auth"].pop("public_operations")),
    "public-mismatch": _edit(
        lambda s: s["contract"]["transport"]["auth"]["public_operations"].pop()
    ),
    "read-only-mismatch": _edit(lambda s: s["contract"]["transport"]["read_only_operations"].pop()),
    "closed-enum-empty": _edit(
        lambda s: s["contract"]["transport"]["errors"].update(closed_enum=[])
    ),
    "closed-enum-duplicate": _edit(
        lambda s: s["contract"]["transport"]["errors"]["closed_enum"].append("internal")
    ),
    "closed-enum-code-without-reading": _edit(
        lambda s: s["contract"]["transport"]["errors"]["closed_enum"].append("gone")
    ),
    "error-shape-extra-field": _edit(
        lambda s: s["contract"]["transport"]["errors"]["shape"]["error"]["fields"].update(
            extra={"type": "string", "required": True}
        )
    ),
    "error-retryable-string": _edit(
        lambda s: s["contract"]["transport"]["errors"]["shape"]["error"]["fields"][
            "retryable"
        ].update(type="string", example="no")
    ),
    "error-code-optional": _edit(
        lambda s: s["contract"]["transport"]["errors"]["shape"]["error"]["fields"]["code"].update(
            required=False
        )
    ),
    "op-extra-key": _edit(lambda s: op_of(s, "quota.reserve").update(extra=1)),
    "op-missing-errors": _edit(lambda s: op_of(s, "quota.reserve").pop("errors")),
    "method-put": _edit(lambda s: op_of(s, "quota.reserve").update(method="PUT")),
    "method-lowercase": _edit(lambda s: op_of(s, "quota.reserve").update(method="post")),
    "auth-optional": _edit(lambda s: op_of(s, "quota.reserve").update(auth="optional")),
    "duplicate-path": _edit(lambda s: op_of(s, "quota.reserve").update(path="/quota/commit")),
    "errors-empty": _edit(lambda s: op_of(s, "quota.reserve").update(errors=[])),
    "errors-duplicate": _edit(lambda s: op_of(s, "quota.reserve")["errors"].append("internal")),
    "error-outside-enum": _edit(lambda s: op_of(s, "quota.reserve")["errors"].append("gone")),
    "mutating-get": _edit(lambda s: op_of(s, "entitlements.get").update(mutating=True)),
    "get-with-request-field": _edit(
        lambda s: op_of(s, "entitlements.get")["request"]["fields"].update(
            x={"type": "string", "required": False}
        )
    ),
    "request-extra-key": _edit(lambda s: op_of(s, "quota.reserve")["request"].update(extra=1)),
    "field-missing-type": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(x={"required": True})
    ),
    "field-unknown-key": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "string", "required": True, "format": "uuid"}
        )
    ),
    "field-type-date": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "date", "required": True}
        )
    ),
    "array-without-items": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "array", "required": True}
        )
    ),
    "array-of-objects": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "array", "required": True, "items": "object"}
        )
    ),
    "items-on-string": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "string", "required": True, "items": "string"}
        )
    ),
    "object-without-fields": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "object", "required": True}
        )
    ),
    "object-with-example": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "object", "required": True, "fields": {}, "example": {}}
        )
    ),
    "fields-on-string": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "string", "required": True, "fields": {}}
        )
    ),
    "example-wrong-type": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "string", "required": True, "example": 5}
        )
    ),
    "write-only-false": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            x={"type": "string", "required": True, "write_only": False}
        )
    ),
    "write-only-in-response": _edit(
        lambda s: op_of(s, "provider_routing.register_key")["response"]["fields"]["key_ref"].update(
            write_only=True
        )
    ),
    "write-only-nested-response": _edit(
        lambda s: op_of(s, "quota.reserve")["response"]["fields"].update(
            box={
                "type": "object",
                "required": False,
                "fields": {"secret": {"type": "string", "required": False, "write_only": True}},
            }
        )
    ),
    "chess-field": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            fen_hint={"type": "string", "required": False}
        )
    ),
    "chess-field-response": _edit(
        lambda s: op_of(s, "quota.reserve")["response"]["fields"].update(
            last_move={"type": "string", "required": False}
        )
    ),
    "chess-field-nested": _edit(
        lambda s: op_of(s, "provider_routing.route")["request"]["fields"]["policy"][
            "fields"
        ].update(game_id={"type": "string", "required": False})
    ),
    "chess-field-plural": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            analysis_notes={"type": "string", "required": False}
        )
    ),
    "area-extra-non-str": _edit(lambda s: s["areas"]["billing"].update(pci_boundary=1)),
    "area-missing-ops": _edit(lambda s: s["areas"]["billing"].pop("ops")),
    # refused by the areas floor alone: the public and read-only lists
    # agree with the (empty) op set
    "no-areas-consistent-lists": _edit(
        lambda s: (
            s.update(areas={}),
            s["contract"]["transport"]["auth"].update(public_operations=[]),
            s["contract"]["transport"].update(read_only_operations=[]),
        )
    ),
    # refused by the mutating-GET rule alone: the read-only list agrees
    "mutating-get-consistent-list": _edit(
        lambda s: (
            op_of(s, "entitlements.get").update(mutating=True),
            s["contract"]["transport"]["read_only_operations"].remove("entitlements.get"),
        )
    ),
    "error-shape-extra-optional-field": _edit(
        lambda s: s["contract"]["transport"]["errors"]["shape"]["error"]["fields"].update(
            extra={"type": "string", "required": False}
        )
    ),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_sources(name):
    assert _refused(MALFORMED[name]())


def test_chess_token_must_be_a_whole_segment():
    # "san" is a token; "sandbox" is not
    _accepted(with_field("sandbox_id"))
    _accepted(with_field("movement"))


# -- R5: hostile types (exact types only) ---------------------------------------


class S(str):
    pass


class I(int):  # noqa: E742
    pass


class D(dict):
    pass


class L(list):
    pass


def _rekey(mapping, old, new):
    items = list(mapping.items())
    mapping.clear()
    for k, v in items:
        mapping[new if k == old else k] = v


HOSTILE = {
    "source-dict-subclass": lambda: D(src()),
    "top-key-str-subclass": _edit(lambda s: _rekey(s, "areas", S("areas"))),
    "schema-version-int-subclass": _edit(lambda s: s.update(schema_version=I(2))),
    "schema-version-bool": _edit(lambda s: s.update(schema_version=True)),
    "schema-version-float": _edit(lambda s: s.update(schema_version=2.0)),
    "base-path-str-subclass": _edit(
        lambda s: s["contract"]["versioning"].update(base_path=S("/control/v1"))
    ),
    "area-key-str-subclass": _edit(lambda s: _rekey(s["areas"], "quota", S("quota"))),
    "area-key-int": _edit(lambda s: _rekey(s["areas"], "quota", 7)),
    "op-key-str-subclass": _edit(
        lambda s: _rekey(s["areas"]["quota"]["ops"], "reserve", S("reserve"))
    ),
    "op-dict-subclass": _edit(
        lambda s: s["areas"]["quota"]["ops"].update(reserve=D(op_of(s, "quota.reserve")))
    ),
    "method-str-subclass": _edit(lambda s: op_of(s, "quota.reserve").update(method=S("POST"))),
    "auth-str-subclass": _edit(lambda s: op_of(s, "quota.reserve").update(auth=S("required"))),
    "path-str-subclass": _edit(
        lambda s: op_of(s, "quota.reserve").update(path=S("/quota/reserve"))
    ),
    "mutating-int": _edit(lambda s: op_of(s, "quota.reserve").update(mutating=1)),
    "mutating-str": _edit(lambda s: op_of(s, "quota.reserve").update(mutating="true")),
    "errors-list-subclass": _edit(
        lambda s: op_of(s, "quota.reserve").update(errors=L(op_of(s, "quota.reserve")["errors"]))
    ),
    "errors-tuple": _edit(
        lambda s: op_of(s, "quota.reserve").update(
            errors=tuple(op_of(s, "quota.reserve")["errors"])
        )
    ),
    "error-code-str-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["errors"].__setitem__(0, S("auth_expired"))
    ),
    "closed-enum-str-subclass": _edit(
        lambda s: s["contract"]["transport"]["errors"]["closed_enum"].__setitem__(
            0, S("auth_expired")
        )
    ),
    "public-op-str-subclass": _edit(
        lambda s: s["contract"]["transport"]["auth"]["public_operations"].__setitem__(
            0, S("identity.register")
        )
    ),
    "field-key-str-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            {S("extra"): {"type": "string", "required": False}}
        )
    ),
    "field-key-int": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            {1: {"type": "string", "required": False}}
        )
    ),
    "field-spec-key-str-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={S("type"): "string", "required": False}
        )
    ),
    "field-type-str-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={"type": S("string"), "required": False}
        )
    ),
    "required-int": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={"type": "string", "required": 0}
        )
    ),
    "write-only-int": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={"type": "string", "required": False, "write_only": 1}
        )
    ),
    "items-str-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={"type": "array", "required": False, "items": S("string")}
        )
    ),
    "example-str-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={"type": "string", "required": False, "example": S("x")}
        )
    ),
    "example-int-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={"type": "integer", "required": False, "example": I(3)}
        )
    ),
    "example-list-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"]["fields"].update(
            extra={"type": "array", "required": False, "items": "string", "example": L(["a"])}
        )
    ),
    "fields-dict-subclass": _edit(
        lambda s: op_of(s, "quota.reserve")["request"].update(
            fields=D(op_of(s, "quota.reserve")["request"]["fields"])
        )
    ),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_types(name):
    assert _refused(HOSTILE[name]())


# -- R6: one-edit reference mutants, killed only by the behavioral rows above ----


REFERENCE_EDITS = {
    "depth-inclusive": ("_need(depth < MAX_DEPTH,", "_need(depth <= MAX_DEPTH,"),
    "fields-exclusive": ("len(fields) <= MAX_FIELDS", "len(fields) < MAX_FIELDS"),
    "fields-unbounded": ("len(fields) <= MAX_FIELDS", "True"),
    "areas-unbounded": ("1 <= len(areas) <= MAX_AREAS", "1 <= len(areas)"),
    "areas-no-floor": ("1 <= len(areas) <= MAX_AREAS", "len(areas) <= MAX_AREAS"),
    "ops-exclusive": ("1 <= len(ops) <= MAX_OPS", "1 <= len(ops) < MAX_OPS"),
    "ops-no-floor": ("1 <= len(ops) <= MAX_OPS", "len(ops) <= MAX_OPS"),
    "match-not-fullmatch": ("regex.fullmatch(value) is not None", "regex.match(value) is not None"),
    "base-major-uncaptured": (
        "major = BASE_RE.fullmatch(base).group(1)",
        'major = base.rsplit("/", 1)[1]',
    ),
    "write-only-anywhere": (
        '_need(side == "request", f"{at}: write_only outside a request")',
        "pass",
    ),
    "write-only-dropped": ('out["writeOnly"] = True', "pass"),
    "write-only-any-truthy": (
        '_need(spec["write_only"] is True,',
        '_need(bool(spec["write_only"]),',
    ),
    "idempotency-on-all": ('if spec["mutating"] else []', "if True else []"),
    "idempotency-on-none": ('if spec["mutating"] else []', "if False else []"),
    "security-on-all": (
        '"security": [] if spec["auth"] == "public" else [{"bearerAuth": []}],',
        '"security": [{"bearerAuth": []}],',
    ),
    "public-unchecked": (
        '_need(sorted(publics) == sorted(public), "public_operations mismatch")',
        "pass",
    ),
    "read-only-unchecked": (
        '_need(sorted(read_onlys) == sorted(read_only), "read_only_operations mismatch")',
        "pass",
    ),
    "chess-unchecked": (
        '_need(not (CHESS_TOKENS & set(name.split("_"))), f"{at}: chess content")',
        "pass",
    ),
    "chess-substring": (
        'set(name.split("_"))',
        "{t for t in CHESS_TOKENS if t in name}",
    ),
    "int-example-isinstance": (
        'if kind == "integer":\n        return type(value) is int and',
        'if kind == "integer":\n        return isinstance(value, int) and',
    ),
    "int-example-unbounded": (
        'if kind == "integer":\n        return type(value) is'
        " int and -MAX_EXAMPLE_INT <= value <= MAX_EXAMPLE_INT",
        'if kind == "integer":\n        return type(value) is int',
    ),
    "int-example-no-floor": (
        'if kind == "integer":\n        return type(value) is'
        " int and -MAX_EXAMPLE_INT <= value <= MAX_EXAMPLE_INT",
        'if kind == "integer":\n        return type(value) is int and value <= MAX_EXAMPLE_INT',
    ),
    "int-example-exclusive": (
        'if kind == "integer":\n        return type(value) is'
        " int and -MAX_EXAMPLE_INT <= value <= MAX_EXAMPLE_INT",
        'if kind == "integer":\n        return type(value) is'
        " int and -MAX_EXAMPLE_INT <= value < MAX_EXAMPLE_INT",
    ),
    "number-nonfinite": ("return math.isfinite(value)", "return True"),
    "number-int-unbounded": (
        "        return type(value) is int and -MAX_EXAMPLE_INT"
        ' <= value <= MAX_EXAMPLE_INT\n    if kind == "boolean"',
        '        return type(value) is int\n    if kind == "boolean"',
    ),
    "number-bool-accepted": (
        "        return type(value) is int and -MAX_EXAMPLE_INT"
        ' <= value <= MAX_EXAMPLE_INT\n    if kind == "boolean"',
        "        return isinstance(value, int) and -MAX_EXAMPLE_INT"
        ' <= value <= MAX_EXAMPLE_INT\n    if kind == "boolean"',
    ),
    "array-example-unchecked": (
        "all(_example_ok(item, None, v) for v in list.__iter__(value))",
        "True",
    ),
    "example-uncopied": (
        "return [_copy_example(v) for v in list.__iter__(value)]",
        "return value",
    ),
    "example-type-unchecked": (
        '_need(_example_ok(kind, item, spec["example"]), f"{at}: example type")',
        "pass",
    ),
    "additional-false": (
        '"required": required,\n        "additionalProperties": True,',
        '"required": required,\n        "additionalProperties": False,',
    ),
    "error-additional-false": (
        '"required": ["error"],\n        "additionalProperties": True,',
        '"required": ["error"],\n        "additionalProperties": False,',
    ),
    "enum-sorted": ('"enum": list(closed)', '"enum": sorted(closed)'),
    "code-without-reading": (
        '_need(code in STATUS or code in UNMAPPED, f"{code}: no status reading")',
        "pass",
    ),
    "error-outside-enum-allowed": (
        '_need(code in closed, f"{name}: {code} outside closed_enum")',
        "pass",
    ),
    "errors-may-be-empty": ('_need(len(codes) >= 1, f"{name}: no errors")', "pass"),
    "unmapped-per-op-dropped": (
        'operation["x-unmapped-errors"] = [c for c in codes if c not in STATUS]',
        'operation["x-unmapped-errors"] = []',
    ),
    "unmapped-given-500": (
        "                if code in STATUS:\n                 "
        "   by_status.setdefault(STATUS[code], []).append(code)",
        "                by_status.setdefault(STATUS.get(code, 500), []).append(code)",
    ),
    "statuses-unsorted": ("for status in sorted(by_status):", "for status in by_status:"),
    "duplicate-path-allowed": ('_need(path not in seen_routes, f"{name}: duplicate path")', "pass"),
    "mutating-get-allowed": ('_need(not spec["mutating"], f"{name}: mutating GET")', "pass"),
    "get-request-allowed": (
        '_need(req["properties"] == {}, f"{name}: GET with request fields")',
        "pass",
    ),
    "method-isinstance": (
        "_need(type(method) is str and method in METHODS,",
        "_need(isinstance(method, str) and method in METHODS,",
    ),
    "mutating-truthy": (
        '_need(type(spec["mutating"]) is bool,',
        '_need(spec["mutating"] in (0, 1),',
    ),
    "required-truthy": (
        '_need(type(spec["required"]) is bool,',
        '_need(spec["required"] in (0, 1),',
    ),
    "key-isinstance": (
        '_need(type(key) is str, f"{where}: non-str key")',
        '_need(isinstance(key, str), f"{where}: non-str key")',
    ),
    "dict-isinstance": (
        '_need(type(value) is dict, f"{where}: not a dict")',
        '_need(isinstance(value, dict), f"{where}: not a dict")',
    ),
    "list-isinstance": (
        '_need(type(value) is list, f"{where}: not a list")',
        '_need(isinstance(value, (list, tuple)), f"{where}: not a list")',
    ),
    "list-duplicates-allowed": (
        '_need(len(set(value)) == len(value), f"{where}: duplicate item")',
        "pass",
    ),
    "schema-version-loose": (
        '_need(type(source["schema_version"]) is int and source["schema_version"] == 2,',
        '_need(source["schema_version"] == 2,',
    ),
    "top-level-subset": (
        '_exact(source, ["schema_version", "contract", "areas"], "source")',
        "pass",
    ),
    "op-keys-subset": ("_exact(spec, OP_KEYS, name)", "pass"),
    "field-keys-open": ('_need(set(dict.keys(spec)) <= FIELD_KEYS, f"{at}: keys")', "pass"),
    "items-on-scalar-allowed": (
        '_need("items" not in spec, f"{at}: items on a non-array")',
        "pass",
    ),
    "fields-on-scalar-allowed": (
        '_need("fields" not in spec, f"{at}: fields on a non-object")',
        "pass",
    ),
    "area-extras-any": (
        '_need(key == "ops" or type(aspec[key]) is str, f"{area}.{key}: extra")',
        "pass",
    ),
    "error-fields-reordered-ok": (
        '_need(list(dict.keys(fields)) == ["code", "message", "retryable"], "error fields")',
        '_need(set(dict.keys(fields)) >= {"code", "message", "retryable"}, "error fields")',
    ),
    "error-required-unchecked": (
        '_need(body["required"] == ["code", "message", "retryable"], "error fields required")',
        "pass",
    ),
    "error-types-unchecked": (
        '_need(body["properties"][name]["type"] == kind, f"error {name} type")',
        "pass",
    ),
    "keys-sorted": (
        'json.dumps(document, ensure_ascii=False, separators=(",", ":"))',
        'json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)',
    ),
    "ascii-escaped": (
        'json.dumps(document, ensure_ascii=False, separators=(",", ":"))',
        'json.dumps(document, ensure_ascii=True, separators=(",", ":"))',
    ),
    "spaced-separators": (
        'json.dumps(document, ensure_ascii=False, separators=(",", ":"))',
        "json.dumps(document, ensure_ascii=False)",
    ),
    "retryable-true": ("self.retryable = False", "self.retryable = True"),
    "chained-error": (
        '    raise OpenApiError("malformed_control_plane", "malformed_request", message)',
        "    try:\n        raise KeyError(message)\n    except KeyError:\n"
        '        raise OpenApiError("malformed_control_plane", "malformed_request", message)',
    ),
    "wrong-code": (
        'OpenApiError("malformed_control_plane", "malformed_request", message)',
        'OpenApiError("malformed_control_plane", "internal", message)',
    ),
}


# Edits that must NOT change behavior.
EQUIVALENT_EDITS = {
    # _example_ok has no object branch, so an object example is already
    # refused as the wrong example type
    "object-example-rule-implied": (
        '_need("example" not in spec, f"{at}: example on an object")',
        "pass",
    ),
    # the field grammar is lowercase ASCII only, so lowering is a no-op
    "chess-lowercased": ('set(name.split("_"))', 'set(name.lower().split("_"))'),
    # _list_of_str checks type(value) is list exactly first, so the bound
    # iterator is the built-in one either way
    "list-plain-iteration": (
        "    for item in list.__iter__(value):\n        _need(type(item)"
        ' is str, f"{where}: non-str item")',
        '    for item in value:\n        _need(type(item) is str, f"{where}: non-str item")',
    ),
}


def _mutant_reference(name):
    """Rebuild the whole reference block with one edit applied; returns
    the rebuilt top-level names."""
    import ast
    import inspect

    old, new = {**REFERENCE_EDITS, **EQUIVALENT_EDITS}[name]
    module_source = inspect.getsource(sys.modules[__name__])
    begin = module_source.index("# -- reference begin")
    end = module_source.index("# -- reference end")
    source = module_source[begin:end]
    assert source.count(old) == 1, name
    source = source.replace(old, new)
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
    namespace = dict(globals())
    exec(compile(source, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return {n: namespace[n] for n in names}


def _install(monkeypatch, name):
    for key, value in _mutant_reference(name).items():
        monkeypatch.setitem(globals(), key, value)


def _battery():
    """Every behavior test that takes no fixture, plus the parametrized
    malformed and hostile rows; returns the failing labels."""
    failures = []
    jobs = [
        (n, f)
        for n, f in sorted(globals().items())
        if n.startswith("test_")
        and callable(f)
        and f.__code__.co_argcount == 0
        and n
        not in (
            "test_lint_clean",
            "test_error_enum_matches_mapping",
            "test_bounds_are_the_contract_literals",
            "test_reference_edits_apply_once",
            "test_identity_battery_is_green",
        )
    ]
    jobs += [(f"malformed:{n}", lambda n=n: test_malformed_sources(n)) for n in MALFORMED]
    jobs += [(f"hostile:{n}", lambda n=n: test_hostile_types(n)) for n in HOSTILE]
    for label, job in jobs:
        try:
            job()
        except BaseException as exc:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(exc).__name__}")
    return failures


def test_reference_edits_apply_once():
    for name in {**REFERENCE_EDITS, **EQUIVALENT_EDITS}:
        _mutant_reference(name)


def test_identity_battery_is_green():
    assert _battery() == []


@pytest.mark.parametrize("name", sorted(REFERENCE_EDITS))
def test_reference_mutant_is_red(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() != [], name


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name, monkeypatch):
    _install(monkeypatch, name)
    assert _battery() == [], name
