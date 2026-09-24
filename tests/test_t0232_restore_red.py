"""T0232 permanent red battery for store-restore engines.

The battery drives EVERY row of the T0231 conformance fixture
(tests/fixtures/restore/cases.json), a closed set of totality
probes and a pinned canonical-order probe through an engine class
constructed with a bundle parser:

- happy/boundary: the pinned restore record exactly (the restored
  state compared order-sensitively), deterministic by value over
  fresh copies (never the same object, no shared container),
  exactly ONE parser call with the receipt's own bundle, and the
  receipt untouched by value AND object identity (exact snapshot)
  with no container shared between the result and the input;
- malformed: the original receipt rejects with the pinned failure
  class and its mapped code, the receipt untouched, and the
  declared minimal repair is accepted with the record the
  reference derives for the repaired input;
- rollback: a rejection leaves the receipt untouched, then the
  valid follow-up (on the SAME engine instance when the parser is
  unchanged) returns the pinned record;
- totality: hostile receipts, receipt keys/values, dict
  subclasses, and hostile parser behavior (raising any
  BaseException, non-mapping output, hostile state keys, records,
  record keys and values, cyclic/deep/huge values, parsers that
  mutate the caller's receipt mid-call or keep references to what
  they return) each reject with the pinned failure class or, for
  accept-probes, return the pinned record - receipt untouched,
  verify-first (a receipt-level rejection never calls the parser),
  any other BaseException escaping is a failure.

Standalone-red convention (T0151, T0178, T0187, T0196): the
battery is permanently GREEN against the contract-derived
reference engine from tests.test_t0230_restore_contract and every
mutant below is RED. The production task switches the binding by
replacing ONLY the two binding lines below with the production
RestoreEngine / RestoreError names; no assertion changes.

Fixture closure: ordered per-section manifests, per-row semantic
pins and closed per-tag edge/defect-locus checks over the
ORIGINAL row data (an unknown tag raises), and a ROW_DIGESTS
whole-row sha256 table whose key set equals the manifests.
test_closure_kills_substitution_mutants proves substitution
mutants are killed with the digest table live AND with digests
neutralized."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0230_restore_contract as _reference  # noqa: E402
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    KINGS,
    STARTPOS,
)
from tests.test_t0194_migration_contract import state_id  # noqa: E402
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    _identity,
    _node,
)
from tests.test_t0221_backup_contract import (  # noqa: E402
    BackupEngine,
    BackupError,
    serialize_bundle,
)
from tests.test_t0230_restore_contract import parse_bundle  # noqa: E402
from tests.test_t0231_restore_fixture import (  # noqa: E402
    ORACLES,
    _assert_malformed_scenario,
    _repaired,
)
from tools.restore_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
)

# -- binding switch: the production task replaces ONLY these two
RestoreEngine = __import__("store.restore").restore.RestoreEngine
RestoreError = __import__("store.restore").restore.RestoreError

FIXTURE = (Path(__file__).parent / "fixtures" / "restore"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
RECEIPT_FIELDS = ("backup_id", "head", "state_id", "entry_count",
                  "bundle")
RESULT_FIELDS = ("restore_id", "backup_id", "state_id", "state")
MRR = "malformed_restore_record"
UB = "unverified_backup"
DP = "divergent_parse"
DS = "divergent_state"
E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_BOARDS = {"kings": KINGS.split(" ")[0],
           "start": STARTPOS.split(" ")[0],
           "e4": E4.split(" ")[0]}

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, edge tag, parser, pins) where pins =
# (receipt entry_count, |restored state|, head is genesis,
# black-to-move records)
HAPPY_MANIFEST = (
    ("restore-put-put-delete-chain", "put-put-delete-chain",
     "honest", (3, 1, False, 0)),
    ("restore-single-put-after-e4", "single-put-after-e4", "honest",
     (1, 1, False, 1)),
    ("restore-put-delete-put", "put-delete-put", "honest",
     (3, 1, False, 0)),
)
BOUNDARY_MANIFEST = (
    ("restore-empty-log-backup", "empty-log", "honest",
     (0, 0, True, 0)),
    ("restore-delete-missing-identity-noop", "delete-missing-noop",
     "honest", (1, 0, False, 0)),
    ("restore-delete-last-record-empty-state", "delete-last-empty",
     "honest", (2, 0, False, 0)),
)
# malformed: (name, defect-locus tag, failure class, parser,
# repair form)
MALFORMED_MANIFEST = (
    ("receipt-missing-field", "missing-receipt-field", MRR, "honest",
     "replace_receipt"),
    ("receipt-extra-field", "extra-receipt-field", MRR, "honest",
     "replace_receipt"),
    ("receipt-bad-backup-id-grammar", "bad-backup-id-grammar", MRR,
     "honest", "set_receipt_field"),
    ("receipt-non-int-entry-count", "non-int-entry-count", MRR,
     "honest", "set_receipt_field"),
    ("receipt-non-str-bundle", "non-str-bundle", MRR, "honest",
     "set_receipt_field"),
    ("receipt-unencodable-bundle", "unencodable-receipt-field", MRR,
     "honest", "set_receipt_field"),
    ("receipt-wrong-backup-id", "wrong-backup-id", UB, "honest",
     "set_receipt_field"),
    ("receipt-head-count-inconsistent", "genesis-head-nonzero-count",
     UB, "honest", "set_receipt_field"),
    ("oracle-raises", "oracle-raises", DP, "raising", "set_oracle"),
    ("oracle-non-dict-output", "oracle-non-dict-output", DP,
     "non-dict", "set_oracle"),
    ("bundle-reordered-records", "reordered-records", DP, "honest",
     "replace_receipt"),
    ("bundle-duplicate-identical-records",
     "duplicate-identical-records", DP, "honest", "replace_receipt"),
    ("bundle-trailing-blank-line", "trailing-blank-line", DP,
     "honest", "replace_receipt"),
    ("bundle-duplicate-field-conflicting",
     "duplicate-field-conflicting", DS, "honest", "replace_receipt"),
    ("oracle-wrong-content", "parser-wrong-content", DS,
     "wrong-content", "set_oracle"),
    ("oracle-key-mismatch", "parser-key-mismatch", DS,
     "key-mismatch", "set_oracle"),
    ("oracle-divergent-state", "parser-state-divergence", DS,
     "divergent-state", "set_oracle"),
)
# rollback: (name, rejection tag, failure class, parser, follow-up
# parser)
ROLLBACK_MANIFEST = (
    ("rejected-restore-raising-parser-then-valid-restore",
     "raising-parser", DP, "raising", "honest"),
    ("rejected-restore-unverified-receipt-then-valid-restore",
     "unverified-receipt", UB, "honest", "honest"),
    ("rejected-restore-divergent-state-then-valid-restore",
     "divergent-state-parser", DS, "divergent-state", "honest"),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:restore-put-put-delete-chain":
        "cd159b122cf7de4f4f2bbf5bffc104a4dbabd526da6e8c404ef672050a8c1e7c",
    "happy:restore-single-put-after-e4":
        "e6ccdcc8473b57928aa5e562b6d1ba1e32d7d1dfc03445a4c96ba3bd25b5f314",
    "happy:restore-put-delete-put":
        "eda55e62796cf04ad378a8bc52f8f5ad97117b7bddbc623d10c0cfb29f446365",
    "boundary:restore-empty-log-backup":
        "df52c57073132303995493db696d9a34fdb53970490f44e1b59ae6497f55c384",
    "boundary:restore-delete-missing-identity-noop":
        "965020072ebe4aa13faf91ad04737d38df6db37a14e6dc9e0b1bbf54cc1b9cac",
    "boundary:restore-delete-last-record-empty-state":
        "906204a9589bb72a1ff502fcab6104bb78b3814188b2e48c2fe3753390d415e3",
    "malformed:receipt-missing-field":
        "383286be63b78b246dcc3647cf1532130cdb89cc42bdbc16e22cfe6645c69452",
    "malformed:receipt-extra-field":
        "79f08bfe2578aafaacd9ec0471c401f577ef7fe7b2baca69795e773cc37885ad",
    "malformed:receipt-bad-backup-id-grammar":
        "6d2e84c7407444dcd542fa3553767f47a65ec9e99874eaf441b589a6527c211e",
    "malformed:receipt-non-int-entry-count":
        "a2e0b9f5a088938c30d4898a842d3b012c03bc8cb43e45a19cb7dc908176d03c",
    "malformed:receipt-non-str-bundle":
        "1d74447c6f41b3fbd081d8d4a61539cfcea46f5173a1e8b645399eb8194f5f26",
    "malformed:receipt-unencodable-bundle":
        "fadfca8605d97f3fa689264445fef1caa08c9ace6075bcd65c3c3c2bdc59514c",
    "malformed:receipt-wrong-backup-id":
        "4df71c38b1d43f89337669baac591e15970d6546489b4424bc28442330d9bec6",
    "malformed:receipt-head-count-inconsistent":
        "a9453f93ac4158c546188bf8e0df0666e053444dcb7fdbbb922bbf5b708dae71",
    "malformed:oracle-raises":
        "d3743139d6008af3f3de5ff79755acf75ec79d9e02a1f8cfefb61ac88a8ad180",
    "malformed:oracle-non-dict-output":
        "015f64cc173b9834880a3fda6b847f3494ea6b7dfc44663aea8a2db983d11b3c",
    "malformed:bundle-reordered-records":
        "7ae3a9df493d3300f3b7cc0f5f3da59e24b13108711f3f71e4e8c77b894fd30b",
    "malformed:bundle-duplicate-identical-records":
        "b2c991177f3d75f21987e7de4b2f862958f52fbcba9770d1d73564d12b736c8d",
    "malformed:bundle-trailing-blank-line":
        "b6a5b4ace99a3d300e5c70b1fd226eaf6fd1af66af992de18ffb8de277cc66c9",
    "malformed:bundle-duplicate-field-conflicting":
        "8de655a5599c736cf60a33452dd9efa6c69cb72416df1412bb78652a2dc30afc",
    "malformed:oracle-wrong-content":
        "b83634f2daa78bc64fab016cf9eaf16f98af166d522f5e321791905a987e3e59",
    "malformed:oracle-key-mismatch":
        "6595f33fbfeb47a85f73c4eda759be1fef76c6037e79d8739340cc52a1a91453",
    "malformed:oracle-divergent-state":
        "25b9c9318b1ed305e6fbe8ed1332fb43d503198510075f5832138ac5b0d0142e",
    "rollback:rejected-restore-raising-parser-then-valid-restore":
        "488817298480df71722699c6bf693df788d30848e37f2e9c6052c6d50c7c4f5e",
    "rollback:rejected-restore-unverified-receipt-then-valid-restore":
        "7d53036f35ff96e91598f2a020791ec2e0c038018444d96b28e82c29beacfb1c",
    "rollback:rejected-restore-divergent-state-then-valid-restore":
        "9ac362265c767c359fbe8f32f038ba04e9e1f021e503a0268c5d70c2b0d3c357",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _section_of(row, cases=None):
    for section, rows in (cases or CASES).items():
        if isinstance(rows, list) and any(r is row for r in rows):
            return section
    for section, manifest in MANIFESTS.items():
        if row["name"] in {m[0] for m in manifest}:
            return section
    raise AssertionError(f"row {row.get('name')!r} has no section")


# -- per-tag semantic checks over ORIGINAL row data ---------------------------
def _verifies(receipt):
    try:
        BackupEngine(serialize_bundle).verify(copy.deepcopy(receipt))
    except BackupError:
        return False
    return True


def _restore_id(backup_id, sid):
    return "rst1:" + hashlib.sha256(
        f"{backup_id}\n{sid}".encode()).hexdigest()


def _reference_restore(parser_name, receipt):
    return _reference.RestoreEngine(ORACLES[parser_name]).restore(
        copy.deepcopy(receipt))


def _reference_failure(parser_name, receipt):
    try:
        _reference_restore(parser_name, receipt)
    except _reference.RestoreError as error:
        return error.failure_class
    return None


def _check_result(name, receipt, expect):
    """The pinned record realizes the contract over the ORIGINAL
    receipt: ids carried/derived, the state is exactly the
    canonical decoding of the bundle, canonical key order."""
    assert type(receipt) is dict and \
        set(receipt) == set(RECEIPT_FIELDS), name
    assert _verifies(receipt), name
    assert set(expect) == set(RESULT_FIELDS), name
    state = expect["state"]
    assert expect["backup_id"] == receipt["backup_id"], name
    assert expect["state_id"] == receipt["state_id"] == \
        state_id(state), name
    assert expect["restore_id"] == _restore_id(
        receipt["backup_id"], receipt["state_id"]), name
    assert list(state) == sorted(state), name
    assert serialize_bundle(state) == receipt["bundle"], name
    for key, rec in state.items():
        assert list(rec) == sorted(RECORD_FIELDS) or \
            set(rec) == set(RECORD_FIELDS), name
        assert set(rec) == set(RECORD_FIELDS), name
        assert _identity(rec) == key, name
        assert rec == _node(rec["snapshot_fen"]), name


def _pins(case):
    receipt, state = case["receipt"], case["expect"]["state"]
    return (receipt["entry_count"], len(state),
            receipt["head"] == GENESIS,
            sum(rec["snapshot_fen"].split(" ")[1] == "b"
                for rec in state.values()))


def _boards(state):
    return sorted(rec["snapshot_fen"].split(" ")[0]
                  for rec in state.values())


def _check_edge(case, tag, oracle, pins):
    """One closed branch per happy/boundary edge tag; an unknown
    tag raises."""
    name = case["name"]
    receipt, state = case["receipt"], case["expect"]["state"]
    assert case["oracle"] == oracle, name
    _check_result(name, receipt, case["expect"])
    assert _pins(case) == pins, name
    if tag == "put-put-delete-chain":
        assert _boards(state) == [_BOARDS["kings"]], name
    elif tag == "single-put-after-e4":
        assert _boards(state) == [_BOARDS["e4"]], name
    elif tag == "put-delete-put":
        assert _boards(state) == [_BOARDS["start"]], name
    elif tag == "empty-log":
        assert receipt["bundle"] == "" and state == {}, name
        assert receipt["head"] == GENESIS, name
    elif tag in ("delete-missing-noop", "delete-last-empty"):
        # a non-empty log whose replay leaves nothing: count 1 is
        # the lone no-op delete, count 2 a put then its delete
        assert receipt["bundle"] == "" and state == {}, name
        assert receipt["head"] != GENESIS, name
        assert receipt["entry_count"] == \
            {"delete-missing-noop": 1, "delete-last-empty": 2}[tag], \
            name
    else:
        raise AssertionError(f"unknown edge tag {tag!r}")


def _check_rollback(case, tag, failure, oracle, then_oracle):
    name = case["name"]
    assert case["expect_failure"] == failure, name
    assert case["oracle"] == oracle, name
    assert case["then_oracle"] == then_oracle, name
    _check_result(name, case["then_receipt"], case["expect"])
    assert _reference_failure(oracle, case["receipt"]) == failure, name
    assert _reference_restore(then_oracle, case["then_receipt"]) == \
        case["expect"], name
    receipt, then = case["receipt"], case["then_receipt"]
    if tag == "raising-parser":
        assert receipt == then, name
        try:
            ORACLES[oracle](receipt["bundle"])
        except Exception:  # noqa: BLE001
            pass
        else:
            raise AssertionError(name)
    elif tag == "unverified-receipt":
        assert set(receipt) == set(then), name
        assert {k for k in receipt if receipt[k] != then[k]} == \
            {"backup_id"}, name
        assert not _verifies(receipt) and _verifies(then), name
    elif tag == "divergent-state-parser":
        assert receipt == then, name
        parsed = ORACLES[oracle](receipt["bundle"])
        assert type(parsed) is dict, name
        assert state_id(parsed) != receipt["state_id"], name
        assert len(then["bundle"].split("\n")) - 1 == \
            2 * len(case["expect"]["state"]) >= 4, name
    else:
        raise AssertionError(f"unknown rollback tag {tag!r}")


def _row(section, name, cases=None):
    for row in (cases or CASES)[section]:
        if row["name"] == name:
            return row


def _check_row(section, case, meta):
    if section in ("happy", "boundary"):
        _, tag, oracle, pins = meta
        _check_edge(case, tag, oracle, pins)
    elif section == "malformed":
        _, tag, failure, oracle, form = meta
        assert case["expect_failure"] == failure, case["name"]
        assert case["oracle"] == oracle, case["name"]
        assert list(case["minimal_repair"]) == [form], case["name"]
        assert case["scenario"] == tag, case["name"]
        _assert_malformed_scenario(case, tag)
        assert _reference_failure(oracle, case["receipt"]) == \
            failure, case["name"]
    else:
        _, tag, failure, oracle, then_oracle = meta
        _check_rollback(case, tag, failure, oracle, then_oracle)


def _validate_closure(cases):
    """Ordered names, whole-row digests and per-tag semantic
    checks for every executed section; the digest table's key set
    equals the manifests."""
    assert set(ROW_DIGESTS) == {
        f"{s}:{m[0]}" for s, man in MANIFESTS.items() for m in man}
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == \
            [m[0] for m in manifest], section
        for row, meta in zip(rows, manifest, strict=True):
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS[label] == _row_digest(row), label
            try:
                _check_row(section, row, meta)
            except AssertionError:
                raise
            except Exception as error:  # noqa: BLE001
                raise AssertionError(
                    f"{label}: {type(error).__name__}") from None


# -- exact snapshot / aliasing -------------------------------------------------
class _KeyMark:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key


def _snap(obj):
    """Exact flat snapshot: type, id, length and key order of every
    container, key identity (text for exact str keys, id otherwise),
    scalar type+value (huge ints by bit length and low bits).
    Iterative, cycle-safe, never hashes, compares or reprs a
    caller-owned key or subclass instance."""
    out, seen, stack = [], set(), [obj]
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is dict or kind is list or kind is tuple:
            if id(node) in seen:
                out.append(("seen", id(node)))
                continue
            seen.add(id(node))
            if kind is dict:
                items = list(dict.items(node))
                out.append(("dict", id(node), len(items)))
                for key, value in reversed(items):
                    stack.append(value)
                    stack.append(_KeyMark(key))
            else:
                values = list(kind.__iter__(node))
                out.append((kind.__name__, id(node), len(values)))
                stack.extend(reversed(values))
        elif kind is _KeyMark:
            key = node.key
            out.append(("key", key if type(key) is str
                        else (type(key).__name__, id(key))))
        elif kind is int:
            out.append(("int", id(node), node.bit_length(),
                        node & 0xFFFF))
        elif kind in (str, bool, float, type(None)):
            out.append((kind.__name__, node))
        else:
            out.append(("object", kind.__name__, id(node)))
    return out


def _not_aliased(result, inputs):
    if not isinstance(result, dict):
        return True
    inner = {e[1] for e in _snap(inputs)
             if e[0] in ("dict", "list", "tuple")}
    return not any(e[0] in ("dict", "list", "tuple") and e[1] in inner
                   for e in _snap(result))


def _same_ordered(result, expect):
    """Order-sensitive receipt equality: the migrated state is also
    compared as an ordered item list."""
    return (result == expect and isinstance(result, dict)
            and isinstance(result.get("state"), dict)
            and list(result["state"].items())
            == list(expect["state"].items()))




def _same_ordered(result, expect):
    """Order-sensitive record equality: the restored state is also
    compared as an ordered item list."""
    return (type(result) is dict and result == expect
            and type(result.get("state")) is dict
            and list(result["state"].items())
            == list(expect["state"].items()))


class _Counting:
    """Wraps a parser and records every bundle it is called with."""

    def __init__(self, parser):
        self.parser, self.calls = parser, []

    def __call__(self, bundle):
        self.calls.append(bundle)
        return self.parser(bundle)


def _engine(cls, parser):
    counter = _Counting(ORACLES[parser] if isinstance(parser, str)
                        else parser)
    return cls(counter), counter


def _checked_restore(engine, counter, receipt, expect):
    """One successful restore: pinned record order-sensitively,
    receipt untouched exactly, no aliasing, exactly one parser
    call with the receipt's own bundle."""
    inputs = (receipt,)
    snap = _snap(inputs)
    bundle = receipt["bundle"]
    n_before = len(counter.calls)
    result = engine.restore(receipt)
    ok = (_snap(inputs) == snap and _not_aliased(result, inputs)
          and counter.calls[n_before:] == [bundle]
          and _same_ordered(result, expect))
    return result, ok


