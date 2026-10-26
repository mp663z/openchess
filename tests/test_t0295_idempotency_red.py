"""T0295 permanent red battery for jobs-idempotency engines.

The battery drives EVERY row of the T0294 conformance fixture
(tests/fixtures/jobs_idempotency/cases.json), a closed set of totality
probes and pinned semantic probes through an engine class constructed
with the contract's canonical request fingerprinter:

- happy/boundary: the single pinned request applied to fresh copies of
  the pinned state and ledger (commit in place) returns the pinned
  outcome and receipt exactly (contract field order on fresh-applied
  receipts, exact field set on replays, and exact field
  types); the receipt id, prior and state ids AND the receipt's jif1
  fingerprint are re-derived HERE from the committed results and the
  PRE-APPLY request; an applied row grows the ledger by exactly the
  returned receipt and moves the state, a replayed row leaves both
  bit-identical and returns a FRESH copy of the stored receipt; the
  caller's request is untouched by value and identity and nothing in the
  committed state or ledger aliases it; the returned result aliases
  neither the state, the ledger nor the request; results are fresh per
  call; determinism by value over fresh copies on a fresh instance, and
  replay determinism on the SAME instance through the FULL per-call
  pins (a snapshot is taken immediately BEFORE the repeat, so a
  repeat-time rewrite of the caller's request, state or ledger after
  the result is computed cannot escape);
- malformed: the original input rejects with the pinned failure class
  and its mapped code, with no explicit re-chaining (__cause__ is
  None), state, ledger and request untouched, and the declared
  minimal-locus repair is accepted with the result the reference
  derives for it;
- queue_rejection: the queue's typed rejection propagates UNWRAPPED with
  the pinned code from the composed error domain, exact QueueError type,
  unchained, committing nothing, and a retry re-attempts (no phantom
  receipt to replay);
- rollback: the apply against the stale composition rejects with the
  pinned class leaving every input bit-identical, then the valid
  follow-up on the SAME engine commits the pinned result;
- totality: hostile requests, payloads, states, ledgers and receipts
  (non-dict and list/dict-subclass containers, a lying dict,
  str-subclass keys in 3 forms - plain, eq-raises, hash-collides -
  str/int-subclass and bool values, grammar edges with a trailing
  newline and non-ASCII digits, cyclic, aliased, too-deep and
  too-long-int payloads, every state and ledger invariant) each reject
  with the pinned class, an empty hostile-call log and unchanged input;
  any other BaseException escaping is a failure;
- oracle boundary: the fingerprinter is UNTRUSTED - oracles that raise
  (including BaseException subclasses), return non-str, wrong-grammar,
  UTF-8-inencodable or request-divergent tokens, or mutate their
  argument each fail closed as divergent_fingerprint with the inputs
  untouched, and the call count is pinned: ZERO calls when the request,
  state or ledger is invalid, EXACTLY ONE otherwise; the detached
  ARGUMENT does not isolate an AMBIENT CLOSURE, so oracles that
  capture and clear the live state/ledger/request mid-call are pinned
  too: bit-identical AND nested-identity-identical restoration on
  rejection, honest-transition commit plus restored request on
  acceptance;
- semantics: replay returns the stored receipt bit-identically, conflict
  detection by key-then-fingerprint, chain/sequence growth, the
  state-neutral replay boundary, request/state/ledger graph
  disjointness.

Standalone-red convention: the battery is permanently GREEN against the
contract-derived reference engine from
tests.test_t0293_jobs_idempotency_contract and every mutant below is RED
on its pinned target label. The production task switches the binding by
replacing ONLY the three binding lines below with the production
queue error, engine and engine error names; no assertion changes.
The queue_rejection rows pin the exact bound QueueError type, so the
switch must bind the production queue's QueueError; the contract
pins the propagated CODES, not the class identity. The source
mutants below are edits of the REFERENCE source text and stay bound
to the reference substrate (they compose the reference queue, whose
validator interfaces differ from the production queue's), so under
the production binding they are SKIPPED and their production-source
counterparts live in the T0296 runtime battery; the class-based
mutants subclass the BOUND engine and run under both bindings.

Unlike the queue engine (T0286), the idempotency engine takes an
untrusted caller callback, so the hostile-oracle boundary IS in scope
here.

Fixture closure: ordered per-section name manifests and a ROW_DIGESTS
whole-row sha256 table whose key set equals the manifests.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0293_jobs_idempotency_contract as _reference  # noqa: E402
from tools.jobs_idempotency_contract_lint import CONTRACT  # noqa: E402
from tools.queue_contract_lint import (  # noqa: E402
    MAX_DEPTH,
    MAX_INT_DIGITS,
)

# -- binding (the production task replaces ONLY these three lines) -------------
QueueError = __import__("server.jobs_idempotency").jobs_idempotency.QueueError
JobsIdempotencyEngine = __import__("server.jobs_idempotency").jobs_idempotency.JobsIdempotencyEngine
IdempotencyError = __import__("server.jobs_idempotency").jobs_idempotency.IdempotencyError

IS_REFERENCE = JobsIdempotencyEngine is _reference.JobsIdempotencyEngine
_REQUIRES_REFERENCE = pytest.mark.skipif(
    not IS_REFERENCE,
    reason="reference-source mutants are bound to the reference substrate; "
    "production mutants live in the T0296 runtime battery",
)

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_idempotency" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_IDS = _CC["identifiers"]
RECEIPT_FIELDS = tuple(_CC["record"]["fields"])
FAILURE_CLASSES = set(_CC["failures"]["classes"])
FAILURE_MAPPING = dict(_CC["failures"]["mapping"])
PROPAGATED = set(_CC["error_composition"]["propagated_queue_rejections"])
OUTCOMES = set(_CC["outcomes"]["values"])
MIR, CQ, CL, KC, DF = (
    "malformed_idempotency_request",
    "corrupt_queue",
    "corrupt_ledger",
    "key_conflict",
    "divergent_fingerprint",
)


# -- fixture closure -------------------------------------------------------------


EXPECTED_NAMES = {
    "happy": (
        "apply-first-key-empty-queue",
        "apply-new-key-extends-queue",
        "replay-seen-key-identical-request",
    ),
    "boundary": (
        "key-single-char",
        "key-max-length-128",
        "same-request-distinct-key-applies-again",
        "apply-ack-at-tip",
        "apply-nack-at-tip",
        "replay-state-neutral-receipt",
    ),
    "malformed": (
        "request-not-a-dict",
        "request-missing-field",
        "request-extra-field",
        "key-empty",
        "key-too-long",
        "key-bad-leading-char",
        "key-non-str",
        "unregistered-op",
        "payload-op-smuggle",
        "enqueue-missing-field",
        "claim-bad-lease",
        "request-aliases-state",
        "request-aliases-ledger",
        "state-job-bad-status",
        "ledger-not-a-list",
        "receipt-missing-field",
        "receipt-extra-field",
        "duplicate-ledger-key",
        "sequence-gap",
        "chain-break",
        "terminal-mismatch",
        "tampered-receipt-id",
        "receipt-fingerprint-mismatch",
        "oracle-raises",
        "oracle-non-str-output",
        "oracle-bad-grammar",
        "oracle-unbound-token",
        "oracle-lone-surrogate",
        "key-conflict-different-payload",
        "key-conflict-different-op",
    ),
    "queue_rejection": (
        "dedupe-conflict",
        "unknown-job",
        "lease-conflict",
    ),
    "rollback": (
        "rollback-past-receipt-reapply-deterministic",
        "rollback-then-divergent-reapply-fresh",
        "rollback-mixed-stale-ledger-fails-closed",
    ),
}
ROW_DIGESTS = dict(
    [
        (
            "happy:apply-first-key-empty-queue",
            "76a8fded483774cb4777d37a9d56cf80c49ea857fde822afc0a9ada35bf57349",
        ),
        (
            "happy:apply-new-key-extends-queue",
            "3621a3ec6baf569620da2ef24462b43fac9adaec99835fae40bd8a46739dd431",
        ),
        (
            "happy:replay-seen-key-identical-request",
            "7c877beefe7b991de4cc1508d8f57f45f0132e1e32427d317513df130f9668ba",
        ),
        (
            "boundary:key-single-char",
            "96bf833574ff9b49f174ed92d9c0ebfbd17a921cf7d148a7789c63bbbc64855b",
        ),
        (
            "boundary:key-max-length-128",
            "deb37cca638235caf8706f4ad52a95698318548724bcbafa9bcfbd69b209a2e3",
        ),
        (
            "boundary:same-request-distinct-key-applies-again",
            "16d9bed1424151b5675b3caa16ba8b4293c82545d052bce2e372b6224133ec2f",
        ),
        (
            "boundary:apply-ack-at-tip",
            "1b32100a87005144a90498afc07b89a103bd24423537b731340796009017b2d9",
        ),
        (
            "boundary:apply-nack-at-tip",
            "18bb1645f0f9a5bb06f56c28b458f7413e90866a1a6438c8e37f7fc4bfdf296e",
        ),
        (
            "boundary:replay-state-neutral-receipt",
            "8af9ccb64a067bcd6f16bdd589c41c85398497389f61294f3f0a41b793293234",
        ),
        (
            "malformed:request-not-a-dict",
            "18c6b7120d6221a683901476121a2befade59bc3df48875b48c967c1d4057350",
        ),
        (
            "malformed:request-missing-field",
            "27a1d8abdb02ac54fef1633139d702edf616f85ab5b195f1ed6c766bac43f915",
        ),
        (
            "malformed:request-extra-field",
            "3470a92ece6a86b762d73a30d7bd64702a314bb65fe9ee021df51091f649f89c",
        ),
        (
            "malformed:key-empty",
            "c6ebc366bf328d105edee75a73e4c918c64ba566650f7c0d0864abc6227cd5f7",
        ),
        (
            "malformed:key-too-long",
            "b33069132e51a2b3cf3c1ef371cdda12d7f1bf1f6a235945fe0933252760a148",
        ),
        (
            "malformed:key-bad-leading-char",
            "fc6c04000cce6933d0a216b4a471924ce0a5142695b971e4d74a43be494c5861",
        ),
        (
            "malformed:key-non-str",
            "5add9f22dec6a65b24b9000ec7c1027f43ca436b17718bbdf5aba356999dff1d",
        ),
        (
            "malformed:unregistered-op",
            "861e12240c32861847567b2bc65b8cf68f726de331d968789ab9dbedbe8192ca",
        ),
        (
            "malformed:payload-op-smuggle",
            "5dd217229939516d55cc0e38cef16dbfb1dd5862a330b2e8cdf4dcce8bebfad5",
        ),
        (
            "malformed:enqueue-missing-field",
            "d1bea2f1eda93fe04525253bd8d59c6c96f25735ac5bc1ed6507d2dc4d9bcea3",
        ),
        (
            "malformed:claim-bad-lease",
            "505558c693e229d39870ec1945c133fb47caa11fd2014bb74c5b45fe9182d6e6",
        ),
        (
            "malformed:request-aliases-state",
            "d738158be6f65a1e60e7c1973373360bff0662c8457e7780c402dc7e47b33165",
        ),
        (
            "malformed:request-aliases-ledger",
            "f0261f0ece177fa9aee1bc58288700e55d73e9549bc6f711488bcc3729616cd9",
        ),
        (
            "malformed:state-job-bad-status",
            "b2d99a8819df4da0fe62447ff3478186e66be1a7b9ff26954430e571a9415f60",
        ),
        (
            "malformed:ledger-not-a-list",
            "03dc500b9341aecdee1cb1deb001a6392eed3c0f5509badf5b1ee06901e01215",
        ),
        (
            "malformed:receipt-missing-field",
            "90f2025ae7cc1d82060e1c5e28f0c974e66bf1a4b31d17651c28c5c806064014",
        ),
        (
            "malformed:receipt-extra-field",
            "2a56241fcbc59c47ab7f89eb38747e22970b82276439c08a08ff7b6d22cc32f8",
        ),
        (
            "malformed:duplicate-ledger-key",
            "fa284b9e293b9c6febf8e1058939ce4ca7b7029f5ae4986ef71c556001760090",
        ),
        (
            "malformed:sequence-gap",
            "d35e01e17a96187fd8159c858b1f1542b20b52f0be7cdd8496861dc2f122ecea",
        ),
        (
            "malformed:chain-break",
            "2ba03247882b9522b98b8db68b88895e99033056343c09d3d95de3b4aba386fc",
        ),
        (
            "malformed:terminal-mismatch",
            "292fc621bb01472db0f2d5202479e04800ba2047746703aa47266265f22de40b",
        ),
        (
            "malformed:tampered-receipt-id",
            "b5590a42cebd68f18cb1d90fe37c64288dc1d65c94e32e6b474e414b4bd00d4a",
        ),
        (
            "malformed:receipt-fingerprint-mismatch",
            "30d5755fa2a0cab1292e20c7b9d25fd04a933e074cdc1f50c1ffa37e6fe876be",
        ),
        (
            "malformed:oracle-raises",
            "05f33dd5817b3cda3109b371b2cf940af8586139c839c52f760808b41d00510d",
        ),
        (
            "malformed:oracle-non-str-output",
            "45487a32bbefd7b7c052aa773741ac5b85a4e3c89f36afff4faa9c4a01e2e669",
        ),
        (
            "malformed:oracle-bad-grammar",
            "9b8ebd01068549a1dbeb435fb9ebae24a69ae3df20f47c323ed6681d5f0cba26",
        ),
        (
            "malformed:oracle-unbound-token",
            "670fb0d0a93c6f62bf655368e9df3ffb3c18abca950ff2bd389e12b63cc39c35",
        ),
        (
            "malformed:oracle-lone-surrogate",
            "8936f1d834feaf6529dd3eb2b598e5a0a19aa04380cf2797aa419fbd8845b816",
        ),
        (
            "malformed:key-conflict-different-payload",
            "a06e232e28f5ba51c394bb2b0f52b3cbca519200a662468047687c735dbecf36",
        ),
        (
            "malformed:key-conflict-different-op",
            "c676703374194517be40afdf10a14aac9ddcbcdc57564f50dcd45742f115d6cc",
        ),
        (
            "queue_rejection:dedupe-conflict",
            "ce8bb1c9515f69a09e6f38b4221b0e122de8bd579b5b2ec44206b09e5da86f5e",
        ),
        (
            "queue_rejection:unknown-job",
            "d2afa238af55b4a916dfa9bf76c7277e90bdbef4ff133958e3a8b66866288e8a",
        ),
        (
            "queue_rejection:lease-conflict",
            "2c5eb10698f9c5f5ebc0ff9f105cc7bf9319200dd39bdbb14fb55ab5af03d18d",
        ),
        (
            "rollback:rollback-past-receipt-reapply-deterministic",
            "ad404661bdeb76225f02dcda820e16a8581e312eb8737c45d34713df6331b4c8",
        ),
        (
            "rollback:rollback-then-divergent-reapply-fresh",
            "9b03bc8e82171a7b5d593b8d71bc2b4e917b735551119d490f07464cc55b9a1a",
        ),
        (
            "rollback:rollback-mixed-stale-ledger-fails-closed",
            "bf50857dfe2025ebf67b07abb86fa4f553c31fc7f54270701a2ea4950109cb04",
        ),
    ]
)


def _validate_closure(cases):
    for section, names in EXPECTED_NAMES.items():
        got = tuple(row["name"] for row in cases[section])
        assert got == names, f"{section} names or order drifted"
        for row in cases[section]:
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS[label] == _row_digest(row), label
    assert set(ROW_DIGESTS) == {
        f"{section}:{name}" for section, names in EXPECTED_NAMES.items() for name in names
    }
    assert len(set(ROW_DIGESTS.values())) == len(ROW_DIGESTS)


# -- independent derivations -------------------------------------------------------


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _row_digest(row):
    return hashlib.sha256(_canon(row).encode()).hexdigest()


def _state_id(state):
    return "qs1:" + hashlib.sha256(_canon(state).encode()).hexdigest()


def _fingerprint(op, payload):
    canon = _canon(payload)
    text = f"jif1|{len(op)}:{op}|{len(canon)}:{canon}"
    return "jif1:" + hashlib.sha256(text.encode()).hexdigest()


def _receipt_id(key, fp, prior, state_id, sequence):
    return (
        "jir1:"
        + hashlib.sha256(f"{key}\n{fp}\n{prior}\n{state_id}\n{sequence}".encode()).hexdigest()
    )


# -- hostile types -----------------------------------------------------------------


class _Armed:
    on = False


HOSTILE = []  # user dunder calls observed while armed


class SK(str):
    pass


class _EqRaises(str):
    def __eq__(self, other):
        raise RuntimeError("eq")

    def __hash__(self):
        return str.__hash__(self)


class HK:
    """A key whose hash collides with a real field name."""

    def __init__(self, text):
        self.text = text

    def __eq__(self, other):
        return other == self.text

    def __hash__(self):
        return hash(self.text)


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _Lying(dict):
    """A dict subclass whose every view is logged while armed."""

    def _log(self, name):
        if _Armed.on:
            HOSTILE.append(f"lying-{name}")

    def keys(self):
        self._log("keys")
        return dict.keys(self)

    def items(self):
        self._log("items")
        return dict.items(self)

    def values(self):
        self._log("values")
        return dict.values(self)

    def get(self, key, default=None):
        self._log("get")
        return dict.get(self, key, default)

    def __iter__(self):
        self._log("iter")
        return dict.__iter__(self)

    def __getitem__(self, key):
        self._log("getitem")
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        self._log("contains")
        return dict.__contains__(self, key)

    def __len__(self):
        self._log("len")
        return dict.__len__(self)


class _LogList(list):
    def __iter__(self):
        if _Armed.on:
            HOSTILE.append("list-iter")
        return list.__iter__(self)

    def __len__(self):
        if _Armed.on:
            HOSTILE.append("list-len")
        return list.__len__(self)


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hkey(form, text):
    if form == "plain":
        return SK(text)
    if form == "eq-raises":
        return _EqRaises(text)
    return HK(text)


def _rekey(mapping, field, key):
    return {(key if k == field else k): v for k, v in dict.items(mapping)}


def _snap(obj, memo=None):
    """Exact structure: type, identity, key order and value at every
    level (base-type methods only, so a subclass cannot lie)."""
    if memo is None:
        memo = set()
    if isinstance(obj, (dict, list)):
        if id(obj) in memo:
            return ("cycle", id(obj))
        memo = memo | {id(obj)}
    if isinstance(obj, dict):
        return (
            "d",
            type(obj).__name__,
            id(obj),
            tuple(
                (type(k).__name__, k if type(k) is str else id(k), _snap(v, memo))
                for k, v in dict.items(obj)
            ),
        )
    if isinstance(obj, list):
        return ("l", type(obj).__name__, id(obj), tuple(_snap(v, memo) for v in list.__iter__(obj)))
    if isinstance(obj, tuple):
        return ("t", id(obj), tuple(_snap(v, memo) for v in obj))
    if isinstance(obj, (str, int)) and type(obj) not in (str, int, bool):
        return ("sub", type(obj).__name__, id(obj))
    return ("v", type(obj).__name__, obj)


def _containers(obj, ids=None, keep=None):
    """(id set, keepalive list) of every dict/list reachable from
    OBJ.  The keepalive holds each container alive so a CPython id
    cannot be freed and reallocated between the collections being
    compared - an id-set alone could in theory miss an equal-content
    replacement (D robustness note; not a demonstrated gap)."""
    if ids is None:
        ids, keep = set(), []
    if isinstance(obj, (dict, list)) and id(obj) not in ids:
        ids.add(id(obj))
        keep.append(obj)
        for v in dict.values(obj) if isinstance(obj, dict) else list.__iter__(obj):
            _containers(v, ids, keep)
    return ids, keep


# -- oracles (the untrusted fingerprinter boundary) --------------------------------


def _fp_raising(op, payload):
    raise ValueError("untrusted fingerprinter failure")


def _fp_non_str(op, payload):
    return []


def _fp_bad_grammar(op, payload):
    return "jif1:zz"


def _fp_unbound(op, payload):
    """Grammar-valid but NOT bound to the local request derivation."""
    return "jif1:" + "f" * 64


def _fp_surrogate(op, payload):
    return "\ud800"


ORACLES = {
    "honest": _reference.request_fingerprint,
    "raising": _fp_raising,
    "non_str_output": _fp_non_str,
    "bad_grammar": _fp_bad_grammar,
    "unbound_token": _fp_unbound,
    "lone_surrogate": _fp_surrogate,
}


# -- row runners -------------------------------------------------------------------


class Fail(AssertionError):
    pass


def _need(cond, why):
    if not cond:
        raise Fail(why)


def _engine(cls, oracle="honest"):
    return cls(ORACLES[oracle])


def _exact_result(result, expect, ledger):
    _need(type(result) is dict, "result not an exact dict")
    _need(set(dict.keys(result)) == {"outcome", "receipt"}, "result keys")
    outcome, receipt = result["outcome"], expect["receipt"]
    _need(type(result["outcome"]) is str, "outcome not exact str")
    _need(
        result["outcome"] == expect["outcome"],
        f"outcome {result['outcome']} != {expect['outcome']}",
    )
    got = result["receipt"]
    _need(type(got) is dict, "receipt not an exact dict")
    if result["outcome"] == "applied":
        # the engine CONSTRUCTS fresh receipts in contract field
        # order; a replay returns dict(stored ledger entry), whose
        # key order is inherited from the ledger data (the grammar
        # is order-insensitive), so order is pinned for applied
        # receipts only
        _need(tuple(dict.keys(got)) == RECEIPT_FIELDS, "receipt field order")
    else:
        _need(set(dict.keys(got)) == set(RECEIPT_FIELDS), "receipt fields")
    _need(_canon(got) == _canon(receipt), f"receipt differs {got!r}"[:200])
    for field in RECEIPT_FIELDS:
        want = int if field == "sequence" else str
        _need(type(got[field]) is want, f"receipt {field} not exact {want.__name__}")
    _need(
        got["receipt_id"]
        == _receipt_id(
            got["idempotency_key"],
            got["request_fingerprint"],
            got["prior_state_id"],
            got["state_id"],
            got["sequence"],
        ),
        "receipt id does not re-derive",
    )
    return outcome, got


def _checked_apply(engine, state, ledger, request, expect, previous=None):
    """One successful apply with every per-call pin."""
    pre_request = copy.deepcopy(request)
    snaps = (_snap(state), _snap(ledger), _snap(request))
    pre_state_id = _state_id(state)
    pre_ledger_len = len(ledger)
    state_id, ledger_id = id(state), id(ledger)
    result = engine.apply(state, ledger, request)
    _need(id(state) == state_id and id(ledger) == ledger_id, "commit not in place")
    outcome, receipt = _exact_result(result, expect, ledger)
    _need(_snap(request) == snaps[2], "request mutated")
    # INDEPENDENT jif1 preimage execution in THIS battery: the
    # receipt's token must re-derive from the PRE-APPLY request,
    # not from any engine-internal or fixture-provided value
    _need(
        receipt["request_fingerprint"] == _fingerprint(pre_request["op"], pre_request["payload"]),
        "receipt fingerprint does not independently re-derive",
    )
    r_ids, r_keep = _containers(result)
    q_ids, q_keep = _containers(request)
    s_ids, s_keep = _containers(state)
    l_ids, l_keep = _containers(ledger)
    _need(not (r_ids & q_ids), "result aliases the request")
    _need(not (r_ids & s_ids), "result aliases the state")
    _need(not (r_ids & l_ids), "result aliases the ledger")
    if outcome == "applied":
        _need(len(ledger) == pre_ledger_len + 1, "applied row did not grow the ledger by one")
        _need(
            _canon(ledger[-1]) == _canon(receipt), "committed receipt differs from the returned one"
        )
        _need(receipt["prior_state_id"] == pre_state_id, "prior does not bind the pre-apply state")
        _need(_state_id(state) == receipt["state_id"], "state_id does not bind the committed state")
    else:
        _need(_snap(state) == snaps[0], "replayed row moved state")
        _need(_snap(ledger) == snaps[1], "replayed row grew or rewrote the ledger")
        stored = [r for r in ledger if r["idempotency_key"] == request["idempotency_key"]]
        _need(
            len(stored) == 1 and _canon(stored[0]) == _canon(receipt),
            "replayed receipt is not the stored one",
        )
    _need(not (q_ids & s_ids), "committed state aliases the request")
    _need(not (q_ids & l_ids), "committed ledger aliases the request")
    # keepalives must outlive every comparison above
    _need(
        bool(r_keep) and bool(q_keep) and bool(s_keep) and bool(l_keep), "keepalive dropped early"
    )
    if previous is not None:
        _need(
            result is not previous and result["receipt"] is not previous["receipt"],
            "result reused across calls",
        )
    return result


def _rejects(engine, state, ledger, request, failure):
    snaps = (_snap(state), _snap(ledger), _snap(request))
    try:
        engine.apply(state, ledger, request)
    except IdempotencyError as exc:
        _need(type(exc) is IdempotencyError, "error subclass")
        _need(exc.failure_class == failure, f"class {exc.failure_class} != {failure}")
        _need(exc.code == FAILURE_MAPPING[failure], "code not mapped")
        # no EXPLICIT re-chaining; __context__ is NOT pinned - the
        # corrupt_queue translation inside the queue-error handler
        # sets it implicitly by design, and the contract pins no
        # freshness beyond the class/code pair
        _need(exc.__cause__ is None, "typed error re-chained")
    else:
        raise Fail(f"accepted, expected {failure}")
    _need((_snap(state), _snap(ledger), _snap(request)) == snaps, "rejected apply changed an input")


def _rejects_queue(engine, state, ledger, request, code):
    snaps = (_snap(state), _snap(ledger), _snap(request))
    try:
        engine.apply(state, ledger, request)
    except QueueError as exc:
        _need(type(exc) is QueueError, "queue error wrapped")
        _need(exc.code == code, f"code {exc.code} != {code}")
        _need(exc.code in PROPAGATED, f"code {exc.code} outside the composed domain")
        _need(exc.__cause__ is None and exc.__context__ is None, "propagated queue error chained")
    else:
        raise Fail(f"accepted, expected queue rejection {code}")
    _need(
        (_snap(state), _snap(ledger), _snap(request)) == snaps, "queue rejection changed an input"
    )


def _repair_forms(case):
    form, body = next(iter(case["minimal_repair"].items()))
    return form, body


def _repaired_inputs(case):
    """The repaired (oracle, state, ledger, request) - the repair
    is single-locus by fixture construction."""
    form, body = _repair_forms(case)
    oracle = case["oracle"]
    state = copy.deepcopy(case["state"])
    ledger = copy.deepcopy(case["ledger"])
    request = copy.deepcopy(case["request"])
    if form == "set_oracle":
        oracle = body["oracle"]
    elif form == "replace_state":
        state = copy.deepcopy(body["state"])
    elif form == "replace_ledger":
        ledger = copy.deepcopy(body["ledger"])
    elif form == "replace_request":
        request = copy.deepcopy(body["request"])
    else:
        request[body["field"]] = copy.deepcopy(body["value"])
    return oracle, state, ledger, request


def _run_ok(cls, row):
    engine = _engine(cls, row["oracle"])
    state, ledger = (copy.deepcopy(row["state"]), copy.deepcopy(row["ledger"]))
    result = _checked_apply(engine, state, ledger, copy.deepcopy(row["request"]), row["expect"])
    # replay determinism on the SAME instance: the repeat goes
    # through the FULL per-call pins as a replayed call (snapshot
    # immediately before the repeat inside _checked_apply) - a bare
    # engine.apply would let a repeat-time mutation of the caller's
    # request/state/ledger escape every purity pin
    _checked_apply(
        engine,
        state,
        ledger,
        copy.deepcopy(row["request"]),
        {"outcome": "replayed", "receipt": result["receipt"]},
        previous=result,
    )
    # value determinism on a FRESH instance over fresh copies
    fresh = _engine(cls, row["oracle"])
    replay = _checked_apply(
        fresh,
        copy.deepcopy(row["state"]),
        copy.deepcopy(row["ledger"]),
        copy.deepcopy(row["request"]),
        row["expect"],
    )
    _need(_canon(replay) == _canon(result), "not deterministic")


def _run_malformed(cls, row):
    engine = _engine(cls, row["oracle"])
    state = copy.deepcopy(row["state"])
    ledger = copy.deepcopy(row["ledger"])
    request = copy.deepcopy(row["request"])
    if row["scenario"] == "request-aliases-state":
        # JSON cannot carry object identity: build the alias the
        # row names (the request payload value IS the live state)
        request["payload"]["payload"] = state
    elif row["scenario"] == "request-aliases-ledger":
        request["payload"]["payload"] = ledger
    _rejects(engine, state, ledger, request, row["expect_failure"])
    oracle, state, ledger, request = _repaired_inputs(row)
    want_engine = _engine(_reference.JobsIdempotencyEngine, oracle)
    want = want_engine.apply(copy.deepcopy(state), copy.deepcopy(ledger), copy.deepcopy(request))
    engine = _engine(cls, oracle)
    _checked_apply(engine, state, ledger, request, want)


def _run_queue_rejection(cls, row):
    engine = _engine(cls, row["oracle"])
    for _attempt in range(2):
        _rejects_queue(
            engine,
            copy.deepcopy(row["state"]),
            copy.deepcopy(row["ledger"]),
            copy.deepcopy(row["request"]),
            row["expect_queue_error"],
        )


def _run_rollback(cls, row):
    # ONE engine instance across the refusal and the follow-up - the
    # instance must stay unpolluted after a rejection.  A leg-switched
    # oracle resolves any declared oracle change without dropping
    # same-instance coverage (the current closure declares
    # honest -> honest on every rollback row; the switch honors
    # then_oracle if a future row changes it, one call per leg).
    oracles = [ORACLES[row["oracle"]], ORACLES[row["then_oracle"]]]
    leg = [0]

    def staged(op, payload):
        return oracles[leg[0]](op, payload)

    engine = _engine_for(cls, staged)
    _rejects(
        engine,
        copy.deepcopy(row["state"]),
        copy.deepcopy(row["ledger"]),
        copy.deepcopy(row["request"]),
        row["expect_failure"],
    )
    leg[0] = 1
    _checked_apply(
        engine,
        copy.deepcopy(row["then_state"]),
        copy.deepcopy(row["then_ledger"]),
        copy.deepcopy(row["then_request"]),
        row["expect"],
    )


RUNNERS = {
    "happy": _run_ok,
    "boundary": _run_ok,
    "malformed": _run_malformed,
    "queue_rejection": _run_queue_rejection,
    "rollback": _run_rollback,
}


# -- totality and semantic probes ---------------------------------------------------


def _build_base():
    """A non-trivial valid state + ledger, built through the
    reference engine: task-a enqueued (order-1) then leased to
    worker-1 until 150 (claim-1)."""
    state = _reference.empty_state()
    ledger = []
    eng = _engine(_reference.JobsIdempotencyEngine)
    eng.apply(
        state,
        ledger,
        {
            "idempotency_key": "order-1",
            "op": "enqueue",
            "payload": {"dedupe_key": "task-a", "priority": 5, "payload": {"task": {"job": "a"}}},
        },
    )
    eng.apply(
        state,
        ledger,
        {
            "idempotency_key": "claim-1",
            "op": "claim",
            "payload": {"worker": "worker-1", "now": 100, "lease_ms": 50},
        },
    )
    return state, ledger


_BASE_STATE, _BASE_LEDGER = _build_base()
LEASED = _reference.job_id_for("task-a")


def _base():
    return copy.deepcopy(_BASE_STATE), copy.deepcopy(_BASE_LEDGER)


R_ENQ = {
    "idempotency_key": "order-9",
    "op": "enqueue",
    "payload": {"dedupe_key": "task-z", "priority": 3, "payload": {"a": [1, {"b": None}]}},
}
R_CLAIM = {
    "idempotency_key": "claim-9",
    "op": "claim",
    "payload": {"worker": "worker-2", "now": 110, "lease_ms": 40},
}
R_ACK = {
    "idempotency_key": "ack-9",
    "op": "ack",
    "payload": {"job_id": LEASED, "worker": "worker-1", "now": 120},
}
R_NACK = {
    "idempotency_key": "nack-9",
    "op": "nack",
    "payload": {"job_id": LEASED, "worker": "worker-1", "now": 120},
}
REQUESTS = {"enqueue": R_ENQ, "claim": R_CLAIM, "ack": R_ACK, "nack": R_NACK}


def _req(base, edit=None):
    def build():
        state, ledger = _base()
        request = copy.deepcopy(base)
        if edit is not None:
            request = edit(request)
        return state, ledger, request, "honest"

    return build


def _st(edit, base=R_CLAIM):
    def build():
        state, ledger = _base()
        out = edit(state)
        if out is not None:
            state = out
        return state, ledger, copy.deepcopy(base), "honest"

    return build


def _led(edit, base=R_CLAIM):
    def build():
        state, ledger = _base()
        out = edit(ledger)
        if out is not None:
            ledger = out
        return state, ledger, copy.deepcopy(base), "honest"

    return build


def _orc(oracle, base=R_CLAIM):
    def build():
        state, ledger = _base()
        return state, ledger, copy.deepcopy(base), oracle

    return build


def _deep(n):
    value = {"task": "x"}
    for _ in range(n):
        value = {"task": value}
    return value


def _probe_builders():
    out = {}
    # request containers
    for label, value in (
        ("none", None),
        ("list", [R_ENQ]),
        ("text", "enqueue"),
        ("tuple", tuple(R_ENQ.items())),
    ):
        out[f"request-{label}"] = (_req(R_ENQ, lambda r, v=value: v), MIR)
    for op, base in REQUESTS.items():
        out[f"{op}-dict-subclass"] = (_req(base, _DictSub), MIR)
        out[f"{op}-lying-dict"] = (_req(base, _Lying), MIR)
        out[f"{op}-extra-field"] = (_req(base, lambda r: {**r, "force": True}), MIR)
        out[f"{op}-extra-colliding-key"] = (_req(base, lambda r: {**r, HK("force"): 1}), MIR)
        for field in base:
            for form in KEY_FORMS:
                out[f"{op}-key-{field}-{form}"] = (
                    _req(base, lambda r, f=field, m=form: _rekey(r, f, _hkey(m, f))),
                    MIR,
                )
        for field in base["payload"]:
            for form in KEY_FORMS:
                out[f"{op}-payload-key-{field}-{form}"] = (
                    _req(
                        base,
                        lambda r, f=field, m=form: dict(
                            r, payload=_rekey(r["payload"], f, _hkey(m, f))
                        ),
                    ),
                    MIR,
                )
    # request values
    for label, key in (
        ("int", 5),
        ("int-sub", _IntSub(5)),
        ("bool", True),
        ("none", None),
        ("list", ["k"]),
        ("subclass-str", SK("key-1")),
    ):
        out[f"key-{label}"] = (_req(R_ENQ, lambda r, k=key: dict(r, idempotency_key=k)), MIR)
    for label, key in (
        ("empty", ""),
        ("too-long", "k" * 129),
        ("leading-dot", ".bad"),
        ("trailing-newline", "key\n"),
        ("non-ascii-digit", "١23"),
    ):
        out[f"key-grammar-{label}"] = (
            _req(R_ENQ, lambda r, k=key: dict(r, idempotency_key=k)),
            MIR,
        )
    for label, op in (
        ("int", 1),
        ("bool", True),
        ("none", None),
        ("unregistered", "delete"),
        ("subclass-str", SK("claim")),
        ("trailing-newline", "claim\n"),
    ):
        out[f"op-{label}"] = (_req(R_CLAIM, lambda r, o=op: dict(r, op=o)), MIR)
    for label, payload in (
        ("list", [1]),
        ("text", "x"),
        ("dict-subclass", _DictSub(R_ENQ["payload"])),
        ("lying-dict", _Lying(R_ENQ["payload"])),
    ):
        out[f"payload-{label}"] = (_req(R_ENQ, lambda r, p=payload: dict(r, payload=p)), MIR)
    out["payload-op-smuggle"] = (
        _req(R_ENQ, lambda r: dict(r, payload={**r["payload"], "op": "ack"})),
        MIR,
    )

    def cyclic():
        state, ledger = _base()
        loop = {"dedupe_key": "t", "priority": 1, "payload": {}}
        loop["payload"]["payload"] = loop
        return state, ledger, loop, "honest"

    out["payload-cyclic"] = (cyclic, MIR)

    # oracle boundary: EVERY BaseException from the untrusted
    # fingerprinter fails closed typed (divergent_fingerprint); a
    # raw escape is a mutant failure, not a rejection
    def oracle_system_exit():
        state, ledger = _base()
        return (state, ledger, copy.deepcopy(R_ENQ), _fp_system_exit)

    out["oracle-raises-system-exit"] = (oracle_system_exit, DF)

    def oracle_keyboard_interrupt():
        state, ledger = _base()
        return (state, ledger, copy.deepcopy(R_ENQ), _fp_keyboard_interrupt)

    out["oracle-raises-keyboard-interrupt"] = (oracle_keyboard_interrupt, DF)

    def oracle_plain_exception():
        state, ledger = _base()
        return (state, ledger, copy.deepcopy(R_ENQ), ORACLES["raising"])

    out["oracle-raises-plain-exception"] = (oracle_plain_exception, DF)

    def oracle_mutates_before_token():
        state, ledger = _base()
        return (state, ledger, copy.deepcopy(R_ENQ), _fp_mutating_before_token)

    out["oracle-mutates-before-token"] = (oracle_mutates_before_token, DF)
    out["payload-too-deep"] = (
        _req(
            R_ENQ,
            lambda r: dict(
                r, payload={"dedupe_key": "t", "priority": 1, "payload": _deep(MAX_DEPTH + 1)}
            ),
        ),
        MIR,
    )
    out["payload-too-long-int"] = (
        _req(
            R_ENQ,
            lambda r: dict(
                r, payload={"dedupe_key": "t", "priority": 1, "payload": {"n": 10**MAX_INT_DIGITS}}
            ),
        ),
        MIR,
    )
    out["payload-priority-int-sub"] = (
        _req(R_ENQ, lambda r: dict(r, payload={**r["payload"], "priority": _IntSub(3)})),
        MIR,
    )
    out["payload-priority-bool"] = (
        _req(R_ENQ, lambda r: dict(r, payload={**r["payload"], "priority": True})),
        MIR,
    )
    out["payload-lease-bool"] = (
        _req(R_CLAIM, lambda r: dict(r, payload={**r["payload"], "lease_ms": True})),
        MIR,
    )

    # request/state and request/ledger aliases
    def alias_state():
        state, ledger = _base()
        request = copy.deepcopy(R_ENQ)
        request["payload"]["payload"] = state
        return state, ledger, request, "honest"

    out["request-aliases-state"] = (alias_state, MIR)

    def alias_ledger():
        state, ledger = _base()
        request = copy.deepcopy(R_ENQ)
        request["payload"]["payload"] = ledger
        return state, ledger, request, "honest"

    out["request-aliases-ledger"] = (alias_ledger, MIR)
    # state boundary
    for label, value in (
        ("not-dict", [("jobs", [])]),
        ("dict-subclass", None),
        ("lying-dict", None),
    ):
        if label == "not-dict":
            out[f"state-{label}"] = (_st(lambda s, v=value: v), CQ)
    out["state-dict-subclass"] = (_st(lambda s: _DictSub(s)), CQ)
    out["state-lying-dict"] = (_st(lambda s: _Lying(s)), CQ)
    out["state-jobs-not-list"] = (_st(lambda s: dict(s, jobs={})), CQ)
    out["state-jobs-list-subclass"] = (_st(lambda s: dict(s, jobs=_ListSub(s["jobs"]))), CQ)
    out["state-job-dict-subclass"] = (
        _st(lambda s: dict(s, jobs=[_DictSub(s["jobs"][0])] + s["jobs"][1:])),
        CQ,
    )
    out["state-job-missing-field"] = (
        _st(lambda s: dict(s, jobs=[{k: v for k, v in s["jobs"][0].items() if k != "status"}])),
        CQ,
    )
    out["state-job-extra-field"] = (_st(lambda s: dict(s, jobs=[{**s["jobs"][0], "force": 1}])), CQ)
    out["state-job-bad-status"] = (
        _st(lambda s: dict(s, jobs=[{**s["jobs"][0], "status": "bogus"}])),
        CQ,
    )
    out["state-duplicate-job-id"] = (_st(lambda s: dict(s, jobs=s["jobs"] + [s["jobs"][0]])), CQ)
    out["state-next-seq-bool"] = (_st(lambda s: dict(s, next_seq=True)), CQ)
    out["state-next-seq-negative"] = (_st(lambda s: dict(s, next_seq=-1)), CQ)
    out["state-job-attempts-negative"] = (
        _st(lambda s: dict(s, jobs=[{**s["jobs"][0], "attempts": -1}])),
        CQ,
    )
    out["state-job-lease-bool"] = (
        _st(lambda s: dict(s, jobs=[{**s["jobs"][0], "lease_expires_at": True}])),
        CQ,
    )
    # ledger boundary
    out["ledger-not-list"] = (_led(lambda ledger: {}), CL)
    out["ledger-list-subclass"] = (_led(lambda ledger: _ListSub(ledger)), CL)
    out["ledger-receipt-not-dict"] = (_led(lambda ledger: ["x"] + ledger), CL)
    out["ledger-receipt-dict-subclass"] = (
        _led(lambda ledger: [_DictSub(ledger[0])] + ledger[1:]),
        CL,
    )
    out["ledger-receipt-lying-dict"] = (_led(lambda ledger: [_Lying(ledger[0])] + ledger[1:]), CL)
    out["ledger-receipt-missing-field"] = (
        _led(lambda ledger: [{k: v for k, v in ledger[0].items() if k != "sequence"}] + ledger[1:]),
        CL,
    )
    out["ledger-receipt-extra-field"] = (
        _led(lambda ledger: [{**ledger[0], "force": 1}] + ledger[1:]),
        CL,
    )
    for form in KEY_FORMS:
        out[f"ledger-receipt-key-form-{form}"] = (
            _led(
                lambda ledger, m=form: (
                    [_rekey(ledger[0], "idempotency_key", _hkey(m, "idempotency_key"))] + ledger[1:]
                )
            ),
            CL,
        )
    out["ledger-key-subclass-str"] = (
        _led(
            lambda ledger: (
                [{**ledger[0], "idempotency_key": SK(ledger[0]["idempotency_key"])}] + ledger[1:]
            )
        ),
        CL,
    )
    out["ledger-fp-bad-grammar"] = (
        _led(lambda ledger: [{**ledger[0], "request_fingerprint": "jif1:zz"}] + ledger[1:]),
        CL,
    )
    out["ledger-prior-bad-grammar"] = (
        _led(lambda ledger: [{**ledger[0], "prior_state_id": "qs1:zz"}] + ledger[1:]),
        CL,
    )
    out["ledger-sequence-bool"] = (
        _led(lambda ledger: [{**ledger[0], "sequence": True}] + ledger[1:]),
        CL,
    )
    out["ledger-sequence-gap"] = (
        _led(
            lambda ledger: [
                {
                    **ledger[1],
                    "sequence": 7,
                    "receipt_id": _receipt_id(
                        ledger[1]["idempotency_key"],
                        ledger[1]["request_fingerprint"],
                        ledger[1]["prior_state_id"],
                        ledger[1]["state_id"],
                        7,
                    ),
                }
            ]
        ),
        CL,
    )
    out["ledger-duplicate-key"] = (
        _led(
            lambda ledger: [
                {
                    **ledger[1],
                    "idempotency_key": ledger[0]["idempotency_key"],
                    "receipt_id": _receipt_id(
                        ledger[0]["idempotency_key"],
                        ledger[1]["request_fingerprint"],
                        ledger[1]["prior_state_id"],
                        ledger[1]["state_id"],
                        ledger[1]["sequence"],
                    ),
                }
            ]
        ),
        CL,
    )
    out["ledger-chain-break"] = (
        _led(
            lambda ledger: [
                ledger[0],
                {
                    **ledger[1],
                    "prior_state_id": ledger[0]["prior_state_id"],
                    "receipt_id": _receipt_id(
                        ledger[1]["idempotency_key"],
                        ledger[1]["request_fingerprint"],
                        ledger[0]["prior_state_id"],
                        ledger[1]["state_id"],
                        ledger[1]["sequence"],
                    ),
                },
            ]
        ),
        CL,
    )
    out["ledger-terminal-mismatch"] = (
        _led(
            lambda ledger: [
                ledger[0],
                {
                    **ledger[1],
                    "state_id": ledger[0]["state_id"],
                    "receipt_id": _receipt_id(
                        ledger[1]["idempotency_key"],
                        ledger[1]["request_fingerprint"],
                        ledger[1]["prior_state_id"],
                        ledger[0]["state_id"],
                        ledger[1]["sequence"],
                    ),
                },
            ]
        ),
        CL,
    )
    out["ledger-tampered-receipt-id"] = (
        _led(lambda ledger: [{**ledger[0], "receipt_id": "jir1:" + "0" * 64}] + ledger[1:]),
        CL,
    )
    # oracle boundary
    out["oracle-raises"] = (_orc("raising"), DF)
    out["oracle-non-str"] = (_orc("non_str_output"), DF)
    out["oracle-bad-grammar"] = (_orc("bad_grammar"), DF)
    out["oracle-unbound-token"] = (_orc("unbound_token"), DF)
    out["oracle-lone-surrogate"] = (_orc("lone_surrogate"), DF)
    # key conflicts
    out["conflict-different-payload"] = (
        _req(
            R_ENQ,
            lambda r: dict(r, idempotency_key="order-1", payload={**r["payload"], "priority": 9}),
        ),
        KC,
    )
    out["conflict-different-op"] = (_req(R_CLAIM, lambda r: dict(r, idempotency_key="order-1")), KC)
    return out


PROBES = _probe_builders()
PROBE_MANIFEST = tuple(PROBES)


def _counting(box, fn):
    def fp(op, payload):
        box.append((op, _canon(payload)))
        return fn(op, payload)

    return fp


def _fp_system_exit(op, payload):
    raise SystemExit(1)


def _fp_keyboard_interrupt(op, payload):
    raise KeyboardInterrupt()


def _fp_mutating_after_token(op, payload):
    """Compute the honest token, THEN try to rewrite the argument:
    the engine's detached copy makes the mutation inert."""
    token = _reference.request_fingerprint(op, payload)
    payload["lease_ms"] = 999999
    payload["injected"] = True
    return token


