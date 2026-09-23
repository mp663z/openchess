"""T0214: standalone WAL red battery.

The battery stays green on its own head by executing the T0212 reference
WAL (tests.test_t0212_wal_contract) over T0213's pinned vectors
(tests/fixtures/wal/cases.json), following the standalone-red convention.
The later implementation task must switch ONLY the bindings in the
"production bindings" block to the production WAL; every scenario,
closure check and assertion below stays byte-for-byte unchanged when
proving the production implementation green.

What is proven, per fixture section:
- happy / boundary: every append receipt and the final replay equal the
  pins, a second run is byte-identical, inputs never mutate;
- malformed: the ORIGINAL input rejects with the pinned failure class and
  code, leaves log and request bit-identical, and the declared minimal
  neighbour (repair touching only the defect locus) is accepted;
- rollback: the rejected call leaves the very objects handed to it
  bit- and reference-identical, then the pinned follow-up commits;
- append onto a corrupt log: every corrupt replay-row log also rejects a
  valid append with the row's class (append validates the whole log);
- request freeze and detachment: each appended request is unchanged after
  a successful append, the committed entry never aliases the request's
  payload and the receipt is never the log's own entry object;
- totality (PROBE_MANIFEST, 52 in-file probes, production bindings only):
  hostile keys HK (hash collides with a field name, raising __eq__) and SK
  (str subclass, raising __eq__) in request / request payload / request
  record / log entry / entry payload / entry record; SK and int-subclass
  values for op, sequence, entry_id and prior_entry_id; dict and list
  subclasses for request, payload, log and entry; self-referencing log
  and payload; 10**5-level nesting; a 5000-digit sequence int; hostile
  canonicalizers (ValueError, KeyboardInterrupt, SystemExit,
  GeneratorExit, hash-raising str, non-str) and canonicalizers mutating
  or clearing the live request / log mid-call. Each rejects with a
  pinned typed WalError class and code (never a raw escape) with inputs
  identical by value and object id, or - for the mid-call mutators -
  returns the honest receipt with the request restored.

The implementation task must keep every section, including the totality
probes, unchanged.

Closure standard: closed ordered manifests, per-row semantic pins over the
original data, closed per-tag defect-locus / edge checks (an unknown tag
raises), a whole-row sha256 ROW_DIGESTS table whose key set equals the
manifests, a substitution-mutant kill test run with and without digests,
and black-box engine mutants each with a non-vacuous witness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# -- production bindings (the implementation task swaps ONLY these) --------
from store import wal as _ref  # noqa: E402

WalEngine = _ref.WalEngine
WalError = _ref.WalError
canonical_payload = _ref.canonical_payload
FAILURE_MAPPING = _ref.FAILURE_MAPPING
GENESIS = _ref.GENESIS
# ---------------------------------------------------------------------------

FIXTURE = ROOT / "tests" / "fixtures" / "wal" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")

ENTRY_RE = re.compile(r"wal1:[0-9a-f]{64}")
DIGEST_RE = re.compile(r"pdv1:[0-9a-f]{64}")
REGISTERED_OPS = ("put", "delete")
REQUEST_FIELDS = {"op", "payload"}
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}
ENTRY_FIELDS = {"entry_id", "sequence", "op", "payload", "prior_entry_id"}
REPLAY_FIELDS = {"state", "state_id", "head", "applied"}

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def _raising_oracle(identity, record):
    raise ValueError("untrusted canonicalizer failure")


def _non_str_oracle(identity, record):
    return None


def _surrogate_oracle(identity, record):
    return "\ud800"


ORACLES = {"honest": canonical_payload,
           "raising": _raising_oracle,
           "non-str": _non_str_oracle,
           "lone-surrogate": _surrogate_oracle}

MWE = "malformed_wal_entry"
UO = "unknown_operation"
SC = "sequence_conflict"
CC = "corrupt_chain"
DC = "divergent_canonicalization"

# -- closed ordered manifests with semantic pins over the ORIGINAL data -----
# happy/boundary: name -> (oracle, ((op, snapshot_fen), ...) script,
#                          applied, final state size)
HAPPY_MANIFEST = {
    "append-put-put-delete-chain": (
        "honest", (("put", START), ("put", KINGS), ("delete", START)), 3, 1),
    "append-single-put-after-e4": ("honest", (("put", E4),), 1, 1),
    "append-put-delete-put": (
        "honest", (("put", KINGS), ("delete", KINGS), ("put", START)), 3, 1),
}
BOUNDARY_MANIFEST = {
    "replay-empty-log": ("honest", (), 0, 0),
    "delete-missing-identity-noop": ("honest", (("delete", E4),), 1, 0),
    "delete-last-record-empty-state": (
        "honest", (("put", KINGS), ("delete", KINGS)), 2, 0),
}
# malformed: name -> (kind, failure class, oracle, repair form,
#                     original log length, CLOSED TAG)
MALFORMED_MANIFEST = {
    "request-missing-op-field": (
        "append", MWE, "honest", "replace_request", 0,
        "missing-request-field"),
    "request-extra-field": (
        "append", MWE, "honest", "replace_request", 0,
        "extra-request-field"),
    "op-unregistered": (
        "append", UO, "honest", "set_request_field", 0, "unregistered-op"),
    "op-non-str": (
        "append", MWE, "honest", "set_request_field", 0, "non-str-op"),
    "payload-missing-record": (
        "append", MWE, "honest", "replace_request", 0,
        "missing-payload-record"),
    "payload-identity-mismatch": (
        "append", MWE, "honest", "replace_request", 0, "identity-mismatch"),
    "record-wrong-digest": (
        "append", MWE, "honest", "replace_request", 0, "wrong-record-digest"),
    "record-non-str-field": (
        "append", MWE, "honest", "replace_request", 0,
        "non-str-record-field"),
    "oracle-raises": (
        "append", DC, "raising", "set_oracle", 1, "oracle-raises"),
    "oracle-non-str-output": (
        "append", DC, "non-str", "set_oracle", 2, "oracle-non-str-output"),
    "oracle-lone-surrogate": (
        "append", DC, "lone-surrogate", "set_oracle", 0,
        "oracle-lone-surrogate"),
    "log-entry-bad-id-grammar": (
        "replay", MWE, "honest", "replace_log", 2, "bad-entry-id-grammar"),
    "log-sequence-gap": (
        "replay", SC, "honest", "replace_log", 2, "sequence-gap"),
    "log-sequence-duplicate": (
        "replay", SC, "honest", "replace_log", 2, "sequence-duplicate"),
    "log-prior-link-broken": (
        "replay", CC, "honest", "replace_log", 2, "broken-prior-link"),
    "log-entry-id-tampered": (
        "replay", CC, "honest", "replace_log", 2, "tampered-entry-id"),
}
# rollback: name -> (kind, failure class, oracle, then_oracle,
#                    rejected log length, ((op, snapshot_fen), ...) of the
#                    rejected request / follow-up log, relation tag)
ROLLBACK_MANIFEST = {
    "rejected-append-raising-oracle-then-valid-append": (
        "append", DC, "raising", "honest", 0, (("put", START),),
        "same-input-honest-oracle"),
    "rejected-replay-tampered-chain-then-valid-replay": (
        "replay", CC, "honest", "honest", 2,
        (("put", START), ("put", KINGS)), "single-entry-id-repair"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST, "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST, "rollback": ROLLBACK_MANIFEST}

# whole-row sha256 over canonical JSON; key set == manifests
ROW_DIGESTS = {
    "happy:append-put-put-delete-chain":
        "9c849e09852e38f94d8b176c7f0bbd2b51bd597ab22432196fdbb9773a31ef1b",
    "happy:append-single-put-after-e4":
        "ac5c14a97e44293478dec696cb104191b123f62d411aff36d52ba3ddd385a8c5",
    "happy:append-put-delete-put":
        "46263a2c964ff42e29d9b49caa2a1de0dc4f5615fed28299e2eac6d4cec39305",
    "boundary:replay-empty-log":
        "2d1181cbd72e580c0f86a31e53eb4c0881ceb96fefafe9c403af61786996ae22",
    "boundary:delete-missing-identity-noop":
        "53722e872730ef5e6a51e00231b263c02f823fee9c52c90f08c02e61c3afc698",
    "boundary:delete-last-record-empty-state":
        "83caa90938ca381e159710d942683be2c48a8ab18a929af0cf9561faa0a9d997",
    "malformed:request-missing-op-field":
        "5dbfc748e0fb39ec5ddd6a9444cf1c8d78e23fd37973669bad45cd45f7716cbd",
    "malformed:request-extra-field":
        "546a95f6ebb9c36fc1d57f01e3024fc715b0bf1bd960bb6de6b3495a023ce43a",
    "malformed:op-unregistered":
        "49e3bb62ef1e4f59dc5d4795458a79835ffb45e13ce98c94fba45de9362e5d56",
    "malformed:op-non-str":
        "80a1886a14e298c52fcce51cc3fb082c5b6866965dcaa84435a6932f289d8d40",
    "malformed:payload-missing-record":
        "41c187cad716e7064f0fc4464279fa4fadce63a12540042e8034ece7f121d07e",
    "malformed:payload-identity-mismatch":
        "f3898a2510ec59d602782ac77d00cb56a3b99b35a898f26f18c2588493812b05",
    "malformed:record-wrong-digest":
        "411674ab73641e8671f62cc02a840e6b8ddee746a77a11404004804d652885b2",
    "malformed:record-non-str-field":
        "09dbfb872af3d276a01ab873622c36361971c09bacbe1cb4e2b78b41ca7086c6",
    "malformed:oracle-raises":
        "9dcb3f432ce79dfa25f03271518302d853d69cf5e94bb2d5dfbb4caa5ef3729a",
    "malformed:oracle-non-str-output":
        "6601322339729fce6a47904925fee4380d4503acfabc06588923cc9155525b49",
    "malformed:oracle-lone-surrogate":
        "503dcad0e1f130c02b8ed70fe48cfdc3d523923b0bd5c6036d85a6070b554c41",
    "malformed:log-entry-bad-id-grammar":
        "7941aed28cc89e33cc4e6536beb4ff21f8b2ba1268af0ee74d0823669b5b41c1",
    "malformed:log-sequence-gap":
        "646340ed84efa06c981a297e86d1ef4824d6f33cb8a16be23881be6af56cfa71",
    "malformed:log-sequence-duplicate":
        "9484dc9e856d33f022af7fce7f698a4f30bfbac741bdeb683a10fcc476b51396",
    "malformed:log-prior-link-broken":
        "3e0599f4a019a1058dcece405086b7da9794abeb08431df791dbc5e5f1819fba",
    "malformed:log-entry-id-tampered":
        "ab86819df6fbe8a449b3daed4679cd36fa5d277d55f207ec3a350e4860b2ed20",
    "rollback:rejected-append-raising-oracle-then-valid-append":
        "73fc15559ea180486a5916f2bce621884a40d08e92181c9311932992eeece838",
    "rollback:rejected-replay-tampered-chain-then-valid-replay":
        "1fe393cd3b98eacb30ff70a73ead16ccb1f0d21b705a1429b749918d864dcc45",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _diff_paths(a, b, path=()):
    """Every leaf path where two JSON values differ (type-exact)."""
    if type(a) is dict and type(b) is dict:
        out = set()
        for key in set(a) | set(b):
            if key not in a or key not in b:
                out.add((*path, key))
            else:
                out |= _diff_paths(a[key], b[key], (*path, key))
        return out
    if type(a) is list and type(b) is list:
        if len(a) != len(b):
            return {(*path, "#len")}
        out = set()
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out |= _diff_paths(x, y, (*path, i))
        return out
    return set() if (type(a), a) == (type(b), b) else {path}


def _repaired(case):
    """Apply the declarative minimal repair (one closed form)."""
    rep = case["minimal_repair"]
    assert len(rep) == 1, case["name"]
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure", "minimal_repair")}
    (form, body), = rep.items()
    if form == "set_request_field":
        assert set(body) == {"field", "value"}, case["name"]
        out["request"][body["field"]] = copy.deepcopy(body["value"])
    elif form == "replace_request":
        assert set(body) == {"request"}, case["name"]
        out["request"] = copy.deepcopy(body["request"])
    elif form == "replace_log":
        assert set(body) == {"log"}, case["name"]
        out["log"] = copy.deepcopy(body["log"])
    elif form == "set_oracle":
        assert set(body) == {"oracle"}, case["name"]
        out["oracle"] = body["oracle"]
    else:
        raise AssertionError(f"{case['name']}: unknown repair form {form}")
    return out


def _valid_request(req, name):
    assert type(req) is dict and set(req) == REQUEST_FIELDS, name
    assert req["op"] in REGISTERED_OPS, name
    payload = req["payload"]
    assert set(payload) == {"identity", "record"}, name
    assert set(payload["record"]) == RECORD_FIELDS, name
    assert all(type(v) is str for v in payload["record"].values()), name
    assert DIGEST_RE.fullmatch(payload["record"]["digest"]), name


def _valid_log(log, name):
    assert type(log) is list, name
    prior = GENESIS
    for i, entry in enumerate(log, start=1):
        assert set(entry) == ENTRY_FIELDS, name
        assert ENTRY_RE.fullmatch(entry["entry_id"]), name
        assert entry["sequence"] == i, name
        assert entry["prior_entry_id"] == prior, name
        prior = entry["entry_id"]


# -- closed per-tag defect-locus checks (unknown tag raises) ---------------
def _locus(case, rep):
    form = next(iter(case["minimal_repair"]))
    if form in ("replace_request", "set_request_field"):
        return _diff_paths(case["request"], rep["request"])
    if form == "replace_log":
        return _diff_paths(case["log"], rep["log"])
    return {("oracle",)} if case["oracle"] != rep["oracle"] else set()


def _check_tag(case, tag):
    name = case["name"]
    rep = _repaired(case)
    locus = _locus(case, rep)
    req = case.get("request")
    if tag in ("oracle-raises", "oracle-non-str-output",
               "oracle-lone-surrogate"):
        selector = tag.removeprefix("oracle-").replace("-output", "")
        selector = {"raises": "raising"}.get(selector, selector)
        assert case["oracle"] == selector, name
        assert rep["oracle"] == "honest" and locus == {("oracle",)}, name
        _valid_request(req, name)
        _valid_log(case["log"], name)
        return
    if case["kind"] == "append":
        _valid_request(rep["request"], name)
        _valid_log(case["log"], name)
        assert rep["oracle"] == case["oracle"] == "honest", name
        if tag == "missing-request-field":
            assert locus == {("op",)} and "op" not in req, name
        elif tag == "extra-request-field":
            assert len(locus) == 1, name
            (extra,), = locus
            assert extra not in REQUEST_FIELDS and extra in req, name
            assert extra not in rep["request"], name
        elif tag == "unregistered-op":
            assert locus == {("op",)}, name
            assert type(req["op"]) is str, name
            assert req["op"] not in REGISTERED_OPS, name
        elif tag == "non-str-op":
            assert locus == {("op",)} and type(req["op"]) is not str, name
        elif tag == "missing-payload-record":
            assert locus == {("payload", "record")}, name
            assert "record" not in req["payload"], name
        elif tag == "identity-mismatch":
            assert locus == {("payload", "identity")}, name
            assert type(req["payload"]["identity"]) is str, name
        elif tag == "wrong-record-digest":
            assert locus == {("payload", "record", "digest")}, name
            assert DIGEST_RE.fullmatch(
                req["payload"]["record"]["digest"]), name
        elif tag == "non-str-record-field":
            assert len(locus) == 1, name
            (p, r, field), = locus
            assert (p, r) == ("payload", "record"), name
            assert field in RECORD_FIELDS, name
            assert type(req["payload"]["record"][field]) is not str, name
        else:
            raise AssertionError(f"{name}: unknown scenario tag {tag!r}")
        return
    # replay-kind log defects: exactly one entry, one field
    log, fixed = case["log"], rep["log"]
    _valid_log(fixed, name)
    assert rep["oracle"] == case["oracle"] == "honest", name
    assert len(locus) == 1, name
    (idx, field), = locus
    bad, good = log[idx][field], fixed[idx][field]
    if tag == "bad-entry-id-grammar":
        assert field == "entry_id", name
        assert not (type(bad) is str and ENTRY_RE.fullmatch(bad)), name
    elif tag == "tampered-entry-id":
        assert field == "entry_id", name
        assert type(bad) is str and ENTRY_RE.fullmatch(bad), name
        assert bad != good, name
    elif tag == "sequence-gap":
        assert field == "sequence" and idx > 0, name
        assert type(bad) is int and bad > idx + 1, name
    elif tag == "sequence-duplicate":
        assert field == "sequence" and idx > 0, name
        assert bad == log[idx - 1]["sequence"], name
    elif tag == "broken-prior-link":
        assert field == "prior_entry_id" and idx > 0, name
        assert bad != log[idx - 1]["entry_id"], name
        assert good == log[idx - 1]["entry_id"], name
    else:
        raise AssertionError(f"{name}: unknown scenario tag {tag!r}")


def _check_rollback_relation(case, tag):
    name = case["name"]
    exp = case["expect"]
    if tag == "same-input-honest-oracle":
        assert case["kind"] == "append", name
        _valid_request(case["request"], name)
        _valid_log(case["log"], name)
        assert set(exp) == ENTRY_FIELDS, name
        assert exp["sequence"] == len(case["log"]) + 1, name
        assert exp["prior_entry_id"] == (
            case["log"][-1]["entry_id"] if case["log"] else GENESIS), name
        assert (exp["op"], exp["payload"]) == (
            case["request"]["op"], case["request"]["payload"]), name
    elif tag == "single-entry-id-repair":
        assert case["kind"] == "replay", name
        _valid_log(case["then_log"], name)
        locus = _diff_paths(case["log"], case["then_log"])
        assert len(locus) == 1, name
        (idx, field), = locus
        assert field == "entry_id", name
        assert ENTRY_RE.fullmatch(case["log"][idx]["entry_id"]), name
        assert set(exp) == REPLAY_FIELDS, name
        assert exp["applied"] == len(case["then_log"]), name
        assert exp["head"] == case["then_log"][-1]["entry_id"], name
    else:
        raise AssertionError(f"{name}: unknown rollback relation {tag!r}")


def _script_sig(case):
    return tuple((a["op"], a["payload"]["record"]["snapshot_fen"])
                 for a in case["appends"])


def _check_edges(section, case):
    name = case["name"]
    exp = case["expect"]
    receipts, replay = exp["receipts"], exp["replay"]
    assert len(receipts) == len(case["appends"]), name
    for i, (req, rec) in enumerate(
            zip(case["appends"], receipts, strict=True), 1):
        _valid_request(req, name)
        assert set(rec) == ENTRY_FIELDS and rec["sequence"] == i, name
        assert (rec["op"], rec["payload"]) == (req["op"], req["payload"])
    _valid_log(receipts, name)
    assert set(replay) == REPLAY_FIELDS, name
    assert replay["head"] == (receipts[-1]["entry_id"]
                              if receipts else GENESIS), name
    if section == "boundary":
        if name == "replay-empty-log":
            assert case["appends"] == [] and replay["state"] == {}, name
        elif name == "delete-missing-identity-noop":
            assert replay["state"] == {}, name
        elif name == "delete-last-record-empty-state":
            (put, delete) = case["appends"]
            assert put["payload"] == delete["payload"], name
            assert replay["state"] == {}, name
        else:
            raise AssertionError(f"{name}: unknown boundary edge")
    else:
        live = {}
        for req in case["appends"]:
            ident = req["payload"]["identity"]
            if req["op"] == "put":
                live[ident] = req["payload"]["record"]
            else:
                live.pop(ident, None)
        assert replay["state"] == live, name


def _validate_closure(cases):
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == list(manifest), section
        for row in rows:
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS.get(label) == _row_digest(row), label
            meta = manifest[row["name"]]
            if section in ("happy", "boundary"):
                assert (row["oracle"], _script_sig(row),
                        row["expect"]["replay"]["applied"],
                        len(row["expect"]["replay"]["state"])) == meta
                _check_edges(section, row)
            elif section == "malformed":
                kind, failure, oracle, form, nlog, tag = meta
                assert (row["kind"], row["expect_failure"], row["oracle"],
                        next(iter(row["minimal_repair"])),
                        len(row["log"])) == (kind, failure, oracle, form,
                                             nlog), label
                assert FAILURE_MAPPING[failure], label
                _check_tag(row, tag)
            else:
                kind, failure, oracle, then, nlog, sig, tag = meta
                items = ([row["request"]] if row["kind"] == "append"
                         else row["then_log"])
                assert (row["kind"], row["expect_failure"], row["oracle"],
                        row["then_oracle"], len(row["log"]),
                        tuple((e["op"], e["payload"]["record"]["snapshot_fen"])
                              for e in items)) == (
                    kind, failure, oracle, then, nlog, sig), label
                _check_rollback_relation(row, tag)
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}


def test_fixture_closure():
    assert set(CASES) >= set(SECTIONS)
    _validate_closure(CASES)


def test_unknown_tags_raise():
    row = CASES["malformed"][0]
    with pytest.raises(AssertionError, match="unknown scenario tag"):
        _check_tag(row, "not-a-tag")
    replay_row = CASES["malformed"][-1]
    with pytest.raises(AssertionError, match="unknown scenario tag"):
        _check_tag(replay_row, "not-a-tag")
    with pytest.raises(AssertionError, match="unknown rollback relation"):
        _check_rollback_relation(CASES["rollback"][0], "not-a-tag")


# -- the red battery (engine factory parameter lets mutants reuse it) ------
def _run_script(make, case):
    appends_p = copy.deepcopy(case["appends"])
    runs = []
    for _ in range(2):
        engine = make(ORACLES[case["oracle"]])
        log, receipts = [], []
        for request in copy.deepcopy(appends_p):
            req_p = copy.deepcopy(request)
            receipt = engine.append(log, request)
            assert request == req_p, case["name"]
            # detachment: the committed entry shares nothing with the
            # caller's request and the receipt is not the log's object
            assert log[-1]["payload"] is not request["payload"]
            assert receipt is not log[-1], case["name"]
            receipts.append(receipt)
        assert receipts == case["expect"]["receipts"], case["name"]
        assert log == receipts, case["name"]
        log_p = copy.deepcopy(log)
        result = make(ORACLES[case["oracle"]]).replay(log)
        assert result == case["expect"]["replay"], case["name"]
        assert log == log_p, case["name"]
        runs.append((receipts, result))
    assert runs[0] == runs[1], case["name"]
    assert case["appends"] == appends_p, case["name"]


def _run_malformed(make, case):
    log = copy.deepcopy(case["log"])
    req = copy.deepcopy(case.get("request"))
    snap = copy.deepcopy((log, req))
    ids = _deep_ids(log, []) + _deep_ids(req, [])
    engine = make(ORACLES[case["oracle"]])
    try:
        if case["kind"] == "append":
            engine.append(log, req)
        else:
            engine.replay(log)
    except WalError as exc:
        assert exc.failure_class == case["expect_failure"], case["name"]
        assert exc.code == FAILURE_MAPPING[case["expect_failure"]]
        assert (log, req) == snap, case["name"]
        assert _deep_ids(log, []) + _deep_ids(req, []) == ids, case["name"]
    else:
        raise AssertionError(f"{case['name']}: input unexpectedly accepted")
    rep = _repaired(case)
    engine = make(ORACLES[rep["oracle"]])
    if case["kind"] == "append":
        before = len(rep["log"])
        entry = engine.append(rep["log"], rep["request"])
        assert set(entry) == ENTRY_FIELDS, case["name"]
        assert entry["sequence"] == before + 1, case["name"]
        assert rep["log"][-1] == entry, case["name"]
        _valid_log(rep["log"], case["name"])
    else:
        result = engine.replay(rep["log"])
        assert set(result) == REPLAY_FIELDS, case["name"]
        assert result["applied"] == len(rep["log"]), case["name"]
        assert result["head"] == rep["log"][-1]["entry_id"], case["name"]


def _deep_ids(obj, acc):
    acc.append(id(obj))
    if type(obj) is dict:
        for v in obj.values():
            _deep_ids(v, acc)
    elif type(obj) is list:
        for v in obj:
            _deep_ids(v, acc)
    return acc


def _run_rollback(make, case):
    log = copy.deepcopy(case["log"])
    req = copy.deepcopy(case.get("request"))
    snap = copy.deepcopy((log, req))
    ids = _deep_ids(log, []) + _deep_ids(req, [])
    engine = make(ORACLES[case["oracle"]])
    with pytest.raises(WalError) as exc:
        if case["kind"] == "append":
            engine.append(log, req)
        else:
            engine.replay(log)
    assert exc.value.failure_class == case["expect_failure"], case["name"]
    assert exc.value.code == FAILURE_MAPPING[case["expect_failure"]]
    assert (log, req) == snap, case["name"]
    assert _deep_ids(log, []) + _deep_ids(req, []) == ids, case["name"]
    follow = make(ORACLES[case["then_oracle"]])
    if case["kind"] == "append":
        assert follow.append(log, req) == case["expect"], case["name"]
        assert log == snap[0] + [case["expect"]], case["name"]
    else:
        then_log = copy.deepcopy(case["then_log"])
        assert follow.replay(then_log) == case["expect"], case["name"]
        assert then_log == case["then_log"], case["name"]


def _ids(section):
    return [r["name"] for r in CASES[section]]


@pytest.mark.parametrize("case", CASES["happy"], ids=_ids("happy"))
def test_wal_happy(case):
    _run_script(WalEngine, case)


@pytest.mark.parametrize("case", CASES["boundary"], ids=_ids("boundary"))
def test_wal_boundary(case):
    _run_script(WalEngine, case)


@pytest.mark.parametrize("case", CASES["malformed"], ids=_ids("malformed"))
def test_wal_malformed_and_minimal_neighbour(case):
    _run_malformed(WalEngine, case)


@pytest.mark.parametrize("case", CASES["rollback"], ids=_ids("rollback"))
def test_wal_rollback_and_recovery(case):
    _run_rollback(WalEngine, case)


def test_param_ids_equal_manifest_order():
    for section, manifest in MANIFESTS.items():
        assert _ids(section) == list(manifest), section


# -- totality: hostile inputs through the production bindings ONLY ---------
# The fixture rows are all well-formed JSON, so they cannot prove the
# engine is total. These probes are in-file builders (hostile keys and
# subclasses do not serialize) and run exclusively through WalEngine /
# WalError / canonical_payload, so the implementation task proves the
# production WAL total by swapping only the bindings.


class HK:
    """Hostile key: hash collides with a field name, __eq__ raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


