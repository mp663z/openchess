"""T0443: integration and restart depth for the shipped control-plane
version comparator (server/control_plane_versioning.py).

Distinct from T0442's in-process fuzz/fault battery: here every comparison
crosses a process boundary. Documents generated from the contract grammar
and the shipped control-plane.yaml source are attested by the independent
T0419 strict derivation, persisted as JSON text, and reloaded in a FRESH
Python process whose only inputs are those bytes. Verdicts, typed refusals,
purity and the derivation binding are checked against expectations fixed by
construction, under several PYTHONHASHSEED values, after abrupt process
loss, and against one-edit source mutants executed in the fresh process.

- happy: additive MINOR chains - generated and over the shipped source -
  survive restart bit-identically under every hash seed, and a valid-source
  breaking change is blessed under a MAJOR bump with a new base path;
- boundary: minor edges (zero, ceiling, equal minors), a same-major
  base-path change answered False, cross-major freedom with a minor reset,
  and key-order independence after serialization;
- malformed: hostile minors and tampered persisted documents (renamed
  same-arity keys, missing or extra keys, a trailing-newline path, a
  wrong-case base path, a nonfinite example, wrong containers, unparseable
  text) fail closed in the fresh process with a fresh typed
  malformed_version_request, while opaque NaN and deep finite metadata stay
  valid across the boundary;
- rollback: the comparator is stateless - refusals interleaved with
  accepted verdicts change nothing, backward MINOR and MAJOR steps refuse
  across restart, a process killed mid-scenario leaves no residue, and a
  fresh process reproduces the model exactly with an untouched working
  directory;
- integration: the T0419 strict derivation and the comparator agree on
  source validity inside the fresh process - valid sources attest, tampered
  ones refuse;
- one-edit source mutants of server/control_plane_versioning.py are pinned
  red in the fresh process, each at its declared witness row; one
  documented equivalent edit stays green.

Declared contract choices (restated from data/contracts/control-plane.yaml,
never from production output): the comparator blesses forward steps only -
a consumer MINOR rollback needs no migration at runtime, but a backward
comparison is a typed refusal, never a False verdict; cross-major
comparisons claim no compatibility and answer True at any minors; tampered
persisted documents fail closed, while key reordering and opaque NaN or
deep finite metadata are not tampering. Test-only: no production semantics
change.
"""

from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import control_plane_versioning as cpv  # noqa: E402
from tests.test_t0419_openapi_contract import OpenApiError, derive  # noqa: E402
from tests.test_t0442_versioning_fuzz_fault import (  # noqa: E402
    CEILING,
    _Alarm,
    _Hostile,
    _HostileDict,
    _HostileInt,
    _HostileList,
    _HostileStr,
    _letters,
    _major_bumped,
    _probe_doc,
    _safe_snapshot,
    attest,
    break_doc,
    evolve,
    random_document,
)

SOURCE_PATH = ROOT / "server" / "control_plane_versioning.py"
SOURCE = SOURCE_PATH.read_text()
SHIPPED = json.loads(
    json.dumps(yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text()))
)
HASH_SEEDS = ("0", "1", "4242")
SUBPROCESS_TIMEOUT = 120
WAIT_TIMEOUT = 25.0
FAILURE = "malformed_version_request"
CODE = "malformed_request"
MESSAGE = "invalid version request"


# -- expected outcomes, fixed by construction ----------------------------------------


def _verdict(value):
    return {"kind": "verdict", "value": value, "unchanged": True}


def _refusal():
    return {
        "kind": "refusal",
        "err": CODE,
        "fc": FAILURE,
        "typed": True,
        "fresh": True,
        "msg": MESSAGE,
        "retry": False,
        "unchanged": True,
    }


def _hostile_refusal():
    out = _refusal()
    out["operator_calls"] = 0
    return out


# -- the runner (executes in this process or in the fresh child) ----------------------


def _canon(doc):
    return json.dumps(doc, sort_keys=True)


def _classify(module, exc):
    typed = type(exc) is module.VersionError
    return {
        "kind": "refusal" if typed else "escape",
        "err": getattr(exc, "code", type(exc).__name__) if typed else type(exc).__name__,
        "fc": getattr(exc, "failure_class", None) if typed else None,
        "typed": typed,
        "fresh": exc.__cause__ is None and exc.__context__ is None,
        "msg": str(exc) if typed else None,
        "retry": getattr(exc, "retryable", None) if typed else None,
    }