def _fp_mutating_before_token(op, payload):
    """Rewrite the argument, THEN fingerprint the corrupted view:
    the token diverges from the engine's local derivation."""
    payload["lease_ms"] = 999999
    return _reference.request_fingerprint(op, payload)


def _engine_for(cls, oracle):
    return cls(ORACLES[oracle] if type(oracle) is str else oracle)


def _accept_probes():
    """Probes expected to be ACCEPTED with the pinned result."""
    out = {}

    def counting_valid():
        state, ledger = _base()
        box = []
        return (
            state,
            ledger,
            copy.deepcopy(R_ENQ),
            _counting(box, _reference.request_fingerprint),
            box,
            1,
        )

    out["oracle-called-once-valid"] = counting_valid

    def counting_replay():
        state, ledger = _base()
        box = []
        request = {
            "idempotency_key": "order-1",
            "op": "enqueue",
            "payload": {"dedupe_key": "task-a", "priority": 5, "payload": {"task": {"job": "a"}}},
        }
        return (state, ledger, request, _counting(box, _reference.request_fingerprint), box, 1)

    out["oracle-called-once-replay"] = counting_replay

    def mutation_inert():
        state, ledger = _base()
        return (state, ledger, copy.deepcopy(R_ENQ), _fp_mutating_after_token, None, 1)

    out["oracle-mutation-inert"] = mutation_inert
    return out


