"""T0339: jobs rate-limit conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the T0338
jobs-limit contract (data/contracts/limit.yaml). The cases execute
against the contract-derived reference admit in
tests.test_t0338_limit_contract - nothing is re-implemented here.
Pinned bucket records were computed from that reference at authoring
time and chain through previous, so any contract or derivation drift
breaks this battery. Every malformed case is discriminating: applying
ONLY its declared single-locus repair makes it decide. Rollback cases
prove a rejected request leaves the exact supplied input type-exactly
unchanged before a valid follow-up is decided as pinned.

Hostile-type rows are derived here from EVERY request the fixture
accepts (happy and boundary requests, malformed repairs, rollback
follow-ups): hostile containers on the request, the policy and the
previous record, str-subclass keys in three forms on every request,
policy and record field, and str-subclass, raising eq/repr, nested,
int-subclass, bool and float values on each of them. Each must fail
typed with a fresh error, run no user code and leave the input
unchanged.

Every one-edit reference mutant pinned by the T0338 battery must turn
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

import tests.test_t0338_limit_contract as _REF  # noqa: E402
from tools.limit_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "limit" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECORD_FIELDS = list(_CC["record"]["fields"])
FIELDS = _CC["request"]["operations"][0]["fields"]
POLICY_FIELDS = list(_CC["request"]["policy_fields"])
DECISIONS = tuple(_CC["record"]["decisions"])
_KEY_RE = re.compile(_CC["identifiers"]["key"]["grammar"], re.ASCII)
_LIMIT_RE = re.compile(_CC["identifiers"]["limit_id"]["grammar"], re.ASCII)
MAX = 9007199254740991  # 2**53 - 1, written out
MAX_CAPACITY = 1000000
MAX_REFILL = 86400000

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "requests", "expect_records"}
BAD_KEYS = {"name", "defect", "expect_failure", "request", "minimal_repair"}
RB_KEYS = {"name", "why", "rejected_request", "expect_failure", "request", "expect_record"}


def _admit(request):
    return _REF.admit(request)  # late-bound: reference mutants rebind it


def _fails(request):
    try:
        _admit(copy.deepcopy(request))
    except _REF.LimitError as exc:
        return exc.failure_class
    return None


def _preimage(record):
    return _REF._independent_id({k: record[k] for k in RECORD_FIELDS[:-1]})


def _validate_record(record, label):
    assert type(record) is dict, label
    assert all(type(k) is str for k in record) and set(record) == set(RECORD_FIELDS), label
    assert record["op"] == "admit" and record["decision"] in DECISIONS, label
    assert _KEY_RE.fullmatch(record["key"]), label
    assert _LIMIT_RE.fullmatch(record["limit_id"]), label
    assert record["previous_id"] is None or _LIMIT_RE.fullmatch(record["previous_id"]), label
    assert 0 <= record["tokens"] <= record["capacity"], label
    if record["decision"] == "admit":
        assert record["retry_after_ms"] is None, label
    else:
        assert record["tokens"] < record["cost"] and record["retry_after_ms"] >= 1, label
    assert record["limit_id"] == _preimage(record), label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert all(type(k) is str for k in request) and set(request) == set(FIELDS), label
    assert type(request["policy"]) is dict, label
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
                    for field in ("key", "cost"):
                        assert record[field] == request[field], label
                    for field in POLICY_FIELDS:
                        assert record[field] == request["policy"][field], label
                    prev = request["previous"]
                    assert record["previous_id"] == (None if prev is None else prev["limit_id"]), (
                        label
                    )
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


def _summary(row):
    return (
        [
            (r["cost"], r["decision"], r["tokens"], r["updated_at"], r["retry_after_ms"])
            for r in row["expect_records"]
        ],
        "".join("L" if r["previous_id"] else "-" for r in row["expect_records"]),
    )


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> ([(cost, decision, tokens, updated_at, retry_after_ms)],
# chain links); malformed: name -> (failure class, pinned defect text);
# rollback: name -> (failure class, follow-up decision, tokens, linked)
HAPPY_MANIFEST = {
    "fresh_bucket_admits": ([(1, "admit", 2, 0, None)], "-"),
    "drain_then_deny": (
        [
            (1, "admit", 2, 0, None),
            (1, "admit", 1, 0, None),
            (1, "admit", 0, 0, None),
            (1, "deny", 0, 0, 1000),
        ],
        "-LLL",
    ),
    "partial_refill_is_carried": (
        [(3, "admit", 0, 0, None), (1, "admit", 0, 1000, None), (1, "admit", 0, 2000, None)],
        "-LL",
    ),
    "refill_caps_at_capacity": ([(1, "admit", 2, 0, None), (1, "admit", 2, 10500, None)], "-L"),
}
BOUNDARY_MANIFEST = {
    "cost_equals_capacity": ([(3, "admit", 0, 0, None)], "-"),
    "capacity_one": ([(1, "admit", 0, 5, None), (1, "deny", 0, 5, 1000)], "-L"),
    "capacity_at_bound": ([(1000000, "admit", 0, 0, None)], "-"),
    "refill_at_bound": ([(1, "admit", 0, 0, None), (1, "deny", 0, 0, 86400000)], "-L"),
    "now_at_clock_limit": ([(1, "admit", 2, MAX, None)], "-"),
    "refill_lands_exactly_on_capacity_with_remainder": (
        [(1, "admit", 2, 1000, None), (1, "admit", 2, 2500, None)],
        "-L",
    ),
    "deny_retry_at_window_ceiling_chains": (
        [
            (1, "admit", 2, 0, None),
            (1, "admit", 1, 0, None),
            (1, "admit", 0, 0, None),
            (1, "deny", 0, 0, 1000),
            (1, "admit", 0, 1000, None),
        ],
        "-LLLL",
    ),
    "equal_now_accepted": ([(1, "admit", 2, 70, None), (1, "admit", 1, 70, None)], "-L"),
    "gain_is_floored": ([(3, "admit", 0, 0, None), (1, "deny", 0, 0, 1)], "-L"),
    "retry_counts_elapsed_partial": ([(3, "admit", 0, 0, None), (3, "deny", 1, 1000, 1500)], "-L"),
    "overflow_edge_accepted": (
        [(1, "admit", 0, MAX - 1000, None), (1, "deny", 0, MAX - 1000, 1000)],
        "-L",
    ),
    "overflow_edge_after_partial": (
        [(2, "admit", 0, MAX - 2000, None), (2, "deny", 1, MAX - 1000, 500)],
        "-L",
    ),
    "key_at_grammar_bound": ([(1, "admit", 2, 3, None)], "-"),
    "policy_change_starts_fresh": ([(1, "admit", 2, 10, None), (1, "admit", 4, 11, None)], "--"),
}
_MR, _IP, _CB = "malformed_limit_request", "invalid_limit_policy", "corrupt_previous_bucket"
_LC, _CO = "limit_conflict", "clock_overflow"
MALFORMED_MANIFEST = {
    "request_not_a_dict": (_MR, "the request is a list"),
    "unknown_op": (_MR, "op is 'take', not a registered operation"),
    "extra_field": (_MR, "the request carries an extra note field"),
    "missing_field": (_MR, "the request has no previous field"),
    "renamed_field": (_MR, "the now field is renamed to at, keeping the field count"),
    "key_bad_grammar": (_MR, "the key holds a space"),
    "key_trailing_newline": (_MR, "the key ends in a newline"),
    "key_past_grammar_bound": (_MR, "the key has 65 characters"),
    "cost_zero": (_MR, "cost is 0"),
    "cost_bool": (_MR, "cost is the bool True"),
    "cost_past_bound_with_bad_policy": (
        _MR,
        "cost 1000001 is past the cost bound while the policy's capacity is 0:"
        " the request check runs first, so this is malformed_limit_request,"
        " not invalid_limit_policy (two loci: the repair also sets capacity 3)",
    ),
    "cost_above_capacity": (
        _MR,
        "cost 4 is above capacity 3, a malformed request and never a deny",
    ),
    "now_negative": (_MR, "now is -1"),
    "now_past_clock": (_MR, "now is 2**53"),
    "policy_not_a_dict": (_MR, "policy is a list"),
    "previous_not_a_dict": (_MR, "previous is a list"),
    "policy_extra_field": (_IP, "the policy carries an extra burst field"),
    "policy_renamed_field": (
        _IP,
        "the policy's refill_ms is renamed to period_ms, keeping the field count",
    ),
    "capacity_zero": (_IP, "capacity is 0"),
    "capacity_past_bound": (_IP, "capacity is 1000001"),
    "capacity_bool": (_IP, "capacity is the bool True"),
    "refill_zero": (_IP, "refill_ms is 0"),
    "refill_past_bound": (_IP, "refill_ms is 86400001"),
    "previous_tokens_tampered": (
        _CB,
        "the previous record's tokens was edited from 1 to 2 without a new limit_id",
    ),
    "previous_id_tampered": (_CB, "the previous record's limit_id has one hex digit changed"),
    "previous_extra_field": (_CB, "the previous record carries an extra note field"),
    "previous_renamed_field": (
        _CB,
        "the previous record's updated_at is renamed to at, keeping the field count",
    ),
    "previous_op_wrong": (
        _CB,
        "the previous record's op is 'take', with its limit_id recomputed to match",
    ),
    "previous_decision_unknown": (
        _CB,
        "a deny previous's decision is 'hold', with its limit_id recomputed to match",
    ),
    "previous_cost_past_capacity": (
        _CB,
        "a deny previous has cost 4 of capacity 3 (retry_after_ms 2990 in"
        " window), with its limit_id recomputed to match",
    ),
    "previous_admit_with_retry": (
        _CB,
        "an admit previous carries retry_after_ms 5, with its limit_id recomputed to match",
    ),
    "previous_admit_tokens_past_cost": (
        _CB,
        "an admit previous of cost 2 keeps tokens 2 of capacity 3, with its"
        " limit_id recomputed to match",
    ),
    "previous_deny_retry_past_window": (
        _CB,
        "a deny previous waits 2001 ms for 2 tokens at refill 1000, with its"
        " limit_id recomputed to match",
    ),
    "previous_deny_retry_overflows": (
        _CB,
        "a deny previous at updated_at 2**53-1000 waits 1990 ms, past 2**53-1,"
        " with its limit_id recomputed to match",
    ),
    "previous_refill_past_bound": (
        _CB,
        "the previous record's refill_ms is 86400001, with its limit_id recomputed to match",
    ),
    "previous_key_bad_grammar": (
        _CB,
        "the previous record's key is 'k 1', with its limit_id recomputed to match",
    ),
    "previous_tokens_negative": (
        _CB,
        "an admit previous keeps tokens -1, with its limit_id recomputed to match",
    ),
    "previous_cost_zero": (
        _CB,
        "the previous record's cost is 0, with its limit_id recomputed to match",
    ),
    "previous_retry_below_window": (
        _CB,
        "a deny previous (cost 2, tokens 0) waits exactly 1000 ms, the"
        " tightest value below its window, with its limit_id recomputed to"
        " match",
    ),
    "previous_prev_id_trailing_newline": (
        _CB,
        "the previous record's previous_id ends in a newline, with its"
        " limit_id recomputed to match",
    ),
    "previous_updated_at_negative": (
        _CB,
        "the previous record's updated_at is -1, with its limit_id recomputed to match",
    ),
    "previous_refill_zero": (
        _CB,
        "the previous record's refill_ms is 0, with its limit_id recomputed to match",
    ),
    "previous_updated_at_past_bound": (
        _CB,
        "the previous record's updated_at is 2**53, with its limit_id recomputed to match",
    ),
    "previous_capacity_past_bound": (
        _CB,
        "the previous record's capacity is 1000001, with its limit_id recomputed to match",
    ),
    "key_changed": (_LC, "key k-2 differs from the previous key k-1"),
    "capacity_changed": (_LC, "capacity 4 differs from the previous capacity 3"),
    "refill_changed": (_LC, "refill_ms 500 differs from the previous refill_ms 1000"),
    "clock_regression": (_LC, "now 49 precedes the previous updated_at 50"),
    "clock_overflow_one_past": (
        _CO,
        "a deny for 1 more token at 2**53-999 would retry to exactly 2**53",
    ),
    "clock_overflow_after_partial": (
        _CO,
        "after a banked partial refill (updated_at 2**53-999), a deny at"
        " 2**53-499 would retry from now to 2**53, though from updated_at it"
        " would fit",
    ),
    "clock_overflow_deny": (
        _CO,
        "a deny for 2 more tokens at 2**53-1501 would retry past 2**53-1",
    ),
}
ROLLBACK_MANIFEST = {
    "conflict_then_forward": (_LC, "admit", 0, True),
    "corrupt_previous_then_intact_previous": (_CB, "admit", 0, True),
    "invalid_policy_then_repaired": (_IP, "admit", 2, False),
    "overflow_then_smaller_cost": (_CO, "admit", 0, True),
    "malformed_then_repaired": (_MR, "admit", 0, False),
}

# content binding: canonical sha256 of every row. Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:fresh_bucket_admits": "d22f97e81a727eae79dc21517a60506e9d07c16ccfbac402845271fa3b828961",  # noqa: E501
    "happy:drain_then_deny": "27ecf5d957ee72f28b71f71735dce762b6435f6bf1f2ee15a78dba3560c2b729",  # noqa: E501
    "happy:partial_refill_is_carried": "de6e0289acb25eecd6077cbe7a35d6c4404711c07a11352aa5980f4f108f9eb7",  # noqa: E501
    "happy:refill_caps_at_capacity": "bd78dbee084ac8b72f95f773459420b385ac0e9c7f2af6760473f23a637933f1",  # noqa: E501
    "boundary:cost_equals_capacity": "ce709bcb23b8ebc58910859c29d2f7a0a24dd743f183a3c60f2e36e44437ff4d",  # noqa: E501
    "boundary:capacity_one": "42292975e2c7372883fb7146a6a4e12e42a9207d27bb4c5aadba6a642e450e98",  # noqa: E501
    "boundary:capacity_at_bound": "a0bb3427866228cd638ce84c5cae600ea7af29a6d171c13412f6efea2eaab564",  # noqa: E501
    "boundary:refill_at_bound": "a61e9f95dcba27826375465ce985a4c2d79b576011a4dd26e02cdc88e0f9a30a",  # noqa: E501
    "boundary:now_at_clock_limit": "b88da3c23826705d48a9dd755d7b33d63e2739567c98dc48fed6589482fcd1f7",  # noqa: E501
    "boundary:refill_lands_exactly_on_capacity_with_remainder": "99dff73ac9f23caa39787a613eff64a42011b57ce4a1e6a040b619fc264cd946",  # noqa: E501
    "boundary:deny_retry_at_window_ceiling_chains": "d117fe826767f284ab86ff90aa305ea3a268abbe469c96621074e12e8d1a003f",  # noqa: E501
    "boundary:equal_now_accepted": "6fd028374d088f2aef1cd4a419fc7ae6088cc98c522d63f45f889aa188438664",  # noqa: E501
    "boundary:gain_is_floored": "d07a13c1d387401142b2434bd172dc79a16cd38096fa5e28e441c25ce43247c0",  # noqa: E501
    "boundary:retry_counts_elapsed_partial": "ca2c7232af1d831718cd4e42e3268a778e4fe35ca1e1a6c51051e10ac9785aa9",  # noqa: E501
    "boundary:overflow_edge_accepted": "c5f2c696db5a774519b4e47aa90a71fa24a9b543ecb49dad75ab6c1ce80aa040",  # noqa: E501
    "boundary:overflow_edge_after_partial": "672449924f3e3362e07afd492b5cd7bcbdcd46bcfca4f0b21cbc858186466528",  # noqa: E501
    "boundary:key_at_grammar_bound": "f02ab15330d91da7b7984731d5a544b8826d9689c7619ccba2caceba95d780ee",  # noqa: E501
    "boundary:policy_change_starts_fresh": "d067214f36f7915eaf6098c69e2b2864436b59606de72237511be973960189a5",  # noqa: E501
    "malformed:request_not_a_dict": "672d5fa6fb9e9e643d8cbcf581eafc328a45915c353b88f21d997e162290721d",  # noqa: E501
    "malformed:unknown_op": "9f92474269c05db68c76fde6f7a2795aabe39d9fcec384a12d93b94f65d0c957",  # noqa: E501
    "malformed:extra_field": "0c7845c4cb7670b0a139019a9b03adcafcad64c5393d95e11b2d48b6ea8fb383",  # noqa: E501
    "malformed:missing_field": "59bd85fccb2558eecf6a1663e08d4c87c71609c35d04c032140509c98057d153",  # noqa: E501
    "malformed:renamed_field": "06a2f82784c9960a4b1491a9ad7a73f3afdccd52fecb72bc3148d4caa57d3149",  # noqa: E501
    "malformed:key_bad_grammar": "2a2e113ecd7d6715b97eaaba530d86dd6af85fa0dd0f649d0c7caa9a6150f5dd",  # noqa: E501
    "malformed:key_trailing_newline": "bb5220ed94c8f1003a6f21a3362c0efb2838dc22e9666a3bb3bd320518ddd94c",  # noqa: E501
    "malformed:key_past_grammar_bound": "4de67ac1ce5073e5a3163c4d6784517f7d0b17e64699b6dd04a3f509ae3365f8",  # noqa: E501
    "malformed:cost_zero": "93739527d64149f0fcfce9cd63544d88055bf9935dbe916f9bccfc466f28855f",  # noqa: E501
    "malformed:cost_bool": "93d4a8aeade911bd8410f07414be6e64f2a708a96c47545749ab75cab434f104",  # noqa: E501
    "malformed:cost_past_bound_with_bad_policy": "8e0f159b7888a6636de3e8ae46887f75dc1e150d64d8602f245c3fd78475320e",  # noqa: E501
    "malformed:cost_above_capacity": "f59c7120dbd0113085163fa2973ef33a3bc57b460b1a974dd513a13212f9e912",  # noqa: E501
    "malformed:now_negative": "3540403d0264ca449f98c1d417607af100fd4419eddfff3434ad9baf5f917b72",  # noqa: E501
    "malformed:now_past_clock": "0a98066e9882f1dd1d0bcc6d4878bbecec618be8266a6bc7ad84f02ba142a702",  # noqa: E501
    "malformed:policy_not_a_dict": "cbbe9002c69bff7a858acfb0c1f20680f38f2082e5dc0580f7a6b477e85cb948",  # noqa: E501
    "malformed:previous_not_a_dict": "8de973ff36c8850d0cb573344cb93b099ed90422d05e2b5b700df39d88ec5a6b",  # noqa: E501
    "malformed:policy_extra_field": "fcbe633419276e369927cf9e900cfe62fdf88d2e4ac42f42d38586b02010c54e",  # noqa: E501
    "malformed:policy_renamed_field": "fc54cc5819a3cab63393d9912ed6681004d4c05747fb6a4fd6317d56a02315b2",  # noqa: E501
    "malformed:capacity_zero": "3f466117663d8cc579190cdd7646e2060902aded92ca8f8a33a354a64f456db1",  # noqa: E501
    "malformed:capacity_past_bound": "28d8f65105ba672adf19bdb6fe799830d00573e0e9f190b2cdc81ed4dab089c6",  # noqa: E501
    "malformed:capacity_bool": "2b2664049d0d1fd09cd75ca014fc7e69697159260433eff666319b500072d239",  # noqa: E501
    "malformed:refill_zero": "7edbfd9c8ac36e7b67f9bc4f41dfc50087ba6e900d423f55a1f782f37b5488a5",  # noqa: E501
    "malformed:refill_past_bound": "1467006aab78608a71c816654eae1e96c913f0aa1b7148d463bf2493460b6258",  # noqa: E501
    "malformed:previous_tokens_tampered": "73fc53e8a880563613fece77f4623125f65a12d6a50daec8039e5278f77fd883",  # noqa: E501
    "malformed:previous_id_tampered": "cb78b20d9ab17551a4cff51c88caedb873ab3727fade7437e65f4c74d98b5173",  # noqa: E501
    "malformed:previous_extra_field": "2de9b2eeacf4469298510674e79528b05fc14ddeff3291fa44e2fc5ce6ed4bfe",  # noqa: E501
    "malformed:previous_renamed_field": "218b3299783a1cea8376774a0ac146ff6b6e53a470b1fa88e30a48234e33ed2d",  # noqa: E501
    "malformed:previous_op_wrong": "c0610002b6f15dfbd989af06281e1c7618f5e754baef4103d184cfbd6eaaedf6",  # noqa: E501
    "malformed:previous_decision_unknown": "9f02ebb04b8571da38d7d1b598b3003be728d8015cc8ce7292d566d0f7033999",  # noqa: E501
    "malformed:previous_cost_past_capacity": "2ff7859c111c24ca335edc50e6151e636a003a83995029b38dc3b74832729136",  # noqa: E501
    "malformed:previous_admit_with_retry": "bdeedd7ca3f1e16a157691dc7eabe84a69894ced35f1bd45c85b79575873fc39",  # noqa: E501
    "malformed:previous_admit_tokens_past_cost": "944d4268d7bcdddfc6fa966798552d08ef2d794d645a3ce5bad5f85c9ea98f64",  # noqa: E501
    "malformed:previous_deny_retry_past_window": "d5461f53caef30499c83557be86938f04ed5e3b286b651585aeb13e79f876640",  # noqa: E501
    "malformed:previous_deny_retry_overflows": "f54a889ae17f492c403a9b515b9c106af0cb8b3add59488c61ea23b758718a22",  # noqa: E501
    "malformed:previous_refill_past_bound": "9e45f87e88e1bccf3969b57f92a5b3bd23cb110ae149d5420965cf7ba6c24f68",  # noqa: E501
    "malformed:previous_key_bad_grammar": "eff7bb3d48904306ed1463447c0ad79130a06b6b66b2c605809dcf82fde5afb8",  # noqa: E501
    "malformed:previous_tokens_negative": "e462ef4f30f857ff9b3cdce5b5b89823502cf8699746981660d7ba05518c87c1",  # noqa: E501
    "malformed:previous_cost_zero": "d9680447f0bd9e5b0e924a5e1cbb5a91ecdb62b0be88b2739ab93cf944aab289",  # noqa: E501
    "malformed:previous_retry_below_window": "578973db9f5a3a6df37a303bc63c39f1e1e739b45bdef80196c1d042d0065f35",  # noqa: E501
    "malformed:previous_prev_id_trailing_newline": "6e586b0574d3a689b888db4711615ef23ebea3a60cd32d4c7fa6b5abba2568a4",  # noqa: E501
    "malformed:previous_updated_at_negative": "a2e2bf519928e5622fb8d2fd59ee99ed1f7d75e5a32e954cd1f10fdba99da56e",  # noqa: E501
    "malformed:previous_refill_zero": "54c93c0f62143ed078087970fd3c9316b6664ddbf71d1faefcc1d775d24e3b49",  # noqa: E501
    "malformed:previous_updated_at_past_bound": "0d2527b4983829924e24bf9be9be1d5be667957f1e5ac347f9eec40cea11ec13",  # noqa: E501
    "malformed:previous_capacity_past_bound": "d2a3a59940e204df27cb0f433515007537a7856da383df3ec986fc08922eae54",  # noqa: E501
    "malformed:key_changed": "0298fc76f1e5bc70cf92e18d304bad498d5ca4c77641c797e9f908695fac3976",  # noqa: E501
    "malformed:capacity_changed": "14a14aa35f807f3f3a6961ac128b1c87f142059d011f234875cdccc6b4b0e11c",  # noqa: E501
    "malformed:refill_changed": "b0700e7404e29a36b3a04152a6035d212e205289180fd3369192f4e70b15f2be",  # noqa: E501
    "malformed:clock_regression": "9295087b08f5459c1c9c543e92574730ff67a3654c4767c42d9a280db6b23409",  # noqa: E501
    "malformed:clock_overflow_one_past": "d452b8c9d14ab015264115f5221db9ca80ac483340a57a125154203290aa4c15",  # noqa: E501
    "malformed:clock_overflow_after_partial": "fc16d79f8c25b1ac0cf8ee061bc8fa67353a1cb367511882d35340147c219a39",  # noqa: E501
    "malformed:clock_overflow_deny": "09fd91b36458502b0e98a1706d73713108ee3a7e75df9a2ee9c042d5d41e80a2",  # noqa: E501
    "rollback:conflict_then_forward": "cfa6eaacba05f1f7b66f80ca82bd8a6c1c8ac8ce3cebd8362675cf76faa9c504",  # noqa: E501
    "rollback:corrupt_previous_then_intact_previous": "cca5b9b5034a94f6179b920048650356cb28c2c73f36a3d9a9e303cb3beaa6f1",  # noqa: E501
    "rollback:invalid_policy_then_repaired": "7d242d7c31b1c0249fd503a888e0080aff446cf0f4f186b2dd47945f62a858a2",  # noqa: E501
    "rollback:overflow_then_smaller_cost": "0b638d0c6eeabcf68137f939d97f627d6a0fe3f65d3b6ca4300fb5a90d94357a",  # noqa: E501
    "rollback:malformed_then_repaired": "54127122111cb11f6783ae06ce59a58181744c9b35dff54682dac206a9de8954",  # noqa: E501
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


def _pol(q):
    return (q["policy"]["capacity"], q["policy"]["refill_ms"])


def _h_fresh(r):
    (q,) = r["requests"]
    assert q["previous"] is None and _pol(q) == (3, 1000) and q["cost"] == 1


def _h_drain(r):
    reqs = r["requests"]
    assert len(reqs) == 4 and {q["now"] for q in reqs} == {0}
    assert sum(q["cost"] for q in reqs[:3]) == reqs[0]["policy"]["capacity"]


def _h_partial(r):
    a, b, c = r["requests"]
    assert (a["cost"], a["now"], b["now"], c["now"]) == (3, 0, 1500, 2000)
    assert (b["now"] - a["now"]) % 1000 != 0  # a real partial refill is banked


def _h_cap(r):
    a, b = r["requests"]
    ra, _ = r["expect_records"]
    gained = (b["now"] - ra["updated_at"]) // 1000
    assert ra["tokens"] + gained > a["policy"]["capacity"]  # the refill overshoots
    assert (b["now"] - ra["updated_at"]) % 1000 != 0  # a partial token is dropped


def _b_cost_cap(r):
    (q,) = r["requests"]
    assert q["cost"] == q["policy"]["capacity"] == 3


def _b_capacity_one(r):
    a, b = r["requests"]
    assert _pol(a) == _pol(b) == (1, 1000) and a["now"] == b["now"]


def _b_capacity_max(r):
    (q,) = r["requests"]
    assert _pol(q) == (MAX_CAPACITY, 1) and q["cost"] == MAX_CAPACITY


def _b_refill_max(r):
    a, b = r["requests"]
    assert _pol(a) == _pol(b) == (1, MAX_REFILL) and a["now"] == b["now"] == 0


def _b_clock(r):
    (q,) = r["requests"]
    assert q["now"] == MAX and type(q["now"]) is int


def _b_equal_now(r):
    a, b = r["requests"]
    assert a["now"] == b["now"] == 70


def _b_floor(r):
    a, b = r["requests"]
    assert (a["cost"], b["now"] - a["now"]) == (3, 999)


def _b_retry_partial(r):
    a, b = r["requests"]
    assert (a["cost"], b["cost"], b["now"]) == (3, 3, 1500)


def _b_overflow_edge(r):
    a, b = r["requests"]
    (_, rb) = r["expect_records"]
    assert _pol(a) == (1, 1000) and a["now"] == b["now"] == MAX - 1000
    assert b["now"] + rb["retry_after_ms"] == MAX


def _b_overflow_partial(r):
    a, b = r["requests"]
    (_, rb) = r["expect_records"]
    assert _pol(a) == (2, 1000) and (a["cost"], b["cost"]) == (2, 2)
    assert rb["updated_at"] < b["now"]  # a banked partial refill: base trails now
    assert b["now"] + rb["retry_after_ms"] == MAX


def _b_exact_cap(r):
    a, b = r["requests"]
    ra, rb = r["expect_records"]
    elapsed = b["now"] - ra["updated_at"]
    assert ra["tokens"] + elapsed // 1000 == a["policy"]["capacity"]  # lands exactly
    assert elapsed % 1000 != 0 and rb["updated_at"] == b["now"]


def _b_retry_ceiling(r):
    d = r["expect_records"][3]
    assert d["decision"] == "deny" and r["requests"][4]["previous"] == d
    assert d["retry_after_ms"] == (d["cost"] - d["tokens"]) * d["refill_ms"]
    assert r["expect_records"][4]["decision"] == "admit"


def _b_key_bound(r):
    (q,) = r["requests"]
    assert len(q["key"]) == 64


def _b_new_policy(r):
    a, b = r["requests"]
    assert b["previous"] is None and _pol(a) != _pol(b)
    assert _same_but(a, b, "policy", "now", "previous")


OK_EDGES = {
    "fresh_bucket_admits": _h_fresh,
    "drain_then_deny": _h_drain,
    "partial_refill_is_carried": _h_partial,
    "refill_caps_at_capacity": _h_cap,
    "cost_equals_capacity": _b_cost_cap,
    "capacity_one": _b_capacity_one,
    "capacity_at_bound": _b_capacity_max,
    "refill_at_bound": _b_refill_max,
    "now_at_clock_limit": _b_clock,
    "refill_lands_exactly_on_capacity_with_remainder": _b_exact_cap,
    "deny_retry_at_window_ceiling_chains": _b_retry_ceiling,
    "equal_now_accepted": _b_equal_now,
    "gain_is_floored": _b_floor,
    "retry_counts_elapsed_partial": _b_retry_partial,
    "overflow_edge_accepted": _b_overflow_edge,
    "overflow_edge_after_partial": _b_overflow_partial,
    "key_at_grammar_bound": _b_key_bound,
    "policy_change_starts_fresh": _b_new_policy,
}


# the intact admit record every forged previous starts from (key k-1,
# capacity 3, refill 1000, cost 2, tokens 1 at 50)
_P1_ID = "lm1:88d875473d05078e1cd6ca2acae514827dcd948ff9b9245aa398ce9fb0cdfb90"

_REQUEST_EDGES = {
    # name -> (field, bad value, repaired value)
    "cost_zero": ("cost", 0, 1),
    "cost_bool": ("cost", True, 1),
    "cost_above_capacity": ("cost", 4, 3),
    "now_negative": ("now", -1, 0),
    "now_past_clock": ("now", MAX + 1, MAX),
    "policy_not_a_dict": ("policy", [], None),
    "previous_not_a_dict": ("previous", [], None),
}
_POLICY_EDGES = {
    "capacity_zero": ("capacity", 0, 1),
    "capacity_past_bound": ("capacity", MAX_CAPACITY + 1, MAX_CAPACITY),
    "capacity_bool": ("capacity", True, 1),
    "refill_zero": ("refill_ms", 0, 1),
    "refill_past_bound": ("refill_ms", MAX_REFILL + 1, MAX_REFILL),
}
# name -> (pinned bad previous values, pinned repaired values); the forged
# record carries a RECOMPUTED limit_id, so only the named field is wrong
_PREV_FORGED = {
    "previous_op_wrong": ({"op": "take"}, {"op": "admit"}),
    "previous_decision_unknown": ({"decision": "hold"}, {"decision": "deny"}),
    "previous_cost_past_capacity": (
        {"cost": 4, "retry_after_ms": 2990},
        {"cost": 3, "retry_after_ms": 1990},
    ),
    "previous_admit_with_retry": ({"retry_after_ms": 5}, {"retry_after_ms": None}),
    "previous_admit_tokens_past_cost": ({"tokens": 2}, {"tokens": 1}),
    "previous_deny_retry_past_window": ({"retry_after_ms": 2001}, {"retry_after_ms": 1990}),
    "previous_deny_retry_overflows": ({"updated_at": MAX - 1000}, {"updated_at": MAX - 2000}),
    "previous_refill_past_bound": ({"refill_ms": MAX_REFILL + 1}, {"refill_ms": 1000}),
    "previous_key_bad_grammar": ({"key": "k 1"}, {"key": "k-1"}),
    "previous_tokens_negative": ({"tokens": -1}, {"tokens": 1}),
    "previous_cost_zero": ({"cost": 0}, {"cost": 2}),
    "previous_retry_below_window": (
        {"decision": "deny", "cost": 2, "tokens": 0, "retry_after_ms": 1000},
        {"decision": "deny", "cost": 2, "tokens": 0, "retry_after_ms": 1001},
    ),
    "previous_prev_id_trailing_newline": (
        {"previous_id": _P1_ID + "\n"},
        {"previous_id": _P1_ID},
    ),
    "previous_updated_at_negative": ({"updated_at": -1}, {"updated_at": 0}),
    "previous_refill_zero": ({"refill_ms": 0}, {"refill_ms": 1000}),
    "previous_updated_at_past_bound": ({"updated_at": MAX + 1}, {"updated_at": 50}),
    "previous_capacity_past_bound": ({"capacity": MAX_CAPACITY + 1}, {"capacity": 3}),
}
# name -> (previous updated_at, request now): each deny would retry to 2**53
_OVERFLOW_EDGES = {
    "clock_overflow_one_past": (MAX - 999, MAX - 999),
    "clock_overflow_after_partial": (MAX - 1999, MAX - 499),
}
_CONFLICTS = {
    # name -> (path, bad value, repaired value)
    "key_changed": (("key",), "k-2", "k-1"),
    "capacity_changed": (("policy", "capacity"), 4, 3),
    "refill_changed": (("policy", "refill_ms"), 500, 1000),
    "clock_regression": (("now",), 49, 50),
}


def _intact(prev):
    return prev["limit_id"] == _preimage(prev)


def _at(obj, path):
    for p in path:
        obj = obj[p]
    return obj


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
    if type(fix) is dict and fix.get("previous") is not None:
        assert _intact(fix["previous"]), name
    if name == "request_not_a_dict":
        assert type(bad) is list and bad == [fix]
    elif name == "unknown_op":
        assert (bad["op"], fix["op"]) == ("take", "admit")
        _only(bad, fix, ("op",))
    elif name == "extra_field":
        assert _same_type_eq(bad["note"], 1)
        _only(bad, fix, ("note",))
    elif name == "missing_field":
        assert set(fix) - set(bad) == {"previous"}
        _only(bad, fix, ("previous",))
    elif name == "renamed_field":
        _renamed(bad, fix, "now", "at")
    elif name == "key_bad_grammar":
        assert (bad["key"], fix["key"]) == ("k 1", "k-1")
        _only(bad, fix, ("key",))
    elif name == "key_trailing_newline":
        assert (bad["key"], fix["key"]) == ("k-1\n", "k-1")
        _only(bad, fix, ("key",))
    elif name == "cost_past_bound_with_bad_policy":
        assert _same_type_eq(bad["cost"], MAX_CAPACITY + 1) and _same_type_eq(fix["cost"], 1)
        assert _same_type_eq(bad["policy"], {"capacity": 0, "refill_ms": 1000})
        assert _same_type_eq(fix["policy"], {"capacity": 3, "refill_ms": 1000})
        _only(bad, fix, ("cost",), ("policy", "capacity"))
    elif name == "key_past_grammar_bound":
        assert (bad["key"], fix["key"]) == ("k" * 65, "k" * 64)
        _only(bad, fix, ("key",))
    elif name in _REQUEST_EDGES:
        field, b, f = _REQUEST_EDGES[name]
        assert _same_type_eq(bad[field], b), name
        if f is None and field == "policy":
            assert _same_type_eq(fix[field], {"capacity": 3, "refill_ms": 1000}), name
        else:
            assert _same_type_eq(fix[field], f), name
        _only(bad, fix, (field,))
    elif name == "policy_extra_field":
        assert _same_type_eq(bad["policy"]["burst"], 1)
        _only(bad, fix, ("policy", "burst"))
    elif name == "policy_renamed_field":
        _renamed(bad["policy"], fix["policy"], "refill_ms", "period_ms")
        assert {p[0] for p in _diff_paths(bad, fix)} == {"policy"}
    elif name in _POLICY_EDGES:
        field, b, f = _POLICY_EDGES[name]
        assert _same_type_eq(bad["policy"][field], b), name
        assert _same_type_eq(fix["policy"][field], f), name
        _only(bad, fix, ("policy", field))
    elif name == "previous_tokens_tampered":
        assert (bad["previous"]["tokens"], fix["previous"]["tokens"]) == (2, 1)
        assert bad["previous"]["limit_id"] == fix["previous"]["limit_id"]
        _only(bad, fix, ("previous", "tokens"))
    elif name == "previous_id_tampered":
        a, b = bad["previous"]["limit_id"], fix["previous"]["limit_id"]
        assert len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) == 1
        assert _LIMIT_RE.fullmatch(a)
        _only(bad, fix, ("previous", "limit_id"))
    elif name == "previous_extra_field":
        assert _same_type_eq(bad["previous"]["note"], 1)
        _only(bad, fix, ("previous", "note"))
    elif name == "previous_renamed_field":
        _renamed(bad["previous"], fix["previous"], "updated_at", "at")
        assert {p[0] for p in _diff_paths(bad, fix)} == {"previous"}
    elif name in _PREV_FORGED:
        bad_vals, fix_vals = _PREV_FORGED[name]
        b, f = bad["previous"], fix["previous"]
        assert _intact(b), name
        for field, value in bad_vals.items():
            assert _same_type_eq(b[field], value), name
        for field, value in fix_vals.items():
            assert _same_type_eq(f[field], value), name
        changed = [k for k in bad_vals if not _same_type_eq(bad_vals[k], fix_vals.get(k))]
        paths = [("previous", k) for k in changed] + [("previous", "limit_id")]
        if name == "previous_deny_retry_overflows":
            assert (b["decision"], b["retry_after_ms"]) == ("deny", 1990)
            assert b["updated_at"] + b["retry_after_ms"] > MAX >= f["updated_at"] + 1990
            assert _same_type_eq(bad["now"], MAX - 1000) and _same_type_eq(fix["now"], MAX - 1000)
        if name == "previous_deny_retry_past_window":
            assert (b["decision"], b["cost"], b["tokens"]) == ("deny", 3, 1)
            assert _same_type_eq(bad["now"], 2060) and _same_type_eq(fix["now"], 2060)
        _only(bad, fix, *paths)
    elif name in _CONFLICTS:
        path, b, f = _CONFLICTS[name]
        assert _same_type_eq(_at(bad, path), b) and _same_type_eq(_at(fix, path), f), name
        _only(bad, fix, path)
    elif name in _OVERFLOW_EDGES:
        base_at, now = _OVERFLOW_EDGES[name]
        prev = bad["previous"]
        assert _pol(bad) == (2, 1000) and (prev["updated_at"], bad["now"]) == (base_at, now)
        assert (bad["cost"], fix["cost"]) == (2, 1)
        _only(bad, fix, ("cost",))
    elif name == "clock_overflow_deny":
        prev = bad["previous"]
        assert (prev["tokens"], prev["updated_at"], bad["now"]) == (1, MAX - 1500, MAX - 1500)
        assert (bad["cost"], fix["cost"]) == (3, 1)
        _only(bad, fix, ("cost",))
    else:
        raise AssertionError(f"unknown malformed tag {name}")


def _check_rollback_scenario(row):
    name, bad, q = row["name"], row["rejected_request"], row["request"]
    assert q["previous"] is None or _intact(q["previous"])
    if name == "conflict_then_forward":
        assert bad["now"] == bad["previous"]["updated_at"] - 1 and q["now"] == 60
        _only(bad, q, ("now",))
    elif name == "corrupt_previous_then_intact_previous":
        assert not _intact(bad["previous"])
        _only(bad, q, ("previous", "tokens"))
    elif name == "invalid_policy_then_repaired":
        assert (bad["policy"]["refill_ms"], q["policy"]["refill_ms"]) == (0, 1)
        _only(bad, q, ("policy", "refill_ms"))
    elif name == "overflow_then_smaller_cost":
        assert (bad["cost"], q["cost"]) == (3, 1) and bad["now"] == MAX - 1500
        _only(bad, q, ("cost",))
    elif name == "malformed_then_repaired":
        assert (bad["cost"], q["cost"]) == (4, 3) == (4, q["policy"]["capacity"])
        _only(bad, q, ("cost",))
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
                rec = row["expect_record"]
                got = (
                    row["expect_failure"],
                    rec["decision"],
                    rec["tokens"],
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
        assert _names(section) == list(manifest)


def test_failure_class_and_enum_coverage():
    assert {row["expect_failure"] for row in CASES["malformed"]} == FAILURE_CLASSES
    assert {row["expect_failure"] for row in CASES["rollback"]} == FAILURE_CLASSES
    assert {FAILURE_MAPPING[c] for c in FAILURE_CLASSES} <= set(ERROR_ENUM)
    recs = [rec for s in ("happy", "boundary") for row in CASES[s] for rec in row["expect_records"]]
    assert {rec["previous_id"] is None for rec in recs} == {True, False}
    assert {rec["decision"] for rec in recs} == set(DECISIONS)
    assert {rec["tokens"] == rec["capacity"] - rec["cost"] for rec in recs} == {True, False}


_S = "boundary"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(schema=2),
        lambda c: c.update(schema=True),
        lambda c: c.update(contract="jobs-retry"),
        lambda c: c.update(contract_base_path="/jobs/limit/v2"),
        lambda c: c.update(extra=1),
        lambda c: c.pop("rollback"),
        lambda c: c["boundary"].clear(),
        lambda c: c["happy"][0].update(eta=0),
        lambda c: c["happy"][0]["expect_records"].pop(),
        lambda c: c["happy"][0]["expect_records"][0].pop("updated_at"),
        lambda c: c["happy"][0]["expect_records"][0].update(extra=1),
        lambda c: c["happy"][0]["expect_records"][0].update(limit_id="lm1:zz"),
        lambda c: c["happy"][0]["expect_records"][0].update(op="take"),
        lambda c: c["happy"][0]["expect_records"][0].update(decision="hold"),
        lambda c: c["happy"][0]["expect_records"][0].update(tokens=4),
        lambda c: c["happy"][0]["expect_records"][0].update(retry_after_ms=1),
        lambda c: c["happy"][0]["expect_records"][0].update(key="k-9"),
        lambda c: c["happy"][0]["expect_records"][0].update(cost=2),
        lambda c: c["happy"][0]["expect_records"][0].update(capacity=4),
        lambda c: c["happy"][0]["expect_records"][0].update(previous_id="lm1:" + "0" * 64),
        lambda c: c["happy"][1]["expect_records"][1].update(previous_id=None),
        lambda c: c["happy"][1]["requests"][2]["previous"].update(tokens=2),
        lambda c: c["happy"][0]["requests"][0].update(eta=1),
        lambda c: c["happy"][0]["requests"][0].update(previous=[]),
        lambda c: c["happy"][0]["requests"][0].update(policy=[]),
        lambda c: c["malformed"][0].update(expect_failure="internal"),
        lambda c: c["malformed"][1].update(
            minimal_repair={"request": c["malformed"][1]["request"]}
        ),
        lambda c: c["malformed"][1]["minimal_repair"].update(state={}),
        lambda c: c["malformed"][1].update(minimal_repair={"previous": {}}),
        lambda c: c["rollback"][0].update(expect_failure="internal"),
        lambda c: c["rollback"][0].update(request=c["rollback"][0]["rejected_request"]),
        lambda c: c["rollback"][0]["expect_record"].update(limit_id="lm1:" + "0" * 63),
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
        records.append(_admit(copy.deepcopy(q)))
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


def _bad(name, *path, **kw):
    """Edit the malformed row's REQUEST at path (a second defect or retype)."""

    def mutant(c):
        node = _bn(c, "malformed", name)["request"]
        for p in path:
            node = node[p]
        node.update(kw)

    return mutant


