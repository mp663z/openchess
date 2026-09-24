"""T0285: jobs queue conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the T0284
jobs-queue contract (data/contracts/queue.yaml). The cases execute
against the contract-derived reference engine in
tests.test_t0284_queue_contract - nothing is re-implemented here.
Pinned receipts AND pinned committed states were computed from
that reference at authoring time, so any contract or derivation
drift breaks this battery. Every malformed case is discriminating:
applying ONLY its declared single-locus repair (state or request)
makes it succeed. Rollback cases prove a rejected transition
leaves the exact supplied state and request bit- and
reference-identical before a valid follow-up commits as pinned.

The queue engine takes no caller callback, so there is no
untrusted-component boundary here and no forged-error battery.

The two re-enqueue-at-capacity boundary rows (reenqueue_at_seq_exhausted,
reenqueue_at_max_jobs) follow the merged T0284 reference reading -
dedupe before the seq/capacity checks (queue.yaml enqueue, dedupe and
capacity_exceeded "new job" trigger) - awaiting owner confirmation.

DESIGN CAUTION: the reference engine is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work must
execute these same cases against a separately implemented
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

from tests.test_t0284_queue_contract import (  # noqa: E402
    MAX_JOBS,
    QueueEngine,
    QueueError,
    _snap,
    _validate_state,
    empty_state,
    job_id_for,
    state_id_for,
)
from tools.queue_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "queue" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECEIPT_FIELDS = list(_CC["record"]["fields"])
OPS = {spec["op"]: spec["fields"] for spec in _CC["request"]["operations"]}
_IDS = _CC["identifiers"]
_JOB_RE = re.compile(_IDS["job_id"]["grammar"], re.ASCII)
_STATE_RE = re.compile(_IDS["state_id"]["grammar"], re.ASCII)
MAX_NOW = 2**53 - 1
MAX_ATTEMPTS = _CC["state"]["max_attempts"]

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "state", "requests", "expect_receipts",
           "expect_state"}
BAD_KEYS = {"name", "defect", "expect_failure", "state", "request",
            "minimal_repair"}
RB_KEYS = {"name", "why", "state", "rejected_request", "expect_failure",
           "request", "expect_receipt", "expect_state"}


def _apply(state, request):
    return QueueEngine().apply(state, request)


def _run(state, requests):
    """Apply in order; every commit must leave a valid state."""
    out = []
    for request in requests:
        out.append(_apply(state, request))
        _validate_state(state)
    return out


def _fails(state, request):
    try:
        _apply(copy.deepcopy(state), copy.deepcopy(request))
    except QueueError as exc:
        return exc.failure_class
    return None


def _validate_receipt(receipt, label):
    assert type(receipt) is dict, label
    assert all(type(k) is str for k in receipt), label
    assert set(receipt) == set(RECEIPT_FIELDS), label
    assert receipt["op"] in OPS, label
    assert _STATE_RE.fullmatch(receipt["state_id"]), label
    if receipt["job_id"] is None:
        assert receipt["op"] == "claim", label
        assert (receipt["status"], receipt["attempts"],
                receipt["lease_expires_at"]) == (None, None, None), label
    else:
        assert _JOB_RE.fullmatch(receipt["job_id"]), label
        assert type(receipt["attempts"]) is int, label
        assert (receipt["status"] == "leased") == (
            type(receipt["lease_expires_at"]) is int), label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert request.get("op") in OPS, label
    assert set(request) == set(OPS[request["op"]]), label


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
                assert type(row["why"]) is str and row["why"], label
                reqs, rcs = row["requests"], row["expect_receipts"]
                assert type(reqs) is list and reqs, label
                assert type(rcs) is list and len(rcs) == len(reqs), label
                for req, rc in zip(reqs, rcs, strict=True):
                    _validate_request_shape(req, label)
                    _validate_receipt(rc, label)
                    assert rc["op"] == req["op"], label
                assert rcs[-1]["state_id"] == state_id_for(
                    row["expect_state"]), label
            elif section == "malformed":
                assert set(row) == BAD_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                assert type(row["defect"]) is str and row["defect"], label
                repair = row["minimal_repair"]
                assert type(repair) is dict and len(repair) == 1, label
                (locus, value), = repair.items()
                assert locus in ("state", "request"), label
                assert _diff_paths(value, row[locus]), label
            else:
                assert set(row) == RB_KEYS, label
                assert type(row["why"]) is str and row["why"], label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                _validate_request_shape(row["request"], label)
                _validate_receipt(row["expect_receipt"], label)
                assert row["expect_receipt"]["state_id"] == state_id_for(
                    row["expect_state"]), label
    assert len(names) == len(set(names)), "fixture names must be unique"


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


def _max_depth(obj):
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
        if type(node) is int and type(node) is not bool:
            best = max(best, len(str(abs(node))))
        elif type(node) is dict:
            stack.extend(node.values())
        elif type(node) is list:
            stack.extend(node)
    return best


def _ops(row):
    return ",".join(r["op"] for r in row["requests"])


def _job(state, jid):
    (job,) = [j for j in state["jobs"] if j["job_id"] == jid]
    return job


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> (ops, final job count, final next_seq, last
# receipt status); malformed: name -> (failure class, repair locus,
# pinned defect text); rollback: name -> (failure class, rejected op,
# follow-up op, follow-up status)
HAPPY_MANIFEST = {
    "enqueue_new_job": ("enqueue", 1, 1, "ready"),
    "enqueue_claim_ack": ("enqueue,claim,ack", 1, 1, "done"),
    "claim_nack_returns_ready": ("enqueue,claim,nack", 1, 1, "ready"),
    "claim_orders_priority_then_seq": (
        "enqueue,enqueue,enqueue,claim", 3, 3, "leased"),
    "identical_reenqueue_is_noop": ("enqueue,enqueue", 1, 1, "ready"),
}
BOUNDARY_MANIFEST = {
    "empty_claim_null_receipt": ("claim", 0, 0, None),
    "lease_expired_at_now_reclaimed": ("enqueue,claim,claim", 1, 1, "leased"),
    "ack_one_ms_before_expiry": ("enqueue,claim,ack", 1, 1, "done"),
    "dead_after_max_attempts": (
        "enqueue" + ",claim,nack" * 5 + ",claim", 1, 1, None),
    "last_free_seq": ("enqueue", 1, 9007199254740991, "ready"),
    "reenqueue_at_seq_exhausted": ("enqueue", 1, 9007199254740991, "ready"),
    "reenqueue_at_max_jobs": ("enqueue", 10000, 10000, "ready"),
    "claim_expiry_at_clock_limit": ("enqueue,claim", 1, 1, "leased"),
    "priority_extremes": ("enqueue,enqueue,claim", 2, 2, "leased"),
    "payload_depth_at_limit": ("enqueue", 1, 1, "ready"),
    "payload_int_at_digit_limit": ("enqueue", 1, 1, "ready"),
}
MALFORMED_MANIFEST = {
    "request_not_a_dict": ("malformed_queue_request", "request",
        "the request is a list"),
    "unknown_op": ("malformed_queue_request", "request",
        "op is 'purge', not a registered operation"),
    "enqueue_extra_field": ("malformed_queue_request", "request",
        "the enqueue carries an extra job_id field"),
    "priority_bool": ("malformed_queue_request", "request",
        "priority is the bool True"),
    "priority_out_of_range": ("malformed_queue_request", "request",
        "priority is 10"),
    "dedupe_key_trailing_newline": ("malformed_queue_request", "request",
        "the dedupe key ends in a newline"),
    "worker_bad_grammar": ("malformed_queue_request", "request",
        "the worker id holds a space"),
    "lease_ms_zero": ("malformed_queue_request", "request",
        "lease_ms is 0"),
    "claim_past_clock_limit": ("malformed_queue_request", "request",
        "now + lease_ms passes 2**53-1"),
    "payload_too_deep": ("malformed_queue_request", "request",
        "the payload nests 65 deep"),
    "payload_int_past_digit_bound": ("malformed_queue_request", "request",
        "the payload holds a 4001-digit int"),
    "ack_unknown_job": ("unknown_job", "request",
        "the acked job id is not in the queue"),
    "ack_by_non_owner": ("lease_conflict", "request",
        "worker w2 acks w1's lease"),
    "ack_at_expiry": ("lease_conflict", "request",
        "the ack arrives exactly at lease expiry"),
    "nack_not_leased": ("lease_conflict", "state",
        "the nacked job is ready, not leased"),
    "dedupe_conflict_priority": ("dedupe_conflict", "request",
        "seen key re-enqueued with priority 4"),
    "dedupe_conflict_payload": ("dedupe_conflict", "request",
        "seen key re-enqueued with a different payload"),
    "seq_space_exhausted": ("capacity_exceeded", "state",
        "next_seq is 2**53-1, no seq left for a new job"),
    "state_not_a_dict": ("corrupt_queue", "state",
        "the state is a list"),
    "duplicate_job_id": ("corrupt_queue", "state",
        "two jobs share one job id"),
    "leased_without_owner": ("corrupt_queue", "state",
        "a leased job has no lease owner"),
    "dead_below_max_attempts": ("corrupt_queue", "state",
        "a dead job has 4 attempts, not 5"),
    "seq_not_increasing": ("corrupt_queue", "state",
        "the second job repeats seq 0"),
}
ROLLBACK_MANIFEST = {
    "dedupe_conflict_then_identical_reenqueue": ("dedupe_conflict", "enqueue",
        "enqueue", "ready"),
    "lease_conflict_then_owner_ack": ("lease_conflict", "ack",
        "ack", "done"),
    "malformed_payload_then_valid_enqueue": ("malformed_queue_request", "enqueue",
        "enqueue", "ready"),
    "expired_ack_then_reclaim": ("lease_conflict", "ack",
        "claim", "leased"),
}

# content binding: canonical sha256 of every row. Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:enqueue_new_job":
        "5ebf63a5c0f5868b3cf843b92e69ba2bb8d6ff86c33a42e9e9d64a528d78227b",
    "happy:enqueue_claim_ack":
        "bb4ddeacc1ea61360bcfe55bf40e3f060ec739279fc2a5702f5f429603c48f6a",
    "happy:claim_nack_returns_ready":
        "6f640cc195fe204bfb9d43cd4792ddbdc778c447e92c33a61079b827426bee85",
    "happy:claim_orders_priority_then_seq":
        "dd22a4ed06f39c61bd65d136eceefc5558fab0597a45239f7fe8b2a7613f62b2",
    "happy:identical_reenqueue_is_noop":
        "29a14ee5df5731731f1ca193fe54ad98c8f71ee161171206dd70f5ce5cad6bf9",
    "boundary:empty_claim_null_receipt":
        "0528e9a7ec830a6809e926ef5803333353ccfae562b0c51f2f03fe0a865d3707",
    "boundary:lease_expired_at_now_reclaimed":
        "0c0a710bffe66ba215012cc6d3c807360519887fad6ee9782429ee2a92b0f0e4",
    "boundary:ack_one_ms_before_expiry":
        "044e58853d11826c0e99fe7cbfe148ddf688de0fc6621a4e156f708ab5c861dc",
    "boundary:dead_after_max_attempts":
        "bf3c4f732ebd13c110ae2eacd640ad2b581e1289a6d25c1064cf2213b896aa54",
    "boundary:last_free_seq":
        "9d3023dac87a1b4e577627162cc913884e7f8fb95f66b44ca8253254f55c501f",
    "boundary:reenqueue_at_seq_exhausted":
        "28a110aa2ee4beb3e1cc6200336e4299e36539df304dd92e236c86addc705829",
    "boundary:reenqueue_at_max_jobs":
        "1929cadab4ac7a687ab33c6b2dd46d2082495122631cc92086b4ef81090e7ba8",
    "boundary:claim_expiry_at_clock_limit":
        "a072b65486c1c325f7f766a5b56e27aa3c498f9953ea06a8aed0f69bcc337723",
    "boundary:priority_extremes":
        "2abee70a52221e7c38f97cc07ae28ee5e628bd25db620b13dcc79f1b95709da6",
    "boundary:payload_depth_at_limit":
        "f1d15706fa3e5ba8eeff489004c8832dd65c8e9d2a9a502838b9d59297225796",
    "boundary:payload_int_at_digit_limit":
        "f16802f2a09f6a670a47d4fd15f1891f5c568fee84616348eb1e2b8f3223b66e",
    "malformed:request_not_a_dict":
        "be98f7f7ad453a48ddea72e9c7d7dc831aa72b88b22a007a97c397ff485283d3",
    "malformed:unknown_op":
        "80050f5efde0796477bbb415f98050f53857f7d4cac0bbdd3c34d8e348c53e25",
    "malformed:enqueue_extra_field":
        "8f31ce91a94c421c22c636f1f7716a15d281e9fa4176832ac4a6ed0c5c32b1bc",
    "malformed:priority_bool":
        "1a5ad8b77c76046293fab59582a7596f2f1f22ef2394b198d7bf2e2540af5972",
    "malformed:priority_out_of_range":
        "2228d37896dca49c65d6e2f13bf9c89859fbc16ddc0d99964e9a03a4aaafb89d",
    "malformed:dedupe_key_trailing_newline":
        "8ff39fa9af26fc2b8f57ee84e6a102eca98b5c50ae22352c431c9764cf6e7e7a",
    "malformed:worker_bad_grammar":
        "80e5876f6df82f377beafb2f3d6c8320a76b753ea9294382157a0da8669e181d",
    "malformed:lease_ms_zero":
        "f73742201d5a358e99aaf7f56a72c7e322f704f5144f2232988ddc162bcd17d8",
    "malformed:claim_past_clock_limit":
        "0d9af084c33428696b9e8601ecd80d9c0b0682a46980bba3735a10c59f9a7769",
    "malformed:payload_too_deep":
        "452d98b7fa3615de7a56f9adda476c78ce7871ba411a3c76221ffabe26d26fd8",
    "malformed:payload_int_past_digit_bound":
        "78fa7f88b101b5d25fd52a9aefb054ecd7efde76fa4651924452e477d5ed2138",
    "malformed:ack_unknown_job":
        "9ce8e0d22412be94e6282bb141bd2c25b71e79a2dc1ab8b85bef55d367d9a711",
    "malformed:ack_by_non_owner":
        "694c8c643be00db12a3ecf91342264953918f11cf8c43bec3e49f7995f303dd7",
    "malformed:ack_at_expiry":
        "26ca743fc0ef37172fdbdb6993eb99f83d7cf3d7f83ad5bc5696a08398695c91",
    "malformed:nack_not_leased":
        "a661a8f7ac92da090bb0cf8eda2bf94b96d25d703ce27aa46bfd8418de5f72aa",
    "malformed:dedupe_conflict_priority":
        "e6aad7dd837364b782ce3460390370a5a94d24074362774baf967e7fd470fb42",
    "malformed:dedupe_conflict_payload":
        "f52353ebed7f0c24719d235d04748b9a3acc007b02b6ddd77d4286d4fa144998",
    "malformed:seq_space_exhausted":
        "913b280f74955b26f9a8f2da901cbc55e49e951ff10f1b614c0a0827655f1560",
    "malformed:state_not_a_dict":
        "0dde8f013ee787ff53edae770562a11d5be4faf87a6e98f55c19ede4f08c078c",
    "malformed:duplicate_job_id":
        "e2fdd7a741e40c2d08b3684eae0e8bac08db8d3c0037ea32a392820a9dc70c31",
    "malformed:leased_without_owner":
        "e401268f8ffb78f1bfaf2e3a2d16f61d7d437c7d4c518cc25b35a60e3dace2d9",
    "malformed:dead_below_max_attempts":
        "8ea4b2ddfb730d5c15633c504229d90a025452627e3dae4b7ce803884108f32d",
    "malformed:seq_not_increasing":
        "9cec17b4272ec4e4bc044a27ee308cb4cc7ef93ae366a6830d7aa9bdf29d82c9",
    "rollback:dedupe_conflict_then_identical_reenqueue":
        "006f3fbecadc4f9b48f4ee4f579e78575e27ad054cf649d09f52934883ea17a5",
    "rollback:lease_conflict_then_owner_ack":
        "901ed74a6fc87a65daca44ac75121475ac16cb02000a71f56606ec8521333587",
    "rollback:malformed_payload_then_valid_enqueue":
        "6b6144b5d2a99f5e65c0c321bbdaead89ddb12e98dd0d3974216b801d32522e2",
    "rollback:expired_ack_then_reclaim":
        "8e13fe33ad2a40bb81734dde4a1ecdf07277c82dfbd50e541c8875aeb858d473",
}
MANIFESTS = {"happy": HAPPY_MANIFEST, "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

A = job_id_for("a")


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


# -- per-row semantic edges over the ORIGINAL data ---------------------------


def _h_enqueue_new(r):
    assert r["state"] == empty_state()
    job = r["expect_state"]["jobs"][0]
    assert job["job_id"] == job_id_for(r["requests"][0]["dedupe_key"])
    assert (job["seq"], job["status"], job["attempts"]) == (0, "ready", 0)


def _h_claim_ack(r):
    rc = r["expect_receipts"]
    assert rc[1]["job_id"] == rc[2]["job_id"] == r["requests"][2]["job_id"]
    assert r["requests"][2]["worker"] == r["requests"][1]["worker"]
    assert r["requests"][2]["now"] < rc[1]["lease_expires_at"]
    assert (rc[2]["status"], rc[2]["attempts"]) == ("done", 1)


def _h_claim_nack(r):
    rc = r["expect_receipts"]
    assert r["requests"][2]["job_id"] == rc[1]["job_id"]
    assert (rc[2]["status"], rc[2]["attempts"],
            rc[2]["lease_expires_at"]) == ("ready", 1, None)


def _h_ordering(r):
    jobs = r["expect_state"]["jobs"]
    prios = [j["priority"] for j in jobs]
    # a higher-number job enqueued first AND an equal-priority tie
    assert prios[0] > min(prios) and prios.count(min(prios)) >= 2
    best = min(jobs, key=lambda j: (j["priority"], j["seq"]))
    assert r["expect_receipts"][-1]["job_id"] == best["job_id"]
    assert best["seq"] != 0


def _h_reenqueue(r):
    a, b = r["requests"]
    assert a == b and r["expect_receipts"][0] == r["expect_receipts"][1]


def _b_empty_claim(r):
    assert r["state"]["jobs"] == []
    assert r["expect_receipts"][0]["job_id"] is None


def _b_expired_at_now(r):
    rq, rc = r["requests"], r["expect_receipts"]
    assert rq[2]["now"] == rc[1]["lease_expires_at"]
    assert rq[2]["worker"] != rq[1]["worker"]
    assert rc[2]["job_id"] == rc[1]["job_id"] and rc[2]["attempts"] == 2
    assert _job(r["expect_state"], rc[2]["job_id"])["lease_owner"] == (
        rq[2]["worker"])


def _b_ack_before_expiry(r):
    rq, rc = r["requests"], r["expect_receipts"]
    assert rq[2]["now"] == rc[1]["lease_expires_at"] - 1
    assert rq[2]["worker"] == rq[1]["worker"]


def _b_dead(r):
    claims = [x for x in r["expect_receipts"] if x["op"] == "claim"]
    assert len(claims) == MAX_ATTEMPTS + 1
    assert all(c["job_id"] is not None for c in claims[:-1])
    assert claims[-1]["job_id"] is None
    (job,) = r["expect_state"]["jobs"]
    assert (job["status"], job["attempts"]) == ("dead", MAX_ATTEMPTS)


def _b_last_free_seq(r):
    assert r["state"] == {"jobs": [], "next_seq": MAX_NOW - 1}
    assert r["expect_state"]["jobs"][0]["seq"] == MAX_NOW - 1


def _b_reenqueue_exhausted(r):
    assert r["state"]["next_seq"] == MAX_NOW
    (req,) = r["requests"]
    stored = _job(r["state"], job_id_for(req["dedupe_key"]))
    assert (stored["priority"], stored["payload"]) == (
        req["priority"], req["payload"])
    assert r["expect_state"] == r["state"]


def _b_reenqueue_max_jobs(r):
    jobs = r["state"]["jobs"]
    assert len(jobs) == MAX_JOBS and r["state"]["next_seq"] == MAX_JOBS
    (req,) = r["requests"]
    stored = _job(r["state"], job_id_for(req["dedupe_key"]))
    assert (stored["priority"], stored["payload"]) == (
        req["priority"], req["payload"])
    assert stored is not jobs[0] and stored is not jobs[-1]
    assert r["expect_receipts"][0]["job_id"] == stored["job_id"]
    assert r["expect_state"] == r["state"]


def _b_clock_limit(r):
    req = r["requests"][1]
    assert req["now"] + req["lease_ms"] == MAX_NOW
    assert r["expect_receipts"][1]["lease_expires_at"] == MAX_NOW


def _b_priority_extremes(r):
    rq = r["requests"]
    assert (rq[0]["priority"], rq[1]["priority"]) == (9, 0)
    assert r["expect_receipts"][-1]["job_id"] == job_id_for(
        rq[1]["dedupe_key"])


def _b_depth(r):
    assert _max_depth(r["requests"][0]["payload"]) == 64


def _b_digits(r):
    assert _max_int_digits(r["requests"][0]["payload"]) == 4000


OK_EDGES = {
    "enqueue_new_job": _h_enqueue_new,
    "enqueue_claim_ack": _h_claim_ack,
    "claim_nack_returns_ready": _h_claim_nack,
    "claim_orders_priority_then_seq": _h_ordering,
    "identical_reenqueue_is_noop": _h_reenqueue,
    "empty_claim_null_receipt": _b_empty_claim,
    "lease_expired_at_now_reclaimed": _b_expired_at_now,
    "ack_one_ms_before_expiry": _b_ack_before_expiry,
    "dead_after_max_attempts": _b_dead,
    "last_free_seq": _b_last_free_seq,
    "reenqueue_at_seq_exhausted": _b_reenqueue_exhausted,
    "reenqueue_at_max_jobs": _b_reenqueue_max_jobs,
    "claim_expiry_at_clock_limit": _b_clock_limit,
    "priority_extremes": _b_priority_extremes,
    "payload_depth_at_limit": _b_depth,
    "payload_int_at_digit_limit": _b_digits,
}


def _only(bad, fix, *paths):
    assert _diff_paths(bad, fix) == set(paths), (bad, fix)


def _m(name, r, bad, fix):  # noqa: C901 - one closed branch per tag
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus."""
    st = r["state"]
    if name == "request_not_a_dict":
        assert type(bad) is list and bad == [fix]
    elif name == "unknown_op":
        assert bad["op"] == "purge" and bad["op"] not in OPS
        _only(bad, fix, ("op",))
    elif name == "enqueue_extra_field":
        assert set(bad) - set(fix) == {"job_id"}
        assert {k: v for k, v in bad.items() if k != "job_id"} == fix
    elif name == "priority_bool":
        assert bad["priority"] is True
        _only(bad, fix, ("priority",))
    elif name == "priority_out_of_range":
        assert (bad["priority"], fix["priority"]) == (10, 9)
        _only(bad, fix, ("priority",))
    elif name == "dedupe_key_trailing_newline":
        assert bad["dedupe_key"] == fix["dedupe_key"] + "\n"
        _only(bad, fix, ("dedupe_key",))
    elif name == "worker_bad_grammar":
        assert " " in bad["worker"] and " " not in fix["worker"]
        _only(bad, fix, ("worker",))
    elif name == "lease_ms_zero":
        assert (bad["lease_ms"], fix["lease_ms"]) == (0, 1)
        _only(bad, fix, ("lease_ms",))
    elif name == "claim_past_clock_limit":
        assert bad["now"] + bad["lease_ms"] == MAX_NOW + 1
        assert fix["now"] + fix["lease_ms"] == MAX_NOW
        _only(bad, fix, ("now",))
    elif name == "payload_too_deep":
        assert (_max_depth(bad["payload"]),
                _max_depth(fix["payload"])) == (65, 64)
        _only({**bad, "payload": 0}, {**fix, "payload": 0})
    elif name == "payload_int_past_digit_bound":
        assert (_max_int_digits(bad["payload"]),
                _max_int_digits(fix["payload"])) == (4001, 4000)
        _only(bad, fix, ("payload", "n"))
    elif name == "ack_unknown_job":
        ids = {j["job_id"] for j in st["jobs"]}
        assert bad["job_id"] not in ids and fix["job_id"] in ids
        _only(bad, fix, ("job_id",))
    elif name == "ack_by_non_owner":
        job = _job(st, bad["job_id"])
        assert job["status"] == "leased"
        assert bad["worker"] != job["lease_owner"] == fix["worker"]
        _only(bad, fix, ("worker",))
    elif name == "ack_at_expiry":
        exp = _job(st, bad["job_id"])["lease_expires_at"]
        assert (bad["now"], fix["now"]) == (exp, exp - 1)
        _only(bad, fix, ("now",))
    elif name == "nack_not_leased":
        req = r["request"]
        assert _job(bad, req["job_id"])["status"] == "ready"
        job = _job(fix, req["job_id"])
        assert (job["status"], job["lease_owner"]) == ("leased",
                                                       req["worker"])
        assert req["now"] < job["lease_expires_at"]
        assert all(p[:2] == ("jobs", 0) for p in _diff_paths(bad, fix))
    elif name in ("dedupe_conflict_priority", "dedupe_conflict_payload"):
        field = name.rsplit("_", 1)[1]
        stored = _job(st, job_id_for(bad["dedupe_key"]))
        assert bad[field] != stored[field] == fix[field]
        other = "payload" if field == "priority" else "priority"
        assert bad[other] == stored[other]
        assert {p[0] for p in _diff_paths(bad, fix)} == {field}
    elif name == "seq_space_exhausted":
        assert bad == {"jobs": [], "next_seq": MAX_NOW}
        _only(bad, fix, ("next_seq",))
        assert fix["next_seq"] == MAX_NOW - 1
    elif name == "state_not_a_dict":
        assert bad == [] and fix == empty_state()
    elif name == "duplicate_job_id":
        ids = [j["job_id"] for j in bad["jobs"]]
        assert len(ids) == 2 and len(set(ids)) == 1
        assert fix["jobs"] == bad["jobs"][:1]
    elif name == "leased_without_owner":
        assert bad["jobs"][0]["status"] == "leased"
        assert bad["jobs"][0]["lease_owner"] is None
        _only(bad, fix, ("jobs", 0, "lease_owner"))
    elif name == "dead_below_max_attempts":
        assert bad["jobs"][0]["status"] == "dead"
        assert (bad["jobs"][0]["attempts"],
                fix["jobs"][0]["attempts"]) == (MAX_ATTEMPTS - 1,
                                                MAX_ATTEMPTS)
        _only(bad, fix, ("jobs", 0, "attempts"))
    elif name == "seq_not_increasing":
        assert bad["jobs"][1]["seq"] == bad["jobs"][0]["seq"]
        _only(bad, fix, ("jobs", 1, "seq"))
    else:
        raise AssertionError(f"unknown malformed tag {name}")


