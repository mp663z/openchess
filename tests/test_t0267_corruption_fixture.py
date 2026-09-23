"""T0267: corruption conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0266 corruption contract. The cases execute against the
contract-derived reference in
tests.test_t0266_corruption_contract (itself fully derived from
data/contracts/corruption.yaml plus the linked WAL contract) -
nothing is re-implemented here. Pinned scan receipts in the
fixture were computed from that reference at authoring time, so
any contract or derivation drift breaks this battery. Every
malformed case is discriminating (repairing ONLY its declared
defect locus makes the case valid) and rollback cases prove a
rejected scan leaves the exact supplied log and request
bit-identical - same objects, same exact types, same alias
topology, same bytes - before the valid follow-up salvage
commits.

The identity-exact atomicity machinery (_snapshot_work /
_assert_work_atomic) is the T0240 fixture's, imported - never
restated.

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
from tests.test_t0240_rollback_fixture import (  # noqa: E402
    _assert_work_atomic,
    _encoded_bytes,
    _graph_signature,
    _snapshot_work,
)
from tests.test_t0266_corruption_contract import (  # noqa: E402
    CorruptionEngine,
    CorruptionError,
    _fail,
    _OutOfDomain,
    canonical_encoding,
    quarantine_suffix,
    verified_prefix,
)
from tools.corruption_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_BITS,
    RECORD,
    VERDICTS,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "corruption"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
_SCAN_RE = re.compile(_CC["identifiers"]["scan_id"]["grammar"])
_TOKEN_RE = re.compile(
    _CC["identifiers"]["quarantine_token"]["grammar"])
_HEAD_RE = re.compile(
    _CC["identifiers"]["verified_head"]["grammar"])
SCAN_FIELDS = list(RECORD["fields"])


def _raising_sink(suffix):
    raise ValueError("untrusted sink failure")


def _keyboard_interrupt_sink(suffix):
    raise KeyboardInterrupt("untrusted sink interrupt")


def _non_str_sink(suffix):
    return []


def _bad_grammar_sink(suffix):
    return "qrn1:zz"


def _unbound_token_sink(suffix):
    """Grammar-valid but NOT bound to the local suffix
    derivation."""
    return "qrn1:" + "f" * 64


def _surrogate_sink(suffix):
    return "\ud800"


ORACLES = {"honest": quarantine_suffix,
           "raising": _raising_sink,
           "raising_keyboard_interrupt": _keyboard_interrupt_sink,
           "non_str_output": _non_str_sink,
           "bad_grammar": _bad_grammar_sink,
           "unbound_token": _unbound_token_sink,
           "lone_surrogate": _surrogate_sink}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
SECTIONS = ("happy", "boundary", "malformed", "rollback")
HAPPY_KEYS = {"name", "kind", "oracle", "log", "request",
              "expect"}
MALFORMED_KEYS = {"name", "kind", "oracle", "log", "request",
                  "expect_failure", "defect", "scenario",
                  "minimal_repair"}
ROLLBACK_KEYS = {"name", "kind", "oracle", "log", "request",
                 "expect_failure", "then_oracle", "then_log",
                 "then_request", "expect"}
REPAIR_FORMS = {"set_request_field", "replace_request",
                "replace_log", "set_oracle"}

# -- the closed scenario manifests -------------------------------------------
# Independently pinned LITERAL maps: every fixture section's
# actual {name: metadata} map must equal its manifest EXACTLY -
# no missing, substituted, duplicated, renamed or extra rows -
# and every advertised semantic shape is asserted over the
# executable data before execution.
MCR = "malformed_corruption_record"
EL = "excessive_loss"
DQ = "divergent_quarantine"

# happy/boundary: name -> (oracle, verdict, verified_count,
# quarantined_count)
HAPPY_MANIFEST = {
    "clean-three-entry-log": ("honest", "clean", 3, 0),
    "salvage-tampered-tail-entry": ("honest", "salvaged", 2, 1),
    "salvage-torn-write-mid-log": ("honest", "salvaged", 1, 2),
    "salvage-garbage-appended": ("honest", "salvaged", 2, 1),
}
BOUNDARY_MANIFEST = {
    "clean-empty-log": ("honest", "clean", 0, 0),
    "salvage-to-genesis": ("honest", "salvaged", 0, 3),
    "loss-exactly-at-bound": ("honest", "salvaged", 1, 2),
    "int-at-range-edge-in-suffix": ("honest", "salvaged", 2, 1),
    "depth-at-limit-in-suffix": ("honest", "salvaged", 2, 1),
}
# boundary rows additionally REALIZE their named edge over the
# executable data (see _assert_boundary_edge)
# malformed: name -> (failure class, oracle, repair form,
# pinned defect description, CLOSED SCENARIO TAG)
MALFORMED_MANIFEST = {
    "log-not-a-list": (MCR, "honest", "replace_log",
        "the source log is a dict, not a list",
        "non-list-source-log"),
    "request-missing-field": (MCR, "honest", "replace_request",
        "the request is missing the max_loss field",
        "missing-request-field"),
    "request-extra-field": (MCR, "honest", "replace_request",
        "the request carries a stray field",
        "extra-request-field"),
    "max-loss-non-int": (MCR, "honest", "set_request_field",
        "max_loss is a string, not an int", "non-int-max-loss"),
    "max-loss-bool": (MCR, "honest", "set_request_field",
        "max_loss is a bool, not an exact int", "bool-max-loss"),
    "max-loss-negative": (MCR, "honest", "set_request_field",
        "max_loss is below zero", "negative-max-loss"),
    "log-float-leaf": (MCR, "honest", "replace_log",
        "a log leaf is a float, outside the scan domain",
        "float-leaf"),
    "log-int-over-range": (MCR, "honest", "replace_log",
        "a log int is wider than the pinned 256-bit range",
        "int-over-range"),
    "log-over-depth": (MCR, "honest", "replace_log",
        "a log value nests deeper than the pinned depth",
        "over-depth"),
    "loss-one-over-bound": (EL, "honest", "set_request_field",
        "the corrupt suffix is one entry longer than max_loss",
        "excessive-loss-by-one"),
    "loss-zero-bound-on-corrupt-log": (EL, "honest",
        "set_request_field",
        "max_loss is zero but the log has a corrupt suffix",
        "excessive-loss-zero-bound"),
    "sink-raises": (DQ, "raising", "set_oracle",
        "the sink raises instead of returning a token",
        "sink-raises"),
    "sink-raises-keyboard-interrupt": (DQ,
        "raising_keyboard_interrupt", "set_oracle",
        "the sink raises KeyboardInterrupt, a BaseException",
        "sink-raises-base-exception"),
    "sink-non-str-output": (DQ, "non_str_output", "set_oracle",
        "the sink returns a list, not a string",
        "sink-non-str-output"),
    "sink-bad-grammar": (DQ, "bad_grammar", "set_oracle",
        "the sink token violates the pinned grammar",
        "sink-bad-grammar"),
    "sink-unbound-token": (DQ, "unbound_token", "set_oracle",
        "the sink token is grammar-valid but not bound to the "
        "local suffix derivation", "sink-unbound-token"),
    "sink-lone-surrogate": (DQ, "lone_surrogate", "set_oracle",
        "the sink token is not UTF-8 encodable",
        "sink-lone-surrogate"),
}
# rollback: name -> (failure class, initial oracle, follow-up
# oracle, follow-up verified_count, follow-up quarantined_count,
# then_log length, CLOSED then-relation to the rejected input)
ROLLBACK_MANIFEST = {
    "rejected-salvage-raising-sink-then-valid-salvage": (
        DQ, "raising", "honest", 2, 1, 3, "same-inputs"),
    "rejected-salvage-keyboard-interrupt-sink-then-valid-salvage": (
        DQ, "raising_keyboard_interrupt", "honest", 2, 2, 4,
        "same-inputs"),
    "rejected-salvage-excessive-loss-then-valid-salvage": (
        EL, "honest", "honest", 1, 2, 3, "raised-max-loss"),
    "rejected-scan-out-of-domain-then-valid-salvage": (
        MCR, "honest", "honest", 2, 1, 3, "leaf-repaired-log"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

# content binding: canonical sha256 of every whole row (payload,
# sink, repair, defect, pinned receipt). Any edit to a row must
# update this table in the same change.
ROW_DIGESTS = {
    "happy:clean-three-entry-log":
        "8db26a840d1c24ecbc737c5d746a8f9780f598635d30b8bd0708ab0ba198bb9f",
    "happy:salvage-tampered-tail-entry":
        "1bae79c089da04f3a6833424ebe0562d2440dc6fdb5f2fc30187d57fdd2d5350",
    "happy:salvage-torn-write-mid-log":
        "a5a989dc1e1bcdaa849eba9c2f44037eb7a4ad8c3372f00cb080879949f2cde8",
    "happy:salvage-garbage-appended":
        "6ec5355ef48098d4e74bcc790143425c2447814bc8cb514a8cd0fdd337e225a5",
    "boundary:clean-empty-log":
        "4e1aab3edb0641a139374f8184b179e21274c988aec7b0c325125c27c905b231",
    "boundary:salvage-to-genesis":
        "7a2f1bf89a0e010b3041a45c7a36c39735d0cefca246643345cd5892798e5850",
    "boundary:loss-exactly-at-bound":
        "64efb9bca3ef9d57ba401a7796580d2f5db641691efb88091feaf18ce56588d9",
    "boundary:int-at-range-edge-in-suffix":
        "8bca94b6e707e1bbce10e37e1845601b83d7f4abde50f0f7a5382eec1364474c",
    "boundary:depth-at-limit-in-suffix":
        "093a412583988bec47158587b97c3650910307c917acdc2c96d1beb4fb391073",
    "malformed:log-not-a-list":
        "c92d1ca1f6af4aa2b4a2b049e5511378c9c905ea406dc7d4de6c6ab2cff253b2",
    "malformed:request-missing-field":
        "7a18728ef03c2e073c23afa053806f7630c2b4bad3e6ca1d5de389cac382c0d1",
    "malformed:request-extra-field":
        "e544667d844bfda331a9ae617bd2e9125223163ab0ef69ad7025f1624c787289",
    "malformed:max-loss-non-int":
        "3523a5e90299c4eeb1145309b864ed25387304c7937ba641786155c6323bc7fa",
    "malformed:max-loss-bool":
        "3cd9a191bf34db886f1b73f29c8f43a61dc2fefe75d6e2f79473e30430367c39",
    "malformed:max-loss-negative":
        "72534e522a0e708351221ff64bf7f1f9d2610445a21812a676695bd5b854b5a0",
    "malformed:log-float-leaf":
        "30ad4c5ff9c65e74192340bf65f12f3dc02c994f50c27f328b01e10e093404dd",
    "malformed:log-int-over-range":
        "3d823bbef8edcaedde245be2935c4d17df8bad0e7e7385af02064561fd469a40",
    "malformed:log-over-depth":
        "c305391ab373ddcb00b51725086170e17feb880b5dbc528ade7f512240a6fef3",
    "malformed:loss-one-over-bound":
        "a6a9671b8e902b03415e1fc2b142b02debcc92168d3c1da9a81fe701e5658886",
    "malformed:loss-zero-bound-on-corrupt-log":
        "d923e1a0f0ce3acbec06ca073c6b6992e7d218b0fca7b95c80fd09abbdfff1e1",
    "malformed:sink-raises":
        "46521f3656d91e9bcafeedd3cce2cf8444c6b4637ca9af8fc420420852e91fe0",
    "malformed:sink-raises-keyboard-interrupt":
        "3309518d1d25003b7ef38e3d333b16bf265542129187bde66e5f27e84c83e31c",
    "malformed:sink-non-str-output":
        "aa844ae25e8f9b258f6684b20ad586e9de2b57114258693f8f06640e8aba34a0",
    "malformed:sink-bad-grammar":
        "13ce01c6821d301c9a1c9b237bd9bbf2d3c2b5640f1631e6d3dc188275e06308",
    "malformed:sink-unbound-token":
        "d50aeb376a9bdc3a6db5c28dedd8509d909a28a63d4c88b70d827e6aed7c14f8",
    "malformed:sink-lone-surrogate":
        "e9f8ffeece6ac05ed3ccf2bc4a11e70590cad9b83576441820e102ce68fc5550",
    "rollback:rejected-salvage-raising-sink-then-valid-salvage":
        "b892f994d4e6ff2b9d6ca87e85ed99e264e579b04108c3e3c64dd108ef0a6701",
    "rollback:rejected-salvage-keyboard-interrupt-sink-then-valid-salvage":
        "c21839ce5f6e9cc269243ec7a9fb376bb62aed2fc4502f6048fef6ca974fa3d3",
    "rollback:rejected-salvage-excessive-loss-then-valid-salvage":
        "0a7f22e0fb74a26ea6d31c95cacf9860d8262a9d6ef938723e1942af38a0496f",
    "rollback:rejected-scan-out-of-domain-then-valid-salvage":
        "888da62928fb1df17743e37f8259db8d644bf674fd4aec3b5217e1133b080ded",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _check_row_digest(section, case):
    label = f"{section}:{case['name']}"
    assert ROW_DIGESTS.get(label) == _row_digest(case), label


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    return next(case for case in CASES[section]
                if case["name"] == name)


# -- recursive shape validation ------------------------------------------------


def _in_domain(value):
    try:
        canonical_encoding(value)
    except _OutOfDomain:
        return False
    return True


def _validate_log_shape(log, label):
    """A shape-valid log is an exact list inside the closed scan
    domain (corrupt entries are legal DATA; out-of-domain values
    are not)."""
    assert type(log) is list, label
    assert _in_domain(log), label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert set(request) == {"max_loss"}, label
    assert type(request["max_loss"]) is int, label
    assert request["max_loss"] >= 0, label


def _validate_receipt_shape(receipt, label):
    assert type(receipt) is dict, label
    assert set(receipt) == set(SCAN_FIELDS), label
    assert type(receipt["scan_id"]) is str, label
    assert _SCAN_RE.fullmatch(receipt["scan_id"]), label
    assert receipt["verdict"] in VERDICTS, label
    assert type(receipt["verdict"]) is str, label
    assert type(receipt["verified_head"]) is str, label
    assert _HEAD_RE.fullmatch(receipt["verified_head"]), label
    for key in ("verified_count", "quarantined_count"):
        assert type(receipt[key]) is int, label
        assert receipt[key] >= 0, label
    if receipt["verdict"] == "clean":
        assert receipt["quarantine_token"] is None, label
        assert receipt["quarantined_count"] == 0, label
    else:
        assert type(receipt["quarantine_token"]) is str, label
        assert _TOKEN_RE.fullmatch(
            receipt["quarantine_token"]), label
        assert receipt["quarantined_count"] > 0, label


def _repaired(case):
    """Apply the declarative minimal repair: it touches ONLY the
    declared locus, everything else byte-identical."""
    rep = case["minimal_repair"]
    assert set(rep) <= REPAIR_FORMS
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure", "scenario",
                        "minimal_repair")}
    if "set_request_field" in rep:
        form = rep["set_request_field"]
        out["request"][form["field"]] = form["value"]
    elif "replace_request" in rep:
        out["request"] = copy.deepcopy(
            rep["replace_request"]["request"])
    elif "replace_log" in rep:
        out["log"] = copy.deepcopy(rep["replace_log"]["log"])
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of four closed
    forms, never mixed, every nested object key-exact and
    scalar-typed:
    - set_request_field: exactly {"field", "value"}; field is
      max_loss; value an exact int >= 0;
    - replace_request: exactly {"request"}; a shape-valid scan
      request;
    - replace_log: exactly {"log"}; a shape-valid in-domain log;
    - set_oracle: exactly {"oracle"}; a declared sink selector."""
    rep = case["minimal_repair"]
    assert type(rep) is dict, case["name"]
    assert len(rep) == 1 and set(rep) <= REPAIR_FORMS, \
        case["name"]
    form = next(iter(rep.values()))
    assert type(form) is dict, case["name"]
    if "set_request_field" in rep:
        assert set(form) == {"field", "value"}, case["name"]
        assert form["field"] == "max_loss", case["name"]
        assert type(form["value"]) is int, case["name"]
        assert form["value"] >= 0, case["name"]
    elif "replace_request" in rep:
        assert set(form) == {"request"}, case["name"]
        _validate_request_shape(form["request"], case["name"])
    elif "replace_log" in rep:
        assert set(form) == {"log"}, case["name"]
        _validate_log_shape(form["log"], case["name"])
    else:
        assert set(form) == {"oracle"}, case["name"]
        assert type(form["oracle"]) is str, case["name"]
        assert form["oracle"] in ORACLES, case["name"]


