"""T0442: deterministic bounded fuzz and injected-fault depth for the shipped
control-plane version comparator (server.control_plane_versioning).

Ground: data/contracts/control-plane.yaml's contract.versioning - clients pin
MAJOR; MINOR is additive-only (new optional request fields, new response
fields, new operations or new provider_kind values); a server MUST accept any
client minor within its major; a consumer may roll back to any earlier MINOR
within the same MAJOR without migration because extra response fields are
never asserted absent; MAJOR bumps require a new base path. The closed T0438
fixture, the T0439 red battery, the T0440 production tests and the T0441
seeded properties on the shipped source document are the baseline.

This battery goes past T0441 in five ways: documents are GENERATED from the
contract grammar rather than mutated from the shipped source; multi-step
MINOR evolution chains are checked end to end with the rollback direction
refused at every backward step; hostile malformed snapshots are fuzzed across
slots; synthetic faults are injected into the validation and comparison
pipeline; and behavioral comparator mutants are killed by a measured harness.
Every loop is bounded and seeded; every expectation is fixed by construction
or by an independently restated grammar predicate, never by production
output. T0443 owns integration/restart; this battery stays on the pure
comparator boundary. It is a test-only battery - no production semantics
change.
"""

from __future__ import annotations

import copy
import random
import re
import string
from pathlib import Path

import pytest
import yaml

from server import control_plane_versioning as cpv
from server.control_plane_versioning import VersionError, compare
from tests.test_t0419_openapi_contract import derive as strict_source_derivation

ROOT = Path(__file__).resolve().parents[1]
CLOSED_ENUM = yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text())["contract"][
    "transport"
]["errors"]["closed_enum"]
SEEDS = (7, 42, 1337, 90210)
CEILING = 2147483647
MAX_EXAMPLE_INT = 2**53 - 1
FIELD_KINDS = ("string", "integer", "number", "boolean", "array", "object")
SCALAR_KINDS = ("string", "integer", "number", "boolean", "array")
ITEM_KINDS = ("string", "integer", "number", "boolean")
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}
# The chess-content vocabulary of the privacy rule ("No games, moves, FENs,
# analysis or notes in any field"), pinned as the shipped field-name rule.
BANNED_TOKENS = frozenset(
    {
        "game",
        "games",
        "move",
        "moves",
        "fen",
        "pgn",
        "san",
        "uci",
        "analysis",
        "note",
        "notes",
        "position",
        "board",
        "eval",
    }
)
NAME_ALPHABET = string.ascii_letters + string.digits + "_ .\u00e9\n"
LOWER = string.ascii_lowercase


def attest(doc):
    """The independent T0419 strict derivation attests source validity."""
    assert strict_source_derivation(doc)["openapi"] == "3.1.0"