def _check_malformed_scenario(row):
    (locus, fix), = row["minimal_repair"].items()
    _m(row["name"], row, row[locus], fix)


def _check_rollback_scenario(row):
    name, st = row["name"], row["state"]
    bad, req = row["rejected_request"], row["request"]
    if name == "dedupe_conflict_then_identical_reenqueue":
        stored = _job(st, job_id_for(req["dedupe_key"]))
        assert bad["dedupe_key"] == req["dedupe_key"]
        assert bad["priority"] != stored["priority"] == req["priority"]
        assert row["expect_state"] == st
    elif name == "lease_conflict_then_owner_ack":
        job = _job(st, req["job_id"])
        assert bad["job_id"] == req["job_id"]
        assert bad["worker"] != job["lease_owner"] == req["worker"]
    elif name == "malformed_payload_then_valid_enqueue":
        assert _max_depth(bad["payload"]) == 65
        assert bad["dedupe_key"] == req["dedupe_key"]
        new = _job(row["expect_state"], job_id_for(req["dedupe_key"]))
        assert new["seq"] == st["next_seq"]
    elif name == "expired_ack_then_reclaim":
        job = _job(st, bad["job_id"])
        assert bad["now"] == job["lease_expires_at"] == req["now"]
        assert req["worker"] != job["lease_owner"] == bad["worker"]
        assert row["expect_receipt"]["attempts"] == job["attempts"] + 1
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
                assert (_ops(row), len(row["expect_state"]["jobs"]),
                        row["expect_state"]["next_seq"],
                        row["expect_receipts"][-1]["status"]) == meta, label
                OK_EDGES[row["name"]](row)
            elif section == "malformed":
                assert (row["expect_failure"],
                        next(iter(row["minimal_repair"])),
                        row["defect"]) == meta, label
                _check_malformed_scenario(row)
            else:
                assert (row["expect_failure"], row["rejected_request"]["op"],
                        row["request"]["op"],
                        row["expect_receipt"]["status"]) == meta, label
                _check_rollback_scenario(row)
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
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


