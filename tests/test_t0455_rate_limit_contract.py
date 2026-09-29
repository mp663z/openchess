"""T0455 synthetic reference battery. No production identity rate limiting is claimed."""

from __future__ import annotations

import copy

import pytest
import yaml

from tools.rate_limit_contract_lint import CONTRACT, SOURCE, lint
from tools.variant_contract_lint import ContractError

OPS = ("identity.register", "identity.login")
MAX_CLOCK = 2**53 - 1


class Refusal(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def decision(
    *,
    operation,
    account,
    source,
    now,
    policy,
    state,
    replay="new",
    body_same=True,
    store_available=True,
    effect_ok=True,
):
    """Synthetic transactional model: returns (result, detached new state).

    body_same is validated as an exact bool before the replay branch, so a
    cached replay with a nonbool body_same refuses as malformed instead of
    being coerced by truthiness. The replay branch itself deliberately skips
    all request, policy, state and store validation; that skip is declared
    and pinned in the T0456 fixture notes.
    """
    if type(body_same) is not bool:
        raise Refusal("malformed_request")
    if replay == "cached" and body_same:
        return "replay", copy.deepcopy(state)
    if replay == "cached" and not body_same:
        raise Refusal("idempotency_conflict")
    if replay != "new":
        raise Refusal("malformed_request")
    if (
        type(operation) is not str
        or operation not in OPS
        or type(account) is not str
        or not account.startswith("opaque:")
        or type(source) is not str
        or not source.startswith("opaque:")
        or type(now) is not int
        or not 0 <= now <= MAX_CLOCK
    ):
        raise Refusal("malformed_request")
    if (
        type(policy) is not dict
        or set(policy) != {"version", "account", "source"}
        or type(policy["version"]) is not int
        or policy["version"] < 1
    ):
        raise Refusal("malformed_request")
    for label in ("account", "source"):
        cfg = policy[label]
        if (
            type(cfg) is not dict
            or set(cfg) != {"capacity", "window_ms"}
            or any(
                type(cfg[k]) is not int or not 1 <= cfg[k] <= MAX_CLOCK
                for k in ("capacity", "window_ms")
            )
        ):
            raise Refusal("malformed_request")
    if type(state) is not dict or any(
        type(k) is not tuple
        or len(k) != 3
        or type(v) is not tuple
        or len(v) != 2
        or any(type(n) is not int or n < 0 for n in v)
        for k, v in state.items()
    ):
        raise Refusal("internal")
    if not store_available:
        raise Refusal("internal")
    pending = {}
    for label, opaque in (("account", account), ("source", source)):
        cfg = policy[label]
        key = (operation, label, opaque)
        window = now // cfg["window_ms"]
        old_window, count = state.get(key, (window, 0))
        if old_window > window or (old_window == window and count > cfg["capacity"]):
            raise Refusal("internal")
        active = count if old_window == window else 0
        if active >= cfg["capacity"]:
            raise Refusal("rate_limited")
        pending[key] = (window, active + 1)
    if not effect_ok:
        raise Refusal("internal")
    new_state = copy.deepcopy(state)
    new_state.update(pending)
    return "admit", new_state


def params(**kw):
    p = dict(
        operation="identity.login",
        account="opaque:account",
        source="opaque:source",
        now=0,
        policy={
            "version": 1,
            "account": {"capacity": 2, "window_ms": 100},
            "source": {"capacity": 3, "window_ms": 100},
        },
        state={},
    )
    p.update(kw)
    return p


def test_lint_and_links():
    lint()
    doc = yaml.safe_load(CONTRACT.read_text())["contract"]
    assert set(doc["unresolved"]) == {
        "account_normalization",
        "source_normalization",
        "thresholds",
        "policy_migration",
        "abuse_tradeoffs",
        "response_transport",
        "idempotency_scope",
    }
    assert doc["role"]["status"] == "reference-only-no-live-enforcement-claimed"


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["contract"]["role"].update(scope=["quota.reserve"]),
        lambda d: d["contract"]["buckets"].update(atomic="one-bucket-only"),
        lambda d: d["contract"]["unresolved"].pop("idempotency_scope"),
        lambda d: d.update(schema_version=True),
        lambda d: d["contract"].update(hidden="policy"),
    ],
)
def test_normative_mutants_fail(tmp_path, edit):
    doc = yaml.safe_load(CONTRACT.read_text())
    edit(doc)
    path = tmp_path / "rate.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(path)


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s["areas"]["identity"]["ops"]["login"].update(errors=["internal"]),
        lambda s: s["contract"]["transport"]["auth"].update(
            public_operations=["identity.register"]
        ),
        lambda s: s["contract"]["transport"]["errors"]["shape"]["error"]["fields"].pop("retryable"),
        lambda s: s["contract"]["transport"].update(idempotency="not required"),
        lambda s: s["contract"]["privacy"].update(logs="all-secrets"),
    ],
)
def test_source_mutants_fail(tmp_path, edit):
    source = yaml.safe_load(SOURCE.read_text())
    edit(source)
    path = tmp_path / "source.yaml"
    path.write_text(yaml.safe_dump(source))
    with pytest.raises(ContractError):
        lint(source_path=path)


