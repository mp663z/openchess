"""T0047 Chess/variant/integration-restart: the variant runtime
(tools.variant_runtime) across a process restart.

Integration path: parse_position -> identity -> persisted JSON text ->
a FRESH Python process reloads the text and runs project_additive on
it, and re-parses the same (variant, FEN) from scratch. Every outcome is
compared with an independent model built from the T0042 fixture
(expected identities and failure classes), never with the runtime
itself.

- happy: every fixture position survives the restart bit-identically
  (same projection, same field order, same JSON bytes), under several
  PYTHONHASHSEED values;
- boundary: counters never reach identity; each single-field change is
  visible after the restart; additive fields are ignored;
- malformed: every malformed fixture FEN and every tampered persisted
  record (bad field value, missing field, renamed same-arity key,
  non-str value, trailing newline, wrong container) fails closed with a
  FRESH typed VariantError (no __cause__, no __context__) and the
  declared failure class, in the fresh process as in this one;
- rollback: the runtime is stateless - a rejected call between two
  accepted ones changes nothing, and the unknown-variant path fails
  closed instead of coercing to standard;
- hostile types at each boundary (container, keys in three str-subclass
  forms, canonical and nested values) fail closed or are ignored with
  an empty hostile-call log and unchanged input;
- one-edit source mutants of tools/variant_runtime.py are pinned red,
  run in the fresh process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import variant_runtime as rt  # noqa: E402

SOURCE_PATH = ROOT / "tools" / "variant_runtime.py"
SOURCE = SOURCE_PATH.read_text()
CASES = json.loads((ROOT / "tests" / "fixtures" / "variant" / "cases.json").read_text())
FIELDS = ("variant", "board", "side_to_move", "castling_rights", "en_passant")
HASH_SEEDS = ("0", "1", "4242")


# -- independent model ------------------------------------------------------------


def _model_identity(variant, fen):
    """The expected identity straight from the FEN text: the first four
    space-separated fields, counters dropped."""
    board, side, castling, ep = fen.split(" ")[:4]
    return dict(zip(FIELDS, (variant, board, side, castling, ep), strict=True))


def _err(failure_class, code=None):
    if code is None:
        code = "illegal_position" if failure_class == "illegal_position" else "malformed_request"
    return {"err": code, "fc": failure_class, "typed": True, "fresh": True}


def _ok(identity):
    return {"ok": identity, "text": json.dumps(identity)}


HAPPY = [(c["variant"], c["fen"], c["expect_identity"]) for c in CASES["happy"]]
MALFORMED = [
    (c["name"], c.get("variant", "standard"), c["fen"], c["expect_failure"])
    for c in CASES["malformed"]
]


# -- the runner (runs in-process or in the fresh child) ------------------------------


HOSTILE = []


class _Armed:
    on = False


class SK(str):
    """Plain str subclass."""


class EqRaises(str):
    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("eq")
            raise RuntimeError("hostile __eq__")
        return str.__eq__(self, other)

    def __hash__(self):
        return str.__hash__(self)


class HashCollides:
    """Hashes like a canonical field name; comparing it runs user code."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("hk-eq")
            raise RuntimeError("hostile __eq__")
        return NotImplemented


class ListSub(list):
    pass


class DictSub(dict):
    pass


class LyingDict(dict):
    """Dict subclass whose accessors lie; any call is logged."""

    def _log(self, name):
        if _Armed.on:
            HOSTILE.append(name)

    def __getitem__(self, key):
        self._log("getitem")
        return "standard"

    def __contains__(self, key):
        self._log("contains")
        return True

    def keys(self):
        self._log("keys")
        return list(FIELDS)

    def __iter__(self):
        self._log("iter")
        return iter(FIELDS)

    def get(self, key, default=None):
        self._log("get")
        return "standard"


def _key(form, name):
    return {"plain": SK, "eq-raises": EqRaises, "hash-collides": HashCollides}[form](name)


def _outcome(module, call):
    try:
        value = call()
    except BaseException as exc:  # noqa: BLE001 - classified below
        typed = type(exc) is module.VariantError
        return {
            "err": getattr(exc, "code", type(exc).__name__) if typed else type(exc).__name__,
            "fc": getattr(exc, "failure_class", None) if typed else None,
            "typed": typed,
            "fresh": exc.__cause__ is None and exc.__context__ is None,
        }
    return {"ok": value, "text": json.dumps(value)}


