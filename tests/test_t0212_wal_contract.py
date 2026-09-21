"""T0212: store WAL contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/wal.yaml:
a single write-ahead log over content-addressed graph-state
mutations (put/delete of exact node records). Entry payloads
validate through the LINKED transposition-node machinery
(imported, never restated); the payload canonicalizer is
UNTRUSTED input behind the boundary (single evaluation per entry
per operation, frozen log and request snapshots, exact
built-in-str output validation); append is atomic with rollback -
a rejected append or replay leaves every input bit-identical.
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

from tests.test_t0113_position_digest_contract import (  # noqa: E402
    digest_fen,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
    NodeError,
    _make_record,
    _record_identity,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _docs as _node_docs,
)
from tests.test_t0194_migration_contract import (  # noqa: E402
    state_id,
)
from tools.variant_contract_lint import ContractError  # noqa: E402
from tools.wal_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)

_NDOCS = _node_docs()
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_ENTRY_RE = re.compile(_CC["identifiers"]["entry_id"]["grammar"])
_PRIOR_RE = re.compile(_CC["identifiers"]["prior_entry_id"]["grammar"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
_OPS = _CC["registry"]["operations"]
GENESIS = "wal0:" + "0" * 64


class WalError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise WalError(cls, FAILURE_MAPPING[cls])


def _node(fen_text):
    return _make_record(*_NDOCS, digest_fen, "standard", fen_text)


def _identity(record):
    return repr(_record_identity(*_NDOCS, record))


def _payload(fen_text):
    rec = _node(fen_text)
    return {"identity": _identity(rec), "record": rec}


def _op_spec(op):
    for entry in _OPS:
        if entry["op"] == op:
            return entry
    return None


def canonical_payload(identity, record):
    """The honest payload canonicalizer: one canonical string per
    validated payload - the entry-id derivation input."""
    return (f"{identity}\n{record['variant']}\n"
            f"{record['digest']}\n{record['snapshot_fen']}")


class WalEngine:
    """The contract's pinned WAL: total validation, untrusted
    payload canonicalizer behind the boundary (one call per entry
    per operation, frozen snapshots), content-addressed chained
    entries, atomic staged commit, deterministic replay fold."""

    def __init__(self, payload_canonicalizer):
        self.canonicalizer = payload_canonicalizer  # UNTRUSTED

    def _canonicalize(self, identity, record):
        """THE canonicalizer boundary: raising or non-exact-str
        output fails closed as divergent_canonicalization."""
        # DETACHED argument copy: the oracle never sees the
        # frozen snapshot object that derivation, staging and the
        # replay fold read - mutating the argument is inert
        try:
            out = self.canonicalizer(identity, dict(record))
        except Exception:
            _fail("divergent_canonicalization")
        if type(out) is not str:
            _fail("divergent_canonicalization")
        # the pinned entry-id serialization is UTF-8: a string
        # that cannot encode (e.g. lone surrogates) fails closed
        # HERE, inside the boundary - never as a raw escape later
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_canonicalization")
        return out

    @staticmethod
    def _derive_entry_id(sequence, op, canonical, prior):
        return "wal1:" + hashlib.sha256(
            f"{sequence}\n{op}\n{canonical}\n{prior}".encode()
        ).hexdigest()

    def _validate_record(self, record):
        """Exact node record through the linked machinery; returns
        the derived identity string."""
        if type(record) is not dict or \
                set(record.keys()) != \
                set(_NDOCS[0]["record"]["fields"]):
            _fail("malformed_wal_entry")
        if type(record["variant"]) is not str or \
                type(record["snapshot_fen"]) is not str or \
                type(record["digest"]) is not str:
            _fail("malformed_wal_entry")
        try:
            derived = _make_record(*_NDOCS, digest_fen,
                                   record["variant"],
                                   record["snapshot_fen"])
        except NodeError:
            _fail("malformed_wal_entry")
        # EXACT record equality: a well-formed-but-wrong digest
        # (e.g. swapped from another valid record) is as rejected
        # as a grammatically invalid one.
        if record != derived:
            _fail("malformed_wal_entry")
        return _identity(record)

    def _validate_payload(self, op, payload):
        spec = _op_spec(op)
        if type(payload) is not dict or \
                set(payload.keys()) != set(spec["payload_fields"]):
            _fail("malformed_wal_entry")
        identity = payload["identity"]
        if type(identity) is not str:
            _fail("malformed_wal_entry")
        derived = self._validate_record(payload["record"])
        if identity != derived:
            _fail("malformed_wal_entry")

    def _validate_entry_structure(self, entry, position,
                                  prior_tip):
        """Phase A - everything decidable WITHOUT the oracle:
        shape, op registration, exact 1-based sequence, id
        grammars, prior-link, payload and record validity."""
        if type(entry) is not dict or \
                set(entry.keys()) != set(_FIELDS):
            _fail("malformed_wal_entry")
        if type(entry["op"]) is not str:
            _fail("malformed_wal_entry")
        if _op_spec(entry["op"]) is None:
            _fail("unknown_operation")
        seq = entry["sequence"]
        if type(seq) is not int:
            _fail("malformed_wal_entry")
        if seq != position:
            _fail("sequence_conflict")
        if type(entry["entry_id"]) is not str or \
                _ENTRY_RE.fullmatch(entry["entry_id"]) is None:
            _fail("malformed_wal_entry")
        if type(entry["prior_entry_id"]) is not str or \
                _PRIOR_RE.fullmatch(
                    entry["prior_entry_id"]) is None:
            _fail("malformed_wal_entry")
        if entry["prior_entry_id"] != prior_tip:
            _fail("corrupt_chain")
        self._validate_payload(entry["op"], entry["payload"])

    def _validate_log_structure(self, log):
        tip = GENESIS
        for position, entry in enumerate(log, start=1):
            self._validate_entry_structure(entry, position, tip)
            tip = entry["entry_id"]

    @staticmethod
    def _snapshot_log(log):
        """INPUT PRESERVATION snapshot (reference-preserving): the
        oracle may hold external references into the caller's live
        log - every exit restores it bit-identical."""
        saved_container = list(log)
        saved_entries = []
        for entry in log:
            payload = entry["payload"]
            record = payload["record"]
            saved_entries.append((entry, dict(entry),
                                  payload, dict(payload),
                                  record, dict(record)))
        return saved_container, saved_entries

    @staticmethod
    def _restore_log(log, saved_container, saved_entries):
        log[:] = saved_container
        for entry, e_copy, payload, p_copy, record, r_copy in \
                saved_entries:
            record.clear()
            record.update(r_copy)
            payload.clear()
            payload.update(p_copy)
            entry.clear()
            entry.update(e_copy)

    @staticmethod
    def _freeze_log(log):
        """FREEZE THE ENTIRE LOG: one detached plain-dict snapshot
        taken BEFORE the first oracle call; every derivation reads
        ONLY the frozen copy, never the caller's live list."""
        return [{"entry_id": entry["entry_id"],
                 "sequence": entry["sequence"],
                 "op": entry["op"],
                 "payload": {
                     "identity": entry["payload"]["identity"],
                     "record": dict(entry["payload"]["record"])},
                 "prior_entry_id": entry["prior_entry_id"]}
                for entry in log]

    def _rederive_chain(self, frozen):
        """Phase C: recompute every entry id through the oracle
        (exactly one call per entry); divergence is corrupt_chain.
        Returns the derived tip."""
        tip = GENESIS
        for entry in frozen:
            canonical = self._canonicalize(
                entry["payload"]["identity"],
                entry["payload"]["record"])
            if self._derive_entry_id(entry["sequence"],
                                     entry["op"], canonical,
                                     entry["prior_entry_id"]) != \
                    entry["entry_id"]:
                _fail("corrupt_chain")
            tip = entry["entry_id"]
        return tip

    def append(self, log, request):
        """ATOMIC: validate and FREEZE the request, validate the
        full log structurally, snapshot and freeze it, re-derive
        the chain behind the oracle boundary, stage the new entry
        and commit LAST - rejection leaves log and request
        bit-identical."""
        if type(log) is not list:
            _fail("malformed_wal_entry")
        if type(request) is not dict or \
                set(request.keys()) != {"op", "payload"}:
            _fail("malformed_wal_entry")
        op = request["op"]
        if type(op) is not str:
            _fail("malformed_wal_entry")
        if _op_spec(op) is None:
            _fail("unknown_operation")
        self._validate_payload(op, request["payload"])
        # FREEZE THE REQUEST DURING VALIDATION: a detached plain
        # dict of the validated exact values, taken BEFORE the
        # first oracle call; derivation and the receipt read ONLY
        # this frozen copy; the caller's request is never re-read
        # after untrusted code runs.
        frozen_req = {"op": op,
                      "payload": {
                          "identity":
                              request["payload"]["identity"],
                          "record":
                              dict(request["payload"]["record"])}}
        self._validate_log_structure(log)
        saved_container, saved_entries = self._snapshot_log(log)
        saved_req = (request, dict(request),
                     request["payload"], dict(request["payload"]),
                     request["payload"]["record"],
                     dict(request["payload"]["record"]))
        frozen = self._freeze_log(log)
        try:
            tip = self._rederive_chain(frozen)
            canonical = self._canonicalize(
                frozen_req["payload"]["identity"],
                frozen_req["payload"]["record"])
            staged = {
                "entry_id": self._derive_entry_id(
                    len(frozen) + 1, frozen_req["op"], canonical,
                    tip),
                "sequence": len(frozen) + 1,
                "op": frozen_req["op"],
                "payload": {
                    "identity": frozen_req["payload"]["identity"],
                    "record":
                        dict(frozen_req["payload"]["record"])},
                "prior_entry_id": tip,
            }
        finally:
            self._restore_log(log, saved_container, saved_entries)
            # the caller's REQUEST is restored bit-identical too -
            # the oracle may hold an external reference to it
            req, req_copy, payload, p_copy, record, r_copy = \
                saved_req
            record.clear()
            record.update(r_copy)
            payload.clear()
            payload.update(p_copy)
            req.clear()
            req.update(req_copy)
        log.append(staged)
        return {"entry_id": staged["entry_id"],
                "sequence": staged["sequence"],
                "op": staged["op"],
                "payload": {"identity": staged["payload"]
                            ["identity"],
                            "record": dict(staged["payload"]
                                           ["record"])},
                "prior_entry_id": staged["prior_entry_id"]}

    def replay(self, log):
        """ATOMIC read: validate the full log, re-derive the chain
        behind the oracle boundary, fold the registered ops in
        sequence order over the empty state - the log is never
        mutated, on success or rejection."""
        if type(log) is not list:
            _fail("malformed_wal_entry")
        self._validate_log_structure(log)
        saved_container, saved_entries = self._snapshot_log(log)
        frozen = self._freeze_log(log)
        try:
            tip = self._rederive_chain(frozen)
            state = {}
            for entry in frozen:
                if entry["op"] == "put":
                    state[entry["payload"]["identity"]] = \
                        dict(entry["payload"]["record"])
                else:
                    state.pop(entry["payload"]["identity"], None)
            result = {"state": state,
                      "state_id": state_id(state),
                      "head": tip,
                      "applied": len(frozen)}
        finally:
            self._restore_log(log, saved_container, saved_entries)
        return result


