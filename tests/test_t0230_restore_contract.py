"""T0230: store restore contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/restore.yaml:
verified restore of content-addressed backup bundles into a
canonical graph state. The backup receipt verifies through the
LINKED backup machinery (imported, never restated) BEFORE any
parse; the bundle parser is UNTRUSTED input behind the boundary
(single evaluation per restore, frozen receipt fields, exact
built-in-mapping output validation); the parsed state is
validated record-by-record through the linked node machinery and
its recomputed id must equal the receipt's state id. A rejected
restore leaves every input bit-identical.
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
    KINGS,
    STARTPOS,
)
from tests.test_t0194_migration_contract import (  # noqa: E402
    state_id,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    WalEngine,
    WalError,
    _identity,
    _log_of,
    _node,
    canonical_payload,
)
from tests.test_t0221_backup_contract import (  # noqa: E402
    BackupEngine,
    BackupError,
    serialize_bundle,
)
from tools.restore_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    RECORD,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_RESTORE_RE = re.compile(
    _CC["identifiers"]["restore_id"]["grammar"])

_WAL = WalEngine(canonical_payload)  # linked, trusted
_BACKUP = BackupEngine(serialize_bundle)  # linked, trusted


class RestoreError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise RestoreError(cls, FAILURE_MAPPING[cls])


def parse_bundle(bundle):
    """The honest bundle parser: exact inverse of the pinned
    canonical serialization - key line, field line, per record."""
    state = {}
    lines = bundle.split("\n")
    # serialization ends with a trailing newline per record
    if lines and lines[-1] == "":
        lines.pop()
    for i in range(0, len(lines), 2):
        key = lines[i]
        body = lines[i + 1]
        record = {}
        for pair in body.split("|"):
            field, _sep, value = pair.partition("=")
            record[field] = value
        state[key] = record
    return state


class RestoreEngine:
    """The contract's pinned restore: full linked backup
    verification FIRST, frozen receipt fields, exactly one
    untrusted parser call, total record-by-record validation,
    recomputed state id vs the receipt - staged state only, the
    input receipt is never mutated."""

    def __init__(self, bundle_parser):
        self.parser = bundle_parser  # UNTRUSTED

    def _parse(self, bundle):
        """THE parser boundary: raising ANY BaseException
        (including KeyboardInterrupt/SystemExit/GeneratorExit) or
        returning non-exact-dict output fails closed as
        divergent_parse."""
        try:
            out = self.parser(bundle)
        except BaseException:
            # fail closed against the FULL BaseException surface:
            # KeyboardInterrupt/SystemExit/GeneratorExit from the
            # untrusted oracle map to the typed failure, never a
            # raw escape (totality)
            _fail("divergent_parse")
        if type(out) is not dict:
            _fail("divergent_parse")
        return out

    @staticmethod
    def _derive_restore_id(backup_id, sid):
        return "rst1:" + hashlib.sha256(
            f"{backup_id}\n{sid}".encode()).hexdigest()

    def restore(self, receipt):
        """ATOMIC: verify the receipt through the linked backup
        machinery (malformed receipt -> malformed_restore_record;
        failed verification -> unverified_backup), FREEZE the
        validated fields, parse exactly once behind the boundary,
        validate every parsed record and its identity, recompute
        the state id, then require the staged state to reserialize
        BYTE-FOR-BYTE to the frozen bundle through the linked
        canonical serializer (non-canonical bytes laundered
        through duplicate/overwrite or reordering fail closed as
        divergent_parse) - the receipt is restored bit-identical
        on every exit."""
        try:
            _BACKUP.verify(receipt)
        except BackupError as err:
            if err.failure_class == "malformed_backup_record":
                _fail("malformed_restore_record")
            _fail("unverified_backup")
        # FREEZE THE RECEIPT FIELDS DURING VALIDATION: the
        # validated exact values copied BEFORE the first parser
        # call; every later phase reads ONLY these frozen values.
        frozen_backup_id = receipt["backup_id"]
        frozen_state_id = receipt["state_id"]
        frozen_bundle = receipt["bundle"]
        # INPUT PRESERVATION snapshot: the parser may hold an
        # external reference into the caller's receipt - every
        # exit restores it bit-identical.
        saved = dict(receipt)
        try:
            parsed = self._parse(frozen_bundle)
        finally:
            receipt.clear()
            receipt.update(saved)
        # TOTAL parsed-state validation: exact str keys, exact
        # node records, identity == key.
        state = {}
        for key, rec in parsed.items():
            if type(key) is not str or type(rec) is not dict:
                _fail("divergent_state")
            try:
                derived_identity = _WAL._validate_record(rec)
            except WalError:
                _fail("divergent_state")
            if key != derived_identity:
                _fail("divergent_state")
            state[key] = dict(rec)
        if state_id(state) != frozen_state_id:
            _fail("divergent_state")
        # CANONICAL ROUND-TRIP: the accepted bundle must be the
        # UNIQUE canonical serialization of the staged validated
        # state, byte-for-byte. A bundle that decodes to the right
        # state only through duplicate/overwrite, record or field
        # reordering, or framing tricks is laundered input - two
        # distinct verified bundles would restore to one state.
        # The LINKED pinned serializer does this check; the
        # untrusted parser is NEVER called again.
        if serialize_bundle(state) != frozen_bundle:
            _fail("divergent_parse")
        return {"restore_id": self._derive_restore_id(
            frozen_backup_id, frozen_state_id),
            "backup_id": frozen_backup_id,
            "state_id": frozen_state_id,
            "state": state}


def _engine():
    return RestoreEngine(parse_bundle)


def _receipt(*ops):
    return _BACKUP.backup(_log_of(*ops))


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_restore_roundtrip():
    engine = _engine()
    receipt = _receipt(("put", STARTPOS), ("put", KINGS),
                       ("delete", STARTPOS))
    before = copy.deepcopy(receipt)
    result = engine.restore(receipt)
    assert set(result) == set(_FIELDS)  # exact four-field record
    assert _RESTORE_RE.fullmatch(result["restore_id"])
    assert result["backup_id"] == receipt["backup_id"]
    assert result["state_id"] == receipt["state_id"]
    replayed = _WAL.replay(_log_of(("put", STARTPOS),
                                   ("put", KINGS),
                                   ("delete", STARTPOS)))
    assert result["state"] == replayed["state"]
    assert receipt == before  # never mutated


def test_result_shape_matches_normative_record_exactly():
    """LINKAGE: restore's output keys equal the contract's
    normative exact record field set - four fields, state
    included, no out-of-band union; the state field is the
    pinned exact built-in mapping."""
    assert list(_CC["record"]["fields"]) == RECORD["fields"]
    assert _CC["record"]["exact"] is True
    assert RECORD["exact"] is True
    assert _CC["record"]["field_definitions"]["state"] == \
        RECORD["field_definitions"]["state"]
    assert RECORD["field_definitions"]["state"]["type"] == \
        "exact-built-in-dict-mapping-exact-built-in-string-" \
        "identities-to-validated-exact-node-records"
    result = _engine().restore(
        _receipt(("put", STARTPOS), ("put", KINGS)))
    assert set(result) == set(_FIELDS)  # output keys exact
    assert len(result) == len(_FIELDS)  # no hidden extra keys
    # the state field honors its pinned structured definition
    assert type(result["state"]) is dict
    assert all(type(key) is str and type(rec) is dict
               for key, rec in result["state"].items())
    # consumer probes: a consumer generated from the normative
    # exact record accepts the engine output, and rejects both
    # exactly-one-missing and exactly-one-extra shapes
    for key in _FIELDS:
        assert set(result) - {key} != set(_FIELDS)
    assert set(result) | {"stray"} != set(_FIELDS)


def test_empty_backup_restore():
    engine = _engine()
    result = engine.restore(_BACKUP.backup([]))
    assert result["state"] == {}
    assert result["state_id"] == state_id({})
    assert _RESTORE_RE.fullmatch(result["restore_id"])


def test_determinism():
    def build():
        engine = _engine()
        receipt = _receipt(("put", STARTPOS), ("delete", KINGS))
        return engine.restore(receipt)
    assert build() == build()


def test_one_parser_call_per_restore():
    calls = {"n": 0}

    def counting(bundle):
        calls["n"] += 1
        return parse_bundle(bundle)

    engine = RestoreEngine(counting)
    engine.restore(_receipt(("put", STARTPOS), ("put", KINGS)))
    assert calls["n"] == 1
    # a rejected restore never reaches the parser
    with pytest.raises(RestoreError):
        engine.restore({"backup_id": "bad"})
    assert calls["n"] == 1


# -- malformed receipts + unverified backups ------------------------------------

HOSTILE_RECEIPTS = [None, True, 0, 1.5, [], "text",
                    {},
                    {"backup_id": "bck1:" + "0" * 64},
                    ]


@pytest.mark.parametrize("receipt", HOSTILE_RECEIPTS)
def test_total_over_hostile_receipts(receipt):
    engine = _engine()
    with pytest.raises(RestoreError) as exc:
        engine.restore(receipt)
    assert exc.value.failure_class == "malformed_restore_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_restore_record"]
    assert exc.value.code in ERROR_ENUM


def _tampered_receipts():
    good = _receipt(("put", STARTPOS))
    return [
        ("backup-id-tampered",
         dict(good, backup_id="bck1:" + "f" * 64)),
        ("bundle-tampered",
         dict(good, bundle=good["bundle"] + "x")),
        ("state-id-tampered",
         dict(good, state_id="gs1:" + "f" * 64)),
        ("count-tampered",
         dict(good, entry_count=good["entry_count"] + 1)),
        ("head-tampered",
         dict(good, head="wal1:" + "f" * 64)),
    ]


@pytest.mark.parametrize("name,receipt", _tampered_receipts(),
                         ids=[n for n, _ in _tampered_receipts()])
def test_unverified_backup_receipts(name, receipt):
    """A grammatically valid but unverifiable receipt fails closed
    as unverified_backup BEFORE any parser call; the receipt is
    never mutated."""
    engine = _engine()
    before = copy.deepcopy(receipt)
    with pytest.raises(RestoreError) as exc:
        engine.restore(receipt)
    assert exc.value.failure_class == "unverified_backup"
    assert exc.value.code == FAILURE_MAPPING["unverified_backup"]
    assert exc.value.code in ERROR_ENUM
    assert receipt == before


# -- untrusted bundle parser ----------------------------------------------------


def _hostile_parsers():
    def raising(bundle):
        raise ValueError("boom")

    def bad_list(bundle):
        return []

    def bad_none(bundle):
        return None

    def bad_str(bundle):
        return bundle

    class EvilDict(dict):
        def items(self):
            raise RuntimeError("evil")

    def evil_dict(bundle):
        return EvilDict()

    def raising_keyboard_interrupt(bundle):
        raise KeyboardInterrupt("boom")

    def raising_system_exit(bundle):
        raise SystemExit("boom")

    def raising_generator_exit(bundle):
        raise GeneratorExit("boom")

    return [("raising", raising), ("bad-list", bad_list),
            ("bad-none", bad_none), ("bad-str", bad_str),
            ("evil-dict", evil_dict),
            ("raising-keyboard-interrupt",
             raising_keyboard_interrupt),
            ("raising-system-exit", raising_system_exit),
            ("raising-generator-exit", raising_generator_exit)]


@pytest.mark.parametrize("name,parser", _hostile_parsers(),
                         ids=[n for n, _ in _hostile_parsers()])
def test_hostile_parser(name, parser):
    """A hostile parser fails closed as divergent_parse; the
    receipt is bit-identical afterwards."""
    engine = RestoreEngine(parser)
    receipt = _receipt(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(receipt)
    with pytest.raises(RestoreError) as exc:
        engine.restore(receipt)
    assert exc.value.failure_class == "divergent_parse"
    assert exc.value.code == FAILURE_MAPPING["divergent_parse"]
    assert exc.value.code in ERROR_ENUM
    assert receipt == before


# -- canonical-bundle laundering ------------------------------------------------


def _reforge(receipt, bundle):
    """A receipt over hostile BYTES whose backup id is re-forged
    through the linked derivation: backup verify accepts it, so
    only restore's own canonical guard can stop it."""
    forged = dict(receipt, bundle=bundle)
    forged["backup_id"] = BackupEngine._derive_backup_id(
        forged["head"], forged["state_id"],
        forged["entry_count"], bundle, "divergent_snapshot")
    assert _BACKUP.verify(forged) is not None
    return forged