def _letters(number):
    """Base-26 lowercase encoding: area and op names take letters only."""
    out = ""
    number += 1
    while number:
        number, rem = divmod(number - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def _gen_example(rng, kind, item):
    if kind == "string":
        return rng.choice(("", "x", "value", "a" * 64))
    if kind == "integer":
        return rng.choice((0, -1, 1, MAX_EXAMPLE_INT, -MAX_EXAMPLE_INT))
    if kind == "number":
        return rng.choice((0, 1, 0.5, -2.25))
    if kind == "boolean":
        return rng.randrange(2) == 0
    return [_gen_example(rng, item, None) for _ in range(rng.randrange(3))]


def _gen_fields(rng, tag, depth, side):
    fields = {}
    for index in range(rng.randrange(4)):
        kind = rng.choice(FIELD_KINDS if depth < 2 else SCALAR_KINDS)
        spec = {"type": kind, "required": rng.randrange(2) == 0}
        if kind == "array":
            spec["items"] = rng.choice(ITEM_KINDS)
            if rng.randrange(3) == 0:
                spec["example"] = _gen_example(rng, "array", spec["items"])
        elif kind == "object":
            spec["fields"] = _gen_fields(rng, f"{tag}n", depth + 1, side)
        elif rng.randrange(3) == 0:
            spec["example"] = _gen_example(rng, kind, None)
        if side == "request" and kind != "object" and rng.randrange(8) == 0:
            spec["write_only"] = True
        fields[f"f{tag}{_letters(index)}"] = spec
    return fields


def _gen_op(rng, tag, path_token, closed):
    method = rng.choice(("GET", "POST"))
    return {
        "method": method,
        "path": f"/p{path_token}",
        "auth": rng.choice(("public", "required")),
        "mutating": method == "POST" and rng.randrange(2) == 0,
        "request": {"fields": {} if method == "GET" else _gen_fields(rng, f"{tag}q", 1, "request")},
        "response": {"fields": _gen_fields(rng, f"{tag}s", 1, "response")},
        "errors": rng.sample(closed, rng.randrange(1, min(3, len(closed)) + 1)),
    }


def random_document(rng, tag):
    """A valid schema-v2 source document built from the contract grammar,
    not derived from the shipped control-plane.yaml."""
    closed = rng.sample(CLOSED_ENUM, rng.randrange(2, min(6, len(CLOSED_ENUM)) + 1))
    areas = {}
    used_paths = set()
    for area_index in range(rng.randrange(1, 4)):
        area_tag = f"{tag}{_letters(area_index)}"
        ops = {}
        for op_index in range(rng.randrange(1, 4)):
            while True:
                token = f"{area_index}{op_index}{tag}{rng.randrange(1000)}"
                if token not in used_paths:
                    used_paths.add(token)
                    break
            op_tag = f"{area_tag}{_letters(op_index)}"
            ops[f"op{_letters(op_index)}"] = _gen_op(rng, op_tag, token, closed)
        areas[f"area{area_tag}"] = {"ops": ops}
    doc = {
        "schema_version": 2,
        "contract": {
            "name": "replaceable-control-plane",
            "versioning": {
                "scheme": "semver",
                "base_path": f"/cp/v{rng.choice((1, 2, 3, 17, 99))}",
            },
            "transport": {
                "auth": {"public_operations": []},
                "read_only_operations": [],
                "errors": {"shape": copy.deepcopy(ERROR_SHAPE), "closed_enum": closed},
            },
            "privacy": {"logs": f"policy-{tag}"},
        },
        "areas": areas,
    }
    _sync_allowlists(doc)
    return doc


def _all_ops(doc):
    return [
        (area, name, spec)
        for area, group in doc["areas"].items()
        for name, spec in group["ops"].items()
    ]


def _sync_allowlists(doc):
    transport = doc["contract"]["transport"]
    transport["auth"]["public_operations"] = [
        f"{area}.{name}" for area, name, spec in _all_ops(doc) if spec["auth"] == "public"
    ]
    transport["read_only_operations"] = [
        f"{area}.{name}" for area, name, spec in _all_ops(doc) if not spec["mutating"]
    ]


def _field_sides(doc, *, with_fields=False):
    sides = []
    for _area, _name, spec in _all_ops(doc):
        for side in ("request", "response"):
            if spec["method"] == "GET" and side == "request":
                continue
            if with_fields and not spec[side]["fields"]:
                continue
            sides.append(spec[side]["fields"])
    return sides


def _unique_path(doc, rng, token):
    used = {spec["path"] for _area, _name, spec in _all_ops(doc)}
    candidate = f"/x{token}"
    while candidate in used:
        candidate = f"/x{token}{rng.randrange(1000)}"
    return candidate


def _new_op(rng, doc, token, closed):
    method = rng.choice(("GET", "POST"))
    return {
        "method": method,
        "path": _unique_path(doc, rng, token),
        "auth": rng.choice(("public", "required")),
        "mutating": method == "POST" and rng.randrange(2) == 0,
        "request": {"fields": {}},
        "response": {"fields": {}},
        "errors": rng.sample(closed, rng.randrange(1, min(2, len(closed)) + 1)),
    }


def evolve(rng, doc, tag):
    """One legal additive MINOR step: only optional fields, new operations
    or new areas are added; every existing declaration is preserved."""
    new = copy.deepcopy(doc)
    closed = new["contract"]["transport"]["errors"]["closed_enum"]
    choice = rng.randrange(4)
    if choice == 0:
        fields = rng.choice(_field_sides(new))
        fields[f"x{tag}"] = {
            "type": rng.choice(("string", "boolean", "integer")),
            "required": False,
        }
    elif choice == 1:
        nested = [
            spec["fields"]
            for fields in _field_sides(new)
            for spec in fields.values()
            if spec["type"] == "object"
        ]
        target = rng.choice(nested) if nested else rng.choice(_field_sides(new))
        target[f"x{tag}n"] = {"type": "string", "required": False}
    elif choice == 2:
        area = rng.choice(list(new["areas"]))
        new["areas"][area]["ops"][f"opx{tag}"] = _new_op(rng, new, tag, closed)
    else:
        new["areas"][f"areax{tag}"] = {"ops": {f"opx{tag}": _new_op(rng, new, tag, closed)}}
    _sync_allowlists(new)
    return new


def break_doc(rng, doc, tag):
    """One valid-source breaking change: the new document still passes the
    strict source grammar, but the delta is not an additive MINOR."""
    new = copy.deepcopy(doc)
    choice = rng.randrange(9)
    rewrite = lambda: new["contract"]["privacy"].update(logs=f"rewritten-{tag}")  # noqa: E731
    if choice == 0:
        rewrite()
    elif choice == 1 and _field_sides(new, with_fields=True):
        fields = rng.choice(_field_sides(new, with_fields=True))
        spec = fields[rng.choice(list(fields))]
        if spec["type"] == "object":
            spec["required"] = not spec["required"]
        else:
            spec.pop("example", None)
            spec.pop("items", None)
            spec["type"] = rng.choice(
                [kind for kind in ("string", "integer", "boolean") if kind != spec["type"]]
            )
    elif choice == 2 and _field_sides(new, with_fields=True):
        fields = rng.choice(_field_sides(new, with_fields=True))
        del fields[rng.choice(list(fields))]
    elif choice == 3 and _field_sides(new, with_fields=True):
        fields = rng.choice(_field_sides(new, with_fields=True))
        spec = fields[rng.choice(list(fields))]
        spec["required"] = not spec["required"]
    elif choice == 4:
        op = rng.choice(_all_ops(new))[2]
        op["path"] = _unique_path(new, rng, f"{op['path'][1:]}x")
    elif choice == 5 and any(
        len(group["ops"]) > 1 or len(new["areas"]) > 1 for group in new["areas"].values()
    ):
        area, name, _spec = rng.choice(_all_ops(new))
        del new["areas"][area]["ops"][name]
        if not new["areas"][area]["ops"]:
            del new["areas"][area]
    elif choice == 6:
        fields = rng.choice(_field_sides(new))
        fields[f"r{tag}"] = {"type": "string", "required": True}
    elif choice == 7 and any(spec["mutating"] for _a, _n, spec in _all_ops(new)):
        mutating = [spec for _a, _n, spec in _all_ops(new) if spec["mutating"]]
        rng.choice(mutating)["mutating"] = False
    elif choice == 8 and any(spec["auth"] == "required" for _a, _n, spec in _all_ops(new)):
        protected = [spec for _a, _n, spec in _all_ops(new) if spec["auth"] == "required"]
        rng.choice(protected)["auth"] = "public"
    else:
        rewrite()
    _sync_allowlists(new)
    return new


def _major_bumped(doc):
    new = copy.deepcopy(doc)
    base = new["contract"]["versioning"]["base_path"]
    major = int(base.rsplit("/v", 1)[1])
    new["contract"]["versioning"]["base_path"] = f"/cp/v{major + 1}"
    return new


def _major_lowered(doc):
    new = copy.deepcopy(doc)
    base = new["contract"]["versioning"]["base_path"]
    major = int(base.rsplit("/v", 1)[1])
    if major <= 1:
        return None
    new["contract"]["versioning"]["base_path"] = f"/cp/v{major - 1}"
    return new


def verdict(old, new, older, newer, expected):
    """An exact deterministic bool with zero effects on either snapshot."""
    before = copy.deepcopy((old, new))
    for _ in range(2):
        result = compare(old, new, older, newer)
        assert type(result) is bool and result is expected
        assert (old, new) == before


def refused(old, new, older, newer):
    """A typed, fresh, context-free refusal with zero effects; the constant
    message can never echo attacker-controlled snapshot content."""
    before = copy.deepcopy((old, new))
    errors = []
    for _ in range(2):
        with pytest.raises(VersionError) as caught:
            compare(old, new, older, newer)
        error = caught.value
        assert type(error) is VersionError
        assert (error.failure_class, error.code, error.retryable) == (
            "malformed_version_request",
            "malformed_request",
            False,
        )
        assert str(error) == "invalid version request"
        assert error.__cause__ is None and error.__context__ is None
        errors.append(error)
    assert errors[0] is not errors[1]
    assert (old, new) == before


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_generated_documents_happy_breaking_major(seed):
    """Generated grammar documents: additive MINOR is True (at equal or
    higher minors), a valid-source breaking change is False, and the same
    breaking change under a MAJOR bump with a new base path is True."""
    rng = random.Random(seed)
    for iteration in range(25):
        tag = _letters(seed % 97) + _letters(iteration)
        old = random_document(rng, tag)
        attest(old)
        minor = rng.choice((0, 1, 3, CEILING - 1))
        bump = rng.randrange(1, CEILING - minor + 1)
        evolved = evolve(rng, old, f"{tag}e")
        attest(evolved)
        verdict(old, evolved, minor, minor + bump, True)
        verdict(old, evolved, 0, 0, True)
        broken = break_doc(rng, old, f"{tag}b")
        attest(broken)
        verdict(old, broken, minor, minor + bump, False)
        verdict(old, _major_bumped(broken), minor, rng.randrange(CEILING + 1), True)


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_evolution_chains_and_rollback_direction(seed):
    """Bounded additive chains: every forward step and the whole chain are
    True; the backward direction - a consumer rollback needs no migration,
    so the comparator must refuse to bless it as a downgrade - is a typed
    refusal at every step and end to end."""
    rng = random.Random(seed + 31000)
    for iteration in range(12):
        tag = f"{_letters(iteration)}c"
        chain = [random_document(rng, tag)]
        attest(chain[0])
        for step in range(3):
            chain.append(evolve(rng, chain[-1], f"{tag}{_letters(step)}"))
            attest(chain[-1])
        minors = [0]
        for _ in range(3):
            minors.append(minors[-1] + rng.randrange(1, 4))
        for index in range(3):
            verdict(chain[index], chain[index + 1], minors[index], minors[index + 1], True)
        verdict(chain[0], chain[3], minors[0], minors[3], True)
        for index in range(3):
            refused(chain[index + 1], chain[index], minors[index + 1], minors[index])
        refused(chain[3], chain[0], minors[3], minors[0])


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_downgrade_fuzz_refuses_typed_fresh_and_pure(seed):
    """Seeded same-major minor downgrades and major downgrades between valid
    documents are always the typed malformed_version_request refusal, fresh
    and pure, never a False verdict that would bless a rollback."""
    rng = random.Random(seed + 47000)
    for iteration in range(30):
        tag = f"{_letters(iteration)}d"
        old = random_document(rng, tag)
        new = evolve(rng, old, f"{tag}e") if rng.randrange(2) else copy.deepcopy(old)
        attest(old)
        attest(new)
        older = rng.randrange(1, CEILING + 1)
        newer = rng.randrange(older)
        refused(old, new, older, newer)
        refused(new, old, older, newer)
        lowered = _major_lowered(new)
        if lowered is not None:
            attest(lowered)
            refused(old, lowered, rng.randrange(CEILING + 1), rng.randrange(CEILING + 1))


class _Alarm:
    calls = []
    armed = False

    @classmethod
    def trip(cls, name):
        cls.calls.append(name)
        raise AssertionError("user operator called")


class _Hostile:
    calls = 0

    def __str__(self):
        _Hostile.calls += 1
        raise AssertionError("called hostile str")

    def __repr__(self):
        _Hostile.calls += 1
        raise AssertionError("called hostile repr")

    def __eq__(self, other):
        _Hostile.calls += 1
        raise AssertionError("called hostile eq")

    def __hash__(self):
        _Hostile.calls += 1
        raise AssertionError("called hostile hash")

    def __bool__(self):
        _Hostile.calls += 1
        raise AssertionError("called hostile bool")


class _HostileStr(str):
    def __eq__(self, other):
        _Alarm.trip("str.eq")

    def __hash__(self):
        if _Alarm.armed:
            _Alarm.trip("str.hash")
        return str.__hash__(self)

    def __str__(self):
        _Alarm.trip("str.str")


class _HostileDict(dict):
    def __iter__(self):
        _Alarm.trip("dict.iter")

    def __getitem__(self, item):
        _Alarm.trip("dict.get")


class _HostileList(list):
    def __iter__(self):
        _Alarm.trip("list.iter")

    def __eq__(self, other):
        _Alarm.trip("list.eq")


class _HostileInt(int):
    def __le__(self, other):
        _Alarm.trip("int.le")

    def __eq__(self, other):
        _Alarm.trip("int.eq")


def _hostile_value(rng):
    cycle = []
    cycle.append(cycle)
    keyed = {}
    dict.__setitem__(keyed, _HostileStr("k"), 1)
    return rng.choice(
        [
            _Hostile(),
            _HostileStr("hostile"),
            _HostileInt(7),
            _HostileList([1]),
            _HostileDict({"a": 1}),
            cycle,
            (1, 2),
            {"a", "b"},
            b"bytes",
            1j,
            keyed,
            {1: "non-str-key"},
        ]
    )


def _safe_snapshot(root):
    """Identity-aware iterative snapshot, without subclass hooks or
    recursion, so hostile and cyclic inputs pin their exact shape without
    invoking a single caller-defined operator."""
    seen = {}
    parts = []
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            if id(node) in seen:
                parts.append(("alias", seen[id(node)], depth))
                continue
            seen[id(node)] = len(seen)
            parts.append((type(node).__name__, id(node), depth))
            if isinstance(node, dict):
                keys = list(dict.keys(node))
                for key in keys:
                    content = str.__str__(key) if isinstance(key, str) else id(key)
                    parts.append(("key", type(key).__name__, id(key), content, depth))
                stack.extend((value, depth + 1) for value in dict.values(node))
            else:
                stack.extend((value, depth + 1) for value in list.__iter__(node))
        elif isinstance(node, str):
            parts.append((type(node).__name__, id(node), str.__str__(node), depth))
        elif isinstance(node, int):
            parts.append((type(node).__name__, id(node), int.__int__(node), depth))
        elif type(node) in (float, bool, type(None)):
            parts.append((type(node).__name__, id(node), node, depth))
        else:
            parts.append((type(node).__name__, id(node), depth))
    return tuple(parts)


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_malformed_snapshot_fuzz(seed):
    """Seeded hostile snapshots - subclassed builtins, caller-defined
    objects, cycles, tuples, sets, bytes, complex and non-str keys - across
    the opaque-declaration, field-example, privacy-section and minor slots
    are refused typed and fresh without invoking one user operator and
    without touching either snapshot."""
    rng = random.Random(seed + 55000)
    for iteration in range(30):
        tag = f"{_letters(iteration)}m"
        old = random_document(rng, tag)
        new = evolve(rng, old, f"{tag}e")
        attest(old)
        attest(new)
        target = old if rng.randrange(2) else new
        slot = rng.choice(("opaque", "example", "minor", "privacy"))
        value = _hostile_value(rng)
        older, newer = 0, 1
        if slot == "opaque":
            target["contract"]["privacy"]["logs"] = value
        elif slot == "privacy":
            target["contract"]["privacy"] = value
        elif slot == "example":
            choices = [
                spec
                for fields in _field_sides(target)
                for spec in fields.values()
                if spec["type"] != "object"
            ]
            if not choices:
                continue
            rng.choice(choices)["example"] = value
        else:
            older, newer = (value, 1) if target is old else (0, value)
        before = (_safe_snapshot(old), _safe_snapshot(new))
        _Alarm.calls = []
        _Alarm.armed = True
        _Hostile.calls = 0
        errors = []
        try:
            for _ in range(2):
                with pytest.raises(VersionError) as caught:
                    compare(old, new, older, newer)
                error = caught.value
                assert type(error) is VersionError
                assert (error.failure_class, error.code, error.retryable) == (
                    "malformed_version_request",
                    "malformed_request",
                    False,
                )
                assert error.__cause__ is None and error.__context__ is None
                errors.append(error)
            assert errors[0] is not errors[1]
            assert _Alarm.calls == []
            assert _Hostile.calls == 0
            assert (_safe_snapshot(old), _safe_snapshot(new)) == before
        finally:
            _Alarm.armed = False


def _contract_field_name(value):
    """Independent restatement of the field-name grammar - ASCII
    [a-z][a-z0-9_]{0,63} per the shared contract grammar - deliberately NOT
    the production regex, so a production grammar drift cannot drag the
    oracle along with it."""
    return (
        type(value) is str
        and value.isascii()
        and 1 <= len(value) <= 64
        and value[0] in LOWER
        and all(char in LOWER + string.digits + "_" for char in value)
    )


def test_field_name_grammar_pinned_edges():
    """The independent predicate is pinned against the grammar with explicit
    edges before any fuzz comparison trusts it."""
    for good in ("a", "z", "a9_", "a" + "b" * 63, "f" + "_" * 63, "q" * 64):
        assert _contract_field_name(good)
    for bad in ("", "A", "1a", "_a", "a.", "a b", "\u00e9", "a\n", "a" * 65):
        assert not _contract_field_name(bad)


@pytest.mark.parametrize("seed", SEEDS)
def test_field_name_grammar_fuzz_matches_pinned_grammar(seed):
    """Seeded field names over a hostile alphabet are accepted as additive
    optional fields IFF the independently restated grammar accepts them -
    length edges 0/1/64/65 included deterministically."""
    rng = random.Random(seed + 19000)
    candidates = ["", "a", "z", "A", "1", "_", "a" + "b" * 63, "a" + "b" * 64, "z" * 64]
    for _ in range(20):
        candidates.append(
            rng.choice(LOWER)
            + "".join(rng.choice(LOWER + string.digits + "_") for _ in range(rng.randrange(0, 64)))
        )
    for _ in range(15):
        near = rng.choice(LOWER) + "".join(
            rng.choice(LOWER + string.digits + "_") for _ in range(rng.randrange(1, 12))
        )
        corruption = rng.randrange(4)
        if corruption == 0:
            near = near.upper()
        elif corruption == 1:
            near = rng.choice(string.digits + "_.") + near
        elif corruption == 2:
            near = near + rng.choice((".", " ", "\u00e9", "\n"))
        else:
            near = near + "x" * 64
        candidates.append(near)
    candidates += [
        "".join(rng.choice(NAME_ALPHABET) for _ in range(rng.randrange(0, 67))) for _ in range(30)
    ]
    base = random_document(rng, f"{_letters(seed % 90)}g")
    attest(base)
    for name in candidates:
        if BANNED_TOKENS & set(name.split("_")):
            continue  # chess-content names are pinned separately below
        new = copy.deepcopy(base)
        fields = rng.choice(_field_sides(new))
        if name in fields:
            continue
        fields[name] = {"type": "string", "required": False}
        if _contract_field_name(name):
            attest(new)
            verdict(base, new, 0, 1, True)
        else:
            refused(base, new, 0, 1)


def _field_name_fuzz_check(compare_fn, candidates):
    """Run the field-name fuzz comparison against any compare callable:
    returns the first name where the callable disagrees with the
    independent contract predicate, else None."""
    base = _probe_doc()
    for name in candidates:
        new = copy.deepcopy(base)
        new["areas"]["alpha"]["ops"]["read"]["response"]["fields"][name] = {
            "type": "string",
            "required": False,
        }
        try:
            accepted = compare_fn(base, new, 0, 1) is True
        except VersionError:
            accepted = False
        if accepted != _contract_field_name(name):
            return name
    return None


def _widened_grammar_compare(old, new, old_minor, new_minor):
    """Behavioral mutant: the field-name grammar silently widens to accept
    dots inside names."""
    real = cpv.FIELD
    cpv.FIELD = re.compile(r"[a-z][a-z0-9_.]{0,63}", re.ASCII)
    try:
        return cpv.compare(old, new, old_minor, new_minor)
    finally:
        cpv.FIELD = real


def test_field_name_fuzz_kills_grammar_widening_mutant():
    """The fuzz comparison is measured against a grammar-widening mutant:
    the shipped comparator shows NO disagreement; the widened mutant is
    caught at the exact widened name."""
    assert _field_name_fuzz_check(compare, ["x.y", "ok_id", "A", "a" * 65]) is None
    assert _field_name_fuzz_check(_widened_grammar_compare, ["ok_id", "x.y"]) == "x.y"


def test_chess_content_field_names_refused_and_near_misses_accepted():
    """The privacy rule's chess-content vocabulary is refused as field
    names - bare, prefixed or suffixed - while near-miss words that merely
    contain a token stay valid additive fields."""
    base = random_document(random.Random(5), "chess")
    attest(base)
    area = next(iter(base["areas"]))
    op = next(iter(base["areas"][area]["ops"]))
    response = base["areas"][area]["ops"][op]["response"]["fields"]
    for banned in sorted(BANNED_TOKENS):
        for name in (banned, f"{banned}_flag", f"the_{banned}"):
            new = copy.deepcopy(base)
            new["areas"][area]["ops"][op]["response"]["fields"][name] = {
                "type": "string",
                "required": False,
            }
            refused(base, new, 0, 1)
    assert not response  # the chosen op's response stays empty for clean deltas
    for near in ("gaming", "moved", "notebook", "evaluate", "boardwalk", "fens"):
        new = copy.deepcopy(base)
        new["areas"][area]["ops"][op]["response"]["fields"][near] = {
            "type": "string",
            "required": False,
        }
        attest(new)
        verdict(base, new, 0, 1, True)


def _nested_object_fields(depth):
    if depth == 0:
        return {"leaf": {"type": "string", "required": False}}
    return {
        f"level{_letters(depth)}": {
            "type": "object",
            "required": False,
            "fields": _nested_object_fields(depth - 1),
        }
    }


def test_object_nesting_depth_boundary():
    """Object nesting is valid through depth seven and refused at depth
    eight, with the depth-seven addition a clean additive MINOR."""
    base = _probe_doc()
    deep = copy.deepcopy(base)
    deep["areas"]["alpha"]["ops"]["read"]["response"]["fields"].update(_nested_object_fields(7))
    attest(deep)
    verdict(base, deep, 0, 1, True)
    deeper = copy.deepcopy(base)
    deeper["areas"]["alpha"]["ops"]["read"]["response"]["fields"].update(_nested_object_fields(8))
    refused(base, deeper, 0, 1)


def test_declaration_order_is_not_semantics():
    """Reordering areas, operations, field maps and the allowlists changes
    no declaration: the documents compare True at equal and bumped minors."""
    old = _probe_doc()
    new = copy.deepcopy(old)
    for group in new["areas"].values():
        group["ops"] = {key: group["ops"][key] for key in reversed(list(group["ops"]))}
        for op in group["ops"].values():
            for side in ("request", "response"):
                op[side]["fields"] = {
                    key: op[side]["fields"][key] for key in reversed(list(op[side]["fields"]))
                }
    transport = new["contract"]["transport"]
    transport["auth"]["public_operations"] = list(reversed(transport["auth"]["public_operations"]))
    transport["read_only_operations"] = list(reversed(transport["read_only_operations"]))
    verdict(old, new, 0, 1, True)
    verdict(old, new, 7, 7, True)


class SyntheticFault(Exception):
    """A synthetic pipeline fault, never a contract refusal."""


@pytest.mark.parametrize("seed", SEEDS)
def test_injected_faults_never_become_verdicts_and_leave_no_residue(seed, monkeypatch):
    """Synthetic faults injected into validation (the _require guard at a
    seeded call index), into the comparison phase (_same_major) and into
    deep equality (a KeyboardInterrupt) always propagate - they are never
    swallowed into a False or True verdict - and leave both snapshots
    byte-identical. Once the fault clears, the same inputs compare cleanly."""
    rng = random.Random(seed + 66000)
    old = random_document(rng, "flt")
    new = evolve(rng, old, "flte")
    attest(old)
    attest(new)
    before = (copy.deepcopy(old), copy.deepcopy(new))
    real_require = cpv._require
    for _ in range(6):
        index = rng.randrange(1, 60)
        calls = {"count": 0}

        def faulty_require(condition, _calls=calls, _index=index, _real=real_require):
            _calls["count"] += 1
            if _calls["count"] == _index:
                raise SyntheticFault("injected validation fault")
            return _real(condition)

        monkeypatch.setattr(cpv, "_require", faulty_require)
        with pytest.raises(SyntheticFault):
            compare(old, new, 0, 1)
    monkeypatch.undo()

    def faulty_same_major(_old, _new):
        raise SyntheticFault("injected comparison fault")

    monkeypatch.setattr(cpv, "_same_major", faulty_same_major)
    with pytest.raises(SyntheticFault):
        compare(old, new, 0, 1)
    monkeypatch.undo()

    def interrupted(_left, _right):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cpv, "_equal", interrupted)
    with pytest.raises(KeyboardInterrupt):
        compare(old, new, 0, 1)
    monkeypatch.undo()
    assert (old, new) == before
    assert compare(old, new, 0, 1) is True