class SK(str):
    """str-subclass key/value whose __eq__ raises."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile str __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile str __ne__")


class IK(int):
    """int-subclass value whose comparisons raise."""

    __hash__ = int.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile int __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile int __ne__")


class _EvilDict(dict):
    def keys(self):
        raise RuntimeError("evil keys")

    def items(self):
        raise RuntimeError("evil items")

    def __iter__(self):
        raise RuntimeError("evil iter")

    def __getitem__(self, key):
        raise RuntimeError("evil getitem")


class _EvilList(list):
    def __iter__(self):
        raise RuntimeError("evil iter")

    def __len__(self):
        raise RuntimeError("evil len")

    def __getitem__(self, index):
        raise RuntimeError("evil getitem")


class _EvilStr(str):
    def __hash__(self):
        raise RuntimeError("evil hash")


def _base_log():
    return copy.deepcopy(
        _bn(CASES, "rollback",
            "rejected-replay-tampered-chain-then-valid-replay")["then_log"])


def _base_request():
    return copy.deepcopy(
        _bn(CASES, "happy", "append-single-put-after-e4")["appends"][0])


def _rekey(mapping, field, key_cls):
    """Same mapping with `field` replaced by a hostile key object."""
    out = {k: v for k, v in mapping.items() if k != field}
    out[key_cls(field)] = mapping[field]
    return out


def _deep(levels):
    node = []
    for _ in range(levels):
        node = [node]
    return node


def _req_key(where, key_cls):
    def build():
        req = _base_request()
        if where == "request":
            req = _rekey(req, "op", key_cls)
        elif where == "payload":
            req["payload"] = _rekey(req["payload"], "record", key_cls)
        else:
            req["payload"]["record"] = _rekey(
                req["payload"]["record"], "variant", key_cls)
        return "append", [], req, "honest"
    return build


def _log_key(where, key_cls, call):
    def build():
        log = _base_log()
        if where == "entry":
            log[1] = _rekey(log[1], "entry_id", key_cls)
        elif where == "payload":
            log[1]["payload"] = _rekey(log[1]["payload"], "identity",
                                       key_cls)
        else:
            log[1]["payload"]["record"] = _rekey(
                log[1]["payload"]["record"], "digest", key_cls)
        return call, log, _base_request(), "honest"
    return build


def _entry_value(field, value):
    def build():
        log = _base_log()
        log[1][field] = value()
        return "replay", log, None, "honest"
    return build


def _op_value(value):
    def build():
        req = _base_request()
        req["op"] = value()
        return "append", [], req, "honest"
    return build


def _containers(target):
    def build():
        log, req = _base_log(), _base_request()
        call = "append"
        if target == "request-dict-sub":
            req = _EvilDict(req)
        elif target == "request-list-sub":
            req = _EvilList([req["op"], req["payload"]])
        elif target == "payload-dict-sub":
            req["payload"] = _EvilDict(req["payload"])
        elif target == "payload-list-sub":
            req["payload"] = _EvilList(list(req["payload"].values()))
        elif target == "log-list-sub-replay":
            log, call = _EvilList(log), "replay"
        elif target == "log-list-sub-append":
            log = _EvilList(log)
        elif target == "log-dict-sub":
            log, call = _EvilDict(enumerate(log)), "replay"
        elif target == "entry-dict-sub":
            log[1], call = _EvilDict(log[1]), "replay"
        elif target == "entry-list-sub":
            log[1], call = _EvilList(list(log[1].values())), "replay"
        elif target == "entry-payload-dict-sub":
            log[1]["payload"], call = _EvilDict(log[1]["payload"]), "replay"
        else:
            raise AssertionError(f"unknown container probe {target!r}")
        return call, log, req, "honest"
    return build


def _structural(target):
    def build():
        log, req = _base_log(), _base_request()
        call = "append"
        if target == "self-referencing-log-replay":
            log.append(log)
            call = "replay"
        elif target == "self-referencing-log-append":
            log.append(log)
        elif target == "self-referencing-payload":
            req["payload"]["record"] = req["payload"]
        elif target == "deep-nesting-request":
            req["payload"]["record"] = _deep(10**5)
        elif target == "deep-nesting-log":
            log[1]["payload"] = _deep(10**5)
            call = "replay"
        elif target == "huge-int-sequence-replay":
            log[1]["sequence"] = 10**5000 - 1  # 5000 digits
            call = "replay"
        elif target == "huge-int-sequence-append":
            log[1]["sequence"] = 10**5000 - 1  # 5000 digits
        else:
            raise AssertionError(f"unknown structural probe {target!r}")
        return call, log, req, "honest"
    return build


def _raise(exc):
    def oracle(identity, record):
        raise exc("hostile canonicalizer")
    return oracle


_PROBE_ORACLES = {
    "raises-value-error": _raise(ValueError),
    "raises-keyboard-interrupt": _raise(KeyboardInterrupt),
    "raises-system-exit": _raise(SystemExit),
    "raises-generator-exit": _raise(GeneratorExit),
    "returns-evil-str": lambda identity, record: _EvilStr("canonical"),
    "returns-list": lambda identity, record: [],
}


def _hostile_oracle(which):
    def build():
        return "append", _base_log(), _base_request(), which
    return build


def _live_mutating_oracle(which):
    """The oracle mutates the caller's live request / log mid-call."""
    def build():
        log, req = _base_log(), _base_request()

        def oracle(identity, record):
            if which == "mutates-request":
                req["op"] = "delete"
                req["payload"]["identity"] = "forged"
                req["payload"]["record"]["digest"] = "forged"
            elif which == "clears-request":
                req.clear()
            elif which == "mutates-log":
                if log and type(log[0]) is dict:
                    log[0]["injected"] = 1
                    log.append({"junk": True})
            else:
                raise AssertionError(f"unknown oracle probe {which!r}")
            return canonical_payload(identity, record)
        return "append", log, req, oracle
    return build