def test_failure_class_coverage():
    covered = {row["expect_failure"] for row in CASES["malformed"]}
    assert covered == FAILURE_CLASSES
    loci = {next(iter(row["minimal_repair"])) for row in CASES["malformed"]}
    assert loci == {"state", "request"}
    ops = {r["op"] for s in ("happy", "boundary") for row in CASES[s]
           for r in row["requests"]}
    assert ops == set(OPS)


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(schema=2),
    lambda c: c.update(schema=True),
    lambda c: c.update(contract="jobs-idempotency"),
    lambda c: c.update(contract_base_path="/jobs/queue/v2"),
    lambda c: c.update(extra=1),
    lambda c: c.pop("rollback"),
    lambda c: c["boundary"].clear(),
    lambda c: c["happy"][0].update(sink="canonical"),
    lambda c: c["happy"][1]["expect_receipts"].pop(),
    lambda c: c["happy"][1]["expect_receipts"][0].pop("attempts"),
    lambda c: c["happy"][1]["expect_receipts"][0].update(extra=1),
    lambda c: c["happy"][1]["expect_receipts"][0].update(
        state_id="qs1:zz"),
    lambda c: c["happy"][1]["expect_receipts"][0].update(job_id="job1:"),
    lambda c: c["happy"][1]["expect_state"].update(next_seq=7),
    lambda c: c["happy"][1]["requests"][0].update(worker="w1"),
    lambda c: c["happy"][1]["requests"][1].update(op="enqueue"),
    lambda c: c["malformed"][0].update(expect_failure="internal"),
    lambda c: c["malformed"][1].update(
        minimal_repair={"request": c["malformed"][1]["request"]}),
    lambda c: c["malformed"][1]["minimal_repair"].update(state={}),
    lambda c: c["malformed"][1].update(minimal_repair={"sink": 1}),
    lambda c: c["rollback"][0].update(expect_failure="internal"),
    lambda c: c["rollback"][0]["expect_receipt"].update(state_id="qs1:"
                                                        + "0" * 64),
    lambda c: c["boundary"].append(copy.deepcopy(c["happy"][0])),
], ids=lambda f: "mut")
def test_structure_mutations_fail(mutate):
    cases = copy.deepcopy(CASES)
    mutate(cases)
    with pytest.raises((AssertionError, KeyError)):
        _validate_structure(cases)


