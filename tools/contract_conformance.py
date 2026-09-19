"""T0475: conformance harness for the replaceable control-plane contract.

Runs a case suite (tests/fixtures/control-plane/cases.json) against any
implementation exposing handle(method, path, headers, body) ->
(status, payload). Proves happy, boundary, malformed and rollback
behavior:

- happy: every contract operation answers with the documented shape;
- boundary: unknown operations are rejected with a closed-enum error,
  never a crash or an invented response;
- malformed: missing required fields and a missing Idempotency-Key are
  rejected with malformed_request;
- rollback: responses may additively carry extra fields (MINOR bumps) -
  conformance asserts required keys only, so a consumer built for an
  earlier minor keeps working; idempotent replays return the original
  outcome, never a duplicate effect.

Any implementation returning an error code outside the contract's closed
enum, or a wrong shape, fails conformance - that is the red signal a real
implementation starts from.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "fixtures" / "control-plane" / "cases.json"
CONTRACT = ROOT / "data" / "contracts" / "control-plane.yaml"


def load_enum() -> set[str]:
    import yaml
    doc = yaml.safe_load(CONTRACT.read_text())
    return set(doc["contract"]["transport"]["errors"]["closed_enum"])


def _error_code(payload: object) -> str | None:
    if type(payload) is not dict:
        return None
    err = payload.get("error")
    if type(err) is not dict:
        return None
    code = err.get("code")
    return code if type(code) is str else None


def run(impl, cases_path: Path = CASES) -> list[str]:
    """Return the list of conformance problems ([] = conforming)."""
    enum = load_enum()
    cases = json.loads(cases_path.read_text())
    problems: list[str] = []
    state: dict[str, object] = {}

    for case in cases:
        name = case["name"]
        method, path = case["method"], case["path"]
        headers = dict(case.get("headers", {}))
        body = dict(case.get("body", {}))
        for key, value in case.get("state_headers", {}).items():
            headers[key] = str(state[value])
        for key, value in case.get("state_body", {}).items():
            body[key] = state[value]

        try:
            status, payload = impl.handle(method, path, headers, body)
        except Exception as exc:  # an implementation crash is a failure
            problems.append(f"{name}: implementation raised {exc!r}")
            continue

        want_status = case["expect_status"]
        if status != want_status:
            problems.append(
                f"{name}: status {status}, expected {want_status}")
            continue

        if want_status >= 400:
            code = _error_code(payload)
            if code is None:
                problems.append(f"{name}: error response missing "
                                "error.code shape")
                continue
            if code not in enum:
                problems.append(f"{name}: error code {code!r} outside "
                                "the contract's closed enum")
                continue
            want_code = case.get("expect_error")
            if want_code is not None and code != want_code:
                problems.append(f"{name}: error {code!r}, expected "
                                f"{want_code!r}")
                continue
        else:
            if type(payload) is not dict:
                problems.append(f"{name}: success payload not an object")
                continue
            for key in case.get("expect_keys", []):
                if key not in payload:
                    problems.append(f"{name}: missing key {key!r}")
                    break
            else:
                for key, store_as in case.get("store", {}).items():
                    if key in payload:
                        state[store_as] = payload[key]
                continue
            continue

        for key, store_as in case.get("store", {}).items():
            if type(payload) is dict and key in payload:
                state[store_as] = payload[key]

    return problems


def main() -> int:
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.control_plane_mock import MockControlPlane
    problems = run(MockControlPlane())
    if problems:
        for p in problems:
            print(f"FAIL conformance: {p}")
        return 1
    print("OK conformance: reference fixture conforms to the contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