ACCEPT_PROBES = _accept_probes()


# -- closure-capturing oracle probes ---------------------------------------------
# The detached oracle ARGUMENT does not isolate an AMBIENT closure: a
# malicious fingerprinter can capture the live state/ledger/request
# variables and rewrite them mid-call.  The engine's pre-call
# reference-preserving snapshots plus the finally-restore must return
# every input bit-identical AND nested-identity-identical on the
# rejection path, and must commit exactly the honest transition with
# the request restored on the accept path.  These probes are the
# restore-skipped source mutant's intended-locus witnesses: both the
# reference and that mutant raise the same typed failure, so the kill
# is the purity assertion, never a raw crash.


def _nested_objs(obj, into):
    """Collect every nested container OBJECT (keepalive): comparing
    identity through live references is immune to CPython id reuse
    after deallocation."""
    if type(obj) in (dict, list) and not any(obj is seen for seen in into):
        into.append(obj)
        children = dict.values(obj) if type(obj) is dict else list.__iter__(obj)
        for child in children:
            _nested_objs(child, into)


def _clearing_oracle(state, ledger, request, after):
    """A fingerprinter that rewrites the CAPTURED live inputs through
    its closure, then delegates to AFTER (raise or return a token)."""

    def oracle(op, payload):
        state.clear()
        ledger.clear()
        request.clear()
        return after(op, payload)

    return oracle


