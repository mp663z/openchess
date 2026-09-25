"""Seeded, contract-derived behavior checks for the shipped local event ledger.

This model deliberately has no dependency on the production validator or ledger.
T0433 owns fault/fuzz depth; T0434 owns integration and process restart depth.
"""

from __future__ import annotations

import copy
import itertools
import json
import random
from pathlib import Path

import pytest
import yaml

from server import control_plane_events as events

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "data/contracts/events.yaml").read_text())["contract"]
SOURCE = yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text())
CASES = json.loads((ROOT / "tests/fixtures/control-plane-events/cases.json").read_text())
ERRORS = {
    f"{area}.{name}": tuple(spec["errors"])
    for area, group in SOURCE["areas"].items()
    for name, spec in group["ops"].items()
}
CATALOG = CONTRACT["catalog"]["operation_ids"]
MAX_TIME = 253402300799999


def row(op, outcome, identifier, *, timestamp=0, code=None):
    result = {
        "version": 1,
        "name": f"control_plane.{op}.{outcome}",
        "operation_id": op,
        "event_id": identifier,
        "occurred_at": timestamp,
        "correlation_id": f"corr_{identifier}",
        "outcome": outcome,
    }
    if code is not None:
        result["error_code"] = code
    return result


def model_add(committed, batch):
    """Ordered, atomic dedupe by full envelope, not a call into production."""
    next_rows = copy.deepcopy(committed)
    by_id = {item["event_id"]: item for item in next_rows}
    for item in batch:
        key = item["event_id"]
        if key in by_id and by_id[key] != item:
            return None
        if key not in by_id:
            by_id[key] = copy.deepcopy(item)
            next_rows.append(by_id[key])
    return next_rows


def assert_state(ledger, expected):
    assert ledger.events == expected
    assert ledger.seen == {item["event_id"]: item for item in expected}
    # Exposed values must not be aliases to persisted state.
    observed = ledger.events
    if observed:
        observed[0]["occurred_at"] = -1
        assert ledger.events == expected


def assert_refusal(call, failure):
    with pytest.raises(events.Refusal) as caught:
        call()
    assert type(caught.value) is events.Refusal
    assert caught.value.failure_class == failure
    assert str(caught.value) == failure


def test_catalog_generated_envelopes_and_boundary_values():
    assert set(CATALOG) == set(ERRORS) and len(CATALOG) == 14
    assert len(CASES["happy"]) == 14 + sum(len(codes) for codes in ERRORS.values())
    for index, op in enumerate(CATALOG):
        for outcome, code in [("succeeded", None), *[("failed", c) for c in ERRORS[op]]]:
            for timestamp in (0, MAX_TIME):
                item = row(op, outcome, f"id_{index}_{outcome}_{code or 'ok'}", timestamp=timestamp,
                           code=code)
                input_before = copy.deepcopy(item)
                detached = events.validate(item)
                assert detached == input_before and detached is not item
                ledger = events.Ledger()
                try:
                    assert ledger.publish([item]) == 1
                    assert_state(ledger, [input_before])
                    item["occurred_at"] = -1
                    assert_state(ledger, [input_before])
                finally:
                    ledger.close()
    base = row(CATALOG[0], "succeeded", "id")
    for ident in ("A", "z", "0", "_", "-", "A" * 64):
        item = {**base, "event_id": ident, "correlation_id": ident}
        assert events.validate(item) == item
    for ident in ("", "A" * 65, "é", "x.y", "x\n", "user@example.com"):
        for field in ("event_id", "correlation_id"):
            assert_refusal(
                lambda field=field, ident=ident: events.validate({**base, field: ident}),
                "malformed_event",
            )
    for timestamp in (-1, MAX_TIME + 1, True, 0.0):
        assert_refusal(
            lambda timestamp=timestamp: events.validate({**base, "occurred_at": timestamp}),
            "malformed_event",
        )
    assert_refusal(lambda: events.validate({**base, "version": 2}), "unsupported_version")
    assert_refusal(lambda: events.validate({**base, "operation_id": "identity.nope"}),
                   "unknown_name")
    assert_refusal(lambda: events.validate({**base, "name": "control_plane.nope"}),
                   "unknown_name")
    assert_refusal(lambda: events.validate({**base, "request_body": "private"}),
                   "malformed_event")
    assert_refusal(lambda: events.validate({**base, "error_code": "internal"}),
                   "malformed_event")
    failed = row("identity.login", "failed", "id", code="auth_invalid")
    assert_refusal(lambda: events.validate({**failed, "error_code": "quota_exhausted"}),
                   "malformed_event")


