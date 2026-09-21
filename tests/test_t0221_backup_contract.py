"""T0221: store backup contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/backup.yaml:
content-addressed backup receipts over a WAL-logged canonical
graph state. The source log validates and replays through the
LINKED WAL machinery (imported, never restated); the bundle
serializer is UNTRUSTED input behind the boundary (single
evaluation per backup, frozen log and state snapshots, exact
built-in-str output validation); verification is LOCAL (no
oracle): recomputed backup id must equal the stored one, with
pinned head/count consistency. A rejected backup or verify
leaves every input bit-identical.
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
from tests.test_t0194_migration_contract import (  # noqa: E402
    state_id,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    WalEngine,
    WalError,
    _log_of,
    canonical_payload,
)
from tools.backup_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_BACKUP_RE = re.compile(
    _CC["identifiers"]["backup_id"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["head"]["grammar"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])


class BackupError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise BackupError(cls, FAILURE_MAPPING[cls])


def serialize_bundle(state):
    """The honest bundle serializer: one canonical string over
    the sorted identity -> exact record map."""
    parts = []
    for key in sorted(state):
        rec = state[key]
        body = "|".join(f"{field}={rec[field]}"
                        for field in sorted(rec))
        parts.append(f"{key}\n{body}\n")
    return "".join(parts)


class BackupEngine:
    """The contract's pinned backup: full linked-WAL validation
    and replay of the source, frozen detached state snapshot,
    untrusted serializer behind the boundary (exactly one call),
    derived content-addressed receipt, LOCAL total verify."""

    def __init__(self, bundle_serializer):
        self.serializer = bundle_serializer  # UNTRUSTED
        self._wal = WalEngine(canonical_payload)  # linked, trusted

    def _serialize(self, frozen_state):
        """THE serializer boundary: raising or non-exact-str
        output fails closed as divergent_snapshot."""
        try:
            out = self.serializer(frozen_state)
        except Exception:
            _fail("divergent_snapshot")
        if type(out) is not str:
            _fail("divergent_snapshot")
        return out

    @staticmethod
    def _derive_backup_id(head, sid, count, bundle):
        return "bck1:" + hashlib.sha256(
            f"{head}\n{sid}\n{count}\n{bundle}".encode()
        ).hexdigest()

    def backup(self, log):
        """ATOMIC: the source log is validated and replayed
        through the linked WAL machinery (any source rejection
        surfaces as corrupt_source); the replayed state is frozen
        BEFORE the single serializer call; the receipt derives
        only from the frozen snapshot; the log is restored
        bit-identical on every exit."""
        if not isinstance(log, list):
            _fail("malformed_backup_record")
        try:
            replayed = self._wal.replay(log)
        except WalError:
            _fail("corrupt_source")
        # INPUT PRESERVATION snapshot (reference-preserving): the
        # serializer may hold external references into the
        # caller's live log - every exit restores it bit-identical.
        saved_container, saved_entries = \
            WalEngine._snapshot_log(log)
        # FREEZE THE STATE: one detached plain-dict snapshot taken
        # BEFORE the serializer call; derivation reads ONLY it.
        frozen_state = {key: dict(rec)
                        for key, rec in replayed["state"].items()}
        try:
            bundle = self._serialize(frozen_state)
        finally:
            WalEngine._restore_log(log, saved_container,
                                   saved_entries)
        receipt = {
            "backup_id": self._derive_backup_id(
                replayed["head"], replayed["state_id"],
                replayed["applied"], bundle),
            "head": replayed["head"],
            "state_id": replayed["state_id"],
            "entry_count": replayed["applied"],
            "bundle": bundle,
        }
        return receipt

    def verify(self, receipt):
        """LOCAL and total: exact shape, exact types, pinned
        grammars, head/count consistency, then recomputed backup
        id vs stored - divergence fails closed as
        divergent_backup. NO oracle calls."""
        if not isinstance(receipt, dict) or \
                set(receipt.keys()) != set(_FIELDS) | {"bundle"}:
            _fail("malformed_backup_record")
        if type(receipt["backup_id"]) is not str or \
                _BACKUP_RE.fullmatch(receipt["backup_id"]) is None:
            _fail("malformed_backup_record")
        if type(receipt["head"]) is not str or \
                _HEAD_RE.fullmatch(receipt["head"]) is None:
            _fail("malformed_backup_record")
        if type(receipt["state_id"]) is not str or \
                _STATE_RE.fullmatch(receipt["state_id"]) is None:
            _fail("malformed_backup_record")
        count = receipt["entry_count"]
        if type(count) is not int or count < 0:
            _fail("malformed_backup_record")
        if type(receipt["bundle"]) is not str:
            _fail("malformed_backup_record")
        # head/count consistency: an empty source pins the genesis
        # head and the empty-state id; a non-empty source never
        # pins genesis.
        if count == 0 and (receipt["head"] != GENESIS or
                           receipt["state_id"] != state_id({})):
            _fail("divergent_backup")
        if count > 0 and receipt["head"] == GENESIS:
            _fail("divergent_backup")
        if self._derive_backup_id(receipt["head"],
                                  receipt["state_id"], count,
                                  receipt["bundle"]) != \
                receipt["backup_id"]:
            _fail("divergent_backup")
        return {field: receipt[field] for field in _FIELDS}


def _engine():
    return BackupEngine(serialize_bundle)


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_backup_receipt():
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("delete", STARTPOS))
    before = copy.deepcopy(log)
    receipt = engine.backup(log)
    assert set(receipt) == set(_FIELDS) | {"bundle"}
    assert _BACKUP_RE.fullmatch(receipt["backup_id"])
    assert receipt["head"] == log[-1]["entry_id"]
    assert receipt["entry_count"] == 3
    replayed = WalEngine(canonical_payload).replay(log)
    assert receipt["state_id"] == replayed["state_id"]
    assert receipt["bundle"] == serialize_bundle(
        replayed["state"])
    assert log == before  # source never mutated


def test_empty_log_backup():
    engine = _engine()
    receipt = engine.backup([])
    assert receipt["head"] == GENESIS
    assert receipt["entry_count"] == 0
    assert receipt["state_id"] == state_id({})
    assert receipt["bundle"] == ""
    # and it verifies
    assert engine.verify(receipt) == {
        field: receipt[field] for field in _FIELDS}


def test_verify_happy_roundtrip():
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    receipt = engine.backup(log)
    verified = engine.verify(receipt)
    assert verified == {field: receipt[field]
                        for field in _FIELDS}


def test_determinism():
    def build():
        engine = _engine()
        log = _log_of(("put", STARTPOS), ("put", KINGS),
                      ("delete", KINGS))
        return engine.backup(log)
    assert build() == build()


def test_verify_makes_no_oracle_calls():
    calls = {"n": 0}

    def counting(state):
        calls["n"] += 1
        return serialize_bundle(state)

    engine = BackupEngine(counting)
    log = _log_of(("put", STARTPOS))
    receipt = engine.backup(log)
    assert calls["n"] == 1
    engine.verify(receipt)
    assert calls["n"] == 1  # verify is LOCAL


# -- corrupt source -------------------------------------------------------------


def _corrupt_sources():
    def seq_gap(log):
        log[1]["sequence"] = 1

    def chain_break(log):
        log[1]["prior_entry_id"] = "wal1:" + "0" * 64

    def entry_tamper(log):
        log[0]["entry_id"] = "wal1:" + "f" * 64

    def unknown_op(log):
        log[0]["op"] = "squash"

    def entry_shape(log):
        log[0]["junk"] = 1

    return [("sequence-gap", seq_gap), ("chain-break", chain_break),
            ("entry-tamper", entry_tamper),
            ("unknown-op", unknown_op),
            ("entry-shape", entry_shape)]


@pytest.mark.parametrize("name,mutate", _corrupt_sources(),
                         ids=[n for n, _ in _corrupt_sources()])
def test_corrupt_source_log(name, mutate):
    """Every WAL-level source rejection surfaces as typed
    corrupt_source - never raw, never the WAL-internal class -
    and the log is left bit-identical."""
    engine = _engine()
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    mutate(log)
    before = copy.deepcopy(log)
    with pytest.raises(BackupError) as exc:
        engine.backup(log)
    assert exc.value.failure_class == "corrupt_source"
    assert exc.value.code == FAILURE_MAPPING["corrupt_source"]
    assert exc.value.code in ERROR_ENUM
    assert log == before


@pytest.mark.parametrize("log", [None, True, 0, 1.5, "text", {}])
def test_total_over_hostile_source_types(log):
    engine = _engine()
    with pytest.raises(BackupError) as exc:
        engine.backup(log)
    assert exc.value.failure_class == "malformed_backup_record"
    assert exc.value.code in ERROR_ENUM


# -- hostile serializers --------------------------------------------------------


def _hostile_serializers():
    def raising(state):
        raise ValueError("boom")

    def bad_type(state):
        return []

    def bad_none(state):
        return None

    class EvilStr(str):
        def __hash__(self):
            raise RuntimeError("evil")

    def evil_str(state):
        return EvilStr("bundle")

    return [("raising", raising), ("bad-type", bad_type),
            ("bad-none", bad_none), ("evil-str", evil_str)]


@pytest.mark.parametrize("name,serializer", _hostile_serializers(),
                         ids=[n for n, _ in _hostile_serializers()])
def test_hostile_serializer(name, serializer):
    """A hostile serializer fails closed as divergent_snapshot;
    the source log is bit-identical afterwards."""
    engine = BackupEngine(serializer)
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)
    with pytest.raises(BackupError) as exc:
        engine.backup(log)
    assert exc.value.failure_class == "divergent_snapshot"
    assert exc.value.code == FAILURE_MAPPING["divergent_snapshot"]
    assert exc.value.code in ERROR_ENUM
    assert log == before


def test_serializer_mutating_log_during_call():
    """The serializer corrupts, injects into and truncates the
    caller's live LOG during its call: the receipt derives
    EXACTLY from the pre-call frozen snapshot and the log is
    restored bit-identical."""
    log = _log_of(("put", STARTPOS), ("put", KINGS),
                  ("put", AFTER_E4))
    before = copy.deepcopy(log)
    honest = _engine().backup(copy.deepcopy(log))

    def serializer(state):
        log[0]["payload"]["record"]["digest"] = "corrupted"
        log.append({"junk": True})
        del log[1]
        return serialize_bundle(state)

    receipt = BackupEngine(serializer).backup(log)
    assert receipt == honest
    assert log == before


def test_serializer_mutating_returned_state_snapshot():
    """The serializer mutates the state mapping it is handed:
    that mapping is the engine's own frozen snapshot - the
    caller's log is never reachable through it."""
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log)

    def serializer(state):
        for rec in state.values():
            rec["digest"] = "corrupted"
        state.clear()
        return serialize_bundle(
            WalEngine(canonical_payload).replay(log)["state"])

    receipt = BackupEngine(serializer).backup(log)
    assert log == before
    assert _BACKUP_RE.fullmatch(receipt["backup_id"])


