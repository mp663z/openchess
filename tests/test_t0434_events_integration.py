"""T0434: integration and restart depth for the shipped file-backed
control-plane event outbox (server/control_plane_events.py). Distinct
from T0433's in-process fuzz/fault battery: here independent PROCESSES
open the same SQLite ledger, with clean exits, abrupt process loss,
two-handle contention, and raw persisted-row corruption.

Ground: data/contracts/events.yaml - ordered committed outbox, dedupe
by full envelope, whole-batch atomicity ("no partial batch
publication"), typed refusals that never echo caller content, replay
no-op across restart, and NO global order guarantee between
independent batches (only strict order within each committed batch).
Subprocess scripts are synthetic and self-contained with fixed
envelopes - no private values, no timing-dependent assertions: every
cross-process rendezvous uses signal files with bounded deadline
waits, and every subprocess has a bounded timeout.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from server import control_plane_events as events

ROOT = Path(__file__).resolve().parents[1]
SUBPROCESS_TIMEOUT = 30
WAIT_TIMEOUT = 10.0


def _row(op, outcome, identifier, *, timestamp=0, code=None):
    envelope = {
        "version": 1,
        "name": f"control_plane.{op}.{outcome}",
        "operation_id": op,
        "event_id": identifier,
        "occurred_at": timestamp,
        "correlation_id": f"corr_{identifier}",
        "outcome": outcome,
    }
    if code is not None:
        envelope["error_code"] = code
    return envelope


def _wait_for(path: Path, timeout: float = WAIT_TIMEOUT) -> bool:
    """Bounded rendezvous: poll for a signal file up to a hard
    deadline. Test correctness never depends on when it appears,
    only on it appearing at all."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return False


def _reap(children, grace: float = 5.0):
    """Bounded cleanup for Popen children: give each a short
    grace to exit on its own (after any released signal file),
    terminate survivors, escalate to kill on a bounded wait, and
    reap every child. Never leaves a subprocess behind."""
    for child in children:
        try:
            child.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=WAIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=WAIT_TIMEOUT)


def _run(script: str, *args: str, check: bool = True,
         timeout: int = SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True, text=True, cwd=ROOT, timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"child exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")
    return result


def _assert_state(ledger, expected):
    """Exact committed envelopes and sequence, plus exact dedupe
    state - never counts alone."""
    assert ledger.events == expected
    assert ledger.seen == {event["event_id"]: event
                           for event in expected}


def _assert_refusal(call, failure):
    with pytest.raises(events.Refusal) as caught:
        call()
    assert type(caught.value) is events.Refusal
    assert caught.value.failure_class == failure
    assert str(caught.value) == failure


BATCH_ONE = [_row("identity.login", "succeeded", "b1_first"),
             _row("quota.reserve", "failed", "b1_second",
                  code="quota_exhausted")]
BATCH_TWO = [_row("billing.get_subscription", "succeeded",
                  "b2_first"),
             _row("quota.release", "succeeded", "b2_second",
                  timestamp=7)]
BATCH_THREE = [_row("identity.logout", "failed", "b3_first",
                    code="auth_invalid")]

_PUBLISH_SCRIPT = """
import json
import sys
from server.control_plane_events import Ledger
from tests.test_t0434_events_integration import _row
path, batches = sys.argv[1], json.loads(sys.argv[2])
replay = json.loads(sys.argv[3])
ledger = Ledger(path)
if replay:
    committed = sum(ledger.publish(batch) for batch in replay)
for batch in batches:
    ledger.publish(batch)
print(json.dumps(ledger.events))
ledger.close()
"""