# -- fixtures -----------------------------------------------------------------


def _engine():
    return WalEngine(canonical_payload)


def _log_of(*ops):
    """Build a valid log by appending (op, fen) pairs."""
    log = []
    engine = _engine()
    for op, fen in ops:
        engine.append(log, {"op": op, "payload": _payload(fen)})
    return log


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_append_genesis_and_chain():
    engine = _engine()
    log = []
    before_reqs = []
    receipts = []
    for op, fen in [("put", STARTPOS), ("put", KINGS),
                    ("delete", STARTPOS)]:
        req = {"op": op, "payload": _payload(fen)}
        before_reqs.append(copy.deepcopy(req))
        receipts.append(engine.append(log, req))
    assert len(log) == 3
    for i, receipt in enumerate(receipts):
        assert set(receipt) == set(_FIELDS)
        assert receipt["sequence"] == i + 1
        assert _ENTRY_RE.fullmatch(receipt["entry_id"])
        assert receipt["prior_entry_id"] == (
            GENESIS if i == 0 else receipts[i - 1]["entry_id"])
        assert receipt == log[i]
        assert receipt is not log[i]  # detached receipt
        assert receipt["payload"] is not log[i]["payload"]


def test_happy_replay_fold():
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("delete", STARTPOS))
    before = copy.deepcopy(log)
    result = _engine().replay(log)
    identity_kings = _identity(_node(KINGS))
    assert set(result["state"]) == {identity_kings}
    assert result["state"][identity_kings] == _node(KINGS)
    assert _STATE_RE.fullmatch(result["state_id"])
    assert result["state_id"] == state_id(result["state"])
    assert result["head"] == log[-1]["entry_id"]
    assert result["applied"] == 3
    assert log == before  # replay never mutates