# -- verify: hostile receipts ---------------------------------------------------


def _verify_hostile():
    engine = _engine()
    good = engine.backup(_log_of(("put", STARTPOS)))
    cases = []

    def case(name, mutate, cls):
        cases.append((name, mutate, cls))

    for bad in (None, True, 0, 1.5, [], "text"):
        case(f"non-dict-{type(bad).__name__}",
             lambda r, bad=bad: bad, "malformed_backup_record")
    case("missing-field",
         lambda r: {k: v for k, v in r.items() if k != "head"},
         "malformed_backup_record")
    case("extra-field",
         lambda r: dict(r, extra=1), "malformed_backup_record")
    case("missing-bundle",
         lambda r: {k: v for k, v in r.items() if k != "bundle"},
         "malformed_backup_record")
    case("backup-id-grammar",
         lambda r: dict(r, backup_id="bck1:zz"),
         "malformed_backup_record")
    case("backup-id-non-str",
         lambda r: dict(r, backup_id=True),
         "malformed_backup_record")
    case("head-grammar",
         lambda r: dict(r, head="wal2:" + "0" * 64),
         "malformed_backup_record")
    case("state-id-grammar",
         lambda r: dict(r, state_id="gs2:" + "0" * 64),
         "malformed_backup_record")
    case("count-non-int",
         lambda r: dict(r, entry_count="3"),
         "malformed_backup_record")
    case("count-bool",
         lambda r: dict(r, entry_count=True),
         "malformed_backup_record")
    case("count-negative",
         lambda r: dict(r, entry_count=-1),
         "malformed_backup_record")
    case("bundle-non-str",
         lambda r: dict(r, bundle=[]),
         "malformed_backup_record")
    case("backup-id-tampered",
         lambda r: dict(r, backup_id="bck1:" + "f" * 64),
         "divergent_backup")
    case("bundle-tampered",
         lambda r: dict(r, bundle=r["bundle"] + "x"),
         "divergent_backup")
    case("head-tampered",
         lambda r: dict(r, head=_engine().backup(
             _log_of(("put", KINGS)))["head"]),
         "divergent_backup")
    case("count-tampered",
         lambda r: dict(r, entry_count=r["entry_count"] + 1),
         "divergent_backup")
    case("state-id-tampered",
         lambda r: dict(r, state_id="gs1:" + "f" * 64),
         "divergent_backup")
    return [(name, mutate, cls, good)
            for name, mutate, cls in cases]