def _closure_probes():
    out = {}

    def clears_then_keyboard_interrupt():
        state, ledger = _base()
        request = copy.deepcopy(R_ENQ)
        oracle = _clearing_oracle(state, ledger, request, _fp_keyboard_interrupt)
        return state, ledger, request, oracle, "reject"

    out["closure-clears-then-keyboard-interrupt"] = clears_then_keyboard_interrupt

    def clears_then_plain_exception():
        state, ledger = _base()
        request = copy.deepcopy(R_ENQ)
        oracle = _clearing_oracle(state, ledger, request, ORACLES["raising"])
        return state, ledger, request, oracle, "reject"

    out["closure-clears-then-plain-exception"] = clears_then_plain_exception

    def clears_then_accepts():
        state, ledger = _base()
        request = copy.deepcopy(R_ENQ)
        oracle = _clearing_oracle(state, ledger, request, _reference.request_fingerprint)
        return state, ledger, request, oracle, "accept"

    out["closure-clears-then-accepts"] = clears_then_accepts
    return out


CLOSURE_PROBES = _closure_probes()


def _closure_ok(cls, name):
    state, ledger, request, oracle, mode = CLOSURE_PROBES[name]()
    pre_objs = []
    for obj in (state, ledger, request):
        _nested_objs(obj, pre_objs)
    pre_ids = {id(o) for o in pre_objs}
    request_objs_before = []
    _nested_objs(request, request_objs_before)
    request_ids_before = {id(o) for o in request_objs_before}
    request_snap = _snap(request)
    engine = _engine_for(cls, oracle)
    if mode == "reject":
        _rejects(engine, state, ledger, request, DF)
        after_objs = []
        for obj in (state, ledger, request):
            _nested_objs(obj, after_objs)
        _need({id(o) for o in after_objs} == pre_ids, "restore lost nested container identities")
        return
    # accept path: the mid-call clear is reverted BEFORE the commit,
    # so the result and the committed state/ledger equal the honest
    # run on identical fresh copies, and the request (never committed)
    # is restored bit-identically and identity-identically; the
    # pre-existing ledger entries keep their identity through the
    # in-place restore.
    honest = _engine_for(cls, "honest")
    h_state, h_ledger = _base()
    expected = honest.apply(h_state, h_ledger, copy.deepcopy(R_ENQ))
    ledger_entry_ids = {id(receipt) for receipt in ledger}
    result = engine.apply(state, ledger, request)
    _need(_canon(result) == _canon(expected), "cleared inputs changed the result")
    _need(_canon(state) == _canon(h_state), "committed state differs from the honest run")
    _need(_canon(ledger) == _canon(h_ledger), "committed ledger differs from the honest run")
    _need(
        {id(receipt) for receipt in ledger[: len(h_ledger) - 1]} == ledger_entry_ids,
        "pre-existing ledger entries lost identity",
    )
    _need(_snap(request) == request_snap, "cleared request not restored")
    request_objs_after = []
    _nested_objs(request, request_objs_after)
    _need(
        {id(o) for o in request_objs_after} == request_ids_before, "request identity not preserved"
    )


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    state, ledger, request, oracle = build()
    HOSTILE.clear()
    _Armed.on = True
    try:
        _rejects(_engine_for(cls, oracle), state, ledger, request, failure)
    finally:
        _Armed.on = False
    _need(not HOSTILE, f"hostile calls observed: {HOSTILE[:4]}")