ACCEPT = "accept-honest-receipt"
# closed ordered probe manifest: name -> pinned outcome (failure class, or
# ACCEPT = the honest receipt with inputs restored)
PROBE_MANIFEST = {
    "hk-key-request": MWE,
    "hk-key-request-payload": MWE,
    "hk-key-request-record": MWE,
    "hk-key-entry-replay": MWE,
    "hk-key-entry-append": MWE,
    "hk-key-entry-payload-replay": MWE,
    "hk-key-entry-payload-append": MWE,
    "hk-key-entry-record-replay": MWE,
    "hk-key-entry-record-append": MWE,
    "sk-key-request": MWE,
    "sk-key-request-payload": MWE,
    "sk-key-request-record": MWE,
    "sk-key-entry-replay": MWE,
    "sk-key-entry-append": MWE,
    "sk-key-entry-payload-replay": MWE,
    "sk-key-entry-payload-append": MWE,
    "sk-key-entry-record-replay": MWE,
    "sk-key-entry-record-append": MWE,
    "sk-value-op": MWE,
    "ik-value-op": MWE,
    "sk-value-sequence": MWE,
    "ik-value-sequence": MWE,
    "sk-value-entry-id": MWE,
    "ik-value-entry-id": MWE,
    "sk-value-prior-entry-id": MWE,
    "ik-value-prior-entry-id": MWE,
    "request-dict-sub": MWE,
    "request-list-sub": MWE,
    "payload-dict-sub": MWE,
    "payload-list-sub": MWE,
    "log-list-sub-replay": MWE,
    "log-list-sub-append": MWE,
    "log-dict-sub": MWE,
    "entry-dict-sub": MWE,
    "entry-list-sub": MWE,
    "entry-payload-dict-sub": MWE,
    "self-referencing-log-replay": MWE,
    "self-referencing-log-append": MWE,
    "self-referencing-payload": MWE,
    "deep-nesting-request": MWE,
    "deep-nesting-log": MWE,
    "huge-int-sequence-replay": SC,
    "huge-int-sequence-append": SC,
    "oracle-raises-value-error": DC,
    "oracle-raises-keyboard-interrupt": DC,
    "oracle-raises-system-exit": DC,
    "oracle-raises-generator-exit": DC,
    "oracle-returns-evil-str": DC,
    "oracle-returns-list": DC,
    "oracle-mutates-request": ACCEPT,
    "oracle-clears-request": ACCEPT,
    "oracle-mutates-log": ACCEPT,
}


