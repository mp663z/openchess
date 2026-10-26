"""T0296: production jobs idempotency runtime proof (server/jobs_idempotency.py).

The amended T0295 red battery is the behavioral proof: it runs against
server.jobs_idempotency with only its three binding lines switched. This
file proves the switch is exactly those three lines, that production
never imports the test package, that its public surface is the
contract's, that production reproduces every fixture row, that every
totality, accept and closure probe passes its reference-derived
pins through production, and that production-source mutants
(the T0295 `_SM` defect classes ported to the production text, plus new
production-only ones) turn red at a pinned first-witness label while an
identity mutant and equivalent edits stay green.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import jobs_idempotency, jobs_queue  # noqa: E402
from server.jobs_idempotency import (  # noqa: E402
    IdempotencyError,
    JobsIdempotencyEngine,
    QueueError,
)
from tests import test_t0293_jobs_idempotency_contract as _reference  # noqa: E402
from tests import test_t0295_idempotency_red as _red  # noqa: E402

RED = ROOT / "tests" / "test_t0295_idempotency_red.py"
PRODUCTION = ROOT / "server" / "jobs_idempotency.py"
CASES = json.loads((ROOT / "tests" / "fixtures" / "jobs_idempotency" / "cases.json").read_text())
ORACLE_LINES = (
    "QueueError = _reference.QueueError\n"
    "JobsIdempotencyEngine = _reference.JobsIdempotencyEngine\n"
    "IdempotencyError = _reference.IdempotencyError\n"
)
_IMP = '__import__("server.jobs_idempotency").jobs_idempotency.'
PRODUCTION_LINES = (
    f"QueueError = {_IMP}QueueError\n"
    f"JobsIdempotencyEngine = {_IMP}JobsIdempotencyEngine\n"
    f"IdempotencyError = {_IMP}IdempotencyError\n"
)
# sha256 of tests/test_t0295_idempotency_red.py as amended (reference-bound) in this change
RED_AS_AMENDED_SHA256 = "432980b8f876f2063029d6f983fd01fda7fe97dd5790820d292abafb22799be0"
ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "re",
    "pathlib",
    "yaml",
    "server.jobs_queue",
}


def test_r1_red_battery_switch_is_exactly_the_three_binding_lines():
    source = RED.read_text()
    assert source.count(PRODUCTION_LINES) == 1
    assert ORACLE_LINES not in source
    restored = source.replace(PRODUCTION_LINES, ORACLE_LINES)
    assert hashlib.sha256(restored.encode()).hexdigest() == RED_AS_AMENDED_SHA256
    assert _red.QueueError is QueueError
    assert _red.JobsIdempotencyEngine is JobsIdempotencyEngine
    assert _red.IdempotencyError is IdempotencyError


def test_r2_production_imports_no_test_code():
    seen = set()
    for node in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            seen.add(node.module)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) in (
            "__import__",
            "exec",
            "eval",
        ):
            raise AssertionError(f"dynamic import or exec at line {node.lineno}")
    assert seen <= ALLOWED_IMPORTS, seen - ALLOWED_IMPORTS
    assert not any(name == "tests" or name.startswith("tests.") for name in seen)


def test_r3_public_surface():
    assert set(jobs_idempotency.__all__) == {
        "FAILURE_MAPPING",
        "IdempotencyError",
        "JobsIdempotencyEngine",
        "QueueError",
        "derive_receipt_id",
        "request_fingerprint",
    }
    assert issubclass(IdempotencyError, Exception)
    assert IdempotencyError is not _reference.IdempotencyError
    assert QueueError is jobs_queue.QueueError
    assert QueueError is not _reference.QueueError
    contract = yaml.safe_load((ROOT / "data" / "contracts" / "jobs_idempotency.yaml").read_text())[
        "contract"
    ]
    assert dict(contract["failures"]["mapping"]) == jobs_idempotency.FAILURE_MAPPING
    assert jobs_idempotency.FAILURE_MAPPING == _red.FAILURE_MAPPING
    for op, payload in (
        ("enqueue", {"dedupe_key": "a", "priority": 5, "payload": {"k": 1}}),
        ("claim", {"worker": "w1", "now": 10, "lease_ms": 140}),
        ("ack", {"job_id": "job1:x", "worker": "w1", "now": 9}),
        ("nack", {"job_id": "j", "worker": "w", "now": 0, "payload": {"x": "é", "n": -(10**30)}}),
    ):
        assert jobs_idempotency.request_fingerprint(op, payload) == _reference.request_fingerprint(
            op, payload
        )
    for args in (
        ("order-1", "jif1:" + "a" * 64, "qs1:" + "b" * 64, "qs1:" + "c" * 64, 0),
        ("k", "jif1:" + "0" * 64, "qs1:" + "1" * 64, "qs1:" + "2" * 64, 17),
    ):
        assert jobs_idempotency.derive_receipt_id(*args) == _reference.derive_receipt_id(*args)


@pytest.mark.parametrize(
    "section", ["happy", "boundary", "malformed", "queue_rejection", "rollback"]
)
def test_r4_reproduces_every_fixture_row(section):
    assert CASES[section]
    for row in CASES[section]:
        _red.RUNNERS[section](JobsIdempotencyEngine, row)


@pytest.mark.parametrize("name", sorted(_red.PROBE_MANIFEST))
def test_r5_every_totality_probe_passes_its_pins(name):
    # The pins are the reference-derived ones from T0294/T0295: the same
    # probe suite runs green against the reference under the reference
    # binding (the battery's standalone-red CI). Here it runs against
    # production. The production-bound battery machinery catches only the
    # bound error classes, so a reference-side rerun does not belong here.
    _red._totality_ok(JobsIdempotencyEngine, name)


@pytest.mark.parametrize("name", sorted(_red.ACCEPT_PROBES))
def test_r5_every_accept_probe_passes_its_pins(name):
    _red._accept_ok(JobsIdempotencyEngine, name)


@pytest.mark.parametrize("name", sorted(_red.CLOSURE_PROBES))
def test_r5_every_closure_probe_passes_its_pins(name):
    _red._closure_ok(JobsIdempotencyEngine, name)


# -- golden edge rows: exact outcomes pinned as literals, never recomputed ----

_JOB_A = {
    "job_id": "job1:1ecb9506bf9cd304276bd611cc8463823c16b1f3629c55262bb20edf3d489cb8",
    "seq": 0,
    "priority": 5,
    "payload": {"task": {"job": "a"}},
    "status": "ready",
    "attempts": 0,
    "lease_owner": None,
    "lease_expires_at": None,
}
_JOB_B = {
    "job_id": "job1:5764147927def31c60936ad0a0e3563865b195cbfb7b00b6b66ab118bf14cf07",
    "seq": 1,
    "priority": 1,
    "payload": {"n": 7},
    "status": "ready",
    "attempts": 0,
    "lease_owner": None,
    "lease_expires_at": None,
}
_JOBID_ZZ = "job1:baed6da606806acb4ef9bb288006e19ec173514945a99e09a1ba3f1561ca8461"
_S0_ID = "qs1:723bd31be8e30722ce4e495bf35de07b106ce5f7b43c8b79954311e15be03974"
_S1_ID = "qs1:8b73af1da81605242166f6e70212b78f80e639601d13f280e0051776027f30fc"
_RECEIPT_1 = {
    "receipt_id": "jir1:a83d984542dd5e6bc91d299740921ddd46e27e7db483dcbda0de779dd78e19d2",
    "idempotency_key": "order-1",
    "request_fingerprint": "jif1:ab67f8766a0e4c6da3cca5997628158fa7606da4a2a46bcfe9e3ba0a664873f0",
    "prior_state_id": _S0_ID,
    "state_id": _S1_ID,
    "sequence": 0,
}
_RECEIPT_2 = {
    "receipt_id": "jir1:592a03795a646a4b144c4c0e25235451608683aaf4e11fbf4aeca02364ad0f8f",
    "idempotency_key": "order-2",
    "request_fingerprint": "jif1:4a149ed5abd3449ac50fe0cc0a927abad934f6bd808db9137ee9a6f89fce3842",
    "prior_state_id": _S1_ID,
    "state_id": "qs1:9bf10cc605f2090641834056bec94faf4cdc736f0ede4335793e1a63abfdbe0b",
    "sequence": 1,
}
_RECEIPT_LEASE = {
    "receipt_id": "jir1:ba39706821810e42154d1d558787f779339c38d0f440281fcce184890bde9dd8",
    "idempotency_key": "lease-1",
    "request_fingerprint": "jif1:33d95a532db10d2375bcc65e9b2ac7de356a0439749b9096df73fb6611a5ae14",
    "prior_state_id": _S1_ID,
    "state_id": "qs1:c880edf83dff8e07a5d7ddc75f860d07beab81fb62f903447d086922f6fdb294",
    "sequence": 1,
}
_RECEIPT_INT4000 = {
    "receipt_id": "jir1:dffb383e807a4ed8ffdf5de33eba587175761f9f99879ef38835949c50551089",
    "idempotency_key": "edge-1",
    "request_fingerprint": "jif1:3bd4d472b020bb5f381daad579884c7b717f3ed7d31770cd14e9526d8f4b4be4",
    "prior_state_id": _S0_ID,
    "state_id": "qs1:1114adb7ad2fc247d7532fb415e49d2f417834caab0df66689d88fb1f1851af7",
    "sequence": 0,
}
_RECEIPT_NEST64 = {
    "receipt_id": "jir1:6454a67e8d5d67cd6467f19ccfce7d9fc8d9d0d53bd95062e08f1df128e2fc43",
    "idempotency_key": "edge-1",
    "request_fingerprint": "jif1:6428607a78de0d15a77bf81a92ed40c682a8563127b12aaf2b07e29805f5e6a0",
    "prior_state_id": _S0_ID,
    "state_id": "qs1:1e26f676d5fb73cc1a3bbe55e7b62c78f43a2a17b3c4989689d05780ae878f9d",
    "sequence": 0,
}
_RECEIPT_NONASCII = {
    "receipt_id": "jir1:23594e96a09533c672a697abad2905ae9fc90cd23ce5fc7561c76975e20367f6",
    "idempotency_key": "edge-1",
    "request_fingerprint": "jif1:1f3698a105613b9953d75f0cae5188ab97cee6ddffb96642148ee8870b1e7c51",
    "prior_state_id": _S0_ID,
    "state_id": "qs1:9a5cf9c6c2573949faabc626074e99c11677fd831233b255e4980f38a91d59df",
    "sequence": 0,
}


def _empty():
    return {"jobs": [], "next_seq": 0}


def _state(*jobs):
    return {"jobs": [dict(j) for j in jobs], "next_seq": len(jobs)}


def _leased(job, owner, expires):
    out = dict(job, status="leased", attempts=job["attempts"] + 1)
    out["lease_owner"] = owner
    out["lease_expires_at"] = expires
    return out


def _req(key, op, **payload):
    return {"idempotency_key": key, "op": op, "payload": payload}


def _enqueue(key, job_payload, dedupe_key="task-a", priority=5):
    return _req(key, "enqueue", dedupe_key=dedupe_key, priority=priority, payload=job_payload)


def _nest_dicts(depth):
    node = {"k": 0}
    for _ in range(depth - 1):
        node = {"k": node}
    return node


def _raising_baseexception(op, payload):
    raise KeyboardInterrupt("hostile oracle")


_MIR, _CQ, _CL, _KC, _DF = (
    "malformed_idempotency_request",
    "corrupt_queue",
    "corrupt_ledger",
    "key_conflict",
    "divergent_fingerprint",
)
# name -> (build state, build ledger, build request, oracle, pinned outcome)
# outcome: ("ok", outcome, receipt literal) | ("fail", failure_class) | ("qfail", code)
GOLDEN = {
    "apply-first-key-empty-queue": (
        _empty,
        list,
        lambda: _enqueue("order-1", {"task": {"job": "a"}}),
        "honest",
        ("ok", "applied", _RECEIPT_1),
    ),
    "replay-same-key-same-request": (
        lambda: _state(_JOB_A),
        lambda: [dict(_RECEIPT_1)],
        lambda: _enqueue("order-1", {"task": {"job": "a"}}),
        "honest",
        ("ok", "replayed", _RECEIPT_1),
    ),
    "apply-second-key-chains": (
        lambda: _state(_JOB_A),
        lambda: [dict(_RECEIPT_1)],
        lambda: _enqueue("order-2", {"n": 7}, dedupe_key="task-b", priority=1),
        "honest",
        ("ok", "applied", _RECEIPT_2),
    ),
    "payload-4000-digit-int": (
        _empty,
        list,
        lambda: _enqueue("edge-1", {"n": 10**4000 - 1}),
        "honest",
        ("ok", "applied", _RECEIPT_INT4000),
    ),
    "payload-4001-digit-int": (
        _empty,
        list,
        lambda: _enqueue("edge-1", {"n": 10**4000}),
        "honest",
        ("fail", _MIR),
    ),
    "payload-64-nested-dicts": (
        _empty,
        list,
        lambda: _enqueue("edge-1", _nest_dicts(64)),
        "honest",
        ("ok", "applied", _RECEIPT_NEST64),
    ),
    "payload-65-nested-dicts": (
        _empty,
        list,
        lambda: _enqueue("edge-1", _nest_dicts(65)),
        "honest",
        ("fail", _MIR),
    ),
    "payload-lone-surrogate": (
        _empty,
        list,
        lambda: _enqueue("edge-1", {"s": "\ud800"}),
        "honest",
        ("fail", _MIR),
    ),
    "payload-non-ascii": (
        _empty,
        list,
        lambda: _enqueue("edge-1", {"x": "é"}),
        "honest",
        ("ok", "applied", _RECEIPT_NONASCII),
    ),
    "key-conflict-same-key-different-payload": (
        lambda: _state(_JOB_A),
        lambda: [dict(_RECEIPT_1)],
        lambda: _enqueue("order-1", {"task": "task-a"}, priority=9),
        "honest",
        ("fail", _KC),
    ),
    "queue-rejection-unknown-job": (
        _empty,
        list,
        lambda: _req("fresh-1", "ack", job_id=_JOBID_ZZ, worker="w1", now=10),
        "honest",
        ("qfail", "unknown_job"),
    ),
    "queue-rejection-lease-conflict": (
        lambda: _state(_leased(_JOB_A, "w1", 150)),
        lambda: [dict(_RECEIPT_1), dict(_RECEIPT_LEASE)],
        lambda: _req("ack-1", "ack", job_id=_JOB_A["job_id"], worker="w2", now=100),
        "honest",
        ("qfail", "lease_conflict"),
    ),
    "rollback-stale-composition": (
        lambda: _state(_JOB_A, _JOB_B),
        lambda: [dict(_RECEIPT_1)],
        lambda: _enqueue("order-3", {"task": {"job": "c"}}, dedupe_key="task-c"),
        "honest",
        ("fail", _CL),
    ),
    "oracle-raises-baseexception": (
        _empty,
        list,
        lambda: _enqueue("order-1", {"task": {"job": "a"}}),
        _raising_baseexception,
        ("fail", _DF),
    ),
}


def _shape(obj, seen=None):
    """Copy-invariant, type-exact shape: containers are numbered in visit
    order and one met again is a reference to its number, so two builds
    of one probe compare equal and a re-wired alias differs."""
    seen = {} if seen is None else seen
    if type(obj) in (dict, list) or isinstance(obj, (dict, list)):
        if id(obj) in seen:
            return ("ref", seen[id(obj)])
        seen[id(obj)] = len(seen)
        if isinstance(obj, dict):
            items = [(_shape(k, seen), _shape(v, seen)) for k, v in dict.items(obj)]
        else:
            items = [_shape(v, seen) for v in list.__iter__(obj)]
        return (type(obj).__qualname__, items)
    if isinstance(obj, str):
        return (type(obj).__qualname__, str.__str__(obj))
    if isinstance(obj, float) and obj != obj:
        return (type(obj).__qualname__, "nan")
    if isinstance(obj, (int, float)) or obj is None:
        return (type(obj).__qualname__, obj)
    return (type(obj).__qualname__, "opaque")


def _state_id(state):
    return (
        "qs1:"
        + hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    )


def _golden_problem(module, name):
    """None when MODULE's engine gives exactly the pinned outcome."""
    build_state, build_ledger, build_request, oracle, expect = GOLDEN[name]
    state, ledger, request = build_state(), build_ledger(), build_request()
    pre = (_shape(state), _shape(ledger), _shape(request))
    oracle = _red.ORACLES[oracle] if type(oracle) is str else oracle
    engine = module.JobsIdempotencyEngine(oracle)
    try:
        result = engine.apply(state, ledger, request)
    except module.QueueError as exc:
        if expect[0] != "qfail" or type(exc) is not module.QueueError:
            return f"queue-rejected {getattr(exc, 'code', '?')}"
        if exc.code != expect[1]:
            return f"queue code {exc.code}"
        if exc.__cause__ is not None or exc.__context__ is not None:
            return "queue error chained"
        if (_shape(state), _shape(ledger), _shape(request)) != pre:
            return "rejected apply changed an input"
        return None
    except module.IdempotencyError as exc:
        if expect[0] != "fail" or type(exc) is not module.IdempotencyError:
            return f"rejected {getattr(exc, 'failure_class', '?')}"
        if exc.failure_class != expect[1]:
            return f"class {exc.failure_class}"
        if exc.code != module.FAILURE_MAPPING[expect[1]]:
            return f"code {exc.code}"
        # __cause__ pinned, __context__ explicitly NOT pinned (the error
        # converts a caught QueueError; same convention as the battery)
        if exc.__cause__ is not None:
            return "typed error re-chained"
        if (_shape(state), _shape(ledger), _shape(request)) != pre:
            return "rejected apply changed an input"
        return None
    except BaseException as exc:  # noqa: BLE001 - a raw escape is a defect
        return f"raw {type(exc).__name__}"
    if expect[0] != "ok":
        return "accepted"
    _, outcome, receipt = expect
    if type(result) is not dict or set(dict.keys(result)) != {"outcome", "receipt"}:
        return "result keys"
    if result["outcome"] != outcome:
        return f"outcome {result['outcome']}"
    got = result["receipt"]
    if got != receipt or type(got) is not dict:
        return f"receipt {got}"
    if tuple(dict.keys(got)) != _red.RECEIPT_FIELDS:
        return "receipt field order"
    if _shape(request) != pre[2]:
        return "request changed"
    if outcome == "replayed":
        if (_shape(state), _shape(ledger)) != (pre[0], pre[1]):
            return "replay changed state or ledger"
        if ledger and got is ledger[-1]:
            return "replay returned the stored receipt object"
        return None
    if len(ledger) != 0 and _shape(ledger[:-1]) != pre[1]:
        return "ledger prefix changed"
    if len(ledger) < 1 or ledger[-1] != receipt:
        return "receipt not appended"
    if got is ledger[-1]:
        return "result aliases the appended receipt"
    if receipt["state_id"] != _state_id(state):
        return "committed state does not hash to the receipt's state_id"
    if receipt["prior_state_id"] != _state_id(build_state()):
        return "prior_state_id does not hash the pre-apply state"
    return None


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_r6_golden_edge_rows(name):
    assert _golden_problem(jobs_idempotency, name) is None


