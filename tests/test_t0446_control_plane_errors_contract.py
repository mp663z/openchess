"""T0446 error-envelope reference contract and hostile-type battery."""
from __future__ import annotations

# ruff: noqa: E501  (explicit source-mutant rows)
import ast
import copy
import inspect
import re
from pathlib import Path

import pytest
import yaml

from tests.test_t0419_openapi_contract import derive as strict_source_derivation
from tools.control_plane_errors_contract_lint import CONTRACT, lint
from tools.openapi_contract_lint import CHESS_TOKENS
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text())
TOKENS = CHESS_TOKENS


class ErrorsError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.failure_class = "malformed_error_result"
        self.code = "malformed_request"
        self.retryable = False


def _fail(message):
    raise ErrorsError(message)


def _source(source):
    bad = False
    try:
        strict_source_derivation(source)
        enum = source["contract"]["transport"]["errors"]["closed_enum"]
    except Exception:
        bad = True
    if bad:
        _fail("source")
    return enum


def _text(text):
    return type(text) is str and not any("\ud800" <= ch <= "\udfff" for ch in text)


def _safe_key(key, declared=False):
    return type(key) is str and _text(key) and not TOKENS.intersection(re.split(r"[_-]", key.lower())) and (declared or key.lower() not in {"key_material", "password_hash_client", "token", "secret"})


def _json_safe(value, depth=0, allowed=frozenset()):
    if depth > 8:
        return False
    if value is None or type(value) in (bool, int):
        return True
    if type(value) is str:
        return _text(value)
    if type(value) is float:
        return value == value and abs(value) != float("inf")
    if type(value) is list:
        return len(value) <= 256 and all(_json_safe(v, depth + 1) for v in value)
    if type(value) is dict:
        return len(value) <= 256 and all(_safe_key(k, k in allowed) and _json_safe(v, depth + 1) for k, v in value.items())
    return False


def classify(source, status, payload, operation=None):
    """Reference only: status class controls error signalling, not key presence."""
    enum = _source(source)
    if type(status) is not int or not (200 <= status <= 299 or 400 <= status <= 599):
        _fail("status")
    declared = frozenset()
    if operation is not None:
        if type(operation) is not str:
            _fail("operation")
        try:
            area, name = operation.split(".")
            declared = frozenset(source["areas"][area]["ops"][name]["response"]["fields"]) if status < 300 else frozenset()
        except Exception:
            _fail("operation")
    if type(payload) is not dict or not _json_safe(payload, allowed=declared):
        _fail("payload")
    if status < 300:
        return {"kind": "success"}
    error = payload.get("error")
    if type(error) is not dict:
        _fail("error object")
    for field in ("code", "message", "retryable"):
        if field not in error:
            _fail("missing field")
    code = error["code"]
    if type(code) is not str or code not in enum or not _text(error["message"]) or type(error["retryable"]) is not bool:
        _fail("error field")
    if any(k in error["message"].lower() for k in ("key_material", "password_hash_client")):
        _fail("secret in message")
    return {"kind": "error", "code": code, "message": error["message"], "retryable": error["retryable"]}


def envelope(code="not_found", message="missing", retryable=False):
    return {"error": {"code": code, "message": message, "retryable": retryable}}


@pytest.mark.parametrize("status", [200, 201, 299])
def test_success_status_ignores_error_field_as_signal(status):
    body = {"error": {"code": "not_found", "message": "data", "retryable": False}}
    before = copy.deepcopy(body)
    assert classify(SOURCE, status, body) == {"kind": "success"}
    assert body == before


@pytest.mark.parametrize("status", [400, 401, 429, 500, 599])
def test_error_class_and_additive_fields(status):
    body = envelope()
    body["trace_id"] = "trace-1"
    body["error"]["support_id"] = "s-1"
    before = copy.deepcopy(body)
    assert classify(SOURCE, status, body) == {"kind": "error", "code": "not_found", "message": "missing", "retryable": False}
    assert body == before


