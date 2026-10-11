"""T0405 independent local-delete model with seeded partial failures and retries."""

from __future__ import annotations

import copy
import hashlib
import random

import pytest

from desktop.privacy import delete as production
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome

CID = "col1:" + "a" * 64
OTHER = "col1:" + "b" * 64
STORES = ("content", "index", "analysis", "training", "ciphertext")
MAX = 2**63 - 1


def decision(revision):
    return "del1:" + hashlib.sha256(f"{CID}|{revision}|delete".encode()).hexdigest()


class Backend:
    def __init__(self, name, failures, log, observe):
        self.name = name
        self.failures = failures
        self.data = {CID, OTHER}
        self.log = log
        self.observe = observe

    def erase(self, cid):
        self.log.append((self.name, cid, self.observe()))
        if self.failures:
            self.failures -= 1
            raise OSError("synthetic local erase failure")
        self.data.discard(cid)


def trace(module, seed, revision):
    rng = random.Random(seed)
    log = []
    failures = {name: rng.randrange(4) for name in STORES}
    store = None
    backends = {
        name: Backend(name, n, log, lambda: store._state[CID][0]) for name, n in failures.items()
    }
    store = module.Deleter(backends)
    assert store.register(CID) == {
        "collection_id": CID,
        "state": "live",
        "revision": 0,
        "pending": [],
    }
    store._state[CID] = ("live", revision, ())
    snapshot = copy.deepcopy((store._state, {n: b.data for n, b in backends.items()}, log))
    rq = {"collection_id": CID, "expected_revision": revision, "decision": None}
    assert outcome(store.delete, module.DeleteError, rq) == (
        "failure",
        "decision_missing",
        "approval_required",
    )
    assert (store._state, {n: b.data for n, b in backends.items()}, log) == snapshot
    pending = list(STORES)
    committed = min(MAX, revision + 1)
    for iteration in range(5):
        rq = {
            "collection_id": CID,
            "expected_revision": revision if iteration == 0 else committed,
            "decision": decision(revision) if iteration == 0 else None,
        }
        before = copy.deepcopy(rq)
        prior_log = len(log)
        targets = list(pending)
        pending = []
        for name in targets:
            if failures[name]:
                failures[name] -= 1
                pending.append(name)
        expected = {
            "collection_id": CID,
            "state": "deleted",
            "revision": committed,
            "pending": list(pending),
        }
        result = outcome(store.delete, module.DeleteError, rq)
        assert result == ("ok", expected)
        assert rq == before
        assert log[prior_log:] == [(name, CID, "deleted") for name in targets]
        for name, b in backends.items():
            assert OTHER in b.data
            assert (CID in b.data) == (name in pending)
        result[1]["pending"].append("caller-only")
        assert store.record(CID) == expected
        assert outcome(store.access, module.DeleteError, {"collection_id": CID}) == (
            "failure",
            "collection_deleted",
            "collection_deleted",
        )
    assert pending == []
    assert outcome(store.register, module.DeleteError, CID) == (
        "failure",
        "collection_deleted",
        "collection_deleted",
    )


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("revision", [0, 1, MAX - 1, MAX])
def test_seeded_failures_retry_tombstone_and_irreversibility(seed, revision):
    trace(production, seed, revision)


@pytest.mark.parametrize(
    "old,new",
    [
        ("if not approved:", "if False:"),
        ("targets = pending", "targets = _STORES"),
        ('if st[0] != "live":', "if False:"),
        ("left.append(name)", "pass"),
        ("rev = rev if rev == _MAX_REV else rev + 1", "rev = rev + 1"),
    ],
)
def test_independent_red_controls(old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        for seed in range(5):
            trace(mutant, seed, MAX)


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_errors_are_not_semantic_kills(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.DeleteError, {})
