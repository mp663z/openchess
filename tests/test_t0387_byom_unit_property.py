"""T0387 independent terms/approval state model with seeded BYOM transitions.

Closed terms are constructed here, digests and approvals derived independently.
This local gate suite covers no provider dispatch, retention assurance or keys.
"""

from __future__ import annotations

import copy
import hashlib
import random

import pytest

from desktop.privacy import byom as production
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome

INS = "ins1:" + "a" * 64
PROVIDER = "prv1:fixture"
CID = "col1:" + "b" * 64
MAX = 2**63 - 1


def digest(terms):
    body = f"{terms['retention_days']}|{terms['region']}|{str(terms['trains_on_inputs']).lower()}"
    return "trm1:" + hashlib.sha256(body.encode()).hexdigest()


def approval(terms, revision):
    return (
        "byt1:"
        + hashlib.sha256(f"{INS}|{PROVIDER}|{digest(terms)}|{revision}".encode()).hexdigest()
    )


class Cloud:
    def __init__(self):
        self.calls = []

    def verdict(self, rq):
        self.calls.append(copy.deepcopy(rq))
        return "allow"


def trace(module, seed, start_revision):
    rng = random.Random(seed)
    cloud = Cloud()
    store = module.ByomStore(cloud)
    store.register(INS, PROVIDER)
    accepted = (
        digest({"retention_days": 30, "region": "us", "trains_on_inputs": True})
        if seed % 2
        else None
    )
    revision = start_revision
    store._state[(INS, PROVIDER)] = (accepted, revision)
    for _ in range(100):
        terms = (
            None
            if rng.randrange(4) == 0
            else {
                "retention_days": rng.choice([0, 30, 3650]),
                "region": rng.choice(["eu", "us", "zz"]),
                "trains_on_inputs": rng.choice([False, True]),
            }
        )
        target = None if terms is None else digest(terms)
        expected_revision = revision if rng.randrange(4) else max(0, revision - 1)
        approved = rng.choice([False, True])
        rq = {
            "install_id": INS,
            "provider_id": PROVIDER,
            "terms": terms,
            "expected_revision": expected_revision,
            "approval": None
            if terms is None or not approved
            else approval(terms, expected_revision),
        }
        before = copy.deepcopy((store._state, rq))
        if expected_revision != revision:
            expected = ("failure", "stale_revision", "conflict")
        elif target == accepted:
            expected = None
        elif target is not None and not approved:
            expected = ("failure", "approval_missing", "approval_required")
        elif target is not None and revision == MAX:
            expected = ("failure", "revision_exhausted", "revision_exhausted")
        else:
            expected = None
        result = outcome(store.transition, module.ByomError, rq)
        if expected:
            assert result == expected
            assert (store._state, rq) == before
        else:
            if target != accepted:
                revision = revision if target is None and revision == MAX else revision + 1
                accepted = target
            record = {
                "install_id": INS,
                "provider_id": PROVIDER,
                "accepted_terms": accepted,
                "revision": revision,
            }
            assert result == ("ok", record)
            assert rq == before[1]
            result[1]["revision"] = -1
            assert store.record(INS, PROVIDER) == record
        current = {
            "retention_days": rng.choice([0, 30, 3650]),
            "region": "eu",
            "trains_on_inputs": False,
        }
        send = {
            "install_id": INS,
            "provider_id": PROVIDER,
            "collection_id": CID,
            "terms": current,
            "payload": {"fen": "~", "task": "weekly-plan", "moves": ["a", "~"], "max-tokens": 1},
        }
        before = copy.deepcopy((store._state, send))
        want = (
            ("failure", "terms_not_accepted", "terms_required")
            if accepted is None
            else (
                ("failure", "terms_changed", "terms_required")
                if accepted != digest(current)
                else ("ok", {"verdict": "allow", "terms_digest": accepted})
            )
        )
        assert outcome(store.send, module.ByomError, send) == want
        assert (store._state, send) == before
        assert cloud.calls[-1] == {"install_id": INS, "collection_id": CID, "destination": "cloud"}


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("revision", [0, MAX - 1, MAX])
def test_seeded_terms_revision_approval_and_send_model(seed, revision):
    trace(production, seed, revision)


@pytest.mark.parametrize(
    "old,new",
    [
        ("if target is not None and not approved:", "if False:"),
        ('if req["expected_revision"] != rev:', "if False:"),
        ("if accepted != digest:", "if False:"),
        ("if accepted is None:", "if False:"),
        ("if rev >= _MAX_REV:", "if rev > _MAX_REV:"),
    ],
)
def test_independent_red_reproductions(old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        for seed in range(5):
            trace(mutant, seed, MAX)


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_crash_never_counts_as_semantic_kill(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.ByomError, {})