def _accept_ok(cls, name):
    state, ledger, request, oracle, box, want_calls = ACCEPT_PROBES[name]()
    snaps = (_snap(state), _snap(ledger), _snap(request))
    engine = _engine_for(cls, oracle)
    result = engine.apply(state, ledger, request)
    _need(set(dict.keys(result)) == {"outcome", "receipt"}, "result keys")
    _need(result["receipt"]["idempotency_key"] == request["idempotency_key"], "receipt key")
    if box is not None:
        _need(len(box) == want_calls, f"oracle called {len(box)} times, want {want_calls}")
    _need(_snap(request) == snaps[2], "request mutated")


# -- oracle call-count and BaseException totality -------------------------------


def _reject_call_count(cls, label, state_edit, request, failure):
    state, ledger = _base()
    if state_edit is not None:
        state, ledger = state_edit(state, ledger)
    box = []
    engine = _engine_for(cls, _counting(box, _reference.request_fingerprint))
    _rejects(engine, state, ledger, request, failure)
    _need(len(box) == 0, f"{label}: oracle called {len(box)} times on invalid input")


def test_oracle_never_called_on_invalid_inputs():
    for cls in (JobsIdempotencyEngine,):
        _reject_call_count(cls, "bad-request", None, dict(R_CLAIM, op="delete"), MIR)
        _reject_call_count(
            cls,
            "bad-state",
            lambda s, ledger: (dict(s, jobs={}), ledger),
            copy.deepcopy(R_CLAIM),
            CQ,
        )
        _reject_call_count(cls, "bad-ledger", lambda s, ledger: (s, {}), copy.deepcopy(R_CLAIM), CL)