_VERIFY_HOSTILE = _verify_hostile()


@pytest.mark.parametrize("name,mutate,cls,good", _VERIFY_HOSTILE,
                         ids=[n for n, _, _, _ in
                              _VERIFY_HOSTILE])
def test_total_over_hostile_receipts(name, mutate, cls, good):
    """Every hostile or tampered receipt maps to its typed class;
    the receipt input is never mutated."""
    engine = _engine()
    receipt = mutate(copy.deepcopy(good))
    before = copy.deepcopy(receipt)
    with pytest.raises(BackupError) as exc:
        engine.verify(receipt)
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert exc.value.code in ERROR_ENUM
    assert receipt == before


def test_verify_head_count_inconsistency():
    """Forged receipts that are SELF-consistent on the id but
    violate the pinned head/count consistency: empty count with a
    non-genesis head (re-forged id) still fails closed."""
    engine = _engine()
    real = engine.backup(_log_of(("put", STARTPOS)))
    forged = dict(real, entry_count=0)
    forged["backup_id"] = engine._derive_backup_id(
        forged["head"], forged["state_id"], 0,
        forged["bundle"])
    with pytest.raises(BackupError) as exc:
        engine.verify(forged)
    assert exc.value.failure_class == "divergent_backup"
    # and the reverse: genesis head with a non-zero count
    forged2 = dict(real, head=GENESIS)
    forged2["backup_id"] = engine._derive_backup_id(
        GENESIS, forged2["state_id"], forged2["entry_count"],
        forged2["bundle"])
    with pytest.raises(BackupError) as exc:
        engine.verify(forged2)
    assert exc.value.failure_class == "divergent_backup"


