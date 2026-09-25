"""Production-only checks for the SQLite outbox boundary and safe emitter."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from server.control_plane_events import Ledger, Refusal, new_event, validate


def _event(event_id="synthetic_one"):
    return {
        "version": 1,
        "name": "control_plane.identity.login.succeeded",
        "operation_id": "identity.login",
        "event_id": event_id,
        "occurred_at": 0,
        "correlation_id": "synthetic_corr",
        "outcome": "succeeded",
    }


def test_process_restart_preserves_order_replay_and_conflict(tmp_path):
    path = tmp_path / "outbox.sqlite"
    source = """
import sys
from server.control_plane_events import Ledger
from tests.test_t0431_local_events import _event
ledger = Ledger(sys.argv[1])
assert ledger.publish([_event('synthetic_one'), _event('synthetic_two')]) == 2
assert ledger.publish([_event('synthetic_one')]) == 2
"""
    subprocess.run(
        [sys.executable, "-c", source, str(path)],
        check=True, cwd=Path(__file__).parents[1],
    )
    ledger = Ledger(path)
    assert ledger.events == [_event("synthetic_one"), _event("synthetic_two")]
    assert ledger.seen == {e["event_id"]: e for e in ledger.events}
    assert ledger.publish([_event("synthetic_two"), _event("synthetic_three")]) == 3
    with pytest.raises(Refusal, match="duplicate_conflict"):
        ledger.publish([dict(_event("synthetic_one"), occurred_at=1)])
    ledger.close()
    reopened = Ledger(path)
    assert reopened.events == [
        _event("synthetic_one"), _event("synthetic_two"), _event("synthetic_three")
    ]
    assert reopened.seen == {e["event_id"]: e for e in reopened.events}


def test_sqlite_insert_fault_rolls_back_all_rows(tmp_path):
    ledger = Ledger(tmp_path / "outbox.sqlite")
    assert ledger.publish([_event("before")]) == 1
    baseline = ledger.events
    ledger._db.execute("""
        CREATE TRIGGER reject_second BEFORE INSERT ON outbox
        WHEN NEW.event_id = 'second'
        BEGIN SELECT RAISE(FAIL, 'synthetic injected storage fault'); END
    """)
    with pytest.raises(Refusal) as caught:
        ledger.publish([_event("first"), _event("second")])
    assert caught.value.failure_class == "publication_failure"
    assert "synthetic injected" not in str(caught.value)
    assert ledger.events == baseline
    assert ledger.seen == {e["event_id"]: e for e in baseline}
    ledger.close()
    reopened = Ledger(tmp_path / "outbox.sqlite")
    assert reopened.events == baseline
    assert reopened.publish([_event("first")]) == 2


def test_fault_and_invalid_batch_do_not_touch_file(tmp_path):
    path = tmp_path / "outbox.sqlite"
    ledger = Ledger(path)
    ledger.publish([_event("before")])
    baseline = path.read_bytes()
    with pytest.raises(Refusal, match="publication_failure"):
        ledger.publish([_event("after")], fail_commit=True)
    with pytest.raises(Refusal, match="malformed_event"):
        ledger.publish([_event("after"), dict(_event("bad"), request_body="private")])
    assert ledger.events == [_event("before")]
    assert path.read_bytes() == baseline


def test_file_lock_failure_does_not_advance_outbox(tmp_path):
    path = tmp_path / "outbox.sqlite"
    ledger = Ledger(path)
    blocker = sqlite3.connect(path, isolation_level=None, timeout=0)
    blocker.execute("BEGIN EXCLUSIVE")
    ledger._db.execute("PRAGMA busy_timeout=0")
    try:
        with pytest.raises(Refusal, match="publication_failure"):
            ledger.publish([_event()])
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert ledger.events == []
    assert ledger.publish([_event()]) == 1


def test_safe_emitter_generates_independent_opaque_identifiers(monkeypatch):
    sequence = iter(("synthetic_random_event", "synthetic_random_correlation") * 2)
    monkeypatch.setattr(
        "server.control_plane_events.secrets.token_urlsafe", lambda _: next(sequence)
    )
    monkeypatch.setattr("server.control_plane_events.time.time_ns", lambda: 123_000_000)
    event = new_event("identity.login", "failed", error_code="auth_invalid")
    assert event == {
        **_event("synthetic_random_event"),
        "name": "control_plane.identity.login.failed",
        "occurred_at": 123,
        "correlation_id": "synthetic_random_correlation",
        "outcome": "failed",
        "error_code": "auth_invalid",
    }
    with pytest.raises(Refusal, match="malformed_event"):
        new_event("identity.login", "failed", error_code="quota_exhausted")
    assert json.loads(json.dumps(validate(event))) == event


def test_process_loss_during_uncommitted_transaction_keeps_prior_sequence(tmp_path):
    path = tmp_path / "outbox.sqlite"
    ledger = Ledger(path)
    assert ledger.publish([_event("prior")]) == 1
    ledger.close()
    source = """
