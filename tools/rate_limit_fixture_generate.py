#!/usr/bin/env python3
# ruff: noqa: E501  (authored contract literals)
"""T0456: deterministic generator for the closed rate-limit fixture.

Rebuilds tests/fixtures/rate-limit/cases.json from the landed public
identity rate-limit reference contract (data/contracts/rate_limit.yaml)
and its operation source (data/contracts/control-plane.yaml). Every
expectation is authored here from the contract's structured rules; the
generator never runs the T0455 synthetic reference decision() or any
implementation to compute an outcome. All corpus values are plain JSON
data: Python-level hostiles (built-in subclasses, NaN/infinity, lone
surrogates, plain objects) are out of representational scope and are
owned by the derived probes in tests/test_t0456_rate_limit_fixture.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "data/contracts/rate_limit.yaml"
SOURCE = "data/contracts/control-plane.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "rate-limit" / "cases.json"
MAX_CLOCK = 2**53 - 1

_CONTRACT_DOC = yaml.safe_load((ROOT / CONTRACT).read_text())
_SOURCE_DOC = yaml.safe_load((ROOT / SOURCE).read_text())

LOGIN = "identity.login"
REGISTER = "identity.register"
ACCOUNT = "account"
SOURCE_SCOPE = "source"
ACC = "opaque:account"
SRC = "opaque:source"
OTHER_ACC = "opaque:other-account"
OTHER_SRC = "opaque:other-source"

# Synthetic injected policies (contract request.policy: injected versioned
# operator policy; unresolved.thresholds keeps production values unset).
W_DAY = MAX_CLOCK // 86400000
W_MAX_BASE = MAX_CLOCK // 100

NOTES = (
    "Closed fixture for the public identity rate-limit reference contract "
    "(data/contracts/rate_limit.yaml), executed against the T0455 test-only "
    "synthetic reference decision() as a separate binding; a later implement "
    "task (T0458) must independently implement the admission decision and "
    "rebind these rows. Expectations are authored from the structured "
    "contract, never computed by the reference. No production semantics are "
    "shipped here: no HTTP middleware, no live enforcement, no account or "
    "source normalization, no production thresholds. Policies are synthetic "
    "injected fixtures per the contract's injected-policy rule "
    "(unresolved.thresholds); opaque: keys are synthetic stand-ins for "
    "normalizer output (unresolved.account_normalization and "
    "unresolved.source_normalization). body_same is validated as an exact "
    "bool before the replay branch, so a cached replay with a nonbool "
    "body_same refuses as malformed. The replay branch skips all request, "
    "policy, state and store validation: a cached replay with a boolean "
    "body_same replays or conflicts without inspecting the operation, keys, "
    "clock, policy, stored records or store availability. The replay "
    "decision does not consult bucket state, and any opaque:-prefixed str "
    "is admitted as a key including bytes a real normalizer would never "
    "emit; these are pinned as the synthetic model's declared behavior, "
    "not production choices. "
    "State records outside the two declared scopes are carried unchanged. "
    "The source document is pinned by source_manifest. All values are "
    "plain JSON data; Python-level hostile objects are out of "
    "representational scope and owned by the fixture battery's derived "
    "probes and the T0455 reference battery."
)


def _pol(account=None, source=None, version=1):
    return {
        "version": version,
        "account": account if account is not None else {"capacity": 2, "window_ms": 100},
        "source": source if source is not None else {"capacity": 3, "window_ms": 100},
    }


def _pol_tight_source():
    return _pol(account={"capacity": 9, "window_ms": 100}, source={"capacity": 1, "window_ms": 100})


def _pol_one():
    return _pol(account={"capacity": 1, "window_ms": 100}, source={"capacity": 1, "window_ms": 100})


def _pol_large():
    big = {"capacity": MAX_CLOCK, "window_ms": 86400000}
    return _pol(account=big, source=dict(big), version=7)


def _pol_fast():
    fast = {"capacity": 3, "window_ms": 1}
    return _pol(account=fast, source=dict(fast))


def rec(operation, scope, opaque, window, count):
    return {
        "operation": operation,
        "scope": scope,
        "opaque": opaque,
        "window": window,
        "count": count,
    }


def call(**overrides):
    base = {
        "operation": LOGIN,
        "account": ACC,
        "source": SRC,
        "now": 0,
        "policy": _pol(),
        "state": [],
        "replay": "new",
        "body_same": True,
        "store_available": True,
        "effect_ok": True,
    }
    base.update(overrides)
    return base


def verdict(name, why, call_, outcome, state):
    return {
        "name": name,
        "why": why,
        "call": call_,
        "expect": {"outcome": outcome, "state": state},
    }


def refusal(name, why, call_, code):
    return {"name": name, "why": why, "call": call_, "expect_failure": code}


def _happy():
    la1 = rec(LOGIN, ACCOUNT, ACC, 0, 1)
    ls1 = rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)
    return [
        verdict(
            "admit-first-empty-login",
            "first attempt on empty buckets admits and charges both scopes once "
            "(contract buckets.atomic, results.admit)",
            call(),
            "admit",
            [la1, ls1],
        ),
        verdict(
            "admit-first-empty-register",
            "register has its own operation-scoped buckets (contract buckets "
            "account_key/source_key include the operation)",
            call(operation=REGISTER),
            "admit",
            [rec(REGISTER, ACCOUNT, ACC, 0, 1), rec(REGISTER, SOURCE_SCOPE, SRC, 0, 1)],
        ),
        verdict(
            "admit-second-slot-both-buckets",
            "a second attempt below capacity increments each bucket by exactly one",
            call(state=[la1, ls1]),
            "admit",
            [rec(LOGIN, ACCOUNT, ACC, 0, 2), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 2)],
        ),
        verdict(
            "admit-other-operation-own-buckets",
            "login buckets at capacity do not gate register: bucket keys are "
            "scoped per operation (contract buckets.independence)",
            call(
                operation=REGISTER,
                state=[
                    rec(LOGIN, ACCOUNT, ACC, 0, 2),
                    rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3),
                ],
            ),
            "admit",
            [
                rec(LOGIN, ACCOUNT, ACC, 0, 2),
                rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3),
                rec(REGISTER, ACCOUNT, ACC, 0, 1),
                rec(REGISTER, SOURCE_SCOPE, SRC, 0, 1),
            ],
        ),
        verdict(
            "admit-other-account-own-bucket",
            "a different canonical account has its own account bucket even when "
            "another account is at capacity",
            call(account=OTHER_ACC, state=[rec(LOGIN, ACCOUNT, ACC, 0, 2)]),
            "admit",
            [
                rec(LOGIN, ACCOUNT, ACC, 0, 2),
                rec(LOGIN, ACCOUNT, OTHER_ACC, 0, 1),
                rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1),
            ],
        ),
        verdict(
            "admit-other-source-own-bucket",
            "a different canonical source has its own source bucket even when "
            "another source is at capacity",
            call(source=OTHER_SRC, state=[rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3)]),
            "admit",
            [
                rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3),
                rec(LOGIN, SOURCE_SCOPE, OTHER_SRC, 0, 1),
                rec(LOGIN, ACCOUNT, ACC, 0, 1),
            ],
        ),
        verdict(
            "admit-policy-version-high",
            "any integer policy version >= 1 is accepted (contract request.policy "
            "is injected and versioned)",
            call(policy=_pol_large()),
            "admit",
            [la1, ls1],
        ),
        verdict(
            "admit-capacity-and-clock-max",
            "capacity and now at the 2**53-1 ceiling admit; the window is the "
            "floor of now / window_ms (contract buckets.algorithm)",
            call(policy=_pol_large(), now=MAX_CLOCK),
            "admit",
            [
                rec(LOGIN, ACCOUNT, ACC, W_DAY, 1),
                rec(LOGIN, SOURCE_SCOPE, SRC, W_DAY, 1),
            ],
        ),
        verdict(
            "admit-window-one-ms",
            "a 1ms window is the minimum window policy; now=5 lands in window 5",
            call(policy=_pol_fast(), now=5),
            "admit",
            [
                rec(LOGIN, ACCOUNT, ACC, 5, 1),
                rec(LOGIN, SOURCE_SCOPE, SRC, 5, 1),
            ],
        ),
        verdict(
            "replay-cached-identical",
            "a byte-identical cached idempotent outcome replays with no counter "
            "change (contract results.replay)",
            call(state=[la1, ls1], replay="cached"),
            "replay",
            [la1, ls1],
        ),
        verdict(
            "replay-cached-empty-state",
            "the synthetic replay decision does not consult bucket state at all: "
            "a cached replay on an empty store replays and charges nothing",
            call(replay="cached"),
            "replay",
            [],
        ),
        verdict(
            "admit-preserves-unrelated-records",
            "admission touches only its two scope buckets; unrelated stored "
            "records pass through bit-identical",
            call(state=[rec(REGISTER, ACCOUNT, ACC, 0, 2)]),
            "admit",
            [rec(REGISTER, ACCOUNT, ACC, 0, 2), la1, ls1],
        ),
    ]


def _boundary():
    full_a = rec(LOGIN, ACCOUNT, ACC, 0, 2)
    full_s = rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3)
    return [
        verdict(
            "admit-last-account-slot",
            "count one below capacity admits and lands exactly at capacity "
            "(contract buckets.boundary: count less than capacity admits)",
            call(state=[rec(LOGIN, ACCOUNT, ACC, 0, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 2)]),
            "admit",
            [rec(LOGIN, ACCOUNT, ACC, 0, 2), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3)],
        ),
        verdict(
            "deny-account-at-capacity",
            "count equal to capacity denies the next attempt and commits "
            "neither counter (contract buckets.boundary, buckets.rejection)",
            call(state=[full_a, rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)]),
            "deny",
            [full_a, rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)],
        ),
        verdict(
            "deny-source-at-capacity",
            "the source bucket denies independently of account headroom "
            "(contract buckets.independence)",
            call(policy=_pol_tight_source(), state=[rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)]),
            "deny",
            [rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)],
        ),
        verdict(
            "deny-account-not-evaded-by-source-rotation",
            "rotating the source cannot evade an account limit; no new source "
            "record is created by the denied attempt",
            call(source=OTHER_SRC, state=[full_a]),
            "deny",
            [full_a],
        ),
        verdict(
            "deny-source-not-evaded-by-account-rotation",
            "rotating the account cannot evade a source limit; no new account "
            "record is created by the denied attempt",
            call(
                policy=_pol_tight_source(),
                account=OTHER_ACC,
                state=[rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)],
            ),
            "deny",
            [rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)],
        ),
        verdict(
            "admit-at-exact-window-start",
            "at the exact new window start the old count does not carry: full "
            "buckets in window 0 admit again at now=window_ms (contract "
            "buckets.boundary)",
            call(now=100, state=[full_a, full_s]),
            "admit",
            [rec(LOGIN, ACCOUNT, ACC, 1, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 1, 1)],
        ),
        verdict(
            "deny-one-tick-before-window-start",
            "one tick before the window rolls the old count still gates",
            call(now=99, state=[full_a, full_s]),
            "deny",
            [full_a, full_s],
        ),
        verdict(
            "admit-same-clock-mid-window",
            "a repeated attempt at the same injected clock stays in the same "
            "window and consumes another slot",
            call(
                now=42, state=[rec(LOGIN, ACCOUNT, ACC, 0, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)]
            ),
            "admit",
            [rec(LOGIN, ACCOUNT, ACC, 0, 2), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 2)],
        ),
        verdict(
            "admit-now-max-clock",
            "now at the 2**53-1 ceiling admits into the floored window",
            call(now=MAX_CLOCK),
            "admit",
            [
                rec(LOGIN, ACCOUNT, ACC, W_MAX_BASE, 1),
                rec(LOGIN, SOURCE_SCOPE, SRC, W_MAX_BASE, 1),
            ],
        ),
        verdict(
            "admit-capacity-one-first-attempt",
            "capacity 1 is the minimum capacity policy and admits the first attempt",
            call(policy=_pol_one()),
            "admit",
            [rec(LOGIN, ACCOUNT, ACC, 0, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)],
        ),
        verdict(
            "deny-capacity-one-second-attempt",
            "capacity 1 denies the second attempt in the same window",
            call(policy=_pol_one(), state=[rec(LOGIN, ACCOUNT, ACC, 0, 1)]),
            "deny",
            [rec(LOGIN, ACCOUNT, ACC, 0, 1)],
        ),
        verdict(
            "replay-at-full-bucket",
            "a cached replay is not gated by bucket state: full buckets replay "
            "with no charge (contract results.replay)",
            call(replay="cached", state=[full_a, full_s]),
            "replay",
            [full_a, full_s],
        ),
        verdict(
            "deny-second-bucket-leaves-first-uncharged",
            "when the source bucket denies, the account bucket that had room "
            "is not charged: both counters commit as one transaction or not "
            "at all (contract buckets.atomic)",
            call(policy=_pol_tight_source(), state=[rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)]),
            "deny",
            [rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)],
        ),
        verdict(
            "admit-old-window-full-count-dropped",
            "full buckets from an old window do not carry into a later window",
            call(now=250, state=[full_a, full_s]),
            "admit",
            [rec(LOGIN, ACCOUNT, ACC, 2, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 2, 1)],
        ),
        verdict(
            "admit-charges-exactly-the-two-scope-buckets",
            "admission increments the account and source buckets by one each "
            "and nothing else in the store",
            call(
                state=[
                    rec(LOGIN, ACCOUNT, ACC, 0, 1),
                    rec(LOGIN, SOURCE_SCOPE, SRC, 0, 2),
                    rec(REGISTER, ACCOUNT, ACC, 0, 2),
                ]
            ),
            "admit",
            [
                rec(LOGIN, ACCOUNT, ACC, 0, 2),
                rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3),
                rec(REGISTER, ACCOUNT, ACC, 0, 2),
            ],
        ),
        verdict(
            "state-unknown-scope-record-preserved",
            "the store snapshot is opaque beyond the two declared scopes: an "
            "unknown-scope record is carried unchanged (the synthetic model "
            "treats stored records as authenticated)",
            call(state=[rec(LOGIN, "jobs", "opaque:jobs", 0, 7)]),
            "admit",
            [
                rec(LOGIN, "jobs", "opaque:jobs", 0, 7),
                rec(LOGIN, ACCOUNT, ACC, 0, 1),
                rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1),
            ],
        ),
    ]


def _malformed():
    corrupt = [rec(LOGIN, ACCOUNT, ACC, -1, 1)]
    return [
        refusal(
            "operation-undeclared",
            "operation must be an exact declared public identity operation (contract request.operation)",
            call(operation="quota.reserve"),
            "malformed_request",
        ),
        refusal(
            "operation-nonstring",
            "operation must be a string",
            call(operation=1),
            "malformed_request",
        ),
        refusal(
            "operation-null",
            "operation must be a string",
            call(operation=None),
            "malformed_request",
        ),
        refusal(
            "account-raw-email",
            "account must be an opaque normalizer key, never a raw email (contract request.canonical_account, privacy.keys)",
            call(account="ada@example.test"),
            "malformed_request",
        ),
        refusal(
            "account-missing-opaque-prefix",
            "account keys carry the opaque: scheme of the synthetic stand-in",
            call(account="account"),
            "malformed_request",
        ),
        refusal(
            "account-nonstring", "account must be a string", call(account=1), "malformed_request"
        ),
        refusal(
            "source-raw-ip",
            "source must be an opaque trusted-edge key, never a raw IP (contract request.canonical_source, privacy.keys)",
            call(source="203.0.113.1"),
            "malformed_request",
        ),
        refusal("source-nonstring", "source must be a string", call(source=1), "malformed_request"),
        refusal(
            "now-bool",
            "now is a nonnegative integer clock; bool is not int (contract request.now_ms)",
            call(now=True),
            "malformed_request",
        ),
        refusal("now-negative", "now is nonnegative", call(now=-1), "malformed_request"),
        refusal("now-float", "now is an integer", call(now=1.5), "malformed_request"),
        refusal("now-string", "now is an integer", call(now="0"), "malformed_request"),
        refusal(
            "now-past-max-clock",
            "now is capped at 2**53-1",
            call(now=MAX_CLOCK + 1),
            "malformed_request",
        ),
        refusal(
            "policy-null",
            "an unconfigured policy refuses closed (contract unresolved.thresholds refuse-unconfigured-policy)",
            call(policy=None),
            "malformed_request",
        ),
        refusal(
            "policy-missing-account",
            "the policy declares both scopes",
            call(policy={"version": 1, "source": {"capacity": 3, "window_ms": 100}}),
            "malformed_request",
        ),
        refusal(
            "policy-extra-key",
            "the policy shape is closed",
            call(
                policy={
                    "version": 1,
                    "account": {"capacity": 2, "window_ms": 100},
                    "source": {"capacity": 3, "window_ms": 100},
                    "billing": {},
                }
            ),
            "malformed_request",
        ),
        refusal(
            "policy-version-zero",
            "policy version is a positive integer",
            call(policy=_pol(version=0)),
            "malformed_request",
        ),
        refusal(
            "policy-version-negative",
            "policy version is a positive integer",
            call(policy=_pol(version=-1)),
            "malformed_request",
        ),
        refusal(
            "policy-version-bool",
            "policy version is an integer; bool is not int",
            call(policy=_pol(version=True)),
            "malformed_request",
        ),
        refusal(
            "policy-capacity-zero",
            "capacities are positive",
            call(policy=_pol(account={"capacity": 0, "window_ms": 100})),
            "malformed_request",
        ),
        refusal(
            "policy-capacity-negative",
            "capacities are positive",
            call(policy=_pol(account={"capacity": -1, "window_ms": 100})),
            "malformed_request",
        ),
        refusal(
            "policy-capacity-bool",
            "capacities are integers; bool is not int",
            call(policy=_pol(account={"capacity": True, "window_ms": 100})),
            "malformed_request",
        ),
        refusal(
            "policy-capacity-float",
            "capacities are integers",
            call(policy=_pol(account={"capacity": 2.5, "window_ms": 100})),
            "malformed_request",
        ),
        refusal(
            "policy-capacity-past-max-clock",
            "capacities are capped at 2**53-1",
            call(policy=_pol(account={"capacity": MAX_CLOCK + 1, "window_ms": 100})),
            "malformed_request",
        ),
        refusal(
            "policy-window-zero",
            "windows are positive",
            call(policy=_pol(source={"capacity": 3, "window_ms": 0})),
            "malformed_request",
        ),
        refusal(
            "policy-window-past-max-clock",
            "windows are capped at 2**53-1",
            call(policy=_pol(source={"capacity": 3, "window_ms": MAX_CLOCK + 1})),
            "malformed_request",
        ),
        refusal(
            "policy-config-missing-window",
            "each scope config declares capacity and window_ms",
            call(policy=_pol(account={"capacity": 2})),
            "malformed_request",
        ),
        refusal(
            "policy-config-extra-key",
            "each scope config shape is closed",
            call(policy=_pol(account={"capacity": 2, "window_ms": 100, "burst": 1})),
            "malformed_request",
        ),
        refusal(
            "replay-token-undeclared",
            "idempotency resolution is new or cached (contract request.idempotency_resolution)",
            call(replay="maybe"),
            "malformed_request",
        ),
        refusal(
            "replay-token-nonstring",
            "idempotency resolution is a string token",
            call(replay=0),
            "malformed_request",
        ),
        refusal(
            "body-same-nonbool-int",
            "body-same is a boolean; the synthetic model validates its exact type before the replay branch",
            call(body_same=1),
            "malformed_request",
        ),
        refusal(
            "body-same-nonbool-null",
            "body-same is a boolean; the synthetic model validates its exact type before the replay branch",
            call(body_same=None),
            "malformed_request",
        ),
        refusal(
            "body-same-nonbool-int-cached",
            "body-same is validated as an exact bool before the replay branch: a cached replay with a truthy nonbool refuses as malformed instead of replaying by truthiness",
            call(replay="cached", body_same=1),
            "malformed_request",
        ),
        refusal(
            "body-same-nonbool-zero-cached",
            "body-same is validated as an exact bool before the replay branch: a cached replay with a falsy nonbool refuses as malformed instead of conflicting by truthiness",
            call(replay="cached", body_same=0),
            "malformed_request",
        ),
        refusal(
            "body-same-nonbool-string-cached",
            "body-same is validated as an exact bool before the replay branch: a cached replay with a string refuses as malformed",
            call(replay="cached", body_same="yes"),
            "malformed_request",
        ),
        refusal(
            "body-same-nonbool-list-cached",
            "body-same is validated as an exact bool before the replay branch: a cached replay with a list refuses as malformed",
            call(replay="cached", body_same=[1]),
            "malformed_request",
        ),
        refusal(
            "body-same-nonbool-null-cached",
            "body-same is validated as an exact bool before the replay branch: a cached replay with null refuses as malformed",
            call(replay="cached", body_same=None),
            "malformed_request",
        ),
        refusal(
            "malformed-operation-with-corrupt-state",
            "request shape is validated before store state (synthetic validation order)",
            call(operation=1, state=corrupt),
            "malformed_request",
        ),
        refusal(
            "malformed-policy-with-corrupt-state",
            "policy is validated before store state (synthetic validation order)",
            call(policy=None, state=corrupt),
            "malformed_request",
        ),
        refusal(
            "conflict-cached-different-body",
            "the same key with a different canonical body conflicts with no charge (contract results.conflict)",
            call(replay="cached", body_same=False),
            "idempotency_conflict",
        ),
        refusal(
            "conflict-at-full-bucket",
            "conflict resolution precedes bucket admission: a conflicting replay at full buckets conflicts rather than denying",
            call(replay="cached", body_same=False, state=[rec(LOGIN, ACCOUNT, ACC, 0, 2)]),
            "idempotency_conflict",
        ),
        refusal(
            "conflict-with-malformed-operation",
            "conflict resolution precedes request-shape validation (synthetic validation order)",
            call(operation=1, replay="cached", body_same=False),
            "idempotency_conflict",
        ),
        refusal(
            "store-unavailable",
            "an unavailable store refuses closed with no identity effect (contract buckets.persistence)",
            call(store_available=False),
            "internal",
        ),
        refusal(
            "store-unavailable-at-full-bucket",
            "store availability precedes bucket admission: an outage at full buckets is internal, not a denial",
            call(store_available=False, state=[rec(LOGIN, ACCOUNT, ACC, 0, 2)]),
            "internal",
        ),
        refusal(
            "effect-failure",
            "a transaction failure commits neither counter (contract buckets.atomic, results.failure)",
            call(effect_ok=False),
            "internal",
        ),
        refusal(
            "state-record-count-bool",
            "stored counts are exact integers; bool is not int (contract buckets.persistence corrupt store refuses)",
            call(state=[rec(LOGIN, ACCOUNT, ACC, 0, True)]),
            "internal",
        ),
        refusal(
            "state-record-count-negative",
            "stored counts are nonnegative",
            call(state=[rec(LOGIN, ACCOUNT, ACC, 0, -1)]),
            "internal",
        ),
        refusal(
            "state-record-window-negative",
            "stored windows are nonnegative",
            call(state=[rec(LOGIN, ACCOUNT, ACC, -1, 1)]),
            "internal",
        ),
        refusal(
            "state-record-future-window",
            "a stored window ahead of the injected clock is a regression and refuses closed",
            call(state=[rec(LOGIN, ACCOUNT, ACC, 5, 1)]),
            "internal",
        ),
        refusal(
            "state-record-count-over-capacity",
            "a stored count above the policy capacity in the active window is corrupt and refuses closed",
            call(state=[rec(LOGIN, ACCOUNT, ACC, 0, 3)]),
            "internal",
        ),
    ]


def _rollback():
    s1 = [rec(LOGIN, ACCOUNT, ACC, 0, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 1)]
    s2 = [rec(LOGIN, ACCOUNT, ACC, 0, 2), rec(LOGIN, SOURCE_SCOPE, SRC, 0, 2)]
    w1 = [rec(LOGIN, ACCOUNT, ACC, 1, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 1, 1)]
    w1b = [rec(LOGIN, ACCOUNT, ACC, 1, 2), rec(LOGIN, SOURCE_SCOPE, SRC, 1, 2)]
    w2 = [rec(LOGIN, ACCOUNT, ACC, 2, 1), rec(LOGIN, SOURCE_SCOPE, SRC, 2, 1)]

    def step(call_, outcome=None, state=None, failure=None):
        if failure is not None:
            return {"call": call_, "expect_failure": failure}
        return {"call": call_, "expect": {"outcome": outcome, "state": state}}

    return [
        {
            "name": "admit-deny-replay-readmit-chain",
            "why": "a pinned admit/deny/replay chain: denial and replay commit "
            "nothing, and the next window admits from the pinned state",
            "calls": [
                step(call(), "admit", s1),
                step(call(state=s1), "admit", s2),
                step(call(state=s2), "deny", s2),
                step(call(state=s2, replay="cached"), "replay", s2),
                step(call(state=s2, now=100), "admit", w1),
            ],
        },
        {
            "name": "conflict-then-replay-then-admit-chain",
            "why": "conflict and replay consume no slot: the follow-up admit "
            "lands exactly at the pinned count",
            "calls": [
                step(call(), "admit", s1),
                step(
                    call(state=s1, replay="cached", body_same=False), failure="idempotency_conflict"
                ),
                step(call(state=s1, replay="cached"), "replay", s1),
                step(call(state=s1), "admit", s2),
                step(call(state=s2), "deny", s2),
            ],
        },
        {
            "name": "store-down-then-recovered-chain",
            "why": "an outage refuses closed and poisons nothing: the "
            "recovered store admits from the pre-outage state",
            "calls": [
                step(call(), "admit", s1),
                step(call(state=s1, store_available=False), failure="internal"),
                step(call(state=s1), "admit", s2),
            ],
        },
        {
            "name": "effect-failure-then-success-chain",
            "why": "a transaction failure commits neither counter: the retry "
            "admits against the untouched state",
            "calls": [
                step(call(effect_ok=False), failure="internal"),
                step(call(), "admit", s1),
            ],
        },
        {
            "name": "malformed-then-valid-unchanged-chain",
            "why": "a malformed refusal leaves the store bit-identical for the valid follow-up",
            "calls": [
                step(call(now=-1), failure="malformed_request"),
                step(call(), "admit", s1),
            ],
        },
        {
            "name": "replay-never-double-charges-chain",
            "why": "repeated cached replays never consume slots: the next new "
            "attempt takes exactly the second slot",
            "calls": [
                step(call(), "admit", s1),
                step(call(state=s1, replay="cached"), "replay", s1),
                step(call(state=s1, replay="cached"), "replay", s1),
                step(call(state=s1), "admit", s2),
            ],
        },
        {
            "name": "window-rollover-chain",
            "why": "a pinned walk across window boundaries: same-window "
            "attempts accumulate, the boundary tick resets, old counts never "
            "carry",
            "calls": [
                step(call(now=0), "admit", s1),
                step(call(now=99, state=s1), "admit", s2),
                step(call(now=99, state=s2), "deny", s2),
                step(call(now=100, state=s2), "admit", w1),
                step(call(now=199, state=w1), "admit", w1b),
                step(call(now=200, state=w1b), "admit", w2),
            ],
        },
        {
            "name": "operation-scope-interleave-chain",
            "why": "login and register buckets interleave independently on "
            "one store; each operation denies only on its own counters",
            "calls": [
                step(call(), "admit", s1),
                step(
                    call(operation=REGISTER, state=s1),
                    "admit",
                    [
                        *s1,
                        rec(REGISTER, ACCOUNT, ACC, 0, 1),
                        rec(REGISTER, SOURCE_SCOPE, SRC, 0, 1),
                    ],
                ),
                step(
                    call(
                        state=[
                            *s1,
                            rec(REGISTER, ACCOUNT, ACC, 0, 1),
                            rec(REGISTER, SOURCE_SCOPE, SRC, 0, 1),
                        ]
                    ),
                    "admit",
                    [
                        *s2,
                        rec(REGISTER, ACCOUNT, ACC, 0, 1),
                        rec(REGISTER, SOURCE_SCOPE, SRC, 0, 1),
                    ],
                ),
                step(
                    call(
                        operation=REGISTER,
                        state=[
                            *s2,
                            rec(REGISTER, ACCOUNT, ACC, 0, 1),
                            rec(REGISTER, SOURCE_SCOPE, SRC, 0, 1),
                        ],
                    ),
                    "admit",
                    [
                        *s2,
                        rec(REGISTER, ACCOUNT, ACC, 0, 2),
                        rec(REGISTER, SOURCE_SCOPE, SRC, 0, 2),
                    ],
                ),
                step(
                    call(
                        state=[
                            *s2,
                            rec(REGISTER, ACCOUNT, ACC, 0, 2),
                            rec(REGISTER, SOURCE_SCOPE, SRC, 0, 2),
                        ]
                    ),
                    "deny",
                    [
                        *s2,
                        rec(REGISTER, ACCOUNT, ACC, 0, 2),
                        rec(REGISTER, SOURCE_SCOPE, SRC, 0, 2),
                    ],
                ),
                step(
                    call(
                        operation=REGISTER,
                        state=[
                            *s2,
                            rec(REGISTER, ACCOUNT, ACC, 0, 2),
                            rec(REGISTER, SOURCE_SCOPE, SRC, 0, 2),
                        ],
                    ),
                    "deny",
                    [
                        *s2,
                        rec(REGISTER, ACCOUNT, ACC, 0, 2),
                        rec(REGISTER, SOURCE_SCOPE, SRC, 0, 2),
                    ],
                ),
            ],
        },
        {
            "name": "rotation-after-deny-chain",
            "why": "after an account denial, source rotation still denies and "
            "creates no record, while a genuinely different account admits "
            "and charges only its own buckets",
            "calls": [
                step(call(), "admit", s1),
                step(call(state=s1), "admit", s2),
                step(call(state=s2), "deny", s2),
                step(call(state=s2, source=OTHER_SRC), "deny", s2),
                step(
                    call(state=s2, account=OTHER_ACC),
                    "admit",
                    [
                        rec(LOGIN, ACCOUNT, ACC, 0, 2),
                        rec(LOGIN, ACCOUNT, OTHER_ACC, 0, 1),
                        rec(LOGIN, SOURCE_SCOPE, SRC, 0, 3),
                    ],
                ),
            ],
        },
        {
            "name": "failure-mid-chain-poisons-nothing",
            "why": "an internal failure, a conflict and a malformed refusal "
            "in sequence leave the pinned state for the valid follow-up",
            "calls": [
                step(call(), "admit", s1),
                step(call(state=s1, store_available=False), failure="internal"),
                step(
                    call(state=s1, replay="cached", body_same=False), failure="idempotency_conflict"
                ),
                step(call(state=s1, now=True), failure="malformed_request"),
                step(call(state=s1), "admit", s2),
            ],
        },
    ]


def _canon(case):
    return json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def build():
    sections = {
        section: sorted(rows, key=lambda row: row["name"])
        for section, rows in (
            ("happy", _happy()),
            ("boundary", _boundary()),
            ("malformed", _malformed()),
            ("rollback", _rollback()),
        )
    }
    cases = {
        "schema_version": 1,
        "contract": CONTRACT,
        "contract_schema_version": _CONTRACT_DOC["schema_version"],
        "source": SOURCE,
        "source_manifest": hashlib.sha256(_canon(_SOURCE_DOC)).hexdigest(),
        "notes": NOTES,
        "section_manifests": {
            section: {row["name"]: hashlib.sha256(_canon(row)).hexdigest() for row in rows}
            for section, rows in sections.items()
        },
        **sections,
    }
    return cases


def dump(cases):
    return json.dumps(cases, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main():
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(dump(build()))
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