def _bn(c, section, name):
    (row,) = [r for r in c[section] if r["name"] == name]
    return row


def _swap(c, section, a, b, *keys):
    x, y = _bn(c, section, a), _bn(c, section, b)
    for key in keys:
        x[key], y[key] = y[key], x[key]


def _regenerate(row):
    state = copy.deepcopy(row["state"])
    row["expect_receipts"] = _run(state, copy.deepcopy(row["requests"]))
    row["expect_state"] = state


def _shallow_payload(c, name):
    row = _bn(c, "boundary", name)
    row["requests"][0]["payload"] = {"k": "a"}
    _regenerate(row)


def _ack_at_expiry_minus_two(c):
    row = _bn(c, "boundary", "ack_one_ms_before_expiry")
    row["requests"][2]["now"] -= 1
    _regenerate(row)


def _claim_short_of_limit(c):
    row = _bn(c, "boundary", "claim_expiry_at_clock_limit")
    row["requests"][1]["now"] -= 1
    _regenerate(row)


def _ordering_without_tie(c):
    row = _bn(c, "happy", "claim_orders_priority_then_seq")
    row["requests"][2]["priority"] = 4
    _regenerate(row)


def _reclaim_by_same_worker(c):
    row = _bn(c, "boundary", "lease_expired_at_now_reclaimed")
    row["requests"][2]["worker"] = row["requests"][1]["worker"]
    _regenerate(row)