@pytest.mark.parametrize("seed", [11, 37, 99, 2026])
def test_seeded_batch_groupings_permutations_replay_and_reopen(tmp_path, seed):
    rng = random.Random(seed)
    path = tmp_path / "ledger.sqlite"
    ledger = events.Ledger(path)
    expected = []
    pool = [row(op, "failed" if i % 2 else "succeeded", f"id_{i}",
                timestamp=rng.choice((0, MAX_TIME, rng.randrange(MAX_TIME + 1))),
                code=ERRORS[op][i % len(ERRORS[op])] if i % 2 else None)
            for i, op in enumerate(CATALOG)]
    # Each sequence has repeats within and across batches, with random cuts.
    for _ in range(12):
        batch = [copy.deepcopy(rng.choice(pool)) for _ in range(rng.randrange(0, 9))]
        before = copy.deepcopy(batch)
        next_expected = model_add(expected, batch)
        assert next_expected is not None
        assert ledger.publish(batch) == len(next_expected)
        expected = next_expected
        assert batch == before
        assert_state(ledger, expected)
        if rng.randrange(3) == 0:
            ledger.close()
            ledger = events.Ledger(path)
            assert_state(ledger, expected)
    ledger.close()
    assert_state(events.Ledger(path), expected)
    # Commuting groups affect first-seen order, never the deduped envelope set.
    for ordering in itertools.permutations(pool[:4]):
        fresh = events.Ledger()
        chunks = [ordering[:2], ordering[2:3], ordering[3:]]
        model = []
        for chunk in chunks:
            model = model_add(model, chunk)
            assert fresh.publish(list(chunk)) == len(model)
            assert_state(fresh, model)
        assert {e["event_id"]: e for e in fresh.events} == {
            e["event_id"]: e for e in pool[:4]
        }
        fresh.close()


@pytest.mark.parametrize("conflict_position", range(5))
def test_conflict_in_staged_and_durable_state_is_whole_batch_rollback(tmp_path, conflict_position):
    path = tmp_path / "ledger.sqlite"
    ledger = events.Ledger(path)
    prior = [row(CATALOG[0], "succeeded", "prior")]
    ledger.publish(prior)
    new = [row(CATALOG[1], "succeeded", f"new_{i}") for i in range(4)]
    conflict = {**prior[0], "occurred_at": 1}
    attempt = new[:conflict_position] + [conflict] + new[conflict_position:]
    snapshot = copy.deepcopy(attempt)
    assert model_add(prior, attempt) is None
    assert_refusal(lambda: ledger.publish(attempt), "duplicate_conflict")
    assert attempt == snapshot
    assert_state(ledger, prior)
    ledger.close()
    reopened = events.Ledger(path)
    assert_state(reopened, prior)
    internal = [new[0], *new[1:], {**new[0], "correlation_id": "other"}]
    assert model_add(prior, internal) is None
    assert_refusal(lambda: reopened.publish(internal), "duplicate_conflict")
    assert_state(reopened, prior)
    assert reopened.publish(new) == len(prior) + len(new)
    assert_state(reopened, prior + new)
    reopened.close()


class Hostile:
    calls = 0

    def __str__(self):
        Hostile.calls += 1
        raise AssertionError("called hostile str")

    def __repr__(self):
        Hostile.calls += 1
        raise AssertionError("called hostile repr")

    def __hash__(self):
        Hostile.calls += 1
        raise AssertionError("called hostile hash")

    def __eq__(self, other):
        Hostile.calls += 1
        raise AssertionError("called hostile eq")

    def __bool__(self):
        Hostile.calls += 1
        raise AssertionError("called hostile bool")


