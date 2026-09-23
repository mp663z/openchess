"""T0194: store migration contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/migration.yaml:
registered single-step schema migration over content-addressed
canonical graph states (gs1: ids, graph-diff semantics). Source
states validate through the LINKED transposition-node machinery
(imported, never restated); the target digest oracle is UNTRUSTED
input (single evaluation, frozen snapshots, exact built-in-str
output validation); migration is atomic with rollback - a rejected
migration leaves every input bit-identical.
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
    LEGAL_EP,
    STARTPOS,
    NodeError,
    _make_record,
    _record_identity,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _docs as _node_docs,
)
from tools.migration_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_NDOCS = _node_docs()
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_ID_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
_MID_RE = re.compile(_CC["identifiers"]["migration_id"]["grammar"])
_SCHEMA_RE = re.compile(_CC["identifiers"]["schema_id"]["grammar"])
_REGISTRY = _CC["registry"]


class MigrationError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise MigrationError(cls, FAILURE_MAPPING[cls])


def _node(fen_text, digest_override=None):
    rec = _make_record(*_NDOCS, digest_fen, "standard", fen_text)
    if digest_override is not None:
        rec = dict(rec)
        rec["digest"] = digest_override
    return rec


def _identity(record):
    return repr(_record_identity(*_NDOCS, record))


def _state(*records):
    return {_identity(r): r for r in records}


def state_id(state):
    """Canonical state content digest (graph-diff semantics)."""
    parts = []
    for key in sorted(state):
        rec = state[key]
        body = "|".join(f"{field}={rec[field]}"
                        for field in sorted(rec))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256(
        "".join(parts).encode()).hexdigest()


def _schema(schema_id):
    for entry in _REGISTRY["schemas"]:
        if entry["id"] == schema_id:
            return entry
    return None


def _step(from_schema, to_schema):
    for step in _REGISTRY["steps"]:
        if step["from_schema"] == from_schema and \
                step["to_schema"] == to_schema:
            return step
    return None


class MigrationEngine:
    """The contract's pinned migration: total validation,
    structural source check, untrusted target oracle behind the
    boundary, atomic staged transform, derived receipt ids."""

    def __init__(self, target_oracle):
        self.target_oracle = target_oracle  # UNTRUSTED

    def _validate_state(self, state, digest_grammar):
        """Every state BEFORE migration: mapping, exact-str keys,
        exact valid linked records (field set + exact-string
        guards BEFORE sibling machinery), digest grammar of the
        SOURCE schema, derived identity == key."""
        # EXACT built-in dicts only: a subclass can override
        # items/keys/__getitem__ and lie about its content.
        if type(state) is not dict:
            _fail("malformed_migration_record")
        digest_re = re.compile(digest_grammar)
        for key, rec in dict.items(state):
            if type(key) is not str:
                _fail("malformed_migration_record")
            if type(rec) is not dict:
                _fail("malformed_migration_record")
            # Record keys are type-checked as EXACT str BEFORE any
            # set build, membership test or lookup: a key with a
            # colliding hash and a raising __eq__ fails closed.
            rec_keys = list(dict.keys(rec))
            if not all(type(k) is str for k in rec_keys):
                _fail("malformed_migration_record")
            if set(rec_keys) != set(_NDOCS[0]["record"]["fields"]):
                _fail("malformed_migration_record")
            if type(rec["variant"]) is not str or \
                    type(rec["snapshot_fen"]) is not str or \
                    type(rec["digest"]) is not str:
                _fail("malformed_migration_record")
            try:
                derived = _make_record(*_NDOCS, digest_fen,
                                       rec["variant"],
                                       rec["snapshot_fen"])
            except NodeError:
                _fail("malformed_migration_record")
            if rec["variant"] != derived["variant"] or \
                    rec["snapshot_fen"] != derived["snapshot_fen"]:
                _fail("malformed_migration_record")
            if digest_re.fullmatch(rec["digest"]) is None:
                _fail("malformed_migration_record")
            if _identity(rec) != key:
                _fail("malformed_migration_record")

    def _validate_target_state(self, staged, frozen, to_schema):
        """Post-transform validation: every staged record against
        the TARGET schema and the retained key set - drift fails
        closed as divergent_target (engine-side, never caller
        malformed)."""
        grammar = _schema(to_schema)["digest_grammar"]
        digest_re = re.compile(grammar)
        if set(staged) != set(frozen) or len(staged) != len(frozen):
            _fail("divergent_target")
        for key, rec in dict.items(staged):
            if type(key) is not str or type(rec) is not dict:
                _fail("divergent_target")
            rec_keys = list(dict.keys(rec))
            if not all(type(k) is str for k in rec_keys) or \
                    set(rec_keys) != \
                    set(_NDOCS[0]["record"]["fields"]):
                _fail("divergent_target")
            if type(rec["variant"]) is not str or \
                    type(rec["snapshot_fen"]) is not str or \
                    type(rec["digest"]) is not str:
                _fail("divergent_target")
            try:
                derived = _make_record(*_NDOCS, digest_fen,
                                       rec["variant"],
                                       rec["snapshot_fen"])
            except NodeError:
                _fail("divergent_target")
            if rec["variant"] != derived["variant"] or \
                    rec["snapshot_fen"] != \
                    derived["snapshot_fen"]:
                _fail("divergent_target")
            if digest_re.fullmatch(rec["digest"]) is None:
                _fail("divergent_target")
            if _identity(rec) != key:
                _fail("divergent_target")

    def _call_target_oracle(self, variant, snapshot_fen):
        """THE target-oracle boundary: raising or non-exact-str or
        wrong-grammar output fails closed as divergent_target."""
        grammar = _schema("store-v2")["digest_grammar"]
        # BaseException: the oracle is untrusted, so KeyboardInterrupt,
        # SystemExit and GeneratorExit raised by it fail closed too
        # (contract: hostile oracles fail closed typed, never raw).
        try:
            key = self.target_oracle(variant, snapshot_fen)
        except BaseException:  # noqa: BLE001
            _fail("divergent_target")
        if type(key) is not str or \
                re.fullmatch(grammar, key) is None:
            _fail("divergent_target")
        return key

    def migrate(self, request, source_state):
        """ATOMIC: validate the request and the source state,
        verify the source id structurally, resolve the registered
        step, transform a STAGED copy behind the oracle boundary,
        derive the receipt - the source is never mutated."""
        # EXACT built-in dict with EXACT str keys, checked BEFORE any
        # set build or comparison over caller-owned keys.
        if type(request) is not dict:
            _fail("malformed_migration_record")
        req_keys = list(dict.keys(request))
        if not all(type(k) is str for k in req_keys) or \
                set(req_keys) != {"from_schema", "to_schema",
                                  "source_id"}:
            _fail("malformed_migration_record")
        # the caller's request exactly as received (validated below:
        # exact str keys and values) - restored in its ORIGINAL key
        # order on every exit
        saved_req_items = list(dict.items(request))
        # FREEZE THE REQUEST DURING VALIDATION: each field is read
        # EXACTLY ONCE into a detached plain dict of the validated
        # exact built-in strings - immediately after total request
        # validation and registered-step resolution, BEFORE the
        # first oracle call. Schema lookup, source-id verification,
        # target validation, migration-id derivation and every
        # receipt field read ONLY this frozen copy; the caller's
        # request is never re-read after untrusted code runs.
        frozen_req = {}
        for field in ("from_schema", "to_schema"):
            value = request[field]
            if type(value) is not str or \
                    _SCHEMA_RE.fullmatch(value) is None or \
                    _schema(value) is None:
                _fail("malformed_migration_record")
            frozen_req[field] = value
        value = request["source_id"]
        if type(value) is not str or \
                _ID_RE.fullmatch(value) is None:
            _fail("malformed_migration_record")
        frozen_req["source_id"] = value
        step = _step(frozen_req["from_schema"],
                     frozen_req["to_schema"])
        if step is None:
            _fail("unknown_migration")
        source_schema = _schema(frozen_req["from_schema"])
        self._validate_state(source_state,
                             source_schema["digest_grammar"])
        # INPUT PRESERVATION snapshot (reference-preserving): the
        # oracle may hold external references into the caller's
        # live state - every exit restores it bit-identical.
        saved_container = dict(source_state)
        saved_recs = {id(rec): (rec, dict(rec))
                      for rec in source_state.values()}
        # FREEZE THE ENTIRE SOURCE: one detached plain-dict
        # snapshot taken BEFORE the first oracle call; every later
        # phase - source-id derivation, iteration, transform -
        # reads ONLY the frozen copy, never the caller's live
        # mapping or records.
        frozen = {key: dict(rec)
                  for key, rec in source_state.items()}
        try:
            if state_id(frozen) != frozen_req["source_id"]:
                _fail("conflicting_source")
            # STAGED transform: ONE oracle call per retained key,
            # retained exact built-in-str result
            staged = {}
            for key in sorted(frozen):
                rec = frozen[key]
                new_key = self._call_target_oracle(
                    rec["variant"], rec["snapshot_fen"])
                staged[key] = {"variant": rec["variant"],
                               "digest": new_key,
                               "snapshot_fen":
                               rec["snapshot_fen"]}
            # POST-TRANSFORM validation: the full staged state
            # against the TARGET schema - exact fields/types,
            # canonical snapshot, target digest grammar, derived
            # identity == retained key - plus exact identity-set
            # and cardinality agreement with the frozen source.
            self._validate_target_state(staged, frozen,
                                        frozen_req["to_schema"])
            target_id = state_id(staged)
        finally:
            for rec, content in saved_recs.values():
                rec.clear()
                rec.update(content)
            source_state.clear()
            source_state.update(saved_container)
            # the caller's REQUEST is restored bit-identical too -
            # the oracle may hold an external reference to it
            dict.clear(request)
            dict.update(request, saved_req_items)
        return {
            "migration_id": "mg1:" + hashlib.sha256(
                f"{frozen_req['from_schema']}\n"
                f"{frozen_req['to_schema']}\n"
                f"{frozen_req['source_id']}\n"
                f"{target_id}".encode()).hexdigest(),
            "from_schema": frozen_req["from_schema"],
            "to_schema": frozen_req["to_schema"],
            "source_id": frozen_req["source_id"],
            "target_id": target_id,
            "state": staged,
        }


# -- oracles and fixtures -----------------------------------------------------

CALLS = None


def pdv2_oracle(variant, fen):
    """The honest target oracle: pdv2 digest."""
    return "pdv2:" + hashlib.sha256(
        f"{variant}\n{fen}".encode()).hexdigest()


def _request(source, from_schema="store-v1", to_schema="store-v2"):
    return {"from_schema": from_schema, "to_schema": to_schema,
            "source_id": state_id(source)}


def _v1_state(*fen_texts):
    return _state(*(_node(f) for f in fen_texts))


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_migration():
    engine = MigrationEngine(pdv2_oracle)
    source = _v1_state(STARTPOS, KINGS, AFTER_E4)
    before = copy.deepcopy(source)
    receipt = engine.migrate(_request(source), source)
    assert set(receipt) == set(_FIELDS) | {"state"}
    assert _MID_RE.fullmatch(receipt["migration_id"])
    assert receipt["from_schema"] == "store-v1"
    assert receipt["to_schema"] == "store-v2"
    assert receipt["source_id"] == state_id(source)
    migrated = receipt["state"]
    assert receipt["target_id"] == state_id(migrated)
    # cardinality + identity preservation
    assert len(migrated) == len(source)
    assert set(migrated) == set(source)
    for key, rec in migrated.items():
        assert rec["variant"] == source[key]["variant"]
        assert rec["snapshot_fen"] == source[key]["snapshot_fen"]
        assert rec["digest"] == pdv2_oracle(rec["variant"],
                                            rec["snapshot_fen"])
        assert rec["digest"].startswith("pdv2:")
    assert source == before  # never mutated


def test_empty_state_migration():
    engine = MigrationEngine(pdv2_oracle)
    receipt = engine.migrate(_request(_state()), _state())
    assert receipt["state"] == {}
    assert receipt["target_id"] == state_id({})


def test_determinism():
    engine = MigrationEngine(pdv2_oracle)
    source = _v1_state(STARTPOS, KINGS)
    assert engine.migrate(_request(source), source) == \
        engine.migrate(_request(source), source)


# -- unknown_migration: noop, downgrade, unregistered -------------------------


@pytest.mark.parametrize("pair", [("store-v1", "store-v1"),
                                  ("store-v2", "store-v2"),
                                  ("store-v2", "store-v1")])
def test_unknown_migration(pair):
    engine = MigrationEngine(pdv2_oracle)
    source = _v1_state(STARTPOS)
    before = copy.deepcopy(source)
    req = {"from_schema": pair[0], "to_schema": pair[1],
           "source_id": state_id(source)}
    with pytest.raises(MigrationError) as exc:
        engine.migrate(req, source)
    assert exc.value.failure_class == "unknown_migration"
    assert exc.value.code == FAILURE_MAPPING["unknown_migration"]
    assert exc.value.code in ERROR_ENUM
    assert source == before


# -- malformed requests -------------------------------------------------------

HOSTILE_REQUESTS = [None, True, 0, 1.5, [], "text",
                    {},
                    {"from_schema": "store-v1",
                     "to_schema": "store-v2"},  # missing field
                    {"from_schema": "store-v1", "to_schema":
                     "store-v2", "source_id": "x", "extra": 1},
                    {"from_schema": "store-v9", "to_schema":
                     "store-v2", "source_id": "x"},
                    {"from_schema": "v1", "to_schema": "store-v2",
                     "source_id": "x"},
                    {"from_schema": None, "to_schema": "store-v2",
                     "source_id": "x"},
                    {"from_schema": "store-v1", "to_schema":
                     "store-v2", "source_id": "bad"},
                    {"from_schema": "store-v1", "to_schema":
                     "store-v2", "source_id": None}]


@pytest.mark.parametrize("req", HOSTILE_REQUESTS)
def test_total_over_hostile_requests(req):
    engine = MigrationEngine(pdv2_oracle)
    source = _v1_state(STARTPOS)
    before = copy.deepcopy(source)
    with pytest.raises(MigrationError) as exc:
        engine.migrate(req, source)
    assert exc.value.failure_class == "malformed_migration_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_migration_record"]
    assert exc.value.code in ERROR_ENUM
    assert source == before


# -- conflicting source -------------------------------------------------------


def test_conflicting_source_tampered_id():
    engine = MigrationEngine(pdv2_oracle)
    source = _v1_state(STARTPOS)
    before = copy.deepcopy(source)
    req = _request(source)
    req["source_id"] = "gs1:" + "f" * 64
    with pytest.raises(MigrationError) as exc:
        engine.migrate(req, source)
    assert exc.value.failure_class == "conflicting_source"
    assert exc.value.code in ERROR_ENUM
    assert source == before


def test_conflicting_source_extra_record():
    engine = MigrationEngine(pdv2_oracle)
    source = _v1_state(STARTPOS)
    bloated = _v1_state(STARTPOS, KINGS)
    req = _request(source)  # id of the SMALLER state
    with pytest.raises(MigrationError) as exc:
        engine.migrate(req, bloated)
    assert exc.value.failure_class == "conflicting_source"


# -- hostile states ------------------------------------------------------------

HOSTILE_STATES = [None, True, 0, 1.5, "text", []]


@pytest.mark.parametrize("state", HOSTILE_STATES)
def test_total_over_hostile_states(state):
    engine = MigrationEngine(pdv2_oracle)
    with pytest.raises(MigrationError) as exc:
        engine.migrate({"from_schema": "store-v1",
                        "to_schema": "store-v2",
                        "source_id": state_id({})}, state)
    assert exc.value.failure_class == "malformed_migration_record"


def _hostile_state_entries():
    good_key = _identity(_node(KINGS))
    good_rec = _node(KINGS)
    entries = [("wrong-digest-schema",
                {good_key: dict(good_rec,
                                digest="pdv2:" + "0" * 64)}),
               ("nonpdv-digest",
                {good_key: dict(good_rec, digest="bad")})]
    for bad_key in (None, True, 0):
        entries.append((f"key-{type(bad_key).__name__}",
                        {bad_key: good_rec}))
    for bad_val in (None, True, {}):
        entries.append((f"value-{type(bad_val).__name__}",
                        {good_key: bad_val}))
    entries.append(("wrong-key", {"wrong": good_rec}))
    entries.append(("extra-field",
                    {good_key: dict(good_rec, label="x")}))
    for field in ("variant", "snapshot_fen", "digest"):
        for value in (None, True, 0, 1.5, [], {}):
            entries.append((f"{field}-{type(value).__name__}",
                            {good_key: dict(good_rec,
                                            **{field: value})}))
    return entries


HOSTILE_ENTRIES = _hostile_state_entries()


@pytest.mark.parametrize("name,state", HOSTILE_ENTRIES,
                         ids=[n for n, _ in HOSTILE_ENTRIES])
def test_total_over_hostile_state_entries(name, state):
    """Every hostile record/key/value maps to
    malformed_migration_record; the source id of a GOOD state
    does not launder a hostile one."""
    engine = MigrationEngine(pdv2_oracle)
    good = _v1_state(KINGS)
    req = _request(good)
    before = copy.deepcopy(state)
    with pytest.raises(MigrationError) as exc:
        engine.migrate(req, state)
    assert exc.value.failure_class == "malformed_migration_record"
    assert exc.value.code in ERROR_ENUM
    assert state == before


# -- untrusted target oracle ----------------------------------------------------


def _hostile_oracles():
    def raising(variant, fen):
        raise ValueError("boom")

    def pdv1_format(variant, fen):
        return "pdv1:" + "0" * 64  # wrong grammar for target

    def bad_type(variant, fen):
        return []

    class EvilStr(str):
        def __hash__(self):
            raise RuntimeError("evil")

    def evil_str(variant, fen):
        return EvilStr("pdv2:" + "0" * 64)
    return [("raising", raising), ("pdv1-format", pdv1_format),
            ("bad-type", bad_type), ("evil-str", evil_str)]


@pytest.mark.parametrize("name,oracle", _hostile_oracles(),
                         ids=[n for n, _ in _hostile_oracles()])
def test_hostile_target_oracle(name, oracle):
    """A hostile target oracle fails closed as divergent_target;
    the source is bit-identical."""
    engine = MigrationEngine(oracle)
    source = _v1_state(STARTPOS, KINGS)
    before = copy.deepcopy(source)
    with pytest.raises(MigrationError) as exc:
        engine.migrate(_request(source), source)
    assert exc.value.failure_class == "divergent_target"
    assert exc.value.code == FAILURE_MAPPING["divergent_target"]
    assert exc.value.code in ERROR_ENUM
    assert source == before


def test_oracle_mutating_source_record_during_call():
    """The oracle mutates the LIVE source record mid-call: the
    migration uses the PRE-CALL frozen snapshot, the receipt is
    clean, the source's mutation never enters the result."""
    source = _v1_state(STARTPOS)
    live = next(iter(source.values()))
    before = copy.deepcopy(source)

    def oracle(variant, fen):
        live["snapshot_fen"] = []
        return pdv2_oracle(variant, fen)

    engine = MigrationEngine(oracle)
    receipt = engine.migrate(_request(source), source)
    key = _identity(_node(STARTPOS))
    assert receipt["state"][key]["snapshot_fen"] == \
        before[key]["snapshot_fen"]
    assert receipt["target_id"] == state_id(receipt["state"])