def _fix(name, *path, **kw):
    def mutant(c):
        node = _bn(c, "malformed", name)["minimal_repair"]["request"]
        for p in path:
            node = node[p]
        node.update(kw)

    return mutant


def _pol_step(i, **kw):
    return lambda row: row["requests"][i]["policy"].update(kw)


_CLOSURE_MUTANTS = {
    "swap_boundary_names": lambda c: _swap(
        c, "boundary", "capacity_one", "refill_at_bound", "name"
    ),
    "swap_happy_names": lambda c: _swap(
        c, "happy", "fresh_bucket_admits", "refill_caps_at_capacity", "name"
    ),
    "swap_malformed_names_same_class": lambda c: _swap(
        c, "malformed", "now_negative", "cost_zero", "name"
    ),
    "swap_malformed_requests": lambda c: _swap(
        c, "malformed", "now_negative", "cost_zero", "request", "minimal_repair"
    ),
    "swap_conflict_requests": lambda c: _swap(
        c, "malformed", "capacity_changed", "refill_changed", "request", "minimal_repair"
    ),
    "swap_policy_requests": lambda c: _swap(
        c, "malformed", "capacity_zero", "refill_zero", "request", "minimal_repair"
    ),
    "drop_cost_bool": lambda c: c["malformed"].remove(_bn(c, "malformed", "cost_bool")),
    "duplicate_unknown_op": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "unknown_op"))
    ),
    "clock_edge_one_short": _edit(_S, "now_at_clock_limit", _step(0, now=MAX - 1)),
    "equal_now_made_strict": _edit(_S, "equal_now_accepted", _step(1, now=71)),
    "floor_gain_reached": _edit(_S, "gain_is_floored", _step(1, now=1000)),
    "overflow_edge_moved": _edit(
        _S,
        "overflow_edge_accepted",
        lambda row: [q.update(now=MAX - 2000) for q in row["requests"]],
    ),
    "capacity_bound_one_short": _edit(
        _S,
        "capacity_at_bound",
        lambda row: row["requests"][0].update(
            cost=MAX_CAPACITY - 1, policy={"capacity": MAX_CAPACITY - 1, "refill_ms": 1}
        ),
    ),
    "refill_bound_one_short": _edit(
        _S,
        "refill_at_bound",
        lambda row: [q["policy"].update(refill_ms=MAX_REFILL - 1) for q in row["requests"]],
    ),
    "key_bound_one_short": _edit(_S, "key_at_grammar_bound", _step(0, key="k" * 63)),
    "cost_below_capacity": _edit(_S, "cost_equals_capacity", _step(0, cost=2)),
    "new_policy_same_policy": _edit(_S, "policy_change_starts_fresh", _pol_step(1, capacity=3)),
    "partial_refill_whole": _edit("happy", "partial_refill_is_carried", _step(1, now=1000)),
    "drain_spread_out": _edit("happy", "drain_then_deny", _step(3, now=5)),
    "fresh_cost_two": _edit("happy", "fresh_bucket_admits", _step(0, cost=2)),
    "repair_touches_two_fields": _fix("now_negative", cost=2),
    "clock_repair_off_by_one": _fix("clock_regression", now=51),
    "key_bound_repair_short": _fix("key_past_grammar_bound", key="k" * 63),
    "capacity_bound_repair_short": _fix("capacity_past_bound", "policy", capacity=MAX_CAPACITY - 1),
    "rollback_follow_up_widened": lambda c: _bn(c, "rollback", "conflict_then_forward")[
        "request"
    ].update(now=61),
    "renamed_field_second_defect": _bad("renamed_field", key="k-9"),
    "policy_renamed_second_defect": _bad("policy_renamed_field", "policy", capacity=4),
    "previous_renamed_second_defect": _bad("previous_renamed_field", "previous", key="k-9"),
    "renamed_back_to_extra": _bad("renamed_field", now=0),
    "unknown_op_retyped": _bad("unknown_op", op=None),
    "cost_edge_retyped": _bad("cost_zero", cost=0.0),
    "capacity_edge_retyped": _bad("capacity_zero", "policy", capacity=False),
    "key_changed_bad_grammar": _bad("key_changed", key="!!"),
    "capacity_changed_past_bound": _bad("capacity_changed", "policy", capacity=MAX_CAPACITY + 1),
    "clock_regression_by_two": _bad("clock_regression", now=48),
    "forged_second_defect": _bad("previous_op_wrong", "previous", key="k-9"),
    "forged_id_not_recomputed": _bad(
        "previous_decision_unknown", "previous", limit_id="lm1:" + "0" * 64
    ),
    "overflow_cost_two": _bad("clock_overflow_deny", cost=2),
    "key_newline_retyped": _bad("key_trailing_newline", key="k-1\r"),
    "cost_bound_policy_made_valid": _bad("cost_past_bound_with_bad_policy", "policy", capacity=3),
    "cost_bound_one_short": _bad("cost_past_bound_with_bad_policy", cost=MAX_CAPACITY),
    "retry_below_window_by_two": _bad(
        "previous_retry_below_window", "previous", retry_after_ms=999
    ),
    "retry_below_window_repair_wide": _fix(
        "previous_retry_below_window", "previous", retry_after_ms=2000
    ),
    "updated_negative_by_two": _bad("previous_updated_at_negative", "previous", updated_at=-2),
    "refill_zero_retyped": _bad("previous_refill_zero", "previous", refill_ms=False),
    "prev_newline_second_defect": _bad("previous_prev_id_trailing_newline", "previous", key="k-9"),
    "exact_cap_no_remainder": _edit(
        _S, "refill_lands_exactly_on_capacity_with_remainder", _step(1, now=2000)
    ),
    "ceiling_deny_spread_out": _edit(_S, "deny_retry_at_window_ceiling_chains", _step(3, now=5)),
    "defect_text_edited": lambda c: _bn(c, "malformed", "cost_bool").update(defect="cost is odd"),
}


