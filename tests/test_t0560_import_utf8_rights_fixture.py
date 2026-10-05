"""T0560 Import/UTF8/rights fixture.

Rights behavior for the sources of the T0559 import scenario: every source in it
resolves to the registry rights class and policy flags in the pinned import.yaml and
rights_policy.yaml, and every source outside the scenario is refused as
`unknown_rights` before any state or telemetry exists. The importer under test
is T0559's PRODUCTION_BINDING, so the implementation task re-runs these rows
without edits. No new fixture file: inputs come from the T0559 contract battery.
"""
from __future__ import annotations

import pytest

from ingest.rights import intake_decision, policy_classes
from tests.test_t0537_import_pgn_contract import VALID_PGN
from tests.test_t0548_import_multi_pgn_contract import CONTRACT, ReferenceStore, Refusal
from tests.test_t0559_import_utf8_contract import (
    PRODUCTION_BINDING,
    SCENARIO,
)

REGISTRY = {e["id"]: e for e in CONTRACT["sources"]["entries"]}
INPUT = [VALID_PGN.encode()]
ALL_SOURCES = sorted(REGISTRY)
OUTSIDE = [s for s in ALL_SOURCES if s not in SCENARIO["sources"]]
USER_OWN_FLAGS = {
    "ownership": "importing-user-own-data",
    "persistence": "stored-for-importing-user",
    "third_party_storage": "never",
    "redistribution": "never",
    "provenance_required": True,
}


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_scenario_sources_resolve_to_user_own_policy(source_id):
    assert REGISTRY[source_id]["rights_class"] == "user-own"
    d = intake_decision(source_id)
    assert (d.known, d.allowed, d.rights_class, d.reason) == (True, True, "user-own", "")
    assert d.flags == policy_classes()["user-own"] == USER_OWN_FLAGS


def test_outside_sources_are_not_user_own_by_default():
    assert OUTSIDE, "scenario must not cover every registry source"
    for source_id in OUTSIDE:
        d = intake_decision(source_id)
        assert d.source_id == source_id
        if source_id == "chesscom-public":
            assert d.allowed is False and d.reason == "ownership_unverified"


@pytest.mark.parametrize("source_id", OUTSIDE + ["not-a-source", "PGN-FILE", " pgn-file"])
def test_sources_outside_scenario_refused_before_state(source_id):
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(INPUT, store, source_id=source_id)
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_stored_records_carry_scenario_source_and_user_own_provenance(source_id):
    store = ReferenceStore()
    INPUT = [VALID_PGN.encode()[:7], VALID_PGN.encode()[7:]]
    PRODUCTION_BINDING(INPUT, store, source_id=source_id)
    assert store.records
    for rec in store.records.values():
        assert rec["source_id"] == rec["provenance"]["source_id"] == source_id
        assert rec["provenance"]["rights_class"] == "user-own"
        assert policy_classes()[rec["provenance"]["rights_class"]]["redistribution"] == "never"