def test_one_oracle_call_per_record():
    calls = {"n": 0}

    def oracle(variant, fen):
        calls["n"] += 1
        return pdv2_oracle(variant, fen)

    engine = MigrationEngine(oracle)
    source = _v1_state(STARTPOS, KINGS, AFTER_E4, LEGAL_EP)
    engine.migrate(_request(source), source)
    assert calls["n"] == 4


# -- lint mutants --------------------------------------------------------------


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
        "free-form-migration")
    add("not_scope drift", ["contract", "role", "not_scope"],
        "auto-chaining-owned-here")
    add("record fields drift", ["contract", "record", "fields"],
        ["migration_id", "from_schema", "to_schema"])
    add("schema grammar drift",
        ["contract", "identifiers", "schema_id", "grammar"],
        "^.*$")
    add("state id supplied",
        ["contract", "identifiers", "state_id", "source"],
        "caller-supplied")
    add("receipt id grammar drift",
        ["contract", "identifiers", "migration_id", "grammar"],
        "^mg1:.*$")
    add("schema dropped",
        ["contract", "registry", "schemas"], [])
    add("step transform drift",
        ["contract", "registry", "steps", 0, "transform"],
        "drop-every-record")
    add("chaining drift", ["contract", "registry", "chaining"],
        "implicit-multi-hop")
    add("source check dropped",
        ["contract", "semantics", "source_check"],
        "caller-id-trusted")
    add("target verification dropped",
        ["contract", "semantics", "target_verification"],
        "oracle-claims-trusted")
    add("cardinality drift",
        ["contract", "semantics", "cardinality"], "best-effort")
    add("identity drift",
        ["contract", "semantics", "identity_preservation"],
        "identities-recomputed")
    add("commit drift", ["contract", "semantics", "commit"],
        "in-place-mutation")
    add("oracle trusted",
        ["contract", "oracle_boundary", "role"],
        "oracle-always-honest")
    add("single evaluation dropped",
        ["contract", "oracle_boundary", "single_evaluation"],
        "requery-allowed")
    add("frozen dropped",
        ["contract", "oracle_boundary", "frozen_snapshots"],
        "live-records")
    add("request freeze dropped",
        ["contract", "oracle_boundary", "request_freeze"],
        "live-request-re-read-after-oracle")
    add("post-transform validation dropped",
        ["contract", "semantics", "post_transform_validation"],
        "digest-only")
    add("output validation dropped",
        ["contract", "oracle_boundary", "output_validation"],
        "any-output")
    add("failure class dropped", ["contract", "failures",
                                  "classes"],
        ["malformed_migration_record"])
    add("failure trigger drift",
        ["contract", "failures", "triggers", "unknown_migration"],
        "never-fails")
    add("failure mapping drift",
        ["contract", "failures", "mapping", "divergent_target"],
        "internal")
    add("enum drift", ["contract", "errors", "closed_enum"],
        ["internal"])
    add("property drift", ["contract", "properties", "atomic"],
        "best-effort")
    add("base path drift", ["contract", "versioning",
                            "base_path"],
        "/store/migration/v0")
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