def _section_of(cases, row):
    for section in SECTIONS:
        if any(r is row for r in cases[section]):
            return section
    raise AssertionError("row not in cases")


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
        _validate_closure(cases)


# -- execution ---------------------------------------------------------------


@pytest.mark.parametrize("section,name", [(s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_records(section, name):
    row = _row(section, name)
    requests = copy.deepcopy(row["requests"])
    assert [_admit(q) for q in requests] == row["expect_records"]
    assert requests == row["requests"]


def test_pinned_ids_match_the_contract_preimage():
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            for rec in row["expect_records"]:
                assert rec["limit_id"] == _preimage(rec)
    for row in CASES["rollback"]:
        assert row["expect_record"]["limit_id"] == _preimage(row["expect_record"])


def test_tampered_pinned_record_is_detected():
    row = copy.deepcopy(_row("happy", "drain_then_deny"))
    row["expect_records"][-1]["retry_after_ms"] += 1
    assert [_admit(copy.deepcopy(q)) for q in row["requests"]] != row["expect_records"]


def _rejects(failure_class, request):
    before = _snap(request)
    with pytest.raises(_REF.LimitError) as err:
        _admit(request)
    exc = err.value
    assert type(exc) is _REF.LimitError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class] and exc.code in ERROR_ENUM
    assert exc.retryable is False
    assert exc.__cause__ is None and exc.__context__ is None
    assert _snap(request) == before  # type-exact: True != 1


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    row = _row("malformed", name)
    request = copy.deepcopy(row["request"])
    _rejects(row["expect_failure"], request)
    assert _snap(request) == _snap(row["request"])


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_succeeds(name):
    row = _row("malformed", name)
    request = copy.deepcopy(row["minimal_repair"]["request"])
    record = _admit(request)
    _validate_record(record, name)
    assert _snap(request) == _snap(row["minimal_repair"]["request"])


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_row("malformed", "cost_above_capacity"))
    row["request"]["cost"] = 3
    assert _fails(row["request"]) is None


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    rejected = copy.deepcopy(row["rejected_request"])
    _rejects(row["expect_failure"], rejected)
    assert _snap(rejected) == _snap(row["rejected_request"])
    request = copy.deepcopy(row["request"])
    assert _admit(request) == row["expect_record"]
    assert _snap(request) == _snap(row["request"])


def test_validation_precedence():
    """request shape -> policy -> cost within capacity -> previous record
    integrity -> chain -> clock overflow, first failure wins (derived from
    fixture rows so the order runs on pinned data)."""
    bad_policy = _row("malformed", "refill_zero")["request"]
    tampered = _row("malformed", "previous_tokens_tampered")["request"]
    conflict = _row("malformed", "key_changed")["request"]
    overflow = _row("malformed", "clock_overflow_deny")["request"]
    _rejects("malformed_limit_request", {**bad_policy, "now": -1})
    _rejects("invalid_limit_policy", {**bad_policy, "cost": 4})
    _rejects("malformed_limit_request", {**tampered, "cost": 4})
    _rejects("invalid_limit_policy", {**tampered, "policy": {"capacity": 3, "refill_ms": 0}})
    _rejects("corrupt_previous_bucket", {**tampered, "key": "k-2"})
    _rejects("corrupt_previous_bucket", {**tampered, "now": 49})
    _rejects("limit_conflict", {**conflict, "now": 49})
    _rejects("limit_conflict", {**overflow, "key": "k-2"})
    _rejects("limit_conflict", {**overflow, "now": overflow["now"] - 1})


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


_MR_ = "malformed_limit_request"
_IP_ = "invalid_limit_policy"
_CB_ = "corrupt_previous_bucket"
_STR_FIELDS = ("op", "key")
_INT_FIELDS = ("cost", "now")
_POLICY_INT_FIELDS = ("capacity", "refill_ms")
_PREV_STR_FIELDS = ("op", "key", "decision", "limit_id")
_PREV_INT_FIELDS = ("capacity", "refill_ms", "cost", "tokens", "updated_at")


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
        out.append(
            (f"{prefix}float-subclass-{field}", wrap({**mapping, field: _FloatSub(value)}), failure)
        )
    return out


def _containers(prefix, mapping, wrap, failure):
    return [
        (f"{prefix}dict-subclass", wrap(_DictSub(mapping)), failure),
        (f"{prefix}lying-dict", wrap(_LyingDict(mapping)), failure),
        (f"{prefix}list-subclass", wrap(_ListSub(mapping.items())), failure),
    ]


def _keys(prefix, mapping, wrap, failure):
    return [
        (f"{prefix}key-{label}-{field}", wrap(_rekey(mapping, field, make)), failure)
        for field in mapping
        for label, make in KEY_FORMS.items()
    ]


def _variants(q):
    """(label, hostile request, expected failure class) for one accepted
    request: every container, key and value boundary on the request, the
    policy and (when chained) the previous record, including the
    previous record's optional previous_id and retry_after_ms."""
    policy, prev = q["policy"], q["previous"]

    def in_policy(m):
        return {**q, "policy": m}

    def in_prev(m):
        return {**q, "previous": m}

    out = _containers("", q, lambda m: m, _MR_)
    out += _keys("", q, lambda m: m, _MR_)
    out += _value_variants("", q, _STR_FIELDS, _INT_FIELDS, lambda m: m, _MR_)
    out += _containers("policy-", policy, in_policy, _MR_)
    out += _keys("policy-", policy, in_policy, _IP_)
    out += _value_variants("policy-", policy, (), _POLICY_INT_FIELDS, in_policy, _IP_)
    if prev is not None:
        strs = _PREV_STR_FIELDS + (("previous_id",) if prev["previous_id"] is not None else ())
        ints = _PREV_INT_FIELDS + (
            ("retry_after_ms",) if prev["retry_after_ms"] is not None else ()
        )
        out += _containers("previous-", prev, in_prev, _MR_)
        out += _keys("previous-", prev, in_prev, _CB_)
        out += _value_variants("previous-", prev, strs, ints, in_prev, _CB_)
    return out


def _expected_count(q):
    n = 3 + 3 * len(FIELDS) + 4 * len(_STR_FIELDS) + 4 * len(_INT_FIELDS)
    n += 3 + 3 * len(POLICY_FIELDS) + 4 * len(_POLICY_INT_FIELDS)
    prev = q["previous"]
    if prev is not None:
        n += 3 + 3 * len(RECORD_FIELDS) + 4 * len(_PREV_STR_FIELDS) + 4 * len(_PREV_INT_FIELDS)
        n += 4 * (prev["previous_id"] is not None) + 4 * (prev["retry_after_ms"] is not None)
    return n


def _hostile(failure_class, request):
    """Typed class, fresh error, no user code ran, input unchanged."""
    snap = _snap(request)
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            _admit(request)
            got = "accepted"
        except _REF.LimitError as exc:
            got = (
                type(exc) is _REF.LimitError,
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
    for label, q in _ACCEPTED:
        labels = [v for v, _, _ in _variants(q)]
        assert len(labels) == len(set(labels)) == _expected_count(q), label
    assert {q["previous"] is None for _, q in _ACCEPTED} == {True, False}


# -- the T0329 reference mutants must each turn this fixture red -------------


def _fixture_red():
    """Labels of every fixture check that disagrees with the reference
    currently bound (empty when green)."""
    red = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            try:
                got = [_admit(copy.deepcopy(q)) for q in row["requests"]]
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


# one unchained and one chained request probe the hostile rows under every
# mutant (the full set runs in the parametrized test)
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


# Extra one-edit mutants of the T0338 reference that this fixture kills on
# its own (the op, grammar, type, floor and previous-record bound guards
# the rows exist for). Names are disjoint from REFERENCE_EDITS.
FIXTURE_EDITS = {
    "op-check-off": ('        and request["op"] == "admit"\n', ""),
    "key-grammar-off": (
        'and _grammar(request["key"], _KEY_RE)',
        'and type(request["key"]) is str',
    ),
    "cost-floor-zero": (
        '_int_in(request["cost"], 1, _REF_MAX_CAPACITY)',
        '_int_in(request["cost"], 0, _REF_MAX_CAPACITY)',
    ),
    "now-floor-minus": (
        '_int_in(request["now"], 0, _REF_MAX_NOW)',
        '_int_in(request["now"], -1, _REF_MAX_NOW)',
    ),
    "policy-type-any": (
        'and type(request["policy"]) is dict',
        'and isinstance(request["policy"], dict)',
    ),
    "previous-type-any": (
        'type(request["previous"]) is dict)',
        'isinstance(request["previous"], dict))',
    ),
    "capacity-floor-zero": (
        '_int_in(policy["capacity"], 1, _REF_MAX_CAPACITY)',
        '_int_in(policy["capacity"], 0, _REF_MAX_CAPACITY)',
    ),
    "refill-floor-zero": (
        '_int_in(policy["refill_ms"], 1, _REF_MAX_REFILL)',
        '_int_in(policy["refill_ms"], 0, _REF_MAX_REFILL)',
    ),
    "prev-op-check-off": ('        and prev["op"] == "admit"\n', ""),
    "prev-updated-bound-plus": (
        '_int_in(prev["updated_at"], 0, _REF_MAX_NOW)',
        '_int_in(prev["updated_at"], 0, _REF_MAX_NOW + 1)',
    ),
    "prev-retry-overflow-off": (
        '        and prev["updated_at"] + prev["retry_after_ms"] <= _REF_MAX_NOW\n',
        "",
    ),
    "prev-deny-retry-upper-strict": (
        'and prev["retry_after_ms"] <= (prev["cost"] - prev["tokens"]) * prev["refill_ms"]',
        'and prev["retry_after_ms"] < (prev["cost"] - prev["tokens"]) * prev["refill_ms"]',
    ),
    "prev-refill-lower-zero": (
        'and _int_in(prev["refill_ms"], 1, _REF_MAX_REFILL)',
        'and _int_in(prev["refill_ms"], 0, _REF_MAX_REFILL)',
    ),
    "key-fullmatch-off": ("regex.fullmatch(value) is not None", "regex.match(value) is not None"),
    "refill-cap-strict": (
        'if prev["tokens"] + gained >= capacity:',
        'if prev["tokens"] + gained > capacity:',
    ),
    "prev-deny-retry-lower-off": (
        '        and (prev["cost"] - prev["tokens"] - 1) * prev["refill_ms"]'
        ' < prev["retry_after_ms"]\n',
        "",
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
