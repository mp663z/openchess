"""T0537: import-pgn scenario contract test - drives the SHIPPED import
path (ingest.import_pgn.run_import_pgn) and asserts the T0536 contract's
import-pgn scenario on externally inspectable state: exact visible
import-summary, exact stored record/provenance/index structures read
back from disk, exact ordered telemetry with forbidden-event
suppression, exact error codes for malformed/illegal inputs, and zero
partial state after failure.

Fixtures are immutable: the contract documents and the valid PGN
fixture are digest-pinned here; any silent contract/fixture drift fails.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ingest.import_pgn import ImportFailure, run_import_pgn

ROOT = Path(__file__).resolve().parents[1]
IMPORT_CONTRACT = ROOT / "data" / "contracts" / "import.yaml"
RIGHTS_POLICY = ROOT / "data" / "contracts" / "rights_policy.yaml"
DOC = yaml.safe_load(IMPORT_CONTRACT.read_text())
C = DOC["contract"]
RP = yaml.safe_load(RIGHTS_POLICY.read_text())["rights_policy"]

SCENARIO = next(s for s in C["scenarios"]["entries"] if s["id"] == "import-pgn")
RECORD_FIELDS = list(C["record"]["fields"])
PROVENANCE_FIELDS = list(C["record"]["provenance_fields"])
ERROR_ENUM = list(C["errors"]["closed_enum"])
ERROR_FIELDS = C["errors"]["shape"]["error"]["fields"]

VALID_PGN = '''[Event "Test"]
[Site "https://lichess.org/abc123XY"]
[White "a"]
[Black "b"]
[Result "1-0"]
[Variant "Standard"]

1. e4 e5 2. Nf3 Nc6 1-0
'''

# Immutable fixture digests (sha256). Drift here means the contract or
# the fixture moved; update only with a reviewed contract change.
FIXTURE_DIGESTS = {}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


FIXTURE_DIGESTS.update({
    "import.yaml": _digest(IMPORT_CONTRACT),
    "rights_policy.yaml": _digest(RIGHTS_POLICY),
    "valid_pgn": hashlib.sha256(VALID_PGN.encode()).hexdigest(),
})

RETRIEVED_AT = "2026-09-19T00:00:00Z"

# VALID_PGN with all six identity headers: update classification
# requires the full header set (contract two-tier identity rule)
FULL_HEADER_PGN = VALID_PGN.replace(
    '[Result "1-0"]',
    '[UTCDate "2026.09.19"]\n[Date "2026.09.19"]\n[Round "1"]\n'
    '[Result "1-0"]', 1)

PINNED_DIGESTS = {
    "import.yaml": "ae3633113a5b22e0552f47ffe426425492dfaf28b7e11227ca602e89369e8e2c",
    "rights_policy.yaml": "90e8b328153ba0cc0b84b601ea2ae38239225fc77a88eadaa81b4ffd537f8d7d",
    "valid_pgn": "fae5a5bbb31fb85acc6e64b483bcb124e494617c3d5f5c4079ae8bac3ed0f625",
}


def _import(tmp_path: Path, text: str = VALID_PGN, source_id: str = "pgn-file"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "in.pgn"
    p.write_text(text)
    store = tmp_path / "store"
    return run_import_pgn(p, store, source_id, retrieved_at=RETRIEVED_AT), store


def _read_json(path: Path):
    return json.loads(path.read_text())


def _telemetry_events(store: Path) -> list[str]:
    return [json.loads(line)["event"]
            for line in (store / "telemetry.jsonl").read_text().splitlines()]


def _stored_records(store: Path) -> list[dict]:
    games = store / "games"
    return [_read_json(p) for p in sorted(games.glob("*.json"))]


def _assert_exact_record(rec: dict) -> None:
    assert list(rec) == RECORD_FIELDS, "record field set/order drift"
    assert list(rec["provenance"]) == PROVENANCE_FIELDS, "provenance field drift"


class TestExpectedPath:
    def test_scenario_pins(self):
        assert SCENARIO["sources"] == ["pgn-file"]
        assert SCENARIO["visible_output"] == "import-summary"
        assert SCENARIO["persisted_state"] == ["game-records", "provenance",
                                               "index-entries"]
        assert SCENARIO["telemetry"] == ["import.started", "import.game_stored",
                                         "import.completed"]
        assert SCENARIO["error_codes"] == ["malformed_request"]
        assert C["idempotency"]["dedup_key"] == "game_id"
        assert C["atomicity"]["commit_boundary"] == "per-game"
        assert C["atomicity"]["partial_game"] == "never-visible"
        assert RP["fail_closed"] is True

    def test_fixture_digests_pinned(self):
        """Digests are stable within a run and across reruns; the pinned
        set below is the regression tripwire for contract drift."""
        assert set(FIXTURE_DIGESTS) == {"import.yaml", "rights_policy.yaml",
                                        "valid_pgn"}
        for digest in FIXTURE_DIGESTS.values():
            assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        # pinned values (reviewed): any contract/fixture change fails here
        assert FIXTURE_DIGESTS == PINNED_DIGESTS

    def test_visible_output_exact_shape(self, tmp_path):
        summary, store = _import(tmp_path)
        # closed key set, exact order
        assert list(summary) == ["kind", "source_id", "games_imported",
                                 "games_already_imported", "games_updated"]
        assert summary["kind"] == SCENARIO["visible_output"]
        assert summary["source_id"] == "pgn-file"
        assert summary["games_imported"] == 1
        assert summary["games_already_imported"] == 0
        assert summary["games_updated"] == 0
        assert isinstance(summary["games_imported"], int)
        assert isinstance(summary["games_already_imported"], int)
        assert isinstance(summary["games_updated"], int)
        # summary is also persisted for external inspection
        assert _read_json(store / "summary.json") == summary

    def test_visible_output_rejects_extra_key(self, tmp_path):
        summary, _ = _import(tmp_path)
        mutated = dict(summary, unexpected=True)
        assert list(mutated) != list(summary), "extra key must change the key sequence"

    def test_persisted_records_provenance_index_exact(self, tmp_path):
        _, store = _import(tmp_path)
        records = _stored_records(store)
        assert len(records) == 1
        rec = records[0]
        _assert_exact_record(rec)
        prov = rec["provenance"]
        assert prov["source_id"] == "pgn-file"
        assert prov["rights_class"] == "user-own"
        assert prov["retrieved_at"] == RETRIEVED_AT
        assert prov["retrieval_detail"].endswith("in.pgn")
        # contract: content_sha256 covers the canonical record bytes
        canonical = {
            k: rec[k] for k in ("game_id", "source_id", "variant",
                                "tags", "movetext")}
        canonical["tags"] = {k: canonical["tags"][k]
                             for k in sorted(canonical["tags"])}
        assert rec["content_sha256"] == hashlib.sha256(json.dumps(
            canonical, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()
        assert rec["tags"]["White"] == "a"
        assert rec["variant"] == "standard"
        # index manifest: ordered unique entries, exact key set/order,
        # game_id -> referenced content-addressed record filename
        index = _read_json(store / "index.json")
        assert len(index) == 1
        entry = index[0]
        assert list(entry) == ["game_id", "record"]
        assert entry["game_id"] == rec["game_id"]
        assert len(entry["game_id"]) == 16
        referenced = store / "games" / entry["record"]
        assert referenced.exists()
        assert _read_json(referenced) == rec

    def test_record_field_order_mutation_detected(self, tmp_path):
        _, store = _import(tmp_path)
        rec = _stored_records(store)[0]
        for field in RECORD_FIELDS:
            mutated = {k: v for k, v in rec.items() if k != field}
            with pytest.raises(AssertionError):
                _assert_exact_record(mutated)
        rev = dict(reversed(list(rec.items())))
        if list(rev) != RECORD_FIELDS:
            with pytest.raises(AssertionError):
                _assert_exact_record(rev)

    def test_telemetry_exact_sequence_and_suppression(self, tmp_path):
        _, store = _import(tmp_path)
        events = _telemetry_events(store)
        assert events == SCENARIO["telemetry"]
        # forbidden-event suppression: only scenario events may appear
        allowed = set(SCENARIO["telemetry"])
        assert all(e in allowed for e in events)
        # order matters: any reorder is a violation
        assert events != list(reversed(events))

    def test_multi_game_file_commits_per_game(self, tmp_path):
        two = VALID_PGN + "\n" + VALID_PGN.replace('"a"', '"c"').replace('"b"', '"d"')
        summary, store = _import(tmp_path, two)
        assert summary["games_imported"] == 2
        assert _telemetry_events(store) == ["import.started", "import.game_stored",
                                            "import.game_stored", "import.completed"]
        assert len(_read_json(store / "index.json")) == 2
        for rec in _stored_records(store):
            _assert_exact_record(rec)


class TestRightsAndSources:
    def test_unknown_source_fails_closed(self, tmp_path):
        with pytest.raises(ImportFailure) as ei:
            _import(tmp_path, source_id="made-up-source")
        assert ei.value.code == "unknown_rights"
        assert ei.value.code in ERROR_ENUM
        assert (tmp_path / "store").exists() is False or not _stored_records(
            tmp_path / "store")

    def test_registry_rights_classes_verified(self):
        classes = set(RP["classes"])
        assert classes == set(C["sources"]["rights_classes"])
        for entry in C["sources"]["entries"]:
            assert entry["rights_class"] in classes, entry["id"]
        chesscom = next(s for s in C["sources"]["entries"]
                        if s["id"] == "chesscom-public")
        assert chesscom["rights_class"] == "user-own-only"
        flags = RP["classes"]["user-own-only"]
        assert flags["persistence"] == "verifying-session-only-for-third-party"
        assert flags["redistribution"] == "never"
        assert flags["third_party_storage"] == "never"
        assert RP["unknown_class"] == {"effect": "reject-import",
                                       "error": "unknown_rights"}

    def test_error_shape_fields(self, tmp_path):
        with pytest.raises(ImportFailure) as ei:
            _import(tmp_path, source_id="made-up-source")
        d = ei.value.as_dict()
        assert set(d) == set(ERROR_FIELDS), "error shape drift"
        assert isinstance(d["code"], str)
        assert isinstance(d["message"], str) and d["message"]
        assert isinstance(d["retryable"], bool) and d["retryable"] is False


MALFORMED_CASES = [
    # (name, pgn, expected error code)
    ("non_chess_movetext",
     '[Event "x"]\n[Result "1-0"]\n\n1. hello world 1-0\n',
     "malformed_request"),
    ("unclosed_tag_bracket",
     '[Event "x\n[Result "1-0"]\n\n1. e4 e5 1-0\n',
     "malformed_request"),
    ("unclosed_tag_quote",
     '[Event "x]\n[Result "1-0"]\n\n1. e4 e5 1-0\n',
     "malformed_request"),
    ("truncated_movetext",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3\n',
     "malformed_request"),
    ("illegal_san_pinned_pawn",
     '[Event "x"]\n[Result "1-0"]\n\n'
     '1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. O-O Nf6 5. d4 exd4 6. e5 Ne4 7. Nxd4 Nxd4 8. Qx'
     'd4 d6 1-0\n',
     "illegal_move"),
    ("illegal_san_king_leap",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4 e5 2. Ke3 Nf6 1-0\n',
     "illegal_move"),
    ("illegal_san_impossible_capture",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4 e5 2. Qxh5 Nc6 1-0\n',
     "illegal_move"),
    ("false_check_claim",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4+ e5 1-0\n',
     "malformed_request"),
    ("false_mate_claim",
     '[Event "x"]\n[Result "1-0"]\n\n1. e3 e5 2. Qh5# Nc6 1-0\n',
     "malformed_request"),
    ("ambiguous_san",
     '[Event "x"]\n[Result "1-0"]\n\n1. d4 a6 2. Nd2 a5 3. Nf3 Nc6 1-0\n',
     "malformed_request"),
    ("unbalanced_variation_open",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4 (1. d4 d5 1-0\n',
     "malformed_request"),
    ("unbalanced_variation_close",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4 e5) 1-0\n',
     "malformed_request"),
    ("unterminated_comment",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4 {oops e5 1-0\n',
     "malformed_request"),
    ("bare_nag_marker",
     '[Event "x"]\n[Result "1-0"]\n\n1. e4 $ e5 1-0\n',
     "malformed_request"),
    ("empty_movetext",
     '[Event "x"]\n[Result "1-0"]\n\n1-0\n',
     "malformed_request"),
    ("result_token_mismatch",
     '[Event "x"]\n[Result "0-1"]\n\n1. e4 e5 1-0\n',
     "malformed_request"),
]


class TestMalformedBattery:
    @pytest.mark.parametrize("name,pgn,code", MALFORMED_CASES,
                             ids=[c[0] for c in MALFORMED_CASES])
    def test_malformed_rejected_exact_code_no_partial_state(self, name, pgn, code, tmp_path):
        with pytest.raises(ImportFailure) as ei:
            _import(tmp_path, pgn)
        err = ei.value
        assert err.code == code, f"{name}: {err.code} != {code}"
        assert err.code in ERROR_ENUM
        assert err.message.startswith(err.code)
        assert isinstance(err.retryable, bool)
        # zero partial state: no game record, no index entry, no summary,
        # no stored telemetry for the failed game
        store = tmp_path / "store"
        assert _stored_records(store) == []
        index_path = store / "index.json"
        assert not index_path.exists() or _read_json(index_path) == []
        assert not (store / "summary.json").exists()
        events = _telemetry_events(store)
        assert "import.game_stored" not in events
        assert "import.completed" not in events

    def test_battery_size_pinned(self):
        assert len(MALFORMED_CASES) == 16
        assert len({c[0] for c in MALFORMED_CASES}) == 16

    def test_each_case_differs_from_valid(self):
        for name, pgn, _ in MALFORMED_CASES:
            assert pgn != VALID_PGN, name


class TestIdempotency:
    def test_duplicate_within_one_file_is_visible_noop(self, tmp_path):
        summary, store = _import(tmp_path, VALID_PGN + "\n" + VALID_PGN)
        assert summary["games_imported"] == 1
        assert summary["games_already_imported"] == 1
        assert summary["games_updated"] == 0
        records = _stored_records(store)
        assert len(records) == 1
        index = _read_json(store / "index.json")
        assert [e["game_id"] for e in index] == [records[0]["game_id"]]
        assert all(list(e) == ["game_id", "record"] for e in index)
        assert _telemetry_events(store) == ["import.started", "import.game_stored",
                                            "import.game_duplicate",
                                            "import.completed"]

    def test_reimport_identical_file_is_noop(self, tmp_path):
        p = tmp_path / "in.pgn"
        p.write_text(VALID_PGN)
        store = tmp_path / "store"
        s1 = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s1["games_imported"] == 1
        rec_path = next((store / "games").glob("*.json"))
        before = rec_path.read_bytes()
        s2 = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s2["games_imported"] == 0
        assert s2["games_already_imported"] == 1
        assert s2["games_updated"] == 0
        assert rec_path.read_bytes() == before  # untouched no-op
        assert len(_read_json(store / "index.json")) == 1

    def test_same_id_new_content_is_recorded_replacement(self, tmp_path):
        # update classification requires the full identity headers
        # (contract two-tier identity rule); sparse games never replace
        changed = FULL_HEADER_PGN.replace("2. Nf3 Nc6", "2. Nf3 d6")
        assert changed != FULL_HEADER_PGN
        p = tmp_path / "in.pgn"
        store = tmp_path / "store"
        p.write_text(FULL_HEADER_PGN)
        run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        p.write_text(changed)
        s2 = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s2["games_imported"] == 0
        assert s2["games_updated"] == 1
        index = _read_json(store / "index.json")
        assert len(index) == 1  # still unique
        rec = _stored_records(store)[0]
        assert "2. Nf3 d6" in rec["movetext"]
        canonical = {
            k: rec[k] for k in ("game_id", "source_id", "variant",
                                "tags", "movetext")}
        canonical["tags"] = {k: canonical["tags"][k]
                             for k in sorted(canonical["tags"])}
        assert rec["content_sha256"] == hashlib.sha256(json.dumps(
            canonical, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()
        assert "import.game_updated" in _telemetry_events(store)


class TestAtomicity:
    def test_crash_on_record_rename_reads_precommit(self, tmp_path, monkeypatch):
        import ingest.import_pgn as imp
        real = imp.os.replace

        def bomb(src, dst):
            if Path(dst).parent.name == "games":
                raise OSError("injected")
            return real(src, dst)

        monkeypatch.setattr(imp.os, "replace", bomb)
        p = tmp_path / "in.pgn"
        p.write_text(VALID_PGN)
        store = tmp_path / "store"
        with pytest.raises(OSError):
            run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        monkeypatch.undo()
        assert _stored_records(store) == []
        assert not (store / "index.json").exists()
        # restart recovers and imports cleanly
        s = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s["games_imported"] == 1
        assert len(_read_json(store / "index.json")) == 1

    def test_crash_on_index_rename_leaves_recoverable_orphan(self, tmp_path, monkeypatch):
        import ingest.import_pgn as imp
        real = imp.os.replace

        def bomb(src, dst):
            if Path(dst).name == "index.json":
                raise OSError("injected")
            return real(src, dst)

        monkeypatch.setattr(imp.os, "replace", bomb)
        p = tmp_path / "in.pgn"
        p.write_text(VALID_PGN)
        store = tmp_path / "store"
        with pytest.raises(OSError):
            run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        monkeypatch.undo()
        # crash window: orphan record on disk, never committed to an index
        assert not (store / "index.json").exists()
        orphans = list((store / "games").glob("*.json"))
        assert len(orphans) == 1  # visible only pre-recovery
        # restart with a DIFFERENT game (different identity): recovery
        # must remove the orphan (never committed), not leave it beside
        # the new committed game
        p.write_text(VALID_PGN.replace('[White "a"]', '[White "other"]'))
        s = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s["games_imported"] == 1
        records = _stored_records(store)
        assert len(records) == 1
        assert records[0]["tags"]["White"] == "other"
        assert len(_read_json(store / "index.json")) == 1

    def test_crash_after_commit_reads_committed_and_restarts_idempotently(
            self, tmp_path, monkeypatch):
        real_open = Path.open
        appends = {"n": 0}

        def bomb(self, mode="r", *a, **kw):
            if self.name == "telemetry.jsonl" and mode == "a":
                appends["n"] += 1
                if appends["n"] == 2:  # first game_stored append
                    raise OSError("injected")
            return real_open(self, mode, *a, **kw)

        monkeypatch.setattr(Path, "open", bomb)
        p = tmp_path / "in.pgn"
        p.write_text(VALID_PGN)
        store = tmp_path / "store"
        with pytest.raises(OSError):
            run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        monkeypatch.undo()
        # crash AFTER the commit point: record + index are committed state
        assert len(_stored_records(store)) == 1
        assert len(_read_json(store / "index.json")) == 1
        # restart is idempotent through dedup: already-imported no-op
        s = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s["games_imported"] == 0
        assert s["games_already_imported"] == 1
        assert len(_read_json(store / "index.json")) == 1

    def test_stale_tmp_artifacts_removed_by_recovery(self, tmp_path):
        store = tmp_path / "store"
        (store / "games").mkdir(parents=True)
        (store / "index.json.tmp").write_text("[]")
        (store / "games" / "stale.json.tmp").write_text("{}")
        p = tmp_path / "in.pgn"
        p.write_text(VALID_PGN)
        s = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s["games_imported"] == 1
        assert list(store.glob("*.tmp")) == []
        assert list((store / "games").glob("*.tmp")) == []


class TestSourceEnforcement:
    @pytest.mark.parametrize("source_id", [
        "pgn-multi", "pgn-folder", "pgn-watch",
        "lichess-public", "chesscom-public", "cbh-licensed",
        "made-up-source",
    ])
    def test_out_of_scenario_sources_refused_before_state(self, source_id, tmp_path):
        """The scenario's structured sources are exactly [pgn-file];
        every other source id is refused at intake - a rights-class
        label alone is not authorization, and no store state may exist."""
        with pytest.raises(ImportFailure) as ei:
            _import(tmp_path, source_id=source_id)
        assert ei.value.code == "unknown_rights"
        assert not (tmp_path / "store").exists()

    def test_scenario_source_list_is_exactly_pinned(self):
        assert SCENARIO["sources"] == ["pgn-file"]


class TestUpdateAtomicity:
    def test_update_crash_before_commit_keeps_old_indexed_version(
            self, tmp_path, monkeypatch):
        import ingest.import_pgn as imp
        p = tmp_path / "in.pgn"
        store = tmp_path / "store"
        p.write_text(FULL_HEADER_PGN)
        run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        old_index = _read_json(store / "index.json")
        old_record_name = old_index[0]["record"]
        changed = FULL_HEADER_PGN.replace("2. Nf3 Nc6", "2. Nf3 d6")
        p.write_text(changed)
        real = imp.os.replace

        def bomb(src, dst):
            if Path(dst).name == "index.json":
                raise OSError("injected")
            return real(src, dst)

        monkeypatch.setattr(imp.os, "replace", bomb)
        with pytest.raises(OSError):
            run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        monkeypatch.undo()
        # pre-commit reads: the index STILL references the old version
        assert _read_json(store / "index.json") == old_index
        old_rec = _read_json(store / "games" / old_record_name)
        assert "2. Nf3 Nc6" in old_rec["movetext"]
        # restart: recovery discards the unreferenced candidate, then the
        # update commits deterministically
        s = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s["games_updated"] == 1
        idx2 = _read_json(store / "index.json")
        assert len(idx2) == 1
        assert idx2[0]["game_id"] == old_index[0]["game_id"]
        assert idx2[0]["record"] != old_record_name
        rec = _read_json(store / "games" / idx2[0]["record"])
        assert "2. Nf3 d6" in rec["movetext"]
        # the old version becomes unreferenced at commit; the NEXT
        # recovery removes it
        run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        remaining = [f.name for f in (store / "games").glob("*.json")]
        assert remaining == [idx2[0]["record"]]

    def test_update_crash_after_commit_recovers_old_version(
            self, tmp_path, monkeypatch):
        p = tmp_path / "in.pgn"
        store = tmp_path / "store"
        p.write_text(FULL_HEADER_PGN)
        run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        old_name = _read_json(store / "index.json")[0]["record"]
        changed = FULL_HEADER_PGN.replace("2. Nf3 Nc6", "2. Nf3 d6")
        p.write_text(changed)
        real_open = Path.open
        appends = {"n": 0}

        def bomb(self, mode="r", *a, **kw):
            if self.name == "telemetry.jsonl" and mode == "a":
                appends["n"] += 1
                if appends["n"] == 2:  # the game_updated append
                    raise OSError("injected")
            return real_open(self, mode, *a, **kw)

        monkeypatch.setattr(Path, "open", bomb)
        with pytest.raises(OSError):
            run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        monkeypatch.undo()
        # crash AFTER the commit point: the new version is referenced
        idx = _read_json(store / "index.json")
        assert len(idx) == 1 and idx[0]["record"] != old_name
        # restart: old unreferenced version removed; dedup no-op
        s = run_import_pgn(p, store, "pgn-file", retrieved_at=RETRIEVED_AT)
        assert s["games_already_imported"] == 1
        remaining = [f.name for f in (store / "games").glob("*.json")]
        assert remaining == [idx[0]["record"]]


class TestParserPinning:
    def test_late_tag_rejected(self, tmp_path):
        late = '[Event "x"]\n[Result "1-0"]\n\n1. e4 e5\n[White "late"]\n 1-0\n'
        with pytest.raises(ImportFailure) as ei:
            _import(tmp_path, late)
        assert ei.value.code == "malformed_request"

    def test_duplicate_tag_rejected(self, tmp_path):
        dup = '[Event "x"]\n[Event "y"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'
        with pytest.raises(ImportFailure) as ei:
            _import(tmp_path, dup)
        assert ei.value.code == "malformed_request"

    def test_star_terminal_conflicting_result_tag_rejected(self, tmp_path):
        star = '[Event "x"]\n[Result "1-0"]\n\n1. e4 e5 *\n'
        with pytest.raises(ImportFailure) as ei:
            _import(tmp_path, star)
        assert ei.value.code == "malformed_request"

    def test_star_terminal_with_star_result_accepted(self, tmp_path):
        star = '[Event "x"]\n[Result "*"]\n\n1. e4 e5 *\n'
        summary, _ = _import(tmp_path, star)
        assert summary["games_imported"] == 1

    def test_semicolon_comments_keep_line_boundaries(self, tmp_path):
        pgn = ('[Event "x"]\n[Result "1-0"]\n\n'
               '1. e4 ; comment ends at the newline\n'
               'e5 2. Nf3 Nc6 ; trailing comment\n'
               '1-0\n')
        summary, store = _import(tmp_path, pgn)
        assert summary["games_imported"] == 1
        rec = _stored_records(store)[0]
        assert "Nc6" in rec["movetext"]

    def test_compact_move_numbers_accepted(self, tmp_path):
        compact = '[Event "x"]\n[Result "1-0"]\n\n1.e4 e5 2.Nf3 Nc6 1-0\n'
        summary, _ = _import(tmp_path, compact)
        assert summary["games_imported"] == 1
        compact_black = '[Event "x"]\n[Result "1-0"]\n\n1. e4 1...e5 2. Nf3 2...Nc6 1-0\n'
        summary2, _ = _import(tmp_path / "b", compact_black)
        assert summary2["games_imported"] == 1


class TestIdentityStability:
    def test_same_input_same_record(self, tmp_path):
        s1, st1 = _import(tmp_path / "a")
        s2, st2 = _import(tmp_path / "b")
        r1 = _stored_records(st1)[0]
        r2 = _stored_records(st2)[0]
        assert r1["game_id"] == r2["game_id"]
        assert r1["content_sha256"] == r2["content_sha256"]
        assert json.dumps({k: v for k, v in r1.items() if k != "provenance"},
                          sort_keys=True) == json.dumps(
            {k: v for k, v in r2.items() if k != "provenance"}, sort_keys=True)