def _repair_widened(c):
    row = _bn(c, "malformed", "priority_out_of_range")
    row["minimal_repair"]["request"]["dedupe_key"] = "zz"


def _ack_expiry_repair_off_by_two(c):
    row = _bn(c, "malformed", "ack_at_expiry")
    row["minimal_repair"]["request"]["now"] -= 1


_CLOSURE_MUTANTS = {
    "swap_edge_names": lambda c: _swap(
        c, "boundary", "payload_depth_at_limit",
        "payload_int_at_digit_limit", "name"),
    "swap_happy_names": lambda c: _swap(
        c, "happy", "enqueue_claim_ack", "claim_nack_returns_ready",
        "name"),
    "swap_malformed_names_same_class": lambda c: _swap(
        c, "malformed", "priority_bool", "priority_out_of_range", "name"),
    "swap_malformed_requests": lambda c: _swap(
        c, "malformed", "payload_too_deep", "payload_int_past_digit_bound",
        "request", "minimal_repair"),
    "drop_lease_ms_zero": lambda c: c["malformed"].remove(
        _bn(c, "malformed", "lease_ms_zero")),
    "duplicate_unknown_op": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "unknown_op"))),
    "depth_edge_shallow": lambda c: _shallow_payload(
        c, "payload_depth_at_limit"),
    "digit_edge_shallow": lambda c: _shallow_payload(
        c, "payload_int_at_digit_limit"),
    "ack_two_ms_before_expiry": _ack_at_expiry_minus_two,
    "claim_short_of_clock_limit": _claim_short_of_limit,
    "ordering_without_tie": _ordering_without_tie,
    "reclaim_by_same_worker": _reclaim_by_same_worker,
    "repair_touches_two_fields": _repair_widened,
    "ack_expiry_repair_off_by_two": _ack_expiry_repair_off_by_two,
    "rollback_follow_up_by_non_owner": lambda c: _bn(
        c, "rollback", "lease_conflict_then_owner_ack")["request"].update(
            worker="w3"),
    "defect_text_edited": lambda c: _bn(
        c, "malformed", "priority_bool").update(defect="priority is odd"),
}


