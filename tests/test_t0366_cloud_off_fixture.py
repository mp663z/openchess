"""T0366 Privacy/cloud-off/fixture.

A closed fixture for the T0365 cloud-off contract
(`tests/fixtures/cloud_off/cases.json`). Rows are scenarios replayed in order on
one fresh world (the T0365 reference `CloudSwitch` composed with the T0356
`FlagStore`): happy, boundary, malformed (every failure class, each with a
minimal repair) and rollback (a rejected request leaves switch and flag state
untouched and the valid follow-up behaves as pinned).

Pinned results come from the T0365 reference. The fixture is closed by name
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
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0365_cloud_off_contract as _ref  # noqa: E402
from tests.test_t0356_sensitive_flag_contract import FlagError, FlagStore  # noqa: E402

CloudError = _ref.CloudError
CloudSwitch = _ref.CloudSwitch
opt_in_for = _ref.opt_in_for
FAILURE_CLASSES = set(_ref.FAILURE_CLASSES)
FAILURE_MAPPING = dict(_ref.FAILURE_MAPPING)
FIXTURE = Path(__file__).parent / "fixtures" / "cloud_off" / "cases.json"
CONTRACT_DOC = yaml.safe_load(_ref.CONTRACT.read_text())["contract"]
MAX_REV = 2**63 - 1
INS = "ins1:" + "1" * 64
INS2 = "ins1:" + "2" * 64
A = "col1:" + "a" * 64
B = "col1:" + "b" * 64
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "steps"}
BAD_KEYS = {"name", "defect", "expect_failure", "setup", "request", "minimal_repair"}
RB_KEYS = {"name", "why", "setup", "rejected", "expect_failure", "follow_up"}
STEP_OPS = {
    "register",
    "verdict",
    "transition",
    "record",
    "seed",
    "flag_register",
    "flag_transition",
}


def world():
    return CloudSwitch(FlagStore())


def _state_of(sw):
    return copy.deepcopy((sw._state, sw._flags._state))


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
        if op == "flag_register":
            out = {"flag": sw._flags.register(rq["collection_id"])}
        elif op == "flag_transition":
            out = {"flag": sw._flags.transition(copy.deepcopy(rq))}
        elif op == "register":
            out = {"record": sw.register(rq["install_id"])}
        elif op == "verdict":
            out = {"verdict": sw.verdict(copy.deepcopy(rq))}
        elif op == "transition":
            out = {"record": sw.transition(copy.deepcopy(rq))}
        elif op == "record":
            out = {"record": sw.record(rq["install_id"])}
        elif op == "seed":
            sw._state[rq["install_id"]] = (rq["cloud_mode"], rq["revision"])
            out = {"seeded": True}
        else:
            raise AssertionError(op)
    except CloudError as exc:
        assert type(exc) is CloudError and exc.code == FAILURE_MAPPING[exc.failure_class]
        out = {"failure": exc.failure_class}
    except FlagError as exc:
        raise AssertionError("flag setup failed: " + exc.failure_class) from None
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
    assert cases["contract"] == CONTRACT_DOC["id"] == "privacy-cloud-off"
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
        "register_defaults_off_local_allowed",
        "turn_on_with_bound_opt_in_opens_cloud_for_an_open_collection",
        "turn_off_needs_no_opt_in",
        "same_value_is_a_noop",
        "turn_on_again_needs_a_fresh_opt_in",
        "installs_are_independent",
        "reregister_keeps_state",
        "local_is_always_allowed",
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
        "cloud_on_still_blocks_a_sensitive_or_unknown_collection",
        "switch_is_checked_before_the_flag",
        "unknown_install_reads_off_even_when_another_is_on",
        "well_formed_opt_in_on_turn_off_is_ignored",
        "turn_off_applies_before_the_next_verdict",
        "verdict_is_read_at_send_time",
    ],
    "malformed": [
        "transition_extra_key",
        "transition_missing_install_id",
        "transition_missing_cloud_mode",
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
        "cloud_mode_uppercase",
        "cloud_mode_true_str",
        "cloud_mode_none",
        "cloud_mode_bytes",
        "cloud_mode_empty",
        "cloud_mode_int_one",
        "cloud_mode_bool_true",
        "cloud_mode_padded",
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
        "verdict_missing_collection_id",
        "verdict_missing_destination",
        "verdict_request_not_a_dict",
        "collection_id_uppercase_hex",
        "collection_id_short_hex",
        "collection_id_wrong_prefix",
        "collection_id_trailing_newline",
        "collection_id_empty",
        "collection_id_not_hex",
        "collection_id_int",
        "collection_id_none",
        "destination_uppercase",
        "destination_edge",
        "destination_none",
        "destination_bytes",
        "destination_empty",
        "destination_padded",
        "verdict_install_id_bad",
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
        "opt_in_preimage_target_off",
        "opt_in_preimage_colon_separator",
        "opt_in_preimage_trailing_separator",
        "opt_in_preimage_leading_zero_revision",
        "opt_in_preimage_plus_revision",
        "opt_in_preimage_uppercase_target",
        "opt_in_preimage_utf16",
        "cloud_verdict_while_off",
        "cloud_verdict_after_turn_off",
        "cloud_verdict_unregistered_install",
        "cloud_verdict_off_beats_sensitive",
        "cloud_verdict_sensitive_collection",
        "cloud_verdict_unregistered_collection",
        "cloud_verdict_after_flag_raise",
        "turn_on_at_max_revision",
        "turn_off_at_max_revision",
    ],
    "rollback": [
        "malformed_leaves_state",
        "unknown_install_leaves_state",
        "stale_revision_leaves_state",
        "opt_in_missing_leaves_state",
        "cloud_off_leaves_state",
        "collection_sensitive_leaves_state",
        "revision_exhausted_leaves_state",
        "rejected_turn_on_does_not_consume_the_opt_in",
    ],
}

ROW_DIGESTS = {
    "register_defaults_off_local_allowed": (
        "b432e064e86d584ae8beec858c8946a9c5a3123e81d3a84f1f49ac4ea889c825"
    ),
    "turn_on_with_bound_opt_in_opens_cloud_for_an_open_collection": (
        "907a878cd44b48ad09993207eb433ea0b0c21aa1a582e4d33a6deed532fc1153"
    ),
    "turn_off_needs_no_opt_in": (
        "c34da5f22a8de390c0bda5090539e3d143ac9f805c35e9be207dcd299f6c1cae"
    ),
    "same_value_is_a_noop": ("2a965b1c605ab6668c90739be6ee73592f1bd7ff51fbc29207552ce1e1c0eda1"),
    "turn_on_again_needs_a_fresh_opt_in": (
        "6e21011a101a516722b8683687de43208b549bf6f5765fc306ca5af062581c17"
    ),
    "installs_are_independent": (
        "39f3c77498ffb64bd4955eec885e7b05c06f34a352dac668b25e4ede3a419d9e"
    ),
    "reregister_keeps_state": ("18db7cea095d931bf6e0c5f0d9f614bab4573f903580f8c373826ca79644ff1b"),
    "local_is_always_allowed": ("e3d62869169278229ccbcb1820e15b632a012f39fd207d8c5f69829e3db528c4"),
    "commit_just_below_max_reaches_max": (
        "0776e8db7076b31314fb2e2a06b62115df1817e01e5d1d8c59d02bab5a5cf5ac"
    ),
    "turn_on_at_max_is_revision_exhausted": (
        "dbaf0acd5d7f3997cb9bab1ad45d7d33cc281f82132e9f4fa321bd498b1297a3"
    ),
    "turn_off_at_max_is_revision_exhausted": (
        "81ee62a704dbf594f934d8c183d60d37b1f55ef238181888a68fe0028572b87e"
    ),
    "same_value_at_max_is_a_noop": (
        "c0142c7c660a85fe3f537c3b1cda43e3ab81c8e8a0487af845f7769ad04ae40d"
    ),
    "unapproved_turn_on_at_max_is_opt_in_missing": (
        "72d94603708410b9463c67e93729563e87ecfc6b3620960e1f764ee6ae4c4eed"
    ),
    "stale_before_exhaustion_at_max": (
        "06f822ba507c824918646dcf698b6980df02aaf82c502817878005f7545854a0"
    ),
    "max_revision_is_well_formed_and_stale": (
        "cf53be36ba2fdcd48ac8af7c84b93cd4cf5683da1a26b1dff1e3d46011d2dca0"
    ),
    "opt_in_is_bound_to_the_revision": (
        "f7ca08752977ae307c50e00a3b424f05deb678d5f2c806061a087881172bb959"
    ),
    "opt_in_is_bound_to_the_install": (
        "bb0fa9f5f92ec3cef80b5eee12ce975646ecf2283b7480ca344de6c27e0c5379"
    ),
    "opt_in_preimage_known_answer": (
        "8260471ac590ca2987fa1b97b6da0331910ac5f85cff38cc32b7ec4176085680"
    ),
    "cloud_on_still_blocks_a_sensitive_or_unknown_collection": (
        "7325de3d1373fa35450696cdb03e67ec16182b9c439613eb8778c4191c16fda6"
    ),
    "switch_is_checked_before_the_flag": (
        "f08cc2b3d434593401aa0c73efbf76d0970196a13289736378b4fe5197e5d04c"
    ),
    "unknown_install_reads_off_even_when_another_is_on": (
        "4c434d685cb90acdf3e462bf8da0e02ed2370bbe5156e7e25d077490cc5011bf"
    ),
    "well_formed_opt_in_on_turn_off_is_ignored": (
        "93e528c2f9677b304a1f317ef96e9fd23537df9590f822a82f7a7cd0aef2e14a"
    ),
    "turn_off_applies_before_the_next_verdict": (
        "3e7a589490599b5bdf95af9b53f929bca1c1c73940ddf401a57f0bcad2fff998"
    ),
    "verdict_is_read_at_send_time": (
        "354df0db12fadb64f36f519d2d76776e2196b8f6ccb216c9c998f1c2b2d3207c"
    ),
    "transition_extra_key": ("2324ecd5dd0d8f8c6074a97135a0e72e241eeaefd2d76c84b0c049683ee820b4"),
    "transition_missing_install_id": (
        "55953d1f5100293f4374702f8169a263c0966583bb0b3d1d391ba721d92c1725"
    ),
    "transition_missing_cloud_mode": (
        "ea98116b68b4f3c6661ac3436a700d229094cb945e6c13e43002f882ed90a29b"
    ),
    "transition_missing_expected_revision": (
        "977ecab02c06528ed4f84ab8061bce8eaa119617c4d3a108b90aedf7e7d8cca1"
    ),
    "transition_missing_opt_in": (
        "31f6b24a198b12f155b4ebe6aefb686a615c1c1e04a6384d9ac7941b7a0ab376"
    ),
    "transition_request_not_a_dict": (
        "d7807dfe8db1df2f1be6112871f04bbf998d10c36e3d0ace86d33e5d9096667c"
    ),
    "transition_request_is_a_list": (
        "a2fab531e03f5075da94c4e0fbe702f164249aa5741a5dd23cdf4250e70b9ddb"
    ),
    "transition_non_str_key": ("e4185f99efd7832e750784ecdabb1259a2d57b17c2e86e6b3407b5d831a17ef6"),
    "install_id_uppercase_hex": (
        "d2f86922bfc18628cae13a85f5bf3aafc2807a70c2f237be9006a91827cc65ac"
    ),
    "install_id_short_hex": ("04466fa4002e204d6d69867c9618138d54d11edf8ce25adf3e70553896ceaad7"),
    "install_id_long_hex": ("0efe4ad12350feded443463e30fe948c01f7358a2109d6e478b77b818c001675"),
    "install_id_wrong_prefix": ("88f6d45cdc0f652ddbeca29dbd8af5f9c364a4725f34f66cc0860d0fc293ea4f"),
    "install_id_trailing_newline": (
        "d89460c9689e162af18c9c331c7a7593350f8059d9ca0f653ce5eed7c10d1a4f"
    ),
    "install_id_empty": ("c2308f7fc224f780d001cb0fc96be4f62145bbdb014bcd5f0f3ce085455df056"),
    "install_id_not_hex": ("de046fd32108ba295bccf985734c164d78f6a55c0287e23b32ea50aee9602c48"),
    "install_id_int": ("3d508572ccfc7d0996c6ed06ac2003262d11f16ef634e3e4c1265a6b0cf3220e"),
    "install_id_bytes": ("cf51d4e12d37b12d0d8a95773437ace3717d0aaf11cb5b8b5d3089a23be1288c"),
    "install_id_none": ("e6cc354185b5a479699c1fee87a5d439a0b180b232cf4b236de7db80aa247dbc"),
    "cloud_mode_uppercase": ("0acc7493ce469a02b681ced8d1a9ecf429dd593a2abc99550a10c32371c2ad1b"),
    "cloud_mode_true_str": ("1c23148bdaa5e7c048c6c61638a5532d8c67055187e3ea5768893ebe05c06da0"),
    "cloud_mode_none": ("d2ef16008707bb6fbe94e2ccbe27869199ead3c262a570633f70de07557cadc4"),
    "cloud_mode_bytes": ("7fc32a80a9d17aa33498bf37718de995090c362d3b9e83066936fe7f4cd66276"),
    "cloud_mode_empty": ("bfc1ee087cc76e248571da7f30e2835914253097ace4e636aa5c7ab437987481"),
    "cloud_mode_int_one": ("2fcf0171b99a6f2774a1cba9f601c06028824a939bb8311291615eb0adca2c89"),
    "cloud_mode_bool_true": ("a586aa24f546d1c7eb30537d295466e76dfe0d6e090400a1cd08731325485b23"),
    "cloud_mode_padded": ("66028faea8f03322bed7d38d82493011f20ae4d3f741cae5f6f96a5113682dae"),
    "expected_revision_negative": (
        "33b210fdbcef30d9604ccab42a9b250a03efeb6c81b9de3637b376d4c768579f"
    ),
    "expected_revision_bool_false": (
        "79f3575dec68447cff9ea609d671a480aac3d96643d53a42eda25c5d522d53b9"
    ),
    "expected_revision_float_zero": (
        "6ed08a29857da45c14049f883302f8b1641398bf09e04d3970c38c519d744a71"
    ),
    "expected_revision_str_zero": (
        "0a54de85608470b380324f89d8e48092abcdc122c36bd1c604f9d45c26fcaf5f"
    ),
    "expected_revision_two_pow_63": (
        "6e415de75be6861be5f6ff8cc1be65a4084c7745a4bc8af485a032a7485f32e8"
    ),
    "expected_revision_huge": ("0fec14719fac0c91d20e5cc6d3f743773c304b44870c4e52b18fdad9cbee0e04"),
    "expected_revision_none": ("b241c8f197343373c9056fa61e625ca92d376e163b626fc22695be24e1712f3c"),
    "opt_in_bad_prefix": ("6426bcfaa748517f34ac1e1fb5ca0be02177fe95a0d14c5f103922dd0a5ae788"),
    "opt_in_short": ("22740a5a81eb2924a1415d0bc1b8c80c8a1bb7cff12937afc23ec428ebc119f7"),
    "opt_in_uppercase": ("ffba6a6017cc29680657c9fa7ee8547e163826f81ceb2d57c297a08965a10484"),
    "opt_in_int": ("9f2618ca1a5faeaee6acb2e38db32f562fbd972709c17754800bdaaab9c4cf48"),
    "opt_in_bytes": ("e04af8ec3768a3cecacb5cd8ba1b14973d0e359a5acee1654bd7807726c3bcbf"),
    "opt_in_trailing_newline": ("e4056327bd54de78473ccc486fa1583744049a65b6152c771fd58395ed90f2bc"),
    "opt_in_empty": ("99106a6189e01a5e6fe7c5566eac9c9164ceac3441007c0c9d4a9e0335a600fb"),
    "malformed_opt_in_on_turn_off": (
        "e705ea5e97f8a78b1382d1e284985e98b5ebf46c65a3821ef0d3943fc75d6692"
    ),
    "malformed_opt_in_precedes_unknown": (
        "3d38d43b744b435d25b524188b021b26cdd82029de58a8f0a61a1e1a780ee22c"
    ),
    "verdict_extra_key": ("fdc94260a485265fd9b7984575717447959d9c541d28383fe264f054f75ff23c"),
    "verdict_missing_install_id": (
        "84edce0eb8146e97cab5a335ad3f2677bd923c31e4aed526782d5da0313c194f"
    ),
    "verdict_missing_collection_id": (
        "059c07ee4cd421eaa5da5734b3af031431af5e273942ef3d6f53d74acaec7b05"
    ),
    "verdict_missing_destination": (
        "020ab78da39ca71c698b5d1fa926437de2bde08f00b7a2cc205ba539360a3a03"
    ),
    "verdict_request_not_a_dict": (
        "33aaa4a176b007852fc20f5b0958496a9f98584a014d868fa57eed27cc37e2bf"
    ),
    "collection_id_uppercase_hex": (
        "a9f1fe1d92c51798cb6b6f160803fd48270dcb828a3829e39ef6676ceac17112"
    ),
    "collection_id_short_hex": ("1c4e07f73008385ca3f72077c4be015e4dae4f3adab777ff7680f740eb74bf8b"),
    "collection_id_wrong_prefix": (
        "cf8e1828ba3b438e80d1148b8425c055ea1195b5c7e143252eb7fa8fd9adf045"
    ),
    "collection_id_trailing_newline": (
        "52c5f1a834390e1fdbf74ac5d965dfad6ff7db1feec83da8ce66318724b7cfd2"
    ),
    "collection_id_empty": ("fb6b01203049ba9d9144a7082c66d9a12f9e3f0cfb8a8838f034985e06e79979"),
    "collection_id_not_hex": ("c57c7bc033d76c98cb85488a752e45f0966c6b0c02cc8f676a89ca31b567e02e"),
    "collection_id_int": ("cab87592534c2f2d41efe65b946f573b5692235264e0a16825911586629ccd11"),
    "collection_id_none": ("8b065e6b288fe46c8416a8a638899bbf2f6a3816111b58b2d7f87576c7c200f0"),
    "destination_uppercase": ("9583ca87d40fedab2be007bf52e8ba24bae228bd9ba5509f0c26f0aa8d618ad6"),
    "destination_edge": ("7513d61db367fa3da25e4d8b53b2b715de0596fb91a7c7fab5c8ebe96891c3f4"),
    "destination_none": ("fd89ec8a82680826b5c3e053f62473e229485cd88d9b29178f0bd48c6f6561bf"),
    "destination_bytes": ("91f0d7ffe96ad12f0e5d65af1d2fad8f88c80a58136a152bc9619c3b0fc67bd7"),
    "destination_empty": ("b7fa157afc7539f240f6baa8a0a2e0cfb4bc8640e119711a098be3740befab97"),
    "destination_padded": ("26bae3a45d2a5fb72f002b7e22d525497eab2a662556af8d40d8018eae977350"),
    "verdict_install_id_bad": ("3022b8e6b961cd3ff1035e6cefb15d04cf482a10b92fe6d05fba378abb3fdb3a"),
    "register_bad_id": ("4ead2c37b84cf28c5c89144c502e50cb46e26c3c8e1b79486772383a91a3533f"),
    "register_non_str_id": ("08c4a6874d3bb93f648f1883aed5836ab208044ce6cfe651038209ea705e95fa"),
    "unknown_install_turn_on": ("538089938c5c8922beea486a98726073039f7436dd576b6d08c8688b8f86ec0d"),
    "unknown_install_turn_off": (
        "64f79a5a3a3d1d0ad470ef8bec13339b34cb6d52de70d2ab977f16be20ed3b7c"
    ),
    "unknown_precedes_stale": ("225f2e3ea05eb322cfed2fbdd529ddbea95f2c3edee9b589fd8ed7e999d4d146"),
    "stale_revision_transition": (
        "78ebef696df57612dc56d0a220b8eda7a516e411e9a7b3111ef6be3c7dd77332"
    ),
    "stale_revision_ahead": ("98071e1df5f14532c2cf058c6617aa23e8fad20721f7d64c3de0c11689beb4f3"),
    "stale_revision_behind": ("3cf135ca4224da0117acdcb071e74cc0b611ca7537a0afa02e79a4c285711307"),
    "stale_revision_same_value_target": (
        "1d98f854ad3ba23b5976a8c74f63e434804ab4133cce5a9946e0dbf8330fedb3"
    ),
    "stale_precedes_opt_in": ("8a0d1a0a32517be5bea543a21bd59d1d3b7f6d152f7400b685325e7cf0d4098a"),
    "opt_in_missing_none": ("17e3c99b24e3721bcbfe11ddd864617bb50e5a34d77943af04200aa6cedf6e5c"),
    "opt_in_foreign_install": ("bf9759cab8cf8bd254ac3239c75f0f55bea18451c407f82eb9781f07451f98a1"),
    "opt_in_stale_bound": ("81f35e495beecbf6a77e9d0c0cff6515dfebcff6d7131d8370048bf2ae9c5e20"),
    "opt_in_preimage_target_off": (
        "749f769d38735c9f88b11d7599b11c83bc462d36a7bb3e32b59a5060a462568d"
    ),
    "opt_in_preimage_colon_separator": (
        "a499b90f800c224177ab1d186e8c52ed84ca8afb4c3802f9555c926b203fd8b4"
    ),
    "opt_in_preimage_trailing_separator": (
        "955101cf555d181b19f5e21f8a81a35e4d8219ee45538cd3993c3aa004ae456f"
    ),
    "opt_in_preimage_leading_zero_revision": (
        "d9a2bf93d972c2637d0ad63eb653ca29367a8ef5d276e2e3e837b525f07f366f"
    ),
    "opt_in_preimage_plus_revision": (
        "5cff26bb952e3f0e378d9539187035b768a24c46b695cd91b43c4ead3b0bc974"
    ),
    "opt_in_preimage_uppercase_target": (
        "9dfb41c3e2a502cf34bc9e2e7b1d82cc9edec548574b12d0ea33612c021950f1"
    ),
    "opt_in_preimage_utf16": ("582cacaa56a4661f112e47787368003b112e4d06b4b07980779781fe5ee4d20f"),
    "cloud_verdict_while_off": ("e66a9df3928c65c2329e67b6c54643fa50ffce96265d28f8b6abddbd9ad2d308"),
    "cloud_verdict_after_turn_off": (
        "79bbd25988f97f56e320921217789bdfa29a508a6d7e033c2b0ac0fcd70b42c4"
    ),
    "cloud_verdict_unregistered_install": (
        "de653a9bd1f0b705d83ff52f48056c5ee609aa21550bdaed347dbd2840fd6988"
    ),
    "cloud_verdict_off_beats_sensitive": (
        "564e0fb1de977841f2328f89e7404c02bb2b6a473316b66eea8c3e97886eef3f"
    ),
    "cloud_verdict_sensitive_collection": (
        "37361f799de4596ecfeae8f6d8d495183b307c3180f0b214b36b5ff74f7c9bb6"
    ),
    "cloud_verdict_unregistered_collection": (
        "a87fe8408a95dd1e3a9ee95856c1267a5862f84ca1385ffab1f731e17bcfb20c"
    ),
    "cloud_verdict_after_flag_raise": (
        "f36d5a08a17ecb0807e8ebe4221c3dfbe88c342a9eafacc3cf9c2cb3223208e0"
    ),
    "turn_on_at_max_revision": ("6dc927cf7a35662f2b27c2aee0643e870855d01a30efad96a29521b4c7e464e5"),
    "turn_off_at_max_revision": (
        "435ffc66b5f5b79c9969407ff513fd15806ad2719814de66cd37c97a8462e6f2"
    ),
    "malformed_leaves_state": ("3c864a438dc491132124cb3fae314ec6b5b536e3f3e94d55d03057065843b5cd"),
    "unknown_install_leaves_state": (
        "1f9e8f10f59e7bee67f42f2098a358ef64617173bed54de7d9f3083f492a1440"
    ),
    "stale_revision_leaves_state": (
        "e2af103f5df990e250d407316f34c6b5f28b547216cec98b28611da1dfc0901e"
    ),
    "opt_in_missing_leaves_state": (
        "526c50c5e8f9b9332c1dbddc9577a1a1ce0609103922892738af4132dceb9663"
    ),
    "cloud_off_leaves_state": ("9706aade1f43e05899c81756a1e0f5063c01b5b40a39eeecb791f07e58e243db"),
    "collection_sensitive_leaves_state": (
        "b36d380d6d0cc4b68caef0315e78ff64d8c17c2a4279fcf038f9f2a85baf442e"
    ),
    "revision_exhausted_leaves_state": (
        "1eeece0e4168715628359c99d32a1b0cbc9f783866c6955f91cb129a36911f88"
    ),
    "rejected_turn_on_does_not_consume_the_opt_in": (
        "7aeebedcd1dc5e8682bf3d9903d87c2e16e78cde0b527a1ce9724508d9aa80f3"
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
    assert len(FAILURE_CLASSES) == 7
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
        "cloud_mode_off",
        "collection_sensitive",
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
    row = _row("happy", "turn_on_with_bound_opt_in_opens_cloud_for_an_open_collection")
    on = next(s for s in row["steps"] if s["op"] == "transition")
    assert on["request"]["opt_in"] == "opt1:" + hashlib.sha256(f"{INS}|0|on".encode()).hexdigest()
    assert on["expect"]["record"] == {"install_id": INS, "cloud_mode": "on", "revision": 1}
    row = _row("happy", "register_defaults_off_local_allowed")
    first = next(s for s in row["steps"] if s["op"] == "register")
    assert first["expect"]["record"] == {"install_id": INS, "cloud_mode": "off", "revision": 0}


def test_opt_in_known_answer_row():
    row = _row("boundary", "opt_in_preimage_known_answer")
    token = next(s for s in row["steps"] if s["op"] == "transition")["request"]["opt_in"]
    assert token == "opt1:" + hashlib.sha256(b"ins1:" + b"1" * 64 + b"|0|on").hexdigest()
    assert token == opt_in_for(INS, 0)
    assert token != opt_in_for(INS, 1) and token != opt_in_for(INS2, 0)


def test_turn_off_rows_pin_the_kill_switch():
    row = _row("happy", "turn_off_needs_no_opt_in")
    off = next(
        s for s in row["steps"] if s["op"] == "transition" and s["request"]["cloud_mode"] == "off"
    )
    assert off["request"]["opt_in"] is None
    assert off["expect"]["record"] == {"install_id": INS, "cloud_mode": "off", "revision": 2}
    row = _row("boundary", "turn_off_applies_before_the_next_verdict")
    assert [s["expect"] for s in row["steps"][-3:]] == [
        {"verdict": "allow"},
        {"record": {"install_id": INS, "cloud_mode": "off", "revision": 2}},
        {"failure": "cloud_mode_off"},
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
        with pytest.raises(CloudError) as info:
            sw.verdict({"install_id": INS, "collection_id": A, "destination": "cloud"})
        errors.append(info.value)
    assert errors[0] is not errors[1]
    for exc in errors:
        assert exc.failure_class == "cloud_mode_off" and exc.code == "cloud_off"
        assert exc.__cause__ is None and exc.__context__ is None


def test_collection_sensitive_maps_to_the_cloud_off_code():
    assert (
        FAILURE_MAPPING["collection_sensitive"] == FAILURE_MAPPING["cloud_mode_off"] == "cloud_off"
    )
    sw = world()
    sw.register(INS)
    sw._state[INS] = ("on", 1)
    with pytest.raises(CloudError) as info:
        sw.verdict({"install_id": INS, "collection_id": B, "destination": "cloud"})
    assert info.value.failure_class == "collection_sensitive"
    assert info.value.code == "cloud_off"


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
    sw = world()
    assert exec_step(sw, mal["request"]) == {"failure": "malformed_cloud_request"}
    for name, failure in (
        ("unknown_precedes_stale", "unknown_install"),
        ("stale_precedes_opt_in", "stale_revision"),
        ("stale_revision_same_value_target", "stale_revision"),
        ("cloud_verdict_off_beats_sensitive", "cloud_mode_off"),
    ):
        assert _row("malformed", name)["expect_failure"] == failure
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
            except CloudError as exc:
                got = {"failure": exc.failure_class}
            except Exception as exc:  # noqa: BLE001 - a raw error is a failed variant
                got = {"raw": type(exc).__name__}
            ok = (
                got == {"failure": "malformed_cloud_request"}
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
    assert len(_ACCEPTED) >= 40
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
        "value-cloud_mode-str-sub",
        "value-cloud_mode-bytes",
        "value-expected_revision-bool",
        "value-expected_revision-int-sub",
        "value-expected_revision-float",
        "value-opt_in-str-sub",
        "value-opt_in-empty",
        "value-opt_in-bytes",
        "value-collection_id-str-sub",
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
    cls = type("Mutant", (CloudSwitch,), methods)
    return lambda: cls(FlagStore())


_orig_opt = _ref.opt_in_for
_orig_bump = _ref._bump
_orig_ok = _ref._opt_in_ok


def _m_default_on(monkeypatch):
    def register(self, iid):
        CloudSwitch.register(self, iid)
        self._state.setdefault(iid, ("on", 0))
        if self._state[iid] == ("off", 0):
            self._state[iid] = ("on", 0)
        return self.record(iid)

    return _sub(register=register)


def _m_reregister_resets(monkeypatch):
    def register(self, iid):
        CloudSwitch.register(self, iid)
        self._state[iid] = ("off", 0)
        return self.record(iid)

    return _sub(register=register)


def _m_opt_in_ignored(monkeypatch):
    return _patch(monkeypatch, _opt_in_ok=lambda req, iid: True)


def _m_opt_in_unbound_revision(monkeypatch):
    return _patch(monkeypatch, opt_in_for=lambda iid, rev, target="on": _orig_opt(iid, 0, target))


def _m_opt_in_unbound_install(monkeypatch):
    return _patch(monkeypatch, opt_in_for=lambda iid, rev, target="on": _orig_opt(INS, rev, target))


def _m_opt_in_target_off(monkeypatch):
    return _patch(monkeypatch, opt_in_for=lambda iid, rev, target="on": _orig_opt(iid, rev, "off"))


def _m_turn_off_needs_opt_in(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("cloud_mode") == "off" and req.get("opt_in") is None:
            iid = req.get("install_id")
            state = self._state.get(iid) if type(iid) is str else None
            if state is not None and state[0] == "on" and req.get("expected_revision") == state[1]:
                _ref._fail("opt_in_missing")
        return CloudSwitch.transition(self, req)

    return _sub(transition=transition)


def _m_malformed_opt_in_ignored_on_off(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("cloud_mode") == "off":
            req = {**req, "opt_in": None}
        return CloudSwitch.transition(self, req)

    return _sub(transition=transition)


def _m_cas_off(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("install_id") in self._state:
            req = {**req, "expected_revision": self._state[req["install_id"]][1]}
        return CloudSwitch.transition(self, req)

    return _sub(transition=transition)


def _m_same_value_bumps(monkeypatch):
    def transition(self, req):
        out = CloudSwitch.transition(self, req)
        iid = req["install_id"]
        mode, rev = self._state[iid]
        if rev == req["expected_revision"] and mode == req["cloud_mode"]:
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
                and req.get("cloud_mode") == "on" == state[0]
                and req.get("opt_in") is None
                and req.get("expected_revision") == state[1]
            ):
                _ref._fail("opt_in_missing")
        return CloudSwitch.transition(self, req)

    return _sub(transition=transition)


def _m_same_value_before_stale(monkeypatch):
    def transition(self, req):
        state = self._state.get(req.get("install_id")) if type(req) is dict else None
        if (
            state is not None
            and req.get("cloud_mode") == state[0]
            and type(req.get("cloud_mode")) is str
        ):
            try:
                return CloudSwitch.transition(self, {**req, "expected_revision": state[1]})
            except CloudError:
                pass
        return CloudSwitch.transition(self, req)

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
        return CloudSwitch.transition(self, req)

    return _sub(transition=transition)


def _m_unknown_reads_on(monkeypatch):
    def reads_off(self, iid):
        state = self._state.get(iid)
        return state is not None and state[0] != "on"

    return _sub(reads_off=reads_off)


def _m_off_not_applied(monkeypatch):
    def transition(self, req):
        out = CloudSwitch.transition(self, req)
        if out["cloud_mode"] == "off" and req["cloud_mode"] == "off":
            before = self._state[req["install_id"]]
            if before[1] != req["expected_revision"]:
                self._state[req["install_id"]] = ("on", before[1])
        return out

    return _sub(transition=transition)


class _NeverSensitive:
    def reads_sensitive(self, cid):
        return False


def _m_flag_ignored(monkeypatch):
    class Mutant(CloudSwitch):
        def verdict(self, req):
            real = self._flags
            self._flags = _NeverSensitive()
            try:
                return CloudSwitch.verdict(self, req)
            finally:
                self._flags = real

    return lambda: Mutant(FlagStore())


class _Lenient:
    def __init__(self, flags):
        self._f = flags

    def reads_sensitive(self, cid):
        return cid in self._f._state and self._f.reads_sensitive(cid)


def _m_unknown_collection_allowed(monkeypatch):
    class Mutant(CloudSwitch):
        def verdict(self, req):
            real = self._flags
            self._flags = _Lenient(real)
            try:
                return CloudSwitch.verdict(self, req)
            finally:
                self._flags = real

    return lambda: Mutant(FlagStore())


def _m_flag_before_switch(monkeypatch):
    def verdict(self, req):
        if (
            type(req) is dict
            and req.get("destination") == "cloud"
            and type(req.get("collection_id")) is str
            and self._flags.reads_sensitive(req["collection_id"])
            and type(req.get("install_id")) is str
            and _ref._INS_RE.fullmatch(req["install_id"])
            and _ref._COL_RE.fullmatch(req["collection_id"])
            and set(req) == {"install_id", "collection_id", "destination"}
        ):
            _ref._fail("collection_sensitive")
        return CloudSwitch.verdict(self, req)

    return _sub(verdict=verdict)


def _m_sensitive_reports_cloud_off(monkeypatch):
    def verdict(self, req):
        try:
            return CloudSwitch.verdict(self, req)
        except CloudError as exc:
            if exc.failure_class == "collection_sensitive":
                _ref._fail("cloud_mode_off")
            raise

    return _sub(verdict=verdict)


def _m_verdict_cached(monkeypatch):
    cache = {}

    def verdict(self, req):
        key = (
            (req.get("install_id"), req.get("collection_id"), req.get("destination"))
            if type(req) is dict
            else None
        )
        if key in cache:
            return cache[key]
        out = CloudSwitch.verdict(self, req)
        cache[key] = out
        return out

    return _sub(verdict=verdict)


def _m_local_blocked_when_off(monkeypatch):
    def verdict(self, req):
        out = CloudSwitch.verdict(self, req)
        if req["destination"] == "local" and self.reads_off(req["install_id"]):
            _ref._fail("cloud_mode_off")
        return out

    return _sub(verdict=verdict)


def _m_local_blocked_by_flag(monkeypatch):
    def verdict(self, req):
        out = CloudSwitch.verdict(self, req)
        if req["destination"] == "local" and self._flags.reads_sensitive(req["collection_id"]):
            _ref._fail("collection_sensitive")
        return out

    return _sub(verdict=verdict)


def _m_subclass_str_ok(monkeypatch):
    return _patch(monkeypatch, _exact_str=lambda v: isinstance(v, str))


def _m_bool_revision_ok(monkeypatch):
    return _patch(monkeypatch, _exact_rev=lambda v: isinstance(v, int) and 0 <= v <= MAX_REV)


def _m_register_accepts_bad_id(monkeypatch):
    def register(self, iid):
        self._state.setdefault(iid, ("off", 0))
        return {"install_id": iid, "cloud_mode": "off", "revision": 0}

    return _sub(register=register)


MUTANTS = {
    "default_on": _m_default_on,
    "reregister_resets": _m_reregister_resets,
    "opt_in_ignored": _m_opt_in_ignored,
    "opt_in_unbound_revision": _m_opt_in_unbound_revision,
    "opt_in_unbound_install": _m_opt_in_unbound_install,
    "opt_in_target_off": _m_opt_in_target_off,
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
    "flag_ignored": _m_flag_ignored,
    "unknown_collection_allowed": _m_unknown_collection_allowed,
    "flag_before_switch": _m_flag_before_switch,
    "sensitive_reports_cloud_off": _m_sensitive_reports_cloud_off,
    "verdict_cached": _m_verdict_cached,
    "local_blocked_when_off": _m_local_blocked_when_off,
    "local_blocked_by_flag": _m_local_blocked_by_flag,
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
    "flip_pinned_mode": lambda r: _row_of(r, "happy", "turn_off_needs_no_opt_in")["steps"][-2][
        "expect"
    ]["record"].update(cloud_mode="on"),
    "bump_pinned_revision": lambda r: _row_of(
        r, "happy", "turn_on_with_bound_opt_in_opens_cloud_for_an_open_collection"
    )["steps"][-3]["expect"]["record"].update(revision=2),
    "change_expected_failure": lambda r: r["malformed"][0].update(expect_failure="stale_revision"),
    "valid_request_in_malformed": lambda r: r["malformed"][0].update(
        request=copy.deepcopy(r["malformed"][0]["minimal_repair"]["request"])
    ),
    "broken_repair": lambda r: r["malformed"][0]["minimal_repair"].update(
        request=copy.deepcopy(r["malformed"][0]["request"])
    ),
    "unrelated_follow_up": lambda r: r["rollback"][0]["follow_up"]["expect"].update(verdict="deny"),
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