# -- production mutants: each must turn red at a pinned first-witness label ---

# name -> (old, new, first-witness label prefix, target label substrings)
PRODUCTION_EDITS = {
    # the nine T0295 _SM defect classes ported to the production text
    "grammar-match": (
        "    return type(value) is str and pattern.fullmatch(value) is not None",
        "    return type(value) is str and pattern.match(value) is not None",
        "totality:",
        ("totality:key-grammar-trailing-newline",),
    ),
    "oracle-guard-exception-only": (
        "        except BaseException:",
        "        except Exception:",
        "totality:oracle-raises-",
        ("totality:oracle-raises-",),
    ),
    "fingerprint-binding-off": (
        '        if out != request_fingerprint(frozen_req["op"], frozen_req["payload"]):',
        "        if out is None and out != request_fingerprint("
        'frozen_req["op"], frozen_req["payload"]):',
        "malformed:",
        ("malformed:oracle-unbound-token",),
    ),
    "conflict-check-off": (
        '                if stored["request_fingerprint"] != token:',
        '                if False and stored["request_fingerprint"] != token:',
        "malformed:",
        ("malformed:key-conflict-different-payload",),
    ),
    "ledger-terminal-off": (
        "        if prev_state is not None and prev_state != live_state_id:",
        "        if False and prev_state is not None and prev_state != live_state_id:",
        "malformed:",
        ("malformed:terminal-mismatch",),
    ),
    "receipt-id-reattach-off": (
        "            if rid != derive_receipt_id(key, fp, prior, sid, seq):",
        "            if False and rid != derive_receipt_id(key, fp, prior, sid, seq):",
        "malformed:",
        ("malformed:tampered-receipt-id",),
    ),
    "sequence-gap-check-skipped": (
        "            if type(seq) is not int or seq != index:",
        "            if type(seq) is not int:",
        "malformed:",
        ("malformed:sequence-gap",),
    ),
    "restore-skipped": (
        "            for snap in snaps:",
        "            for snap in ():",
        "closure:",
        ("closure:",),
    ),
    "alias-check-off": (
        "        if req_ids & _container_ids(state) or req_ids & _container_ids(ledger):",
        "        if False and (req_ids & _container_ids(state)"
        " or req_ids & _container_ids(ledger)):",
        "malformed:",
        ("malformed:request-aliases-state",),
    ),
    # production-only mutants beyond the T0295 table
    "ops-gate-inverted": (
        "        if type(op) is not str or op not in _OPS:",
        "        if type(op) is not str or op in _OPS:",
        "happy:",
        ("happy:apply-first-key-empty-queue",),
    ),
    "oracle-argument-not-detached": (
        '                frozen_req["op"], json.loads(_canonical(frozen_req["payload"]))',
        '                frozen_req["op"], frozen_req["payload"]',
        "totality:oracle-mutates-before-token",
        ("totality:oracle-mutates-before-token", "accept:oracle-mutation-inert"),
    ),
    "chain-check-off": (
        "            if prev_state is not None and prior != prev_state:",
        "            if False and prev_state is not None and prior != prev_state:",
        "malformed:",
        ("malformed:chain-break",),
    ),
    "unique-key-off": (
        "            if key in seen_keys:",
        "            if False and key in seen_keys:",
        "malformed:duplicate-ledger-key",
        ("malformed:duplicate-ledger-key",),
    ),
    "snapshot-over-copy": (
        "        snaps = (_snapshot(state), _snapshot(ledger), _snapshot(request))",
        "        snaps = (_snapshot(dict(state)), _snapshot(list(ledger)),"
        " _snapshot(dict(request)))",
        "closure:",
        ("closure:",),
    ),
}