def _probe_builders():
    out = {}
    for prefix, cls in (("hk", HK), ("sk", SK)):
        for where in ("request", "payload", "record"):
            label = "request" if where == "request" else f"request-{where}"
            out[f"{prefix}-key-{label}"] = _req_key(where, cls)
        for where in ("entry", "payload", "record"):
            label = "entry" if where == "entry" else f"entry-{where}"
            for call in ("replay", "append"):
                out[f"{prefix}-key-{label}-{call}"] = _log_key(
                    where, cls, call)
    out["sk-value-op"] = _op_value(lambda: SK("put"))
    out["ik-value-op"] = _op_value(lambda: IK(1))
    for field, label, sval, ival in (
            ("sequence", "sequence", "2", 2),
            ("entry_id", "entry-id", "wal1:" + "0" * 64, 7),
            ("prior_entry_id", "prior-entry-id", "wal1:" + "0" * 64, 7)):
        out[f"sk-value-{label}"] = _entry_value(
            field, lambda s=sval: SK(s))
        out[f"ik-value-{label}"] = _entry_value(
            field, lambda i=ival: IK(i))
    for target in ("request-dict-sub", "request-list-sub",
                   "payload-dict-sub", "payload-list-sub",
                   "log-list-sub-replay", "log-list-sub-append",
                   "log-dict-sub", "entry-dict-sub", "entry-list-sub",
                   "entry-payload-dict-sub"):
        out[target] = _containers(target)
    for target in ("self-referencing-log-replay",
                   "self-referencing-log-append",
                   "self-referencing-payload", "deep-nesting-request",
                   "deep-nesting-log", "huge-int-sequence-replay",
                   "huge-int-sequence-append"):
        out[target] = _structural(target)
    for which in _PROBE_ORACLES:
        out[f"oracle-{which}"] = _hostile_oracle(which)
    for which in ("mutates-request", "clears-request", "mutates-log"):
        out[f"oracle-{which}"] = _live_mutating_oracle(which)
    return out