def _laundering_bundles():
    one = _BACKUP.backup(_log_of(("put", STARTPOS)))
    two = _BACKUP.backup(
        _log_of(("put", STARTPOS), ("put", KINGS)))
    honest1 = one["bundle"]
    key, body = honest1.rstrip("\n").split("\n")
    pairs = body.split("|")
    rec = two["bundle"].rstrip("\n").split("\n")
    swapped = (f"{rec[2]}\n{rec[3]}\n"
               f"{rec[0]}\n{rec[1]}\n")
    blank_between = (f"{rec[0]}\n{rec[1]}\n\n"
                     f"{rec[2]}\n{rec[3]}\n")
    return [
        ("duplicate-identical-records", one,
         honest1 + honest1, "divergent_parse"),
        ("duplicate-field-same-value", one,
         f"{key}\n{body}|{pairs[0]}\n", "divergent_parse"),
        ("duplicate-field-conflicting", one,
         f"{key}\n{body}|digest={'0' * 64}\n",
         "divergent_state"),
        ("reordered-records", two, swapped, "divergent_parse"),
        ("reordered-fields", one,
         f"{key}\n{'|'.join(reversed(pairs))}\n",
         "divergent_parse"),
        ("malformed-separator", one,
         f"{key}\n{body}|bogus\n", "divergent_state"),
        ("trailing-blank-line", one,
         honest1 + "\n", "divergent_parse"),
        ("blank-line-between-records", two, blank_between,
         "divergent_parse"),
        ("missing-trailing-newline", one,
         honest1.rstrip("\n"), "divergent_parse"),
    ]


