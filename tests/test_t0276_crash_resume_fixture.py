"""T0276: crash-resume conformance fixture - the fixture must
PROVE happy, boundary, malformed and rollback behavior against
the T0275 store-crash-resume contract. The cases execute against
the contract-derived reference in
tests.test_t0275_crash_resume_contract (itself derived from
data/contracts/crash_resume.yaml plus the linked WAL contract) -
nothing is re-implemented here. Pinned resume receipts AND the
pinned surviving logs were computed from that reference at
authoring time, so any contract or derivation drift breaks this
battery. Every malformed case is discriminating: applying ONLY
its declared single-locus repair (log, request or sink) makes it
resume successfully. Rollback cases prove a rejected resume
leaves the exact supplied log and request bit- and
reference-identical before the valid follow-up resume removes
exactly the torn tail.

DESIGN CAUTION: the reference engine is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work
must execute these same cases against a separately implemented
runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0212_wal_contract import (  # noqa: E402
    WalEngine,
    WalError,
    canonical_payload,
)
from tests.test_t0275_crash_resume_contract import (  # noqa: E402
    ResumeEngine,
    ResumeError,
    quarantine_tail,
)
from tools.crash_resume_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "crash_resume"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECEIPT_FIELDS = list(_CC["record"]["fields"])
_IDS = _CC["identifiers"]
_RESUME_RE = re.compile(_IDS["resume_id"]["grammar"])
_HEAD_RE = re.compile(_IDS["head"]["grammar"])
_STATE_RE = re.compile(_IDS["state_id"]["grammar"])
_TOKEN_RE = re.compile(_IDS["quarantine_token"]["grammar"])

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "log", "request", "sink", "expect",
           "expect_log"}
BAD_KEYS = {"name", "defect", "expect_failure", "log", "request",
            "sink", "minimal_repair"}
RB_KEYS = {"name", "why", "log", "request", "rejected_sink",
           "expect_failure", "expect", "expect_log"}


# -- closed sink registry (names in the fixture resolve ONLY here) -----------


def _raising(tail):
    raise ValueError("untrusted sink failure")


def _bytes(tail):
    return quarantine_tail(tail).encode()


def _bad_grammar(tail):
    return "qtn1:" + quarantine_tail(tail)[5:].upper()


def _empty_tail_token(tail):
    return quarantine_tail([])


def _unframed(tail):
    text = "".join(json.dumps(e, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False) for e in tail)
    return "qtn1:" + hashlib.sha256(text.encode()).hexdigest()


SINKS = {
    "canonical": quarantine_tail,
    "raising": _raising,
    "bytes": _bytes,
    "bad_grammar": _bad_grammar,
    "empty_tail_token": _empty_tail_token,
    "unframed": _unframed,
}


def _rollback_sink(name, log, request):
    """Hostile sinks bound to the CALLER's live inputs."""

    def scribble():
        log.clear()
        log.append({"forged": True})
        request["checkpoint_sequence"] = 0
        request["extra"] = 1

    def mutate_inputs_then_raise(tail):
        scribble()
        raise RuntimeError("after scribbling")

    def mutate_inputs_then_wrong_token(tail):
        scribble()
        return quarantine_tail([])

    def mutate_tail_arg_then_matching_token(tail):
        tail.append({"smuggled": True})
        return quarantine_tail(tail)

    return {
        "mutate_inputs_then_raise": mutate_inputs_then_raise,
        "mutate_inputs_then_wrong_token": mutate_inputs_then_wrong_token,
        "mutate_tail_arg_then_matching_token":
            mutate_tail_arg_then_matching_token,
    }[name]


ROLLBACK_SINKS = ("mutate_inputs_then_raise",
                  "mutate_inputs_then_wrong_token",
                  "mutate_tail_arg_then_matching_token")


# -- structure ---------------------------------------------------------------


def _validate_receipt(receipt, label):
    assert type(receipt) is dict, label
    assert set(receipt) == set(RECEIPT_FIELDS), label
    assert _RESUME_RE.fullmatch(receipt["resume_id"]), label
    assert _HEAD_RE.fullmatch(receipt["head"]), label
    assert _STATE_RE.fullmatch(receipt["state_id"]), label
    assert _TOKEN_RE.fullmatch(receipt["quarantine_token"]), label
    for field in ("resumed_count", "discarded_count"):
        assert type(receipt[field]) is int and receipt[field] >= 0, label


