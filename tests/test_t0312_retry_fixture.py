"""T0312: jobs retry conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the T0311
jobs-retry contract (data/contracts/retry.yaml). The cases execute
against the contract-derived reference decide in
tests.test_t0311_retry_contract - nothing is re-implemented here.
Pinned decision records were computed from that reference at authoring
time, so any contract or derivation drift breaks this battery. Every
malformed case is discriminating: applying ONLY its declared
single-locus repair makes it decide. Rollback cases prove a rejected
decision leaves the exact supplied request bit- and value-identical
before a valid follow-up decides as pinned.

Hostile-type rows are derived here from EVERY request the fixture
accepts (happy and boundary requests, malformed repairs, rollback
follow-ups): hostile containers, str-subclass keys in three forms on
every field of the request and the policy, and str-subclass, raising
repr, int-subclass and bool values. Each must fail typed with a fresh
error, run no user code and leave the input unchanged.

Every one-edit reference mutant pinned by the T0311 battery must turn
this fixture red on its own.

DESIGN CAUTION: the reference is derived from the same contract
document, so this fixture proves fixture/contract CONSISTENCY, not
production behavior. The later runtime work must execute these same
cases against a separately implemented runtime."""

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

import tests.test_t0311_retry_contract as _REF  # noqa: E402
from tools.retry_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "retry" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECORD_FIELDS = list(_CC["record"]["fields"])
FIELDS = _CC["request"]["operations"][0]["fields"]
POLICY_FIELDS = _CC["request"]["policy_fields"]
FAILURES = tuple(_CC["request"]["failures"])
MAX_ATTEMPTS = _CC["request"]["max_attempts"]
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_DECISION_RE = re.compile(_CC["identifiers"]["decision_id"]["grammar"], re.ASCII)
MAX = 9007199254740991  # 2**53 - 1, written out

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "requests", "expect_records"}
BAD_KEYS = {"name", "defect", "expect_failure", "request", "minimal_repair"}
RB_KEYS = {"name", "why", "rejected_request", "expect_failure", "request", "expect_record"}


def _decide(request):
    return _REF.decide(request)  # late-bound: reference mutants rebind it


def _fails(request):
    try:
        _decide(copy.deepcopy(request))
    except _REF.RetryError as exc:
        return exc.failure_class
    return None


def _validate_record(record, label):
    assert type(record) is dict, label
    assert all(type(k) is str for k in record) and set(record) == set(RECORD_FIELDS), label
    assert record["op"] == "decide", label
    assert _JOB_RE.fullmatch(record["job_id"]), label
    assert _DECISION_RE.fullmatch(record["decision_id"]), label
    assert record["decision"] in ("retry", "dead"), label
    if record["decision"] == "dead":
        assert (record["delay_ms"], record["retry_at"]) == (None, None), label
    else:
        assert type(record["delay_ms"]) is int and record["delay_ms"] >= 1, label
        assert type(record["retry_at"]) is int and record["retry_at"] <= MAX, label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert all(type(k) is str for k in request) and set(request) == set(FIELDS), label
    assert type(request["policy"]) is dict, label
    assert set(request["policy"]) == set(POLICY_FIELDS), label


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
    if type(a) is list and type(b) is list and len(a) == len(b):
        out = set()
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out |= _diff_paths(x, y, (*path, i))
        return out
    return set() if (type(a) is type(b) and a == b) else {path}


def _validate_structure(cases):
    assert type(cases) is dict and set(cases) == TOP_KEYS
    assert type(cases["schema"]) is int and cases["schema"] == FIXTURE_SCHEMA_VERSION
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
                assert type(row["why"]) is str and row["why"], label
                reqs, recs = row["requests"], row["expect_records"]
                assert type(reqs) is list and reqs, label
                assert type(recs) is list and len(recs) == len(reqs), label
                for request, record in zip(reqs, recs, strict=True):
                    _validate_request_shape(request, label)
                    _validate_record(record, label)
                    assert (record["job_id"], record["attempts"]) == (
                        request["job_id"],
                        request["attempts"],
                    ), label
            elif section == "malformed":
                assert set(row) == BAD_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                assert type(row["defect"]) is str and row["defect"], label
                repair = row["minimal_repair"]
                assert type(repair) is dict and set(repair) == {"request"}, label
                assert _diff_paths(repair["request"], row["request"]), label
                _validate_request_shape(repair["request"], label)
            else:
                assert set(row) == RB_KEYS, label
                assert type(row["why"]) is str and row["why"], label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                _validate_request_shape(row["request"], label)
                _validate_record(row["expect_record"], label)
                assert row["rejected_request"] != row["request"], label
    assert len(names) == len(set(names)), "fixture names must be unique"


def _decisions(row):
    return ",".join(rec["decision"] for rec in row["expect_records"])