# -- measured behavioral comparator mutants ------------------------------------
# The harness runs the same fixed probes against any compare callable and
# returns the tag of the FIRST contract violation observed. A kill is
# counted only when the observed witness is exactly the behavior the mutant
# violates; impurity is checked after every probe.


def _probe_doc():
    return {
        "schema_version": 2,
        "contract": {
            "name": "replaceable-control-plane",
            "versioning": {"scheme": "semver", "base_path": "/cp/v1"},
            "transport": {
                "auth": {"public_operations": ["beta.ping"]},
                "read_only_operations": ["alpha.read", "beta.ping"],
                "errors": {
                    "shape": copy.deepcopy(ERROR_SHAPE),
                    "closed_enum": ["malformed_request", "not_found", "internal"],
                },
            },
            "privacy": {"logs": "no-secrets"},
        },
        "areas": {
            "alpha": {
                "ops": {
                    "read": {
                        "method": "GET",
                        "path": "/alpha/read",
                        "auth": "required",
                        "mutating": False,
                        "request": {"fields": {}},
                        "response": {"fields": {"summary": {"type": "string", "required": True}}},
                        "errors": ["not_found", "internal"],
                    },
                    "write": {
                        "method": "POST",
                        "path": "/alpha/write",
                        "auth": "required",
                        "mutating": True,
                        "request": {"fields": {"payload": {"type": "string", "required": True}}},
                        "response": {"fields": {"ack": {"type": "boolean", "required": True}}},
                        "errors": ["malformed_request", "internal"],
                    },
                }
            },
            "beta": {
                "ops": {
                    "ping": {
                        "method": "GET",
                        "path": "/beta/ping",
                        "auth": "public",
                        "mutating": False,
                        "request": {"fields": {}},
                        "response": {"fields": {}},
                        "errors": ["internal"],
                    }
                }
            },
        },
    }


