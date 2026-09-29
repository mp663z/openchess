"""Pure, injected identity admission decision for the two public identity operations.

This is a production implementation of the *decision*, not an installed rate
limiter. Callers must supply opaque, trusted-boundary keys, a reviewed policy,
a serializable store snapshot, and an atomic identity-effect transaction. There
is no live middleware or account/source normalizer here. In particular, this
function cannot make concurrent admissions safe without its caller's transaction.
"""

from __future__ import annotations

from copy import deepcopy

_OPERATIONS = frozenset({"identity.register", "identity.login"})
_MAX_INTEGER = 2**53 - 1


class AdmissionRefusal(Exception):
    """Closed internal refusal code; no identifiers or counters in the error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _bad_request() -> None:
    raise AdmissionRefusal("malformed_request")


def _policy_bucket(value: object) -> tuple[int, int]:
    if type(value) is not dict or set(value) != {"capacity", "window_ms"}:
        _bad_request()
    capacity = value["capacity"]
    window_ms = value["window_ms"]
    if any(type(n) is not int or n < 1 or n > _MAX_INTEGER for n in (capacity, window_ms)):
        _bad_request()
    return capacity, window_ms


def decide(
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
    """Return (admit|replay, detached snapshot) or raise AdmissionRefusal.

    The replay shortcut intentionally trusts the upstream idempotency result:
    it does not inspect request, policy, store or state. This is a synthetic
    boundary contract, not a definition of public idempotency-key scope.
    """
    if type(body_same) is not bool:
        _bad_request()
    if type(replay) is not str or replay not in ("new", "cached"):
        _bad_request()
    if replay == "cached":
        if not body_same:
            raise AdmissionRefusal("idempotency_conflict")
        return "replay", deepcopy(state)
    if (
        type(operation) is not str
        or operation not in _OPERATIONS
        or type(account) is not str
        or not account.startswith("opaque:")
        or type(source) is not str
        or not source.startswith("opaque:")
        or type(now) is not int
        or now < 0
        or now > _MAX_INTEGER
    ):
        _bad_request()
    if (
        type(policy) is not dict
        or set(policy) != {"version", "account", "source"}
        or type(policy["version"]) is not int
        or policy["version"] < 1
    ):
        _bad_request()
    limits = {scope: _policy_bucket(policy[scope]) for scope in ("account", "source")}

    if type(state) is not dict:
        raise AdmissionRefusal("internal")
    for key, value in state.items():
        if (
            type(key) is not tuple
            or len(key) != 3
            or type(value) is not tuple
            or len(value) != 2
            or any(type(number) is not int or number < 0 for number in value)
        ):
            raise AdmissionRefusal("internal")
    if type(store_available) is not bool or not store_available:
        raise AdmissionRefusal("internal")

    updates = {}
    for scope, identifier in (("account", account), ("source", source)):
        capacity, duration = limits[scope]
        slot = now // duration
        key = (operation, scope, identifier)
        previous_slot, previous_count = state.get(key, (slot, 0))
        if previous_slot > slot or (previous_slot == slot and previous_count > capacity):
            raise AdmissionRefusal("internal")
        count = previous_count if previous_slot == slot else 0
        if count >= capacity:
            raise AdmissionRefusal("rate_limited")
        updates[key] = (slot, count + 1)

    if type(effect_ok) is not bool or not effect_ok:
        raise AdmissionRefusal("internal")
    result = deepcopy(state)
    result.update(updates)
    return "admit", result