def _validate_success_row(row, label):
    _validate_receipt(row["expect"], label)
    assert type(row["log"]) is list, label
    exp = row["expect"]
    assert exp["resumed_count"] + exp["discarded_count"] == len(row["log"])
    # the surviving log is EXACTLY the resumed prefix
    assert row["expect_log"] == row["log"][:exp["resumed_count"]], label
    assert type(row["request"]["checkpoint_sequence"]) is int, label
    assert row["request"]["checkpoint_sequence"] <= exp["resumed_count"]


def _validate_structure(cases):
    assert type(cases) is dict and set(cases) == TOP_KEYS
    assert type(cases["schema"]) is int
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == _CC["versioning"]["base_path"]
    assert type(cases["notes"]) is str and cases["notes"]
    names = []
    for section in SECTIONS:
        rows = cases[section]
        assert type(rows) is list and rows, section
        for row in rows:
            assert type(row) is dict, section
            names.append(row.get("name"))
            label = f"{section}:{row.get('name')}"
            assert type(row.get("name")) is str, label
            assert re.fullmatch(r"[a-z0-9_]+", row["name"]), label
            if section in ("happy", "boundary"):
                assert set(row) == OK_KEYS, label
                assert row["sink"] == "canonical", label
                _validate_success_row(row, label)
            elif section == "malformed":
                assert set(row) == BAD_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                assert row["sink"] in SINKS, label
                assert type(row["defect"]) is str and row["defect"], label
                repair = row["minimal_repair"]
                assert type(repair) is dict and len(repair) == 1, label
                (locus, value), = repair.items()
                assert locus in ("log", "request", "sink"), label
                assert value != row[locus], label
                if locus == "sink":
                    assert value == "canonical", label
            else:
                assert set(row) == RB_KEYS, label
                assert row["rejected_sink"] in ROLLBACK_SINKS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                _validate_success_row(row, label)
                assert row["expect"]["discarded_count"] > 0, label
    assert len(names) == len(set(names)), "fixture names must be unique"


# -- the closed scenario manifests -------------------------------------------
# Every section's ordered names must equal its manifest EXACTLY (no
# missing, duplicated, renamed, reordered or extra rows) and every
# row's metadata and semantic shape must equal its manifest entry.
G0 = "wal0:" + "0" * 64

