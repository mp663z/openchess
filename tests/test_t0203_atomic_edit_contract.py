"""T0203: store atomic edit contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/
atomic_edit.yaml: one edit request against one content-addressed
base state commits entirely or not at all. Total request,
operation and base validation; structural base check; whole edit
staged on a detached copy; single commit point; fresh
deep-copied result state; derived gs1 target id and ae1 edit id.
Record construction, identity and validation all compose the
REAL linked node machinery (imported, never restated).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import itertools
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
from tools.atomic_edit_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_NDOCS = _node_docs()
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_OP_FIELDS = set(_CC["operation"]["fields"])
_OP_KINDS = set(_CC["operation"]["kinds"])
_ID_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
_EID_RE = re.compile(_CC["identifiers"]["edit_id"]["grammar"])
_DIGEST_RE = re.compile(
    yaml.safe_load((ROOT / "data" / "contracts" /
                    "position_digest.yaml").read_text())
    ["contract"]["digest"]["format"]["regex"])

K1 = "pdv1:" + "1" * 64
K2 = "pdv1:" + "2" * 64


class AtomicEditError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise AtomicEditError(cls, FAILURE_MAPPING[cls])


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
    """The canonical state content digest: sha256 over the
    canonical serialization of the FULL identity->record map.
    Derived, never supplied."""
    parts = []
    for key in sorted(state):
        rec = state[key]
        body = "|".join(f"{field}={rec[field]}"
                        for field in sorted(rec))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256(
        "".join(parts).encode()).hexdigest()


def _validate_record(rec):
    """An exact valid LINKED node record: canonical fields through
    the node machinery (exact built-in-str guards BEFORE sibling
    machinery), exact built-in-str digest in the linked format."""
    if not isinstance(rec, dict):
        _fail("malformed_edit_record")
    if set(rec.keys()) != set(_NDOCS[0]["record"]["fields"]):
        _fail("malformed_edit_record")
    if type(rec.get("variant")) is not str or \
            type(rec.get("snapshot_fen")) is not str or \
            type(rec.get("digest")) is not str:
        _fail("malformed_edit_record")
    try:
        derived = _make_record(*_NDOCS, digest_fen,
                               rec["variant"],
                               rec["snapshot_fen"])
    except NodeError:
        _fail("malformed_edit_record")
    if rec["variant"] != derived["variant"] or \
            rec["snapshot_fen"] != derived["snapshot_fen"]:
        _fail("malformed_edit_record")
    if _DIGEST_RE.fullmatch(rec["digest"]) is None:
        _fail("malformed_edit_record")
    # the digest is DERIVED, never asserted: a well-formed but
    # wrong digest is as malformed as a missing field
    if rec["digest"] != derived["digest"]:
        _fail("malformed_edit_record")


class AtomicEditEngine:
    """The contract's pinned edit: total validation, structural
    base check, whole edit staged on a detached copy, single
    commit point, fresh deep-copied result, derived ids."""

    def apply(self, request, base):
        """ATOMIC: validate the request, the base and EVERY
        operation BEFORE staging; the base is never mutated; a
        rejected edit leaves every input bit-identical."""
        # TOTAL REQUEST VALIDATION: exact field set, exact
        # built-in-str base_id in the pinned grammar, operations
        # a list of exact operation shapes - explicit guards on
        # every field, never a raw escape.
        if not isinstance(request, dict) or \
                set(request.keys()) != {"base_id", "operations"}:
            _fail("malformed_edit_record")
        if type(request["base_id"]) is not str or \
                _ID_RE.fullmatch(request["base_id"]) is None:
            _fail("malformed_edit_record")
        operations = request["operations"]
        if not isinstance(operations, list):
            _fail("malformed_edit_record")
        seen = set()
        for op in operations:
            if not isinstance(op, dict) or set(op.keys()) != \
                    _OP_FIELDS:
                _fail("malformed_edit_record")
            if op["kind"] not in _OP_KINDS or \
                    type(op["kind"]) is not str:
                _fail("malformed_edit_record")
            if type(op["identity"]) is not str:
                _fail("malformed_edit_record")
            if op["identity"] in seen:
                # exactly ONE operation per identity per edit -
                # operation order can never change the outcome
                _fail("malformed_edit_record")
            seen.add(op["identity"])
            if op["kind"] == "delete":
                if op["record"] is not None:
                    _fail("malformed_edit_record")
            else:
                _validate_record(op["record"])
                if _identity(op["record"]) != op["identity"]:
                    _fail("malformed_edit_record")
        # BASE VALIDATION: mapping of exact-str keys to exact
        # valid records whose derived identity equals the key.
        if not isinstance(base, dict):
            _fail("malformed_edit_record")
        for key, rec in base.items():
            if type(key) is not str:
                _fail("malformed_edit_record")
            _validate_record(rec)
            if _identity(rec) != key:
                _fail("malformed_edit_record")
        # STRUCTURAL BASE CHECK: the recomputed base state id must
        # equal the request's base_id - a caller cannot edit a
        # state it mislabels.
        if state_id(base) != request["base_id"]:
            _fail("conflicting_base")
        # STAGED application in canonical identity order: the
        # whole edit lands on a DETACHED copy; the base is never
        # touched.
        staged = copy.deepcopy(base)
        for op in sorted(operations,
                         key=lambda op: op["identity"]):
            if op["kind"] == "delete":
                if op["identity"] not in staged:
                    _fail("unknown_identity")
                del staged[op["identity"]]
            else:
                staged[op["identity"]] = copy.deepcopy(
                    op["record"])
        target_id = state_id(staged)
        canonical_ops = ";".join(
            f"{op['kind']}:{op['identity']}"
            for op in sorted(operations,
                             key=lambda op: op["identity"]))
        edit_id = "ae1:" + hashlib.sha256(
            f"{request['base_id']}\n{target_id}\n"
            f"{canonical_ops}".encode()).hexdigest()
        return {"edit_id": edit_id,
                "base_id": request["base_id"],
                "target_id": target_id,
                "state": staged}


# -- behavior battery ---------------------------------------------------------

REC_A = _node(STARTPOS)
REC_B = _node(AFTER_E4)
REC_C = _node(KINGS)
REC_D = _node(LEGAL_EP)
ID_A = _identity(REC_A)
ID_B = _identity(REC_B)
ID_C = _identity(REC_C)
ID_D = _identity(REC_D)
RECEIPT_KEYS = {"edit_id", "base_id", "target_id", "state"}


def _put(rec):
    return {"kind": "put", "identity": _identity(rec),
            "record": rec}


def _delete(identity):
    return {"kind": "delete", "identity": identity,
            "record": None}


def _request(base, operations):
    return {"base_id": state_id(base),
            "operations": operations}


def _apply(request, base):
    return AtomicEditEngine().apply(request, base)


def test_lint_clean():
    lint()


def test_put_inserts_into_nonempty_base():
    base = _state(REC_A, REC_B)
    out = _apply(_request(base, [_put(REC_C)]), base)
    assert set(out) == RECEIPT_KEYS
    assert set(out["state"]) == {ID_A, ID_B, ID_C}
    assert out["base_id"] == state_id(base)
    assert _EID_RE.fullmatch(out["edit_id"])
    assert _ID_RE.fullmatch(out["target_id"])
    # target verification: the receipt target id is exactly the
    # recomputed result-state id
    assert out["target_id"] == state_id(out["state"])


def test_put_replaces_present_identity():
    base = _state(REC_A, REC_B)
    out = _apply(_request(base, [_put(REC_A)]), base)
    assert out["state"] == base
    assert out["target_id"] == out["base_id"]


def test_delete_removes_present_identity():
    base = _state(REC_A, REC_B)
    out = _apply(_request(base, [_delete(ID_A)]), base)
    assert set(out["state"]) == {ID_B}
    assert out["state"][ID_B] == REC_B


def test_mixed_multi_operation_edit():
    base = _state(REC_A, REC_B)
    out = _apply(_request(base, [_put(REC_C), _delete(ID_A),
                                 _put(REC_D)]), base)
    assert set(out["state"]) == {ID_B, ID_C, ID_D}


def test_empty_base_boundary():
    out = _apply(_request({}, [_put(REC_A)]), {})
    assert set(out["state"]) == {ID_A}
    with pytest.raises(AtomicEditError) as exc:
        _apply(_request({}, [_delete(ID_A)]), {})
    assert exc.value.failure_class == "unknown_identity"


def test_determinism_identical_receipts():
    base = _state(REC_A, REC_B)
    req = _request(base, [_put(REC_C), _delete(ID_A)])
    assert _apply(req, base) == _apply(req, base)


def test_operation_order_insignificant():
    base = _state(REC_A)
    ops = [_put(REC_B), _put(REC_C), _delete(ID_A)]
    receipts = set()
    for perm in itertools.permutations(ops):
        out = _apply(_request(base, list(perm)), base)
        receipts.add((out["edit_id"], out["target_id"]))
    assert len(receipts) == 1


def _freshness_witness(engine):
    base = _state(REC_A, REC_B)
    out = engine.apply(_request(base, [_put(REC_C)]), base)
    for key, rec in out["state"].items():
        if key in base:
            assert rec is not base[key], key
        assert rec is not REC_C


def test_result_state_fresh_never_aliases():
    _freshness_witness(AtomicEditEngine())


def _preservation_witness(engine, request, base):
    pristine_base = copy.deepcopy(base)
    pristine_req = copy.deepcopy(request)
    with contextlib.suppress(AtomicEditError):
        engine.apply(request, base)
    assert base == pristine_base
    assert request == pristine_req


def test_inputs_never_mutated_success_and_rejection():
    engine = AtomicEditEngine()
    base = _state(REC_A, REC_B)
    # success
    _preservation_witness(engine, _request(base, [_put(REC_C)]),
                          base)
    # every failure class
    _preservation_witness(engine, {"base_id": state_id({}),
                                   "operations": []}, base)
    _preservation_witness(engine,
                          _request(base, [_delete(ID_C)]), base)
    _preservation_witness(engine, {"base_id": 7,
                                   "operations": []}, base)


def test_conflicting_base_rejected():
    base = _state(REC_A, REC_B)
    with pytest.raises(AtomicEditError) as exc:
        _apply({"base_id": state_id({}), "operations": []}, base)
    assert exc.value.failure_class == "conflicting_base"
    assert exc.value.code == FAILURE_MAPPING["conflicting_base"]
    assert exc.value.code in ERROR_ENUM


def test_unknown_identity_delete_rejected():
    base = _state(REC_A, REC_B)
    with pytest.raises(AtomicEditError) as exc:
        _apply(_request(base, [_delete(ID_C)]), base)
    assert exc.value.failure_class == "unknown_identity"
    assert exc.value.code in ERROR_ENUM


def _atomic_witness(engine):
    """A multi-operation edit whose LAST staged operation fails
    must leave the base bit-identical - no partial application
    is ever observable."""
    base = _state(REC_A, REC_B)
    pristine = copy.deepcopy(base)
    req = _request(base, [_put(REC_C), _delete(ID_D)])
    with pytest.raises(AtomicEditError) as exc:
        engine.apply(req, base)
    assert exc.value.failure_class == "unknown_identity"
    assert base == pristine


def test_atomic_last_operation_fails():
    _atomic_witness(AtomicEditEngine())


def test_rollback_rejected_then_valid_edit():
    engine = AtomicEditEngine()
    base = _state(REC_A, REC_B)
    pristine = copy.deepcopy(base)
    with pytest.raises(AtomicEditError):
        engine.apply({"base_id": state_id({}), "operations": []},
                     base)
    assert base == pristine
    req = _request(base, [_put(REC_C)])
    assert engine.apply(req, base) == _apply(req, base)


def test_closed_failure_enumeration():
    assert set(FAILURE_MAPPING) == {"malformed_edit_record",
                                    "conflicting_base",
                                    "unknown_identity"}
    assert set(FAILURE_MAPPING.values()) <= set(ERROR_ENUM)


# -- hostile totality ---------------------------------------------------------


def _hostile_requests():
    """(name, mutate, expected failure class) over a VALID
    request+base pair - every hostile variant fails closed,
    typed, never raw."""
    bad_id = state_id({})
    out = []

    def add(name, mutate, cls="malformed_edit_record"):
        out.append((name, mutate, cls))

    add("request-not-mapping", lambda r, b: [1, 2])
    add("request-missing-base-id",
        lambda r, b: {"operations": r["operations"]})
    add("request-extra-field",
        lambda r, b: dict(r, wat=1))
    add("base-id-not-str-int", lambda r, b: dict(r, base_id=7))
    add("base-id-not-str-none", lambda r, b: dict(r, base_id=None))
    add("base-id-not-str-bool",
        lambda r, b: dict(r, base_id=True))
    add("base-id-bad-grammar-empty", lambda r, b: dict(r,
                                                       base_id=""))
    add("base-id-bad-grammar-prefix",
        lambda r, b: dict(r, base_id="xx1:" + "0" * 64))
    add("base-id-bad-grammar-short",
        lambda r, b: dict(r, base_id="gs1:" + "0" * 63))
    add("base-id-conflicting", lambda r, b: dict(r, base_id=bad_id),
        "conflicting_base")
    add("operations-not-list-dict", lambda r, b: dict(r,
                                                      operations={}))
    add("operations-not-list-none",
        lambda r, b: dict(r, operations=None))
    add("op-not-mapping",
        lambda r, b: dict(r, operations=[["put"]]))
    add("op-missing-record",
        lambda r, b: dict(r, operations=[{"kind": "put",
                                          "identity": ID_C}]))
    add("op-extra-field",
        lambda r, b: dict(r, operations=[dict(_put(REC_C),
                                              wat=1)]))
    add("op-kind-unknown",
        lambda r, b: dict(r, operations=[{"kind": "upsert",
                                          "identity": ID_C,
                                          "record": REC_C}]))
    add("op-kind-not-str",
        lambda r, b: dict(r, operations=[{"kind": 1,
                                          "identity": ID_C,
                                          "record": REC_C}]))
    add("op-identity-not-str",
        lambda r, b: dict(r, operations=[{"kind": "put",
                                          "identity": 5,
                                          "record": REC_C}]))
    add("op-duplicate-identity",
        lambda r, b: dict(r, operations=[_put(REC_C),
                                         _put(REC_C)]))
    add("delete-record-not-null",
        lambda r, b: dict(r, operations=[{"kind": "delete",
                                          "identity": ID_A,
                                          "record": REC_A}]))
    add("put-identity-mismatch",
        lambda r, b: dict(r, operations=[{"kind": "put",
                                          "identity": ID_D,
                                          "record": REC_C}]))
    add("put-record-bad-digest",
        lambda r, b: dict(r, operations=[
            _put(_node(STARTPOS, digest_override=K1))]))
    add("put-record-not-mapping",
        lambda r, b: dict(r, operations=[{"kind": "put",
                                          "identity": ID_C,
                                          "record": "x"}]))
    add("delete-unknown-identity",
        lambda r, b: dict(r, operations=[_delete(ID_C)]),
        "unknown_identity")
    return out


@pytest.mark.parametrize("name,mutate,cls", _hostile_requests(),
                         ids=[n for n, _m, _c in
                              _hostile_requests()])
def test_total_over_hostile_requests(name, mutate, cls):
    base = _state(REC_A, REC_B)
    req = mutate(_request(base, [_put(REC_C)]), base)
    pristine = copy.deepcopy(base)
    with pytest.raises(AtomicEditError) as exc:
        _apply(req, base)
    assert exc.value.failure_class == cls, name
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert exc.value.code in ERROR_ENUM
    assert base == pristine


def _hostile_bases():
    out = []

    def add(name, base):
        out.append((name, base))

    add("base-not-mapping", [1, 2])
    add("base-key-not-str", {1: REC_A})
    add("base-key-unequal-derived-identity", {ID_D: REC_A})
    add("base-record-not-mapping", {ID_A: "x"})
    add("base-record-bad-digest",
        {ID_A: _node(STARTPOS, digest_override=K2)})
    add("base-record-extra-field",
        {ID_A: dict(REC_A, wat=1)})
    add("base-record-non-str-field",
        {ID_A: dict(REC_A, variant=7)})
    return out


@pytest.mark.parametrize("name,base", _hostile_bases(),
                         ids=[n for n, _b in _hostile_bases()])
def test_total_over_hostile_bases(name, base):
    with pytest.raises(AtomicEditError) as exc:
        _apply(_request(_state(REC_A), [_put(REC_C)]), base)
    assert exc.value.failure_class == "malformed_edit_record", \
        name
    assert exc.value.code in ERROR_ENUM


# -- discriminating engine mutants --------------------------------------------


class _NonAtomicEngine:
    """Mutant: stages ON THE BASE ITSELF - a rejected edit leaves
    a partially mutated base observable."""

    def apply(self, request, base):
        for op in request["operations"]:
            if op["kind"] == "delete":
                if op["identity"] not in base:
                    _fail("unknown_identity")
                del base[op["identity"]]
            else:
                base[op["identity"]] = copy.deepcopy(
                    op["record"])
        return {"edit_id": "ae1:" + "0" * 64,
                "base_id": request["base_id"],
                "target_id": state_id(base), "state": base}


class _AliasingEngine(AtomicEditEngine):
    """Mutant: the result state ALIASES base records instead of
    deep-copying them."""

    def apply(self, request, base):
        out = super().apply(request, base)
        for key in out["state"]:
            if key in base:
                out["state"][key] = base[key]
        return out


class _OrderDependentEngine(AtomicEditEngine):
    """Mutant: the receipt derives from REQUEST operation order,
    not canonical identity order."""

    def apply(self, request, base):
        out = dict(super().apply(request, base))
        canonical = ";".join(
            f"{op['kind']}:{op['identity']}"
            for op in request["operations"])
        out["edit_id"] = "ae1:" + hashlib.sha256(
            f"{request['base_id']}\n{out['target_id']}\n"
            f"{canonical}".encode()).hexdigest()
        return out


class _RequestMutatingEngine(AtomicEditEngine):
    """Mutant: sorts the caller's operations list IN PLACE."""

    def apply(self, request, base):
        request["operations"].sort(key=lambda op: op["identity"])
        return super().apply(request, base)