def test_oracle_baseexception_totality():
    """A fingerprinter raising ANY BaseException subclass fails
    closed typed; nothing escapes raw."""
    for oracle in (_fp_system_exit, _fp_keyboard_interrupt, _fp_raising):
        state, ledger = _base()
        _rejects(
            _engine_for(JobsIdempotencyEngine, oracle), state, ledger, copy.deepcopy(R_CLAIM), DF
        )


def test_oracle_mutating_before_token_fails_closed():
    state, ledger = _base()
    _rejects(
        _engine_for(JobsIdempotencyEngine, _fp_mutating_before_token),
        state,
        ledger,
        copy.deepcopy(R_ENQ),
        DF,
    )


# -- pinned semantic probes ------------------------------------------------------


def test_chain_and_sequence_growth():
    """Explicit expectations (not just reference parity): three
    sequential applies chain 0,1,2 with prior binding and the
    terminal receipt naming the live state."""
    engine = _engine(JobsIdempotencyEngine)
    state = _reference.empty_state()
    ledger = []
    key_ops = [
        ("k-enq", "enqueue", {"dedupe_key": "task-g", "priority": 2, "payload": {"task": "g"}}),
        ("k-claim", "claim", {"worker": "w", "now": 10, "lease_ms": 30}),
        ("k-ack", "ack", {"job_id": _reference.job_id_for("task-g"), "worker": "w", "now": 20}),
    ]
    prior = _state_id(state)
    for seq, (key, op, payload) in enumerate(key_ops):
        result = engine.apply(state, ledger, {"idempotency_key": key, "op": op, "payload": payload})
        receipt = result["receipt"]
        assert result["outcome"] == "applied"
        assert receipt["sequence"] == seq
        assert receipt["prior_state_id"] == prior
        assert receipt["state_id"] == _state_id(state)
        assert receipt["receipt_id"] == _receipt_id(
            key, receipt["request_fingerprint"], prior, receipt["state_id"], seq
        )
        assert len(ledger) == seq + 1
        assert receipt is not ledger[-1]
        assert _canon(receipt) == _canon(ledger[-1])
        prior = receipt["state_id"]


