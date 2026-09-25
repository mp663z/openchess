"""T0559 UTF-8 import pure reference battery, with T0561 binding hook.

Malformed UTF-8 is refused before state per the import.yaml fail-closed rule.
Current streaming.py replacement decoding diverges and is not bound here.
"""
from __future__ import annotations

import codecs
from pathlib import Path

import pytest
import yaml

from tests.test_t0548_import_multi_pgn_contract import ReferenceStore, Refusal, reference_import

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/import.yaml").read_text())["contract"]
SCENARIO = next(s for s in CONTRACT["scenarios"]["entries"] if s["chain_task"] == "T0559")

UNICODE_GAME = ('[Event "Café 世界 ♟"]\n[Site "test"]\n[White "Zoë"]\n'
                '[Black "李"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n')


def reference_utf8(chunks, store, *, source_id="pgn-multi"):
    if source_id not in SCENARIO["sources"]:
        raise Refusal("unknown_rights")
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        text = "".join(decoder.decode(chunk) for chunk in chunks)
        text += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise Refusal("malformed_request") from exc
    return reference_import(text, store, source_id=source_id, scenario=SCENARIO)


# T0561 swaps only this binding to its shipped importer adapter.
PRODUCTION_BINDING = reference_utf8


def _chunks(data, size):
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_scenario_and_valid_utf8_contract():
    assert SCENARIO == {"id": "import-utf8", "chain_task": "T0559",
                        "sources": ["pgn-file", "pgn-multi"],
                        "visible_output": "import-summary",
                        "persisted_state": ["game-records", "provenance", "index-entries"],
                        "telemetry": ["import.started", "import.game_stored", "import.completed"],
                        "error_codes": ["malformed_request"]}


@pytest.mark.parametrize("source_id", SCENARIO["sources"])
@pytest.mark.parametrize("size", [1, 2, 3, 4, 7, 64, 1024])
def test_multibyte_boundaries_preserve_tags_identity_and_hash(source_id, size):
    data = UNICODE_GAME.encode("utf-8")
    store = ReferenceStore()
    summary = PRODUCTION_BINDING(_chunks(data, size), store, source_id=source_id)
    assert summary["kind"] == "import-summary" and summary["source_id"] == source_id
    assert summary["games_imported"] == 1
    assert store.telemetry == ["import.started", "import.game_stored", "import.completed"]
    assert len(store.index) == 1
    record = store.records[store.index[0]["record"]]
    assert record["tags"] == {"Event": "Café 世界 ♟", "Site": "test", "White": "Zoë",
                              "Black": "李", "Result": "1-0"}
    assert record["provenance"]["source_id"] == record["source_id"] == source_id
    assert "\ufffd" not in repr(record)


def test_chunking_does_not_change_stored_record_or_summary():
    data = UNICODE_GAME.encode("utf-8")
    baseline = ReferenceStore()
    first = PRODUCTION_BINDING([data], baseline)
    for size in (1, 3, 5, 11):
        next_store = ReferenceStore()
        assert PRODUCTION_BINDING(_chunks(data, size), next_store) == first
        assert next_store.index == baseline.index
        assert next_store.records == baseline.records
        assert next_store.telemetry == baseline.telemetry


def _replacement_mutant(chunks, store, *, source_id="pgn-multi"):
    text = b"".join(chunks).decode("utf-8", errors="replace")
    # This mutant replaces every non-ASCII character, including valid UTF-8.
    text = "".join(ch if ord(ch) < 128 else "\ufffd" for ch in text)
    return reference_import(text, store, source_id=source_id)


def _probe_preserved(binding):
    store = ReferenceStore()
    binding(_chunks(UNICODE_GAME.encode("utf-8"), 1), store)
    rec = store.records[store.index[0]["record"]]
    assert rec["tags"]["Event"] == "Café 世界 ♟"