def _hostile_value(label):
    """(record, expected) for an in-process hostile row built from a
    valid identity; expected None means accept with the canonical
    identity."""
    base = _model_identity(*HAPPY[0][:2])
    if label == "container-list-subclass":
        return ListSub(base.items()), _err(None)
    if label == "container-dict-subclass":
        return DictSub(base), _err(None)
    if label == "container-lying-dict":
        return LyingDict(base), _err(None)
    if label == "container-pairs":
        return list(base.items()), _err(None)
    if label.startswith("key-"):
        _, form, field = label.split(":")
        return {(_key(form, k) if k == field else k): v for k, v in base.items()}, _err(None)
    if label.startswith("extra-key-"):
        form = label.split(":")[1]
        # an additive key that is not an exact str (the hash-collides one
        # shadows a canonical name without being equal to it)
        name = "variant" if form == "hash-collides" else "x_extra"
        return {**base, _key(form, name): "standard"}, _err(None)
    if label.startswith("value-"):
        field = label.split(":")[1]
        return {**base, field: SK(base[field])}, _err(None)
    if label == "nested-additive-str-subclass":
        return {**base, "x_note": [SK("a"), {"k": SK("b")}]}, None
    raise AssertionError(label)


HOSTILE_LABELS = (
    [
        "container-list-subclass",
        "container-dict-subclass",
        "container-lying-dict",
        "container-pairs",
        "nested-additive-str-subclass",
    ]
    + [f"key-:{form}:{f}" for form in ("plain", "eq-raises", "hash-collides") for f in FIELDS]
    + [f"extra-key-:{form}" for form in ("plain", "eq-raises", "hash-collides")]
    + [f"value-:{f}" for f in FIELDS]
)


def _snap(obj):
    """Structure snapshot that calls no overridden accessor."""
    if isinstance(obj, dict):
        return (
            type(obj),
            [
                (type(k), str(k) if isinstance(k, str) else k.name, _snap(v))
                for k, v in dict.items(obj)
            ],
        )
    if isinstance(obj, list):
        return (type(obj), [_snap(v) for v in list.__iter__(obj)])
    return (type(obj), obj)


def _run_step(module, step):
    do = step["do"]
    if do == "parse":
        return _outcome(
            module, lambda: module.identity(module.parse_position(step["variant"], step["fen"]))
        )
    if do == "load":
        return _outcome(module, lambda: module.project_additive(json.loads(step["text"])))
    if do == "hostile":
        record, _ = _hostile_value(step["label"])
        before = _snap(record)
        HOSTILE.clear()
        _Armed.on = True
        try:
            out = _outcome(module, lambda: module.project_additive(record))
        finally:
            _Armed.on = False
        out["hostile"] = list(HOSTILE)
        out["unchanged"] = _snap(record) == before
        return out
    if do == "mutated-position":
        position = module.parse_position(step["variant"], step["fen"])
        value = SK(step["value"]) if step.get("sk") else step["value"]
        if step["how"] == "replace":
            position = replace(position, **{step["field"]: value})
        else:
            object.__setattr__(position, step["field"], value)
        return _outcome(module, lambda: module.identity(position))
    raise AssertionError(do)


def _source_mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"tools._t0047_mutant_{name}")
    module.__file__ = str(SOURCE_PATH)
    # dataclasses resolves the class module through sys.modules
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    finally:
        del sys.modules[module.__name__]
    return module


def _child_main():
    payload = json.load(sys.stdin)
    name = payload.get("mutant")
    module = rt if name is None else _source_mutant(name, MUTANTS.get(name, []))
    json.dump([_run_step(module, s) for s in payload["steps"]], sys.stdout)


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0047_variant_integration_restart import _child_main; _child_main()"
)