@pytest.mark.parametrize("status", [0, 100, 199, 300, 399, 600, True, 400.0])
def test_unsupported_status_is_typed(status):
    with pytest.raises(ErrorsError) as e:
        classify(SOURCE, status, envelope())
    assert e.value.code == "malformed_request" and e.value.__cause__ is None and e.value.__context__ is None


@pytest.mark.parametrize("change", [
    lambda d: d.pop("error"),
    lambda d: d.update(error=[]),
    lambda d: d["error"].pop("code"),
    lambda d: d["error"].update(code="outside_enum"),
    lambda d: d["error"].update(code=1),
    lambda d: d["error"].update(message=True),
    lambda d: d["error"].update(message="\ud800"),
    lambda d: d["error"].update(retryable=1),
    lambda d: d.update({1: "bad key"}),
    lambda d: d["error"].update(key_material="secret"),
    lambda d: d["error"].update(message="key_material = secret"),
    lambda d: d.update(analysis="private"),
    lambda d: d.update(extra=object()),
    lambda d: d.update(extra=float("nan")),
])
def test_malformed_error_rejects_without_mutation(change):
    source = copy.deepcopy(SOURCE)
    body = envelope()
    change(body)
    before = copy.deepcopy((source, body))
    with pytest.raises(ErrorsError) as e:
        classify(source, 400, body)
    assert e.value.failure_class == "malformed_error_result" and e.value.__context__ is None
    assert source == before[0]
    if "extra" not in body or type(body["extra"]) not in (object, float):
        assert body == before[1]


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version=True),
    lambda d: d["contract"]["transport"]["errors"].update(closed_enum=[object()]),
    lambda d: d["contract"]["transport"]["errors"]["shape"]["error"]["fields"].update({1: None}),
])
def test_hostile_source_rejects_typed(change):
    source = copy.deepcopy(SOURCE)
    change(source)
    with pytest.raises(ErrorsError) as e:
        classify(source, 400, envelope())
    assert e.value.code == "malformed_request" and e.value.__context__ is None


def test_detached_result_and_fresh_error():
    body = envelope()
    result = classify(SOURCE, 404, body)
    body["error"]["message"] = "changed"
    assert result["message"] == "missing"
    errors = []
    for _ in range(2):
        with pytest.raises(ErrorsError) as e:
            classify(SOURCE, 300, envelope())
        errors.append(e.value)
    assert errors[0] is not errors[1]


def test_lint_and_mutated_contract(tmp_path):
    lint()
    doc = yaml.safe_load(CONTRACT.read_text())
    doc["contract"]["result"]["success"] = "error-key-signals-failure"
    path = tmp_path / "mutant.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