def _restore_ok(cls, case, prefix="", engine=None):
    """Pinned record, determinism BY VALUE (a repeat never returns
    the same object or shares a container), one parser call,
    receipt untouched, no aliasing."""
    parser = case[prefix + "oracle"]
    if engine is None:
        engine = _engine(cls, parser)
    results = []
    for eng, counter in (engine, _engine(cls, parser)):
        result, ok = _checked_restore(
            eng, counter, copy.deepcopy(case[prefix + "receipt"]),
            case["expect"])
        if not ok:
            return False
        results.append(result)
    return (results[1] is not results[0]
            and _not_aliased(results[1], (results[0],)))


def _rejects(engine, counter, receipt, failure):
    inputs = (receipt,)
    snap = _snap(inputs)
    n_before = len(counter.calls)
    try:
        engine.restore(receipt)
    except RestoreError as error:
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and error.code in ERROR_ENUM
                and _snap(inputs) == snap
                and len(counter.calls) - n_before <= 1)
    return False


def _malformed_ok(cls, case):
    eng, counter = _engine(cls, case["oracle"])
    if not _rejects(eng, counter, copy.deepcopy(case["receipt"]),
                    case["expect_failure"]):
        return False
    fixed = _repaired(case)
    expect = _reference_restore(fixed["oracle"], fixed["receipt"])
    eng, counter = _engine(cls, fixed["oracle"])
    _, ok = _checked_restore(eng, counter,
                             copy.deepcopy(fixed["receipt"]), expect)
    return ok


