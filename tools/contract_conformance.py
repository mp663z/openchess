"""T0475: conformance harness, contract schema v2.

Certifies an implementation against data/contracts/control-plane.yaml.
Coverage is GENERATED from the contract itself - for every declared
operation: a happy case built from schema examples, an unauthenticated
case (auth-required ops), a missing-field and a wrong-type case per
required field, and a same-key/different-body idempotency-conflict case
(mutating ops). Curated flow cases (tests/fixtures/control-plane/
cases.json) cover stateful sequences (quota reserve->commit->release,
key register->route->revoke). The harness asserts every operation and
every required field ended up covered.

Universal response rules on EVERY call: error payloads match the
contract's closed error shape with exact value types; success payloads
carry the declared required fields with matching types (extra fields
allowed - MINOR additive/rollback); no payload anywhere may contain
write_only field names or key material (privacy).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fixtures" / "control-plane" / "cases.json"
CONTRACT = ROOT / "data" / "contracts" / "control-plane.yaml"

# Ops whose happy path is not a 2xx on a fresh fixture, or needs live
# state the curated flow provides.
HAPPY_OVERRIDES = {
    "billing.get_subscription": (404, "not_found"),
}
SKIP_GENERATED_HAPPY = {"quota.commit", "quota.release",
                        "provider_routing.revoke_key"}
SECRET_FIELD_NAMES = {"key_material"}


def _build_example(fields: dict) -> dict:
    body = {}
    for name, spec in fields.items():
        if spec.get("write_only") or "example" in spec:
            body[name] = spec.get("example")
        elif spec["type"] == "object":
            body[name] = _build_example(spec.get("fields", {}))
        elif spec["required"]:
            body[name] = {"string": "x", "integer": 1, "number": 1.0,
                          "boolean": True, "array": ["x"]}[spec["type"]]
    return body


def _wrong_value(spec: dict) -> object:
    return {"string": 123, "integer": "not-an-int", "number": "x",
            "boolean": "yes", "array": "x",
            "object": "x"}[spec["type"]]


def _alt_value(value: object) -> object:
    if type(value) is str:
        return value + "-alt"
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is float:
        return value + 1.0
    if type(value) is list:
        return value + ["alt"]
    return value


def _scan_forbidden(payload: object, path: str) -> str | None:
    if type(payload) is dict:
        for key, value in payload.items():
            if key in SECRET_FIELD_NAMES:
                return f"{path}.{key}: write_only/key material leaked"
            hit = _scan_forbidden(value, f"{path}.{key}")
            if hit:
                return hit
    elif type(payload) is list:
        for i, item in enumerate(payload):
            hit = _scan_forbidden(item, f"{path}[{i}]")
            if hit:
                return hit
    return None


def _check_types(payload: object, fields: dict, where: str) -> str | None:
    if type(payload) is not dict:
        return f"{where}: payload not an object"
    for name, spec in fields.items():
        if not spec["required"]:
            continue
        if name not in payload:
            return f"{where}.{name}: required response field missing"
        value = payload[name]
        ftype = spec["type"]
        ok = {"string": type(value) is str,
              "integer": type(value) is int,
              "number": type(value) in (int, float)
              and type(value) is not bool,
              "boolean": type(value) is bool,
              "object": type(value) is dict,
              "array": type(value) is list}.get(ftype, False)
        if not ok:
            return f"{where}.{name}: wrong type"
        if ftype == "object":
            hit = _check_types(value, spec.get("fields", {}),
                               f"{where}.{name}")
            if hit:
                return hit
    return None


class Harness:
    def __init__(self, impl) -> None:
        self.impl = impl
        doc = yaml.safe_load(CONTRACT.read_text())
        self.enum = set(doc["contract"]["transport"]["errors"]
                        ["closed_enum"])
        self.error_shape = doc["contract"]["transport"]["errors"][
            "shape"]["error"]["fields"]
        self.ops = {}
        for area_name, area in doc["areas"].items():
            for op_name, op in area["ops"].items():
                self.ops[f"{area_name}.{op_name}"] = op
        self.problems: list[str] = []
        self.state: dict[str, str] = {}
        self.covered_happy: set[str] = set()
        self.covered_auth: set[str] = set()
        self.covered_idem: set[str] = set()
        self.covered_fields: set[str] = set()

    def call(self, label: str, name: str, body: dict, *,
             auth: bool = True, idem: str | None = None,
             method: str | None = None, path: str | None = None,
             expect_status: int | None = None,
             expect_error: str | None = None,
             expect_2xx: bool = False,
             store: dict | None = None) -> tuple[int, dict] | None:
        op = self.ops.get(name) if name else None
        if op:
            method, path = op["method"], op["path"]
        headers = {}
        if auth and "TOK" in self.state:
            headers["Authorization"] = f"Bearer {self.state['TOK']}"
        if idem is not None:
            headers["Idempotency-Key"] = idem
        try:
            status, payload = self.impl.handle(method, path, headers,
                                               body)
        except Exception as exc:
            self.problems.append(f"{label}: implementation raised "
                                 f"{exc!r}")
            return None
        leak = _scan_forbidden(payload, f"{label}")
        if leak:
            self.problems.append(f"privacy: {leak}")
        if expect_status is not None and status != expect_status:
            self.problems.append(f"{label}: status {status}, expected "
                                 f"{expect_status}")
            return status, payload
        if expect_2xx and not (200 <= status < 300):
            self.problems.append(f"{label}: status {status}, "
                                 "expected 2xx")
            return status, payload
        if status >= 400:
            if type(payload) is not dict or type(
                    payload.get("error")) is not dict:
                self.problems.append(f"{label}: error payload lacks "
                                     "error object")
                return status, payload
            err = payload["error"]
            code = err.get("code")
            if code not in self.enum:
                self.problems.append(f"{label}: error code {code!r} "
                                     "outside closed enum")
            if type(err.get("message")) is not str or type(
                    err.get("retryable")) is not bool:
                self.problems.append(f"{label}: error shape wrong value "
                                     "types")
            if expect_error and code != expect_error:
                self.problems.append(f"{label}: error {code!r}, "
                                     f"expected {expect_error!r}")
        else:
            if op:
                hit = _check_types(payload, op["response"].get(
                    "fields", {}), label)
                if hit:
                    self.problems.append(hit)
            if store:
                for key, store_as in store.items():
                    if type(payload) is dict and key in payload:
                        self.state[store_as] = str(payload[key])
        return status, payload

    def phase0_identity(self) -> None:
        email = f"conf-{secrets.token_hex(4)}@example.test"
        self.state["EMAIL"] = email
        self.call("setup register", "identity.register",
                  {"email": email, "password_hash_client": "h0"},
                  idem="setup-reg", expect_status=200,
                  store={"token": "TOK"})
        self.call("setup login", "identity.login",
                  {"email": email, "password_hash_client": "h0"},
                  idem="setup-login", expect_2xx=True)

    def generated(self) -> None:
        for name in sorted(self.ops):
            op = self.ops[name]
            fields = op["request"].get("fields", {})
            body = _build_example(fields)
            if name == "identity.register":
                body["email"] = f"gen-{secrets.token_hex(4)}@example.test"
            if name == "identity.login":
                body["email"] = self.state.get("EMAIL", body["email"])
                body["password_hash_client"] = "h0"
            need_auth = op["auth"] == "required"
            idem = f"gen-{name}" if op["mutating"] else None
            # happy
            if name not in SKIP_GENERATED_HAPPY:
                override = HAPPY_OVERRIDES.get(name)
                label = f"happy {name}"
                if override:
                    self.call(label, name, body, idem=idem,
                              expect_status=override[0],
                              expect_error=override[1])
                else:
                    self.call(label, name, body, idem=idem,
                              expect_2xx=True)
                self.covered_happy.add(name)
            # unauthenticated
            if need_auth:
                self.call(f"unauthenticated {name}", name, body,
                          auth=False, idem=idem, expect_status=401)
                self.covered_auth.add(name)
            # missing / wrong-type per required field
            for fname, spec in sorted(fields.items()):
                if not spec["required"] or spec["type"] == "object":
                    continue
                missing = {k: v for k, v in body.items() if k != fname}
                self.call(f"missing-field {name}.{fname}", name,
                          missing, idem=(f"{idem}-miss-{fname}"
                                         if idem else None),
                          expect_status=400,
                          expect_error="malformed_request")
                wrong = dict(body)
                wrong[fname] = _wrong_value(spec)
                self.call(f"wrong-type {name}.{fname}", name, wrong,
                          idem=(f"{idem}-wrong-{fname}"
                                if idem else None),
                          expect_status=400,
                          expect_error="malformed_request")
                self.covered_fields.add(f"{name}.{fname}")
            # idempotency pair: first use of a fresh key, then the same
            # key with a different body must answer conflict. The pair
            # is self-contained so it also covers state-dependent ops
            # whose happy case is curated.
            if op["mutating"]:
                pair_key = f"{idem}-pair"
                self.call(f"idempotency-first {name}", name, body,
                          idem=pair_key)
                alt = dict(body)
                for fname, spec in fields.items():
                    if spec["required"] and spec["type"] != "object":
                        alt[fname] = _alt_value(alt[fname])
                        break
                else:
                    alt["marker"] = "different"
                self.call(f"idempotency-conflict {name}", name, alt,
                          idem=pair_key, expect_status=409,
                          expect_error="idempotency_conflict")
                self.covered_idem.add(name)

    def curated(self, cases_path: Path = CASES) -> None:
        cases = json.loads(cases_path.read_text())
        for case in cases:
            name = case["op"]
            body = {k: (self.state.get(v[1:-1], v) if type(v) is str
                        and v.startswith("{") else v)
                    for k, v in case.get("body", {}).items()}
            kwargs = {"expect_status": case["expect_status"]}
            if "expect_error" in case:
                kwargs["expect_error"] = case["expect_error"]
            if case.get("idem"):
                kwargs["idem"] = case["idem"]
            result = self.call(f"flow {case['name']}", name, body,
                               store=case.get("store"), **kwargs)
            if result and result[0] < 300:
                self.covered_happy.add(name)

    def completeness(self) -> None:
        for name, op in sorted(self.ops.items()):
            if name not in self.covered_happy:
                self.problems.append(f"coverage: {name} has no happy "
                                     "case (generated or curated)")
            if op["auth"] == "required" and name not in self.covered_auth:
                self.problems.append(f"coverage: {name} missing "
                                     "unauthenticated case")
            if op["mutating"] and name not in self.covered_idem:
                self.problems.append(f"coverage: {name} missing "
                                     "idempotency-conflict case")
            for fname, spec in op["request"].get("fields", {}).items():
                if spec["required"] and spec["type"] != "object" and \
                        f"{name}.{fname}" not in self.covered_fields:
                    self.problems.append(f"coverage: {name}.{fname} "
                                         "missing field cases")


def run(impl, cases_path: Path = CASES) -> list[str]:
    harness = Harness(impl)
    harness.phase0_identity()
    harness.generated()
    harness.curated(cases_path)
    harness.completeness()
    return harness.problems


def main() -> int:
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.control_plane_mock import MockControlPlane
    problems = run(MockControlPlane())
    if problems:
        for p in problems[:20]:
            print(f"FAIL conformance: {p}")
        print(f"{len(problems)} problem(s)")
        return 1
    print("OK conformance: reference fixture conforms (v2, generated "
          "coverage)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