REFERENCE_MUTANTS = {
    "error-key-controls-success": ("if status < 300:", "if status < 300 and \"error\" not in payload:", "success"),
    "error-code-outside-enum": ("code not in enum", "False", "outside_enum"),
    "error-bool-as-integer": ("type(error[\"retryable\"]) is not bool", "type(error[\"retryable\"]) not in (bool, int)", "bad_bool"),
    "missing-error-accepted": ("if type(error) is not dict:\n        _fail(\"error object\")", "if type(error) is not dict:\n        error = {\"code\": \"not_found\", \"message\": \"missing\", \"retryable\": False}", "missing"),
    "allow-secret-key": ("key.lower() not in {\"key_material\", \"password_hash_client\", \"token\", \"secret\"}", "True", "secret"),
    "allow-chess-content": ("not TOKENS.intersection(re.split(r\"[_-]\", key.lower()))", "True", "chess"),
    "status-float-accepted": ("type(status) is not int", "type(status) not in (int, float)", "status_float"),
    "ignore-utf8": ("return type(text) is str and not any(\"\\ud800\" <= ch <= \"\\udfff\" for ch in text)", "return type(text) is str", "surrogate"),
    "max-status-off-by-one": ("status <= 599", "status <= 600", "status_600"),
    "strict-source-removed": ("strict_source_derivation(source)", "None", "source"),
    "declared-secret-exemption-removed": ("_safe_key(k, k in allowed)", "_safe_key(k)", "declared_success"),
    "error-token-exempted": ("declared = frozenset(source[\"areas\"][area][\"ops\"][name][\"response\"][\"fields\"]) if status < 300 else frozenset()", "declared = frozenset(source[\"areas\"][area][\"ops\"][name][\"response\"][\"fields\"]) if status < 300 else frozenset({\"token\"})", "error_token"),
    "depth-nine-accepted": ("depth > 8", "depth > 9", "depth"),
    "depth-eight-refused": ("depth > 8", "depth > 7", "depth"),
    "dict-257-accepted": ("len(value) <= 256 and all(_safe_key", "len(value) <= 257 and all(_safe_key", "dict_size"),
    "list-257-accepted": ("len(value) <= 256 and all(_json_safe", "len(value) <= 257 and all(_json_safe", "list_size"),
    "infinity-accepted": ('and abs(value) != float("inf")', "", "infinity"),
    "case-key-accepted": ("key.lower() not in", "key not in", "case_key"),
    "case-message-accepted": ('error["message"].lower()', 'error["message"]', "case_message"),
    "hyphenated-chess-key-accepted": ('r"[_-]"', 'r"[_]"', "hyphen"),
    "int-subclass-accepted": ("type(value) in (bool, int)", "isinstance(value, int)", "int_subclass"),
    "success-payload-validation-skipped": ("if type(payload) is not dict or not _json_safe(payload, allowed=declared):", "if status >= 400 and (type(payload) is not dict or not _json_safe(payload, allowed=declared)):", "hostile_2xx"),
}


def _mutant(name):
    before, after, _ = REFERENCE_MUTANTS[name]
    source = "\n".join(inspect.getsource(f) for f in (_text, _safe_key, _json_safe, _source, classify))
    assert source.count(before) == 1, name
    scope = dict(globals())
    exec(compile(ast.parse(source.replace(before, after, 1)), f"<reference-mutant:{name}>", "exec"), scope)
    return scope["classify"]


@pytest.mark.parametrize("name", REFERENCE_MUTANTS)
def test_reference_mutant_red(name):
    target = _mutant(name)
    source = copy.deepcopy(SOURCE)
    body = envelope()
    status = 400
    if name == "error-key-controls-success":
        status = 200
        assert classify(source, status, body) == {"kind": "success"}
        assert target(source, status, body) != {"kind": "success"}
        return
    if name == "error-code-outside-enum":
        body["error"]["code"] = "outside_enum"
    elif name == "error-bool-as-integer":
        body["error"]["retryable"] = 1
    elif name == "missing-error-accepted":
        body.pop("error")
    elif name == "allow-secret-key":
        body["error"]["key_material"] = "secret"
    elif name == "allow-chess-content":
        body["error"]["analysis"] = "private"
    elif name == "status-float-accepted":
        status = 400.0
    elif name == "ignore-utf8":
        body["error"]["message"] = "\ud800"
    elif name == "max-status-off-by-one":
        status = 600
    elif name == "declared-secret-exemption-removed":
        body = {"token": "tok-1"}
        assert classify(source, 200, body, "identity.login") == {"kind": "success"}
        with pytest.raises(ErrorsError):
            target(source, 200, body, "identity.login")
        return
    elif name == "error-token-exempted":
        body["token"] = "tok-1"
        with pytest.raises(ErrorsError):
            classify(source, 400, body, "identity.login")
        assert target(source, 400, body, "identity.login")["kind"] == "error"
        return
    elif name in ("depth-nine-accepted", "depth-eight-refused"):
        body = {"x": 1}
        for _ in range(7 if name == "depth-eight-refused" else 8):
            body = {"x": body}
        if name == "depth-eight-refused":
            assert classify(source, 200, body) == {"kind": "success"}
            with pytest.raises(ErrorsError):
                target(source, 200, body)
        else:
            with pytest.raises(ErrorsError):
                classify(source, 200, body)
            assert target(source, 200, body) == {"kind": "success"}
        return
    elif name in ("dict-257-accepted", "list-257-accepted"):
        body["extra"] = {f"x{i}": i for i in range(257)} if name == "dict-257-accepted" else list(range(257))
    elif name == "infinity-accepted":
        body["extra"] = float("inf")
    elif name == "case-key-accepted":
        body["Key_Material"] = "secret"
    elif name == "case-message-accepted":
        body["error"]["message"] = "KEY_MATERIAL=x"
    elif name == "hyphenated-chess-key-accepted":
        body["game-id"] = "private"
    elif name == "int-subclass-accepted":
        body["extra"] = type("IntChild", (int,), {})(1)
    elif name == "success-payload-validation-skipped":
        body = {"extra": float("nan")}
        with pytest.raises(ErrorsError):
            classify(source, 200, body)
        assert target(source, 200, body) == {"kind": "success"}
        return
    else:
        source["contract"]["transport"]["errors"]["shape"]["error"]["fields"]["code"]["required"] = False
    with pytest.raises(ErrorsError):
        classify(source, status, body)
    assert target(source, status, body) == {"kind": "error", "code": body.get("error", {}).get("code", "not_found"), "message": body.get("error", {}).get("message", "missing"), "retryable": body.get("error", {}).get("retryable", False)}