def test_verify_empty_receipt_forged_state_id():
    """An empty-backup receipt whose state id is not the pinned
    empty-state digest fails closed even with a re-forged id."""
    engine = _engine()
    empty = engine.backup([])
    forged = dict(empty, state_id="gs1:" + "f" * 64)
    forged["backup_id"] = engine._derive_backup_id(
        forged["head"], forged["state_id"], 0,
        forged["bundle"])
    with pytest.raises(BackupError) as exc:
        engine.verify(forged)
    assert exc.value.failure_class == "divergent_backup"


# -- rollback + behavioral mutants ----------------------------------------------


def test_rejected_backup_and_verify_leave_inputs_bit_identical():
    engine = _engine()
    # corrupt source on backup
    log = _log_of(("put", STARTPOS), ("put", KINGS))
    log[1]["sequence"] = 1
    before = copy.deepcopy(log)
    with pytest.raises(BackupError) as exc:
        engine.backup(log)
    assert exc.value.failure_class == "corrupt_source"
    assert log == before
    # serializer failure mid-backup
    calls = {"n": 0}

    def midway(state):
        calls["n"] += 1
        raise ValueError("mid-backup")

    log = _log_of(("put", STARTPOS))
    before = copy.deepcopy(log)
    with pytest.raises(BackupError):
        BackupEngine(midway).backup(log)
    assert log == before
    # divergent receipt on verify
    receipt = _engine().backup(_log_of(("put", KINGS)))
    receipt["bundle"] += "tampered"
    receipt_before = copy.deepcopy(receipt)
    with pytest.raises(BackupError) as exc:
        engine.verify(receipt)
    assert exc.value.failure_class == "divergent_backup"
    assert receipt == receipt_before


def test_mutant_backup_skipping_source_validation():
    """Behavioral mutant: backing up WITHOUT the linked WAL
    validation and replay accepts a corrupt source and receipts
    over garbage. Counter-test: the real engine fails closed."""
    def mutant_backup(log):
        state = {}
        for entry in log:
            if entry["op"] == "put":
                state[entry["payload"]["identity"]] = \
                    dict(entry["payload"]["record"])
        bundle = serialize_bundle(state)
        head = log[-1]["entry_id"] if log else GENESIS
        sid = state_id(state)
        return {"backup_id": BackupEngine._derive_backup_id(
            head, sid, len(log), bundle),
            "head": head, "state_id": sid,
            "entry_count": len(log), "bundle": bundle}

    log = _log_of(("put", STARTPOS), ("put", KINGS))
    log[1]["prior_entry_id"] = "wal1:" + "0" * 64  # broken chain
    mutant_receipt = mutant_backup(log)
    assert _BACKUP_RE.fullmatch(mutant_receipt["backup_id"])
    # the mutant's receipt even self-verifies - corruption laundered
    assert _engine().verify(mutant_receipt) is not None
    before = copy.deepcopy(log)
    with pytest.raises(BackupError) as exc:
        _engine().backup(log)
    assert exc.value.failure_class == "corrupt_source"
    assert log == before


