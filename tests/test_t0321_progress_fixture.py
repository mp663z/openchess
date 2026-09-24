"""T0321: jobs progress conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the T0320
jobs-progress contract (data/contracts/progress.yaml). The cases execute
against the contract-derived reference report in
tests.test_t0320_progress_contract - nothing is re-implemented here.
Pinned progress records were computed from that reference at authoring
time and chain through previous, so any contract or derivation drift
breaks this battery. Every malformed case is discriminating: applying
ONLY its declared single-locus repair makes it report. Rollback cases
prove a rejected report leaves the exact supplied request bit- and
value-identical before a valid follow-up reports as pinned.

Hostile-type rows are derived here from EVERY request the fixture
accepts (happy and boundary requests, malformed repairs, rollback
follow-ups): hostile containers on the request and on the previous
record, str-subclass keys in three forms on every request and record
field, and str-subclass, raising eq/repr, int-subclass and bool values,
including nested inside the previous record. Each must fail typed with
a fresh error, run no user code and leave the input unchanged.

Every one-edit reference mutant pinned by the T0320 battery must turn
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

import tests.test_t0320_progress_contract as _REF  # noqa: E402
from tools.progress_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "progress" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECORD_FIELDS = list(_CC["record"]["fields"])
FIELDS = _CC["request"]["operations"][0]["fields"]
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_CC["identifiers"]["worker"]["grammar"], re.ASCII)
_PROGRESS_RE = re.compile(_CC["identifiers"]["progress_id"]["grammar"], re.ASCII)
MAX = 9007199254740991  # 2**53 - 1, written out
BP = 10000

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "requests", "expect_records"}
BAD_KEYS = {"name", "defect", "expect_failure", "request", "minimal_repair"}
RB_KEYS = {"name", "why", "rejected_request", "expect_failure", "request", "expect_record"}


def _report(request):
    return _REF.report(request)  # late-bound: reference mutants rebind it


def _fails(request):
    try:
        _report(copy.deepcopy(request))
    except _REF.ProgressError as exc:
        return exc.failure_class
    return None


def _validate_record(record, label):
    assert type(record) is dict, label
    assert all(type(k) is str for k in record) and set(record) == set(RECORD_FIELDS), label
    assert record["op"] == "report", label
    assert _JOB_RE.fullmatch(record["job_id"]), label
    assert _WORKER_RE.fullmatch(record["worker"]), label
    assert _PROGRESS_RE.fullmatch(record["progress_id"]), label
    assert record["previous_id"] is None or _PROGRESS_RE.fullmatch(record["previous_id"]), label
    assert 0 <= record["done"] <= record["total"], label
    assert record["percent_bp"] == record["done"] * BP // record["total"], label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert all(type(k) is str for k in request) and set(request) == set(FIELDS), label
    prev = request["previous"]
    assert prev is None or type(prev) is dict, label


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
                    for field in ("job_id", "worker", "done", "total"):
                        assert record[field] == request[field], label
                    assert record["reported_at"] == request["now"], label
                    prev = request["previous"]
                    assert record["previous_id"] == (
                        None if prev is None else prev["progress_id"]
                    ), label
                # a chained step's previous is exactly the prior pinned record
                for i in range(1, len(reqs)):
                    if reqs[i]["previous"] is not None:
                        assert reqs[i]["previous"] == recs[i - 1], label
                assert reqs[0]["previous"] is None, label
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


def _progress(row):
    return [(rec["done"], rec["total"], rec["percent_bp"]) for rec in row["expect_records"]]


def _links(row):
    return "".join("L" if rec["previous_id"] else "-" for rec in row["expect_records"])


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> ([(done, total, percent_bp)], chain links);
# malformed: name -> (failure class, pinned defect text); rollback: name ->
# (failure class, follow-up done, follow-up percent_bp, follow-up linked)
HAPPY_MANIFEST = {
    "first_report_at_zero": ([(0, 10, 0)], "-"),
    "chain_to_completion": ([(0, 4, 0), (1, 4, 2500), (2, 4, 5000), (4, 4, 10000)], "-LLL"),
    "heartbeat_equal_done": ([(3, 10, 3000), (3, 10, 3000)], "-L"),
    "percent_is_floored": ([(1, 3, 3333), (2, 3, 6666)], "-L"),
}
BOUNDARY_MANIFEST = {
    "total_one_complete_at_first_report": ([(1, 1, 10000)], "-"),
    "total_at_clock_bound": ([(MAX - 1, MAX, 9999)], "-"),
    "equal_now_accepted": ([(1, 10, 1000), (2, 10, 2000)], "-L"),
    "now_at_clock_limit": ([(0, 10, 0)], "-"),
    "jump_straight_to_completion": ([(0, 10, 0), (10, 10, 10000)], "-L"),
    "percent_floor_not_float": ([(57, 100, 5700)], "-"),
    "worker_at_grammar_bound": ([(5, 10, 5000)], "-"),
    "new_worker_starts_new_chain": ([(4, 10, 4000), (4, 10, 4000)], "--"),
}
_MP, _CP, _PC = "malformed_progress_request", "corrupt_previous_record", "progress_conflict"
MALFORMED_MANIFEST = {
    "request_not_a_dict": (_MP, "the request is a list"),
    "unknown_op": (_MP, "op is 'progress', not a registered operation"),
    "extra_field": (_MP, "the request carries an extra eta field"),
    "missing_field": (_MP, "the request has no previous field"),
    "renamed_field": (_MP, "the now field is renamed to eta, keeping the field count"),
    "job_id_trailing_newline": (_MP, "the job id ends in a newline"),
    "worker_bad_grammar": (_MP, "the worker id holds a space"),
    "worker_past_grammar_bound": (_MP, "the worker id has 65 characters"),
    "total_zero": (_MP, "total is 0"),
    "total_past_clock_bound": (_MP, "total is 2**53"),
    "done_over_total": (_MP, "done 11 is above total 10"),
    "done_negative": (_MP, "done is -1"),
    "done_bool": (_MP, "done is the bool True"),
    "now_negative": (_MP, "now is -1"),
    "now_past_clock": (_MP, "now is 2**53"),
    "previous_not_a_dict": (_MP, "previous is a list"),
    "previous_done_tampered": (
        _CP,
        "the previous record's done was edited from 2 to 3 without a new progress_id",
    ),
    "previous_id_tampered": (_CP, "the previous record's progress_id has one hex digit changed"),
    "previous_percent_forged": (
        _CP,
        "the previous record's percent_bp is 2001, not 2000, with its progress_id"
        " recomputed to match",
    ),
    "previous_done_past_total": (
        _CP,
        "the previous record's done is 11 of total 10, with percent_bp 11000 and its"
        " progress_id recomputed to match",
    ),
    "previous_total_past_bound": (
        _CP,
        "the previous record's total is 2**53 (done 1, percent_bp 0), with its"
        " progress_id recomputed to match",
    ),
    "previous_total_zero": (
        _CP,
        "the previous record's total is 0 (done 0, percent_bp 0), with its"
        " progress_id recomputed to match",
    ),
    "previous_reported_at_past_bound": (
        _CP,
        "the previous record's reported_at is 2**53, with its progress_id recomputed to match",
    ),
    "previous_op_wrong": (
        _CP,
        "the previous record's op is 'reprt', with its progress_id recomputed to match",
    ),
    "previous_renamed_field": (
        _CP,
        "the previous record's reported_at is renamed to eta, keeping the field count",
    ),
    "previous_extra_field": (_CP, "the previous record carries an extra eta field"),
    "total_changed": (_PC, "total 20 differs from the previous total 10"),
    "done_decreased": (_PC, "done 1 is below the previous done 2"),
    "clock_regression": (_PC, "now 49 precedes the previous reported_at 50"),
    "worker_changed": (_PC, "worker w-2 differs from the previous worker w-1"),
    "job_changed": (_PC, "the job id differs from the previous record's"),
    "report_after_completion": (
        _PC,
        "the previous record is complete (10 of 10), so no report may follow",
    ),
}
ROLLBACK_MANIFEST = {
    "conflict_then_forward_report": (_PC, 3, 3000, True),
    "corrupt_previous_then_intact_previous": (_CP, 3, 3000, True),
    "malformed_then_repaired": (_MP, 10, 10000, False),
}

# content binding: canonical sha256 of every row. Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:first_report_at_zero": "65ac96ecd6f53d4a5688948cd76f29448fe39ad41e1f97acce6de8cc2a32b88a",  # noqa: E501
    "happy:chain_to_completion": "13d88dc74b52456359a89a6e2cd0759b61f96a2f3f271eeb4c5fc811c8bf3eeb",  # noqa: E501
    "happy:heartbeat_equal_done": "8d8bd28b9d99bab97beea122d69a6154b8a051cdac36a348c0b27cc345e5efa2",  # noqa: E501
    "happy:percent_is_floored": "53c8438d50bac6e15d5ac5059eec2c0a08678ac82ebd098289076903e4d2318a",  # noqa: E501
    "boundary:total_one_complete_at_first_report": "b58fb10e98e7c693fd85987fbc6a05a30e8fd343bf4e09429faaf8f0f597b107",  # noqa: E501
    "boundary:total_at_clock_bound": "439c06e8d088d6a7494b840d9130be7164c9f12bc49109c87c399dce306afe54",  # noqa: E501
    "boundary:equal_now_accepted": "0e10539339dbce937fd0ee8fd0739ccccd11c2d96cf44587f6b676dbab766633",  # noqa: E501
    "boundary:now_at_clock_limit": "298a537297b6601057a30f11e4209f85dc84d91de1d96906b1495145aa8c292b",  # noqa: E501
    "boundary:jump_straight_to_completion": "c9f23e5158e54aa9e3520c731ab393a5213ec4e8404a2e5358cea1a4833bad4b",  # noqa: E501
    "boundary:percent_floor_not_float": "808aea401c4fa0f890f077ef13ec50eefb1da9f5db118d31d96fb110af25e884",  # noqa: E501
    "boundary:worker_at_grammar_bound": "a7177b950d3e448bfe98d42fc313bc6942fda8cd9fceab4fd5699423f1e58fde",  # noqa: E501
    "boundary:new_worker_starts_new_chain": "08caea1993d678d09b4e1d273ec794aef8ab7e22b2350cd4a1401cbfe2fe11a5",  # noqa: E501
    "malformed:request_not_a_dict": "e51f654f2105de4f5cf88245f7fe9636b64f16ae344f6f1d98c57e793cb39d62",  # noqa: E501
    "malformed:unknown_op": "46d073b6a78c23a136cf4d72cdbe163360562ff8a15668f4c600864e24163860",  # noqa: E501
    "malformed:extra_field": "3cd36f1c8ea7e032ae4cf08ee6a6a16da834dfddae1d6fdff9ebb72d1a716a3a",  # noqa: E501
    "malformed:missing_field": "02f21033eb618351ec0bdd661ccbb0981c67ed6991d5a74744ca2c3a34903399",  # noqa: E501
    "malformed:renamed_field": "ababf8fbf36a25a464b51f9126a1c286e85d534d8eb7084682b584debbf60bba",  # noqa: E501
    "malformed:job_id_trailing_newline": "fb5817e92fe2a4baaa79cccada7504fec9e2cdb2ca1fbda75bd49ad97549f045",  # noqa: E501
    "malformed:worker_bad_grammar": "e1024913f70458a062a3f0be92fe7b9e73edb3e31c0740c4d0b5de2ab7ecdbdc",  # noqa: E501
    "malformed:worker_past_grammar_bound": "17ab44ce73e3e798f1eb370f086dcf3d76907904c3c33d8f8d13b138b8d79ad5",  # noqa: E501
    "malformed:total_zero": "a4c6ce8455628db7503861bbcbbdd6e4430fcccd28411fb077187a690476dfcf",  # noqa: E501
    "malformed:total_past_clock_bound": "0cac9543b4c9a79912c235edfe6f6d163bfe96daa291b40b7d058665690f4337",  # noqa: E501
    "malformed:done_over_total": "9ab82b57ad16ea675c1cea20f53ac58bee6551f85c2ccb90a28fd1cc8aac303b",  # noqa: E501
    "malformed:done_negative": "db25b37871d8034918cc2ab054b2b551f1c909ebb81d85ea7fc19d0e82f7291e",  # noqa: E501
    "malformed:done_bool": "6125b5541a2e51e21d64989f3894917c1090de96bb77a9c62554ad468d936422",  # noqa: E501
    "malformed:now_negative": "7eb305eecead7f29084d210d2bbcdabaf6a4be9e526944b977d6974c2fabda75",  # noqa: E501
    "malformed:now_past_clock": "f7fbafcb2b99139601d0d911c34c58446189464407e3c3de8fe799ced21816c3",  # noqa: E501
    "malformed:previous_not_a_dict": "2ae8aa5de58720ec66e04f635a21cb8b5a188b686584de6473f2e4e93648a29b",  # noqa: E501
    "malformed:previous_done_tampered": "d9aa79b7c9e46c9bd4c6fbb9784d8e70bad9ebc67bdf1aa5ec82f16ad9444800",  # noqa: E501
    "malformed:previous_id_tampered": "82bcade4a43ac8acae5e2b5a3444189060bb1370fc06c254def013aafbab87fd",  # noqa: E501
    "malformed:previous_percent_forged": "7172d7df81d49848ae57727732f1ffe8bdb6ae5840d814761d295629d769bac6",  # noqa: E501
    "malformed:previous_done_past_total": "d42bd174ab6c172a63d18182406fce7c451fd993cfdcc9bb5920659b0bd07cee",  # noqa: E501
    "malformed:previous_total_past_bound": "945d09af67e8769369c980d5e69e014289814a5686b869050dad9bdcf32a4c70",  # noqa: E501
    "malformed:previous_total_zero": "b485460b6538ce344781089c7e4e53d3bc2a882a4f866ed4b0396caed46e4b94",  # noqa: E501
    "malformed:previous_reported_at_past_bound": "20cdb4c0f15276aa132f57d9fe92d338fa33bc0f8a211234922c58bdb48df61e",  # noqa: E501
    "malformed:previous_op_wrong": "b9c7822c68a8c12027ed450d6f84b5f54a524c0340f0b68dcc38a0d36fc455c6",  # noqa: E501
    "malformed:previous_renamed_field": "21a67d8d41d402f563f240e5b6372e6c39aeb0996bc02119cd441de66f34acf9",  # noqa: E501
    "malformed:previous_extra_field": "1bc075a1386ea62b27d909965f43c3ddb083ea248724da63dce77afaee66a5d8",  # noqa: E501
    "malformed:total_changed": "130db5203c2528e5af7a51d3810c4cb9ee9b96ab97f5b3f64b1573c71225866f",  # noqa: E501
    "malformed:done_decreased": "c2aaf6eefda40421dc30b51ef93eccbf822873ca83cd259b6dbc0581a997f4dd",  # noqa: E501
    "malformed:clock_regression": "cb00e80183828c412a30db17a4e9472c8e2f9b610c564ed38b593d1785595bdf",  # noqa: E501
    "malformed:worker_changed": "ef26b6ac781dcfa9ba76309d7e198894b7ce775f821cfd586ced18f93db2f9c5",  # noqa: E501
    "malformed:job_changed": "29aeec3d94d9d24a47c53eaee1f7928b2dc01a72836fa3e90e9daf68f1e5fd52",  # noqa: E501
    "malformed:report_after_completion": "67f249d59fdfe0e71e67466ffae1d8a07d84f8ae952b1505b1203ebf163bd9a7",  # noqa: E501
    "rollback:conflict_then_forward_report": "81fad4b33cfff89b6c3af2378f5a45fa69f41b766eb2c40c51ebee2c34d5dd8b",  # noqa: E501
    "rollback:corrupt_previous_then_intact_previous": "53c71a5e92a6470381d4d7e1d5a703b7c4b3bf3c2eef4fa9560ccd1641231bb7",  # noqa: E501
    "rollback:malformed_then_repaired": "f3077d472bad15cb09f562319ba08b72e0aaef906afbf1bd958f95d6b8547c52",  # noqa: E501
}

MANIFESTS = {
    "happy": HAPPY_MANIFEST,
    "boundary": BOUNDARY_MANIFEST,
    "malformed": MALFORMED_MANIFEST,
    "rollback": ROLLBACK_MANIFEST,
}


# -- per-row semantic edges over the ORIGINAL data ---------------------------


def _same_but(a, b, *fields):
    return {k: v for k, v in a.items() if k not in fields} == {
        k: v for k, v in b.items() if k not in fields
    }


def _h_first(r):
    (q,) = r["requests"]
    assert (q["done"], q["previous"]) == (0, None)


def _h_chain(r):
    reqs = r["requests"]
    assert reqs[-1]["done"] == reqs[-1]["total"]
    assert [q["done"] for q in reqs] == sorted(q["done"] for q in reqs)
    assert len({q["done"] for q in reqs}) == len(reqs)  # strictly forward
    assert [q["now"] for q in reqs] == sorted(q["now"] for q in reqs)
    assert len({q["now"] for q in reqs}) == len(reqs)


def _h_heartbeat(r):
    a, b = r["requests"]
    assert a["done"] == b["done"] < a["total"] and b["now"] > a["now"]
    ra, rb = r["expect_records"]
    assert ra["progress_id"] != rb["progress_id"]


def _h_floor(r):
    for q, rec in zip(r["requests"], r["expect_records"], strict=True):
        assert q["done"] * BP % q["total"] != 0  # a real remainder is dropped
        assert rec["percent_bp"] < q["done"] * BP / q["total"]


def _b_total_one(r):
    (q,) = r["requests"]
    assert (q["done"], q["total"], q["previous"]) == (1, 1, None)


def _b_total_max(r):
    (q,) = r["requests"]
    assert (q["total"], q["done"]) == (MAX, MAX - 1)
    assert type(q["total"]) is int


def _b_equal_now(r):
    a, b = r["requests"]
    assert a["now"] == b["now"] and b["done"] > a["done"]


def _b_clock(r):
    (q,) = r["requests"]
    assert q["now"] == MAX


def _b_jump(r):
    a, b = r["requests"]
    assert (a["done"], b["done"]) == (0, b["total"])


def _b_worker_bound(r):
    (q,) = r["requests"]
    assert len(q["worker"]) == 64


def _b_float_floor(r):
    (q,) = r["requests"]
    (rec,) = r["expect_records"]
    assert (q["done"], q["total"]) == (57, 100)
    assert int(q["done"] / q["total"] * BP) != rec["percent_bp"] == q["done"] * BP // q["total"]


def _b_new_worker(r):
    a, b = r["requests"]
    assert a["worker"] != b["worker"] and b["previous"] is None
    assert _same_but(a, b, "worker", "now", "previous")


OK_EDGES = {
    "first_report_at_zero": _h_first,
    "chain_to_completion": _h_chain,
    "heartbeat_equal_done": _h_heartbeat,
    "percent_is_floored": _h_floor,
    "total_one_complete_at_first_report": _b_total_one,
    "total_at_clock_bound": _b_total_max,
    "equal_now_accepted": _b_equal_now,
    "now_at_clock_limit": _b_clock,
    "jump_straight_to_completion": _b_jump,
    "percent_floor_not_float": _b_float_floor,
    "worker_at_grammar_bound": _b_worker_bound,
    "new_worker_starts_new_chain": _b_new_worker,
}


_FIELD_EDGES = {
    # name -> (field, bad value, repaired value)
    "total_zero": ("total", 0, 1),
    "total_past_clock_bound": ("total", MAX + 1, MAX),
    "done_over_total": ("done", 11, 10),
    "done_negative": ("done", -1, 0),
    "now_negative": ("now", -1, 0),
    "now_past_clock": ("now", MAX + 1, MAX),
}


# name -> (pinned bad previous values, pinned repaired values, changed fields
# besides the recomputed progress_id)
_PREV_FORGED = {
    "previous_total_past_bound": (
        {"total": MAX + 1, "done": 1, "percent_bp": 0},
        {"total": 10, "done": 1, "percent_bp": 1000},
        ("total", "percent_bp"),
    ),
    "previous_total_zero": (
        {"total": 0, "done": 0, "percent_bp": 0},
        {"total": 10, "done": 0, "percent_bp": 0},
        ("total",),
    ),
    "previous_reported_at_past_bound": (
        {"reported_at": MAX + 1},
        {"reported_at": 50},
        ("reported_at",),
    ),
    "previous_op_wrong": ({"op": "reprt"}, {"op": "report"}, ("op",)),
}


def _intact(prev):
    return prev["progress_id"] == _REF._independent_id({k: prev[k] for k in RECORD_FIELDS[:-1]})


def _m(name, bad, fix):  # noqa: C901 - one closed branch per tag
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus."""
    if type(fix) is dict and fix.get("previous") is not None:
        assert _intact(fix["previous"]), name
    if name == "request_not_a_dict":
        assert type(bad) is list and bad == [fix]
    elif name == "unknown_op":
        assert (bad["op"], fix["op"]) == ("progress", "report")
        _only(bad, fix, ("op",))
    elif name == "extra_field":
        _only(bad, fix, ("eta",))
    elif name == "missing_field":
        assert set(fix) - set(bad) == {"previous"}
        _only(bad, fix, ("previous",))
    elif name == "renamed_field":
        assert type(bad) is dict and len(bad) == len(fix)
        assert set(bad) - set(fix) == {"eta"} and set(fix) - set(bad) == {"now"}
        assert bad["eta"] == fix["now"]
        _only(bad, fix, ("eta",), ("now",))
    elif name == "job_id_trailing_newline":
        assert bad["job_id"] == fix["job_id"] + "\n"
        _only(bad, fix, ("job_id",))
    elif name == "worker_bad_grammar":
        assert " " in bad["worker"] and " " not in fix["worker"]
        _only(bad, fix, ("worker",))
    elif name == "worker_past_grammar_bound":
        assert (len(bad["worker"]), len(fix["worker"])) == (65, 64)
        _only(bad, fix, ("worker",))
    elif name in _FIELD_EDGES:
        field, b, f = _FIELD_EDGES[name]
        assert (bad[field], fix[field]) == (b, f) and type(bad[field]) is int
        _only(bad, fix, (field,))
    elif name == "done_bool":
        assert bad["done"] is True and fix["done"] == 1
        _only(bad, fix, ("done",))
    elif name == "previous_not_a_dict":
        assert (bad["previous"], fix["previous"]) == ([], None)
        _only(bad, fix, ("previous",))
    elif name == "previous_done_tampered":
        assert (bad["previous"]["done"], fix["previous"]["done"]) == (3, 2)
        assert bad["previous"]["progress_id"] == fix["previous"]["progress_id"]
        _only(bad, fix, ("previous", "done"))
    elif name == "previous_id_tampered":
        a, b = bad["previous"]["progress_id"], fix["previous"]["progress_id"]
        assert len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) == 1
        assert _PROGRESS_RE.fullmatch(a)
        _only(bad, fix, ("previous", "progress_id"))
    elif name == "previous_percent_forged":
        prev = bad["previous"]
        assert (prev["percent_bp"], fix["previous"]["percent_bp"]) == (2001, 2000)
        assert _intact(prev) and prev["percent_bp"] != prev["done"] * BP // prev["total"]
        _only(bad, fix, ("previous", "percent_bp"), ("previous", "progress_id"))
    elif name == "previous_done_past_total":
        b, f = bad["previous"], fix["previous"]
        assert (b["done"], b["total"], b["percent_bp"]) == (11, 10, 11000) and _intact(b)
        assert (f["done"], f["total"], f["percent_bp"]) == (2, 10, 2000)
        assert (bad["done"], bad["total"]) == (10, 10)
        _only(
            bad,
            fix,
            ("previous", "done"),
            ("previous", "percent_bp"),
            ("previous", "progress_id"),
        )
    elif name in _PREV_FORGED:
        (bad_vals, fix_vals, paths) = _PREV_FORGED[name]
        b, f = bad["previous"], fix["previous"]
        assert _intact(b), name
        for field, value in bad_vals.items():
            assert type(b[field]) is type(value) and b[field] == value, name
        for field, value in fix_vals.items():
            assert type(f[field]) is type(value) and f[field] == value, name
        _only(bad, fix, *[("previous", p) for p in (*paths, "progress_id")])
    elif name == "previous_renamed_field":
        b, f = bad["previous"], fix["previous"]
        assert type(b) is dict and len(b) == len(f)
        assert set(b) - set(f) == {"eta"} and set(f) - set(b) == {"reported_at"}
        assert b["eta"] == f["reported_at"]
        _only(bad, fix, ("previous", "eta"), ("previous", "reported_at"))
    elif name == "previous_extra_field":
        _only(bad, fix, ("previous", "eta"))
    elif name == "total_changed":
        assert bad["total"] != bad["previous"]["total"] == fix["total"]
        _only(bad, fix, ("total",))
    elif name == "done_decreased":
        assert bad["done"] < bad["previous"]["done"] == fix["done"]
        _only(bad, fix, ("done",))
    elif name == "clock_regression":
        assert bad["now"] == bad["previous"]["reported_at"] - 1
        assert fix["now"] == fix["previous"]["reported_at"]
        _only(bad, fix, ("now",))
    elif name in ("worker_changed", "job_changed"):
        field = "worker" if name == "worker_changed" else "job_id"
        assert bad[field] != bad["previous"][field] == fix[field]
        _only(bad, fix, (field,))
    elif name == "report_after_completion":
        prev = bad["previous"]
        assert prev["done"] == prev["total"] and _intact(prev)
        assert fix["previous"]["done"] < fix["previous"]["total"]
        assert {p[0] for p in _diff_paths(bad, fix)} == {"previous"}
    else:
        raise AssertionError(f"unknown malformed tag {name}")


