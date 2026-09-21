"""T0150: closed provenance fixture, before T0151 red and T0152 runtime."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import marshal
from pathlib import Path

import pytest
import yaml

import tests.test_t0149_provenance_contract as oracle

ROOT = Path(__file__).resolve().parents[1]
ORACLE_PATH = ROOT / "tests" / "test_t0149_provenance_contract.py"
ORACLE_FILE_SHA256 = "8a2e5ab1f1e9be53bfe39cf19414da50ecbc7f86b623c7820eee410374cf6b93"


def _load_isolated_oracle_class():
    assert hashlib.sha256(ORACLE_PATH.read_bytes()).hexdigest() == ORACLE_FILE_SHA256
    name = "_t0150_isolated_provenance_oracle"
    spec = importlib.util.spec_from_file_location(name, ORACLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    prior = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if prior is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prior
    cls = module.ProvenanceTable
    cls._t0150_source_path = str(ORACLE_PATH.resolve())
    cls._t0150_error_class = module.ProvenanceError
    return cls


ANCHORED_PROVENANCE_TABLE = _load_isolated_oracle_class()
CASES = json.loads((ROOT / "tests/fixtures/provenance/cases.json").read_text())
PC, IC = oracle._docs()
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes", *SECTIONS}
COMMON = {"name", "scenario", "kind", "payload_sha256"}
KIND_KEYS = {
    "insert": COMMON | {"record"},
    "sequence": COMMON | {"records", "expect_record_count"},
    "reject": COMMON | {"record", "expect_failure", "minimal_repair"},
    "rollback": COMMON
    | {"setup", "reject_record", "expect_failure", "then_record", "expect_record_count"},
}
OPTIONAL = {"expect_record", "expect_source_count"}
RECORD_KEYS = {"target_kind", "target", "sources"}
SOURCE_KEYS = {"source_id", "game_id", "first_observed_at"}
TARGET_KEYS = {
    "transposition_node": {"variant", "snapshot_fen"},
    "route_edge": {"variant", "move", "from_snapshot_fen", "to_snapshot_fen"},
    "opening_context": {"variant", "path_moves"},
}
PINS = {
    (
        "happy",
        "node-unsorted-sources-canonicalize",
    ): "334bc30673e084598be7e0fbac17ed51025b07f24fb00bce380bf027e2993cb2",
    (
        "happy",
        "edge-single-source",
    ): "418e60b01f9fbb4cda6d6f7f61bbb9f64883dda91f268ebd86b040a48c68ef94",
    (
        "happy",
        "context-single-source",
    ): "4e37c25f0bda4fd6c4a0107052317d6e3c3843cf46760c80777f504d62ac876e",
    (
        "boundary",
        "empty-opening-path-is-valid",
    ): "a02d3679684b089cb36c99a62ca3e6f708b6e7006d58c9bcaf43532ea5986aa1",
    (
        "boundary",
        "duplicate-sources-collapse",
    ): "9282c64c291c4f99a83907c5500d6d33837b13368ee8e3da56f654cd08cd75e3",
    (
        "boundary",
        "same-target-unions-sources",
    ): "75e45574144de5080c62f3f38aea0557184d2f50f3c6e2d8838990e4a7772e0d",
    (
        "boundary",
        "distinct-targets-stay-distinct",
    ): "9732465012a28a92cc7c51c8e3e04a04f09f64516a4f997db7b8be7293dc5518",
    (
        "malformed",
        "unknown-target-kind",
    ): "e966b1caea2609a4c0ee39452259e798891b8d5834e1c1939afd2072a8fc1603",
    (
        "malformed",
        "unknown-source-id",
    ): "d9d2eff7ecbd61ce38fb62466ff340ad8589545ac958c4f4029c4e4268cf30ae",
    (
        "malformed",
        "empty-source-set",
    ): "d2047f6722f662913cd0f5e55ebb7560d847386bc4c1d63bea7a85aea23d376e",
    (
        "malformed",
        "node-target-invalid-fen",
    ): "9408b7d23cca311c1e3795cf458832dd568e229d743e7f4007a874026a77573b",
    (
        "malformed",
        "source-timestamp-impossible",
    ): "8621ead70477878d8b6fbc97611ae6ed7fb4a0886b067babf483309b87500c85",
    (
        "malformed",
        "record-extra-field",
    ): "c9f76f77e628322ea331b7610a58fd8daa55e0817d2c1e286efe75db00225366",
    (
        "rollback",
        "unknown-source-leaves-existing-table",
    ): "169c97d695670af2833204579144bd8862ce1b0bfe2ded8f9798ab356d9d5ea7",
    (
        "rollback",
        "bad-target-leaves-existing-table",
    ): "e20364af84091ca559194fc148024cf284b449e9fe9d4cefc5b14bddbd0d42fa",
}
FAMILIES = {
    "happy": {"insert-new-context-target", "insert-new-edge-target", "insert-new-node-target"},
    "boundary": {
        "monotone-same-key-union",
        "set-deduplication",
        "sibling-legal-empty-path",
        "target-identity-separation",
    },
    "malformed": {
        "closed-target-kind",
        "linked-target-validation",
        "record-closed-key-set",
        "rights-registry-fail-closed",
        "sources-nonempty",
        "timestamp-calendar-validation",
    },
    "rollback": {"insert-rollback-malformed-target", "insert-rollback-unknown-source"},
}


def _payload_hash(case):
    payload = {
        key: case[key] for key in sorted(case) if key not in {"name", "scenario", "payload_sha256"}
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _source(value):
    assert type(value) is dict and set(value) == SOURCE_KEYS
    assert all(type(value[key]) is str for key in SOURCE_KEYS)


def _target(kind, value, allow_unknown=False):
    assert type(value) is dict
    if kind not in TARGET_KEYS:
        assert allow_unknown
        return
    assert set(value) == TARGET_KEYS[kind]
    assert type(value["variant"]) is str
    if kind == "opening_context":
        assert type(value["path_moves"]) is list
        assert all(type(move) is str for move in value["path_moves"])
    else:
        assert all(type(value[key]) is str for key in TARGET_KEYS[kind])


def _record(value, allow_invalid=False):
    assert type(value) is dict
    if allow_invalid and set(value) != RECORD_KEYS:
        return
    assert set(value) == RECORD_KEYS
    assert type(value["target_kind"]) is str
    _target(value["target_kind"], value["target"], allow_unknown=allow_invalid)
    assert type(value["sources"]) is list
    for source in value["sources"]:
        _source(source)


def _repair(case):
    repair = case["minimal_repair"]
    assert type(repair) is dict and set(repair) == {"op", "path", "value"}
    assert repair["op"] in {"set", "append", "remove"}
    assert type(repair["path"]) is list and repair["path"]
    assert all(type(part) in {str, int} and type(part) is not bool for part in repair["path"])
    parent = case["record"]
    for part in repair["path"][:-1]:
        if type(parent) is list:
            assert type(part) is int and 0 <= part < len(parent)
        else:
            assert type(part) is str and part in parent
        parent = parent[part]
    leaf = repair["path"][-1]
    if repair["op"] == "append":
        assert type(parent) is dict and type(leaf) is str and leaf in parent
        assert type(parent[leaf]) is list
        _source(repair["value"])
    elif repair["op"] == "remove":
        assert repair["value"] is None and type(parent) is dict
        assert type(leaf) is str and leaf in parent
    else:
        assert (type(parent) is dict and type(leaf) is str and leaf in parent) or (
            type(parent) is list and type(leaf) is int and 0 <= leaf < len(parent)
        )
    out = copy.deepcopy(case["record"])
    node = out
    for part in repair["path"][:-1]:
        node = node[part]
    if repair["op"] == "set":
        node[leaf] = copy.deepcopy(repair["value"])
    elif repair["op"] == "append":
        node[leaf].append(copy.deepcopy(repair["value"]))
    else:
        del node[leaf]
    _record(out)
    return out


def _strict(section, case):
    assert type(case) is dict and type(case.get("kind")) is str
    assert case["kind"] in KIND_KEYS
    required = KIND_KEYS[case["kind"]]
    assert required <= set(case) <= required | OPTIONAL
    assert type(case["name"]) is str and case["name"].strip()
    assert type(case["scenario"]) is str and case["scenario"].strip()
    assert case["scenario"] in FAMILIES[section]
    assert case["payload_sha256"] == _payload_hash(case)
    assert case["payload_sha256"] == PINS[(section, case["name"])]
    kind = case["kind"]
    if kind == "insert":
        _record(case["record"])
    elif kind == "sequence":
        assert type(case["records"]) is list and case["records"]
        for record in case["records"]:
            _record(record)
    elif kind == "reject":
        _record(case["record"], allow_invalid=True)
        assert type(case["expect_failure"]) is str
        _repair(case)
    else:
        assert type(case["setup"]) is list and case["setup"]
        for record in case["setup"]:
            _record(record)
        _record(case["reject_record"])
        _record(case["then_record"])
        assert type(case["expect_failure"]) is str
    if "expect_record" in case:
        _record(case["expect_record"])
    for count in ("expect_record_count", "expect_source_count"):
        if count in case:
            assert type(case[count]) is int and type(case[count]) is not bool and case[count] >= 0


def _structure(doc):
    assert type(doc) is dict and set(doc) == TOP_KEYS
    assert type(doc["schema"]) is int and doc["schema"] == 1
    assert doc["contract"] == PC["id"]
    schema = yaml.safe_load((ROOT / "data/contracts/provenance.yaml").read_text())["schema_version"]
    assert doc["contract_schema_version"] == schema
    assert set(PINS) == {(section, case["name"]) for section in SECTIONS for case in doc[section]}
    for section in SECTIONS:
        assert type(doc[section]) is list and doc[section]
        assert {case["scenario"] for case in doc[section]} == FAMILIES[section]
        for case in doc[section]:
            _strict(section, case)
    assert {c["expect_failure"] for c in doc["malformed"]} == set(PC["failures"]["classes"])


def _object_fingerprint(value):
    code = getattr(value, "__code__", None)
    if code is not None:
        return ("code", hashlib.sha256(marshal.dumps(code)).hexdigest())
    return ("identity", id(value))


def _class_fingerprint(cls):
    assert type(cls) is type
    assert cls.__bases__ == (object,)
    own = tuple(
        sorted(
            (name, _object_fingerprint(value))
            for name, value in vars(cls).items()
            if name not in {"__dict__", "__weakref__"}
        )
    )
    dispatch = tuple(
        (name, _object_fingerprint(inspect.getattr_static(cls, name)))
        for name in (
            "__new__",
            "__getattribute__",
            "__getattr__",
            "__init__",
            "insert",
            "merge",
            "records",
            "serialize",
        )
        if inspect.getattr_static(cls, name, None) is not None
    )
    return own, dispatch


def _make_resolver(anchor):
    frozen_class = anchor
    frozen_fingerprint = _class_fingerprint(anchor)
    frozen_source = anchor._t0150_source_path

    def resolve(provider):
        cls = provider()
        assert cls is frozen_class
        assert type(cls) is type
        assert _class_fingerprint(cls) == frozen_fingerprint
        assert getattr(cls, "_t0150_source_path", None) == frozen_source
        return cls

    return resolve, frozen_class, frozen_fingerprint


_resolve_provider, _CAPTURED_ANCHOR, _CAPTURED_FINGERPRINT = _make_resolver(
    ANCHORED_PROVENANCE_TABLE
)


def _provider(_anchor=_CAPTURED_ANCHOR):
    return _anchor


def _assert_acyclic(value, active=None, done=None):
    active = set() if active is None else active
    done = set() if done is None else done
    if not isinstance(value, (dict, list)):
        return
    identity = id(value)
    if identity in active:
        raise AssertionError("cyclic fixture payload is forbidden")
    if identity in done:
        return
    active.add(identity)
    children = dict.values(value) if isinstance(value, dict) else list.__iter__(value)
    for child in children:
        _assert_acyclic(child, active, done)
    active.remove(identity)
    done.add(identity)


def _safe_scalar(value):
    assert type(value) in {str, int, bool, type(None)}
    return value


def _snapshot_graph(value, memo=None):
    memo = {} if memo is None else memo
    if isinstance(value, (dict, list)) and id(value) in memo:
        return memo[id(value)]
    if isinstance(value, dict):
        snapshot = ["dict", value, []]
        memo[id(value)] = snapshot
        snapshot[2].extend(
            (_safe_scalar(key), _snapshot_graph(item, memo)) for key, item in dict.items(value)
        )
        return snapshot
    if isinstance(value, list):
        snapshot = ["list", value, []]
        memo[id(value)] = snapshot
        snapshot[2].extend(_snapshot_graph(item, memo) for item in list.__iter__(value))
        return snapshot
    return ["scalar", None, _safe_scalar(value)]


def _restore_graph(snapshot, restored=None):
    restored = {} if restored is None else restored
    kind, original, payload = snapshot
    if kind == "scalar":
        return payload
    if id(snapshot) in restored:
        return restored[id(snapshot)]
    restored[id(snapshot)] = original
    if kind == "dict":
        values = [(key, _restore_graph(child, restored)) for key, child in payload]
        dict.clear(original)
        for key, item in values:
            dict.__setitem__(original, key, item)
    else:
        values = [_restore_graph(child, restored) for child in payload]
        list.clear(original)
        list.extend(original, values)
    return original


def _probe_hooks(value):
    if isinstance(value, dict):
        if type(value) is not dict:
            list(iter(value))
            raise AssertionError("mapping subclasses cannot cross fixture boundary")
        for item in dict.values(value):
            _probe_hooks(item)
    elif isinstance(value, list):
        if type(value) is not list:
            list(iter(value))
            raise AssertionError("sequence subclasses cannot cross fixture boundary")
        for item in list.__iter__(value):
            _probe_hooks(item)


class _FixtureTable:
    """Closure guard around the anchored contract oracle.

    Only exact builtin containers cross the boundary. Stored state is
    revalidated before and after each operation; hostile subclasses
    never become live table state or caller-owned input mutations.
    """

    def __init__(self, provider=_provider):
        self._class = _resolve_provider(provider)
        self.Error = self._class._t0150_error_class
        self._table = self._class((PC, IC))

    @property
    def by_key(self):
        return self._table.by_key

    @by_key.setter
    def by_key(self, value):
        self._table.by_key = value

    def _closed_state(self):
        assert type(self._table.by_key) is dict
        records = self._table.records()
        for record in records:
            oracle.validate_record(PC, IC, record)
        return copy.deepcopy(records)

    def insert(self, record):
        self._closed_state()
        _assert_acyclic(record)
        caller_snapshot = None
        try:
            caller_snapshot = _snapshot_graph(record)
            _probe_hooks(record)
            _record(record, allow_invalid=True)
            owned = copy.deepcopy(record)
            out = self._table.insert(owned)
            self._closed_state()
            return out
        finally:
            if caller_snapshot is not None:
                _restore_graph(caller_snapshot)

    def merge(self, other):
        self._closed_state()
        assert isinstance(other, _FixtureTable)
        other._closed_state()
        out = self._table.merge(other._table)
        self._closed_state()
        return out

    def records(self):
        self._closed_state()
        return self._table.records()

    def serialize(self):
        self._closed_state()
        return self._table.serialize()


def _table(provider=_provider):
    return _FixtureTable(provider)


def test_structure_and_external_pins():
    _structure(CASES)
    assert "T0151" in CASES["notes"] and "T0152" in CASES["notes"]


def test_rewrite_rehash_mutants_fail_external_pins_and_nested_schemas():
    donor = CASES["happy"][1]
    target = CASES["happy"][0]
    mutants = []
    whole = copy.deepcopy(target)
    for key in set(whole) - {"name", "scenario", "payload_sha256"}:
        whole.pop(key)
    for key, value in donor.items():
        if key not in {"name", "scenario", "payload_sha256"}:
            whole[key] = copy.deepcopy(value)
    mutants.append(whole)
    outcome = copy.deepcopy(target)
    outcome["expect_record"] = copy.deepcopy(CASES["happy"][2]["expect_record"])
    mutants.append(outcome)
    rogue = copy.deepcopy(target)
    rogue["record"]["rogue"] = 1
    mutants.append(rogue)
    missing = copy.deepcopy(target)
    missing["record"]["sources"][0].pop("game_id")
    mutants.append(missing)
    wrong = copy.deepcopy(target)
    wrong["record"]["target"]["variant"] = ["standard"]
    mutants.append(wrong)
    for mutant in mutants:
        mutant["payload_sha256"] = _payload_hash(mutant)
        with pytest.raises(AssertionError):
            _strict("happy", mutant)


def test_fixture_oracle_binding_and_twin_substitution_guard(monkeypatch):
    """The resolver closes over the original exact class and full
    dispatch surface. Global rebinding and in-place mutation fail."""
    assert _resolve_provider(lambda: _CAPTURED_ANCHOR) is _CAPTURED_ANCHOR

    second = _load_isolated_oracle_class()
    monkeypatch.setitem(globals(), "ANCHORED_PROVENANCE_TABLE", second)
    with pytest.raises(AssertionError):
        _resolve_provider(lambda: ANCHORED_PROVENANCE_TABLE)
    assert _resolve_provider(lambda: _CAPTURED_ANCHOR) is _CAPTURED_ANCHOR

    def malicious_getattribute(self, name):
        if name == "insert":
            return lambda record: record
        return object.__getattribute__(self, name)

    monkeypatch.setattr(_CAPTURED_ANCHOR, "__getattribute__", malicious_getattribute)
    with pytest.raises(AssertionError):
        _resolve_provider(lambda: _CAPTURED_ANCHOR)
    monkeypatch.undo()
    assert _resolve_provider(lambda: _CAPTURED_ANCHOR) is _CAPTURED_ANCHOR

    for hostile in (
        type("GetattrTwin", (_CAPTURED_ANCHOR,), {"__getattr__": lambda self, name: None}),
        type("NewTwin", (_CAPTURED_ANCHOR,), {"__new__": lambda cls, *a: object.__new__(cls)}),
        type("DescriptorTwin", (_CAPTURED_ANCHOR,), {"insert": property(lambda self: None)}),
    ):
        with pytest.raises(AssertionError):
            _resolve_provider(lambda value=hostile: value)


@pytest.mark.parametrize("case", CASES["happy"], ids=lambda c: c["name"])
def test_happy(case):
    table = _table()
    record = table.insert(copy.deepcopy(case["record"]))
    assert record == case["expect_record"]
    oracle.validate_record(PC, IC, record)


@pytest.mark.parametrize("case", CASES["boundary"], ids=lambda c: c["name"])
def test_boundary(case):
    table = _table()
    if case["kind"] == "insert":
        record = table.insert(copy.deepcopy(case["record"]))
        assert len(record["sources"]) == case["expect_source_count"]
    else:
        returned = [table.insert(copy.deepcopy(r)) for r in case["records"]]
        assert len(table.records()) == case["expect_record_count"]
        if "expect_record" in case:
            assert returned[-1] == case["expect_record"]
        reverse = _table()
        for record in reversed(case["records"]):
            reverse.insert(copy.deepcopy(record))
        assert reverse.serialize() == table.serialize()


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda c: c["name"])
def test_malformed_discriminating(case):
    original = copy.deepcopy(case["record"])
    table = _table()
    with pytest.raises(table.Error) as caught:
        table.insert(case["record"])
    assert caught.value.failure_class == case["expect_failure"]
    assert caught.value.code == oracle.FAILURE_MAPPING[case["expect_failure"]]
    assert case["record"] == original and table.records() == []
    accepted = table.insert(_repair(case))
    oracle.validate_record(PC, IC, accepted)


@pytest.mark.parametrize("case", CASES["rollback"], ids=lambda c: c["name"])
def test_rollback_hostile_containers_and_recovery(case):
    table = _table()
    for record in case["setup"]:
        table.insert(copy.deepcopy(record))
    before, state = table.serialize(), copy.deepcopy(table.by_key)
    with pytest.raises(table.Error) as caught:
        table.insert(copy.deepcopy(case["reject_record"]))
    assert caught.value.failure_class == case["expect_failure"]
    assert table.serialize() == before and table.by_key == state
    for malformed in (None, True, 1, [], "record"):
        with pytest.raises((table.Error, AssertionError)):
            table.insert(malformed)
        assert table.serialize() == before and table.by_key == state
    table.insert(copy.deepcopy(case["then_record"]))
    assert len(table.records()) == case["expect_record_count"]


class _DirectBase(BaseException):
    pass


class _HookedDict(dict):
    def __init__(self, *args, error=None, mutate=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.error = error
        self.mutate = mutate

    def __iter__(self):
        if self.mutate:
            self["injected"] = True
        if self.error is not None:
            raise self.error("hooked iteration")
        return super().__iter__()


class _HookedList(list):
    def __init__(self, *args, error=None, mutate=False):
        super().__init__(*args)
        self.error = error
        self.mutate = mutate

    def __iter__(self):
        if self.mutate:
            self.append({"injected": True})
        if self.error is not None:
            raise self.error("hooked iteration")
        return super().__iter__()


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, GeneratorExit, _DirectBase])
def test_hooked_input_baseexceptions_preserve_table_and_caller(error):
    setup = copy.deepcopy(CASES["happy"][0]["record"])
    base = copy.deepcopy(CASES["happy"][1]["record"])
    attacks = [
        _HookedDict(base, error=error),
        {**base, "target": _HookedDict(base["target"], error=error)},
        {**base, "sources": _HookedList(base["sources"], error=error)},
    ]
    for attack in attacks:
        nested_aliases = []
        if isinstance(attack, dict):
            for key in ("target", "sources"):
                if (
                    key in attack
                    and type(attack[key]) is not dict
                    and type(attack[key]) is not list
                ) or (
                    key in attack
                    and isinstance(attack[key], (dict, list))
                    and type(attack[key]) not in (dict, list)
                ):
                    nested_aliases.append((attack[key], _snapshot_graph(attack[key])))
        table = _table()
        table.insert(setup)
        before = table.serialize()
        caller_before = dict(attack) if isinstance(attack, dict) else copy.copy(attack)
        with pytest.raises(error):
            table.insert(attack)
        assert table.serialize() == before
        assert dict(attack) == caller_before
        for alias, snapshot in nested_aliases:
            assert alias is snapshot[1]
            assert _snapshot_graph(alias)[2] == snapshot[2]
        table.insert(copy.deepcopy(CASES["happy"][1]["record"]))
        assert len(table.records()) == 2


@pytest.mark.parametrize("boundary", ["record", "target", "sources"])
def test_mutating_returning_hooks_are_rejected_without_dispatch(boundary):
    table = _table()
    table.insert(copy.deepcopy(CASES["happy"][0]["record"]))
    base = copy.deepcopy(CASES["happy"][1]["record"])
    if boundary == "record":
        attack = _HookedDict(base, mutate=True)
    elif boundary == "target":
        attack = {**base, "target": _HookedDict(base["target"], mutate=True)}
    else:
        attack = {**base, "sources": _HookedList(base["sources"], mutate=True)}
    before = table.serialize()
    caller_before = copy.deepcopy(dict(attack))
    alias = attack if boundary == "record" else attack[boundary]
    alias_before = _snapshot_graph(alias)
    with pytest.raises((AssertionError, table.Error)):
        table.insert(attack)
    assert table.serialize() == before
    assert dict(attack) == caller_before
    assert alias is alias_before[1]
    assert _snapshot_graph(alias)[2] == alias_before[2]
    table.insert(copy.deepcopy(CASES["happy"][1]["record"]))
    assert len(table.records()) == 2


def test_preexisting_hostile_mapping_is_rejected_without_dispatch_and_recovers():
    table = _table()
    table.insert(copy.deepcopy(CASES["happy"][0]["record"]))
    inert = dict(table.by_key)
    table.by_key = _HookedDict(inert, mutate=True)
    with pytest.raises((AssertionError, table.Error)):
        table.insert(copy.deepcopy(CASES["happy"][1]["record"]))
    assert "injected" not in dict(table.by_key)
    table.by_key = inert
    table.insert(copy.deepcopy(CASES["happy"][1]["record"]))
    assert len(table.records()) == 2


def test_cyclic_and_shared_caller_graph_boundaries():
    table = _table()
    table.insert(copy.deepcopy(CASES["happy"][0]["record"]))
    before = table.serialize()

    self_cycle = {}
    self_cycle["self"] = self_cycle
    mutual_dict = {}
    mutual_list = [mutual_dict]
    mutual_dict["list"] = mutual_list
    for cyclic in (self_cycle, mutual_dict, mutual_list):
        with pytest.raises(AssertionError, match="cyclic fixture payload"):
            table.insert(cyclic)
        assert table.serialize() == before
        if cyclic is self_cycle:
            assert cyclic["self"] is cyclic
        else:
            assert mutual_dict["list"] is mutual_list
            assert mutual_list[0] is mutual_dict

    shared = copy.deepcopy(CASES["happy"][1]["record"])
    alias = shared["target"]
    shared["shadow"] = alias
    with pytest.raises((AssertionError, table.Error)):
        table.insert(shared)
    assert shared["target"] is alias and shared["shadow"] is alias
    assert table.serialize() == before
    table.insert(copy.deepcopy(CASES["happy"][1]["record"]))
    assert len(table.records()) == 2


class _DeepcopyBomb:
    def __init__(self, outer, error):
        self.outer = outer
        self.error = error

    def __deepcopy__(self, memo):
        self.outer["injected"] = "survives"
        raise self.error("deepcopy bomb")

    def __hash__(self):
        return object.__hash__(self)


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, GeneratorExit, _DirectBase])
def test_nested_scalar_and_key_deepcopy_hooks_never_dispatch(error):
    table = _table()
    table.insert(copy.deepcopy(CASES["happy"][0]["record"]))
    before = table.serialize()

    scalar_outer = copy.deepcopy(CASES["happy"][1]["record"])
    scalar_bomb = _DeepcopyBomb(scalar_outer, error)
    scalar_outer["target"]["variant"] = scalar_bomb
    with pytest.raises(AssertionError):
        table.insert(scalar_outer)
    assert "injected" not in scalar_outer
    assert scalar_outer["target"]["variant"] is scalar_bomb
    assert table.serialize() == before

    key_outer = copy.deepcopy(CASES["happy"][1]["record"])
    key_bomb = _DeepcopyBomb(key_outer, error)
    key_outer["target"][key_bomb] = "hostile-key"
    with pytest.raises(AssertionError):
        table.insert(key_outer)
    assert "injected" not in key_outer
    assert next(key for key in key_outer["target"] if key is key_bomb) is key_bomb
    assert table.serialize() == before

    table.insert(copy.deepcopy(CASES["happy"][1]["record"]))
    assert len(table.records()) == 2