# -- semantic predicates over executable data ----------------------------------


def _scan(oracle, log, request):
    return CorruptionEngine(ORACLES[oracle]).scan(
        copy.deepcopy(log), copy.deepcopy(request))


def _scan_succeeds(oracle, log, request):
    """The reference engine accepts the scan end to end under
    the given sink."""
    try:
        _scan(oracle, log, request)
    except CorruptionError:
        return False
    return True


def _prefix(log):
    """(verified_count, quarantined_count) of an in-domain log
    under the linked WAL."""
    frozen, _ = canonical_encoding(log)
    count, _head = verified_prefix(frozen)
    return count, len(frozen) - count


def _wal_verifies(log):
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return False
    return True


def _leaves(node, path=()):
    """Every (path, leaf) of a JSON tree, exact containers only."""
    if type(node) is dict:
        for key, value in node.items():
            yield from _leaves(value, path + (("k", key),))
    elif type(node) is list:
        for index, value in enumerate(node):
            yield from _leaves(value, path + (("i", index),))
    else:
        yield path, node


def _depth(node):
    if type(node) is dict:
        return 1 + max((_depth(v) for v in node.values()),
                       default=0)
    if type(node) is list:
        return 1 + max((_depth(v) for v in node), default=0)
    return 1


def _diff_fields(original, repaired, label):
    assert set(original) == set(repaired), label
    return {key for key in original
            if type(original[key]) is not type(repaired[key])
            or original[key] != repaired[key]}