def test_mutant_non_atomic_caught():
    with pytest.raises(AssertionError):
        _atomic_witness(_NonAtomicEngine())


def test_mutant_aliasing_caught():
    with pytest.raises(AssertionError):
        _freshness_witness(_AliasingEngine())


def test_mutant_order_dependent_caught():
    base = _state(REC_A)
    engine = _OrderDependentEngine()
    fwd = engine.apply(_request(base, [_put(REC_B),
                                       _delete(ID_A)]), base)
    rev = engine.apply(_request(base, [_delete(ID_A),
                                       _put(REC_B)]), base)
    assert fwd["edit_id"] != rev["edit_id"], (
        "the mutant MUST be order-dependent - the real engine's "
        "permutation witness catches exactly this")


def test_mutant_request_mutating_caught():
    base = _state(REC_A, REC_B)
    with pytest.raises(AssertionError):
        _preservation_witness(
            _RequestMutatingEngine(),
            _request(base, [_put(REC_D), _put(REC_C)]), base)


# -- contract lint closure ----------------------------------------------------


def _contract_mutants():
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
    add("role kind drift", c + ["role", "kind"],
        "last-writer-wins-edit")
    add("role serves drift", c + ["role", "serves"],
        "eventual-consistency-writes")
    add("not_scope drift", c + ["role", "not_scope"],
        "base-validation-owned-elsewhere")
    add("record field dropped", c + ["record", "fields"],
        ["edit_id", "base_id", "state"])
    add("record extra field", c + ["record", "fields"],
        ["edit_id", "base_id", "target_id", "state", "winner"])
    add("record exactness dropped", c + ["record", "exact"],
        False)
    add("operation kinds drift", c + ["operation", "kinds"],
        ["put", "delete", "merge"])
    add("uniqueness drift", c + ["operation", "uniqueness"],
        "last-operation-wins-per-identity")
    add("order drift", c + ["operation", "order"],
        "significant-request-order-decides")
    add("state id grammar drift",
        c + ["identifiers", "state_id", "grammar"],
        "^gs1:[0-9a-f]{16}$")
    add("edit id grammar drift",
        c + ["identifiers", "edit_id", "grammar"],
        "^ae1:[0-9a-f]{16}$")
    add("state id source drift",
        c + ["identifiers", "state_id", "source"],
        "caller-supplied")
    add("base check dropped", c + ["semantics", "base_check"],
        "caller-labels-trusted")
    add("staging drift", c + ["semantics", "staging"],
        "operations-applied-in-place")
    add("commit drift", c + ["semantics", "commit"],
        "per-operation-commit-points")
    add("result state drift", c + ["semantics", "result_state"],
        "aliases-base-records-allowed")
    add("failure class dropped", c + ["failures", "classes"],
        ["malformed_edit_record", "conflicting_base"])
    add("failure class added", c + ["failures", "classes"],
        ["malformed_edit_record", "conflicting_base",
         "unknown_identity", "retry_later"])
    add("failure mapping drift",
        c + ["failures", "mapping", "unknown_identity"],
        "internal")
    add("failures closed dropped", c + ["failures", "closed"],
        False)
    add("error enum drift", c + ["errors", "closed_enum"],
        ["malformed_request", "conflicting_base"])
    add("retryable drift",
        c + ["errors", "shape", "retryable_true_only_for"],
        ["internal", "conflicting_base"])
    add("total property drift", c + ["properties", "total"],
        "requests-trusted")
    add("atomic property drift", c + ["properties", "atomic"],
        "best-effort-partial-commits")
    add("base path drift", c + ["versioning", "base_path"],
        "/store/atomic-edit/v2")
    add("link drift",
        c + ["links", "transposition_node_contract"],
        "data/contracts/variant.yaml")
    return out