@pytest.mark.parametrize("operation", OPS)
def test_happy_count_boundary_and_restart(operation):
    p = params(operation=operation)
    untouched = copy.deepcopy(p)
    outcome, first = decision(**p)
    assert outcome == "admit" and p == untouched and first is not p["state"]
    second = decision(**params(operation=operation, state=first))[1]
    with pytest.raises(Refusal, match="rate_limited"):
        decision(**params(operation=operation, state=copy.deepcopy(second)))
    assert decision(**params(operation=operation, state=second, now=100))[0] == "admit"
    # Source rotation cannot evade the account bucket.
    with pytest.raises(Refusal, match="rate_limited"):
        decision(**params(operation=operation, state=second, source="opaque:other"))
    # A different operation has its own scope.
    other = next(x for x in OPS if x != operation)
    assert decision(**params(operation=other, state=second))[0] == "admit"


def test_source_bucket_is_separate_across_accounts():
    state = {}
    for i in range(3):
        state = decision(**params(account=f"opaque:a{i}", state=state))[1]
    with pytest.raises(Refusal, match="rate_limited"):
        decision(**params(account="opaque:a3", state=state))
    assert decision(**params(account="opaque:a3", source="opaque:other", state=state))[0] == "admit"


@pytest.mark.parametrize(
    "mutation",
    [
        {"operation": "quota.reserve"},
        {"account": "raw@example.test"},
        {"source": "203.0.113.1"},
        {"now": True},
        {"now": -1},
        {"policy": None},
        {
            "policy": {
                "version": 1,
                "account": {"capacity": 0, "window_ms": 1},
                "source": {"capacity": 1, "window_ms": 1},
            }
        },
    ],
)
def test_malformed_refuses_without_effect(mutation):
    p = params(**mutation)
    original = copy.deepcopy(p)
    with pytest.raises(Refusal, match="malformed_request"):
        decision(**p)
    assert p == original


@pytest.mark.parametrize(
    "flags,code",
    [
        ({"replay": "cached", "body_same": False}, "idempotency_conflict"),
        ({"store_available": False}, "internal"),
        ({"effect_ok": False}, "internal"),
    ],
)
def test_failure_atomicity(flags, code):
    p = params(**flags)
    before = copy.deepcopy(p)
    with pytest.raises(Refusal, match=code):
        decision(**p)
    assert p == before


@pytest.mark.parametrize("body_same", [1, 0, "yes", "", None, [1], 1.5])
def test_cached_replay_validates_body_same_before_the_replay_branch(body_same):
    """A cached replay with a nonbool body_same refuses as malformed: the
    exact-bool check fires before the replay branch, so truthiness never
    coerces a replay or a conflict."""
    p = params(replay="cached", body_same=body_same)
    original = copy.deepcopy(p)
    with pytest.raises(Refusal, match="malformed_request"):
        decision(**p)
    assert p == original


def test_cached_replay_does_not_consume_or_remint():
    state = decision(**params())[1]
    kind, replay_state = decision(**params(state=state, replay="cached"))
    assert kind == "replay" and replay_state == state and replay_state is not state


def test_corrupt_and_regressed_state_fail_closed():
    state = decision(**params())[1]
    key = next(iter(state))
    state[key] = (1, 1)
    with pytest.raises(Refusal, match="internal"):
        decision(**params(state=state, now=0))


def test_no_default_policy_and_no_public_retry_guarantee():
    doc = yaml.safe_load(CONTRACT.read_text())["contract"]
    assert "refuse-unconfigured-policy" in doc["unresolved"]["thresholds"]
    assert "unmapped" in doc["unresolved"]["response_transport"]
    assert not any("retry_after" in str(value) for value in doc.values())


def test_second_bucket_denial_rolls_back_first_bucket():
    source_limited = params(
        policy={
            "version": 1,
            "account": {"capacity": 9, "window_ms": 100},
            "source": {"capacity": 1, "window_ms": 100},
        }
    )
    source_limited["state"] = decision(**source_limited)[1]
    frozen = copy.deepcopy(source_limited["state"])
    with pytest.raises(Refusal, match="rate_limited"):
        decision(**source_limited)
    assert source_limited["state"] == frozen


def test_exact_window_edge_and_same_clock():
    state = decision(**params(now=99))[1]
    state = decision(**params(now=99, state=state))[1]
    with pytest.raises(Refusal, match="rate_limited"):
        decision(**params(now=99, state=state))
    assert decision(**params(now=100, state=state))[0] == "admit"


def test_unconfigured_and_hostile_state_refuse():
    with pytest.raises(Refusal, match="malformed_request"):
        decision(**params(policy=None))
    with pytest.raises(Refusal, match="internal"):
        decision(**params(state={("identity.login", "account", "opaque:a"): (0, True)}))


def test_policy_state_is_not_a_rollout_migration():
    doc = yaml.safe_load(CONTRACT.read_text())["contract"]
    assert "no-reset-by-default" in doc["unresolved"]["policy_migration"]
    assert "no-claim-of-fixed-retry-time" in doc["unresolved"]["response_transport"]
