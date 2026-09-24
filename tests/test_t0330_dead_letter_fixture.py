"""T0330: jobs dead-letter conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the T0329
jobs-dead-letter contract (data/contracts/dead_letter.yaml). The cases
execute against the contract-derived reference bury in
tests.test_t0329_dead_letter_contract - nothing is re-implemented here.
Pinned dead-letter entries were computed from that reference at
authoring time, so any contract or derivation drift breaks this battery.
Every malformed case is discriminating: applying ONLY its declared
single-locus repair makes it bury. Rollback cases prove a rejected
burial leaves the exact supplied request type-exactly unchanged before a
valid follow-up buries as pinned.

PROVISIONAL row: permanent_dead_below_max_provisional pins the T0329
fail-closed reading (a permanent-failure dead job below max_attempts is
corrupt_job) pending the owner ruling on the retry permanent-dead rule vs
the queue dead invariant. If the ruling goes the other way, that row and
its manifest entry change together with the contract.

Hostile-type rows are derived here from EVERY request the fixture
accepts: hostile containers on the request, the job and the payload,
str-subclass keys in three forms on every request and job field, and
str-subclass, raising eq/repr, nested, int-subclass, bool and float
values, plus payload shapes JSON cannot store (non-finite floats,
aliased containers, non-str keys, hostile scalars inside the payload).
Each must fail typed with a fresh error, run no user code and leave the
input unchanged.

Every one-edit reference mutant pinned by the T0329 battery must turn
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

import tests.test_t0329_dead_letter_contract as _REF  # noqa: E402
from tools.dead_letter_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "dead_letter" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECORD_FIELDS = list(_CC["record"]["fields"])
FIELDS = _CC["request"]["operations"][0]["fields"]
REASONS = tuple(_CC["request"]["reasons"])
MAX_ATTEMPTS = _CC["request"]["max_attempts"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
JOB_FIELDS = list(_QUEUE["state"]["job_fields"])
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_PD_RE = re.compile(_CC["identifiers"]["payload_digest"]["grammar"], re.ASCII)
_DL_RE = re.compile(_CC["identifiers"]["entry_id"]["grammar"], re.ASCII)
MAX = 9007199254740991  # 2**53 - 1, written out
DEPTH = 64
DIGITS = 4000

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "requests", "expect_records"}
BAD_KEYS = {"name", "defect", "expect_failure", "request", "minimal_repair"}
RB_KEYS = {"name", "why", "rejected_request", "expect_failure", "request", "expect_record"}


def _bury(request):
    return _REF.bury(request)  # late-bound: reference mutants rebind it


def _fails(request):
    try:
        _bury(copy.deepcopy(request))
    except _REF.DeadLetterError as exc:
        return exc.failure_class
    return None


def _preimage(prefix, value):
    return _REF._independent(prefix, value)


def _validate_record(record, label):
    assert type(record) is dict, label
    assert all(type(k) is str for k in record) and set(record) == set(RECORD_FIELDS), label
    assert record["op"] == "bury", label
    assert _JOB_RE.fullmatch(record["job_id"]), label
    assert _PD_RE.fullmatch(record["payload_digest"]), label
    assert _DL_RE.fullmatch(record["entry_id"]), label
    assert record["attempts"] == MAX_ATTEMPTS and record["reason"] in REASONS, label
    assert record["entry_id"] == _preimage("dl1", {k: record[k] for k in RECORD_FIELDS[:-1]})


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert all(type(k) is str for k in request) and set(request) == set(FIELDS), label
    assert type(request["job"]) is dict and set(request["job"]) == set(JOB_FIELDS), label


def _validate_structure(cases):
    assert type(cases) is dict and set(cases) == TOP_KEYS
    assert type(cases["schema"]) is int and cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == _CC["versioning"]["base_path"]
    assert type(cases["notes"]) is str and "PROVISIONAL" in cases["notes"]
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
                    job = request["job"]
                    for field in ("job_id", "seq", "priority", "attempts"):
                        assert record[field] == job[field], label
                    assert record["reason"] == request["reason"], label
                    assert record["buried_at"] == request["now"], label
                    assert record["payload_digest"] == _preimage("pd1", job["payload"]), label
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


def _summary(row):
    return [
        (rec["seq"], rec["priority"], rec["reason"], rec["buried_at"])
        for rec in row["expect_records"]
    ]


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> [(seq, priority, reason, buried_at)]; malformed:
# name -> (failure class, pinned defect text); rollback: name -> (failure
# class, follow-up reason)
_E, _P = "exhausted", "permanent"
HAPPY_MANIFEST = {
    "exhausted_dead_job": [(3, 4, _E, 100)],
    "permanent_at_max_attempts": [(3, 4, _P, 100)],
    "deterministic_same_request": [(3, 4, _E, 7), (3, 4, _E, 7)],
    "payload_bound_by_digest": [(3, 4, _E, 100), (3, 4, _E, 100)],
}
BOUNDARY_MANIFEST = {
    "now_zero": [(3, 4, _E, 0)],
    "now_at_clock_limit": [(3, 4, _E, MAX)],
    "seq_zero": [(0, 4, _E, 100)],
    "seq_at_bound": [(MAX - 1, 4, _E, 100)],
    "priority_zero": [(3, 0, _E, 100)],
    "priority_nine": [(3, 9, _E, 100)],
    "payload_depth_at_bound": [(3, 4, _E, 100)],
    "payload_int_4000_digits": [(3, 4, _E, 100)],
    "payload_null": [(3, 4, _E, 100)],
    "payload_non_ascii": [(3, 4, _E, 100)],
}
_MB, _CJ, _ND = "malformed_bury_request", "corrupt_job", "job_not_dead"
MALFORMED_MANIFEST = {
    "request_not_a_dict": (_MB, "the request is a list"),
    "unknown_op": (_MB, "op is 'redrive', not a registered operation"),
    "extra_field": (_MB, "the request carries an extra note field"),
    "missing_field": (_MB, "the request has no now field"),
    "renamed_field": (_MB, "the now field is renamed to at, keeping the field count"),
    "reason_unknown": (_MB, "reason is 'timeout', not exhausted or permanent"),
    "reason_none": (_MB, "reason is null"),
    "job_not_a_dict": (_MB, "the job is a list"),
    "now_negative": (_MB, "now is -1"),
    "now_past_clock": (_MB, "now is 2**53"),
    "now_bool": (_MB, "now is the bool True"),
    "job_extra_field": (_CJ, "the job carries an extra note field"),
    "job_renamed_field": (_CJ, "the job's payload is renamed to data, keeping the field count"),
    "job_id_trailing_newline": (_CJ, "the job id ends in a newline"),
    "seq_past_bound": (_CJ, "seq is 2**53-1, one past the seq bound"),
    "priority_ten": (_CJ, "priority is 10"),
    "seq_negative": (_CJ, "seq is -1, one below the seq floor"),
    "priority_negative": (_CJ, "priority is -1, one below the priority floor"),
    "status_unknown": (_CJ, "status is 'buried', not a queue status"),
    "dead_below_max_attempts": (
        _CJ,
        "an exhausted dead job carries attempts 4, not max_attempts 5",
    ),
    "permanent_dead_below_max_provisional": (
        _CJ,
        "PROVISIONAL, fail-closed: a permanent-failure dead job at attempts 4 is refused"
        " as corrupt_job pending the owner ruling on retry permanent-dead vs the queue"
        " dead invariant",
    ),
    "dead_with_lease_owner": (_CJ, "a dead job holds lease_owner w-1"),
    "dead_with_lease_expiry": (_CJ, "a dead job holds lease_expires_at 200"),
    "payload_depth_past_bound": (_CJ, "the payload is nested 65 containers deep"),
    "payload_int_4001_digits": (
        _CJ,
        "a payload int has 4001 digits (10**4000, inside the bit pre-screen)",
    ),
    "payload_lone_surrogate": (_CJ, "a payload string holds a lone surrogate"),
    "status_ready": (_ND, "a valid ready job is not dead"),
    "status_leased": (_ND, "a valid leased job is not dead"),
    "status_done": (_ND, "a valid done job is not dead"),
}
ROLLBACK_MANIFEST = {
    "corrupt_then_lease_cleared": (_CJ, _E),
    "not_dead_then_dead": (_ND, _E),
    "malformed_then_repaired": (_MB, _P),
}

# content binding: canonical sha256 of every row. Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:exhausted_dead_job": "cff7c4af4ee0acbc977b0e4371fba887c8cbed7e5dedb8fa7c9d5f6cd78b39ab",  # noqa: E501
    "happy:permanent_at_max_attempts": "db7255269805378b80c1e53c8619cb9334b10356522e55b82a21931d17cfe0c3",  # noqa: E501
    "happy:deterministic_same_request": "a1c23d9395d4c4f27527885c01716a56a5455bb5460c7b36ebc9ee64f69d91bb",  # noqa: E501
    "happy:payload_bound_by_digest": "b24b78938e78c526eae49be3531bec363659a38818eea596ad487df873c6b30b",  # noqa: E501
    "boundary:now_zero": "073b568ab2b9dc025c18061b72edf1b46cd9591f0db6ca539c491fbf36d40a55",  # noqa: E501
    "boundary:now_at_clock_limit": "28f741d8925cf5b18273a2301cea7cf726848c92619e8ee1ce53612d34ca3f05",  # noqa: E501
    "boundary:seq_zero": "fd380c34edea1317141e5f76beba0f5ed6deb4b048cff2cec962a9b393215245",  # noqa: E501
    "boundary:seq_at_bound": "287de6938407e69dfe8f4ad145d33651a51a9e09e6d019f2429415cba9193919",  # noqa: E501
    "boundary:priority_zero": "396fe9dc77e9efa751025d3cfa4e157d036232a8bf403f1ed3be515f749ba7a7",  # noqa: E501
    "boundary:priority_nine": "d61831ffb605d3fb451ad2173b5262ff5dc752885192d31ae2125ec8e796d3d4",  # noqa: E501
    "boundary:payload_depth_at_bound": "e1830417e202271ac27140f1bdf44cbfbcf6b7bc751145c2c8f86a10a0fa497a",  # noqa: E501
    "boundary:payload_int_4000_digits": "3f3ae70f08af6af551434b9097d1d22ebd36feea0c11a746dc3013a59e56c5e2",  # noqa: E501
    "boundary:payload_null": "b596eebcad12d2a4ed96912f8aabfddd9018644fc0a079150e99593c3b7f25d8",  # noqa: E501
    "boundary:payload_non_ascii": "6523f13815002336a1678a8b507d86a28025ce8cad4b25b686bb8d5188c08c17",  # noqa: E501
    "malformed:request_not_a_dict": "5b8246577e3df804617414b71e5e1a892d3273d620a14d604ecc622ad088e4f3",  # noqa: E501
    "malformed:unknown_op": "1f4cf601b36792f51a805b246a868cc0ddb0a63c94d818192bf5b75f513a4179",  # noqa: E501
    "malformed:extra_field": "1227504a909c45267dfa4d64ad155b6e0de8bf5311c7e4e6cff5f1a7368e6666",  # noqa: E501
    "malformed:missing_field": "aac793e83340d23651fe3f5c8a55b5186ed7126baad1fea73a69846ddb659726",  # noqa: E501
    "malformed:renamed_field": "e605ad50e7ba565d61251e6f9f201051c7d523d4051e28ae25d9a2935cf12cbe",  # noqa: E501
    "malformed:reason_unknown": "e91b9cb982e28745086715c4da6c1f1ee5572cfa6ca6af99b7bd7c21d1e26b05",  # noqa: E501
    "malformed:reason_none": "f233f67992216a9513ac84e1daa91e026ca82ef34eb7a71678158e7d1a7413f4",  # noqa: E501
    "malformed:job_not_a_dict": "8dc7153129f88c48bdce890d0aba09061821fcc0f638b9fe3386b33b197fe01d",  # noqa: E501
    "malformed:now_negative": "cf3d63ae454aef0aa78df90f5ff093512012520fb0c29caabfb250be399a5313",  # noqa: E501
    "malformed:now_past_clock": "fc172fa57f995e8f5380c87eaa44e01826aabaf933432d202d88b144f7882673",  # noqa: E501
    "malformed:now_bool": "6b00ee388a884e248e9f2009a05c29117fcdae0371a58fd598776dcb7361d99c",  # noqa: E501
    "malformed:job_extra_field": "9af86016c7c4d171d66c3a221efd6724e74c94bbb4fd5156a10205103a79e74f",  # noqa: E501
    "malformed:job_renamed_field": "7ea5f8ff963b3208be6a8bdf1e96b19a6cf0d44d8fb3c684e054607473d64c8e",  # noqa: E501
    "malformed:job_id_trailing_newline": "966e23103bc5d0eeb1bcb0c0f6c772224abb6377b2e05ffe38ff06a8aca74093",  # noqa: E501
    "malformed:seq_past_bound": "035b819035b9030e9fdbec76b85936b763ca4c22195de013f1a4508bd9f7f5b5",  # noqa: E501
    "malformed:priority_ten": "0bb6321b4d1f4bc0983aa543e8c1ba09067b9a682b54887dcc05c224ac355376",  # noqa: E501
    "malformed:seq_negative": "5971e5f643f02d607cd7157324fbb4c54437f3bc6e8ac18cfeb5f25e22617a40",  # noqa: E501
    "malformed:priority_negative": "6b6fd5a71d12d132c702a378d63eaceab7a6407f95552f97cb1eca5a4ca02a5d",  # noqa: E501
    "malformed:status_unknown": "965c10d82c8b7be2c7a7989777c3c7d176faf4601e9bce263897af3b25b01b8e",  # noqa: E501
    "malformed:dead_below_max_attempts": "1a58fd68a3051d027855d6d708a060c8b9a6694d6f9f3716bbef3dbfc1f48b67",  # noqa: E501
    "malformed:permanent_dead_below_max_provisional": "3f281b7d2df8bbcd557cda6cacc367d3c56d3639f4284714bd530c36385eebf4",  # noqa: E501
    "malformed:dead_with_lease_owner": "7f77fe7d3bf00f84fca5a81d80250531d14e269c9d3ddba9d393f9320a6a7240",  # noqa: E501
    "malformed:dead_with_lease_expiry": "bde63e4b585f960ac5cb963e1f47acd551170b09c66fd7704c760035e330490e",  # noqa: E501
    "malformed:payload_depth_past_bound": "e0b7bee107814716b8d450d096207189db0da04140bc69f03473cd93d4db1c52",  # noqa: E501
    "malformed:payload_int_4001_digits": "da78f67fdcab968bdc45b5a12251a848dc0c334b747c77a5487186a13041b5f5",  # noqa: E501
    "malformed:payload_lone_surrogate": "7634041bd035bda437421e4b56674af26df01ced36c93fe8c4de5458d3ece9ca",  # noqa: E501
    "malformed:status_ready": "458090076cbde6af35969ed9eebfba0586869cfb1157a8f8f561208e458c1e84",  # noqa: E501
    "malformed:status_leased": "259742623a148d5a0e100534d8b97b6a49db57b4f98d3444d1761b8ae8d02feb",  # noqa: E501
    "malformed:status_done": "6d0660794b00ee8da4c738f87dcf3bdb1286cde3dfe8356e496e1363362ec6d5",  # noqa: E501
    "rollback:corrupt_then_lease_cleared": "9eca72aa1b1cebf629a18659967c5a60c211a83a25e664e7e4351e7d42b8671c",  # noqa: E501
    "rollback:not_dead_then_dead": "22b63a8048c3ab7a752ca1bb71cf2171eca669dd908d20238047e5842320b780",  # noqa: E501
    "rollback:malformed_then_repaired": "ad60296f7343cbb626660532fa4d4392ecf93553c6797f0896568d325af79e4f",  # noqa: E501
}

MANIFESTS = {
    "happy": HAPPY_MANIFEST,
    "boundary": BOUNDARY_MANIFEST,
    "malformed": MALFORMED_MANIFEST,
    "rollback": ROLLBACK_MANIFEST,
}


# -- per-row semantic edges over the ORIGINAL data ---------------------------


def _depth(node):
    depth = 0
    while type(node) is list:
        (node,) = node
        depth += 1
    return depth


def _one(r):
    (q,) = r["requests"]
    return q, q["job"]


def _h_exhausted(r):
    q, job = _one(r)
    assert (q["reason"], job["status"], job["attempts"]) == (_E, "dead", MAX_ATTEMPTS)


def _h_permanent(r):
    q, job = _one(r)
    assert (q["reason"], job["attempts"]) == (_P, MAX_ATTEMPTS)


def _h_same(r):
    a, b = r["requests"]
    assert a == b and r["expect_records"][0] == r["expect_records"][1]


def _h_digest(r):
    a, b = r["requests"]
    assert {p[:2] for p in _diff_paths(a, b)} == {("job", "payload")}
    ra, rb = r["expect_records"]
    assert ra["payload_digest"] != rb["payload_digest"] and ra["entry_id"] != rb["entry_id"]


def _field_is(section, field, value):
    def check(r):
        q, job = _one(r)
        where = q if section == "request" else job
        assert type(where[field]) is type(value) and where[field] == value

    return check


def _b_depth(r):
    _q, job = _one(r)
    assert _depth(job["payload"]) == DEPTH


def _b_digits(r):
    _q, job = _one(r)
    (n,) = job["payload"].values()
    assert type(n) is int and len(str(n)) == DIGITS


def _b_non_ascii(r):
    _q, job = _one(r)
    assert any(ord(ch) > 127 for ch in job["payload"]["s"])


OK_EDGES = {
    "exhausted_dead_job": _h_exhausted,
    "permanent_at_max_attempts": _h_permanent,
    "deterministic_same_request": _h_same,
    "payload_bound_by_digest": _h_digest,
    "now_zero": _field_is("request", "now", 0),
    "now_at_clock_limit": _field_is("request", "now", MAX),
    "seq_zero": _field_is("job", "seq", 0),
    "seq_at_bound": _field_is("job", "seq", MAX - 1),
    "priority_zero": _field_is("job", "priority", 0),
    "priority_nine": _field_is("job", "priority", 9),
    "payload_depth_at_bound": _b_depth,
    "payload_int_4000_digits": _b_digits,
    "payload_null": _field_is("job", "payload", None),
    "payload_non_ascii": _b_non_ascii,
}

_REQUEST_EDGES = {
    # name -> (field, bad value, repaired value)
    "reason_unknown": ("reason", "timeout", _E),
    "reason_none": ("reason", None, _E),
    "now_negative": ("now", -1, 0),
    "now_past_clock": ("now", MAX + 1, MAX),
    "now_bool": ("now", True, 1),
    "unknown_op": ("op", "redrive", "bury"),
    "job_not_a_dict": ("job", [], None),
}
_JOB_EDGES = {
    "seq_past_bound": ("seq", MAX, MAX - 1),
    "priority_ten": ("priority", 10, 9),
    "seq_negative": ("seq", -1, 0),
    "priority_negative": ("priority", -1, 0),
    "status_unknown": ("status", "buried", "dead"),
    "dead_below_max_attempts": ("attempts", MAX_ATTEMPTS - 1, MAX_ATTEMPTS),
    "dead_with_lease_owner": ("lease_owner", "w-1", None),
    "dead_with_lease_expiry": ("lease_expires_at", 200, None),
    "status_ready": ("status", "ready", "dead"),
    "status_done": ("status", "done", "dead"),
}


def _same_type_eq(a, b):
    return type(a) is type(b) and a == b


def _renamed(bad, fix, old, new):
    assert type(bad) is dict and len(bad) == len(fix)
    assert set(bad) - set(fix) == {new} and set(fix) - set(bad) == {old}
    assert _same_type_eq(bad[new], fix[old])
    moved = {(new if k == old else k): v for k, v in fix.items()}
    assert _diff_paths(bad, moved) == set()  # the rename is the only change


def _m(name, bad, fix):  # noqa: C901 - one closed branch per tag
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus."""
    if name == "request_not_a_dict":
        assert type(bad) is list and bad == [fix]
    elif name in _REQUEST_EDGES:
        field, b, f = _REQUEST_EDGES[name]
        assert _same_type_eq(bad[field], b), name
        if f is not None:
            assert _same_type_eq(fix[field], f), name
            _only(bad, fix, (field,))
        else:
            assert type(fix[field]) is dict
            assert {p[0] for p in _diff_paths(bad, fix)} == {field}
    elif name == "extra_field":
        assert _same_type_eq(bad["note"], "x")
        _only(bad, fix, ("note",))
    elif name == "missing_field":
        assert set(fix) - set(bad) == {"now"}
        _only(bad, fix, ("now",))
    elif name == "renamed_field":
        _renamed(bad, fix, "now", "at")
    elif name == "job_extra_field":
        _only(bad, fix, ("job", "note"))
    elif name == "job_renamed_field":
        _renamed(bad["job"], fix["job"], "payload", "data")
        assert {p[0] for p in _diff_paths(bad, fix)} == {"job"}
    elif name == "job_id_trailing_newline":
        assert bad["job"]["job_id"] == fix["job"]["job_id"] + "\n"
        _only(bad, fix, ("job", "job_id"))
    elif name in _JOB_EDGES:
        field, b, f = _JOB_EDGES[name]
        assert _same_type_eq(bad["job"][field], b) and _same_type_eq(fix["job"][field], f)
        _only(bad, fix, ("job", field))
        assert bad["reason"] == _E, name
    elif name == "permanent_dead_below_max_provisional":
        assert (bad["reason"], fix["reason"]) == (_P, _P)
        assert (bad["job"]["attempts"], fix["job"]["attempts"]) == (MAX_ATTEMPTS - 1, MAX_ATTEMPTS)
        assert bad["job"]["status"] == "dead"
        _only(bad, fix, ("job", "attempts"))
    elif name == "status_leased":
        job = bad["job"]
        assert (job["status"], job["lease_owner"], job["lease_expires_at"]) == (
            "leased",
            "w-1",
            200,
        )
        diff = {p[1] for p in _diff_paths(bad, fix)}
        assert diff == {"status", "lease_owner", "lease_expires_at"}  # the lease state is one locus
        assert {p[0] for p in _diff_paths(bad, fix)} == {"job"}
    elif name == "payload_depth_past_bound":
        assert (_depth(bad["job"]["payload"]), _depth(fix["job"]["payload"])) == (DEPTH + 1, DEPTH)
        assert {p[:2] for p in _diff_paths(bad, fix)} == {("job", "payload")}
    elif name == "payload_int_4001_digits":
        b, f = bad["job"]["payload"]["n"], fix["job"]["payload"]["n"]
        assert (len(str(b)), len(str(f))) == (DIGITS + 1, DIGITS)
        _only(bad, fix, ("job", "payload", "n"))
    elif name == "payload_lone_surrogate":
        assert bad["job"]["payload"]["s"] == "\ud800"
        fix["job"]["payload"]["s"].encode("utf-8")
        _only(bad, fix, ("job", "payload", "s"))
    else:
        raise AssertionError(f"unknown malformed tag {name}")