def _entry_diff(log, rep_log, label):
    """Indices of the entries that differ between two equal-
    length logs."""
    assert type(log) is list and type(rep_log) is list, label
    assert len(log) == len(rep_log), label
    return {i for i, (a, b) in enumerate(zip(log, rep_log,
                                              strict=True))
            if a != b}


def _single_leaf_swap(log, rep_log, label):
    """The two logs differ at EXACTLY one leaf path (same tree
    shape); returns (original leaf, repaired leaf)."""
    a = dict(_leaves(log))
    b = dict(_leaves(rep_log))
    assert set(a) == set(b), label
    diff = [p for p in a if type(a[p]) is not type(b[p])
            or a[p] != b[p]]
    assert len(diff) == 1, label
    return a[diff[0]], b[diff[0]]


# -- closed scenario tags with semantic defect-locus assertions --------------
# The defect prose stays as documentation, but it is NOT the
# proof: each malformed row carries a closed machine scenario
# tag, and _assert_malformed_scenario verifies over the ORIGINAL
# executable data that (a) the data realizes exactly the defect
# the tag names, and (b) the minimal repair changes exactly that
# locus and no other semantic locus. Every check fails CLOSED.

_SINK_TAG_SELECTOR = {
    "sink-raises": "raising",
    "sink-raises-base-exception": "raising_keyboard_interrupt",
    "sink-non-str-output": "non_str_output",
    "sink-bad-grammar": "bad_grammar",
    "sink-unbound-token": "unbound_token",
    "sink-lone-surrogate": "lone_surrogate"}
# sink-tag rows are pairwise discriminated by (log length,
# verified count) - a substituted sink row can never satisfy the
# wrong cardinality
_SINK_CARDINALITY = {
    "sink-raises": (3, 2),
    "sink-raises-base-exception": (4, 3),
    "sink-non-str-output": (3, 1),
    "sink-bad-grammar": (2, 1),
    "sink-unbound-token": (4, 2),
    "sink-lone-surrogate": (1, 0)}


def _check_sink_scenario(case, tag, rep):
    name = case["name"]
    log = case["log"]
    request = case["request"]
    assert case["oracle"] == _SINK_TAG_SELECTOR[tag], name
    _validate_log_shape(log, name)
    _validate_request_shape(request, name)
    count, lost = _prefix(log)
    assert (len(log), count) == _SINK_CARDINALITY[tag], name
    # a REAL salvage within the loss bound: the ONLY defect is
    # the sink itself - the honest sink succeeds
    assert 0 < lost <= request["max_loss"], name
    assert _scan_succeeds("honest", log, request), name
    assert case["minimal_repair"]["set_oracle"]["oracle"] == \
        "honest", name
    assert rep["log"] == log and rep["request"] == request, name
    suffix = copy.deepcopy(log[count:])
    if tag == "sink-raises":
        try:
            ORACLES[case["oracle"]](suffix)
        except ValueError:
            return
        raise AssertionError(name)
    if tag == "sink-raises-base-exception":
        try:
            ORACLES[case["oracle"]](suffix)
        except Exception:
            raise AssertionError(name) from None
        except BaseException:
            return
        raise AssertionError(name)
    out = ORACLES[case["oracle"]](suffix)
    if tag == "sink-non-str-output":
        assert type(out) is not str, name
    elif tag == "sink-bad-grammar":
        assert type(out) is str, name
        assert _TOKEN_RE.fullmatch(out) is None, name
    elif tag == "sink-unbound-token":
        assert type(out) is str, name
        assert _TOKEN_RE.fullmatch(out) is not None, name
        assert out != quarantine_suffix(log[count:]), name
    elif tag == "sink-lone-surrogate":
        assert type(out) is str, name
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            return
        raise AssertionError(name)


