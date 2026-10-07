"""T0375 Privacy/local model/fixture.

A closed fixture for the T0374 local-model contract
(`tests/fixtures/local_model/cases.json`). Rows are scenarios replayed in order on
one fresh world (the T0374 reference `LocalModels`): happy, boundary, malformed
(every failure class, each with a minimal repair) and rollback (a rejected request
leaves model state untouched and the valid follow-up behaves as pinned).

Pinned results come from the T0374 reference. The fixture is closed by name
manifest, per-row sha256 digest and a replay checked against the pinned results,
so a substituted, swapped, dropped or retyped row fails on its own. Hostile-type
variants are derived from every accepted request. Reference mutants and fixture
edits must each turn the fixture red.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from tests import test_t0374_local_model_contract as _ref  # noqa: E402

ModelError = _ref.ModelError
LocalModels = _ref.LocalModels
opt_in_for = _ref.opt_in_for
FAILURE_CLASSES = set(_ref.FAILURE_CLASSES)
FAILURE_MAPPING = dict(_ref.FAILURE_MAPPING)
FIXTURE = Path(__file__).parent / "fixtures" / "local_model" / "cases.json"
CONTRACT_DOC = yaml.safe_load(_ref.CONTRACT.read_text())["contract"]
MAX_REV = 2**63 - 1
INS = "ins1:" + "1" * 64
INS2 = "ins1:" + "2" * 64
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "steps"}
BAD_KEYS = {"name", "defect", "expect_failure", "setup", "request", "minimal_repair"}
RB_KEYS = {"name", "why", "setup", "rejected", "expect_failure", "follow_up"}
STEP_OPS = {"register", "verdict", "transition", "record", "seed"}


def world():
    return LocalModels()


def _state_of(sw):
    return copy.deepcopy(sw._state)


# ---- decoding of values JSON cannot carry --------------------------------------------


def _dec(o):
    if isinstance(o, dict) and "$t" in o:
        kind = o["$t"]
        if kind == "bytes":
            return bytes.fromhex(o["hex"])
        if kind == "int":
            return o["pow"][0] ** o["pow"][1]
        if kind == "dict":
            return {_dec(k): _dec(v) for k, v in o["items"]}
        raise AssertionError(kind)
    if isinstance(o, dict):
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(x) for x in o]
    return o


RAW = json.loads(FIXTURE.read_text())
CASES = _dec(RAW)


# ---- replay --------------------------------------------------------------------------


def exec_step(sw, step):
    op, rq = step["op"], step.get("request")
    try:
        if op == "register":
            out = {"record": sw.register(rq["install_id"])}
        elif op == "verdict":
            out = {"verdict": sw.verdict(copy.deepcopy(rq))}
        elif op == "transition":
            out = {"record": sw.transition(copy.deepcopy(rq))}
        elif op == "record":
            out = {"record": sw.record(rq["install_id"])}
        elif op == "seed":
            sw._state[rq["install_id"]] = (rq["large_model"], rq["revision"])
            out = {"seeded": True}
        else:
            raise AssertionError(op)
    except ModelError as exc:
        assert type(exc) is ModelError and exc.code == FAILURE_MAPPING[exc.failure_class]
        out = {"failure": exc.failure_class}
    return out


def replay(cases, factory):
    """Every mismatch between the pinned results and the world under test."""
    bad = []
    for section in ("happy", "boundary"):
        for row in cases[section]:
            sw = factory()
            for i, step in enumerate(row["steps"]):
                if exec_step(sw, step) != step["expect"]:
                    bad.append((section, row["name"], i))
    for row in cases["malformed"]:
        sw = factory()
        for step in row["setup"]:
            exec_step(sw, step)
        if exec_step(sw, row["request"]) != {"failure": row["expect_failure"]}:
            bad.append(("malformed", row["name"], "rejects"))
        sw = factory()
        for step in row["minimal_repair"]["setup"]:
            exec_step(sw, step)
        if "failure" in exec_step(sw, row["minimal_repair"]["request"]):
            bad.append(("malformed", row["name"], "repair"))
    for row in cases["rollback"]:
        sw = factory()
        for step in row["setup"]:
            exec_step(sw, step)
        before = _state_of(sw)
        if exec_step(sw, row["rejected"]) != {"failure": row["expect_failure"]}:
            bad.append(("rollback", row["name"], "rejects"))
        if _state_of(sw) != before:
            bad.append(("rollback", row["name"], "state"))
        if exec_step(sw, row["follow_up"]) != row["follow_up"]["expect"]:
            bad.append(("rollback", row["name"], "follow_up"))
    return bad


# ---- structure and closure -----------------------------------------------------------


def _is_step(step, with_expect):
    return (
        type(step) is dict
        and set(step) == ({"op", "request", "expect"} if with_expect else {"op", "request"})
        and step["op"] in STEP_OPS
    )


def validate_structure(cases):
    assert type(cases) is dict and set(cases) == TOP_KEYS
    assert cases["schema"] == 1
    assert cases["contract"] == CONTRACT_DOC["id"] == "privacy-local-model"
    assert cases["contract_base_path"] == CONTRACT_DOC["versioning"]["base_path"]
    for section in SECTIONS:
        rows = cases[section]
        assert type(rows) is list and rows
        names = [r["name"] for r in rows]
        assert len(set(names)) == len(names), section
    for section in ("happy", "boundary"):
        for row in cases[section]:
            assert set(row) == OK_KEYS and row["why"]
            assert row["steps"] and all(_is_step(s, True) for s in row["steps"])
    for row in cases["malformed"]:
        assert set(row) == BAD_KEYS and row["defect"]
        assert row["expect_failure"] in FAILURE_CLASSES
        assert all(_is_step(s, False) for s in row["setup"])
        assert _is_step(row["request"], False)
        repair = row["minimal_repair"]
        assert set(repair) == {"setup", "request"}
        assert all(_is_step(s, False) for s in repair["setup"])
        assert _is_step(repair["request"], False)
        assert repair["request"]["op"] == row["request"]["op"]
    for row in cases["rollback"]:
        assert set(row) == RB_KEYS and row["why"]
        assert row["expect_failure"] in FAILURE_CLASSES
        assert all(_is_step(s, False) for s in row["setup"])
        assert _is_step(row["rejected"], False)
        assert _is_step(row["follow_up"], True)


def _row_digest(raw_row):
    body = json.dumps(raw_row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()


MANIFEST = {
    "happy": [
        "register_defaults_off_claims_valid",
        "small_tier_is_available_in_both_modes",
        "turn_on_with_bound_opt_in_opens_large_and_suspends_claims",
        "turn_off_needs_no_opt_in_and_restores_claims",
        "same_value_is_a_noop",
        "turn_on_again_needs_a_fresh_opt_in",
        "installs_are_independent",
        "reregister_keeps_state_and_claims",
    ],
    "boundary": [
        "commit_just_below_max_reaches_max",
        "turn_on_at_max_is_revision_exhausted",
        "turn_off_at_max_is_revision_exhausted",
        "same_value_at_max_is_a_noop",
        "unapproved_turn_on_at_max_is_opt_in_missing",
        "stale_before_exhaustion_at_max",
        "max_revision_is_well_formed_and_stale",
        "opt_in_is_bound_to_the_revision",
        "opt_in_is_bound_to_the_install",
        "opt_in_preimage_known_answer",
        "large_is_off_for_an_unknown_install",
        "well_formed_opt_in_on_turn_off_is_ignored",
        "turn_off_applies_before_the_next_verdict",
        "verdict_is_read_at_invocation_time",
        "precedence_egress_then_host_then_mode",
        "web_and_server_never_run_a_model",
        "a_non_local_destination_is_refused_whatever_the_mode",
        "claims_are_suspended_exactly_while_large_is_on",
    ],
    "malformed": [
        "transition_extra_key",
        "transition_missing_install_id",
        "transition_missing_large_model",
        "transition_missing_expected_revision",
        "transition_missing_opt_in",
        "transition_request_not_a_dict",
        "transition_request_is_a_list",
        "transition_non_str_key",
        "install_id_uppercase_hex",
        "install_id_short_hex",
        "install_id_long_hex",
        "install_id_wrong_prefix",
        "install_id_trailing_newline",
        "install_id_empty",
        "install_id_not_hex",
        "install_id_int",
        "install_id_bytes",
        "install_id_none",
        "large_model_uppercase",
        "large_model_true_str",
        "large_model_none",
        "large_model_bytes",
        "large_model_empty",
        "large_model_int_one",
        "large_model_bool_true",
        "large_model_padded",
        "large_model_large_on_token",
        "expected_revision_negative",
        "expected_revision_bool_false",
        "expected_revision_float_zero",
        "expected_revision_str_zero",
        "expected_revision_two_pow_63",
        "expected_revision_huge",
        "expected_revision_none",
        "opt_in_bad_prefix",
        "opt_in_short",
        "opt_in_uppercase",
        "opt_in_int",
        "opt_in_bytes",
        "opt_in_trailing_newline",
        "opt_in_empty",
        "malformed_opt_in_on_turn_off",
        "malformed_opt_in_precedes_unknown",
        "verdict_extra_key",
        "verdict_missing_install_id",
        "verdict_missing_tier",
        "verdict_missing_host",
        "verdict_missing_destination",
        "verdict_request_not_a_dict",
        "verdict_install_id_bad",
        "tier_uppercase",
        "tier_medium",
        "tier_none",
        "tier_bytes",
        "tier_empty",
        "tier_padded",
        "tier_int",
        "host_uppercase",
        "host_mobile",
        "host_none",
        "host_bytes",
        "host_empty",
        "host_padded",
        "host_int",
        "destination_uppercase",
        "destination_edge",
        "destination_none",
        "destination_bytes",
        "destination_empty",
        "destination_padded",
        "destination_bool",
        "malformed_tier_precedes_egress",
        "register_bad_id",
        "register_non_str_id",
        "unknown_install_turn_on",
        "unknown_install_turn_off",
        "unknown_precedes_stale",
        "stale_revision_transition",
        "stale_revision_ahead",
        "stale_revision_behind",
        "stale_revision_same_value_target",
        "stale_precedes_opt_in",
        "opt_in_missing_none",
        "opt_in_foreign_install",
        "opt_in_stale_bound",
        "opt_in_preimage_target_on",
        "opt_in_preimage_target_off",
        "opt_in_preimage_colon_separator",
        "opt_in_preimage_trailing_separator",
        "opt_in_preimage_leading_zero_revision",
        "opt_in_preimage_plus_revision",
        "opt_in_preimage_uppercase_target",
        "opt_in_preimage_utf16",
        "egress_small_cloud",
        "egress_large_cloud_with_large_on",
        "egress_beats_host",
        "egress_beats_large_off",
        "host_web_small",
        "host_server_large_with_large_on",
        "host_beats_large_off",
        "large_model_off_desktop",
        "large_model_off_after_turn_off",
        "large_model_off_unregistered_install",
        "turn_on_at_max_revision",
        "turn_off_at_max_revision",
    ],
    "rollback": [
        "malformed_leaves_state",
        "unknown_install_leaves_state",
        "stale_revision_leaves_state",
        "opt_in_missing_leaves_state",
        "egress_requested_leaves_state",
        "host_not_desktop_leaves_state",
        "large_model_off_leaves_state",
        "revision_exhausted_leaves_state",
        "rejected_turn_on_does_not_consume_the_opt_in",
    ],
}

ROW_DIGESTS = {
    "register_defaults_off_claims_valid": (
        "131b61265dbab9c8304b5f4ef49c7182819d11fd2bfce1aeb149634e1ff32f62"
    ),
    "small_tier_is_available_in_both_modes": (
        "3ca27d817a6f4db537e558000be9f62e3795d65189e3ed242ef3865c2924ee47"
    ),
    "turn_on_with_bound_opt_in_opens_large_and_suspends_claims": (
        "c762329ee1007f10f7b34cfe86920b4658c058bb45e987830279b185e9e346c5"
    ),
    "turn_off_needs_no_opt_in_and_restores_claims": (
        "965a40307db65992c2b2292d8390dccc62d96368a014dbe45e51ed327d6ef3c5"
    ),
    "same_value_is_a_noop": ("1941618d9c5eebbdb2e690e70c59a82c230e8f2ac17878bb1673ea6ca4f34f8c"),
    "turn_on_again_needs_a_fresh_opt_in": (
        "64341ff8fed02882af8eb400b763f2c0b10b7cdfdf1ccc49254eba249436ecbd"
    ),
    "installs_are_independent": (
        "cb86d5f2acd18b3012d1a1ab92019588610659ad6a609f4fb0d8d3f186f8732d"
    ),
    "reregister_keeps_state_and_claims": (
        "3b012c9785aa1f4e60d4285e8bd58022178c7e8f658cf7b75133f82394dd11c1"
    ),
    "commit_just_below_max_reaches_max": (
        "e0741a23bbf970df4f2325d6fce750e9e8e7dfaf470877c051ddf60cbfa15e7b"
    ),
    "turn_on_at_max_is_revision_exhausted": (
        "bae217b9cf4d2c2cf4f60945b257103d092887a83296117815a071d8cf2cc645"
    ),
    "turn_off_at_max_is_revision_exhausted": (
        "6096c3b2a14068a4e2dd617b127d1a32f6d9cb5416e70d18aa3263ba6ab935a1"
    ),
    "same_value_at_max_is_a_noop": (
        "fc275e700cd8ab8a5d4108cc17415febebbb74922a2a0fc1ff22666aaa32fbee"
    ),
    "unapproved_turn_on_at_max_is_opt_in_missing": (
        "e24c649300b6de1e5ed4e986fd37d16f56a2057a5df4af4ee13ab49031e9b8ef"
    ),
    "stale_before_exhaustion_at_max": (
        "e2f40d7c47abe7189ab881bb1d091e5e06bbf29c0a737880fa4d912b13b56c0e"
    ),
    "max_revision_is_well_formed_and_stale": (
        "a95dd38f10b979dcc9ea2586ea9ff04c8db5f4d4b2c40ebf65629a0934ff07a8"
    ),
    "opt_in_is_bound_to_the_revision": (
        "7c203f0b617bdc8e0ecea74e4a97095d5d4980f3474a8feff9ba2a5d7cdd40d1"
    ),
    "opt_in_is_bound_to_the_install": (
        "7e23a3a4a0470773b1961531a43e000254ca25e0a42cb4088e9ce4c7f19aed5c"
    ),
    "opt_in_preimage_known_answer": (
        "1f3e94a64c1c6afde060abaa9b28b3bc72f9525ddbda4507fea02f71700efa8f"
    ),
    "large_is_off_for_an_unknown_install": (
        "e372e36a7ed64aa6fdb0ea2a96f052581f62bb2135f0cdcf2a66603dfe64d7b9"
    ),
    "well_formed_opt_in_on_turn_off_is_ignored": (
        "d3323d6edcc92d74dff816bb65389ed85cc78d2bfd85bf648fbcb11cf3a3a2e3"
    ),
    "turn_off_applies_before_the_next_verdict": (
        "58bd5af0605800da73a174544cf4e22737ab5d78df1d9f4d148e5b87e453f580"
    ),
    "verdict_is_read_at_invocation_time": (
        "6535284e17136961572147536f37778160c91056b30bb56e19e60177f53d8402"
    ),
    "precedence_egress_then_host_then_mode": (
        "61ca4bcf5d293fc17e8f6a4cfc4d787c604debb4c442978cc7e2f973831f242c"
    ),
    "web_and_server_never_run_a_model": (
        "59dd45e61f1daa8bfa23a4f712c325e06d34de9057aa888b0722b1fa09a806a6"
    ),
    "a_non_local_destination_is_refused_whatever_the_mode": (
        "1a1f1f78e61ff7a0ac92d86964bcbf3e8dc416612c0aa09904929f64f26135bb"
    ),
    "claims_are_suspended_exactly_while_large_is_on": (
        "6266a7e59f4d86a9cf8f9df99e93abf64a0236f44ddfcb48c7aa70bb11241d05"
    ),
    "transition_extra_key": ("852831bdb07bf1132cb5d3ea9df7d16c48681340f4f53e04cad91f36f1c93804"),
    "transition_missing_install_id": (
        "bbaef0c714e20228c876fdb6b0a50b3de7a590bd2e9127b9e494be906d7557b5"
    ),
    "transition_missing_large_model": (
        "323c86a3d11dc5f0fec957facbf8838a8a15ac02fad9d18216df2781b47142b8"
    ),
    "transition_missing_expected_revision": (
        "db0963aff09b4300fb3b09ce4b0a76e2565beb1b85857a985c031f65db665352"
    ),
    "transition_missing_opt_in": (
        "12e402908fcf6e82acc91f3c553e66c75ef2f6fc653948e78081dee3aca720fb"
    ),
    "transition_request_not_a_dict": (
        "c83746e0d5c4e3f058cc35d856c69b7c27c9a67acbc6d4d2bc0c89f28963f7bd"
    ),
    "transition_request_is_a_list": (
        "92e29158ec6bfe855d5acaf4ac3633325e0f6c7345772550d28d40c7918a4c96"
    ),
    "transition_non_str_key": ("3ab16d40f163bc50b84b011d6fd32276f105158d4eced2a15597e6792edf80b2"),
    "install_id_uppercase_hex": (
        "97256d0174837d46fb6d57519f3baffee646de69360020992a0e5fb85acb4d28"
    ),
    "install_id_short_hex": ("5d69ab57c45341474ac2bfe23a0776688603de4af995750bb9b9c9c934185016"),
    "install_id_long_hex": ("052752316dbde4c235bb10e8744940e0a5f1d819e9821848f2bce051594c1a77"),
    "install_id_wrong_prefix": ("6979632a0803d5c342006daf8c07cea0beb31e2fb144441ba808328a8488037a"),
    "install_id_trailing_newline": (
        "0552ff99db903038d0c8f3572690d0145bbbe630dea693497678e7472c9ec1ae"
    ),
    "install_id_empty": ("8c5dcba58d0d82aa40b0ab368ccecffb26719840d47b6d726986986d8ce1b88a"),
    "install_id_not_hex": ("0a4c8874d452ab209dec12bb7f57139a84289de82d09cce16e248df57cd5a8c5"),
    "install_id_int": ("6e24993019a02c807fa1e3e15271a92c8e409ace329f6f43f284f97f5c410c55"),
    "install_id_bytes": ("f8e8075ad28420e4072c355415fef6c785e89fdc59b953ebaba89931ef93ee6d"),
    "install_id_none": ("6bfef9bb5c9cf228ad5eb65bc23c433c84832d10c5c45ca316ec76dd2b1e72a3"),
    "large_model_uppercase": ("b19d03abc02452a663599e47b9766b993abb4884c0286cad33720af6aeec35ef"),
    "large_model_true_str": ("6bc2277a9e493a5462e6a03da860fb8d471ee72bca8ef46a2e2bb325e8be5d07"),
    "large_model_none": ("884fd04095ccdaa543d7c12873662428d18098463ed952c32e883cb7c5dd57e8"),
    "large_model_bytes": ("653b98a464c7b0d1c6f6d714e3cac5a7eeff6736b076a837eb8cc5c629897ce1"),
    "large_model_empty": ("166e337906d3f32da4ff3b4d7598efcbc831831eb66771791563492e8674cecc"),
    "large_model_int_one": ("87dbe9d666ef9dda328d70bab332cdffd8c58c882849fdd28482a7f23a7e0fb0"),
    "large_model_bool_true": ("a7dd1eeda31387aaf1e2a85d9a0bb7629c6cf3332bc7634a58543bbc8931161b"),
    "large_model_padded": ("f9dc3885d532a283988739164bad5830aad249ab09d116918b553798a3fa60a4"),
    "large_model_large_on_token": (
        "32a80ae3e30c99bf8e8e0b8b6c024c7633d132925880de69be91526e93d565f3"
    ),
    "expected_revision_negative": (
        "af476450a86249cee4d322e1b166a52aca8d13a3c5be9b3ba7056878c5b96930"
    ),
    "expected_revision_bool_false": (
        "93b706ca889ed214585cb7a0bf4f0388144374cc3b8e790daf2f7ad57877db4d"
    ),
    "expected_revision_float_zero": (
        "057c04c8b0a9b47ac38f9cd34417c48803960e70c185656c3ec7e12085f9671f"
    ),
    "expected_revision_str_zero": (
        "758f5851edd77ce12517ea20a273f2ef99e0a7fbec17ef726995697b935b5da2"
    ),
    "expected_revision_two_pow_63": (
        "8178500cde3d588366c62c824a490cd41618a012e1fa19e17a6e40cbcf097bd2"
    ),
    "expected_revision_huge": ("a843ebf74d54734f6f3554241128da70a2163e1f3e3cbb121e57e1812204fb05"),
    "expected_revision_none": ("399b16a847134b90ebf151e59b12e490fe87cdd6f43b7b26fa5de074782c31ff"),
    "opt_in_bad_prefix": ("ec48e78b80446e4aa5a0b12e1211dc6e38967a1a0727f476a356b24a05c6e14e"),
    "opt_in_short": ("5ae323e46499b6d430d52ef7a37ba54ab665d5b887b515f917c18f60f4214f4b"),
    "opt_in_uppercase": ("512718e52e77b49eb15ce3beb010d4e4958481d1225c00e2d493922f6e155055"),
    "opt_in_int": ("a7beb2e791b24d417a1eb362dbbbf3a42fc86bca3517b99e1ee43f1a985635a6"),
    "opt_in_bytes": ("c97434d12d34bf36b760410066906cd5b524c1d1f5454a77734e467cab74d980"),
    "opt_in_trailing_newline": ("642b1fa8da7a0f5837d3c036e4eba05a73d5407c47e4bd54208038f54fea2372"),
    "opt_in_empty": ("53480e1999e1a77a25cf59b12fb2369eaf7db9e9a5e42788d52c71572cf40f92"),
    "malformed_opt_in_on_turn_off": (
        "68301abc2a4726d0fa76320ab2bfa1d6ffe6b5a2f32f9f79ac23604adea257a8"
    ),
    "malformed_opt_in_precedes_unknown": (
        "562fd6eea6d6ecc0780b8363cf087bf208f1438b08a928d6e22ec070f9833060"
    ),
    "verdict_extra_key": ("89811e1f1ee3cf802f40916380d3cf4b80b442cab5f0f4e861e9fd028647f235"),
    "verdict_missing_install_id": (
        "2dd60f402bcf9741027d5ba40824276ab948def9389c0fcaf43b704b3dfece5d"
    ),
    "verdict_missing_tier": ("00aa9153b346c82c0e15c992aa02c3a54c6a5581153cb020d3649aa487f85f17"),
    "verdict_missing_host": ("efeea87a2c7163daeb0ce6d766dc8b7afc17eb2a04ba75279a023756fd468cb0"),
    "verdict_missing_destination": (
        "5c61449ebcc96401070dc4d738a8d84100ee0d9144204d12ea41dd286f3d95bb"
    ),
    "verdict_request_not_a_dict": (
        "930e92b169134f5d1564840f370d06d38b3e6b0c4dc5c41e2d1d11f33af99a99"
    ),
    "verdict_install_id_bad": ("5b40accc706be7c295e5d09c8d01b38ae288d8b7ac39edab61f692bc496cc529"),
    "tier_uppercase": ("5b2060742901ccc02ffdcbc901b16b30ae33ae172c869e27d1937f434aa0d141"),
    "tier_medium": ("656593d83436ae1fb8d29765b7d2bb5b15ca97f8a57f0146bec35dfaef6312ca"),
    "tier_none": ("49f25b495fa152cd1bc6684b43314e388bc33d457598636b382fc4799575c310"),
    "tier_bytes": ("fa56cf23d31d2fd40a0c4eb88f7a87e6ace0f60e4b495ce0b1bcb0de2100d8a3"),
    "tier_empty": ("398b458514a9e32309a10cbc0ee2211fa4c2de9e5c1a6d55591cc8429bb18a76"),
    "tier_padded": ("89c494967103bf93e9399b1229a1f9b13fe2da84fe075e2f7cab7f2dd6a25df8"),
    "tier_int": ("63943a0ee649cf331af03b3f605122f9a7ebd4e988f1ec322c0bf7cbf6354d56"),
    "host_uppercase": ("c9829557aabc086abc928ee584c039e9d339cbec7c8d9efb68c424b96a055c05"),
    "host_mobile": ("a787caa34d92e353677c0af46b9368fba8aadb27af3727d477ac1089292b2fd0"),
    "host_none": ("8f8ba9e2fb8d0e5cade5620dcbd8fe08b24998f4e7e0dad21ae90f37c42b749c"),
    "host_bytes": ("1a2e4d148c6e8c01c76ed4bb4a2c11decd6d6ec0df88a9e982f3817086b0fd1a"),
    "host_empty": ("bdfd9890b81ab45ae948f2f464a92adaad3feecafe3be04b2bd4de86269f16f6"),
    "host_padded": ("c8a214cc4b83539c0fe3eb771a56e501fa0d24fde7d9cdc88b495c3078a46565"),
    "host_int": ("57588e93353395e67d4840d342910048715ba934b2981ed76940dabf49a44d8b"),
    "destination_uppercase": ("965f294f63fd4bd5cc943b48c677882b501dab4746d370dbfef0954675007f45"),
    "destination_edge": ("4198e7733b93085454692bfbdc3d2ac9d50b1fa494b5f793325e3ba0541f261d"),
    "destination_none": ("64bc4dfc48e66dfc0f88b7077a05b4392b6292c1b2e8256e7ce67eea81bb6633"),
    "destination_bytes": ("8373957584e0361532f39152d9815ae0d4f57db8a8466b06cf3e180637b769fc"),
    "destination_empty": ("e3cff31c7a7c5750fa00947836bbc27a776001f9c75b5f5d885227daa08958fd"),
    "destination_padded": ("9d768f635bd2eaff832abfc0fc332a6855993657c7ad7948d6e52ce1552cb36c"),
    "destination_bool": ("f952cc78d3fab18eba649401ee182ca514d4e7a4d10610e31d1bcc09c5de43f9"),
    "malformed_tier_precedes_egress": (
        "6ed92eb31b8f11dfa8ffbb9253fab5b743f51a4b62b69fa24a7ef37b88261cdf"
    ),
    "register_bad_id": ("53ed2f68c3e9828939dd1202b9255d29b5b38d647831da2ae4bf83cfe2ac3e87"),
    "register_non_str_id": ("aa28471583a65bc13b92718fb7cab40b4dbb15d17a823db7e0c2c72e3275fc08"),
    "unknown_install_turn_on": ("8025700959a8ed9f6e416588febfc20c1f0f9853ce5eff866150e00a777ee26f"),
    "unknown_install_turn_off": (
        "d8a0b0c7120e1924e2c6058de4ff78f36dc4c5d9c7b079c9a56148de4a6b17da"
    ),
    "unknown_precedes_stale": ("51abad839ea138ab5090fa4e20c6b2c1a436884affcd5abb0c45988f4e9e98f0"),
    "stale_revision_transition": (
        "3ed60fa873f6a9230e5038766048a99f96698618c402755871a332b202f432eb"
    ),
    "stale_revision_ahead": ("4fb69d43e60d3418dbac6880cd2c6e23eaa792f07deb6b491532048dbf75223e"),
    "stale_revision_behind": ("d4c020c62340f4cc2e8a5f2549c12319fafcaa1cf5b2a0cf52eb471b7e596641"),
    "stale_revision_same_value_target": (
        "a18ebc3899872fdcfab44817f257683f07ceac5585191103cea17c580052d8b8"
    ),
    "stale_precedes_opt_in": ("b33ce819815c763d60be886f2bde508001ac85291a789978566ebabb7ca60c1b"),
    "opt_in_missing_none": ("a6202cb620340763ad25c1c074a68778e186d7eeafdf5b2e498c7b2df97b238c"),
    "opt_in_foreign_install": ("ca48e3013fac73dc6a18b200d9e4070ae05deea8a84eb618c70bb0b3f8852a27"),
    "opt_in_stale_bound": ("73a6ac6a31356adf9c9028969523d750d81f6d99ce0dcd8d9998e40c6452296e"),
    "opt_in_preimage_target_on": (
        "2551f6b4661b8cd17d15f2fa56876eb138fafcdfddb39d99bf0dc83c148049c7"
    ),
    "opt_in_preimage_target_off": (
        "400a4317ca7695ad57f72e3e465dc48480e4d3ed038c1a1ae7f9eda6b1850223"
    ),
    "opt_in_preimage_colon_separator": (
        "5b746742c5b4c1bb80a316b7552012f3dc3f48029af038e86139855db55aabdd"
    ),
    "opt_in_preimage_trailing_separator": (
        "e40a4ba6c483b8dc028477156a58f31f189765d0864ec60a9940c6866815fef0"
    ),
    "opt_in_preimage_leading_zero_revision": (
        "a557321c61e921d41c0f9cc3f40fc87efbb0cfd3af6df990a30e620e9fcbe498"
    ),
    "opt_in_preimage_plus_revision": (
        "5542f2b4a1a3d972890cfc235228f4edde6164f0fc312bb41505a1e0b90d3c59"
    ),
    "opt_in_preimage_uppercase_target": (
        "fb46ce0b7f063a8ebec76c8840fdfaf2b03350cd66c8af8bd1422c622cc2541b"
    ),
    "opt_in_preimage_utf16": ("d53d228f8da6ea47b877c7373c5d84c8adb5b5d15fa6664fe0d9c4b6e653d50c"),
    "egress_small_cloud": ("738e77cef371a61ce741d312a134bec3a10907675e396951293f7e7bf97734ed"),
    "egress_large_cloud_with_large_on": (
        "8e1cdc6251c7502ce14bc2f94f182f5a41f80b31b166f099ba8d46fe4d34b22b"
    ),
    "egress_beats_host": ("4c97b1852478669b1a9ec753ef81efcc4a4deaf1a7f64fb077a71df85c3c22b3"),
    "egress_beats_large_off": ("33bb17719cf4b7d0b20af32c7d26a428a9ea224dc064b16a5c680616ba79f823"),
    "host_web_small": ("9e37e49943e42201e05ecba61805a223840c565bf29ee657313c80c346d685b7"),
    "host_server_large_with_large_on": (
        "f2523c20a4960f89986f6c83f972046ea5b8e5f111f14f7f4c857749997bfb23"
    ),
    "host_beats_large_off": ("7fafe20628151f73e03b64c3c0616b98915d938d5c23ce1106f75d613609a4f6"),
    "large_model_off_desktop": ("5e74c6ace5639f3c0a08ac7386f8afb79fdfba2a6bb0741435867ade3dd799df"),
    "large_model_off_after_turn_off": (
        "ef3274277078e72964bdb883d42bf7fd9531b171878b20304e252a13723a3822"
    ),
    "large_model_off_unregistered_install": (
        "cc26d4c84d0a00fb353958836b7f916b833583f32afcfa403137d2565991a86d"
    ),
    "turn_on_at_max_revision": ("b6ebfa37f4246abe6d5132ebcf50599a86b1058bd783a6f78791b5dc064a1def"),
    "turn_off_at_max_revision": (
        "8771345b47c2842fa042582958c8a4fd9aa9c488fc4012d118a5196b7608bd38"
    ),
    "malformed_leaves_state": ("7a4ac8c4a6671e834c621793e7c0909a76f37a7c0a3372d55afa1516a65a4c0c"),
    "unknown_install_leaves_state": (
        "0aa2aa34a1dcacec9cbef7f7a4230172b84a74555fb7d62f4341edb20c3d1956"
    ),
    "stale_revision_leaves_state": (
        "dbad74b02c4c86470309253cac0de0f972c594c8810903c713de8d285c3c5236"
    ),
    "opt_in_missing_leaves_state": (
        "9e42f94556004b246365d3a2da4ab4ae02c25de0849f6f6fdb8d4815f900214e"
    ),
    "egress_requested_leaves_state": (
        "ed44f754f033a0fc8ce835b7c65546a94ac4b8a5d5704fc9667e305a565f16ca"
    ),
    "host_not_desktop_leaves_state": (
        "6340f6f7f1904bed141d9460a75bb299748559aacfdf0b8b022443a476cfb0b8"
    ),
    "large_model_off_leaves_state": (
        "efa4760f4f0d9781aca37b9a6a5e65481a131484a56c1ecab9c5424cf353b323"
    ),
    "revision_exhausted_leaves_state": (
        "f5e9ec77cc36c982529ba7a1d1f69a37ec43b27803f719a0befbeb35d5c795cb"
    ),
    "rejected_turn_on_does_not_consume_the_opt_in": (
        "930b84d5813ca34577c2c72c7e0b98f3f535c51b05c11d44a4bc8d9456c7062b"
    ),
}


def validate_closure(cases, raw, digests=True):
    validate_structure(cases)
    for section in SECTIONS:
        assert [r["name"] for r in cases[section]] == MANIFEST[section], section
    assert replay(cases, world) == []
    if digests:
        for section in SECTIONS:
            for raw_row in raw[section]:
                assert _row_digest(raw_row) == ROW_DIGESTS[raw_row["name"]], raw_row["name"]
    assert {r["expect_failure"] for r in cases["malformed"]} == FAILURE_CLASSES - {"internal"}
    assert {r["expect_failure"] for r in cases["rollback"]} == FAILURE_CLASSES - {"internal"}
    steps = [s for sec in ("happy", "boundary") for r in cases[sec] for s in r["steps"]]
    assert {s["op"] for s in steps} == STEP_OPS


def _names(section):
    return [r["name"] for r in CASES[section]]


def _row(section, name):
    return next(r for r in CASES[section] if r["name"] == name)


def test_fixture_structure_and_closure():
    validate_closure(CASES, RAW)


def test_param_ids_equal_manifest_names():
    for section in SECTIONS:
        assert _names(section) == MANIFEST[section]
    assert len(ROW_DIGESTS) == sum(len(v) for v in MANIFEST.values())


def test_failure_class_and_enum_coverage():
    assert set(FAILURE_MAPPING) == FAILURE_CLASSES
    assert len(FAILURE_CLASSES) == 8
    for failure in FAILURE_CLASSES:
        assert any(r["expect_failure"] == failure for r in CASES["malformed"])
        assert any(r["expect_failure"] == failure for r in CASES["rollback"])
    boundary_failures = {
        s["expect"]["failure"]
        for r in CASES["boundary"]
        for s in r["steps"]
        if "failure" in s["expect"]
    }
    assert {
        "revision_exhausted",
        "opt_in_missing",
        "stale_revision",
        "unknown_install",
        "egress_requested",
        "host_not_desktop",
        "large_model_off",
    } <= boundary_failures


# ---- pinned scenarios ----------------------------------------------------------------


@pytest.mark.parametrize("section", ("happy", "boundary"))
def test_pinned_scenarios(section):
    for row in CASES[section]:
        sw = world()
        for i, step in enumerate(row["steps"]):
            assert exec_step(sw, step) == step["expect"], (row["name"], i)


def test_happy_rows_only_accept():
    for row in CASES["happy"]:
        for step in row["steps"]:
            assert "failure" not in step["expect"], row["name"]


def test_default_and_turn_on_are_pinned_independently():
    row = _row("happy", "turn_on_with_bound_opt_in_opens_large_and_suspends_claims")
    on = next(s for s in row["steps"] if s["op"] == "transition")
    assert (
        on["request"]["opt_in"]
        == "lmo1:" + hashlib.sha256(f"{INS}|0|large-on".encode()).hexdigest()
    )
    assert on["expect"]["record"] == {
        "install_id": INS,
        "large_model": "on",
        "revision": 1,
        "reference_claims": "suspended",
    }
    row = _row("happy", "register_defaults_off_claims_valid")
    first = next(s for s in row["steps"] if s["op"] == "register")
    assert first["expect"]["record"] == {
        "install_id": INS,
        "large_model": "off",
        "revision": 0,
        "reference_claims": "valid",
    }


def test_opt_in_known_answer_row():
    row = _row("boundary", "opt_in_preimage_known_answer")
    token = next(s for s in row["steps"] if s["op"] == "transition")["request"]["opt_in"]
    assert token == "lmo1:" + hashlib.sha256(b"ins1:" + b"1" * 64 + b"|0|large-on").hexdigest()
    assert token == opt_in_for(INS, 0)
    assert token != opt_in_for(INS, 1) and token != opt_in_for(INS2, 0)


def test_turn_off_rows_pin_the_off_switch_and_claims():
    row = _row("happy", "turn_off_needs_no_opt_in_and_restores_claims")
    off = next(s for s in row["steps"] if s["request"].get("large_model") == "off")
    assert off["request"]["opt_in"] is None
    assert off["expect"]["record"] == {
        "install_id": INS,
        "large_model": "off",
        "revision": 2,
        "reference_claims": "valid",
    }
    row = _row("boundary", "turn_off_applies_before_the_next_verdict")
    assert [s["expect"] for s in row["steps"][-3:]] == [
        {"verdict": {"verdict": "allow", "reference_claims": "suspended"}},
        {"record": off["expect"]["record"]},
        {"failure": "large_model_off"},
    ]


# ---- malformed and rollback ----------------------------------------------------------


def _snapshot(obj):
    """A type-exact structural snapshot that never calls user code."""
    kind = type(obj).__name__
    if isinstance(obj, dict):
        return (kind, [(_snapshot(k), _snapshot(v)) for k, v in dict.items(obj)])
    if isinstance(obj, (list, tuple)):
        return (
            kind,
            [_snapshot(v) for v in list.__iter__(obj)]
            if isinstance(obj, list)
            else [_snapshot(v) for v in tuple.__iter__(obj)],
        )
    if isinstance(obj, bool) or obj is None:
        return (kind, obj is True)
    if isinstance(obj, int):
        return (kind, obj.bit_length(), obj % 1_000_003)
    if isinstance(obj, str):
        return (kind, "".join([obj]))
    if isinstance(obj, bytes):
        return (kind, bytes.__bytes__(obj) if False else bytes(obj))
    return (kind, float.hex(obj) if isinstance(obj, float) else "opaque")


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_rejects_typed_and_leaves_inputs_unchanged(name):
    row = _row("malformed", name)
    sw = world()
    for step in row["setup"]:
        exec_step(sw, step)
    state = _state_of(sw)
    request = copy.deepcopy(row["request"])
    snap = _snapshot(request)
    assert exec_step(sw, request) == {"failure": row["expect_failure"]}
    assert _snapshot(request) == snap
    assert _state_of(sw) == state


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_succeeds(name):
    row = _row("malformed", name)
    sw = world()
    for step in row["minimal_repair"]["setup"]:
        exec_step(sw, step)
    assert "failure" not in exec_step(sw, row["minimal_repair"]["request"])


def test_rejections_are_fresh_typed_errors():
    sw = world()
    sw.register(INS)
    errors = []
    for _ in range(2):
        with pytest.raises(ModelError) as info:
            sw.verdict(
                {"install_id": INS, "tier": "large", "host": "desktop", "destination": "local"}
            )
        errors.append(info.value)
    assert errors[0] is not errors[1]
    for exc in errors:
        assert exc.failure_class == "large_model_off" and exc.code == "model_unavailable"
        assert exc.__cause__ is None and exc.__context__ is None


def test_failure_codes_are_pinned():
    assert {
        "egress_requested": "local_only",
        "host_not_desktop": "host_refused",
        "large_model_off": "model_unavailable",
        "opt_in_missing": "approval_required",
        "stale_revision": "conflict",
    }.items() <= FAILURE_MAPPING.items()
    for failure in ("egress_requested", "host_not_desktop", "large_model_off"):
        row = next(r for r in CASES["malformed"] if r["expect_failure"] == failure)
        sw = world()
        for step in row["setup"]:
            exec_step(sw, step)
        with pytest.raises(ModelError) as info:
            getattr(sw, row["request"]["op"])(copy.deepcopy(row["request"]["request"]))
        assert info.value.code == FAILURE_MAPPING[failure]


def test_injected_valid_request_as_malformed_row_fails_replay():
    cases = copy.deepcopy(CASES)
    row = cases["malformed"][0]
    row["request"] = copy.deepcopy(row["minimal_repair"]["request"])
    assert replay(cases, world)


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback_rows(name):
    row = _row("rollback", name)
    sw = world()
    for step in row["setup"]:
        exec_step(sw, step)
    before = _state_of(sw)
    assert exec_step(sw, row["rejected"]) == {"failure": row["expect_failure"]}
    assert _state_of(sw) == before
    assert exec_step(sw, row["follow_up"]) == row["follow_up"]["expect"]


def test_validation_precedence_rows():
    mal = _row("malformed", "malformed_opt_in_precedes_unknown")
    assert exec_step(world(), mal["request"]) == {"failure": "malformed_model_request"}
    for name, failure in (
        ("malformed_tier_precedes_egress", "malformed_model_request"),
        ("unknown_precedes_stale", "unknown_install"),
        ("stale_precedes_opt_in", "stale_revision"),
        ("stale_revision_same_value_target", "stale_revision"),
        ("egress_beats_host", "egress_requested"),
        ("egress_beats_large_off", "egress_requested"),
        ("host_beats_large_off", "host_not_desktop"),
    ):
        assert _row("malformed", name)["expect_failure"] == failure
    row = _row("boundary", "precedence_egress_then_host_then_mode")
    assert [s["expect"] for s in row["steps"][-4:]] == [
        {"failure": "egress_requested"},
        {"failure": "host_not_desktop"},
        {"failure": "large_model_off"},
        {"failure": "egress_requested"},
    ]
    ex = _row("boundary", "unapproved_turn_on_at_max_is_opt_in_missing")
    assert ex["steps"][-1]["expect"] == {"failure": "opt_in_missing"}
    ex = _row("boundary", "stale_before_exhaustion_at_max")
    assert [s["expect"] for s in ex["steps"][-2:]] == [
        {"failure": "stale_revision"},
        {"failure": "unknown_install"},
    ]


# ---- hostile-type variants of every accepted request ---------------------------------

CALLS = []


class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _EqRaises(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("eq")

    __hash__ = str.__hash__


class _ReprRaises(str):
    def __repr__(self):
        CALLS.append("repr")
        raise RuntimeError("repr")


class _HashCollides(str):
    def __hash__(self):
        CALLS.append("hash")
        return str.__hash__(self)

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)


def _accepted_requests():
    out = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            for i, step in enumerate(row["steps"]):
                if step["op"] in ("verdict", "transition") and "failure" not in step["expect"]:
                    out.append((f"{section}/{row['name']}/{i}", step))
    return out


def _variants(step):
    base = step["request"]
    out = [("dict-sub", _DictSub(base)), ("list", list(base.items())), ("none", None)]
    for key in base:
        for tag, make in (
            ("str-sub", _StrSub),
            ("eq-raises", _EqRaises),
            ("collides", _HashCollides),
        ):
            out.append(
                (f"key-{key}-{tag}", {(make(k) if k == key else k): v for k, v in base.items()})
            )
        out.append((f"drop-{key}", {k: v for k, v in base.items() if k != key}))
    out.append(("extra-key", {**base, "x": 1}))
    out.append(("int-key", {**base, 7: 1}))
    for key, value in base.items():
        if value is None:
            out.append((f"value-{key}-empty", {**base, key: ""}))
            out.append((f"value-{key}-bytes", {**base, key: b""}))
        elif type(value) is str:
            for tag, make in (
                ("str-sub", _StrSub),
                ("eq-raises", _EqRaises),
                ("repr-raises", _ReprRaises),
            ):
                out.append((f"value-{key}-{tag}", {**base, key: make(value)}))
            out.append((f"value-{key}-bytes", {**base, key: value.encode()}))
        elif type(value) is int:
            out.append((f"value-{key}-bool", {**base, key: bool(value)}))
            out.append((f"value-{key}-int-sub", {**base, key: _IntSub(value)}))
            out.append((f"value-{key}-float", {**base, key: float(value)}))
    return out


_ACCEPTED = _accepted_requests()


def _hostile_failures(factory, only=None):
    """Every hostile variant the world under test mishandles."""
    bad = []
    for label, step in _ACCEPTED:
        if only is not None and label != only:
            continue
        section, row_name, index = label.split("/")
        for tag, request in _variants(step):
            sw = factory()
            for prior in _row(section, row_name)["steps"][: int(index)]:
                exec_step(sw, prior)
            state = _state_of(sw)
            CALLS.clear()
            snap = _snapshot(request)
            try:
                got = getattr(sw, step["op"])(request)
            except ModelError as exc:
                got = {"failure": exc.failure_class}
            except Exception as exc:  # noqa: BLE001 - a raw error is a failed variant
                got = {"raw": type(exc).__name__}
            ok = (
                got == {"failure": "malformed_model_request"}
                and CALLS == []
                and _snapshot(request) == snap
                and _state_of(sw) == state
            )
            if not ok:
                bad.append((label, tag, got, list(CALLS)))
    return bad


@pytest.mark.parametrize("label", [label for label, _ in _ACCEPTED])
def test_hostile_variants_of_every_accepted_request(label):
    assert _hostile_failures(world, only=label) == []


def test_variant_set_is_complete():
    assert len(_ACCEPTED) >= 45
    assert {s["op"] for _, s in _ACCEPTED} == {"verdict", "transition"}
    tags = {t for _, s in _ACCEPTED for t, _ in _variants(s)}
    for needed in (
        "dict-sub",
        "list",
        "none",
        "extra-key",
        "int-key",
        "key-install_id-str-sub",
        "key-install_id-eq-raises",
        "key-install_id-collides",
        "value-install_id-str-sub",
        "value-install_id-eq-raises",
        "value-install_id-repr-raises",
        "value-install_id-bytes",
        "value-large_model-str-sub",
        "value-large_model-bytes",
        "value-expected_revision-bool",
        "value-expected_revision-int-sub",
        "value-expected_revision-float",
        "value-opt_in-str-sub",
        "value-opt_in-empty",
        "value-opt_in-bytes",
        "value-tier-str-sub",
        "value-tier-bytes",
        "value-host-str-sub",
        "value-host-eq-raises",
        "value-destination-str-sub",
        "value-destination-bytes",
    ):
        assert needed in tags, needed


# ---- reference mutants must turn the fixture red -------------------------------------


def _red(factory):
    return bool(replay(CASES, factory))


def test_reference_is_green():
    assert replay(CASES, world) == []


def _patch(monkeypatch, **attrs):
    for name, value in attrs.items():
        monkeypatch.setattr(_ref, name, value)
    return world


def _sub(**methods):
    return type("Mutant", (LocalModels,), methods)


_orig_opt = _ref.opt_in_for
_orig_bump = _ref._bump


def _m_default_on(monkeypatch):
    def register(self, iid):
        fresh = iid not in self._state
        LocalModels.register(self, iid)
        if fresh:
            self._state[iid] = ("on", 0)
        return self.record(iid)

    return _sub(register=register)


def _m_reregister_resets(monkeypatch):
    def register(self, iid):
        LocalModels.register(self, iid)
        self._state[iid] = ("off", 0)
        return self.record(iid)

    return _sub(register=register)


def _m_opt_in_ignored(monkeypatch):
    return _patch(monkeypatch, _opt_in_ok=lambda req, iid: True)


def _m_opt_in_unbound_revision(monkeypatch):
    return _patch(
        monkeypatch, opt_in_for=lambda iid, rev, target="large-on": _orig_opt(iid, 0, target)
    )


def _m_opt_in_unbound_install(monkeypatch):
    return _patch(
        monkeypatch, opt_in_for=lambda iid, rev, target="large-on": _orig_opt(INS, rev, target)
    )


def _m_opt_in_target_on(monkeypatch):
    return _patch(
        monkeypatch, opt_in_for=lambda iid, rev, target="large-on": _orig_opt(iid, rev, "on")
    )


def _m_turn_off_needs_opt_in(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("large_model") == "off" and req.get("opt_in") is None:
            iid = req.get("install_id")
            state = self._state.get(iid) if type(iid) is str else None
            if state is not None and state[0] == "on" and req.get("expected_revision") == state[1]:
                _ref._fail("opt_in_missing")
        return LocalModels.transition(self, req)

    return _sub(transition=transition)


def _m_malformed_opt_in_ignored_on_off(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("large_model") == "off":
            req = {**req, "opt_in": None}
        return LocalModels.transition(self, req)

    return _sub(transition=transition)


def _m_cas_off(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("install_id") in self._state:
            req = {**req, "expected_revision": self._state[req["install_id"]][1]}
        return LocalModels.transition(self, req)

    return _sub(transition=transition)


def _m_same_value_bumps(monkeypatch):
    def transition(self, req):
        out = LocalModels.transition(self, req)
        iid = req["install_id"]
        mode, rev = self._state[iid]
        if rev == req["expected_revision"] and mode == req["large_model"]:
            self._state[iid] = (mode, _orig_bump(rev))
            return self.record(iid)
        return out

    return _sub(transition=transition)


def _m_same_value_needs_opt_in(monkeypatch):
    def transition(self, req):
        if type(req) is dict and type(req.get("install_id")) is str:
            state = self._state.get(req["install_id"])
            if (
                state is not None
                and req.get("large_model") == "on" == state[0]
                and req.get("opt_in") is None
                and req.get("expected_revision") == state[1]
            ):
                _ref._fail("opt_in_missing")
        return LocalModels.transition(self, req)

    return _sub(transition=transition)


def _m_same_value_before_stale(monkeypatch):
    def transition(self, req):
        state = self._state.get(req.get("install_id")) if type(req) is dict else None
        if (
            state is not None
            and type(req.get("large_model")) is str
            and req.get("large_model") == state[0]
        ):
            try:
                return LocalModels.transition(self, {**req, "expected_revision": state[1]})
            except ModelError:
                pass
        return LocalModels.transition(self, req)

    return _sub(transition=transition)


def _m_saturates(monkeypatch):
    return _patch(monkeypatch, _bump=lambda rev: min(rev + 1, MAX_REV))


def _m_wraps(monkeypatch):
    return _patch(monkeypatch, _bump=lambda rev: 0 if rev >= MAX_REV else rev + 1)


def _m_exhausted_first(monkeypatch):
    def transition(self, req):
        state = self._state.get(req.get("install_id")) if type(req) is dict else None
        if state is not None and state[1] >= MAX_REV:
            _ref._fail("revision_exhausted")
        return LocalModels.transition(self, req)

    return _sub(transition=transition)


def _m_unknown_reads_on(monkeypatch):
    def reads_off(self, iid):
        state = self._state.get(iid)
        return state is not None and state[0] != "on"

    return _sub(reads_off=reads_off)


def _m_off_not_applied(monkeypatch):
    def transition(self, req):
        out = LocalModels.transition(self, req)
        if req["large_model"] == "off":
            before = self._state[req["install_id"]]
            if before[1] != req["expected_revision"]:
                self._state[req["install_id"]] = ("on", before[1])
                return self.record(req["install_id"])
        return out

    return _sub(transition=transition)


def _verdict_with(order_fn):
    def verdict(self, req):
        iid = _ref._request(req, _ref._VERDICT_KEYS)
        tier = _ref._member(req["tier"], _ref._TIERS)
        host = _ref._member(req["host"], _ref._HOSTS)
        dest = _ref._member(req["destination"], _ref._DESTINATIONS)
        order_fn(self, iid, tier, host, dest)
        return {"verdict": "allow", "reference_claims": self.claims(iid)}

    return verdict


def _egress(dest):
    if dest != "local":
        _ref._fail("egress_requested")


def _host(host):
    if host != "desktop":
        _ref._fail("host_not_desktop")


def _large(self, iid, tier):
    if tier == "large" and self.reads_off(iid):
        _ref._fail("large_model_off")


def _m_egress_allowed(monkeypatch):
    def order(self, iid, tier, host, dest):
        _host(host)
        _large(self, iid, tier)

    return _sub(verdict=_verdict_with(order))


def _m_host_before_egress(monkeypatch):
    def order(self, iid, tier, host, dest):
        _host(host)
        _egress(dest)
        _large(self, iid, tier)

    return _sub(verdict=_verdict_with(order))


def _m_mode_before_host(monkeypatch):
    def order(self, iid, tier, host, dest):
        _egress(dest)
        _large(self, iid, tier)
        _host(host)

    return _sub(verdict=_verdict_with(order))


def _m_mode_before_egress(monkeypatch):
    def order(self, iid, tier, host, dest):
        _large(self, iid, tier)
        _egress(dest)
        _host(host)

    return _sub(verdict=_verdict_with(order))


def _m_web_allowed(monkeypatch):
    def order(self, iid, tier, host, dest):
        _egress(dest)
        if host == "server":
            _ref._fail("host_not_desktop")
        _large(self, iid, tier)

    return _sub(verdict=_verdict_with(order))


def _m_server_allowed(monkeypatch):
    def order(self, iid, tier, host, dest):
        _egress(dest)
        if host == "web":
            _ref._fail("host_not_desktop")
        _large(self, iid, tier)

    return _sub(verdict=_verdict_with(order))


def _m_small_needs_opt_in(monkeypatch):
    def order(self, iid, tier, host, dest):
        _egress(dest)
        _host(host)
        if self.reads_off(iid):
            _ref._fail("large_model_off")

    return _sub(verdict=_verdict_with(order))


def _m_large_always_available(monkeypatch):
    def order(self, iid, tier, host, dest):
        _egress(dest)
        _host(host)

    return _sub(verdict=_verdict_with(order))


def _m_claims_never_valid(monkeypatch):
    return _sub(claims=lambda self, iid: "suspended")


def _m_claims_always_valid(monkeypatch):
    return _sub(claims=lambda self, iid: "valid")


def _m_claims_stay_suspended(monkeypatch):
    def claims(self, iid):
        return "valid" if self._state.get(iid, ("off", 0)) == ("off", 0) else "suspended"

    return _sub(claims=claims)


def _m_verdict_without_claims(monkeypatch):
    def verdict(self, req):
        out = LocalModels.verdict(self, req)
        return {"verdict": out["verdict"], "reference_claims": "valid"}

    return _sub(verdict=verdict)


def _m_verdict_cached(monkeypatch):
    cache = {}

    def verdict(self, req):
        key = (
            tuple((k, req.get(k)) for k in sorted(req))
            if type(req) is dict and all(type(k) is str for k in req)
            else None
        )
        if key in cache:
            return cache[key]
        out = LocalModels.verdict(self, req)
        cache[key] = out
        return out

    return _sub(verdict=verdict)


def _m_tier_unvalidated(monkeypatch):
    def member(v, allowed):
        if type(v) is not str:
            _ref._fail("malformed_model_request")
        return v

    return _patch(monkeypatch, _member=member)


def _m_subclass_str_ok(monkeypatch):
    return _patch(monkeypatch, _exact_str=lambda v: isinstance(v, str))


def _m_bool_revision_ok(monkeypatch):
    return _patch(monkeypatch, _exact_rev=lambda v: isinstance(v, int) and 0 <= v <= MAX_REV)


def _m_register_accepts_bad_id(monkeypatch):
    def register(self, iid):
        self._state.setdefault(iid, ("off", 0))
        return {
            "install_id": iid,
            "large_model": "off",
            "revision": 0,
            "reference_claims": "valid",
        }

    return _sub(register=register)


MUTANTS = {
    "default_on": _m_default_on,
    "reregister_resets": _m_reregister_resets,
    "opt_in_ignored": _m_opt_in_ignored,
    "opt_in_unbound_revision": _m_opt_in_unbound_revision,
    "opt_in_unbound_install": _m_opt_in_unbound_install,
    "opt_in_target_on": _m_opt_in_target_on,
    "turn_off_needs_opt_in": _m_turn_off_needs_opt_in,
    "malformed_opt_in_ignored_on_off": _m_malformed_opt_in_ignored_on_off,
    "cas_off": _m_cas_off,
    "same_value_bumps": _m_same_value_bumps,
    "same_value_needs_opt_in": _m_same_value_needs_opt_in,
    "same_value_before_stale": _m_same_value_before_stale,
    "saturates": _m_saturates,
    "wraps": _m_wraps,
    "exhausted_first": _m_exhausted_first,
    "unknown_reads_on": _m_unknown_reads_on,
    "off_not_applied": _m_off_not_applied,
    "egress_allowed": _m_egress_allowed,
    "host_before_egress": _m_host_before_egress,
    "mode_before_host": _m_mode_before_host,
    "mode_before_egress": _m_mode_before_egress,
    "web_allowed": _m_web_allowed,
    "server_allowed": _m_server_allowed,
    "small_needs_opt_in": _m_small_needs_opt_in,
    "large_always_available": _m_large_always_available,
    "claims_never_valid": _m_claims_never_valid,
    "claims_always_valid": _m_claims_always_valid,
    "claims_stay_suspended": _m_claims_stay_suspended,
    "verdict_without_claims": _m_verdict_without_claims,
    "verdict_cached": _m_verdict_cached,
    "tier_unvalidated": _m_tier_unvalidated,
    "subclass_str_ok": _m_subclass_str_ok,
    "bool_revision_ok": _m_bool_revision_ok,
    "register_accepts_bad_id": _m_register_accepts_bad_id,
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_reference_mutants_turn_the_fixture_red(name, monkeypatch):
    factory = MUTANTS[name](monkeypatch)
    assert replay(CASES, factory) or _hostile_failures(factory), (
        f"mutant {name} survived the fixture"
    )


# ---- fixture edits must be detected by the closure -----------------------------------


def _edit(fn):
    raw = copy.deepcopy(RAW)
    fn(raw)
    return raw


def _row_of(raw, section, name):
    return next(r for r in raw[section] if r["name"] == name)


FIXTURE_EDITS = {
    "drop_happy_row": lambda r: r["happy"].pop(),
    "drop_malformed_row": lambda r: r["malformed"].pop(0),
    "duplicate_malformed_row": lambda r: r["malformed"].append(copy.deepcopy(r["malformed"][0])),
    "swap_two_happy_rows": lambda r: r["happy"].insert(0, r["happy"].pop()),
    "rename_row": lambda r: _row_of(r, "happy", "same_value_is_a_noop").update(name="x"),
    "flip_pinned_mode": lambda r: _row_of(
        r, "happy", "turn_off_needs_no_opt_in_and_restores_claims"
    )["steps"][-3]["expect"]["record"].update(large_model="on"),
    "flip_pinned_claims": lambda r: _row_of(
        r, "happy", "turn_on_with_bound_opt_in_opens_large_and_suspends_claims"
    )["steps"][-3]["expect"]["record"].update(reference_claims="valid"),
    "bump_pinned_revision": lambda r: _row_of(
        r, "happy", "turn_on_with_bound_opt_in_opens_large_and_suspends_claims"
    )["steps"][-3]["expect"]["record"].update(revision=2),
    "change_expected_failure": lambda r: r["malformed"][0].update(expect_failure="stale_revision"),
    "valid_request_in_malformed": lambda r: r["malformed"][0].update(
        request=copy.deepcopy(r["malformed"][0]["minimal_repair"]["request"])
    ),
    "broken_repair": lambda r: r["malformed"][0]["minimal_repair"].update(
        request=copy.deepcopy(r["malformed"][0]["request"])
    ),
    "unrelated_follow_up": lambda r: r["rollback"][0]["follow_up"]["expect"]["verdict"].update(
        reference_claims="valid"
    ),
    "drop_rollback_row": lambda r: r["rollback"].pop(),
    "retype_revision_edge": lambda r: _row_of(r, "boundary", "commit_just_below_max_reaches_max")[
        "steps"
    ][-3]["request"].update(revision=2**62),
    "change_contract": lambda r: r.update(contract="x"),
    "extra_top_key": lambda r: r.update(extra=1),
}


@pytest.mark.parametrize("edit", sorted(FIXTURE_EDITS))
@pytest.mark.parametrize("digests", [True, False], ids=["with-digests", "closure-only"])
def test_fixture_edits_are_detected(edit, digests):
    raw = _edit(FIXTURE_EDITS[edit])
    with pytest.raises(AssertionError):
        validate_closure(_dec(raw), raw, digests=digests)


def test_digest_table_detects_a_silent_retype():
    raw = _edit(
        lambda r: _row_of(r, "happy", "same_value_is_a_noop")["steps"][-1]["request"].update(
            note="x"
        )
    )
    cases = _dec(raw)
    # structure and replay still pass: only the digest table notices
    validate_closure(cases, raw, digests=False)
    with pytest.raises(AssertionError):
        validate_closure(cases, raw, digests=True)