def _rollback_ok(cls, case):
    engine = _engine(cls, case["oracle"])
    if not _rejects(*engine, copy.deepcopy(case["receipt"]),
                    case["expect_failure"]):
        return False
    same = case["then_oracle"] == case["oracle"]
    return _restore_ok(cls, case, "then_", engine if same else None)


_RUNNERS = {"happy": _restore_ok, "boundary": _restore_ok,
            "malformed": _malformed_ok, "rollback": _rollback_ok}


# -- pinned canonical-order probe ----------------------------------------------
def _order_receipt():
    """Three records put in NON-canonical order: e4, start,
    kings."""
    return _reference._receipt(("put", E4), ("put", STARTPOS),
                               ("put", KINGS))


ORDER_EXPECT = {
    "restore_id":
        "rst1:f7acdff86965f5b84bb17bb1034b4ac0ff6d4fcd2d25c4746d166d57a5e6a2f4",
    "backup_id":
        "bck1:2d2ee4599b916e78e937662ff7d0203f2a8f4378106fdbbb0b9f7ded13277274",
    "state_id":
        "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
    "state": {
        "('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')": {
            "digest":
                "pdv1:5a52f52530c2a06135595264d05d0e93dc422ee8fb28818eef7d85a706bb949b",
            "snapshot_fen":
                "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
            "variant":
                "standard",
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR', 'b', 'KQkq', '-')": {
            "digest":
                "pdv1:736a6cefc8595ac6eafae7ee667e6490abaa30e2a0969607aee8dce93c2381ff",
            "snapshot_fen":
                "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            "variant":
                "standard",
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', 'KQkq', '-')": {
            "digest":
                "pdv1:66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2cae32a8b88ffe",
            "snapshot_fen":
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "variant":
                "standard",
        },
    },
}
ORDER_LABEL = "probe:canonical-order"