def test_every_declared_operation_success_example_is_allowed():
    for area, a in SOURCE["areas"].items():
        for name, op in a["ops"].items():
            # The source's examples give one valid value for every required leaf.
            def example(fields):
                out = {}
                for key, field in fields.items():
                    if field["required"]:
                        out[key] = example(field["fields"]) if field["type"] == "object" else field["example"]
                return out
            body = example(op["response"]["fields"])
            assert classify(SOURCE, 200, body, f"{area}.{name}") == {"kind": "success"}


@pytest.mark.parametrize("status,operation", [(400, "identity.register"), (200, "entitlements.get")])
def test_undeclared_token_is_refused(status, operation):
    body = envelope() if status >= 400 else {}
    body["token"] = "tok-1"
    with pytest.raises(ErrorsError):
        classify(SOURCE, status, body, operation)


@pytest.mark.parametrize("body", [{"extra": float("nan")}, {1: "bad"}])
def test_hostile_success_body_refused(body):
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, body, "entitlements.get")


@pytest.mark.parametrize("factory,allowed", [
    (lambda: {"extra": float("inf")}, False),
    (lambda: {"extra": -float("inf")}, False),
    (lambda: {"Key_Material": "secret"}, False),
    (lambda: {"TOKEN": "secret"}, False),
    (lambda: {"game-id": "private"}, False),
    (lambda: {"extra": type("IntChild", (int,), {})(1)}, False),
])
def test_refused_extra_shapes(factory, allowed):
    body = envelope()
    body.update(factory())
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, body)


def test_uppercase_secret_in_error_message_refused():
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, envelope(message="KEY_MATERIAL=x"))


@pytest.mark.parametrize("kind", ["dict", "list"])
def test_256_extra_bound_accepted_and_257_refused(kind):
    if kind == "dict":
        good = {f"x{i}": i for i in range(256)}
        bad = {f"x{i}": i for i in range(257)}
    else:
        good = list(range(256))
        bad = list(range(257))
    body = envelope()
    body["extra"] = good
    assert classify(SOURCE, 400, body)["kind"] == "error"
    body["extra"] = bad
    with pytest.raises(ErrorsError):
        classify(SOURCE, 400, body)


def test_depth_eight_accepted_nine_refused():
    # Top-level payload depth 0; last scalar at depth 8.
    body = {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": 1}}}}}}}}
    assert _json_safe(body)
    deeper = {"x": body}
    assert not _json_safe(deeper)
    success = {"extra": 0}
    cursor = success
    for _ in range(7):
        cursor["extra"] = {"x": 0}
        cursor = cursor["extra"]
    assert classify(SOURCE, 200, success)["kind"] == "success"
    cursor["x"] = {"x": 0}
    with pytest.raises(ErrorsError):
        classify(SOURCE, 200, success)
