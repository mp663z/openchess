"""T0257: store idempotency contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/idempotency.yaml:
keyed exactly-once apply over a WAL-logged store. The request
payload and the source log validate through the LINKED WAL
machinery (imported, never restated); the ledger of idempotency
receipts is validated in full and bound to the live log entry at
each receipt's sequence; the request fingerprinter is UNTRUSTED
input behind a BaseException boundary (single evaluation per
apply, detached argument copy, frozen request, log and ledger,
exact built-in-str pinned-grammar output bound byte-exact to the
local canonical request serialization). An unseen key appends
exactly one WAL entry and one receipt, committed LAST; a seen key
with an identical fingerprint replays the stored receipt and
appends nothing; a seen key with a different fingerprint fails
closed. A rejected apply leaves log, ledger and request
bit-identical.
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
    WalEngine,
    WalError,
    _op_spec,
    _payload,
    canonical_payload,
)
from tests.test_t0239_rollback_contract import (  # noqa: E402
    RollbackEngine,
    archive_tail,
)
from tools.idempotency_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_OUTCOMES = _CC["outcomes"]["values"]
_RECEIPT_RE = re.compile(_CC["identifiers"]["receipt_id"]["grammar"])
_KEY_RE = re.compile(_CC["identifiers"]["idempotency_key"]["grammar"])
_FP_RE = re.compile(_CC["identifiers"]["request_fingerprint"]["grammar"])
_ENTRY_RE = re.compile(_CC["identifiers"]["entry_id"]["grammar"])
_REQUEST_KEYS = {"idempotency_key", "op", "payload"}

_WAL = WalEngine(canonical_payload)  # linked, trusted


class IdempotencyError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise IdempotencyError(cls, FAILURE_MAPPING[cls])


def request_fingerprint(op, payload):
    """THE pinned canonical request fingerprint: sha256 over the
    DOMAIN-SEPARATED, LENGTH-FRAMED serialization of EVERY exact
    frozen request field except the key (op, identity, and every
    node-record field), in order. The engine derives this LOCALLY
    and binds the untrusted fingerprinter's output to it
    byte-for-byte."""
    record = payload["record"]
    parts = ["idf1"]
    for field in (
        op,
        payload["identity"],
        record["variant"],
        record["digest"],
        record["snapshot_fen"],
    ):
        text = str(field)
        parts.append(f"{len(text)}:{text}")
    return "idf1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def derive_receipt_id(key, fingerprint, entry_id, sequence):
    return (
        "idr1:"
        + hashlib.sha256(f"{key}\n{fingerprint}\n{entry_id}\n{sequence}".encode()).hexdigest()
    )


def _valid_key(key):
    return type(key) is str and _KEY_RE.fullmatch(key) is not None


def _str_keyed(d):
    """HOSTILE-KEY GUARD for an exact built-in dict: every key must
    be an exact built-in str. Runs BEFORE any set construction or
    key comparison, so a key object whose __hash__ collides with a
    real field name and whose __eq__ raises is rejected typed and
    never compared. dict.keys is the unbound built-in: no
    caller-overridable method runs."""
    return all(type(k) is str for k in dict.keys(d))


def _payload_str_keyed(payload):
    """Guard a payload and its nested record (when they are exact
    dicts) before the linked WAL machinery compares their keys."""
    if type(payload) is not dict:
        return True
    if not _str_keyed(payload):
        return False
    record = payload.get("record")
    return type(record) is not dict or _str_keyed(record)


def _log_str_keyed(log):
    """Guard every exact-dict log entry, its payload and record
    before the linked WAL validation compares their keys."""
    if type(log) is not list:
        return True
    for entry in log:
        if type(entry) is not dict:
            continue
        if not _str_keyed(entry) or not _payload_str_keyed(entry.get("payload")):
            return False
    return True


class IdempotencyEngine:
    """The contract's pinned idempotent apply: exact request
    validation (payload through the linked WAL), full linked-WAL
    source validation, full ledger validation bound to the live
    log, frozen request/log/ledger, exactly one untrusted
    fingerprinter call on a DETACHED copy, byte-exact
    fingerprint binding, key-then-fingerprint deduplication, WAL
    entry and receipt committed together LAST."""

    def __init__(self, request_fingerprinter):
        self.fingerprinter = request_fingerprinter  # UNTRUSTED

    def _fingerprint(self, frozen_req):
        """THE fingerprinter boundary: raising ANY BaseException
        (including KeyboardInterrupt/SystemExit/GeneratorExit),
        non-exact-str, UTF-8-inencodable, wrong-grammar or
        request-divergent output fails closed as
        divergent_fingerprint."""
        payload = frozen_req["payload"]
        try:
            # DETACHED argument copy: the fingerprinter never sees
            # the frozen snapshot objects - mutation is inert
            out = self.fingerprinter(
                frozen_req["op"],
                {"identity": payload["identity"], "record": dict(payload["record"])},
            )
        except BaseException:
            # fail closed against the FULL BaseException surface
            # (totality): no raw escape from the untrusted oracle
            _fail("divergent_fingerprint")
        if type(out) is not str or _FP_RE.fullmatch(out) is None:
            _fail("divergent_fingerprint")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_fingerprint")
        # REQUEST-BOUND: byte-exact equality with the LOCAL
        # canonical derivation over the frozen request
        if out != request_fingerprint(frozen_req["op"], payload):
            _fail("divergent_fingerprint")
        return out

    @staticmethod
    def _validate_request(request):
        if (
            type(request) is not dict
            or not _str_keyed(request)
            or set(dict.keys(request)) != _REQUEST_KEYS
        ):
            _fail("malformed_idempotency_request")
        if not _valid_key(request["idempotency_key"]):
            _fail("malformed_idempotency_request")
        op = request["op"]
        if type(op) is not str or _op_spec(op) is None:
            _fail("malformed_idempotency_request")
        if not _payload_str_keyed(request["payload"]):
            _fail("malformed_idempotency_request")
        try:
            _WAL._validate_payload(op, request["payload"])
        except WalError:
            _fail("malformed_idempotency_request")

    @staticmethod
    def _validate_ledger(ledger, log):
        """Everything decidable WITHOUT the oracle: exact receipt
        shape, grammars, receipt-id re-derivation, unique keys,
        strictly increasing sequences, and binding to the exact
        live entry (id AND local fingerprint) at each
        sequence."""
        if type(ledger) is not list:
            _fail("corrupt_ledger")
        seen_keys = set()
        last_seq = 0
        for receipt in ledger:
            if (
                type(receipt) is not dict
                or not _str_keyed(receipt)
                or set(dict.keys(receipt)) != set(_FIELDS)
            ):
                _fail("corrupt_ledger")
            key = receipt["idempotency_key"]
            fp = receipt["request_fingerprint"]
            entry_id = receipt["entry_id"]
            seq = receipt["sequence"]
            rid = receipt["receipt_id"]
            if not _valid_key(key):
                _fail("corrupt_ledger")
            for value, grammar in ((fp, _FP_RE), (entry_id, _ENTRY_RE), (rid, _RECEIPT_RE)):
                if type(value) is not str or grammar.fullmatch(value) is None:
                    _fail("corrupt_ledger")
            if type(seq) is not int or seq <= last_seq or seq > len(log):
                _fail("corrupt_ledger")
            if key in seen_keys:
                _fail("corrupt_ledger")
            if rid != derive_receipt_id(key, fp, entry_id, seq):
                _fail("corrupt_ledger")
            entry = log[seq - 1]
            if (
                entry["entry_id"] != entry_id
                or request_fingerprint(entry["op"], entry["payload"]) != fp
            ):
                _fail("corrupt_ledger")
            seen_keys.add(key)
            last_seq = seq

    def apply(self, log, ledger, request):
        """ATOMIC: validate the request, the source log and the
        ledger; snapshot and FREEZE all three; one fingerprinter
        call; dedupe by key then byte-exact fingerprint; stage the
        WAL entry on a detached copy; commit entry and receipt
        together LAST - rejection leaves every input
        bit-identical."""
        self._validate_request(request)
        if not _log_str_keyed(log):
            _fail("corrupt_source")
        try:
            _WAL.replay(log)
        except WalError:
            _fail("corrupt_source")
        self._validate_ledger(ledger, log)
        # INPUT PRESERVATION snapshots (reference-preserving) +
        # FREEZE, all BEFORE the fingerprinter call.
        saved_log = WalEngine._snapshot_log(log)
        saved_ledger = (list(ledger), [(r, dict(r)) for r in ledger])
        payload = request["payload"]
        saved_req = (
            request,
            dict(request),
            payload,
            dict(payload),
            payload["record"],
            dict(payload["record"]),
        )
        frozen_req = {
            "idempotency_key": request["idempotency_key"],
            "op": request["op"],
            "payload": {"identity": payload["identity"], "record": dict(payload["record"])},
        }
        frozen_log = WalEngine._freeze_log(log)
        frozen_ledger = [dict(r) for r in ledger]
        try:
            token = self._fingerprint(frozen_req)
            stored = None
            for receipt in frozen_ledger:
                if receipt["idempotency_key"] == frozen_req["idempotency_key"]:
                    stored = receipt
            if stored is not None:
                if stored["request_fingerprint"] != token:
                    _fail("key_conflict")
                outcome, receipt, staged = "replayed", dict(stored), None
            else:
                work = WalEngine._freeze_log(frozen_log)
                staged = _WAL.append(
                    work, {"op": frozen_req["op"], "payload": copy.deepcopy(frozen_req["payload"])}
                )
                receipt = {
                    "receipt_id": derive_receipt_id(
                        frozen_req["idempotency_key"], token, staged["entry_id"], staged["sequence"]
                    ),
                    "idempotency_key": frozen_req["idempotency_key"],
                    "request_fingerprint": token,
                    "entry_id": staged["entry_id"],
                    "sequence": staged["sequence"],
                }
                outcome = "applied"
        finally:
            WalEngine._restore_log(log, *saved_log)
            container, receipts = saved_ledger
            for obj, snap in receipts:
                obj.clear()
                obj.update(snap)
            ledger[:] = container
            req, req_copy, pay, pay_copy, rec, rec_copy = saved_req
            rec.clear()
            rec.update(rec_copy)
            pay.clear()
            pay.update(pay_copy)
            req.clear()
            req.update(req_copy)
        if staged is not None:
            # COMMIT LAST: entry and receipt together
            log.append(staged)
            ledger.append(dict(receipt))
        return {"outcome": outcome, "receipt": dict(receipt)}


def _engine():
    return IdempotencyEngine(request_fingerprint)


def _req(key, fen=STARTPOS, op="put"):
    return {"idempotency_key": key, "op": op, "payload": _payload(fen)}


def _store(*items):
    """Build a valid (log, ledger) by applying (key, op, fen)."""
    log, ledger = [], []
    engine = _engine()
    for key, op, fen in items:
        engine.apply(log, ledger, _req(key, fen, op))
    return log, ledger


class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _snap(*objs):
    """Structural snapshot that NEVER calls a caller-supplied
    __eq__/__hash__: exact types, str keys by value, any other
    key or leaf object by identity."""

    def walk(o):
        t = type(o)
        if t is dict:
            return (
                "dict",
                tuple(
                    (k if type(k) is str else ("key", _Pin(k)), walk(v)) for k, v in dict.items(o)
                ),
            )
        if t in (list, tuple):
            return (t.__name__, tuple(walk(v) for v in o))
        if t in (str, int, bool, float, bytes, type(None)):
            return (t.__name__, o)
        return ("obj", _Pin(o))

    return walk(objs)


def _expect(cls, fn, *inputs):
    before = _snap(*inputs)
    try:
        fn()
    except IdempotencyError as caught:
        err = caught
    except BaseException as raw:  # noqa: BLE001 - totality probe
        # a RAW escape (including KeyboardInterrupt/SystemExit/
        # GeneratorExit) is a totality failure, reported as a test
        # failure rather than aborting the session
        pytest.fail(f"raw escape: {type(raw).__name__}")
    else:
        pytest.fail(f"expected {cls}, apply succeeded")
    assert err.failure_class == cls
    assert err.code == FAILURE_MAPPING[cls]
    assert err.code in ERROR_ENUM
    assert _snap(*inputs) == before  # atomic
    return err


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_applied():
    log, ledger = [], []
    req = _req("order-1")
    req_before = copy.deepcopy(req)
    result = _engine().apply(log, ledger, req)
    assert set(result) == {"outcome", "receipt"}
    assert result["outcome"] == "applied"
    assert result["outcome"] in _OUTCOMES
    receipt = result["receipt"]
    assert list(receipt) == _FIELDS
    assert _RECEIPT_RE.fullmatch(receipt["receipt_id"])
    assert _FP_RE.fullmatch(receipt["request_fingerprint"])
    assert receipt["idempotency_key"] == "order-1"
    assert receipt["sequence"] == 1
    assert len(log) == 1 and ledger == [receipt]
    assert log[0]["entry_id"] == receipt["entry_id"]
    assert _WAL.replay(log)["head"] == receipt["entry_id"]
    assert req == req_before


def test_replay_returns_identical_receipt_and_appends_nothing():
    log, ledger = [], []
    engine = _engine()
    first = engine.apply(log, ledger, _req("k1"))
    engine.apply(log, ledger, _req("k2", KINGS))
    log_before, ledger_before = copy.deepcopy((log, ledger))
    for _ in range(3):
        again = engine.apply(log, ledger, _req("k1"))
        assert again["outcome"] == "replayed"
        assert again["receipt"] == first["receipt"]
    assert (log, ledger) == (log_before, ledger_before)


def test_same_payload_distinct_keys_apply_twice():
    """The key, not the content, is the identity: two keys with
    byte-identical requests are two applies."""
    log, ledger = _store(("a", "put", STARTPOS), ("b", "put", STARTPOS))
    assert len(log) == 2 and len(ledger) == 2
    assert ledger[0]["request_fingerprint"] == ledger[1]["request_fingerprint"]
    assert ledger[0]["entry_id"] != ledger[1]["entry_id"]


def test_apply_extends_existing_log_and_ledger():
    log, ledger = _store(("a", "put", STARTPOS), ("b", "delete", STARTPOS))
    result = _engine().apply(log, ledger, _req("c", AFTER_E4))
    assert result["receipt"]["sequence"] == 3
    assert _WAL.replay(log)["applied"] == 3
    assert [r["sequence"] for r in ledger] == [1, 2, 3]


def test_ledger_may_cover_a_subset_of_the_log():
    """Unkeyed WAL appends are legal: receipts bind to exact
    positions, not every position needs one."""
    log, ledger = _store(("a", "put", STARTPOS))
    _WAL.append(log, {"op": "put", "payload": _payload(KINGS)})
    result = _engine().apply(log, ledger, _req("b", AFTER_E4))
    assert result["receipt"]["sequence"] == 3


def test_determinism():
    def build():
        log, ledger = _store(("a", "put", STARTPOS), ("b", "put", KINGS))
        replay = _engine().apply(log, ledger, _req("a"))
        return log, ledger, replay

    assert build() == build()


def test_one_fingerprinter_call_per_apply():
    calls = {"n": 0}

    def counting(op, payload):
        calls["n"] += 1
        return request_fingerprint(op, payload)

    engine = IdempotencyEngine(counting)
    log, ledger = [], []
    engine.apply(log, ledger, _req("k"))  # applied
    engine.apply(log, ledger, _req("k"))  # replayed
    with pytest.raises(IdempotencyError):
        engine.apply(log, ledger, _req("k", KINGS))  # conflict
    assert calls["n"] == 3
    # rejections decidable without the oracle never reach it
    with pytest.raises(IdempotencyError):
        engine.apply(log, ledger, _req(".bad"))
    with pytest.raises(IdempotencyError):
        engine.apply(log, "not-a-ledger", _req("k"))
    assert calls["n"] == 3


# -- key conflict ------------------------------------------------------------------


@pytest.mark.parametrize(
    "other",
    [_req("k", KINGS), _req("k", STARTPOS, "delete")],
    ids=["different-payload", "different-op"],
)
def test_key_conflict(other):
    log, ledger = _store(("k", "put", STARTPOS))
    _expect("key_conflict", lambda: _engine().apply(log, ledger, other), log, ledger, other)


# -- malformed ---------------------------------------------------------------------


def _hostile_requests():
    good = _req("k")
    out = [None, True, 0, "text", [], {}]
    missing = dict(good)
    del missing["op"]
    out.append(missing)
    out.append(dict(good, extra=1))
    out.append(dict(good, op="replace"))
    out.append(dict(good, op=1))
    out.append(dict(good, payload=None))
    out.append(dict(good, payload={"identity": "x"}))
    bad_rec = copy.deepcopy(good)
    bad_rec["payload"]["record"]["digest"] = "0" * 64
    out.append(bad_rec)
    wrong_identity = copy.deepcopy(good)
    wrong_identity["payload"]["identity"] = "other"
    out.append(wrong_identity)
    return out


class _StrKey(str):
    pass


BAD_KEYS = [
    None,
    1,
    True,
    b"k",
    "",
    ".lead",
    "-lead",
    "a" * 129,
    "k\n",
    "k k",
    "k/1",
    "ké",
    _StrKey("k"),
]
GOOD_KEYS = ["k", "A", "0", "a" * 128, "order:2026-09.v1_x"]


@pytest.mark.parametrize("req", _hostile_requests())
def test_total_over_hostile_requests(req):
    log, ledger = _store(("a", "put", STARTPOS))
    _expect("malformed_idempotency_request", lambda: _engine().apply(log, ledger, req), log, ledger)


@pytest.mark.parametrize("key", BAD_KEYS, ids=repr)
def test_key_grammar_rejects(key):
    log, ledger = [], []
    req = _req("k")
    req["idempotency_key"] = key
    _expect("malformed_idempotency_request", lambda: _engine().apply(log, ledger, req), log, ledger)


@pytest.mark.parametrize("key", GOOD_KEYS)
def test_key_grammar_boundary_accepts(key):
    log, ledger = [], []
    assert _engine().apply(log, ledger, _req(key))["outcome"] == "applied"


# -- corrupt source + corrupt ledger ---------------------------------------------------


def _corrupt_sources():
    def seq_gap(log):
        log[1]["sequence"] = 1

    def chain_break(log):
        log[1]["prior_entry_id"] = "wal1:" + "0" * 64

    def entry_tamper(log):
        log[0]["entry_id"] = "wal1:" + "f" * 64

    def not_a_list(log):
        return {"log": log}

    return [
        ("sequence-gap", seq_gap),
        ("chain-break", chain_break),
        ("entry-tamper", entry_tamper),
        ("not-a-list", not_a_list),
    ]


@pytest.mark.parametrize("name,mutate", _corrupt_sources(), ids=[n for n, _ in _corrupt_sources()])
def test_corrupt_source_log(name, mutate):
    log, ledger = _store(("a", "put", STARTPOS), ("b", "put", KINGS))
    log = mutate(log) or log
    req = _req("c", AFTER_E4)
    _expect("corrupt_source", lambda: _engine().apply(log, ledger, req), log, ledger, req)


def _resign(receipt):
    receipt["receipt_id"] = derive_receipt_id(
        receipt["idempotency_key"],
        receipt["request_fingerprint"],
        receipt["entry_id"],
        receipt["sequence"],
    )


def _corrupt_ledgers():
    def m(fn):
        return fn

    @m
    def not_a_list(ledger, log):
        return tuple(ledger)

    @m
    def receipt_not_dict(ledger, log):
        ledger[0] = list(ledger[0].items())

    @m
    def extra_field(ledger, log):
        ledger[0]["outcome"] = "applied"

    @m
    def missing_field(ledger, log):
        del ledger[0]["sequence"]

    @m
    def bad_receipt_grammar(ledger, log):
        ledger[0]["receipt_id"] = "idr1:zz"

    @m
    def bad_fingerprint_grammar(ledger, log):
        ledger[0]["request_fingerprint"] = "idf1:" + "Z" * 64

    @m
    def bad_key(ledger, log):
        ledger[0]["idempotency_key"] = ".x"
        _resign(ledger[0])

    @m
    def tampered_receipt_id(ledger, log):
        ledger[0]["receipt_id"] = "idr1:" + "0" * 64

    @m
    def bool_sequence(ledger, log):
        ledger[0]["sequence"] = True
        _resign(ledger[0])

    @m
    def sequence_out_of_range(ledger, log):
        ledger[1]["sequence"] = 9
        _resign(ledger[1])

    @m
    def sequence_zero(ledger, log):
        ledger[0]["sequence"] = 0
        _resign(ledger[0])

    @m
    def wrong_entry_resigned(ledger, log):
        ledger[0]["entry_id"] = log[1]["entry_id"]
        _resign(ledger[0])

    @m
    def fingerprint_of_other_request_resigned(ledger, log):
        ledger[0]["request_fingerprint"] = request_fingerprint("put", _payload(AFTER_E4))
        _resign(ledger[0])

    @m
    def duplicate_key_resigned(ledger, log):
        ledger[1]["idempotency_key"] = ledger[0]["idempotency_key"]
        _resign(ledger[1])

    @m
    def duplicate_sequence(ledger, log):
        ledger.append(dict(ledger[1], idempotency_key="z"))
        _resign(ledger[2])

    @m
    def reordered(ledger, log):
        ledger.reverse()

    @m
    def str_subclass_key(ledger, log):
        ledger[0]["idempotency_key"] = _StrKey(ledger[0]["idempotency_key"])

    return [
        (fn.__name__, fn)
        for fn in (
            not_a_list,
            receipt_not_dict,
            extra_field,
            missing_field,
            bad_receipt_grammar,
            bad_fingerprint_grammar,
            bad_key,
            tampered_receipt_id,
            bool_sequence,
            sequence_out_of_range,
            sequence_zero,
            wrong_entry_resigned,
            fingerprint_of_other_request_resigned,
            duplicate_key_resigned,
            duplicate_sequence,
            reordered,
            str_subclass_key,
        )
    ]


@pytest.mark.parametrize("name,mutate", _corrupt_ledgers(), ids=[n for n, _ in _corrupt_ledgers()])
def test_corrupt_ledger(name, mutate):
    log, ledger = _store(("a", "put", STARTPOS), ("b", "put", KINGS))
    ledger = mutate(ledger, log) or ledger
    req = _req("a")  # would otherwise replay
    _expect("corrupt_ledger", lambda: _engine().apply(log, ledger, req), log, ledger, req)


# -- untrusted request fingerprinter ------------------------------------------------------


def _hostile_fingerprinters():
    def raising(op, payload):
        raise ValueError("boom")

    def bad_type(op, payload):
        return None

    def bad_grammar(op, payload):
        return "idf1:zz"

    def wrong_prefix(op, payload):
        return "arc1:" + "0" * 64

    def trailing_newline(op, payload):
        return request_fingerprint(op, payload) + "\n"

    class EvilStr(str):
        def __eq__(self, other):
            return True

        __hash__ = str.__hash__

    def evil_str(op, payload):
        return EvilStr(request_fingerprint(op, payload))

    def lone_surrogate(op, payload):
        return "\ud800"

    def arbitrary_valid_token(op, payload):
        return "idf1:" + "f" * 64

    def other_request_token(op, payload):
        return request_fingerprint("delete", payload)

    def key_included_token(op, payload):
        return request_fingerprint(op + "k", payload)

    def keyboard_interrupt(op, payload):
        raise KeyboardInterrupt("boom")

    def system_exit(op, payload):
        raise SystemExit("boom")

    def generator_exit(op, payload):
        raise GeneratorExit("boom")

    return [
        (fn.__name__, fn)
        for fn in (
            raising,
            bad_type,
            bad_grammar,
            wrong_prefix,
            trailing_newline,
            evil_str,
            lone_surrogate,
            arbitrary_valid_token,
            other_request_token,
            key_included_token,
            keyboard_interrupt,
            system_exit,
            generator_exit,
        )
    ]


@pytest.mark.parametrize(
    "name,fp", _hostile_fingerprinters(), ids=[n for n, _ in _hostile_fingerprinters()]
)
@pytest.mark.parametrize("seen", [False, True], ids=["new", "seen"])
def test_hostile_fingerprinter(name, fp, seen):
    """A hostile fingerprinter fails closed as
    divergent_fingerprint on BOTH the apply and the replay path:
    nothing commits, nothing replays, inputs bit-identical."""
    log, ledger = _store(("a", "put", STARTPOS))
    req = _req("a") if seen else _req("b", KINGS)
    _expect(
        "divergent_fingerprint",
        lambda: IdempotencyEngine(fp).apply(log, ledger, req),
        log,
        ledger,
        req,
    )


def test_stateful_alternating_fingerprinter_never_commits():
    calls = {"n": 0}

    def alternating(op, payload):
        calls["n"] += 1
        honest = request_fingerprint(op, payload)
        return honest if calls["n"] % 2 == 0 else "idf1:" + "1" * 64

    engine = IdempotencyEngine(alternating)
    log, ledger = [], []
    req = _req("k")
    _expect("divergent_fingerprint", lambda: engine.apply(log, ledger, req), log, ledger, req)
    assert engine.apply(log, ledger, req)["outcome"] == "applied"
    _expect("divergent_fingerprint", lambda: engine.apply(log, ledger, req), log, ledger, req)


def test_fingerprint_binds_every_request_field():
    base = _payload(STARTPOS)
    token = request_fingerprint("put", base)
    assert request_fingerprint("delete", base) != token
    assert request_fingerprint("put", _payload(KINGS)) != token
    for field in ("variant", "digest", "snapshot_fen"):
        other = copy.deepcopy(base)
        other["record"][field] = other["record"][field] + "x"
        assert request_fingerprint("put", other) != token
    same_len = copy.deepcopy(base)
    same_len["identity"] = "z" * len(base["identity"])
    assert request_fingerprint("put", same_len) != token
    # length framing: shifting a boundary never collides
    a = {"identity": "ab", "record": {"variant": "c", "digest": "d", "snapshot_fen": "e"}}
    b = {"identity": "a", "record": {"variant": "bc", "digest": "d", "snapshot_fen": "e"}}
    assert request_fingerprint("put", a) != request_fingerprint("put", b)


def test_fingerprinter_mutating_its_argument_is_inert():
    def mutating(op, payload):
        token = request_fingerprint(op, payload)
        payload["record"]["digest"] = "forged"
        payload.clear()
        return token

    log, ledger = [], []
    req = _req("k")
    req_before = copy.deepcopy(req)
    result = IdempotencyEngine(mutating).apply(log, ledger, req)
    assert result["outcome"] == "applied"
    assert req == req_before
    assert log[0]["payload"] == req_before["payload"]
    assert _WAL.replay(log)["head"] == result["receipt"]["entry_id"]


def test_fingerprinter_mutating_live_inputs_mid_call():
    """The fingerprinter corrupts the caller's live log, ledger
    and request mid-call: the commit derives only from the frozen
    snapshots and every input is restored before the commit."""
    log, ledger = _store(("a", "put", STARTPOS))
    log_before, ledger_before = copy.deepcopy((log, ledger))
    req = _req("b", KINGS)
    req_before = copy.deepcopy(req)

    def hostile(op, payload):
        token = request_fingerprint(op, payload)
        log[0]["op"] = "delete"
        log.append({"junk": True})
        ledger[0]["sequence"] = 99
        ledger.clear()
        req["idempotency_key"] = "a"
        req["payload"]["record"]["digest"] = "x"
        return token

    result = IdempotencyEngine(hostile).apply(log, ledger, req)
    assert result["outcome"] == "applied"
    assert req == req_before
    assert log[:1] == log_before and ledger[:1] == ledger_before
    assert result["receipt"]["idempotency_key"] == "b"
    assert len(log) == 2 and len(ledger) == 2
    assert _WAL.replay(log)["head"] == result["receipt"]["entry_id"]


# -- rollback ------------------------------------------------------------------------


def test_rejected_apply_leaves_inputs_bit_identical():
    log, ledger = _store(("a", "put", STARTPOS))
    engine = _engine()
    for cls, req, lg in [
        ("malformed_idempotency_request", _req(""), ledger),
        ("key_conflict", _req("a", KINGS), ledger),
        ("corrupt_ledger", _req("b"), ledger + [{}]),
    ]:
        _expect(cls, lambda r=req, g=lg: engine.apply(log, g, r), log, lg, req)
    req = _req("b", KINGS)
    _expect(
        "divergent_fingerprint",
        lambda: IdempotencyEngine(lambda o, p: 0).apply(log, ledger, req),
        log,
        ledger,
        req,
    )


def test_log_rollback_makes_stale_ledger_fail_closed():
    """ROLLBACK (linked T0239): truncating the log past a receipt
    leaves the ledger naming a vanished entry - every apply fails
    closed as corrupt_ledger, the stale receipt is NEVER replayed.
    Trimming the ledger to the surviving prefix restores service;
    the rolled-back key then re-applies as a fresh entry."""
    log, ledger = _store(("a", "put", STARTPOS), ("b", "put", KINGS))
    stale = dict(ledger[1])
    RollbackEngine(archive_tail).rollback(log, {"target_sequence": 1})
    assert len(log) == 1
    engine = _engine()
    for req in (_req("b", KINGS), _req("a"), _req("c", AFTER_E4)):
        _expect("corrupt_ledger", lambda r=req: engine.apply(log, ledger, r), log, ledger, req)
    ledger = [r for r in ledger if r["sequence"] <= len(log)]
    again = engine.apply(log, ledger, _req("b", KINGS))
    assert again["outcome"] == "applied"
    assert again["receipt"]["sequence"] == 2
    assert again["receipt"] == stale  # deterministic re-apply


def test_log_rollback_then_divergent_append_rejects_stale_receipt():
    """After a rollback, a DIFFERENT entry lands at the stale
    receipt's sequence: the receipt's entry binding fails, so the
    old key can never replay the wrong entry."""
    log, ledger = _store(("a", "put", STARTPOS), ("b", "put", KINGS))
    RollbackEngine(archive_tail).rollback(log, {"target_sequence": 1})
    _WAL.append(log, {"op": "put", "payload": _payload(AFTER_E4)})
    req = _req("b", KINGS)
    _expect("corrupt_ledger", lambda: _engine().apply(log, ledger, req), log, ledger, req)


# -- behavioral mutants ------------------------------------------------------------------


def test_mutant_key_only_deduplication():
    """Mutant: deduplicating on the key alone silently returns
    the stored receipt for a DIFFERENT request - the second write
    is lost. Counter-test: the real engine fails closed."""

    def mutant(log, ledger, request):
        for r in ledger:
            if r["idempotency_key"] == request["idempotency_key"]:
                return {"outcome": "replayed", "receipt": dict(r)}
        return _engine().apply(log, ledger, request)

    log, ledger = _store(("k", "put", STARTPOS))
    lost = mutant(log, ledger, _req("k", KINGS))
    assert lost["outcome"] == "replayed"  # KINGS write lost
    req = _req("k", KINGS)
    _expect("key_conflict", lambda: _engine().apply(log, ledger, req), log, ledger, req)


def test_mutant_no_deduplication():
    """Mutant: an apply that ignores the ledger double-appends on
    retry. Counter-test: the real engine replays."""

    def mutant(log, ledger, request):
        entry = _WAL.append(log, {"op": request["op"], "payload": request["payload"]})
        return entry

    log, ledger = [], []
    mutant(log, ledger, _req("k"))
    mutant(log, ledger, _req("k"))
    assert len(log) == 2  # the retry DUPLICATED the write
    log, ledger = [], []
    _engine().apply(log, ledger, _req("k"))
    assert _engine().apply(log, ledger, _req("k"))["outcome"] == "replayed"
    assert len(log) == 1


def test_mutant_grammar_only_fingerprint():
    """Mutant: validating only the token's grammar lets a lying
    fingerprinter write a receipt whose fingerprint does not
    describe the entry - later a different request with the same
    lie would replay it. Counter-test: byte-exact binding fails
    closed and the forged receipt is rejected as corrupt_ledger."""
    lie = "idf1:" + "f" * 64

    def mutant(log, ledger, request):
        entry = _WAL.append(log, {"op": request["op"], "payload": request["payload"]})
        receipt = {
            "receipt_id": derive_receipt_id(
                request["idempotency_key"], lie, entry["entry_id"], entry["sequence"]
            ),
            "idempotency_key": request["idempotency_key"],
            "request_fingerprint": lie,
            "entry_id": entry["entry_id"],
            "sequence": entry["sequence"],
        }
        ledger.append(receipt)

    log, ledger = [], []
    mutant(log, ledger, _req("k"))
    assert ledger[0]["request_fingerprint"] == lie
    req = _req("n", KINGS)
    _expect("corrupt_ledger", lambda: _engine().apply(log, ledger, req), log, ledger, req)
    log, ledger = [], []
    req = _req("k")
    _expect(
        "divergent_fingerprint",
        lambda: IdempotencyEngine(lambda o, p: lie).apply(log, ledger, req),
        log,
        ledger,
        req,
    )


def test_mutant_ledger_unbound_to_log():
    """Mutant: replaying from the ledger without binding it to the
    live log returns a receipt for an entry the rollback erased.
    Counter-test: corrupt_ledger."""

    def mutant(log, ledger, request):
        for r in ledger:
            if r["idempotency_key"] == request["idempotency_key"]:
                return {"outcome": "replayed", "receipt": dict(r)}
        raise AssertionError("unreachable")

    log, ledger = _store(("a", "put", STARTPOS), ("b", "put", KINGS))
    RollbackEngine(archive_tail).rollback(log, {"target_sequence": 1})
    ghost = mutant(log, ledger, _req("b", KINGS))
    assert ghost["receipt"]["sequence"] > len(log)  # a ghost write
    req = _req("b", KINGS)
    _expect("corrupt_ledger", lambda: _engine().apply(log, ledger, req), log, ledger, req)


def test_mutant_commit_before_fingerprint():
    """Mutant: appending the WAL entry BEFORE the fingerprinter
    call leaves an orphan entry with no receipt when the oracle
    fails - a retry then double-applies. Counter-test: commit
    last."""

    def mutant(engine, log, ledger, request):
        _WAL.append(log, {"op": request["op"], "payload": request["payload"]})
        engine._fingerprint(copy.deepcopy(request))

    log, ledger = [], []
    with pytest.raises(IdempotencyError):
        mutant(IdempotencyEngine(lambda o, p: None), log, ledger, _req("k"))
    assert len(log) == 1 and ledger == []  # orphan write
    log, ledger = [], []
    req = _req("k")
    _expect(
        "divergent_fingerprint",
        lambda: IdempotencyEngine(lambda o, p: None).apply(log, ledger, req),
        log,
        ledger,
        req,
    )


def test_mutant_live_request_reread_after_fingerprinter():
    """Mutant: re-reading the LIVE request after the oracle lets a
    mid-call rewrite commit a payload the fingerprint never
    described. Counter-test: the frozen request is committed."""
    req = _req("k")

    def rewriting(op, payload):
        token = request_fingerprint(op, payload)
        req["payload"] = _payload(KINGS)
        return token

    def mutant(log, request):
        token = rewriting(request["op"], request["payload"])
        _WAL.append(log, {"op": request["op"], "payload": request["payload"]})
        return token

    log = []
    token = mutant(log, req)
    assert request_fingerprint("put", log[0]["payload"]) != token
    req = _req("k")
    log, ledger = [], []
    result = IdempotencyEngine(rewriting).apply(log, ledger, req)
    assert log[0]["payload"] == _payload(STARTPOS)
    assert request_fingerprint("put", log[0]["payload"]) == result["receipt"]["request_fingerprint"]
    assert req == _req("k")


# -- lint mutants -------------------------------------------------------------------------


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

    c = ["contract"]
    add("role kind drift", c + ["role", "kind"], "best-effort-dedupe")
    add("not_scope drift", c + ["role", "not_scope"], "key-expiry-owned-here")
    add("record fields drift", c + ["record", "fields"], ["receipt_id"])
    add("record exact drift", c + ["record", "exact"], False)
    add("record exact int confusion", c + ["record", "exact"], 1)
    add("receipt id grammar drift", c + ["identifiers", "receipt_id", "grammar"], "^.*$")
    add("receipt id supplied", c + ["identifiers", "receipt_id", "source"], "caller-supplied")
    add("key grammar drift", c + ["identifiers", "idempotency_key", "grammar"], "^.*$")
    add(
        "fingerprint derivation drift",
        c + ["identifiers", "request_fingerprint", "derivation"],
        "sha256-over-request-including-key",
    )
    add(
        "fingerprint binding dropped",
        c + ["identifiers", "request_fingerprint", "source"],
        "fingerprinter-output-shape-validated-only",
    )
    add("entry id grammar drift", c + ["identifiers", "entry_id", "grammar"], "^.*$")
    add("outcome added", c + ["outcomes", "values"], ["applied", "replayed", "overwritten"])
    add("outcomes open", c + ["outcomes", "closed"], False)
    add("replayed semantics drift", c + ["outcomes", "replayed"], "seen-key-returns-stored-receipt")
    add("request validation dropped", c + ["semantics", "request_validation"], "trusted")
    add("source validation dropped", c + ["semantics", "source_validation"], "log-trusted")
    add("ledger binding dropped", c + ["semantics", "ledger_binding"], "ledger-trusted")
    add("key-only dedupe", c + ["semantics", "deduplication"], "key-only")
    add("conflict overwrites", c + ["semantics", "conflict"], "last-write-wins")
    add("commit drift", c + ["semantics", "commit"], "append-then-fingerprint")
    add("oracle trusted", c + ["oracle_boundary", "role"], "fingerprinter-always-honest")
    add("single evaluation dropped", c + ["oracle_boundary", "single_evaluation"], "retry-allowed")
    add("frozen dropped", c + ["oracle_boundary", "frozen_snapshots"], "live-re-read")
    add("output validation dropped", c + ["oracle_boundary", "output_validation"], "any-output")
    add("failure class dropped", c + ["failures", "classes"], ["corrupt_source"])
    add("failure trigger drift", c + ["failures", "triggers", "key_conflict"], "never")
    add("failure mapping drift", c + ["failures", "mapping", "key_conflict"], "internal")
    add("failures open", c + ["failures", "closed"], False)
    add("enum drift", c + ["errors", "closed_enum"], ["internal"])
    add(
        "retryable drift",
        c + ["errors", "shape", "retryable_true_only_for"],
        ["internal", "key_conflict"],
    )
    add("property drift", c + ["properties", "idempotent"], "at-least-once")
    add("rollback property drift", c + ["properties", "rollback"], "stale-receipts-replayed")
    add("base path drift", c + ["versioning", "base_path"], "/store/idempotency/v0")
    add("link drift", c + ["links", "wal_contract"], "data/contracts/san.yaml")
    add("undeclared section", c + ["expiry"], {"ttl": 60})
    add("contract id drift", c + ["id"], "store-dedupe")
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 30
    for name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)
        assert name


def test_mutants_never_silent_subset():
    base = yaml.safe_load(CONTRACT.read_text())["contract"]
    covered = set()
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if (
                content != base.get(section)
                or type(content) is not type(base.get(section))
                or repr(content) != repr(base.get(section))
            ):
                covered.add(section)
    assert covered >= set(base)


# -- hostile dict keys --------------------------------------------------------------


class _CollidingKey:
    """Non-str key whose hash equals a real field name's hash and
    whose __eq__ raises: any set construction or key comparison
    against the real field name escapes raw unless guarded."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile key __eq__")