def _order_ok(cls):
    engine, counter = _engine(cls, "honest")
    _, ok = _checked_restore(engine, counter, _order_receipt(),
                             ORDER_EXPECT)
    return ok


# -- closed totality probes ----------------------------------------------------


class SK(str):
    """str-subclass key/value whose comparisons raise."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile str __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile str __ne__")


class HK:
    """Non-str key whose hash collides with a real key and whose
    __eq__ raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


class _DictSub(dict):
    """dict subclass whose accessors raise."""

    def items(self):
        raise RuntimeError("hostile items")

    def keys(self):
        raise RuntimeError("hostile keys")

    def __getitem__(self, key):
        raise RuntimeError("hostile __getitem__")


def _self_ref():
    loop = []
    loop.append(loop)
    return loop


def _deep():
    node = []
    for _ in range(100_000):
        node = [node]
    return node




def _probe_receipt():
    """A valid two-record backup receipt; every probe corrupts one
    component (or attacks through the parser)."""
    return _reference._receipt(("put", KINGS), ("put", STARTPOS))


def _rekey(mapping, key, new_key):
    return {new_key if k == key else k: v for k, v in mapping.items()}


_HOSTILE_CONTAINERS = (("none", None), ("true", True), ("zero", 0),
                       ("float", 1.5), ("text", "text"), ("list", []))
_HOSTILE_VALUES = (("none", None), ("true", True), ("float", 1.5),
                   ("list", []), ("dict", {}))
_FORGED_SID = "gs1:" + "f" * 64


def _rcpt(fn):
    """Receipt-level probe: honest parser, corrupted receipt."""
    def build():
        return fn(_probe_receipt()), parse_bundle, None
    return build


def _parsed(fn):
    """Parser-output probe: valid receipt, the honest parse is
    corrupted by FN before it is returned."""
    def build():
        return (_probe_receipt(),
                lambda bundle: fn(parse_bundle(bundle)), None)
    return build


def _parser(parser):
    def build():
        return _probe_receipt(), parser, None
    return build


def _target(state):
    return sorted(state)[-1]


def _with_rec(state, fn):
    out = dict(state)
    out[_target(state)] = fn(dict(state[_target(state)]))
    return out


def _raises(exc):
    def parser(bundle):
        raise exc
    return parser


_HOSTILE_PARSERS = {
    "raises-value-error": _raises(ValueError("x")),
    "raises-runtime-error": _raises(RuntimeError("x")),
    "raises-keyboard-interrupt": _raises(KeyboardInterrupt()),
    "raises-system-exit": _raises(SystemExit(1)),
    "raises-generator-exit": _raises(GeneratorExit()),
    "returns-none": lambda b: None,
    "returns-int": lambda b: 7,
    "returns-list": lambda b: [],
    "returns-text": lambda b: b,
    "returns-bytes": lambda b: b.encode(),
    "returns-dict-subclass": lambda b: _DictSub(parse_bundle(b)),
}

# Accept-probes: the parser attacks the caller's OWN receipt on
# its call (built-in dict methods only), then returns the honest
# parse of the bundle it was handed. The restore must still return
# exactly PROBE_EXPECT (order-sensitive) and leave the ORIGINAL
# receipt object _snap-identical.
ACCEPT = None
_LIVE_ATTACKS = ("mutates-receipt-bundle", "mutates-receipt-state-id",
                 "clears-receipt", "reorders-receipt",
                 "adds-receipt-key")


def _attack(kind, receipt):
    if kind in ("mutates-receipt-bundle", "mutates-then-raises"):
        dict.__setitem__(receipt, "bundle", "x")
    if kind in ("mutates-receipt-state-id", "mutates-then-raises"):
        dict.__setitem__(receipt, "state_id", _FORGED_SID)
    if kind == "clears-receipt":
        dict.clear(receipt)
    if kind == "reorders-receipt":
        items = sorted(dict.items(receipt), reverse=True)
        dict.clear(receipt)
        dict.update(receipt, items)
    if kind in ("adds-receipt-key", "mutates-then-raises"):
        dict.__setitem__(receipt, "restored", True)


def _live(kind):
    def build():
        receipt = _probe_receipt()

        def parser(bundle):
            _attack(kind, receipt)
            if kind == "mutates-then-raises":
                raise ValueError("mutate then explode")
            return parse_bundle(bundle)
        return receipt, parser, None
    return build


def _holding():
    """Accept-probe: the parser keeps every container it returns;
    the result must share none of them."""
    def build():
        held = []

        def parser(bundle):
            out = parse_bundle(bundle)
            held.append(out)
            return out
        return _probe_receipt(), parser, held
    return build