def _check_malformed_scenario(case, tag):
    name = case["name"]
    rep = _repaired(case)
    if tag in _SINK_TAG_SELECTOR:
        _check_sink_scenario(case, tag, rep)
        return
    log = case["log"]
    request = case["request"]
    rep_log = rep["log"]
    rep_req = rep["request"]
    assert rep["oracle"] == case["oracle"] == "honest", name
    if tag == "non-list-source-log":
        assert type(log) is not list, name
        assert "replace_log" in case["minimal_repair"], name
        _validate_log_shape(rep_log, name)
        assert rep_req == request, name
        _validate_request_shape(request, name)
    elif tag == "missing-request-field":
        assert type(request) is dict, name
        assert "max_loss" not in request, name
        assert rep_log == log, name
        _validate_request_shape(rep_req, name)
    elif tag == "extra-request-field":
        assert type(request) is dict, name
        assert "max_loss" in request, name
        assert set(request) != {"max_loss"}, name
        assert rep_req == {"max_loss": request["max_loss"]}, name
        assert rep_log == log, name
    elif tag in ("non-int-max-loss", "bool-max-loss",
                 "negative-max-loss"):
        value = request["max_loss"]
        if tag == "non-int-max-loss":
            assert type(value) is str, name
        elif tag == "bool-max-loss":
            assert type(value) is bool, name
        else:
            assert type(value) is int and value < 0, name
        assert _diff_fields(request, rep_req, name) == \
            {"max_loss"}, name
        assert rep_log == log, name
    elif tag in ("float-leaf", "int-over-range"):
        assert type(log) is list, name
        assert not _in_domain(log), name
        assert rep_req == request, name
        old, new = _single_leaf_swap(log, rep_log, name)
        if tag == "float-leaf":
            assert type(old) is float, name
            assert type(new) is int and new == old, name
        else:
            assert type(old) is int, name
            assert old.bit_length() > MAX_INT_BITS, name
            assert type(new) is int, name
            assert new.bit_length() <= MAX_INT_BITS, name
    elif tag == "over-depth":
        assert type(log) is list, name
        assert not _in_domain(log), name
        assert _depth(log) == MAX_DEPTH + 1, name
        assert _depth(rep_log) == MAX_DEPTH, name
        assert len(_entry_diff(log, rep_log, name)) == 1, name
        assert rep_req == request, name
    elif tag in ("excessive-loss-by-one",
                 "excessive-loss-zero-bound"):
        _validate_log_shape(log, name)
        _validate_request_shape(request, name)
        count, lost = _prefix(log)
        max_loss = request["max_loss"]
        if tag == "excessive-loss-by-one":
            assert max_loss > 0 and lost == max_loss + 1, name
        else:
            assert max_loss == 0 and lost > 1, name
        assert _diff_fields(request, rep_req, name) == \
            {"max_loss"}, name
        assert rep_req["max_loss"] == lost, name
        assert rep_log == log, name
    else:
        raise AssertionError(
            f"{name}: unknown scenario tag {tag!r}")
    # every non-sink repair yields a fully valid salvage
    assert _scan_succeeds(rep["oracle"], rep_log, rep_req), name


def _assert_malformed_scenario(case, tag):
    """Fail-closed wrapper: scenario checks raise AssertionError
    on EVERY mismatch or unexpected shape - any raw exception,
    BaseException included, is converted, never allowed to
    escape as itself."""
    try:
        _check_malformed_scenario(case, tag)
    except AssertionError:
        raise
    except BaseException as exc:
        raise AssertionError(
            f"{case.get('name', '?')}: scenario {tag!r} check "
            f"raised unexpected {type(exc).__name__}") from None


def _assert_cardinalities(case, want):
    """The advertised verdict and counts are REALLY present, and
    the pinned receipt re-derives from the reference engine - a
    substituted row can never satisfy the wrong cardinality."""
    name = case["name"]
    oracle, verdict, count, lost = want
    assert case["oracle"] == oracle, name
    assert case["expect"]["verdict"] == verdict, name
    assert case["expect"]["verified_count"] == count, name
    assert case["expect"]["quarantined_count"] == lost, name
    assert (count, lost) == _prefix(case["log"]), name
    assert len(case["log"]) == count + lost, name
    derived = _scan(oracle, case["log"], case["request"])
    assert derived == case["expect"], name


def _assert_happy_edge(case):
    """Each happy row REALIZES the corruption its name claims, over
    the ORIGINAL data - rows sharing a (verdict, counts) tuple can
    never trade payloads, even with a regenerated receipt."""
    name = case["name"]
    log = case["log"]
    exp = case["expect"]
    assert type(log) is list, name
    if name == "clean-three-entry-log":
        assert len(log) == 3, name
        assert _wal_verifies(log), name
        assert exp["quarantined_count"] == 0, name
    elif name == "salvage-tampered-tail-entry":
        assert len(log) == 3, name
        assert all(type(e) is dict for e in log), name
        assert set(log[2]) == set(log[0]), name
        assert _wal_verifies(log[:2]), name
        assert not _wal_verifies(log), name
        # the tail entry is a real entry whose ONLY defect is its
        # entry id: honestly re-appending its op+payload onto the
        # verified prefix differs from it at exactly that field
        honest = WalEngine(canonical_payload).append(
            copy.deepcopy(log[:2]),
            {"op": log[2]["op"],
             "payload": copy.deepcopy(log[2]["payload"])})
        assert _diff_fields(log[2], honest, name) == \
            {"entry_id"}, name
    elif name == "salvage-torn-write-mid-log":
        assert len(log) == 3, name
        assert all(type(e) is dict for e in log), name
        assert _wal_verifies(log[:1]), name
        assert set(log[1]) < set(log[0]), name
        assert set(log[2]) == set(log[0]), name
    elif name == "salvage-garbage-appended":
        assert len(log) == 3, name
        assert type(log[0]) is dict and type(log[1]) is dict, name
        assert type(log[2]) is not dict, name
        assert _wal_verifies(log[:2]), name
    else:
        raise AssertionError(f"{name}: no happy edge")


