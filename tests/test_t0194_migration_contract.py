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
        if not isinstance(state, dict):
            _fail("malformed_migration_record")
        digest_re = re.compile(digest_grammar)
        for key, rec in state.items():
            if type(key) is not str:
                _fail("malformed_migration_record")
            if not isinstance(rec, dict):
                _fail("malformed_migration_record")
            if set(rec.keys()) != \
                    set(_NDOCS[0]["record"]["fields"]):
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

    def _call_target_oracle(self, variant, snapshot_fen):
        """THE target-oracle boundary: raising or non-exact-str or
        wrong-grammar output fails closed as divergent_target."""
        grammar = _schema("store-v2")["digest_grammar"]
        try:
            key = self.target_oracle(variant, snapshot_fen)
        except Exception:
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
        if not isinstance(request, dict) or \
                set(request.keys()) != {"from_schema", "to_schema",
                                        "source_id"}:
            _fail("malformed_migration_record")
        for field in ("from_schema", "to_schema"):
            value = request[field]
            if type(value) is not str or \
                    _SCHEMA_RE.fullmatch(value) is None or \
                    _schema(value) is None:
                _fail("malformed_migration_record")
        if type(request["source_id"]) is not str or \
                _ID_RE.fullmatch(request["source_id"]) is None:
            _fail("malformed_migration_record")
        step = _step(request["from_schema"], request["to_schema"])
        if step is None:
            _fail("unknown_migration")
        source_schema = _schema(request["from_schema"])
        self._validate_state(source_state,
                             source_schema["digest_grammar"])
        if state_id(source_state) != request["source_id"]:
            _fail("conflicting_source")
        # STAGED transform: frozen record snapshots, ONE oracle
        # call per record, retained exact built-in-str key
        staged = {}
        for key in sorted(source_state):
            rec = source_state[key]
            frozen = {"variant": rec["variant"],
                      "digest": rec["digest"],
                      "snapshot_fen": rec["snapshot_fen"]}
            new_key = self._call_target_oracle(
                frozen["variant"], frozen["snapshot_fen"])
            staged[key] = {"variant": frozen["variant"],
                           "digest": new_key,
                           "snapshot_fen": frozen["snapshot_fen"]}
        if len(staged) != len(source_state):
            _fail("divergent_target")
        target_id = state_id(staged)
        return {
            "migration_id": "mg1:" + hashlib.sha256(
                f"{request['from_schema']}\n{request['to_schema']}"
                f"\n{request['source_id']}\n{target_id}".encode()
            ).hexdigest(),
            "from_schema": request["from_schema"],
            "to_schema": request["to_schema"],
            "source_id": request["source_id"],
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
