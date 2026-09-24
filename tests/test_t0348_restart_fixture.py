"""T0348: jobs worker-restart conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the T0347
jobs-restart contract (data/contracts/restart.yaml). The cases execute
against the contract-derived reference restart in
tests.test_t0347_restart_contract - nothing is re-implemented here.
Each happy/boundary row applies its requests in turn to one evolving
queue state; every receipt and every committed state was computed from
that reference at authoring time, so any contract or derivation drift
breaks this battery. Every malformed case is discriminating: applying
ONLY its declared minimal repair makes it commit. Repairs are
single-locus (of the state or of the request), except the precedence
row, which has a bad request AND a corrupt state and so must repair
both. Rollback cases prove a rejected restart leaves the
exact supplied state and request type-exactly unchanged before a valid
follow-up commits as pinned.

Hostile-type rows are derived here from EVERY input the fixture accepts:
hostile containers on the request, the state, the jobs list, a job and
a payload; str-subclass keys in three forms on every request, state and
job field; and str-subclass, raising eq/repr, nested, int-subclass,
bool and float values on each of them, plus payload shapes JSON cannot
store. Each must fail typed with a fresh error, run no user code and
leave the state and request unchanged.

Every one-edit reference mutant pinned by the T0347 battery must turn
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

import tests.test_t0347_restart_contract as _REF  # noqa: E402
from tools.restart_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "restart" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECORD_FIELDS = list(_CC["record"]["fields"])
FIELDS = _CC["request"]["operations"][0]["fields"]
STATE_FIELDS = list(_QUEUE["state"]["fields"])
JOB_FIELDS = list(_QUEUE["state"]["job_fields"])
_WORKER_RE = re.compile(_CC["identifiers"]["worker"]["grammar"], re.ASCII)
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"], re.ASCII)
_RESTART_RE = re.compile(_CC["identifiers"]["restart_id"]["grammar"], re.ASCII)
MAX = 9007199254740991  # 2**53 - 1, written out
MAX_ATTEMPTS = 5

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "state", "requests", "expect_records", "expect_states"}
BAD_KEYS = {"name", "defect", "expect_failure", "state", "request", "minimal_repair"}
RB_KEYS = {
    "name",
    "why",
    "rejected",
    "expect_failure",
    "state",
    "request",
    "expect_record",
    "expect_state",
}


def _restart(state, request):
    return _REF.restart(state, request)  # late-bound: reference mutants rebind it


def _containers_of(*roots):
    """Every container reachable from ROOTS, each once, in visit order
    (an alias- and cycle-aware walk: a container met again is skipped)."""
    seen, out, stack = set(), [], list(reversed(roots))
    while stack:
        node = stack.pop()
        if isinstance(node, (dict, list)) and id(node) not in seen:
            seen.add(id(node))
            out.append(node)
            stack.extend(dict.values(node) if isinstance(node, dict) else list.__iter__(node))
    return out


def _assert_detached(state, before):
    """Copy-on-write (restart.yaml): no container of the committed STATE
    is one of BEFORE (every container of the pre-call state and request,
    held alive here so no id can be reused). Only the root dict is the
    caller's, since the commit is in place."""
    held = {id(node) for node in before}
    root, *inner = _containers_of(state)
    assert root is state
    shared = [node for node in inner if id(node) in held]
    assert shared == [], "the committed state shares a container with the call's inputs"


def _run(state, requests):
    """Apply each request in turn to a private copy of STATE; each
    committed step must be detached from that call's inputs."""
    cur = copy.deepcopy(state)
    records, states = [], []
    for q in requests:
        request = copy.deepcopy(q)
        before = _containers_of(cur, request)
        records.append(_restart(cur, request))
        _assert_detached(cur, before)
        states.append(copy.deepcopy(cur))
    return records, states


def _fails(state, request):
    try:
        _restart(copy.deepcopy(state), copy.deepcopy(request))
    except _REF.RestartError as exc:
        return exc.failure_class
    return None


def _state_id(state):
    return _REF._independent_state_id(state)


def _preimage(record):
    return _REF._independent_restart_id({k: record[k] for k in RECORD_FIELDS[:-1]})


def _validate_record(record, label):
    assert type(record) is dict, label
    assert all(type(k) is str for k in record) and set(record) == set(RECORD_FIELDS), label
    assert record["op"] == "restart" and _WORKER_RE.fullmatch(record["worker"]), label
    assert type(record["released"]) is list, label
    assert _STATE_RE.fullmatch(record["prior_state_id"]), label
    assert _STATE_RE.fullmatch(record["state_id"]), label
    assert _RESTART_RE.fullmatch(record["restart_id"]), label
    assert record["restart_id"] == _preimage(record), label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert all(type(k) is str for k in request) and set(request) == set(FIELDS), label


def _validate_state_shape(state, label):
    assert type(state) is dict and set(state) == set(STATE_FIELDS), label
    assert type(state["jobs"]) is list, label
    for job in state["jobs"]:
        assert type(job) is dict and set(job) == set(JOB_FIELDS), label


def _check_step(before, request, record, after, label):
    """One committed step: ids bind the two states, and the ONLY change is
    each released job going leased -> ready with its lease cleared."""
    assert record["worker"] == request["worker"], label
    assert record["restarted_at"] == request["now"], label
    assert record["prior_state_id"] == _state_id(before), label
    assert record["state_id"] == _state_id(after), label
    assert after["next_seq"] == before["next_seq"], label
    assert len(after["jobs"]) == len(before["jobs"]), label
    released = []
    for old, new in zip(before["jobs"], after["jobs"], strict=True):
        if old != new:
            assert old["status"] == "leased" and old["lease_owner"] == request["worker"], label
            assert request["now"] < old["lease_expires_at"], label
            assert new == {**old, "status": "ready", "lease_owner": None, "lease_expires_at": None}
            released.append(old["job_id"])
    assert record["released"] == released, label


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
                reqs, recs, states = row["requests"], row["expect_records"], row["expect_states"]
                assert type(reqs) is list and reqs, label
                assert type(recs) is list and len(recs) == len(reqs), label
                assert type(states) is list and len(states) == len(reqs), label
                _validate_state_shape(row["state"], label)
                before = row["state"]
                for request, record, after in zip(reqs, recs, states, strict=True):
                    _validate_request_shape(request, label)
                    _validate_record(record, label)
                    _validate_state_shape(after, label)
                    _check_step(before, request, record, after, label)
                    before = after
            elif section == "malformed":
                assert set(row) == BAD_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                assert type(row["defect"]) is str and row["defect"], label
                repair = row["minimal_repair"]
                assert type(repair) is dict and set(repair) == {"state", "request"}, label
                assert _diff_paths(
                    [repair["state"], repair["request"]], [row["state"], row["request"]]
                ), label
                _validate_request_shape(repair["request"], label)
                _validate_state_shape(repair["state"], label)
            else:
                assert set(row) == RB_KEYS, label
                assert type(row["why"]) is str and row["why"], label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                rejected = row["rejected"]
                assert type(rejected) is dict and set(rejected) == {"state", "request"}, label
                _validate_request_shape(row["request"], label)
                _validate_state_shape(row["state"], label)
                _validate_record(row["expect_record"], label)
                _check_step(
                    row["state"], row["request"], row["expect_record"], row["expect_state"], label
                )
                assert [rejected["state"], rejected["request"]] != [row["state"], row["request"]]
    assert len(names) == len(set(names)), "fixture names must be unique"