def test_empty_log_replay():
    result = _engine().replay([])
    assert result["state"] == {}
    assert result["state_id"] == state_id({})
    assert result["head"] == GENESIS
    assert result["applied"] == 0


def test_delete_absent_identity_is_noop():
    log = _log_of(("delete", KINGS))
    result = _engine().replay(log)
    assert result["state"] == {}
    assert result["applied"] == 1


def test_put_upsert_last_write_wins():
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("delete", STARTPOS), ("put", STARTPOS))
    result = _engine().replay(log)
    assert set(result["state"]) == {_identity(_node(STARTPOS)),
                                    _identity(_node(KINGS))}


def test_determinism():
    def build():
        engine = _engine()
        log = []
        receipts = [engine.append(log, {"op": op,
                                        "payload": _payload(fen)})
                    for op, fen in [("put", STARTPOS),
                                    ("put", KINGS)]]
        return log, receipts, engine.replay(log)
    assert build() == build()


# -- unknown_operation ----------------------------------------------------------


def test_unknown_operation_request():
    engine = _engine()
    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    req = {"op": "compact", "payload": _payload(KINGS)}
    req_before = copy.deepcopy(req)
    with pytest.raises(WalError) as exc:
        engine.append(log, req)
    assert exc.value.failure_class == "unknown_operation"
    assert exc.value.code == FAILURE_MAPPING["unknown_operation"]
    assert exc.value.code in ERROR_ENUM
    assert log == before
    assert req == req_before


def test_unknown_operation_log_entry():
    log = _log_of(("put", STARTPOS))
    log[0]["op"] = "squash"
    before = copy.deepcopy(log)
    engine = _engine()
    for call in (lambda: engine.replay(log),
                 lambda: engine.append(
                     log, {"op": "put",
                           "payload": _payload(KINGS)})):
        with pytest.raises(WalError) as exc:
            call()
        assert exc.value.failure_class == "unknown_operation"
    assert log == before


# -- malformed requests ---------------------------------------------------------

HOSTILE_REQUESTS = [None, True, 0, 1.5, [], "text",
                    {},
                    {"op": "put"},  # missing payload
                    {"op": "put", "payload": {}, "extra": 1},
                    {"op": None, "payload": {}},
                    {"op": True, "payload": {}},
                    {"op": "put", "payload": None},
                    {"op": "put", "payload": []},
                    {"op": "put", "payload": {"identity": "x"}},
                    {"op": "put",
                     "payload": {"identity": "x", "record": {},
                                 "extra": 1}},
                    {"op": "put", "payload": {"identity": None,
                                              "record": {}}},
                    {"op": "put", "payload": {"identity": "x",
                                              "record": None}}]


@pytest.mark.parametrize("req", HOSTILE_REQUESTS)
def test_total_over_hostile_requests(req):
    engine = _engine()
    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        engine.append(log, req)
    assert exc.value.failure_class == "malformed_wal_entry"
    assert exc.value.code == FAILURE_MAPPING["malformed_wal_entry"]
    assert exc.value.code in ERROR_ENUM
    assert log == before