class _CollidingStrKey(str):
    """str-subclass variant of the same attack."""

    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        raise RuntimeError("hostile str key __eq__")


def _swap_key(d, name, cls):
    d[cls(name)] = d.pop(name)


def _hostile_key_cases():
    def request(cls):
        log, ledger = _store(("a", "put", STARTPOS))
        req = _req("b", KINGS)
        _swap_key(req, "op", cls)
        return "malformed_idempotency_request", log, ledger, req

    def payload(cls):
        log, ledger = _store(("a", "put", STARTPOS))
        req = _req("b", KINGS)
        _swap_key(req["payload"], "identity", cls)
        return "malformed_idempotency_request", log, ledger, req

    def record(cls):
        log, ledger = _store(("a", "put", STARTPOS))
        req = _req("b", KINGS)
        _swap_key(req["payload"]["record"], "digest", cls)
        return "malformed_idempotency_request", log, ledger, req

    def ledger_receipt(cls):
        log, ledger = _store(("a", "put", STARTPOS))
        _swap_key(ledger[0], "sequence", cls)
        return "corrupt_ledger", log, ledger, _req("a")

    def log_entry(cls):
        log, ledger = _store(("a", "put", STARTPOS))
        _swap_key(log[0], "op", cls)
        return "corrupt_source", log, ledger, _req("b", KINGS)

    def log_payload(cls):
        log, ledger = _store(("a", "put", STARTPOS))
        _swap_key(log[0]["payload"], "identity", cls)
        return "corrupt_source", log, ledger, _req("b", KINGS)

    def log_record(cls):
        log, ledger = _store(("a", "put", STARTPOS))
        _swap_key(log[0]["payload"]["record"], "digest", cls)
        return "corrupt_source", log, ledger, _req("b", KINGS)

    return [
        (fn.__name__, fn)
        for fn in (request, payload, record, ledger_receipt, log_entry, log_payload, log_record)
    ]