def test_independent_processes_extend_one_ledger(tmp_path):
    """Process A commits two batches and exits cleanly; process B
    reopens the same file, replays one batch (dedupe no-op),
    appends a third; the parent observes the exact envelope
    sequence and dedupe state."""
    path = tmp_path / "outbox.sqlite"
    first = _run(_PUBLISH_SCRIPT, str(path),
                 json.dumps([BATCH_ONE, BATCH_TWO]), "[]")
    assert json.loads(first.stdout) == BATCH_ONE + BATCH_TWO
    second = _run(_PUBLISH_SCRIPT, str(path),
                  json.dumps([BATCH_THREE]),
                  json.dumps([BATCH_ONE]))
    assert json.loads(second.stdout) == \
        BATCH_ONE + BATCH_TWO + BATCH_THREE
    _assert_state(events.Ledger(path),
                  BATCH_ONE + BATCH_TWO + BATCH_THREE)


_CRASH_SCRIPT = """
import os
import sys
from server.control_plane_events import Ledger
from tests.test_t0434_events_integration import _row
path = sys.argv[1]
ledger = Ledger(path)
signal = sys.argv[2]
# stage an uncommitted batch row by row, then die abruptly
import server.control_plane_events as events
rows = [dict(_row("identity.login", "succeeded", "crash_a")),
        dict(_row("identity.login", "succeeded", "crash_b"))]
ledger._db.execute("BEGIN IMMEDIATE")
for row in rows:
    ledger._db.execute(
        "INSERT INTO outbox(event_id, envelope) VALUES (?, ?)",
        (row["event_id"], events._encode(row)))
open(signal, "w").write("staged")
os.kill(os.getpid(), 9)
"""


def test_sigkill_mid_transaction_leaves_no_partial_rows(tmp_path):
    """Abrupt process loss after BEGIN and staged inserts but
    before COMMIT: the reopened ledger exposes EXACTLY the prior
    committed envelopes - no partially visible rows - and the
    same batch then commits whole in exact order."""
    path = tmp_path / "outbox.sqlite"
    signal = tmp_path / "staged"
    ledger = events.Ledger(path)
    ledger.publish([BATCH_ONE[0]])
    prior = ledger.events
    ledger.close()
    child = subprocess.Popen(
        [sys.executable, "-c", _CRASH_SCRIPT, str(path),
         str(signal)], cwd=ROOT)
    try:
        assert _wait_for(signal), "child never staged its batch"
        assert child.wait(timeout=SUBPROCESS_TIMEOUT) == -9
    finally:
        _reap([child])
    reopened = events.Ledger(path)
    _assert_state(reopened, prior)
    staged = [_row("identity.login", "succeeded", "crash_a"),
              _row("identity.login", "succeeded", "crash_b")]
    assert reopened.publish(staged) == 3
    _assert_state(reopened, prior + staged)
    reopened.close()
    _assert_state(events.Ledger(path), prior + staged)


_LOCK_HOLDER_SCRIPT = """
import sys
import time
from server.control_plane_events import Ledger
path, ready, release = sys.argv[1], sys.argv[2], sys.argv[3]
ledger = Ledger(path)
ledger._db.execute("BEGIN EXCLUSIVE")
open(ready, "w").write("locked")
deadline = time.monotonic() + 25
while not __import__("os").path.exists(release):
    if time.monotonic() > deadline:
        sys.exit(42)
    time.sleep(0.01)
ledger._db.execute("ROLLBACK")
ledger.close()
"""


