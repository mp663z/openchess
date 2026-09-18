"""T2722: new pinned releases ingest idempotently and publish a delta manifest."""

import json

from ingest.incremental import IncrementalUpdater

PINS_V1 = [
    {"id": "2026-08", "url": "https://example/2026-08.pgn.zst", "sha256": "aaa", "bytes": 10},
    {"id": "2026-09", "url": "https://example/2026-09.pgn.zst", "sha256": "bbb", "bytes": 11},
]
PINS_V2 = PINS_V1 + [
    {"id": "2026-10", "url": "https://example/2026-10.pgn.zst", "sha256": "ccc", "bytes": 12}
]


def test_new_release_ingests_and_publishes_delta(tmp_path):
    up = IncrementalUpdater(tmp_path / "state.db")
    calls = []
    manifest = up.apply(PINS_V1, lambda pin: calls.append(pin["id"]) or {"rows": 100},
                        tmp_path / "delta.json")
    assert calls == ["2026-08", "2026-09"]
    assert manifest["counts"] == {"ingested": 2, "skipped_unchanged": 0, "conflicts": 0}
    on_disk = json.loads((tmp_path / "delta.json").read_text())
    assert [s["snapshot_id"] for s in on_disk["snapshots"]] == ["2026-08", "2026-09"]
    up.close()


def test_rerun_is_idempotent_no_reingest(tmp_path):
    up = IncrementalUpdater(tmp_path / "state.db")
    calls = []
    up.apply(PINS_V1, lambda pin: calls.append(pin["id"]) or {}, tmp_path / "d1.json")
    manifest = up.apply(PINS_V1, lambda pin: calls.append(pin["id"]) or {}, tmp_path / "d2.json")
    assert calls == ["2026-08", "2026-09"]  # no second ingest
    assert manifest["counts"] == {"ingested": 0, "skipped_unchanged": 2, "conflicts": 0}
    up.close()


def test_incremental_release_ingests_only_new(tmp_path):
    up = IncrementalUpdater(tmp_path / "state.db")
    calls = []
    ingest = lambda pin: calls.append(pin["id"]) or {}  # noqa: E731
    up.apply(PINS_V1, ingest, tmp_path / "d1.json")
    manifest = up.apply(PINS_V2, ingest, tmp_path / "d2.json")
    assert calls == ["2026-08", "2026-09", "2026-10"]
    assert manifest["counts"] == {"ingested": 1, "skipped_unchanged": 2, "conflicts": 0}
    up.close()


def test_changed_hash_is_conflict_not_ingested(tmp_path):
    up = IncrementalUpdater(tmp_path / "state.db")
    calls = []
    ingest = lambda pin: calls.append(pin["id"]) or {}  # noqa: E731
    up.apply(PINS_V1, ingest, tmp_path / "d1.json")
    tampered = [dict(PINS_V1[0], sha256="DIFFERENT")] + PINS_V1[1:]
    manifest = up.apply(tampered, ingest, tmp_path / "d2.json")
    assert calls == ["2026-08", "2026-09"]  # tampered snapshot NOT ingested
    assert manifest["counts"]["conflicts"] == 1
    conflict = [s for s in manifest["snapshots"] if s["action"] == "conflict_hash_changed"][0]
    assert conflict["snapshot_id"] == "2026-08" and "NOT ingested" in conflict["detail"]
    up.close()