def test_request_identity_record_mismatch():
    engine = _engine()
    log = []
    payload = _payload(STARTPOS)
    payload["identity"] = _identity(_node(KINGS))
    with pytest.raises(WalError) as exc:
        engine.append(log, {"op": "put", "payload": payload})
    assert exc.value.failure_class == "malformed_wal_entry"
    assert log == []


# -- hostile logs ---------------------------------------------------------------

HOSTILE_LOGS = [None, True, 0, 1.5, "text", {}]


@pytest.mark.parametrize("log", HOSTILE_LOGS)
def test_total_over_hostile_logs(log):
    engine = _engine()
    with pytest.raises(WalError) as exc:
        engine.replay(log)
    assert exc.value.failure_class == "malformed_wal_entry"
    with pytest.raises(WalError) as exc:
        engine.append(log, {"op": "put",
                            "payload": _payload(STARTPOS)})
    assert exc.value.failure_class == "malformed_wal_entry"


# -- hostile container subclasses ---------------------------------------------


class _RaisingKeysDict(dict):
    def keys(self):
        raise RuntimeError("evil keys")


class _RaisingItemsDict(dict):
    def items(self):
        raise RuntimeError("evil items")


class _RaisingIterList(list):
    def __iter__(self):
        raise RuntimeError("evil iter")


def test_hostile_container_subclasses_fail_closed_typed():
    """Attacker-controlled container SUBCLASSES: the engine never
    invokes their methods - exact built-in type boundaries reject
    them as malformed_wal_entry, typed, never a raw escape."""
    engine = _engine()
    # request: dict subclass whose keys() raises
    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    evil_req = _RaisingKeysDict(
        {"op": "put", "payload": _payload(KINGS)})
    with pytest.raises(WalError) as exc:
        engine.append(log, evil_req)
    assert exc.value.failure_class == "malformed_wal_entry"
    assert exc.value.code in ERROR_ENUM
    assert log == before
    # log: list subclass whose __iter__ raises - append and replay
    evil_log = _RaisingIterList(
        _log_of(("put", STARTPOS)))
    for call in (lambda: engine.replay(evil_log),
                 lambda: engine.append(
                     evil_log, {"op": "put",
                                "payload": _payload(KINGS)})):
        with pytest.raises(WalError) as exc:
            call()
        assert exc.value.failure_class == "malformed_wal_entry"
    # nested containers: entry, payload, record as raising dict
    # subclasses - replay and append both reject typed
    good_entry = _log_of(("put", STARTPOS))[0]
    nested = [
        ("entry-keys", _RaisingKeysDict(good_entry)),
        ("entry-items", _RaisingItemsDict(good_entry)),
        ("payload-keys", dict(
            good_entry,
            payload=_RaisingKeysDict(good_entry["payload"]))),
        ("record-keys", dict(
            good_entry,
            payload=dict(good_entry["payload"],
                         record=_RaisingKeysDict(
                             good_entry["payload"]
                             ["record"])))),
        ("record-items", dict(
            good_entry,
            payload=dict(good_entry["payload"],
                         record=_RaisingItemsDict(
                             good_entry["payload"]
                             ["record"])))),
    ]
    for _name, entry in nested:
        log = [entry]
        # deepcopy would invoke the hostile methods: snapshot
        # through the exact built-in constructors instead
        before = [dict(e) for e in log]
        for call in (lambda log=log: engine.replay(log),
                     lambda log=log: engine.append(
                         log, {"op": "put",
                               "payload": _payload(KINGS)})):
            with pytest.raises(WalError) as exc:
                call()
            assert exc.value.failure_class == \
                "malformed_wal_entry", _name
        assert log == before, _name


def test_well_behaved_subclass_containers_rejected():
    """Even a WELL-BEHAVED subclass (no raising methods) is not
    the exact built-in type: rejected typed, inputs unchanged."""
    engine = _engine()
    # control: exact built-in containers are accepted
    log = []
    engine.append(log, {"op": "put",
                        "payload": _payload(STARTPOS)})
    assert len(log) == 1
    before = copy.deepcopy(log)

    class QuietDict(dict):
        pass

    class QuietList(list):
        pass

    quiet_log = QuietList(_log_of(("put", STARTPOS)))
    log_before = copy.deepcopy(quiet_log)
    with pytest.raises(WalError) as exc:
        engine.replay(quiet_log)
    assert exc.value.failure_class == "malformed_wal_entry"
    assert quiet_log == log_before
    quiet_req = QuietDict({"op": "put",
                           "payload": _payload(KINGS)})
    req_before = copy.deepcopy(quiet_req)
    with pytest.raises(WalError) as exc:
        engine.append(log, quiet_req)
    assert exc.value.failure_class == "malformed_wal_entry"
    assert quiet_req == req_before
    assert log == before


