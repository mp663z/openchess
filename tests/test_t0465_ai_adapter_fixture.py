"""T0465 executable synthetic fixture for the T0464 contract.

No provider adapter, credential flow, network request or payment is shipped here.
The reference is a test-only adapter boundary; T0466 can bind the corpus to
its own red tests. A recorded hosted effect means *attempted* send, not a
promise of delivery, cancellation rollback or billing outcome.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest
import yaml

from tests.test_t0383_byom_contract import ByomError, _payload
from tools.ai_adapter_contract_lint import lint

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/ai_adapter/cases.json"
CASES = json.loads(FIXTURE.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
REQUIRED = {"version", "mode", "capability", "provider_ref", "payload"}
ALLOWED = REQUIRED | {"max_cost_usd"}
FAILURES = set(
    yaml.safe_load((ROOT / CASES["contract"]).read_text())["contract"]["failures"]["classes"]
)


class Refusal(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _refuse(code):
    raise Refusal(code)


def _envelope(request):
    if type(request) is not dict or any(type(k) is not str for k in request):
        _refuse("malformed_request")
    if not request.keys() >= REQUIRED or not request.keys() <= ALLOWED:
        _refuse("malformed_request")
    if type(request["version"]) is not int or request["version"] != 1:
        _refuse("malformed_request")
    if type(request["mode"]) is not str or request["mode"] not in ("local", "hosted_byom"):
        _refuse("malformed_request")
    for field in ("capability", "provider_ref"):
        if type(request[field]) is not str or not request[field]:
            _refuse("malformed_request")
    if "max_cost_usd" in request:
        cap = request["max_cost_usd"]
        if type(cap) not in (float, int) or not math.isfinite(cap) or cap < 0:
            _refuse("malformed_request")


def reference(request, state, effects):
    """A deterministic seam, not a provider implementation or a public API.

    State is read on invocation, not held from request construction. The
    preflight is injected as a finite provider-bounded estimate in test data;
    this reference must never be used to claim that a real provider offers it.
    """
    _envelope(request)
    mode = request["mode"]
    if mode == "hosted_byom":
        try:
            _payload(request["payload"])
        except ByomError as exc:
            _refuse(exc.failure_class)
        if not state.get("cloud", False) or state.get("sensitive", False):
            _refuse("cloud_off")
        if state.get("accepted_terms") != "current":
            _refuse("terms_required")
        cap = request.get("max_cost_usd")
        bounded = state.get("bounded_cost")
        if cap is None or type(bounded) not in (int, float) or not math.isfinite(bounded):
            _refuse("cost_cap_exceeded")
        if bounded < 0 or bounded > cap:
            _refuse("cost_cap_exceeded")
    # Cancellation before dispatch has no effect; after dispatch is explicitly
    # outside this fixture and has no no-charge or rollback guarantee.
    if state.get("cancelled", False):
        _refuse("cancelled")
    effects.append("hosted" if mode == "hosted_byom" else "local")
    if state.get("provider_error", False):
        _refuse("provider_unavailable")
    return {"version": 1, "status": "succeeded", "output": "synthetic text"}


def materialize(row):
    base = next((r for r in CASES["happy"] if r["name"] == row.get("base")), None)
    request = copy.deepcopy(base["request"] if base else row["request"])
    state = copy.deepcopy(base["state"] if base else row["state"])
    for key, value in row.get("request", {}).items() if base else ():
        if value == "DELETE":
            request.pop(key, None)
        else:
            request[key] = value
    state.update(row.get("state", {}) if base else {})
    return request, state


def run_row(row, adapter=reference):
    request, state = materialize(row)
    before = copy.deepcopy((request, state))
    effects = []
    try:
        result = adapter(request, state, effects)
    except Refusal as exc:
        assert exc.code == row["expect"], row["name"]
        assert "SECRET-FIXTURE" not in str(exc)
    else:
        assert row["expect"] == "succeeded", row["name"]
        assert set(result) == {"version", "status", "output"}
        assert result == {"version": 1, "status": "succeeded", "output": "synthetic text"}
    assert effects == row["effects"], row["name"]
    assert (request, state) == before, row["name"]


def test_fixture_schema_and_contract_closure():
    lint()
    assert set(CASES) == {"schema_version", "contract", "source", "section_manifests", *SECTIONS}
    assert type(CASES["schema_version"]) is int and CASES["schema_version"] == 1
    assert CASES["contract"] == "data/contracts/ai_adapter.yaml"
    assert "synthetic" in CASES["source"]
    names = []
    for section in SECTIONS:
        rows = CASES[section]
        assert list(CASES["section_manifests"][section]) == [r["name"] for r in rows]
        assert type(rows) is list and rows, section
        for row in rows:
            required = ({"name", "request", "state", "expect", "effects"}
                        if section == "happy" else {"name", "base", "expect", "effects"})
            assert required <= set(row) <= required | {"request", "state"}, row
            if section != "happy":
                assert row["base"] in {r["name"] for r in CASES["happy"]}
                assert "request" in row or "state" in row
            assert CASES["section_manifests"][section][row["name"]] == hashlib.sha256(
                json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            assert type(row["name"]) is str and row["name"]
            assert row["expect"] == "succeeded" or row["expect"] in FAILURES
            assert row["effects"] in ([], ["local"], ["hosted"])
            names.append(row["name"])
            materialize(row)
    assert len(names) == len(set(names))


@pytest.mark.parametrize("section,row", [(s, r) for s in SECTIONS for r in CASES[s]],
                         ids=[r["name"] for s in SECTIONS for r in CASES[s]])
def test_fixture_row(section, row):
    assert section in SECTIONS
    run_row(row)


def test_precedence_payload_before_privacy_before_cost():
    row = next(r for r in CASES["happy"] if r["name"] == "hosted-opted-in")
    request, state = materialize(row)
    effects = []
    request["payload"]["full-corpus"] = "SECRET-FIXTURE"
    state.update(cloud=False, bounded_cost=None)
    with pytest.raises(Refusal, match="^payload_rejected$"):
        reference(request, state, effects)
    assert not effects
    del request["payload"]["full-corpus"]
    with pytest.raises(Refusal, match="^cloud_off$"):
        reference(request, state, effects)
    assert not effects


def test_fresh_gate_on_same_request_after_state_change():
    row = next(r for r in CASES["happy"] if r["name"] == "hosted-opted-in")
    request, state = materialize(row)
    assert reference(request, state, [])['status'] == "succeeded"
    state["cloud"] = False
    effects = []
    with pytest.raises(Refusal, match="^cloud_off$"):
        reference(request, state, effects)
    assert effects == []


@pytest.mark.parametrize("candidate", [None, [], {"version": 1}, {1: "secret"},
                                    {"version": True}])
def test_python_hostile_envelopes_fail_closed(candidate):
    effects = []
    with pytest.raises(Refusal, match="^malformed_request$"):
        reference(candidate, {}, effects)
    assert effects == []


def test_manifests_refuse_reorder_drop_and_row_rewrite():
    for section in SECTIONS:
        rows = CASES[section]
        manifest = CASES["section_manifests"][section]
        assert list(manifest) == [row["name"] for row in rows]
        assert len(manifest) == len(rows)
        changed = copy.deepcopy(rows[0])
        changed["expect"] = "internal"
        digest = hashlib.sha256(
            json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert digest != manifest[rows[0]["name"]]
        assert list(manifest) != [row["name"] for row in reversed(rows)]
        assert list(manifest) != [row["name"] for row in rows[1:]]
