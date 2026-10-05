"""T0449: production classifier binding checks beyond the T0448 battery.

server.control_plane_errors must agree verdict-for-verdict with the T0446 test
reference on a seeded hostile corpus (the reference stays the oracle; production
is independently written), never mutate inputs, and stay linear on FEN-like and
token-run strings.
"""

from __future__ import annotations

import contextlib
import copy
import random
import time

import pytest

from server import control_plane_errors as production
from tests import test_t0446_control_plane_errors_contract as ref

SOURCE = ref.SOURCE
LEAVES = [
    None, True, 0, 7, -1, 2**60, 1.5, float("nan"), "", "ok", "hello world", "x" * 31, "x" * 32,
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR", "a\ud800b", "key_material", b"bytes", (1,),
]  # fmt: skip
KEYS = ["a", "b", "game", "Moves", "fenString", "provider_key", "token", "x" * 32, "note1", 3]


def _tree(rng, depth=0):
    if depth > 3 or rng.random() < 0.4:
        return rng.choice(LEAVES)
    if rng.random() < 0.5:
        return [_tree(rng, depth + 1) for _ in range(rng.randrange(3))]
    return {rng.choice(KEYS): _tree(rng, depth + 1) for _ in range(rng.randrange(4))}


def _payload(rng):
    error = {"code": rng.choice(["not_found", "internal", "bogus", 1]),
             "message": rng.choice(["m", "", 5, "key_material!", "x" * 32]),
             "retryable": rng.choice([True, False, 0])}  # fmt: skip
    if rng.random() < 0.3:
        error.pop(rng.choice(sorted(error)))
    body = {"error": error} if rng.random() < 0.9 else {}
    for _ in range(rng.randrange(3)):
        body[rng.choice(KEYS[:-1])] = _tree(rng)
    return body


def args_operation_guard(args):
    """Only the operation-parse guard may carry a context."""
    return type(args[3]) is str


def _outcome(binding, args):
    decide, error = binding
    try:
        return decide(*args)
    except error as exc:
        if error is production.ErrorsError:
            assert exc.__cause__ is None
            assert exc.__context__ is None or args_operation_guard(args)
        return ("refused", exc.failure_class, exc.code, exc.retryable)


@pytest.mark.parametrize("seed", [449, 4490, 20261005])
def test_agrees_with_reference_on_seeded_hostile_corpus(seed):
    rng = random.Random(seed)
    for _ in range(600):
        status = rng.choice([200, 204, 299, 400, 404, 500, 599, 0, 100, 300, 600, True, "400"])
        operation = rng.choice([None, None, "bogus", 3, "a.b.c"])
        payload = _payload(rng)
        before = copy.deepcopy(payload)
        args = (SOURCE, status, payload, operation)
        got = _outcome((production.classify, production.ErrorsError), args)
        want = _outcome((ref.classify, ref.ErrorsError), args)
        assert repr(got) == repr(want), (seed, status, operation, before)
        assert repr(payload) == repr(before)
        if type(got) is dict:
            assert got is not payload and got.get("kind") in ("success", "error")


def test_refusal_is_fresh_typed_and_not_a_cached_instance():
    seen = []
    for _ in range(2):
        with pytest.raises(production.ErrorsError) as caught:
            production.classify(SOURCE, 0, {})
        seen.append(caught.value)
    assert seen[0] is not seen[1]
    assert str(seen[0]) == "invalid error result"
    assert (seen[0].failure_class, seen[0].code, seen[0].retryable) == (
        "malformed_error_result",
        "malformed_request",
        False,
    )


def test_hostile_source_types_refuse_without_running_user_code():
    class Boom(dict):
        def __getitem__(self, key):
            raise RuntimeError("user code ran")

        def get(self, *a):
            raise RuntimeError("user code ran")

    for bad in (None, [], Boom(), {"schema_version": True}, 3):
        with pytest.raises(production.ErrorsError):
            production.classify(bad, 400, {"error": {}})


@pytest.mark.parametrize(
    "text", ["/" * 200000, "1/" * 100000, "8/8/8/8/8/8/8/" * 20000, "A" * 400000]
)
def test_adversarial_strings_stay_linear(text):
    start = time.perf_counter()
    with contextlib.suppress(production.ErrorsError):
        production.classify(
            SOURCE, 500, {"error": {"code": "internal", "message": text, "retryable": True}}
        )
    assert time.perf_counter() - start < 3