# -- rollback + mutants ---------------------------------------------------------


def test_rejected_migration_rollback_bit_identical():
    """Every failure class leaves the request and source
    bit-identical."""
    engine = MigrationEngine(pdv2_oracle)
    source = _v1_state(STARTPOS, KINGS)
    before = copy.deepcopy(source)
    # unknown
    with pytest.raises(MigrationError):
        engine.migrate({"from_schema": "store-v1",
                        "to_schema": "store-v1",
                        "source_id": state_id(source)}, source)
    # conflicting
    with pytest.raises(MigrationError):
        engine.migrate({"from_schema": "store-v1",
                        "to_schema": "store-v2",
                        "source_id": "gs1:" + "0" * 64}, source)
    # oracle failure mid-batch
    def midway(variant, fen):
        midway.n += 1
        if midway.n == 2:
            raise ValueError("mid-batch")
        return pdv2_oracle(variant, fen)
    midway.n = 0
    with pytest.raises(MigrationError):
        MigrationEngine(midway).migrate(_request(source), source)
    assert source == before


def test_mutant_in_place_migration_corrupts_source():
    """Behavioral mutant: migrating IN PLACE mutates the caller's
    source records - pinned to prove the staged copy is
    load-bearing. Counter-test: the real engine never mutates
    the source."""
    def mutant_migrate(source):
        for rec in source.values():
            rec["digest"] = pdv2_oracle(rec["variant"],
                                        rec["snapshot_fen"])
        return source

    source = _v1_state(STARTPOS)
    before = copy.deepcopy(source)
    mutant_migrate(source)
    assert source != before  # the mutant corrupted the source
    source = _v1_state(STARTPOS)
    engine = MigrationEngine(pdv2_oracle)
    engine.migrate(_request(source), source)
    assert source == before