def _assert_boundary_edge(case):
    """Each boundary row REALIZES the edge its name claims."""
    name = case["name"]
    log = case["log"]
    exp = case["expect"]
    if name == "clean-empty-log":
        assert log == [] and exp["verified_head"] == \
            "wal0:" + "0" * 64, name
    elif name == "salvage-to-genesis":
        assert all(type(e) is dict for e in log), name
        assert log[0]["sequence"] != 1, name
        assert exp["verified_count"] == 0, name
        assert exp["verified_head"] == "wal0:" + "0" * 64, name
    elif name == "loss-exactly-at-bound":
        # the corruption is a CHAIN BREAK at entry 2 over full-
        # shape entries
        assert all(type(e) is dict and set(e) == set(log[0])
                   for e in log), name
        assert log[1]["prior_entry_id"] != log[0]["entry_id"], name
        assert exp["quarantined_count"] == \
            case["request"]["max_loss"], name
        over = {"max_loss": case["request"]["max_loss"] - 1}
        try:
            _scan("honest", log, over)
            raise AssertionError(name)
        except CorruptionError as exc:
            assert exc.failure_class == EL, name
    elif name == "int-at-range-edge-in-suffix":
        ints = [v for p, v in _leaves(log[exp["verified_count"]:])
                if type(v) is int]
        assert max((v.bit_length() for v in ints), default=0) == \
            MAX_INT_BITS, name
    elif name == "depth-at-limit-in-suffix":
        assert _depth(log) == MAX_DEPTH, name
    else:
        raise AssertionError(f"{name}: no boundary edge")


def _fail_closed(check, case):
    """Run a semantic edge check; ANY raw exception (BaseException
    included) becomes AssertionError, never escapes as itself."""
    try:
        check(case)
    except AssertionError:
        raise
    except BaseException as exc:
        raise AssertionError(
            f"{case.get('name', '?')}: {check.__name__} raised "
            f"{type(exc).__name__}") from None


def _validate_case(section, case):
    """ONE row's complete validation: key-exact shape, manifest
    metadata, semantic scenario realization and execution
    evidence. _validate_structure runs it for every row."""
    assert type(case) is dict, section
    name = case.get("name")
    assert type(name) is str, section
    manifest = MANIFESTS[section]
    assert name in manifest, name
    want = manifest[name]
    assert case.get("kind") == "corruption", name
    if section in ("happy", "boundary"):
        assert set(case) == HAPPY_KEYS, name
        assert type(case["oracle"]) is str, name
        assert case["oracle"] in ORACLES, name
        _validate_log_shape(case["log"], name)
        _validate_request_shape(case["request"], name)
        _validate_receipt_shape(case["expect"], name)
        _assert_cardinalities(case, want)
        _fail_closed(_assert_boundary_edge if section == "boundary"
                     else _assert_happy_edge, case)
    elif section == "malformed":
        assert set(case) == MALFORMED_KEYS, name
        failure, oracle, repair, defect, tag = want
        assert case["expect_failure"] == failure, name
        assert case["expect_failure"] in FAILURE_CLASSES, name
        assert case["oracle"] == oracle, name
        assert type(case["defect"]) is str, name
        assert case["defect"] == defect, name
        assert case["scenario"] == tag, name
        _validate_repair(case)
        assert set(case["minimal_repair"]) == {repair}, name
        # SEMANTIC PIN: the original executable data must REALIZE
        # the closed scenario tag, and REALLY fail with the
        # declared class
        _assert_malformed_scenario(case, tag)
        _assert_rejects(case["oracle"], case["log"],
                        case["request"], failure, name)
    else:
        assert set(case) == ROLLBACK_KEYS, name
        (failure, oracle, then_oracle, count, lost, then_len,
         relation) = want
        assert case["expect_failure"] == failure, name
        assert case["oracle"] == oracle, name
        assert case["then_oracle"] == then_oracle, name
        _validate_log_shape(case["then_log"], name)
        _validate_request_shape(case["then_request"], name)
        _validate_receipt_shape(case["expect"], name)
        assert case["expect"]["verdict"] == "salvaged", name
        _assert_rejects(case["oracle"], case["log"],
                        case["request"], failure, name)
        derived = _scan(then_oracle, case["then_log"],
                        case["then_request"])
        assert derived == case["expect"], name
        assert len(case["then_log"]) == then_len, name
        assert case["expect"]["verified_count"] == count, name
        assert case["expect"]["quarantined_count"] == lost, name
        assert (count, lost) == _prefix(case["then_log"]), name
        _assert_then_relation(case, relation)


def _assert_then_relation(case, relation):
    """The follow-up is bound to the REJECTED input by a closed
    relation - it can never be an unrelated valid salvage."""
    name = case["name"]
    log, req = case["log"], case["request"]
    then_log, then_req = case["then_log"], case["then_request"]
    if relation == "same-inputs":
        assert then_log == log and then_req == req, name
    elif relation == "raised-max-loss":
        assert then_log == log, name
        assert _diff_fields(req, then_req, name) == {"max_loss"}, \
            name
        assert then_req["max_loss"] > req["max_loss"], name
    elif relation == "leaf-repaired-log":
        assert then_req == req, name
        assert not _in_domain(log), name
        old, new = _single_leaf_swap(log, then_log, name)
        assert type(old) is float and type(new) is int, name
        assert new == old, name
    else:
        raise AssertionError(f"{name}: unknown relation")


def _validate_structure(cases):
    assert type(cases) is dict
    assert set(cases) == TOP_KEYS
    # the fixture-format version is pinned exactly
    assert type(cases["schema"]) is int, "schema must be an int"
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert type(cases["contract"]) is str
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == \
        _CC["versioning"]["base_path"]
    assert type(cases["notes"]) is str and cases["notes"]
    for section in SECTIONS:
        assert type(cases[section]) is list, section
        assert cases[section], f"{section} must be non-empty"
        for case in cases[section]:
            assert type(case) is dict, section
            assert type(case.get("name")) is str, section
    # section-level closure FIRST (cheap): exact name lists
    for section in SECTIONS:
        manifest = MANIFESTS[section]
        names = [case["name"] for case in cases[section]]
        assert len(names) == len(set(names)), (
            f"{section} has duplicate names")
        assert names == list(manifest), (
            f"{section} scenario set drifted: "
            f"missing={set(manifest) - set(names)} "
            f"extra={set(names) - set(manifest)}")
    # content binding: every row's canonical digest, key set
    # closed over the manifests
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    for section in SECTIONS:
        for case in cases[section]:
            _check_row_digest(section, case)
    # then every row's full semantic validation (independent of
    # the digests - see the closure-only mutant runs)
    for section in SECTIONS:
        for case in cases[section]:
            _validate_case(section, case)
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared == FAILURE_CLASSES


def _assert_rejects(oracle, log, request, failure, label):
    """The executable data REALLY fails with the declared class
    through the typed boundary - never a raw escape."""
    try:
        _scan(oracle, log, request)
    except CorruptionError as exc:
        assert exc.failure_class == failure, label
        return
    except BaseException as exc:
        raise AssertionError(
            f"{label}: raw {type(exc).__name__} escaped") from None
    raise AssertionError(f"{label}: scan accepted")


def test_fixture_structure():
    _validate_structure(CASES)


def test_fixture_schema_version_mutations_fail():
    for mutate in (
            lambda m: m.__setitem__("schema", 0),
            lambda m: m.__setitem__("schema", 2),
            lambda m: m.__setitem__("schema", "1"),
            lambda m: m.__setitem__("schema", True),
            lambda m: m.__setitem__("schema", 1.0),
            lambda m: m.__delitem__("schema")):
        m = copy.deepcopy(CASES)
        mutate(m)
        with pytest.raises(AssertionError):
            _validate_structure(m)


# -- execution ------------------------------------------------------------------