def _verdict_outcome(value):
    if type(value) is bool:
        return {"kind": "verdict", "value": value}
    return {"kind": "non-bool", "type": type(value).__name__}


def _compare_step(module, old, new, old_minor, new_minor):
    before = (_canon(old), _canon(new))
    try:
        value = module.compare(old, new, old_minor, new_minor)
    except BaseException as exc:  # classified below, never re-raised
        out = _classify(module, exc)
    else:
        out = _verdict_outcome(value)
    out["unchanged"] = (_canon(old), _canon(new)) == before
    return out


def _tampered_step(module, step):
    try:
        old = json.loads(step["old_text"])
        new = json.loads(step["new_text"])
    except json.JSONDecodeError:
        return {"kind": "parse-error"}
    return _compare_step(module, old, new, step["old_minor"], step["new_minor"])


def _attest_step(step):
    try:
        derive(step["doc"])
    except OpenApiError:
        return {"attest": "refused"}
    return {"attest": "ok"}


def _first_fields(doc):
    for group in doc["areas"].values():
        for op in group["ops"].values():
            for side in ("request", "response"):
                if op[side]["fields"]:
                    return op[side]["fields"]
    raise AssertionError("no non-empty fields map")


def _hostile_inputs(step):
    old = copy.deepcopy(step["old"])
    new = copy.deepcopy(step["new"])
    old_minor, new_minor = step["old_minor"], step["new_minor"]
    label = step["label"]
    if label == "container-dict-subclass":
        old = _HostileDict(old)
    elif label == "container-list-subclass":
        errors = old["contract"]["transport"]["errors"]
        errors["closed_enum"] = _HostileList(errors["closed_enum"])
    elif label == "str-subclass-base-path":
        versioning = old["contract"]["versioning"]
        versioning["base_path"] = _HostileStr(versioning["base_path"])
    elif label == "hostile-object-opaque":
        old["contract"]["privacy"]["logs"] = _Hostile()
    elif label == "hostile-str-field-key":
        fields = _first_fields(old)
        key = next(iter(fields))
        items = list(fields.items())
        fields.clear()
        for name, spec in items:
            if name == key:
                dict.__setitem__(fields, _HostileStr(name), spec)
            else:
                fields[name] = spec
    elif label == "cycle-opaque":
        cycle = []
        cycle.append(cycle)
        old["contract"]["privacy"]["logs"] = cycle
    elif label == "tuple-opaque":
        old["contract"]["privacy"]["logs"] = (1, 2)
    elif label == "set-opaque":
        old["contract"]["privacy"]["logs"] = {"a", "b"}
    elif label == "bytes-opaque":
        old["contract"]["privacy"]["logs"] = b"bytes"
    elif label == "complex-opaque":
        old["contract"]["privacy"]["logs"] = 1j
    elif label == "int-subclass-minor":
        old_minor = _HostileInt(old_minor)
    else:
        raise AssertionError(label)
    return old, new, old_minor, new_minor


def _hostile_step(module, step):
    old, new, old_minor, new_minor = _hostile_inputs(step)
    before = (_safe_snapshot(old), _safe_snapshot(new))
    _Alarm.calls = []
    _Hostile.calls = 0
    _Alarm.armed = True
    try:
        try:
            value = module.compare(old, new, old_minor, new_minor)
        except BaseException as exc:  # classified below, never re-raised
            out = _classify(module, exc)
        else:
            out = _verdict_outcome(value)
    finally:
        _Alarm.armed = False
    out["operator_calls"] = len(_Alarm.calls) + _Hostile.calls
    out["unchanged"] = (_safe_snapshot(old), _safe_snapshot(new)) == before
    return out


def _run_step(module, step):
    do = step["do"]
    if do == "compare":
        return _compare_step(module, step["old"], step["new"], step["old_minor"], step["new_minor"])
    if do == "tampered":
        return _tampered_step(module, step)
    if do == "attest":
        return _attest_step(step)
    if do == "hostile":
        return _hostile_step(module, step)
    raise AssertionError(do)


