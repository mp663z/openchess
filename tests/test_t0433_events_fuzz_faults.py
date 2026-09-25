"""T0433: deterministic bounded fuzz and injected-fault depth for the shipped
local event publisher (server.control_plane_events). Hosted send, OAuth and
sync are out of scope; T0434 owns deeper process integration/restart work, so
this battery stays inside one process plus file reopen.

Ground: data/contracts/events.yaml (closed envelope, pinned catalog, refusal
classes, whole-batch atomicity, ordered outbox + dedupe replay), the closed
T0429 fixture, tests/test_t0430_events_red.py, the T0431 production tests and
the T0432 independent model. Every loop is bounded and seeded; mutant kills
are classified only where a harness measures them.
"""

from __future__ import annotations

import copy
import random
import sqlite3
import string
from pathlib import Path

import pytest
import yaml

from server import control_plane_events as events

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load(
    (ROOT / "data/contracts/events.yaml").read_text())["contract"]
SOURCE = yaml.safe_load(
    (ROOT / "data/contracts/control-plane.yaml").read_text())
ERRORS = {
    f"{area}.{name}": tuple(spec["errors"])
    for area, group in SOURCE["areas"].items()
    for name, spec in group["ops"].items()
}
CATALOG = CONTRACT["catalog"]["operation_ids"]
MAX_TIME = 253402300799999
SEEDS = (7, 42, 1337, 90210)
IDENT_ALPHABET = string.ascii_letters + string.digits + "_-" + \
    " .@/\n"


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


def assert_refusal(call, *classes):
    """A typed, non-echo refusal of one of the expected classes."""
    assert classes
    with pytest.raises(events.Refusal) as caught:
        call()
    assert type(caught.value) is events.Refusal
    assert caught.value.failure_class in classes
    assert str(caught.value) == caught.value.failure_class
    return caught.value


def assert_state(ledger, expected):
    assert ledger.events == expected
    assert ledger.seen == {item["event_id"]: item
                           for item in expected}