def _hostile_entries():
    """Single-defect log entries; (name, mutate, class) where
    mutate edits a valid one-entry log in place."""
    cases = []

    def case(name, mutate, cls):
        cases.append((name, mutate, cls))

    case("entry-non-dict", lambda log: log.__setitem__(0, None),
         "malformed_wal_entry")
    case("entry-extra-key",
         lambda log: log[0].__setitem__("extra", 1),
         "malformed_wal_entry")
    case("entry-missing-key",
         lambda log: log[0].__delitem__("op"),
         "malformed_wal_entry")
    case("op-non-str", lambda log: log[0].__setitem__("op", True),
         "malformed_wal_entry")
    case("sequence-non-int",
         lambda log: log[0].__setitem__("sequence", "1"),
         "malformed_wal_entry")
    case("sequence-bool",
         lambda log: log[0].__setitem__("sequence", True),
         "malformed_wal_entry")
    case("sequence-gap",
         lambda log: log[0].__setitem__("sequence", 2),
         "sequence_conflict")
    case("sequence-zero",
         lambda log: log[0].__setitem__("sequence", 0),
         "sequence_conflict")
    case("entry-id-bad-grammar",
         lambda log: log[0].__setitem__("entry_id", "wal1:zz"),
         "malformed_wal_entry")
    case("entry-id-tampered",
         lambda log: log[0].__setitem__(
             "entry_id", "wal1:" + "f" * 64),
         "corrupt_chain")
    case("prior-bad-grammar",
         lambda log: log[0].__setitem__("prior_entry_id", "x"),
         "malformed_wal_entry")
    case("prior-link-broken",
         lambda log: log[0].__setitem__(
             "prior_entry_id", "wal1:" + "0" * 64),
         "corrupt_chain")
    case("payload-non-dict",
         lambda log: log[0].__setitem__("payload", []),
         "malformed_wal_entry")
    case("payload-identity-mismatch",
         lambda log: log[0]["payload"].__setitem__(
             "identity", _identity(_node(KINGS))),
         "malformed_wal_entry")
    case("record-non-dict",
         lambda log: log[0]["payload"].__setitem__("record", 1),
         "malformed_wal_entry")
    case("record-extra-field",
         lambda log: log[0]["payload"]["record"].__setitem__(
             "label", "x"),
         "malformed_wal_entry")
    case("record-bad-digest",
         lambda log: log[0]["payload"]["record"].__setitem__(
             "digest", "bad"),
         "malformed_wal_entry")
    case("record-digest-swapped-well-formed",
         lambda log: log[0]["payload"]["record"].__setitem__(
             "digest", _node(KINGS)["digest"]),
         "malformed_wal_entry")
    case("record-tampered-valid-record",
         lambda log: log[0]["payload"].__setitem__(
             "record", _node(AFTER_E4)),
         "malformed_wal_entry")  # identity no longer matches
    return cases


_HOSTILE_ENTRY_CASES = _hostile_entries()


@pytest.mark.parametrize("name,mutate,cls", _HOSTILE_ENTRY_CASES,
                         ids=[n for n, _, _ in
                              _HOSTILE_ENTRY_CASES])
def test_total_over_hostile_log_entries(name, mutate, cls):
    """Every single-defect entry maps to its typed class for BOTH
    replay and append; the log is left bit-identical."""
    engine = _engine()
    for call in ("replay", "append"):
        log = _log_of(("put", STARTPOS))
        mutate(log)
        before = copy.deepcopy(log)
        with pytest.raises(WalError) as exc:
            if call == "replay":
                engine.replay(log)
            else:
                engine.append(log, {"op": "put",
                                    "payload": _payload(KINGS)})
        assert exc.value.failure_class == cls
        assert exc.value.code == FAILURE_MAPPING[cls]
        assert exc.value.code in ERROR_ENUM
        assert log == before


def test_well_formed_wrong_digest_rejected_append_and_replay():
    """Adversarial digest substitution: a payload whose record
    carries ANOTHER valid record's well-formed pdv1: digest
    (variant, snapshot and identity preserved) is semantically
    invalid - BOTH append and replay reject it as
    malformed_wal_entry and leave every input bit-identical."""
    engine = _engine()
    forged = _payload(STARTPOS)
    forged["record"]["digest"] = _node(KINGS)["digest"]
    assert forged["record"]["digest"].startswith("pdv1:")
    # append path
    log = _log_of(("put", KINGS))
    before = copy.deepcopy(log)
    forged_before = copy.deepcopy(forged)
    with pytest.raises(WalError) as exc:
        engine.append(log, {"op": "put", "payload": forged})
    assert exc.value.failure_class == "malformed_wal_entry"
    assert exc.value.code in ERROR_ENUM
    assert log == before
    assert forged == forged_before
    # replay path: same substitution inside a committed entry
    log = _log_of(("put", STARTPOS))
    log[0]["payload"]["record"]["digest"] = _node(KINGS)["digest"]
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        engine.replay(log)
    assert exc.value.failure_class == "malformed_wal_entry"
    assert log == before


def test_record_replaced_and_identity_reforged_is_corrupt_chain():
    """A tampered entry whose identity is re-forged to MATCH the
    swapped record passes payload validation but diverges at
    entry-id recomputation: corrupt_chain, never silent."""
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    forged = _node(AFTER_E4)
    log[1]["payload"]["record"] = forged
    log[1]["payload"]["identity"] = _identity(forged)
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        _engine().replay(log)
    assert exc.value.failure_class == "corrupt_chain"
    assert log == before


def test_sequence_duplicate_in_longer_log():
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    log[2]["sequence"] = 2  # duplicate, no gap at position
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        _engine().replay(log)
    assert exc.value.failure_class == "sequence_conflict"
    assert log == before


# -- untrusted payload canonicalizer --------------------------------------------