def _check_rollback_scenario(row):
    name, bad, q = row["name"], row["rejected_request"], row["request"]
    assert q["previous"] is None or _intact(q["previous"])
    if name == "conflict_then_forward_report":
        assert bad["done"] < bad["previous"]["done"] < q["done"]
        _only(bad, q, ("done",))
    elif name == "corrupt_previous_then_intact_previous":
        assert not _intact(bad["previous"])
        assert {p[0] for p in _diff_paths(bad, q)} == {"previous"}
    elif name == "malformed_then_repaired":
        assert bad["done"] == q["total"] + 1 and q["done"] == q["total"]
        _only(bad, q, ("done",))
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
                assert (_progress(row), _links(row)) == meta, label
                OK_EDGES[row["name"]](row)
            elif section == "malformed":
                assert (row["expect_failure"], row["defect"]) == meta, label
                _m(row["name"], row["request"], row["minimal_repair"]["request"])
            else:
                rec = row["expect_record"]
                got = (
                    row["expect_failure"],
                    rec["done"],
                    rec["percent_bp"],
                    rec["previous_id"] is not None,
                )
                assert got == meta, label
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
    recs = [rec for s in ("happy", "boundary") for row in CASES[s] for rec in row["expect_records"]]
    assert {rec["previous_id"] is None for rec in recs} == {True, False}
    assert {rec["done"] == rec["total"] for rec in recs} == {True, False}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(schema=2),
        lambda c: c.update(schema=True),
        lambda c: c.update(contract="jobs-retry"),
        lambda c: c.update(contract_base_path="/jobs/progress/v2"),
        lambda c: c.update(extra=1),
        lambda c: c.pop("rollback"),
        lambda c: c["boundary"].clear(),
        lambda c: c["happy"][0].update(eta=0),
        lambda c: c["happy"][0]["expect_records"].pop(),
        lambda c: c["happy"][0]["expect_records"][0].pop("reported_at"),
        lambda c: c["happy"][0]["expect_records"][0].update(extra=1),
        lambda c: c["happy"][0]["expect_records"][0].update(progress_id="pg1:zz"),
        lambda c: c["happy"][0]["expect_records"][0].update(op="progress"),
        lambda c: c["happy"][0]["expect_records"][0].update(percent_bp=1),
        lambda c: c["happy"][0]["expect_records"][0].update(done=11),
        lambda c: c["happy"][0]["expect_records"][0].update(reported_at=1),
        lambda c: c["happy"][0]["expect_records"][0].update(worker="w-9"),
        lambda c: c["happy"][0]["expect_records"][0].update(previous_id="pg1:" + "0" * 64),
        lambda c: c["happy"][1]["expect_records"][1].update(previous_id=None),
        lambda c: c["happy"][1]["requests"][2]["previous"].update(done=0),
        lambda c: c["happy"][0]["requests"][0].update(eta=1),
        lambda c: c["happy"][0]["requests"][0].update(previous=[]),
        lambda c: c["malformed"][0].update(expect_failure="internal"),
        lambda c: c["malformed"][1].update(
            minimal_repair={"request": c["malformed"][1]["request"]}
        ),
        lambda c: c["malformed"][1]["minimal_repair"].update(state={}),
        lambda c: c["malformed"][1].update(minimal_repair={"previous": {}}),
        lambda c: c["rollback"][0].update(expect_failure="internal"),
        lambda c: c["rollback"][0].update(request=c["rollback"][0]["rejected_request"]),
        lambda c: c["rollback"][0]["expect_record"].update(progress_id="pg1:" + "0" * 63),
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
    """Re-pin the row from the reference, re-chaining every linked step."""
    records = []
    for q in row["requests"]:
        if q["previous"] is not None:
            q["previous"] = records[-1]
        records.append(_report(copy.deepcopy(q)))
    row["expect_records"] = records