def _probes():
    base = _probe_doc()
    additive = copy.deepcopy(base)
    additive["areas"]["alpha"]["ops"]["read"]["response"]["fields"]["detail"] = {
        "type": "string",
        "required": False,
    }
    wider = copy.deepcopy(base)
    wider["areas"]["gamma"] = {
        "ops": {
            "scan": {
                "method": "GET",
                "path": "/gamma/scan",
                "auth": "public",
                "mutating": False,
                "request": {"fields": {}},
                "response": {"fields": {}},
                "errors": ["internal"],
            }
        }
    }
    _sync_allowlists(wider)
    decl = copy.deepcopy(base)
    decl["contract"]["privacy"]["logs"] = "rewritten"
    removed = copy.deepcopy(base)
    del removed["areas"]["alpha"]["ops"]["read"]["response"]["fields"]["summary"]
    required = copy.deepcopy(base)
    required["areas"]["alpha"]["ops"]["write"]["request"]["fields"]["tenant"] = {
        "type": "string",
        "required": True,
    }
    drift_old = _probe_doc()
    drift_new = _probe_doc()
    drift_old["contract"]["privacy"]["logs"] = 1
    drift_new["contract"]["privacy"]["logs"] = True
    bad_path = copy.deepcopy(base)
    bad_path["contract"]["versioning"]["base_path"] = "/CP/V2"
    return [
        ("additive-field", base, additive, 0, 1, "true"),
        ("additive-area", base, wider, 0, CEILING, "true"),
        ("identical", base, copy.deepcopy(base), CEILING, CEILING, "true"),
        ("breaking-decl", base, decl, 0, 1, "false"),
        ("removal", base, removed, 0, 1, "false"),
        ("required-addition", base, required, 0, 1, "false"),
        ("numeric-coercion", drift_old, drift_new, 0, 1, "false"),
        ("malformed-minor", base, copy.deepcopy(base), True, 1, "refusal"),
        ("malformed-doc", base, bad_path, 0, 1, "refusal"),
        ("minor-downgrade", additive, base, 7, 3, "refusal"),
        ("major-downgrade", _major_bumped(base), base, 0, 0, "refusal"),
    ]