def _hostile_oracles():
    def raising(identity, record):
        raise ValueError("boom")

    def bad_type(identity, record):
        return []

    def bad_none(identity, record):
        return None

    class EvilStr(str):
        def __hash__(self):
            raise RuntimeError("evil")

    def evil_str(identity, record):
        return EvilStr("canonical")

    def lone_high_surrogate(identity, record):
        return "\ud800"

    def lone_low_surrogate(identity, record):
        return "\udfff"

    return [("raising", raising), ("bad-type", bad_type),
            ("bad-none", bad_none), ("evil-str", evil_str),
            ("lone-high-surrogate", lone_high_surrogate),
            ("lone-low-surrogate", lone_low_surrogate)]


@pytest.mark.parametrize("name,oracle", _hostile_oracles(),
                         ids=[n for n, _ in _hostile_oracles()])
def test_hostile_canonicalizer(name, oracle):
    """A hostile canonicalizer fails closed as
    divergent_canonicalization for append AND replay; every input
    is bit-identical afterwards."""
    engine = WalEngine(oracle)
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    req = {"op": "delete", "payload": _payload(KINGS)}
    req_before = copy.deepcopy(req)
    with pytest.raises(WalError) as exc:
        engine.replay(log)
    assert exc.value.failure_class == \
        "divergent_canonicalization"
    with pytest.raises(WalError) as exc:
        engine.append(log, req)
    assert exc.value.failure_class == \
        "divergent_canonicalization"
    assert exc.value.code == FAILURE_MAPPING[
        "divergent_canonicalization"]
    assert exc.value.code in ERROR_ENUM
    assert log == before
    assert req == req_before


def test_inconsistent_canonicalizer_breaks_replay_chain():
    """An oracle that answers with a DIFFERENT well-formed string
    on replay than at append: the recomputed entry id diverges -
    typed corrupt_chain, never a silently accepted log."""
    calls = {"n": 0}

    def shifting(identity, record):
        calls["n"] += 1
        return canonical_payload(identity, record) + (
            "" if calls["n"] <= 2 else "x")

    engine = WalEngine(shifting)
    log = []
    engine.append(log, {"op": "put", "payload": _payload(STARTPOS)})
    engine.append(log, {"op": "put", "payload": _payload(KINGS)})
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        engine.replay(log)
    assert exc.value.failure_class == "corrupt_chain"
    assert log == before


def test_oracle_mutating_its_record_argument_append():
    """The oracle computes the honest canonical string, THEN
    mutates the record argument it was handed (field swap, clear,
    replace): the committed entry and receipt carry the ORIGINAL
    validated record, the entry id binds that original, caller
    inputs are unchanged, exactly one oracle call per entry."""
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    honest_log = _log_of(("put", STARTPOS), ("put", KINGS),
                         ("delete", KINGS))
    calls = {"n": 0}

    def oracle(identity, record):
        calls["n"] += 1
        canonical = canonical_payload(identity, record)
        record["digest"] = "pdv1:" + "f" * 64
        record["snapshot_fen"] = "garbage"
        record.clear()
        return canonical

    req = {"op": "delete", "payload": _payload(KINGS)}
    req_before = copy.deepcopy(req)
    receipt = WalEngine(oracle).append(log, req)
    assert calls["n"] == 3  # 2 entries + 1 new payload
    assert receipt == honest_log[2]
    assert log == honest_log  # original records committed
    assert log[:2] == before
    assert req == req_before
    # the chain re-derives cleanly with an honest oracle
    assert _engine().replay(log)["applied"] == 3


def test_oracle_mutating_its_record_argument_replay():
    """Same argument attack during REPLAY: the folded state
    carries the original validated records and the log is
    bit-identical."""
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    calls = {"n": 0}

    def oracle(identity, record):
        calls["n"] += 1
        canonical = canonical_payload(identity, record)
        record["digest"] = "pdv1:" + "e" * 64
        record.clear()
        record["forged"] = True
        return canonical

    result = WalEngine(oracle).replay(log)
    assert calls["n"] == 2
    assert result["state"] == {
        _identity(_node(STARTPOS)): _node(STARTPOS),
        _identity(_node(KINGS)): _node(KINGS)}
    assert result["state_id"] == state_id(result["state"])
    assert log == before


def test_oracle_mutating_log_during_append():
    """The oracle corrupts a LATER entry, injects one and deletes
    one during call 1 of an append: the commit derives EXACTLY
    from the pre-call frozen snapshot, the pre-existing entries
    are restored bit-identical, one oracle call per original
    entry plus one for the new payload."""
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    before = copy.deepcopy(log)
    calls = {"n": 0}

    def oracle(identity, record):
        calls["n"] += 1
        if calls["n"] == 1:
            log[1]["payload"]["record"]["snapshot_fen"] = []
            log[0]["injected"] = 1
            del log[2]
            log.append({"junk": True})
        return canonical_payload(identity, record)

    receipt = WalEngine(oracle).append(
        log, {"op": "delete", "payload": _payload(STARTPOS)})
    assert calls["n"] == 4  # 3 original entries + 1 new payload
    assert receipt["sequence"] == 4
    assert receipt["prior_entry_id"] == before[-1]["entry_id"]
    assert len(log) == 4
    assert log[:3] == before  # restored bit-identical
    assert log[3] == receipt
    # the chain still re-derives cleanly with an honest oracle
    result = _engine().replay(log)
    assert result["applied"] == 4


