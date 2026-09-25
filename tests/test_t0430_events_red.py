"""T0430: closed event corpus acceptance battery, ready for the T0431 publisher.

The single binding below is deliberately the T0428 *test reference* while there
is no shipped publisher. T0431 must replace this binding with its separate
production validate/refusal/ledger implementation. The fixture, assertions and
mutants must not be switched to that implementation's own oracle.

Contract public surface: validate(event) returns a detached envelope; Ledger()
exposes events (ordered committed sequence), seen (dedupe ledger), and
publish(batch, fail_commit=False) returning committed count. A restart builds
an empty Ledger then restores committed events and seen. No external sink here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from server import control_plane_events as _production
from tests import test_t0428_events_contract as _reference

PRODUCTION_BINDING = (_production.validate, _production.Ledger, _production.Refusal)
ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/control-plane-events/cases.json").read_text())
EVENTS = yaml.safe_load((ROOT / "data/contracts/events.yaml").read_text())["contract"]
SOURCE = yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text())
CATALOG = EVENTS["catalog"]["operation_ids"]
ERRORS = {
    f"{area}.{op}": set(spec["errors"])
    for area, group in SOURCE["areas"].items()
    for op, spec in group["ops"].items()
}
FIELDS = set(EVENTS["envelope"]["fields"])
SECRETS = (
    "1. e4 e5 2. Nf3 Nc6", "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQkq e3 0 1",
    "sk-live-9f8e7d6c5b4a-fixture", "hunter2-fixture", "user@example.com",
)


def _snapshot(value, seen=None):
    """Inspect built-in containers without invoking user-defined operators."""
    seen = {} if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return ("alias", seen[id(value)])
        seen[id(value)] = len(seen)
        if isinstance(value, dict):
            items = [(type(k).__name__, str.__str__(k) if isinstance(k, str) else "non-str",
                      _snapshot(v, seen)) for k, v in dict.items(value)]
        else:
            items = [_snapshot(v, seen) for v in list.__iter__(value)]
        return (type(value).__name__, items)
    if isinstance(value, str):
        return (type(value).__name__, str.__str__(value))
    if type(value) in (int, float, bool, type(None)):
        return (type(value).__name__, value)
    return (type(value).__name__, id(value))


def _refused(call, failure, refusal):
    with pytest.raises(refusal) as caught:
        call()
    assert type(caught.value) is refusal
    assert caught.value.failure_class == failure
    for secret in SECRETS:
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)


def _closed(event):
    """Independent, contract-derived expectation for accepted envelopes."""
    assert type(event) is dict and set(event) <= FIELDS
    assert set(FIELDS) - {"error_code"} <= set(event)
    assert type(event["version"]) is int and event["version"] == 1
    op = event["operation_id"]
    assert op in CATALOG and op in ERRORS and len(CATALOG) == 14
    assert event["outcome"] in ("succeeded", "failed")
    assert event["name"] == f"control_plane.{op}.{event['outcome']}"
    assert type(event["occurred_at"]) is int
    assert 0 <= event["occurred_at"] <= 253402300799999
    for field in ("event_id", "correlation_id"):
        ident = event[field]
        assert type(ident) is str and 1 <= len(ident) <= 64
        assert all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in ident
        )
    if event["outcome"] == "failed":
        assert event["error_code"] in ERRORS[op]
    else:
        assert "error_code" not in event


def _state(ledger):
    return (_snapshot(ledger.events), _snapshot(ledger.seen))


def _model_add(events, batch):
    """Fixture-only ordered dedupe oracle; never call publisher or reference."""
    ids = {event["event_id"] for event in events}
    for event in batch:
        if event["event_id"] not in ids:
            events.append(copy.deepcopy(event))
            ids.add(event["event_id"])


def _expected(events):
    return (_snapshot(events), _snapshot({e["event_id"]: e for e in events}))


def _restart(binding, ledger):
    _, ledger_type, _ = binding
    fresh = ledger_type()
    fresh.events = copy.deepcopy(ledger.events)
    fresh.seen = {event["event_id"]: event for event in fresh.events}
    return fresh


def _accepted(binding, event):
    validate, ledger_type, _ = binding
    _closed(event)
    original = copy.deepcopy(event)
    row = copy.deepcopy(event)
    output = validate(row)
    assert output == event and output is not row
    ledger = ledger_type()
    assert ledger.publish([row]) == 1
    assert _state(ledger) == _expected([event])
    assert ledger.events[0] is not row
    assert row == original and event == original
    assert ledger.publish([copy.deepcopy(event)]) == 1
    assert _state(ledger) == _expected([event])
    restored = _restart(binding, ledger)
    assert restored.publish([copy.deepcopy(event)]) == 1
    assert _state(restored) == _expected([event])
    assert _state(restored) == _state(ledger)


def _malformed(binding, case):
    validate, ledger_type, refusal = binding
    row = copy.deepcopy(case["event"])
    before = _snapshot(row)
    failure = case["expect_failure"]
    _refused(lambda: validate(row), failure, refusal)
    assert _snapshot(row) == before
    ledger = ledger_type()
    good = copy.deepcopy(CASES["happy"][0]["event"])
    batch = [good, row]
    _refused(lambda: ledger.publish(batch), failure, refusal)
    assert _state(ledger) == _state(ledger_type())
    assert _snapshot(row) == before
    _accepted(binding, case["minimal_repair"])


def _rollback(binding, case):
    _, ledger_type, refusal = binding
    ledger = ledger_type()
    model = []
    for batch in case["published"]:
        ledger.publish(copy.deepcopy(batch))
        _model_add(model, batch)
    assert _state(ledger) == _expected(model)
    if case["restart"]:
        ledger = _restart(binding, ledger)
    before = _state(ledger)
    attempt = copy.deepcopy(case["batch"])
    input_before = _snapshot(attempt)
    if case["expect_failure"] is None:
        assert ledger.publish(attempt, fail_commit=case["inject_fault"]) == case["expect_published"]
    else:
        _refused(
            lambda: ledger.publish(attempt, fail_commit=case["inject_fault"]),
            case["expect_failure"], refusal,
        )
        assert _state(ledger) == before, "failed batch changed sequence or dedupe ledger"
    if case["expect_failure"] is None:
        _model_add(model, case["batch"])
    assert _state(ledger) == _expected(model)
    assert len(ledger.events) == case["expect_published"]
    assert _snapshot(attempt) == input_before
    follow = copy.deepcopy(case["follow_up"])
    assert ledger.publish(follow) == case["expect_after_follow_up"]
    _model_add(model, case["follow_up"])
    assert _state(ledger) == _expected(model)
    assert _snapshot(follow) == _snapshot(case["follow_up"])
    for committed in ledger.events:
        _closed(committed)
        assert all(committed is not r for r in attempt)


def _matrix(binding):
    assert set(ERRORS) == set(CATALOG)
    assert len(CASES["happy"]) == 14 + sum(map(len, ERRORS.values())) == 84
    assert {r["name"] for r in CASES["happy"]} == {
        f"{op}-succeeded" for op in CATALOG
    } | {f"{op}-failed-{code}" for op, codes in ERRORS.items() for code in codes}
    for section in ("happy", "boundary"):
        for case in CASES[section]:
            _accepted(binding, case["event"])
    for case in CASES["malformed"]:
        _malformed(binding, case)
    for case in CASES["rollback"]:
        _rollback(binding, case)


class _Armed:
    active = False
    calls = []


def _trap(name):
    if _Armed.active:
        _Armed.calls.append(name)
        raise AssertionError(f"user code executed: {name}")


class EvilStr(str):
    def __eq__(self, other):
        _trap("eq")
        return str.__eq__(self, other)

    def __hash__(self):
        _trap("hash")
        return str.__hash__(self)

    def __repr__(self):
        _trap("repr")
        return str.__repr__(self)


class EvilDict(dict):
    def __iter__(self):
        _trap("dict iter")
        return dict.__iter__(self)

    def __getitem__(self, key):
        _trap("dict getitem")
        return dict.__getitem__(self, key)


class EvilList(list):
    def __iter__(self):
        _trap("list iter")
        return list.__iter__(self)


class EvilBatch:
    def __iter__(self):
        _trap("batch iter")
        return iter(())


def _hostile_variants(event):
    yield EvilDict(event), "malformed_event"
    for replacement in (None, 7, [event]):
        yield replacement, "malformed_event"
    for field in event:
        key = EvilStr(field)
        yield {key if k == field else k: v for k, v in event.items()}, "malformed_event"
        yield {7 if k == field else k: v for k, v in event.items()}, "malformed_event"
        value = event[field]
        if type(value) is str:
            failure = "unknown_name" if field in ("name", "operation_id") else "malformed_event"
            yield {**event, field: EvilStr(value)}, failure
            for bad in (None, 7, True, 1.0):
                yield {**event, field: bad}, failure
        elif type(value) is int:
            for bad in (True, float(value), str(value), int_subclass(value)):
                yield {**event, field: bad}, "malformed_event"


class int_subclass(int):
    pass


def _hostile_refusal(binding, row, failure, *, batch=False):
    validate, ledger_type, refusal = binding
    before = _snapshot(row)
    ledger = ledger_type()
    _Armed.calls = []
    _Armed.active = True
    try:
        if not batch:
            _refused(lambda: validate(row), failure, refusal)
        _refused(lambda: ledger.publish(row if batch else [row]), failure, refusal)
    finally:
        _Armed.active = False
    assert not _Armed.calls
    assert _snapshot(row) == before
    assert _state(ledger) == _state(ledger_type())
    if not batch:
        mixed = ledger_type()
        valid = CASES["happy"][0]["event"]
        _Armed.calls = []
        _Armed.active = True
        try:
            _refused(lambda: mixed.publish([valid, row]), failure, refusal)
        finally:
            _Armed.active = False
        assert not _Armed.calls
        assert _state(mixed) == _state(ledger_type())


def _hostile(binding):
    for section in ("happy", "boundary"):
        for case in CASES[section]:
            event = case["event"]
            for row, failure in _hostile_variants(event):
                _hostile_refusal(binding, row, failure)
            for batch in (EvilList([event]), tuple([event]), EvilBatch(), None, "bad"):
                _hostile_refusal(binding, batch, "malformed_event", batch=True)


def test_closed_corpus_binds_reference_until_t0431():
    _matrix(PRODUCTION_BINDING)
    _hostile(PRODUCTION_BINDING)


# Black-box behavioral mutants: the same corpus executes against each faulty
# binding. No fixture-shape or source-text assertions count as a mutant kill.
def _always_accept(row):
    return copy.deepcopy(row)


def _wrong_class(row):
    try:
        return _reference.validate(row)
    except _reference.Refusal:
        raise _reference.Refusal("malformed_event") from None


class _Dropped(_reference.Ledger):
    def publish(self, batch, *, fail_commit=False):
        return len(self.events)


class _ReplayAppend(_reference.Ledger):
    def publish(self, batch, *, fail_commit=False):
        result = super().publish(batch, fail_commit=fail_commit)
        if batch:
            self.events.append(copy.deepcopy(batch[0]))
        return result


class _PartialCommit(_reference.Ledger):
    def publish(self, batch, *, fail_commit=False):
        if (
            (fail_commit or (type(batch) is list and len(batch) > 1))
            and type(batch) is list and batch
            and type(batch[0]) is dict
        ):
            super().publish([batch[0]])
        return super().publish(batch, fail_commit=fail_commit)


class _NoConflict(_reference.Ledger):
    def publish(self, batch, *, fail_commit=False):
        try:
            return super().publish(batch, fail_commit=fail_commit)
        except _reference.Refusal as error:
            if error.failure_class == "duplicate_conflict":
                return len(self.events)
            raise


def _version_two(row):
    if type(row) is dict and row.get("version") == 2:
        return copy.deepcopy(row)
    return _reference.validate(row)


def _timestamp_too_high(row):
    if type(row) is dict and row.get("occurred_at") == 253402300800000:
        return copy.deepcopy(row)
    return _reference.validate(row)


def _undeclared_field(row):
    if type(row) is dict and "chess_content" in row:
        return copy.deepcopy(row)
    return _reference.validate(row)


def _wrong_error_scope(row):
    if type(row) is dict and row.get("error_code") == "quota_exhausted":
        return copy.deepcopy(row)
    return _reference.validate(row)


MUTANTS = {
    "permissive-validator": (_always_accept, _reference.Ledger, _reference.Refusal),
    "wrong-typed-error": (_wrong_class, _reference.Ledger, _reference.Refusal),
    "missing-publisher": (_reference.validate, _Dropped, _reference.Refusal),
    "replay-appends": (_reference.validate, _ReplayAppend, _reference.Refusal),
    "partial-commit": (_reference.validate, _PartialCommit, _reference.Refusal),
    "conflict-ignored": (_reference.validate, _NoConflict, _reference.Refusal),
    "version-two-accepted": (_version_two, _reference.Ledger, _reference.Refusal),
    "timestamp-over-ceiling": (_timestamp_too_high, _reference.Ledger, _reference.Refusal),
    "chess-content-leaked": (_undeclared_field, _reference.Ledger, _reference.Refusal),
    "error-scope-ignored": (_wrong_error_scope, _reference.Ledger, _reference.Refusal),
}


@pytest.mark.parametrize("name", MUTANTS)
def test_behavioral_mutants_turn_rows_red(name):
    binding = MUTANTS[name]
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        _matrix(binding)


def test_missing_production_binding_is_demonstrably_red():
    def not_implemented(_row):
        raise NotImplementedError("T0431 has not supplied a publisher")

    with pytest.raises(NotImplementedError):
        _matrix((not_implemented, _reference.Ledger, _reference.Refusal))


def test_equivalent_edits_keep_same_rows_green():
    # Wrapper delegation and outcome tuple order are behaviorally equivalent;
    # neither alters any envelope or committed sequence. No false mutant kill.
    def equivalent(row):
        return _reference.validate(row)

    _matrix((equivalent, _reference.Ledger, _reference.Refusal))