# -- v2: full-source freeze + post-transform validation -----------------------


def _future_record_attack_oracle(source, later_key, calls):
    """UNTRUSTED: on call 1, corrupts the record scheduled for a
    LATER call (replace contents, delete it, add a new one) AND
    mutates the live container."""
    def oracle(variant, fen):
        calls["n"] += 1
        if calls["n"] == 1:
            source[later_key]["snapshot_fen"] = []  # hostile
            source["injected"] = {"variant": "standard",
                                  "digest": "pdv1:" + "9" * 64,
                                  "snapshot_fen": STARTPOS}
            doomed = sorted(k for k in source
                            if k != later_key)[0]
            if doomed in source:
                del source[doomed]
        return pdv2_oracle(variant, fen)
    return oracle


def test_future_record_attack_closed_by_full_freeze():
    """The verifier's replay: an oracle corrupting FUTURE records
    and the live container on call 1 cannot affect the migration
    - receipt and migrated state derive EXACTLY from the pre-call
    frozen snapshot, one oracle call per original retained key,
    and the caller's input is restored bit-identical."""
    source = _v1_state(STARTPOS, KINGS, AFTER_E4)
    before = copy.deepcopy(source)
    keys = sorted(source)
    later_key = keys[1]
    calls = {"n": 0}
    engine = MigrationEngine(
        _future_record_attack_oracle(source, later_key, calls))
    receipt = engine.migrate(_request(source), source)
    assert calls["n"] == 3  # one per ORIGINAL retained key
    expected = {}
    for key, rec in before.items():
        expected[key] = {"variant": rec["variant"],
                         "digest": pdv2_oracle(rec["variant"],
                                               rec["snapshot_fen"]),
                         "snapshot_fen": rec["snapshot_fen"]}
    assert receipt["state"] == expected
    assert receipt["target_id"] == state_id(expected)
    assert receipt["source_id"] == state_id(before)
    assert source == before  # input restored bit-identical