_LAUNDERING = _laundering_bundles()


@pytest.mark.parametrize("name,base,bundle,cls", _LAUNDERING,
                         ids=[n for n, _, _, _ in _LAUNDERING])
def test_noncanonical_bundle_laundering_fails_closed(
        name, base, bundle, cls):
    """A bundle that is NOT the unique canonical serialization of
    the state it decodes to - duplicated records or fields,
    reordered records or fields, malformed separators, bad
    framing - fails closed typed even when its backup id is
    re-forged so linked verification accepts it. The decisive
    invariant: only byte-canonical bundles restore."""
    forged = _reforge(base, bundle)
    before = copy.deepcopy(forged)
    with pytest.raises(RestoreError) as exc:
        _engine().restore(forged)
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert exc.value.code in ERROR_ENUM
    assert forged == before


def test_canonical_roundtrip_invariant_on_accepted_receipts():
    """DECISIVE INVARIANT: every accepted receipt's bundle is
    byte-for-byte the canonical serialization of the restored
    state."""
    for ops in [(), (("put", STARTPOS),),
                (("put", STARTPOS), ("put", KINGS)),
                (("put", STARTPOS), ("put", KINGS),
                 ("delete", STARTPOS))]:
        receipt = _BACKUP.backup(_log_of(*ops))
        result = _engine().restore(receipt)
        assert serialize_bundle(result["state"]) == \
            receipt["bundle"]


