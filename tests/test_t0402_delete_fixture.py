"""T0402 closed delete privacy fixture.

Pinned rows are independent of runtime results, closed by a literal name
manifest and SHA-256 of each complete row. Replays check exact typed outputs,
input immutability, deep composed-state atomicity and the valid continuation
following refusals. Fixture tampering and semantic mutants must fail assertions;
foreign exceptions are never counted as kills. All values and stores are synthetic.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests import test_t0401_delete_contract as ref

FIXTURE = Path(__file__).parent / "fixtures" / "delete" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
PINNED = [
    ("delete-erases-in-order", "86085790323c64c04e0ca2f28600e11fcbe0a4fcfc4aa0f289a03fcb4ce690e6"),
    ("reregister-live-noop", "53c0a4866ab500afa2e2f42c7d5451fc9c25869f1ad96f8c180ebe533ed2e251"),
    ("empty-retry-no-effects", "5fae446766a10da0f6aa0d79e98186b725f8f9ac4dcb03b0c0e8367dc15c8f36"),
    (
        "deleted-never-reregister",
        "5e68fbbddc18cde0f725c69d540c5c11dba688ab707e72f6e3c0eaa0e21cba21",
    ),
    ("unknown-access", "28a7093d0aa126b75dd7751881faa6112d8b20659d204878448be13bc7e54de0"),
    ("unknown-delete", "1bc128877386a0cd34bd6d4012d2c054e5b33fe6b69c70d82183914fc8377597"),
    ("partial-content", "715ae7018476a3414931ffd2c08d5d743c4a4b384972a21105f6b65ddf8ad250"),
    ("partial-index", "6e97ae6837b6a9b2cd826b406f7009c552e9ea814c3ae7aba7aeb853088a53e3"),
    ("partial-analysis", "f3dcf72c1a6ee7326c339ba61aa5b95e00484bfaca54d1ab80b6dff0a2849d96"),
    ("partial-training", "430408da0b19e4e461b7c945f710420c02e1c8dfb98c8e9051bdace289cae3c3"),
    ("partial-ciphertext", "714f16a046791c458f683ff469696dae93acd8e8c5343c222f31f4ca66526ff3"),
    (
        "all-fail-in-declared-order",
        "ac95f504261032176f1456d23624b297530a75c8755ee9d7af65b0297e26be60",
    ),
    (
        "revision-9223372036854775806",
        "cbbbce77b9a496cec3ea28165163b4008708c524e8093df18b1307963547388b",
    ),
    (
        "revision-9223372036854775807",
        "c2203d935370ca864c6f5f373ac729c66b055b21e28440f4f0be4ee02917ca84",
    ),
    ("missing-decision", "bd778eec0ecdec750f08de1ea761849599565e8a555a5c836ff5b21c60893f87"),
    ("wrong-collection", "9ff6a5f9030b0e9e2300b7e1652c15c15e0ec5384e033ab970434508a46eee34"),
    ("wrong-revision", "6f68159f8676b366fb9b02e537c3ce24c173a8339024d44861db938b7ec37214"),
    ("stale", "43718e4db92dad58aa9a0c86dc43f49a7b26b922e29e2fbe1dc7e368208fd6e7"),
    ("negative", "71edf5b2cae863e17354e5cdfc22e20efd6397ffa9b733450b1e551b4ce08e32"),
    ("bool-revision", "4b8f6e5f84144c119e8fd7b633b3e6ac910c177b8fc87e7168151fbc012f4d8e"),
    ("bad-token", "65924241d7e3ba705c8003cdc021f4370958d9e0bf93253e3dea0a3d1f9003a3"),
    (
        "rejected-retry-unchanged",
        "50f5d066a2c1cd0ca4fafe80864e7062e76ea68a7021cc9fba5ec5d3f0583abc",
    ),
    (
        "retry-malformed-decision",
        "428855fe8327204fbaa09380fa373a502b537daa222dc6170949cffc0679d551",
    ),
]


def digest(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def closure(doc):
    assert type(doc) is dict and set(doc) == {"schema", "contract", "sections", "rows"}
    assert type(doc["schema"]) is int and doc["schema"] == 1
    assert doc["contract"] == "privacy-delete"
    assert doc["sections"] == list(SECTIONS)
    assert type(doc["rows"]) is list
    assert [(r["name"], digest(r)) for r in doc["rows"]] == PINNED
    assert {row["section"] for row in doc["rows"]} == set(SECTIONS)
    failures = set()
    for row in doc["rows"]:
        assert set(row) == {"name", "section", "config", "steps"}
        assert type(row["name"]) is str and type(row["config"]) is dict
        assert type(row["steps"]) is list and row["steps"]
        for step in row["steps"]:
            assert set(step) == {"op", "request", "expect"}
            assert type(step["op"]) is str and type(step["expect"]) is dict
            failures.update([step["expect"]["failure"]] if "failure" in step["expect"] else [])
    assert failures == set(ref.FAILURE_CLASSES)


def exact(got, expected):
    assert type(got) is type(expected)
    if type(expected) is dict:
        assert {(type(k), k) for k in got} == {(type(k), k) for k in expected}
        for key in expected:
            exact(got[key], expected[key])
    elif type(expected) in (tuple, list):
        assert len(got) == len(expected)
        for left, right in zip(got, expected, strict=True):
            exact(left, right)
    else:
        assert got == expected


def world(config, binding):
    store, stores = ref._world(config.get("fails"))
    if binding is not None:
        store = call(binding, stores)
        call(store.register, ref.A)
        call(store.register, ref.B)
    store._state[ref.A] = ("live", config.get("revision", 0), ())
    ref.erase_log.clear()
    violations = []

    # Every erase independently witnesses the tombstone-before-effects rule.
    def probe(_name, cid):
        if store._state[cid][0] != "deleted":
            violations.append(("tombstone", cid))

    for backend in stores.values():
        backend.probe = probe
    return store, stores, violations


def state(context):
    store, stores, violations = context
    return copy.deepcopy(
        (store._state, {n: (set(s.data), s.fail) for n, s in stores.items()}, ref.erase_log)
    )


def execute(context, op, request):
    store = context[0]
    if op == "register":
        return {"record": store.register(request["collection_id"])}
    if op == "access":
        return {"verdict": store.access(request)}
    return {"record": store.delete(request)}


def containers(value):
    found = set()
    if type(value) in (dict, list, tuple, set):
        found.add(id(value))
        children = value.values() if type(value) is dict else value
        for child in children:
            found.update(containers(child))
    return found


def owned_containers(context):
    store, stores, violations = context
    return containers((store._state, ref.erase_log, violations)) | set().union(
        *(containers(vars(backend)) for backend in stores.values())
    )


class ForeignCrash(Exception):
    """An implementation exception, never a semantic kill."""


def call(fn, *args):
    try:
        return fn(*args)
    except ref.DeleteError:
        raise
    except Exception as exc:  # noqa: BLE001 - includes implementation AssertionError
        raise ForeignCrash(type(exc).__name__) from exc


def replay(row, binding=None):
    context = world(row["config"], binding)
    for step in row["steps"]:
        rq = copy.deepcopy(step["request"])
        before = copy.deepcopy(rq)
        prior = state(context)
        try:
            got = call(execute, context, step["op"], rq)
        except ref.DeleteError as exc:
            assert type(exc) is ref.DeleteError
            assert exc.failure_class in ref.FAILURE_CLASSES
            assert exc.code == ref.FAILURE_MAPPING[exc.failure_class]
            assert exc.__cause__ is None and exc.__context__ is None
            got = {"failure": exc.failure_class}
            exact(state(context), prior)
        assert not (containers(got) & (containers(rq) | owned_containers(context)))
        exact(rq, before)
        exact(got, step["expect"])
        if step["op"] == "delete" and "record" in got:
            store, stores, violations = context
            cid = rq["collection_id"]
            record = got["record"]
            prior_lifecycle, _rev, prior_pending = prior[0][cid]
            targets = tuple(ref._STORES) if prior_lifecycle == "live" else prior_pending
            assert violations == []
            calls = ref.erase_log[len(prior[2]) :]
            assert calls == [(name, cid) for name in targets]
            for name in targets:
                assert (cid in stores[name].data) == (name in record["pending"])
            assert store._state[ref.B] == prior[0][ref.B]
        elif step["op"] == "access":
            exact(state(context), prior)


def battery(binding=None):
    closure(CASES)
    for row in CASES["rows"]:
        replay(row, binding)


@pytest.mark.parametrize("row", CASES["rows"], ids=[r["name"] for r in CASES["rows"]])
def test_pinned_row(row):
    replay(row)


def test_manifest_and_green_battery():
    battery()


@pytest.mark.parametrize(
    "edit",
    [
        "drop",
        "duplicate",
        "swap",
        "outcome",
        "type",
        "request",
        "section",
        "rename",
        "extra",
        "config",
    ],
)
def test_fixture_edits_fail_closed(edit):
    doc = copy.deepcopy(CASES)
    rows = doc["rows"]
    if edit == "drop":
        rows.pop()
    elif edit == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif edit == "swap":
        rows[0], rows[1] = rows[1], rows[0]
    elif edit == "outcome":
        rows[0]["steps"][0]["expect"] = {"failure": "internal"}
    elif edit == "type":
        doc["schema"] = True
    elif edit == "request":
        rows[0]["steps"][0]["request"] = None
    elif edit == "section":
        rows[0]["section"] = "rollback"
    elif edit == "rename":
        rows[0]["name"] = "replacement"
    elif edit == "extra":
        rows[0]["unreviewed"] = True
    else:
        rows[0]["config"]["extra"] = True
    with pytest.raises(AssertionError):
        closure(doc)


class NoDecision(ref.Deleter):
    def delete(self, request):
        request = dict(
            request,
            decision=ref.decision_for(request["collection_id"], request["expected_revision"]),
        )
        return super().delete(request)


class AllowDeleted(ref.Deleter):
    def access(self, request):
        return "allow"


class Resurrect(ref.Deleter):
    def register(self, cid):
        self._state.pop(cid, None)
        return super().register(cid)


class DropPending(ref.Deleter):
    def delete(self, request):
        record = super().delete(request)
        record["pending"] = []
        return record


class TouchInput(ref.Deleter):
    def delete(self, request):
        record = super().delete(request)
        request["decision"] = None
        return record


class CrashOnly(ref.Deleter):
    def delete(self, request):
        raise KeyError("synthetic crash")


MUTANTS = {
    "decision": NoDecision,
    "access": AllowDeleted,
    "resurrect": Resurrect,
    "pending": DropPending,
    "request": TouchInput,
}


def test_foreign_crash_does_not_count_as_a_semantic_kill():
    with pytest.raises(ForeignCrash):
        battery(CrashOnly)


HOSTILE_NAMES = [
    "access/dict_sub",
    "access/list",
    "access/str",
    "access/none",
    "access/collection_id_sub",
    "access/collection_id_listsub",
    "access/collection_id_dictsub",
    "access/missing_collection_id",
    "access/real_key_strsub_collection_id",
    "access/renamed_key_collection_id",
    "access/extra_key_plain",
    "access/extra_key_eq_raises",
    "access/extra_key_hash_collide",
    "access/bytes_key",
    "access/cid_upper",
    "access/cid_short",
    "access/cid_long",
    "access/cid_newline",
    "access/cid_prefix",
    "access/cid_eq_raises",
    "access/cid_hash_collide",
    "access/cid_bytes",
    "access/cid_none",
    "delete/dict_sub",
    "delete/list",
    "delete/str",
    "delete/none",
    "delete/collection_id_sub",
    "delete/collection_id_listsub",
    "delete/collection_id_dictsub",
    "delete/missing_collection_id",
    "delete/real_key_strsub_collection_id",
    "delete/renamed_key_collection_id",
    "delete/expected_revision_sub",
    "delete/expected_revision_listsub",
    "delete/expected_revision_dictsub",
    "delete/missing_expected_revision",
    "delete/real_key_strsub_expected_revision",
    "delete/renamed_key_expected_revision",
    "delete/decision_sub",
    "delete/decision_listsub",
    "delete/decision_dictsub",
    "delete/missing_decision",
    "delete/real_key_strsub_decision",
    "delete/renamed_key_decision",
    "delete/extra_key_plain",
    "delete/extra_key_eq_raises",
    "delete/extra_key_hash_collide",
    "delete/bytes_key",
    "delete/cid_upper",
    "delete/cid_short",
    "delete/cid_long",
    "delete/cid_newline",
    "delete/cid_prefix",
    "delete/cid_eq_raises",
    "delete/cid_hash_collide",
    "delete/cid_bytes",
    "delete/cid_none",
    "delete/rev_true",
    "delete/rev_false",
    "delete/rev_neg",
    "delete/rev_float",
    "delete/rev_str",
    "delete/rev_none",
    "delete/rev_huge",
    "delete/rev_2**63",
    "delete/decision_upper",
    "delete/decision_hex_upper",
    "delete/decision_newline",
    "delete/decision_hex63",
    "delete/decision_hex65",
    "delete/decision_apv_prefix",
    "delete/decision_eq_raises",
    "delete/decision_bytes",
    "delete/decision_false",
    "delete/decision_empty",
]


@pytest.mark.parametrize("op,name,rq", ref.ROWS, ids=[o + "/" + n for o, n, _ in ref.ROWS])
def test_hostile_fixture(op, name, rq):
    assert [o + "/" + n for o, n, _ in ref.ROWS] == HOSTILE_NAMES
    context = world({}, None)
    before = state(context)
    snap = ref._snap(rq)
    ref.CALLS.clear()
    with pytest.raises(ref.DeleteError) as caught:
        getattr(context[0], op)(rq)
    assert type(caught.value) is ref.DeleteError
    assert caught.value.failure_class == "malformed_delete_request"
    assert caught.value.code == "malformed_request"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert ref.CALLS == []
    assert ref._unchanged(rq, snap)
    exact(state(context), before)


class EraseBeforeTombstone(ref.Deleter):
    def delete(self, request):
        for store in self._stores.values():
            store.erase(request["collection_id"])
        return super().delete(request)


class RetryEveryStore(ref.Deleter):
    def delete(self, request):
        cid = request["collection_id"]
        status, revision, pending = self._state[cid]
        if status == "deleted":
            self._state[cid] = (status, revision, tuple(ref._STORES))
        return super().delete(request)


class RefusalErases(ref.Deleter):
    def delete(self, request):
        try:
            return super().delete(request)
        except ref.DeleteError:
            self._stores["content"].data.clear()
            raise


MUTANTS.update(
    tombstone_order=EraseBeforeTombstone, retry_scope=RetryEveryStore, rollback=RefusalErases
)


class AliasedReceipt(ref.Deleter):
    def delete(self, request):
        record = super().delete(request)
        cid = request["collection_id"]
        status, revision, _pending = self._state[cid]
        self._state[cid] = (status, revision, record["pending"])
        return record


MUTANTS["receipt_alias"] = AliasedReceipt


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_semantic_mutants_fail_pinned_replay(name):
    with pytest.raises(AssertionError):
        battery(MUTANTS[name])


@pytest.mark.parametrize("exception", [AssertionError, KeyError])
def test_implementation_exceptions_are_foreign(exception):
    class Broken(ref.Deleter):
        def delete(self, request):
            raise exception("implementation-only crash")

    with pytest.raises(ForeignCrash):
        battery(Broken)