def test_input_restored_after_success_against_external_reference():
    """A hostile oracle with an external reference into the
    caller's state: even a SUCCESSFUL migration leaves the
    caller's input bit-identical."""
    source = _v1_state(STARTPOS, KINGS)
    before = copy.deepcopy(source)

    def oracle(variant, fen):
        for rec in source.values():
            rec["digest"] = "corrupted"
        source["junk"] = {"x": 1}
        return pdv2_oracle(variant, fen)

    engine = MigrationEngine(oracle)
    receipt = engine.migrate(_request(source), source)
    assert _MID_RE.fullmatch(receipt["migration_id"])
    assert source == before


def test_mutant_skipping_post_transform_validation():
    """Behavioral mutant: the v1 engine (freeze-per-record, no
    full-source freeze, no post-transform validation) ACCEPTS the
    future-record attack - receipt citing the pre-mutation source
    id over a corrupted target. Counter-test: the real engine is
    immune."""
    def mutant_migrate(engine, request, source):
        staged = {}
        for key in sorted(source):
            rec = source[key]
            frozen = dict(rec)
            staged[key] = {"variant": frozen["variant"],
                           "digest": engine._call_target_oracle(
                               frozen["variant"],
                               frozen["snapshot_fen"]),
                           "snapshot_fen": frozen["snapshot_fen"]}
        return staged

    def make_source():
        return _v1_state(STARTPOS, KINGS, AFTER_E4)

    source = make_source()
    before = copy.deepcopy(source)
    keys = sorted(source)
    calls = {"n": 0}
    engine = MigrationEngine(
        _future_record_attack_oracle(source, keys[1], calls))
    mutant_result = mutant_migrate(engine, _request(source),
                                   source)
    # the mutant's target carries the corrupted record / wrong
    # cardinality - the attack landed
    assert mutant_result.get(keys[1], {}).get(
        "snapshot_fen") != before[keys[1]]["snapshot_fen"] or \
        len(mutant_result) != len(before)
    # real engine: exact pre-call snapshot derivation
    source = make_source()
    calls = {"n": 0}
    engine = MigrationEngine(
        _future_record_attack_oracle(source, keys[1], calls))
    receipt = engine.migrate(_request(source), source)
    assert receipt["state"][keys[1]]["snapshot_fen"] == \
        before[keys[1]]["snapshot_fen"]
    assert len(receipt["state"]) == len(before)
    assert source == before


