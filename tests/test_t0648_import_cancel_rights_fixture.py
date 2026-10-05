"""T0648 Import/cancel/rights fixture.

Rights behavior for the sources of the T0647 import-cancel scenario: every
scenario source resolves to the registry rights class and policy flags in the
pinned import.yaml and rights_policy.yaml; every source outside the scenario
is refused as `unknown_rights` before any state or telemetry exists; and a
cancelled run persists committed games only, each carrying the scenario source
id and `user-own` provenance. The importer under test is T0647's
PRODUCTION_BINDING, so the implementation task re-runs these rows without
edits. No new fixture file: inputs come from the T0647 contract battery.
"""

from __future__ import annotations

import pytest

from ingest.rights import intake_decision, policy_classes
from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, Refusal
from tests.test_t0647_import_cancel_contract import (
    CONTRACT,
    PRODUCTION_BINDING,
    SCENARIO,
    _input,
    reference_cancel,
)

REGISTRY = {e["id"]: e for e in CONTRACT["sources"]["entries"]}
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


def test_a_user_own_source_outside_the_scenario_exists():
    """The scenario list, not the rights class, must gate the run."""
    assert "pgn-watch" in OUTSIDE and REGISTRY["pgn-watch"]["rights_class"] == "user-own"


def test_outside_sources_are_not_user_own_by_default():
    assert OUTSIDE, "scenario must not cover every registry source"
    d = intake_decision("chesscom-public")
    assert d.allowed is False and d.reason == "ownership_unverified"


def _refused(binding, source_id):
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        binding(_input("pgn-multi"), store, source_id=source_id, cancel_at=2)
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None


@pytest.mark.parametrize(
    "source_id", [*OUTSIDE, "not-a-source", "PGN-MULTI", " pgn-multi", "pgn-multi ", ""]
)
def test_sources_outside_scenario_refused_before_state(source_id):
    _refused(PRODUCTION_BINDING, source_id)


def _cancelled_state_holds(binding, source_id, cancel_at):
    store = ReferenceStore()
    result = binding(_input(source_id), store, source_id=source_id, cancel_at=cancel_at)
    assert result["kind"] == SCENARIO["visible_output"]
    assert result["source_id"] == source_id
    committed = cancel_at - 1
    assert len(store.records) == committed
    assert sorted(entry["record"] for entry in store.index) == sorted(store.records)
    assert len(store.index) == committed
    assert store.telemetry == ["import.started", "import.cancelled"]
    for record in store.records.values():
        assert record.get("source_id") == source_id
        provenance = record.get("provenance")
        assert type(provenance) is dict, "record has no provenance"
        assert provenance.get("source_id") == source_id
        assert provenance.get("rights_class") == "user-own"


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
@pytest.mark.parametrize("cancel_at", [1, 2, 3])
def test_cancelled_records_carry_scenario_source_and_user_own_provenance(source_id, cancel_at):
    _cancelled_state_holds(PRODUCTION_BINDING, source_id, cancel_at)


# -- black-box bindings that break rights behavior must each be red ----------------


def _wrong_class(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    for record in store.records.values():
        record["provenance"] = {**record["provenance"], "rights_class": "cc0"}
    return result


def _drop_provenance(payload, store, **kwargs):
    result = reference_cancel(payload, store, **kwargs)
    for record in store.records.values():
        record.pop("provenance", None)
    return result


def _allow_outside_source(payload, store, *, source_id="pgn-multi", **kwargs):
    if source_id == "pgn-watch":
        source_id = "pgn-multi"
    return reference_cancel(payload, store, source_id=source_id, **kwargs)


def _telemetry_before_refusal(payload, store, *, source_id="pgn-multi", **kwargs):
    if source_id not in SCENARIO["sources"]:
        store.telemetry.append("import.started")
    return reference_cancel(payload, store, source_id=source_id, **kwargs)


def _persist_inflight(payload, store, *, cancel_at=2, **kwargs):
    result = reference_cancel(payload, store, cancel_at=cancel_at, **kwargs)
    store.records["inflight"] = {
        "source_id": kwargs.get("source_id", "pgn-multi"),
        "provenance": {
            "source_id": kwargs.get("source_id", "pgn-multi"),
            "rights_class": "user-own",
        },
    }
    store.index.append("inflight")
    return result


def _rights_probe(binding):
    """Every rights assertion this fixture makes, as one callable probe."""
    for source_id in (*OUTSIDE, "not-a-source", "PGN-MULTI"):
        _refused_plain(binding, source_id)
    for source_id in SCENARIO["sources"]:
        for cancel_at in (1, 2, 3):
            _cancelled_state_holds(binding, source_id, cancel_at)


def _refused_plain(binding, source_id):
    store = ReferenceStore()
    try:
        binding(_input("pgn-multi"), store, source_id=source_id, cancel_at=2)
    except Refusal as error:
        assert error.code == "unknown_rights", error.code
    else:
        raise AssertionError(f"accepted outside source {source_id}")
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None


def test_probe_is_green_for_the_production_binding():
    _rights_probe(PRODUCTION_BINDING)


@pytest.mark.parametrize(
    "binding",
    [
        _wrong_class,
        _drop_provenance,
        _allow_outside_source,
        _telemetry_before_refusal,
        _persist_inflight,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_rights_breaking_binding_is_red_by_an_assertion(binding):
    with pytest.raises(AssertionError):
        _rights_probe(binding)