def test_mutant_verify_trusting_stored_id():
    """Behavioral mutant: verify WITHOUT recomputation accepts a
    tampered bundle. Counter-test: the real verify recomputes."""
    def mutant_verify(receipt):
        return {field: receipt[field] for field in _FIELDS}

    receipt = _engine().backup(_log_of(("put", STARTPOS)))
    tampered = dict(receipt, bundle=receipt["bundle"] + "forged")
    assert mutant_verify(tampered)["backup_id"] == \
        receipt["backup_id"]  # tamper accepted by the mutant
    before = copy.deepcopy(tampered)
    with pytest.raises(BackupError) as exc:
        _engine().verify(tampered)
    assert exc.value.failure_class == "divergent_backup"
    assert tampered == before


def test_mutant_backup_serializing_live_state():
    """Behavioral mutant: serializing the LIVE replayed state
    without a frozen snapshot lets a serializer mutation corrupt
    the derivation basis. Counter-test: the real engine derives
    only from the frozen snapshot."""
    def mutant_backup(serializer, log):
        replayed = WalEngine(canonical_payload).replay(log)
        bundle = serializer(replayed["state"])  # LIVE mapping
        return {"backup_id": BackupEngine._derive_backup_id(
            replayed["head"], replayed["state_id"],
            replayed["applied"], bundle),
            "head": replayed["head"],
            "state_id": replayed["state_id"],
            "entry_count": replayed["applied"],
            "bundle": bundle}

    log = _log_of(("put", STARTPOS), ("put", KINGS))
    honest = _engine().backup(copy.deepcopy(log))

    def poison(state):
        poisoned = dict(state)
        poisoned["injected"] = {"variant": "standard",
                                "digest": "pdv1:" + "9" * 64,
                                "snapshot_fen": STARTPOS}
        return serialize_bundle(poisoned)

    mutant_receipt = mutant_backup(poison, log)
    assert mutant_receipt["backup_id"] != honest["backup_id"]
    # the same poison against the real engine: the receipt is the
    # honest derivation over the FROZEN snapshot... the serializer
    # itself poisons - but the receipt stays self-consistent and
    # the log untouched; the boundary contains the damage to the
    # bundle the serializer chose.
    log2 = _log_of(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(log2)
    receipt = BackupEngine(poison).backup(log2)
    assert log2 == before
    assert receipt["entry_count"] == 2
    assert receipt["head"] == log2[-1]["entry_id"]


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
        "free-form-export")
    add("not_scope drift", ["contract", "role", "not_scope"],
        "restore-owned-here")
    add("record fields drift", ["contract", "record", "fields"],
        ["backup_id", "head"])
    add("record exact drift", ["contract", "record", "exact"],
        False)
    add("backup id grammar drift",
        ["contract", "identifiers", "backup_id", "grammar"],
        "^.*$")
    add("backup id supplied",
        ["contract", "identifiers", "backup_id", "source"],
        "caller-supplied")
    add("head grammar drift",
        ["contract", "identifiers", "head", "grammar"], "^.*$")
    add("state id supplied",
        ["contract", "identifiers", "state_id", "source"],
        "caller-supplied")
    add("source validation dropped",
        ["contract", "semantics", "source_validation"],
        "log-trusted-as-given")
    add("snapshot drift", ["contract", "semantics", "snapshot"],
        "live-state-serialized")
    add("integrity drift", ["contract", "semantics", "integrity"],
        "stored-id-trusted")
    add("consistency drift",
        ["contract", "semantics", "head_count_consistency"],
        "any-head-any-count")
    add("commit drift", ["contract", "semantics", "commit"],
        "in-place-log-mutation")
    add("oracle trusted",
        ["contract", "oracle_boundary", "role"],
        "serializer-always-honest")
    add("single evaluation dropped",
        ["contract", "oracle_boundary", "single_evaluation"],
        "reserialize-allowed")
    add("frozen dropped",
        ["contract", "oracle_boundary", "frozen_snapshots"],
        "live-log-re-read")
    add("output validation dropped",
        ["contract", "oracle_boundary", "output_validation"],
        "any-output")
    add("failure class dropped",
        ["contract", "failures", "classes"],
        ["malformed_backup_record"])
    add("failure trigger drift",
        ["contract", "failures", "triggers", "corrupt_source"],
        "never-fails")
    add("failure mapping drift",
        ["contract", "failures", "mapping", "divergent_backup"],
        "internal")
    add("enum drift", ["contract", "errors", "closed_enum"],
        ["internal"])
    add("property drift", ["contract", "properties", "atomic"],
        "best-effort")
    add("base path drift",
        ["contract", "versioning", "base_path"],
        "/store/backup/v0")
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
