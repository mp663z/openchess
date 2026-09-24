"""T0188: production conflict-detector runtime proof (graph/conflict.py).

The T0187 red battery is the behavioral proof: it runs against
graph.conflict with only its two binding lines switched. This file
proves the switch is exactly that binding, that production never
imports the test package and links only shipped runtimes, that
production matches the T0185 contract reference on every fixture row
(and every malformed repair) and on a hostile probe corpus, that the
linked-runtime rejections this module catches (its own except clause:
VariantError, DigestError, ValueError) map to a FRESH, unchained
malformed_conflict_record, and that one-edit source mutants of the
production module are killed.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import itertools
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph.conflict as prod  # noqa: E402
from graph.position_digest import DigestError  # noqa: E402
from tests import test_t0185_conflict_contract as ref  # noqa: E402
from tests.test_t0187_conflict_red import CASES, _repaired  # noqa: E402
from tools.variant_runtime import VariantError  # noqa: E402

RED = ROOT / "tests" / "test_t0187_conflict_red.py"
PRODUCTION = ROOT / "graph" / "conflict.py"
ORACLE_BINDING = ("ConflictDetector = _reference.ConflictDetector\n"
                  "ConflictError = _reference.ConflictError_\n")
PRODUCTION_BINDING = ("from graph.conflict import ConflictDetector, "
                      "ConflictError  # noqa: E402  # isort: skip\n")
# sha256 of tests/test_t0187_conflict_red.py as merged
RED_AS_MERGED_SHA256 = (
    "b5ec0f6139b4cc2a71fdce58b4a3479bda0cd93128aa4273d04787ddc3118fcb")

STARTPOS, AFTER_E4, KINGS, LEGAL_EP = (ref.STARTPOS, ref.AFTER_E4,
                                       ref.KINGS, ref.LEGAL_EP)
K1, K2, K3 = ref.K1, ref.K2, ref.K3
MCR = "malformed_conflict_record"
_node, _state, _identity = ref._node, ref._state, ref._identity


# -- R1: surface and links -----------------------------------------------------


def test_r1_production_imports_only_shipped_runtimes():
    mods = set()
    for n in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            assert n.level == 0
            mods.add(n.module)
    assert not any(m.split(".")[0] == "tests" for m in mods)
    assert mods == {"__future__", "hashlib", "re", "pathlib", "yaml",
                    "graph.node", "graph.position_digest",
                    "tools.variant_runtime"}


def test_r1_public_surface():
    assert prod.__all__ == ["FAILURE_MAPPING", "ConflictDetector",
                            "ConflictError", "load_docs", "state_id"]
    assert dict(ref.FAILURE_MAPPING) == prod.FAILURE_MAPPING
    assert prod.load_docs()[0] == ref._CC
    assert prod.load_docs()[0] is not prod.load_docs()[0]


def test_r1_red_switch_is_exactly_the_binding():
    text = RED.read_text()
    assert text.count(PRODUCTION_BINDING) == 1
    assert ORACLE_BINDING not in text
    back = text.replace(PRODUCTION_BINDING, ORACLE_BINDING)
    assert hashlib.sha256(back.encode()).hexdigest() == RED_AS_MERGED_SHA256


# -- R2: parity with the contract reference ------------------------------------


def _outcome(mod, base, left, right):
    """Outcome of one detect over deep copies, plus proof the received
    objects were neither mutated nor re-bound."""
    args = [copy.deepcopy(x) for x in (base, left, right)]
    snap = copy.deepcopy(args)
    refs = [_refs(x) for x in args]
    err_cls = (prod.ConflictError, ref.ConflictError_, mod.ConflictError
               if hasattr(mod, "ConflictError") else ref.ConflictError_)
    try:
        got = ("ok", mod.ConflictDetector().detect(*args))
    except err_cls as exc:
        got = ("err", exc.failure_class, exc.code)
    if args != snap or [_refs(x) for x in args] != refs:
        return ("mutated-input",)
    if got[0] == "ok":
        # order is part of the result: witnesses in canonical order,
        # record fields exactly as listed
        res = got[1]
        return ("ok", list(res), list(res["conflicts"].items()), res)
    return got


def _refs(obj):
    if type(obj) is dict:
        return tuple((id(k), id(v), _refs(v)) for k, v in dict.items(obj))
    return id(obj)


def _fixture_rows():
    return [(s, c) for s in ("happy", "boundary", "malformed", "rollback")
            for c in CASES[s]]


def _row_outcomes(mod, case):
    out = [_outcome(mod, case["base"], case["left"], case["right"])]
    if case.get("kind") == "rollback-detect":
        out.append(_outcome(mod, case["then_base"], case["then_left"],
                            case["then_right"]))
    return out


def test_r2_fixture_parity_and_pins():
    for section, case in _fixture_rows():
        got = _row_outcomes(prod, case)
        assert got == _row_outcomes(ref, case), case["name"]
        if section in ("happy", "boundary"):
            assert got[0][3] == case["expect"], case["name"]
            assert got[0][2] == list(case["expect"]["conflicts"].items())
        else:
            assert got[0][1] == case["expect_failure"], case["name"]
        if section == "rollback":
            assert got[1][3] == case["expect"], case["name"]
        if section == "malformed":
            fixed = _repaired(case)
            fgot = _row_outcomes(prod, fixed)
            assert fgot == _row_outcomes(ref, fixed), case["name"]
            assert fgot[0][0] == "ok", case["name"]


class _SK(str):
    def __hash__(self):
        return str.__hash__(str(self))

    def __eq__(self, other):
        raise RuntimeError("hostile key compare")

    __ne__ = __eq__


class _S(str):
    pass


class _DictSub(dict):
    pass


POOL = [_node(STARTPOS), _node(AFTER_E4), _node(KINGS), _node(LEGAL_EP),
        _node(KINGS, K1), _node(KINGS, K2), _node(STARTPOS, K3)]


def _pool_states():
    """Every state over the pool keyed canonically (one record per
    identity)."""
    out = []
    for r in range(3):
        for combo in itertools.combinations(POOL, r):
            ids = [_identity(x) for x in combo]
            if len(set(ids)) == len(ids):
                out.append(_state(*combo))
    return out


STATES = _pool_states()


def _triples():
    # deterministic spread over all (base, left, right) triples
    return [t for i, t in enumerate(itertools.product(STATES, repeat=3))
            if i % 7 == 0]


def test_r2_pool_triple_parity_symmetry_determinism():
    for base, left, right in _triples():
        got = _outcome(prod, base, left, right)
        assert got == _outcome(ref, base, left, right)
        assert got == _outcome(prod, base, left, right)
        swap = _outcome(prod, base, right, left)
        if got[0] == "ok":
            flipped = {k: {"kind": w["kind"], "left": w["right"],
                           "right": w["left"]}
                       for k, w in got[3]["conflicts"].items()}
            assert swap[3]["conflicts"] == flipped
            assert list(got[3]["conflicts"]) == sorted(got[3]["conflicts"])
        else:
            assert swap == got


def _hostile_triples():
    good_key = _identity(_node(KINGS))
    good = _node(KINGS)
    b, l_, r_ = (_state(_node(STARTPOS)), _state(_node(KINGS, K1)),
                 _state(_node(KINGS, K2)))
    bad_states = [None, True, 0, 1.5, "text", [], (), {"k": {"v": 0}},
                  _DictSub(b), {None: good}, {0: good}, {"wrong": good},
                  {_S(good_key): good}, {good_key: None}, {good_key: []},
                  {good_key: _DictSub(good)}, {good_key: {**good, "x": 1}},
                  {good_key: {k: v for k, v in good.items() if k != "digest"}},
                  {good_key: {**good, "digest": "bad"}},
                  {good_key: {**good, "digest": K1.upper()}},
                  {good_key: {**good, "digest": K1 + "\n"}},
                  {good_key: {**good, "variant": "c960"}},
                  {good_key: {**good, "variant": _S("standard")}},
                  {good_key: {**good, "snapshot_fen": _S(good["snapshot_fen"])}},
                  {good_key: {**good, "digest": _S(good["digest"])}},
                  {good_key: {**good, "snapshot_fen": KINGS.replace(" 0 1", " 3 1")}},
                  {good_key: {**good, "snapshot_fen": KINGS + " "}},
                  {good_key: {**good, "snapshot_fen": "garbage"}},
                  {good_key: {**good, "snapshot_fen": AFTER_E4.replace(" - ", " e3 ")}},
                  {good_key + " ": good},
                  {good_key: {"variant": good["variant"], "digest": good["digest"],
                              "snapshot_fen_x": good["snapshot_fen"]}},
                  {good_key: {**good, 1: "x"}}]
    for field in ("variant", "digest", "snapshot_fen"):
        for value in (None, True, 0, 1.5, [], {}, ""):
            bad_states.append({good_key: {**good, field: value}})
    out = []
    for bad in bad_states:
        out += [(bad, l_, r_), (b, bad, r_), (b, l_, bad)]
    out.append((b, b, r_))
    out.append((b, l_, b))
    return out


HOSTILE_TRIPLES = _hostile_triples()


@pytest.mark.parametrize("i", range(len(HOSTILE_TRIPLES)))
def test_r2_hostile_parity(i):
    triple = HOSTILE_TRIPLES[i]
    got = _outcome(prod, *triple)
    assert got == _outcome(ref, *triple)
    assert got[0] == "err"


def _sk_state():
    good = _node(KINGS)
    rec = {_SK("variant"): good["variant"], "digest": good["digest"],
           "snapshot_fen": good["snapshot_fen"]}
    return {_identity(good): rec}


def _hostile_key_red(mod):
    """A record key hashing like a field name whose == raises must fail
    closed (typed) before any set build or lookup."""
    b, r_ = _state(_node(STARTPOS)), _state(_node(KINGS, K2))
    for triple in ((_sk_state(), b, r_), (b, _sk_state(), r_),
                   (b, r_, _sk_state())):
        try:
            mod.ConflictDetector().detect(*triple)
            return True
        except mod.ConflictError as err:
            if err.failure_class != MCR:
                return True
    return False


def test_r2_hostile_record_key_fails_closed_typed():
    assert not _hostile_key_red(prod)


HUGE = "9" * 5000  # beyond the interpreter's int-string limit (4300)


def _huge_clock_red(mod):
    """A clock beyond the int-string limit fails as a FRESH typed
    malformed_conflict_record (the reference lets ValueError escape)."""
    good = _node(STARTPOS)
    key = _identity(good)
    for fen in (STARTPOS.replace(" 0 1", f" {HUGE} 1"),
                STARTPOS.replace(" 0 1", f" 0 {HUGE}")):
        bad = {key: {**good, "snapshot_fen": fen}}
        try:
            mod.ConflictDetector().detect(bad, _state(_node(KINGS)),
                                          _state(_node(AFTER_E4)))
            return True
        except mod.ConflictError as err:
            if err.failure_class != MCR or err.__context__ is not None or \
                    err.__cause__ is not None:
                return True
    return False


def test_r2_clock_beyond_int_string_limit_fails_closed_typed():
    assert not _huge_clock_red(prod)


def _rank(i):
    return (str(i) if i else "") + "K" + (str(7 - i) if 7 - i else "")


# sixteen distinct king-vs-king identities: white king on each square
# of ranks 1 and 2, black king on e8
WIDE_FENS = [f"4k3/8/8/8/8/8/8/{_rank(i)} w - - 0 1" for i in range(8)] + \
    [f"4k3/8/8/8/8/8/{_rank(i)}/8 w - - 0 1" for i in range(8)]


def _wide_order_red(mod):
    """Sixteen overlapping identities, each re-keyed differently on each
    side: the witnesses must come back in canonical (sorted) identity
    order. An unordered result matches sorted order with probability
    1/16! - never by chance."""
    base = _state(*[_node(f, K1) for f in WIDE_FENS])
    left = _state(*[_node(f, K2) for f in WIDE_FENS])
    right = _state(*[_node(f, K3) for f in WIDE_FENS])
    got = mod.ConflictDetector().detect(base, left, right)["conflicts"]
    return len(got) != 16 or list(got) != sorted(got)


def test_r2_witnesses_in_canonical_order_over_sixteen_identities():
    assert not _wide_order_red(prod)


def test_r2_state_id_is_the_linked_derivation():
    for state in STATES:
        assert prod.state_id(state) == ref.state_id(state)
    with pytest.raises(prod.ConflictError):
        prod.state_id({"k": {"v": 0}})


# -- R3: linked-runtime rejections (per-module forge set) ----------------------

FORGED = {
    **{f"variant-{code}": VariantError(code=code, message="forged")
       for code in ("malformed_request", "unknown_variant",
                    "illegal_position")},
    "digest-malformed_position": DigestError("malformed_position",
                                             "malformed_request"),
    "digest-unknown_variant": DigestError("unknown_variant",
                                          "unknown_variant"),
    "value-error": ValueError("forged"),
    "unicode-encode-error": UnicodeEncodeError("utf-8", "\ud800", 0, 1,
                                               "forged"),
}
LINKED = ("make_record", "parse_position", "identity")


def _forged_red(mod, forged, name):
    original = getattr(mod, name)

    def raiser(*a, **k):
        raise forged

    setattr(mod, name, raiser)
    try:
        mod.ConflictDetector().detect(_state(_node(STARTPOS)),
                                      _state(_node(KINGS)),
                                      _state(_node(AFTER_E4)))
        return True
    except BaseException as err:  # noqa: BLE001
        return not (type(err) is mod.ConflictError
                    and err.failure_class == MCR and err is not forged
                    and err.__context__ is None and err.__cause__ is None)
    finally:
        setattr(mod, name, original)


@pytest.mark.parametrize("name", LINKED)
@pytest.mark.parametrize("forged", list(FORGED))
def test_r3_linked_rejections_fail_closed_fresh(forged, name):
    assert not _forged_red(prod, FORGED[forged], name)


def test_r3_per_module_forge_set_is_covered():
    """Per-module scope: the classes this module raises plus the classes
    its own except clauses name, AST-enumerated."""
    raised, caught = set(), set()
    for n in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(n, ast.Raise) and n.exc is not None:
            exc = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            raised.add(ast.unparse(exc))
        elif isinstance(n, ast.ExceptHandler):
            assert n.type is not None
            elts = n.type.elts if isinstance(n.type, ast.Tuple) else [n.type]
            caught |= {ast.unparse(t) for t in elts}
    assert raised == {"ConflictError"}
    assert caught == {"VariantError", "DigestError", "ValueError"}
    forged = {type(e).__name__ for e in FORGED.values()}
    assert {"VariantError", "DigestError", "ValueError"} <= forged
    assert not {k for k, v in vars(prod).items() if k.startswith("_")
                and isinstance(v, type) and issubclass(v, BaseException)}


# -- R4: source mutants of the production module are killed --------------------

MUTANTS = {
    "state-isinstance": ("    if type(state) is not dict:\n",
                         "    if not isinstance(state, dict):\n"),
    "state-key-type-off": ("    if type(key) is not str or type(rec) is not dict:",
                           "    if type(rec) is not dict:"),
    "record-isinstance": ("    if type(key) is not str or type(rec) is not dict:",
                          "    if type(key) is not str or not isinstance(rec, dict):"),
    "record-key-guard-off": (
        "    if not all(type(k) is str for k in rec_keys):\n        _fail(_MCR)\n", ""),
    "record-key-set-len-only": (
        "    if len(rec_keys) != len(_RECORD_FIELDS) or \\\n"
        "            set(rec_keys) != _RECORD_FIELDS:",
        "    if len(rec_keys) != len(_RECORD_FIELDS):"),
    "record-key-set-subset": (
        "    if len(rec_keys) != len(_RECORD_FIELDS) or \\\n"
        "            set(rec_keys) != _RECORD_FIELDS:",
        "    if not set(rec_keys) >= _RECORD_FIELDS:"),
    "field-type-off": (
        "    if type(variant) is not str or type(digest) is not str or \\\n"
        "            type(snapshot) is not str:\n        _fail(_MCR)\n", ""),
    "digest-type-off": (
        "    if type(variant) is not str or type(digest) is not str or \\\n",
        "    if type(variant) is not str or \\\n"),
    "catch-narrowed-no-value-error": (
        "    except (VariantError, DigestError, ValueError):",
        "    except (VariantError, DigestError):"),
    "catch-narrowed-no-digest-error": (
        "    except (VariantError, DigestError, ValueError):",
        "    except (VariantError, ValueError):"),
    "catch-narrowed-no-variant-error": (
        "    except (VariantError, DigestError, ValueError):",
        "    except (DigestError, ValueError):"),
    "fails-inside-except-chained": (
        "    except (VariantError, DigestError, ValueError):\n        failed = True\n",
        "    except (VariantError, DigestError, ValueError):\n        _fail(_MCR)\n"),
    "snapshot-compare-off": (
        "    if variant != derived[\"variant\"] or snapshot != derived[\"snapshot_fen\"]:",
        "    if variant != derived[\"variant\"]:"),
    "digest-grammar-off": ("    if _DIGEST_RE.fullmatch(digest) is None:\n",
                           "    if False:\n"),
    "digest-grammar-match": ("    if _DIGEST_RE.fullmatch(digest) is None:\n",
                             "    if _DIGEST_RE.match(digest) is None:\n"),
    "digest-decides-identity": (
        "    if derived_key != key:",
        "    if derived_key != key or digest != derived[\"digest\"]:"),
    "identity-key-check-off": ("    if derived_key != key:\n", "    if False:\n"),
    "divergent-base-off": ("        if base_id in (left_id, right_id):\n",
                           "        if False:\n"),
    "divergent-base-left-right": (
        "        if base_id in (left_id, right_id):\n",
        "        if base_id in (left_id, right_id) or left_id == right_id:\n"),
    "divergent-base-left-only": ("        if base_id in (left_id, right_id):\n",
                                 "        if base_id == left_id:\n"),
    "same-outcome-reported": ("                if lrec != rrec:\n",
                              "                if True:\n"),
    "added-kind-swapped": (
        "                        \"kind\": (\"added_differently\" if lkind == \"added\"",
        "                        \"kind\": (\"added_differently\" if lkind != \"added\""),
    "witness-sides-swapped": (
        "                        \"left\": dict(lrec), \"right\": dict(rrec)}",
        "                        \"left\": dict(rrec), \"right\": dict(lrec)}"),
    "removed-witness-not-null": (
        "                \"left\": dict(lrec) if lkind == \"changed\" else None,",
        "                \"left\": dict(lrec),"),
    "changed-not-derived": ("        elif base[key] != rec:\n",
                            "        elif False:\n"),
    "removed-not-derived": ("        if key not in derived:\n",
                            "        if False:\n"),
    "state-id-unsorted-keys": ("    for key in sorted(frozen):\n",
                               "    for key in frozen:\n"),
    "state-id-unsorted-fields": (
        "for field in sorted(rec))", "for field in rec)"),
    "state-id-prefix": ("    return \"gs1:\" + hashlib", "    return \"gs2:\" + hashlib"),
    "conflicts-unsorted": ("        for key in sorted(set(lch) & set(rch)):\n",
                           "        for key in set(lch) & set(rch):\n"),
    "validation-skipped-for-right": (
        "        states = [_validated_state(s) for s in (base, left, right)]\n",
        "        states = [_validated_state(base), _validated_state(left), right]\n"),
}

# edits no black-box probe can separate (reason per entry)
EQUIVALENT_EDITS = {
    # make_record rejects every unregistered variant, and a registered
    # id derives to itself, so the variant comparison never decides
    "variant-compare-off": (
        "    if variant != derived[\"variant\"] or snapshot != derived[\"snapshot_fen\"]:",
        "    if snapshot != derived[\"snapshot_fen\"]:"),
    # both-removed records are the same base record, so the equal-
    # outcome test below skips them with no witness either way
    "both-removed-reported": (
        "            if lkind == rkind == \"removed\":\n                continue",
        "            if False:\n                continue"),
    # witnesses come from the detector's private validated copies,
    # never from caller objects, so the extra copy is unobservable
    "witness-aliases-input": (
        "                        \"left\": dict(lrec), \"right\": dict(rrec)}",
        "                        \"left\": lrec, \"right\": rrec}"),
    # the key-set comparison alone decides the same cases; the length
    # test is a cheap prefilter
    "record-key-len-prefilter-off": (
        "    if len(rec_keys) != len(_RECORD_FIELDS) or \\\n"
        "            set(rec_keys) != _RECORD_FIELDS:",
        "    if set(rec_keys) != _RECORD_FIELDS:"),
}


def _load_mutant(src):
    mod = types.ModuleType("graph._t0188_mutant")
    mod.__file__ = str(PRODUCTION)
    exec(compile(src, str(PRODUCTION), "exec"), mod.__dict__)  # noqa: S102
    return mod


# a fixed quarter of the pool triples keeps the per-mutant replay short
KILL_TRIPLES = _triples()[::4]


def _kill_suite_red(mod):
    try:
        for _s, case in _fixture_rows():
            if _row_outcomes(mod, case) != _row_outcomes(ref, case):
                return True
            if _s == "malformed":
                fixed = _repaired(case)
                if _row_outcomes(mod, fixed) != _row_outcomes(ref, fixed):
                    return True
        for triple in KILL_TRIPLES:
            if _outcome(mod, *triple) != _outcome(ref, *triple):
                return True
        for triple in HOSTILE_TRIPLES:
            if _outcome(mod, *triple) != _outcome(ref, *triple):
                return True
        for state in STATES:
            if mod.state_id(state) != ref.state_id(state):
                return True
        # a witness must never alias the caller's records
        b = _state(_node(KINGS, K1))
        lt, rt = _state(_node(KINGS, K2)), _state(_node(KINGS, K3))
        w = mod.ConflictDetector().detect(b, lt, rt)["conflicts"]
        rec_ids = {id(x) for s in (b, lt, rt) for x in s.values()}
        if any(id(v) in rec_ids for x in w.values() for v in x.values()):
            return True
        if _hostile_key_red(mod) or _huge_clock_red(mod) or \
                _wide_order_red(mod):
            return True
        for forged in FORGED.values():
            for name in LINKED:
                if _forged_red(mod, forged, name):
                    return True
    except BaseException:  # noqa: BLE001 - any raw escape is a kill
        return True
    return False


def test_r4_production_is_green_under_the_kill_suite():
    assert not _kill_suite_red(_load_mutant(PRODUCTION.read_text()))


@pytest.mark.parametrize("name", list(MUTANTS))
def test_r4_source_mutants_are_killed(name):
    old, new = MUTANTS[name]
    src = PRODUCTION.read_text()
    assert src.count(old) == 1, (name, old)
    assert _kill_suite_red(_load_mutant(src.replace(old, new))), name


@pytest.mark.parametrize("name", list(EQUIVALENT_EDITS))
def test_r4_equivalent_edits_stay_green(name):
    old, new = EQUIVALENT_EDITS[name]
    src = PRODUCTION.read_text()
    assert src.count(old) == 1, name
    assert not _kill_suite_red(_load_mutant(src.replace(old, new))), name