# happy/boundary: name -> (log length, checkpoint, resumed_count,
# discarded_count, head kind)
HAPPY_MANIFEST = {
    "clean_log_resumes_unchanged": (3, 3, 3, 0, "wal1"),
    "torn_fragment_discarded": (4, 3, 3, 1, "wal1"),
    "torn_broken_chain_discarded": (4, 3, 3, 1, "wal1"),
}
BOUNDARY_MANIFEST = {
    "empty_log_genesis": (0, 0, 0, 0, "wal0"),
    "checkpoint_zero_whole_log_torn": (3, 0, 0, 3, "wal0"),
    "valid_looking_entries_after_first_invalid": (4, 2, 2, 2, "wal1"),
    "tail_depth_at_limit": (4, 3, 3, 1, "wal1"),
    "tail_int_at_digit_limit": (4, 3, 3, 1, "wal1"),
}
# malformed: name -> (failure class, sink, repair locus, pinned
# defect text); the name is the CLOSED scenario tag
MALFORMED_MANIFEST = {
    "log_not_a_list": ('malformed_resume_record', 'canonical', 'log',
        'the source log is a dict, not a list'),
    "request_missing_checkpoint": ('malformed_resume_record', 'canonical', 'request',
        'request has no checkpoint_sequence'),
    "request_extra_field": ('malformed_resume_record', 'canonical', 'request',
        'request carries an unknown extra field'),
    "checkpoint_bool": ('malformed_resume_record', 'canonical', 'request',
        'checkpoint_sequence is a bool, not an int'),
    "checkpoint_string": ('malformed_resume_record', 'canonical', 'request',
        'checkpoint_sequence is a string'),
    "checkpoint_negative": ('unknown_checkpoint', 'canonical', 'request',
        'checkpoint_sequence is -1'),
    "checkpoint_past_log_end": ('corrupt_source', 'canonical', 'request',
        'checkpoint 5 acknowledges entries the log does not have'),
    "durable_entry_damaged": ('corrupt_source', 'canonical', 'log',
        'acknowledged entry 2 has a wrong digest'),
    "tail_too_deep": ('malformed_resume_record', 'canonical', 'log',
        'the torn entry nests 65 deep'),
    "tail_int_past_digit_bound": ('malformed_resume_record', 'canonical', 'log',
        'the torn entry holds a 4001-digit int'),
    "tail_lone_surrogate": ('malformed_resume_record', 'canonical', 'log',
        'the torn entry holds a lone surrogate, not UTF-8 encodable'),
    "sink_raises": ('divergent_quarantine', 'raising', 'sink',
        'the quarantine sink raises'),
    "sink_non_string": ('divergent_quarantine', 'bytes', 'sink',
        'the sink returns bytes'),
    "sink_bad_grammar": ('divergent_quarantine', 'bad_grammar', 'sink',
        'the sink token breaks the pinned grammar'),
    "sink_empty_tail_token": ('divergent_quarantine', 'empty_tail_token', 'sink',
        'the sink returns the token of an empty tail'),
    "sink_unframed_token": ('divergent_quarantine', 'unframed', 'sink',
        'the sink hashes the tail without length framing'),
}
# rollback: name -> (rejected sink, failure class, log length,
# resumed_count, discarded_count of the valid follow-up)
ROLLBACK_MANIFEST = {
    "mutate_inputs_then_raise": ('mutate_inputs_then_raise', 'divergent_quarantine',
        4, 3, 1),
    "mutate_inputs_then_wrong_token": ('mutate_inputs_then_wrong_token', 'divergent_quarantine',
        4, 3, 1),
    "mutate_tail_arg_then_matching_token": (
        'mutate_tail_arg_then_matching_token', 'divergent_quarantine',
        4, 3, 1),
}
MANIFESTS = {"happy": HAPPY_MANIFEST, "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

# content binding: canonical sha256 of every row (log, request, sink,
# repair, defect, pinned receipt and surviving log). Any edit to a
# row must update this table in the same change.
ROW_DIGESTS = {
    "happy:clean_log_resumes_unchanged":
        "82006fd90a3991dedc223b4faf9188194c90363ce005edc926414ec600600602",
    "happy:torn_fragment_discarded":
        "74c04ee56d4676bee9880a8ae9973662d3e411d0594f850f1a28552952b15183",
    "happy:torn_broken_chain_discarded":
        "46db686dbababa3fa80643a2c9250e1ffd67ccdd3fa103c5c3eb1aa420be3501",
    "boundary:empty_log_genesis":
        "6063c70b67086a13f7b1a8c415a38fb2d0ca6d083235c4c128233c2ec00bf9c9",
    "boundary:checkpoint_zero_whole_log_torn":
        "c9b2dbdf83221ab35a73372b96124b3e2effc364853850041357416be40309ab",
    "boundary:valid_looking_entries_after_first_invalid":
        "28793d5347cac0d7d776749496c84adb0f76b12176f01c0811cdd0dd7fdf2ba2",
    "boundary:tail_depth_at_limit":
        "a655a53aa0e0837f63ee5eda1bcbea344f5a3d41ff90df0b8ed782d7b933590e",
    "boundary:tail_int_at_digit_limit":
        "dae960514cf9675272dab32a4be174bec527a3c63d6970d0964521df26f1a58c",
    "malformed:log_not_a_list":
        "fbbd15656185b268314b34ac3188d36c5df0f0ddaa84ca3a51e85948b9b98b9f",
    "malformed:request_missing_checkpoint":
        "66733ed8973ee23e6a61b57ccd3c6fed0962249270fdf40a4ce2843dbe4392c7",
    "malformed:request_extra_field":
        "1c66e997e0e6f6cc485e02fd293a0bea226784cf2bfcab784de642df431a68c9",
    "malformed:checkpoint_bool":
        "53d849abc2cdddbfb7af73e7de1990655f96329907742aa8b29e2b407630d5a9",
    "malformed:checkpoint_string":
        "7c8d0300e208e2ab930591853110ce76b16d5f7d0e842d556feae9a8f761d81d",
    "malformed:checkpoint_negative":
        "b29276a60d8f92b31c43fc1c55f335a6314c8f9ecfc59b39ddd71eab5ba53376",
    "malformed:checkpoint_past_log_end":
        "064a8955260d5756714aa5eac061fa02bc7d0212586d17cad8266d966fb1a943",
    "malformed:durable_entry_damaged":
        "f95c56c71cf7600e8d3495b475aca48a3e2eee34e897aeec6e5de38d5fc11486",
    "malformed:tail_too_deep":
        "9557a38b6392b40225fc845835cfe2c7e3bf08f93ae648e4a836f9d60d2fb606",
    "malformed:tail_int_past_digit_bound":
        "cadc23dfa876e09255ff14c5033084f4f28122cdef42d659b79134be30c87635",
    "malformed:tail_lone_surrogate":
        "f23eef3022b426f897fb970ec32f718708710be04ad42725406df07b79371e46",
    "malformed:sink_raises":
        "7eec5077c238bdd4de196efe1ad06509e2c7334fb4964114565a7559514ee1dc",
    "malformed:sink_non_string":
        "d7034d56bd271c636e8272b728751c775897afee725d1ac0c410cc29a61abc0c",
    "malformed:sink_bad_grammar":
        "6aee9b7855f2349a5c772f2d7841c5f748d656c274f6508cc55912ad9e6914ca",
    "malformed:sink_empty_tail_token":
        "5911d5fb2f575b877a63ffa0af24a59e9646f00d9866e545af0c94a576de5e49",
    "malformed:sink_unframed_token":
        "f2e6461fa6cd57c1c0ff0595e5d869d6e248372c26e085b2884c8c43e6569c8d",
    "rollback:mutate_inputs_then_raise":
        "facd5ff14cf1154123a55c5689eb2cd0266af2640a4d11cd9a8d2c84303f260d",
    "rollback:mutate_inputs_then_wrong_token":
        "42f5a0e54e80862c02503df919136777d286bbe8659284d59e0e8a722238bebf",
    "rollback:mutate_tail_arg_then_matching_token":
        "c0ee6c12f8965899fbbff972b8b909c705a7dab08f50c3e22269cf8286c48ecf",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _diff_paths(a, b, path=()):
    """Every leaf path where two JSON values differ."""
    if type(a) is dict and type(b) is dict:
        out = set()
        for key in set(a) | set(b):
            if key not in a or key not in b:
                out.add((*path, key))
            else:
                out |= _diff_paths(a[key], b[key], (*path, key))
        return out
    if type(a) is list and type(b) is list and len(a) == len(b):
        out = set()
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out |= _diff_paths(x, y, (*path, i))
        return out
    return set() if (type(a) is type(b) and a == b) else {path}


def _depth(obj):
    depth = 0
    while type(obj) is list and len(obj) == 1:
        obj, depth = obj[0], depth + 1
    return depth if obj == 0 else -1


def _resumes(log, request):
    try:
        ResumeEngine(quarantine_tail).resume(copy.deepcopy(log),
                                             copy.deepcopy(request))
    except ResumeError:
        return False
    return True


CP = 3
VALID_REQ = {"checkpoint_sequence": CP}
TORN_FRAGMENT = {"op": "pu"}
_SINK_TAGS = {"sink_raises", "sink_non_string", "sink_bad_grammar",
              "sink_empty_tail_token", "sink_unframed_token"}
_TAIL_TAGS = {
    # tag -> (defective torn entry, repaired torn entry)
    "tail_too_deep": ("depth", 65, 64),
    "tail_int_past_digit_bound": ("int", 10**4000, 10**4000 - 1),
    "tail_lone_surrogate": ("value", {"op": "\ud800"}, TORN_FRAGMENT),
}


def _check_malformed_scenario(row):
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus."""
    name = row["name"]
    log, request = row["log"], row["request"]
    (locus, fix), = row["minimal_repair"].items()
    if name in _SINK_TAGS:
        assert locus == "sink" and fix == "canonical", name
        assert request == VALID_REQ and _resumes(log, request), name
        tail = log[CP:]
        assert tail == [TORN_FRAGMENT], name
        good = quarantine_tail(tail)
        fn = SINKS[row["sink"]]
        if name == "sink_raises":
            with pytest.raises(ValueError):
                fn(copy.deepcopy(tail))
            return
        out = fn(copy.deepcopy(tail))
        if name == "sink_non_string":
            assert type(out) is bytes and out.decode() == good, name
        elif name == "sink_bad_grammar":
            assert type(out) is str and _TOKEN_RE.fullmatch(out) is None
            assert out.lower() == good, name
        elif name == "sink_empty_tail_token":
            assert _TOKEN_RE.fullmatch(out) and out != good, name
            assert out == quarantine_tail([]), name
        elif name == "sink_unframed_token":
            assert _TOKEN_RE.fullmatch(out) and out != good, name
            assert out != quarantine_tail([]), name
        else:
            raise AssertionError(name)
        return
    assert row["sink"] == "canonical", name
    if locus == "request":
        assert fix == VALID_REQ and _resumes(log, fix), name
        if name == "request_missing_checkpoint":
            assert request == {}, name
        elif name == "request_extra_field":
            assert request == {**VALID_REQ, "force": True}, name
        elif name == "checkpoint_bool":
            assert set(request) == set(VALID_REQ), name
            assert type(request["checkpoint_sequence"]) is bool, name
        elif name == "checkpoint_string":
            assert request == {"checkpoint_sequence": str(CP)}, name
        elif name == "checkpoint_negative":
            assert request == {"checkpoint_sequence": -1}, name
        elif name == "checkpoint_past_log_end":
            cp = request["checkpoint_sequence"]
            assert set(request) == set(VALID_REQ), name
            assert type(cp) is int and cp > len(log), name
        else:
            raise AssertionError(name)
        return
    assert locus == "log" and request == VALID_REQ, name
    assert _resumes(fix, request), name
    if name == "log_not_a_list":
        assert type(log) is not list, name
        return
    assert type(log) is list, name
    if name == "durable_entry_damaged":
        assert len(log) == CP, name
        assert _diff_paths(log, fix) == {
            (1, "payload", "record", "digest")}, name
        return
    assert name in _TAIL_TAGS, name
    kind, bad, good = _TAIL_TAGS[name]
    # the durable prefix is untouched: ONLY the torn entry differs
    assert len(log) == len(fix) == CP + 1, name
    assert log[:CP] == fix[:CP] and _wal_replays(log[:CP]), name
    if kind == "depth":
        assert (_depth(log[CP]), _depth(fix[CP])) == (bad, good), name
    elif kind == "int":
        assert (log[CP], fix[CP]) == ({"n": bad}, {"n": good}), name
    else:
        assert (log[CP], fix[CP]) == (bad, good), name


def _validate_closure(cases):
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == list(manifest), section
        for row in rows:
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS.get(label) == _row_digest(row), label
            meta = manifest[row["name"]]
            if section in ("happy", "boundary"):
                exp = row["expect"]
                assert (len(row["log"]),
                        row["request"]["checkpoint_sequence"],
                        exp["resumed_count"], exp["discarded_count"],
                        exp["head"].split(":")[0]) == meta, label
                assert (exp["head"] == G0) == (
                    exp["resumed_count"] == 0), label
            elif section == "malformed":
                assert (row["expect_failure"], row["sink"],
                        next(iter(row["minimal_repair"])),
                        row["defect"]) == meta, label
                _check_malformed_scenario(row)
            else:
                exp = row["expect"]
                assert (row["rejected_sink"], row["expect_failure"],
                        len(row["log"]), exp["resumed_count"],
                        exp["discarded_count"]) == meta, label
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    _check_boundary_edges({r["name"]: r for r in cases["boundary"]})
    _check_happy_edges({r["name"]: r for r in cases["happy"]})



def _max_depth(obj):
    """Local iterative nesting walker (containers only)."""
    best, stack = 0, [(obj, 0)]
    while stack:
        node, d = stack.pop()
        if type(node) in (list, dict):
            best = max(best, d + 1)
            kids = node.values() if type(node) is dict else node
            stack.extend((k, d + 1) for k in kids)
    return best


def _max_int_digits(obj):
    best, stack = 0, [obj]
    while stack:
        node = stack.pop()
        if type(node) is int:
            best = max(best, len(str(abs(node))))
        elif type(node) is dict:
            stack.extend(node.values())
        elif type(node) is list:
            stack.extend(node)
    return best


def _check_boundary_edges(rows):
    """Each boundary row REALIZES its edge over the original data."""
    r = rows["empty_log_genesis"]
    assert r["log"] == [] and r["expect"]["head"] == G0
    r = rows["checkpoint_zero_whole_log_torn"]
    assert r["request"]["checkpoint_sequence"] == 0
    assert r["expect"]["discarded_count"] == len(r["log"]) > 0
    assert not _wal_replays(r["log"][:1])
    r = rows["valid_looking_entries_after_first_invalid"]
    k = r["expect"]["resumed_count"]
    assert not _wal_replays(r["log"][:k + 1])
    assert _wal_replays(r["log"][:k] + r["log"][k + 1:])
    for name, walker, edge in (
            ("tail_depth_at_limit", _max_depth, 64),
            ("tail_int_at_digit_limit", _max_int_digits, 4000)):
        r = rows[name]
        k = r["expect"]["resumed_count"]
        assert r["expect"]["discarded_count"] == 1, name
        assert walker(r["log"][k]) == edge, name



def _check_happy_edges(h):
    """Each happy row REALIZES its scenario over the original data -
    rows with identical cardinalities are still told apart."""
    r = h["clean_log_resumes_unchanged"]
    assert r["expect_log"] == r["log"]
    assert r["expect"]["discarded_count"] == 0
    a = h["torn_fragment_discarded"]["log"]
    b = h["torn_broken_chain_discarded"]["log"]
    assert a[CP:] == [TORN_FRAGMENT]
    assert len(b) == CP + 1 and type(b[CP]) is dict
    assert set(b[CP]) == set(b[CP - 1])
    assert b[CP]["prior_entry_id"] != b[CP - 1]["entry_id"]


def test_fixture_scenario_closure():
    _validate_closure(CASES)


def test_param_ids_equal_manifest_names():
    for section, manifest in MANIFESTS.items():
        assert _names(section) == list(manifest), section


def test_fixture_structure():
    _validate_structure(CASES)
    _validate_closure(CASES)


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(schema=2),
    lambda c: c.update(schema=True),
    lambda c: c.update(contract="store-rollback"),
    lambda c: c.update(extra=1),
    lambda c: c.pop("rollback"),
    lambda c: c["boundary"].clear(),
    lambda c: c["happy"][0].update(sink="raising"),
    lambda c: c["happy"][1]["expect"].pop("quarantine_token"),
    lambda c: c["happy"][1]["expect"].update(resume_id="rsm1:zz"),
    lambda c: c["happy"][1]["expect"].update(discarded_count=0),
    lambda c: c["happy"][1]["expect_log"].pop(),
    lambda c: c["happy"][1].update(expect_log=c["happy"][1]["log"]),
    lambda c: c["malformed"][0].update(expect_failure="internal"),
    lambda c: c["malformed"][0].update(sink="eval"),
    lambda c: c["malformed"][1].update(minimal_repair={"request": {}}),
    lambda c: c["malformed"][0]["minimal_repair"].update(sink="canonical"),
    lambda c: c["malformed"][11].update(minimal_repair={"sink": "bytes"}),
    lambda c: c["rollback"][0].update(rejected_sink="canonical"),
    lambda c: c["boundary"].append(copy.deepcopy(c["happy"][0])),
], ids=lambda f: "mut")
def test_structure_mutations_fail(mutate):
    cases = copy.deepcopy(CASES)
    mutate(cases)
    with pytest.raises(AssertionError):
        _validate_structure(cases)


def test_failure_class_coverage():
    covered = {row["expect_failure"] for row in CASES["malformed"]}
    assert covered == FAILURE_CLASSES
    loci = {next(iter(row["minimal_repair"])) for row in CASES["malformed"]}
    assert loci == {"log", "request", "sink"}


# -- execution ---------------------------------------------------------------


def _wal_replays(log):
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return False
    return True


def _resume(sink, log, request):
    return ResumeEngine(sink).resume(log, request)


def _names(section):
    return [row["name"] for row in CASES[section]]


def _row(section, name):
    (row,) = [r for r in CASES[section] if r["name"] == name]
    return row


@pytest.mark.parametrize("section,name", [
    (s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_receipts(section, name):
    row = _row(section, name)
    log, request = copy.deepcopy(row["log"]), copy.deepcopy(row["request"])
    survivors = list(log[:row["expect"]["resumed_count"]])
    receipt = _resume(SINKS[row["sink"]], log, request)
    assert receipt == row["expect"]
    assert request == row["request"]
    # commit removed EXACTLY the torn tail, keeping the prefix objects
    assert log == row["expect_log"]
    assert all(a is b for a, b in zip(log, survivors, strict=True))
    assert _wal_replays(log)
    # idempotent: resuming the resumed log discards nothing
    again = _resume(quarantine_tail, log,
                    {"checkpoint_sequence": len(log)})
    assert again["discarded_count"] == 0
    assert again["head"] == receipt["head"]
    assert again["state_id"] == receipt["state_id"]
    assert log == row["expect_log"]


def test_boundary_covers_the_edges():
    rows = {r["name"]: r for r in CASES["boundary"]}
    exps = [r["expect"] for r in rows.values()]
    assert any(e["head"] == "wal0:" + "0" * 64 and e["discarded_count"] == 0
               for e in exps)
    assert any(r["request"]["checkpoint_sequence"] == 0
               and r["expect"]["discarded_count"] == len(r["log"]) > 0
               for r in rows.values())
    # a later valid-looking entry after the first invalid one is dropped
    assert any(any(_wal_replays(r["log"][:r["expect"]["resumed_count"]]
                                + [e]) for e in r["log"][
                   r["expect"]["resumed_count"] + 1:])
               for r in rows.values())


def _apply_repair(row):
    (locus, value), = row["minimal_repair"].items()
    parts = {"log": row["log"], "request": row["request"],
             "sink": row["sink"]}
    parts[locus] = value
    return parts


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    row = _row("malformed", name)
    log, request = copy.deepcopy(row["log"]), copy.deepcopy(row["request"])
    with pytest.raises(ResumeError) as err:
        _resume(SINKS[row["sink"]], log, request)
    assert err.value.failure_class == row["expect_failure"]
    assert err.value.code == FAILURE_MAPPING[row["expect_failure"]]
    assert err.value.code in ERROR_ENUM
    assert log == row["log"] and request == row["request"]


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_is_discriminating(name):
    row = _row("malformed", name)
    parts = _apply_repair(row)
    receipt = _resume(SINKS[parts["sink"]], copy.deepcopy(parts["log"]),
                      copy.deepcopy(parts["request"]))
    _validate_receipt(receipt, name)
    assert receipt == _resume(quarantine_tail, copy.deepcopy(parts["log"]),
                              copy.deepcopy(parts["request"]))


@pytest.mark.parametrize("name", [
    n for n in _names("malformed")
    if _row("malformed", n)["expect_failure"] == "corrupt_source"
    and "log" in _row("malformed", n)["minimal_repair"]])
def test_damaged_durable_rows_fail_the_linked_wal(name):
    row = _row("malformed", name)
    cp = row["request"]["checkpoint_sequence"]
    assert not _wal_replays(row["log"][:cp])
    assert _wal_replays(row["minimal_repair"]["log"][:cp])


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(CASES["malformed"][11])
    row["sink"] = "canonical"
    with pytest.raises(pytest.fail.Exception), pytest.raises(ResumeError):
        _resume(SINKS[row["sink"]], copy.deepcopy(row["log"]),
                copy.deepcopy(row["request"]))


def test_tampered_pinned_receipt_is_detected():
    row = copy.deepcopy(CASES["happy"][1])
    row["expect"]["discarded_count"] += 1
    receipt = _resume(quarantine_tail, copy.deepcopy(row["log"]),
                      copy.deepcopy(row["request"]))
    assert receipt != row["expect"]


# -- rollback ----------------------------------------------------------------


class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _deep_ids(obj, out):
    out.append((_Pin(obj), type(obj)))
    if type(obj) is dict:
        for value in obj.values():
            _deep_ids(value, out)
    elif type(obj) is list:
        for value in obj:
            _deep_ids(value, out)
    return out


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    log, request = copy.deepcopy(row["log"]), copy.deepcopy(row["request"])
    before_ids = _deep_ids(log, []) + _deep_ids(request, [])
    hostile = _rollback_sink(row["rejected_sink"], log, request)
    with pytest.raises(ResumeError) as err:
        _resume(hostile, log, request)
    assert err.value.failure_class == row["expect_failure"]
    # bit-identical AND reference-identical restoration
    assert log == row["log"] and request == row["request"]
    assert _deep_ids(log, []) + _deep_ids(request, []) == before_ids
    # the valid follow-up on the very same objects commits as pinned
    survivors = list(log[:row["expect"]["resumed_count"]])
    assert _resume(quarantine_tail, log, request) == row["expect"]
    assert log == row["expect_log"]
    assert all(a is b for a, b in zip(log, survivors, strict=True))


def test_rollback_sinks_are_all_exercised():
    assert {r["rejected_sink"] for r in CASES["rollback"]} == set(
        ROLLBACK_SINKS)



def _bn(c, section, name):
    (row,) = [r for r in c[section] if r["name"] == name]
    return row


def _swap(c, section, a, b, *keys):
    x, y = _bn(c, section, a), _bn(c, section, b)
    for key in keys:
        x[key], y[key] = y[key], x[key]


def _substitute_payload(c, name):
    donor = _bn(c, "happy", "torn_fragment_discarded")
    row = _bn(c, "boundary", name)
    row["log"] = copy.deepcopy(donor["log"])


def _swap_happy_logs_regenerated(c):
    """Swap the two torn happy logs and REGENERATE their pins from the
    engine, so only the scenario semantics can tell them apart."""
    _swap(c, "happy", "torn_fragment_discarded",
          "torn_broken_chain_discarded", "log")
    for name in ("torn_fragment_discarded", "torn_broken_chain_discarded"):
        row = _bn(c, "happy", name)
        log = copy.deepcopy(row["log"])
        row["expect"] = _resume(SINKS[row["sink"]], log,
                                copy.deepcopy(row["request"]))
        row["expect_log"] = log


_CLOSURE_MUTANTS = {
    "happy_logs_swapped_regenerated": _swap_happy_logs_regenerated,
    "swap_boundary_edge_names": lambda c: _swap(
        c, "boundary", "tail_depth_at_limit", "tail_int_at_digit_limit",
        "name"),
    "swap_happy_names": lambda c: _swap(
        c, "happy", "torn_fragment_discarded",
        "torn_broken_chain_discarded", "name"),
    "swap_tail_log_and_repair": lambda c: _swap(
        c, "malformed", "tail_too_deep", "tail_int_past_digit_bound",
        "log", "minimal_repair"),
    "drop_checkpoint_string": lambda c: c["malformed"].remove(
        _bn(c, "malformed", "checkpoint_string")),
    "duplicate_sink_raises": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "sink_raises"))),
    "bad_grammar_uses_raising_sink": lambda c: _bn(
        c, "malformed", "sink_bad_grammar").update(sink="raising"),
    "depth_edge_payload_shallow": lambda c: _substitute_payload(
        c, "tail_depth_at_limit"),
    "digit_edge_payload_shallow": lambda c: _substitute_payload(
        c, "tail_int_at_digit_limit"),
}


@pytest.mark.parametrize("mutant", list(_CLOSURE_MUTANTS))
@pytest.mark.parametrize("digests", [True, False],
                         ids=["with-digests", "closure-only"])
def test_closure_kills_substitution_mutants(mutant, digests, monkeypatch):
    """Every A-H style substitution is caught - and caught by the
    semantic closure ALONE, not only by the row digest table."""
    cases = copy.deepcopy(CASES)
    _CLOSURE_MUTANTS[mutant](cases)
    if not digests:
        monkeypatch.setattr(sys.modules[__name__], "_row_digest",
                            lambda row: ROW_DIGESTS.get(
                                f"{_section_of(cases, row)}:{row['name']}"))
    with pytest.raises((AssertionError, KeyError, ValueError)):
        _validate_structure(cases)
        _validate_closure(cases)


def _section_of(cases, row):
    for section in SECTIONS:
        if any(r is row for r in cases[section]):
            return section
    raise AssertionError("row not in fixture")




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _deep_ids(value, [])
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _deep_ids(value, []) != before