def test_contract_mutations_fail_lint(tmp_path):
    mutants = _contract_mutants()
    assert len(mutants) >= 20
    for _name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)


def test_contract_mutants_never_silent_subset():
    """Every mutant actually CHANGES the contract document - a
    no-op mutant can never masquerade as coverage."""
    base_doc = yaml.safe_load(CONTRACT.read_text())
    for name, m in _contract_mutants():
        assert m != base_doc, name


def test_empty_operations_noop_edit():
    base = _state(REC_A, REC_B)
    out = _apply(_request(base, []), base)
    assert out["state"] == base
    assert out["target_id"] == out["base_id"]


class _NoBaseCheckEngine(AtomicEditEngine):
    """Mutant: the structural base check is skipped - a caller
    can edit a state it mislabels."""

    def apply(self, request, base):
        req = dict(request, base_id=state_id(base))
        out = dict(super().apply(req, base))
        out["base_id"] = request["base_id"]
        return out


class _DeleteAbsentAllowedEngine(AtomicEditEngine):
    """Mutant: deleting an absent identity silently no-ops."""

    def apply(self, request, base):
        ops = [op for op in request["operations"]
               if not (op["kind"] == "delete"
                       and op["identity"] not in base)]
        return super().apply(dict(request, operations=ops), base)