def _run_scan(oracle_name, log, request, expect):
    work_log = copy.deepcopy(log)
    work_req = copy.deepcopy(request)
    kept_ids = [id(entry) for entry in work_log]
    receipt = CorruptionEngine(ORACLES[oracle_name]).scan(
        work_log, work_req)
    assert receipt == expect
    _validate_receipt_shape(receipt, "execution")
    count = expect["verified_count"]
    # the commit removed EXACTLY the corrupt suffix, nothing
    # else, and kept the surviving entry OBJECTS
    assert work_log == log[:count]
    assert [id(entry) for entry in work_log] == kept_ids[:count]
    assert work_req == request
    assert _wal_verifies(work_log)
    # deterministic: fresh copies re-derive the same receipt
    assert _scan(oracle_name, log, request) == expect


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        _run_scan(case["oracle"], case["log"], case["request"],
                  case["expect"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        _run_scan(case["oracle"], case["log"], case["request"],
                  case["expect"])


def test_clean_rows_never_call_the_sink():
    for section in ("happy", "boundary"):
        for case in CASES[section]:
            calls = []

            def sink(suffix, calls=calls):
                calls.append(1)
                return quarantine_suffix(suffix)

            CorruptionEngine(sink).scan(
                copy.deepcopy(case["log"]),
                copy.deepcopy(case["request"]))
            want = 0 if case["expect"]["verdict"] == "clean" else 1
            assert len(calls) == want, case["name"]


def _exec_malformed(case):
    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req) \
        if type(work_log) is list and type(work_req) is dict \
        else None
    with pytest.raises(CorruptionError) as exc:
        CorruptionEngine(ORACLES[case["oracle"]]).scan(
            work_log, work_req)
    assert exc.value.failure_class == case["expect_failure"], \
        case["name"]
    assert exc.value.code == FAILURE_MAPPING[
        case["expect_failure"]], case["name"]
    assert exc.value.code in ERROR_ENUM, case["name"]
    if snap is not None:
        _assert_work_atomic(snap, work_log, work_req)


def _exec_repaired(case):
    rep = _repaired(case)
    receipt = _scan(rep["oracle"], rep["log"], rep["request"])
    _validate_receipt_shape(receipt, case["name"])
    assert receipt["verdict"] == "salvaged", case["name"]


@pytest.mark.parametrize("name", list(MALFORMED_MANIFEST))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    assert list(MALFORMED_MANIFEST) == _names("malformed")


def test_repairs_minimal_locus():
    for name in _names("malformed"):
        case = _case("malformed", name)
        rep = _repaired(case)
        form = set(case["minimal_repair"])
        if form != {"set_oracle"}:
            assert rep["oracle"] == case["oracle"], name
        if form != {"replace_log"}:
            assert rep["log"] == case["log"], name
        if not form & {"replace_request", "set_request_field"}:
            assert rep["request"] == case["request"], name


def test_collection_guard_detects_late_fixture_row():
    injected = copy.deepcopy(CASES)
    row = copy.deepcopy(injected["happy"][0])
    row["name"] = "late-injected-row"
    injected["happy"].append(row)
    with pytest.raises(AssertionError):
        _validate_structure(injected)
    assert set(_names("happy")) == set(HAPPY_MANIFEST)


def test_injected_valid_malformed_row_fails_rejection():
    """A malformed row silently swapped for its repaired (fully
    valid) form fails structure validation - the battery cannot
    pass on valid data."""
    for name in _names("malformed"):
        m = copy.deepcopy(CASES)
        index = _names("malformed").index(name)
        rep = _repaired(m["malformed"][index])
        m["malformed"][index].update(
            {k: rep[k] for k in ("oracle", "log", "request")})
        with pytest.raises(AssertionError):
            _validate_case("malformed", m["malformed"][index])


def _repair_mutations():
    def bad_form_name(c):
        c["minimal_repair"] = {"set_field": {
            "field": "max_loss", "value": 2}}

    def two_forms(c):
        c["minimal_repair"] = {
            "set_request_field": {"field": "max_loss", "value": 2},
            "set_oracle": {"oracle": "honest"}}

    def missing_value(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "max_loss"}}

    def bad_field(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "bogus", "value": 2}}

    def bad_value_type(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "max_loss", "value": "2"}}

    def bool_value(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "max_loss", "value": True}}

    def negative_value(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "max_loss", "value": -1}}

    def bad_oracle(c):
        c["minimal_repair"] = {"set_oracle": {"oracle": "bogus"}}

    def non_dict_form(c):
        c["minimal_repair"] = {"set_oracle": "honest"}

    def replace_log_extra_key(c):
        c["minimal_repair"] = {"replace_log": {
            "log": [], "extra": True}}

    def replace_log_out_of_domain(c):
        c["minimal_repair"] = {"replace_log": {"log": [1.5]}}

    def replace_request_bad_shape(c):
        c["minimal_repair"] = {"replace_request": {
            "request": {"max_loss": "2"}}}

    return [bad_form_name, two_forms, missing_value, bad_field,
            bad_value_type, bool_value, negative_value, bad_oracle,
            non_dict_form, replace_log_extra_key,
            replace_log_out_of_domain, replace_request_bad_shape]


def test_repair_form_mutations_fail_structure():
    index = _names("malformed").index("max-loss-non-int")
    for mutate in _repair_mutations():
        m = copy.deepcopy(CASES)
        mutate(m["malformed"][index])
        with pytest.raises(AssertionError):
            _validate_case("malformed", m["malformed"][index])


# -- rollback: identity-exact input atomicity -----------------------------------