def _edit(section, name, fn):
    def mutant(c):
        row = _bn(c, section, name)
        fn(row)
        if section in ("happy", "boundary"):
            _regenerate(row)

    return mutant


def _step(i, **kw):
    return lambda row: row["requests"][i].update(kw)


_CLOSURE_MUTANTS = {
    "swap_boundary_names": lambda c: _swap(
        c, "boundary", "equal_now_accepted", "jump_straight_to_completion", "name"
    ),
    "swap_happy_names": lambda c: _swap(
        c, "happy", "first_report_at_zero", "heartbeat_equal_done", "name"
    ),
    "swap_malformed_names_same_class": lambda c: _swap(
        c, "malformed", "done_negative", "now_negative", "name"
    ),
    "swap_malformed_requests": lambda c: _swap(
        c, "malformed", "done_negative", "now_negative", "request", "minimal_repair"
    ),
    "swap_conflict_requests": lambda c: _swap(
        c, "malformed", "worker_changed", "job_changed", "request", "minimal_repair"
    ),
    "drop_done_bool": lambda c: c["malformed"].remove(_bn(c, "malformed", "done_bool")),
    "duplicate_unknown_op": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "unknown_op"))
    ),
    "clock_edge_one_short": _edit("boundary", "now_at_clock_limit", _step(0, now=MAX - 1)),
    "equal_now_made_strict": _edit("boundary", "equal_now_accepted", _step(1, now=71)),
    "heartbeat_same_instant": _edit(
        "happy",
        "heartbeat_equal_done",
        lambda row: row["requests"][1].update(now=row["requests"][0]["now"]),
    ),
    "worker_bound_one_short": _edit(
        "boundary", "worker_at_grammar_bound", _step(0, worker="w" * 63)
    ),
    "new_worker_same_worker": _edit(
        "boundary", "new_worker_starts_new_chain", _step(1, worker="w-1")
    ),
    "first_report_not_zero": _edit("happy", "first_report_at_zero", _step(0, done=1)),
    "chain_repeats_a_step": _edit("happy", "chain_to_completion", _step(2, done=1)),
    "repair_touches_two_fields": lambda c: _bn(c, "malformed", "done_negative")["minimal_repair"][
        "request"
    ].update(now=1),
    "clock_repair_off_by_one": lambda c: _bn(c, "malformed", "clock_regression")["minimal_repair"][
        "request"
    ].update(now=51),
    "worker_bound_repair_short": lambda c: _bn(c, "malformed", "worker_past_grammar_bound")[
        "minimal_repair"
    ]["request"].update(worker="w" * 63),
    "rollback_follow_up_widened": lambda c: _bn(c, "rollback", "conflict_then_forward_report")[
        "request"
    ].update(now=61),
    "renamed_field_second_defect": lambda c: _bn(c, "malformed", "renamed_field")["request"].update(
        worker="w-9"
    ),
    "previous_renamed_second_defect": lambda c: _bn(c, "malformed", "previous_renamed_field")[
        "request"
    ]["previous"].update(worker="w-9"),
    "renamed_back_to_extra": lambda c: _bn(c, "malformed", "renamed_field")["request"].update(
        now=0
    ),
    "unknown_op_retyped": lambda c: _bn(c, "malformed", "unknown_op")["request"].update(op=None),
    "defect_text_edited": lambda c: _bn(c, "malformed", "done_bool").update(defect="done is odd"),
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
    assert [_report(q) for q in requests] == row["expect_records"]
    assert requests == row["requests"]
    again = [_report(copy.deepcopy(q)) for q in row["requests"]]
    assert again == row["expect_records"]