def test_oracle_mutating_request_during_append():
    """The oracle mutates the caller's live REQUEST during the
    payload call: the staged entry derives EXACTLY from the
    pre-call frozen request and the request is restored
    bit-identical."""
    honest_log = []
    _engine().append(honest_log, {"op": "put",
                                  "payload": _payload(STARTPOS)})
    log = []
    req = {"op": "put", "payload": _payload(STARTPOS)}
    req_before = copy.deepcopy(req)

    def oracle(identity, record):
        req["op"] = "delete"
        req["payload"]["record"]["digest"] = "forged"
        req["payload"]["identity"] = "forged"
        return canonical_payload(identity, record)

    receipt = WalEngine(oracle).append(log, req)
    assert receipt == honest_log[0]
    assert req == req_before
    assert log == honest_log


def test_request_cleared_during_oracle_stays_total():
    """An oracle CLEARING the request dict mid-call: the engine
    stays TOTAL (no raw escape - the receipt derives from the
    frozen request) and restores the caller's request."""
    log = []
    req = {"op": "put", "payload": _payload(KINGS)}
    req_before = copy.deepcopy(req)

    def oracle(identity, record):
        req.clear()
        return canonical_payload(identity, record)

    receipt = WalEngine(oracle).append(log, req)
    assert _ENTRY_RE.fullmatch(receipt["entry_id"])
    assert receipt["op"] == "put"
    assert req == req_before
    assert log != []


def test_replay_log_mutation_during_oracle_restored():
    """A hostile oracle with an external reference into the
    caller's log during REPLAY: even a successful replay leaves
    the log bit-identical and folds the pre-call snapshot."""
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)

    calls = {"n": 0}

    def oracle(identity, record):
        calls["n"] += 1
        if calls["n"] == 1:
            log[0]["payload"]["record"]["digest"] = "corrupted"
            log.clear()
        return canonical_payload(identity, record)

    result = WalEngine(oracle).replay(log)
    assert calls["n"] == 2
    assert result["applied"] == 2
    assert result["head"] == before[-1]["entry_id"]
    assert set(result["state"]) == {_identity(_node(STARTPOS)),
                                    _identity(_node(KINGS))}
    assert log == before


def test_one_oracle_call_per_entry_per_operation():
    calls = {"n": 0}

    def counting(identity, record):
        calls["n"] += 1
        return canonical_payload(identity, record)

    engine = WalEngine(counting)
    log = []
    engine.append(log, {"op": "put", "payload": _payload(STARTPOS)})
    assert calls["n"] == 1  # 0 entries + 1 new payload
    engine.append(log, {"op": "put", "payload": _payload(KINGS)})
    assert calls["n"] == 3  # 1 entry + 1 new payload
    engine.replay(log)
    assert calls["n"] == 5  # 2 entries
    engine.replay([])
    assert calls["n"] == 5  # empty replay: no oracle calls


# -- rollback: rejection leaves every input bit-identical -----------------------


def test_rejected_append_and_replay_rollback_bit_identical():
    # malformed append request
    engine = _engine()
    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        engine.append(log, {"op": "put", "payload": {}})
    assert exc.value.failure_class == "malformed_wal_entry"
    assert log == before

    # sequence conflict on replay
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    log[1]["sequence"] = 1
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        engine.replay(log)
    assert exc.value.failure_class == "sequence_conflict"
    assert log == before

    # corrupt chain on append (existing log tampered)
    log = _log_of(("put", STARTPOS))
    log[0]["entry_id"] = "wal1:" + "f" * 64
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        engine.append(log, {"op": "put", "payload": _payload(KINGS)})
    assert exc.value.failure_class == "corrupt_chain"
    assert log == before

    # oracle failure mid-append after earlier entries canonicalized
    calls = {"n": 0}

    def midway(identity, record):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("mid-log")
        return canonical_payload(identity, record)

    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    req = {"op": "delete", "payload": _payload(KINGS)}
    req_before = copy.deepcopy(req)
    with pytest.raises(WalError) as exc:
        WalEngine(midway).append(log, req)
    assert exc.value.failure_class == "divergent_canonicalization"
    assert log == before
    assert req == req_before


# -- behavioral mutants: the pinned defenses are load-bearing -------------------


def test_mutant_in_place_append_accepts_corrupt_tip():
    """Behavioral mutant: appending WITHOUT re-deriving the
    existing chain accepts a tampered log and extends the
    corruption. Counter-test: the real engine rejects it."""
    def mutant_append(engine, log, request):
        tip = log[-1]["entry_id"] if log else GENESIS
        canonical = canonical_payload(
            request["payload"]["identity"],
            request["payload"]["record"])
        entry = {"entry_id": engine._derive_entry_id(
            len(log) + 1, request["op"], canonical, tip),
            "sequence": len(log) + 1, "op": request["op"],
            "payload": request["payload"],
            "prior_entry_id": tip}
        log.append(entry)
        return entry

    log = _log_of(("put", STARTPOS))
    log[0]["entry_id"] = "wal1:" + "f" * 64  # silent corruption
    mutant_receipt = mutant_append(_engine(), log,
                                   {"op": "put",
                                    "payload": _payload(KINGS)})
    assert mutant_receipt["prior_entry_id"] == "wal1:" + "f" * 64
    assert len(log) == 2  # the mutant EXTENDED the corrupt log
    log = _log_of(("put", STARTPOS))
    log[0]["entry_id"] = "wal1:" + "f" * 64
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        _engine().append(log, {"op": "put",
                               "payload": _payload(KINGS)})
    assert exc.value.failure_class == "corrupt_chain"
    assert log == before