def _row_digest(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> (decisions, delays); malformed: name -> (failure
# class, pinned defect text); rollback: name -> (failure class, follow-up
# decision, follow-up delay)
HAPPY_MANIFEST = {
    "transient_backoff_chain": ("retry,retry,retry,retry,dead", [1000, 2000, 4000, 8000, None]),
    "timeout_retries_like_transient": ("retry,retry", [2000, 2000]),
    "permanent_dead_at_first_attempt": ("dead", [None]),
    "constant_backoff_multiplier_one": ("retry,retry,retry,retry", [750, 750, 750, 750]),
}
BOUNDARY_MANIFEST = {
    "delay_capped_at_max": ("retry", [30000]),
    "delay_hits_max_exactly": ("retry", [8000]),
    "retry_at_on_clock_limit": ("retry", [1000]),
    "dead_at_clock_limit": ("dead", [None]),
    "last_retry_then_dead": ("retry,dead", [8000, None]),
    "timeout_last_retry_then_dead": ("retry,dead", [8000, None]),
    "largest_policy": ("retry", [86400000]),
    "smallest_policy": ("retry", [1]),
    "max_equals_base_caps_every_retry": ("retry,retry,retry", [2000, 2000, 2000]),
}
MALFORMED_MANIFEST = {
    "request_not_a_dict": ("malformed_retry_request", "the request is a list"),
    "unknown_op": ("malformed_retry_request", "op is 'retry', not a registered operation"),
    "extra_field": ("malformed_retry_request", "the request carries an extra jitter field"),
    "missing_field": ("malformed_retry_request", "the request has no now field"),
    "renamed_field": (
        "malformed_retry_request",
        "the now field is renamed to jitter, keeping the field count",
    ),
    "job_id_bad_grammar": ("malformed_retry_request", "the job id has 63 hex digits"),
    "job_id_trailing_newline": ("malformed_retry_request", "the job id ends in a newline"),
    "attempts_zero": ("malformed_retry_request", "attempts is 0"),
    "attempts_six": ("malformed_retry_request", "attempts is 6, past max_attempts"),
    "attempts_bool": ("malformed_retry_request", "attempts is the bool True"),
    "failure_unknown": ("malformed_retry_request", "failure is 'crash'"),
    "now_negative": ("malformed_retry_request", "now is -1"),
    "now_past_clock": ("malformed_retry_request", "now is 2**53"),
    "policy_not_a_dict": ("malformed_retry_request", "the policy is a list"),
    "base_zero": ("invalid_retry_policy", "base_delay_ms is 0"),
    "base_past_bound": ("invalid_retry_policy", "base_delay_ms is 3600001"),
    "multiplier_eleven": ("invalid_retry_policy", "multiplier is 11"),
    "multiplier_zero": ("invalid_retry_policy", "multiplier is 0"),
    "max_below_base": ("invalid_retry_policy", "max_delay_ms 999 is below base_delay_ms 1000"),
    "max_past_bound": ("invalid_retry_policy", "max_delay_ms is 86400001"),
    "policy_extra_field": ("invalid_retry_policy", "the policy carries an extra jitter_ms field"),
    "policy_renamed_field": (
        "invalid_retry_policy",
        "the policy's max_delay_ms is renamed to jitter_ms, keeping the field count",
    ),
    "policy_float": ("invalid_retry_policy", "multiplier is the float 2.0"),
    "retry_at_past_clock": ("clock_overflow", "now + delay is 2**53, one past the clock"),
}
ROLLBACK_MANIFEST = {
    "clock_overflow_then_earlier_now": ("clock_overflow", "retry", 1000),
    "bad_policy_then_fixed_policy": ("invalid_retry_policy", "retry", 1000),
    "malformed_then_repaired": ("malformed_retry_request", "dead", None),
}

# content binding: canonical sha256 of every row. Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:transient_backoff_chain": "b4b39205172a9ef30015813f4828c6773a91b7cbc419bc00a9404774b6fea21d",  # noqa: E501
    "happy:timeout_retries_like_transient": "3602b4efebd2a712f7cafcc7843edb80ae6c1839cf4a6f73c18c962056790217",  # noqa: E501
    "happy:permanent_dead_at_first_attempt": "602c1c39a01e93a9ba980438c29a925e74ec6f6dbdba92784d86cc0717ee2096",  # noqa: E501
    "happy:constant_backoff_multiplier_one": "e8c5b1d14d63e30dd2df5e8f55743151245e4d32975c9a3d4877ea7195ba7dd6",  # noqa: E501
    "boundary:delay_capped_at_max": "04e4e45def219c2ad7e39235c8202f0c06664808fcb220616336debd4df8a66d",  # noqa: E501
    "boundary:delay_hits_max_exactly": "e87722cab1c806f3ec29dfe161dfa5d5b5bf2dae246da7833847e3e6051a251d",  # noqa: E501
    "boundary:retry_at_on_clock_limit": "7fc23b3ef02d30aeda7eaf73ebb933dfe1d379eacdbb2263ac2e93ebebf5da85",  # noqa: E501
    "boundary:dead_at_clock_limit": "afab7959258e87e9f252148d954be86b86ac6e195c52f0c6828f51bc0c5865d7",  # noqa: E501
    "boundary:last_retry_then_dead": "f4fed346afc7e75614b4ed82d857069659ca1a897c1e096b076b31b884905e43",  # noqa: E501
    "boundary:timeout_last_retry_then_dead": "777f788f869cdbc79d8effc80abeb39e9fdc8a9cd74ddeda14e3eafa7397d5b7",  # noqa: E501
    "boundary:largest_policy": "d2a73553ad43ba67254a303d69ed3a390e7476462146b2fdda9130e4af33ad55",  # noqa: E501
    "boundary:smallest_policy": "cf40694820fff4b89319704a34963a49880a30e522378333e7ed13e11bd1c895",  # noqa: E501
    "boundary:max_equals_base_caps_every_retry": "33c6a90155bd394d1be633596a4fdb4d2caf122153444b43ea09e32c97f75625",  # noqa: E501
    "malformed:request_not_a_dict": "7dd7f19f3e18f0adf2216db3847d5c99ecfa8c87cc779d6d8d6014c06f5bc1f0",  # noqa: E501
    "malformed:unknown_op": "3698cc222ff2f712b3c153b83b309ec7d0a1a56f5e5e79d2875133df73a31570",  # noqa: E501
    "malformed:extra_field": "dc239878364a92a60c3ea8d7bb9514d093fad15e9b83530d84bb28ecd12dd6fd",  # noqa: E501
    "malformed:missing_field": "9a0dab1318a290d333316265e57ffa78147106c6e9cca444676e110ac7e25215",  # noqa: E501
    "malformed:renamed_field": "bcb8eee96b10279a13decfa424aa61b2ea7e1137b6a6f18cbfe6e579e7d7e5ad",  # noqa: E501
    "malformed:job_id_bad_grammar": "b6d66a548791eb9ac7b2547a37fe99bfa6081792d3fe45e14d9a681c5aaf55a5",  # noqa: E501
    "malformed:job_id_trailing_newline": "46e9590f3d6f6d17738210316811e973d621832cd5ee5269ed730a0624593535",  # noqa: E501
    "malformed:attempts_zero": "4ee8b7226a9a216c66c49b593a9a6de73cee1223b659a33671ba1d0fb3402184",  # noqa: E501
    "malformed:attempts_six": "24b5728125354b9bf7590d05ca101771bb7f068ee04343c72af617dcd403d89f",  # noqa: E501
    "malformed:attempts_bool": "faef8037bde0942d6555c6d2a02e20dee11fe60124aaa0f6a61f55dd152ccca9",  # noqa: E501
    "malformed:failure_unknown": "e23b501d02254461be083d0f0f6e865153def493be24137f29cec1c861d20f3c",  # noqa: E501
    "malformed:now_negative": "df9fcfd600d858304cca693ce6218f63725896cbc2883582d3383c6aad25160c",  # noqa: E501
    "malformed:now_past_clock": "f5bd381a07553f5c2619cb2459ed48266fd3b1051c6d9378c606c5aeaf30fcc2",  # noqa: E501
    "malformed:policy_not_a_dict": "34ac9923e92bc20add184ac4008123dde3fdf9d7e63b74a424379d6b0b222532",  # noqa: E501
    "malformed:base_zero": "d1b6a0d8064559bab3a98b3ddfd3a3f3074a609231040f100a541925863562a3",  # noqa: E501
    "malformed:base_past_bound": "468fb55d460d4e9efec126696016c0c6bbb9496aa33d565fbaf5f09e8e4511a0",  # noqa: E501
    "malformed:multiplier_eleven": "aa87dde95506fcacf01f3fc178b56a64395b53b1debed56f6caed92e7ef5b3f4",  # noqa: E501
    "malformed:multiplier_zero": "725d6b891ef5dbae181ab9b0c1872cc381c4281286c343c7940bd0f51de9644e",  # noqa: E501
    "malformed:max_below_base": "bb085aa0182dc43a5c090bda355bf34a2bb2ef005d2065d34caa1e0da8a56750",  # noqa: E501
    "malformed:max_past_bound": "eb3121ca5376c05588e37406f8236f8cbfa3c45e65c3133cb2e74920a15d6ad0",  # noqa: E501
    "malformed:policy_extra_field": "b8e5884c9582aa2593b01e05a398d9278849e0b96b0eb4e6594b37e867237436",  # noqa: E501
    "malformed:policy_renamed_field": "83f9688bb92af371f83d8f51899b6d8dda3ea96a344d1fe1c0f62580866c4a97",  # noqa: E501
    "malformed:policy_float": "bea4cd58717f5f653e174f3ebee5ac7edd1b9ee5632bf87a7685583635385a98",  # noqa: E501
    "malformed:retry_at_past_clock": "6b726b4a11fba118538d66db6777a2914634509ae4fd8f1202f5a1f511b0e34c",  # noqa: E501
    "rollback:clock_overflow_then_earlier_now": "33386fc5255e79f0b283b2a0efef043835e31f4742e68f735746609fa3af4cc5",  # noqa: E501
    "rollback:bad_policy_then_fixed_policy": "3d899f9b36af2710cbc678ce8229db8458e2b57df33da429e0840c9397e724ee",  # noqa: E501
    "rollback:malformed_then_repaired": "867e1641fa854e95ff05c9ff86da524ae0f0757f062ece609b4ce0a81da3e304",  # noqa: E501
}

MANIFESTS = {
    "happy": HAPPY_MANIFEST,
    "boundary": BOUNDARY_MANIFEST,
    "malformed": MALFORMED_MANIFEST,
    "rollback": ROLLBACK_MANIFEST,
}
P = {"base_delay_ms": 1000, "multiplier": 2, "max_delay_ms": 30000}


# -- per-row semantic edges over the ORIGINAL data ---------------------------


def _pol(request):
    p = request["policy"]
    return p["base_delay_ms"], p["multiplier"], p["max_delay_ms"]


def _uncapped(request):
    base, mult, _ = _pol(request)
    return base * mult ** (request["attempts"] - 1)


def _h_chain(r):
    reqs = r["requests"]
    assert [q["attempts"] for q in reqs] == [1, 2, 3, 4, MAX_ATTEMPTS]
    assert {q["failure"] for q in reqs} == {"transient"}
    assert all(q["policy"] == P for q in reqs)
    assert len({q["job_id"] for q in reqs}) == 1
    for q, rec in zip(reqs, r["expect_records"], strict=True):
        if rec["decision"] == "retry":
            assert rec["retry_at"] == q["now"] + rec["delay_ms"]


def _h_timeout(r):
    a, b = r["requests"]
    assert (a["failure"], b["failure"]) == ("timeout", "transient")
    assert {k: v for k, v in a.items() if k != "failure"} == {
        k: v for k, v in b.items() if k != "failure"
    }
    assert a["attempts"] >= 2  # past the first attempt, so a budget would show
    ra, rb = r["expect_records"]
    assert (ra["delay_ms"], ra["retry_at"]) == (rb["delay_ms"], rb["retry_at"])
    assert ra["decision_id"] != rb["decision_id"]  # the failure is in the preimage


def _h_permanent(r):
    (q,) = r["requests"]
    assert (q["failure"], q["attempts"]) == ("permanent", 1)


def _h_constant(r):
    reqs = r["requests"]
    assert all(_pol(q)[1] == 1 for q in reqs)
    assert all(_pol(q)[2] > _pol(q)[0] or _pol(q)[2] == _pol(q)[0] for q in reqs)
    assert [q["attempts"] for q in reqs] == [1, 2, 3, 4]


def _b_capped(r):
    (q,) = r["requests"]
    assert _uncapped(q) > _pol(q)[2]
    assert r["expect_records"][0]["delay_ms"] == _pol(q)[2]


def _b_exact_max(r):
    (q,) = r["requests"]
    assert _uncapped(q) == _pol(q)[2]


def _b_clock_edge(r):
    (q,) = r["requests"]
    assert q["now"] + r["expect_records"][0]["delay_ms"] == MAX


def _b_dead_at_clock(r):
    (q,) = r["requests"]
    assert (q["now"], q["attempts"], q["failure"]) == (MAX, MAX_ATTEMPTS, "transient")


def _b_last_retry(r):
    a, b = r["requests"]
    assert (a["attempts"], b["attempts"]) == (MAX_ATTEMPTS - 1, MAX_ATTEMPTS)
    assert {k: v for k, v in a.items() if k != "attempts"} == {
        k: v for k, v in b.items() if k != "attempts"
    }


def _b_timeout_last_retry(r):
    a, b = r["requests"]
    assert (a["failure"], b["failure"]) == ("timeout", "timeout")
    assert (a["attempts"], b["attempts"]) == (MAX_ATTEMPTS - 1, MAX_ATTEMPTS)
    assert {k: v for k, v in a.items() if k != "attempts"} == {
        k: v for k, v in b.items() if k != "attempts"
    }


def _b_largest(r):
    (q,) = r["requests"]
    assert _pol(q) == (3600000, 10, 86400000) and q["attempts"] == MAX_ATTEMPTS - 1
    assert _uncapped(q) > _pol(q)[2]


def _b_smallest(r):
    (q,) = r["requests"]
    assert _pol(q) == (1, 1, 1) and (q["now"], q["attempts"]) == (0, 1)


def _b_max_equals_base(r):
    reqs = r["requests"]
    assert all(_pol(q)[0] == _pol(q)[2] and _pol(q)[1] > 1 for q in reqs)
    assert [q["attempts"] for q in reqs] == [1, 2, 3]


OK_EDGES = {
    "transient_backoff_chain": _h_chain,
    "timeout_retries_like_transient": _h_timeout,
    "permanent_dead_at_first_attempt": _h_permanent,
    "constant_backoff_multiplier_one": _h_constant,
    "delay_capped_at_max": _b_capped,
    "delay_hits_max_exactly": _b_exact_max,
    "retry_at_on_clock_limit": _b_clock_edge,
    "dead_at_clock_limit": _b_dead_at_clock,
    "last_retry_then_dead": _b_last_retry,
    "timeout_last_retry_then_dead": _b_timeout_last_retry,
    "largest_policy": _b_largest,
    "smallest_policy": _b_smallest,
    "max_equals_base_caps_every_retry": _b_max_equals_base,
}


def _only(bad, fix, *paths):
    assert _diff_paths(bad, fix) == set(paths), (bad, fix)


_POLICY_EDGES = {
    # name -> (field, bad value, repaired value)
    "base_zero": ("base_delay_ms", 0, 1),
    "multiplier_eleven": ("multiplier", 11, 10),
    "multiplier_zero": ("multiplier", 0, 1),
    "max_below_base": ("max_delay_ms", 999, 1000),
    "max_past_bound": ("max_delay_ms", 86400001, 86400000),
}
_REQUEST_EDGES = {
    "attempts_zero": ("attempts", 0, 1),
    "attempts_six": ("attempts", 6, 5),
    "now_negative": ("now", -1, 0),
    "now_past_clock": ("now", MAX + 1, MAX),
}


def _m(name, bad, fix):  # noqa: C901 - one closed branch per tag
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus."""
    if name == "request_not_a_dict":
        assert type(bad) is list and bad == [fix]
    elif name == "unknown_op":
        assert bad["op"] == "retry" and fix["op"] == "decide"
        _only(bad, fix, ("op",))
    elif name == "extra_field":
        assert set(bad) - set(fix) == {"jitter"}
        _only(bad, fix, ("jitter",))
    elif name == "missing_field":
        assert set(fix) - set(bad) == {"now"}
        _only(bad, fix, ("now",))
    elif name == "renamed_field":
        assert type(bad) is dict and len(bad) == len(fix)
        assert set(bad) - set(fix) == {"jitter"} and set(fix) - set(bad) == {"now"}
        assert bad["jitter"] == fix["now"]
    elif name == "job_id_bad_grammar":
        assert bad["job_id"] == fix["job_id"][:-1]
        _only(bad, fix, ("job_id",))
    elif name == "job_id_trailing_newline":
        assert bad["job_id"] == fix["job_id"] + "\n"
        _only(bad, fix, ("job_id",))
    elif name in _REQUEST_EDGES:
        field, b, f = _REQUEST_EDGES[name]
        assert (bad[field], fix[field]) == (b, f) and type(bad[field]) is int
        _only(bad, fix, (field,))
    elif name == "attempts_bool":
        assert bad["attempts"] is True and fix["attempts"] == 1
        _only(bad, fix, ("attempts",))
    elif name == "failure_unknown":
        assert type(bad["failure"]) is str and bad["failure"] == "crash"
        assert fix["failure"] in FAILURES
        _only(bad, fix, ("failure",))
    elif name == "policy_not_a_dict":
        assert type(bad["policy"]) is list and type(fix["policy"]) is dict
        assert bad["policy"] == [fix["policy"][k] for k in POLICY_FIELDS]
        _only(bad, fix, ("policy",))
    elif name in _POLICY_EDGES:
        field, b, f = _POLICY_EDGES[name]
        assert (bad["policy"][field], fix["policy"][field]) == (b, f)
        _only(bad, fix, ("policy", field))
    elif name == "base_past_bound":
        assert (bad["policy"]["base_delay_ms"], fix["policy"]["base_delay_ms"]) == (
            3600001,
            3600000,
        )
        assert bad["policy"]["max_delay_ms"] >= bad["policy"]["base_delay_ms"]
        _only(bad, fix, ("policy", "base_delay_ms"))
    elif name == "policy_extra_field":
        assert set(bad["policy"]) - set(fix["policy"]) == {"jitter_ms"}
        _only(bad, fix, ("policy", "jitter_ms"))
    elif name == "policy_renamed_field":
        b, f = bad["policy"], fix["policy"]
        assert type(b) is dict and len(b) == len(f)
        assert set(b) - set(f) == {"jitter_ms"} and set(f) - set(b) == {"max_delay_ms"}
        assert b["jitter_ms"] == f["max_delay_ms"]
        _only(bad, fix, ("policy", "jitter_ms"), ("policy", "max_delay_ms"))
    elif name == "policy_float":
        assert type(bad["policy"]["multiplier"]) is float
        assert bad["policy"]["multiplier"] == fix["policy"]["multiplier"]
        _only(bad, fix, ("policy", "multiplier"))
    elif name == "retry_at_past_clock":
        assert bad["now"] + _uncapped(bad) == MAX + 1
        assert fix["now"] + _uncapped(fix) == MAX
        _only(bad, fix, ("now",))
    else:
        raise AssertionError(f"unknown malformed tag {name}")


def _check_rollback_scenario(row):
    name, bad, q = row["name"], row["rejected_request"], row["request"]
    if name == "clock_overflow_then_earlier_now":
        assert bad["now"] + _uncapped(bad) == MAX + 1
        assert row["expect_record"]["retry_at"] == MAX
        _only(bad, q, ("now",))
    elif name == "bad_policy_then_fixed_policy":
        assert bad["policy"]["max_delay_ms"] < bad["policy"]["base_delay_ms"]
        assert row["expect_record"]["delay_ms"] == q["policy"]["max_delay_ms"]
        _only(bad, q, ("policy", "max_delay_ms"))
    elif name == "malformed_then_repaired":
        assert (bad["attempts"], q["attempts"]) == (MAX_ATTEMPTS + 1, MAX_ATTEMPTS)
        _only(bad, q, ("attempts",))
    else:
        raise AssertionError(f"unknown rollback tag {name}")


def _validate_closure(cases):
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == list(manifest), section
        for row in rows:
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS.get(label) == _row_digest(row), label
            meta = manifest[row["name"]]
            if section in ("happy", "boundary"):
                delays = [rec["delay_ms"] for rec in row["expect_records"]]
                assert (_decisions(row), delays) == meta, label
                OK_EDGES[row["name"]](row)
            elif section == "malformed":
                assert (row["expect_failure"], row["defect"]) == meta, label
                _m(row["name"], row["request"], row["minimal_repair"]["request"])
            else:
                rec = row["expect_record"]
                assert (row["expect_failure"], rec["decision"], rec["delay_ms"]) == meta, label
                _check_rollback_scenario(row)
    assert set(ROW_DIGESTS) == {f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    assert set(OK_EDGES) == set(HAPPY_MANIFEST) | set(BOUNDARY_MANIFEST)


def _names(section):
    return [row["name"] for row in CASES[section]]


def _row(section, name):
    (row,) = [r for r in CASES[section] if r["name"] == name]
    return row


# -- structure and closure ---------------------------------------------------


def test_fixture_structure():
    _validate_structure(CASES)


def test_fixture_scenario_closure():
    _validate_closure(CASES)


def test_param_ids_equal_manifest_names():
    for section, manifest in MANIFESTS.items():
        assert _names(section) == list(manifest), section


def test_failure_class_and_enum_coverage():
    covered = {row["expect_failure"] for row in CASES["malformed"]}
    assert covered == FAILURE_CLASSES
    assert {row["expect_failure"] for row in CASES["rollback"]} == FAILURE_CLASSES
    failures = {
        q["failure"] for s in ("happy", "boundary") for row in CASES[s] for q in row["requests"]
    }
    assert failures == set(FAILURES)
    decisions = {
        rec["decision"]
        for s in ("happy", "boundary")
        for row in CASES[s]
        for rec in row["expect_records"]
    }
    assert decisions == {"retry", "dead"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(schema=2),
        lambda c: c.update(schema=True),
        lambda c: c.update(contract="jobs-queue"),
        lambda c: c.update(contract_base_path="/jobs/retry/v2"),
        lambda c: c.update(extra=1),
        lambda c: c.pop("rollback"),
        lambda c: c["boundary"].clear(),
        lambda c: c["happy"][0].update(jitter=0),
        lambda c: c["happy"][0]["expect_records"].pop(),
        lambda c: c["happy"][0]["expect_records"][0].pop("retry_at"),
        lambda c: c["happy"][0]["expect_records"][0].update(extra=1),
        lambda c: c["happy"][0]["expect_records"][0].update(decision_id="rd1:zz"),
        lambda c: c["happy"][0]["expect_records"][0].update(decision="later"),
        lambda c: c["happy"][0]["expect_records"][4].update(delay_ms=0),
        lambda c: c["happy"][0]["expect_records"][0].update(delay_ms=None),
        lambda c: c["happy"][0]["expect_records"][0].update(attempts=2),
        lambda c: c["happy"][0]["requests"][0]["policy"].update(jitter_ms=0),
        lambda c: c["malformed"][0].update(expect_failure="internal"),
        lambda c: c["malformed"][1].update(
            minimal_repair={"request": c["malformed"][1]["request"]}
        ),
        lambda c: c["malformed"][1]["minimal_repair"].update(state={}),
        lambda c: c["malformed"][1].update(minimal_repair={"policy": {}}),
        lambda c: c["rollback"][0].update(expect_failure="internal"),
        lambda c: c["rollback"][0].update(request=c["rollback"][0]["rejected_request"]),
        lambda c: c["rollback"][0]["expect_record"].update(decision_id="rd1:" + "0" * 63),
        lambda c: c["boundary"].append(copy.deepcopy(c["happy"][0])),
    ],
    ids=lambda f: "mut",
)
def test_structure_mutations_fail(mutate):
    cases = copy.deepcopy(CASES)
    mutate(cases)
    with pytest.raises((AssertionError, KeyError, IndexError, TypeError)):
        _validate_structure(cases)


def _bn(c, section, name):
    (row,) = [r for r in c[section] if r["name"] == name]
    return row


def _swap(c, section, a, b, *keys):
    x, y = _bn(c, section, a), _bn(c, section, b)
    for key in keys:
        x[key], y[key] = y[key], x[key]


def _regenerate(row):
    row["expect_records"] = [_decide(copy.deepcopy(q)) for q in row["requests"]]


def _edit(section, name, fn):
    def mutant(c):
        row = _bn(c, section, name)
        fn(row)
        if section in ("happy", "boundary"):
            _regenerate(row)

    return mutant


def _set_policy(**kw):
    def fn(row):
        for q in row["requests"]:
            q["policy"].update(kw)

    return fn


_CLOSURE_MUTANTS = {
    "swap_boundary_names": lambda c: _swap(
        c, "boundary", "delay_capped_at_max", "delay_hits_max_exactly", "name"
    ),
    "swap_happy_names": lambda c: _swap(
        c, "happy", "permanent_dead_at_first_attempt", "constant_backoff_multiplier_one", "name"
    ),
    "swap_malformed_names_same_class": lambda c: _swap(
        c, "malformed", "attempts_zero", "attempts_six", "name"
    ),
    "swap_malformed_requests": lambda c: _swap(
        c, "malformed", "base_zero", "multiplier_zero", "request", "minimal_repair"
    ),
    "drop_policy_float": lambda c: c["malformed"].remove(_bn(c, "malformed", "policy_float")),
    "duplicate_unknown_op": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "unknown_op"))
    ),
    "cap_edge_not_capped": _edit(
        "boundary", "delay_capped_at_max", _set_policy(max_delay_ms=1000000)
    ),
    "exact_max_off_by_one": _edit(
        "boundary", "delay_hits_max_exactly", _set_policy(max_delay_ms=8001)
    ),
    "clock_edge_one_short": _edit(
        "boundary", "retry_at_on_clock_limit", lambda row: row["requests"][0].update(now=MAX - 1001)
    ),
    "dead_clock_edge_one_short": _edit(
        "boundary", "dead_at_clock_limit", lambda row: row["requests"][0].update(now=MAX - 1)
    ),
    "timeout_row_both_transient": _edit(
        "happy",
        "timeout_retries_like_transient",
        lambda row: row["requests"][0].update(failure="transient"),
    ),
    "timeout_row_at_first_attempt": _edit(
        "happy",
        "timeout_retries_like_transient",
        lambda row: [q.update(attempts=1) for q in row["requests"]],
    ),
    "largest_policy_shrunk": _edit("boundary", "largest_policy", _set_policy(multiplier=9)),
    "chain_policy_edited": _edit(
        "happy",
        "transient_backoff_chain",
        lambda row: row["requests"][2]["policy"].update(multiplier=3),
    ),
    "repair_touches_two_fields": lambda c: _bn(c, "malformed", "attempts_six")["minimal_repair"][
        "request"
    ].update(now=1),
    "clock_repair_off_by_two": lambda c: _bn(c, "malformed", "retry_at_past_clock")[
        "minimal_repair"
    ]["request"].update(now=MAX - 1001),
    "rollback_follow_up_widened": lambda c: _bn(c, "rollback", "malformed_then_repaired")[
        "request"
    ].update(now=5),
    "failure_unknown_retyped": lambda c: _bn(c, "malformed", "failure_unknown")["request"].update(
        failure=None
    ),
    "renamed_back_to_extra": lambda c: _bn(c, "malformed", "renamed_field")["request"].update(
        now=0
    ),
    "timeout_last_retry_transient": _edit(
        "boundary",
        "timeout_last_retry_then_dead",
        lambda row: row["requests"][0].update(failure="transient"),
    ),
    "defect_text_edited": lambda c: _bn(c, "malformed", "attempts_bool").update(
        defect="attempts is odd"
    ),
}


def _section_of(cases, row):
    for section in SECTIONS:
        if any(r is row for r in cases[section]):
            return section
    raise AssertionError("row not in fixture")


@pytest.mark.parametrize("mutant", list(_CLOSURE_MUTANTS))
@pytest.mark.parametrize("digests", [True, False], ids=["with-digests", "closure-only"])
def test_closure_kills_substitution_mutants(mutant, digests, monkeypatch):
    """Every substitution is caught - and caught by the semantic closure
    ALONE, not only by the row digest table."""
    cases = copy.deepcopy(CASES)
    _CLOSURE_MUTANTS[mutant](cases)
    if not digests:
        monkeypatch.setattr(
            sys.modules[__name__],
            "_row_digest",
            lambda row: ROW_DIGESTS.get(f"{_section_of(cases, row)}:{row['name']}"),
        )
    with pytest.raises((AssertionError, KeyError, ValueError)):
        _validate_structure(cases)
        _validate_closure(cases)


# -- execution ---------------------------------------------------------------


@pytest.mark.parametrize("section,name", [(s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_records(section, name):
    row = _row(section, name)
    requests = copy.deepcopy(row["requests"])
    assert [_decide(q) for q in requests] == row["expect_records"]
    assert requests == row["requests"]
    again = [_decide(copy.deepcopy(q)) for q in row["requests"]]
    assert again == row["expect_records"]


def test_tampered_pinned_record_is_detected():
    row = copy.deepcopy(_row("happy", "transient_backoff_chain"))
    row["expect_records"][1]["delay_ms"] += 1
    assert [_decide(copy.deepcopy(q)) for q in row["requests"]] != row["expect_records"]


def _rejects(failure_class, request):
    before = copy.deepcopy(request)
    with pytest.raises(_REF.RetryError) as err:
        _decide(request)
    exc = err.value
    assert type(exc) is _REF.RetryError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class] and exc.code in ERROR_ENUM
    assert exc.retryable is False
    assert exc.__cause__ is None and exc.__context__ is None
    assert _snap(request) == _snap(before)  # type-exact: True != 1


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    row = _row("malformed", name)
    request = copy.deepcopy(row["request"])
    _rejects(row["expect_failure"], request)
    assert request == row["request"]


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_succeeds(name):
    row = _row("malformed", name)
    record = _decide(copy.deepcopy(row["minimal_repair"]["request"]))
    _validate_record(record, name)


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_row("malformed", "attempts_six"))
    row["request"]["attempts"] = 5
    assert _fails(row["request"]) is None


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    rejected = copy.deepcopy(row["rejected_request"])
    _rejects(row["expect_failure"], rejected)
    assert _snap(rejected) == _snap(row["rejected_request"])
    request = copy.deepcopy(row["request"])
    assert _decide(request) == row["expect_record"]
    assert _snap(request) == _snap(row["request"])


def test_validation_precedence():
    """request shape -> policy -> clock, first failure wins (derived from
    fixture rows so the precedence is exercised on pinned data)."""
    bad_policy = _row("malformed", "base_zero")["request"]
    overflow = _row("malformed", "retry_at_past_clock")["request"]
    _rejects("malformed_retry_request", {**bad_policy, "attempts": 0})
    _rejects("invalid_retry_policy", {**overflow, "policy": dict(bad_policy["policy"])})
    _rejects("malformed_retry_request", {**overflow, "attempts": 6})


# -- hostile types (logged: any call is a failure) ---------------------------

HOSTILE = []


class _Armed:
    on = False


def _log(name):
    if _Armed.on:
        HOSTILE.append(name)


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _LyingDict(dict):
    def __getitem__(self, key):
        _log("getitem")
        return dict.__getitem__(self, key)

    def __iter__(self):
        _log("iter")
        return dict.__iter__(self)

    def __len__(self):
        _log("len")
        return dict.__len__(self)

    def keys(self):
        _log("keys")
        return dict.keys(self)

    def items(self):
        _log("items")
        return dict.items(self)


class _EqRaises(str):
    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")

    def __hash__(self):
        _log("hash")
        return str.__hash__(self)


class _Collides:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        _log("hash")
        return hash(self.name)

    def __eq__(self, other):
        _log("eq")
        raise RuntimeError("hostile __eq__")


class _ReprRaises(str):
    def __repr__(self):
        _log("repr")
        raise RuntimeError("hostile __repr__")

    def __str__(self):
        _log("str")
        raise RuntimeError("hostile __str__")

    def __format__(self, spec):
        _log("format")
        raise RuntimeError("hostile __format__")


KEY_FORMS = {"str-subclass": _StrSub, "eq-raises": _EqRaises, "hash-collides": _Collides}


def _rekey(mapping, field, make):
    return {(make(k) if k == field else k): v for k, v in mapping.items()}


def _snap(obj):
    if isinstance(obj, dict):
        return (
            type(obj),
            [
                (type(k), k.name if type(k) is _Collides else str.__str__(k), _snap(v))
                for k, v in dict.items(obj)
            ],
        )
    if isinstance(obj, list):
        return (type(obj), [_snap(v) for v in list.__iter__(obj)])
    if isinstance(obj, str):
        return (type(obj), str.__str__(obj))
    return (type(obj), obj)


# -- hostile-type rows derived from EVERY accepted request -------------------


def _accepted_requests():
    out = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            for i, q in enumerate(row["requests"]):
                out.append((f"{section}:{row['name']}:{i}", q))
    for row in CASES["malformed"]:
        out.append((f"repair:{row['name']}", row["minimal_repair"]["request"]))
    for row in CASES["rollback"]:
        out.append((f"follow-up:{row['name']}", row["request"]))
    return out


_STR_FIELDS = ("op", "job_id", "failure")
_INT_FIELDS = ("attempts", "now")


def _variants(q):
    """(label, hostile request, expected failure class) for one accepted
    request: every container, key and value boundary."""
    out = [
        ("dict-subclass", _DictSub(q), "malformed_retry_request"),
        ("lying-dict", _LyingDict(q), "malformed_retry_request"),
        ("list-subclass", _ListSub(q.items()), "malformed_retry_request"),
        ("policy-dict-subclass", {**q, "policy": _DictSub(q["policy"])}, "malformed_retry_request"),
        ("policy-lying-dict", {**q, "policy": _LyingDict(q["policy"])}, "malformed_retry_request"),
        (
            "policy-list-subclass",
            {**q, "policy": _ListSub(q["policy"].items())},
            "malformed_retry_request",
        ),
    ]
    for field in FIELDS:
        for label, make in KEY_FORMS.items():
            out.append((f"key-{label}-{field}", _rekey(q, field, make), "malformed_retry_request"))
    for field in POLICY_FIELDS:
        for label, make in KEY_FORMS.items():
            out.append(
                (
                    f"policy-key-{label}-{field}",
                    {**q, "policy": _rekey(q["policy"], field, make)},
                    "invalid_retry_policy",
                )
            )
        value = q["policy"][field]
        out.append(
            (
                f"policy-int-subclass-{field}",
                {**q, "policy": {**q["policy"], field: _IntSub(value)}},
                "invalid_retry_policy",
            )
        )
        out.append(
            (
                f"policy-bool-{field}",
                {**q, "policy": {**q["policy"], field: True}},
                "invalid_retry_policy",
            )
        )
    for field in _STR_FIELDS:
        out.append(
            (f"str-subclass-{field}", {**q, field: _StrSub(q[field])}, "malformed_retry_request")
        )
        out.append(
            (f"repr-raises-{field}", {**q, field: _ReprRaises(q[field])}, "malformed_retry_request")
        )
        out.append(
            (f"eq-raises-{field}", {**q, field: _EqRaises(q[field])}, "malformed_retry_request")
        )
    for field in _INT_FIELDS:
        out.append(
            (f"int-subclass-{field}", {**q, field: _IntSub(q[field])}, "malformed_retry_request")
        )
        out.append((f"bool-{field}", {**q, field: True}, "malformed_retry_request"))
    return out


def _hostile(failure_class, request):
    """Typed class, fresh error, no user code ran, input unchanged."""
    snap = _snap(request)
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            _decide(request)
            got = "accepted"
        except _REF.RetryError as exc:
            got = (
                type(exc) is _REF.RetryError,
                exc.failure_class,
                exc.code,
                exc.retryable,
                exc.__cause__ is None and exc.__context__ is None,
            )
        except BaseException as exc:  # noqa: BLE001 - a raw escape is the failure
            got = ("raw", type(exc).__name__)
        calls = list(HOSTILE)
    finally:
        _Armed.on = False
    assert got == (True, failure_class, FAILURE_MAPPING[failure_class], False, True), got
    assert calls == [], calls
    assert _snap(request) == snap


_ACCEPTED = _accepted_requests()


@pytest.mark.parametrize("label", [label for label, _ in _ACCEPTED])
def test_hostile_variants_of_every_accepted_request(label):
    (q,) = [q for lbl, q in _ACCEPTED if lbl == label]
    assert _fails(q) is None  # the base request decides
    for vlabel, hostile, failure_class in _variants(copy.deepcopy(q)):
        try:
            _hostile(failure_class, hostile)
        except AssertionError as exc:
            raise AssertionError(f"{label} {vlabel}: {exc}") from None


def test_variant_set_is_complete():
    q = _ACCEPTED[0][1]
    labels = [label for label, _, _ in _variants(q)]
    assert len(labels) == len(set(labels))
    assert len(labels) == 6 + 3 * len(FIELDS) + 5 * len(POLICY_FIELDS) + 3 * 3 + 2 * 2


# -- the T0311 reference mutants must each turn this fixture red -------------


def _fixture_red():
    """Labels of every fixture check that disagrees with the reference
    currently bound (empty when green)."""
    red = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            try:
                got = [_decide(copy.deepcopy(q)) for q in row["requests"]]
            except BaseException:  # noqa: BLE001
                got = None
            if got != row["expect_records"]:
                red.append(f"{section}:{row['name']}")
    for row in CASES["malformed"]:
        for label, check in (
            ("", lambda row=row: test_malformed(row["name"])),
            ("repair", lambda row=row: test_malformed_minimal_repair_succeeds(row["name"])),
        ):
            try:
                check()
            except BaseException:  # noqa: BLE001
                red.append(f"malformed:{row['name']}{':' + label if label else ''}")
    for row in CASES["rollback"]:
        try:
            test_rollback(row["name"])
        except BaseException:  # noqa: BLE001
            red.append(f"rollback:{row['name']}")
    for label, _q in _ACCEPTED[:1] + _ACCEPTED[-1:]:
        try:
            test_hostile_variants_of_every_accepted_request(label)
        except BaseException:  # noqa: BLE001
            red.append(f"hostile:{label}")
    try:
        test_validation_precedence()
    except BaseException:  # noqa: BLE001
        red.append("precedence")
    return red


def test_reference_is_green():
    assert _fixture_red() == []


@pytest.mark.parametrize("name", sorted(_REF.REFERENCE_EDITS))
def test_reference_mutants_turn_the_fixture_red(name, monkeypatch):
    for key, value in _REF._mutant_reference(name).items():
        monkeypatch.setattr(_REF, key, value)
    assert _fixture_red() != [], name


@pytest.mark.parametrize("name", sorted(_REF.EQUIVALENT_EDITS))
def test_equivalent_edits_keep_the_fixture_green(name, monkeypatch):
    for key, value in _REF._mutant_reference(name).items():
        monkeypatch.setattr(_REF, key, value)
    assert _fixture_red() == [], name


# Extra one-edit mutants of the T0311 reference that this fixture kills on
# its own (the type and grammar guards the hostile rows exist for).
FIXTURE_EDITS = {
    "keys-off": ("        and set(mapping) == set(fields)\n", ""),
    "key-guard-off": ("        and all(type(k) is str for k in mapping)\n", ""),
    "int-type-off": ("return type(value) is int and lo <= value", "return lo <= value"),
    "op-type-off": ('        type(request["op"]) is str\n        and ', "        "),
    "op-check-off": ('        and request["op"] == "decide"\n', ""),
    "job-id-type-off": ('        and type(request["job_id"]) is str\n', ""),
    "job-id-grammar-off": (
        '        and _JOB_RE.fullmatch(request["job_id"]) is not None\n',
        "",
    ),
    "failure-type-off": ('        and type(request["failure"]) is str\n', ""),
    "failure-enum-off": ('        and request["failure"] in _FAILURES\n', ""),
    "policy-type-off": ('        and type(request["policy"]) is dict\n', ""),
    "attempts-lower-zero": (
        '_int_in(request["attempts"], 1, MAX_ATTEMPTS)',
        '_int_in(request["attempts"], 0, MAX_ATTEMPTS)',
    ),
    "attempts-upper-plus": (
        '_int_in(request["attempts"], 1, MAX_ATTEMPTS)',
        '_int_in(request["attempts"], 1, MAX_ATTEMPTS + 1)',
    ),
    "now-lower-minus": (
        '_int_in(request["now"], 0, MAX_NOW)',
        '_int_in(request["now"], -1, MAX_NOW)',
    ),
    "now-upper-plus": (
        '_int_in(request["now"], 0, MAX_NOW)',
        '_int_in(request["now"], 0, MAX_NOW + 1)',
    ),
    "base-lower-zero": (
        '_int_in(policy["base_delay_ms"], 1, _REF_MAX_BASE)',
        '_int_in(policy["base_delay_ms"], 0, _REF_MAX_BASE)',
    ),
    "mult-lower-zero": (
        '_int_in(policy["multiplier"], 1, _REF_MAX_MULT)',
        '_int_in(policy["multiplier"], 0, _REF_MAX_MULT)',
    ),
    "max-below-base-ok": (
        '_int_in(policy["max_delay_ms"], policy["base_delay_ms"], _REF_MAX_DELAY)',
        '_int_in(policy["max_delay_ms"], 1, _REF_MAX_DELAY)',
    ),
    "clock-edge-plus-one": ("if retry_at > MAX_NOW:", "if retry_at > MAX_NOW + 1:"),
    "clock-edge-inclusive": ("if retry_at > MAX_NOW:", "if retry_at >= MAX_NOW:"),
    "policy-before-request": (
        "    if not _request_ok(request):\n"
        '        failed = "malformed_retry_request"\n'
        '    elif not _policy_ok(request["policy"]):\n'
        '        failed = "invalid_retry_policy"\n',
        '    if type(request) is dict and type(request.get("policy")) is dict'
        ' and not _policy_ok(request["policy"]):\n'
        '        failed = "invalid_retry_policy"\n'
        "    elif not _request_ok(request):\n"
        '        failed = "malformed_retry_request"\n',
    ),
}


@pytest.mark.parametrize("name", sorted(FIXTURE_EDITS))
def test_fixture_edits_turn_the_fixture_red(name, monkeypatch):
    monkeypatch.setattr(_REF, "REFERENCE_EDITS", {**_REF.REFERENCE_EDITS, **FIXTURE_EDITS})
    for key, value in _REF._mutant_reference(name).items():
        monkeypatch.setattr(_REF, key, value)
    assert _fixture_red() != [], name


def test_fixture_edits_are_new():
    assert not set(FIXTURE_EDITS) & set(_REF.REFERENCE_EDITS)
    assert not set(FIXTURE_EDITS) & set(_REF.EQUIVALENT_EDITS)