def test_pinned_ids_match_the_contract_preimage():
    """Every pinned progress_id is the hand-built contract preimage of its
    own record, so a record and its id cannot drift together."""
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            for rec in row["expect_records"]:
                assert _intact(rec), row["name"]
    for row in CASES["rollback"]:
        assert _intact(row["expect_record"]), row["name"]


def test_tampered_pinned_record_is_detected():
    row = copy.deepcopy(_row("happy", "chain_to_completion"))
    row["expect_records"][1]["percent_bp"] += 1
    assert [_report(copy.deepcopy(q)) for q in row["requests"]] != row["expect_records"]


def _rejects(failure_class, request):
    before = copy.deepcopy(request)
    with pytest.raises(_REF.ProgressError) as err:
        _report(request)
    exc = err.value
    assert type(exc) is _REF.ProgressError
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
    record = _report(request)
    _validate_record(record, name)
    assert request == row["minimal_repair"]["request"]


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_row("malformed", "done_over_total"))
    row["request"]["done"] = 10
    assert _fails(row["request"]) is None


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    rejected = copy.deepcopy(row["rejected_request"])
    _rejects(row["expect_failure"], rejected)
    assert _snap(rejected) == _snap(row["rejected_request"])
    request = copy.deepcopy(row["request"])
    assert _report(request) == row["expect_record"]
    assert _snap(request) == _snap(row["request"])