PROBES = _probe_builders()
HOSTILE_KEY_PROBES = [n for n in PROBE_MANIFEST
                      if n.startswith(("hk-key-", "sk-key-"))]


def _snap(obj):
    """Flat identity-and-value snapshot. Iterative (survives 10**5
    nesting), cycle-safe, never hashes/compares/reprs a caller-owned key
    or subclass instance, never converts a huge int to text."""
    out, seen, stack = [], set(), [obj]
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is dict or kind is list:
            if id(node) in seen:
                out.append(("seen", id(node)))
                continue
            seen.add(id(node))
            if kind is dict:
                items = list(dict.items(node))
                out.append(("dict", id(node), len(items)))
                for key, value in reversed(items):
                    stack.append(value)
                    stack.append(("key", key if type(key) is str
                                  else id(key)))
            else:
                values = list(list.__iter__(node))
                out.append(("list", id(node), len(values)))
                stack.extend(reversed(values))
        elif kind is tuple and len(node) == 2 and node[0] == "key":
            out.append(node)
        elif kind is int:
            out.append(("int", id(node), node.bit_length(), node & 0xFFFF))
        elif kind in (str, bool, float, type(None)):
            out.append((kind.__name__, node))
        else:
            out.append(("object", kind.__name__, id(node)))
    return out