def _restart(steps, mutant=None, hash_seed="0", raw=False):
    run = subprocess.run(
        [sys.executable, "-c", _CHILD, str(ROOT)],
        input=json.dumps({"mutant": mutant, "steps": steps}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=dict(os.environ, PYTHONHASHSEED=hash_seed),
        timeout=100,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout if raw else json.loads(run.stdout)


def _local(steps, module=rt):
    return json.loads(json.dumps([_run_step(module, s) for s in steps]))


# -- scenario corpus: (steps, expected) ----------------------------------------------


def _persisted(variant, fen):
    """What the parent persists: the identity JSON text, computed from
    the MODEL (the parent-side runtime is checked against it too)."""
    return json.dumps(_model_identity(variant, fen))


def _tampered_rows():
    """Persisted records altered after the parent wrote them."""
    variant, fen, ident = HAPPY[0]
    rows = []
    bad_values = {
        "board": ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNX", "bad_board"),
        "side_to_move": ("x", "bad_side"),
        "castling_rights": ("KQkqK", "bad_castling"),
        "en_passant": ("e9", "bad_en_passant"),
    }
    for field, (value, fc) in bad_values.items():
        rows.append((f"bad-{field}", json.dumps({**ident, field: value}), _err(fc)))
        # trailing newline: a regex with `$` would accept it
        rows.append(
            (f"newline-{field}", json.dumps({**ident, field: ident[field] + "\n"}), _err(fc))
        )
    rows.append(("newline-variant", json.dumps({**ident, "variant": "standard\n"}), _err(None)))
    rows.append(("unknown-variant", json.dumps({**ident, "variant": "crazyhouse"}), _err(None)))
    for field in FIELDS:
        rows.append(
            (
                f"missing-{field}",
                json.dumps({k: v for k, v in ident.items() if k != field}),
                _err(None),
            )
        )
        # same arity, renamed key: a length compare would miss it
        rows.append(
            (
                f"renamed-{field}",
                json.dumps({(k.upper() if k == field else k): v for k, v in ident.items()}),
                _err(None),
            )
        )
        for label, value in (("null", None), ("int", 0), ("list", []), ("bool", True)):
            rows.append((f"{label}-{field}", json.dumps({**ident, field: value}), _err(None)))
    for label, text in (
        ("list", json.dumps(list(ident.items()))),
        ("null", "null"),
        ("string", json.dumps("standard")),
    ):
        rows.append((f"container-{label}", text, _err(None)))
    # legal FEN syntax, illegal position: restart must not launder it
    rows.append(
        (
            "illegal-position",
            json.dumps({**ident, "board": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQ1BNR"}),
            _err("illegal_position"),
        )
    )
    # additive fields ride along and are ignored, including nested ones
    rows.append(
        ("additive", json.dumps({**ident, "x_hint": {"a": [1, "b"]}, "zz": None}), _ok(ident))
    )
    return rows


TAMPERED = _tampered_rows()


def _scenario():
    steps, want, labels = [], [], []

    def add(label, step, expected):
        labels.append(label)
        steps.append(step)
        want.append(expected)

    for variant, fen, ident in HAPPY:
        assert _model_identity(variant, fen) == ident
        add(f"happy-parse:{fen}", {"do": "parse", "variant": variant, "fen": fen}, _ok(ident))
        add(f"happy-load:{fen}", {"do": "load", "text": _persisted(variant, fen)}, _ok(ident))
    for case in CASES["boundary"]:
        if "pair" not in case:
            continue
        for member in case["pair"]:
            ident = _model_identity(member["variant"], member["fen"])
            if member["variant"] not in rt._REGISTRY:
                continue
            add(
                f"boundary:{case['name']}:{member['fen']}",
                {"do": "parse", "variant": member["variant"], "fen": member["fen"]},
                _ok(ident),
            )
    for name, variant, fen, fc in MALFORMED:
        add(f"malformed:{name}", {"do": "parse", "variant": variant, "fen": fen}, _err(fc))
    for name, text, expected in TAMPERED:
        add(f"tampered:{name}", {"do": "load", "text": text}, expected)
    unknown = next(c for c in CASES["rollback"] if c["kind"] == "unknown-variant")
    add(
        "rollback:unknown-variant",
        {"do": "parse", "variant": unknown["input"]["variant"], "fen": unknown["input"]["fen"]},
        _err(None, unknown["expect_failure"]),
    )
    additive = next(c for c in CASES["rollback"] if c["kind"] == "additive-fields")
    base = next(c for c in CASES["happy"] if c["name"] == additive["base_case"])
    add(
        "rollback:additive",
        {"do": "load", "text": json.dumps({**base["expect_identity"], **additive["extra_fields"]})},
        _ok(base["expect_identity"]),
    )
    # stateless: the first happy call again, after every rejection above
    variant, fen, ident = HAPPY[0]
    add("rollback:after-rejections", {"do": "parse", "variant": variant, "fen": fen}, _ok(ident))
    add(
        "rollback:reload-after-rejections",
        {"do": "load", "text": _persisted(variant, fen)},
        _ok(ident),
    )
    # a mutated Position is revalidated, never trusted
    for how in ("replace", "setattr"):
        for field, value, sk, fc in (
            ("board", "8/8/8/8/8/8/8/8", False, "illegal_position"),
            ("side_to_move", "w", True, None),
            ("board", ident["board"], True, None),
            ("variant", "crazyhouse", False, None),
            ("en_passant", "e3\n", False, "bad_en_passant"),
        ):
            add(
                f"mutated:{how}:{field}:{'sk' if sk else value}",
                {
                    "do": "mutated-position",
                    "variant": variant,
                    "fen": fen,
                    "how": how,
                    "field": field,
                    "value": value,
                    "sk": sk,
                },
                _err(fc),
            )
    for label in HOSTILE_LABELS:
        _, expected = _hostile_value(label)
        full = dict(_ok(_model_identity(*HAPPY[0][:2])) if expected is None else expected)
        full["hostile"] = []
        full["unchanged"] = True
        add(f"hostile:{label}", {"do": "hostile", "label": label}, full)
    return steps, want, labels


STEPS, WANT, LABELS = _scenario()


def _diff(got, want=WANT):
    assert len(got) == len(want)
    return [lbl for lbl, g, w in zip(LABELS, got, want, strict=True) if g != w]


# -- tests ----------------------------------------------------------------------------


def test_scenario_is_not_vacuous():
    kinds = {lbl.split(":")[0] for lbl in LABELS}
    assert kinds >= {
        "happy-parse",
        "happy-load",
        "boundary",
        "malformed",
        "tampered",
        "rollback",
        "mutated",
        "hostile",
    }
    assert sum(1 for w in WANT if "ok" in w) >= 2 * len(HAPPY)
    assert {w.get("fc") for w in WANT if "err" in w} >= {
        None,
        "bad_board",
        "bad_side",
        "bad_castling",
        "bad_en_passant",
        "illegal_position",
        "wrong_field_count",
        "bad_counters",
    }


def test_parent_process_matches_the_model():
    assert _diff(_local(STEPS)) == []


@pytest.mark.parametrize("hash_seed", HASH_SEEDS)
def test_fresh_process_matches_the_model(hash_seed):
    assert _diff(_restart(STEPS, hash_seed=hash_seed)) == []


def test_restart_output_is_byte_identical_across_hash_seeds():
    outputs = {_restart(STEPS, hash_seed=seed, raw=True) for seed in HASH_SEEDS}
    assert len(outputs) == 1


def test_persisted_text_round_trips_bit_identically():
    """identity() in the parent, persisted as JSON, reloaded and
    projected in a fresh process: same dict, same field order, same
    bytes; the parent's own persisted text equals the model's."""
    steps, texts = [], []
    for variant, fen, ident in HAPPY:
        text = json.dumps(rt.identity(rt.parse_position(variant, fen)))
        assert text == json.dumps(ident)
        texts.append(text)
        steps.append({"do": "load", "text": text})
    got = _restart(steps)
    assert [g["text"] for g in got] == texts
    assert [list(g["ok"]) for g in got] == [list(FIELDS)] * len(HAPPY)


def test_returned_projection_is_detached():
    record = {**_model_identity(*HAPPY[0][:2]), "x": 1}
    out = rt.project_additive(record)
    out["board"] = "tampered"
    assert record["board"] != "tampered"
    assert rt.project_additive(record) != out


# -- mutants: one edit each to tools/variant_runtime.py, run in the fresh process -----

MUTANTS = {
    "key-guard-off": [("        if type(key) is not str:\n", "        if False:\n")],
    "key-guard-isinstance": [
        ("        if type(key) is not str:\n", "        if not isinstance(key, str):\n")
    ],
    "validate-fields-isinstance": [
        ("        if type(v) is not str:\n", "        if not isinstance(v, str):\n")
    ],
    "container-isinstance": [
        ("    if type(record) is not dict:\n", "    if not isinstance(record, dict):\n")
    ],
    "revalidate-off-additive": [
        (
            '    _validate_fields(\n        record["variant"],',
            '    (lambda *a: None)(\n        record["variant"],',
        )
    ],
    "revalidate-off-identity": [
        (
            "    _validate_fields(\n        position.variant,",
            "    (lambda *a: None)(\n        position.variant,",
        )
    ],
    "chained-variant-error": [
        (
            "        failed = str(exc)\n",
            '        raise VariantError(code=str(exc).split(":", 1)[0], message=str(exc))\n',
        )
    ],
    "chained-fen-error": [
        (
            "        failed = (exc.failure_class, str(exc))\n",
            '        raise VariantError(code="malformed_request", '
            "failure_class=exc.failure_class, message=str(exc)) from exc\n",
        )
    ],
    "fen-class-dropped": [
        (
            "            failure_class=cls,\n            message=message,",
            "            message=message,",
        )
    ],
    "illegal-code-collapsed": [
        ('_CODE_FOR_CLASS = {"illegal_position": "illegal_position"}', "_CODE_FOR_CLASS = {}")
    ],
    "projection-keeps-extras": [
        (
            '    return _projection(record["variant"], record["board"],',
            '    return {**record} or _projection(record["variant"], record["board"],',
        )
    ],
    "missing-check-off": [("    if missing:\n", "    if False:\n")],
}


# Edits that must NOT change behavior. field-type-isinstance: a str
# subclass value that passes project_additive's own check reaches
# _validate_fields next, whose exact-str check raises the same fresh
# malformed_request (failure class None); between the two checks nothing
# else reads the record (exact dict, exact-str keys) or fails otherwise.
EQUIVALENT_EDITS = {
    "field-type-isinstance": [
        (
            "        if type(record[f]) is not str:\n",
            "        if not isinstance(record[f], str):\n",
        )
    ],
}


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name):
    assert _diff(_local(STEPS, _source_mutant(name, EQUIVALENT_EDITS[name]))) == []


def test_mutants_apply_exactly_once():
    for name, edits in MUTANTS.items():
        _source_mutant(name, edits)


def test_identity_source_copy_is_green():
    """The unedited source, exec'd the same way as every mutant."""
    assert "_identity" not in MUTANTS
    assert _diff(_restart(STEPS, mutant="_identity")) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_after_restart(name):
    assert _diff(_restart(STEPS, mutant=name)) != [], name


# -- forges: every class the module raises or catches -------------------------------


def _forge_outcomes(module):
    """Replace the module's lint hooks with forgers: each declared
    failure class via _check_fen (parse and load), the registry error via
    check_variant_id, and a foreign RuntimeError that must pass through
    unmapped (the module maps only its own lint's ContractError)."""
    lint_error = module._ContractError
    variant, fen, ident = HAPPY[0]
    out = {}
    real_fen, real_vid = module._check_fen, module.check_variant_id
    try:
        for fc in sorted(rt.FAILURE_CLASSES):

            def forged(*_a, fc=fc):
                raise lint_error(f"forged {fc}", failure_class=fc)

            module._check_fen = forged
            out[f"parse:{fc}"] = _outcome(module, lambda: module.parse_position(variant, fen))
            out[f"load:{fc}"] = _outcome(module, lambda: module.project_additive(dict(ident)))
        module._check_fen = real_fen

        def forged_vid(*_a):
            raise lint_error("malformed_request: forged registry failure")

        def forged_vid_other_code(*_a):
            # a declared code other than malformed_request: the mapped
            # code must come from the lint message, never be flattened
            raise lint_error("unknown_variant: forged registry failure")

        def foreign(*_a):
            raise RuntimeError("foreign")

        for label, hook in (
            ("registry", forged_vid),
            ("registry-code", forged_vid_other_code),
            ("foreign-registry", foreign),
        ):
            module.check_variant_id = hook
            out[f"parse:{label}"] = _outcome(module, lambda: module.parse_position(variant, fen))
            out[f"load:{label}"] = _outcome(module, lambda: module.project_additive(dict(ident)))
        module.check_variant_id = real_vid

        module._check_fen = foreign
        out["parse:foreign-fen"] = _outcome(module, lambda: module.parse_position(variant, fen))
        out["load:foreign-fen"] = _outcome(module, lambda: module.project_additive(dict(ident)))
        module._check_fen = real_fen

        # the class the module itself raises, forged through each hook:
        # the module maps only its own lint's ContractError, so a forged
        # VariantError passes through as the SAME object, unchanged
        for hook_name in ("_check_fen", "check_variant_id"):
            for path, call in (
                ("parse", lambda: module.parse_position(variant, fen)),
                ("load", lambda: module.project_additive(dict(ident))),
            ):
                forged_error = module.VariantError(
                    code="illegal_position",
                    message="forged",
                    failure_class="illegal_position",
                )

                def raise_forged(*_a, e=forged_error):
                    raise e

                setattr(module, hook_name, raise_forged)
                try:
                    call()
                    got = {"ok": True}
                except BaseException as exc:  # noqa: BLE001 - classified below
                    got = {
                        "same": exc is forged_error,
                        "err": getattr(exc, "code", None),
                        "fc": getattr(exc, "failure_class", None),
                        "typed": type(exc) is module.VariantError,
                    }
                module._check_fen, module.check_variant_id = real_fen, real_vid
                out[f"{path}:own-class-via-{hook_name}"] = got
    finally:
        module._check_fen, module.check_variant_id = real_fen, real_vid
    return out


def _forge_expected():
    want = {}
    for fc in sorted(rt.FAILURE_CLASSES):
        want[f"parse:{fc}"] = _err(fc)
        want[f"load:{fc}"] = _err(fc)
    foreign = {"err": "RuntimeError", "fc": None, "typed": False, "fresh": True}
    for path in ("parse", "load"):
        want[f"{path}:registry"] = _err(None)
        want[f"{path}:registry-code"] = _err(None, "unknown_variant")
        want[f"{path}:foreign-registry"] = foreign
        want[f"{path}:foreign-fen"] = foreign
        for hook_name in ("_check_fen", "check_variant_id"):
            want[f"{path}:own-class-via-{hook_name}"] = {
                "same": True,
                "err": "illegal_position",
                "fc": "illegal_position",
                "typed": True,
            }
    return want


def test_forged_errors_map_fresh_and_foreign_errors_pass_through():
    assert _forge_outcomes(_source_mutant("_forge", [])) == _forge_expected()


FORGE_MUTANTS = {
    "fen-catches-own-class-too": [
        (
            "    except _ContractError as exc:\n        failed = (exc.failure_class, str(exc))\n",
            "    except (_ContractError, VariantError) as exc:\n"
            "        failed = (exc.failure_class, str(exc))\n",
        )
    ],
    "variant-catches-own-class-too": [
        (
            "    except _ContractError as exc:\n        failed = str(exc)\n",
            "    except (_ContractError, VariantError) as exc:\n        failed = str(exc)\n",
        )
    ],
    "variant-catches-everything": [
        (
            "    except _ContractError as exc:\n        failed = str(exc)\n",
            "    except Exception as exc:\n        failed = str(exc)\n",
        )
    ],
    "variant-code-flattened": [
        (
            '        raise VariantError(code=failed.split(":", 1)[0], message=failed)\n',
            '        raise VariantError(code="malformed_request", message=failed)\n',
        )
    ],
    "catches-everything": [
        (
            "    except _ContractError as exc:\n        failed = (exc.failure_class, str(exc))\n",
            "    except Exception as exc:\n"
            "        failed = (getattr(exc, 'failure_class', None), str(exc))\n",
        )
    ],
    "raises-inside-except": [
        (
            "        failed = (exc.failure_class, str(exc))\n",
            "        raise VariantError(code=_CODE_FOR_CLASS.get(exc.failure_class, "
            "'malformed_request'), failure_class=exc.failure_class, message=str(exc))\n",
        )
    ],
}


@pytest.mark.parametrize("name", sorted(FORGE_MUTANTS))
def test_forge_mutant_is_red(name):
    got = _forge_outcomes(_source_mutant(name, FORGE_MUTANTS[name]))
    assert got != _forge_expected()