def _divergent_states():
    """Well-formed mappings whose CONTENT is wrong."""
    good_record = _node(STARTPOS)
    good_key = _identity(good_record)
    other_record = _node(KINGS)
    return [
        ("wrong-content", {good_key: other_record}),
        ("key-identity-mismatch",
         {_identity(other_record): good_record}),
        ("non-str-key", {0: good_record}),
        ("non-dict-record", {good_key: None}),
        ("extra-record", {good_key: good_record,
                          _identity(other_record): other_record}),
        ("empty", {}),
    ]


@pytest.mark.parametrize("name,state", _divergent_states(),
                         ids=[n for n, _ in _divergent_states()])
def test_divergent_parsed_state(name, state):
    """A parser returning well-formed but WRONG content fails
    closed as divergent_state - invalid records, identity drift
    and recomputed-id divergence all land there."""
    receipt = _receipt(("put", STARTPOS))
    before = copy.deepcopy(receipt)
    engine = RestoreEngine(lambda bundle: dict(state))
    with pytest.raises(RestoreError) as exc:
        engine.restore(receipt)
    assert exc.value.failure_class == "divergent_state"
    assert exc.value.code == FAILURE_MAPPING["divergent_state"]
    assert exc.value.code in ERROR_ENUM
    assert receipt == before


def test_parser_mutating_receipt_during_call():
    """The parser mutates the caller's live RECEIPT during its
    call: the restore derives EXACTLY from the pre-call frozen
    fields and the receipt is restored bit-identical."""
    receipt = _receipt(("put", STARTPOS), ("put", KINGS))
    before = copy.deepcopy(receipt)
    honest = _engine().restore(copy.deepcopy(receipt))

    def parser(bundle):
        receipt["backup_id"] = "bck1:" + "f" * 64
        receipt["state_id"] = "gs1:" + "f" * 64
        receipt["bundle"] = "forged"
        receipt.clear()
        return parse_bundle(bundle)

    result = RestoreEngine(parser).restore(receipt)
    assert result == honest
    assert receipt == before


