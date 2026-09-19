"""T0538: import rights fixture - the rights fixture passes expected and
adjacent-negative behavior on the SHIPPED path; persisted state is
checked.

Shipped surfaces under test:
- ingest.rights (the policy-driven intake gate): exact per-class
  decisions and structured flags, fail-closed unknowns, and the
  user-own-only ownership condition.
- ingest.import_pgn.run_import_pgn (the shipped import-pgn runner,
  wired through the rights gate): persisted provenance on disk carries
  the registry rights class, and the policy's structured flags for that
  class hold exactly.

Fixture: tests/fixtures/import_rights/ (digest-pinned).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import ingest.rights as rights_mod
from ingest.import_pgn import ImportFailure, run_import_pgn
from ingest.rights import intake_decision, policy_classes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "import_rights"
CASES = json.loads((FIXTURE_DIR / "cases.json").read_text())

RETRIEVED_AT = "2026-09-19T00:00:00Z"

PINNED_DIGESTS = {
    "cases.json": "ead98fc27483478730fd2a478db170af804fdf0129f4d571d30828c519dd2819",
    "valid_user_game.pgn": "749aec6c1ff3c925247f5713116d7a41748e7b0dd6c07d62be41258fceb343f2",
    "multi_game.pgn": "3736a90c1b010210d068b8fa84a5d08335c1bccb03862db4dc02c24c5493c399",
}


def _digest(name: str) -> str:
    return hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest()


class TestFixtureIntegrity:
    def test_digests_pinned(self):
        assert set(PINNED_DIGESTS) == {"cases.json", "valid_user_game.pgn",
                                       "multi_game.pgn"}
        for name, digest in PINNED_DIGESTS.items():
            assert _digest(name) == digest, f"fixture drift: {name}"

    def test_cases_schema_and_contracts(self):
        assert CASES["schema"] == 1
        assert CASES["contracts"] == ["data/contracts/import.yaml",
                                      "data/contracts/rights_policy.yaml"]
        assert {c["name"] for c in CASES["happy"]} == {
            "user-file-user-own", "lichess-cc0", "cbh-licensed-own",
            "chesscom-user-own-only-verified"}
        assert {c["name"] for c in CASES["negative"]} == {
            "chesscom-user-own-only-unverified", "unknown-source-id"}


class TestIntakeDecisions:
    @pytest.mark.parametrize("case", CASES["happy"] + CASES["negative"],
                             ids=[c["name"] for c in CASES["happy"] + CASES["negative"]])
    def test_fixture_case_matches_shipped_decision(self, case):
        d = intake_decision(case["source_id"],
                            ownership_verified=case.get("ownership_verified", False))
        exp = case["expect"]
        assert d.known is exp["known"], case["name"]
        assert d.rights_class == exp["rights_class"], case["name"]
        assert d.allowed is exp["allowed"], case["name"]
        assert d.reason == exp["reason"], case["name"]
        if exp["rights_class"] is not None:
            # flags are the policy class's structured fields, verbatim
            assert d.flags == policy_classes()[exp["rights_class"]], case["name"]
            assert set(d.flags) == {"ownership", "persistence", "third_party_storage",
                                    "redistribution", "provenance_required"}
        else:
            assert d.flags == {}

    def test_every_registry_source_has_a_decision(self):
        for source_id in ("pgn-file", "pgn-multi", "pgn-folder", "pgn-watch",
                          "lichess-public", "chesscom-public", "cbh-licensed"):
            d = intake_decision(source_id)
            assert d.known is True
            assert d.rights_class is not None

    def test_policy_classes_exact(self):
        classes = policy_classes()
        assert set(classes) == {"user-own", "cc0", "user-own-only", "licensed-own"}
        assert classes["user-own"] == {
            "ownership": "importing-user-own-data",
            "persistence": "stored-for-importing-user",
            "third_party_storage": "never",
            "redistribution": "never",
            "provenance_required": True}
        assert classes["user-own-only"] == {
            "ownership": "importing-user-own-games-only",
            "persistence": "verifying-session-only-for-third-party",
            "third_party_storage": "never",
            "redistribution": "never",
            "provenance_required": True}

    def test_redistribution_never_for_user_classes(self):
        for source_id in ("pgn-file", "chesscom-public", "cbh-licensed"):
            d = intake_decision(source_id, ownership_verified=True)
            assert d.flags["redistribution"] == "never"
            assert d.flags["third_party_storage"] == "never"

def _entry(entries, sid):
    return next(e for e in entries if isinstance(e, dict) and e.get("id") == sid)


_CLASS_SOURCE = {"user-own": "pgn-file", "cc0": "lichess-public",
                 "user-own-only": "chesscom-public",
                 "licensed-own": "cbh-licensed"}


def _contra_mutations():
    """Every operative field of every class, contradicted independently:
    cross-class value swaps, unknown values, wrong types (incl.
    unhashable), bool flips and bool-as-int."""
    out = []
    exp = rights_mod._EXPECTED_CLASSES
    for cls, fields in exp.items():
        for f, want in fields.items():
            others = sorted(str(v) for v in
                            {exp[c][f] for c in exp if exp[c][f] != want})
            for bad in others:
                out.append((cls, f, bad, f"swap-{bad}"))
            if type(want) is bool:
                out.append((cls, f, not want, "flip"))
                out.append((cls, f, 1 if want else 0, "bool-as-int"))
            else:
                out.append((cls, f, "MUTATED-UNKNOWN-VALUE", "unknown-value"))
                out.append((cls, f, [want], "unhashable-type"))
                out.append((cls, f, None, "none-type"))
    return out


_CONTRA = _contra_mutations()
_MISSING = [(c, f) for c in rights_mod._EXPECTED_CLASSES
            for f in rights_mod._CLASS_FIELDS]


class TestShippedGateMutationBattery:
    """The verifier replays one-defect mutations against the shipped
    gate: every case below mutates the loaded policy and calls the
    SHIPPED intake_decision, asserting a closed refusal with a stable
    reason. No local-dict assertions anywhere in this battery."""

    @pytest.mark.parametrize("cls,field,bad,tag", _CONTRA,
                             ids=[f"{c}:{f}:{t}" for c, f, b, t in _CONTRA])
    def test_field_contradiction_refused(self, monkeypatch, cls, field, bad, tag):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["classes"][cls][field] = bad
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision(_CLASS_SOURCE[cls], ownership_verified=True)
        assert d.allowed is False
        assert d.reason == f"policy_contradiction:{cls}:{field}"

    @pytest.mark.parametrize("cls,field", _MISSING,
                             ids=[f"{c}:{f}" for c, f in _MISSING])
    def test_missing_field_refused(self, monkeypatch, cls, field):
        doc = copy.deepcopy(rights_mod._POLICY)
        del doc["classes"][cls][field]
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision(_CLASS_SOURCE[cls], ownership_verified=True)
        assert d.allowed is False
        assert d.reason == f"policy_contradiction:{cls}:{field}"

    @pytest.mark.parametrize("cls", list(rights_mod._EXPECTED_CLASSES))
    def test_extra_field_refused(self, monkeypatch, cls):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["classes"][cls]["rogue_field"] = "x"
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision(_CLASS_SOURCE[cls], ownership_verified=True)
        assert d.allowed is False
        assert d.reason == f"policy_contradiction:{cls}:rogue_field"

    @pytest.mark.parametrize("bad", [False, "yes", 1, None],
                             ids=["false", "str-yes", "int-1", "none"])
    def test_fail_closed_reversal_refused(self, monkeypatch, bad):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["fail_closed"] = bad
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:fail_closed"

    def test_fail_closed_missing_refused(self, monkeypatch):
        doc = copy.deepcopy(rights_mod._POLICY)
        del doc["fail_closed"]
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:fail_closed"

    def test_policy_id_drift_refused(self, monkeypatch):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["id"] = "import-rights-policy-v2"
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:id"

    def test_extra_class_refused(self, monkeypatch):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["classes"]["rogue"] = dict(doc["classes"]["cc0"])
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:classes"

    def test_missing_class_refused(self, monkeypatch):
        doc = copy.deepcopy(rights_mod._POLICY)
        del doc["classes"]["cc0"]
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("lichess-public")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:classes"

    def test_unknown_class_drift_refused(self, monkeypatch):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["unknown_class"]["effect"] = "allow-anyway"
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:unknown_class"

    @pytest.mark.parametrize("key", list(rights_mod._ROOT_FIELDS))
    def test_missing_policy_root_key_refused(self, monkeypatch, key):
        doc = copy.deepcopy(rights_mod._POLICY)
        del doc[key]
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == f"policy_contradiction:{key}"

    def test_extra_policy_root_key_refused(self, monkeypatch):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["rogue"] = "x"
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:rogue"

    def test_rule_wrong_type_refused(self, monkeypatch):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["rule"] = 1
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:rule"

    def test_registry_not_mapping_refused(self, monkeypatch):
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", "not-a-registry")
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "registry_contradiction:registry"

    def test_registry_extra_source_refused(self, monkeypatch):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        entries.append({"id": "rogue-source", "kind": "user-file",
                        "rights_class": "user-own"})
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "registry_contradiction:sources"

    def test_registry_missing_source_refused(self, monkeypatch):
        entries = [e for e in copy.deepcopy(rights_mod._SOURCE_ENTRIES)
                   if e["id"] != "pgn-watch"]
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "registry_contradiction:sources"

    def test_registry_duplicate_source_same_value_refused(self, monkeypatch):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        entries.append(dict(entries[0]))
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "registry_contradiction:duplicate:pgn-file"

    def test_registry_duplicate_source_conflicting_refused(self, monkeypatch):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        entries.append({"id": "pgn-file", "kind": "public-api",
                        "rights_class": "cc0"})
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "registry_contradiction:duplicate:pgn-file"

    @pytest.mark.parametrize("sid", list(rights_mod._EXPECTED_SOURCES))
    def test_entry_not_mapping_refused(self, monkeypatch, sid):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        i = list(rights_mod._EXPECTED_SOURCES).index(sid)
        entries[i] = "not-a-mapping"
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision(sid, ownership_verified=True)
        assert d.allowed is False
        assert d.reason == f"registry_contradiction:entry[{i}]"

    @pytest.mark.parametrize("field", ["id", "kind", "rights_class"])
    @pytest.mark.parametrize("sid", list(rights_mod._EXPECTED_SOURCES))
    def test_entry_missing_field_refused(self, monkeypatch, sid, field):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        del _entry(entries, sid)[field]
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision(sid, ownership_verified=True)
        assert d.allowed is False
        i = list(rights_mod._EXPECTED_SOURCES).index(sid)
        assert d.reason == f"registry_contradiction:entry[{i}]:{field}"

    @pytest.mark.parametrize("sid", list(rights_mod._EXPECTED_SOURCES))
    def test_entry_extra_field_refused(self, monkeypatch, sid):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        _entry(entries, sid)["rogue"] = "x"
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision(sid, ownership_verified=True)
        assert d.allowed is False
        i = list(rights_mod._EXPECTED_SOURCES).index(sid)
        assert d.reason == f"registry_contradiction:entry[{i}]:rogue"

    @pytest.mark.parametrize("bad", ["rogue-kind", 1, None],
                             ids=["unknown", "int", "none"])
    @pytest.mark.parametrize("sid", list(rights_mod._EXPECTED_SOURCES))
    def test_entry_bad_kind_refused(self, monkeypatch, sid, bad):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        _entry(entries, sid)["kind"] = bad
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision(sid, ownership_verified=True)
        assert d.allowed is False
        assert d.reason == f"registry_contradiction:{sid}:kind"

    @pytest.mark.parametrize("bad", [True, "not-a-class", ["user-own"], None],
                             ids=["bool", "unknown-class", "list", "none"])
    @pytest.mark.parametrize("sid", list(rights_mod._EXPECTED_SOURCES))
    def test_entry_bad_rights_class_refused(self, monkeypatch, sid, bad):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        _entry(entries, sid)["rights_class"] = bad
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        d = rights_mod.intake_decision(sid, ownership_verified=True)
        assert d.allowed is False
        assert d.reason == f"registry_contradiction:{sid}:rights_class"

    @pytest.mark.parametrize("bad", [[], {}, True, 1, None],
                             ids=["list", "dict", "bool", "int", "none"])
    def test_source_id_wrong_type_refused_no_raise(self, bad):
        d = rights_mod.intake_decision(bad)
        assert d.allowed is False
        assert d.reason == "source_id_invalid_type"

    def test_source_id_arbitrary_object_refused_no_raise(self):
        d = rights_mod.intake_decision(object())
        assert d.allowed is False
        assert d.reason == "source_id_invalid_type"

    @pytest.mark.parametrize("bad", ["", "   "], ids=["empty", "whitespace"])
    def test_source_id_empty_refused(self, bad):
        d = rights_mod.intake_decision(bad)
        assert d.allowed is False
        assert d.reason == "source_id_empty"

    @pytest.mark.parametrize("bad", ["", "   "], ids=["empty", "whitespace"])
    def test_empty_rule_refused(self, monkeypatch, bad):
        doc = copy.deepcopy(rights_mod._POLICY)
        doc["rule"] = bad
        monkeypatch.setattr(rights_mod, "_POLICY", doc)
        d = rights_mod.intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "policy_contradiction:rule"

    @pytest.mark.parametrize("bad", [1, 0, "yes", "true", None],
                             ids=["int-1", "int-0", "str-yes", "str-true", "none"])
    def test_non_bool_ownership_verification_refused(self, bad):
        d = rights_mod.intake_decision("chesscom-public", ownership_verified=bad)
        assert d.allowed is False
        assert d.reason == "ownership_verification_invalid_type"

    def test_arbitrary_object_ownership_verification_refused(self):
        d = rights_mod.intake_decision("chesscom-public",
                                       ownership_verified=object())
        assert d.allowed is False
        assert d.reason == "ownership_verification_invalid_type"


class TestShippedPathPersistedState:
    def test_user_fixture_persists_user_own_provenance(self, tmp_path):
        store = tmp_path / "store"
        summary = run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", store,
                                 "pgn-file", retrieved_at=RETRIEVED_AT)
        assert summary["games_imported"] == 1
        rec = json.loads(next((store / "games").glob("*.json")).read_text())
        prov = rec["provenance"]
        assert prov["source_id"] == "pgn-file"
        assert prov["rights_class"] == "user-own"
        # the policy's structured flags for the persisted class hold exactly
        flags = policy_classes()[prov["rights_class"]]
        assert flags["ownership"] == "importing-user-own-data"
        assert flags["persistence"] == "stored-for-importing-user"
        assert flags["third_party_storage"] == "never"
        assert flags["redistribution"] == "never"
        assert flags["provenance_required"] is True
        assert rec["tags"]["White"] == "Fixture Player A"

    def test_multi_fixture_persists_rights_on_every_record(self, tmp_path):
        store = tmp_path / "store"
        summary = run_import_pgn(FIXTURE_DIR / "multi_game.pgn", store,
                                 "pgn-file", retrieved_at=RETRIEVED_AT)
        assert summary["games_imported"] == 2
        records = [json.loads(p.read_text()) for p in (store / "games").glob("*.json")]
        assert len(records) == 2
        for rec in records:
            assert rec["provenance"]["rights_class"] == "user-own"
            assert rec["provenance"]["source_id"] == "pgn-file"
        index = json.loads((store / "index.json").read_text())
        assert len({e["game_id"] for e in index}) == 2

    def test_reimport_keeps_provenance_and_rights(self, tmp_path):
        store = tmp_path / "store"
        run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", store,
                       "pgn-file", retrieved_at=RETRIEVED_AT)
        before = next((store / "games").glob("*.json")).read_bytes()
        s2 = run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", store,
                            "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s2["games_already_imported"] == 1
        assert next((store / "games").glob("*.json")).read_bytes() == before


class TestAdjacentNegatives:
    def test_unknown_source_refused_on_shipped_path(self, tmp_path):
        with pytest.raises(ImportFailure) as ei:
            run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", tmp_path / "store",
                           "not-a-source", retrieved_at=RETRIEVED_AT)
        assert ei.value.code == "unknown_rights"
        assert not (tmp_path / "store").exists()

    def test_out_of_scenario_registry_source_refused(self, tmp_path):
        # the import-pgn scenario ships exactly [pgn-file]; even a
        # storage-permitting registry source is refused at this entrypoint
        with pytest.raises(ImportFailure) as ei:
            run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", tmp_path / "store",
                           "lichess-public", retrieved_at=RETRIEVED_AT)
        assert ei.value.code == "unknown_rights"
        assert not (tmp_path / "store").exists()

    def test_user_own_only_unverified_forbidden_at_gate(self):
        d = intake_decision("chesscom-public")
        assert d.allowed is False
        assert d.reason == "ownership_unverified"
        # and the same class stays forbidden for ANY registry id mapped to it
        assert intake_decision("chesscom-public", ownership_verified=False).allowed is False

    def test_runner_refuses_registry_contradiction_before_state(self, tmp_path,
                                                                monkeypatch):
        entries = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        entries[0] = "not-a-mapping"
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", entries)
        with pytest.raises(ImportFailure) as ei:
            run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", tmp_path / "store",
                           "pgn-file", retrieved_at=RETRIEVED_AT)
        assert ei.value.code == "unknown_rights"
        assert not (tmp_path / "store").exists()

    @pytest.mark.parametrize("bad", [[], {}, True, 1, None, ""],
                             ids=["list", "dict", "bool", "int", "none", "empty"])
    def test_runner_refuses_wrong_type_source_id_before_state(self, tmp_path, bad):
        with pytest.raises(ImportFailure) as ei:
            run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", tmp_path / "store",
                           bad, retrieved_at=RETRIEVED_AT)
        assert ei.value.code == "unknown_rights"
        assert ei.value.message.startswith(
            "unknown_rights: source id must be a nonempty string")
        assert not (tmp_path / "store").exists()

    def test_runner_refuses_when_gate_forbids(self, tmp_path, monkeypatch):
        """Wiring proof: the runner consults the rights gate - if the
        gate forbids (even for a scenario-pinned source), the import is
        refused before any store state exists."""
        import ingest.import_pgn as imp
        from ingest.rights import RightsDecision
        forbidden = RightsDecision(source_id="pgn-file", known=True,
                                   rights_class="user-own", allowed=False,
                                   reason="policy_changed")
        monkeypatch.setattr(imp, "intake_decision", lambda sid: forbidden)
        with pytest.raises(ImportFailure) as ei:
            run_import_pgn(FIXTURE_DIR / "valid_user_game.pgn", tmp_path / "store",
                           "pgn-file", retrieved_at=RETRIEVED_AT)
        assert ei.value.code == "unknown_rights"
        assert not (tmp_path / "store").exists()

    def test_unknown_rights_class_registry_contradiction(self, monkeypatch):
        fake = copy.deepcopy(rights_mod._SOURCE_ENTRIES)
        _entry(fake, "pgn-file")["rights_class"] = "not-a-class"
        monkeypatch.setattr(rights_mod, "_SOURCE_ENTRIES", fake)
        d = intake_decision("pgn-file")
        assert d.allowed is False
        assert d.reason == "registry_contradiction:pgn-file:rights_class"
        monkeypatch.undo()
        assert intake_decision("pgn-file").allowed is True