def test_rollback():
    """A rejected scan leaves the EXACT objects handed to it
    identity-exact (named working inputs, snapshotted, passed by
    identity, compared after the call), and the valid follow-up
    salvage returns the pinned receipt and commits exactly the
    verified prefix."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        work_log = copy.deepcopy(case["log"])
        work_req = copy.deepcopy(case["request"])
        snap = _snapshot_work(work_log, work_req)
        with pytest.raises(CorruptionError) as exc:
            CorruptionEngine(ORACLES[case["oracle"]]).scan(
                work_log, work_req)
        _assert_work_atomic(snap, work_log, work_req)
        assert exc.value.failure_class == case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        _run_scan(case["then_oracle"], case["then_log"],
                  case["then_request"], case["expect"])


def test_rejected_sink_scan_restores_vandalised_live_inputs():
    """A sink holding closures on the LIVE inputs vandalises
    them and then raises: the engine restores them identity-
    exact before the typed failure surfaces."""
    case = _case("rollback",
                 "rejected-salvage-raising-sink-then-valid-salvage")
    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)

    def vandal(suffix):
        work_log[0]["payload"]["record"]["digest"] = "x"
        work_log[1]["entry_id"] = work_log[1].pop("entry_id")
        work_log.reverse()
        work_req["max_loss"] = 99
        raise SystemExit("vandal")

    with pytest.raises(CorruptionError) as exc:
        CorruptionEngine(vandal).scan(work_log, work_req)
    assert exc.value.failure_class == DQ
    _assert_work_atomic(snap, work_log, work_req)


class _LogMutatingEngine(CorruptionEngine):
    def scan(self, log, request):
        log.append({"junk": True})
        _fail("divergent_quarantine")


class _RequestMutatingEngine(CorruptionEngine):
    def scan(self, log, request):
        request.clear()
        _fail("divergent_quarantine")


class _DeepMutatingEngine(CorruptionEngine):
    def scan(self, log, request):
        log[0]["payload"]["record"]["digest"] = "pdv1:" + "0" * 64
        _fail("divergent_quarantine")


class _OrderMutatingEngine(CorruptionEngine):
    def scan(self, log, request):
        entry = log[0]
        entry["entry_id"] = entry.pop("entry_id")
        _fail("divergent_quarantine")


class _IdentityMutatingEngine(CorruptionEngine):
    """Replaces a nested object with an EQUAL deepcopy - bytes
    and topology unchanged, identity replaced."""

    def scan(self, log, request):
        log[0]["payload"] = copy.deepcopy(log[0]["payload"])
        _fail("divergent_quarantine")


class _NoRestoreEngine(CorruptionEngine):
    """The real scan WITHOUT snapshot/restore around the sink."""

    def scan(self, log, request):
        frozen, _ = canonical_encoding(log)
        count, _head = verified_prefix(frozen)
        self._quarantine(frozen[count:])


def test_rollback_atomicity_kills_mutants():
    """The atomicity assertions are NOT vacuous: every mutant
    raises the pinned failure class after corrupting the
    SUPPLIED objects, the corruption is witnessed at its layer
    (bytes, order or identity), and _assert_work_atomic KILLS
    it."""
    case = _case("rollback",
                 "rejected-salvage-raising-sink-then-valid-salvage")
    for mutant in (_LogMutatingEngine, _RequestMutatingEngine,
                   _DeepMutatingEngine):
        work_log = copy.deepcopy(case["log"])
        work_req = copy.deepcopy(case["request"])
        snap = _snapshot_work(work_log, work_req)
        with pytest.raises(CorruptionError) as exc:
            mutant(quarantine_suffix).scan(work_log, work_req)
        assert exc.value.failure_class == case["expect_failure"]
        assert (_encoded_bytes(work_log) != snap[0][0]
                or _encoded_bytes(work_req) != snap[0][1])
        with pytest.raises(AssertionError):
            _assert_work_atomic(snap, work_log, work_req)

    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(CorruptionError):
        _OrderMutatingEngine(quarantine_suffix).scan(work_log,
                                                     work_req)
    assert work_log == case["log"]  # == is blind
    assert _encoded_bytes(work_log) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(CorruptionError):
        _IdentityMutatingEngine(quarantine_suffix).scan(work_log,
                                                        work_req)
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _graph_signature((work_log, work_req)) == snap[1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # a sink with a live closure + the no-restore mutant: the
    # vandalism survives and is KILLED; the real engine restores
    def vandal_for(log):
        def vandal(suffix):
            log[0]["op"] = "delete"
            raise ValueError("vandal")
        return vandal

    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(CorruptionError):
        _NoRestoreEngine(vandal_for(work_log)).scan(work_log,
                                                    work_req)
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)
    work_log = copy.deepcopy(case["log"])
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(CorruptionError):
        CorruptionEngine(vandal_for(work_log)).scan(work_log,
                                                    work_req)
    _assert_work_atomic(snap, work_log, work_req)


# -- closed scenario coverage ---------------------------------------------------


def test_dispatch_sections_match_manifest():
    for section, manifest in MANIFESTS.items():
        assert _names(section) == list(manifest), section


def _pop_first(m, section):
    m[section].pop(0)


def _rename_first(m, section):
    m[section][0]["name"] += "-renamed"


def _add_extra(m, section):
    row = copy.deepcopy(m[section][0])
    row["name"] = "extra-row-witness"
    m[section].append(row)


def _duplicate_first(m, section):
    m[section].append(copy.deepcopy(m[section][0]))


def _substitute_first(m, section):
    row = copy.deepcopy(m[section][1])
    row["name"] = m[section][0]["name"]
    m[section][0] = row


def _move_row(m, section):
    other = sorted(set(SECTIONS) - {section})[0]
    m[section][0] = copy.deepcopy(m[other][0])


def _reorder(m, section):
    m[section][0], m[section][1] = m[section][1], m[section][0]


def _malformed_index(name):
    return list(MALFORMED_MANIFEST).index(name)


def _swap_key(key, a, b):
    def mutate(m, section):
        x = m["malformed"][_malformed_index(a)]
        y = m["malformed"][_malformed_index(b)]
        x[key], y[key] = y[key], x[key]
    return mutate


def _section_mutation_cases():
    cases = []
    for section in SECTIONS:
        for label, fn in (("pop", _pop_first),
                          ("rename", _rename_first),
                          ("add-extra", _add_extra),
                          ("duplicate", _duplicate_first),
                          ("substitute", _substitute_first),
                          ("move", _move_row),
                          ("reorder", _reorder)):
            cases.append((f"{section}-{label}", section, fn))
    cases += [
        ("swap-failure", "malformed", _swap_key(
            "expect_failure", "log-not-a-list",
            "loss-one-over-bound")),
        ("swap-oracle", "malformed", _swap_key(
            "oracle", "sink-raises", "sink-bad-grammar")),
        ("swap-repair", "malformed", _swap_key(
            "minimal_repair", "log-float-leaf",
            "max-loss-negative")),
        ("swap-scenario", "malformed", _swap_key(
            "scenario", "request-missing-field",
            "request-extra-field")),
        ("swap-defect", "malformed", _swap_key(
            "defect", "max-loss-bool", "max-loss-non-int")),
    ]
    return cases


def test_section_mutations_fail_structure():
    for label, section, mutate in _section_mutation_cases():
        m = copy.deepcopy(CASES)
        mutate(m, section)
        with pytest.raises(AssertionError):
            _validate_structure(m)
        assert label


_PAYLOAD_KEYS = ("oracle", "log", "request", "minimal_repair")


def test_malformed_payload_substitution_closure():
    """SCENARIO-SUBSTITUTION CLOSURE over EXECUTABLE payloads:
    for every malformed row, replacing its executable payload
    (sink selector, log, request, repair) with ANY other row's -
    while name, tag, class and prose stay - is rejected by
    structure validation. Non-vacuity witness: each substituted
    payload really differs from the original."""
    names = _names("malformed")
    rejected = 0
    for i, target in enumerate(names):
        for j, source in enumerate(names):
            if i == j:
                continue
            m = copy.deepcopy(CASES)
            src = m["malformed"][j]
            dst = m["malformed"][i]
            before = {k: copy.deepcopy(dst[k]) for k in _PAYLOAD_KEYS}
            for key in _PAYLOAD_KEYS:
                dst[key] = copy.deepcopy(src[key])
            assert {k: dst[k] for k in _PAYLOAD_KEYS} != before, \
                (target, source)
            with pytest.raises(AssertionError):
                _validate_case("malformed", dst)
            rejected += 1
    assert rejected == len(names) * (len(names) - 1)


def test_happy_boundary_payload_substitution_closure():
    """Swapping ANY two happy/boundary rows' executable payloads
    (log, request) is rejected: the pinned cardinalities and
    receipt re-derivation bind each row to its own data."""
    rows = [(s, i) for s in ("happy", "boundary")
            for i in range(len(CASES[s]))]
    for a in rows:
        for b in rows:
            if a >= b:
                continue
            m = copy.deepcopy(CASES)
            x, y = m[a[0]][a[1]], m[b[0]][b[1]]
            if (x["log"], x["request"]) == (y["log"], y["request"]):
                continue
            for key in ("log", "request"):
                x[key], y[key] = y[key], x[key]
            # at least one of the two swapped rows is rejected
            failures = 0
            for sec, row in ((a[0], x), (b[0], y)):
                try:
                    _validate_case(sec, row)
                except AssertionError:
                    failures += 1
            assert failures >= 1, (a, b)


def test_failure_class_coverage():
    declared = {c["expect_failure"] for c in CASES["malformed"]}
    assert declared == FAILURE_CLASSES == {MCR, EL, DQ}
    for failure in FAILURE_CLASSES:
        assert any(c["expect_failure"] == failure
                   for c in CASES["rollback"]), failure


def test_boundary_edge_mutations_fail_validation():
    """Boundary rows are pinned to their EDGE, not just to a
    self-consistent receipt: each mutant below moves a row off
    its edge and RE-DERIVES the pinned receipt so cardinalities
    and receipt equality still hold - only the edge realization
    rejects it (non-vacuity witness: the mutant's receipt
    re-derives)."""
    def loosen_bound(case):
        case["request"]["max_loss"] += 1

    def narrow_int(case):
        case["log"][2]["sequence"] = 2 ** (MAX_INT_BITS - 1) - 1

    def shallow_depth(case):
        case["log"][2]["deep"] = case["log"][2]["deep"][0]

    for name, mutate in (("loss-exactly-at-bound", loosen_bound),
                         ("int-at-range-edge-in-suffix", narrow_int),
                         ("depth-at-limit-in-suffix", shallow_depth)):
        case = copy.deepcopy(_case("boundary", name))
        mutate(case)
        case["expect"] = _scan("honest", case["log"],
                               case["request"])
        _assert_cardinalities(case, BOUNDARY_MANIFEST[name])
        with pytest.raises(AssertionError):
            _validate_case("boundary", case)


def _regen(case):
    case["expect"] = _scan(case["oracle"], case["log"],
                           case["request"])


def _m1_swap_same_tuple_happy_rows(m):
    """Swap the log+request of the two happy rows sharing
    ("honest", "salvaged", 2, 1) and REGENERATE both receipts."""
    x = next(c for c in m["happy"]
             if c["name"] == "salvage-tampered-tail-entry")
    y = next(c for c in m["happy"]
             if c["name"] == "salvage-garbage-appended")
    for key in ("log", "request"):
        x[key], y[key] = y[key], x[key]
    _regen(x)
    _regen(y)


def _m2_torn_write_replaced_by_digest_tamper(m):
    """Replace the torn-write row's log with a full-shape log whose
    entry 1 has a tampered digest (same counts), receipt
    REGENERATED."""
    x = next(c for c in m["happy"]
             if c["name"] == "salvage-torn-write-mid-log")
    log = copy.deepcopy(_case("happy", "clean-three-entry-log")["log"])
    log[1]["payload"]["record"]["digest"] = "pdv1:" + "0" * 64
    x["log"] = log
    _regen(x)


_REGEN_MUTANTS = {"m1-swap-same-tuple-happy-rows":
                  _m1_swap_same_tuple_happy_rows,
                  "m2-torn-write-replaced-by-digest-tamper":
                  _m2_torn_write_replaced_by_digest_tamper}


@pytest.mark.parametrize("mutant", list(_REGEN_MUTANTS))
@pytest.mark.parametrize("digests", [True, False],
                         ids=["with-digests", "closure-only"])
def test_regenerated_expect_substitution_mutants(mutant, digests,
                                                 monkeypatch):
    """Payload substitutions whose receipts are REGENERATED (so
    cardinalities and receipt equality still hold) are caught -
    with the digest table, and by the semantic closure ALONE.
    Non-vacuity: the mutant's receipts really re-derive and keep
    the manifest counts."""
    m = copy.deepcopy(CASES)
    _REGEN_MUTANTS[mutant](m)
    for case in m["happy"]:
        _assert_cardinalities(case, HAPPY_MANIFEST[case["name"]])
    if not digests:
        monkeypatch.setattr(sys.modules[__name__],
                            "_check_row_digest",
                            lambda section, case: None)
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_row_digests_pin_every_row():
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    for section in SECTIONS:
        for case in CASES[section]:
            m = copy.deepcopy(case)
            m["log"] = [m["log"], "edit"]
            with pytest.raises(AssertionError):
                _check_row_digest(section, m)


def test_one_way_payload_copies_fail_closure():
    """ONE-WAY copies (duplication, not only swaps): copying ANY
    other happy/boundary row's log+request+expect over a row is
    rejected by the semantic closure alone (no digests); likewise
    copying any other rollback row's then_log/then_request/expect
    over a rollback row."""
    rows = [(s, c["name"]) for s in ("happy", "boundary")
            for c in CASES[s]]
    checked = 0
    for dst_sec, dst_name in rows:
        for src_sec, src_name in rows:
            if (dst_sec, dst_name) == (src_sec, src_name):
                continue
            dst = copy.deepcopy(_case(dst_sec, dst_name))
            src = _case(src_sec, src_name)
            for key in ("log", "request", "expect"):
                dst[key] = copy.deepcopy(src[key])
            with pytest.raises(AssertionError):
                _validate_case(dst_sec, dst)
            checked += 1
    assert checked == len(rows) * (len(rows) - 1)
    names = _names("rollback")
    for dst_name in names:
        for src_name in names:
            if dst_name == src_name:
                continue
            dst = copy.deepcopy(_case("rollback", dst_name))
            src = _case("rollback", src_name)
            for key in ("then_log", "then_request", "expect"):
                dst[key] = copy.deepcopy(src[key])
            with pytest.raises(AssertionError):
                _validate_case("rollback", dst)


def test_verifier1_named_survivors_are_killed():
    """B: garbage-appended <- tampered-tail payload; C: garbage-
    appended <- int-at-range-edge payload; E: raising-sink
    follow-up <- excessive-loss follow-up. Each caught by the
    closure alone."""
    for src_sec, src_name in (
            ("happy", "salvage-tampered-tail-entry"),
            ("boundary", "int-at-range-edge-in-suffix")):
        dst = copy.deepcopy(_case("happy", "salvage-garbage-appended"))
        for key in ("log", "request", "expect"):
            dst[key] = copy.deepcopy(_case(src_sec, src_name)[key])
        assert dst["expect"]["verified_count"] == 2
        with pytest.raises(AssertionError):
            _validate_case("happy", dst)
    dst = copy.deepcopy(_case(
        "rollback", "rejected-salvage-raising-sink-then-valid-salvage"))
    src = _case("rollback",
                "rejected-salvage-excessive-loss-then-valid-salvage")
    for key in ("then_log", "then_request", "expect"):
        dst[key] = copy.deepcopy(src[key])
    with pytest.raises(AssertionError):
        _validate_case("rollback", dst)