def _check_rollback_scenario(row):
    name, bad, q = row["name"], row["rejected_request"], row["request"]
    if name == "corrupt_then_lease_cleared":
        assert (bad["job"]["lease_owner"], q["job"]["lease_owner"]) == ("w-1", None)
        _only(bad, q, ("job", "lease_owner"))
    elif name == "not_dead_then_dead":
        assert (bad["job"]["status"], q["job"]["status"]) == ("ready", "dead")
        _only(bad, q, ("job", "status"))
    elif name == "malformed_then_repaired":
        assert (bad["reason"], q["reason"]) == ("timeout", _P)
        _only(bad, q, ("reason",))
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
                assert _summary(row) == meta, label
                OK_EDGES[row["name"]](row)
            elif section == "malformed":
                assert (row["expect_failure"], row["defect"]) == meta, label
                _m(row["name"], row["request"], row["minimal_repair"]["request"])
            else:
                assert (row["expect_failure"], row["expect_record"]["reason"]) == meta, label
                _check_rollback_scenario(row)
    assert set(ROW_DIGESTS) == {f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    assert set(OK_EDGES) == set(HAPPY_MANIFEST) | set(BOUNDARY_MANIFEST)


def _names(section):
    return [row["name"] for row in CASES[section]]


def _row(section, name):
    (row,) = [r for r in CASES[section] if r["name"] == name]
    return row


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


def _only(bad, fix, *paths):
    assert _diff_paths(bad, fix) == set(paths), (bad, fix)


def _row_digest(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


# -- structure and closure ---------------------------------------------------


def test_fixture_structure():
    _validate_structure(CASES)


def test_fixture_scenario_closure():
    _validate_closure(CASES)


def test_param_ids_equal_manifest_names():
    for section, manifest in MANIFESTS.items():
        assert _names(section) == list(manifest), section


def test_failure_class_and_enum_coverage():
    assert {row["expect_failure"] for row in CASES["malformed"]} == FAILURE_CLASSES
    assert {row["expect_failure"] for row in CASES["rollback"]} == FAILURE_CLASSES
    assert {FAILURE_MAPPING[c] for c in FAILURE_CLASSES} <= set(ERROR_ENUM)
    reasons = {
        rec["reason"]
        for s in ("happy", "boundary")
        for row in CASES[s]
        for rec in row["expect_records"]
    }
    assert reasons == set(REASONS)
    statuses = {
        row["request"]["job"]["status"]
        for row in CASES["malformed"]
        if row["expect_failure"] == "job_not_dead"
    }
    assert statuses == set(_QUEUE["state"]["statuses"]) - {"dead"}


def test_provisional_row_is_marked_and_fail_closed():
    """PROVISIONAL (pending the owner ruling on permanent-dead attempt
    count): the row reads fail-closed as corrupt_job, the contract still
    states that reading, and the same job at max_attempts buries."""
    row = _row("malformed", "permanent_dead_below_max_provisional")
    assert row["defect"].startswith("PROVISIONAL, fail-closed:")
    assert row["expect_failure"] == "corrupt_job"
    assert "provisional" in _CC["semantics"]["provisional"]
    assert "is-corrupt-job" in _CC["semantics"]["provisional"]
    record = _bury(copy.deepcopy(row["minimal_repair"]["request"]))
    assert (record["reason"], record["attempts"]) == ("permanent", MAX_ATTEMPTS)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(schema=2),
        lambda c: c.update(schema=True),
        lambda c: c.update(contract="jobs-retry"),
        lambda c: c.update(contract_base_path="/jobs/dead-letter/v2"),
        lambda c: c.update(notes="no marker"),
        lambda c: c.update(extra=1),
        lambda c: c.pop("rollback"),
        lambda c: c["boundary"].clear(),
        lambda c: c["happy"][0].update(eta=0),
        lambda c: c["happy"][0]["expect_records"].pop(),
        lambda c: c["happy"][0]["expect_records"][0].pop("buried_at"),
        lambda c: c["happy"][0]["expect_records"][0].update(extra=1),
        lambda c: c["happy"][0]["expect_records"][0].update(entry_id="dl1:zz"),
        lambda c: c["happy"][0]["expect_records"][0].update(op="redrive"),
        lambda c: c["happy"][0]["expect_records"][0].update(attempts=4),
        lambda c: c["happy"][0]["expect_records"][0].update(seq=4),
        lambda c: c["happy"][0]["expect_records"][0].update(buried_at=1),
        lambda c: c["happy"][0]["expect_records"][0].update(reason="permanent"),
        lambda c: c["happy"][0]["expect_records"][0].update(payload_digest="pd1:" + "0" * 64),
        lambda c: c["happy"][0]["requests"][0].update(note=1),
        lambda c: c["happy"][0]["requests"][0]["job"].update(note=1),
        lambda c: c["happy"][0]["requests"][0]["job"]["payload"].update(extra=1),
        lambda c: c["malformed"][0].update(expect_failure="internal"),
        lambda c: c["malformed"][1].update(
            minimal_repair={"request": c["malformed"][1]["request"]}
        ),
        lambda c: c["malformed"][1]["minimal_repair"].update(state={}),
        lambda c: c["malformed"][1].update(minimal_repair={"job": {}}),
        lambda c: c["rollback"][0].update(expect_failure="internal"),
        lambda c: c["rollback"][0].update(request=c["rollback"][0]["rejected_request"]),
        lambda c: c["rollback"][0]["expect_record"].update(entry_id="dl1:" + "0" * 64),
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
    row["expect_records"] = [_bury(copy.deepcopy(q)) for q in row["requests"]]


def _edit(section, name, fn):
    def mutant(c):
        row = _bn(c, section, name)
        fn(row)
        if section in ("happy", "boundary"):
            _regenerate(row)

    return mutant


def _job(i, **kw):
    return lambda row: row["requests"][i]["job"].update(kw)


def _bad_job(name, **kw):
    return lambda c: _bn(c, "malformed", name)["request"]["job"].update(kw)


_CLOSURE_MUTANTS = {
    "swap_boundary_names": lambda c: _swap(c, "boundary", "seq_zero", "priority_zero", "name"),
    "swap_happy_names": lambda c: _swap(
        c, "happy", "exhausted_dead_job", "permanent_at_max_attempts", "name"
    ),
    "swap_malformed_names_same_class": lambda c: _swap(
        c, "malformed", "now_negative", "now_past_clock", "name"
    ),
    "swap_malformed_requests": lambda c: _swap(
        c, "malformed", "now_negative", "now_past_clock", "request", "minimal_repair"
    ),
    "swap_not_dead_requests": lambda c: _swap(
        c, "malformed", "status_ready", "status_done", "request", "minimal_repair"
    ),
    "swap_lease_requests": lambda c: _swap(
        c,
        "malformed",
        "dead_with_lease_owner",
        "dead_with_lease_expiry",
        "request",
        "minimal_repair",
    ),
    "drop_provisional": lambda c: c["malformed"].remove(
        _bn(c, "malformed", "permanent_dead_below_max_provisional")
    ),
    "duplicate_unknown_op": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "unknown_op"))
    ),
    "provisional_made_exhausted": lambda c: _bn(
        c, "malformed", "permanent_dead_below_max_provisional"
    )["request"].update(reason="exhausted"),
    "provisional_repair_exhausted": lambda c: _bn(
        c, "malformed", "permanent_dead_below_max_provisional"
    )["minimal_repair"]["request"].update(reason="exhausted"),
    "clock_edge_one_short": _edit(
        "boundary", "now_at_clock_limit", lambda row: row["requests"][0].update(now=MAX - 1)
    ),
    "seq_edge_one_short": _edit("boundary", "seq_at_bound", _job(0, seq=MAX - 2)),
    "priority_edge_one_short": _edit("boundary", "priority_nine", _job(0, priority=8)),
    "depth_edge_one_short": _edit("boundary", "payload_depth_at_bound", _job(0, payload=[[0]])),
    "digits_edge_one_short": _edit(
        "boundary", "payload_int_4000_digits", _job(0, payload={"n": int("9" * 3999)})
    ),
    "non_ascii_made_ascii": _edit("boundary", "payload_non_ascii", _job(0, payload={"s": "e"})),
    "digest_row_same_payload": _edit(
        "happy",
        "payload_bound_by_digest",
        lambda row: row["requests"][1].update(job=copy.deepcopy(row["requests"][0]["job"])),
    ),
    "repair_touches_two_fields": lambda c: _bn(c, "malformed", "now_negative")["minimal_repair"][
        "request"
    ].update(reason="permanent"),
    "renamed_field_second_defect": lambda c: _bn(c, "malformed", "renamed_field")["request"].update(
        reason="timeout"
    ),
    "job_renamed_second_defect": _bad_job("job_renamed_field", seq=4),
    "leased_row_second_defect": _bad_job("status_leased", priority=5),
    "lease_owner_bad_grammar": _bad_job("dead_with_lease_owner", lease_owner="w 1"),
    "status_unknown_retyped": _bad_job("status_unknown", status=None),
    "depth_row_one_short": _bad_job("payload_depth_past_bound", payload=[[0]]),
    "rollback_follow_up_widened": lambda c: _bn(c, "rollback", "not_dead_then_dead")[
        "request"
    ].update(now=101),
    "defect_text_edited": lambda c: _bn(c, "malformed", "now_bool").update(defect="now is odd"),
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
    with pytest.raises((AssertionError, KeyError, ValueError, TypeError)):
        _validate_structure(cases)
        _validate_closure(cases)


