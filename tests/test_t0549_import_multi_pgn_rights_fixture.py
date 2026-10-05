"""T0549 Import/multi-PGN rights fixture.

Rights behavior for the `pgn-multi` source: user-owned file, class `user-own`,
never redistributed, provenance required on every stored record. Every
expectation is derived from the pinned import.yaml and rights_policy.yaml, not
from this file. The importer under test is T0548's PRODUCTION_BINDING, so
T0550 swapping the binding re-runs these rows against the shipped importer.

The fixture file is the digest-pinned tests/fixtures/import_rights/multi_game.pgn
already owned by T0538; it is not copied.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
import yaml

from ingest.import_pgn import ImportFailure, run_import_pgn
from ingest.rights import intake_decision, policy_classes
from tests.test_t0548_import_multi_pgn_contract import (
    CONTRACT,
    PRODUCTION_BINDING,
    ReferenceStore,
    Refusal,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "import_rights" / "multi_game.pgn"
FIXTURE_SHA256 = "3736a90c1b010210d068b8fa84a5d08335c1bccb03862db4dc02c24c5493c399"
POLICY = yaml.safe_load((ROOT / "data/contracts/rights_policy.yaml").read_text())

USER_OWN_FLAGS = {
    "ownership": "importing-user-own-data",
    "persistence": "stored-for-importing-user",
    "third_party_storage": "never",
    "redistribution": "never",
    "provenance_required": True,
}
OTHER_SOURCES = [
    "pgn-file",
    "pgn-folder",
    "pgn-watch",
    "lichess-public",
    "chesscom-public",
    "cbh-licensed",
]


def _text():
    return FIXTURE.read_text()


def test_fixture_digest_pinned():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_registry_and_policy_agree_on_pgn_multi():
    source = next(e for e in CONTRACT["sources"]["entries"] if e["id"] == "pgn-multi")
    assert source == {"id": "pgn-multi", "kind": "user-file", "rights_class": "user-own"}
    assert policy_classes()["user-own"] == USER_OWN_FLAGS
    d = intake_decision("pgn-multi")
    assert (d.known, d.allowed, d.rights_class, d.reason) == (True, True, "user-own", "")
    assert d.flags == USER_OWN_FLAGS


def test_every_stored_record_carries_user_own_provenance():
    store = ReferenceStore()
    summary = PRODUCTION_BINDING(_text(), store)
    assert summary["games_imported"] == 2
    assert len(store.records) == 2
    for rec in store.records.values():
        prov = rec["provenance"]
        assert prov["source_id"] == "pgn-multi"
        assert prov["rights_class"] == "user-own"
        assert prov["retrieved_at"] and prov["retrieval_detail"]
        assert rec["source_id"] == "pgn-multi"
    assert {e["record"] for e in store.index} == set(store.records)


def test_no_record_claims_redistribution_or_third_party_class():
    store = ReferenceStore()
    PRODUCTION_BINDING(_text(), store)
    allowed = set(policy_classes())
    for rec in store.records.values():
        cls = rec["provenance"]["rights_class"]
        assert cls in allowed
        flags = policy_classes()[cls]
        assert flags["redistribution"] == "never"
        assert flags["third_party_storage"] == "never"


def _reimport_probe(binding):
    store = ReferenceStore()
    binding(_text(), store)
    before = copy.deepcopy(store.records)
    index_before = copy.deepcopy(store.index)
    again = binding(_text(), store)
    assert again["games_already_imported"] == 2 and again["games_imported"] == 0
    assert store.records == before  # deep: nested provenance included
    assert store.index == index_before
    for rec in store.records.values():
        assert rec["provenance"]["rights_class"] == "user-own"
        assert rec["provenance"]["source_id"] == "pgn-multi"


def _rights_rewriting_mutant(text, store, **kw):
    result = PRODUCTION_BINDING(text, store, **kw)
    if result["games_already_imported"]:
        for rec in store.records.values():
            rec["provenance"]["rights_class"] = "cc0"
    return result


def _provenance_dropping_mutant(text, store, **kw):
    result = PRODUCTION_BINDING(text, store, **kw)
    if result["games_already_imported"]:
        for rec in store.records.values():
            rec["provenance"]["retrieval_detail"] = "changed"
    return result


def test_reimport_keeps_rights_and_provenance_unchanged():
    _reimport_probe(PRODUCTION_BINDING)


@pytest.mark.parametrize("mutant", [_rights_rewriting_mutant, _provenance_dropping_mutant])
def test_reimport_mutants_rewriting_provenance_are_red(mutant):
    _reimport_probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _reimport_probe(mutant)


@pytest.mark.parametrize("source_id", OTHER_SOURCES + ["not-a-source", "PGN-MULTI", " pgn-multi"])
def test_other_sources_refused_before_state(source_id):
    store = ReferenceStore()
    with pytest.raises(Refusal) as ei:
        PRODUCTION_BINDING(_text(), store, source_id=source_id)
    assert ei.value.code == "unknown_rights"
    assert store.records == {} and store.index == [] and store.summary is None
    assert store.telemetry == []


@pytest.mark.parametrize("source_id", OTHER_SOURCES)
def test_other_registry_sources_keep_their_own_class(source_id):
    # adjacent negative: a source that is not pgn-multi never inherits its decision
    d = intake_decision(source_id)
    assert d.source_id == source_id
    if source_id == "chesscom-public":
        assert d.allowed is False and d.reason == "ownership_unverified"
    if source_id == "lichess-public":
        assert d.rights_class == "cc0"
    if source_id == "cbh-licensed":
        assert d.rights_class == "licensed-own"


def test_shipped_import_pgn_does_not_accept_pgn_multi(tmp_path):
    # T0550 owns the pgn-multi dispatcher; until then the shipped path refuses it
    with pytest.raises(ImportFailure) as ei:
        run_import_pgn(
            FIXTURE, tmp_path / "store", "pgn-multi", retrieved_at="2026-09-25T00:00:00Z"
        )
    assert ei.value.code == "unknown_rights"
    assert not (tmp_path / "store").exists()


def test_policy_fails_closed_for_unknown_source():
    d = intake_decision("pgn-multi-typo")
    assert (d.known, d.allowed, d.rights_class, d.reason) == (False, False, None, "unknown_source")
    assert POLICY  # policy file parsed