def _probe_builders():
    """name -> (builder returning (receipt, parser, held), pinned
    failure class or ACCEPT)."""
    out = {}
    for label, value in _HOSTILE_CONTAINERS:
        out[f"receipt-{label}"] = (
            _rcpt(lambda r, v=value: copy.deepcopy(v)), MRR)
    out["receipt-empty"] = (_rcpt(lambda r: {}), MRR)
    out["receipt-dict-subclass"] = (_rcpt(_DictSub), MRR)
    out["receipt-key-none"] = (_rcpt(lambda r: {**r, None: "x"}), MRR)
    out["receipt-extra-key"] = (_rcpt(lambda r: {**r, "state": {}}),
                                MRR)
    for field in RECEIPT_FIELDS:
        out[f"receipt-missing-{field}"] = (
            _rcpt(lambda r, f=field: {k: v for k, v in r.items()
                                      if k != f}), MRR)
        for label, cls in (("str-subclass", SK),
                           ("colliding-hash", HK)):
            out[f"receipt-key-{field}-{label}"] = (
                _rcpt(lambda r, f=field, c=cls: _rekey(r, f, c(f))),
                MRR)
        for label, value in _HOSTILE_VALUES:
            out[f"receipt-value-{field}-{label}"] = (
                _rcpt(lambda r, f=field, v=value: dict(
                    r, **{f: copy.deepcopy(v)})), MRR)
        if field == "entry_count":
            out["receipt-value-entry_count-huge-int"] = (
                _rcpt(lambda r: dict(r, entry_count=10 ** 5000)), MRR)
            out["receipt-value-entry_count-negative"] = (
                _rcpt(lambda r: dict(r, entry_count=-1)), MRR)
        else:
            out[f"receipt-value-{field}-str-subclass"] = (
                _rcpt(lambda r, f=field: dict(r, **{f: SK(r[f])})),
                MRR)
    out["receipt-value-bundle-unencodable"] = (
        _rcpt(lambda r: dict(r, bundle=r["bundle"] + "\ud800")), MRR)
    out["receipt-value-bundle-self-referential"] = (
        _rcpt(lambda r: dict(r, bundle=_self_ref())), MRR)
    for label, parser in _HOSTILE_PARSERS.items():
        out[f"parser-{label}"] = (_parser(parser), DP)
    for label, value in (("none", None), ("zero", 0)):
        out[f"parser-state-key-{label}"] = (
            _parsed(lambda s, v=value: _rekey(s, _target(s), v)), DS)
    for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
        out[f"parser-state-key-{label}"] = (
            _parsed(lambda s, c=cls: _rekey(s, _target(s),
                                            c(_target(s)))), DS)
    for label, value in (("none", None), ("zero", 0), ("list", []),
                         ("text", "text")):
        out[f"parser-record-{label}"] = (
            _parsed(lambda s, v=value: {
                **s, _target(s): copy.deepcopy(v)}), DS)
    out["parser-record-dict-subclass"] = (
        _parsed(lambda s: {**s, _target(s): _DictSub(s[_target(s)])}),
        DS)
    for field in RECORD_FIELDS:
        out[f"parser-record-missing-{field}"] = (
            _parsed(lambda s, f=field: _with_rec(
                s, lambda r: {k: v for k, v in r.items() if k != f})),
            DS)
        for label, cls in (("str-subclass", SK),
                           ("colliding-hash", HK)):
            out[f"parser-record-key-{field}-{label}"] = (
                _parsed(lambda s, f=field, c=cls: _with_rec(
                    s, lambda r: _rekey(r, f, c(f)))), DS)
        for label, value in (("none", None), ("zero", 0),
                             ("list", []), ("empty", "")):
            out[f"parser-record-value-{field}-{label}"] = (
                _parsed(lambda s, f=field, v=value: _with_rec(
                    s, lambda r: {**r, f: copy.deepcopy(v)})), DS)
        out[f"parser-record-value-{field}-str-subclass"] = (
            _parsed(lambda s, f=field: _with_rec(
                s, lambda r: {**r, f: SK(r[f])})), DS)
    out["parser-record-extra-field"] = (
        _parsed(lambda s: _with_rec(s, lambda r: {**r, "label": "x"})),
        DS)
    out["parser-record-value-huge-int"] = (
        _parsed(lambda s: _with_rec(
            s, lambda r: {**r, "digest": 10 ** 5000})), DS)
    out["parser-record-value-self-referential"] = (
        _parsed(lambda s: _with_rec(
            s, lambda r: {**r, "digest": _self_ref()})), DS)
    out["parser-record-value-deep-nested"] = (
        _parsed(lambda s: _with_rec(
            s, lambda r: {**r, "snapshot_fen": _deep()})), DS)
    out["parser-empty-state"] = (_parsed(lambda s: {}), DS)
    out["parser-drops-record"] = (
        _parsed(lambda s: {k: v for k, v in s.items()
                           if k != _target(s)}), DS)
    out["parser-extra-record"] = (
        _parsed(lambda s: {**s, _identity(_node(E4)): _node(E4)}), DS)
    out["parser-swapped-records"] = (
        _parsed(lambda s: dict(zip(s, reversed(list(s.values())), strict=True))),
        DS)
    for kind in _LIVE_ATTACKS:
        out[f"parser-{kind}"] = (_live(kind), ACCEPT)
    out["parser-mutates-then-raises"] = (
        _live("mutates-then-raises"), DP)
    out["parser-holds-returned-state"] = (_holding(), ACCEPT)
    return out


