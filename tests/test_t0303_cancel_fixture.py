"""T0303: jobs cancel conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0302 jobs-cancel
contract (data/contracts/cancel.yaml). The cases execute against the
contract-derived reference cancel in tests.test_t0302_cancel_contract -
nothing is re-implemented here. Pinned cancellation records were computed
from that reference at authoring time, so any contract or derivation
drift breaks this battery. Every malformed case is discriminating:
applying ONLY its declared single-locus repair makes it cancel, except
that a corrupt done/dead job (flagged by minimal_repair.expect_failure)
repairs to a still-not-cancellable job that fails job_not_cancellable,
which pins job integrity ahead of the cancellable check. Rollback cases
prove a rejected cancel leaves the exact supplied request type-exactly
unchanged before a valid follow-up cancels as pinned.

Hostile-type rows are derived here from EVERY request the fixture
accepts: hostile containers on the request, the job and the payload,
str-subclass keys in three forms on every request and job field, and
str-subclass, raising eq/repr, nested, int-subclass, bool and float
values, plus payload shapes JSON cannot store (non-finite floats,
aliased containers, non-str keys, hostile scalars inside the payload).
Each must fail typed with a fresh error, run no user code and leave the
input unchanged.

Every one-edit reference mutant pinned by the T0302 battery must turn
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

import tests.test_t0302_cancel_contract as _REF  # noqa: E402
from tools.cancel_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "cancel" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECORD_FIELDS = list(_CC["record"]["fields"])
STATUSES_BEFORE = tuple(_CC["record"]["statuses_before"])
FIELDS = _CC["request"]["operations"][0]["fields"]
MAX_ATTEMPTS = _CC["request"]["max_attempts"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
JOB_FIELDS = list(_QUEUE["state"]["job_fields"])
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_PD_RE = re.compile(_CC["identifiers"]["payload_digest"]["grammar"], re.ASCII)
_CD_RE = re.compile(_CC["identifiers"]["decision_id"]["grammar"], re.ASCII)
MAX = 9007199254740991  # 2**53 - 1, written out
DEPTH = 64
DIGITS = 4000

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "requests", "expect_records"}
BAD_KEYS = {"name", "defect", "expect_failure", "request", "minimal_repair"}
REPAIR_NC_KEYS = {"request", "expect_failure"}
_NC_CLASS = "job_not_cancellable"
RB_KEYS = {"name", "why", "rejected_request", "expect_failure", "request", "expect_record"}


def _cancel(request):
    return _REF.cancel(request)  # late-bound: reference mutants rebind it


def _fails(request):
    try:
        _cancel(copy.deepcopy(request))
    except _REF.CancelError as exc:
        return exc.failure_class
    return None


def _preimage(prefix, value):
    return _REF._independent(prefix, value)


def _validate_record(record, label):
    assert type(record) is dict, label
    assert all(type(k) is str for k in record) and set(record) == set(RECORD_FIELDS), label
    assert record["op"] == "cancel", label
    assert _JOB_RE.fullmatch(record["job_id"]), label
    assert _PD_RE.fullmatch(record["payload_digest"]), label
    assert _CD_RE.fullmatch(record["decision_id"]), label
    assert record["status_before"] in STATUSES_BEFORE, label
    assert record["decision_id"] == _preimage("cd1", {k: record[k] for k in RECORD_FIELDS[:-1]}), (
        label
    )


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert all(type(k) is str for k in request) and set(request) == set(FIELDS), label
    assert type(request["job"]) is dict and set(request["job"]) == set(JOB_FIELDS), label


def _validate_structure(cases):
    assert type(cases) is dict and set(cases) == TOP_KEYS
    assert type(cases["schema"]) is int and cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == _CC["versioning"]["base_path"]
    assert type(cases["notes"]) is str and "Closed fixture for the T0302" in cases["notes"]
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
                    assert record["status_before"] == job["status"], label
                    assert record["requester"] == request["requester"], label
                    assert record["cancelled_at"] == request["now"], label
                    assert record["payload_digest"] == _preimage("pd1", job["payload"]), label
            elif section == "malformed":
                assert set(row) == BAD_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                assert type(row["defect"]) is str and row["defect"], label
                repair = row["minimal_repair"]
                assert type(repair) is dict and set(repair) in ({"request"}, REPAIR_NC_KEYS), label
                if set(repair) == REPAIR_NC_KEYS:
                    # a corrupt done/dead job: its repair is still not cancellable
                    assert repair["expect_failure"] == _NC_CLASS, label
                    assert row["expect_failure"] == "corrupt_job", label
                    assert repair["request"]["job"]["status"] in ("done", "dead"), label
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
        (
            rec["seq"],
            rec["priority"],
            rec["status_before"],
            rec["attempts"],
            rec["requester"],
            rec["cancelled_at"],
        )
        for rec in row["expect_records"]
    ]


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> [(seq, priority, status_before, attempts,
# requester, cancelled_at)]; malformed: name -> (failure class, pinned
# defect text); rollback: name -> (failure class, follow-up status_before)
_R, _L = "ready", "leased"
HAPPY_MANIFEST = {
    "ready_withdrawn_before_claim": [(3, 4, _R, 0, "owner-1", 100)],
    "leased_recorded_against_live_lease": [(3, 4, _L, 2, "owner-1", 100)],
    "deterministic_same_request": [(3, 4, _R, 0, "owner-1", 7), (3, 4, _R, 0, "owner-1", 7)],
    "payload_bound_by_digest": [
        (3, 4, _R, 0, "owner-1", 100),
        (3, 4, _R, 0, "owner-1", 100),
    ],
}
BOUNDARY_MANIFEST = {
    "now_zero": [(3, 4, _R, 0, "owner-1", 0)],
    "now_at_clock_limit": [(3, 4, _R, 0, "owner-1", MAX)],
    "seq_zero": [(0, 4, _R, 0, "owner-1", 100)],
    "seq_at_bound": [(MAX - 1, 4, _R, 0, "owner-1", 100)],
    "priority_zero": [(3, 0, _R, 0, "owner-1", 100)],
    "priority_nine": [(3, 9, _R, 0, "owner-1", 100)],
    "payload_depth_at_bound": [(3, 4, _R, 0, "owner-1", 100)],
    "payload_int_4000_digits": [(3, 4, _R, 0, "owner-1", 100)],
    "payload_null": [(3, 4, _R, 0, "owner-1", 100)],
    "payload_non_ascii": [(3, 4, _R, 0, "owner-1", 100)],
    "requester_one_char": [(3, 4, _R, 0, "a", 100)],
    "requester_at_grammar_limit": [
        (3, 4, _R, 0, "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", 100)
    ],  # noqa: E501
}
_MB, _CJ, _NC = "malformed_cancel_request", "corrupt_job", "job_not_cancellable"
MALFORMED_MANIFEST = {
    "request_not_a_dict": (_MB, "the request is a list"),
    "unknown_op": (_MB, "op is 'redrive', not a registered operation"),
    "extra_field": (_MB, "the request carries an extra note field"),
    "missing_field": (_MB, "the request has no now field"),
    "renamed_field": (_MB, "the now field is renamed to at, keeping the field count"),
    "requester_none": (_MB, "the requester is null"),
    "requester_empty": (_MB, "the requester is empty"),
    "requester_too_long": (_MB, "the requester is 65 chars, past the 64-char grammar bound"),
    "requester_bad_char": (_MB, "the requester holds a space, outside the grammar"),
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
    "status_unknown": (_CJ, "status is 'cancelled', not a queue status"),
    "ready_with_lease_owner": (_CJ, "a ready job holds lease_owner w-1"),
    "ready_attempts_below_floor": (_CJ, "a ready job carries attempts -1"),
    "leased_missing_owner": (_CJ, "a leased job holds no lease_owner"),
    "leased_owner_bad_grammar": (
        _CJ,
        "a leased job's lease_owner is '!!', outside the worker grammar",
    ),
    "leased_expiry_zero": (_CJ, "a leased job's lease_expires_at is 0"),
    "leased_expiry_past_bound": (_CJ, "a leased job's lease_expires_at is 2**53"),
    "leased_attempts_zero": (_CJ, "a leased job carries attempts 0"),
    "payload_depth_past_bound": (_CJ, "the payload is nested 65 containers deep"),
    "payload_int_4001_digits": (
        _CJ,
        "a payload int has 4001 digits (10**4000, inside the bit pre-screen)",
    ),
    "payload_lone_surrogate": (_CJ, "a payload string holds a lone surrogate"),
    "payload_key_lone_surrogate": (_CJ, "a payload key is a lone surrogate"),
    "done_attempts_zero": (_CJ, "a done job carries attempts 0"),
    "done_attempts_past_max": (_CJ, "a done job carries attempts 6, past max_attempts 5"),
    "dead_attempts_below_max": (_CJ, "a dead job carries attempts 4, not max_attempts 5"),
    "done_with_lease_owner": (_CJ, "a done job holds lease_owner w-1"),
    "dead_with_lease_expiry": (_CJ, "a dead job holds lease_expires_at 200"),
    "status_done": (_NC, "a valid done job is not cancellable"),
    "status_done_at_max": (_NC, "a valid done job at max attempts is not cancellable"),
    "status_dead": (_NC, "a valid dead job is not cancellable"),
}
ROLLBACK_MANIFEST = {
    "corrupt_then_owner_repaired": (_CJ, "leased"),
    "not_cancellable_then_ready": (_NC, "ready"),
    "malformed_then_repaired": (_MB, "ready"),
}

# content binding: canonical sha256 of every row. Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:ready_withdrawn_before_claim": "2a078ad8ba67c2859ae2b1eadc5096ae2feaf5cd70fd9594b9fcc0a0e255aa25",  # noqa: E501
    "happy:leased_recorded_against_live_lease": "333cc363ae611a84874b9066a86539dc9dd240d1e5eb900297ccd8dd49a7162e",  # noqa: E501
    "happy:deterministic_same_request": "4ca724ace3a66f96caa74627e9242da740ba50a3e8d4273624cc05d950095bf4",  # noqa: E501
    "happy:payload_bound_by_digest": "d2a38910c603fd1f5f878db8621de7070c9e1af6a04525d7214c7866104ef386",  # noqa: E501
    "boundary:now_zero": "64067612cdfea124385f6a259eeff278e5c2ad2799acf2584f688451e2738285",  # noqa: E501
    "boundary:now_at_clock_limit": "231c7bb804346837863da55f69bf3020a868ac7e048d2d29936972c4c056464c",  # noqa: E501
    "boundary:seq_zero": "19a49da7b0b8c5e6103af9720faae644ec8a04bd8da64c32457d9e410b2058c3",  # noqa: E501
    "boundary:seq_at_bound": "cab3efda07ec95c8dfc40267f08241ae70eb0ed7c743532ec40bcaf8e5e5eb7f",  # noqa: E501
    "boundary:priority_zero": "071d6adc0fbc44b3a2ab25e57da8223a8bb4f5512604cf77718ca72507b2c24e",  # noqa: E501
    "boundary:priority_nine": "8e7cd0cfc44043fa4c5cda23560ad159660fa690f81a5866bd627f1702e010d6",  # noqa: E501
    "boundary:payload_depth_at_bound": "f938d3c621cea7d9c5e74820044a012a1a07c8eb952a5d32daa3e6b8648a9369",  # noqa: E501
    "boundary:payload_int_4000_digits": "2b3072d9c9396a802c9d7e2ed7f3f43c998259b3b46cbdabdfbf72623961adc5",  # noqa: E501
    "boundary:payload_null": "248a0983ded581aa30b288b0df385a2d432a79f6ca74d5a6c1fc9554f4f14b26",  # noqa: E501
    "boundary:payload_non_ascii": "dfa222c4d79a2ddcc1aed1a37a76f32f41214ef1915999fd498a0c168582964c",  # noqa: E501
    "boundary:requester_one_char": "02353e0cdeb3d91a40924be6045f17a44f4f1b15ba5367e762b8819f18f8c606",  # noqa: E501
    "boundary:requester_at_grammar_limit": "a03494b3b67a9cd756c1f2dbbe2711f4cf5fc37e7e5265351217dff941631b78",  # noqa: E501
    "malformed:request_not_a_dict": "b95f7b53bc98027fc3543b5254db069fdefa34ce52ec531369742fe19aeeda9c",  # noqa: E501
    "malformed:unknown_op": "af47eb91f5bd2c66c3d336c62d2e969a33a615bda2a0bfc522e441c9040658eb",  # noqa: E501
    "malformed:extra_field": "155cebdc4bf6567516d821f9dde45e89107fc28de05dfdc4b4d0983621c420aa",  # noqa: E501
    "malformed:missing_field": "0eb344c9ee07c92b4cc898e53a0d071445e0f9687fe41e364d9fdf4f7d90de05",  # noqa: E501
    "malformed:renamed_field": "21895d5a5219046c323ddc3f27c4c3d69aacd5aa2a91657f77b9d1789d5dc75d",  # noqa: E501
    "malformed:requester_none": "479cf78ff6d4341a51fd9db799c31aeb4c0a7c00b3eca8080c877f10c3dbc272",  # noqa: E501
    "malformed:requester_empty": "077b87802f59bbfcf24e5a66d6f7cf28c7453d73ae748bb4eaa8e47159d1de69",  # noqa: E501
    "malformed:requester_too_long": "91a04af3c67018bf768950c44849561cb04749cbe7ab0b222655fcf39270c4f2",  # noqa: E501
    "malformed:requester_bad_char": "872cc5038ef3c748475d76ca1bf5840f715f2dde7cbb093968c7aec6ae082bf4",  # noqa: E501
    "malformed:job_not_a_dict": "3a96e3626b493639a37e5ca725e02adb5e2f99b3e32b210c017fb4226ba2f092",  # noqa: E501
    "malformed:now_negative": "8ee37173a0a29a53da61e191f49f0cb8303685d608eb91707910883898588291",  # noqa: E501
    "malformed:now_past_clock": "62ee83d31ca2c8bb5b27ce68501cc2c502f5a0dcdda63bc4bb0bea913307b913",  # noqa: E501
    "malformed:now_bool": "1f458ddfd6f4da674b439f324fc1ea512862e5d931f74684a4e9d90594392ea2",  # noqa: E501
    "malformed:job_extra_field": "37b6094bb1f40db62199a79495e26b148f7a74fa3c978e22c1da1788101505cf",  # noqa: E501
    "malformed:job_renamed_field": "c6e41c134663dc707e06b2af1273343e5fb38761e21f843ff44b3d719c73f809",  # noqa: E501
    "malformed:job_id_trailing_newline": "c7bb064869837990d27e835442f0133f34f72e9df8bb15ab92117efb4a985fed",  # noqa: E501
    "malformed:seq_past_bound": "1e19ce56a11c3173f59ce6795f74ed9bb7c8b770d495e3ac9ea5deb03040c3c0",  # noqa: E501
    "malformed:priority_ten": "d347a0ae0ce82e5c192fe3e7b283c170529922f665e0563f20110f59bf56051a",  # noqa: E501
    "malformed:seq_negative": "5e629592fe647495278401c0b876a21026a81b48f12ca6659074db7ae9355bb6",  # noqa: E501
    "malformed:priority_negative": "cde4e14f3ae441360be145dd68a762c2dedec3f7889e74bcacb07372f7ce138d",  # noqa: E501
    "malformed:status_unknown": "7366feda19bf78643935daf6d3df4dd47729e716eb999c3430db06a33fb0d96e",  # noqa: E501
    "malformed:ready_with_lease_owner": "8936938320b6b23b6d0b99458b59f8bdbc3e78f28b59ba51d1a113f30045e51c",  # noqa: E501
    "malformed:ready_attempts_below_floor": "fb38dde06719ac3fb5cd17a312a4f27e6b3ebe1d74963d9517c94e7c710890a0",  # noqa: E501
    "malformed:leased_missing_owner": "2422d5d06147384c0c2a57843109dc27e5d7084635990f3ad20c2c967e202b21",  # noqa: E501
    "malformed:leased_owner_bad_grammar": "1f695babd46f99f5f70b035ad5600cb57c9b71f345dcd22312febfe1b2da7fec",  # noqa: E501
    "malformed:leased_expiry_zero": "e5305a3848ebef88e6db78fffe7c438f4c1b9080585277147f05651154d51d4a",  # noqa: E501
    "malformed:leased_expiry_past_bound": "bedb3f3beae9200c747d2109c9f85469247b6e1e603138c0c9cdb64357b621ce",  # noqa: E501
    "malformed:leased_attempts_zero": "523524f0334492ec11370f166107765463234fd7e771dae8b5a47f26da65ec12",  # noqa: E501
    "malformed:payload_depth_past_bound": "46d4c088a08344cbf88db97fd9840ecd6b7c9dfd61cd861815d15a9fdd2154d7",  # noqa: E501
    "malformed:payload_int_4001_digits": "6d973b29d3997fafe1a7b8e5eca1d84b3fa9e37c3abffa8dc7f3da82c840128a",  # noqa: E501
    "malformed:payload_lone_surrogate": "8e60e6a32285ec70a3217c6b71e0a1c65d044795284fecbeb78092190410676d",  # noqa: E501
    "malformed:payload_key_lone_surrogate": "83cc625c1229c5bb2c2a9c1d346d4b57dc0ab7e0db0a7afab3b9370f6e63ba2d",  # noqa: E501
    "malformed:done_attempts_zero": "ce3fd6dcfdc80154dbc4abed6c7d808903419fc1014e3f62a22152aada72fa09",  # noqa: E501
    "malformed:done_attempts_past_max": "51944fac91f4dbda6fff66a7622b1a499531a5cfe7ca7e2fa740a0183b819e68",  # noqa: E501
    "malformed:dead_attempts_below_max": "de056ab5fc47644beaf87c6b9a5e4df3b244b46afedc7ab91636fb418cce186a",  # noqa: E501
    "malformed:done_with_lease_owner": "134e8c88e569c4f4311018242c1cfc623348883295fb3e9a28b7a986425e00c0",  # noqa: E501
    "malformed:dead_with_lease_expiry": "a925ae76e7596bc81f5b8a0e8e2c2e437b1624c97a9c8399374206678f7bd6fa",  # noqa: E501
    "malformed:status_done": "4e66c3a9ba31593245616989c34b164efd7c0bb1a2a1a9fad297ec7ce3d4e5cb",  # noqa: E501
    "malformed:status_done_at_max": "320dee204114373d9504875f1d005f5f3aec8580a479df1a59bb2da53797e8ae",  # noqa: E501
    "malformed:status_dead": "551078b17e69daf9369dcca5690a1db3acc657aac12b952df00511c303fc0784",  # noqa: E501
    "rollback:corrupt_then_owner_repaired": "6c09510542a590b65c20e27722d508a48a438df45bddfcf66cba3cccbcbf8380",  # noqa: E501
    "rollback:not_cancellable_then_ready": "d297e8c5c93ec52454957118a33ab3cd72cfe9c6498c3d8d5c35c8765ff8f994",  # noqa: E501
    "rollback:malformed_then_repaired": "7bf42f0816f367071cae156875d7b1867e3b5a713bab6164b9a2ff4f48bc9d63",  # noqa: E501
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


def _h_ready(r):
    q, job = _one(r)
    assert (job["status"], job["attempts"]) == ("ready", 0)
    assert (job["lease_owner"], job["lease_expires_at"]) == (None, None)


def _h_leased(r):
    q, job = _one(r)
    assert (job["status"], job["attempts"]) == ("leased", 2)
    assert (job["lease_owner"], job["lease_expires_at"]) == ("w-1", 200)


def _h_same(r):
    a, b = r["requests"]
    assert a == b and r["expect_records"][0] == r["expect_records"][1]


def _h_digest(r):
    a, b = r["requests"]
    assert {p[:2] for p in _diff_paths(a, b)} == {("job", "payload")}
    ra, rb = r["expect_records"]
    assert ra["payload_digest"] != rb["payload_digest"]
    assert ra["decision_id"] != rb["decision_id"]


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


def _requester_len(length):
    def check(r):
        q, _job_ = _one(r)
        assert type(q["requester"]) is str and len(q["requester"]) == length

    return check


OK_EDGES = {
    "ready_withdrawn_before_claim": _h_ready,
    "leased_recorded_against_live_lease": _h_leased,
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
    "requester_one_char": _requester_len(1),
    "requester_at_grammar_limit": _requester_len(64),
}

_REQUEST_EDGES = {
    # name -> (field, bad value, repaired value)
    "requester_none": ("requester", None, "owner-1"),
    "requester_empty": ("requester", "", "owner-1"),
    "requester_too_long": ("requester", "z" * 65, "z" * 64),
    "requester_bad_char": ("requester", "has space", "owner-1"),
    "now_negative": ("now", -1, 0),
    "now_past_clock": ("now", MAX + 1, MAX),
    "now_bool": ("now", True, 1),
    "unknown_op": ("op", "redrive", "cancel"),
    "job_not_a_dict": ("job", [], None),
}
_JOB_EDGES = {
    "seq_past_bound": ("seq", MAX, MAX - 1),
    "priority_ten": ("priority", 10, 9),
    "seq_negative": ("seq", -1, 0),
    "priority_negative": ("priority", -1, 0),
    "status_unknown": ("status", "cancelled", "ready"),
    "ready_with_lease_owner": ("lease_owner", "w-1", None),
    "ready_attempts_below_floor": ("attempts", -1, 0),
    "leased_missing_owner": ("lease_owner", None, "w-1"),
    "leased_owner_bad_grammar": ("lease_owner", "!!", "w-1"),
    "leased_expiry_zero": ("lease_expires_at", 0, 1),
    "leased_expiry_past_bound": ("lease_expires_at", MAX + 1, MAX),
    "leased_attempts_zero": ("attempts", 0, 1),
}
_LEASED_JOB_EDGES = {
    "leased_missing_owner",
    "leased_owner_bad_grammar",
    "leased_expiry_zero",
    "leased_expiry_past_bound",
    "leased_attempts_zero",
}


# corrupt done/dead jobs: name -> (status, field, bad value, repaired value);
# each repair is still not cancellable, so it fails job_not_cancellable
_NON_CANCELLABLE_EDGES = {
    "done_attempts_zero": ("done", "attempts", 0, 1),
    "done_attempts_past_max": ("done", "attempts", MAX_ATTEMPTS + 1, MAX_ATTEMPTS),
    "dead_attempts_below_max": ("dead", "attempts", MAX_ATTEMPTS - 1, MAX_ATTEMPTS),
    "done_with_lease_owner": ("done", "lease_owner", "w-1", None),
    "dead_with_lease_expiry": ("dead", "lease_expires_at", 200, None),
}

# valid not-cancellable jobs: name -> (status, attempts); the repair flips
# only the status locus to ready, keeping attempts
_NC_STATUS_EDGES = {
    "status_done": ("done", 1),
    "status_done_at_max": ("done", MAX_ATTEMPTS),
    "status_dead": ("dead", MAX_ATTEMPTS),
}


def _int_in_range(value, lo, hi):
    return type(value) is int and lo <= value <= hi


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
        if name in _LEASED_JOB_EDGES:
            assert bad["job"]["status"] == fix["job"]["status"] == "leased", name
            assert _same_type_eq(fix["job"]["lease_owner"], "w-1"), name
            assert _int_in_range(fix["job"]["lease_expires_at"], 1, MAX), name
    elif name in _NON_CANCELLABLE_EDGES:
        status, field, b, f = _NON_CANCELLABLE_EDGES[name]
        assert bad["job"]["status"] == fix["job"]["status"] == status, name
        assert _same_type_eq(bad["job"][field], b) and _same_type_eq(fix["job"][field], f), name
        _only(bad, fix, ("job", field))
    elif name in _NC_STATUS_EDGES:
        status, attempts = _NC_STATUS_EDGES[name]
        assert (bad["job"]["status"], fix["job"]["status"]) == (status, "ready"), name
        assert bad["job"]["attempts"] == fix["job"]["attempts"] == attempts, name
        _only(bad, fix, ("job", "status"))
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
    elif name == "payload_key_lone_surrogate":
        assert _same_type_eq(bad["job"]["payload"], {"\ud800": 1})
        assert _same_type_eq(fix["job"]["payload"], {"k": 1})
        _only(bad, fix, ("job", "payload", "\ud800"), ("job", "payload", "k"))
    else:
        raise AssertionError(f"unknown malformed tag {name}")


def _check_rollback_scenario(row):
    name, bad, q = row["name"], row["rejected_request"], row["request"]
    if name == "corrupt_then_owner_repaired":
        assert (bad["job"]["lease_owner"], q["job"]["lease_owner"]) == ("!!", "w-1")
        _only(bad, q, ("job", "lease_owner"))
    elif name == "not_cancellable_then_ready":
        assert (bad["job"]["status"], q["job"]["status"]) == ("done", "ready")
        _only(bad, q, ("job", "status"))
    elif name == "malformed_then_repaired":
        assert (bad["requester"], q["requester"]) == ("has space", "owner-1")
        _only(bad, q, ("requester",))
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
                repair = row["minimal_repair"]
                want = (
                    {"expect_failure": _NC_CLASS} if row["name"] in _NON_CANCELLABLE_EDGES else {}
                )
                assert {k: v for k, v in repair.items() if k != "request"} == want, label
                _m(row["name"], row["request"], repair["request"])
            else:
                assert (row["expect_failure"], row["expect_record"]["status_before"]) == meta, label
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
    recorded = {
        rec["status_before"]
        for s in ("happy", "boundary")
        for row in CASES[s]
        for rec in row["expect_records"]
    }
    assert recorded == set(STATUSES_BEFORE)
    statuses = {
        row["request"]["job"]["status"]
        for row in CASES["malformed"]
        if row["expect_failure"] == "job_not_cancellable"
    }
    assert statuses == {"done", "dead"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(schema=2),
        lambda c: c.update(schema=True),
        lambda c: c.update(contract="jobs-retry"),
        lambda c: c.update(contract_base_path="/jobs/cancel/v2"),
        lambda c: c.update(notes="no marker"),
        lambda c: c.update(extra=1),
        lambda c: c.pop("rollback"),
        lambda c: c["boundary"].clear(),
        lambda c: c["happy"][0].update(eta=0),
        lambda c: c["happy"][0]["expect_records"].pop(),
        lambda c: c["happy"][0]["expect_records"][0].pop("cancelled_at"),
        lambda c: c["happy"][0]["expect_records"][0].update(extra=1),
        lambda c: c["happy"][0]["expect_records"][0].update(decision_id="cd1:zz"),
        lambda c: c["happy"][0]["expect_records"][0].update(op="redrive"),
        lambda c: c["happy"][0]["expect_records"][0].update(attempts=1),
        lambda c: c["happy"][0]["expect_records"][0].update(seq=4),
        lambda c: c["happy"][0]["expect_records"][0].update(cancelled_at=1),
        lambda c: c["happy"][0]["expect_records"][0].update(status_before="leased"),
        lambda c: c["happy"][0]["expect_records"][0].update(requester="x"),
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
        lambda c: c["rollback"][0]["expect_record"].update(decision_id="cd1:" + "0" * 64),
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
    row["expect_records"] = [_cancel(copy.deepcopy(q)) for q in row["requests"]]


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
        c, "happy", "ready_withdrawn_before_claim", "leased_recorded_against_live_lease", "name"
    ),
    "swap_malformed_names_same_class": lambda c: _swap(
        c, "malformed", "now_negative", "now_past_clock", "name"
    ),
    "swap_malformed_requests": lambda c: _swap(
        c, "malformed", "now_negative", "now_past_clock", "request", "minimal_repair"
    ),
    "swap_not_cancellable_requests": lambda c: _swap(
        c, "malformed", "status_done", "status_dead", "request", "minimal_repair"
    ),
    "swap_lease_requests": lambda c: _swap(
        c,
        "malformed",
        "done_with_lease_owner",
        "dead_with_lease_expiry",
        "request",
        "minimal_repair",
    ),
    "drop_status_dead": lambda c: c["malformed"].remove(_bn(c, "malformed", "status_dead")),
    "duplicate_unknown_op": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "unknown_op"))
    ),
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
    "requester_edge_one_short": _edit(
        "boundary",
        "requester_at_grammar_limit",
        lambda row: row["requests"][0].update(requester="z" * 63),
    ),
    "requester_edge_swapped": _edit(
        "boundary", "requester_one_char", lambda row: row["requests"][0].update(requester="b")
    ),
    "digest_row_same_payload": _edit(
        "happy",
        "payload_bound_by_digest",
        lambda row: row["requests"][1].update(job=copy.deepcopy(row["requests"][0]["job"])),
    ),
    "repair_touches_two_fields": lambda c: _bn(c, "malformed", "now_negative")["minimal_repair"][
        "request"
    ].update(requester="other"),
    "renamed_field_second_defect": lambda c: _bn(c, "malformed", "renamed_field")["request"].update(
        requester="has space"
    ),
    "job_renamed_second_defect": _bad_job("job_renamed_field", seq=4),
    "ready_floor_retyped": _bad_job("ready_attempts_below_floor", attempts=-1.0),
    "leased_owner_second_defect": _bad_job("leased_owner_bad_grammar", seq=4),
    "leased_expiry_bound_shifted": _bad_job("leased_expiry_past_bound", lease_expires_at=MAX + 2),
    "leased_expiry_zero_retyped": _bad_job("leased_expiry_zero", lease_expires_at=False),
    "non_cancellable_repair_cancels": lambda c: _bn(c, "malformed", "done_attempts_zero")[
        "minimal_repair"
    ].pop("expect_failure"),
    "payload_key_retyped": _bad_job("payload_key_lone_surrogate", payload={"\ud801": 1}),
    "payload_key_value_changed": _bad_job("payload_key_lone_surrogate", payload={"\ud800": 2}),
    "leased_row_second_defect": _bad_job("leased_expiry_zero", priority=5),
    "done_owner_bad_grammar": _bad_job("done_with_lease_owner", lease_owner="w 1"),
    "status_unknown_retyped": _bad_job("status_unknown", status=None),
    "depth_row_one_short": _bad_job("payload_depth_past_bound", payload=[[0]]),
    "status_done_second_defect": _bad_job("status_done", attempts=2),
    "rollback_follow_up_widened": lambda c: _bn(c, "rollback", "not_cancellable_then_ready")[
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
    assert [_cancel(q) for q in requests] == row["expect_records"]
    assert requests == row["requests"]
    again = [_cancel(copy.deepcopy(q)) for q in row["requests"]]
    assert again == row["expect_records"]


def test_pinned_digests_match_the_contract_preimages():
    """Every pinned payload_digest and decision_id is the hand-built
    contract preimage, so a record and its ids cannot drift together."""
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            for q, rec in zip(row["requests"], row["expect_records"], strict=True):
                assert rec["payload_digest"] == _preimage("pd1", q["job"]["payload"])
                assert rec["decision_id"] == _preimage(
                    "cd1", {k: rec[k] for k in RECORD_FIELDS[:-1]}
                )


def test_tampered_pinned_record_is_detected():
    row = copy.deepcopy(_row("happy", "ready_withdrawn_before_claim"))
    row["expect_records"][0]["cancelled_at"] += 1
    assert [_cancel(copy.deepcopy(q)) for q in row["requests"]] != row["expect_records"]


def _rejects(failure_class, request):
    before = copy.deepcopy(request)
    with pytest.raises(_REF.CancelError) as err:
        _cancel(request)
    exc = err.value
    assert type(exc) is _REF.CancelError
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
    """The single-locus repair clears the declared defect: it cancels, or
    for a corrupt done/dead job it reaches the cancellable check and
    fails job_not_cancellable."""
    row = _row("malformed", name)
    repair = row["minimal_repair"]
    request = copy.deepcopy(repair["request"])
    if "expect_failure" in repair:
        _rejects(repair["expect_failure"], request)
    else:
        _validate_record(_cancel(request), name)
    assert _snap(request) == _snap(repair["request"])


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
    assert _cancel(request) == row["expect_record"]
    assert _snap(request) == _snap(row["request"])


def test_validation_precedence():
    """request shape -> job integrity -> cancellable status, first failure
    wins (derived from fixture rows so the order runs on pinned data)."""
    corrupt = _row("malformed", "priority_ten")["request"]
    done = _row("malformed", "status_done")["request"]
    _rejects("malformed_cancel_request", {**corrupt, "now": -1})
    _rejects("malformed_cancel_request", {**done, "requester": "has space"})
    _rejects("corrupt_job", {**done, "job": {**done["job"], "priority": 10}})
    # job integrity outranks the cancellable check for both terminal statuses
    dead = _row("malformed", "status_dead")["request"]
    _rejects("job_not_cancellable", done)
    _rejects("job_not_cancellable", dead)
    _rejects("corrupt_job", {**done, "job": {**done["job"], "attempts": 0}})
    _rejects("corrupt_job", {**dead, "job": {**dead["job"], "attempts": 4}})


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
        if "expect_failure" not in row["minimal_repair"]:
            out.append((f"repair:{row['name']}", row["minimal_repair"]["request"]))
    for row in CASES["rollback"]:
        out.append((f"follow-up:{row['name']}", row["request"]))
    return out


_MB_ = "malformed_cancel_request"
_CJ_ = "corrupt_job"
_STR_FIELDS = ("op", "requester")
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
            _cancel(request)
            got = "accepted"
        except _REF.CancelError as exc:
            got = (
                type(exc) is _REF.CancelError,
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


# -- the T0302 reference mutants must each turn this fixture red -------------


def _fixture_red():
    """Labels of every fixture check that disagrees with the reference
    currently bound (empty when green)."""
    red = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            try:
                got = [_cancel(copy.deepcopy(q)) for q in row["requests"]]
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


# Extra one-edit mutants of the T0302 reference that this fixture kills on
# its own (the type, grammar, bound and lease guards the rows exist for).
FIXTURE_EDITS = {
    "op-check-off": ('        and request["op"] == "cancel"\n', ""),
    "job-dict-any": (
        'and type(request["job"]) is dict',
        'and isinstance(request["job"], dict)',
    ),
    "requester-check-off": ('        and _grammar(request["requester"], _REQUESTER_RE)\n', ""),
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
    "payload-key-scalar-off": (
        "if type(key) is not str or not _scalar_ok(key):",
        "if type(key) is not str:",
    ),
    "lease-expiry-floor-zero": (
        "_int_in(expires, 1, _REF_MAX_NOW)",
        "_int_in(expires, 0, _REF_MAX_NOW)",
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