def test_mutant_replay_trusting_stored_ids():
    """Behavioral mutant: replay WITHOUT entry-id recomputation
    folds a tampered record into the state. Counter-test: the
    real engine fails closed as corrupt_chain."""
    def mutant_replay(log):
        state = {}
        for entry in log:
            if entry["op"] == "put":
                state[entry["payload"]["identity"]] = \
                    dict(entry["payload"]["record"])
            else:
                state.pop(entry["payload"]["identity"], None)
        return state

    log = _log_of(("put", STARTPOS))
    forged = _node(KINGS)
    log[0]["payload"]["record"] = forged
    log[0]["payload"]["identity"] = _identity(forged)
    mutant_state = mutant_replay(log)
    assert set(mutant_state) == {_identity(forged)}  # tamper landed
    before = copy.deepcopy(log)
    with pytest.raises(WalError) as exc:
        _engine().replay(log)
    assert exc.value.failure_class == "corrupt_chain"
    assert log == before


def test_mutant_live_request_reread_after_oracle():
    """Behavioral mutant: re-reading the LIVE request after the
    oracle call lets a mid-call mutation rewrite the staged
    entry; a cleared request escapes as a raw KeyError.
    Counter-test: the real engine derives only from the frozen
    request and stays total."""
    def mutant_append(engine, log, request):
        canonical = engine._canonicalize(
            request["payload"]["identity"],
            request["payload"]["record"])
        staged = {"entry_id": engine._derive_entry_id(
            len(log) + 1, request["op"], canonical, GENESIS),
            "sequence": len(log) + 1, "op": request["op"],
            "payload": {"identity": request["payload"]["identity"],
                        "record": dict(request["payload"]
                                       ["record"])},
            "prior_entry_id": GENESIS}
        log.append(staged)
        return staged

    req = {"op": "put", "payload": _payload(STARTPOS)}

    def forging(identity, record):
        req["op"] = "delete"
        return canonical_payload(identity, record)

    log = []
    receipt = mutant_append(WalEngine(forging), log, req)
    assert receipt["op"] == "delete"  # the forgery landed

    req2 = {"op": "put", "payload": _payload(STARTPOS)}

    def clearing(identity, record):
        req2.clear()
        return canonical_payload(identity, record)

    with pytest.raises(KeyError):
        mutant_append(WalEngine(clearing), [], req2)

    log = []
    req3 = {"op": "put", "payload": _payload(STARTPOS)}
    req3_before = copy.deepcopy(req3)

    def clearing2(identity, record):
        req3.clear()
        return canonical_payload(identity, record)

    receipt = WalEngine(clearing2).append(log, req3)
    assert receipt["op"] == "put"
    assert req3 == req3_before
    assert len(log) == 1


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
        "free-form-event-log")
    add("scope drift", ["contract", "role", "scope"],
        "multi-log-replication")
    add("record fields drift", ["contract", "record", "fields"],
        ["entry_id", "sequence", "op"])
    add("record exact drift", ["contract", "record", "exact"],
        False)
    add("entry id grammar drift",
        ["contract", "identifiers", "entry_id", "grammar"],
        "^.*$")
    add("entry id supplied",
        ["contract", "identifiers", "entry_id", "source"],
        "caller-supplied")
    add("genesis grammar drift",
        ["contract", "identifiers", "prior_entry_id", "grammar"],
        "^.*$")
    add("op registry drift",
        ["contract", "registry", "operations"],
        [{"op": "put", "payload_fields": ["identity"],
          "effect": "best-effort"}])
    add("registry closure dropped",
        ["contract", "registry", "closure"],
        "open-registry")
    add("sequencing drift", ["contract", "semantics",
                             "sequencing"],
        "approximately-ordered")
    add("chaining drift", ["contract", "semantics", "chaining"],
        "entries-unlinked")
    add("append order drift", ["contract", "semantics", "append"],
        "commit-then-validate")
    add("replay drift", ["contract", "semantics", "replay"],
        "fold-over-current-state")
    add("head verification drift",
        ["contract", "semantics", "head_verification"],
        "tip-trusted")
    add("oracle trusted", ["contract", "oracle_boundary", "role"],
        "canonicalizer-always-honest")
    add("single evaluation dropped",
        ["contract", "oracle_boundary", "single_evaluation"],
        "recanonicalize-allowed")
    add("frozen dropped",
        ["contract", "oracle_boundary", "frozen_snapshots"],
        "live-log-re-read")
    add("request freeze dropped",
        ["contract", "oracle_boundary", "request_freeze"],
        "live-request-re-read-after-oracle")
    add("output validation dropped",
        ["contract", "oracle_boundary", "output_validation"],
        "any-output")
    add("failure class dropped", ["contract", "failures",
                                  "classes"],
        ["malformed_wal_entry"])
    add("failure trigger drift",
        ["contract", "failures", "triggers", "corrupt_chain"],
        "never-fails")
    add("failure mapping drift",
        ["contract", "failures", "mapping", "corrupt_chain"],
        "internal")
    add("enum drift", ["contract", "errors", "closed_enum"],
        ["internal"])
    add("property drift", ["contract", "properties", "atomic"],
        "best-effort")
    add("base path drift", ["contract", "versioning",
                            "base_path"],
        "/store/wal/v0")
    add("link drift", ["contract", "links", "diff_contract"],
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
    assert covered >= {"role", "record", "identifiers", "registry",
                       "semantics", "oracle_boundary", "failures",
                       "errors", "properties", "versioning",
                       "links"}
