"""T0266: store corruption contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/corruption.yaml:
scan a WAL-logged store for corruption and salvage it to the
longest verified prefix. Every prefix is judged by the LINKED WAL
machinery (imported, never restated); the log must lie inside the
closed scan domain (exact built-in acyclic alias-free tree of
dict/list/str/int/bool/None within nesting depth 8) or the scan is
malformed; the quarantine sink is UNTRUSTED input behind a
BaseException boundary (one call per salvage, zero when clean,
detached argument copy, frozen log and request, exact built-in-str
pinned-grammar output bound byte-for-byte to the local canonical
suffix encoding); the commit removes exactly the corrupt suffix,
only after the full scan, the loss check and quarantine - a
rejected scan leaves every input bit-identical.
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
from tools.corruption_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_CLASSES,
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_BITS,
    VERDICTS,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_SCAN_RE = re.compile(_CC["identifiers"]["scan_id"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["verified_head"]["grammar"])
_TOKEN_RE = re.compile(
    _CC["identifiers"]["quarantine_token"]["grammar"])

_WAL = WalEngine(canonical_payload)  # linked, trusted


class CorruptionError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise CorruptionError(cls, FAILURE_MAPPING[cls])


class _OutOfDomain(Exception):
    pass


def _encode(value, depth, seen, out):
    """Type-tagged, length-framed canonical encoding of ONE value of
    the closed scan domain; returns a DETACHED deep copy. Anything
    outside the domain (subclass, float, bytes, tuple, non-str key,
    lone surrogate, cycle, alias, depth > MAX_DEPTH, int wider than
    MAX_INT_BITS) raises
    _OutOfDomain. Only exact built-in types are inspected, so no
    caller-defined code runs here."""
    if depth > MAX_DEPTH:
        raise _OutOfDomain("depth")
    kind = type(value)
    if value is None:
        out.append(b"n")
        return None
    if kind is bool:
        out.append(b"t" if value else b"f")
        return value
    if kind is int:
        # PINNED INT RANGE, checked BEFORE str(): an unbounded int
        # would hit the interpreter's int->str digit limit and escape
        # raw as ValueError (totality)
        if value.bit_length() > MAX_INT_BITS:
            raise _OutOfDomain("int-range")
        text = str(value).encode()
        out.append(b"i%d:" % len(text) + text)
        return value
    if kind is str:
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError:
            raise _OutOfDomain("surrogate") from None
        out.append(b"s%d:" % len(raw) + raw)
        return value
    if kind is not list and kind is not dict:
        raise _OutOfDomain(kind.__name__)
    # ONE seen-set closes both cycles and aliases: a container met
    # twice anywhere in the walk (including its own descendant) is
    # outside the tree domain
    ident = id(value)
    if ident in seen:
        raise _OutOfDomain("cycle-or-alias")
    seen.add(ident)
    if kind is list:
        out.append(b"l%d:" % len(value))
        copied = [_encode(item, depth + 1, seen, out)
                  for item in list(value)]
    else:
        items = list(dict.items(value))
        for key, _ in items:
            if type(key) is not str:
                raise _OutOfDomain("key")
        items.sort(key=lambda kv: kv[0])
        out.append(b"d%d:" % len(items))
        copied = {}
        for key, item in items:
            _encode(key, depth + 1, seen, out)
            copied[key] = _encode(item, depth + 1, seen, out)
    return copied


def canonical_encoding(value):
    """(detached copy, canonical bytes) or _OutOfDomain."""
    out = []
    copied = _encode(value, 1, set(), out)
    return copied, b"".join(out)


def quarantine_suffix(suffix):
    """THE pinned canonical quarantine token: sha256 over the
    domain-separated canonical encoding of the ENTIRE frozen corrupt
    suffix - deterministic in the suffix and only in the suffix. The
    engine derives this LOCALLY and binds the untrusted sink's output
    to it byte-for-byte."""
    _, raw = canonical_encoding(list(suffix))
    return "qrn1:" + hashlib.sha256(b"qrn1|" + raw).hexdigest()


def _snapshot(value, saved):
    """Reference-preserving snapshot of every container in the
    (validated, alias-free) tree: restore puts each live object back
    to its exact prior contents."""
    if type(value) is list:
        saved.append((value, list(value)))
        for item in value:
            _snapshot(item, saved)
    elif type(value) is dict:
        saved.append((value, dict(value)))
        for item in value.values():
            _snapshot(item, saved)
    return saved


def _restore(saved):
    for obj, contents in saved:
        if type(obj) is list:
            obj[:] = contents
        else:
            obj.clear()
            obj.update(contents)


def _prefix_head(frozen, count):
    """Head of frozen[:count] under the linked WAL, or None."""
    if count == 0:
        return GENESIS
    try:
        return _WAL.replay(copy.deepcopy(frozen[:count]))["head"]
    except WalError:
        return None


def verified_prefix(frozen):
    """Longest prefix passing FULL linked WAL validation and chain
    re-derivation. WAL validation is prefix-monotone (entry k is
    judged against entries 1..k only), so the valid prefixes are
    exactly 0..k and a binary search finds k in O(log n) replays.
    Returns (count, head)."""
    head = _prefix_head(frozen, len(frozen))
    if head is not None:
        return len(frozen), head
    good, bad = 0, len(frozen)  # frozen[:good] valid, [:bad] not
    while bad - good > 1:
        mid = (good + bad) // 2
        if _prefix_head(frozen, mid) is None:
            bad = mid
        else:
            good = mid
    return good, _prefix_head(frozen, good)


class CorruptionEngine:
    """The contract's pinned scan: closed scan domain, prefix-wise
    linked WAL detection, loss bound, frozen log and request,
    exactly one untrusted sink call on a DETACHED suffix copy
    (zero when clean), suffix removal committed LAST."""

    def __init__(self, quarantine_sink):
        self.sink = quarantine_sink  # UNTRUSTED

    def _quarantine(self, frozen_suffix):
        """THE sink boundary: raising ANY BaseException (including
        KeyboardInterrupt/SystemExit/GeneratorExit), non-exact-str,
        UTF-8-inencodable or wrong-grammar output fails closed as
        divergent_quarantine."""
        try:
            out = self.sink(copy.deepcopy(frozen_suffix))
        except BaseException:
            _fail("divergent_quarantine")
        if type(out) is not str or _TOKEN_RE.fullmatch(out) is None:
            _fail("divergent_quarantine")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_quarantine")
        return out

    @staticmethod
    def _derive_scan_id(verdict, head, count, lost, token):
        return "crp1:" + hashlib.sha256(
            f"{verdict}\n{head}\n{count}\n{lost}\n"
            f"{token if token is not None else '-'}".encode()
        ).hexdigest()

    def _receipt(self, verdict, head, count, lost, token):
        return {"scan_id": self._derive_scan_id(verdict, head, count,
                                                lost, token),
                "verdict": verdict,
                "verified_head": head,
                "verified_count": count,
                "quarantined_count": lost,
                "quarantine_token": token}

    def scan(self, log, request):
        """ATOMIC: exact request, closed scan domain (FREEZE by
        detached canonical copy), prefix-wise linked WAL detection,
        loss bound, one sink call bound byte-for-byte to the local
        canonical suffix encoding, commit LAST - rejection leaves
        log and request bit-identical."""
        # exact built-in dict with exactly one exact-str key: key
        # types are checked BEFORE any hashing comparison so a
        # hostile key object never runs caller code
        if type(request) is not dict or len(request) != 1 or \
                any(type(key) is not str for key in dict.keys(request)) \
                or "max_loss" not in request:
            _fail("malformed_corruption_record")
        max_loss = request["max_loss"]
        if type(max_loss) is not int or max_loss < 0:
            _fail("malformed_corruption_record")
        if type(log) is not list:
            _fail("malformed_corruption_record")
        try:
            frozen, _ = canonical_encoding(log)
        except _OutOfDomain:
            _fail("malformed_corruption_record")
        count, head = verified_prefix(frozen)
        lost = len(frozen) - count
        if lost == 0:
            return self._receipt("clean", head, count, 0, None)
        if lost > max_loss:
            _fail("excessive_loss")
        saved = _snapshot(log, [])
        saved_req = dict(request)
        try:
            frozen_suffix = frozen[count:]
            token = self._quarantine(frozen_suffix)
            # SUFFIX-BOUND QUARANTINE: the untrusted token must
            # equal the LOCAL canonical encoding of the frozen
            # corrupt suffix byte-for-byte, or nothing commits.
            if token != quarantine_suffix(frozen_suffix):
                _fail("divergent_quarantine")
        finally:
            _restore(saved)
            request.clear()
            request.update(saved_req)
        # COMMIT LAST: remove exactly the corrupt suffix
        del log[count:]
        return self._receipt("salvaged", head, count, lost, token)


# -- fixtures -------------------------------------------------------------------


def _engine():
    return CorruptionEngine(quarantine_suffix)


def _log3():
    return _log_of(("put", STARTPOS), ("put", KINGS),
                   ("put", AFTER_E4))


class _Counting:
    def __init__(self, inner=quarantine_suffix):
        self.inner = inner
        self.calls = 0

    def __call__(self, suffix):
        self.calls += 1
        return self.inner(suffix)


def _expect(cls, fn, *args):
    with pytest.raises(CorruptionError) as exc:
        fn(*args)
    assert exc.value.failure_class == cls
    assert exc.value.failure_class in FAILURE_CLASSES
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert exc.value.code in ERROR_ENUM
    return exc.value


def _identity_map(log):
    return [id(entry) for entry in log]


# -- lint + happy path ------------------------------------------------------------


def test_lint_clean():
    lint()


def test_clean_log_never_calls_sink_and_is_untouched():
    sink = _Counting()
    log = _log3()
    before, ids = copy.deepcopy(log), _identity_map(log)
    req = {"max_loss": 0}
    receipt = CorruptionEngine(sink).scan(log, req)
    assert list(receipt) == _FIELDS
    assert receipt["verdict"] == "clean"
    assert receipt["verdict"] in VERDICTS
    assert receipt["verified_count"] == 3
    assert receipt["quarantined_count"] == 0
    assert receipt["quarantine_token"] is None
    assert receipt["verified_head"] == _WAL.replay(log)["head"]
    assert _SCAN_RE.fullmatch(receipt["scan_id"])
    assert sink.calls == 0
    assert log == before and _identity_map(log) == ids
    assert req == {"max_loss": 0}


def test_empty_log_is_clean_at_genesis():
    receipt = _engine().scan([], {"max_loss": 0})
    assert receipt["verdict"] == "clean"
    assert receipt["verified_head"] == GENESIS
    assert receipt["verified_count"] == 0


def test_salvage_corrupt_tail():
    log = _log3()
    log[2]["entry_id"] = "wal1:" + "f" * 64
    suffix = copy.deepcopy(log[2:])
    expected_head = log[1]["entry_id"]
    kept = _identity_map(log)[:2]
    sink = _Counting()
    receipt = CorruptionEngine(sink).scan(log, {"max_loss": 1})
    assert list(receipt) == _FIELDS
    assert receipt["verdict"] == "salvaged"
    assert receipt["verified_head"] == expected_head
    assert _HEAD_RE.fullmatch(receipt["verified_head"])
    assert receipt["verified_count"] == 2
    assert receipt["quarantined_count"] == 1
    assert receipt["quarantine_token"] == quarantine_suffix(suffix)
    assert _TOKEN_RE.fullmatch(receipt["quarantine_token"])
    assert _SCAN_RE.fullmatch(receipt["scan_id"])
    assert sink.calls == 1
    assert len(log) == 2 and _identity_map(log) == kept
    assert _WAL.replay(log)["head"] == expected_head


def test_determinism():
    def build():
        log = _log3()
        log[1]["sequence"] = 7
        receipt = _engine().scan(log, {"max_loss": 5})
        return receipt, log
    assert build() == build()


def test_scan_id_binds_every_receipt_field():
    base = ("salvaged", "wal1:" + "a" * 64, 2, 1, "qrn1:" + "b" * 64)
    ref = CorruptionEngine._derive_scan_id(*base)
    variants = [("clean",) + base[1:],
                (base[0], GENESIS) + base[2:],
                base[:2] + (3,) + base[3:],
                base[:3] + (2,) + base[4:],
                base[:4] + ("qrn1:" + "c" * 64,),
                base[:4] + (None,)]
    ids = {CorruptionEngine._derive_scan_id(*v) for v in variants}
    assert ref not in ids and len(ids) == len(variants)


# -- boundary ---------------------------------------------------------------------


def test_corruption_at_first_entry_salvages_to_genesis():
    log = _log3()
    log[0]["payload"]["record"]["digest"] = "0" * 64
    receipt = _engine().scan(log, {"max_loss": 3})
    assert receipt["verified_head"] == GENESIS
    assert receipt["verified_count"] == 0
    assert receipt["quarantined_count"] == 3
    assert log == []


def test_mid_log_corruption_quarantines_the_whole_suffix():
    """Entry 3 is individually intact but sits behind corrupt entry
    2 - salvage is a PREFIX, never a filter."""
    log = _log3()
    log[1]["prior_entry_id"] = "wal1:" + "0" * 64
    receipt = _engine().scan(log, {"max_loss": 2})
    assert receipt["verified_count"] == 1
    assert receipt["quarantined_count"] == 2
    assert len(log) == 1


def test_loss_bound_is_inclusive():
    log = _log3()
    log[1]["sequence"] = 9
    receipt = _engine().scan(log, {"max_loss": 2})
    assert receipt["quarantined_count"] == 2


def test_loss_one_over_the_bound_is_excessive_and_untouched():
    sink = _Counting()
    log = _log3()
    log[1]["sequence"] = 9
    before, ids = copy.deepcopy(log), _identity_map(log)
    req = {"max_loss": 1}
    _expect("excessive_loss", CorruptionEngine(sink).scan, log, req)
    assert sink.calls == 0
    assert log == before and _identity_map(log) == ids
    assert req == {"max_loss": 1}


def _corruptions():
    def seq_gap(log):
        log[1]["sequence"] = 1

    def chain_break(log):
        log[1]["prior_entry_id"] = "wal1:" + "0" * 64

    def entry_tamper(log):
        log[1]["entry_id"] = "wal1:" + "e" * 64

    def digest_tamper(log):
        log[1]["payload"]["record"]["digest"] = "0" * 64

    def identity_tamper(log):
        log[1]["payload"]["identity"] = "x"

    def unknown_op(log):
        log[1]["op"] = "truncate"

    def missing_field(log):
        del log[1]["op"]

    def extra_field(log):
        log[1]["extra"] = 1

    def bool_sequence(log):
        log[1]["sequence"] = True

    def garbage_string(log):
        log[1] = "garbage"

    def garbage_null(log):
        log[1] = None

    def garbage_list(log):
        log[1] = [1, [2, {"k": "v"}]]

    def torn_write(log):
        log[1] = {"entry_id": log[1]["entry_id"]}

    return [(f.__name__, f) for f in (
        seq_gap, chain_break, entry_tamper, digest_tamper,
        identity_tamper, unknown_op, missing_field, extra_field,
        bool_sequence, garbage_string, garbage_null, garbage_list,
        torn_write)]


@pytest.mark.parametrize("name,mutate", _corruptions(),
                         ids=[n for n, _ in _corruptions()])
def test_every_corruption_kind_salvages_to_the_verified_prefix(
        name, mutate):
    log = _log3()
    head = log[0]["entry_id"]
    mutate(log)
    suffix = copy.deepcopy(log[1:])
    receipt = _engine().scan(log, {"max_loss": 2})
    assert receipt["verdict"] == "salvaged"
    assert receipt["verified_count"] == 1
    assert receipt["verified_head"] == head
    assert receipt["quarantine_token"] == quarantine_suffix(suffix)
    assert _WAL.replay(log)["head"] == head


# -- malformed ------------------------------------------------------------------------


class _EvilKey(str):
    """Hashes honestly while being inserted, then turns hostile:
    any later hash or comparison would run caller code and raise."""

    armed = False

    def __hash__(self):
        if _EvilKey.armed:
            raise RuntimeError("evil hash")
        return str.__hash__(self)

    def __eq__(self, other):
        if _EvilKey.armed:
            raise RuntimeError("evil eq")
        return str.__eq__(self, other)


@pytest.fixture
def armed_keys():
    _EvilKey.armed = False
    yield
    _EvilKey.armed = False


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _StrSub(str):
    pass


class _IntSub(int):
    pass


HOSTILE_REQUESTS = [None, True, 0, 1.5, [], "text", {},
                    {"max_loss": -1}, {"max_loss": True},
                    {"max_loss": 1.0}, {"max_loss": "1"},
                    {"max_loss": None}, {"max_loss": _IntSub(1)},
                    {"max_loss": 1, "extra": 1}, {1: 1},
                    {"max_los": 1}, _DictSub(max_loss=1)]


@pytest.mark.parametrize("req", HOSTILE_REQUESTS)
def test_total_over_hostile_requests(req):
    sink = _Counting()
    log = _log3()
    log[2]["sequence"] = 9
    before = copy.deepcopy(log)
    _expect("malformed_corruption_record", CorruptionEngine(sink).scan,
            log, req)
    assert sink.calls == 0
    assert log == before


def test_hostile_request_key_never_runs_caller_code(armed_keys):
    req = {_EvilKey("max_loss"): 1}
    _EvilKey.armed = True
    _expect("malformed_corruption_record", _engine().scan, [], req)


@pytest.mark.parametrize("log", [None, True, 0, 1.5, "text", {},
                                 (), _ListSub()])
def test_total_over_hostile_log_containers(log):
    _expect("malformed_corruption_record", _engine().scan, log,
            {"max_loss": 0})


def _out_of_domain():
    def floating(log):
        log[1]["sequence"] = 2.0

    def raw_bytes(log):
        log[1]["op"] = b"put"

    def tuple_value(log):
        log[1]["payload"]["record"]["digest"] = ("a",)

    def set_value(log):
        log[1]["extra"] = {1}

    def dict_subclass(log):
        log[1] = _DictSub(log[1])

    def str_subclass(log):
        log[1]["op"] = _StrSub("put")

    def int_subclass(log):
        log[1]["sequence"] = _IntSub(2)

    def non_str_key(log):
        log[1][3] = "x"

    def evil_key(log):
        log[1][_EvilKey("k")] = 1
        _EvilKey.armed = True

    def lone_surrogate(log):
        log[1]["payload"]["identity"] = "\ud800"

    def cycle(log):
        log[1]["self"] = log[1]

    def alias(log):
        log.append(log[0])

    def too_deep(log):
        # log(1) > entry(2) > six lists (3..8) > leaf at depth 9
        node = "leaf"
        for _ in range(MAX_DEPTH - 2):
            node = [node]
        log[1]["deep"] = node

    return [(f.__name__, f) for f in (
        floating, raw_bytes, tuple_value, set_value, dict_subclass,
        str_subclass, int_subclass, non_str_key, evil_key,
        lone_surrogate, cycle, alias, too_deep)]


@pytest.mark.parametrize("name,mutate", _out_of_domain(),
                         ids=[n for n, _ in _out_of_domain()])
def test_log_outside_scan_domain_is_malformed_untouched(name, mutate,
                                                       armed_keys):
    sink = _Counting()
    log = _log3()
    mutate(log)
    ids = _identity_map(log)
    shallow = [dict(e) if type(e) is dict else e for e in log]
    _expect("malformed_corruption_record", CorruptionEngine(sink).scan,
            log, {"max_loss": 10})
    assert sink.calls == 0
    assert _identity_map(log) == ids
    assert all(dict(e) == s if type(e) is dict else e is s
               for e, s in zip(log, shallow, strict=True))


@pytest.mark.parametrize("where", ["corrupt-suffix",
                                   "durable-sequence"])
def test_big_int_is_malformed_not_raw(where):
    """10**5000 exceeds the interpreter's int->str digit limit; the
    pinned int range rejects it BEFORE str(), typed, inputs
    untouched and reference-preserved."""
    sink = _Counting()
    log = _log3()
    log[2]["sequence"] = 9
    if where == "corrupt-suffix":
        log[2]["payload"]["record"]["digest"] = 10 ** 5000
    else:
        log[0]["sequence"] = 10 ** 5000
    ids = _identity_map(log)
    inner = [(e, dict(e), e["payload"], dict(e["payload"]))
             for e in log]
    req = {"max_loss": 3}
    _expect("malformed_corruption_record", CorruptionEngine(sink).scan,
            log, req)
    assert sink.calls == 0
    assert _identity_map(log) == ids
    for e, e_copy, p, p_copy in inner:
        assert e == e_copy and e["payload"] is p and p == p_copy
    assert req == {"max_loss": 3}


def test_int_range_is_inclusive():
    for ok in (2 ** MAX_INT_BITS - 1, -(2 ** MAX_INT_BITS - 1)):
        log = _log3()
        log[2]["sequence"] = ok
        receipt = _engine().scan(log, {"max_loss": 1})
        assert receipt["quarantined_count"] == 1
    for bad in (2 ** MAX_INT_BITS, -(2 ** MAX_INT_BITS)):
        log = _log3()
        log[2]["sequence"] = bad
        _expect("malformed_corruption_record", _engine().scan, log,
                {"max_loss": 1})
        assert len(log) == 3


def test_mutant_unguarded_int_str_escapes_raw(monkeypatch):
    """Mutant: no int range before str(). 10**5000 escapes raw as
    ValueError; the real engine fails closed typed."""
    monkeypatch.setattr(sys.modules[__name__], "MAX_INT_BITS",
                        10 ** 9)
    log = _log3()
    log[2]["sequence"] = 10 ** 5000
    with pytest.raises(ValueError):
        _engine().scan(log, {"max_loss": 1})
    monkeypatch.undo()
    _expect("malformed_corruption_record", _engine().scan, log,
            {"max_loss": 1})


def test_prefix_search_matches_linear_scan():
    """The binary search is exactly the linear first-failure scan
    for every corruption position."""
    for pos in range(4):
        log = _log_of(("put", STARTPOS), ("put", KINGS),
                      ("put", AFTER_E4), ("delete", STARTPOS))
        log[pos]["sequence"] = 99
        frozen, _ = canonical_encoding(log)
        linear = 0
        while linear < 4 and _prefix_head(frozen, linear + 1):
            linear += 1
        assert verified_prefix(frozen)[0] == linear == pos
        assert verified_prefix(frozen)[1] == _prefix_head(frozen,
                                                          linear)


def test_depth_limit_is_inclusive():
    log = _log3()
    node = "leaf"
    for _ in range(MAX_DEPTH - 3):
        node = [node]
    log[1]["deep"] = node  # log(1) > entry(2) > 5 lists > leaf at 8
    receipt = _engine().scan(log, {"max_loss": 2})
    assert receipt["quarantined_count"] == 2


# -- untrusted quarantine sink ------------------------------------------------------


def _hostile_sinks():
    def raising(suffix):
        raise ValueError("boom")

    def bad_type(suffix):
        return []

    def none(suffix):
        return None

    def bad_grammar(suffix):
        return "qrn1:zz"

    def wrong_prefix(suffix):
        return "arc1:" + "0" * 64

    def uppercase(suffix):
        return quarantine_suffix(suffix).upper()

    def trailing_newline(suffix):
        return quarantine_suffix(suffix) + "\n"

    class EvilStr(str):
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    def evil_str(suffix):
        return EvilStr(quarantine_suffix(suffix))

    def lone_surrogate(suffix):
        return "\ud800"

    def arbitrary_valid(suffix):
        return "qrn1:" + "f" * 64

    def different_suffix(suffix):
        return quarantine_suffix(suffix + ["x"])

    def partial_suffix(suffix):
        return quarantine_suffix(suffix[:1])

    def keyboard_interrupt(suffix):
        raise KeyboardInterrupt

    def system_exit(suffix):
        raise SystemExit(1)

    def generator_exit(suffix):
        raise GeneratorExit

    return [(f.__name__, f) for f in (
        raising, bad_type, none, bad_grammar, wrong_prefix, uppercase,
        trailing_newline, evil_str, lone_surrogate, arbitrary_valid,
        different_suffix, partial_suffix, keyboard_interrupt,
        system_exit, generator_exit)]


@pytest.mark.parametrize("name,sink", _hostile_sinks(),
                         ids=[n for n, _ in _hostile_sinks()])
def test_hostile_sink_fails_closed_and_never_commits(name, sink):
    log = _log3()
    log[1]["sequence"] = 9
    before, ids = copy.deepcopy(log), _identity_map(log)
    req = {"max_loss": 2}
    _expect("divergent_quarantine", CorruptionEngine(sink).scan, log,
            req)
    assert log == before and _identity_map(log) == ids
    assert req == {"max_loss": 2}


def test_stateful_alternating_sink_never_commits():
    calls = {"n": 0}

    def alternating(suffix):
        calls["n"] += 1
        return "qrn1:" + str(calls["n"] % 2) * 64

    for _ in range(2):
        log = _log3()
        log[2]["sequence"] = 9
        before = copy.deepcopy(log)
        _expect("divergent_quarantine",
                CorruptionEngine(alternating).scan, log,
                {"max_loss": 1})
        assert log == before


def test_one_sink_call_per_salvage_and_none_on_rejection():
    sink = _Counting()
    engine = CorruptionEngine(sink)
    log = _log3()
    log[2]["sequence"] = 9
    engine.scan(log, {"max_loss": 1})
    assert sink.calls == 1
    engine.scan(log, {"max_loss": 0})  # now clean
    assert sink.calls == 1
    bad = _log3()
    bad[0]["sequence"] = 9
    with pytest.raises(CorruptionError):
        engine.scan(bad, {"max_loss": 0})
    with pytest.raises(CorruptionError):
        engine.scan([1.5], {"max_loss": 1})
    assert sink.calls == 1


def test_quarantine_token_binds_every_suffix_byte():
    """Changing ANY leaf of the suffix, or its order or length,
    changes the pinned token."""
    log = _log3()
    log[1]["sequence"] = 9
    suffix = copy.deepcopy(log[1:])
    ref = quarantine_suffix(suffix)
    seen = {ref}

    def leaves(node, path):
        if type(node) is dict:
            for key, value in node.items():
                yield from leaves(value, path + [key])
        elif type(node) is list:
            for i, value in enumerate(node):
                yield from leaves(value, path + [i])
        else:
            yield path, node

    count = 0
    for path, value in list(leaves(suffix, [])):
        mutated = copy.deepcopy(suffix)
        node = mutated
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = (value + 1 if type(value) is int
                          else str(value) + "~")
        token = quarantine_suffix(mutated)
        assert token not in seen
        seen.add(token)
        count += 1
    assert count >= 16
    assert quarantine_suffix(suffix[::-1]) != ref
    assert quarantine_suffix(suffix[:1]) != ref
    # type tags: 1 vs "1" vs True vs None never collide
    tags = {quarantine_suffix([v]) for v in (1, "1", True, None, [],
                                             {}, "", 0, False)}
    assert len(tags) == 9


def test_sink_mutating_its_argument_is_inert():
    def vandal(suffix):
        token = quarantine_suffix(suffix)
        suffix.clear()
        return token

    log = _log3()
    log[2]["sequence"] = 9
    receipt = CorruptionEngine(vandal).scan(log, {"max_loss": 1})
    assert receipt["quarantined_count"] == 1
    assert len(log) == 2


def test_sink_mutating_live_log_and_request_is_restored():
    log = _log3()
    log[2]["sequence"] = 9
    req = {"max_loss": 1}
    kept = copy.deepcopy(log[:2])
    ids = _identity_map(log)[:2]

    def vandal(suffix):
        token = quarantine_suffix(suffix)
        log[0]["op"] = "delete"
        log[1].clear()
        log.insert(0, "junk")
        req["max_loss"] = 99
        return token

    receipt = CorruptionEngine(vandal).scan(log, req)
    assert receipt["verified_count"] == 2
    assert log == kept and _identity_map(log) == ids
    assert req == {"max_loss": 1}
    assert _WAL.replay(log)["head"] == receipt["verified_head"]


def test_sink_mutating_live_log_then_failing_restores_bit_identical():
    log = _log3()
    log[2]["sequence"] = 9
    before, ids = copy.deepcopy(log), _identity_map(log)
    record_obj = log[0]["payload"]["record"]

    def vandal(suffix):
        log[0]["payload"]["record"]["digest"] = "x"
        del log[:]
        raise SystemExit

    _expect("divergent_quarantine", CorruptionEngine(vandal).scan, log,
            {"max_loss": 1})
    assert log == before and _identity_map(log) == ids
    assert log[0]["payload"]["record"] is record_obj


# -- behavioral mutants -------------------------------------------------------------


def _corrupt_log():
    log = _log3()
    log[2]["sequence"] = 9
    return log


def test_mutant_grammar_only_token_validation():
    """Mutant: accept any grammar-valid token. It commits on an
    arbitrary token - the real engine refuses."""
    def arbitrary(suffix):
        return "qrn1:" + "f" * 64

    def mutant(log):
        frozen, _ = canonical_encoding(log)
        count, _ = verified_prefix(frozen)
        token = arbitrary(frozen[count:])
        assert _TOKEN_RE.fullmatch(token)
        del log[count:]

    log = _corrupt_log()
    mutant(log)
    assert len(log) == 2  # the mutant destroyed the suffix
    log = _corrupt_log()
    _expect("divergent_quarantine", CorruptionEngine(arbitrary).scan,
            log, {"max_loss": 1})
    assert len(log) == 3


def test_mutant_commit_before_quarantine():
    def failing(suffix):
        raise ValueError

    def mutant(log):
        frozen, _ = canonical_encoding(log)
        count, _ = verified_prefix(frozen)
        del log[count:]
        CorruptionEngine(failing)._quarantine(frozen[count:])

    log = _corrupt_log()
    with pytest.raises(CorruptionError):
        mutant(log)
    assert len(log) == 2  # the suffix is gone with no quarantine
    log = _corrupt_log()
    _expect("divergent_quarantine", CorruptionEngine(failing).scan,
            log, {"max_loss": 1})
    assert len(log) == 3


def test_mutant_filter_instead_of_prefix():
    """Mutant: drop only the entries that fail on their own and keep
    the rest. The survivor is not a valid WAL - the real engine keeps
    exactly the verified prefix."""
    log = _log3()
    log[1]["sequence"] = 9
    survivors = [e for i, e in enumerate(log) if i != 1]
    with pytest.raises(WalError):
        _WAL.replay(survivors)
    receipt = _engine().scan(log, {"max_loss": 2})
    assert receipt["verified_count"] == 1
    assert _WAL.replay(log)["head"] == receipt["verified_head"]


def test_mutant_without_loss_bound():
    def mutant(log):
        frozen, _ = canonical_encoding(log)
        count, _ = verified_prefix(frozen)
        del log[count:]

    log = _log3()
    log[0]["sequence"] = 9
    mutant(log)
    assert log == []  # the mutant wiped the whole store
    log = _log3()
    log[0]["sequence"] = 9
    _expect("excessive_loss", _engine().scan, log, {"max_loss": 1})
    assert len(log) == 3


def test_mutant_exception_only_boundary_leaks_raw():
    def interrupting(suffix):
        raise KeyboardInterrupt

    class Mutant(CorruptionEngine):
        def _quarantine(self, frozen_suffix):
            try:
                return self.sink(copy.deepcopy(frozen_suffix))
            except Exception:
                _fail("divergent_quarantine")

    with pytest.raises(KeyboardInterrupt):
        Mutant(interrupting).scan(_corrupt_log(), {"max_loss": 1})
    _expect("divergent_quarantine",
            CorruptionEngine(interrupting).scan, _corrupt_log(),
            {"max_loss": 1})


def test_mutant_live_suffix_handed_to_sink():
    """Mutant: hand the sink the LIVE suffix objects. A sink that
    edits its argument then corrupts the caller's store on a
    rejected scan - the real engine hands a detached copy."""
    def vandal(suffix):
        suffix[0]["op"] = "vandalised"
        raise ValueError

    log = _corrupt_log()
    with pytest.raises(ValueError):
        vandal(log[2:])
    assert log[2]["op"] == "vandalised"
    log = _corrupt_log()
    before = copy.deepcopy(log)
    _expect("divergent_quarantine", CorruptionEngine(vandal).scan, log,
            {"max_loss": 1})
    assert log == before


def test_mutant_without_restore():
    """Mutant: no snapshot/restore around the sink. A sink holding a
    closure on the live log leaves it vandalised on rejection."""
    log = _corrupt_log()
    before = copy.deepcopy(log)

    def vandal(suffix):
        log[0]["op"] = "delete"
        raise ValueError

    class Mutant(CorruptionEngine):
        def scan(self, log, request):
            frozen, _ = canonical_encoding(log)
            count, _ = verified_prefix(frozen)
            self._quarantine(frozen[count:])

    with pytest.raises(CorruptionError):
        Mutant(vandal).scan(log, {"max_loss": 1})
    assert log != before
    log[0]["op"] = "put"
    _expect("divergent_quarantine", CorruptionEngine(vandal).scan, log,
            {"max_loss": 1})
    assert log == before


def test_mutant_isinstance_domain_admits_hostile_subclass():
    """Mutant: isinstance-based domain check. A str subclass lying
    about equality slips in; the exact-type domain rejects it."""
    class Liar(str):
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    value = Liar("put")
    assert isinstance(value, str) and value == "anything"
    log = _log3()
    log[1]["op"] = value
    _expect("malformed_corruption_record", _engine().scan, log,
            {"max_loss": 3})


# -- lint mutants -------------------------------------------------------------------


def _mutants():
    doc = yaml.safe_load(CONTRACT.read_text())
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        if value is _DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        out.append((name, m))

    c = ["contract"]
    add("schema version string", ["schema_version"], "1")
    add("schema version bool", ["schema_version"], True)
    add("extra top-level key", ["extra"], 1)
    add("extra section", c + ["notes"], "free-form")
    add("id drift", c + ["id"], "store-rollback")
    add("role kind drift", c + ["role", "kind"], "best-effort-fsck")
    add("not_scope drift", c + ["role", "not_scope"],
        "entry-repair-owned-here")
    add("record fields drift", c + ["record", "fields"], ["scan_id"])
    add("record field order drift", c + ["record", "fields"],
        ["verdict", "scan_id", "verified_head", "verified_count",
         "quarantined_count", "quarantine_token"])
    add("record exact coerced", c + ["record", "exact"], 1)
    add("verdict values widened",
        c + ["record", "field_definitions", "verdict", "values"],
        ["clean", "salvaged", "repaired"])
    add("verified count source drift",
        c + ["record", "field_definitions", "verified_count",
             "source"], "caller-supplied")
    add("field definition dropped",
        c + ["record", "field_definitions", "quarantined_count"],
        _DELETE)
    add("scan id grammar drift",
        c + ["identifiers", "scan_id", "grammar"], "^.*$")
    add("scan id supplied",
        c + ["identifiers", "scan_id", "source"], "caller-supplied")
    add("head grammar drift",
        c + ["identifiers", "verified_head", "grammar"], "^.*$")
    add("token grammar drift",
        c + ["identifiers", "quarantine_token", "grammar"], "^.*$")
    add("token trusted",
        c + ["identifiers", "quarantine_token", "source"],
        "sink-always-honest")
    add("token derivation weakened",
        c + ["identifiers", "quarantine_token", "derivation"],
        "sha256-over-first-entry")
    add("scan domain widened", c + ["semantics", "scan_domain"],
        "any-python-object")
    add("depth pin drift", c + ["semantics", "scan_domain"],
        "exact-built-in-acyclic-alias-free-tree-of-dict-list-str-int-"
        "bool-null-within-nesting-depth-64")
    add("int bound drift", c + ["semantics", "scan_domain"],
        "exact-built-in-acyclic-alias-free-tree-of-dict-list-str-int-"
        "bool-null-within-nesting-depth-8")
    add("detection drift", c + ["semantics", "detection"],
        "drop-individually-bad-entries")
    add("loss bound dropped", c + ["semantics", "loss_bound"],
        "unbounded")
    add("quarantine binding dropped", c + ["semantics", "quarantine"],
        "suffix-quarantined-before-commit")
    add("clean drift", c + ["semantics", "clean"],
        "sink-called-on-empty-suffix")
    add("commit drift", c + ["semantics", "commit"],
        "truncate-then-quarantine")
    add("oracle trusted", c + ["oracle_boundary", "role"],
        "sink-always-honest")
    add("single evaluation dropped",
        c + ["oracle_boundary", "single_evaluation"], "retry-allowed")
    add("frozen dropped", c + ["oracle_boundary", "frozen_snapshots"],
        "live-log-re-read")
    add("output validation dropped",
        c + ["oracle_boundary", "output_validation"], "any-output")
    add("failure class dropped", c + ["failures", "classes"],
        ["malformed_corruption_record", "excessive_loss"])
    add("failure class added", c + ["failures", "classes"],
        FAILURE_CLASSES + ["corrupt_source"])
    add("failure trigger drift",
        c + ["failures", "triggers", "excessive_loss"], "never-fails")
    add("failure mapping drift",
        c + ["failures", "mapping", "divergent_quarantine"], "internal")
    add("failures open", c + ["failures", "closed"], False)
    add("failures closed coerced", c + ["failures", "closed"], 1)
    add("failures extra key", c + ["failures", "notes"], "x")
    add("enum drift", c + ["errors", "closed_enum"], ["internal"])
    add("retryable widened",
        c + ["errors", "shape", "retryable_true_only_for"],
        ["internal", "excessive_loss"])
    add("errors extra key", c + ["errors", "notes"], "x")
    add("property drift", c + ["properties", "atomic"], "best-effort")
    add("property dropped", c + ["properties", "rollback"], _DELETE)
    add("base path drift", c + ["versioning", "base_path"],
        "/store/corruption/v0")
    add("versioning rule drift", c + ["versioning", "rule"],
        "anything goes")
    add("link drift", c + ["links", "wal_contract"],
        "data/contracts/san.yaml")
    add("link missing target", c + ["links", "restore_contract"],
        "data/contracts/nope.yaml")
    return out


_DELETE = object()


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 40
    names = [n for n, _ in mutants]
    assert len(set(names)) == len(names)
    for name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)
        assert name


def test_mutants_cover_every_section():
    clean = yaml.safe_load(CONTRACT.read_text())
    covered = set()
    for _name, m in _mutants():
        for key in set(m) | set(clean):
            if m.get(key) != clean.get(key) and key != "contract":
                covered.add(key)
        for section in set(m["contract"]) | set(clean["contract"]):
            if m["contract"].get(section) != \
                    clean["contract"].get(section):
                covered.add(section)
    assert covered >= {"schema_version", "id", "role", "record",
                       "identifiers", "semantics", "oracle_boundary",
                       "failures", "errors", "properties",
                       "versioning", "links"}