def _summary(row):
    """Per step: the seqs of the released jobs, in receipt order."""
    seq = {job["job_id"]: job["seq"] for job in row["state"]["jobs"]}
    return [[seq[j] for j in r["released"]] for r in row["expect_records"]]


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> per step, the seqs of the released jobs;
# malformed: name -> (failure class, pinned defect text); rollback: name ->
# (failure class, released seqs of the follow-up)
HAPPY_MANIFEST = {
    "releases_own_unexpired_leases": [[1]],
    "mixed_state": [[0, 4]],
    "restart_is_idempotent": [[1], []],
    "no_lease_empty_receipt": [[]],
}
BOUNDARY_MANIFEST = {
    "expiry_one_after_now": [[0]],
    "expiry_equal_now_untouched": [[]],
    "now_zero": [[0]],
    "now_at_clock_limit": [[]],
    "expiry_at_clock_limit": [[0]],
    "empty_queue": [[]],
    "worker_at_grammar_bound": [[0]],
    "attempts_at_max_released": [[0]],
    "next_seq_gap_kept": [[1]],
    "payload_negative_4000_digits": [[0]],
    "payload_depth_at_bound": [[0]],
    "payload_dict_depth_at_bound": [[0]],
    "payload_json_literals": [[0]],
    "payload_4000_digits": [[0]],
    "next_seq_at_clock_limit": [[0]],
    "priority_bounds": [[0, 1]],
    "non_ascii_payload": [[0]],
}
_MR, _CQ = "malformed_restart_request", "corrupt_queue"
MALFORMED_MANIFEST = {
    "request_not_a_dict": (_MR, "the request is a list"),
    "unknown_op": (_MR, "op is 'reboot', not a registered operation"),
    "extra_field": (_MR, "the request carries an extra note field"),
    "missing_field": (_MR, "the request has no now field"),
    "renamed_field": (_MR, "the now field is renamed to at, keeping the field count"),
    "worker_bad_grammar": (_MR, "the worker holds a space"),
    "worker_past_grammar_bound": (_MR, "the worker has 65 characters"),
    "now_negative": (_MR, "now is -1"),
    "now_past_clock": (_MR, "now is 2**53"),
    "now_bool": (_MR, "now is the bool True"),
    "worker_trailing_newline": (_MR, "the worker ends in a newline"),
    "bad_request_and_bad_state": (
        _MR,
        "now is -1 and the state is corrupt (priority 10): the request check"
        " runs first, so this is malformed_restart_request (two loci: the"
        " repair sets now 0 and priority 9)",
    ),
    "state_not_a_dict": (_CQ, "the state is a list"),
    "state_extra_field": (_CQ, "the state carries an extra note field"),
    "state_renamed_field": (
        _CQ,
        "the state's next_seq is renamed to seq, keeping the field count",
    ),
    "jobs_not_a_list": (_CQ, "jobs is a dict"),
    "next_seq_negative": (_CQ, "next_seq is -1 on an empty queue"),
    "next_seq_not_past_last": (_CQ, "next_seq 1 equals the last seq 1"),
    "seq_not_increasing": (_CQ, "the second job's seq repeats 0"),
    "duplicate_job_id": (_CQ, "two jobs share one job_id"),
    "job_extra_field": (_CQ, "a job carries an extra note field"),
    "job_id_bad_grammar": (_CQ, "a job_id ends in a newline"),
    "next_seq_past_clock": (_CQ, "next_seq is 2**53"),
    "priority_negative": (_CQ, "a job's priority is -1"),
    "attempts_negative": (_CQ, "a ready job carries attempts -1"),
    "owner_trailing_newline": (_CQ, "a leased job's lease_owner ends in a newline"),
    "done_with_lease_expiry": (_CQ, "a done job holds lease_expires_at 2000 with no owner"),
    "payload_depth_past_bound": (_CQ, "a payload nests 65 containers deep"),
    "payload_dict_depth_past_bound": (_CQ, "a payload nests 65 dicts deep through their values"),
    "payload_4001_digits": (_CQ, "a payload int has 4001 digits"),
    "priority_ten": (_CQ, "a job's priority is 10"),
    "status_unknown": (_CQ, "a job's status is 'paused'"),
    "attempts_past_max": (_CQ, "a leased job carries attempts 6"),
    "leased_attempts_zero": (_CQ, "a leased job carries attempts 0"),
    "leased_expiry_zero": (_CQ, "a leased job's lease_expires_at is 0"),
    "leased_expiry_past_bound": (_CQ, "a leased job's lease_expires_at is 2**53"),
    "leased_owner_bad_grammar": (_CQ, "a leased job's lease_owner is '!!'"),
    "ready_with_lease_owner": (_CQ, "a ready job holds lease_owner w1"),
    "dead_below_max": (_CQ, "a dead job carries attempts 4"),
    "done_attempts_zero": (_CQ, "a done job carries attempts 0"),
    "payload_lone_surrogate": (_CQ, "a payload string holds a lone surrogate"),
    "payload_key_lone_surrogate": (_CQ, "a payload key is a lone surrogate"),
}
ROLLBACK_MANIFEST = {
    "malformed_then_repaired": (_MR, [0]),
    "corrupt_then_repaired": (_CQ, [0]),
}