# -- scenario corpus: (steps, expected, labels) ---------------------------------------


def _cmp_step(old, new, old_minor, new_minor):
    return {
        "do": "compare",
        "old": old,
        "new": new,
        "old_minor": old_minor,
        "new_minor": new_minor,
    }


def _text_step(old_text, new_text, old_minor, new_minor):
    return {
        "do": "tampered",
        "old_text": old_text,
        "new_text": new_text,
        "old_minor": old_minor,
        "new_minor": new_minor,
    }


HOSTILE_LABELS = (
    "container-dict-subclass",
    "container-list-subclass",
    "str-subclass-base-path",
    "hostile-object-opaque",
    "hostile-str-field-key",
    "cycle-opaque",
    "tuple-opaque",
    "set-opaque",
    "bytes-opaque",
    "complex-opaque",
    "int-subclass-minor",
)


def _scenario():
    rng = random.Random(443)
    steps, want, labels = [], [], []

    def add(label, step, expected):
        assert label not in labels
        labels.append(label)
        steps.append(step)
        want.append(expected)

    # generated grammar documents: additive MINOR chains, rollback refusals interleaved
    chains = []
    for chain in range(3):
        tag = f"r{_letters(chain)}"
        doc = random_document(rng, tag)
        attest(doc)
        add(f"attest:generated-{chain}", {"do": "attest", "doc": doc}, {"attest": "ok"})
        current, minor = doc, 0
        for index in range(2):
            following = evolve(rng, current, f"{tag}{_letters(index)}")
            attest(following)
            bump = rng.randrange(1, 5)
            add(
                f"happy:chain{chain}-step{index}",
                _cmp_step(current, following, minor, minor + bump),
                _verdict(True),
            )
            add(
                f"rollback:chain{chain}-step{index}",
                _cmp_step(following, current, minor + bump, minor),
                _refusal(),
            )
            current, minor = following, minor + bump
        add(f"happy:chain{chain}-end-to-end", _cmp_step(doc, current, 0, minor), _verdict(True))
        add(f"rollback:chain{chain}-end-to-end", _cmp_step(current, doc, minor, 0), _refusal())
        chains.append((doc, current, minor))
    base = chains[0][0]
    major = int(base["contract"]["versioning"]["base_path"].rsplit("/v", 1)[1])

    # shipped source document integration
    add("attest:shipped", {"do": "attest", "doc": SHIPPED}, {"attest": "ok"})
    shipped_evolved = copy.deepcopy(SHIPPED)
    shipped_evolved["areas"]["identity"]["ops"]["register"]["response"]["fields"]["extra_flag"] = {
        "type": "boolean",
        "required": False,
    }
    add("happy:shipped-additive", _cmp_step(SHIPPED, shipped_evolved, 0, 1), _verdict(True))
    add("rollback:shipped-additive", _cmp_step(shipped_evolved, SHIPPED, 1, 0), _refusal())
    shipped_broken = copy.deepcopy(SHIPPED)
    shipped_broken["areas"]["quota"]["ops"]["reserve"]["path"] = "/quota/hold"
    add("attest:shipped-broken", {"do": "attest", "doc": shipped_broken}, {"attest": "ok"})
    add("breaking:shipped-path-change", _cmp_step(SHIPPED, shipped_broken, 0, 1), _verdict(False))
    add(
        "happy:shipped-path-change-major",
        _cmp_step(SHIPPED, _major_bumped(shipped_broken), 0, 0),
        _verdict(True),
    )

    # boundary rows on the generated base document
    add(
        "boundary:identical-ceiling",
        _cmp_step(base, copy.deepcopy(base), CEILING, CEILING),
        _verdict(True),
    )
    add("boundary:identical-zero", _cmp_step(base, copy.deepcopy(base), 0, 0), _verdict(True))
    additive_equal = evolve(rng, base, "eq")
    attest(additive_equal)
    add("boundary:additive-equal-minor", _cmp_step(base, additive_equal, 5, 5), _verdict(True))
    broken = break_doc(rng, base, "br")
    attest(broken)
    add("boundary:breaking-same-major", _cmp_step(base, broken, 0, 1), _verdict(False))
    add(
        "boundary:breaking-major-bump", _cmp_step(base, _major_bumped(broken), 7, 0), _verdict(True)
    )
    alt = copy.deepcopy(base)
    alt["contract"]["versioning"]["base_path"] = f"/alt/v{major}"
    attest(alt)
    add("boundary:same-major-other-base-path", _cmp_step(base, alt, 0, 1), _verdict(False))
    add("attest:same-major-other-base-path", {"do": "attest", "doc": alt}, {"attest": "ok"})
    other = random_document(rng, "x")
    other["contract"]["versioning"]["base_path"] = f"/cp/v{major + 1}"
    attest(other)
    add("boundary:cross-major-minor-reset", _cmp_step(base, other, 7, 0), _verdict(True))
    required = copy.deepcopy(base)
    area0 = next(iter(required["areas"]))
    op0 = next(iter(required["areas"][area0]["ops"]))
    required["areas"][area0]["ops"][op0]["response"]["fields"]["tenant"] = {
        "type": "string",
        "required": True,
    }
    attest(required)
    add("breaking:required-addition", _cmp_step(base, required, 0, 1), _verdict(False))

    # malformed minors over identical documents
    for label, minors in (
        ("minor-bool-old", (True, 1)),
        ("minor-bool-new", (0, False)),
        ("minor-float", (0, 1.5)),
        ("minor-negative", (-1, 0)),
        ("minor-above-ceiling", (0, CEILING + 1)),
        ("minor-str", ("1", 2)),
        ("minor-null", (None, 0)),
    ):
        add(
            f"malformed:{label}",
            _cmp_step(base, copy.deepcopy(base), minors[0], minors[1]),
            _refusal(),
        )
    add("rollback:major-downgrade", _cmp_step(_major_bumped(base), base, 0, 0), _refusal())

    # tampered persisted documents: reloaded text fails closed
    evolved_text_doc = evolve(rng, base, "tp")
    attest(evolved_text_doc)
    old_text = json.dumps(base)
    good_text = json.dumps(evolved_text_doc)

    renamed_new = copy.deepcopy(evolved_text_doc)
    renamed_new["AREAS"] = renamed_new.pop("areas")
    add("tampered:renamed-top-key", _text_step(old_text, json.dumps(renamed_new), 0, 1), _refusal())
    add(
        "attest:tampered-renamed-top-key",
        {"do": "attest", "doc": renamed_new},
        {"attest": "refused"},
    )
    missing_contract = copy.deepcopy(evolved_text_doc)
    del missing_contract["contract"]
    add(
        "tampered:missing-contract",
        _text_step(old_text, json.dumps(missing_contract), 0, 1),
        _refusal(),
    )
    add(
        "attest:tampered-missing-contract",
        {"do": "attest", "doc": missing_contract},
        {"attest": "refused"},
    )
    extra_key = copy.deepcopy(evolved_text_doc)
    extra_key["x_extra"] = None
    add("tampered:extra-top-key", _text_step(old_text, json.dumps(extra_key), 0, 1), _refusal())
    newline_path = copy.deepcopy(evolved_text_doc)
    area1 = next(iter(newline_path["areas"]))
    op1 = next(iter(newline_path["areas"][area1]["ops"]))
    newline_path["areas"][area1]["ops"][op1]["path"] += "\n"
    add(
        "tampered:trailing-newline-path",
        _text_step(old_text, json.dumps(newline_path), 0, 1),
        _refusal(),
    )
    add(
        "attest:tampered-newline-path", {"do": "attest", "doc": newline_path}, {"attest": "refused"}
    )
    wrong_case = copy.deepcopy(evolved_text_doc)
    wrong_case["contract"]["versioning"]["base_path"] = "/CP/V2"
    add(
        "tampered:wrong-case-base-path",
        _text_step(old_text, json.dumps(wrong_case), 0, 1),
        _refusal(),
    )
    add(
        "attest:tampered-wrong-case-base",
        {"do": "attest", "doc": wrong_case},
        {"attest": "refused"},
    )
    nan_example = copy.deepcopy(evolved_text_doc)
    nan_example["areas"][area1]["ops"][op1]["response"]["fields"]["nval"] = {
        "type": "number",
        "required": False,
        "example": float("nan"),
    }
    add("tampered:nan-example", _text_step(old_text, json.dumps(nan_example), 0, 1), _refusal())
    add("attest:tampered-nan-example", {"do": "attest", "doc": nan_example}, {"attest": "refused"})

    # opaque NaN and deep finite metadata are not tampering: valid across the boundary
    nan_opaque = copy.deepcopy(base)
    nan_opaque["contract"]["privacy"]["logs"] = float("nan")
    nan_text = json.dumps(nan_opaque)
    add("tampered:opaque-nan-identical", _text_step(nan_text, nan_text, 2, 2), _verdict(True))
    add("attest:opaque-nan", {"do": "attest", "doc": nan_opaque}, {"attest": "ok"})
    deep_opaque = copy.deepcopy(base)
    node = {"logs": "pinned"}
    for _ in range(40):
        node = {"k": [node]}
    deep_opaque["contract"]["privacy"]["logs"] = node
    add(
        "happy:deep-opaque-self",
        _cmp_step(deep_opaque, copy.deepcopy(deep_opaque), 3, 3),
        _verdict(True),
    )
    add("attest:deep-opaque", {"do": "attest", "doc": deep_opaque}, {"attest": "ok"})

    # key order is not semantics, including after serialization
    reordered = copy.deepcopy(base)
    for group in reordered["areas"].values():
        group["ops"] = {key: group["ops"][key] for key in reversed(list(group["ops"]))}
    transport = reordered["contract"]["transport"]
    transport["auth"]["public_operations"] = list(reversed(transport["auth"]["public_operations"]))
    transport["read_only_operations"] = list(reversed(transport["read_only_operations"]))
    add("happy:reordered-keys", _cmp_step(base, reordered, 0, 0), _verdict(True))

    # wrong containers and unparseable persisted text
    for label, text in (("list", "[]"), ("null", "null"), ("string", '"cp"'), ("int", "5")):
        add(f"tampered:container-{label}", _text_step(old_text, text, 0, 1), _refusal())
    add("tampered:unparseable", _text_step(old_text, "{not json", 0, 1), {"kind": "parse-error"})

    # a tampered persisted OLD document is refused symmetrically
    renamed_old = json.loads(old_text)
    renamed_old["AREAS"] = renamed_old.pop("areas")
    add(
        "tampered:old-renamed-top-key",
        _text_step(json.dumps(renamed_old), good_text, 0, 1),
        _refusal(),
    )

    # hostile types at the comparison boundary, constructed in the fresh process
    probe_old = _probe_doc()
    probe_new = copy.deepcopy(probe_old)
    probe_new["areas"]["alpha"]["ops"]["read"]["response"]["fields"]["detail"] = {
        "type": "string",
        "required": False,
    }
    add("happy:probe-additive", _cmp_step(probe_old, probe_new, 0, 1), _verdict(True))
    for label in HOSTILE_LABELS:
        add(
            f"hostile:{label}",
            {
                "do": "hostile",
                "label": label,
                "old": probe_old,
                "new": probe_new,
                "old_minor": 0,
                "new_minor": 1,
            },
            _hostile_refusal(),
        )
    return steps, want, labels


