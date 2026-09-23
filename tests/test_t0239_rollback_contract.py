"""T0239: store rollback contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/rollback.yaml:
verified truncation rollback of a WAL-logged store to a prior
position. The source log validates through the LINKED WAL
machinery (imported, never restated); the tail archiver is
UNTRUSTED input behind the boundary (single evaluation per
rollback, detached argument copy, frozen log and request, exact
built-in-str pinned-grammar UTF-8-encodable output); the commit
removes exactly the tail, only after full validation and
archival - a rejected rollback leaves every input bit-identical.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    WalEngine,
    WalError,
    _log_of,
    canonical_payload,
)
from tools.rollback_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_ROLLBACK_RE = re.compile(
    _CC["identifiers"]["rollback_id"]["grammar"])
_TOKEN_RE = re.compile(
    _CC["identifiers"]["archive_token"]["grammar"])

_WAL = WalEngine(canonical_payload)  # linked, trusted


class RollbackError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise RollbackError(cls, FAILURE_MAPPING[cls])


def archive_tail(tail):
    """THE pinned canonical tail archive token: sha256 over the
    DOMAIN-SEPARATED, LENGTH-FRAMED serialization of EVERY exact
    WAL field of every frozen tail entry, in order -
    deterministic in the tail and only in the tail. The engine
    derives this LOCALLY and binds the untrusted archiver's
    output to it byte-for-byte."""
    parts = ["arc1"]
    for entry in tail:
        record = entry["payload"]["record"]
        for field in (entry["sequence"], entry["op"],
                      entry["entry_id"], entry["prior_entry_id"],
                      entry["payload"]["identity"],
                      record["variant"], record["digest"],
                      record["snapshot_fen"]):
            text = str(field)
            parts.append(f"{len(text)}:{text}")
    return "arc1:" + hashlib.sha256(
        "|".join(parts).encode()).hexdigest()


class RollbackEngine:
    """The contract's pinned rollback: full linked-WAL source
    validation, exact target resolution, frozen log and request,
    exactly one untrusted archiver call on a DETACHED tail copy,
    tail removal committed LAST."""

    def __init__(self, tail_archiver):
        self.archiver = tail_archiver  # UNTRUSTED

    def _archive(self, frozen_tail):
        """THE archiver boundary: raising ANY BaseException
        (including KeyboardInterrupt/SystemExit/GeneratorExit),
        non-exact-str, UTF-8-inencodable or wrong-grammar output
        fails closed as divergent_archive."""
        try:
            # DETACHED argument copy: the archiver never sees the
            # frozen snapshot objects - mutating them is inert
            out = self.archiver([dict(entry) for entry in
                                 frozen_tail])
        except BaseException:
            # fail closed against the FULL BaseException surface:
            # KeyboardInterrupt/SystemExit/GeneratorExit from the
            # untrusted oracle map to the typed failure, never a
            # raw escape (totality)
            _fail("divergent_archive")
        if type(out) is not str or \
                _TOKEN_RE.fullmatch(out) is None:
            _fail("divergent_archive")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_archive")
        return out

    @staticmethod
    def _derive_rollback_id(from_head, to_head, count, token):
        return "rbk1:" + hashlib.sha256(
            f"{from_head}\n{to_head}\n{count}\n{token}".encode()
        ).hexdigest()

    def rollback(self, log, request):
        """ATOMIC: exact container boundaries, linked-WAL source
        validation, frozen request and log, one archiver call
        whose token is bound byte-for-byte to the local canonical
        tail serialization, commit LAST - rejection leaves log and
        request bit-identical."""
        if type(log) is not list:
            _fail("malformed_rollback_record")
        # Request keys are checked as EXACT str BEFORE the set
        # compare: a key with a colliding hash and a raising __eq__
        # must fail closed, never escape raw.
        if type(request) is not dict or \
                not all(type(k) is str for k in dict.keys(request)) or \
                set(request.keys()) != {"target_sequence"}:
            _fail("malformed_rollback_record")
        target = request["target_sequence"]
        if type(target) is not int:
            _fail("malformed_rollback_record")
        try:
            _WAL.replay(log)
        except WalError:
            _fail("corrupt_source")
        if target < 0 or target > len(log):
            _fail("unknown_target")
        # INPUT PRESERVATION snapshot (reference-preserving) +
        # FREEZE, both BEFORE the archiver call.
        saved_container, saved_entries = \
            WalEngine._snapshot_log(log)
        saved_req = dict(request)
        frozen = WalEngine._freeze_log(log)
        try:
            frozen_tail = frozen[target:]
            token = self._archive(frozen_tail)
            # TAIL-BOUND ARCHIVAL: the untrusted archiver's token
            # must equal the LOCAL deterministic canonical
            # serialization of the frozen full tail byte-for-byte.
            # An arbitrary valid-shaped token, a token for a
            # different tail, or any stateful variation fails
            # closed - the destructive commit never happens.
            if token != archive_tail(frozen_tail):
                _fail("divergent_archive")
        finally:
            WalEngine._restore_log(log, saved_container,
                                   saved_entries)
            request.clear()
            request.update(saved_req)
        from_head = frozen[-1]["entry_id"] if frozen else GENESIS
        to_head = frozen[target - 1]["entry_id"] if target \
            else GENESIS
        count = len(frozen) - target
        # COMMIT LAST: remove exactly the tail
        del log[target:]
        return {"rollback_id": self._derive_rollback_id(
            from_head, to_head, count, token),
            "from_head": from_head,
            "to_head": to_head,
            "truncated_count": count,
            "archive_token": token}


def _engine():
    return RollbackEngine(archive_tail)


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_rollback_partial_tail():
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    expected_tip = log[1]["entry_id"]
    req = {"target_sequence": 2}
    req_before = dict(req)
    receipt = engine.rollback(log, req)
    assert set(receipt) == set(_FIELDS)
    assert _ROLLBACK_RE.fullmatch(receipt["rollback_id"])
    assert _TOKEN_RE.fullmatch(receipt["archive_token"])
    assert receipt["to_head"] == expected_tip
    assert receipt["from_head"] != receipt["to_head"]
    assert receipt["truncated_count"] == 1
    assert len(log) == 2  # exactly the tail removed
    assert log[-1]["entry_id"] == expected_tip
    assert req == req_before
    # the surviving prefix still re-derives cleanly
    assert _WAL.replay(log)["head"] == expected_tip


def test_rollback_to_genesis():
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    receipt = engine.rollback(log, {"target_sequence": 0})
    assert receipt["to_head"] == GENESIS
    assert receipt["truncated_count"] == 2
    assert log == []


def test_rollback_noop_at_tip():
    engine = _engine()
    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    receipt = engine.rollback(log, {"target_sequence": 1})
    assert receipt["to_head"] == receipt["from_head"]
    assert receipt["truncated_count"] == 0
    assert log == before


def test_determinism():
    def build():
        engine = _engine()
        log = _log_of(("put", STARTPOS), ("put", KINGS),
                      ("delete", STARTPOS))
        receipt = engine.rollback(log, {"target_sequence": 1})
        return receipt, log
    assert build() == build()


def test_one_archiver_call_per_rollback():
    calls = {"n": 0}

    def counting(tail):
        calls["n"] += 1
        return archive_tail(tail)

    engine = RollbackEngine(counting)
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    engine.rollback(log, {"target_sequence": 1})
    assert calls["n"] == 1
    # a rejected rollback never reaches the archiver
    with pytest.raises(RollbackError):
        engine.rollback(log, {"target_sequence": 9})
    assert calls["n"] == 1


# -- malformed + unknown target + corrupt source ---------------------------------

HOSTILE_REQUESTS = [None, True, 0, 1.5, [], "text", {},
                    {"target_sequence": "2"},
                    {"target_sequence": True},
                    {"target_sequence": 1.5},
                    {"target_sequence": 1, "extra": 1}]


@pytest.mark.parametrize("req", HOSTILE_REQUESTS)
def test_total_over_hostile_requests(req):
    engine = _engine()
    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, req)
    assert exc.value.failure_class == "malformed_rollback_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_rollback_record"]
    assert exc.value.code in ERROR_ENUM
    assert log == before


@pytest.mark.parametrize("log", [None, True, 0, 1.5, "text", {}])
def test_total_over_hostile_log_types(log):
    engine = _engine()
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, {"target_sequence": 0})
    assert exc.value.failure_class == "malformed_rollback_record"


@pytest.mark.parametrize("target", [-1, 3, 100])
def test_unknown_target(target):
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, {"target_sequence": target})
    assert exc.value.failure_class == "unknown_target"
    assert exc.value.code == FAILURE_MAPPING["unknown_target"]
    assert exc.value.code in ERROR_ENUM
    assert log == before


def _corrupt_sources():
    def seq_gap(log):
        log[1]["sequence"] = 1

    def chain_break(log):
        log[1]["prior_entry_id"] = "wal1:" + "0" * 64

    def entry_tamper(log):
        log[0]["entry_id"] = "wal1:" + "f" * 64

    return [("sequence-gap", seq_gap), ("chain-break", chain_break),
            ("entry-tamper", entry_tamper)]


@pytest.mark.parametrize("name,mutate", _corrupt_sources(),
                         ids=[n for n, _ in _corrupt_sources()])
def test_corrupt_source_log(name, mutate):
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    mutate(log)
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, {"target_sequence": 1})
    assert exc.value.failure_class == "corrupt_source"
    assert exc.value.code == FAILURE_MAPPING["corrupt_source"]
    assert exc.value.code in ERROR_ENUM
    assert log == before


# -- untrusted tail archiver -----------------------------------------------------


def _hostile_archivers():
    def raising(tail):
        raise ValueError("boom")

    def bad_type(tail):
        return []

    def bad_grammar(tail):
        return "arc1:zz"

    def wrong_prefix(tail):
        return "wal1:" + "0" * 64

    class EvilStr(str):
        def __hash__(self):
            raise RuntimeError("evil")

    def evil_str(tail):
        return EvilStr("arc1:" + "0" * 64)

    def lone_surrogate(tail):
        return "\ud800"

    def raising_keyboard_interrupt(tail):
        raise KeyboardInterrupt("boom")

    def raising_system_exit(tail):
        raise SystemExit("boom")

    def raising_generator_exit(tail):
        raise GeneratorExit("boom")

    def arbitrary_valid_token(tail):
        return "arc1:" + "f" * 64

    def constant_zero_token(tail):
        return "arc1:" + "0" * 64

    def different_tail_token(tail):
        # a REAL canonical token - for a DIFFERENT tail
        return archive_tail([dict(tail[0], sequence=9999)]
                            if tail else
                            [{"sequence": 1, "op": "put",
                              "entry_id": "wal1:" + "0" * 64,
                              "prior_entry_id": GENESIS,
                              "payload": {
                                  "identity": "x",
                                  "record": {"variant": "v",
                                             "digest": "d",
                                             "snapshot_fen":
                                                 "f"}}}])

    return [("raising", raising), ("bad-type", bad_type),
            ("bad-grammar", bad_grammar),
            ("wrong-prefix", wrong_prefix),
            ("evil-str", evil_str),
            ("lone-surrogate", lone_surrogate),
            ("arbitrary-valid-token", arbitrary_valid_token),
            ("constant-zero-token", constant_zero_token),
            ("different-tail-token", different_tail_token),
            ("raising-keyboard-interrupt",
             raising_keyboard_interrupt),
            ("raising-system-exit", raising_system_exit),
            ("raising-generator-exit", raising_generator_exit)]


@pytest.mark.parametrize("name,archiver", _hostile_archivers(),
                         ids=[n for n, _ in _hostile_archivers()])
def test_hostile_archiver(name, archiver):
    """A hostile archiver fails closed as divergent_archive; the
    rollback NEVER commits - log and request bit-identical."""
    engine = RollbackEngine(archiver)
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    req = {"target_sequence": 1}
    req_before = dict(req)
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, req)
    assert exc.value.failure_class == "divergent_archive"
    assert exc.value.code == FAILURE_MAPPING["divergent_archive"]
    assert exc.value.code in ERROR_ENUM
    assert log == before
    assert req == req_before


def test_stateful_alternating_valid_tokens_never_commit():
    """A stateful archiver alternating between two valid-shaped
    tokens across byte-identical rollback attempts: EVERY attempt
    fails closed (no commit, inputs bit-identical) - and the
    honest archiver on the same inputs produces byte-identical
    receipts, pinning determinism."""
    calls = {"n": 0}

    def alternating(tail):
        calls["n"] += 1
        return "arc1:" + str(calls["n"] % 2) * 64

    engine = RollbackEngine(alternating)
    for _ in range(2):
        log = _log_of(("put", STARTPOS), ("put", KINGS))
        before = copy.deepcopy(log)
        req = {"target_sequence": 1}
        with pytest.raises(RollbackError) as exc:
            engine.rollback(log, req)
        assert exc.value.failure_class == "divergent_archive"
        assert log == before
        assert req == {"target_sequence": 1}
    # determinism with the honest archiver: same inputs, same
    # receipt AND surviving prefix
    def build():
        log = _log_of(("put", STARTPOS), ("put", KINGS))
        receipt = _engine().rollback(log, {"target_sequence": 1})
        return receipt, log
    assert build() == build()


def test_archive_token_binds_every_exact_tail_field():
    """The canonical token is a function of EVERY exact WAL field
    of the frozen tail: payload-only differences, reordered
    entries and tampered fields all change it; empty and
    nonempty tails never collide."""
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    frozen = WalEngine._freeze_log(log)
    tail = frozen[1:]
    token = archive_tail(tail)
    # same shape, payload-only difference (same-length fields)
    other = [dict(tail[0],
                  payload={"identity": "z" * len(
                      tail[0]["payload"]["identity"]),
                           "record": dict(
                               tail[0]["payload"]["record"])})]
    assert archive_tail(other) != token
    # reordered entries change the token
    two = WalEngine._freeze_log(log)
    assert archive_tail(two) != archive_tail(
        list(reversed(two)))
    # a tampered field changes the token
    tampered = [dict(tail[0],
                     payload=dict(
                         tail[0]["payload"],
                         record=dict(
                             tail[0]["payload"]["record"],
                             snapshot_fen="tampered")))]
    assert archive_tail(tampered) != token
    # empty vs nonempty never collides
    assert archive_tail([]) != token
    assert archive_tail([]) != archive_tail(two[:1])


def test_mutant_grammar_only_archival_validation():
    """Behavioral mutant: a rollback that validates only the
    token's grammar (never binding it to the tail) ACCEPTS an
    arbitrary valid-shaped token and commits the destructive
    truncation. Counter-test: the real engine binds the token to
    the local canonical tail serialization byte-for-byte -
    mismatch fails closed, no commit, inputs bit-identical."""
    def mutant_rollback(engine, log, request):
        frozen = WalEngine._freeze_log(log)
        tail = frozen[request["target_sequence"]:]
        token = engine.archiver(
            [dict(entry) for entry in tail])
        assert type(token) is str and \
            _TOKEN_RE.fullmatch(token)  # grammar ONLY
        del log[request["target_sequence"]:]
        return token

    engine = RollbackEngine(lambda tail: "arc1:" + "f" * 64)
    mutant_log = _log_of(("put", STARTPOS), ("put", KINGS))
    assert mutant_rollback(engine, mutant_log,
                           {"target_sequence": 1}) == \
        "arc1:" + "f" * 64
    assert len(mutant_log) == 1  # the mutant DESTROYED the tail
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    req = {"target_sequence": 1}
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, req)
    assert exc.value.failure_class == "divergent_archive"
    assert log == before
    assert req == {"target_sequence": 1}


def test_archiver_mutating_its_tail_argument():
    """The archiver computes the honest token, THEN mutates the
    tail argument it was handed: the receipt and commit derive
    from the untouched frozen snapshot, the caller's pre-target
    entries are unchanged, exactly one call."""
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    honest_log = list(log)
    calls = {"n": 0}

    def archiver(tail):
        calls["n"] += 1
        token = archive_tail(tail)
        for entry in tail:
            entry["op"] = "forged"
            entry.clear()
        tail.clear()
        return token

    receipt = RollbackEngine(archiver).rollback(
        log, {"target_sequence": 2})
    assert calls["n"] == 1
    assert receipt["truncated_count"] == 1
    assert receipt["to_head"] == honest_log[1]["entry_id"]
    assert log == honest_log[:2]


def test_archiver_mutating_log_and_request_during_call():
    """The archiver corrupts and truncates the caller's live LOG
    and rewrites the REQUEST mid-call: the commit still removes
    exactly the frozen tail, the prefix is restored
    bit-identical, and the request is restored."""
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    prefix_before = copy.deepcopy(log[:2])
    req = {"target_sequence": 2}
    req_before = dict(req)

    def archiver(tail):
        log[0]["payload"]["record"]["digest"] = "corrupted"
        del log[1]
        log.append({"junk": True})
        req["target_sequence"] = 0
        req.clear()
        return archive_tail(tail)

    receipt = RollbackEngine(archiver).rollback(log, req)
    assert receipt["truncated_count"] == 1
    assert log == prefix_before  # exactly the frozen prefix
    assert req == req_before
    assert _WAL.replay(log)["head"] == receipt["to_head"]


# -- rollback (rejection) + behavioral mutants -------------------------------------


def test_rejected_rollback_leaves_inputs_bit_identical():
    engine = _engine()
    # malformed request
    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError):
        engine.rollback(log, {"target_sequence": "x"})
    assert log == before
    # unknown target
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, {"target_sequence": 5})
    assert exc.value.failure_class == "unknown_target"
    assert log == before
    # corrupt source
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    log[1]["sequence"] = 1
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, {"target_sequence": 1})
    assert exc.value.failure_class == "corrupt_source"
    assert log == before
    # archiver failure
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    req = {"target_sequence": 1}
    with pytest.raises(RollbackError) as exc:
        RollbackEngine(lambda tail: []).rollback(log, req)
    assert exc.value.failure_class == "divergent_archive"
    assert log == before
    assert req == {"target_sequence": 1}


def test_mutant_commit_before_archival():
    """Behavioral mutant: truncating BEFORE the archiver call
    loses the tail forever when the archiver fails. Counter-test:
    the real engine commits last."""
    def mutant_rollback(engine, log, request):
        target = request["target_sequence"]
        del log[target:]  # committed FIRST
        return engine._archive([dict(e) for e in log])

    log = _log_of(("put", STARTPOS), ("put", KINGS))
    with pytest.raises(RollbackError):
        mutant_rollback(RollbackEngine(lambda tail: []),
                        log, {"target_sequence": 1})
    assert len(log) == 1  # the mutant DESTROYED the tail
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError) as exc:
        RollbackEngine(lambda tail: []).rollback(
            log, {"target_sequence": 1})
    assert exc.value.failure_class == "divergent_archive"
    assert log == before


def test_mutant_rollback_skipping_source_validation():
    """Behavioral mutant: rolling back WITHOUT linked WAL
    validation truncates a corrupt log and launders the
    corruption into a receipt. Counter-test: the real engine
    fails closed as corrupt_source."""
    def mutant_rollback(log, target):
        del log[target:]
        return {"truncated_count": 1}

    log = _log_of(("put", STARTPOS), ("put", KINGS))
    log[0]["entry_id"] = "wal1:" + "f" * 64  # silent corruption
    mutant_rollback(log, 1)
    assert len(log) == 1  # the mutant committed over corruption
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    log[0]["entry_id"] = "wal1:" + "f" * 64
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError) as exc:
        _engine().rollback(log, {"target_sequence": 1})
    assert exc.value.failure_class == "corrupt_source"
    assert log == before


def test_mutant_live_request_reread_after_archiver():
    """Behavioral mutant: re-reading the LIVE request after the
    archiver call lets a mid-call rewrite change the commit.
    Counter-test: the real engine uses only the frozen target."""
    def mutant_rollback(engine, log, request):
        target = request["target_sequence"]
        token = engine._archive(
            WalEngine._freeze_log(log)[target:])
        target = request["target_sequence"]  # LIVE re-read
        del log[target:]
        return token

    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    req = {"target_sequence": 2}

    def archiver(tail):
        req["target_sequence"] = 0
        return archive_tail(tail)

    mutant_rollback(RollbackEngine(archiver), log, req)
    assert log == []  # the mutant wiped the WHOLE log
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    prefix_before = copy.deepcopy(log[:2])
    req = {"target_sequence": 2}
    receipt = RollbackEngine(archiver).rollback(log, req)
    assert receipt["truncated_count"] == 1
    assert log == prefix_before
    assert req == {"target_sequence": 2}


# -- lint mutants ---------------------------------------------------------------


def _mutants():
    doc = yaml.safe_load(CONTRACT.read_text())
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    add("role kind drift", ["contract", "role", "kind"],
        "free-form-truncation")
    add("not_scope drift", ["contract", "role", "not_scope"],
        "state-restore-owned-here")
    add("record fields drift", ["contract", "record", "fields"],
        ["rollback_id"])
    add("record exact drift", ["contract", "record", "exact"],
        False)
    add("rollback id grammar drift",
        ["contract", "identifiers", "rollback_id", "grammar"],
        "^.*$")
    add("rollback id supplied",
        ["contract", "identifiers", "rollback_id", "source"],
        "caller-supplied")
    add("head grammar drift",
        ["contract", "identifiers", "head", "grammar"], "^.*$")
    add("token derivation dropped",
        ["contract", "identifiers", "archive_token", "source"],
        "archiver-output-shape-validated-never-trusted-beyond-"
        "shape")
    add("archival binding dropped",
        ["contract", "semantics", "archival"],
        "truncated-tail-archived-exactly-once-before-commit")
    add("token grammar drift",
        ["contract", "identifiers", "archive_token", "grammar"],
        "^.*$")
    add("token trusted",
        ["contract", "identifiers", "archive_token", "source"],
        "archiver-always-honest")
    add("source validation dropped",
        ["contract", "semantics", "source_validation"],
        "log-trusted-as-given")
    add("target resolution drift",
        ["contract", "semantics", "target_resolution"],
        "nearest-position")
    add("archival drift", ["contract", "semantics", "archival"],
        "tail-dropped-silently")
    add("chaining drift", ["contract", "semantics", "chaining"],
        "prefix-re-linked")
    add("commit drift", ["contract", "semantics", "commit"],
        "truncate-then-validate")
    add("oracle trusted",
        ["contract", "oracle_boundary", "role"],
        "archiver-always-honest")
    add("single evaluation dropped",
        ["contract", "oracle_boundary", "single_evaluation"],
        "re-archive-allowed")
    add("frozen dropped",
        ["contract", "oracle_boundary", "frozen_snapshots"],
        "live-log-re-read")
    add("output validation dropped",
        ["contract", "oracle_boundary", "output_validation"],
        "any-output")
    add("failure class dropped",
        ["contract", "failures", "classes"],
        ["malformed_rollback_record"])
    add("failure trigger drift",
        ["contract", "failures", "triggers", "unknown_target"],
        "never-fails")
    add("failure mapping drift",
        ["contract", "failures", "mapping", "divergent_archive"],
        "internal")
    add("enum drift", ["contract", "errors", "closed_enum"],
        ["internal"])
    add("property drift", ["contract", "properties", "atomic"],
        "best-effort")
    add("base path drift",
        ["contract", "versioning", "base_path"],
        "/store/rollback/v0")
    add("link drift", ["contract", "links", "wal_contract"],
        "data/contracts/san.yaml")
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 20
    for _name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)


def test_mutants_never_silent_subset():
    covered = set()
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != yaml.safe_load(
                    CONTRACT.read_text())["contract"].get(section):
                covered.add(section)
    assert covered >= {"role", "record", "identifiers",
                       "semantics", "oracle_boundary", "failures",
                       "errors", "properties", "versioning",
                       "links"}


# -- totality sweep: hostile request keys --------------------------------------


class _SK(str):
    """str subclass: hashes like the field it imitates, raises on ==."""

    def __hash__(self):
        return str.__hash__(str(self))

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    __ne__ = __eq__


class _HK:
    """Non-str key with a colliding hash and a raising ==."""

    def __init__(self, text):
        self.text = text

    def __hash__(self):
        return hash(self.text)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    __ne__ = __eq__


class _DictSub(dict):
    pass


def _refs(obj):
    """Identity snapshot that never hashes or compares a caller key."""
    if type(obj) is dict:
        return tuple((id(k), id(v)) for k, v in dict.items(obj))
    return id(obj)


@pytest.mark.parametrize("key_type", [_SK, _HK], ids=["SK", "HK"])
def test_total_over_hostile_request_keys(key_type):
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    request = {key_type("target_sequence"): 1}
    refs = _refs(request)
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, request)
    assert exc.value.failure_class == "malformed_rollback_record"
    assert exc.value.code in ERROR_ENUM
    assert log == before
    assert _refs(request) == refs


@pytest.mark.parametrize("key_type", [_SK, _HK], ids=["SK", "HK"])
def test_hostile_request_key_escapes_raw_without_guard(key_type):
    """The probe bites: the unguarded set compare raises raw."""
    request = {key_type("target_sequence"): 1}
    with pytest.raises(RuntimeError):
        set(request.keys()) != {"target_sequence"}  # noqa: B015


def test_dict_subclass_request_rejected():
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    with pytest.raises(RollbackError) as exc:
        engine.rollback(log, _DictSub(target_sequence=1))
    assert exc.value.failure_class == "malformed_rollback_record"
    assert log == before