def test_post_transform_validation_catches_identity_drift():
    """Direct unit: a staged record whose derived identity differs
    from its retained key is divergent_target."""
    engine = MigrationEngine(pdv2_oracle)
    frozen = _v1_state(STARTPOS)
    key = next(iter(frozen))
    drifted = {_identity(_node(KINGS)): dict(
        frozen[key], digest="pdv2:" + "0" * 64)}
    with pytest.raises(MigrationError) as exc:
        engine._validate_target_state(drifted, frozen, "store-v2")
    assert exc.value.failure_class == "divergent_target"
    wrong_grammar = {key: dict(frozen[key],
                               digest="pdv1:" + "0" * 64)}
    with pytest.raises(MigrationError) as exc:
        engine._validate_target_state(wrong_grammar, frozen,
                                      "store-v2")
    assert exc.value.failure_class == "divergent_target"
    shrunk = {}
    with pytest.raises(MigrationError) as exc:
        engine._validate_target_state(shrunk, frozen, "store-v2")
    assert exc.value.failure_class == "divergent_target"


# -- v3: request freeze against request-closing oracles -----------------------

_FORGED_REQUEST_VALUES = {
    "from_schema": "store-v2",
    "to_schema": "store-v1",
    "source_id": "gs1:" + "f" * 64,
}


def _request_mutating_oracle(request, mutation, calls,
                             raise_on=None):
    """UNTRUSTED: on call 1, mutates the caller's live REQUEST
    dict (a single field, a cleared/replaced dict, or a combined
    request+source attack); optionally raises on a later call."""
    def oracle(variant, fen):
        calls["n"] += 1
        if calls["n"] == 1:
            if mutation in _FORGED_REQUEST_VALUES:
                request[mutation] = _FORGED_REQUEST_VALUES[
                    mutation]
            elif mutation == "clear":
                request.clear()
            elif mutation == "replace":
                request.clear()
                request.update({"from_schema": "store-v2",
                                "to_schema": "store-v2",
                                "source_id": "gs1:" + "f" * 64})
        if raise_on is not None and calls["n"] == raise_on:
            raise ValueError("mutate then explode")
        return pdv2_oracle(variant, fen)
    return oracle


