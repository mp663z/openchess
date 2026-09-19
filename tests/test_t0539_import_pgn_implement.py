"""T0539: Import/PGN/implement - the local-runnable import-pgn surface
(python -m ingest.import_pgn) drives the SHIPPED scenario runner and
renders the contract's visible output (import-summary JSON on stdout)
and error shape (contract error JSON on stderr), with exact exit codes
and persisted state checked on disk.

Expected and adjacent-negative behavior, all through the shipped CLI in
a subprocess; no test-local emulation of the runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "import_rights"
VALID_PGN = FIXTURE_DIR / "valid_user_game.pgn"
MULTI_PGN = FIXTURE_DIR / "multi_game.pgn"
RETRIEVED_AT = "2026-09-19T00:00:00Z"
SUMMARY_KEYS = ["kind", "source_id", "games_imported",
                "games_already_imported", "games_updated"]
ERROR_KEYS = ["code", "message", "retryable"]


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-m", "ingest.import_pgn", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def import_ok(pgn: Path, store: Path, **extra: str) -> dict:
    args = [str(pgn), "--store", str(store), "--retrieved-at", RETRIEVED_AT]
    for k, v in extra.items():
        args += [f"--{k.replace('_', '-')}", v]
    cp = run_cli(*args)
    assert cp.returncode == 0, cp.stderr
    assert cp.stderr == ""
    summary = json.loads(cp.stdout)
    assert list(summary) == SUMMARY_KEYS
    return summary


def store_state(store: Path) -> dict:
    games = sorted((store / "games").glob("*.json"))
    telemetry = ((store / "telemetry.jsonl").read_text().strip().splitlines()
                 if (store / "telemetry.jsonl").exists() else [])
    return {
        "games": games,
        "index": json.loads((store / "index.json").read_text())
        if (store / "index.json").exists() else None,
        "telemetry": [json.loads(line)["event"] for line in telemetry],
        "summary": json.loads((store / "summary.json").read_text())
        if (store / "summary.json").exists() else None,
    }


class TestExpectedBehavior:
    def test_import_success(self, tmp_path):
        store = tmp_path / "store"
        summary = import_ok(VALID_PGN, store)
        assert summary == {"kind": "import-summary", "source_id": "pgn-file",
                           "games_imported": 1, "games_already_imported": 0,
                           "games_updated": 0}
        state = store_state(store)
        assert len(state["games"]) == 1
        rec = json.loads(state["games"][0].read_text())
        assert rec["provenance"]["source_id"] == "pgn-file"
        assert rec["provenance"]["rights_class"] == "user-own"
        assert rec["provenance"]["retrieved_at"] == RETRIEVED_AT
        assert len(state["index"]) == 1
        assert state["telemetry"] == ["import.started", "import.game_stored",
                                      "import.completed"]
        # summary.json on disk is exactly the visible output on stdout
        assert state["summary"] == summary

    def test_multi_game_file(self, tmp_path):
        store = tmp_path / "store"
        summary = import_ok(MULTI_PGN, store)
        assert summary["games_imported"] == 2
        state = store_state(store)
        assert len(state["games"]) == 2
        assert state["telemetry"] == ["import.started", "import.game_stored",
                                      "import.game_stored", "import.completed"]

    def test_rerun_already_imported_noop(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        summary = import_ok(VALID_PGN, store)
        assert summary == {"kind": "import-summary", "source_id": "pgn-file",
                           "games_imported": 0, "games_already_imported": 1,
                           "games_updated": 0}
        state = store_state(store)
        assert len(state["games"]) == 1  # still exactly one record
        assert state["telemetry"] == [
            "import.started", "import.game_stored", "import.completed",
            "import.started", "import.game_duplicate", "import.completed"]

    def test_updated_recorded_replacement(self, tmp_path):
        store = tmp_path / "store"
        # update classification requires ALL six identity headers
        # (contract two-tier identity rule); the fixture bytes are
        # T0538-pinned, so build a full-header copy
        original = VALID_PGN.read_text().replace(
            '[Result "1-0"]',
            '[UTCDate "2026.09.19"]\n[Date "2026.09.19"]\n[Round "1"]\n'
            '[Result "1-0"]', 1)
        full = tmp_path / "full.pgn"
        full.write_text(original)
        import_ok(full, store)
        event_line = next(line for line in original.splitlines()
                          if line.startswith("[Event "))
        changed = original.replace(
            event_line, '[Event "Casual game (revised)"]', 1)
        assert changed != original
        variant = tmp_path / "variant.pgn"
        variant.write_text(changed)
        summary = import_ok(variant, store)
        assert summary["games_updated"] == 1
        assert summary["games_imported"] == 0
        state = store_state(store)
        assert len(state["index"]) == 1
        assert state["telemetry"][-3:] == ["import.started",
                                           "import.game_updated",
                                           "import.completed"]


class TestAdjacentNegatives:
    def test_malformed_pgn_refused(self, tmp_path):
        bad = tmp_path / "bad.pgn"
        bad.write_text("this is not a pgn at all\n")
        store = tmp_path / "store"
        cp = run_cli(str(bad), "--store", str(store),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert cp.stdout == ""
        err = json.loads(cp.stderr)
        assert list(err) == ERROR_KEYS
        assert err["code"] == "malformed_request"
        assert err["retryable"] is False
        # exact refused-import state: no game record, no index, no
        # summary; the pre-refusal import.started telemetry and the
        # empty games directory may persist (pinned, not overstated)
        assert not (store / "index.json").exists()
        assert not (store / "summary.json").exists()
        assert list((store / "games").glob("*.json")) == []
        assert store_state(store)["telemetry"] == ["import.started"]

    def test_unknown_source_refused(self, tmp_path):
        store = tmp_path / "store"
        cp = run_cli(str(VALID_PGN), "--store", str(store),
                     "--source", "not-a-source",
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        err = json.loads(cp.stderr)
        assert err["code"] == "unknown_rights"
        assert not store.exists()

    def test_out_of_scenario_source_refused(self, tmp_path):
        store = tmp_path / "store"
        cp = run_cli(str(VALID_PGN), "--store", str(store),
                     "--source", "lichess-public",
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert json.loads(cp.stderr)["code"] == "unknown_rights"
        assert not store.exists()

    def test_empty_source_refused(self, tmp_path):
        store = tmp_path / "store"
        cp = run_cli(str(VALID_PGN), "--store", str(store), "--source", "",
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert json.loads(cp.stderr)["code"] == "unknown_rights"
        assert not store.exists()

    @pytest.mark.parametrize("bad", ["", "   "], ids=["empty", "whitespace"])
    def test_blank_source_is_contract_refusal(self, tmp_path, bad):
        store = tmp_path / "store"
        cp = run_cli(str(VALID_PGN), "--store", str(store), "--source", bad,
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert json.loads(cp.stderr)["code"] == "unknown_rights"
        assert not store.exists()

    def test_missing_input_file_refused(self, tmp_path):
        store = tmp_path / "store"
        cp = run_cli(str(tmp_path / "does-not-exist.pgn"),
                     "--store", str(store), "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        err = json.loads(cp.stderr)
        assert err["code"] == "malformed_request"
        assert "unreadable" in err["message"]
        assert not store.exists()

    def test_directory_as_input_refused(self, tmp_path):
        store = tmp_path / "store"
        cp = run_cli(str(tmp_path), "--store", str(store),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert json.loads(cp.stderr)["code"] == "malformed_request"
        assert not store.exists()

    def test_no_importable_games_refused(self, tmp_path):
        empty = tmp_path / "empty.pgn"
        empty.write_text("")
        store = tmp_path / "store"
        cp = run_cli(str(empty), "--store", str(store),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert json.loads(cp.stderr)["code"] == "malformed_request"


class TestFilesystemErrors:
    """Expected local I/O failures surface as the exact contract error
    JSON on stderr with exit 1 - never a traceback."""

    def _assert_internal_json(self, cp):
        assert cp.returncode == 1
        assert cp.stdout == ""
        assert "Traceback" not in cp.stderr
        err = json.loads(cp.stderr)
        assert list(err) == ERROR_KEYS
        assert err["code"] == "internal"
        assert err["retryable"] is False

    def test_store_is_file(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        cp = run_cli(str(VALID_PGN), "--store", str(f),
                     "--retrieved-at", RETRIEVED_AT)
        self._assert_internal_json(cp)

    def test_store_parent_is_file(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        cp = run_cli(str(VALID_PGN), "--store", str(f / "child"),
                     "--retrieved-at", RETRIEVED_AT)
        self._assert_internal_json(cp)

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root ignores permission bits")
    def test_store_permission_denied(self, tmp_path):
        parent = tmp_path / "locked"
        parent.mkdir()
        parent.chmod(0o500)
        try:
            cp = run_cli(str(VALID_PGN), "--store", str(parent / "sub"),
                         "--retrieved-at", RETRIEVED_AT)
            self._assert_internal_json(cp)
        finally:
            parent.chmod(0o700)

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root ignores permission bits")
    def test_store_write_failure(self, tmp_path):
        store = tmp_path / "store"
        games = store / "games"
        games.mkdir(parents=True)
        games.chmod(0o500)
        try:
            cp = run_cli(str(VALID_PGN), "--store", str(store),
                         "--retrieved-at", RETRIEVED_AT)
            self._assert_internal_json(cp)
        finally:
            games.chmod(0o700)

    def test_malformed_existing_index_json(self, tmp_path):
        store = tmp_path / "store"
        (store / "games").mkdir(parents=True)
        (store / "index.json").write_text("{not json")
        cp = run_cli(str(VALID_PGN), "--store", str(store),
                     "--retrieved-at", RETRIEVED_AT)
        self._assert_internal_json(cp)

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root ignores permission bits")
    def test_input_unreadable_after_precheck(self, tmp_path):
        unreadable = tmp_path / "input.pgn"
        unreadable.write_text(VALID_PGN.read_text())
        unreadable.chmod(0o000)
        try:
            cp = run_cli(str(unreadable), "--store", str(tmp_path / "store"),
                         "--retrieved-at", RETRIEVED_AT)
            self._assert_internal_json(cp)
        finally:
            unreadable.chmod(0o600)


def _corrupt_committed_store(tmp_path, mutate):
    store = tmp_path / "store"
    import_ok(VALID_PGN, store)
    rec_path = next((store / "games").glob("*.json"))
    rec = json.loads(rec_path.read_text())
    mutate(rec, rec_path, store)
    original_index = (store / "index.json").read_text()
    cp = run_cli(str(VALID_PGN), "--store", str(store),
                 "--retrieved-at", RETRIEVED_AT)
    assert cp.returncode == 1
    assert cp.stdout == ""
    assert "Traceback" not in cp.stderr
    assert json.loads(cp.stderr)["code"] == "internal"
    assert (store / "index.json").read_text() == original_index


class TestExistingStoreManifest:
    """Strict existing-store manifest boundary (v3): valid-JSON but
    corrupt indexes refuse with the exact internal JSON envelope, no
    traceback, and the corrupt index is never overwritten."""

    def _refuse(self, tmp_path, index_payload, pre=None):
        store = tmp_path / "store"
        (store / "games").mkdir(parents=True)
        if pre is not None:
            pre(store)
        (store / "index.json").write_text(index_payload)
        original = (store / "index.json").read_text()
        cp = run_cli(str(VALID_PGN), "--store", str(store),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert cp.stdout == ""
        assert "Traceback" not in cp.stderr
        err = json.loads(cp.stderr)
        assert list(err) == ERROR_KEYS
        assert err["code"] == "internal"
        assert err["retryable"] is False
        assert "store manifest contradiction" in err["message"]
        # the corrupt index is never overwritten
        assert (store / "index.json").read_text() == original

    @pytest.mark.parametrize("payload", [
        "{\"x\": 1}", "[1]", '[{"game_id": "x"}]', '"x"', "1", "true",
        "null", "{}",
    ], ids=["map", "int-entry", "missing-key-entry", "string", "int",
            "bool", "null", "empty-map"])
    def test_wrong_root_or_entry_shapes(self, tmp_path, payload):
        self._refuse(tmp_path, payload)

    def test_entry_extra_key(self, tmp_path):
        self._refuse(tmp_path, '[{"game_id": "' + "a" * 16
                     + '", "record": "r.json", "extra": 1}]')

    def test_entry_key_order(self, tmp_path):
        self._refuse(tmp_path, '[{"record": "r.json", "game_id": "'
                     + "a" * 16 + '"}]')

    def test_game_id_wrong_type(self, tmp_path):
        self._refuse(tmp_path, '[{"game_id": 1, "record": "r.json"}]')

    def test_game_id_not_hex16(self, tmp_path):
        self._refuse(tmp_path, '[{"game_id": "notanidentifier", '
                     '"record": "r.json"}]')

    def test_record_path_traversal(self, tmp_path):
        gid = "a" * 16
        self._refuse(tmp_path, f'[{{"game_id": "{gid}", '
                     '"record": "../escape.json"}]')

    def test_record_name_wrong_prefix(self, tmp_path):
        rec = "b" * 16 + "--" + "c" * 16 + ".json"
        self._refuse(tmp_path, f'[{{"game_id": "{"a" * 16}", '
                     f'"record": "{rec}"}}]')

    def test_record_name_bad_hash(self, tmp_path):
        gid = "a" * 16
        self._refuse(tmp_path, f'[{{"game_id": "{gid}", '
                     f'"record": "{gid}--xyz.json"}}]')

    def test_duplicate_game_id(self, tmp_path):
        gid = "a" * 16
        self._refuse(tmp_path, json.dumps([
            {"game_id": gid, "record": gid + "--" + "c" * 16 + ".json"},
            {"game_id": gid, "record": gid + "--" + "d" * 16 + ".json"}]))

    def test_duplicate_record_name(self, tmp_path):
        rec = "a" * 16 + "--" + "c" * 16 + ".json"
        self._refuse(tmp_path, json.dumps([
            {"game_id": "a" * 16, "record": rec},
            {"game_id": "a" * 16, "record": rec}]))

    def test_referenced_record_file_missing(self, tmp_path):
        gid = "a" * 16
        rec = gid + "--" + "c" * 16 + ".json"
        self._refuse(tmp_path, json.dumps([{"game_id": gid, "record": rec}]))

    def _corrupt_committed_store(self, tmp_path, mutate):
        _corrupt_committed_store(tmp_path, mutate)

    def test_record_content_game_id_contradiction(self, tmp_path):
        def mutate(rec, rec_path, store):
            rec["game_id"] = "f" * 16
            rec_path.write_text(json.dumps(rec))
        self._corrupt_committed_store(tmp_path, mutate)

    def test_record_content_hash_contradiction(self, tmp_path):
        def mutate(rec, rec_path, store):
            rec["content_sha256"] = "0" * 64
            rec_path.write_text(json.dumps(rec))
        self._corrupt_committed_store(tmp_path, mutate)

    def test_record_file_not_json(self, tmp_path):
        def mutate(rec, rec_path, store):
            rec_path.write_text("{nope")
        self._corrupt_committed_store(tmp_path, mutate)

    def test_record_name_prefix_must_match_game_id(self, tmp_path):
        def mutate(rec, rec_path, store):
            # rename the committed record to a wrong-prefix (but
            # grammar-valid) name and point the manifest at it: the
            # file's content still matches the entry, so only the
            # prefix check can refuse this contradiction
            index = json.loads((store / "index.json").read_text())
            gid = index[0]["game_id"]
            hash16 = index[0]["record"].split("--", 1)[1]
            renamed = "0" * 16 + "--" + hash16
            rec_path.rename(store / "games" / renamed)
            (store / "index.json").write_text(
                json.dumps([{"game_id": gid, "record": renamed}]))
        self._corrupt_committed_store(tmp_path, mutate)

    def test_record_name_hash_suffix_must_match_verified_digest(self, tmp_path):
        def mutate(rec, rec_path, store):
            # rename to a grammar-valid name whose hash suffix is 16 hex
            # but differs from the verified full digest's prefix
            index = json.loads((store / "index.json").read_text())
            gid = index[0]["game_id"]
            correct = index[0]["record"].split("--", 1)[1]
            wrong = ("0" if correct[0] != "0" else "1") + correct[1:]
            renamed = f"{gid}--{wrong}"
            rec_path.rename(store / "games" / renamed)
            (store / "index.json").write_text(
                json.dumps([{"game_id": gid, "record": renamed}]))
        self._corrupt_committed_store(tmp_path, mutate)

    def test_empty_manifest_is_valid(self, tmp_path):
        store = tmp_path / "store"
        (store / "games").mkdir(parents=True)
        (store / "index.json").write_text("[]")
        summary = import_ok(VALID_PGN, store)
        assert summary["games_imported"] == 1


class TestRetrievedAtGrammar:
    @pytest.mark.parametrize("bad", [
        "not-a-date", "2026-09-19", "2026-09-19 00:00:00",
        "2026-13-19T00:00:00Z", "2026-09-19T25:00:00Z",
        "2026-09-19T00:00:00+02:00", "2026-9-19T0:00:00Z",
    ], ids=["garbage", "date-only", "space-separated", "bad-month",
            "bad-hour", "offset-not-z", "not-zero-padded"])
    def test_invalid_retrieved_at_usage_error(self, tmp_path, bad):
        store = tmp_path / "store"
        cp = run_cli(str(VALID_PGN), "--store", str(store),
                     "--retrieved-at", bad)
        assert cp.returncode == 2
        assert cp.stderr.startswith("usage: python -m ingest.import_pgn")
        assert not store.exists()

    def test_valid_retrieved_at_accepted(self, tmp_path):
        summary = import_ok(VALID_PGN, tmp_path / "store")
        assert summary["games_imported"] == 1


class TestUsageErrors:
    @pytest.mark.parametrize("args", [
        [],
        ["--help"],
        ["only-positional.pgn"],
        ["a.pgn", "b.pgn", "--store", "s"],
        ["a.pgn", "--store"],
        ["a.pgn", "--store", "s", "--bogus", "x"],
        ["a.pgn", "--store", "s", "--store", "t"],
        ["a.pgn", "--source", "pgn-file"],
        ["a.pgn", "--store", ""],
        ["a.pgn", "--store", "   "],
        ["a.pgn", "--store", "s", "--retrieved-at", ""],
        ["a.pgn", "--source", "--bogus"],
        ["a.pgn", "--store", "--source"],
        ["a.pgn", "--store", "s", "--retrieved-at", "--source"],
    ], ids=["no-args", "help", "missing-store", "two-positionals",
            "flag-missing-value", "unknown-option", "duplicate-option",
            "no-store-flag", "empty-store", "whitespace-store",
            "empty-retrieved-at", "source-value-looks-like-option",
            "store-value-looks-like-option",
            "retrieved-at-value-looks-like-option"])
    def test_usage_error_exit_2(self, args):
        cp = run_cli(*args)
        assert cp.returncode == 2
        assert cp.stdout == ""
        assert cp.stderr.startswith("usage: python -m ingest.import_pgn")
    def test_empty_store_creates_nothing_in_cwd(self, tmp_path):
        cp = run_cli("a.pgn", "--store", "", cwd=tmp_path)
        assert cp.returncode == 2
        assert list(tmp_path.iterdir()) == []
# --- one-defect stored-record mutators (T0539 stored-record contract) ---

def _drop_field(key):
    def m(rec):
        del rec[key]
    return m


def _m_extra_key(rec):
    rec["rogue"] = "x"


def _m_key_order(rec):
    items = list(rec.items())
    items[0], items[1] = items[1], items[0]
    rec.clear()
    rec.update(items)


def _m_game_id_type(rec):
    rec["game_id"] = 123


def _m_source_unknown(rec):
    rec["source_id"] = "no-such-source"
    rec["provenance"]["source_id"] = "no-such-source"


def _m_source_type(rec):
    rec["source_id"] = 7
    rec["provenance"]["source_id"] = 7


def _m_prov_source_mismatch(rec):
    rec["provenance"]["source_id"] = "lichess-cc0"


def _m_prov_rights_wrong(rec):
    rec["provenance"]["rights_class"] = "cc0"


def _m_prov_rights_type(rec):
    rec["provenance"]["rights_class"] = True


def _drop_prov(key):
    def m(rec):
        del rec["provenance"][key]
    return m


def _m_prov_extra(rec):
    rec["provenance"]["rogue"] = "x"


def _m_prov_order(rec):
    prov = rec["provenance"]
    items = list(prov.items())
    items[0], items[1] = items[1], items[0]
    rec["provenance"] = dict(items)


def _m_prov_empty(rec):
    rec["provenance"] = {}


def _m_prov_retrieved_bad(rec):
    rec["provenance"]["retrieved_at"] = "2026-01-01 00:00:00Z"


def _m_prov_retrieved_offset(rec):
    rec["provenance"]["retrieved_at"] = "2026-01-01T00:00:00+00:00"


def _m_prov_detail_empty(rec):
    rec["provenance"]["retrieval_detail"] = ""


def _m_prov_detail_type(rec):
    rec["provenance"]["retrieval_detail"] = 9


def _m_variant(rec):
    rec["variant"] = "Standard"


def _m_tags_list(rec):
    rec["tags"] = []


def _m_tags_value_type(rec):
    k = next(iter(rec["tags"]))
    rec["tags"][k] = 1


def _m_tags_value_changed(rec):
    k = next(iter(rec["tags"]))
    rec["tags"][k] = rec["tags"][k] + "-tampered"


def _m_movetext_type(rec):
    rec["movetext"] = 5


def _m_movetext_replaced(rec):
    rec["movetext"] = rec["movetext"] + " 99. Qz9"


def _m_imported_at_bad(rec):
    rec["imported_at"] = "yesterday"


def _m_hash_tail_hex(rec):
    rec["content_sha256"] = rec["content_sha256"][:16] + "f" * 48


def _m_hash_tail_nonhex(rec):
    rec["content_sha256"] = rec["content_sha256"][:16] + "z" * 48


def _m_hash_short(rec):
    rec["content_sha256"] = rec["content_sha256"][:63]


def _m_hash_upper(rec):
    rec["content_sha256"] = rec["content_sha256"].upper()


def _m_hash_type(rec):
    rec["content_sha256"] = 123


def _m_prov_detail_whitespace(rec):
    rec["provenance"]["retrieval_detail"] = "   "


def _m_movetext_whitespace(rec):
    rec["movetext"] = "  \n "


def _m_tag_key_newline(rec):
    items = list(rec["tags"].items())
    rec["tags"] = {"Ev\nent": items[0][1], **dict(items[1:])}


def _m_tag_value_newline(rec):
    k = next(iter(rec["tags"]))
    rec["tags"][k] = "a\nb"


RECORD_MUTATIONS = [
    _drop_field("game_id"), _drop_field("source_id"),
    _drop_field("provenance"), _drop_field("content_sha256"),
    _drop_field("variant"), _drop_field("tags"), _drop_field("movetext"),
    _drop_field("imported_at"),
    _m_extra_key, _m_key_order, _m_game_id_type,
    _m_source_unknown, _m_source_type, _m_prov_source_mismatch,
    _m_prov_rights_wrong, _m_prov_rights_type,
    _drop_prov("source_id"), _drop_prov("rights_class"),
    _drop_prov("retrieved_at"), _drop_prov("retrieval_detail"),
    _m_prov_extra, _m_prov_order, _m_prov_empty,
    _m_prov_retrieved_bad, _m_prov_retrieved_offset,
    _m_prov_detail_empty, _m_prov_detail_type,
    _m_variant, _m_tags_list, _m_tags_value_type, _m_tags_value_changed,
    _m_movetext_type, _m_movetext_replaced, _m_imported_at_bad,
    _m_hash_tail_hex, _m_hash_tail_nonhex, _m_hash_short, _m_hash_upper,
    _m_hash_type,
    _m_prov_detail_whitespace, _m_movetext_whitespace,
    _m_tag_key_newline, _m_tag_value_newline,
]


class TestStoredRecordContract:
    """One-defect mutations of a committed stored record: each must be
    refused with the internal envelope and a byte-identical store."""

    @pytest.mark.parametrize(
        "mutate", RECORD_MUTATIONS,
        ids=[m.__name__ for m in RECORD_MUTATIONS])
    def test_one_defect_record_mutation_refused(self, tmp_path, mutate):
        def apply(rec, rec_path, store):
            mutate(rec)
            rec_path.write_text(json.dumps(rec))
        _corrupt_committed_store(tmp_path, apply)
def _out_of_scenario_sources() -> list[tuple[str, str]]:
    import yaml
    doc = yaml.safe_load(
        (ROOT / "data" / "contracts" / "import.yaml").read_text())
    scenario = next(s for s in doc["contract"]["scenarios"]["entries"]
                    if s["id"] == "import-pgn")
    scenario_sources = set(scenario["sources"])
    return [(s["id"], s["rights_class"])
            for s in doc["contract"]["sources"]["entries"]
            if s["id"] not in scenario_sources]


class TestStoredRecordScenarioGate:
    """Stored records must pass the SAME scenario source restriction and
    shipped rights gate as the import path: a self-consistent record
    from another registry source - correct provenance, recomputed
    canonical digest, content-addressed filename, updated manifest - is
    still refused, byte-identical store."""

    @pytest.mark.parametrize(
        "source_id,rights_class", _out_of_scenario_sources(),
        ids=[s for s, _ in _out_of_scenario_sources()])
    def test_out_of_scenario_source_swap_refused(
            self, tmp_path, source_id, rights_class):
        from ingest.import_pgn import _canonical_record_bytes

        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        index_path = store / "index.json"
        index = json.loads(index_path.read_text())
        rec_path = store / "games" / index[0]["record"]
        rec = json.loads(rec_path.read_text())
        # fully self-consistent swap: source, provenance, rights class,
        # recomputed canonical digest, content-addressed filename
        rec["source_id"] = source_id
        rec["provenance"]["source_id"] = source_id
        rec["provenance"]["rights_class"] = rights_class
        rec["content_sha256"] = hashlib.sha256(
            _canonical_record_bytes(rec)).hexdigest()
        new_name = f"{rec['game_id']}--{rec['content_sha256'][:16]}.json"
        rec_path.write_text(json.dumps(rec))
        rec_path.rename(store / "games" / new_name)
        index[0]["record"] = new_name
        index_path.write_text(json.dumps(index))
        before = {p.name: p.read_bytes()
                  for p in (store / "games").glob("*.json")}
        cp = run_cli(str(VALID_PGN), "--store", str(store),
                     "--retrieved-at", RETRIEVED_AT)
        assert cp.returncode == 1
        assert cp.stdout == ""
        assert "Traceback" not in cp.stderr
        assert json.loads(cp.stderr)["code"] == "internal"
        assert index_path.read_text() == json.dumps(index)
        after = {p.name: p.read_bytes()
                 for p in (store / "games").glob("*.json")}
        assert after == before
def _self_consistent_mutate(store, mutate):
    """Mutate the committed record, then make the store fully
    self-consistent again: recomputed canonical digest, content-addressed
    filename, updated manifest. Only the specific semantic check under
    test can refuse the result."""
    from ingest.import_pgn import _canonical_record_bytes

    index_path = store / "index.json"
    index = json.loads(index_path.read_text())
    rec_path = store / "games" / index[0]["record"]
    rec = json.loads(rec_path.read_text())
    mutate(rec)
    rec["content_sha256"] = hashlib.sha256(
        _canonical_record_bytes(rec)).hexdigest()
    new_name = f"{rec['game_id']}--{rec['content_sha256'][:16]}.json"
    rec_path.write_text(json.dumps(rec))
    if new_name != rec_path.name:
        rec_path.rename(store / "games" / new_name)
    index[0]["record"] = new_name
    index_path.write_text(json.dumps(index))
    before = {p.name: p.read_bytes()
              for p in (store / "games").glob("*.json")}
    cp = run_cli(str(VALID_PGN), "--store", str(store),
                 "--retrieved-at", RETRIEVED_AT)
    assert cp.returncode == 1
    assert cp.stdout == ""
    assert "Traceback" not in cp.stderr
    assert json.loads(cp.stderr)["code"] == "internal"
    assert index_path.read_text() == json.dumps(index)
    after = {p.name: p.read_bytes()
             for p in (store / "games").glob("*.json")}
    assert after == before


class TestStoredRecordSelfConsistentMutations:
    """Self-consistent one-defect mutations (recomputed digest, renamed
    file, updated manifest): integrity mechanisms all pass, so only the
    semantic check under test can refuse."""

    def test_whitespace_movetext(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(store, lambda r: r.update(movetext=" \n "))

    def test_tag_key_newline(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        def m(r):
            items = list(r["tags"].items())
            r["tags"] = {"Ev\nent": items[0][1], **dict(items[1:])}
        _self_consistent_mutate(store, m)

    def test_tag_value_newline(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        def m(r):
            k = next(iter(r["tags"]))
            r["tags"][k] = "a\nb"
        _self_consistent_mutate(store, m)

    def test_whitespace_retrieval_detail(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r["provenance"].update(retrieval_detail="   "))

    def test_wrong_rights_class_label(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        def m(r):
            r["provenance"]["rights_class"] = "cc0"
        _self_consistent_mutate(store, m)
IDENTITY_HEADERS = ["Site", "White", "Black", "UTCDate", "Date", "Round"]


class TestStoredRecordSemanticValidation:
    """Self-consistent semantic mutations (recomputed digest, renamed
    file, updated manifest): the stored record must re-validate through
    the shipped parser and recompute its own identity - integrity since
    mutation is not semantic validity."""

    @pytest.mark.parametrize("header", IDENTITY_HEADERS)
    def test_identity_header_changed(self, tmp_path, header):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        def m(r):
            r["tags"][header] = "ZZZ-DIFFERENT"
        _self_consistent_mutate(store, m)

    def test_identity_header_emptied(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(store, lambda r: r["tags"].update(White=""))

    def test_result_tag_reversed(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        def m(r):
            r["tags"]["Result"] = {"1-0": "0-1", "0-1": "1-0"}.get(
                r["tags"].get("Result", "1-0"), "1/2-1/2")
        _self_consistent_mutate(store, m)

    def test_movetext_not_chess(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r.update(movetext="not chess at all"))

    def test_movetext_illegal_san(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r.update(
                movetext="1. e4 e5 2. Nf3 Nf6 3. Qh5 Qh4 0-1"))

    def test_movetext_truncated_no_result(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r.update(movetext="1. e4 e5 2. Nf3"))

    def test_tags_emptied(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(store, lambda r: r.update(tags={}))

    def test_variant_tag_unsupported(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r["tags"].update(Variant="Chess960"))

    def test_fen_tag_injected(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r["tags"].update(
                FEN="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w"))

    def test_nul_in_movetext(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r.update(movetext=r["movetext"] + " {\x00}"))

    def test_nul_in_tag_value(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        def m(r):
            r["tags"]["Event"] = r["tags"].get("Event", "x") + "\x00"
        _self_consistent_mutate(store, m)

    def test_nul_in_retrieval_detail(self, tmp_path):
        store = tmp_path / "store"
        import_ok(VALID_PGN, store)
        _self_consistent_mutate(
            store, lambda r: r["provenance"].update(
                retrieval_detail="/tmp/\x00.pgn"))
class TestIdentityCollisionSafety:
    """Two-tier identity rule (contract identity_rule): header-sparse
    distinct games never silently replace each other; true duplicates
    no-op; full-header games keep update classification."""

    def _write(self, tmp_path, name, games):
        pgn = tmp_path / name
        pgn.write_text("\n\n".join(games) + "\n")
        return pgn

    def test_two_distinct_headerless_games_never_replace(self, tmp_path):
        # the verifier's probe: no identity headers at all
        g1 = ('[Event "A"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n')
        g2 = ('[Event "B"]\n[Result "0-1"]\n\n1. d4 d5 0-1\n')
        store = tmp_path / "store"
        s1 = import_ok(self._write(tmp_path, "a.pgn", [g1]), store)
        assert s1["games_imported"] == 1
        s2 = import_ok(self._write(tmp_path, "b.pgn", [g2]), store)
        assert s2["games_imported"] == 1
        assert s2["games_updated"] == 0
        index = json.loads((store / "index.json").read_text())
        assert len(index) == 2
        assert len({e["game_id"] for e in index}) == 2
        assert len(list((store / "games").glob("*.json"))) == 2

    def test_partially_overlapping_headers_distinct(self, tmp_path):
        g1 = ('[White "a"]\n[Black "b"]\n[Result "1-0"]\n\n'
              '1. e4 e5 1-0\n')
        g2 = ('[White "a"]\n[Black "b"]\n[Result "1-0"]\n\n'
              '1. d4 d5 2. c4 1-0\n')
        store = tmp_path / "store"
        import_ok(self._write(tmp_path, "a.pgn", [g1]), store)
        s2 = import_ok(self._write(tmp_path, "b.pgn", [g2]), store)
        assert s2["games_imported"] == 1
        assert s2["games_updated"] == 0
        assert len(json.loads((store / "index.json").read_text())) == 2

    def test_full_headers_coinciding_is_same_game_slot(self, tmp_path):
        # pinned rule boundary: all six identity headers equal =>
        # same game slot by definition; changed content is an update
        headers = ('[Site "local"]\n[White "a"]\n[Black "b"]\n'
                   '[UTCDate "2026.09.19"]\n[Date "2026.09.19"]\n'
                   '[Round "1"]\n')
        g1 = headers + '[Result "1-0"]\n\n1. e4 e5 1-0\n'
        g2 = headers + '[Result "1-0"]\n\n1. d4 d5 2. c4 e6 1-0\n'
        store = tmp_path / "store"
        import_ok(self._write(tmp_path, "a.pgn", [g1]), store)
        s2 = import_ok(self._write(tmp_path, "b.pgn", [g2]), store)
        assert s2["games_updated"] == 1
        assert s2["games_imported"] == 0
        assert len(json.loads((store / "index.json").read_text())) == 1

    def test_sparse_game_edited_imports_as_new_game(self, tmp_path):
        g1 = ('[Event "A"]\n[White "a"]\n[Result "1-0"]\n\n'
              '1. e4 e5 1-0\n')
        g2 = ('[Event "A"]\n[White "a"]\n[Result "1-0"]\n\n'
              '1. e4 e5 2. Nf3 Nc6 1-0\n')
        store = tmp_path / "store"
        import_ok(self._write(tmp_path, "a.pgn", [g1]), store)
        s2 = import_ok(self._write(tmp_path, "b.pgn", [g2]), store)
        assert s2["games_imported"] == 1
        assert s2["games_updated"] == 0
        assert len(json.loads((store / "index.json").read_text())) == 2

    def test_sparse_true_duplicate_noop(self, tmp_path):
        g1 = ('[Event "A"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n')
        store = tmp_path / "store"
        import_ok(self._write(tmp_path, "a.pgn", [g1]), store)
        s2 = import_ok(self._write(tmp_path, "b.pgn", [g1]), store)
        assert s2["games_already_imported"] == 1
        assert s2["games_imported"] == 0
        assert len(json.loads((store / "index.json").read_text())) == 1

    def test_tag_order_normalization_same_identity(self, tmp_path):
        g1 = ('[Event "A"]\n[White "a"]\n[Result "1-0"]\n\n'
              '1. e4 e5 1-0\n')
        g2 = ('[White "a"]\n[Event "A"]\n[Result "1-0"]\n\n'
              '1. e4 e5 1-0\n')
        store = tmp_path / "store"
        import_ok(self._write(tmp_path, "a.pgn", [g1]), store)
        s2 = import_ok(self._write(tmp_path, "b.pgn", [g2]), store)
        assert s2["games_already_imported"] == 1
        assert len(json.loads((store / "index.json").read_text())) == 1
class TestIdentityEncodingInjectivity:
    """Header-tier preimage must be injective: no two distinct identity
    six-tuples may share a game_id. Delimiters, escapes, quotes,
    Unicode and whitespace moved across every adjacent field pair."""

    @staticmethod
    def _gid(fields):
        from ingest.import_pgn import game_identity
        tags = dict(zip(["Site", "White", "Black", "UTCDate", "Date",
                         "Round"], fields, strict=True))
        return game_identity(tags, "1. e4 e5 1-0")

    HEADERS = ["Site", "White", "Black", "UTCDate", "Date", "Round"]

    @pytest.mark.parametrize("boundary", range(5))
    @pytest.mark.parametrize("token", [
        "|", "\\", '"', "é", " ", "|\\", '"|', "\x00",
    ])
    def test_delimiter_moved_across_boundary(self, boundary, token):
        base = ["a", "b", "c", "d", "e", "f"]
        left = base[:]
        left[boundary] = base[boundary] + token
        # the SAME characters, attributed to the next field instead
        right = base[:]
        right[boundary + 1] = token + base[boundary + 1]
        assert left != right
        assert self._gid(left) != self._gid(right)

    def test_verifier_probe_pair_distinct(self, tmp_path):
        # the exact v7 probe: Site="a|b"/White="c" vs Site="a"/White="b|c"
        def game(site, white, ply):
            return (f'[Site "{site}"]\n[White "{white}"]\n[Black "d"]\n'
                    f'[UTCDate "e"]\n[Date "f"]\n[Round "g"]\n'
                    f'[Result "1-0"]\n\n{ply} 1-0\n')
        g1 = game("a|b", "c", "1. e4 e5")
        g2 = game("a", "b|c", "1. d4 d5 2. c4 e6")
        store = tmp_path / "store"
        p1 = tmp_path / "a.pgn"
        p1.write_text(g1)
        p2_ = tmp_path / "b.pgn"
        p2_.write_text(g2)
        s1 = import_ok(p1, store)
        assert s1["games_imported"] == 1
        s2 = import_ok(p2_, store)
        assert s2["games_imported"] == 1
        assert s2["games_updated"] == 0
        index = json.loads((store / "index.json").read_text())
        assert len(index) == 2
        assert len({e["game_id"] for e in index}) == 2