def test_validation_precedence():
    """request shape -> previous integrity -> chain, first failure wins
    (derived from fixture rows so the order runs on pinned data)."""
    tampered = _row("malformed", "previous_done_tampered")["request"]
    conflict = _row("malformed", "total_changed")["request"]
    _rejects("malformed_progress_request", {**tampered, "done": -1})
    _rejects("malformed_progress_request", {**conflict, "now": -1})
    _rejects("corrupt_previous_record", {**conflict, "previous": tampered["previous"]})


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


_STR_FIELDS = ("op", "job_id", "worker")
_INT_FIELDS = ("done", "total", "now")
_PREV_STR_FIELDS = ("op", "job_id", "worker", "progress_id")
_PREV_INT_FIELDS = ("done", "total", "percent_bp", "reported_at")
_MP_ = "malformed_progress_request"
_CP_ = "corrupt_previous_record"


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
        out.append(
            (
                f"{prefix}int-subclass-{field}",
                wrap({**mapping, field: _IntSub(mapping[field])}),
                failure,
            )
        )
        out.append((f"{prefix}bool-{field}", wrap({**mapping, field: True}), failure))
    return out


def _variants(q):
    """(label, hostile request, expected failure class) for one accepted
    request: every container, key and value boundary, on the request and
    (when present) on the nested previous record."""
    out = [
        ("dict-subclass", _DictSub(q), _MP_),
        ("lying-dict", _LyingDict(q), _MP_),
        ("list-subclass", _ListSub(q.items()), _MP_),
    ]
    for field in FIELDS:
        for label, make in KEY_FORMS.items():
            out.append((f"key-{label}-{field}", _rekey(q, field, make), _MP_))
    out += _value_variants("", q, _STR_FIELDS, _INT_FIELDS, lambda m: m, _MP_)
    prev = q["previous"]
    if prev is None:
        out += [
            ("previous-dict-subclass", {**q, "previous": _DictSub()}, _MP_),
            ("previous-lying-dict", {**q, "previous": _LyingDict()}, _MP_),
            ("previous-list-subclass", {**q, "previous": _ListSub()}, _MP_),
        ]
        return out
    out += [
        ("previous-dict-subclass", {**q, "previous": _DictSub(prev)}, _MP_),
        ("previous-lying-dict", {**q, "previous": _LyingDict(prev)}, _MP_),
        ("previous-list-subclass", {**q, "previous": _ListSub(prev.items())}, _MP_),
    ]
    for field in RECORD_FIELDS:
        for label, make in KEY_FORMS.items():
            out.append(
                (
                    f"previous-key-{label}-{field}",
                    {**q, "previous": _rekey(prev, field, make)},
                    _CP_,
                )
            )

    def wrap(p):
        return {**q, "previous": p}

    out += _value_variants("previous-", prev, _PREV_STR_FIELDS, _PREV_INT_FIELDS, wrap, _CP_)
    if prev["previous_id"] is not None:
        out.append(
            (
                "previous-str-subclass-previous_id",
                wrap({**prev, "previous_id": _StrSub(prev["previous_id"])}),
                _CP_,
            )
        )
        out.append(
            (
                "previous-repr-raises-previous_id",
                wrap({**prev, "previous_id": _ReprRaises(prev["previous_id"])}),
                _CP_,
            )
        )
    out.append(
        ("previous-nested-previous_id", wrap({**prev, "previous_id": [prev["previous_id"]]}), _CP_)
    )
    return out


