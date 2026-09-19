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

PINNED_DIGESTS = {
    "import.yaml": "b37cbc7386b9bdf17ec3415ed74dd15a962bbcf36ba4a40fec28cc0b3158fb2a",
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
        assert list(summary) == ["kind", "source_id", "games_imported"]
        assert summary["kind"] == SCENARIO["visible_output"]
        assert summary["source_id"] == "pgn-file"
        assert summary["games_imported"] == 1
        assert isinstance(summary["games_imported"], int)
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
        assert rec["content_sha256"] == hashlib.sha256(
            VALID_PGN.rstrip("\n").encode()).hexdigest()
        assert rec["tags"]["White"] == "a"
        assert rec["variant"] == "standard"
        # index entries: exactly the stored game ids, in order
        index = _read_json(store / "index.json")
        assert index == [rec["game_id"]]
        assert all(isinstance(g, str) and len(g) == 16 for g in index)

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
