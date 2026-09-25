"""T0401: privacy delete contract behavior battery.

Reference engine derived from data/contracts/delete.yaml. A collection is
registered live. Deleting it needs a user decision bound to the
collection and expected revision (deletions are never automated). The
tombstone is written before any store is erased; then every declared
store is erased in order. A store that fails to erase stays pending; the
collection stays deleted and is never readable or registrable again. A
repeat delete needs no decision and re-erases only the pending stores. A
delete is never refused for revision exhaustion. Every rejection is typed
and leaves state, every store and inputs unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.delete_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_CLASSES,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

REPO = CONTRACT.parents[2]  # data/contracts/<name>.yaml; valid for battery copies too
DOC = yaml.safe_load(CONTRACT.read_text())
_CC = DOC["contract"]
_FIELDS = _CC["record"]["fields"]
_COL_RE = re.compile(_CC["identifiers"]["collection_id"]["grammar"])
_DEL_RE = re.compile(_CC["identifiers"]["approval"]["grammar"])
_STORES = tuple(_CC["identifiers"]["stores"]["names"])
_MAX_REV = 2**63 - 1
_ACCESS_KEYS = {"collection_id"}
_DELETE_KEYS = {"collection_id", "expected_revision", "decision"}


class DeleteError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(cls):
    raise DeleteError(cls)


def decision_for(collection_id, expected_revision):
    """The pinned delete-decision binding: collection, revision, delete."""
    raw = f"{collection_id}|{expected_revision}|delete".encode()
    return "del1:" + hashlib.sha256(raw).hexdigest()


def _exact_str(v):
    return type(v) is str


def _exact_rev(v):
    return type(v) is int and 0 <= v <= _MAX_REV


def _request(req, keys):
    if type(req) is not dict:
        _fail("malformed_delete_request")
    for k in list(req.keys()):
        if type(k) is not str:
            _fail("malformed_delete_request")
    if set(req) != keys:
        _fail("malformed_delete_request")
    cid = req["collection_id"]
    if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:
        _fail("malformed_delete_request")
    return cid


def _decision_ok(req, cid):
    token = req["decision"]
    if token is None:
        return False
    if not _exact_str(token) or _DEL_RE.fullmatch(token) is None:
        _fail("malformed_delete_request")
    return token == decision_for(cid, req["expected_revision"])


class Deleter:
    def __init__(self, stores):
        self._stores = stores
        self._state = {}

    def register(self, collection_id):
        if not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None:
            _fail("malformed_delete_request")
        st = self._state.get(collection_id)
        if st is not None and st[0] == "deleted":
            _fail("collection_deleted")
        self._state.setdefault(collection_id, ("live", 0, ()))
        return self.record(collection_id)

    def record(self, cid):
        state, rev, pending = self._state[cid]
        return dict(zip(_FIELDS, (cid, state, rev, list(pending)), strict=True))

    def access(self, req):
        cid = _request(req, _ACCESS_KEYS)
        st = self._state.get(cid)
        if st is None:
            _fail("unknown_collection")
        if st[0] != "live":
            _fail("collection_deleted")
        return "allow"

    def delete(self, req):
        cid = _request(req, _DELETE_KEYS)
        if not _exact_rev(req["expected_revision"]):
            _fail("malformed_delete_request")
        approved = _decision_ok(req, cid)
        if cid not in self._state:
            _fail("unknown_collection")
        state, rev, pending = self._state[cid]
        if req["expected_revision"] != rev:
            _fail("stale_revision")
        if state == "deleted":
            targets = pending
        else:
            if not approved:
                _fail("decision_missing")
            targets = _STORES
            rev = rev if rev == _MAX_REV else rev + 1
        self._state[cid] = ("deleted", rev, tuple(targets))  # tombstone first
        left = []
        for name in targets:
            try:
                self._stores[name].erase(cid)
            except Exception:  # noqa: BLE001 - a failed erase stays pending
                left.append(name)
        self._state[cid] = ("deleted", rev, tuple(left))
        return self.record(cid)


erase_log = []


class Store:
    """A local store fake: holds collection ids, logs every erase call."""

    def __init__(self, name, fail=0, exc=OSError, probe=None):
        self.name = name
        self.data = set()
        self.fail = fail
        self.exc = exc
        self.probe = probe

    def erase(self, cid):
        erase_log.append((self.name, cid))
        if self.probe is not None:
            self.probe(self.name, cid)
        if self.fail:
            self.fail -= 1
            raise self.exc("erase failed")
        self.data.discard(cid)


A = "col1:" + "a" * 64
B = "col1:" + "b" * 64
C = "col1:" + "c" * 64


def _world(fails=None, exc=OSError, probe=None):
    fails = fails or {}
    stores = {n: Store(n, fails.get(n, 0), exc, probe) for n in _STORES}
    for s in stores.values():
        s.data |= {A, B}
    d = Deleter(stores)
    d.register(A)
    d.register(B)
    erase_log.clear()
    return d, stores


def _del(d, rev, cid=A, decision=None, bound=True):
    if bound and decision is None:
        decision = decision_for(cid, rev)
    return d.delete({"collection_id": cid, "expected_revision": rev, "decision": decision})


def _ask(d, cid=A):
    return d.access({"collection_id": cid})


def _raises(cls, fn, *args, **kw):
    with pytest.raises(DeleteError) as ei:
        fn(*args, **kw)
    assert ei.value.failure_class == cls
    assert ei.value.code == FAILURE_MAPPING[cls]
    assert ei.value.__cause__ is None
    return ei.value


def _data(stores):
    return {n: frozenset(s.data) for n, s in stores.items()}


def _at(d, rev, state="live", pending=(), cid=A):
    d._state[cid] = (state, rev, tuple(pending))


# ---- contract / lint ------------------------------------------------------


def test_contract_lints_clean():
    lint()


def test_error_model_is_closed_and_consistent():
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASSES)
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert _CC["errors"]["shape"]["retryable_true_only_for"] == ["internal"]
    assert "revision_exhausted" not in ERROR_ENUM  # a delete is never exhausted


def test_linked_documents_exist_and_agree():
    flag = yaml.safe_load((REPO / _CC["links"]["sensitive_flag_contract"]).read_text())
    assert flag["contract"]["identifiers"]["collection_id"]["grammar"] == _COL_RE.pattern
    cp = (REPO / _CC["links"]["control_plane_contract"]).read_text()
    assert "identity.delete_account" in cp
    adr = (REPO / _CC["links"]["architecture_decision"]).read_text()
    front = yaml.safe_load(adr.split("---")[1])
    assert front["data_flow"]["desktop"]["content"] == "store-process"
    assert front["data_flow"]["desktop"]["ciphertext"] == "store"
    report = (REPO / _CC["links"]["product_report"]).read_text()
    assert "| Never automated | New-line additions, deletions," in report


def _sem(key, value):
    return lambda c: c["semantics"].__setitem__(key, value)


LINT_MUTATIONS = {
    "default_deleted": _sem("default", "registering-reads-deleted"),
    "decision_dropped": _sem("decision", "live-to-deleted-applies-immediately"),
    "order_erase_first": _sem("order", "stores-are-erased-then-the-tombstone-is-written"),
    "erase_failure_revives": _sem("erase_failure", "a-failed-erase-restores-live"),
    "retry_needs_decision": _sem("retry", "a-repeat-delete-needs-a-fresh-decision"),
    "access_pending_only": _sem("access", "refused-only-while-pending"),
    "tombstone_reuse": _sem("tombstone", "a-deleted-id-may-be-registered-again"),
    "reregister_resets": _sem("reregister", "registering-resets-to-live"),
    "concurrency_dropped": _sem("concurrency", "last-writer-wins"),
    "saturation_refused": _sem("saturation", "a-delete-at-max-revision-fails-typed"),
    "rollback_undelete": _sem("rollback", "an-undelete-restores-the-collection"),
    "reading_dropped": lambda c: c["semantics"]["readings"].pop("irreversible"),
    "reading_changed": lambda c: c["semantics"]["readings"].__setitem__(
        "never_exhausted", "exhaustion-refuses"
    ),
    "store_dropped": lambda c: c["identifiers"]["stores"]["names"].remove("ciphertext"),
    "store_order": lambda c: c["identifiers"]["stores"].__setitem__(
        "names", ["index", "content", "analysis", "training", "ciphertext"]
    ),
    "state_grammar": lambda c: c["identifiers"]["state"].__setitem__(
        "grammar", "exact-built-in-str-live-deleted-or-restored"
    ),
    "pending_grammar": lambda c: c["identifiers"]["pending"].__setitem__("grammar", "any-set"),
    "decision_source_widened": lambda c: c["identifiers"]["approval"].__setitem__(
        "source", "any-caller"
    ),
    "decision_binding_loosened": lambda c: c["identifiers"]["approval"].__setitem__(
        "binding", "sha256-over-collection-id"
    ),
    "preimage_separator": lambda c: c["identifiers"]["approval"]["preimage"].__setitem__(
        "separator", ":"
    ),
    "preimage_trailing": lambda c: c["identifiers"]["approval"]["preimage"].__setitem__(
        "trailing_separator", True
    ),
    "preimage_trailing_int": lambda c: c["identifiers"]["approval"]["preimage"].__setitem__(
        "trailing_separator", 0
    ),
    "preimage_order": lambda c: c["identifiers"]["approval"]["preimage"].__setitem__(
        "fields", ["expected_revision", "collection_id", "target"]
    ),
    "preimage_encoding": lambda c: c["identifiers"]["approval"]["preimage"].__setitem__(
        "encoding", "utf-16"
    ),
    "preimage_target": lambda c: c["identifiers"]["approval"]["preimage"].__setitem__(
        "target", "erase"
    ),
    "preimage_example": lambda c: c["identifiers"]["approval"]["preimage"].__setitem__(
        "example", "col1:<64-lowercase-hex>:0:delete"
    ),
    "digest_uppercase": lambda c: c["identifiers"]["approval"].__setitem__(
        "digest", "sha256-of-the-preimage-bytes-as-64-uppercase-hex-after-the-del1-prefix"
    ),
    "record_field_dropped": lambda c: c["record"]["fields"].remove("pending"),
    "record_exact_int": lambda c: c["record"].__setitem__("exact", 1),
    "role_not_scope_changed": lambda c: c["role"].__setitem__("not_scope", "none"),
    "extra_section": lambda c: c.__setitem__("notes", {"x": 1}),
    "error_added": lambda c: c["errors"]["closed_enum"].append("revision_exhausted"),
    "mapping_swapped": lambda c: c["failures"]["mapping"].__setitem__(
        "collection_deleted", "unknown_collection"
    ),
    "open_failures": lambda c: c["failures"].__setitem__("closed", False),
    "retryable_widened": lambda c: c["errors"]["shape"].__setitem__(
        "retryable_true_only_for", ["internal", "conflict"]
    ),
    "failure_class_dropped": lambda c: c["failures"]["classes"].remove("collection_deleted"),
    "trigger_reworded": lambda c: c["failures"]["triggers"].__setitem__(
        "decision_missing", "delete-without-approval"
    ),
    "failures_extra_key": lambda c: c["failures"].__setitem__("notes", "x"),
    "property_changed": lambda c: c["properties"].__setitem__("fail_closed", "best-effort"),
    "base_path_changed": lambda c: c["versioning"].__setitem__("base_path", "/privacy/delete/v2"),
    "link_changed": lambda c: c["links"].__setitem__(
        "control_plane_contract", "data/contracts/control_plane.yaml"
    ),
    "revision_unbounded": lambda c: c["identifiers"]["revision"].__setitem__(
        "grammar", "exact-built-in-int-zero-or-greater-never-bool"
    ),
    "schema_version_bool": lambda c: None,  # envelope mutation, see below
}


@pytest.mark.parametrize("name", sorted(LINT_MUTATIONS))
def test_lint_rejects_mutation(name, tmp_path):
    doc = copy.deepcopy(DOC)
    LINT_MUTATIONS[name](doc["contract"])
    if name == "schema_version_bool":
        doc["schema_version"] = True
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(p)


# ---- happy / default ------------------------------------------------------


def test_register_defaults_live():
    d = Deleter({})
    assert d.register(C) == {"collection_id": C, "state": "live", "revision": 0, "pending": []}


def test_reregister_live_is_a_noop():
    d, _ = _world()
    _at(d, 5)
    assert d.register(A)["revision"] == 5 and d.register(A)["state"] == "live"


@pytest.mark.parametrize("cid", [A, B])
def test_live_collection_is_allowed(cid):
    d, _ = _world()
    assert _ask(d, cid) == "allow"


def test_unregistered_collection_is_unknown_on_access_and_delete():
    d, stores = _world()
    _raises("unknown_collection", _ask, d, C)
    _raises("unknown_collection", _del, d, 0, C)
    assert erase_log == [] and C not in d._state


def test_delete_with_a_bound_decision_erases_every_store_in_order():
    d, stores = _world()
    rec = _del(d, 0)
    assert rec == {"collection_id": A, "state": "deleted", "revision": 1, "pending": []}
    assert erase_log == [(n, A) for n in _STORES]
    assert all(A not in s.data and B in s.data for s in stores.values())
    _raises("collection_deleted", _ask, d, A)
    assert _ask(d, B) == "allow"


def test_store_order_is_the_declared_order():
    assert _STORES == ("content", "index", "analysis", "training", "ciphertext")


def test_tombstone_is_written_before_any_store_is_erased():
    seen = []
    holder = {}

    def probe(name, cid):
        try:
            holder["d"].access({"collection_id": cid})
            seen.append((name, "allow"))
        except DeleteError as e:
            seen.append((name, e.failure_class))

    d, _ = _world(probe=probe)
    holder["d"] = d
    _del(d, 0)
    assert seen == [(n, "collection_deleted") for n in _STORES]


# ---- decision: deletions are never automated -------------------------------


@pytest.mark.parametrize("decision", [None, decision_for(B, 0), decision_for(A, 1)])
def test_delete_without_a_bound_decision_is_refused(decision):
    d, stores = _world()
    before, data = d.record(A), _data(stores)
    _raises("decision_missing", _del, d, 0, A, decision, False)
    assert d.record(A) == before and _data(stores) == data and erase_log == []
    assert _ask(d) == "allow"


def test_a_decision_is_bound_to_its_collection_both_ways():
    d, _ = _world()
    _raises("decision_missing", _del, d, 0, B, decision_for(A, 0))
    _raises("decision_missing", _del, d, 0, A, decision_for(B, 0))


_PRE = _CC["identifiers"]["approval"]["preimage"]
_KAT_HEX = "c2886e73b0f7c175c082fd53a0a68b2ddb7f9f423f4921f8c0e30e38f1f4a3cb"


def _spec_bytes(cid, rev):
    """Build the preimage only from the contract text."""
    parts = {"collection_id": cid, "expected_revision": str(rev), "target": _PRE["target"]}
    body = _PRE["separator"].join(parts[f] for f in _PRE["fields"])
    if _PRE["trailing_separator"]:
        body += _PRE["separator"]
    return body.encode(_PRE["encoding"])


def test_decision_preimage_known_answer():
    raw = ("col1:" + "a" * 64 + "|0|delete").encode("ascii")
    assert _spec_bytes(A, 0) == raw
    assert hashlib.sha256(raw).hexdigest() == _KAT_HEX
    assert decision_for(A, 0) == "del1:" + _KAT_HEX
    assert _PRE["example"] == "col1:<64-lowercase-hex>|0|delete"


@pytest.mark.parametrize("rev", [0, 1, 9, 10, 100, _MAX_REV - 1])
def test_decision_matches_the_contract_preimage(rev):
    want = "del1:" + hashlib.sha256(_spec_bytes(A, rev)).hexdigest()
    assert decision_for(A, rev) == want
    d, _ = _world()
    _at(d, rev)
    assert _del(d, rev, A, want)["state"] == "deleted"


def _variant(tag, cid, rev):
    return {
        "colon_sep": f"{cid}:{rev}:delete",
        "no_sep": f"{cid}{rev}delete",
        "trailing_sep": f"{cid}|{rev}|delete|",
        "leading_sep": f"|{cid}|{rev}|delete",
        "order_swapped": f"{rev}|{cid}|delete",
        "target_first": f"delete|{cid}|{rev}",
        "target_erase": f"{cid}|{rev}|erase",
        "rev_padded": f"{cid}|{rev:03d}|delete",
        "rev_signed": f"{cid}|+{rev}|delete",
        "rev_hex": f"{cid}|{rev:#x}|delete",
        "space_sep": f"{cid} | {rev} | delete",
    }[tag]


@pytest.mark.parametrize(
    "tag",
    [
        "colon_sep",
        "no_sep",
        "trailing_sep",
        "leading_sep",
        "order_swapped",
        "target_first",
        "target_erase",
        "rev_padded",
        "rev_signed",
        "rev_hex",
        "space_sep",
    ],
)
@pytest.mark.parametrize("rev", [0, 7])
def test_decision_from_another_preimage_is_refused(tag, rev):
    d, stores = _world()
    _at(d, rev)
    wrong = "del1:" + hashlib.sha256(_variant(tag, A, rev).encode()).hexdigest()
    assert wrong != decision_for(A, rev)
    before = d.record(A)
    _raises("decision_missing", _del, d, rev, A, wrong)
    assert d.record(A) == before and erase_log == []


def test_decision_utf16_preimage_is_refused():
    d, _ = _world()
    wrong = "del1:" + hashlib.sha256(f"{A}|0|delete".encode("utf-16")).hexdigest()
    _raises("decision_missing", _del, d, 0, A, wrong)


# ---- erase failure and retry -------------------------------------------------


@pytest.mark.parametrize("bad", _STORES)
def test_a_failed_erase_stays_pending_and_the_collection_stays_deleted(bad):
    d, stores = _world({bad: 1})
    rec = _del(d, 0)
    assert rec["state"] == "deleted" and rec["pending"] == [bad] and rec["revision"] == 1
    assert erase_log == [(n, A) for n in _STORES]  # later stores are still erased
    assert A in stores[bad].data
    assert all(A not in s.data for n, s in stores.items() if n != bad)
    _raises("collection_deleted", _ask, d, A)
    _raises("collection_deleted", d.register, A)


def test_pending_keeps_declared_order():
    d, _ = _world({"training": 1, "content": 1, "ciphertext": 1})
    assert _del(d, 0)["pending"] == ["content", "training", "ciphertext"]


def test_retry_needs_no_decision_and_re_erases_only_pending():
    d, stores = _world({"index": 2, "training": 1})
    assert _del(d, 0)["pending"] == ["index", "training"]
    erase_log.clear()
    rec = _del(d, 1, A, None, False)
    assert rec["pending"] == ["index"] and rec["revision"] == 1
    assert erase_log == [("index", A), ("training", A)]
    erase_log.clear()
    rec = _del(d, 1, A, None, False)
    assert rec == {"collection_id": A, "state": "deleted", "revision": 1, "pending": []}
    assert erase_log == [("index", A)]
    assert all(A not in s.data for s in stores.values())


def test_retry_with_nothing_pending_touches_no_store():
    d, _ = _world()
    _del(d, 0)
    erase_log.clear()
    assert _del(d, 1, A, None, False)["revision"] == 1
    assert erase_log == []


def test_well_formed_decision_on_a_retry_is_ignored():
    d, _ = _world()
    _del(d, 0)
    assert _del(d, 1)["revision"] == 1
    assert _del(d, 1, A, decision_for(B, 7))["revision"] == 1


def test_malformed_decision_on_a_retry_is_malformed():
    d, _ = _world()
    _del(d, 0)
    for bad in ("junk", "DEL1:" + "a" * 64, decision_for(A, 1) + "\n", StrSub(decision_for(A, 1))):
        _raises("malformed_delete_request", _del, d, 1, A, bad)


def test_retry_is_compare_and_set():
    d, _ = _world({"index": 1})
    _del(d, 0)
    _raises("stale_revision", _del, d, 0, A, None, False)
    _raises("stale_revision", _del, d, 2, A, None, False)


def test_base_exception_during_erase_leaves_the_tombstone_and_all_targets_pending():
    d, stores = _world({"index": 1}, exc=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        _del(d, 0)
    assert d.record(A) == {
        "collection_id": A,
        "state": "deleted",
        "revision": 1,
        "pending": list(_STORES),
    }
    _raises("collection_deleted", _ask, d, A)


# ---- tombstone -----------------------------------------------------------


def test_a_deleted_id_is_never_registered_again():
    d, _ = _world()
    _del(d, 0)
    before = d.record(A)
    _raises("collection_deleted", d.register, A)
    assert d.record(A) == before


def test_access_is_refused_even_with_nothing_pending():
    d, _ = _world()
    assert _del(d, 0)["pending"] == []
    _raises("collection_deleted", _ask, d, A)


# ---- concurrency and precedence ---------------------------------------------


@pytest.mark.parametrize("rev", [1, 2, _MAX_REV])
def test_stale_revision_is_conflict(rev):
    d, _ = _world()
    before = d.record(A)
    _raises("stale_revision", _del, d, rev)
    assert d.record(A) == before and erase_log == []


def test_stale_revision_edges():
    d, _ = _world()
    _at(d, 5)
    for rev in (4, 6, 0):
        _raises("stale_revision", _del, d, rev)
    assert _del(d, 5)["revision"] == 6


def test_malformed_decision_precedes_unknown_and_stale():
    d, _ = _world()
    _raises("malformed_delete_request", _del, d, 0, C, "junk")
    _raises("malformed_delete_request", _del, d, 3, A, "junk")


def test_unknown_precedes_stale_and_stale_precedes_decision():
    d, _ = _world()
    _raises("unknown_collection", _del, d, 3, C, None, False)
    _raises("stale_revision", _del, d, 3, A, None, False)


def test_stale_precedes_the_retry():
    d, _ = _world()
    _del(d, 0)
    erase_log.clear()
    _raises("stale_revision", _del, d, 0, A, None, False)
    assert erase_log == []


# ---- saturation: a delete is never refused for exhaustion ------------------


def test_delete_at_max_revision_writes_deleted_with_the_revision_unchanged():
    d, stores = _world()
    _at(d, _MAX_REV)
    rec = _del(d, _MAX_REV)
    assert rec == {"collection_id": A, "state": "deleted", "revision": _MAX_REV, "pending": []}
    assert erase_log == [(n, A) for n in _STORES]


def test_delete_just_below_max_revision_commits_to_max():
    d, _ = _world()
    _at(d, _MAX_REV - 1)
    assert _del(d, _MAX_REV - 1)["revision"] == _MAX_REV


def test_undecided_delete_at_max_revision_is_decision_missing():
    d, _ = _world()
    _at(d, _MAX_REV)
    _raises("decision_missing", _del, d, _MAX_REV, A, None, False)


def test_retry_at_max_revision_keeps_the_revision():
    d, _ = _world()
    _at(d, _MAX_REV, "deleted", ("index",))
    assert _del(d, _MAX_REV, A, None, False)["revision"] == _MAX_REV


@pytest.mark.parametrize("rev", [0, 1, _MAX_REV - 2])
def test_ordinary_delete_bumps_by_one(rev):
    d, _ = _world()
    _at(d, rev)
    assert _del(d, rev)["revision"] == rev + 1


# ---- rollback ------------------------------------------------------------


@pytest.mark.parametrize("case", ["decision_missing", "stale", "unknown", "malformed"])
def test_rejected_delete_leaves_everything_unchanged(case):
    d, stores = _world()
    before, data, states = d.record(A), _data(stores), dict(d._state)
    req = {
        "decision_missing": {"collection_id": A, "expected_revision": 0, "decision": None},
        "stale": {"collection_id": A, "expected_revision": 1, "decision": decision_for(A, 1)},
        "unknown": {"collection_id": C, "expected_revision": 0, "decision": decision_for(C, 0)},
        "malformed": {"collection_id": A, "expected_revision": -1, "decision": None},
    }[case]
    snap = copy.deepcopy(req)
    with pytest.raises(DeleteError):
        d.delete(req)
    assert d.record(A) == before and dict(d._state) == states
    assert _data(stores) == data and erase_log == [] and req == snap


def test_there_is_no_undelete():
    assert not hasattr(Deleter, "undelete") and not hasattr(Deleter, "restore")
    d, _ = _world()
    _del(d, 0)
    _raises("collection_deleted", d.register, A)
    _raises("collection_deleted", _ask, d, A)


def test_refused_access_changes_nothing():
    d, stores = _world()
    _del(d, 0)
    before, data = d.record(A), _data(stores)
    erase_log.clear()
    with pytest.raises(DeleteError):
        _ask(d, A)
    assert d.record(A) == before and _data(stores) == data and erase_log == []


# ---- hostile rows ---------------------------------------------------------

CALLS = []


class StrSub(str):
    pass


class DictSub(dict):
    pass


class ListSub(list):
    pass


class IntSub(int):
    pass


class EqRaises(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("eq")

    __hash__ = str.__hash__


class HashCollide(str):
    def __hash__(self):
        CALLS.append("hash")
        return hash("collection_id")

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)


def _base(op):
    if op == "access":
        return {"collection_id": A}
    return {"collection_id": A, "expected_revision": 0, "decision": decision_for(A, 0)}


def _hostile_rows():
    rows = []
    for op in ("access", "delete"):
        base = _base(op)
        rows.append((op, "dict_sub", DictSub(base)))
        rows.append((op, "list", list(base.items())))
        rows.append((op, "str", "collection_id"))
        rows.append((op, "none", None))
        for key in base:
            r = dict(base)
            if type(r[key]) is str:
                r[key] = StrSub(r[key])
            elif type(r[key]) is int:
                r[key] = IntSub(r[key])
            rows.append((op, f"{key}_sub", r))
            r = dict(base)
            r[key] = ListSub([r[key]])
            rows.append((op, f"{key}_listsub", r))
            r = dict(base)
            r[key] = DictSub({"v": r[key]})
            rows.append((op, f"{key}_dictsub", r))
            r = dict(base)
            del r[key]
            rows.append((op, f"missing_{key}", r))
            r = dict(base)
            r[StrSub(key)] = r.pop(key)
            rows.append((op, f"real_key_strsub_{key}", r))
            r = dict(base)
            r[key + "_x"] = r.pop(key)
            rows.append((op, f"renamed_key_{key}", r))
        for form, cls in (("plain", str), ("eq_raises", EqRaises), ("hash_collide", HashCollide)):
            r = dict(base)
            r[cls("extra")] = 1
            rows.append((op, f"extra_key_{form}", r))
        r = dict(base)
        r[b"collection_id"] = r.pop("collection_id")
        rows.append((op, "bytes_key", r))
        for tag, cid in (
            ("upper", "col1:" + "A" * 64),
            ("short", "col1:" + "a" * 63),
            ("long", "col1:" + "a" * 65),
            ("newline", A + "\n"),
            ("prefix", "COL1:" + "a" * 64),
            ("eq_raises", EqRaises(A)),
            ("hash_collide", HashCollide(A)),
            ("bytes", A.encode()),
            ("none", None),
        ):
            rows.append((op, f"cid_{tag}", dict(base, collection_id=cid)))
    t = _base("delete")
    for tag, bad in (
        ("true", True),
        ("false", False),
        ("neg", -1),
        ("float", 0.0),
        ("str", "0"),
        ("none", None),
        ("huge", 10**5000),
        ("2**63", 2**63),
    ):
        rows.append(("delete", f"rev_{tag}", dict(t, expected_revision=bad)))
    good = decision_for(A, 0)
    for tag, token in (
        ("upper", "DEL1:" + good[5:]),
        ("hex_upper", "del1:" + good[5:].upper()),
        ("newline", good + "\n"),
        ("hex63", good[:-1]),
        ("hex65", good + "a"),
        ("apv_prefix", "apv1:" + good[5:]),
        ("eq_raises", EqRaises(good)),
        ("bytes", good.encode()),
        ("false", False),
        ("empty", ""),
    ):
        rows.append(("delete", f"decision_{tag}", dict(t, decision=token)))
    return rows


ROWS = _hostile_rows()


def _snap(req):
    if isinstance(req, dict):
        return [(k, copy.deepcopy(v)) for k, v in dict.items(req)]
    return copy.deepcopy(req)


def _same(v1, v2):
    """Equality that never calls a caller method."""
    if type(v1) is not type(v2):
        return False
    if isinstance(v1, str):
        return str.__eq__(v1, v2) is True
    return v1 == v2


def _unchanged(req, snap):
    if isinstance(req, dict):
        now = list(dict.items(req))
        return len(now) == len(snap) and all(
            k1 is k2 and _same(v1, v2) for (k1, v1), (k2, v2) in zip(now, snap, strict=True)
        )
    return type(req) is type(snap) and req == snap


@pytest.mark.parametrize("op,name,req", ROWS, ids=[f"{o}-{n}" for o, n, _ in ROWS])
def test_hostile_request_fails_closed_typed(op, name, req):
    d, stores = _world()
    before, data = dict(d._state), _data(stores)
    snap = _snap(req)
    CALLS.clear()
    _raises("malformed_delete_request", getattr(d, op), req)
    assert CALLS == [] and erase_log == []
    assert dict(d._state) == before and _data(stores) == data
    assert _unchanged(req, snap)


@pytest.mark.parametrize(
    "bad",
    [StrSub(A), A.upper(), None, [A], A + "\n", A.encode(), EqRaises(A), HashCollide(A)],
    ids=["strsub", "upper", "none", "list", "newline", "bytes", "eq_raises", "hash_collide"],
)
def test_register_hostile_ids(bad):
    d = Deleter({})
    CALLS.clear()
    _raises("malformed_delete_request", d.register, bad)
    assert d._state == {} and CALLS == []


# ---- executable kill check: one-edit mutants of the reference engine ------
# Each edit is applied to THIS file's source; the mutated copy's battery
# (minus this section) must go red. The identity edit must stay green.

MUTANTS = {
    "req_isinstance": (
        "    if type(req) is not dict:",
        "    if not isinstance(req, dict):",
    ),
    "key_type_skip": (
        "        if type(k) is not str:",
        "        if not isinstance(k, str):",
    ),
    "keys_subset": (
        "    if set(req) != keys:",
        "    if not keys <= set(req):",
    ),
    "keys_same_arity": (
        "    if set(req) != keys:",
        "    if len(req) != len(keys):",
    ),
    "cid_isinstance": (
        "    if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:",
        "    if not isinstance(cid, str) or _COL_RE.fullmatch(cid) is None:",
    ),
    "cid_match": (
        "    if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:",
        "    if not _exact_str(cid) or _COL_RE.match(cid) is None:",
    ),
    "cid_search": (
        "    if not _exact_str(cid) or _COL_RE.fullmatch(cid) is None:",
        "    if not _exact_str(cid) or _COL_RE.search(cid.lower()) is None:",
    ),
    "register_unchecked": (
        "        if not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None:",
        "        if collection_id is None:",
    ),
    "register_match": (
        "        if not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None:",
        "        if not _exact_str(collection_id) or _COL_RE.match(collection_id) is None:",
    ),
    "register_isinstance": (
        "        if not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None:",
        "        if not isinstance(collection_id, str) or _COL_RE.fullmatch(collection_id) is None:",  # noqa: E501
    ),
    "str_isinstance": (
        "    return type(v) is str",
        "    return isinstance(v, str)",
    ),
    "rev_isinstance": (
        "    return type(v) is int and 0 <= v <= _MAX_REV",
        "    return isinstance(v, int) and 0 <= v <= _MAX_REV",
    ),
    "rev_lower_edge": (
        "    return type(v) is int and 0 <= v <= _MAX_REV",
        "    return type(v) is int and -1 <= v <= _MAX_REV",
    ),
    "rev_upper_edge": (
        "    return type(v) is int and 0 <= v <= _MAX_REV",
        "    return type(v) is int and 0 <= v <= _MAX_REV + 1",
    ),
    "rev_unchecked": (
        '        if not _exact_rev(req["expected_revision"]):',
        "        if False:",
    ),
    "decision_none_ok": (
        "    if token is None:\n        return False",
        "    if token is None:\n        return True",
    ),
    "decision_isinstance": (
        "    if not _exact_str(token) or _DEL_RE.fullmatch(token) is None:",
        "    if not isinstance(token, str) or _DEL_RE.fullmatch(token) is None:",
    ),
    "decision_match": (
        "    if not _exact_str(token) or _DEL_RE.fullmatch(token) is None:",
        "    if not _exact_str(token) or _DEL_RE.match(token) is None:",
    ),
    "decision_grammar_unchecked": (
        "    if not _exact_str(token) or _DEL_RE.fullmatch(token) is None:",
        "    if not _exact_str(token):",
    ),
    "decision_unbound": (
        '    return token == decision_for(cid, req["expected_revision"])',
        "    return True",
    ),
    "decision_cid_ignored": (
        '    return token == decision_for(cid, req["expected_revision"])',
        '    return token == decision_for(A, req["expected_revision"])',
    ),
    "decision_rev_ignored": (
        '    return token == decision_for(cid, req["expected_revision"])',
        "    return token == decision_for(cid, 0)",
    ),
    "pre_sep": (
        '    raw = f"{collection_id}|{expected_revision}|delete".encode()',
        '    raw = f"{collection_id}:{expected_revision}:delete".encode()',
    ),
    "pre_trailing": (
        '    raw = f"{collection_id}|{expected_revision}|delete".encode()',
        '    raw = f"{collection_id}|{expected_revision}|delete|".encode()',
    ),
    "pre_order": (
        '    raw = f"{collection_id}|{expected_revision}|delete".encode()',
        '    raw = f"{expected_revision}|{collection_id}|delete".encode()',
    ),
    "pre_padded": (
        '    raw = f"{collection_id}|{expected_revision}|delete".encode()',
        '    raw = f"{collection_id}|{expected_revision:03d}|delete".encode()',
    ),
    "pre_target": (
        '    raw = f"{collection_id}|{expected_revision}|delete".encode()',
        '    raw = f"{collection_id}|{expected_revision}|erase".encode()',
    ),
    "pre_encoding": (
        '    raw = f"{collection_id}|{expected_revision}|delete".encode()',
        '    raw = f"{collection_id}|{expected_revision}|delete".encode("utf-16")',
    ),
    "digest_upper": (
        '    return "del1:" + hashlib.sha256(raw).hexdigest()',
        '    return "del1:" + hashlib.sha256(raw).hexdigest().upper()',
    ),
    "decision_after_unknown": (
        "        approved = _decision_ok(req, cid)\n        if cid not in self._state:",
        "        if cid not in self._state:",
    ),
    "unknown_unchecked": (
        '        if cid not in self._state:\n            _fail("unknown_collection")',
        '        if cid not in self._state:\n            self._state[cid] = ("live", 0, ())',
    ),
    "stale_unchecked": (
        '        if req["expected_revision"] != rev:',
        '        if req["expected_revision"] > rev:',
    ),
    "retry_needs_decision": (
        '        if state == "deleted":\n            targets = pending',
        '        if state == "deleted" and approved:\n            targets = pending',
    ),
    "retry_all_stores": (
        '        if state == "deleted":\n            targets = pending',
        '        if state == "deleted":\n            targets = _STORES',
    ),
    "decision_skipped": (
        '            if not approved:\n                _fail("decision_missing")',
        '            if False:\n                _fail("decision_missing")',
    ),
    "exhausted_refused": (
        "            rev = rev if rev == _MAX_REV else rev + 1",
        '            rev = _fail("stale_revision") if rev == _MAX_REV else rev + 1',
    ),
    "saturation_wraps": (
        "            rev = rev if rev == _MAX_REV else rev + 1",
        "            rev = 0 if rev == _MAX_REV else rev + 1",
    ),
    "no_bump": (
        "            rev = rev if rev == _MAX_REV else rev + 1",
        "            rev = rev",
    ),
    "retry_bumps": (
        '        if state == "deleted":\n            targets = pending',
        '        if state == "deleted":\n            targets, rev = pending, rev + 1',
    ),
    "tombstone_late": (
        '        self._state[cid] = ("deleted", rev, tuple(targets))  # tombstone first',
        "        pass  # tombstone first",
    ),
    "tombstone_pending_empty": (
        '        self._state[cid] = ("deleted", rev, tuple(targets))  # tombstone first',
        '        self._state[cid] = ("deleted", rev, ())  # tombstone first',
    ),
    "erase_stops_on_failure": (
        "                left.append(name)",
        "                left.append(name)\n                break",
    ),
    "failed_not_pending": (
        "                left.append(name)",
        "                pass",
    ),
    "erase_failure_revives": (
        '        self._state[cid] = ("deleted", rev, tuple(left))',
        '        self._state[cid] = ("live" if left else "deleted", rev, tuple(left))',
    ),
    "pending_sorted": (
        '        self._state[cid] = ("deleted", rev, tuple(left))',
        '        self._state[cid] = ("deleted", rev, tuple(sorted(left)))',
    ),
    "erase_reversed": (
        "        for name in targets:",
        "        for name in reversed(targets):",
    ),
    "catch_base": (
        "            except Exception:  # noqa: BLE001 - a failed erase stays pending",
        "            except BaseException:  # noqa: BLE001 - a failed erase stays pending",
    ),
    "access_unknown_allowed": (
        '        if st is None:\n            _fail("unknown_collection")',
        '        if st is None:\n            return "allow"',
    ),
    "access_pending_only": (
        '        if st[0] != "live":',
        "        if st[2]:",
    ),
    "access_unknown_as_deleted": (
        '        if st is None:\n            _fail("unknown_collection")',
        '        if st is None:\n            _fail("collection_deleted")',
    ),
    "tombstone_reuse": (
        '        if st is not None and st[0] == "deleted":',
        "        if False:",
    ),
    "reregister_resets": (
        '        self._state.setdefault(collection_id, ("live", 0, ()))',
        '        self._state[collection_id] = ("live", 0, ())',
    ),
    "record_pending_tuple": (
        "        return dict(zip(_FIELDS, (cid, state, rev, list(pending)), strict=True))",
        "        return dict(zip(_FIELDS, (cid, state, rev, pending), strict=True))",
    ),
    "error_code": (
        "        self.code = FAILURE_MAPPING[failure_class]",
        '        self.code = "internal"',
    ),
    "error_chained": (
        "    raise DeleteError(cls)",
        "    raise DeleteError(cls) from ValueError()",
    ),
    "max_rev_const": (
        "_MAX_REV = 2**63 - 1",
        "_MAX_REV = 2**63",
    ),
}

EQUIVALENT = {
    # the stored state is only ever the exact str "live" or "deleted"
    "access_eq_deleted": ('        if st[0] != "live":', '        if st[0] == "deleted":'),
}
_SELF = Path(__file__)


# the battery without this section: every test above, parametrized rows
# expanded; a green copy must run exactly this many and pass them all
EXPECTED_BATTERY = 206


def _battery(tmp_dir, source):
    """Run the copy (kill-check section stripped, so no selection flags are
    needed) from a neutrally named dir; return (returncode, passed, failed)."""
    copy_ = tmp_dir / "test_battery_copy.py"
    copy_.write_text(source)
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
            str(copy_),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    passed = re.search(r"(\d+) passed", tail)
    failed = re.search(r"(\d+) failed", tail)
    return (
        r.returncode,
        int(passed.group(1)) if passed else 0,
        int(failed.group(1)) if failed else 0,
    )


def _green(tmp_dir, source):
    rc, passed, failed = _battery(tmp_dir, source)
    return rc == 0 and passed == EXPECTED_BATTERY and failed == 0


def _red(tmp_dir, source):
    # tests ran and at least one FAILED; rc 2/4/5 (errors, usage, nothing
    # collected) is never a kill
    rc, passed, failed = _battery(tmp_dir, source)
    return rc == 1 and failed >= 1 and passed + failed == EXPECTED_BATTERY


def _edited(before, after):
    source = _SELF.read_text()
    head, _, _ = source.partition("# ---- executable kill check")
    assert head.count(before) == 1, before
    return head.replace(before, after)


def test_identity_mutant_is_green(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("battery")
    assert _green(tmp, _edited("_MAX_REV = 2**63 - 1", "_MAX_REV = 2**63 - 1"))


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_source_mutant_is_killed(name, tmp_path_factory):
    assert _red(tmp_path_factory.mktemp("battery"), _edited(*MUTANTS[name]))


@pytest.mark.parametrize("name", sorted(EQUIVALENT))
def test_equivalent_edit_stays_green(name, tmp_path_factory):
    assert _green(tmp_path_factory.mktemp("battery"), _edited(*EQUIVALENT[name]))