def _harness_outcome(compare_fn):
    """The versioning contract as an executable, classifying harness.
    Returns "survived" when every probe holds; otherwise the witness tag of
    the first observed violation."""
    for tag, old, new, older, newer, expected in _probes():
        before = (copy.deepcopy(old), copy.deepcopy(new))
        try:
            result = compare_fn(old, new, older, newer)
        except VersionError:
            outcome = "refusal"
        except Exception:
            return f"{tag}:raw-escape"
        else:
            if type(result) is not bool:
                return f"{tag}:non-bool"
            outcome = "true" if result else "false"
        if outcome != expected:
            return tag
        if (old, new) != before:
            return "impurity"
    return "survived"


def _order_blind_compare(old, new, old_minor, new_minor):
    """Behavioral mutant: minor and major monotonicity is never checked,
    so a downgrade comparison is answered like a forward one."""
    if type(old_minor) is not int or type(new_minor) is not int:
        raise VersionError()
    if not (0 <= old_minor <= CEILING and 0 <= new_minor <= CEILING):
        raise VersionError()
    cpv._source(old)
    cpv._source(new)
    if old["contract"]["versioning"]["base_path"] == new["contract"]["versioning"]["base_path"]:
        return cpv._same_major(old, new)
    return True


def _required_blind_compare(old, new, old_minor, new_minor):
    """Behavioral mutant: added fields are accepted even when required."""
    real = cpv._additive_fields

    def lax(before, after):
        for name, original in before.items():
            if name not in after:
                return False
            updated = after[name]
            if original["type"] == "object" and updated["type"] == "object":
                if {k: v for k, v in original.items() if k != "fields"} != {
                    k: v for k, v in updated.items() if k != "fields"
                }:
                    return False
                if not lax(original["fields"], updated["fields"]):
                    return False
            elif not cpv._equal(original, updated):
                return False
        return True  # any addition accepted, required or not

    cpv._additive_fields = lax
    try:
        return cpv.compare(old, new, old_minor, new_minor)
    finally:
        cpv._additive_fields = real