def test_cross_process_lock_contention_typed_refusal_then_retry(
        tmp_path):
    """A second process holding an exclusive lock makes this
    process's publish a typed, non-echo publication_failure with
    no partial rows; after the lock releases, the SAME batch
    commits whole in exact order and replays as a no-op."""
    path = tmp_path / "outbox.sqlite"
    ready = tmp_path / "locked"
    release = tmp_path / "release"
    ledger = events.Ledger(path)
    ledger.publish([BATCH_ONE[0]])
    baseline = ledger.events
    ledger._db.execute("PRAGMA busy_timeout=0")
    holder = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER_SCRIPT, str(path),
         str(ready), str(release)], cwd=ROOT)
    try:
        assert _wait_for(ready), "lock holder never acquired"
        _assert_refusal(lambda: ledger.publish(BATCH_TWO),
                        "publication_failure")
        # an EXCLUSIVE lock blocks readers as well as writers;
        # the no-partial-rows proof comes after the release
    finally:
        release.write_text("go")
        _reap([holder])
    assert holder.wait(timeout=SUBPROCESS_TIMEOUT) == 0
    _assert_state(ledger, baseline)
    # retry with the same envelopes after contention clears
    deadline = time.monotonic() + WAIT_TIMEOUT
    while True:
        try:
            assert ledger.publish(BATCH_TWO) == 3
            break
        except events.Refusal:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)
    _assert_state(ledger, baseline + BATCH_TWO)
    assert ledger.publish(BATCH_TWO) == 3
    ledger.close()
    _assert_state(events.Ledger(path), baseline + BATCH_TWO)


def test_two_handles_one_process_contention_and_repair(tmp_path):
    """Two Ledger handles on the same file: the blocked handle
    earns a typed refusal while the other holds the write lock,
    then both handles converge on the exact same committed
    sequence and dedupe state."""
    path = tmp_path / "outbox.sqlite"
    first = events.Ledger(path)
    second = events.Ledger(path)
    first.publish([BATCH_ONE[0]])
    baseline = first.events
    second._db.execute("BEGIN EXCLUSIVE")
    first._db.execute("PRAGMA busy_timeout=0")
    try:
        _assert_refusal(lambda: first.publish(BATCH_TWO),
                        "publication_failure")
    finally:
        second._db.execute("ROLLBACK")
    assert first.publish(BATCH_TWO) == 3
    _assert_state(first, baseline + BATCH_TWO)
    _assert_state(second, baseline + BATCH_TWO)
    first.close()
    second.close()
    _assert_state(events.Ledger(path), baseline + BATCH_TWO)


def test_uncommitted_batch_rollback_across_restart(tmp_path):
    """fail_commit rolls the whole batch back; the file on disk
    and the reopened ledger both hold EXACTLY the baseline
    envelopes, and the batch then commits whole."""
    path = tmp_path / "outbox.sqlite"
    ledger = events.Ledger(path)
    ledger.publish([BATCH_ONE[0]])
    baseline = ledger.events
    _assert_refusal(
        lambda: ledger.publish(BATCH_TWO, fail_commit=True),
        "publication_failure")
    _assert_state(ledger, baseline)
    ledger.close()
    reopened = events.Ledger(path)
    _assert_state(reopened, baseline)
    assert reopened.publish(BATCH_TWO) == 3
    _assert_state(reopened, baseline + BATCH_TWO)
    reopened.close()


def test_replay_and_conflict_after_clean_restart(tmp_path):
    """After a clean restart the identical batch is a dedupe
    no-op; the same event_id with a different envelope is a
    typed, non-echo duplicate_conflict that leaves the exact
    committed sequence and dedupe state untouched."""
    path = tmp_path / "outbox.sqlite"
    ledger = events.Ledger(path)
    assert ledger.publish(BATCH_ONE + BATCH_TWO) == 4
    ledger.close()
    reopened = events.Ledger(path)
    expected = BATCH_ONE + BATCH_TWO
    assert reopened.publish(BATCH_ONE) == 4
    _assert_state(reopened, expected)
    conflict = {**BATCH_ONE[0], "occurred_at": 99}
    _assert_refusal(lambda: reopened.publish([conflict]),
                    "duplicate_conflict")
    assert "99" not in str(events.Refusal("duplicate_conflict"))
    _assert_state(reopened, expected)
    # a conflict staged anywhere in a batch rejects the WHOLE
    # batch, even the valid fresh rows around it
    mixed = [_row("identity.refresh", "succeeded", "fresh_a"),
             conflict,
             _row("identity.refresh", "succeeded", "fresh_b")]
    _assert_refusal(lambda: reopened.publish(mixed),
                    "duplicate_conflict")
    _assert_state(reopened, expected)
    reopened.close()
    _assert_state(events.Ledger(path), expected)