class DBSpy:
    """Records every storage statement reaching the connection."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.calls = []

    def execute(self, statement, *args):
        self.calls.append(statement)
        return self.wrapped.execute(statement, *args)

    def __getattr__(self, key):
        return getattr(self.wrapped, key)


class FaultDB(DBSpy):
    """Injects a synthetic sqlite fault at one execute index -
    never at ROLLBACK, so the production rollback path runs."""

    def __init__(self, wrapped, fail_at):
        super().__init__(wrapped)
        self.fail_at = fail_at

    def execute(self, statement, *args):
        if len(self.calls) == self.fail_at and \
                not statement.startswith("ROLLBACK"):
            self.calls.append(statement)
            raise sqlite3.OperationalError(
                f"synthetic injected fault at {self.fail_at}")
        return super().execute(statement, *args)


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


# -- the closed mutation library ----------------------------------------------
# (label, mutate(envelope, rng), expected refusal class). Every mutation is a
# contract-pinned defect; combinations assert membership, never a pinned
# precedence, because the contract declares classes, not ordering.
PROHIBITED = ("request_body", "response_body", "email",
              "account_id", "notes", "credentials")


def _mut_extra_field(event, rng):
    field = rng.choice(PROHIBITED)
    event[field] = "private-payload"
    return f"extra-{field}"


def _mut_drop_field(event, rng):
    field = rng.choice(
        ["version", "name", "operation_id", "event_id",
         "occurred_at", "correlation_id", "outcome"])
    del event[field]
    return f"dropped-{field}"


def _mut_version_value(event, rng):
    event["version"] = rng.choice([0, 2, -1, 99])
    return "version-value"


def _mut_version_type(event, rng):
    event["version"] = rng.choice(
        [True, "1", 1.0, None, Hostile()])
    return "version-type"


def _mut_op_unknown(event, rng):
    event["operation_id"] = rng.choice([
        "identity.nope", "identity.logins", "Identity.login",
        "identity/login", "identity.login.extra", "",
        "quota", "billing.create_checkout "])
    # keep the envelope internally consistent otherwise: name
    # still references the ORIGINAL op, so the name check can
    # only add unknown_name
    return "op-unknown"


def _mut_op_type(event, rng):
    event["operation_id"] = rng.choice([Hostile(), 1, None])
    return "op-type"


def _mut_outcome_value(event, rng):
    event["outcome"] = rng.choice(
        ["success", "FAILED", "Succeeded", "ok", ""])
    return "outcome-value"


def _mut_outcome_type(event, rng):
    event["outcome"] = rng.choice([Hostile(), 1, None, True])
    return "outcome-type"


def _mut_name_op(event, rng):
    other = rng.choice(
        [op for op in CATALOG
         if op != event["operation_id"]])
    event["name"] = \
        f"control_plane.{other}.{event['outcome']}"
    return "name-other-op"


def _mut_name_outcome(event, rng):
    flipped = {"succeeded": "failed",
               "failed": "succeeded"}[event["outcome"]]
    event["name"] = \
        f"control_plane.{event['operation_id']}.{flipped}"
    return "name-flipped-outcome"


def _mut_name_shape(event, rng):
    event["name"] = rng.choice([
        "control_plane.identity.login",
        f"control_plane.{event['operation_id']}",
        "controlplane.identity.login.succeeded",
        "control_plane.identity.login.succeeded.extra",
        "Control_plane.identity.login.succeeded"])
    return "name-shape"


def _mut_name_type(event, rng):
    event["name"] = rng.choice([Hostile(), 1, None])
    return "name-type"


def _mut_ident_value(field):
    def mutate(event, rng):
        event[field] = rng.choice([
            "", "A" * 65, "x.y", "x\n", "user@example.com",
            "has space", "api.key", "é", "a/b"])
        return f"{field}-grammar"
    return mutate


def _mut_ident_type(field):
    def mutate(event, rng):
        event[field] = rng.choice([Hostile(), 1, None, True])
        return f"{field}-type"
    return mutate


def _mut_time_range(event, rng):
    event["occurred_at"] = rng.choice([-1, MAX_TIME + 1, -99999])
    return "time-range"


def _mut_time_type(event, rng):
    event["occurred_at"] = rng.choice(
        [True, 0.0, "0", None, Hostile()])
    return "time-type"


def _mut_error_scope(event, rng):
    assert event["outcome"] == "failed"
    op = event["operation_id"]
    foreign = [code for other in CATALOG if other != op
               for code in ERRORS[other]
               if code not in ERRORS[op]]
    event["error_code"] = rng.choice(foreign)
    return "error-scope"


def _mut_error_drop(event, rng):
    assert event["outcome"] == "failed"
    del event["error_code"]
    return "error-dropped"


def _mut_error_present(event, rng):
    assert event["outcome"] == "succeeded"
    event["error_code"] = "internal"
    return "error-on-success"


def _mut_error_type(event, rng):
    assert event["outcome"] == "failed"
    event["error_code"] = rng.choice([Hostile(), 1, None])
    return "error-type"


MUTATIONS = [
    (_mut_extra_field, "malformed_event"),
    (_mut_drop_field, "malformed_event"),
    (_mut_version_value, "unsupported_version"),
    (_mut_version_type, "malformed_event"),
    (_mut_op_unknown, "unknown_name"),
    (_mut_op_type, "unknown_name"),
    (_mut_outcome_value, "malformed_event"),
    (_mut_outcome_type, "malformed_event"),
    (_mut_name_op, "unknown_name"),
    (_mut_name_outcome, "unknown_name"),
    (_mut_name_shape, "unknown_name"),
    (_mut_name_type, "unknown_name"),
    (_mut_ident_value("event_id"), "malformed_event"),
    (_mut_ident_value("correlation_id"), "malformed_event"),
    (_mut_ident_type("event_id"), "malformed_event"),
    (_mut_ident_type("correlation_id"), "malformed_event"),
    (_mut_time_range, "malformed_event"),
    (_mut_time_type, "malformed_event"),
    (_mut_error_scope, "malformed_event"),
    (_mut_error_drop, "malformed_event"),
    (_mut_error_present, "malformed_event"),
    (_mut_error_type, "malformed_event"),
]


def _safe_snapshot(mapping):
    """Type- and value-exact comparison snapshot that never
    invokes caller-defined operators: plain scalars compare by
    value; anything else pins its exact type name only."""
    return [(key, type(value).__name__,
             value if type(value) in (int, str, bool, float,
                                      type(None)) else None)
            for key, value in mapping.items()]


def _random_valid(rng):
    op = rng.choice(CATALOG)
    if rng.randrange(2):
        return row(op, "failed", f"id_{rng.randrange(10**6)}",
                   timestamp=rng.choice(
                       [0, MAX_TIME,
                        rng.randrange(MAX_TIME + 1)]),
                   code=rng.choice(ERRORS[op]))
    return row(op, "succeeded", f"id_{rng.randrange(10**6)}",
               timestamp=rng.choice(
                   [0, MAX_TIME, rng.randrange(MAX_TIME + 1)]))


def _applicable(mutation, event):
    label_probe = copy.deepcopy(event)
    try:
        mutation(label_probe, random.Random(0))
    except Exception:
        return False
    return True


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_single_mutations_typed_refusal(seed):
    """Bounded single-defect fuzz: every mutated envelope earns
    EXACTLY its contract-pinned refusal class, typed and
    non-echo, with the caller's dict untouched and zero storage
    statements issued before full validation."""
    rng = random.Random(seed)
    ledger = events.Ledger()
    spy = DBSpy(ledger._db)
    ledger._db = spy
    try:
        for _ in range(30):
            event = _random_valid(rng)
            mutation, failure = rng.choice(MUTATIONS)
            if not _applicable(mutation, event):
                continue
            mutated = copy.deepcopy(event)
            label = mutation(mutated, rng)
            assert _safe_snapshot(mutated) != \
                _safe_snapshot(event), label
            before = _safe_snapshot(mutated)
            assert_refusal(
                lambda mutated=mutated: events.validate(
                    mutated), failure)
            assert _safe_snapshot(mutated) == before
            # the same defect inside a batch still refuses before
            # any storage call
            anchor = row("identity.login", "succeeded",
                         f"anchor_{seed}")
            assert_refusal(
                lambda anchor=anchor, mutated=mutated:
                    ledger.publish([anchor, mutated]),
                failure)
            assert spy.calls == [], label
            assert_state(ledger, [])
            spy.calls.clear()  # authorized reads, not publication
            Hostile.calls = 0
    finally:
        ledger.close()


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_mutation_combinations_fail_closed(seed):
    """Bounded 2-3 defect combinations: the refusal is typed,
    non-echo and one of the constituent defect classes; the
    envelope is never silently repaired or partially accepted."""
    rng = random.Random(seed)
    for _ in range(40):
        event = _random_valid(rng)
        picks = rng.sample(MUTATIONS, rng.choice([2, 2, 3]))
        mutated = copy.deepcopy(event)
        classes = set()
        applied = 0
        for mutation, failure in picks:
            if _applicable(mutation, mutated):
                mutation(mutated, rng)
                classes.add(failure)
                applied += 1
        if not applied or _safe_snapshot(mutated) == \
                _safe_snapshot(event):
            continue
        before = _safe_snapshot(mutated)
        caught = assert_refusal(
            lambda mutated=mutated: events.validate(mutated),
            *classes)
        assert caught.failure_class in events_refusal_classes()
        assert _safe_snapshot(mutated) == before


def events_refusal_classes():
    return set(CONTRACT["failure"]["classes"])


_IDENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-")


def _contract_identifier(value):
    """Independent restatement of the contract's identifier
    grammar - ASCII [A-Za-z0-9_-]{1,64} per events.yaml -
    deliberately NOT the production regex, so a production
    grammar drift cannot drag the oracle along with it."""
    return (type(value) is str
            and 1 <= len(value) <= 64
            and value.isascii()
            and all(char in _IDENT_CHARS for char in value))


def test_contract_identifier_predicate_edges():
    """The independent predicate is pinned against the contract
    grammar (events.yaml: ASCII [A-Za-z0-9_-]{1,64}) with
    explicit edges before any fuzz comparison trusts it."""
    for good in ("A", "z", "0", "_", "-", "A" * 64,
                 "aB09_-", "x" * 64):
        assert _contract_identifier(good)
    for bad in ("", "A" * 65, "x.y", "\u00e9", "x\n",
                "has space", "user@example.com", "a/b", "."):
        assert not _contract_identifier(bad)


@pytest.mark.parametrize("seed", SEEDS)
def test_identifier_grammar_fuzz_matches_pinned_grammar(seed):
    """Seeded identifier strings over a hostile alphabet are
    accepted IFF the independently restated contract grammar
    accepts them - length edges 0/1/64/65 included
    deterministically."""
    rng = random.Random(seed)
    base = row("identity.login", "succeeded", "base")
    candidates = ["", "A", "z", "0", "_", "-", "A" * 64,
                  "A" * 65, "-" * 64, "_" * 65]
    candidates += [
        "".join(rng.choice(IDENT_ALPHABET)
                for _ in range(rng.randrange(0, 67)))
        for _ in range(60)
    ]
    for ident in candidates:
        expected = _contract_identifier(ident)
        for field in ("event_id", "correlation_id"):
            candidate = {**base, field: ident}
            before = copy.deepcopy(candidate)
            if expected:
                detached = events.validate(candidate)
                assert detached == before
                assert detached is not candidate
            else:
                assert_refusal(
                    lambda candidate=candidate:
                        events.validate(candidate),
                    "malformed_event")
            assert candidate == before


@pytest.mark.parametrize("seed", SEEDS)
def test_catalog_name_consistency_fuzz(seed):
    """Seeded (operation_id, name, outcome) triples: accept IFF
    the triple is the exact declared catalog shape; every drift
    is unknown_name (type-clean) or malformed_event."""
    rng = random.Random(seed)
    near_ops = ["identity.login", "identity.nope",
                "Identity.login", "identity.login ",
                "quota.reserve", "billing.refund", ""]
    for _ in range(50):
        op = rng.choice(near_ops)
        outcome = rng.choice(["succeeded", "failed"])
        name_op = rng.choice(near_ops)
        name_outcome = rng.choice(["succeeded", "failed"])
        event = row(op, outcome, f"cat_{rng.randrange(10**6)}")
        event["name"] = \
            f"control_plane.{name_op}.{name_outcome}"
        if outcome == "failed" and op in ERRORS:
            event["error_code"] = rng.choice(ERRORS[op])
        elif outcome == "failed":
            event["error_code"] = "internal"
        consistent = (
            op in ERRORS
            and name_op == op
            and name_outcome == outcome
            and (outcome == "succeeded"
                 or event["error_code"] in ERRORS[op]))
        before = copy.deepcopy(event)
        if consistent:
            assert events.validate(event) == before
        else:
            classes = {"unknown_name", "malformed_event"}
            if op in ERRORS and name_op == op and \
                    name_outcome == outcome and \
                    outcome == "failed" and \
                    event["error_code"] not in ERRORS[op]:
                classes = {"malformed_event"}
            assert_refusal(
                lambda event=event: events.validate(event),
                *classes)
        assert event == before


def _identifier_fuzz_check(validate_fn, candidates):
    """Run the identifier fuzz comparison against any validate
    callable: returns the first identifier where the callable
    disagrees with the independent contract predicate, else
    None."""
    base = row("identity.login", "succeeded", "base")
    for ident in candidates:
        candidate = {**base, "event_id": ident}
        try:
            validate_fn(candidate)
            accepted = True
        except events.Refusal:
            accepted = False
        if accepted != _contract_identifier(ident):
            return ident
    return None


def _widened_grammar_validate(event):
    """Behavioral mutant: the identifier grammar silently
    widens to accept dots inside event_id."""
    if type(event) is dict and \
            type(event.get("event_id")) is str and \
            "." in event["event_id"]:
        return dict(event)
    return events.validate(event)


def test_identifier_fuzz_kills_grammar_widening_mutant():
    """The fuzz comparison is measured against a regex-widening
    mutant: the shipped validator shows NO disagreement; the
    widened mutant is caught at the exact widened identifier."""
    assert _identifier_fuzz_check(
        events.validate, ["x.y", "ok_id", "A" * 65]) is None
    assert _identifier_fuzz_check(
        _widened_grammar_validate, ["ok_id", "x.y"]) == "x.y"


# -- injected SQLite transaction faults at every publish stage -----------------


def test_publish_statement_sequence_is_batch_validated_ordered():
    """A successful 3-event publish issues EXACTLY: validate
    everything (no storage), BEGIN, read existing, ordered
    inserts, count, COMMIT - never an interleaved or partial
    pattern."""
    ledger = events.Ledger()
    spy = DBSpy(ledger._db)
    ledger._db = spy
    batch = [row(op, "succeeded", f"seq_{i}")
             for i, op in enumerate(CATALOG[:3])]
    assert ledger.publish(batch) == 3
    assert [stmt.split(" ")[0] for stmt in spy.calls] == [
        "BEGIN", "SELECT", "INSERT", "INSERT", "INSERT",
        "SELECT", "COMMIT"]
    ledger.close()


@pytest.mark.parametrize("fail_at", range(7))
def test_publish_fault_at_each_stage_rolls_back_and_recovers(
        tmp_path, fail_at):
    """A synthetic sqlite fault injected at EVERY statement index
    of one publish (BEGIN, read, three inserts, count, COMMIT)
    is a typed non-echo publication_failure; the outbox and
    dedupe state stay at baseline in-session AND across reopen,
    and the same batch commits in exact order once the fault
    clears."""
    path = tmp_path / "ledger.sqlite"
    ledger = events.Ledger(path)
    prior = row("identity.login", "succeeded", "prior")
    assert ledger.publish([prior]) == 1
    baseline = ledger.events
    batch = [row(op, "succeeded", f"new_{i}")
             for i, op in enumerate(CATALOG[:3])]
    before = copy.deepcopy(batch)
    fault = FaultDB(ledger._db, fail_at)
    ledger._db = fault
    caught = assert_refusal(
        lambda: ledger.publish(batch), "publication_failure")
    assert "synthetic" not in str(caught)
    assert "fault" not in str(caught)
    assert batch == before
    assert_state(ledger, baseline)
    ledger._db = fault.wrapped
    # retry with the SAME event IDs and envelopes: full batch,
    # exact input order, count is the whole committed outbox
    assert ledger.publish(batch) == 4
    expected = baseline + batch
    assert_state(ledger, expected)
    # replay is a no-op; dedupe tracks the committed sequence
    assert ledger.publish(batch) == 4
    assert_state(ledger, expected)
    ledger.close()
    reopened = events.Ledger(path)
    assert_state(reopened, expected)
    conflict = {**batch[0], "occurred_at": 1}
    assert_refusal(lambda: reopened.publish([conflict]),
                   "duplicate_conflict")
    assert_state(reopened, expected)
    reopened.close()


@pytest.mark.parametrize("fail_at", range(5))
def test_hydration_fault_at_each_stage_preserves_prior_state(
        tmp_path, fail_at):
    """The same staged-fault battery over the legacy hydration
    setter (BEGIN, DELETE, inserts, COMMIT): typed refusal,
    prior committed sequence intact in-session and on reopen."""
    path = tmp_path / "ledger.sqlite"
    ledger = events.Ledger(path)
    prior = row("identity.login", "succeeded", "prior")
    ledger.publish([prior])
    baseline = ledger.events
    fault = FaultDB(ledger._db, fail_at)
    ledger._db = fault
    caught = assert_refusal(
        lambda: setattr(
            ledger, "events",
            [prior, row("quota.reserve", "failed", "hyd",
                        code="quota_exhausted")]),
        "publication_failure")
    assert "synthetic" not in str(caught)
    ledger._db = fault.wrapped
    assert_state(ledger, baseline)
    ledger.close()
    assert_state(events.Ledger(path), baseline)


# -- hostile types inside fuzz combinations ------------------------------------


def test_hostile_fields_inside_combinations_never_invoke_caller(
        tmp_path):
    """Hostile-typed fields combined with second defects: zero
    caller-controlled method invocations, zero preflight storage
    calls, inputs untouched, typed refusal, and a batch that is
    not an exact list (subclass, tuple, mapping) refuses before
    storage too."""
    ledger = events.Ledger(tmp_path / "ledger.sqlite")
    spy = DBSpy(ledger._db)
    ledger._db = spy
    base = row("identity.login", "succeeded", "base")
    combinations = [
        {**base, "event_id": Hostile(),
         "occurred_at": -1},
        {**base, "operation_id": Hostile(),
         "version": 2},
        {**base, "correlation_id": Hostile(),
         "request_body": "private"},
        {**base, "outcome": Hostile(),
         "name": "control_plane.identity.login.succeeded"},
        {**base, "version": Hostile(), "outcome": "nope"},
    ]
    for candidate in combinations:
        before = list(candidate.items())
        Hostile.calls = 0
        assert_refusal(lambda candidate=candidate:
                       events.validate(candidate),
                       "malformed_event", "unknown_name",
                       "unsupported_version")
        assert_refusal(lambda candidate=candidate:
                       ledger.publish([candidate]),
                       "malformed_event", "unknown_name",
                       "unsupported_version")
        assert list(candidate.items()) == before
        assert Hostile.calls == 0
        assert spy.calls == []
    assert_state(ledger, [])
    spy.calls.clear()  # authorized reads, not publication

    class ListSubclass(list):
        pass

    for batch in (ListSubclass([base]), (base,),
                  {"row": base}, base):
        assert_refusal(lambda batch=batch:
                       ledger.publish(batch),
                       "malformed_event")
        assert spy.calls == []
    ledger.close()


# -- measured behavioral mutant kills ------------------------------------------
# Every mutant faces the SAME scenario built on fresh IDs, and the
# harness returns the classified witness tag of the FIRST contract
# violation it observes. A kill is counted only when the observed
# witness is exactly the behavior the mutant violates.


def _fault_trigger(db):
    db.execute("""
        CREATE TRIGGER reject_faulty BEFORE INSERT ON outbox
        WHEN NEW.event_id = 'faulty'
        BEGIN SELECT RAISE(FAIL, 'synthetic storage fault');
        END
    """)


_FAULT_BATCH = [row("identity.login", "succeeded", "new_a"),
                row("quota.reserve", "succeeded", "faulty"),
                row("quota.commit", "succeeded", "new_c")]
_EXPECTED_IDS = ["prior", "new_a", "faulty", "new_c"]


def _harness_outcome(make_ledger, path):
    """The publication contract as an executable, classifying
    harness. Returns "survived" when every checkpoint holds;
    otherwise the witness tag of the first observed violation."""
    ledger = make_ledger(path)
    try:
        if ledger.publish(
                [row("identity.login", "succeeded",
                     "prior")]) != 1:
            return "initial-publish"
    except Exception:
        return "initial-publish"
    _fault_trigger(ledger._db)
    try:
        ledger.publish(_FAULT_BATCH)
        return "fault-swallowed"  # the storage fault was hidden
    except events.Refusal as caught:
        if caught.failure_class != "publication_failure":
            return "fault-misclassified"
    except Exception:
        return "fault-raw-escape"
    # checkpoint 1: no partial publication after the fault
    if [event["event_id"] for event in ledger.events] != \
            ["prior"]:
        return "partial"
    ledger._db.execute("DROP TRIGGER reject_faulty")
    # checkpoint 2: the SAME IDs and envelopes commit whole
    try:
        if ledger.publish(_FAULT_BATCH) != 4:
            return "retry-count"
    except Exception:
        return "retry-refusal"
    # checkpoint 3: exact input order in the committed outbox
    if [event["event_id"] for event in ledger.events] != \
            _EXPECTED_IDS:
        return "order"
    # checkpoint 4: identical replay is a no-op
    try:
        if ledger.publish(_FAULT_BATCH) != 4:
            return "replay-count"
    except Exception:
        return "replay-refusal"
    if [event["event_id"] for event in ledger.events] != \
            _EXPECTED_IDS:
        return "replay-state"
    # checkpoint 5: same event_id, different envelope is a
    # whole-batch conflict
    conflict = {**_FAULT_BATCH[1], "occurred_at": 99}
    try:
        ledger.publish([conflict])
        return "conflict"  # the conflict was accepted
    except events.Refusal as caught:
        if caught.failure_class != "duplicate_conflict":
            return "conflict-misclassified"
    except Exception:
        return "conflict-raw-escape"
    if [event["event_id"] for event in ledger.events] != \
            _EXPECTED_IDS:
        return "conflict-state"
    ledger.close()
    # checkpoint 6: reopen preserves the exact committed
    # sequence
    try:
        reopened = make_ledger(path)
        if [event["event_id"] for event in reopened.events] \
                != _EXPECTED_IDS:
            return "reopen"
    except Exception:
        return "reopen-failure"
    return "survived"


class _PartialPublishMutant(events.Ledger):
    """Behavioral mutant: attempts EVERY row in autocommit and
    only then reports the storage fault - the rows after the
    failure leak into the committed outbox."""

    def publish(self, batch, *, fail_commit=False):
        failure = None
        for event in batch:
            proposed = events.validate(event)
            try:
                self._db.execute(
                    "INSERT OR IGNORE INTO outbox(event_id, "
                    "envelope) VALUES (?, ?)",
                    (proposed["event_id"],
                     events._encode(proposed)))
            except sqlite3.Error:
                failure = events.Refusal("publication_failure")
        if failure is not None:
            raise failure
        return self._db.execute(
            "SELECT COUNT(*) FROM outbox").fetchone()[0]


class _OrderSwapMutant(events.Ledger):
    """Behavioral mutant: dedupes against existing rows and
    survives the injected trigger, but commits NEW rows in
    reverse order."""

    def publish(self, batch, *, fail_commit=False):
        proposed = [events.validate(event) for event in batch]
        try:
            self._db.execute("BEGIN IMMEDIATE")
            existing = {key for (key,) in self._db.execute(
                "SELECT event_id FROM outbox")}
            for event in reversed(proposed):
                if event["event_id"] not in existing:
                    self._db.execute(
                        "INSERT INTO outbox(event_id, envelope)"
                        " VALUES (?, ?)",
                        (event["event_id"],
                         events._encode(event)))
            self._db.execute("COMMIT")
        except sqlite3.Error:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise events.Refusal(
                "publication_failure") from None
        return self._db.execute(
            "SELECT COUNT(*) FROM outbox").fetchone()[0]


class _DedupeBlindMutant(events.Ledger):
    """Behavioral mutant: last write wins on event_id instead of
    refusing a conflicting duplicate."""

    def publish(self, batch, *, fail_commit=False):
        proposed = [events.validate(event) for event in batch]
        try:
            self._db.execute("BEGIN IMMEDIATE")
            for event in proposed:
                self._db.execute(
                    "INSERT OR REPLACE INTO outbox(event_id, "
                    "envelope) VALUES (?, ?)",
                    (event["event_id"],
                     events._encode(event)))
            self._db.execute("COMMIT")
        except sqlite3.Error:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise events.Refusal(
                "publication_failure") from None
        return self._db.execute(
            "SELECT COUNT(*) FROM outbox").fetchone()[0]


def test_publication_harness_accepts_shipped_ledger(tmp_path):
    """Control: the harness itself is not vacuously strict - the
    shipped ledger satisfies every checkpoint end to end."""
    assert _harness_outcome(events.Ledger,
                            tmp_path / "real.sqlite") == "survived"


@pytest.mark.parametrize("mutant,witness", [
    (_PartialPublishMutant, "partial"),
    (_OrderSwapMutant, "order"),
    (_DedupeBlindMutant, "conflict"),
])
def test_behavioral_mutants_measured_kills(tmp_path, mutant,
                                           witness):
    """Each behavioral mutant is KILLED at exactly its own
    behavioral checkpoint: the partial mutant is observed
    leaking committed rows after the fault, the order mutant is
    observed committing fresh rows out of order, the dedupe
    mutant is observed accepting a conflicting duplicate. The
    witness is measured by the harness, never assumed."""
    outcome = _harness_outcome(
        mutant, tmp_path / f"{mutant.__name__}.sqlite")
    assert outcome == witness