# -- rollback + behavioral mutants ----------------------------------------------


def test_rejected_restore_leaves_inputs_bit_identical():
    engine = _engine()
    # malformed
    receipt = _receipt(("put", STARTPOS))
    bad = dict(receipt, backup_id="bad")
    with pytest.raises(RestoreError) as exc:
        engine.restore(bad)
    assert exc.value.failure_class == "malformed_restore_record"
    # unverified
    tampered = dict(receipt, bundle=receipt["bundle"] + "x")
    tampered_before = copy.deepcopy(tampered)
    with pytest.raises(RestoreError) as exc:
        engine.restore(tampered)
    assert exc.value.failure_class == "unverified_backup"
    assert tampered == tampered_before
    # parse failure
    receipt2 = _receipt(("put", KINGS))
    before2 = copy.deepcopy(receipt2)
    with pytest.raises(RestoreError) as exc:
        RestoreEngine(lambda b: []).restore(receipt2)
    assert exc.value.failure_class == "divergent_parse"
    assert receipt2 == before2
    # state divergence
    receipt3 = _receipt(("put", STARTPOS))
    before3 = copy.deepcopy(receipt3)
    with pytest.raises(RestoreError) as exc:
        RestoreEngine(lambda b: {}).restore(receipt3)
    assert exc.value.failure_class == "divergent_state"
    assert receipt3 == before3


def test_mutant_restore_skipping_verification():
    """Behavioral mutant: restoring WITHOUT linked backup
    verification accepts a tampered receipt and stages garbage.
    Counter-test: the real engine verifies first."""
    def mutant_restore(receipt):
        parsed = parse_bundle(receipt["bundle"])
        return {"backup_id": receipt["backup_id"],
                "state_id": receipt["state_id"],
                "state": parsed}

    receipt = _receipt(("put", STARTPOS))
    tampered = dict(receipt, backup_id="bck1:" + "f" * 64)
    # the mutant restores under the tampered id without a peep
    assert mutant_restore(tampered)["backup_id"] == \
        "bck1:" + "f" * 64
    before = copy.deepcopy(tampered)
    with pytest.raises(RestoreError) as exc:
        _engine().restore(tampered)
    assert exc.value.failure_class == "unverified_backup"
    assert tampered == before


def test_mutant_restore_trusting_receipt_state_id():
    """Behavioral mutant: skipping the recomputed-id comparison
    lets a parser substitute a DIFFERENT valid state under the
    receipt's identity. Counter-test: the real engine
    recomputes."""
    def mutant_restore(receipt):
        parsed = parse_bundle(receipt["bundle"])
        return {"restore_id": "rst1:" + "0" * 64,
                "backup_id": receipt["backup_id"],
                "state_id": receipt["state_id"],  # trusted
                "state": parsed}

    receipt = _receipt(("put", STARTPOS))
    other = _BACKUP.backup(_log_of(("put", KINGS)))

    def swapping(bundle):
        return parse_bundle(other["bundle"])  # different state

    mutant_result = mutant_restore(
        dict(receipt, bundle=other["bundle"]))
    assert mutant_result["state"] != _WAL.replay(
        _log_of(("put", STARTPOS)))["state"]
    assert mutant_result["state_id"] == receipt["state_id"]
    # real engine: the swapped state's recomputed id diverges
    with pytest.raises(RestoreError) as exc:
        RestoreEngine(swapping).restore(receipt)
    assert exc.value.failure_class == "divergent_state"


def test_mutant_parser_output_trusted_without_validation():
    """Behavioral mutant: staging the parser's output WITHOUT
    record-by-record validation lets an invalid record through.
    Counter-test: the real engine validates every record."""
    def mutant_restore(parser, receipt):
        parsed = parser(receipt["bundle"])
        return {"state": {k: dict(v) for k, v in parsed.items()}}

    receipt = _receipt(("put", STARTPOS))
    invalid_record = dict(_node(STARTPOS), digest="pdv1:" + "0" * 64)

    def poisoning(bundle):
        return {_identity(_node(STARTPOS)): invalid_record}

    mutant_result = mutant_restore(poisoning, receipt)
    key = _identity(_node(STARTPOS))
    assert mutant_result["state"][key]["digest"] == \
        "pdv1:" + "0" * 64  # invalid record landed in the mutant
    with pytest.raises(RestoreError) as exc:
        RestoreEngine(poisoning).restore(receipt)
    assert exc.value.failure_class == "divergent_state"