def _coercive_compare(old, new, old_minor, new_minor):
    """Behavioral mutant: deep equality coerces across types, so 1 and True
    read as the same declaration."""
    real = cpv._equal
    cpv._equal = lambda left, right: left == right
    try:
        return cpv.compare(old, new, old_minor, new_minor)
    finally:
        cpv._equal = real


def _refusal_swallower(old, new, old_minor, new_minor):
    """Behavioral mutant: a malformed request is reported as a clean False
    verdict, laundering malformed into merely incompatible."""
    try:
        return cpv.compare(old, new, old_minor, new_minor)
    except VersionError:
        return False


def _impure_compare(old, new, old_minor, new_minor):
    """Behavioral mutant: the comparison rewrites the candidate document."""
    result = cpv.compare(old, new, old_minor, new_minor)
    new["contract"]["privacy"]["logs"] = "rewritten-by-compare"
    return result


def test_comparison_harness_accepts_shipped_compare():
    """Control: the harness itself is not vacuously strict - the shipped
    comparator satisfies every probe end to end."""
    assert _harness_outcome(compare) == "survived"


@pytest.mark.parametrize(
    "mutant,witness",
    [
        (_order_blind_compare, "minor-downgrade"),
        (_required_blind_compare, "required-addition"),
        (_coercive_compare, "numeric-coercion"),
        (_refusal_swallower, "malformed-minor"),
        (_impure_compare, "impurity"),
    ],
    ids=["order-blind", "required-blind", "coercive-equal", "refusal-swallower", "impure"],
)
def test_comparator_mutants_measured_kills(mutant, witness):
    """Each behavioral mutant is KILLED at exactly its own behavioral
    checkpoint: the order-blind mutant is observed blessing a minor
    downgrade, the required-blind mutant is observed accepting a required
    addition, the coercive mutant is observed equating 1 with True, the
    swallower is observed reporting a malformed request as False, and the
    impure mutant is observed rewriting its candidate. The witness is
    measured by the harness, never assumed."""
    assert _harness_outcome(mutant) == witness