def _run_probe(make, name):
    call, log, req, oracle = PROBES[name]()
    expected = PROBE_MANIFEST[name]
    fn = _PROBE_ORACLES.get(oracle, ORACLES.get(oracle, oracle)) \
        if isinstance(oracle, str) else oracle
    before_log, before_req = _snap(log), _snap(req)
    engine = make(fn)
    try:
        result = (engine.append(log, req) if call == "append"
                  else engine.replay(log))
    except WalError as exc:
        assert expected != ACCEPT, f"{name}: rejected {exc.failure_class}"
        assert exc.failure_class == expected, name
        assert exc.code == FAILURE_MAPPING[expected], name
        assert _snap(log) == before_log, name
        assert _snap(req) == before_req, name
        return
    except BaseException as exc:  # a raw escape is the defect
        raise AssertionError(
            f"{name}: raw {type(exc).__name__} escaped") from None
    assert expected == ACCEPT, f"{name}: hostile input accepted"
    honest_log, honest_req = _base_log(), _base_request()
    receipt = make(canonical_payload).append(honest_log, honest_req)
    assert result == receipt, name
    assert _snap(req) == before_req, name
    assert log == honest_log, name


def test_probe_manifest_closed_and_ordered():
    assert list(PROBES) == list(PROBE_MANIFEST)
    assert len(HOSTILE_KEY_PROBES) == 18
    assert set(PROBE_MANIFEST.values()) == {MWE, SC, DC, ACCEPT}