@pytest.mark.parametrize("cls", [_CollidingKey, _CollidingStrKey], ids=["non-str", "str-subclass"])
@pytest.mark.parametrize(
    "name,build", _hostile_key_cases(), ids=[n for n, _ in _hostile_key_cases()]
)
def test_hostile_colliding_dict_key(name, build, cls):
    """At EVERY dict boundary (request, payload, record, ledger
    receipt, log entry, log payload, log record) a key whose hash
    collides with a real field name and whose __eq__ raises fails
    closed typed, never raw, inputs bit-identical, and the
    fingerprinter is never reached."""
    calls = {"n": 0}

    def counting(op, payload):
        calls["n"] += 1
        return request_fingerprint(op, payload)

    failure, log, ledger, req = build(cls)
    _expect(failure, lambda: IdempotencyEngine(counting).apply(log, ledger, req), log, ledger, req)
    assert calls["n"] == 0


@pytest.mark.parametrize(
    "name,build", _hostile_key_cases(), ids=[n for n, _ in _hostile_key_cases()]
)
def test_mutant_without_hostile_key_guard_escapes_raw(name, build, monkeypatch):
    """Engine mutant: with the key-type guard removed, the same
    input escapes RAW (the hostile __eq__ fires inside set
    comparison or the linked WAL) - the guard is what makes the
    boundary total."""
    monkeypatch.setattr(sys.modules[__name__], "_str_keyed", lambda d: True)
    # the linked WAL now carries its own key-type guard (#201); strip
    # it too so the mutant is truly guardless at every layer
    monkeypatch.setattr(
        sys.modules["tests.test_t0212_wal_contract"], "_exact_str_keys", lambda m: True
    )
    _failure, log, ledger, req = build(_CollidingKey)
    with pytest.raises(RuntimeError, match="hostile key"):
        _engine().apply(log, ledger, req)


class _PinProbeLeaf:
    pass


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original leaf and
    the second can land on its freed address, which a bare id() would
    miss. The fingerprint pins the original, so the swap goes red."""
    value = {"k": _PinProbeLeaf()}
    before = _snap(value)
    value["k"] = _PinProbeLeaf()
    value["k"] = _PinProbeLeaf()
    assert _snap(value) != before