_RACE_SCRIPT = """
import json
import sys
import time
from server.control_plane_events import Ledger
path, ready, go = sys.argv[1], sys.argv[2], sys.argv[3]
batch = json.loads(sys.argv[4])
with open(ready, "w") as handle:
    handle.write("ready")
deadline = time.monotonic() + 25
import os
while not os.path.exists(go):
    if time.monotonic() > deadline:
        sys.exit(42)
    time.sleep(0.005)
ledger = Ledger(path)
ledger.publish(batch)
ledger.close()
"""


def test_concurrent_process_batches_keep_within_batch_order(
        tmp_path):
    """Two processes released at one barrier each publish a
    distinct batch. The contract gives NO global order between
    independent batches, so the committed sequence must be
    EXACTLY one of the two whole-batch concatenations - never an
    interleave - with within-batch order strict and the dedupe
    state the exact union."""
    path = tmp_path / "outbox.sqlite"
    go = tmp_path / "go"
    batch_a = [_row("identity.login", "succeeded", "race_a1"),
               _row("identity.refresh", "succeeded", "race_a2"),
               _row("identity.logout", "succeeded", "race_a3")]
    batch_b = [_row("quota.reserve", "succeeded", "race_b1"),
               _row("quota.commit", "succeeded", "race_b2"),
               _row("quota.release", "succeeded", "race_b3")]
    children = []
    try:
        for index, batch in enumerate((batch_a, batch_b)):
            children.append(subprocess.Popen(
                [sys.executable, "-c", _RACE_SCRIPT, str(path),
                 str(tmp_path / f"ready_{index}"), str(go),
                 json.dumps(batch)], cwd=ROOT))
        for index in range(2):
            assert _wait_for(tmp_path / f"ready_{index}"), \
                f"child {index} never reached the barrier"
        go.write_text("go")
        for child in children:
            assert child.wait(timeout=SUBPROCESS_TIMEOUT) == 0
    finally:
        go.write_text("go")
        _reap(children)
    ledger = events.Ledger(path)
    committed = ledger.events
    assert committed in (batch_a + batch_b, batch_b + batch_a)
    _assert_state(ledger, committed)
    ledger.close()


_CORRUPT_READER_SCRIPT = """
import sys
from server.control_plane_events import Ledger, Refusal
path = sys.argv[1]
ledger = Ledger(path)
for read in (lambda: ledger.events, lambda: ledger.seen):
    try:
        read()
    except Refusal as caught:
        assert type(caught) is Refusal
        assert caught.failure_class == "publication_failure"
        assert str(caught) == "publication_failure"
        assert not ledger._db.in_transaction
    else:
        sys.exit(43)
sys.exit(0)
"""


def test_malformed_persisted_row_fails_closed_across_processes(
        tmp_path):
    """A corrupted persisted envelope makes a DIFFERENT process
    fail closed on read: typed, non-echo publication_failure, no
    open transaction, no partial read - and the raw rows on disk
    stay byte-for-byte untouched."""
    path = tmp_path / "outbox.sqlite"
    ledger = events.Ledger(path)
    ledger.publish(BATCH_ONE)
    ledger.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE outbox SET envelope = ? WHERE event_id = ?",
            ("{corrupted-payload", "b1_second"))
        raw_before = list(connection.execute(
            "SELECT event_id, envelope FROM outbox"
            " ORDER BY ordinal"))
    result = _run(_CORRUPT_READER_SCRIPT, str(path),
                  check=False)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as connection:
        raw_after = list(connection.execute(
            "SELECT event_id, envelope FROM outbox"
            " ORDER BY ordinal"))
    assert raw_after == raw_before
    assert raw_after[1][1] == "{corrupted-payload"