@pytest.mark.parametrize("mutant", list(_CLOSURE_MUTANTS))
@pytest.mark.parametrize("digests", [True, False],
                         ids=["with-digests", "closure-only"])
def test_closure_kills_substitution_mutants(mutant, digests, monkeypatch):
    """Every substitution is caught - and caught by the semantic
    closure ALONE, not only by the row digest table."""
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


# -- execution ---------------------------------------------------------------


@pytest.mark.parametrize("section,name", [
    (s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_receipts_and_state(section, name):
    row = _row(section, name)
    state = copy.deepcopy(row["state"])
    requests = copy.deepcopy(row["requests"])
    assert _run(state, requests) == row["expect_receipts"]
    assert requests == row["requests"]
    assert state == row["expect_state"]
    # deterministic by value: a second fresh run matches exactly
    again = copy.deepcopy(row["state"])
    assert _run(again, copy.deepcopy(row["requests"])) == (
        row["expect_receipts"])
    assert again == state


def test_tampered_pinned_receipt_is_detected():
    row = copy.deepcopy(_row("happy", "enqueue_claim_ack"))
    row["expect_receipts"][1]["attempts"] += 1
    assert _run(copy.deepcopy(row["state"]),
                copy.deepcopy(row["requests"])) != row["expect_receipts"]


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    row = _row("malformed", name)
    state, request = copy.deepcopy(row["state"]), copy.deepcopy(row["request"])
    before_s, before_r = _snap(state), _snap(request)
    with pytest.raises(QueueError) as err:
        _apply(state, request)
    assert err.value.failure_class == row["expect_failure"]
    assert err.value.code == FAILURE_MAPPING[row["expect_failure"]]
    assert err.value.code in ERROR_ENUM
    # ATOMIC: bit- and reference-identical after the rejection
    assert _snap(state) == before_s and _snap(request) == before_r
    assert state == row["state"] and request == row["request"]


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_succeeds(name):
    row = _row("malformed", name)
    (locus, value), = row["minimal_repair"].items()
    parts = {"state": row["state"], "request": row["request"]}
    parts[locus] = value
    state = copy.deepcopy(parts["state"])
    (receipt,) = _run(state, [copy.deepcopy(parts["request"])])
    assert receipt["state_id"] == state_id_for(state)


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_row("malformed", "priority_out_of_range"))
    row["request"]["priority"] = 9
    assert _fails(row["state"], row["request"]) is None


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    state = copy.deepcopy(row["state"])
    rejected = copy.deepcopy(row["rejected_request"])
    before_s, before_r = _snap(state), _snap(rejected)
    with pytest.raises(QueueError) as err:
        _apply(state, rejected)
    assert err.value.failure_class == row["expect_failure"]
    assert _snap(state) == before_s and _snap(rejected) == before_r
    assert state == row["state"]
    # the valid follow-up on the very same state object commits as pinned
    request = copy.deepcopy(row["request"])
    assert _run(state, [request]) == [row["expect_receipt"]]
    assert state == row["expect_state"] and request == row["request"]


# -- black-box engine mutants: the fixture must turn RED ---------------------

_REF = sys.modules["tests.test_t0284_queue_contract"]


def _fixture_red():
    """True when ANY row disagrees with the engine currently bound."""
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            state = copy.deepcopy(row["state"])
            try:
                got = _run(state, copy.deepcopy(row["requests"]))
            except QueueError:
                return True
            if got != row["expect_receipts"] or state != row["expect_state"]:
                return True
    for row in CASES["malformed"]:
        if _fails(row["state"], row["request"]) != row["expect_failure"]:
            return True
        (locus, value), = row["minimal_repair"].items()
        parts = {"state": row["state"], "request": row["request"], locus: value}
        if _fails(parts["state"], parts["request"]) is not None:
            return True
    for row in CASES["rollback"]:
        state = copy.deepcopy(row["state"])
        if _fails(state, row["rejected_request"]) != row["expect_failure"]:
            return True
        try:
            if _apply(state, copy.deepcopy(row["request"])) != row[
                    "expect_receipt"] or state != row["expect_state"]:
                return True
        except QueueError:
            return True
    return False


class _CapacityBeforeDedupe(QueueEngine):
    """The max_jobs capacity check moved ahead of the identical-existing
    (dedupe) check."""

    def _transition(self, work, req):
        if req["op"] == "enqueue" and len(work["jobs"]) >= MAX_JOBS:
            raise QueueError("capacity_exceeded",
                             FAILURE_MAPPING["capacity_exceeded"])
        return super()._transition(work, req)


class _SeqLimitOff(QueueEngine):
    SEQ_LIMIT = MAX_NOW + 1


class _ClockLimitOff(QueueEngine):
    CLOCK_LIMIT = MAX_NOW + 1


class _AckAtExpiry(QueueEngine):
    def _transition(self, work, req):
        if req["op"] in ("ack", "nack"):
            job = self._find(work, req["job_id"])
            if job is not None and job["lease_expires_at"] is not None:
                req = {**req, "now": min(req["now"],
                                         job["lease_expires_at"] - 1)}
        return super()._transition(work, req)


class _NackResetsAttempts(QueueEngine):
    def _transition(self, work, req):
        job = super()._transition(work, req)
        if req["op"] == "nack":
            job["attempts"] = 0
        return job


class _ReenqueueOverwrites(QueueEngine):
    def _transition(self, work, req):
        if req["op"] == "enqueue":
            job = self._find(work, job_id_for(req["dedupe_key"]))
            if job is not None:
                job.update(priority=req["priority"], payload=req["payload"])
        return super()._transition(work, req)


_ENGINE_MUTANTS = {
    "expiry_strictly_before_now": ("_claimable", lambda job, now: (
        job["status"] == "ready" or (job["status"] == "leased"
                                     and job["lease_expires_at"] < now))),
    "ordering_by_seq_only": ("sorted", lambda it, key=None: builtins_sorted(
        it, key=lambda j: j["seq"])),
    "max_attempts_six": ("MAX_ATTEMPTS", MAX_ATTEMPTS + 1),
    "seq_limit_off_by_one": ("engine", _SeqLimitOff),
    "clock_limit_off_by_one": ("engine", _ClockLimitOff),
    "ack_allowed_at_expiry": ("engine", _AckAtExpiry),
    "nack_resets_attempts": ("engine", _NackResetsAttempts),
    "reenqueue_overwrites": ("engine", _ReenqueueOverwrites),
    "capacity_before_dedupe": ("engine", _CapacityBeforeDedupe),
}
builtins_sorted = sorted


def test_reference_engine_is_green():
    assert not _fixture_red()


@pytest.mark.parametrize("mutant", list(_ENGINE_MUTANTS))
def test_engine_mutants_turn_the_fixture_red(mutant, monkeypatch):
    attr, value = _ENGINE_MUTANTS[mutant]
    if attr == "engine":
        monkeypatch.setattr(sys.modules[__name__], "QueueEngine", value)
    elif attr == "sorted":
        monkeypatch.setattr(_REF, "sorted", value, raising=False)
    else:
        monkeypatch.setattr(_REF, attr, value)
    assert _fixture_red(), mutant
