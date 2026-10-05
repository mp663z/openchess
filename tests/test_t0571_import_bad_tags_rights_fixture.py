"""T0571 Import/bad tags/rights fixture.

Rights behavior for the sources of the T0570 import scenario: every source in it
resolves to the registry rights class and policy flags in the pinned import.yaml and
rights_policy.yaml, and every source outside the scenario is refused as
`unknown_rights` before any state or telemetry exists. The importer under test
is T0570's PRODUCTION_BINDING, so the implementation task re-runs these rows
without edits. No new fixture file: inputs come from the T0570 contract battery.
"""

from __future__ import annotations

import pytest

from ingest.rights import intake_decision, policy_classes
from tests.test_t0548_import_multi_pgn_contract import CONTRACT, ReferenceStore, Refusal
from tests.test_t0570_import_bad_tags_contract import (
    BAD_TAGS,
    PRODUCTION_BINDING,
    SCENARIO,
)

REGISTRY = {e["id"]: e for e in CONTRACT["sources"]["entries"]}
INPUT = BAD_TAGS[0]
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
        PRODUCTION_BINDING(INPUT, store, source_id=source_id, scenario=SCENARIO)
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
def test_refusal_keeps_scenario_source_and_stores_no_game_record(source_id):
    store = ReferenceStore()
    result = PRODUCTION_BINDING(INPUT, store, source_id=source_id, scenario=SCENARIO)
    assert result.source_id == source_id
    assert store.records == {} and store.index == []
    assert intake_decision(source_id).rights_class == "user-own"