# content binding: canonical sha256 of every row. Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:releases_own_unexpired_leases": "9c97e3038fb0bb93bd528566740b22881fcadd9a96c24932f152b4884fc810f7",  # noqa: E501
    "happy:mixed_state": "080fbcdc5587c9cbc63d6c867560a03777cf247259361fffaae6de167a440687",  # noqa: E501
    "happy:restart_is_idempotent": "aaaaf7783d8070cc0b25078b8c158631045a67eb2e20b576e5d70287fc5c5bbb",  # noqa: E501
    "happy:no_lease_empty_receipt": "86826c9b46541982c56435464b56d6dbf155e77ca4d97498f22b3eebbbe03730",  # noqa: E501
    "boundary:expiry_one_after_now": "cbac7767036c3b249feebb68995ffaa18feea41e4479a81520cdb44956795a58",  # noqa: E501
    "boundary:expiry_equal_now_untouched": "9312112e63baeec27acf537c9df6e11f0bafe59f54cf3321d7685b036ccbf783",  # noqa: E501
    "boundary:now_zero": "900fb5324170b9c297c56695e2033a235f52b33e97b48ec54653d9390ed4069b",  # noqa: E501
    "boundary:now_at_clock_limit": "be7c91122696a47665773ff6d932b3c435578ca75dadc744769bc436e9b749e1",  # noqa: E501
    "boundary:expiry_at_clock_limit": "762b88620e3fb0de80e8da03f9b42f58e1325d0be4ec4fb198f4b2f86210ba9e",  # noqa: E501
    "boundary:empty_queue": "2113764dd73c2d343e3a95078ad412ccb5b1d969998954a68c9478f337829190",  # noqa: E501
    "boundary:worker_at_grammar_bound": "ddaa492d33f76d357b92b8fbf673e5ceeac4c89704278e23811f0072b9f8b061",  # noqa: E501
    "boundary:attempts_at_max_released": "25d225657741303a529f4f352a9b3f5984d9d5b9ac9c846c65d65c1502293925",  # noqa: E501
    "boundary:next_seq_gap_kept": "e8a16cea141033fa4a8806ee748d3dacb62f67eab4f422a98eec41b508446c77",  # noqa: E501
    "boundary:payload_negative_4000_digits": "1f00c4772b26a0f74eb663457c6a346598cb18bac6a3cc08e47c1a6e1865aaac",  # noqa: E501
    "boundary:payload_depth_at_bound": "09fe02ea7ee2905d6029dda6a4482acf6d6599a107b8e5b84814c767fd23e7ee",  # noqa: E501
    "boundary:payload_dict_depth_at_bound": "313854a638adea0809845d2314f6e6b435847b09fb0c520fecb528be34dbeccb",  # noqa: E501
    "boundary:payload_json_literals": "8406500b712479a483832b3f45c26ebb11615095a8544e02cce90265778789c2",  # noqa: E501
    "boundary:payload_4000_digits": "0e96d7c57e7f27e8891ccf8a64002187431ca1d57c8a38da8b93c1626dba0a2f",  # noqa: E501
    "boundary:next_seq_at_clock_limit": "701fe2fa3764cfb04b9b955b931e01be4c120b00d34aecf506fb92804f348868",  # noqa: E501
    "boundary:priority_bounds": "efb13292d5a7a316daf7fb8da8fa440bf2be2b0010685ab2ffe867dbab2bbdb1",  # noqa: E501
    "boundary:non_ascii_payload": "12b62b31c724883c4c9013d5b44afc00dca24e2825329a84119650d752facff5",  # noqa: E501
    "malformed:request_not_a_dict": "043449247b5d6429c8de4ab1e7a3dbfcdc03b0caa9eeafaec658d244bc759202",  # noqa: E501
    "malformed:unknown_op": "d9adf3959aab4b8fab45479ec698b4ef2833ac37b77f0bd0c169df5d72f81fbd",  # noqa: E501
    "malformed:extra_field": "9558c8dbee1fbf6dd79213ae85c589fc03956c8f8d7fdb6350ab2dd83f553779",  # noqa: E501
    "malformed:missing_field": "48a6da0120da2e1d7a8d69403700750c003a33db6d5e3211107ff61c7415ddc9",  # noqa: E501
    "malformed:renamed_field": "ea0cb52eb3d956ac446600115388d335e621ff10fcaf85213256ae9d33144896",  # noqa: E501
    "malformed:worker_bad_grammar": "b4de07b3b33f4845f5a5490ae6ae10353c6fb620190300db766f32d0570d1664",  # noqa: E501
    "malformed:worker_past_grammar_bound": "03f31f308422944440016d47a857d171806112b9d06474816b8e5ee989927b0f",  # noqa: E501
    "malformed:now_negative": "fb7d64bbba5ff00b3eae36daff595c31c80dbcebd1c9d20f5962cb588c9bd44b",  # noqa: E501
    "malformed:now_past_clock": "e574e7ef1226b8c734960de911da2cfe992ea2b0702cdfa9ce3d5a54fdff7a8d",  # noqa: E501
    "malformed:now_bool": "208fff8737289bbd215db4dd8422b46b588cc0d6a542e3a986174dbe3b68e1d2",  # noqa: E501
    "malformed:worker_trailing_newline": "9d379b99cf49e76b2b8c13bd410405b616dabec84fc7a19dcbc481557155da6c",  # noqa: E501
    "malformed:bad_request_and_bad_state": "df4f5d6008a426c173e260c7269daba92a13e9a089b859148cbe4baa8cf9a371",  # noqa: E501
    "malformed:state_not_a_dict": "ac099b922affaac0344fa6e18dddb819f9bd1afe5c59b33c3f7af91016a0c183",  # noqa: E501
    "malformed:state_extra_field": "bf7bec39a878ff90ecfb62971753917019951650a1820abc3bf332a5da3a6ca4",  # noqa: E501
    "malformed:state_renamed_field": "ff01b2cbe24eea01cb34c9abb73ac859601f0d6936eca33fec0b35a43b44cb07",  # noqa: E501
    "malformed:jobs_not_a_list": "45fc9a95f5498e43b388496447a58835d018173463e8005d6b4ad91dda928696",  # noqa: E501
    "malformed:next_seq_negative": "0f29b80cdd2130c6e654ed380e8f0f18b03c7834da6e20eeb56bd4b7c9936192",  # noqa: E501
    "malformed:next_seq_not_past_last": "fcf0b7ce38e3a1160f29a5ae71a17ebccfbeb8c0c1eec6d108ff0dedfd079764",  # noqa: E501
    "malformed:seq_not_increasing": "03554b0f634a8dca019760abf5b3751e3212344abf5a534fadeef310fe27f74e",  # noqa: E501
    "malformed:duplicate_job_id": "14e76d61f876bf4654b1d53ab1227063993e737aea105947613350d75799246b",  # noqa: E501
    "malformed:job_extra_field": "eeda45fb122c0a71dbae4f567c67b8670b7871ca66ca3e812e1f2e28d1b30cc1",  # noqa: E501
    "malformed:job_id_bad_grammar": "7887932974c0772797bd38725fd130ba11087a9beb8661dd94791dd33b08c1d7",  # noqa: E501
    "malformed:next_seq_past_clock": "e36b0d2e79718c4f43d8d04f7f1d7f7281a5f0ec940b1ed4b725acede4ad3ee9",  # noqa: E501
    "malformed:priority_negative": "d5dbe8466ee038316576abe58ff41e7751953e3af290738274e292f2625c3c59",  # noqa: E501
    "malformed:attempts_negative": "499bf16597b8aa802d67cad1a621168f53de9241387f59d692ac488a25db0684",  # noqa: E501
    "malformed:owner_trailing_newline": "5831a75acd2deda273470ed2bf68cbe01fdaf3a23393f2446d7665bed0d63c34",  # noqa: E501
    "malformed:done_with_lease_expiry": "6ae6e355e281c198c6300e07998cb14c001c5315d1a01e531bd4bcd3465b9f79",  # noqa: E501
    "malformed:payload_depth_past_bound": "7532087121d5a543a88d34cb18dbedd9f61d16424a9fb7513259793a30614f46",  # noqa: E501
    "malformed:payload_dict_depth_past_bound": "46b83eeaf5e4f33acc92da16904e9d23b448abdcf8227e2a3503fcd0dbcdb4ca",  # noqa: E501
    "malformed:payload_4001_digits": "38ca3880856bce687e25f4b37bd7e8bdb7a392e89893caad2ce2d47c8f42f9a2",  # noqa: E501
    "malformed:priority_ten": "fbe0540f946625adad99b9f69774ea035ce0e004d43ee1631bc279a63b76de32",  # noqa: E501
    "malformed:status_unknown": "4b8520cc0ea69770771f8076ce82b29de931e0ba5d10297d9173de42540d4425",  # noqa: E501
    "malformed:attempts_past_max": "a2cbb2dda6ef2475462206a39010ad7ea715378f20f71cc25a351be63cd9f306",  # noqa: E501
    "malformed:leased_attempts_zero": "9055579282874afbf0504e58e58aa491abd5029cc6a1f883bc0f8d3902645589",  # noqa: E501
    "malformed:leased_expiry_zero": "bc1c7eea5dc83ce0b3442ba105d896dd274d53898a53c41e33095e4ae4b19021",  # noqa: E501
    "malformed:leased_expiry_past_bound": "8ab47a15c5925f40b29be376ad5c0983af0c9dc1ec7b8976c1ee33a19d07d3b9",  # noqa: E501
    "malformed:leased_owner_bad_grammar": "f19e34a4e895d0f6deda6bf7cabba5384c43b3a03c91a93ce1d0305ab9ef8a79",  # noqa: E501
    "malformed:ready_with_lease_owner": "1ca2c94bcaa64327006aae64964f5ff69a2267130205f9434bb6f5a07fe76d27",  # noqa: E501
    "malformed:dead_below_max": "50b47776944b12b63e90eba3a6e670b5addd359ec445bdf7191cae3d9e94489f",  # noqa: E501
    "malformed:done_attempts_zero": "3aa984815b2ec193db19ce9cb0be86d55931361158953296e2c4602875e952c7",  # noqa: E501
    "malformed:payload_lone_surrogate": "751e3f05deface7aab12c6a4710e49b6f038fb737c921873b8e153ffc5fa3ac4",  # noqa: E501
    "malformed:payload_key_lone_surrogate": "6ef5b61aa118cb5296f52e8e20ff7a526fb83338bf4674abb2a999962f95a7a0",  # noqa: E501
    "rollback:malformed_then_repaired": "4e5c005d23f3e6ccae1fca0b661a9cc95ce90434dc82bbb990fdf6050bf81ed5",  # noqa: E501
    "rollback:corrupt_then_repaired": "1e2488dfe21fc52c60c5c36f36610a65968632d57567636829561befc40cd817",  # noqa: E501
}

MANIFESTS = {
    "happy": HAPPY_MANIFEST,
    "boundary": BOUNDARY_MANIFEST,
    "malformed": MALFORMED_MANIFEST,
    "rollback": ROLLBACK_MANIFEST,
}

# -- per-row semantic edges over the ORIGINAL data ---------------------------


def _jobs(r):
    return r["state"]["jobs"]


def _only_request(r):
    (q,) = r["requests"]
    return q


def _single_lease(r, owner="w1"):
    (j,) = _jobs(r)
    assert j["status"] == "leased" and j["lease_owner"] == owner
    return j


def _chain(node):
    """(container types along a single-child chain, the leaf): a list
    holds one item, a dict one "k" key; dict values are followed too."""
    kinds = []
    while type(node) is list or type(node) is dict:
        kinds.append(type(node))
        if type(node) is dict:
            assert list(node) == ["k"]
            node = node["k"]
        else:
            (node,) = node
    return kinds, node


def _depth(node):
    kinds, _leaf = _chain(node)
    return len(kinds)


def _h_own(r):
    q = _only_request(r)
    statuses = [(j["status"], j["lease_owner"]) for j in _jobs(r)]
    assert statuses == [("ready", None), ("leased", "w1"), ("leased", "w2"), ("done", None)]
    assert q["worker"] == "w1" and q["now"] < _jobs(r)[1]["lease_expires_at"]


def _h_mixed(r):
    q = _only_request(r)
    a, b, c, d, e, f = _jobs(r)
    assert q["now"] == 1000 == b["lease_expires_at"]  # b expired exactly at now
    assert a["lease_expires_at"] > q["now"] and e["lease_expires_at"] == q["now"] + 1
    assert e["attempts"] == MAX_ATTEMPTS and c["lease_owner"] == "w2"
    assert (d["status"], f["status"]) == ("ready", "dead")


def _h_idem(r):
    a, b = r["requests"]
    assert a == b and _summary(r)[0]  # the first releases something


def _h_nolease(r):
    q = _only_request(r)
    assert q["worker"] not in {j["lease_owner"] for j in _jobs(r)}
    assert any(j["status"] == "leased" for j in _jobs(r))


def _b_one_after(r):
    j = _single_lease(r)
    assert _only_request(r)["now"] == j["lease_expires_at"] - 1


def _b_equal(r):
    j = _single_lease(r)
    assert _only_request(r)["now"] == j["lease_expires_at"]


def _b_now_zero(r):
    j = _single_lease(r)
    assert _only_request(r)["now"] == 0 and j["lease_expires_at"] == 1


def _b_now_max(r):
    j = _single_lease(r)
    assert _only_request(r)["now"] == MAX == j["lease_expires_at"]


def _b_expiry_max(r):
    j = _single_lease(r)
    assert j["lease_expires_at"] == MAX and _only_request(r)["now"] == MAX - 1