# Honest reference record for _probe_receipt(): every accept-probe
# must return exactly this, order-sensitively.
PROBE_EXPECT = {
    "restore_id":
        "rst1:997d7620206b9bdfa9223e69856ac3e4d83363e83250b1cbb2f5dc8be799da82",
    "backup_id":
        "bck1:82d4de6955c7c0976745f33aa5bc776170df0b57ef42ca288925d5b8e69ce218",
    "state_id":
        "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
    "state": {
        "('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')": {
            "digest":
                "pdv1:5a52f52530c2a06135595264d05d0e93dc422ee8fb28818eef7d85a706bb949b",
            "snapshot_fen":
                "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
            "variant":
                "standard",
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', 'KQkq', '-')": {
            "digest":
                "pdv1:66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2cae32a8b88ffe",
            "snapshot_fen":
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "variant":
                "standard",
        },
    },
}
PROBES = _probe_builders()
PROBE_MANIFEST = (
    "receipt-none",
    "receipt-true",
    "receipt-zero",
    "receipt-float",
    "receipt-text",
    "receipt-list",
    "receipt-empty",
    "receipt-dict-subclass",
    "receipt-key-none",
    "receipt-extra-key",
    "receipt-missing-backup_id",
    "receipt-key-backup_id-str-subclass",
    "receipt-key-backup_id-colliding-hash",
    "receipt-value-backup_id-none",
    "receipt-value-backup_id-true",
    "receipt-value-backup_id-float",
    "receipt-value-backup_id-list",
    "receipt-value-backup_id-dict",
    "receipt-value-backup_id-str-subclass",
    "receipt-missing-head",
    "receipt-key-head-str-subclass",
    "receipt-key-head-colliding-hash",
    "receipt-value-head-none",
    "receipt-value-head-true",
    "receipt-value-head-float",
    "receipt-value-head-list",
    "receipt-value-head-dict",
    "receipt-value-head-str-subclass",
    "receipt-missing-state_id",
    "receipt-key-state_id-str-subclass",
    "receipt-key-state_id-colliding-hash",
    "receipt-value-state_id-none",
    "receipt-value-state_id-true",
    "receipt-value-state_id-float",
    "receipt-value-state_id-list",
    "receipt-value-state_id-dict",
    "receipt-value-state_id-str-subclass",
    "receipt-missing-entry_count",
    "receipt-key-entry_count-str-subclass",
    "receipt-key-entry_count-colliding-hash",
    "receipt-value-entry_count-none",
    "receipt-value-entry_count-true",
    "receipt-value-entry_count-float",
    "receipt-value-entry_count-list",
    "receipt-value-entry_count-dict",
    "receipt-value-entry_count-huge-int",
    "receipt-value-entry_count-negative",
    "receipt-missing-bundle",
    "receipt-key-bundle-str-subclass",
    "receipt-key-bundle-colliding-hash",
    "receipt-value-bundle-none",
    "receipt-value-bundle-true",
    "receipt-value-bundle-float",
    "receipt-value-bundle-list",
    "receipt-value-bundle-dict",
    "receipt-value-bundle-str-subclass",
    "receipt-value-bundle-unencodable",
    "receipt-value-bundle-self-referential",
    "parser-raises-value-error",
    "parser-raises-runtime-error",
    "parser-raises-keyboard-interrupt",
    "parser-raises-system-exit",
    "parser-raises-generator-exit",
    "parser-returns-none",
    "parser-returns-int",
    "parser-returns-list",
    "parser-returns-text",
    "parser-returns-bytes",
    "parser-returns-dict-subclass",
    "parser-state-key-none",
    "parser-state-key-zero",
    "parser-state-key-str-subclass",
    "parser-state-key-colliding-hash",
    "parser-record-none",
    "parser-record-zero",
    "parser-record-list",
    "parser-record-text",
    "parser-record-dict-subclass",
    "parser-record-missing-variant",
    "parser-record-key-variant-str-subclass",
    "parser-record-key-variant-colliding-hash",
    "parser-record-value-variant-none",
    "parser-record-value-variant-zero",
    "parser-record-value-variant-list",
    "parser-record-value-variant-empty",
    "parser-record-value-variant-str-subclass",
    "parser-record-missing-digest",
    "parser-record-key-digest-str-subclass",
    "parser-record-key-digest-colliding-hash",
    "parser-record-value-digest-none",
    "parser-record-value-digest-zero",
    "parser-record-value-digest-list",
    "parser-record-value-digest-empty",
    "parser-record-value-digest-str-subclass",
    "parser-record-missing-snapshot_fen",
    "parser-record-key-snapshot_fen-str-subclass",
    "parser-record-key-snapshot_fen-colliding-hash",
    "parser-record-value-snapshot_fen-none",
    "parser-record-value-snapshot_fen-zero",
    "parser-record-value-snapshot_fen-list",
    "parser-record-value-snapshot_fen-empty",
    "parser-record-value-snapshot_fen-str-subclass",
    "parser-record-extra-field",
    "parser-record-value-huge-int",
    "parser-record-value-self-referential",
    "parser-record-value-deep-nested",
    "parser-empty-state",
    "parser-drops-record",
    "parser-extra-record",
    "parser-swapped-records",
    "parser-mutates-receipt-bundle",
    "parser-mutates-receipt-state-id",
    "parser-clears-receipt",
    "parser-reorders-receipt",
    "parser-adds-receipt-key",
    "parser-mutates-then-raises",
    "parser-holds-returned-state",
)
PROBE_COUNT = 117


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    receipt, parser, held = build()
    engine, counter = _engine(cls, parser)
    inputs = (receipt,)
    snap = _snap(inputs)
    # verify-first: a receipt-level rejection never reaches the
    # parser; every other probe reaches it exactly once
    calls = 0 if name.startswith("receipt-") else 1
    if failure is ACCEPT:
        try:
            result = engine.restore(receipt)
        except BaseException:  # noqa: BLE001 - any rejection fails
            return False
        return (_snap(inputs) == snap and len(counter.calls) == 1
                and _same_ordered(result, PROBE_EXPECT)
                and _not_aliased(result, (receipt, held or [])))
    try:
        engine.restore(receipt)
    except RestoreError as error:
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and error.code in ERROR_ENUM
                and _snap(inputs) == snap
                and len(counter.calls) == calls)
    except BaseException:  # noqa: BLE001 - a raw escape is the defect
        return False
    return False


def _probe(cls, executed=None, first_only=False, only=None):
    """Every manifest row, every totality probe and the order probe
    through CLS; returns failing labels (only the first when
    FIRST_ONLY). Untrusted-component boundary: BaseException is
    caught."""
    failures = []
    jobs = []
    for section, manifest in MANIFESTS.items():
        rows = {r["name"]: r for r in CASES[section]}
        for meta in manifest:
            jobs.append((f"{section}:{meta[0]}",
                         lambda s=section, r=rows[meta[0]]:
                         _RUNNERS[s](cls, r)))
    for name in PROBE_MANIFEST:
        jobs.append((f"totality:{name}",
                     lambda n=name: _totality_ok(cls, n)))
    jobs.append((ORDER_LABEL, lambda: _order_ok(cls)))
    for label, job in jobs:
        if only is not None and label not in only:
            continue
        try:
            ok = job()
        except BaseException as error:  # noqa: BLE001
            failures.append(f"{label}:{type(error).__name__}")
        else:
            if executed is not None:
                executed.append(label)
            if not ok:
                failures.append(label)
        if first_only and failures:
            return failures
    return failures


# -- mutants -------------------------------------------------------------------
def _fake_result(receipt):
    return {"restore_id": "rst1:" + "0" * 64, "backup_id":
            "bck1:" + "0" * 64, "state_id": "gs1:" + "0" * 64,
            "state": {}}


class AcceptsAll(RestoreEngine):
    def restore(self, receipt):
        try:
            return super().restore(receipt)
        except RestoreError:
            return _fake_result(receipt)


class WrongCode(RestoreEngine):
    def restore(self, receipt):
        try:
            return super().restore(receipt)
        except RestoreError as error:
            raise RestoreError(error.failure_class, "internal") \
                from None


def _remap(frm, to):
    class Remap(RestoreEngine):
        def restore(self, receipt):
            try:
                return super().restore(receipt)
            except RestoreError as error:
                if error.failure_class == frm:
                    raise RestoreError(to, FAILURE_MAPPING[to]) \
                        from None
                raise
    Remap.__name__ = f"Remap_{frm}_to_{to}"
    return Remap


class DoubleParserCall(RestoreEngine):
    def __init__(self, parser):
        def twice(bundle):
            parser(bundle)
            return parser(bundle)
        super().__init__(twice)


class UnguardedParser(RestoreEngine):
    """Calls the parser once OUTSIDE the boundary, before
    verification."""

    def restore(self, receipt):
        if type(receipt) is dict and type(receipt.get("bundle")) is str:
            self.parser(receipt["bundle"])
        return super().restore(receipt)


class ParsesBeforeVerify(RestoreEngine):
    """Guarded, but parses before verification (verify-first
    broken)."""

    def restore(self, receipt):
        if type(receipt) is dict and type(receipt.get("bundle")) is str:
            with contextlib.suppress(BaseException):
                self.parser(receipt["bundle"])
        return super().restore(receipt)


class ReverseState(RestoreEngine):
    def restore(self, receipt):
        out = super().restore(receipt)
        out["state"] = dict(reversed(list(out["state"].items())))
        return out


