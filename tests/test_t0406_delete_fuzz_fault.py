"""T0406 delete refusal fuzz and synthetic local erase fault containment.

Erase exceptions (including AssertionError) are specified pending outcomes,
not mutant kills. Foreign exceptions from the policy boundary are crashes.
No durable disk or restart guarantee is established here.
"""

from __future__ import annotations

import copy
import random

import pytest

from desktop.privacy import delete as production
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome

CID = "col1:" + "a" * 64
OTHER = "col1:" + "b" * 64
NAMES = ("content", "index", "analysis", "training", "ciphertext")


class Backend:
    def __init__(self, name, log, exception=None):
        self.name = name
        self.log = log
        self.exception = exception
        self.data = {CID, OTHER}

    def erase(self, cid):
        self.log.append((self.name, cid))
        if self.exception:
            raise self.exception("synthetic backend fault")
        self.data.discard(cid)


def world(module, exception=None, failing=None):
    log = []
    backends = {n: Backend(n, log, exception if n == failing else None) for n in NAMES}
    store = module.Deleter(backends)
    store.register(CID)
    return store, backends, log


def request(module):
    return {"collection_id": CID, "expected_revision": 0, "decision": module.decision_for(CID, 0)}


def defects(module):
    rows = []
    for field in request(module):
        rq = request(module)
        del rq[field]
        rows.append(rq)
    rq = request(module)
    rq["extra"] = 1
    rows.append(rq)
    for field, values in [
        ("collection_id", ["", "col1:" + "g" * 64, CID + "\n", None, [], True]),
        ("expected_revision", [-1, 2**63, True, None, 1.0, "0"]),
        ("decision", ["", "del1:" + "g" * 64, "del1:" + "a" * 63, "del1:" + "a" * 65, 1, []]),
    ]:
        for value in values:
            rq = request(module)
            rq[field] = value
            rows.append(rq)
    return rows


def refuse(module, rq):
    store, backends, log = world(module)
    before = copy.deepcopy((store._state, rq, {n: b.data for n, b in backends.items()}, log))
    assert outcome(store.delete, module.DeleteError, rq) == (
        "failure",
        "malformed_delete_request",
        "malformed_request",
    )
    assert (store._state, rq, {n: b.data for n, b in backends.items()}, log) == before
    assert outcome(store.delete, module.DeleteError, request(module))[0] == "ok"


@pytest.mark.parametrize("seed", range(30))
def test_seeded_malformed_request_fuzz_is_atomic(seed):
    rows = defects(production)
    random.Random(seed).shuffle(rows)
    for rq in rows:
        refuse(production, rq)


def fault(module, name, exception):
    store, backends, log = world(module, exception, name)
    result = outcome(store.delete, module.DeleteError, request(module))
    assert result == (
        "ok",
        {"collection_id": CID, "state": "deleted", "revision": 1, "pending": [name]},
    )
    assert log == [(n, CID) for n in NAMES]
    assert CID in backends[name].data
    assert all(OTHER in b.data for b in backends.values())
    assert outcome(store.access, module.DeleteError, {"collection_id": CID}) == (
        "failure",
        "collection_deleted",
        "collection_deleted",
    )
    before = list(log)
    backends[name].exception = None
    rq = {"collection_id": CID, "expected_revision": 1, "decision": None}
    assert outcome(store.delete, module.DeleteError, rq) == (
        "ok",
        {"collection_id": CID, "state": "deleted", "revision": 1, "pending": []},
    )
    assert log[len(before) :] == [(name, CID)]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("exception", [OSError, RuntimeError, KeyError, AssertionError])
def test_every_store_fault_remains_pending_and_retry_is_scoped(name, exception):
    fault(production, name, exception)


@pytest.mark.parametrize(
    "old,new",
    [
        ("left.append(name)", "pass"),
        ("targets = pending", "targets = _STORES"),
        ('if req["expected_revision"] != rev:', "if False:"),
    ],
)
def test_independent_red_fault_reproductions(old, new):
    module = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        fault(module, "index", OSError)
        store, _, _ = world(module)
        rq = request(module)
        rq["expected_revision"] = 1
        rq["decision"] = module.decision_for(CID, 1)
        assert outcome(store.delete, module.DeleteError, rq) == (
            "failure",
            "stale_revision",
            "conflict",
        )


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_policy_crash_is_not_semantic(exception):
    def broken(rq):
        raise exception("policy-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.DeleteError, {})