EQUIVALENT_PRODUCTION_EDITS = {
    "comment-reworded": (
        "            # fail closed against the FULL BaseException surface",
        "            # fail closed against the entire BaseException surface",
    ),
    "enumerate-start-explicit": (
        "        for index, receipt in enumerate(ledger):",
        "        for index, receipt in enumerate(ledger, 0):",
    ),
    "demorgan-shape-guard": (
        "        if type(out) is not str or _FP_RE.fullmatch(out) is None:",
        "        if not (type(out) is str and _FP_RE.fullmatch(out) is not None):",
    ),
    "extend-iterates-list": (
        "            stack.extend(list.__iter__(node))",
        "            stack.extend(node)",
    ),
}


def _mutant_module(name, edits):
    module = types.ModuleType(f"server.jobs_idempotency_mutant_{name}")
    module.__file__ = str(PRODUCTION)
    text = PRODUCTION.read_text()
    for old, new in edits:
        assert text.count(old) == 1, (name, old)
        text = text.replace(old, new)
    exec(compile(text, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    # rebind the error classes to the bound ones so the battery's typed
    # catches and exact-type pins compare one class, as in T0295's
    # _source_mutant (QueueError is already the same object)
    module.IdempotencyError = IdempotencyError
    module.QueueError = QueueError
    return module


def _edit_module(name, table):
    old, new = table[name][0], table[name][1]
    return _mutant_module(name, [(old, new)])


def test_r6_identity_production_source_is_green():
    assert _red._probe(_mutant_module("identity", []).JobsIdempotencyEngine) == []


@pytest.mark.parametrize("name", sorted(EQUIVALENT_PRODUCTION_EDITS))
def test_r6_equivalent_production_edits_stay_green(name):
    assert _red._probe(_edit_module(name, EQUIVALENT_PRODUCTION_EDITS).JobsIdempotencyEngine) == []


@pytest.mark.parametrize("name", sorted(PRODUCTION_EDITS))
def test_r6_production_mutants_red_at_the_pinned_locus(name):
    old, new, first, targets = PRODUCTION_EDITS[name]
    failures = _red._probe(_mutant_module(name, [(old, new)]).JobsIdempotencyEngine)
    assert failures != [], name
    assert failures[0].startswith(first), (name, failures[0])
    assert any(t in f for f in failures for t in targets), (
        f"{name}: red but never at the pinned locus {targets}"
    )


def test_r6_production_mutants_cover_the_reference_source_edits():
    """Every T0295 _SM defect class has a production-source counterpart."""
    assert set(_red._SM) <= set(PRODUCTION_EDITS)


def test_r6_golden_rows_all_pinned():
    assert len(GOLDEN) == 14