class ValueRotate(RestoreEngine):
    def restore(self, receipt):
        out = super().restore(receipt)
        keys, values = list(out["state"]), list(out["state"].values())
        out["state"] = dict(zip(keys, values[1:] + values[:1], strict=True))
        return out


class DropsRecord(RestoreEngine):
    def restore(self, receipt):
        out = super().restore(receipt)
        if out["state"]:
            out["state"].pop(sorted(out["state"])[-1])
        return out


class StaleRestoreId(RestoreEngine):
    def restore(self, receipt):
        out = super().restore(receipt)
        out["restore_id"] = "rst1:" + hashlib.sha256(
            out["backup_id"].encode()).hexdigest()
        return out


class ReordersReceiptKeys(RestoreEngine):
    def restore(self, receipt):
        out = super().restore(receipt)
        items = list(receipt.items())
        receipt.clear()
        receipt.update(reversed(items))
        return out


class MarksReceipt(RestoreEngine):
    """Records the restore on the caller's receipt (commit is NOT
    staged-only)."""

    def restore(self, receipt):
        out = super().restore(receipt)
        receipt["restore_id"] = out["restore_id"]
        return out


class CachedResult(RestoreEngine):
    """Class-level cache: a repeated state returns the SAME record
    object."""
    _cache = {}

    def restore(self, receipt):
        out = super().restore(receipt)
        return CachedResult._cache.setdefault(out["restore_id"], out)


class RawReceiptKeySet(RestoreEngine):
    def restore(self, receipt):
        if isinstance(receipt, dict):
            set(receipt.keys()) == set(RECEIPT_FIELDS)  # noqa: B015
        return super().restore(receipt)


class RawRecordFieldSet(RestoreEngine):
    """Compares each parsed record's raw key set before any
    guard."""

    def _parse(self, bundle):
        out = super()._parse(bundle)
        for rec in dict.values(out):
            if isinstance(rec, dict):
                set(rec.keys()) == set(RECORD_FIELDS)  # noqa: B015
        return out


class RawOnNonDictReceipt(RestoreEngine):
    def restore(self, receipt):
        len(receipt.items())
        return super().restore(receipt)


def _source_mutant(name, edits):
    """A one-guard edit of the reference engine: each OLD must
    occur in the reference source (first occurrence replaced)."""
    src = inspect.getsource(_reference.RestoreEngine)
    for old, new in edits:
        if old not in src:
            raise AssertionError(f"{name}: edit site missing: {old!r}")
        src = src.replace(old, new, 1)
    namespace = dict(vars(_reference))
    # the mutant raises the BOUND error class, so a correct rejection
    # counts as one under any binding (reference or production)
    namespace["RestoreError"] = RestoreError

    def _bound_fail(cls):
        raise RestoreError(cls, FAILURE_MAPPING[cls])

    namespace["_fail"] = _bound_fail
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return namespace["RestoreEngine"]


_I8, _I12 = " " * 8, " " * 12
NoReceiptRestore = _source_mutant("no-receipt-restore", [(
    f"{_I12}receipt.clear()\n{_I12}receipt.update(saved)\n",
    f"{_I12}pass\n")])
LiveStateIdReread = _source_mutant("live-state-id-reread", [(
    f"{_I12}parsed = self._parse(frozen_bundle)\n",
    f"{_I12}parsed = self._parse(frozen_bundle)\n"
    f"{_I12}frozen_state_id = receipt[\"state_id\"]\n")])
NarrowParserBoundary = _source_mutant("narrow-parser-boundary", [(
    "except BaseException:", "except Exception:")])
IsinstanceParserOutput = _source_mutant("isinstance-parser-output", [(
    "if type(out) is not dict:", "if not isinstance(out, dict):")])
AliasesParserRecords = _source_mutant("aliases-parser-records", [(
    "state[key] = dict(rec)", "state[key] = rec")])
SkipsCanonicalRoundtrip = _source_mutant(
    "skips-canonical-roundtrip", [(
        "if serialize_bundle(state) != frozen_bundle:",
        "if False:")])
TrustsReceiptStateId = _source_mutant("trusts-receipt-state-id", [(
    "if state_id(state) != frozen_state_id:", "if False:")])
SkipsVerify = _source_mutant("skips-verify", [(
    f"{_I8}try:\n{_I12}_BACKUP.verify(receipt)\n",
    f"{_I8}try:\n{_I12}pass\n")])

MUTANTS = {
    "accepts-all": AcceptsAll,
    "wrong-code": WrongCode,
    "unverified-as-malformed": _remap(UB, MRR),
    "malformed-as-unverified": _remap(MRR, UB),
    "parse-as-state": _remap(DP, DS),
    "state-as-parse": _remap(DS, DP),
    "double-parser-call": DoubleParserCall,
    "unguarded-parser": UnguardedParser,
    "parses-before-verify": ParsesBeforeVerify,
    "reverse-state": ReverseState,
    "value-rotate": ValueRotate,
    "drops-record": DropsRecord,
    "stale-restore-id": StaleRestoreId,
    "reorders-receipt-keys": ReordersReceiptKeys,
    "marks-receipt": MarksReceipt,
    "cached-result": CachedResult,
    "raw-receipt-key-set": RawReceiptKeySet,
    "raw-record-field-set": RawRecordFieldSet,
    "raw-on-non-dict-receipt": RawOnNonDictReceipt,
    "no-receipt-restore": NoReceiptRestore,
    "live-state-id-reread": LiveStateIdReread,
    "narrow-parser-boundary": NarrowParserBoundary,
    "isinstance-parser-output": IsinstanceParserOutput,
    "aliases-parser-records": AliasesParserRecords,
    "skips-canonical-roundtrip": SkipsCanonicalRoundtrip,
    "trusts-receipt-state-id": TrustsReceiptStateId,
    "skips-verify": SkipsVerify,
}

MUTANT_TARGETS = {
    "accepts-all": "malformed:receipt-wrong-backup-id",
    "wrong-code": "malformed:receipt-missing-field",
    "unverified-as-malformed": "malformed:receipt-wrong-backup-id",
    "malformed-as-unverified": "malformed:receipt-missing-field",
    "parse-as-state": "malformed:oracle-raises",
    "state-as-parse": "malformed:oracle-divergent-state",
    "double-parser-call": "happy:restore-single-put-after-e4",
    "unguarded-parser": "totality:parser-raises-value-error",
    "parses-before-verify": "totality:receipt-missing-backup_id",
    "reverse-state": ORDER_LABEL,
    "value-rotate": ORDER_LABEL,
    "drops-record": "happy:restore-single-put-after-e4",
    "stale-restore-id": "happy:restore-single-put-after-e4",
    "reorders-receipt-keys": "happy:restore-single-put-after-e4",
    "marks-receipt": "happy:restore-single-put-after-e4",
    "cached-result": "happy:restore-single-put-after-e4",
    "raw-receipt-key-set": "totality:receipt-key-bundle-colliding-hash",
    "raw-record-field-set":
        "totality:parser-record-key-digest-colliding-hash",
    "raw-on-non-dict-receipt": "totality:receipt-none",
    "no-receipt-restore": "totality:parser-mutates-receipt-bundle",
    "live-state-id-reread":
        "totality:parser-mutates-receipt-state-id",
    "narrow-parser-boundary":
        "totality:parser-raises-keyboard-interrupt",
    "isinstance-parser-output":
        "totality:parser-returns-dict-subclass",
    "aliases-parser-records": "totality:parser-holds-returned-state",
    "skips-canonical-roundtrip": "malformed:bundle-reordered-records",
    "trusts-receipt-state-id": "malformed:oracle-divergent-state",
    "skips-verify": "malformed:receipt-wrong-backup-id",
}