class HostileStr(str):
    def __hash__(self):
        Hostile.calls += 1
        raise AssertionError("called hostile hash")

    def __eq__(self, other):
        Hostile.calls += 1
        raise AssertionError("called hostile eq")


class DBSpy:
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.calls = []

    def execute(self, statement, *args):
        self.calls.append(statement)
        return self.wrapped.execute(statement, *args)

    def __getattr__(self, key):
        return getattr(self.wrapped, key)


def test_hostile_types_refuse_before_storage_or_generator_calls(monkeypatch, tmp_path):
    item = row("identity.login", "succeeded", "id")
    ledger = events.Ledger(tmp_path / "ledger.sqlite")
    spy = DBSpy(ledger._db)
    ledger._db = spy
    def no_token(_size):
        raise AssertionError("token generated")
    monkeypatch.setattr(events.secrets, "token_urlsafe", no_token)
    for field, bad, failure in [
        ("version", Hostile(), "malformed_event"),
        ("operation_id", Hostile(), "unknown_name"),
        ("name", Hostile(), "unknown_name"),
        ("outcome", Hostile(), "malformed_event"),
        ("event_id", Hostile(), "malformed_event"),
        ("correlation_id", Hostile(), "malformed_event"),
        ("occurred_at", Hostile(), "malformed_event"),
        ("error_code", Hostile(), "malformed_event"),
        ("event_id", HostileStr("id"), "malformed_event"),
    ]:
        candidate = {**item, field: bad}
        before = list(candidate.items())
        assert_refusal(lambda candidate=candidate: events.validate(candidate), failure)
        assert_refusal(lambda candidate=candidate: ledger.publish([item, candidate]), failure)
        assert list(candidate.items()) == before
        assert spy.calls == [] and Hostile.calls == 0
        assert_state(ledger, [])
        spy.calls.clear()  # assert_state is an authorized read, not publication
    # The fault-injection option is a separate public boundary. It must not
    # evaluate caller-owned truthiness inside an open transaction.
    for flag in (Hostile(), 1, None):
        assert_refusal(
            lambda flag=flag: ledger.publish([item], fail_commit=flag),
            "malformed_event",
        )
        assert spy.calls == [] and Hostile.calls == 0
    assert_state(ledger, [])
    spy.calls.clear()
    for batch in (Hostile(), (item,), {"row": item}):
        assert_refusal(lambda batch=batch: ledger.publish(batch), "malformed_event")
        assert spy.calls == [] and Hostile.calls == 0
    for op, outcome, code, failure in [
        (Hostile(), "succeeded", None, "unknown_name"),
        ("identity.login", Hostile(), None, "malformed_event"),
        ("identity.login", "failed", Hostile(), "malformed_event"),
    ]:
        assert_refusal(
            lambda op=op, outcome=outcome, code=code: events.new_event(
                op, outcome, error_code=code
            ),
            failure,
        )
        assert Hostile.calls == 0 and spy.calls == []
    ledger.close()


def test_hydration_boundary_rejects_hostile_values_before_sql_and_preserves_state(tmp_path):
    ledger = events.Ledger(tmp_path / "ledger.sqlite")
    prior = row("identity.login", "succeeded", "prior")
    ledger.publish([prior])
    spy = DBSpy(ledger._db)
    ledger._db = spy
    malformed = {**prior, "event_id": Hostile()}
    before = list(malformed.items())
    assert_refusal(lambda: setattr(ledger, "events", [prior, malformed]), "malformed_event")
    assert list(malformed.items()) == before
    assert spy.calls == [] and Hostile.calls == 0
    assert_state(ledger, [prior])
    spy.calls.clear()
    assert_refusal(lambda: setattr(ledger, "events", Hostile()), "malformed_event")
    assert spy.calls == [] and Hostile.calls == 0
    ledger.close()
    assert_state(events.Ledger(tmp_path / "ledger.sqlite"), [prior])