# -- execution ---------------------------------------------------------------


@pytest.mark.parametrize("section,name", [(s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_records(section, name):
    row = _row(section, name)
    requests = copy.deepcopy(row["requests"])
    assert [_bury(q) for q in requests] == row["expect_records"]
    assert requests == row["requests"]
    again = [_bury(copy.deepcopy(q)) for q in row["requests"]]
    assert again == row["expect_records"]


def test_pinned_digests_match_the_contract_preimages():
    """Every pinned payload_digest and entry_id is the hand-built contract
    preimage, so a record and its ids cannot drift together."""
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            for q, rec in zip(row["requests"], row["expect_records"], strict=True):
                assert rec["payload_digest"] == _preimage("pd1", q["job"]["payload"])
                assert rec["entry_id"] == _preimage("dl1", {k: rec[k] for k in RECORD_FIELDS[:-1]})


def test_tampered_pinned_record_is_detected():
    row = copy.deepcopy(_row("happy", "exhausted_dead_job"))
    row["expect_records"][0]["buried_at"] += 1
    assert [_bury(copy.deepcopy(q)) for q in row["requests"]] != row["expect_records"]


def _rejects(failure_class, request):
    before = copy.deepcopy(request)
    with pytest.raises(_REF.DeadLetterError) as err:
        _bury(request)
    exc = err.value
    assert type(exc) is _REF.DeadLetterError
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
    request = copy.deepcopy(row["minimal_repair"]["request"])
    record = _bury(request)
    _validate_record(record, name)
    assert request == row["minimal_repair"]["request"]


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_row("malformed", "priority_ten"))
    row["request"]["job"]["priority"] = 9
    assert _fails(row["request"]) is None


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    rejected = copy.deepcopy(row["rejected_request"])
    _rejects(row["expect_failure"], rejected)
    assert _snap(rejected) == _snap(row["rejected_request"])
    request = copy.deepcopy(row["request"])
    assert _bury(request) == row["expect_record"]
    assert _snap(request) == _snap(row["request"])


def test_validation_precedence():
    """request shape -> job integrity -> dead status, first failure wins
    (derived from fixture rows so the order runs on pinned data)."""
    corrupt = _row("malformed", "priority_ten")["request"]
    ready = _row("malformed", "status_ready")["request"]
    _rejects("malformed_bury_request", {**corrupt, "now": -1})
    _rejects("malformed_bury_request", {**ready, "reason": "timeout"})
    _rejects("corrupt_job", {**ready, "job": {**ready["job"], "priority": 10}})
    # job integrity outranks the dead check for every non-dead status too
    done = _row("malformed", "status_done")["request"]
    leased = _row("malformed", "status_leased")["request"]
    _rejects("job_not_dead", done)
    _rejects("job_not_dead", leased)
    _rejects("corrupt_job", {**done, "job": {**done["job"], "attempts": 0}})
    _rejects("corrupt_job", {**leased, "job": {**leased["job"], "attempts": 0}})


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

    def get(self, key, default=None):
        _log("get")
        return dict.get(self, key, default)


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


def _snap_key(k):
    if type(k) is _Collides:
        return k.name
    return str.__str__(k) if isinstance(k, str) else _snap(k)


def _snap(obj):
    if isinstance(obj, dict):
        return (
            type(obj),
            [(type(k), _snap_key(k), _snap(v)) for k, v in dict.items(obj)],
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


_MB_ = "malformed_bury_request"
_CJ_ = "corrupt_job"
_STR_FIELDS = ("op", "reason")
_JOB_STR_FIELDS = ("job_id", "status")
_JOB_INT_FIELDS = ("seq", "priority", "attempts")


class _FloatSub(float):
    pass


def _value_variants(prefix, mapping, str_fields, int_fields, wrap, failure):
    out = []
    for field in str_fields:
        value = mapping[field]
        for label, make in (
            ("str-subclass", _StrSub),
            ("repr-raises", _ReprRaises),
            ("eq-raises", _EqRaises),
        ):
            out.append((f"{prefix}{label}-{field}", wrap({**mapping, field: make(value)}), failure))
        out.append((f"{prefix}nested-{field}", wrap({**mapping, field: [value]}), failure))
    for field in int_fields:
        value = mapping[field]
        out.append(
            (f"{prefix}int-subclass-{field}", wrap({**mapping, field: _IntSub(value)}), failure)
        )
        out.append((f"{prefix}bool-{field}", wrap({**mapping, field: True}), failure))
        out.append((f"{prefix}float-{field}", wrap({**mapping, field: float(value)}), failure))
    return out


def _payload_variants(q):
    job = q["job"]

    def wrap(payload):
        return {**q, "job": {**job, "payload": payload}}

    shared = [1]
    payload = job["payload"]
    out = [
        ("payload-nan", wrap({"f": float("nan")})),
        ("payload-inf", wrap({"f": float("inf")})),
        ("payload-alias", wrap({"a": shared, "b": shared})),
        ("payload-int-key", wrap({1: "x"})),
        ("payload-str-subclass-key", wrap({_StrSub("k"): 1})),
        ("payload-eq-raises-key", wrap({_EqRaises("k"): 1})),
        ("payload-str-subclass-value", wrap({"k": _StrSub("v")})),
        ("payload-repr-raises-value", wrap({"k": _ReprRaises("v")})),
        ("payload-int-subclass-value", wrap({"k": _IntSub(1)})),
        ("payload-float-subclass-value", wrap({"k": _FloatSub(1.5)})),
        ("payload-tuple-value", wrap({"k": (1, 2)})),
    ]
    if type(payload) is dict:
        out += [
            ("payload-dict-subclass", wrap(_DictSub(payload))),
            ("payload-lying-dict", wrap(_LyingDict(payload))),
            ("payload-list-subclass", wrap(_ListSub(payload.items()))),
        ]
    else:
        out += [
            ("payload-dict-subclass", wrap(_DictSub())),
            ("payload-lying-dict", wrap(_LyingDict())),
            ("payload-list-subclass", wrap(_ListSub())),
        ]
    return [(label, request, _CJ_) for label, request in out]


def _variants(q):
    """(label, hostile request, expected failure class) for one accepted
    request: every container, key and value boundary on the request, the
    job and the payload, plus payload shapes JSON cannot store."""
    job = q["job"]
    out = [
        ("dict-subclass", _DictSub(q), _MB_),
        ("lying-dict", _LyingDict(q), _MB_),
        ("list-subclass", _ListSub(q.items()), _MB_),
        ("job-dict-subclass", {**q, "job": _DictSub(job)}, _MB_),
        ("job-lying-dict", {**q, "job": _LyingDict(job)}, _MB_),
        ("job-list-subclass", {**q, "job": _ListSub(job.items())}, _MB_),
    ]
    for field in FIELDS:
        for label, make in KEY_FORMS.items():
            out.append((f"key-{label}-{field}", _rekey(q, field, make), _MB_))
    for field in JOB_FIELDS:
        for label, make in KEY_FORMS.items():
            out.append((f"job-key-{label}-{field}", {**q, "job": _rekey(job, field, make)}, _CJ_))
    out += _value_variants("", q, _STR_FIELDS, ("now",), lambda m: m, _MB_)
    out += _value_variants(
        "job-", job, _JOB_STR_FIELDS, _JOB_INT_FIELDS, lambda m: {**q, "job": m}, _CJ_
    )
    out += _payload_variants(q)
    return out


def _hostile(failure_class, request):
    """Typed class, fresh error, no user code ran, input unchanged."""
    snap = _snap(request)
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            _bury(request)
            got = "accepted"
        except _REF.DeadLetterError as exc:
            got = (
                type(exc) is _REF.DeadLetterError,
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
    assert _fails(q) is None  # the base request reports
    for vlabel, hostile, failure_class in _variants(copy.deepcopy(q)):
        try:
            _hostile(failure_class, hostile)
        except AssertionError as exc:
            raise AssertionError(f"{label} {vlabel}: {exc}") from None


def test_variant_set_is_complete():
    q = _ACCEPTED[0][1]
    labels = [label for label, _, _ in _variants(q)]
    assert len(labels) == len(set(labels))
    want = (
        6
        + 3 * len(FIELDS)
        + 3 * len(JOB_FIELDS)
        + 4 * len(_STR_FIELDS)
        + 3
        + 4 * len(_JOB_STR_FIELDS)
        + 3 * len(_JOB_INT_FIELDS)
        + 14
    )
    assert len(labels) == want
    assert {len(_variants(x)) for _, x in _ACCEPTED} == {want}


# -- the T0329 reference mutants must each turn this fixture red -------------


def _fixture_red():
    """Labels of every fixture check that disagrees with the reference
    currently bound (empty when green)."""
    red = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            try:
                got = [_bury(copy.deepcopy(q)) for q in row["requests"]]
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
    for label, _q in _HOSTILE_PROBES:
        try:
            test_hostile_variants_of_every_accepted_request(label)
        except BaseException:  # noqa: BLE001
            red.append(f"hostile:{label}")
    try:
        test_validation_precedence()
    except BaseException:  # noqa: BLE001
        red.append("precedence")
    return red


# the first and last accepted requests probe the hostile rows under every
# mutant (the full set runs in the parametrized test)
_HOSTILE_PROBES = [_ACCEPTED[0], _ACCEPTED[-1]]


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


# Extra one-edit mutants of the T0329 reference that this fixture kills on
# its own (the type, grammar, bound and lease guards the rows exist for).
FIXTURE_EDITS = {
    "op-check-off": ('        and request["op"] == "bury"\n', ""),
    "job-dict-any": (
        'and type(request["job"]) is dict',
        'and isinstance(request["job"], dict)',
    ),
    "reason-type-off": ('        and type(request["reason"]) is str\n', ""),
    "now-lower-minus": (
        '_int_in(request["now"], 0, _REF_MAX_NOW)',
        '_int_in(request["now"], -1, _REF_MAX_NOW)',
    ),
    "seq-lower-minus": (
        '_int_in(job["seq"], 0, _REF_MAX_SEQ)',
        '_int_in(job["seq"], -1, _REF_MAX_SEQ)',
    ),
    "priority-lower-minus": (
        '_int_in(job["priority"], 0, _REF_MAX_PRIORITY)',
        '_int_in(job["priority"], -1, _REF_MAX_PRIORITY)',
    ),
    "job-grammar-off": ('        _grammar(job["job_id"], _JOB_RE)\n        and ', "        "),
    "status-enum-off": ("        and status in _STATUSES\n", ""),
    "owner-none-off": (
        "elif owner is not None or expires is not None:",
        "elif expires is not None:",
    ),
    "expires-none-off": (
        "elif owner is not None or expires is not None:",
        "elif owner is not None:",
    ),
    "payload-digest-of-job": ('_digest("pd1", job["payload"])', '_digest("pd1", job)'),
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
    assert len({new for _old, new in FIXTURE_EDITS.values()}) >= 1