@pytest.mark.parametrize("field", ["from_schema", "to_schema",
                                   "source_id"])
def test_request_field_mutation_during_oracle(field):
    """An oracle mutating each request field separately on call 1:
    the SUCCESSFUL migration returns a receipt EXACTLY equal to
    the honest pre-call migration, and the caller's request and
    source are restored bit-identical."""
    source = _v1_state(STARTPOS, KINGS)
    source_before = copy.deepcopy(source)
    honest = MigrationEngine(pdv2_oracle).migrate(
        _request(copy.deepcopy(source)), copy.deepcopy(source))
    request = _request(source)
    request_before = dict(request)
    calls = {"n": 0}
    engine = MigrationEngine(
        _request_mutating_oracle(request, field, calls))
    receipt = engine.migrate(request, source)
    assert receipt == honest
    assert request == request_before
    assert source == source_before


@pytest.mark.parametrize("mutation", ["clear", "replace"])
def test_request_cleared_or_replaced_during_oracle(mutation):
    """An oracle clearing or wholesale replacing the request dict
    on call 1: the engine stays TOTAL (no raw KeyError - the
    receipt derives from the frozen request), returns the honest
    receipt, and restores the caller's request bit-identical."""
    source = _v1_state(STARTPOS, KINGS)
    source_before = copy.deepcopy(source)
    honest = MigrationEngine(pdv2_oracle).migrate(
        _request(copy.deepcopy(source)), copy.deepcopy(source))
    request = _request(source)
    request_before = dict(request)
    calls = {"n": 0}
    engine = MigrationEngine(
        _request_mutating_oracle(request, mutation, calls))
    receipt = engine.migrate(request, source)
    assert receipt == honest
    assert request == request_before
    assert source == source_before


def test_request_mutation_combined_with_source_attack():
    """Combined attack: on call 1 the oracle mutates a request
    field AND the record scheduled for call 2 AND the live
    container - the receipt and migrated state remain exactly the
    honest pre-call derivation, one oracle call per original
    retained key, request and source restored."""
    source = _v1_state(STARTPOS, KINGS, AFTER_E4)
    source_before = copy.deepcopy(source)
    honest = MigrationEngine(pdv2_oracle).migrate(
        _request(copy.deepcopy(source)), copy.deepcopy(source))
    request = _request(source)
    request_before = dict(request)
    keys = sorted(source)
    calls = {"n": 0}

    def oracle(variant, fen):
        calls["n"] += 1
        if calls["n"] == 1:
            request["source_id"] = "gs1:" + "f" * 64
            source[keys[1]]["snapshot_fen"] = []
            source["injected"] = {"variant": "standard",
                                  "digest": "pdv1:" + "9" * 64,
                                  "snapshot_fen": STARTPOS}
            del source[keys[0]]
        return pdv2_oracle(variant, fen)

    receipt = MigrationEngine(oracle).migrate(request, source)
    assert calls["n"] == 3
    assert receipt == honest
    assert request == request_before
    assert source == source_before


def test_request_mutation_then_raise_stays_typed():
    """An oracle mutating the request on call 1 and RAISING on
    call 2: typed divergent_target (never a raw escape), request
    and source restored bit-identical."""
    source = _v1_state(STARTPOS, KINGS)
    source_before = copy.deepcopy(source)
    request = _request(source)
    request_before = dict(request)
    calls = {"n": 0}
    engine = MigrationEngine(
        _request_mutating_oracle(request, "source_id", calls,
                                 raise_on=2))
    with pytest.raises(MigrationError) as exc:
        engine.migrate(request, source)
    assert exc.value.failure_class == "divergent_target"
    assert request == request_before
    assert source == source_before


def test_mutant_live_request_reread_after_oracle():
    """Behavioral mutant: the v2 engine re-read the LIVE request
    after oracle calls to build the receipt - a forged source_id
    is accepted into the receipt and a cleared request escapes as
    a raw KeyError. Counter-test: the real engine derives the
    receipt only from the frozen request and stays total."""
    def mutant_migrate(engine, request, source):
        frozen = {key: dict(rec) for key, rec in source.items()}
        staged = {}
        for key in sorted(frozen):
            rec = frozen[key]
            staged[key] = {
                "variant": rec["variant"],
                "digest": engine._call_target_oracle(
                    rec["variant"], rec["snapshot_fen"]),
                "snapshot_fen": rec["snapshot_fen"]}
        engine._validate_target_state(staged, frozen, "store-v2")
        target_id = state_id(staged)
        return {"migration_id": "mg1:" + hashlib.sha256(
            f"{request['from_schema']}\n{request['to_schema']}"
            f"\n{request['source_id']}\n{target_id}".encode()
        ).hexdigest(),
            "from_schema": request["from_schema"],
            "to_schema": request["to_schema"],
            "source_id": request["source_id"],
            "target_id": target_id,
            "state": staged}

    # forged source_id lands in the mutant's receipt
    source = _v1_state(STARTPOS)
    request = _request(source)
    calls = {"n": 0}
    engine = MigrationEngine(
        _request_mutating_oracle(request, "source_id", calls))
    receipt = mutant_migrate(engine, request, source)
    assert receipt["source_id"] == "gs1:" + "f" * 64
    # cleared request escapes the mutant as a raw KeyError
    source = _v1_state(STARTPOS)
    request = _request(source)
    calls = {"n": 0}
    engine = MigrationEngine(
        _request_mutating_oracle(request, "clear", calls))
    with pytest.raises(KeyError):
        mutant_migrate(engine, request, source)
    # the real engine: honest receipt, restored inputs, total
    source = _v1_state(STARTPOS)
    source_before = copy.deepcopy(source)
    honest = MigrationEngine(pdv2_oracle).migrate(
        _request(copy.deepcopy(source)), copy.deepcopy(source))
    request = _request(source)
    request_before = dict(request)
    calls = {"n": 0}
    engine = MigrationEngine(
        _request_mutating_oracle(request, "clear", calls))
    assert engine.migrate(request, source) == honest
    assert request == request_before
    assert source == source_before