import os
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute('BEGIN IMMEDIATE')
connection.execute('INSERT INTO outbox(event_id, envelope) VALUES (?, ?)',
                   ('uncommitted', '{}'))
os._exit(21)
"""
    result = subprocess.run(
        [sys.executable, "-c", source, str(path)], check=False,
        cwd=Path(__file__).parents[1],
    )
    assert result.returncode == 21
    reopened = Ledger(path)
    assert reopened.events == [_event("prior")]
    assert reopened.seen == {"prior": _event("prior")}
    assert reopened.publish([_event("uncommitted")]) == 2


def test_emitter_rejects_unsafe_types_before_formatting_or_token_generation(monkeypatch):
    class Hostile:
        def __str__(self):
            raise AssertionError("user code executed")

    def no_tokens(_):
        raise AssertionError("tokens generated before rejection")

    monkeypatch.setattr("server.control_plane_events.secrets.token_urlsafe", no_tokens)
    with pytest.raises(Refusal, match="unknown_name"):
        new_event(Hostile(), "succeeded")
    with pytest.raises(Refusal, match="malformed_event"):
        new_event("identity.login", Hostile())
    with pytest.raises(Refusal, match="malformed_event"):
        new_event("identity.login", "failed", error_code=Hostile())


@pytest.mark.parametrize("corruption", ["{broken", "{}", json.dumps({
    **_event("prior"), "request_body": "private", "occurred_at": 0,
}), json.dumps(_event("different_key"))])
def test_corrupt_file_refuses_read_and_publish_without_open_transaction(tmp_path, corruption):
    path = tmp_path / "outbox.sqlite"
    ledger = Ledger(path)
    ledger.publish([_event("prior")])
    ledger.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE outbox SET envelope = ? WHERE event_id = 'prior'", (corruption,))
    reopened = Ledger(path)
    for read in (lambda: reopened.events, lambda: reopened.seen,
                 lambda: reopened.publish([_event("safe_new")])):
        with pytest.raises(Refusal) as caught:
            read()
        assert caught.value.failure_class == "publication_failure"
        assert "private" not in str(caught.value)
        assert not reopened._db.in_transaction
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
        assert connection.execute("SELECT envelope FROM outbox").fetchone()[0] == corruption


def test_hydration_rollback_trigger_refuses_without_losing_prior_state(tmp_path):
    ledger = Ledger(tmp_path / "outbox.sqlite")
    ledger.publish([_event("prior")])
    ledger._db.execute("""
        CREATE TRIGGER rollback_insert BEFORE INSERT ON outbox
        WHEN NEW.event_id = 'bad'
        BEGIN SELECT RAISE(ROLLBACK, 'synthetic rollback trigger'); END
    """)
    with pytest.raises(Refusal) as caught:
        ledger.events = [_event("good"), _event("bad")]
    assert caught.value.failure_class == "publication_failure"
    assert "synthetic rollback" not in str(caught.value)
    assert not ledger._db.in_transaction
    assert ledger.events == [_event("prior")]
    ledger.close()
    assert Ledger(tmp_path / "outbox.sqlite").events == [_event("prior")]


def test_constructor_rejects_hostile_path_before_user_code():
    class Hostile:
        def __str__(self):
            raise AssertionError("hostile __str__ executed")

        def __fspath__(self):
            raise AssertionError("hostile __fspath__ executed")

    with pytest.raises(Refusal) as caught:
        Ledger(Hostile())
    assert caught.value.failure_class == "publication_failure"


@pytest.mark.parametrize("as_path", [False, True])
def test_constructor_nul_path_is_typed_and_non_echo(tmp_path, as_path):
    bad = str(tmp_path / "private\x00not-a-path.sqlite")
    with pytest.raises(Refusal) as caught:
        Ledger(Path(bad) if as_path else bad)
    assert caught.value.failure_class == "publication_failure"
    assert bad not in str(caught.value)
    assert "embedded null" not in str(caught.value)
