"""T0475: conformance harness, contract schema v4.

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

# Ops whose happy path consumes the caller (the contract's declared
# self-destructive operations): logout revokes the token that made the
# call, delete_account removes the account. Their happy cases run
# against a throwaway account so the primary one survives. Their
# idempotency replay IS reachable: the contract's replay-before-auth
# rule scopes the retry by the presented credential, never its
# validity, so the generated pair and lifecycle replay cases run for
# these ops exactly as for any other mutating op.
def _self_destructive_ops() -> frozenset:
    doc = yaml.safe_load(CONTRACT.read_text())
    return frozenset(doc["contract"]["transport"][
        "self_destructive_operations"])


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


_TYPE_OK = {
    "string": lambda v: type(v) is str,
    "integer": lambda v: type(v) is int,
    "number": lambda v: type(v) in (int, float) and type(v) is not bool,
    "boolean": lambda v: type(v) is bool,
    "object": lambda v: type(v) is dict,
    "array": lambda v: type(v) is list,
}


def _check_types(payload: object, fields: dict, where: str) -> str | None:
    if type(payload) is not dict:
        return f"{where}: payload not an object"
    for name, spec in fields.items():
        if name not in payload:
            if spec["required"]:
                return (f"{where}.{name}: required response field "
                        "missing")
            continue
        # optional fields are type-checked whenever present
        value = payload[name]
        ftype = spec["type"]
        if not _TYPE_OK.get(ftype, lambda v: False)(value):
            return f"{where}.{name}: wrong type"
        if ftype == "object":
            hit = _check_types(value, spec.get("fields", {}),
                               f"{where}.{name}")
            if hit:
                return hit
        if ftype == "array":
            item_type = spec.get("items")
            if type(item_type) is str:
                for i, item in enumerate(value):
                    if not _TYPE_OK.get(item_type,
                                        lambda v: True)(item):
                        return (f"{where}.{name}[{i}]: wrong item "
                                "type")
    return None


class Harness:
    def __init__(self, impl, fixture=None) -> None:
        self.impl = impl
        # Fixture adapter, supplied SEPARATELY from the implementation
        # interface: an object exposing fixture_expire_token(token).
        # Implementations are certified through handle() alone; the
        # adapter only manipulates fixture state for the expiry case.
        self.fixture = fixture
        doc = yaml.safe_load(CONTRACT.read_text())
        self.self_destructive = frozenset(
            doc["contract"]["transport"]["self_destructive_operations"])
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
             token: str | None = None,
             method: str | None = None, path: str | None = None,
             expect_status: int | None = None,
             expect_error: str | None = None,
             expect_2xx: bool = False,
             store: dict | None = None) -> tuple[int, dict] | None:
        op = self.ops.get(name) if name else None
        if op:
            method, path = op["method"], op["path"]
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        elif auth and "TOK" in self.state:
            headers["Authorization"] = f"Bearer {self.state['TOK']}"
        if idem is not None:
            headers["Idempotency-Key"] = idem
        try:
            result = self.impl.handle(method, path, headers, body)
        except BaseException as exc:
            # SystemExit(0), KeyboardInterrupt: never a pass, never an
            # abort - a raised implementation is a failed implementation.
            self.problems.append(f"{label}: implementation raised "
                                 f"{type(exc).__name__}")
            return None
        # Fail closed on malformed returns; validate types BEFORE any
        # membership test or comparison so a hostile return can never
        # crash the harness itself.
        if type(result) is not tuple or len(result) != 2:
            self.problems.append(f"{label}: malformed return type "
                                 f"{type(result).__name__} (need exact "
                                 "(status, payload) 2-tuple)")
            return None
        status, payload = result
        if type(status) is not int or not (100 <= status <= 599):
            self.problems.append(f"{label}: malformed status type "
                                 f"{type(status).__name__} (need exact "
                                 "int in 100..599)")
            return None
        if type(payload) is not dict:
            self.problems.append(f"{label}: payload type "
                                 f"{type(payload).__name__} is not an "
                                 "object")
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
            if type(payload.get("error")) is not dict:
                self.problems.append(f"{label}: error payload lacks "
                                     "error object")
                return status, payload
            err = payload["error"]
            code = err.get("code")
            if type(code) is not str:
                self.problems.append(f"{label}: error code type "
                                     f"{type(code).__name__} is not a "
                                     "string")
            elif code not in self.enum:
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
                    if key in payload:
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

    def phase_lifecycle(self) -> None:
        """Real identity lifecycle: expired tokens answer auth_expired,
        logout revokes, delete removes, refresh binds to the caller."""
        email = f"life-{secrets.token_hex(4)}@example.test"
        self.call("lifecycle register", "identity.register",
                  {"email": email, "password_hash_client": "h0"},
                  idem=f"life-reg-{secrets.token_hex(4)}",
                  expect_status=200, store={"token": "LTOK"})
        # refresh must mint for the AUTHENTICATED account: the refreshed
        # token must be able to delete THIS account.
        self.call("lifecycle refresh", "identity.refresh", {},
                  token=self.state.get("LTOK"), idem="life-refresh",
                  expect_status=200, store={"token": "RTOK"})
        self.covered_happy.add("identity.refresh")
        self.call("lifecycle delete via refreshed token",
                  "identity.delete_account", {"confirm": "DELETE"},
                  token=self.state.get("RTOK"), idem="life-delete",
                  expect_status=200)
        self.covered_happy.add("identity.delete_account")
        self.call("delete same-body replay",
                  "identity.delete_account", {"confirm": "DELETE"},
                  token=self.state.get("RTOK"), idem="life-delete",
                  expect_status=200)
        self.call("delete same-key conflict",
                  "identity.delete_account", {"confirm": "YES"},
                  token=self.state.get("RTOK"), idem="life-delete",
                  expect_status=409,
                  expect_error="idempotency_conflict")
        self.call("deleted token rejected", "entitlements.get", {},
                  token=self.state.get("RTOK"), expect_status=401,
                  expect_error="auth_invalid")
        self.call("deleted account cannot login", "identity.login",
                  {"email": email, "password_hash_client": "h0"},
                  idem="life-login-after-delete", expect_status=401,
                  expect_error="auth_invalid")
        # logout must revoke the token that made the call
        email2 = f"life2-{secrets.token_hex(4)}@example.test"
        self.call("lifecycle register 2", "identity.register",
                  {"email": email2, "password_hash_client": "h0"},
                  idem=f"life-reg2-{secrets.token_hex(4)}",
                  expect_status=200, store={"token": "LTOK2"})
        self.call("lifecycle logout", "identity.logout", {},
                  token=self.state.get("LTOK2"), idem="life-logout",
                  expect_status=200)
        self.covered_happy.add("identity.logout")
        # replay-before-auth: same key + same body replays the recorded
        # outcome even though the credential is now revoked
        self.call("logout same-body replay", "identity.logout", {},
                  token=self.state.get("LTOK2"), idem="life-logout",
                  expect_status=200)
        self.call("logout same-key conflict", "identity.logout",
                  {"marker": "different"},
                  token=self.state.get("LTOK2"), idem="life-logout",
                  expect_status=409,
                  expect_error="idempotency_conflict")
        self.call("logged-out token rejected", "entitlements.get", {},
                  token=self.state.get("LTOK2"), expect_status=401,
                  expect_error="auth_invalid")
        # an expired token must answer auth_expired, not auth_invalid
        hook = (getattr(self.fixture, "fixture_expire_token", None)
                if self.fixture is not None else None)
        if not callable(hook):
            self.problems.append("lifecycle: no fixture adapter with "
                                 "fixture_expire_token supplied; the "
                                 "token-expiry case cannot run")
            return
        self.call("lifecycle login 2", "identity.login",
                  {"email": email2, "password_hash_client": "h0"},
                  idem="life-login2", expect_status=200,
                  store={"token": "LTOK3"})
        token3 = self.state.get("LTOK3")
        if token3 is not None:
            try:
                hook(token3)
            except BaseException as exc:
                self.problems.append("lifecycle: _test_expire_token "
                                     f"raised {type(exc).__name__}")
                return
            self.call("expired token", "entitlements.get", {},
                      token=token3, expect_status=401,
                      expect_error="auth_expired")

    def _throwaway(self, name: str, purpose: str) -> str | None:
        throw_email = (f"throw-{name.replace('.', '-')}-{purpose}-"
                       f"{secrets.token_hex(4)}@example.test")
        key = f"THROWTOK-{name}-{purpose}"
        self.call(f"throwaway register {name} {purpose}",
                  "identity.register",
                  {"email": throw_email, "password_hash_client": "h0"},
                  idem=f"throw-{name}-{purpose}-{secrets.token_hex(4)}",
                  expect_status=200, store={"token": key})
        return self.state.get(key)

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
                call_token = None
                if name in self.self_destructive:
                    call_token = self._throwaway(name, "happy")
                override = HAPPY_OVERRIDES.get(name)
                label = f"happy {name}"
                if override:
                    self.call(label, name, body, idem=idem,
                              token=call_token,
                              expect_status=override[0],
                              expect_error=override[1])
                else:
                    self.call(label, name, body, idem=idem,
                              token=call_token, expect_2xx=True)
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
                pair_token = (self._throwaway(name, "pair")
                              if name in self.self_destructive else None)
                pair_key = f"{idem}-pair"
                self.call(f"idempotency-first {name}", name, body,
                          idem=pair_key, token=pair_token)
                alt = dict(body)
                for fname, spec in fields.items():
                    if spec["required"] and spec["type"] != "object":
                        alt[fname] = _alt_value(alt[fname])
                        break
                else:
                    alt["marker"] = "different"
                self.call(f"idempotency-conflict {name}", name, alt,
                          idem=pair_key, token=pair_token,
                          expect_status=409,
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


def run(impl, cases_path: Path = CASES, fixture=None) -> list[str]:
    harness = Harness(impl, fixture=fixture)
    harness.phase0_identity()
    harness.phase_lifecycle()
    harness.generated()
    harness.curated(cases_path)
    harness.completeness()
    return harness.problems


def main() -> int:
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.control_plane_mock import MockControlPlane
    mock = MockControlPlane()
    problems = run(mock, fixture=mock)
    if problems:
        for p in problems[:20]:
            print(f"FAIL conformance: {p}")
        print(f"{len(problems)} problem(s)")
        return 1
    print("OK conformance: reference fixture conforms (v4, generated "
          "coverage)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
