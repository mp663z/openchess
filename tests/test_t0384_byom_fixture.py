"""T0384 closed byom privacy fixture.

Pinned rows are independent of runtime results, closed by a literal name
manifest and SHA-256 of each complete row. Replays check exact typed outputs,
input immutability, deep composed-state atomicity and the valid continuation
following refusals. Fixture tampering and semantic mutants must fail assertions;
foreign exceptions are never counted as kills. All values and stores are synthetic.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests import test_t0383_byom_contract as ref

FIXTURE = Path(__file__).parent / "fixtures" / "byom" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
PINNED = [
    ("accept-send-revoke", "924c21f4704af027f141f01bf989a10e9bfa26bf6ec4b2ee9aa04c2c4a27cb27"),
    (
        "same-value-and-reregister",
        "3a8c09e549300b8b181e9952cb0bf721ddbac42fc8fa3d618137788655bee95a",
    ),
    ("changed-retention_days", "25ac7cdb65fe3239a6725bffe174087b6aa87da4b8193fed5fb41c62c8b60fe4"),
    ("changed-region", "601bfd48c135941a8d0a9b2b3bda994fe1119247e7571d866c8646d15fbda86c"),
    (
        "changed-trains_on_inputs",
        "a27ebfca72954f0627690ed7d9e10e3b699f5d9701827c0abd0b5e4dff201f27",
    ),
    ("cloud-off", "4a7ca7a218054fcb7557121d79261de6a23dd71be5ca7c4eecf43f2a92dd48f4"),
    ("sensitive", "56a352ff6249bed0aa1f58a2c14d3b538b140271737baa69d0216089cf463419"),
    ("unknown-collection", "60d5a4e4f34699c43c00784429bb707661f90b85526d41c65b05027f4f9170e8"),
    ("unaccepted", "4d1f6197a621ad8a2c018f77c8c701716aae3ec34fecb11e559daa6dc7741a85"),
    ("unknown-provider-send", "99d8f8a64877833da60d0012c1836358eb3a74afe449c0802988d87fa0a9833f"),
    ("retention-edge-0", "65c850bb2ece5ff3f5d12e9206fac7a3a600dfc0bd3ca8cced53afcaf951abc9"),
    ("retention-edge-3650", "480e3e5ff6c1024c6372b43991233b014a0b51aa328fa754b44cb1dffd304d12"),
    ("max-payload", "af6319a35044b2734bd2e737c4cff55c2c7b0e754cc1c13719a91fd4223f7e4b"),
    ("min-payload", "b5dad87d1b837b8d28f327e510097fe58a47151e605c136460144cf5264df9d6"),
    ("max-accept-refused", "e1387d18d5c434be72d91bb110a40fff2debf866571e07a962c6d3d836ee120c"),
    ("max-revoke-terminal", "aec797ced8288b375a1ee43fcaeb8316a36530943c7076a7007bcebd156b1be9"),
    ("max-same-value", "abc70df6d7052e77c653c24f923c99168eea05e731a52abdfeb03db9c4965fa5"),
    ("missing-approval", "91797a62fc33c198b3c9fb88be5e45a2cdcb3c6d443ba8f6bfd5892b96dc7ad2"),
    ("wrong-install-approval", "2f130100e254d10447428054542de7971927f4f6caf267493d86387957094b71"),
    ("wrong-provider-approval", "612a41ebdb68159a5b0df7c6523e01ee8f4c652955fb5f243f735023de3ef64d"),
    ("wrong-revision-approval", "dd800d5a86558ded410b9a2fa0b0ce7c3976820465a1721f263b63ba50063178"),
    ("stale", "af7ed221338600a79a68ad4a9cac4a17a79f60992039ef0773f7f0a05eec7922"),
    ("unknown-provider", "0a3832e5f1ed7eb8243c7cf8bce18cf907a539c8cdba2aa79902b1f4d5978555"),
    ("malformed-before-stale", "7dd1f7d4aaa13521aee88014bdefa1a7f16a51249e5f73f003a490fb11537c2e"),
    ("terms-retention_days-0", "f32211f5f5bfc756d96dc70d0198d73591b892317c8a2eb07fad00e852987a14"),
    ("terms-retention_days-1", "6cfae2869571bda26ccbe0dedb511ec576adbbbe53a851b63ace90180255b124"),
    ("terms-retention_days-2", "39d1744a11dabda9bd05103520a1782ad59f2ce9bf61eb56e8d305f532a8d110"),
    ("terms-region-0", "2e3877b75624cfbb5f13ba8779c06af5f47d501e958aceaf95c2307bfd12d7f4"),
    ("terms-region-1", "f484ba226a2689a640ac5765b95febaf0cf2970c7d5ce659d41960e936fff534"),
    ("terms-region-2", "ab7aedb524850e8b30008dcb6756fc3b4abf8685df854305c74d5f1559a1748e"),
    (
        "terms-trains_on_inputs-0",
        "2dc6a4d05b2991186854b516ba59033cac98a871348196050081cbcd071633a7",
    ),
    (
        "terms-trains_on_inputs-1",
        "128c98ff1d41f5f41c75b36db61eb7e9d46a7cc1ae3a4fd5d3882dc7b2d85edf",
    ),
    ("payload-fen-0", "942bd1a79e2269d9fd1a527f046fec84352f3dc7b38b12d120268015b88eb259"),
    ("payload-fen-1", "86d546efdde2ab6047c97e5c51d15ddcc39993a446fce567fe85d9c6b9d4918a"),
    ("payload-fen-2", "e760021a9f87f5c5d483ffeeccab2a420dc099c284fd5634fa936407129efca9"),
    ("payload-moves-0", "15c0c3b589f69a1fd0e4928e800fb17e378ede667229f02b99317df9f24b3ecd"),
    ("payload-moves-1", "865532b6612927a8f2156c76cc042f5a7f04824df4fcf83f0ca2d590910619a2"),
    ("payload-moves-2", "85ba42ab8c9fdacbe3f63c77bb3ab2f7b876060995dc5a119a5206a6ccc47ac1"),
    ("payload-moves-3", "411a67b2b11b2e51fc05ea2ca34741ccde26cfa5e7cd56e0c64f9e1219949618"),
    ("payload-task-0", "be940d720e5a89d3107a02abf208b32382bca83009bd93ed04c468874942be6a"),
    ("payload-task-1", "0f27b35402c424bef99df626f8dcf94467c84b8549a3a9a9649fa14e120b3c82"),
    ("payload-max-tokens-0", "758bdd8b095b70d22c4142303035e86903bf401d2812ec366dd650c0b8d2874f"),
    ("payload-max-tokens-1", "a1542fe8cecb4ea4cb4ff44e11a1a216f1683fd91bf67c9b6a77e22129336ca8"),
    ("payload-max-tokens-2", "0eacce2a16e73facff742cc274bfdcaeab6937a73ef540898a8e8ca7d80f595c"),
    ("forbidden-account-keys", "bd5d04437a056bf50ca0bc163383fc76437d8c60a5dc266651e8e132ef6b6a31"),
    ("forbidden-full-corpus", "c6616ba9ce35ea97a35a293fd8a3238aea00fa9cc5f195a94fda7db7ba09f787"),
    ("forbidden-sync-keys", "d4de71cdf6d09fc54f1309b5d015530e85d8bdd53985f25ba3163c1cf428e321"),
    (
        "precedence-payload-before-cloud",
        "03250bb3e6b96cabd9343b1103facdb350c0e96bf1b7bf30725b77d7aad3a8ee",
    ),
    (
        "failed-change-then-revoke",
        "318ead69d4413ff9cafb5e577ae11294850737ed64a192184918956ac90774d1",
    ),
    (
        "revoke-then-fresh-approval",
        "8eedef68824d2a9d5d1b0b80326167a11b61dec29dd77b40c818bd8142ec9938",
    ),
]


def digest(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def closure(doc):
    assert type(doc) is dict and set(doc) == {"schema", "contract", "sections", "rows"}
    assert type(doc["schema"]) is int and doc["schema"] == 1
    assert doc["contract"] == "privacy-byom"
    assert doc["sections"] == list(SECTIONS)
    assert type(doc["rows"]) is list
    assert [(r["name"], digest(r)) for r in doc["rows"]] == PINNED
    assert {row["section"] for row in doc["rows"]} == set(SECTIONS)
    failures = set()
    for row in doc["rows"]:
        assert set(row) == {"name", "section", "config", "steps"}
        assert type(row["name"]) is str and type(row["config"]) is dict
        assert type(row["steps"]) is list and row["steps"]
        for step in row["steps"]:
            assert set(step) == {"op", "request", "expect"}
            assert type(step["op"]) is str and type(step["expect"]) is dict
            failures.update([step["expect"]["failure"]] if "failure" in step["expect"] else [])
    assert failures == set(ref.FAILURE_CLASSES)


def exact(got, expected):
    assert type(got) is type(expected)
    if type(expected) is dict:
        assert {(type(k), k) for k in got} == {(type(k), k) for k in expected}
        for key in expected:
            exact(got[key], expected[key])
    elif type(expected) in (tuple, list):
        assert len(got) == len(expected)
        for left, right in zip(got, expected, strict=True):
            exact(left, right)
    else:
        assert got == expected


def world(config, binding):
    store, cloud, flags = ref._world(config.get("cloud_on", True), config.get("open_a", True))
    if binding is not None:
        store = call(binding, cloud)
        call(store.register, ref.INS, ref.P)
    store._state[(ref.INS, ref.P)] = (config.get("accepted_terms"), config.get("revision", 0))
    return store, cloud, flags


def state(context):
    store, cloud, flags = context
    return copy.deepcopy((store._state, cloud._state, flags._state))


def execute(context, op, request):
    store = context[0]
    if op == "register":
        return {"record": store.register(request["install_id"], request["provider_id"])}
    if op == "send":
        return {"verdict": store.send(request)}
    return {"record": store.transition(request)}


class ForeignCrash(Exception):
    """An implementation exception, never a semantic kill."""


def call(fn, *args):
    try:
        return fn(*args)
    except ref.ByomError:
        raise
    except Exception as exc:  # noqa: BLE001 - includes implementation AssertionError
        raise ForeignCrash(type(exc).__name__) from exc


def replay(row, binding=None):
    context = world(row["config"], binding)
    for step in row["steps"]:
        rq = copy.deepcopy(step["request"])
        before = copy.deepcopy(rq)
        prior = state(context)
        try:
            got = call(execute, context, step["op"], rq)
        except ref.ByomError as exc:
            assert type(exc) is ref.ByomError
            assert exc.failure_class in ref.FAILURE_CLASSES
            assert exc.code == ref.FAILURE_MAPPING[exc.failure_class]
            assert exc.__cause__ is None and exc.__context__ is None
            got = {"failure": exc.failure_class}
            exact(state(context), prior)
        exact(rq, before)
        exact(got, step["expect"])
        if step["op"] == "send":
            exact(state(context), prior)


def battery(binding=None):
    closure(CASES)
    for row in CASES["rows"]:
        replay(row, binding)


@pytest.mark.parametrize("row", CASES["rows"], ids=[r["name"] for r in CASES["rows"]])
def test_pinned_row(row):
    replay(row)


def test_manifest_and_green_battery():
    battery()


@pytest.mark.parametrize(
    "edit",
    [
        "drop",
        "duplicate",
        "swap",
        "outcome",
        "type",
        "request",
        "section",
        "rename",
        "extra",
        "config",
    ],
)
def test_fixture_edits_fail_closed(edit):
    doc = copy.deepcopy(CASES)
    rows = doc["rows"]
    if edit == "drop":
        rows.pop()
    elif edit == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif edit == "swap":
        rows[0], rows[1] = rows[1], rows[0]
    elif edit == "outcome":
        rows[0]["steps"][0]["expect"] = {"failure": "internal"}
    elif edit == "type":
        doc["schema"] = True
    elif edit == "request":
        rows[0]["steps"][0]["request"] = None
    elif edit == "section":
        rows[0]["section"] = "rollback"
    elif edit == "rename":
        rows[0]["name"] = "replacement"
    elif edit == "extra":
        rows[0]["unreviewed"] = True
    else:
        rows[0]["config"]["extra"] = True
    with pytest.raises(AssertionError):
        closure(doc)


class NoApproval(ref.ByomStore):
    def transition(self, request):
        request = copy.deepcopy(request)
        if request["terms"] is not None:
            request["approval"] = ref.byom_approval_for(
                request["install_id"],
                request["provider_id"],
                ref.terms_digest(request["terms"]),
                request["expected_revision"],
            )
        return super().transition(request)


class IgnoreCloud(ref.ByomStore):
    def _cloud_refusal(self, iid, cid):
        return None


class IgnoreTerms(ref.ByomStore):
    def send(self, request):
        result = super().send(request)
        result["terms_digest"] = "trm1:" + "0" * 64
        return result


class MutableRequest(ref.ByomStore):
    def send(self, request):
        result = super().send(request)
        request["payload"]["moves"] = []
        return result


class ResetRegister(ref.ByomStore):
    def register(self, iid, pid):
        self._state.pop((iid, pid), None)
        return super().register(iid, pid)


class RevokeNeedsApproval(ref.ByomStore):
    def transition(self, request):
        if request["terms"] is None:
            raise ref.ByomError("approval_missing")
        return super().transition(request)


class ChangedTermsAllowed(ref.ByomStore):
    def send(self, request):
        try:
            return super().send(request)
        except ref.ByomError as exc:
            if exc.failure_class != "terms_changed":
                raise
            return {"verdict": "allow", "terms_digest": ref.terms_digest(request["terms"])}


class RefusalWritesState(ref.ByomStore):
    def transition(self, request):
        try:
            return super().transition(request)
        except ref.ByomError:
            self._cloud._flags._state.clear()
            raise


class CrashOnly(ref.ByomStore):
    def transition(self, request):
        raise KeyError("synthetic crash")


MUTANTS = {
    "approval": NoApproval,
    "cloud": IgnoreCloud,
    "digest": IgnoreTerms,
    "request": MutableRequest,
    "register": ResetRegister,
    "revoke": RevokeNeedsApproval,
}


def test_foreign_crash_does_not_count_as_a_semantic_kill():
    with pytest.raises(ForeignCrash):
        battery(CrashOnly)


HOSTILE_NAMES = [
    "send/dict_sub",
    "send/list",
    "send/str",
    "send/none",
    "send/install_id_sub",
    "send/install_id_listsub",
    "send/install_id_dictsub",
    "send/missing_install_id",
    "send/real_key_strsub_install_id",
    "send/renamed_key_install_id",
    "send/provider_id_sub",
    "send/provider_id_listsub",
    "send/provider_id_dictsub",
    "send/missing_provider_id",
    "send/real_key_strsub_provider_id",
    "send/renamed_key_provider_id",
    "send/collection_id_sub",
    "send/collection_id_listsub",
    "send/collection_id_dictsub",
    "send/missing_collection_id",
    "send/real_key_strsub_collection_id",
    "send/renamed_key_collection_id",
    "send/terms_sub",
    "send/terms_listsub",
    "send/terms_dictsub",
    "send/missing_terms",
    "send/real_key_strsub_terms",
    "send/renamed_key_terms",
    "send/missing_payload",
    "send/real_key_strsub_payload",
    "send/renamed_key_payload",
    "send/extra_key_plain",
    "send/extra_key_eq_raises",
    "send/extra_key_hash_collide",
    "send/bytes_key",
    "send/int_key",
    "send/iid_69_ins1_'A'",
    "send/iid_68_ins1_'1'",
    "send/iid_70_ins1_'1'",
    "send/iid_70_ins1_'\\n'",
    "send/iid_69_INS1_'1'",
    "send/pid_empty",
    "send/pid_len33",
    "send/pid_upper",
    "send/pid_hyphen",
    "send/pid_newline",
    "send/pid_prefix",
    "send/pid_none",
    "send/terms_dict_sub",
    "send/terms_list",
    "send/terms_str",
    "send/terms_none",
    "send/terms_retention_days_sub",
    "send/terms_retention_days_listsub",
    "send/terms_retention_days_dictsub",
    "send/terms_missing_retention_days",
    "send/terms_real_key_strsub_retention_days",
    "send/terms_renamed_key_retention_days",
    "send/terms_region_sub",
    "send/terms_region_listsub",
    "send/terms_region_dictsub",
    "send/terms_missing_region",
    "send/terms_real_key_strsub_region",
    "send/terms_renamed_key_region",
    "send/terms_trains_on_inputs_sub",
    "send/terms_trains_on_inputs_listsub",
    "send/terms_trains_on_inputs_dictsub",
    "send/terms_missing_trains_on_inputs",
    "send/terms_real_key_strsub_trains_on_inputs",
    "send/terms_renamed_key_trains_on_inputs",
    "send/terms_extra_key_plain",
    "send/terms_extra_key_eq_raises",
    "send/terms_extra_key_hash_collide",
    "send/terms_bytes_key",
    "send/terms_int_key",
    "send/terms_days_neg",
    "send/terms_days_3651",
    "send/terms_days_true",
    "send/terms_days_float",
    "send/terms_days_str",
    "send/terms_region_upper",
    "send/terms_region_one",
    "send/terms_region_three",
    "send/terms_region_digit",
    "send/terms_region_newline",
    "send/terms_region_empty",
    "send/terms_trains_none",
    "send/terms_trains_str",
    "send/terms_trains_0",
    "transition/dict_sub",
    "transition/list",
    "transition/str",
    "transition/none",
    "transition/install_id_sub",
    "transition/install_id_listsub",
    "transition/install_id_dictsub",
    "transition/missing_install_id",
    "transition/real_key_strsub_install_id",
    "transition/renamed_key_install_id",
    "transition/provider_id_sub",
    "transition/provider_id_listsub",
    "transition/provider_id_dictsub",
    "transition/missing_provider_id",
    "transition/real_key_strsub_provider_id",
    "transition/renamed_key_provider_id",
    "transition/terms_sub",
    "transition/terms_listsub",
    "transition/terms_dictsub",
    "transition/missing_terms",
    "transition/real_key_strsub_terms",
    "transition/renamed_key_terms",
    "transition/expected_revision_sub",
    "transition/expected_revision_listsub",
    "transition/expected_revision_dictsub",
    "transition/missing_expected_revision",
    "transition/real_key_strsub_expected_revision",
    "transition/renamed_key_expected_revision",
    "transition/approval_sub",
    "transition/approval_listsub",
    "transition/approval_dictsub",
    "transition/missing_approval",
    "transition/real_key_strsub_approval",
    "transition/renamed_key_approval",
    "transition/extra_key_plain",
    "transition/extra_key_eq_raises",
    "transition/extra_key_hash_collide",
    "transition/bytes_key",
    "transition/int_key",
    "transition/iid_69_ins1_'A'",
    "transition/iid_68_ins1_'1'",
    "transition/iid_70_ins1_'1'",
    "transition/iid_70_ins1_'\\n'",
    "transition/iid_69_INS1_'1'",
    "transition/pid_empty",
    "transition/pid_len33",
    "transition/pid_upper",
    "transition/pid_hyphen",
    "transition/pid_newline",
    "transition/pid_prefix",
    "transition/pid_none",
    "transition/terms_dict_sub",
    "transition/terms_list",
    "transition/terms_str",
    "transition/terms_retention_days_sub",
    "transition/terms_retention_days_listsub",
    "transition/terms_retention_days_dictsub",
    "transition/terms_missing_retention_days",
    "transition/terms_real_key_strsub_retention_days",
    "transition/terms_renamed_key_retention_days",
    "transition/terms_region_sub",
    "transition/terms_region_listsub",
    "transition/terms_region_dictsub",
    "transition/terms_missing_region",
    "transition/terms_real_key_strsub_region",
    "transition/terms_renamed_key_region",
    "transition/terms_trains_on_inputs_sub",
    "transition/terms_trains_on_inputs_listsub",
    "transition/terms_trains_on_inputs_dictsub",
    "transition/terms_missing_trains_on_inputs",
    "transition/terms_real_key_strsub_trains_on_inputs",
    "transition/terms_renamed_key_trains_on_inputs",
    "transition/terms_extra_key_plain",
    "transition/terms_extra_key_eq_raises",
    "transition/terms_extra_key_hash_collide",
    "transition/terms_bytes_key",
    "transition/terms_int_key",
    "transition/terms_days_neg",
    "transition/terms_days_3651",
    "transition/terms_days_true",
    "transition/terms_days_float",
    "transition/terms_days_str",
    "transition/terms_region_upper",
    "transition/terms_region_one",
    "transition/terms_region_three",
    "transition/terms_region_digit",
    "transition/terms_region_newline",
    "transition/terms_region_empty",
    "transition/terms_trains_none",
    "transition/terms_trains_str",
    "transition/terms_trains_0",
    "send/cid_'col1:AAAAAA_69",
    "send/cid_'col1:aaaaaa_68",
    "send/cid_'col1:aaaaaa_70",
    "send/cid_None_0",
    "transition/rev_true",
    "transition/rev_false",
    "transition/rev_neg",
    "transition/rev_float",
    "transition/rev_str",
    "transition/rev_none",
    "transition/rev_huge",
    "transition/rev_2**63",
    "transition/approval_upper",
    "transition/approval_newline",
    "transition/approval_hex63",
    "transition/approval_hex65",
    "transition/approval_opt_prefix",
    "transition/approval_strsub",
    "transition/approval_bytes",
    "transition/approval_false",
]


@pytest.mark.parametrize("op,name,rq", ref.ROWS, ids=[o + "/" + n for o, n, _ in ref.ROWS])
def test_hostile_fixture(op, name, rq):
    assert [o + "/" + n for o, n, _ in ref.ROWS] == HOSTILE_NAMES
    context = world({}, None)
    before = state(context)
    snap = ref._snap(rq)
    ref.CALLS.clear()
    with pytest.raises(ref.ByomError) as caught:
        getattr(context[0], op)(rq)
    assert type(caught.value) is ref.ByomError
    assert caught.value.failure_class == "malformed_byom_request"
    assert caught.value.code == "malformed_request"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert ref.CALLS == []
    assert ref._snap(rq) == snap
    exact(state(context), before)


PAYLOAD_NAMES = [
    "dict_sub",
    "list",
    "str",
    "none",
    "fen_sub",
    "fen_listsub",
    "fen_dictsub",
    "missing_fen",
    "real_key_strsub_fen",
    "renamed_key_fen",
    "moves_sub",
    "moves_listsub",
    "moves_dictsub",
    "real_key_strsub_moves",
    "renamed_key_moves",
    "task_sub",
    "task_listsub",
    "task_dictsub",
    "missing_task",
    "real_key_strsub_task",
    "renamed_key_task",
    "max-tokens_sub",
    "max-tokens_listsub",
    "max-tokens_dictsub",
    "real_key_strsub_max-tokens",
    "renamed_key_max-tokens",
    "extra_key_plain",
    "extra_key_eq_raises",
    "extra_key_hash_collide",
    "bytes_key",
    "int_key",
    "envelope_payload_dictsub",
    "moves_item_strsub",
    "moves_item_eq_raises",
    "task_eq_raises",
    "fen_eq_raises",
    "tokens_bool_false",
]


@pytest.mark.parametrize("name,payload", ref.PAYLOAD_ROWS, ids=[n for n, _ in ref.PAYLOAD_ROWS])
def test_hostile_payload_fixture(name, payload):
    assert [n for n, _ in ref.PAYLOAD_ROWS] == PAYLOAD_NAMES
    context = world({}, None)
    before = state(context)
    snap = ref._snap(payload)
    ref.CALLS.clear()
    with pytest.raises(ref.ByomError) as caught:
        context[0].send(
            {
                "install_id": ref.INS,
                "provider_id": ref.P,
                "collection_id": ref.A,
                "terms": copy.deepcopy(ref.T),
                "payload": payload,
            }
        )
    assert type(caught.value) is ref.ByomError
    assert caught.value.failure_class == "payload_rejected"
    assert caught.value.code == "payload_rejected"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert ref.CALLS == [] and ref._snap(payload) == snap
    exact(state(context), before)


MUTANTS.update(terms_changed=ChangedTermsAllowed, rollback=RefusalWritesState)


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_semantic_mutants_fail_pinned_replay(name):
    with pytest.raises(AssertionError):
        battery(MUTANTS[name])


@pytest.mark.parametrize("exception", [AssertionError, KeyError])
def test_implementation_exceptions_are_foreign(exception):
    class Broken(ref.ByomStore):
        def transition(self, request):
            raise exception("implementation-only crash")

    with pytest.raises(ForeignCrash):
        battery(Broken)