STEPS, WANT, LABELS = _scenario()

PARITY_LABELS = (
    "happy:chain0-step0",
    "boundary:breaking-same-major",
    "rollback:chain0-step0",
    "malformed:minor-bool-old",
    "tampered:trailing-newline-path",
    "tampered:opaque-nan-identical",
    "attest:tampered-renamed-top-key",
    "hostile:cycle-opaque",
    "happy:shipped-additive",
)
PARITY_INDICES = [LABELS.index(label) for label in PARITY_LABELS]


def _diff(got):
    assert len(got) == len(WANT)
    return [
        label
        for label, outcome, expected in zip(LABELS, got, WANT, strict=True)
        if outcome != expected
    ]


def _local(steps, module=cpv):
    return json.loads(json.dumps([_run_step(module, step) for step in steps], sort_keys=True))


# -- the fresh process ----------------------------------------------------------------


def _source_mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"server._t0443_mutant_{name}")
    module.__file__ = str(SOURCE_PATH)
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    return module


def _child_main():
    payload = json.load(sys.stdin)
    name = payload.get("mutant")
    module = cpv if name is None else _source_mutant(name, _edits_for(name))
    pause = payload.get("pause")
    outcomes = []
    for index, step in enumerate(payload["steps"]):
        outcomes.append(_run_step(module, step))
        if pause is not None and index + 1 == pause["after_step"]:
            Path(pause["ready"]).write_text("ready")
            deadline = time.monotonic() + WAIT_TIMEOUT
            while time.monotonic() < deadline and not Path(pause["proceed"]).exists():
                time.sleep(0.01)
    json.dump(outcomes, sys.stdout, sort_keys=True)


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0443_versioning_integration_restart import _child_main; _child_main()"
)