def _b_empty(r):
    assert r["state"] == {"jobs": [], "next_seq": 0}


def _b_worker_bound(r):
    q = _only_request(r)
    j = _single_lease(r, owner=q["worker"])
    assert len(q["worker"]) == 64 and j["lease_owner"] == q["worker"]


def _b_attempts_max(r):
    assert _single_lease(r)["attempts"] == MAX_ATTEMPTS


def _b_gap(r):
    assert r["state"]["next_seq"] == _jobs(r)[-1]["seq"] + 8


def _b_neg_digits(r):
    n = _single_lease(r)["payload"]["n"]
    assert type(n) is int and n < 0 and len(str(-n)) == 4000


def _b_digits(r):
    n = _single_lease(r)["payload"]["n"]
    assert type(n) is int and n > 0 and len(str(n)) == 4000


def _b_depth(r):
    kinds, leaf = _chain(_single_lease(r)["payload"])
    assert kinds == [list] * 64 and type(leaf) is int and leaf == 0
    assert _depth(r["expect_states"][0]["jobs"][0]["payload"]) == 64


def _b_dict_depth(r):
    kinds, leaf = _chain(_single_lease(r)["payload"])
    assert kinds == [dict] * 64 and type(leaf) is int and leaf == 0
    kinds, leaf = _chain(r["expect_states"][0]["jobs"][0]["payload"])
    assert kinds == [dict] * 64 and type(leaf) is int and leaf == 0


def _typed(values):
    return [(type(v), v) for v in values]


_LITERALS = [(bool, True), (bool, False), (type(None), None)]


def _b_literals(r):
    (committed,) = r["expect_states"][0]["jobs"]
    for p in (_single_lease(r)["payload"], committed["payload"]):
        assert sorted(p) == ["f", "l", "n", "t"]
        assert _typed([p["t"], p["f"], p["n"]]) == _LITERALS
        assert _typed(p["l"]) == [*_LITERALS, (int, 1), (int, 0)]


def _b_next_seq_max(r):
    _single_lease(r)
    assert r["state"]["next_seq"] == MAX


def _b_priorities(r):
    assert [j["priority"] for j in _jobs(r)] == [0, 9]


def _b_non_ascii(r):
    s = _single_lease(r)["payload"]["s"]
    assert any(ord(ch) > 127 for ch in s)


OK_EDGES = {
    "releases_own_unexpired_leases": _h_own,
    "mixed_state": _h_mixed,
    "restart_is_idempotent": _h_idem,
    "no_lease_empty_receipt": _h_nolease,
    "expiry_one_after_now": _b_one_after,
    "expiry_equal_now_untouched": _b_equal,
    "now_zero": _b_now_zero,
    "now_at_clock_limit": _b_now_max,
    "expiry_at_clock_limit": _b_expiry_max,
    "empty_queue": _b_empty,
    "worker_at_grammar_bound": _b_worker_bound,
    "attempts_at_max_released": _b_attempts_max,
    "next_seq_gap_kept": _b_gap,
    "payload_negative_4000_digits": _b_neg_digits,
    "payload_depth_at_bound": _b_depth,
    "payload_dict_depth_at_bound": _b_dict_depth,
    "payload_json_literals": _b_literals,
    "payload_4000_digits": _b_digits,
    "next_seq_at_clock_limit": _b_next_seq_max,
    "priority_bounds": _b_priorities,
    "non_ascii_payload": _b_non_ascii,
}


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


# -- closed malformed tags: exact values, exact loci -------------------------

_JA = "job1:c320a4de55bab79ab85ec24089658fef29dea4beb512432f69b3b5c8c980274f"
_JB = "job1:5fb9c7b041f77c9415089adbdb9d0aa85db517818878e47a0c59febba7d3b4a1"
_RQ = {"op": "restart", "worker": "w1", "now": 1000}
_W64 = "w" * 64


def _lease_a(**kw):
    """The pinned leased job a at seq 0 every single-job row starts from."""
    job = {
        "job_id": _JA,
        "seq": 0,
        "priority": 5,
        "payload": {"k": "a"},
        "status": "leased",
        "attempts": 1,
        "lease_owner": "w1",
        "lease_expires_at": 2000,
    }
    job.update(kw)
    return job


def _idle(key_id, seq, **kw):
    job = _lease_a(job_id=key_id, seq=seq, payload={"k": "a" if key_id == _JA else "b"})
    job.update(status="ready", attempts=0, lease_owner=None, lease_expires_at=None)
    job.update(kw)
    return job


def _st(*jobs, next_seq=None):
    jobs = list(jobs)
    nxt = (jobs[-1]["seq"] + 1 if jobs else 0) if next_seq is None else next_seq
    return {"jobs": jobs, "next_seq": nxt}


_S1 = _st(_lease_a())


def _same_type_eq(a, b):
    return _diff_paths(a, b) == set()


def _renamed(bad, fix, old, new):
    assert type(bad) is dict and len(bad) == len(fix)
    assert set(bad) - set(fix) == {new} and set(fix) - set(bad) == {old}
    assert _same_type_eq(bad[new], fix[old])
    moved = {(new if k == old else k): v for k, v in fix.items()}
    assert _diff_paths(bad, moved) == set()  # the rename is the only change


def _nest_dict(depth):
    x = {"k": 0}
    for _ in range(depth - 1):
        x = {"k": x}
    return x


def _nest(depth):
    x = [0]
    for _ in range(depth - 1):
        x = [x]
    return x


# name -> (bad request, fixed request): request-only defects on the S1 state
_REQUEST_ONLY = {
    "unknown_op": ({**_RQ, "op": "reboot"}, _RQ),
    "worker_bad_grammar": ({**_RQ, "worker": "w 1"}, _RQ),
    "worker_trailing_newline": ({**_RQ, "worker": "w1\n"}, _RQ),
    "worker_past_grammar_bound": ({**_RQ, "worker": "w" * 65}, {**_RQ, "worker": _W64}),
    "now_negative": ({**_RQ, "now": -1}, {**_RQ, "now": 0}),
    "now_past_clock": ({**_RQ, "now": MAX + 1}, {**_RQ, "now": MAX}),
    "now_bool": ({**_RQ, "now": True}, {**_RQ, "now": 1}),
}
# name -> (bad state, fixed state): state-only defects under the pinned request
_STATE_ONLY = {
    "next_seq_negative": (_st(next_seq=-1), _st(next_seq=0)),
    "next_seq_not_past_last": (
        _st(_idle(_JA, 0), _idle(_JB, 1), next_seq=1),
        _st(_idle(_JA, 0), _idle(_JB, 1), next_seq=2),
    ),
    "seq_not_increasing": (
        _st(_idle(_JA, 0), _idle(_JB, 0), next_seq=2),
        _st(_idle(_JA, 0), _idle(_JB, 1), next_seq=2),
    ),
    "duplicate_job_id": (
        _st(_idle(_JA, 0), _idle(_JB, 1, job_id=_JA)),
        _st(_idle(_JA, 0), _idle(_JB, 1)),
    ),
    "job_id_bad_grammar": (_st(_lease_a(job_id=_JA + "\n")), _S1),
    "next_seq_past_clock": (_st(_lease_a(), next_seq=MAX + 1), _st(_lease_a(), next_seq=MAX)),
    "priority_negative": (_st(_lease_a(priority=-1)), _st(_lease_a(priority=0))),
    "priority_ten": (_st(_lease_a(priority=10)), _st(_lease_a(priority=9))),
    "attempts_negative": (_st(_idle(_JA, 0, attempts=-1)), _st(_idle(_JA, 0))),
    "owner_trailing_newline": (_st(_lease_a(lease_owner="w1\n")), _S1),
    "done_with_lease_expiry": (
        _st(_idle(_JA, 0, status="done", attempts=1, lease_expires_at=2000)),
        _st(_idle(_JA, 0, status="done", attempts=1)),
    ),
    "payload_depth_past_bound": (
        _st(_lease_a(payload=_nest(65))),
        _st(_lease_a(payload=_nest(64))),
    ),
    "payload_dict_depth_past_bound": (
        _st(_lease_a(payload=_nest_dict(65))),
        _st(_lease_a(payload=_nest_dict(64))),
    ),
    "payload_4001_digits": (
        _st(_lease_a(payload={"n": 10**4000})),
        _st(_lease_a(payload={"n": 10**4000 - 1})),
    ),
    "status_unknown": (_st(_idle(_JA, 0, status="paused")), _st(_idle(_JA, 0))),
    "attempts_past_max": (_st(_lease_a(attempts=6)), _st(_lease_a(attempts=5))),
    "leased_attempts_zero": (_st(_lease_a(attempts=0)), _st(_lease_a(attempts=1))),
    "leased_expiry_zero": (_st(_lease_a(lease_expires_at=0)), _st(_lease_a(lease_expires_at=1))),
    "leased_expiry_past_bound": (
        _st(_lease_a(lease_expires_at=MAX + 1)),
        _st(_lease_a(lease_expires_at=MAX)),
    ),
    "leased_owner_bad_grammar": (
        _st(_lease_a(lease_owner="!!")),
        _st(_lease_a(lease_owner="w2")),
    ),
    "ready_with_lease_owner": (_st(_idle(_JA, 0, lease_owner="w1")), _st(_idle(_JA, 0))),
    "dead_below_max": (
        _st(_idle(_JA, 0, status="dead", attempts=4)),
        _st(_idle(_JA, 0, status="dead", attempts=5)),
    ),
    "done_attempts_zero": (
        _st(_idle(_JA, 0, status="done", attempts=0)),
        _st(_idle(_JA, 0, status="done", attempts=1)),
    ),
    "payload_lone_surrogate": (
        _st(_lease_a(payload={"s": "\ud800"})),
        _st(_lease_a(payload={"s": "x"})),
    ),
    "payload_key_lone_surrogate": (
        _st(_lease_a(payload={"\ud800": 1})),
        _st(_lease_a(payload={"k": 1})),
    ),
}