def _hostile(failure_class, request):
    """Typed class, fresh error, no user code ran, input unchanged."""
    snap = _snap(request)
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            _report(request)
            got = "accepted"
        except _REF.ProgressError as exc:
            got = (
                type(exc) is _REF.ProgressError,
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
    base = 3 + 3 * len(FIELDS) + 4 * len(_STR_FIELDS) + 2 * len(_INT_FIELDS)
    chained = [q for _, q in _ACCEPTED if q["previous"] is not None]
    first = [q for _, q in _ACCEPTED if q["previous"] is None]
    assert chained and first
    linked = [q for q in chained if q["previous"]["previous_id"] is not None]
    unlinked = [q for q in chained if q["previous"]["previous_id"] is None]
    assert linked and unlinked
    prev = 3 + 3 * len(RECORD_FIELDS) + 4 * len(_PREV_STR_FIELDS) + 2 * len(_PREV_INT_FIELDS)
    for q, want in (
        (first[0], base + 3),
        (unlinked[0], base + prev + 1),
        (linked[0], base + prev + 3),
    ):
        labels = [label for label, _, _ in _variants(q)]
        assert len(labels) == len(set(labels)) == want


# -- the T0320 reference mutants must each turn this fixture red -------------


def _fixture_red():
    """Labels of every fixture check that disagrees with the reference
    currently bound (empty when green)."""
    red = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            try:
                got = [_report(copy.deepcopy(q)) for q in row["requests"]]
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


# one unchained and one linked-chain request probe the hostile rows under
# every mutant (the full set runs in the parametrized test)
_HOSTILE_PROBES = [
    next(x for x in _ACCEPTED if x[1]["previous"] is None),
    next(x for x in _ACCEPTED if x[1]["previous"] is not None and x[1]["previous"]["previous_id"]),
]


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


# Extra one-edit mutants of the T0320 reference that this fixture kills on
# its own (the type, grammar and bound guards the rows exist for). Not
# listed: dropping the len(mapping) == len(fields) clause of _exact_keys is
# equivalent, since exact-str dict keys are unique, so set equality
# already implies equal length.
FIXTURE_EDITS = {
    "prev-total-bound-plus": (
        '_int_in(prev["total"], 1, _REF_MAX_TOTAL)',
        '_int_in(prev["total"], 1, _REF_MAX_TOTAL + 1)',
    ),
    "prev-total-lower-zero": (
        '_int_in(prev["total"], 1, _REF_MAX_TOTAL)',
        '_int_in(prev["total"], 0, _REF_MAX_TOTAL)',
    ),
    "prev-done-upper-plus": (
        '_int_in(prev["done"], 0, prev["total"])',
        '_int_in(prev["done"], 0, prev["total"] + 1)',
    ),
    "prev-clock-bound-plus": (
        '_int_in(prev["reported_at"], 0, _REF_MAX_NOW)',
        '_int_in(prev["reported_at"], 0, _REF_MAX_NOW + 1)',
    ),
    "prev-op-check-off": ('        and prev["op"] == "report"\n', ""),
    "percent-float": (
        '"percent_bp": req["done"] * _REF_BP // req["total"],',
        '"percent_bp": int(req["done"] / req["total"] * _REF_BP),',
    ),
    "prev-done-bound-total": (
        '_int_in(prev["done"], 0, prev["total"])',
        '_int_in(prev["done"], 0, _REF_MAX_TOTAL)',
    ),
    "keys-off": ("        and set(mapping) == set(fields)\n", ""),
    "exact-dict-off": (
        "        type(mapping) is dict\n        and ",
        "        isinstance(mapping, dict)\n        and ",
    ),
    "op-type-off": (
        '        type(request["op"]) is str\n        and request["op"]',
        '        request["op"]',
    ),
    "op-check-off": (
        '        and request["op"] == "report"\n        and _grammar(request["job_id"]',
        '        and _grammar(request["job_id"]',
    ),
    "job-grammar-off": ('        and _grammar(request["job_id"], _JOB_RE)\n', ""),
    "worker-grammar-off": ('        and _grammar(request["worker"], _WORKER_RE)\n', ""),
    "total-lower-zero": (
        '_int_in(request["total"], 1, _REF_MAX_TOTAL)',
        '_int_in(request["total"], 0, _REF_MAX_TOTAL)',
    ),
    "done-upper-plus": (
        '_int_in(request["done"], 0, request["total"])',
        '_int_in(request["done"], 0, request["total"] + 1)',
    ),
    "now-lower-minus": (
        '_int_in(request["now"], 0, _REF_MAX_NOW)',
        '_int_in(request["now"], -1, _REF_MAX_NOW)',
    ),
    "prev-dict-any": (
        'type(request["previous"]) is dict)',
        'isinstance(request["previous"], dict))',
    ),
    "prev-op-type-off": (
        '        type(prev["op"]) is str\n        and prev["op"]',
        '        prev["op"]',
    ),
    "prev-job-grammar-off": ('        and _grammar(prev["job_id"], _JOB_RE)\n', ""),
    "prev-worker-grammar-off": ('        and _grammar(prev["worker"], _WORKER_RE)\n', ""),
    "prev-done-type-off": ('        and _int_in(prev["done"], 0, prev["total"])\n', ""),
    "prev-percent-type-off": ('        and type(prev["percent_bp"]) is int\n', ""),
    "prev-link-type-off": (
        '(prev["previous_id"] is None or _grammar(prev["previous_id"], _PROGRESS_RE))',
        "True",
    ),
    "prev-id-type-off": ('        and _grammar(prev["progress_id"], _PROGRESS_RE)\n', ""),
    "prev-shape-skipped": ("    if not shape:\n        return False\n", ""),
    "chain-skipped": (
        '    elif request["previous"] is not None and not _chain_ok(request):\n'
        '        failed = "progress_conflict"\n',
        "",
    ),
    "percent-from-total": (
        '"percent_bp": req["done"] * _REF_BP // req["total"],',
        '"percent_bp": req["done"] * _REF_BP // max(req["total"], 4),',
    ),
    "link-dropped": (
        '"previous_id": None if prev is None else prev["progress_id"],',
        '"previous_id": None,',
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
    assert len({new for _old, new in FIXTURE_EDITS.values()}) >= 1