# -- kill-proof: substitution mutants vs the closure ---------------------------
def _payload(row):
    return {k: v for k, v in row.items() if k != "name"}


def _erasures(section, row):
    """Single-edge erasures of ROW, still executable where it
    matters, with pinned records regenerated from the reference so
    only the closure can kill them. An erasure equal to its row is
    an equivalent mutant and is dropped."""
    out = []
    if section in ("happy", "boundary"):
        regrown = copy.deepcopy(row)
        regrown["receipt"] = _reference._receipt(("put", KINGS),
                                                 ("put", E4))
        regrown["expect"] = _reference_restore("honest",
                                               regrown["receipt"])
        out.append(("receipt-regrown", regrown))
        emptied = copy.deepcopy(row)
        emptied["receipt"] = _reference._receipt()
        emptied["expect"] = _reference_restore("honest",
                                               emptied["receipt"])
        out.append(("receipt-emptied", emptied))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        rep = _repaired(row)
        fixed.update(receipt=rep["receipt"], oracle=rep["oracle"])
        out.append(("defect-repaired", fixed))
    else:
        unfixed = copy.deepcopy(row)
        unfixed["then_receipt"] = copy.deepcopy(row["receipt"])
        unfixed["then_oracle"] = row["oracle"]
        out.append(("follow-up-not-fixed", unfixed))
        swapped = copy.deepcopy(row)
        swapped["oracle"], swapped["then_oracle"] = \
            row["then_oracle"], row["oracle"]
        out.append(("oracles-swapped", swapped))
    return [(label, m) for label, m in out if m != row]


def _substitution_mutants():
    out = []
    labels = [(s, r["name"]) for s in MANIFESTS for r in CASES[s]]
    for sa, na in labels:
        for sb, nb in labels:
            if (sa, na) == (sb, nb):
                continue
            m = copy.deepcopy(CASES)
            i = [r["name"] for r in m[sa]].index(na)
            src = next(r for r in CASES[sb] if r["name"] == nb)
            m[sa][i] = dict(copy.deepcopy(src), name=na)
            out.append((f"payload:{sa}:{na}<-{sb}:{nb}", m))
    for section in MANIFESTS:
        rows = CASES[section]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                m = copy.deepcopy(CASES)
                m[section][i]["name"], m[section][j]["name"] = \
                    rows[j]["name"], rows[i]["name"]
                out.append((f"name-swap:{section}:{i}:{j}", m))
        for label, fn in (("reversed", lambda r: r.reverse()),
                          ("dropped", lambda r: r.pop()),
                          ("duplicated",
                           lambda r: r.append(copy.deepcopy(r[0])))):
            m = copy.deepcopy(CASES)
            fn(m[section])
            out.append((f"{label}:{section}", m))
        for i, row in enumerate(CASES[section]):
            for label, mutant in _erasures(section, row):
                m = copy.deepcopy(CASES)
                m[section][i] = mutant
                out.append((f"erased:{section}:{row['name']}:{label}",
                            m))
    return out


# -- tests -----------------------------------------------------------------------
def test_closure():
    _validate_closure(CASES)


def test_row_digest_table_is_closed():
    assert set(ROW_DIGESTS) == {
        f"{s}:{r['name']}" for s in MANIFESTS for r in CASES[s]}
    for s in MANIFESTS:
        for r in CASES[s]:
            assert ROW_DIGESTS[f"{s}:{r['name']}"] == _row_digest(r)


def test_no_equivalent_payload_pairs():
    """No two rows share a payload, so every substitution is a
    real mutant."""
    rows = [_payload(r) for s in MANIFESTS for r in CASES[s]]
    assert all(a != b for i, a in enumerate(rows)
               for b in rows[i + 1:])


def test_probe_manifest_closed_and_ordered():
    assert list(PROBES) == list(PROBE_MANIFEST)
    assert len(PROBE_MANIFEST) == PROBE_COUNT


def test_order_probe_is_discriminating():
    receipt = _order_receipt()
    _check_result(ORDER_LABEL, receipt, ORDER_EXPECT)
    state = list(ORDER_EXPECT["state"])
    put_order = [_identity(_node(f)) for f in (E4, STARTPOS, KINGS)]
    assert len(state) == 3 and state == sorted(state)
    assert state not in (put_order, put_order[::-1])


def test_probe_expect_is_the_reference_record():
    _check_result("probe", _probe_receipt(), PROBE_EXPECT)
    assert len(PROBE_EXPECT["state"]) == 2


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(RestoreEngine, executed=executed) == []
    assert executed == [f"{s}:{m[0]}" for s, man in MANIFESTS.items()
                        for m in man] + [
        f"totality:{n}" for n in PROBE_MANIFEST] + [ORDER_LABEL]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_red(name):
    assert _probe(MUTANTS[name], first_only=True), \
        f"{name}: mutant passed battery"


def test_identity_source_mutant_is_green_under_current_binding():
    """Structural guard: an unedited reference-source mutant passes
    the whole battery under the CURRENT binding, so a source mutant
    dies only for its edit - never because it raises a different
    error class than the one the probes catch."""
    assert _probe(_source_mutant("identity", [])) == []


def test_mutant_targets_closed():
    assert set(MUTANT_TARGETS) == set(MUTANTS)


def test_mutants_fail_on_their_target_rows():
    for name, label in MUTANT_TARGETS.items():
        failures = _probe(MUTANTS[name], only={label})
        assert [":".join(f.split(":")[:2]) for f in failures] == \
            [label], (name, failures)


def test_reference_green_on_every_mutant_target():
    assert _probe(RestoreEngine, only=set(MUTANT_TARGETS.values())) \
        == []


def test_every_row_has_a_real_erasure():
    for section in MANIFESTS:
        for row in CASES[section]:
            erasures = _erasures(section, row)
            assert erasures, (section, row["name"])
            assert all(m != row for _, m in erasures)


@pytest.mark.parametrize("with_digests", [True, False],
                         ids=["with-digests", "digests-neutralized"])
def test_closure_kills_substitution_mutants(with_digests,
                                            monkeypatch):
    """Every cross-section payload substitution, rename, reorder,
    drop, duplicate and single-edge erasure is killed by
    _validate_closure with the digest table live AND with
    _row_digest monkeypatched to return the table value (digests
    neutralized)."""
    if not with_digests:
        monkeypatch.setattr(
            sys.modules[__name__], "_row_digest",
            lambda row: ROW_DIGESTS.get(
                f"{_section_of(row)}:{row['name']}", ""))
    mutants = _substitution_mutants()
    assert len(mutants) == 840
    survivors = []
    for label, m in mutants:
        try:
            _validate_closure(m)
        except (AssertionError, ValueError, KeyError, TypeError):
            continue
        survivors.append(label)
    assert survivors == [], survivors