@pytest.mark.parametrize("name", list(PROBE_MANIFEST))
def test_wal_total_over_hostile_inputs(name):
    _run_probe(WalEngine, name)


def _chain_request():
    return copy.deepcopy(
        _bn(CASES, "happy", "append-put-put-delete-chain")["appends"][0])


def _run_append_onto_log(make, case):
    """append must validate the WHOLE existing log (wal.yaml append:
    request-then-full-log-validation, staged commit last): every corrupt
    replay-row log rejects an otherwise valid append with the row's
    pinned class, inputs bit- and reference-identical."""
    log = copy.deepcopy(case["log"])
    req = _chain_request()
    snap = copy.deepcopy((log, req))
    ids = _deep_ids(log, []) + _deep_ids(req, [])
    try:
        make(ORACLES["honest"]).append(log, req)
    except WalError as exc:
        assert exc.failure_class == case["expect_failure"], case["name"]
        assert exc.code == FAILURE_MAPPING[case["expect_failure"]]
    else:
        raise AssertionError(f"{case['name']}: append onto corrupt log")
    assert (log, req) == snap, case["name"]
    assert _deep_ids(log, []) + _deep_ids(req, []) == ids, case["name"]


def _replay_rows():
    return [r for r in CASES["malformed"] if r["kind"] == "replay"]


@pytest.mark.parametrize("case", _replay_rows(),
                         ids=[r["name"] for r in _replay_rows()])
def test_append_validates_the_existing_log(case):
    _run_append_onto_log(WalEngine, case)


def test_append_onto_log_rows_are_closed():
    assert [r["name"] for r in _replay_rows()] == [
        n for n, m in MALFORMED_MANIFEST.items() if m[0] == "replay"]
    assert len(_replay_rows()) == 5


# -- black-box engine mutants, each with a non-vacuous witness -------------
class _AcceptAll:
    """Never rejects: echoes a fabricated entry / empty replay."""

    def __init__(self, oracle):
        self._real = WalEngine(ORACLES["honest"])

    def append(self, log, request):
        try:
            return self._real.append(log, request)
        except WalError:
            return {"entry_id": GENESIS}

    def replay(self, log):
        try:
            return self._real.replay(log)
        except WalError:
            return {"state": {}, "state_id": "", "head": GENESIS,
                    "applied": 0}


class _PartialCommit(WalEngine):
    """Leaks the rejected request into the supplied log (append) or
    swaps an equal-but-foreign entry object into it (replay)."""

    def append(self, log, request):
        try:
            return super().append(log, request)
        except WalError:
            log.append(copy.deepcopy(request))
            raise

    def replay(self, log):
        try:
            return super().replay(log)
        except WalError:
            if log:
                log[-1] = copy.deepcopy(log[-1])
            raise


class _CollapsedClass(WalEngine):
    """Maps every rejection to malformed_wal_entry."""

    def append(self, log, request):
        try:
            return super().append(log, request)
        except WalError:
            _ref._fail(MWE)

    def replay(self, log):
        try:
            return super().replay(log)
        except WalError:
            _ref._fail(MWE)


class _StaleHead(WalEngine):
    """Replays the right state but reports the genesis head."""

    def replay(self, log):
        return dict(super().replay(log), head=GENESIS)


class _OverStrict(WalEngine):
    """Rejects every delete - the valid neighbours must catch this."""

    def append(self, log, request):
        if type(request) is dict and request.get("op") == "delete":
            _ref._fail(UO)
        return super().append(log, request)


class _AppendNoLogCheck(WalEngine):
    """Appends a fabricated entry even when the existing log is corrupt."""

    def append(self, log, request):
        try:
            return super().append(log, request)
        except WalError:
            if not log:
                raise
            entry = {"entry_id": "wal1:" + "0" * 64,
                     "sequence": len(log) + 1, "op": request["op"],
                     "payload": copy.deepcopy(request["payload"]),
                     "prior_entry_id": "wal1:" + "0" * 64}
            log.append(entry)
            return copy.deepcopy(entry)


class _MutReqOnSuccess(WalEngine):
    """Rewrites the caller's request after a successful append."""

    def append(self, log, request):
        receipt = super().append(log, request)
        request["payload"]["record"]["digest"] = "pdv1:" + "0" * 64
        return receipt


class _AliasPayload(WalEngine):
    """The committed entry shares the request's payload object."""

    def append(self, log, request):
        receipt = super().append(log, request)
        log[-1]["payload"] = request["payload"]
        return receipt


class _ReceiptAliasesLog(WalEngine):
    """Returns the committed log entry object itself as the receipt."""

    def append(self, log, request):
        super().append(log, request)
        return log[-1]


class _Guardless(WalEngine):
    """The pre-#201 WAL: the exact-str key guard disabled."""

    def _guardless(self, method, *args):
        saved = _ref._exact_str_keys
        _ref._exact_str_keys = lambda mapping: True
        try:
            return method(*args)
        finally:
            _ref._exact_str_keys = saved

    def append(self, log, request):
        return self._guardless(super().append, log, request)

    def replay(self, log):
        return self._guardless(super().replay, log)


_BATTERY = {"happy": _run_script, "boundary": _run_script,
            "malformed": _run_malformed, "rollback": _run_rollback,
            "malformed-append-onto-log": _run_append_onto_log,
            "totality": _run_probe}
# mutant -> (sections expected to catch it, minimum caught rows)
_ENGINE_MUTANTS = {
    "accept-all": (_AcceptAll, {"malformed": 16, "rollback": 2}),
    "partial-commit": (_PartialCommit, {"malformed": 16, "rollback": 2}),
    "collapsed-class": (_CollapsedClass, {"malformed": 8, "rollback": 2}),
    "stale-head": (_StaleHead, {"happy": 3, "boundary": 2}),
    "over-strict": (_OverStrict, {"happy": 2, "boundary": 2}),
    "append-no-log-check": (_AppendNoLogCheck,
                            {"malformed-append-onto-log": 5}),
    "mut-req-on-success": (_MutReqOnSuccess, {"happy": 3, "boundary": 2}),
    "alias-payload": (_AliasPayload, {"happy": 3, "boundary": 2}),
    "receipt-aliases-log": (_ReceiptAliasesLog,
                            {"happy": 3, "boundary": 2}),
    "guardless": (_Guardless, {"totality": 18}),
}