def test_mutant_restore_skipping_canonical_roundtrip():
    """Behavioral mutant: a restore that validates the parsed
    state and its id but skips the canonical reserialization
    guard ACCEPTS a duplicated-record bundle - laundering
    non-canonical bytes into a verified restore. Counter-test:
    the real engine's round-trip guard rejects it as
    divergent_parse, receipt bit-identical."""
    def mutant_restore(receipt):
        _BACKUP.verify(receipt)
        parsed = parse_bundle(receipt["bundle"])
        state = {}
        for key, rec in parsed.items():
            assert key == _WAL._validate_record(rec)
            state[key] = dict(rec)
        assert state_id(state) == receipt["state_id"]
        # NO canonical round-trip guard
        return state

    good = _BACKUP.backup(_log_of(("put", STARTPOS)))
    doubled = _reforge(good, good["bundle"] + good["bundle"])
    assert mutant_restore(doubled)  # the mutant launders it
    before = copy.deepcopy(doubled)
    with pytest.raises(RestoreError) as exc:
        _engine().restore(doubled)
    assert exc.value.failure_class == "divergent_parse"
    assert doubled == before


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
        "free-form-import")
    add("not_scope drift", ["contract", "role", "not_scope"],
        "log-reconstruction-owned-here")
    add("record fields drift", ["contract", "record", "fields"],
        ["restore_id"])
    add("record exact drift", ["contract", "record", "exact"],
        False)
    add("parse canonical-roundtrip dropped",
        ["contract", "semantics", "parse"],
        "canonical-bundle-parsed-exactly-once")
    add("record drops state (implementation returns it)",
        ["contract", "record", "fields"],
        ["restore_id", "backup_id", "state_id"])
    add("record state definition dropped",
        ["contract", "record", "field_definitions"], {})
    add("record state type drift",
        ["contract", "record", "field_definitions", "state",
         "type"], "any-mapping")
    add("restore id grammar drift",
        ["contract", "identifiers", "restore_id", "grammar"],
        "^.*$")
    add("restore id supplied",
        ["contract", "identifiers", "restore_id", "source"],
        "caller-supplied")
    add("backup id free-form",
        ["contract", "identifiers", "backup_id", "source"],
        "caller-supplied")
    add("state id supplied",
        ["contract", "identifiers", "state_id", "source"],
        "receipt-trusted")
    add("verify first dropped",
        ["contract", "semantics", "verify_first"],
        "parse-before-verify")
    add("parse drift", ["contract", "semantics", "parse"],
        "reparse-until-success")
    add("reconstruction drift",
        ["contract", "semantics", "reconstruction"],
        "parser-output-trusted")
    add("integrity drift", ["contract", "semantics",
                            "integrity"],
        "receipt-id-trusted")
    add("commit drift", ["contract", "semantics", "commit"],
        "receipt-mutated-in-place")
    add("oracle trusted",
        ["contract", "oracle_boundary", "role"],
        "parser-always-honest")
    add("single evaluation dropped",
        ["contract", "oracle_boundary", "single_evaluation"],
        "reparse-allowed")
    add("frozen dropped",
        ["contract", "oracle_boundary", "frozen_snapshots"],
        "live-receipt-re-read")
    add("output validation dropped",
        ["contract", "oracle_boundary", "output_validation"],
        "any-output")
    add("failure class dropped",
        ["contract", "failures", "classes"],
        ["malformed_restore_record"])
    add("failure trigger drift",
        ["contract", "failures", "triggers",
         "unverified_backup"], "never-fails")
    add("failure mapping drift",
        ["contract", "failures", "mapping", "divergent_state"],
        "internal")
    add("enum drift", ["contract", "errors", "closed_enum"],
        ["internal"])
    add("property drift", ["contract", "properties", "atomic"],
        "best-effort")
    add("base path drift",
        ["contract", "versioning", "base_path"],
        "/store/restore/v0")
    add("link drift", ["contract", "links", "backup_contract"],
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
