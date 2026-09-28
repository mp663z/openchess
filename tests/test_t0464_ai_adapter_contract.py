"""T0464 contract-only battery. No adapter implementation or paid invocation."""

from __future__ import annotations

import copy

import pytest
import yaml

from tools.ai_adapter_contract_lint import CONTRACT, SOURCES, lint
from tools.variant_contract_lint import ContractError


def _contract():
    return yaml.safe_load(CONTRACT.read_text())


def test_contract_and_cross_source_boundaries():
    c = lint(_contract())
    assert c["role"]["status"].startswith("contract-only")
    assert c["interface"]["request"]["mode"] == ["local", "hosted_byom"]
    assert c["failures"]["mapping"]["cost_cap_exceeded"] == "cost_cap_exceeded"
    assert c["failures"]["mapping"]["provider_unavailable"] == "provider_unavailable"
    assert c["failures"]["mapping"]["cancelled"] == "cancelled"
    assert "network" in c["modes"]["local"]["availability"]
    assert c["cost"]["report"].startswith("measured-cost-only")
    assert "send-time" in c["modes"]["hosted_byom"]["timing"]
    assert len(c["unresolved"]) == 4


MUTATIONS = {
    "extra_section": lambda c: c.__setitem__("provider_key", "secret"),
    "hosted_default": lambda c: c["modes"]["hosted_byom"].__setitem__("approval", "none"),
    "no_sensitive_check": lambda c: c["modes"]["hosted_byom"]["checks"].remove(
        "sensitive-collection-current-verdict"
    ),
    "stale_terms": lambda c: c["modes"]["hosted_byom"].__setitem__("timing", "only-at-route-time"),
    "extra_payload": lambda c: c["modes"]["hosted_byom"].__setitem__(
        "payload", "arbitrary-game-data"
    ),
    "key_in_response": lambda c: c["interface"]["response"]["fields"].append("key_material"),
    "key_in_request": lambda c: c["interface"]["request"]["fields"].append("api_key"),
    "local_egress": lambda c: c["modes"]["local"].__setitem__("egress", "allowed"),
    "silent_paid_send": lambda c: c["cost"].__setitem__("policy", "uncapped"),
    "invented_zero": lambda c: c["interface"]["response"].__setitem__(
        "cost_usd", "zero-if-unknown"
    ),
    "lose_failure": lambda c: c["failures"]["mapping"].pop("provider_unavailable"),
    "lost_cancellation": lambda c: c["failures"]["classes"].remove("cancelled"),
    "weaken_rollback": lambda c: c["failures"].__setitem__("atomic", "partial-effects-okay"),
    "unknown_field": lambda c: c["interface"]["request"].__setitem__("undocumented", "open"),
    "major_downgrade": lambda c: c["versioning"].__setitem__(
        "rollback", "send-new-payload-to-old-client"
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_contract_mutation_is_refused(name):
    doc = copy.deepcopy(_contract())
    MUTATIONS[name](doc["contract"])
    with pytest.raises(ContractError):
        lint(doc)


@pytest.mark.parametrize(
    "document", [[], {}, {"schema_version": True}, {"schema_version": 1, "contract": []}]
)
def test_malformed_envelope_is_refused(document):
    with pytest.raises(ContractError):
        lint(document)


@pytest.mark.parametrize(
    "source,field,value",
    [
        (
            "byom",
            ("identifiers", "payload", "allowed_keys"),
            ["fen", "moves", "task", "max-tokens", "full-corpus"],
        ),
        ("byom", ("semantics", "evaluation"), "cached-verdict"),
        ("cloud", ("semantics", "evaluation"), "cached-verdict"),
        ("sensitive", ("semantics", "default"), "unknown-allows-cloud"),
        ("local", ("semantics", "egress"), "network-allowed"),
        (
            "route",
            ("areas", "provider_routing", "ops", "route", "request", "fields", "policy"),
            {"type": "object", "fields": {"content": {"type": "string"}}},
        ),
    ],
)
def test_cross_source_drift_is_refused(monkeypatch, tmp_path, source, field, value):
    doc = yaml.safe_load(SOURCES[source].read_text())
    target = doc["contract"] if source != "route" else doc
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = value
    path = tmp_path / f"{source}.yaml"
    path.write_text(yaml.safe_dump(doc))
    monkeypatch.setitem(SOURCES, source, path)
    with pytest.raises(ContractError):
        lint(_contract())