def test_state_neutral_apply_commits_and_replays():
    """A fresh key re-claiming the SAME lease is a state-neutral
    APPLY (prior == state_id): it commits a receipt and later
    replays it bit-identically."""
    state, ledger = _base()
    engine = _engine(JobsIdempotencyEngine)
    request = {
        "idempotency_key": "claim-2",
        "op": "claim",
        "payload": {"worker": "worker-1", "now": 100, "lease_ms": 50},
    }
    result = engine.apply(state, ledger, copy.deepcopy(request))
    receipt = result["receipt"]
    assert result["outcome"] == "applied"
    assert receipt["prior_state_id"] == receipt["state_id"]
    assert receipt["sequence"] == 2
    assert len(ledger) == 3
    replay = engine.apply(state, ledger, copy.deepcopy(request))
    assert replay["outcome"] == "replayed"
    assert _canon(replay["receipt"]) == _canon(receipt)
    assert replay["receipt"] is not ledger[-1]
    assert len(ledger) == 3


def test_conflict_then_original_replays():
    """A key-conflicting retry rejects typed and commits nothing;
    the original request then replays the stored receipt."""
    state, ledger = _base()
    engine = _engine(JobsIdempotencyEngine)
    _rejects(
        engine,
        state,
        ledger,
        {
            "idempotency_key": "order-1",
            "op": "enqueue",
            "payload": {"dedupe_key": "task-a", "priority": 9, "payload": {"task": {"job": "a"}}},
        },
        KC,
    )
    result = engine.apply(
        state,
        ledger,
        {
            "idempotency_key": "order-1",
            "op": "enqueue",
            "payload": {"dedupe_key": "task-a", "priority": 5, "payload": {"task": {"job": "a"}}},
        },
    )
    assert result["outcome"] == "replayed"
    assert _canon(result["receipt"]) == _canon(ledger[0])


def test_request_errors_precede_state_and_ledger_errors():
    """Validation order is pinned: a malformed request wins over a
    corrupt state; a corrupt state wins over a corrupt ledger."""
    state, ledger = _base()
    _rejects(
        _engine(JobsIdempotencyEngine),
        dict(state, jobs={}),
        ledger,
        dict(R_CLAIM, op="delete"),
        MIR,
    )
    _rejects(_engine(JobsIdempotencyEngine), dict(state, jobs={}), {}, copy.deepcopy(R_CLAIM), CQ)


# -- mutants -------------------------------------------------------------------------

REFERENCE_TEXT = inspect.getsource(_reference)