def _section_rows(section):
    if section == "malformed-append-onto-log":
        return _replay_rows()
    if section == "totality":
        return list(PROBE_MANIFEST)
    return CASES[section]


def _caught(make, section):
    n = 0
    for case in _section_rows(section):
        try:
            _BATTERY[section](make, copy.deepcopy(case))
        except (AssertionError, WalError, pytest.fail.Exception):
            n += 1
    return n


@pytest.mark.parametrize("mutant", list(_ENGINE_MUTANTS))
def test_battery_kills_engine_mutants(mutant):
    make, expected = _ENGINE_MUTANTS[mutant]
    for section, minimum in expected.items():
        assert _caught(make, section) >= minimum, (mutant, section)
    # witness: the reference engine passes the very same rows
    for section in expected:
        assert _caught(WalEngine, section) == 0, section


# -- substitution mutants: caught with AND without the digest table --------
def _bn(c, section, name):
    (row,) = [r for r in c[section] if r["name"] == name]
    return row


def _swap(c, section, a, b, *keys):
    x, y = _bn(c, section, a), _bn(c, section, b)
    for key in keys:
        x[key], y[key] = y[key], x[key]


def _regen_script(row):
    engine = WalEngine(ORACLES[row["oracle"]])
    log = []
    receipts = [engine.append(log, copy.deepcopy(r))
                for r in row["appends"]]
    row["expect"] = {"receipts": receipts,
                     "replay": WalEngine(ORACLES[row["oracle"]]).replay(log)}


def _happy_scripts_swapped_regenerated(c):
    _swap(c, "happy", "append-put-put-delete-chain",
          "append-put-delete-put", "appends")
    for name in ("append-put-put-delete-chain", "append-put-delete-put"):
        _regen_script(_bn(c, "happy", name))


def _boundary_one_way_copy_regenerated(c):
    donor = _bn(c, "happy", "append-single-put-after-e4")
    row = _bn(c, "boundary", "delete-missing-identity-noop")
    row["appends"] = copy.deepcopy(donor["appends"])
    _regen_script(row)


def _rollback_follow_up_is_rejected_input(c):
    row = _bn(c, "rollback",
              "rejected-replay-tampered-chain-then-valid-replay")
    row["then_log"] = copy.deepcopy(row["log"])


def _rollback_request_swapped_regenerated(c):
    row = _bn(c, "rollback",
              "rejected-append-raising-oracle-then-valid-append")
    row["request"] = copy.deepcopy(
        _bn(c, "happy", "append-single-put-after-e4")["appends"][0])
    row["expect"] = WalEngine(canonical_payload).append(
        copy.deepcopy(row["log"]), copy.deepcopy(row["request"]))


def _malformed_payload_copy(c, target, donor, *keys):
    src = _bn(c, "malformed", donor)
    for key in keys:
        _bn(c, "malformed", target)[key] = copy.deepcopy(src[key])


_CLOSURE_MUTANTS = {
    "swap-happy-names": lambda c: _swap(
        c, "happy", "append-put-put-delete-chain",
        "append-put-delete-put", "name"),
    "happy-scripts-swapped-regenerated": _happy_scripts_swapped_regenerated,
    "swap-boundary-names": lambda c: _swap(
        c, "boundary", "delete-missing-identity-noop",
        "delete-last-record-empty-state", "name"),
    "boundary-one-way-copy-regenerated": _boundary_one_way_copy_regenerated,
    "swap-grammar-tamper-logs": lambda c: _swap(
        c, "malformed", "log-entry-bad-id-grammar",
        "log-entry-id-tampered", "log", "minimal_repair"),
    "swap-gap-duplicate-logs": lambda c: _swap(
        c, "malformed", "log-sequence-gap", "log-sequence-duplicate",
        "log", "minimal_repair"),
    "swap-digest-identity-requests": lambda c: _swap(
        c, "malformed", "record-wrong-digest", "payload-identity-mismatch",
        "request", "minimal_repair"),
    "unregistered-op-copies-non-str-payload": lambda c:
        _malformed_payload_copy(c, "op-unregistered", "op-non-str",
                                "request"),
    "missing-record-copies-missing-op": lambda c: _malformed_payload_copy(
        c, "payload-missing-record", "request-missing-op-field",
        "request", "minimal_repair"),
    "raising-oracle-becomes-non-str": lambda c: _bn(
        c, "malformed", "oracle-raises").update(oracle="non-str"),
    "drop-sequence-gap": lambda c: c["malformed"].remove(
        _bn(c, "malformed", "log-sequence-gap")),
    "duplicate-oracle-raises": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "oracle-raises"))),
    "rollback-follow-up-is-rejected-input":
        _rollback_follow_up_is_rejected_input,
    "rollback-request-swapped-regenerated":
        _rollback_request_swapped_regenerated,
}


def _section_of(cases, row):
    for section in SECTIONS:
        if any(r is row for r in cases[section]):
            return section
    raise AssertionError("row not in fixture")


@pytest.mark.parametrize("mutant", list(_CLOSURE_MUTANTS))
@pytest.mark.parametrize("digests", [True, False],
                         ids=["with-digests", "closure-only"])
def test_closure_kills_substitution_mutants(mutant, digests, monkeypatch):
    """Every substitution is caught - and caught by the semantic closure
    ALONE, not only by the row digest table."""
    cases = copy.deepcopy(CASES)
    _CLOSURE_MUTANTS[mutant](cases)
    assert cases != CASES, mutant  # non-vacuous: the mutant changed data
    if not digests:
        monkeypatch.setattr(sys.modules[__name__], "_row_digest",
                            lambda row: ROW_DIGESTS.get(
                                f"{_section_of(cases, row)}:{row['name']}"))
    with pytest.raises((AssertionError, KeyError, ValueError)):
        _validate_closure(cases)


def test_closure_only_mode_still_accepts_clean_fixture(monkeypatch):
    """Witness: the digest bypass alone does not make closure fail."""
    monkeypatch.setattr(sys.modules[__name__], "_row_digest",
                        lambda row: ROW_DIGESTS.get(
                            f"{_section_of(CASES, row)}:{row['name']}"))
    _validate_closure(CASES)