class _DupIdentityAllowedEngine(AtomicEditEngine):
    """Mutant: a repeated identity is allowed, last operation
    wins - order now decides the outcome."""

    def apply(self, request, base):
        seen = {}
        for op in request["operations"]:
            seen[op["identity"]] = op
        return super().apply(
            dict(request, operations=list(seen.values())), base)


def _conflicting_base_witness(engine):
    base = _state(REC_A, REC_B)
    try:
        engine.apply({"base_id": state_id({}), "operations": []},
                     base)
    except AtomicEditError as exc:
        assert exc.failure_class == "conflicting_base"
        return
    raise AssertionError("mislabeled base accepted")


def _unknown_identity_witness(engine):
    base = _state(REC_A, REC_B)
    try:
        engine.apply(_request(base, [_delete(ID_C)]), base)
    except AtomicEditError as exc:
        assert exc.failure_class == "unknown_identity"
        return
    raise AssertionError("absent-identity delete accepted")


def _dup_identity_witness(engine):
    base = _state(REC_A, REC_B)
    try:
        engine.apply(_request(base, [_put(REC_C), _put(REC_C)]),
                     base)
    except AtomicEditError as exc:
        assert exc.failure_class == "malformed_edit_record"
        return
    raise AssertionError("duplicate identity accepted")


def test_mutant_no_base_check_caught():
    with pytest.raises(AssertionError):
        _conflicting_base_witness(_NoBaseCheckEngine())


def test_mutant_delete_absent_allowed_caught():
    with pytest.raises(AssertionError):
        _unknown_identity_witness(_DeleteAbsentAllowedEngine())


def test_mutant_dup_identity_allowed_caught():
    with pytest.raises(AssertionError):
        _dup_identity_witness(_DupIdentityAllowedEngine())