def test_black_box_unicode_replacement_mutant_red():
    _probe_preserved(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        _probe_preserved(_replacement_mutant)


INVALID_BYTES = [
    ("invalid-lead", b"\xff"),
    ("lone-continuation", b"\x80"),
    ("c1-overlong-lead", b"\xc1\xbf"),
    ("above-unicode-max", b"\xf4\x90\x80\x80"),
    ("f5-lead", b"\xf5\x80\x80\x80"),
    ("truncated-at-eof", b"\xe2\x82"),
    ("overlong", b"\xc0\xaf"),
    ("encoded-surrogate", b"\xed\xa0\x80"),
    ("invalid-continuation-mid-stream", b"\xe2A\x82"),
]


@pytest.mark.parametrize("name,bad", INVALID_BYTES, ids=[c[0] for c in INVALID_BYTES])
@pytest.mark.parametrize("size", (1, 2, 4, 1024))
def test_invalid_utf8_refused_before_any_state(name, bad, size):
    # Place bad bytes in a tag value: replacement would leave valid PGN.
    data = UNICODE_GAME.encode("utf-8").replace(b"Caf\xc3\xa9", b"Caf" + bad)
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(_chunks(data, size), store)
    assert error.value.code == "malformed_request"
    assert store.index == [] and store.records == {}
    assert store.summary is None and store.telemetry == []


def test_invalid_sequence_crosses_chunk_boundary_and_refuses_atomically():
    prefix = UNICODE_GAME.encode("utf-8").split(b"Caf", 1)[0] + b"Caf"
    suffix = UNICODE_GAME.encode("utf-8").split(b"Caf\xc3\xa9", 1)[1]
    chunks = [prefix + b"\xe2", b"A", b"\x82" + suffix]
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(chunks, store)
    assert error.value.code == "malformed_request"
    assert store.index == [] and store.records == {} and store.summary is None
    assert store.telemetry == []


def _replace_invalid_mutant(chunks, store, *, source_id="pgn-multi"):
    text = b"".join(chunks).decode("utf-8", errors="replace")
    return reference_import(text, store, source_id=source_id)


def test_black_box_replacement_decoder_mutant_red():
    chunks = [UNICODE_GAME.encode("utf-8").replace(b"Caf\xc3\xa9", b"Caf\xff")]
    store = ReferenceStore()
    with pytest.raises(Refusal):
        PRODUCTION_BINDING(chunks, store)
    assert store.index == []
    mutant_store = ReferenceStore()
    assert _replace_invalid_mutant(chunks, mutant_store)["games_imported"] == 1
    assert mutant_store.index


@pytest.mark.parametrize("size", [1, 2, 3, 4, 7])
def test_invalid_utf8_in_second_game_refuses_whole_stream_before_state(size):
    from tests.test_t0537_import_pgn_contract import VALID_PGN

    first = VALID_PGN.encode("utf-8")
    second = UNICODE_GAME.encode("utf-8").replace(b"Caf\xc3\xa9", b"Caf\xff")
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(_chunks(first + b"\n" + second, size), store)
    assert error.value.code == "malformed_request"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None


def test_wrong_source_is_typed_unknown_rights_without_state():
    store = ReferenceStore()
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING([UNICODE_GAME.encode("utf-8")], store, source_id="lichess-public")
    assert error.value.code == "unknown_rights"
    assert store.index == [] and store.records == {} and store.telemetry == []
    assert store.summary is None


def _streaming_commit_mutant(chunks, store, *, source_id="pgn-multi"):
    from tests.test_t0548_import_multi_pgn_contract import _split

    text = b"".join(chunks)
    first = text.split(b"\n\n[", 1)[0].decode("utf-8")
    reference_import(first, store, source_id=source_id)
    remainder = text[len(first.encode("utf-8")):]
    # The candidate incorrectly commits before checking the later bytes.
    if remainder:
        remainder.decode("utf-8")
    return reference_import("".join(_split(first)), store, source_id=source_id)


def test_black_box_early_streaming_commit_mutant_red():
    from tests.test_t0537_import_pgn_contract import VALID_PGN

    data = VALID_PGN.encode() + b"\n" + UNICODE_GAME.encode().replace(
        b"Caf\xc3\xa9", b"Caf\xff")

    def probe(binding):
        store = ReferenceStore()
        with pytest.raises((Refusal, UnicodeDecodeError)):
            binding(_chunks(data, 2), store)
        assert store.index == [] and store.records == {} and store.telemetry == []
        assert store.summary is None

    probe(PRODUCTION_BINDING)
    with pytest.raises(AssertionError):
        probe(_streaming_commit_mutant)


@pytest.mark.parametrize("size", [1, 2, 4, 1024])
def test_true_truncated_utf8_after_valid_game_refuses_before_state(size):
    from tests.test_t0537_import_pgn_contract import VALID_PGN

    store = ReferenceStore()
    stream = VALID_PGN.encode("utf-8") + b"\xe2\x82"
    with pytest.raises(Refusal) as error:
        PRODUCTION_BINDING(_chunks(stream, size), store)
    assert error.value.code == "malformed_request"
    assert store.index == [] and store.records == {}
    assert store.telemetry == [] and store.summary is None


def _final_flush_deleted_mutant(chunks, store, *, source_id="pgn-multi"):
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    text = "".join(decoder.decode(chunk) for chunk in chunks)
    # Final decoder flush omitted: pending multibyte suffix never fails.
    return reference_import(text, store, source_id=source_id)


def test_final_flush_deletion_mutant_red():
    from tests.test_t0537_import_pgn_contract import VALID_PGN

    stream = VALID_PGN.encode("utf-8") + b"\xe2\x82"

    def probe(binding):
        store = ReferenceStore()
        with pytest.raises(Refusal):
            binding(_chunks(stream, 1), store)
        assert store.index == [] and store.records == {}
        assert store.telemetry == [] and store.summary is None

    probe(PRODUCTION_BINDING)
    mutant_store = ReferenceStore()
    assert _final_flush_deleted_mutant(_chunks(stream, 1), mutant_store)["games_imported"] == 1
    assert mutant_store.index and mutant_store.telemetry