def _env(hash_seed):
    return dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE="1")


def _restart(steps, mutant=None, hash_seed="0", cwd=None):
    run = subprocess.run(
        [sys.executable, "-c", _CHILD, str(ROOT)],
        input=json.dumps({"mutant": mutant, "steps": steps}),
        text=True,
        capture_output=True,
        cwd=cwd or ROOT,
        env=_env(hash_seed),
        timeout=SUBPROCESS_TIMEOUT,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


_RAW_RUNS = {}


def _restart_raw(hash_seed):
    if hash_seed not in _RAW_RUNS:
        run = subprocess.run(
            [sys.executable, "-c", _CHILD, str(ROOT)],
            input=json.dumps({"mutant": None, "steps": STEPS}),
            text=True,
            capture_output=True,
            cwd=ROOT,
            env=_env(hash_seed),
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
        )
        assert run.returncode == 0, run.stderr
        _RAW_RUNS[hash_seed] = run.stdout
    return _RAW_RUNS[hash_seed]


def _wait_for(path, timeout=WAIT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return False


# -- tests -----------------------------------------------------------------------------


def test_scenario_is_not_vacuous():
    assert {step["do"] for step in STEPS} == {"compare", "tampered", "attest", "hostile"}
    assert len(LABELS) == len(STEPS) == len(WANT)
    assert len(set(LABELS)) == len(LABELS)
    verdicts = [w for w in WANT if w.get("kind") == "verdict"]
    assert {w["value"] for w in verdicts} == {True, False}
    assert all(w["unchanged"] for w in verdicts)
    assert len([w for w in WANT if w.get("kind") == "refusal"]) >= 20
    assert any(w.get("kind") == "parse-error" for w in WANT)
    attests = [w for w in WANT if "attest" in w]
    assert {w["attest"] for w in attests} == {"ok", "refused"}
    assert all(w["operator_calls"] == 0 for w in WANT if "operator_calls" in w)
    assert all(w["fresh"] and w["typed"] for w in WANT if w.get("kind") == "refusal")


def test_parent_process_matches_the_model():
    assert _diff(_local(STEPS)) == []


@pytest.mark.parametrize("hash_seed", HASH_SEEDS)
def test_fresh_process_matches_the_model(hash_seed):
    assert _diff(json.loads(_restart_raw(hash_seed))) == []


def test_restart_output_is_byte_identical_across_hash_seeds():
    assert len({_restart_raw(seed) for seed in HASH_SEEDS}) == 1


def test_single_step_processes_match_the_long_lived_process():
    """Statelessness across restart: each representative step re-run alone in
    its own fresh process (a different hash seed, after every sibling step
    ran or refused before it in the long-lived process) is identical."""
    full = json.loads(_restart_raw("0"))
    for index in PARITY_INDICES:
        got = _restart([STEPS[index]], hash_seed="2")
        assert got == [full[index]], LABELS[index]


def test_abrupt_process_loss_mid_scenario_leaves_no_residue(tmp_path):
    """A process SIGKILLed mid-scenario wrote nothing and changed nothing:
    a fresh process over the same persisted inputs reproduces the model."""
    work = tmp_path / "work"
    work.mkdir()
    signals = tmp_path / "signals"
    signals.mkdir()
    ready = signals / "ready"
    payload = {
        "mutant": None,
        "steps": STEPS,
        "pause": {
            "ready": str(ready),
            "proceed": str(signals / "proceed"),
            "after_step": 3,
        },
    }
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(ROOT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=work,
        env=_env("0"),
    )
    child.stdin.write(json.dumps(payload))
    child.stdin.close()
    assert _wait_for(ready)
    child.kill()
    child.wait(timeout=10)
    assert child.returncode != 0
    assert child.stdout.read() == ""
    assert _diff(_restart(STEPS, cwd=work)) == []
    assert os.listdir(work) == []


def test_child_process_writes_nothing_to_its_working_directory(tmp_path):
    _restart(STEPS, hash_seed="1", cwd=tmp_path)
    assert os.listdir(tmp_path) == []


# -- one-edit source mutants, pinned red in the fresh process ---------------------------

MUTANTS = {
    "minor-monotonicity-off": (
        [("        _require(new_minor >= old_minor)\n", "        pass\n")],
        "rollback:chain0-step0",
    ),
    "major-monotonicity-off": (
        [("    _require(new_major >= old_major)\n", "    pass\n")],
        "rollback:major-downgrade",
    ),
    "exact-minor-type-isinstance": (
        [
            (
                "    _require(type(old_minor) is int and type(new_minor) is int)\n",
                "    _require(isinstance(old_minor, int) and isinstance(new_minor, int))\n",
            )
        ],
        "malformed:minor-bool-old",
    ),
    "minor-ceiling-off": (
        [
            (
                "    _require(0 <= old_minor <= 2147483647 and 0 <= new_minor <= 2147483647)\n",
                "    _require(0 <= old_minor and 0 <= new_minor)\n",
            )
        ],
        "malformed:minor-above-ceiling",
    ),
    "match-instead-of-fullmatch": (
        [
            (
                "    _require(type(value) is str and pattern.fullmatch(value) is not None)\n",
                "    _require(type(value) is str and pattern.match(value) is not None)\n",
            )
        ],
        "tampered:trailing-newline-path",
    ),
    "chained-refusal": (
        [
            (
                "        raise VersionError()\n",
                '        raise VersionError() from ValueError("context")\n',
            )
        ],
        "rollback:chain0-step0",
    ),
    "error-message-not-constant": (
        [("        super().__init__(message)\n", '        super().__init__(message + "!")\n')],
        "rollback:chain0-step0",
    ),
    "nan-example-accepted": (
        [
            (
                "        return (type(value) is float and math.isfinite(value)) or (\n",
                "        return type(value) is float or (\n",
            )
        ],
        "tampered:nan-example",
    ),
    "required-additions-accepted": (
        [
            (
                '    return all(spec["required"] is False for name, spec in after.items()'
                " if name not in before)\n",
                "    return True\n",
            )
        ],
        "breaking:required-addition",
    ),
    "impure-compare": (
        [
            (
                "        return _same_major(old, new)\n",
                '        return old["areas"].clear() or _same_major(old, new)\n',
            )
        ],
        "happy:chain0-step0",
    ),
}


# Edits that must NOT change behavior. minor-type-check-operand-order: both
# operands of the conjunction are side-effect-free exact-type checks over
# local parameters - neither raises, mutates, nor depends on the other, so
# the short-circuit order is unobservable and the accepted input set is
# identical.
EQUIVALENT_EDITS = {
    "minor-type-check-operand-order": [
        (
            "    _require(type(old_minor) is int and type(new_minor) is int)\n",
            "    _require(type(new_minor) is int and type(old_minor) is int)\n",
        )
    ],
}


def _edits_for(name):
    if name == "_identity":
        return []
    if name in MUTANTS:
        return MUTANTS[name][0]
    return EQUIVALENT_EDITS[name]


def test_mutants_apply_exactly_once():
    for name, (edits, _witness) in MUTANTS.items():
        _source_mutant(name, edits)
    for name, edits in EQUIVALENT_EDITS.items():
        _source_mutant(name, edits)


def test_identity_source_copy_is_green():
    """The unedited source, exec'd the same way as every mutant."""
    assert "_identity" not in MUTANTS
    assert _diff(_restart(STEPS, mutant="_identity")) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_after_restart_at_its_witness(name):
    """Each one-edit mutant is KILLED in the fresh process, and the measured
    differences include its declared witness row: the behavior the edit
    breaks, never an assumed one."""
    edits, witness = MUTANTS[name]
    diff = _diff(_restart(STEPS, mutant=name))
    assert witness in diff, (name, diff)


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name):
    assert _diff(_restart(STEPS, mutant=name)) == []