# -- totality: hostile keys, dict subclasses, BaseException oracles ----------


class _SK(str):
    """str subclass: hashes like the key it imitates, raises on ==."""

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


def _rekey(mapping, field, key_type):
    """Copy of mapping, same order, with one key re-keyed hostile."""
    return {key_type(k) if k == field else k: v
            for k, v in dict.items(mapping)}


def _refs(obj):
    """Identity snapshot that never hashes or compares a caller key."""
    if type(obj) is dict:
        return tuple((id(k), id(v), _refs(v))
                     for k, v in dict.items(obj))
    return id(obj)


def _assert_fails_untouched(request, source, failure_class,
                            oracle=pdv2_oracle):
    before = (_refs(request), _refs(source))
    with pytest.raises(MigrationError) as exc:
        MigrationEngine(oracle).migrate(request, source)
    assert exc.value.failure_class == failure_class
    assert exc.value.code == FAILURE_MAPPING[failure_class]
    assert exc.value.code in ERROR_ENUM
    assert (_refs(request), _refs(source)) == before


@pytest.mark.parametrize("field", ["from_schema", "to_schema",
                                   "source_id"])
@pytest.mark.parametrize("key_type", [_SK, _HK], ids=["SK", "HK"])
def test_total_over_hostile_request_keys(field, key_type):
    source = _v1_state(STARTPOS, KINGS)
    request = _rekey(_request(source), field, key_type)
    _assert_fails_untouched(request, source,
                            "malformed_migration_record")


@pytest.mark.parametrize("field", ["variant", "snapshot_fen",
                                   "digest"])
@pytest.mark.parametrize("key_type", [_SK, _HK], ids=["SK", "HK"])
def test_total_over_hostile_record_keys(field, key_type):
    good = _node(KINGS)
    source = {_identity(good): _rekey(good, field, key_type)}
    _assert_fails_untouched(_request(_v1_state(KINGS)), source,
                            "malformed_migration_record")


@pytest.mark.parametrize("key_type", [_SK, _HK], ids=["SK", "HK"])
def test_hostile_keys_escape_raw_without_guard(key_type):
    """The probes bite: an unguarded set comparison raises raw."""
    rec = _rekey(_node(KINGS), "digest", key_type)
    with pytest.raises(RuntimeError):
        set(rec.keys()) != set(_NDOCS[0]["record"]["fields"])  # noqa: B015
    req = _rekey(_request(_v1_state(KINGS)), "source_id", key_type)
    with pytest.raises(RuntimeError):
        set(req.keys()) != {"from_schema", "to_schema",  # noqa: B015
                            "source_id"}


@pytest.mark.parametrize("where", ["request", "source", "record"])
def test_dict_subclasses_rejected(where):
    source = _v1_state(STARTPOS, KINGS)
    request = _request(source)
    if where == "request":
        request = _DictSub(request)
    elif where == "source":
        source = _DictSub(source)
    else:
        source = {k: _DictSub(v) for k, v in source.items()}
    _assert_fails_untouched(request, source,
                            "malformed_migration_record")


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit,
                                      GeneratorExit])
def test_base_exception_oracle_fails_closed(exc_type):
    """The oracle boundary catches BaseException: an untrusted
    oracle raising KeyboardInterrupt/SystemExit/GeneratorExit
    fails closed as divergent_target, never raw."""
    def oracle(variant, fen):
        raise exc_type("hostile")
    source = _v1_state(STARTPOS, KINGS)
    _assert_fails_untouched(_request(source), source,
                            "divergent_target", oracle)


def test_request_restored_in_original_key_order():
    """A successful migrate leaves the caller's request with the
    same keys, values AND key order (and same objects) it had,
    even when the oracle reorders it mid-call."""
    source = _v1_state(STARTPOS, KINGS)
    base = _request(source)
    request = {k: base[k] for k in ("source_id", "to_schema",
                                    "from_schema")}
    order = list(request)
    items = [(id(k), id(v)) for k, v in request.items()]

    def reordering(variant, fen):
        saved = dict(request)
        request.clear()
        request.update(sorted(saved.items()))
        return pdv2_oracle(variant, fen)
    receipt = MigrationEngine(reordering).migrate(request, source)
    assert receipt == MigrationEngine(pdv2_oracle).migrate(
        dict(base), copy.deepcopy(source))
    assert list(request) == order
    assert [(id(k), id(v)) for k, v in request.items()] == items
