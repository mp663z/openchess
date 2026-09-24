"""T0204: atomic edit conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0203
store-atomic-edit contract. The cases execute against the
contract-derived reference in tests.test_t0203_atomic_edit_contract
(itself derived from data/contracts/atomic_edit.yaml plus the linked
node contracts) - nothing is re-implemented here. Pinned receipts
(edit id, base id, target id and the full result state) were computed
from that reference at authoring time, so any contract or derivation
drift breaks this battery. Every malformed case is discriminating:
applying ONLY its declared single-locus repair (request or base) makes
the edit commit. Rollback cases prove a rejected edit leaves the exact
supplied base and request bit- and reference-identical before a valid
follow-up on the very same base object commits as pinned.

Fixture closure: ordered per-section manifests, per-row semantic pins
and closed per-name checks over the ORIGINAL row data (an unknown name
raises), and a ROW_DIGESTS whole-row sha256 table whose key set equals
the manifests. test_closure_kills_substitution_mutants proves
substitution mutants are killed with the digest table live AND with
digests neutralized.

DESIGN CAUTION: the reference engine is derived from the same contract
document, so this fixture proves fixture/contract CONSISTENCY, not
production behavior. The later red test and runtime work must execute
these same cases against a separately implemented runtime."""

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

from tests.test_t0203_atomic_edit_contract import (  # noqa: E402
    ID_A,
    ID_B,
    ID_C,
    ID_D,
    REC_A,
    REC_B,
    REC_C,
    REC_D,
    AtomicEditEngine,
    AtomicEditError,
    _identity,
    state_id,
)
from tools.atomic_edit_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "atomic_edit"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECEIPT_FIELDS = list(_CC["record"]["fields"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
_EDIT_RE = re.compile(_CC["identifiers"]["edit_id"]["grammar"])
MWE = "malformed_edit_record"

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "base", "request", "expect"}
BAD_KEYS = {"name", "defect", "expect_failure", "base", "request",
            "minimal_repair"}
RB_KEYS = {"name", "why", "base", "request", "expect_failure", "follow_up",
           "expect"}
RECORDS = {ID_A: REC_A, ID_B: REC_B, ID_C: REC_C, ID_D: REC_D}
LETTER = {ID_A: "A", ID_B: "B", ID_C: "C", ID_D: "D"}


def _validate_receipt(receipt, label):
    assert type(receipt) is dict, label
    assert list(receipt) == RECEIPT_FIELDS, label
    assert _EDIT_RE.fullmatch(receipt["edit_id"]), label
    assert _STATE_RE.fullmatch(receipt["base_id"]), label
    assert _STATE_RE.fullmatch(receipt["target_id"]), label
    state = receipt["state"]
    assert type(state) is dict, label
    assert all(RECORDS.get(k) == v for k, v in state.items()), label
    assert receipt["target_id"] == state_id(state), label


def _validate_success_row(row, label):
    _validate_receipt(row["expect"], label)
    base = row["base"]
    assert type(base) is dict, label
    assert all(RECORDS.get(k) == v for k, v in base.items()), label
    assert row["expect"]["base_id"] == state_id(base), label


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
                _validate_success_row(row, label)
                assert row["request"]["base_id"] == \
                    row["expect"]["base_id"], label
            elif section == "malformed":
                assert set(row) == BAD_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                assert type(row["defect"]) is str and row["defect"], label
                repair = row["minimal_repair"]
                assert type(repair) is dict and len(repair) == 1, label
                (locus, value), = repair.items()
                assert locus in ("base", "request"), label
                assert value != row[locus], label
            else:
                assert set(row) == RB_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                _validate_success_row(row, label)
                assert row["follow_up"] != row["request"], label
    assert len(names) == len(set(names)), "fixture names must be unique"


# -- the closed scenario manifests -------------------------------------------
# happy/boundary: name -> (base letters, canonical operation signature,
# target letters)
HAPPY_MANIFEST = {
    "put_inserts_into_nonempty_base": ("A", "put:B", "AB"),
    "put_replaces_present_identity": ("AB", "put:B", "AB"),
    "delete_removes_present_identity": ("AB", "delete:B", "A"),
    "mixed_multi_operation_edit": ("AB", "delete:A,put:B,put:C", "BC"),
}
BOUNDARY_MANIFEST = {
    "empty_base_put": ("", "put:A", "A"),
    "empty_operations_noop": ("AB", "", "AB"),
    "delete_last_identity_to_empty": ("A", "delete:A", ""),
    "empty_base_empty_operations": ("", "", ""),
    "operation_order_reversed": ("AB", "delete:A,put:B,put:C", "BC"),
}
# malformed: name -> (failure class, repair locus, pinned defect text);
# the name is the CLOSED scenario tag
MALFORMED_MANIFEST = {
    "request_not_a_dict": ('malformed_edit_record', 'request',
        'the request is a list, not a dict'),
    "request_missing_operations": ('malformed_edit_record', 'request',
        'the request has no operations field'),
    "request_renamed_field": ('malformed_edit_record', 'request',
        'the request renames operations to operation at the same arity'),
    "request_extra_field": ('malformed_edit_record', 'request',
        'the request carries an unknown extra field'),
    "base_id_bad_grammar": ('malformed_edit_record', 'request',
        'base_id breaks the gs1 grammar'),
    "base_id_trailing_newline": ('malformed_edit_record', 'request',
        "base_id ends in a newline, which the grammar's $ accepts but a full match rejects"),
    "base_id_not_a_string": ('malformed_edit_record', 'request',
        'base_id is an int'),
    "operations_not_a_list": ('malformed_edit_record', 'request',
        'operations is a dict, not a list'),
    "operations_empty_non_list": ('malformed_edit_record', 'request',
        'operations is an empty string, not a list'),
    "operation_extra_field": ('malformed_edit_record', 'request',
        'an operation carries an extra field'),
    "operation_renamed_field": ('malformed_edit_record', 'request',
        'an operation renames record to payload at the same arity'),
    "operation_unknown_kind": ('malformed_edit_record', 'request',
        'an operation kind is upsert, not put or delete'),
    "delete_with_record": ('malformed_edit_record', 'request',
        'a delete carries a record instead of null'),
    "put_null_record": ('malformed_edit_record', 'request',
        'a put carries a null record'),
    "put_identity_mismatch": ('malformed_edit_record', 'request',
        'a put record derives a different identity than the operation names'),
    "put_wrong_digest": ('malformed_edit_record', 'request',
        'a put record has a well-formed but wrong digest'),
    "put_non_canonical_fen": ('malformed_edit_record', 'request',
        'a put record carries a parseable but non-canonical snapshot_fen (clocks 5 9)'),
    "put_record_missing_field": ('malformed_edit_record', 'request',
        'a put record has no digest field'),
    "duplicate_identity": ('malformed_edit_record', 'request',
        'two operations target the same identity'),
    "base_not_a_dict": ('malformed_edit_record', 'base',
        'the base is a list of records, not a map'),
    "base_key_mismatch": ('malformed_edit_record', 'base',
        "a base key is not its record's identity"),
    "base_record_wrong_digest": ('malformed_edit_record', 'base',
        'a base record has a well-formed but wrong digest'),
    "base_id_names_another_state": ('conflicting_base', 'request',
        'base_id is the id of a different valid state'),
    "base_differs_from_base_id": ('conflicting_base', 'base',
        'the base is missing a record the base_id covers'),
    "delete_absent_identity": ('unknown_identity', 'request',
        'a delete targets an identity absent from the base'),
}
# rollback: name -> (failure class, follow-up canonical operation signature,
# target letters)
ROLLBACK_MANIFEST = {
    "last_operation_unknown_identity": ("unknown_identity", "put:C,put:D", "ABCD"),
    "middle_operation_malformed": (MWE, "delete:A,put:C,put:D", "BCD"),
    "stale_base_id": ("conflicting_base", "delete:B", "A"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST, "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

# content binding: canonical sha256 of every row (base, request, repair,
# defect, follow-up and pinned receipt). Any edit to a row must update
# this table in the same change.
ROW_DIGESTS = {
    "happy:put_inserts_into_nonempty_base":
        "b46a2c6c355d94b27c0480fb7c9e9edc3b5e2c191125a636dafc241c114b3f93",
    "happy:put_replaces_present_identity":
        "40fff80fc6ce6377b581bf7ff0bcb0522578c3f35b36409bf2dcde6955f1ab3a",
    "happy:delete_removes_present_identity":
        "43fb2bdda15a65f4afcfac6653db19e64c34c025125aae0bffea7b9e30e3c077",
    "happy:mixed_multi_operation_edit":
        "280c067a7f4ece21f49777a0fd1912945e42d6efe8e23461d86036dae2505f11",
    "boundary:empty_base_put":
        "318a28dc0c0a178d1bdf4733f167f63dd98775d6e3eb2c4d7001e69552549df2",
    "boundary:empty_operations_noop":
        "86b1c2bcb64ca119c216f2123615402bfe6973cb64666e7cddf05b4345c17af2",
    "boundary:delete_last_identity_to_empty":
        "547c8195a5b93a14eda4491936a408016122aac4799c7b13d0afd72a1b9c79c1",
    "boundary:empty_base_empty_operations":
        "6e34b516249eb97d403b9c1f611a7e57584d1f8c4c133f81d0a782d4347a5d9f",
    "boundary:operation_order_reversed":
        "7faa3be542ea00f899255e6c00daf4f6455af259ea314fffdece9bae99417dd9",
    "malformed:request_not_a_dict":
        "10ae18a624382772d1694b72465d7644fc8a24409ae972f40cd2e645a990eff7",
    "malformed:request_missing_operations":
        "1c1448611f9539552cae43b02717af25982e93e67eab9cee4a8dbf85a44c2d5f",
    "malformed:request_renamed_field":
        "2f47d0caed18c34e5bc25d6483f77b35d94b5f96280962104643fa55782e86c4",
    "malformed:request_extra_field":
        "1dd645a7d0b657afb0027251138472a842869999f624507cddf54d486ce2c88b",
    "malformed:base_id_bad_grammar":
        "0b08b43b2102346e8e5fa26b90b94bfc67734facdf4002491d61cf4ceda085b2",
    "malformed:base_id_trailing_newline":
        "b3eb5b6c6a13ffc35c3fc0d589ea9a899f818e6f83fa1ef6908b0ee81a146a0e",
    "malformed:base_id_not_a_string":
        "d80952fc5469ba716a2c9452300cab32d7c0790362456fa840a2a4585899f1c6",
    "malformed:operations_not_a_list":
        "5ad9a985f6214a37e46569879269847ae867440acb3caad6bc7f33c94fb853a3",
    "malformed:operations_empty_non_list":
        "f2d82442deb674aca7a83076a75739dc9d9dbff6fdf8a65d50fa06a4c709d451",
    "malformed:operation_extra_field":
        "8217aa53781f167b7313277fa623afb8378b1c9ec0aaed89cb46e1c2330e1f5e",
    "malformed:operation_renamed_field":
        "7a95234d69aa57b481d821e8a5e6dd0f7f70dd8756d64dbe335abc04aa162982",
    "malformed:operation_unknown_kind":
        "549d2f9a419d3bdea7d7d069b84fb23ca488aa8b9b6e0fb81d5f3f51afc123c3",
    "malformed:delete_with_record":
        "e4ce6a96edab1282b7a3451ca0f2634aa800b7fc880e1d56e729bdaee19bf3cf",
    "malformed:put_null_record":
        "245dc849429dcbb09488af0123ff5666ed225bb54542a643dc45f36b6212f982",
    "malformed:put_identity_mismatch":
        "3379390f19d3952826e9b9ee43f00111b3e77ef99fb95cf64dd3b6ac90c528d1",
    "malformed:put_wrong_digest":
        "cb3c19bff2199ad8f72f8bd026e2cd9b0a0ccb8915d81942286200fbfa095cb2",
    "malformed:put_non_canonical_fen":
        "36c2d77b6a9b764ce9e91814ce123ebeda59e3ffc24713dd5b07ea89da1fec40",
    "malformed:put_record_missing_field":
        "f18efe9c26b74a644dfbcbc03ad9bdb0414287a5bd6b99044d4e8ba68f05f6a3",
    "malformed:duplicate_identity":
        "461fdcb7ca5b043365046591c5d317ca46389071b5c514c70cd7d50371c2ce6a",
    "malformed:base_not_a_dict":
        "af025962105ece8d17d3a133353a708a6c28a4401f0be1823cd7d75c7771ec76",
    "malformed:base_key_mismatch":
        "ada4d96f58d5e243d9d7716f3b9f9ed22aad9afdfd6a4ff8158ed94978b44dfd",
    "malformed:base_record_wrong_digest":
        "50db0a463b0688e81a105920220c767b126d7923bdda55d468c6192b2f676d35",
    "malformed:base_id_names_another_state":
        "86178b288064cd658b39255784efe0bea70c83a188392dcefb01f5489e88952c",
    "malformed:base_differs_from_base_id":
        "defc950029b42fe0bac4936a15d1fe204f4a1f38cd162ff7cf57b645c393c78f",
    "malformed:delete_absent_identity":
        "059860cc49171ca53094510937f6146d242f6fec6ab3fbf652190df7be8fd53d",
    "rollback:last_operation_unknown_identity":
        "42757cf8e74c51d6f80a9d8cb30719260d9ba866908820b44ed0894c78c44eef",
    "rollback:middle_operation_malformed":
        "0c6d79272b102b15e2a6be15710433b468b4aca3a48e23b21801ed55c93d70b2",
    "rollback:stale_base_id":
        "19298bca4675a2276cb85b2ad535498b3c400bb6ca2cbb320dbaf959a6a2f872",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _letters(state):
    return "".join(sorted(LETTER[k] for k in state))


def _signature(operations):
    return ",".join(sorted(f"{op['kind']}:{LETTER[op['identity']]}"
                           for op in operations))


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


def _commits(request, base):
    try:
        AtomicEditEngine().apply(copy.deepcopy(request), copy.deepcopy(base))
    except AtomicEditError:
        return False
    return True


# the shared valid edit every malformed row is one locus away from
BASE_AB = {ID_A: REC_A, ID_B: REC_B}
GOOD_OPS = [{"kind": "put", "identity": ID_C, "record": REC_C},
            {"kind": "delete", "identity": ID_A, "record": None}]
GOOD_REQ = {"base_id": state_id(BASE_AB), "operations": GOOD_OPS}


def _wrong_digest(rec, other):
    return (other != rec and set(other) == set(rec)
            and _diff_paths(rec, other) == {("digest",)}
            and re.fullmatch(r"pdv1:[0-9a-f]{64}", other["digest"]))


def _check_op_edit(name, ops):
    """Operation-level tags: EXACTLY one operation differs from the good
    edit (or one is added), in exactly the tagged way."""
    if name == "duplicate_identity":
        assert ops[:2] == GOOD_OPS and len(ops) == 3, name
        assert ops[2]["identity"] in {o["identity"] for o in GOOD_OPS}
        return
    assert len(ops) == 2, name
    diffs = [i for i in range(2) if ops[i] != GOOD_OPS[i]]
    assert len(diffs) == 1, name
    i = diffs[0]
    bad, good = ops[i], GOOD_OPS[i]
    paths = _diff_paths(good, bad)
    if name == "operation_extra_field":
        assert paths == {("force",)}, name
    elif name == "operation_renamed_field":
        # exactly one key renamed, same arity, same value
        assert len(bad) == len(good) and paths == {("record",), ("payload",)}
        assert bad["payload"] == good["record"], name
    elif name == "operation_unknown_kind":
        assert paths == {("kind",)} and bad["kind"] not in ("put", "delete")
    elif name == "delete_with_record":
        assert good["kind"] == "delete" and paths == {("record",)}, name
        assert bad["record"] in RECORDS.values(), name
    elif name == "put_null_record":
        assert good["kind"] == "put" and bad["record"] is None, name
    elif name == "put_identity_mismatch":
        assert good["kind"] == "put" and bad["record"] in RECORDS.values()
        assert _identity(bad["record"]) != bad["identity"], name
    elif name == "put_wrong_digest":
        assert _wrong_digest(good["record"], bad["record"]), name
    elif name == "put_non_canonical_fen":
        assert paths == {("record", "snapshot_fen")}, name
        fen, canon = bad["record"]["snapshot_fen"], good["record"]["snapshot_fen"]
        assert fen.split()[:4] == canon.split()[:4] and fen != canon, name
    elif name == "put_record_missing_field":
        assert set(good["record"]) - set(bad["record"]) == {"digest"}, name
        assert all(bad["record"][k] == good["record"][k]
                   for k in bad["record"]), name
    elif name == "delete_absent_identity":
        assert good["kind"] == bad["kind"] == "delete", name
        assert paths == {("identity",)} and bad["identity"] in RECORDS
        assert bad["identity"] not in BASE_AB, name
    else:
        raise AssertionError(name)


def _check_malformed_scenario(row):
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus back to the shared good edit."""
    name = row["name"]
    base, request = row["base"], row["request"]
    (locus, fix), = row["minimal_repair"].items()
    if locus == "base":
        assert fix == BASE_AB and request == GOOD_REQ, name
        if name == "base_not_a_dict":
            assert type(base) is list and base == list(BASE_AB.values())
        elif name == "base_key_mismatch":
            assert list(base.values()) == list(BASE_AB.values()), name
            assert list(base) != list(BASE_AB) and set(base) <= set(RECORDS)
        elif name == "base_record_wrong_digest":
            assert set(base) == set(BASE_AB) and base[ID_A] == REC_A, name
            assert _wrong_digest(REC_B, base[ID_B]), name
        elif name == "base_differs_from_base_id":
            assert base == {ID_A: REC_A}, name
            assert state_id(base) != request["base_id"], name
        else:
            raise AssertionError(name)
        return
    assert locus == "request" and fix == GOOD_REQ and base == BASE_AB, name
    if name == "request_not_a_dict":
        assert request == [GOOD_REQ["base_id"], GOOD_OPS], name
    elif name == "request_missing_operations":
        assert request == {"base_id": GOOD_REQ["base_id"]}, name
    elif name == "request_renamed_field":
        # exactly one key renamed, same arity, same value
        assert len(request) == len(GOOD_REQ), name
        assert _diff_paths(GOOD_REQ, request) == {("operations",),
                                                  ("operation",)}, name
        assert request["operation"] == GOOD_OPS, name
    elif name == "request_extra_field":
        assert request == {**GOOD_REQ, "force": True}, name
    elif name == "base_id_bad_grammar":
        assert request["operations"] == GOOD_OPS, name
        assert request["base_id"] == GOOD_REQ["base_id"].upper(), name
    elif name == "base_id_trailing_newline":
        assert request["operations"] == GOOD_OPS, name
        assert request["base_id"] == GOOD_REQ["base_id"] + "\n", name
        assert _STATE_RE.match(request["base_id"]), name
    elif name == "operations_empty_non_list":
        assert request == {**GOOD_REQ, "operations": ""}, name
    elif name == "base_id_not_a_string":
        assert set(request) == set(GOOD_REQ), name
        assert type(request["base_id"]) is int, name
    elif name == "operations_not_a_list":
        assert request["base_id"] == GOOD_REQ["base_id"], name
        assert type(request["operations"]) is dict, name
    elif name == "base_id_names_another_state":
        assert request["operations"] == GOOD_OPS, name
        assert request["base_id"] == state_id({ID_A: REC_A}), name
    else:
        assert set(request) == set(GOOD_REQ), name
        assert request["base_id"] == GOOD_REQ["base_id"], name
        _check_op_edit(name, request["operations"])


def _check_success_edges(rows):
    """Each happy/boundary row REALIZES its scenario over the original
    data - rows with identical signatures are still told apart."""
    r = rows["put_replaces_present_identity"]
    assert r["expect"]["target_id"] == r["expect"]["base_id"]
    assert r["request"]["operations"][0]["identity"] in r["base"]
    r = rows["put_inserts_into_nonempty_base"]
    assert r["request"]["operations"][0]["identity"] not in r["base"]
    r = rows["empty_operations_noop"]
    assert r["request"]["operations"] == [] and r["expect"]["state"] == \
        r["base"] and r["expect"]["target_id"] == r["expect"]["base_id"]
    assert rows["delete_last_identity_to_empty"]["expect"]["target_id"] == \
        state_id({})
    r = rows["empty_base_empty_operations"]
    assert r["base"] == {} and r["request"]["operations"] == []
    mixed = rows["mixed_multi_operation_edit"]
    rev = rows["operation_order_reversed"]
    assert rev["request"]["operations"] == \
        mixed["request"]["operations"][::-1]
    assert len(rev["request"]["operations"]) > 1
    assert rev["expect"] == mixed["expect"]


def _check_rollback_scenario(row):
    name = row["name"]
    ops, follow = row["request"]["operations"], row["follow_up"]["operations"]
    assert row["base"] == BASE_AB, name
    if name == "last_operation_unknown_identity":
        assert ops[:-1] == follow and ops[-1]["kind"] == "delete", name
        assert ops[-1]["identity"] not in RECORDS, name
        assert row["request"]["base_id"] == row["follow_up"]["base_id"]
    elif name == "middle_operation_malformed":
        assert len(ops) == len(follow) == 3, name
        assert _diff_paths(follow, ops) == {(1, "record", "digest")}, name
        assert ops[0] == follow[0] and ops[2] == follow[2], name
    elif name == "stale_base_id":
        assert ops == follow, name
        assert row["request"]["base_id"] != state_id(row["base"]), name
        assert _STATE_RE.fullmatch(row["request"]["base_id"]), name
    else:
        raise AssertionError(name)


def _validate_closure(cases):
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == list(manifest), section
        for row in rows:
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS.get(label) == _row_digest(row), label
            meta = manifest[row["name"]]
            if section in ("happy", "boundary"):
                assert (_letters(row["base"]),
                        _signature(row["request"]["operations"]),
                        _letters(row["expect"]["state"])) == meta, label
            elif section == "malformed":
                assert (row["expect_failure"],
                        next(iter(row["minimal_repair"])),
                        row["defect"]) == meta, label
                _check_malformed_scenario(row)
            else:
                assert (row["expect_failure"],
                        _signature(row["follow_up"]["operations"]),
                        _letters(row["expect"]["state"])) == meta, label
                _check_rollback_scenario(row)
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    _check_success_edges({r["name"]: r for s in ("happy", "boundary")
                          for r in cases[s]})


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
    lambda c: c.update(contract="store-wal"),
    lambda c: c.update(contract_base_path="/store/atomic-edit/v2"),
    lambda c: c.update(extra=1),
    lambda c: c.pop("rollback"),
    lambda c: c["boundary"].clear(),
    lambda c: c["happy"][0].update(extra=1),
    lambda c: c["happy"][0]["expect"].pop("state"),
    lambda c: c["happy"][0]["expect"].update(edit_id="ae1:zz"),
    lambda c: c["happy"][0]["expect"].update(target_id=state_id({})),
    lambda c: c["happy"][1]["expect"].update(
        base_id=c["happy"][1]["expect"]["target_id"][:-1] + "0"),
    lambda c: c["happy"][0]["expect"]["state"].update(x=1),
    lambda c: c["happy"][0]["base"].update(x=1),
    lambda c: c["malformed"][0].update(expect_failure="internal"),
    lambda c: c["malformed"][0].update(minimal_repair={"sink": 1}),
    lambda c: c["malformed"][1].update(
        minimal_repair={"request": c["malformed"][1]["request"]}),
    lambda c: c["malformed"][0]["minimal_repair"].update(base={}),
    lambda c: c["rollback"][0].update(
        follow_up=c["rollback"][0]["request"]),
    lambda c: c["rollback"][0].pop("follow_up"),
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
    assert loci == {"base", "request"}
    assert {row["expect_failure"] for row in CASES["rollback"]} == \
        FAILURE_CLASSES


# -- execution ---------------------------------------------------------------


def _apply(request, base):
    return AtomicEditEngine().apply(request, base)


def _names(section):
    return [row["name"] for row in CASES[section]]


def _row(section, name):
    (row,) = [r for r in CASES[section] if r["name"] == name]
    return row


def _assert_fresh(receipt, before):
    """No container in the result state is an input container."""
    held = {i for i, kind in before if kind in (dict, list)}
    fresh = [i for i, kind in _deep_ids(receipt["state"], [])
             if kind in (dict, list)]
    assert fresh and not any(i in held for i in fresh)


def _deep_ids(obj, out):
    out.append((id(obj), type(obj)))
    if type(obj) is dict:
        for value in obj.values():
            _deep_ids(value, out)
    elif type(obj) is list:
        for value in obj:
            _deep_ids(value, out)
    return out


@pytest.mark.parametrize("section,name", [
    (s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_receipts(section, name):
    row = _row(section, name)
    base, request = copy.deepcopy(row["base"]), copy.deepcopy(row["request"])
    before = _deep_ids(base, []) + _deep_ids(request, [])
    receipt = _apply(request, base)
    assert receipt == row["expect"]
    # inputs preserved by value and reference; the result is fresh
    assert base == row["base"] and request == row["request"]
    assert _deep_ids(base, []) + _deep_ids(request, []) == before
    _assert_fresh(receipt, before)
    # deterministic, and chaining from the target is a valid base
    assert _apply(copy.deepcopy(request), copy.deepcopy(base)) == receipt
    again = _apply({"base_id": receipt["target_id"], "operations": []},
                   copy.deepcopy(receipt["state"]))
    assert again["target_id"] == receipt["target_id"]


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    row = _row("malformed", name)
    base, request = copy.deepcopy(row["base"]), copy.deepcopy(row["request"])
    with pytest.raises(AtomicEditError) as err:
        _apply(request, base)
    assert err.value.failure_class == row["expect_failure"]
    assert err.value.code == FAILURE_MAPPING[row["expect_failure"]]
    assert err.value.code in ERROR_ENUM
    assert base == row["base"] and request == row["request"]


def _apply_repair(row):
    (locus, value), = row["minimal_repair"].items()
    parts = {"base": row["base"], "request": row["request"]}
    parts[locus] = value
    return parts


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_is_discriminating(name):
    parts = _apply_repair(_row("malformed", name))
    receipt = _apply(copy.deepcopy(parts["request"]),
                     copy.deepcopy(parts["base"]))
    _validate_receipt(receipt, name)
    assert _signature(parts["request"]["operations"]) == "delete:A,put:C"
    assert _letters(receipt["state"]) == "BC"


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_row("malformed", "put_wrong_digest"))
    row["request"] = row["minimal_repair"]["request"]
    with pytest.raises(pytest.fail.Exception), \
            pytest.raises(AtomicEditError):
        _apply(copy.deepcopy(row["request"]), copy.deepcopy(row["base"]))


def test_tampered_pinned_receipt_is_detected():
    row = copy.deepcopy(_row("happy", "mixed_multi_operation_edit"))
    row["expect"]["state"].pop(ID_C)
    receipt = _apply(copy.deepcopy(row["request"]), copy.deepcopy(row["base"]))
    assert receipt != row["expect"]
    assert _commits(row["request"], row["base"])


# -- rollback ----------------------------------------------------------------


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    base, request = copy.deepcopy(row["base"]), copy.deepcopy(row["request"])
    before = _deep_ids(base, []) + _deep_ids(request, [])
    with pytest.raises(AtomicEditError) as err:
        _apply(request, base)
    assert err.value.failure_class == row["expect_failure"]
    # bit-identical AND reference-identical: nothing was committed
    assert base == row["base"] and request == row["request"]
    assert _deep_ids(base, []) + _deep_ids(request, []) == before
    # the valid follow-up on the very same base object commits as pinned
    follow = copy.deepcopy(row["follow_up"])
    held = _deep_ids(base, []) + _deep_ids(follow, [])
    receipt = _apply(follow, base)
    assert receipt == row["expect"]
    _assert_fresh(receipt, held)
    assert base == row["base"]


# -- closure mutants ---------------------------------------------------------


def _bn(c, section, name):
    (row,) = [r for r in c[section] if r["name"] == name]
    return row


def _swap(c, section, a, b, *keys):
    x, y = _bn(c, section, a), _bn(c, section, b)
    for key in keys:
        x[key], y[key] = y[key], x[key]


def _regenerate(c, section, name):
    row = _bn(c, section, name)
    row["expect"] = _apply(copy.deepcopy(row["request"]),
                           copy.deepcopy(row["base"]))


def _reversed_is_mixed_regenerated(c):
    """Replace the reversed-order edit with the mixed edit itself and
    regenerate its pin, so only the scenario semantics can tell."""
    row = _bn(c, "boundary", "operation_order_reversed")
    row["request"] = copy.deepcopy(
        _bn(c, "happy", "mixed_multi_operation_edit")["request"])
    _regenerate(c, "boundary", "operation_order_reversed")


def _insert_is_replace_regenerated(c):
    row = _bn(c, "happy", "put_inserts_into_nonempty_base")
    row["base"] = {ID_A: REC_A, ID_B: REC_B}
    row["request"]["base_id"] = state_id(row["base"])
    _regenerate(c, "happy", "put_inserts_into_nonempty_base")


_CLOSURE_MUTANTS = {
    "reversed_is_mixed_regenerated": _reversed_is_mixed_regenerated,
    "insert_is_replace_regenerated": _insert_is_replace_regenerated,
    "swap_happy_names": lambda c: _swap(
        c, "happy", "put_inserts_into_nonempty_base",
        "put_replaces_present_identity", "name"),
    "swap_digest_tags": lambda c: _swap(
        c, "malformed", "put_wrong_digest", "put_record_missing_field",
        "request"),
    "swap_base_tags": lambda c: _swap(
        c, "malformed", "base_key_mismatch", "base_record_wrong_digest",
        "base"),
    "swap_conflict_tags": lambda c: _swap(
        c, "malformed", "base_id_names_another_state",
        "base_id_bad_grammar", "request"),
    "drop_duplicate_identity": lambda c: c["malformed"].remove(
        _bn(c, "malformed", "duplicate_identity")),
    "duplicate_request_extra_field": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "request_extra_field"))),
    "rollback_follow_up_swapped": lambda c: _swap(
        c, "rollback", "last_operation_unknown_identity",
        "middle_operation_malformed", "follow_up", "expect"),
    "unknown_name": lambda c: _bn(c, "malformed", "put_null_record").update(
        name="put_none_record"),
}


@pytest.mark.parametrize("mutant", list(_CLOSURE_MUTANTS))
@pytest.mark.parametrize("digests", [True, False],
                         ids=["with-digests", "closure-only"])
def test_closure_kills_substitution_mutants(mutant, digests, monkeypatch):
    """Every substitution is caught - and caught by the semantic closure
    ALONE, not only by the row digest table."""
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