def _source_mutant(name, edits):
    """The reference module with EDITS (old, new) applied, each matching
    exactly once; its IdempotencyError is rebound to the bound class so
    typed failures stay comparable. Returns the mutant engine class."""
    text = REFERENCE_TEXT
    for old, new in edits:
        assert text.count(old) == 1, (name, old)
        text = text.replace(old, new)
    module = types.ModuleType(f"tests._t0295_mutant_{name}")
    module.__file__ = _reference.__file__
    exec(compile(text, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    module.IdempotencyError = IdempotencyError
    return module.JobsIdempotencyEngine


class WrongCode(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        try:
            return super().apply(state, ledger, request)
        except IdempotencyError as exc:
            exc.code = "internal"
            raise


class SubclassError(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        try:
            return super().apply(state, ledger, request)
        except IdempotencyError as exc:
            error = type("IdempotencyErrorSub", (IdempotencyError,), {})(
                exc.failure_class, exc.code
            )
        raise error


class ChainedError(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        try:
            return super().apply(state, ledger, request)
        except IdempotencyError as exc:
            raise IdempotencyError(exc.failure_class, exc.code) from exc


class CachedResult(JobsIdempotencyEngine):
    _last = None

    def apply(self, state, ledger, request):
        result = super().apply(state, ledger, request)
        if self._last is not None and self._last == result:
            return self._last
        self._last = result
        return result


class MutatesRequest(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        result = super().apply(state, ledger, request)
        if type(request) is dict:
            request.pop("op", None)
        return result


class RepeatMutatesRequest(JobsIdempotencyEngine):
    """E's repeat-purity escape: delegates cleanly, THEN rewrites the
    caller's request after the result is computed, on every call
    from the second on.  Only a fully-pinned repeat call (snapshot
    immediately before it) sees the corruption."""

    def __init__(self, fp):
        super().__init__(fp)
        self._calls = 0

    def apply(self, state, ledger, request):
        self._calls += 1
        result = super().apply(state, ledger, request)
        if self._calls >= 2:
            request.pop("op", None)
        return result


class RepeatMutatesState(JobsIdempotencyEngine):
    """Same shape, clearing the caller's state after the second-plus
    call returns."""

    def __init__(self, fp):
        super().__init__(fp)
        self._calls = 0

    def apply(self, state, ledger, request):
        self._calls += 1
        result = super().apply(state, ledger, request)
        if self._calls >= 2 and type(state) is dict:
            state.clear()
        return result


class RepeatMutatesLedger(JobsIdempotencyEngine):
    """Same shape, clearing the caller's ledger after the
    second-plus call returns."""

    def __init__(self, fp):
        super().__init__(fp)
        self._calls = 0

    def apply(self, state, ledger, request):
        self._calls += 1
        result = super().apply(state, ledger, request)
        if self._calls >= 2 and type(ledger) is list:
            del ledger[:]
        return result


class NewStateObject(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        work = dict(state) if type(state) is dict else state
        return super().apply(work, ledger, request)


class NewLedgerObject(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        work = list(ledger) if type(ledger) is list else ledger
        return super().apply(state, work, request)


class ReplayReturnsStored(JobsIdempotencyEngine):
    """The replayed receipt returned IS the ledger's stored object,
    not a fresh copy."""

    def apply(self, state, ledger, request):
        result = super().apply(state, ledger, request)
        if result["outcome"] == "replayed":
            for receipt in ledger:
                if receipt["receipt_id"] == result["receipt"]["receipt_id"]:
                    result = {"outcome": "replayed", "receipt": receipt}
        return result


class WrapsQueueError(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        try:
            return super().apply(state, ledger, request)
        except QueueError as exc:
            raise IdempotencyError(CL, FAILURE_MAPPING[CL]) from exc


class SubclassQueueError(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        try:
            return super().apply(state, ledger, request)
        except QueueError as exc:
            error = type("QueueErrorSub", (QueueError,), {})(exc.failure_class, exc.code)
        raise error


class FingerprinterTwice(JobsIdempotencyEngine):
    def apply(self, state, ledger, request):
        if (
            type(request) is dict
            and type(request.get("op")) is str
            and type(request.get("payload")) is dict
        ):
            self.fingerprinter(request["op"], request["payload"])
        return super().apply(state, ledger, request)


class SkipsFingerprintBinding(JobsIdempotencyEngine):
    """The oracle's token is trusted WITHOUT the local derivation."""

    def _fingerprint(self, frozen_req):
        return self.fingerprinter(frozen_req["op"], json.loads(_canon(frozen_req["payload"])))


_SM = {
    "grammar-match": [
        (
            "    return type(value) is str and pattern.fullmatch(value) is not None",
            "    return type(value) is str and pattern.match(value) is not None",
        )
    ],
    "oracle-guard-exception-only": [("        except BaseException:", "        except Exception:")],
    "fingerprint-binding-off": [
        (
            "        if out != request_fingerprint(\n"
            '                frozen_req["op"], frozen_req["payload"]):',
            "        if False and out != request_fingerprint(\n"
            '                frozen_req["op"], frozen_req["payload"]):',
        )
    ],
    "conflict-check-off": [
        (
            '                if stored["request_fingerprint"] != token:',
            '                if False and stored["request_fingerprint"] != token:',
        )
    ],
    "ledger-terminal-off": [
        (
            "        if prev_state is not None and prev_state != live_state_id:",
            "        if False and prev_state is not None:",
        )
    ],
    "receipt-id-reattach-off": [
        (
            "            if rid != derive_receipt_id(",
            "            if False and rid != derive_receipt_id(",
        )
    ],
    # genuinely PERMISSIVE: the sequence continuity check is dropped
    # (the type pin stays); the intended witness is
    # malformed:sequence-gap's reject leg - the mutant ACCEPTS the
    # gapped ledger.  (The earlier seq != -1 variant was RESTRICTIVE -
    # it rejected every valid nonempty ledger - and is withdrawn as
    # mislabeled.)
    "sequence-gap-check-skipped": [
        (
            "            if type(seq) is not int or seq != index:",
            "            if type(seq) is not int:",
        )
    ],
    "restore-skipped": [
        (
            "        finally:\n            for snap in snaps:\n                _restore(snap)",
            "        finally:\n            pass",
        )
    ],
    "alias-check-off": [
        (
            "        if req_ids & _container_ids(state) or \\\n"
            "                req_ids & _container_ids(ledger):",
            "        if False and req_ids & _container_ids(state) or \\\n"
            "                req_ids & _container_ids(ledger):",
        )
    ],
}

# NO source mutant is claimed at ONE locus:
#  - replay-returns-stored-object: the dedupe scan iterates the
#    FROZEN ledger snapshot taken BEFORE the oracle call, so the
#    returned replay receipt derives from that detached copy whatever
#    the oracle does; removing the copy boundaries at the assignment
#    and/or the return changes no observable behavior - all three
#    edits below are verified equivalent.  The OBSERVABLE behavior (a
#    replay never aliases the live ledger) is pinned by the alias
#    checks in the runners and witnessed red by the named behavioral
#    class ReplayReturnsStored.
# ("restore-skipped" IS claimed, witnessed by the closure-capture
# probes: the detached oracle argument does not stop a closure from
# rewriting the live inputs mid-call.)

EQUIVALENT_EDITS = {
    # each edit below is verified to stay green: the dedupe scan
    # runs over the FROZEN pre-oracle ledger snapshot, so no
    # copy-boundary edit at the replay-return locus is observable -
    # not the assignment alone, not the return unwrap alone, not the
    # pair
    "replay-assignment-copy-masked": [
        (
            '                outcome, receipt, staged = "replayed", \\\n'
            "                    dict(stored), None",
            '                outcome, receipt, staged = "replayed", \\\n'
            "                    stored, None",
        )
    ],
    "return-copy-unwrap-masked": [
        (
            '        return {"outcome": outcome, "receipt": dict(receipt)}',
            '        return {"outcome": outcome, "receipt": receipt}',
        )
    ],
    "replay-return-pair-masked-by-frozen-scan": [
        (
            '                outcome, receipt, staged = "replayed", \\\n'
            "                    dict(stored), None",
            '                outcome, receipt, staged = "replayed", \\\n'
            "                    stored, None",
        ),
        (
            '        return {"outcome": outcome, "receipt": dict(receipt)}',
            '        return {"outcome": outcome, "receipt": receipt}',
        ),
    ],
    "comment-reworded": [
        (
            "        # fail closed against the FULL BaseException surface",
            "        # fail closed against the whole BaseException surface",
        )
    ],
}

MUTANTS = {
    "wrong-code": WrongCode,
    "subclass-error": SubclassError,
    "chained-error": ChainedError,
    "cached-result": CachedResult,
    "mutates-request": MutatesRequest,
    "repeat-mutates-request": RepeatMutatesRequest,
    "repeat-mutates-state": RepeatMutatesState,
    "repeat-mutates-ledger": RepeatMutatesLedger,
    "new-state-object": NewStateObject,
    "new-ledger-object": NewLedgerObject,
    "replay-returns-stored": ReplayReturnsStored,
    "wraps-queue-error": WrapsQueueError,
    "subclass-queue-error": SubclassQueueError,
    "fingerprinter-twice": FingerprinterTwice,
    "skips-fingerprint-binding": SkipsFingerprintBinding,
    **{name: _source_mutant(name, edits) for name, edits in _SM.items()},
}


# -- tests ---------------------------------------------------------------------------


def _probe(cls, only=None):
    """Every fixture row and every totality probe through CLS; returns
    the failing labels. Any BaseException escaping a row is a failure."""
    failures = []
    jobs = []
    for section, names in EXPECTED_NAMES.items():
        rows = {r["name"]: r for r in CASES[section]}
        for name in names:
            jobs.append((f"{section}:{name}", lambda s=section, r=rows[name]: RUNNERS[s](cls, r)))
    for name in PROBE_MANIFEST:
        jobs.append((f"totality:{name}", lambda n=name: _totality_ok(cls, n)))
    for name in ACCEPT_PROBES:
        jobs.append((f"accept:{name}", lambda n=name: _accept_ok(cls, n)))
    for name in CLOSURE_PROBES:
        jobs.append((f"closure:{name}", lambda n=name: _closure_ok(cls, n)))
    for label, job in jobs:
        if only is not None and label not in only:
            continue
        try:
            job()
        except BaseException as error:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(error).__name__} {error}"[:200])
    return failures


def test_fixture_closure():
    _validate_closure(CASES)


def test_closure_rejects_substitution():
    for section in EXPECTED_NAMES:
        tampered = copy.deepcopy(CASES)
        row = tampered[section][0]
        key = "defect" if "defect" in row else "kind"
        row[key] = row[key] + " "
        with pytest.raises(AssertionError):
            _validate_closure(tampered)
        swapped = copy.deepcopy(CASES)
        swapped[section].reverse()
        with pytest.raises(AssertionError):
            _validate_closure(swapped)


@pytest.mark.parametrize(
    "section,name", [(s, n) for s, names in EXPECTED_NAMES.items() for n in names]
)
def test_fixture_rows(section, name):
    rows = {r["name"]: r for r in CASES[section]}
    RUNNERS[section](JobsIdempotencyEngine, rows[name])


def test_totality_and_accept_probes():
    assert (
        _probe(
            JobsIdempotencyEngine,
            only={f"totality:{n}" for n in PROBE_MANIFEST} | {f"accept:{n}" for n in ACCEPT_PROBES},
        )
        == []
    )


def test_probes_reach_every_failure_class():
    assert {failure for _, failure in PROBES.values()} == {MIR, CQ, CL, KC, DF}


def test_hostile_rows_cover_every_boundary():
    prefixes = set()
    for name in PROBE_MANIFEST:
        prefixes.add(name.split("-")[0])
    assert {"request", "payload", "state", "ledger", "oracle", "conflict", "key", "op"} <= prefixes


@_REQUIRES_REFERENCE
def test_identity_source_mutant_is_green():
    assert _probe(_source_mutant("identity", [])) == []


@pytest.mark.parametrize("name", sorted(EQUIVALENT_EDITS))
@_REQUIRES_REFERENCE
def test_equivalent_edit_stays_green(name):
    assert _probe(_source_mutant(name, EQUIVALENT_EDITS[name])) == []


# Intended first-witness target labels: a red that NEVER reaches the
# pinned locus fails test_mutant_is_red even when _probe is non-empty
# (a wrong-locus or runner-bug envelope is not semantic provenance).
MUTANT_TARGET_LABELS = {
    # behavioral classes (named surrogate implementations)
    "wrong-code": ("malformed:request-not-a-dict",),
    "subclass-error": ("malformed:request-not-a-dict",),
    "chained-error": ("malformed:request-not-a-dict",),
    "cached-result": ("happy:replay-seen-key-identical-request",),
    "mutates-request": ("happy:apply-first-key-empty-queue",),
    "new-state-object": ("happy:apply-first-key-empty-queue",),
    "new-ledger-object": ("happy:apply-first-key-empty-queue",),
    "replay-returns-stored": ("happy:replay-seen-key-identical-request",),
    "wraps-queue-error": ("queue_rejection:",),
    "subclass-queue-error": ("queue_rejection:",),
    "fingerprinter-twice": ("accept:oracle-called-once",),
    "skips-fingerprint-binding": ("malformed:oracle-unbound-token",),
    "repeat-mutates-request": ("happy:apply-first-key-empty-queue",),
    "repeat-mutates-state": ("happy:apply-first-key-empty-queue",),
    "repeat-mutates-ledger": ("happy:apply-first-key-empty-queue",),
    # source edits on the T0293 reference text
    "grammar-match": ("totality:key-grammar-trailing-newline",),
    "oracle-guard-exception-only": ("totality:oracle-raises-",),
    "fingerprint-binding-off": ("malformed:oracle-unbound-token",),
    "conflict-check-off": ("malformed:key-conflict-different-payload",),
    "ledger-terminal-off": ("malformed:terminal-mismatch",),
    "receipt-id-reattach-off": ("malformed:tampered-receipt-id",),
    "sequence-gap-check-skipped": ("malformed:sequence-gap",),
    "restore-skipped": ("closure:",),
    "alias-check-off": ("malformed:request-aliases-state",),
}


CLASS_MUTANTS = set(MUTANTS) - set(_SM)
assert set(_SM) < set(MUTANTS)


def _assert_red_at_pinned_locus(name):
    failures = _probe(MUTANTS[name])
    assert failures != [], name
    targets = MUTANT_TARGET_LABELS[name]
    assert any(t in f for f in failures for t in targets), (
        f"{name}: red but never at the pinned locus {targets}"
    )


@pytest.mark.parametrize("name", sorted(CLASS_MUTANTS))
def test_mutant_is_red(name):
    _assert_red_at_pinned_locus(name)


@_REQUIRES_REFERENCE
@pytest.mark.parametrize("name", sorted(_SM))
def test_source_mutant_is_red(name):
    _assert_red_at_pinned_locus(name)