def _m(name, bst, bq, fst, fq):  # noqa: C901 - one closed branch per tag
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus (paths are over [state, request])."""
    bad, fix = [bst, bq], [fst, fq]
    if name in _REQUEST_ONLY:
        b, f = _REQUEST_ONLY[name]
        assert _same_type_eq(bst, _S1) and _same_type_eq(fst, _S1), name
        assert _same_type_eq(bq, b) and _same_type_eq(fq, f), name
        assert len(_diff_paths(bq, fq)) == 1, name
    elif name in _STATE_ONLY:
        b, f = _STATE_ONLY[name]
        assert _same_type_eq(bq, _RQ) and _same_type_eq(fq, _RQ), name
        assert _same_type_eq(bst, b) and _same_type_eq(fst, f), name
        paths = _diff_paths(bst, fst)
        # one job field, or the whole payload / next_seq for container defects
        assert len({p[:3] if p[:1] == ("jobs",) else p for p in paths}) == 1, name
    elif name == "request_not_a_dict":
        assert _same_type_eq(bst, _S1) and _same_type_eq(fst, _S1)
        assert type(bq) is list and _same_type_eq(bq, [_RQ]) and _same_type_eq(fq, _RQ)
    elif name == "extra_field":
        assert _same_type_eq(bq, {**_RQ, "note": 1})
        _only(bad, fix, (1, "note"))
    elif name == "missing_field":
        assert _same_type_eq(bq, {"op": "restart", "worker": "w1"})
        _only(bad, fix, (1, "now"))
    elif name == "renamed_field":
        _renamed(bq, fq, "now", "at")
        _only([bst], [fst])
    elif name == "bad_request_and_bad_state":
        assert _same_type_eq(bq, {**_RQ, "now": -1}) and _same_type_eq(fq, {**_RQ, "now": 0})
        assert _same_type_eq(bst, _st(_lease_a(priority=10)))
        assert _same_type_eq(fst, _st(_lease_a(priority=9)))
        _only(bad, fix, (0, "jobs", 0, "priority"), (1, "now"))
    elif name == "state_not_a_dict":
        assert type(bst) is list and _same_type_eq(bst, [_S1]) and _same_type_eq(fst, _S1)
        _only([bq], [fq])
    elif name == "state_extra_field":
        assert _same_type_eq(bst, {**_S1, "note": 1})
        _only(bad, fix, (0, "note"))
    elif name == "state_renamed_field":
        _renamed(bst, fst, "next_seq", "seq")
        _only([bq], [fq])
    elif name == "jobs_not_a_list":
        assert _same_type_eq(bst, {"jobs": {}, "next_seq": 0}) and _same_type_eq(fst, _st())
        _only(bad, fix, (0, "jobs"))
    elif name == "job_extra_field":
        assert _same_type_eq(bst, _st({**_lease_a(), "note": 1}))
        _only(bad, fix, (0, "jobs", 0, "note"))
    else:
        raise AssertionError(f"unknown malformed tag {name}")
    if name in _REQUEST_ONLY or name in _STATE_ONLY:
        assert _diff_paths(bad, fix), name


def _check_rollback_scenario(row):
    name, rej = row["name"], row["rejected"]
    if name == "malformed_then_repaired":
        assert _same_type_eq(rej["request"], {**_RQ, "now": -1})
        assert _same_type_eq(row["request"], {**_RQ, "now": 0})
        assert _same_type_eq(rej["state"], _S1) and _same_type_eq(row["state"], _S1)
    elif name == "corrupt_then_repaired":
        assert _same_type_eq(rej["state"], _st(_lease_a(priority=10)))
        assert _same_type_eq(row["state"], _st(_lease_a(priority=9)))
        assert _same_type_eq(rej["request"], _RQ) and _same_type_eq(row["request"], _RQ)
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
                rep = row["minimal_repair"]
                _m(row["name"], row["state"], row["request"], rep["state"], rep["request"])
            else:
                seq = {j["job_id"]: j["seq"] for j in row["state"]["jobs"]}
                got = (row["expect_failure"], [seq[j] for j in row["expect_record"]["released"]])
                assert got == meta, label
                _check_rollback_scenario(row)
    assert set(ROW_DIGESTS) == {f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    assert set(OK_EDGES) == set(HAPPY_MANIFEST) | set(BOUNDARY_MANIFEST)


def _names(section):
    return [row["name"] for row in CASES[section]]


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
    return str.__str__(k) if isinstance(k, str) else (type(k), k)


def _snap(obj, _seen=None):
    """Type-exact snapshot, copy-invariant: containers are numbered in
    visit order and a container met again (an alias or a cycle) snapshots
    as a reference to its first number, so aliased or cyclic payloads
    snapshot finitely and a re-wired alias is a difference."""
    seen = {} if _seen is None else _seen
    if isinstance(obj, (dict, list)):
        if id(obj) in seen:
            return ("ref", seen[id(obj)])
        seen[id(obj)] = len(seen)
        if isinstance(obj, dict):
            body = [(type(k), _snap_key(k), _snap(v, seen)) for k, v in dict.items(obj)]
        else:
            body = [_snap(v, seen) for v in list.__iter__(obj)]
        return (type(obj), body)
    if isinstance(obj, str):
        return (type(obj), str.__str__(obj))
    if isinstance(obj, float) and obj != obj:
        return (type(obj), "nan")
    return (type(obj), obj)


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
    assert {len(rec["released"]) for rec in recs} >= {0, 1, 2}
    assert {rec["prior_state_id"] == rec["state_id"] for rec in recs} == {True, False}
    statuses = {
        j["status"] for s in ("happy", "boundary") for r in CASES[s] for j in r["state"]["jobs"]
    }
    assert statuses == set(_QUEUE["state"]["statuses"])


_S = "boundary"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(schema=2),
        lambda c: c.update(schema=True),
        lambda c: c.update(contract="jobs-limit"),
        lambda c: c.update(contract_base_path="/jobs/restart/v2"),
        lambda c: c.update(extra=1),
        lambda c: c.pop("rollback"),
        lambda c: c["boundary"].clear(),
        lambda c: c["happy"][0].update(eta=0),
        lambda c: c["happy"][0]["expect_records"].pop(),
        lambda c: c["happy"][0]["expect_states"].pop(),
        lambda c: c["happy"][0]["expect_records"][0].pop("state_id"),
        lambda c: c["happy"][0]["expect_records"][0].update(extra=1),
        lambda c: c["happy"][0]["expect_records"][0].update(restart_id="rst1:zz"),
        lambda c: c["happy"][0]["expect_records"][0].update(op="nack"),
        lambda c: c["happy"][0]["expect_records"][0].update(worker="w2"),
        lambda c: c["happy"][0]["expect_records"][0].update(restarted_at=0),
        lambda c: c["happy"][0]["expect_records"][0].update(released=[]),
        lambda c: c["happy"][0]["expect_records"][0].update(prior_state_id="qs1:" + "0" * 64),
        lambda c: c["happy"][0]["expect_states"][0].update(next_seq=99),
        lambda c: c["happy"][0]["expect_states"][0]["jobs"][1].update(attempts=0),
        lambda c: c["happy"][0]["expect_states"][0]["jobs"][1].update(status="dead"),
        lambda c: c["happy"][0]["expect_states"][0]["jobs"][2].update(status="ready"),
        lambda c: c["happy"][0]["state"].update(extra=1),
        lambda c: c["happy"][0]["requests"][0].update(eta=1),
        lambda c: c["malformed"][0].update(expect_failure="internal"),
        lambda c: c["malformed"][1].update(
            minimal_repair={"request": c["malformed"][1]["request"]}
        ),
        lambda c: c["malformed"][1]["minimal_repair"].update(request=c["malformed"][1]["request"]),
        lambda c: c["malformed"][1]["minimal_repair"].update(previous={}),
        lambda c: c["rollback"][0].update(expect_failure="internal"),
        lambda c: c["rollback"][0].update(request=c["rollback"][0]["rejected"]["request"]),
        lambda c: c["rollback"][0]["expect_record"].update(restart_id="rst1:" + "0" * 63),
        lambda c: c["rollback"][0]["expect_state"]["jobs"][0].update(status="leased"),
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
    """Re-pin the row from the reference so only the closure can catch it."""
    row["expect_records"], row["expect_states"] = _run(row["state"], row["requests"])


def _edit(section, name, fn):
    def mutant(c):
        row = _bn(c, section, name)
        fn(row)
        if section in ("happy", "boundary"):
            _regenerate(row)

    return mutant


def _req(i, **kw):
    return lambda row: row["requests"][i].update(kw)


def _job(i, **kw):
    return lambda row: row["state"]["jobs"][i].update(kw)


def _at(node, path):
    for p in path:
        node = node[p]
    return node


def _bad(name, where, *path, **kw):
    """Edit the malformed row's STATE or REQUEST at path (a second defect
    or a retype)."""
    return lambda c: _at(_bn(c, "malformed", name)[where], path).update(kw)


def _fix(name, where, *path, **kw):
    return lambda c: _at(_bn(c, "malformed", name)["minimal_repair"][where], path).update(kw)


_CLOSURE_MUTANTS = {
    "swap_boundary_names": lambda c: _swap(c, _S, "now_zero", "expiry_one_after_now", "name"),
    "swap_happy_names": lambda c: _swap(
        c, "happy", "releases_own_unexpired_leases", "no_lease_empty_receipt", "name"
    ),
    "swap_malformed_names_same_class": lambda c: _swap(
        c, "malformed", "priority_ten", "attempts_past_max", "name"
    ),
    "swap_malformed_inputs": lambda c: _swap(
        c, "malformed", "priority_ten", "attempts_past_max", "state", "minimal_repair"
    ),
    "swap_request_inputs": lambda c: _swap(
        c, "malformed", "now_negative", "now_past_clock", "request", "minimal_repair"
    ),
    "drop_now_bool": lambda c: c["malformed"].remove(_bn(c, "malformed", "now_bool")),
    "duplicate_unknown_op": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "unknown_op"))
    ),
    "expiry_edge_one_short": _edit(_S, "expiry_one_after_now", _req(0, now=1998)),
    "equal_expiry_made_strict": _edit(_S, "expiry_equal_now_untouched", _req(0, now=2001)),
    "now_zero_moved": _edit(_S, "now_zero", _req(0, now=1)),
    "clock_limit_one_short": _edit(_S, "now_at_clock_limit", _req(0, now=MAX - 2)),
    "expiry_limit_one_short": _edit(_S, "expiry_at_clock_limit", _job(0, lease_expires_at=MAX - 1)),
    "worker_bound_one_short": _edit(
        _S,
        "worker_at_grammar_bound",
        lambda row: [
            row["requests"][0].update(worker="w" * 63),
            _job(0, lease_owner="w" * 63)(row),
        ],
    ),
    "attempts_below_max": _edit(_S, "attempts_at_max_released", _job(0, attempts=4)),
    "gap_closed": _edit(_S, "next_seq_gap_kept", lambda row: row["state"].update(next_seq=2)),
    "negative_digits_short": _edit(
        _S, "payload_negative_4000_digits", _job(0, payload={"n": -(10**3999 - 1)})
    ),
    "digits_short": _edit(_S, "payload_4000_digits", _job(0, payload={"n": 10**3999 - 1})),
    "depth_short": _edit(
        _S,
        "payload_depth_at_bound",
        lambda row: _job(0, payload=row["state"]["jobs"][0]["payload"][0])(row),
    ),
    "dict_depth_short": _edit(
        _S,
        "payload_dict_depth_at_bound",
        lambda row: _job(0, payload=row["state"]["jobs"][0]["payload"]["k"])(row),
    ),
    "dict_depth_made_list": _edit(
        _S, "payload_dict_depth_at_bound", _job(0, payload=[{"k": 0}] * 1)
    ),
    "literal_made_int": _edit(
        _S,
        "payload_json_literals",
        lambda row: _job(0, payload={**row["state"]["jobs"][0]["payload"], "t": 1})(row),
    ),
    "literal_in_list_made_int": _edit(
        _S,
        "payload_json_literals",
        lambda row: _job(
            0, payload={**row["state"]["jobs"][0]["payload"], "l": [True, 0, None, 1, 0]}
        )(row),
    ),
    "dict_depth_repair_short": _fix(
        "payload_dict_depth_past_bound", "state", "jobs", 0, payload={"k": 0}
    ),
    "dict_depth_second_defect": _bad(
        "payload_dict_depth_past_bound", "state", "jobs", 0, priority=10
    ),
    "next_seq_limit_short": _edit(
        _S, "next_seq_at_clock_limit", lambda row: row["state"].update(next_seq=MAX - 1)
    ),
    "priority_bounds_inner": _edit(_S, "priority_bounds", _job(1, priority=8)),
    "payload_made_ascii": _edit(_S, "non_ascii_payload", _job(0, payload={"s": "e"})),
    "mixed_expired_lease_moved": _edit("happy", "mixed_state", _job(1, lease_expires_at=1001)),
    "mixed_other_worker_owned": _edit("happy", "mixed_state", _job(2, lease_owner="w1")),
    "own_lease_expired": _edit("happy", "releases_own_unexpired_leases", _req(0, now=2000)),
    "idempotent_second_differs": _edit("happy", "restart_is_idempotent", _req(1, now=999)),
    "no_lease_worker_holds": _edit("happy", "no_lease_empty_receipt", _req(0, worker="w2")),
    "repair_touches_two_fields": _fix("now_negative", "request", worker="w2"),
    "repair_state_touched": _fix("now_negative", "state", next_seq=5),
    "priority_repair_short": _fix("priority_ten", "state", "jobs", 0, priority=8),
    "expiry_repair_short": _fix(
        "leased_expiry_past_bound", "state", "jobs", 0, lease_expires_at=MAX - 1
    ),
    "rollback_follow_up_widened": lambda c: _bn(c, "rollback", "malformed_then_repaired")[
        "request"
    ].update(now=1),
    "renamed_field_second_defect": _bad("renamed_field", "request", worker="w9"),
    "state_renamed_second_defect": _bad("state_renamed_field", "state", seq=2),
    "unknown_op_retyped": _bad("unknown_op", "request", op=None),
    "now_edge_retyped": _bad("now_negative", "request", now=-1.0),
    "priority_edge_retyped": _bad("priority_ten", "state", "jobs", 0, priority=10.0),
    "priority_by_two": _bad("priority_ten", "state", "jobs", 0, priority=11),
    "priority_negative_by_two": _bad("priority_negative", "state", "jobs", 0, priority=-2),
    "attempts_negative_by_two": _bad("attempts_negative", "state", "jobs", 0, attempts=-2),
    "worker_newline_retyped": _bad("worker_trailing_newline", "request", worker="w1\r"),
    "owner_newline_retyped": _bad("owner_trailing_newline", "state", "jobs", 0, lease_owner="w1\r"),
    "done_expiry_also_owner": _bad("done_with_lease_expiry", "state", "jobs", 0, lease_owner="w1"),
    "precedence_state_made_valid": _bad(
        "bad_request_and_bad_state", "state", "jobs", 0, priority=9
    ),
    "precedence_request_made_valid": _bad("bad_request_and_bad_state", "request", now=0),
    "job_extra_second_defect": _bad("job_extra_field", "state", "jobs", 0, priority=10),
    "dead_attempts_retyped": _bad("dead_below_max", "state", "jobs", 0, attempts=4.0),
    "expiry_zero_by_two": _bad("leased_expiry_zero", "state", "jobs", 0, lease_expires_at=-1),
    "defect_text_edited": lambda c: _bn(c, "malformed", "now_bool").update(defect="now is odd"),
}


def _section_of(cases, row):
    for section in SECTIONS:
        if any(r is row for r in cases[section]):
            return section
    raise AssertionError("row not in cases")


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


def _row(section, name):
    (row,) = [r for r in CASES[section] if r["name"] == name]
    return row


@pytest.mark.parametrize("section,name", [(s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_records_and_states(section, name):
    row = _row(section, name)
    state, requests = copy.deepcopy(row["state"]), copy.deepcopy(row["requests"])
    records = []
    for q in requests:
        records.append(_restart(state, q))  # commits into the SAME object
        assert _snap(q) == _snap(row["requests"][len(records) - 1])
        assert _snap(state) == _snap(row["expect_states"][len(records) - 1])
    assert records == row["expect_records"]


def test_pinned_ids_match_the_contract_preimages():
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            before = row["state"]
            for rec, after in zip(row["expect_records"], row["expect_states"], strict=True):
                assert rec["restart_id"] == _preimage(rec)
                assert (rec["prior_state_id"], rec["state_id"]) == (
                    _state_id(before),
                    _state_id(after),
                )
                before = after
    for row in CASES["rollback"]:
        assert row["expect_record"]["restart_id"] == _preimage(row["expect_record"])


def test_tampered_pinned_state_is_detected():
    row = copy.deepcopy(_row("happy", "mixed_state"))
    row["expect_states"][0]["jobs"][0]["attempts"] = 0
    assert _run(row["state"], row["requests"])[1] != row["expect_states"]


def _rejects(failure_class, state, request):
    before = (_snap(state), _snap(request))
    with pytest.raises(_REF.RestartError) as err:
        _restart(state, request)
    exc = err.value
    assert type(exc) is _REF.RestartError
    assert exc.failure_class == failure_class
    assert exc.code == FAILURE_MAPPING[failure_class] and exc.code in ERROR_ENUM
    assert exc.retryable is False
    assert exc.__cause__ is None and exc.__context__ is None
    assert (_snap(state), _snap(request)) == before  # type-exact: True != 1


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    row = _row("malformed", name)
    state, request = copy.deepcopy(row["state"]), copy.deepcopy(row["request"])
    _rejects(row["expect_failure"], state, request)
    assert (_snap(state), _snap(request)) == (_snap(row["state"]), _snap(row["request"]))


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_succeeds(name):
    rep = _row("malformed", name)["minimal_repair"]
    state, request = copy.deepcopy(rep["state"]), copy.deepcopy(rep["request"])
    record = _restart(state, request)
    _validate_record(record, name)
    _check_step(rep["state"], rep["request"], record, state, name)
    assert _snap(request) == _snap(rep["request"])


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_row("malformed", "priority_ten"))
    row["state"]["jobs"][0]["priority"] = 9
    assert _fails(row["state"], row["request"]) is None


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    rej = copy.deepcopy(row["rejected"])
    _rejects(row["expect_failure"], rej["state"], rej["request"])
    assert _snap(rej) == _snap(row["rejected"])
    state, request = copy.deepcopy(row["state"]), copy.deepcopy(row["request"])
    before = _containers_of(state, request)
    assert _restart(state, request) == row["expect_record"]
    _assert_detached(state, before)
    assert _snap(state) == _snap(row["expect_state"])
    assert _snap(request) == _snap(row["request"])


def test_payload_literals_stay_distinct_from_int_subclasses():
    """The accepted bools and null of payload_json_literals sit beside
    hostile int subclasses in the same dict and list slots: those are
    rejected typed and untouched, so bool acceptance is exact-type."""
    row = _row("boundary", "payload_json_literals")
    (job,) = row["state"]["jobs"]
    (request,) = row["requests"]
    for payload in (
        {**job["payload"], "t": _IntSub(1)},
        {**job["payload"], "f": _IntSub(0)},
        {**job["payload"], "l": [_IntSub(1), False, None, 1, 0]},
    ):
        hostile = {**row["state"], "jobs": [{**job, "payload": payload}]}
        _hostile("corrupt_queue", hostile, copy.deepcopy(request))
    record = _restart(copy.deepcopy(row["state"]), copy.deepcopy(request))
    assert record == row["expect_records"][0]


def test_validation_precedence():
    """request shape before state integrity, first failure wins (derived
    from fixture rows so the order runs on pinned data)."""
    corrupt = _row("malformed", "priority_ten")
    bad_req = _row("malformed", "worker_bad_grammar")["request"]
    _rejects("malformed_restart_request", copy.deepcopy(corrupt["state"]), copy.deepcopy(bad_req))
    _rejects("malformed_restart_request", [], {**corrupt["request"], "op": "reboot"})
    _rejects("corrupt_queue", copy.deepcopy(corrupt["state"]), copy.deepcopy(corrupt["request"]))


# -- hostile-type rows derived from EVERY accepted input ---------------------


def _accepted_inputs():
    """(label, state, request) for every input the fixture accepts: each
    step of a happy/boundary row (on the state it actually meets), every
    minimal repair and every rollback follow-up."""
    out = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            states = [row["state"], *row["expect_states"][:-1]]
            for i, (st, q) in enumerate(zip(states, row["requests"], strict=True)):
                out.append((f"{section}:{row['name']}:{i}", st, q))
    for row in CASES["malformed"]:
        rep = row["minimal_repair"]
        out.append((f"repair:{row['name']}", rep["state"], rep["request"]))
    for row in CASES["rollback"]:
        out.append((f"follow-up:{row['name']}", row["state"], row["request"]))
    return out


_MR_ = "malformed_restart_request"
_CQ_ = "corrupt_queue"
_REQ_STR_FIELDS = ("op", "worker")
_REQ_INT_FIELDS = ("now",)
_JOB_STR_FIELDS = ("job_id", "status")
_JOB_INT_FIELDS = ("seq", "priority", "attempts")


class _FloatSub(float):
    pass


def _value_variants(prefix, mapping, str_fields, int_fields, wrap):
    out = []
    for field in str_fields:
        value = mapping[field]
        for label, make in (
            ("str-subclass", _StrSub),
            ("repr-raises", _ReprRaises),
            ("eq-raises", _EqRaises),
        ):
            out.append((f"{prefix}{label}-{field}", wrap({**mapping, field: make(value)})))
        out.append((f"{prefix}nested-{field}", wrap({**mapping, field: [value]})))
    for field in int_fields:
        value = mapping[field]
        out.append((f"{prefix}int-subclass-{field}", wrap({**mapping, field: _IntSub(value)})))
        out.append((f"{prefix}bool-{field}", wrap({**mapping, field: bool(value)})))
        out.append((f"{prefix}float-{field}", wrap({**mapping, field: float(value)})))
        out.append((f"{prefix}float-subclass-{field}", wrap({**mapping, field: _FloatSub(value)})))
    return out


def _containers(prefix, mapping, wrap):
    return [
        (f"{prefix}dict-subclass", wrap(_DictSub(mapping))),
        (f"{prefix}lying-dict", wrap(_LyingDict(mapping))),
        (f"{prefix}list-subclass", wrap(_ListSub(mapping.items()))),
    ]


def _keys(prefix, mapping, wrap):
    return [
        (f"{prefix}key-{label}-{field}", wrap(_rekey(mapping, field, make)))
        for field in mapping
        for label, make in KEY_FORMS.items()
    ]


def _payloads():
    """Payload shapes JSON cannot store, each on its own: hostile
    containers, keys and values, non-finite floats, a cycle and an alias."""
    cyc = []
    cyc.append(cyc)
    shared = [1]
    return [
        ("dict-subclass", _DictSub({"k": 1})),
        ("lying-dict", _LyingDict({"k": 1})),
        ("list-subclass", _ListSub([1])),
        ("tuple", (1,)),
        ("key-str-subclass", {_StrSub("k"): 1}),
        ("key-eq-raises", {_EqRaises("k"): 1}),
        ("key-collides", {_Collides("k"): 1}),
        ("key-int", {1: 1}),
        ("value-str-subclass", {"k": _StrSub("x")}),
        ("value-repr-raises", {"k": _ReprRaises("x")}),
        ("value-int-subclass", {"k": _IntSub(1)}),
        ("value-float-subclass", {"k": _FloatSub(1.0)}),
        ("value-nan", {"k": float("nan")}),
        ("value-inf", {"k": float("-inf")}),
        ("value-set", {"k": {1}}),
        ("cycle", cyc),
        ("alias-within", {"a": shared, "b": shared}),
    ]


def _variants(st, q):
    """(label, hostile state, hostile request, expected class) for one
    accepted input: every container, key and value boundary on the
    request, the state, the jobs list, the first job (with its optional
    lease fields when set) and its payload; plus a payload aliased
    across two jobs."""
    out = []
    for label, hq in (
        _containers("request-", q, lambda m: m)
        + _keys("request-", q, lambda m: m)
        + _value_variants("request-", q, _REQ_STR_FIELDS, _REQ_INT_FIELDS, lambda m: m)
    ):
        out.append((label, st, hq, _MR_))

    def put(label, hst):
        out.append((label, hst, q, _CQ_))

    for label, hst in _containers("state-", st, lambda m: m) + _keys("state-", st, lambda m: m):
        put(label, hst)
    for label, hst in _value_variants("state-", st, (), ("next_seq",), lambda m: m):
        put(label, hst)
    put("jobs-list-subclass", {**st, "jobs": _ListSub(st["jobs"])})
    put("jobs-tuple", {**st, "jobs": tuple(st["jobs"])})
    jobs = st["jobs"]
    if jobs:
        first = jobs[0]

        def in_job(m):
            return {**st, "jobs": [m, *jobs[1:]]}

        strs = _JOB_STR_FIELDS + (("lease_owner",) if first["lease_owner"] is not None else ())
        ints = _JOB_INT_FIELDS + (
            ("lease_expires_at",) if first["lease_expires_at"] is not None else ()
        )
        for label, hst in (
            _containers("job-", first, in_job)
            + _keys("job-", first, in_job)
            + _value_variants("job-", first, strs, ints, in_job)
        ):
            put(label, hst)
        for label, payload in _payloads():
            put(f"payload-{label}", in_job({**first, "payload": payload}))
    if len(jobs) >= 2:
        shared = {"k": 1}
        a, b = {**jobs[0], "payload": shared}, {**jobs[1], "payload": shared}
        put("payload-alias-across-jobs", {**st, "jobs": [a, b, *jobs[2:]]})
    return out


def _expected_count(st, q):
    n = 3 + 3 * len(FIELDS) + 4 * len(_REQ_STR_FIELDS) + 4 * len(_REQ_INT_FIELDS)
    n += 3 + 3 * len(STATE_FIELDS) + 4 + 2
    jobs = st["jobs"]
    if jobs:
        first = jobs[0]
        n += 3 + 3 * len(JOB_FIELDS) + 4 * len(_JOB_STR_FIELDS) + 4 * len(_JOB_INT_FIELDS)
        n += 4 * (first["lease_owner"] is not None) + 4 * (first["lease_expires_at"] is not None)
        n += len(_payloads())
    return n + (len(jobs) >= 2)


def _hostile(failure_class, state, request):
    """Typed class, fresh error, no user code ran, inputs unchanged."""
    snap = (_snap(state), _snap(request))
    HOSTILE.clear()
    _Armed.on = True
    try:
        try:
            _restart(state, request)
            got = "accepted"
        except _REF.RestartError as exc:
            got = (
                type(exc) is _REF.RestartError,
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
    assert (_snap(state), _snap(request)) == snap


_ACCEPTED = _accepted_inputs()


@pytest.mark.parametrize("label", [label for label, _, _ in _ACCEPTED])
def test_hostile_variants_of_every_accepted_input(label):
    ((st, q),) = [(s, r) for lbl, s, r in _ACCEPTED if lbl == label]
    assert _fails(st, q) is None  # the base input commits
    for vlabel, hst, hq, failure_class in _variants(copy.deepcopy(st), copy.deepcopy(q)):
        try:
            _hostile(failure_class, hst, hq)
        except AssertionError as exc:
            raise AssertionError(f"{label} {vlabel}: {exc}") from None


def test_variant_set_is_complete():
    for label, st, q in _ACCEPTED:
        labels = [v for v, _, _, _ in _variants(st, q)]
        assert len(labels) == len(set(labels)) == _expected_count(st, q), label
    assert {bool(st["jobs"]) for _, st, _ in _ACCEPTED} == {True, False}
    assert any(len(st["jobs"]) >= 2 for _, st, _ in _ACCEPTED)


# -- the job-count bound (derived: 10000 jobs do not belong in the file) -----

_MAX_JOBS = _QUEUE["state"]["max_jobs"]  # the contract literal, never the bound reference


def _many_jobs(n):
    """N idle jobs cloned from the pinned repair state of priority_ten,
    with distinct grammar-valid ids, increasing seqs and a fresh payload
    each (a shared payload would be an alias)."""
    (template,) = _row("malformed", "priority_ten")["minimal_repair"]["state"]["jobs"]
    idle = {**template, "status": "ready", "attempts": 0}
    idle.update(lease_owner=None, lease_expires_at=None)
    jobs = [{**idle, "job_id": f"job1:{i:064x}", "seq": i, "payload": {"i": i}} for i in range(n)]
    return {"jobs": jobs, "next_seq": n}


def test_job_count_bound():
    request = _row("malformed", "priority_ten")["request"]
    at_bound = _many_jobs(_MAX_JOBS)
    record = _restart(at_bound, copy.deepcopy(request))
    assert record["released"] == [] and record["prior_state_id"] == record["state_id"]
    _rejects("corrupt_queue", _many_jobs(_MAX_JOBS + 1), copy.deepcopy(request))


# -- the T0347 reference mutants must each turn this fixture red -------------


def _fixture_red():
    """Labels of every fixture check that disagrees with the reference
    currently bound (empty when green). Every check executes restart on
    pinned or derived inputs: no source-text or table-shape check counts."""
    red = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            try:
                got = _run(row["state"], row["requests"])
            except BaseException:  # noqa: BLE001
                got = None
            if got != (row["expect_records"], row["expect_states"]):
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
    for label, _st, _q in _HOSTILE_PROBES:
        try:
            test_hostile_variants_of_every_accepted_input(label)
        except BaseException:  # noqa: BLE001
            red.append(f"hostile:{label}")
    for label, check in (
        ("precedence", test_validation_precedence),
        ("jobs", test_job_count_bound),
        ("literals", test_payload_literals_stay_distinct_from_int_subclasses),
    ):
        try:
            check()
        except BaseException:  # noqa: BLE001
            red.append(label)
    return red


# two probes run the hostile rows under every mutant (the full set runs in
# the parametrized test): a two-job state with a leased first job, and the
# empty queue
_HOSTILE_PROBES = [
    next(x for x in _ACCEPTED if len(x[1]["jobs"]) >= 2 and x[1]["jobs"][0]["status"] == "leased"),
    next(x for x in _ACCEPTED if not x[1]["jobs"]),
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


# -- fixture-level mutants beyond the T0347 table (names disjoint from its
# REFERENCE_EDITS and EQUIVALENT_EDITS); each must turn the fixture red
FIXTURE_EDITS = {
    "grammar-fullmatch-off": (
        "regex.fullmatch(value) is not None",
        "regex.match(value) is not None",
    ),
    "max-jobs-strict": ("if len(jobs) > _REF_MAX_JOBS:", "if len(jobs) >= _REF_MAX_JOBS:"),
    "depth-strict": (
        "if depth > _REF_MAX_DEPTH or id(node) in seen:",
        "if depth >= _REF_MAX_DEPTH or id(node) in seen:",
    ),
    "alias-check-off": (
        "if depth > _REF_MAX_DEPTH or id(node) in seen:",
        "if depth > _REF_MAX_DEPTH:",
    ),
    "alias-not-recorded": ("            seen.add(id(node))\n", ""),
    "dict-depth-not-incremented": (
        "return False\n                    stack.append((value, depth + 1))",
        "return False\n                    stack.append((value, depth))",
    ),
    "bool-scalar-rejected": ("if node is None or kind is bool:", "if node is None:"),
    "shallow-copy-work": (
        "work = json.loads(_canon_queue(state))  # detached copy-on-write",
        'work = dict(state, jobs=[dict(j) for j in state["jobs"]])',
    ),
    "digits-strict": (
        "len(str(abs(node))) <= _REF_MAX_DIGITS",
        "len(str(abs(node))) < _REF_MAX_DIGITS",
    ),
    "priority-lower-minus": (
        '_int_in(job["priority"], 0, _REF_MAX_PRIORITY)',
        '_int_in(job["priority"], -1, _REF_MAX_PRIORITY)',
    ),
    "expiry-upper-minus": (
        "_int_in(expires, 1, _REF_MAX_NOW)",
        "_int_in(expires, 1, _REF_MAX_NOW - 1)",
    ),
    "next-seq-upper-plus": (
        "_int_in(next_seq, 0, _REF_MAX_NOW)",
        "_int_in(next_seq, 0, _REF_MAX_NOW + 1)",
    ),
    "now-upper-plus": (
        '_int_in(request["now"], 0, _REF_MAX_NOW)',
        '_int_in(request["now"], 0, _REF_MAX_NOW + 1)',
    ),
    "idle-expiry-arm-off": (
        "elif owner is not None or expires is not None:",
        "elif owner is not None:",
    ),
    "op-type-off": ('type(request["op"]) is str\n        and ', ""),
    "status-type-off": (
        "and type(status) is str\n        and status in _STATUSES",
        "and status in _STATUSES",
    ),
    "done-invariant-strict": (
        'if status == "done" and attempts < 1:',
        'if status == "done" and attempts < 2:',
    ),
    "prior-seq-not-advanced": ('        prior_seq = job["seq"]\n', ""),
    "seen-ids-not-recorded": ('        seen_ids.add(job["job_id"])\n', ""),
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
