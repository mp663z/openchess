"""T0357 Privacy/sensitive flag/fixture.

A closed fixture for the T0356 sensitive-flag contract
(`tests/fixtures/sensitive_flag/cases.json`). Rows are scenarios replayed in
order on one store: happy, boundary, malformed (every failure class, each with
a single-locus minimal repair) and rollback (a rejected request leaves the
state untouched and the valid follow-up commits as pinned).

Pinned results come from the T0356 reference `FlagStore`. The fixture is closed
by name manifest, per-row sha256 digest and a replay that is checked against the
pinned results, so a substituted, swapped, dropped or retyped row fails on its
own. Hostile-type variants are derived from every accepted request. Reference
mutants and fixture edits must each turn the fixture red.
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

from tests import test_t0356_sensitive_flag_contract as _ref  # noqa: E402

FlagError = _ref.FlagError
FlagStore = _ref.FlagStore
approval_for = _ref.approval_for
FAILURE_CLASSES = set(_ref.FAILURE_CLASSES)
FAILURE_MAPPING = dict(_ref.FAILURE_MAPPING)
FIXTURE = Path(__file__).parent / "fixtures" / "sensitive_flag" / "cases.json"
MAX_REV = 2**63 - 1
A = "col1:" + "a" * 64
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "steps"}
BAD_KEYS = {"name", "defect", "expect_failure", "setup", "request", "minimal_repair"}
RB_KEYS = {"name", "why", "setup", "rejected", "expect_failure", "follow_up"}
STEP_OPS = {"register", "verdict", "transition", "rollback", "record", "seed"}


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


def exec_step(store, step):
    op, rq = step["op"], step.get("request")
    try:
        if op == "register":
            out = {"record": store.register(rq["collection_id"])}
        elif op == "verdict":
            out = {"verdict": store.verdict(copy.deepcopy(rq))}
        elif op == "transition":
            out = {"record": store.transition(copy.deepcopy(rq))}
        elif op == "rollback":
            out = {"record": store.rollback(copy.deepcopy(rq))}
        elif op == "record":
            out = {"record": store.record(rq["collection_id"])}
        elif op == "seed":
            store._state[rq["collection_id"]] = (
                rq["sensitive"],
                rq["revision"],
                list(rq["history"]),
            )
            out = {"seeded": True}
        else:
            raise AssertionError(op)
    except FlagError as exc:
        assert type(exc) is FlagError and exc.code == FAILURE_MAPPING[exc.failure_class]
        out = {"failure": exc.failure_class}
    return out


def _run(store, step):
    """exec_step, but an escaped non-typed exception is a crash outcome."""
    try:
        return exec_step(store, step)
    except Exception as exc:  # noqa: BLE001 - recorded as a crash, never as a kill
        return {"crash": type(exc).__name__}


def _kind(*outs):
    return "crash" if any("crash" in o for o in outs) else "mismatch"


def replay(cases, factory):
    """Every mismatch between the pinned results and the world under test.

    Entries are (section, row, where, kind). kind is "crash" when the world
    raised a non-typed exception and "mismatch" otherwise. A scenario stops at
    its first crash: nothing after it is observed, so downstream effects of a
    crash are never reported as semantic evidence. Each scenario starts from a
    fresh world, so later scenarios still count."""
    bad = []

    def run_all(world, steps):
        """Run setup steps; return a crash outcome or None."""
        for step in steps:
            out = _run(world, step)
            if "crash" in out:
                return out
        return None

    for section in ("happy", "boundary"):
        for row in cases[section]:
            store = factory()
            for i, step in enumerate(row["steps"]):
                got = _run(store, step)
                if got != step["expect"]:
                    bad.append((section, row["name"], i, _kind(got)))
                    if "crash" in got:
                        break
    for row in cases["malformed"]:
        store = factory()
        crash = run_all(store, row["setup"])
        if crash is not None:
            bad.append(("malformed", row["name"], "setup", "crash"))
        else:
            got = _run(store, row["request"])
            if got != {"failure": row["expect_failure"]}:
                bad.append(("malformed", row["name"], "rejects", _kind(got)))
        store = factory()
        crash = run_all(store, row["minimal_repair"]["setup"])
        if crash is not None:
            bad.append(("malformed", row["name"], "repair", "crash"))
        else:
            got = _run(store, row["minimal_repair"]["request"])
            if "failure" in got or "crash" in got:
                bad.append(("malformed", row["name"], "repair", _kind(got)))
    for row in cases["rollback"]:
        store = factory()
        if run_all(store, row["setup"]) is not None:
            bad.append(("rollback", row["name"], "setup", "crash"))
            continue
        before = copy.deepcopy(store._state)
        got = _run(store, row["rejected"])
        if got != {"failure": row["expect_failure"]}:
            bad.append(("rollback", row["name"], "rejects", _kind(got)))
            if "crash" in got:
                continue
        if copy.deepcopy(store._state) != before:
            bad.append(("rollback", row["name"], "state", "mismatch"))
        got = _run(store, row["follow_up"])
        if got != row["follow_up"]["expect"]:
            bad.append(("rollback", row["name"], "follow_up", _kind(got)))
    return bad


# ---- structure and closure -----------------------------------------------------------


def _is_step(step, with_expect):
    keys = {"op", "request"} | ({"expect"} if with_expect else set())
    return (
        type(step) is dict
        and set(step) <= keys | {"expect"}
        and step.get("op") in STEP_OPS
        and "request" in step
        and ("expect" in step) == with_expect
    )


def validate_structure(cases):
    assert type(cases) is dict and set(cases) == TOP_KEYS
    assert cases["schema"] == 1
    assert cases["contract"] == "privacy-sensitive-flag"
    assert cases["contract_base_path"] == "/privacy/sensitive-flag/v1"
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
        assert _is_step({k: v for k, v in row["follow_up"].items()}, True)


def _row_digest(raw_row):
    body = json.dumps(raw_row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()


MANIFEST = {
    "happy": [
        "default_is_sensitive_local_allowed",
        "lower_with_bound_approval_opens_cloud",
        "raise_needs_no_approval_and_blocks_next_verdict",
        "same_value_set_is_a_noop",
        "rollback_walks_back_one_step_per_call",
        "set_after_rollback_continues",
        "collections_are_independent",
        "reregister_keeps_state",
    ],
    "boundary": [
        "commit_just_below_max_reaches_max",
        "transition_at_max_is_revision_exhausted",
        "raise_at_max_is_revision_exhausted",
        "rollback_at_max_is_revision_exhausted",
        "same_value_at_max_is_a_noop",
        "unapproved_lower_at_max_is_approval_missing",
        "empty_history_rollback_at_max_is_empty_history",
        "max_revision_is_well_formed_and_stale",
        "approval_is_bound_to_the_revision",
        "approval_is_bound_to_the_collection",
        "approval_preimage_known_answer",
        "rollback_restoring_false_needs_approval",
        "rollback_restoring_true_needs_none",
        "empty_history_after_full_walk_back",
    ],
    "malformed": [
        "transition_extra_key",
        "transition_missing_key",
        "rollback_extra_key",
        "verdict_missing_key",
        "verdict_extra_key",
        "request_not_a_dict",
        "non_str_key",
        "collection_id_uppercase_hex",
        "collection_id_short_hex",
        "collection_id_long_hex",
        "collection_id_wrong_prefix",
        "collection_id_trailing_newline",
        "collection_id_empty",
        "collection_id_not_hex",
        "sensitive_int_one",
        "sensitive_int_zero",
        "sensitive_str_true",
        "sensitive_none",
        "expected_revision_negative",
        "expected_revision_bool_true",
        "expected_revision_float_zero",
        "expected_revision_str_zero",
        "expected_revision_two_pow_63",
        "expected_revision_huge",
        "expected_revision_none",
        "approval_bad_prefix",
        "approval_short",
        "approval_uppercase",
        "approval_int",
        "approval_bytes",
        "destination_edge",
        "destination_uppercase",
        "destination_none",
        "destination_bytes",
        "destination_empty",
        "register_bad_id",
        "malformed_approval_precedes_unknown",
        "unknown_collection_transition",
        "unknown_collection_rollback",
        "stale_revision_transition",
        "stale_revision_ahead",
        "stale_revision_rollback",
        "lower_without_approval",
        "lower_with_foreign_approval",
        "lower_with_stale_bound_approval",
        "rollback_to_false_without_approval",
        "cloud_verdict_while_sensitive",
        "cloud_verdict_after_raise",
        "cloud_verdict_unregistered_reads_sensitive",
        "rollback_on_empty_history",
        "transition_at_max_revision",
        "rollback_at_max_revision",
    ],
    "rollback": [
        "malformed_leaves_state",
        "unknown_collection_leaves_state",
        "stale_revision_leaves_state",
        "approval_missing_leaves_state",
        "egress_blocked_leaves_state",
        "empty_history_leaves_state",
        "revision_exhausted_leaves_state",
        "rejected_rollback_keeps_history",
    ],
}

ROW_DIGESTS = {
    "default_is_sensitive_local_allowed": (
        "cbe76de81870978e387ed3c4127a5d0fdb31439a68a3c9088a1da419f0470294"
    ),
    "lower_with_bound_approval_opens_cloud": (
        "9fbb049aecde899f5c2411acea9e8b8e1442f64e339c0cf94348112bcad4807d"
    ),
    "raise_needs_no_approval_and_blocks_next_verdict": (
        "340277839a2a594a4418464c03dd645314964304431393486860c4515b3270f2"
    ),
    "same_value_set_is_a_noop": (
        "c2228efcfdcef00e65d6318526b8ff55e06feb160ff23051fd68ecb8e5aebdf1"
    ),
    "rollback_walks_back_one_step_per_call": (
        "78eab3e97f8a6fef17068f86c39dee8da5ee825a557c8e242e0d82b073ead06e"
    ),
    "set_after_rollback_continues": (
        "db7e3f60d8c1158183399d6c2276424c9a7b645fc4606cf45a6cb7f6b7d8cfaa"
    ),
    "collections_are_independent": (
        "afa767ae64708b5fd5c9f2f4dd401c1b903b8371748b0a51c88846ccbccc2745"
    ),
    "reregister_keeps_state": ("cf18d6aba9f752df3e6b5e778ee71e9fe11855e31c171e1bea6620085b26114e"),
    "commit_just_below_max_reaches_max": (
        "7ade43b01d51b0543f40242548febbc1084ae0a327bd6219e82a38f7e5609d1c"
    ),
    "transition_at_max_is_revision_exhausted": (
        "95f42c684531aabec4aa020beb8aab18fb23695c0f09fa4cb0cfdf63e4fc2277"
    ),
    "raise_at_max_is_revision_exhausted": (
        "1d50385edc1be45d91afdc560a0d30f0ec2653a7ea50ec738cdb784d73ca4e0b"
    ),
    "rollback_at_max_is_revision_exhausted": (
        "ba05be8a46987c647c9f70d7e8c8fc0b6f21b7529cb1e6a13ffe2b9bcece6b4b"
    ),
    "same_value_at_max_is_a_noop": (
        "6a030f76d59027e260f929165ac39775754b7ca138b68d9273766d6333a2cc6e"
    ),
    "unapproved_lower_at_max_is_approval_missing": (
        "314ff2c0aa9a1085be7707913d55d6faad4a3b741b9038e513064f2bb518bf86"
    ),
    "empty_history_rollback_at_max_is_empty_history": (
        "2617a5d4e2efeca5af9bfb112877773c9ae08631b1665c72e43cbdbd73986e15"
    ),
    "max_revision_is_well_formed_and_stale": (
        "9897a0640a5f46659485f8e95c42d3cdf71401ac70143c89b8cb3b7938e96dd6"
    ),
    "approval_is_bound_to_the_revision": (
        "0dc3a68bc6e881a77e621fb9a27d280c63b0c55e8f2591820926c0597bcaa916"
    ),
    "approval_is_bound_to_the_collection": (
        "5c5e6e73e12a6a701df4c34b5a01d384ce302a555037074b633aab0b033e3711"
    ),
    "approval_preimage_known_answer": (
        "598008118a1957ca9534ea222eca9039f43cd2fc92b5deaf6151aa0b25964949"
    ),
    "rollback_restoring_false_needs_approval": (
        "4e60a99c0cb70fa5dd810baf9eac00105b2d60298f7a075395cb028703f55f43"
    ),
    "rollback_restoring_true_needs_none": (
        "5226e9ba2a15cdd60a7bd223fe5df06032d1ecf7d67cceedf5f58bfbf499c8e4"
    ),
    "empty_history_after_full_walk_back": (
        "0363a23cb73f4b4f10b5447a8897b717ac925d6d177317dd13e920958cae258e"
    ),
    "transition_extra_key": ("0fa9c3071ec232707119725834e8fbbff98e1260dbac6a57daac766beef03622"),
    "transition_missing_key": ("516c928ade98bad30a66ab55fe26ad2eea4155d48b57d3ab6160391ec2e08937"),
    "rollback_extra_key": ("256c3802621841c42df24c0e7e001e8c341f942d096847c75c5af4e0e3b2065b"),
    "verdict_missing_key": ("2e1c9abe37c9f720d2f43e87d2597743057da5d4be005d8452337168a368b444"),
    "verdict_extra_key": ("de11706417c2482a593299aabd9909149ac1cff3628d39f1bbdb0981f4a26704"),
    "request_not_a_dict": ("46564263d79610cfc96c25cea45de314f9031e138b42ffc45c02db2f057c9d89"),
    "non_str_key": ("8617e1d9ec91cd3cc4eb2654fae33e253f8823bfae38f4387186c8de946b40be"),
    "collection_id_uppercase_hex": (
        "9dc4dba2834e26c544fcaed476d4f868847ae38cc3989275391c5489a6c07b8f"
    ),
    "collection_id_short_hex": ("15f098dada5049b6ecf0db5a6da77d4ad99f383b06f47f38cb59ac50778a32bd"),
    "collection_id_long_hex": ("c1a139d53f05efca53fb1a0e7fecb5bbd75820b2ee77c2fb382e136c5b1f9847"),
    "collection_id_wrong_prefix": (
        "25f01aec7a0f1e03efb6617fd33a0d0d2ea749a846627e392b8b2aeb1e6dae60"
    ),
    "collection_id_trailing_newline": (
        "47143ad0b20a6449ee94e2176f743c0cf1d6c319d10d949c174365aa110ca5a5"
    ),
    "collection_id_empty": ("38648c025ac3ee0b04d4376c76a29dabee1f2aa9cd646b89818d848972432df1"),
    "collection_id_not_hex": ("180881d5538bbc29d7aeb0647c3592c852adf7c0d73b1124ed7570a900add819"),
    "sensitive_int_one": ("daa76d12c36858127e3ffe3766297c50fc931031d306569bf93a2f13b71fbe46"),
    "sensitive_int_zero": ("79b51d1132d75e9162a18ba0044b04f765b16a6cad9d103d5984d34020461acc"),
    "sensitive_str_true": ("fda6e77bd97bff6b5755ee4f0cb8e506c75db0d1aa4b55bca736a75e23e2958d"),
    "sensitive_none": ("319fde9c89bd6e41eeac2f7d28529f231acb15b69937eec919138d4bde589a5b"),
    "expected_revision_negative": (
        "e3ff691bcb19e863e085a796dc4aa5cce79bad2b24e70adacdb6da463da014f6"
    ),
    "expected_revision_bool_true": (
        "b414b6b8500d5c64143068e1ad583e65236fc0e2e0f879ebba4e37e9f933e121"
    ),
    "expected_revision_float_zero": (
        "6cbfe09d4eddb8712ad6584dbf0ce241ac85c8d26007b5fc685d9c91cf71213a"
    ),
    "expected_revision_str_zero": (
        "068b030fdb326c8391021b53b8dbae039612fac0e8da57eb21615b1c1791cd30"
    ),
    "expected_revision_two_pow_63": (
        "cadcedafd635cfec25d37f521b81d87ab0cfa869ad171b62e54f04f851339cbc"
    ),
    "expected_revision_huge": ("ea355a550af2123299df20d3ab2e090771cc1b622e09badccb622c8993a96f7a"),
    "expected_revision_none": ("c07a8294ff3488964c2626f16cf4c36775d41ca001c3d34da4a9dd9f7511756c"),
    "approval_bad_prefix": ("42e3257f23ae38d8f66828e77556e2dda9fdaaf033b8c069c0198e2a2d5ccd03"),
    "approval_short": ("646e29a69c6939e933fd30544f9e2c29efcb29e33a16dcd02adb759e4918c8bf"),
    "approval_uppercase": ("59920949d802e4a67ca93dcfe4d890062a22007a1e54e667f7559d2be99df4a9"),
    "approval_int": ("99e2c4607ad84434168266c65ed70a2b625d2c04e40867c87638d03ed2c2e1de"),
    "approval_bytes": ("1abfa015384e26909d75a822dbb36da85f36ab05c63619db255ec43afbd54363"),
    "destination_edge": ("4451b6d28bdc5d0b8d8bbad0a6c37e2192016f20cb6daf88dd01aefb008babfc"),
    "destination_uppercase": ("7fd474f885e3a62b08be4d6cdecc2082514a9953f9d659d9b4a4661aef924d93"),
    "destination_none": ("c0fb76f83b32a7eeae5a188130ef14b48d2c9dcb2fdb037d9008e54f8f98c638"),
    "destination_bytes": ("fb309317e5a381585f8ce02c011c51c3e8d1921a423a9cea2efb28da68dfbc37"),
    "destination_empty": ("7a11f9f58a921851a17bafb0b7fde6ad04853ffdf2e8fcaffd5a91a5bddf8587"),
    "register_bad_id": ("c586ed759451ebbd0d7e8692954be481b9a8c482551233c7c48f7846658f956d"),
    "malformed_approval_precedes_unknown": (
        "9a6401cc87a2b1718fa2a3516bc21ad769fbc4ba93f534bef429056ac2792afe"
    ),
    "unknown_collection_transition": (
        "596db86cd883d853916416711a91b1d5fc6a36ce5ab130b605c2ac55c337609c"
    ),
    "unknown_collection_rollback": (
        "651db2c06983a2116564fcd52ee883a2c12276fd4cac80bcfcafb70fd959ce38"
    ),
    "stale_revision_transition": (
        "cf78222430d4ba7f68c85055eb91cd47251b02eb2051934eb8a95dcfc42aa70c"
    ),
    "stale_revision_ahead": ("c24064c85a5365f2daf4fd8e5dc387e190432e718c59a1cc4d328a8c3270c374"),
    "stale_revision_rollback": ("b641e330fe5b2df8f27b3977e1965a12a8df6a566a56ef45a6b684369887e769"),
    "lower_without_approval": ("b7d35433e67e3d6abeb7c2e9f2b1dcd80bd91543327f9a31fd5c847153c8123a"),
    "lower_with_foreign_approval": (
        "a2e623b7a0c51125ebd213a6c90c2d34f8595e966ece6991fa68b3ee73d175ff"
    ),
    "lower_with_stale_bound_approval": (
        "e11606c1c6a7411edbd7f7d2b544eb4590065b600bb751c77e70aea0f7c5d24e"
    ),
    "rollback_to_false_without_approval": (
        "7b3d3264d53901aed6ccd02fc5e18a92cc050219b28625d644bc9d501d398588"
    ),
    "cloud_verdict_while_sensitive": (
        "c35f4ea67871b87201b2558ae15106cabdac07a365850b95dca8e461f7756046"
    ),
    "cloud_verdict_after_raise": (
        "aa9a6ef60c6e317fc38e552c13986be748a55bfb5b44608691aac6a45a590479"
    ),
    "cloud_verdict_unregistered_reads_sensitive": (
        "35c1d405da865aaf3222a6ff4895cd087985c179d7671ff9fc7ab9d756496aa2"
    ),
    "rollback_on_empty_history": (
        "23c8972a1fcfbc2562ca0a3e0a43c8badbf4406c472dadb381d5e7e0d4c4dea0"
    ),
    "transition_at_max_revision": (
        "99f70612587b432e684c5dd938dca8220385acc0e7e1bb4431b878aac2f0c9be"
    ),
    "rollback_at_max_revision": (
        "88a9e63b1b4add413fe61f859b22830502c934b44d1c6891ea1e10a57da9c055"
    ),
    "malformed_leaves_state": ("98170de5f5abc4bfce4c554b688e80e2a52aaa69d3aa8549a88520f5fdb82717"),
    "unknown_collection_leaves_state": (
        "275be23a5206e2c860d62c805799d23c9b72d49f02491b1f0d705c1448a2c0fa"
    ),
    "stale_revision_leaves_state": (
        "56e5e2254a3d5c9134da35a85186474540e3ffc08c3ed97fd0ff17574e42f793"
    ),
    "approval_missing_leaves_state": (
        "3cc585de30facf72bac0acf3e820ecadfc0fe6d65d6f4fd95e1d6c0a366b155e"
    ),
    "egress_blocked_leaves_state": (
        "7d7328fdde4d1ac614cd12f3f974ef562517dc6b5a1b7fa9a30ab40decdcb5cd"
    ),
    "empty_history_leaves_state": (
        "2790c2597d6aab0aeb734bbe8f7e9f3aaa0c7120420c9c4cdce39a556db99d80"
    ),
    "revision_exhausted_leaves_state": (
        "2ecec30bb5192bbd5f6b2a3577e79a436810ca485acae1fcd8e50daa8a32aa7c"
    ),
    "rejected_rollback_keeps_history": (
        "f246562bf66ccbd7e86a873b9811479473e018e0e160b1e624c7eeff044aa460"
    ),
}


def validate_closure(cases, raw, digests=True):
    validate_structure(cases)
    for section in SECTIONS:
        assert [r["name"] for r in cases[section]] == MANIFEST[section], section
    assert replay(cases, FlagStore) == []
    if digests:
        for section in SECTIONS:
            for raw_row in raw[section]:
                assert _row_digest(raw_row) == ROW_DIGESTS[raw_row["name"]], raw_row["name"]
    seen = {r["expect_failure"] for r in cases["malformed"]}
    assert seen == FAILURE_CLASSES
    assert {r["expect_failure"] for r in cases["rollback"]} == FAILURE_CLASSES
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
    assert {"revision_exhausted", "approval_missing", "empty_history", "stale_revision"} <= (
        boundary_failures
    )


# ---- pinned scenarios ----------------------------------------------------------------


@pytest.mark.parametrize("section", ("happy", "boundary"))
def test_pinned_scenarios(section):
    for row in CASES[section]:
        store = FlagStore()
        for i, step in enumerate(row["steps"]):
            assert exec_step(store, step) == step["expect"], (row["name"], i)


def test_happy_rows_only_accept():
    for row in CASES["happy"]:
        for step in row["steps"]:
            assert "failure" not in step["expect"], row["name"]


def test_default_and_lower_are_pinned_independently():
    row = _row("happy", "lower_with_bound_approval_opens_cloud")
    reg, lower, cloud, local = row["steps"]
    assert reg["expect"]["record"] == {
        "collection_id": A,
        "sensitive": True,
        "revision": 0,
        "prior": None,
    }
    assert lower["request"]["approval"] == (
        "apv1:" + hashlib.sha256(f"{A}|0|false".encode()).hexdigest()
    )
    assert lower["expect"]["record"] == {
        "collection_id": A,
        "sensitive": False,
        "revision": 1,
        "prior": True,
    }
    assert cloud["expect"] == {"verdict": "allow"} and local["expect"] == {"verdict": "allow"}


def test_approval_known_answer_row():
    row = _row("boundary", "approval_preimage_known_answer")
    token = row["steps"][1]["request"]["approval"]
    assert (
        token == "apv1:" + "10de3c993bc04182d7f3045a54ca8fa0" + "7c7afd741d604997103cdd7ddaf210af"
    )
    assert token == approval_for(A, 0)
    assert row["steps"][1]["expect"]["record"]["sensitive"] is False


def test_walk_back_rows_never_reapply_an_undone_value():
    row = _row("happy", "rollback_walks_back_one_step_per_call")
    first, second = row["steps"][3]["expect"]["record"], row["steps"][4]["expect"]["record"]
    assert (first["sensitive"], first["revision"], first["prior"]) == (False, 3, True)
    assert (second["sensitive"], second["revision"], second["prior"]) == (True, 4, None)
    assert row["steps"][5]["expect"]["record"] == second


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
    store = FlagStore()
    for step in row["setup"]:
        exec_step(store, step)
    state = copy.deepcopy(store._state)
    request = copy.deepcopy(row["request"])
    snap = _snapshot(request)
    got = exec_step(store, request)
    assert got == {"failure": row["expect_failure"]}
    assert _snapshot(request) == snap
    assert store._state == state


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_succeeds(name):
    row = _row("malformed", name)
    store = FlagStore()
    for step in row["minimal_repair"]["setup"]:
        exec_step(store, step)
    assert "failure" not in exec_step(store, row["minimal_repair"]["request"])


def test_rejections_are_fresh_typed_errors():
    store = FlagStore()
    store.register(A)
    errors = []
    for _ in range(2):
        with pytest.raises(FlagError) as info:
            store.verdict({"collection_id": A, "destination": "cloud"})
        errors.append(info.value)
    assert errors[0] is not errors[1]
    for exc in errors:
        assert exc.failure_class == "egress_blocked" and exc.code == "cloud_off"
        assert exc.__cause__ is None and exc.__context__ is None


def test_injected_valid_request_as_malformed_row_fails_replay():
    cases = copy.deepcopy(CASES)
    row = cases["malformed"][0]
    row["request"] = copy.deepcopy(row["minimal_repair"]["request"])
    assert replay(cases, FlagStore)


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback_rows(name):
    row = _row("rollback", name)
    store = FlagStore()
    for step in row["setup"]:
        exec_step(store, step)
    before = copy.deepcopy(store._state)
    assert exec_step(store, row["rejected"]) == {"failure": row["expect_failure"]}
    assert store._state == before
    assert exec_step(store, row["follow_up"]) == row["follow_up"]["expect"]


def test_validation_precedence_rows():
    mal = _row("malformed", "malformed_approval_precedes_unknown")
    assert exec_step(FlagStore(), mal["request"]) == {"failure": "malformed_flag_request"}
    ex = _row("boundary", "unapproved_lower_at_max_is_approval_missing")
    assert ex["steps"][1]["expect"] == {"failure": "approval_missing"}
    ex = _row("boundary", "empty_history_rollback_at_max_is_empty_history")
    assert ex["steps"][1]["expect"] == {"failure": "empty_history"}


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
        return hash("collection_id")

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)


def _accepted_requests():
    out = []
    for section in ("happy", "boundary"):
        for row in CASES[section]:
            for i, step in enumerate(row["steps"]):
                if step["op"] in ("verdict", "transition", "rollback") and (
                    "failure" not in step["expect"]
                ):
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
        if type(value) is str:
            for tag, make in (
                ("str-sub", _StrSub),
                ("eq-raises", _EqRaises),
                ("repr-raises", _ReprRaises),
            ):
                out.append((f"value-{key}-{tag}", {**base, key: make(value)}))
            out.append((f"value-{key}-bytes", {**base, key: value.encode()}))
        elif type(value) is bool:
            out.append((f"value-{key}-int", {**base, key: int(value)}))
            out.append((f"value-{key}-str", {**base, key: str(value).lower()}))
        elif type(value) is int:
            out.append((f"value-{key}-bool", {**base, key: bool(value)}))
            out.append((f"value-{key}-int-sub", {**base, key: _IntSub(value)}))
            out.append((f"value-{key}-float", {**base, key: float(value)}))
    return out


_ACCEPTED = _accepted_requests()


def run_all_steps(world, steps):
    """Run prior steps; return the first crash outcome, or None."""
    for step in steps:
        out = _run(world, step)
        if "crash" in out:
            return out
    return None


def _hostile_failures(factory, only=None):
    """Every hostile variant the store under test mishandles."""
    bad = []
    for label, step in _ACCEPTED:
        if only is not None and label != only:
            continue
        section, row_name, index = label.split("/")
        for tag, request in _variants(step):
            store = factory()
            store.register(A)
            if run_all_steps(store, _row(section, row_name)["steps"][: int(index)]) is not None:
                bad.append((label, tag, {"raw": "setup"}, []))
                continue
            state = copy.deepcopy(store._state)
            CALLS.clear()
            snap = _snapshot(request)
            try:
                got = getattr(store, step["op"])(request)
            except FlagError as exc:
                got = {"failure": exc.failure_class}
            except Exception as exc:  # noqa: BLE001 - a crash, tagged as such
                got = {"raw": type(exc).__name__}
            ok = (
                got == {"failure": "malformed_flag_request"}
                and CALLS == []
                and _snapshot(request) == snap
                and store._state == state
            )
            if not ok:
                bad.append((label, tag, got, list(CALLS)))
    return bad


@pytest.mark.parametrize("label", [label for label, _ in _ACCEPTED])
def test_hostile_variants_of_every_accepted_request(label):
    assert _hostile_failures(FlagStore, only=label) == []


def test_variant_set_is_complete():
    assert len(_ACCEPTED) >= 25
    ops = {s["op"] for _, s in _ACCEPTED}
    assert ops == {"verdict", "transition", "rollback"}
    tags = {t for _, s in _ACCEPTED for t, _ in _variants(s)}
    for needed in (
        "dict-sub",
        "list",
        "none",
        "extra-key",
        "int-key",
        "key-collection_id-str-sub",
        "key-collection_id-eq-raises",
        "key-collection_id-collides",
        "value-collection_id-str-sub",
        "value-collection_id-eq-raises",
        "value-collection_id-repr-raises",
        "value-collection_id-bytes",
        "value-sensitive-int",
        "value-sensitive-str",
        "value-expected_revision-bool",
        "value-expected_revision-int-sub",
        "value-expected_revision-float",
        "value-destination-str-sub",
    ):
        assert needed in tags, needed


# ---- reference mutants must turn the fixture red -------------------------------------


def test_reference_is_green():
    assert replay(CASES, FlagStore) == []


def _patch(monkeypatch, **attrs):
    for name, value in attrs.items():
        monkeypatch.setattr(_ref, name, value)
    return FlagStore


def _sub(**methods):
    return type("Mutant", (FlagStore,), methods)


_orig_for = _ref.approval_for
_orig_bump = _ref._bump


def _m_default_open(monkeypatch):
    def register(self, cid):
        self._state.setdefault(cid, (False, 0, []))
        return self.record(cid)

    return _sub(register=register)


def _m_reregister_resets(monkeypatch):
    def register(self, cid):
        self._state[cid] = (True, 0, [])
        return self.record(cid)

    return _sub(register=register)


def _m_approval_ignored(monkeypatch):
    return _patch(monkeypatch, _approval_ok=lambda req, cid: True)


def _m_approval_unbound_revision(monkeypatch):
    return _patch(monkeypatch, approval_for=lambda cid, rev: _orig_for(cid, 0))


def _m_approval_unbound_collection(monkeypatch):
    return _patch(monkeypatch, approval_for=lambda cid, rev: _orig_for(A, rev))


def _m_raise_needs_approval(monkeypatch):
    def transition(self, req):
        cid = req.get("collection_id") if type(req) is dict else None
        lowered = type(cid) is str and cid in self._state and self._state[cid][0] is False
        if lowered and req.get("sensitive") is True and req.get("approval") is None:
            _ref._fail("approval_missing")
        return FlagStore.transition(self, req)

    return _sub(transition=transition)


def _m_cas_off(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("collection_id") in self._state:
            req = {**req, "expected_revision": self._state[req["collection_id"]][1]}
        return FlagStore.transition(self, req)

    return _sub(transition=transition)


def _m_same_value_bumps(monkeypatch):
    def transition(self, req):
        out = FlagStore.transition(self, req)
        cid = req["collection_id"]
        sens, rev, hist = self._state[cid]
        if rev == req["expected_revision"] and sens == req["sensitive"]:
            self._state[cid] = (sens, _orig_bump(rev), hist + [sens])
            return self.record(cid)
        return out

    return _sub(transition=transition)


def _m_history_not_pushed(monkeypatch):
    def transition(self, req):
        cid = req["collection_id"] if type(req) is dict else None
        before = self._state.get(cid)
        out = FlagStore.transition(self, req)
        after = self._state[cid]
        if before is not None and after[1] != before[1]:
            self._state[cid] = (after[0], after[1], before[2])
        return self.record(cid) if out else out

    return _sub(transition=transition)


def _m_rollback_pushes(monkeypatch):
    def rollback(self, req):
        before = self._state.get(req.get("collection_id")) if type(req) is dict else None
        out = FlagStore.rollback(self, req)
        if before is not None:
            cid = req["collection_id"]
            sens, rev, hist = self._state[cid]
            self._state[cid] = (sens, rev, before[2])
            return self.record(cid)
        return out

    return _sub(rollback=rollback)


def _m_rollback_no_approval(monkeypatch):
    def rollback(self, req):
        if type(req) is dict and type(req.get("collection_id")) is str:
            cid = req["collection_id"]
            if cid in self._state and self._state[cid][2] and req.get("approval") is None:
                req = {**req, "approval": _orig_for(cid, req.get("expected_revision"))}
        return FlagStore.rollback(self, req)

    return _sub(rollback=rollback)


def _m_empty_rollback_noop(monkeypatch):
    def rollback(self, req):
        try:
            return FlagStore.rollback(self, req)
        except FlagError as exc:
            if exc.failure_class == "empty_history":
                return self.record(req["collection_id"])
            raise

    return _sub(rollback=rollback)


def _m_saturates(monkeypatch):
    return _patch(monkeypatch, _bump=lambda rev: min(rev + 1, MAX_REV))


def _m_wraps(monkeypatch):
    return _patch(monkeypatch, _bump=lambda rev: 0 if rev >= MAX_REV else rev + 1)


def _m_verdict_unknown_allows(monkeypatch):
    def reads_sensitive(self, cid):
        state = self._state.get(cid)
        return state is not None and state[0] is not False

    return _sub(reads_sensitive=reads_sensitive)


def _m_verdict_cached(monkeypatch):
    cache = {}

    def verdict(self, req):
        key = (req.get("collection_id"), req.get("destination")) if type(req) is dict else None
        if key in cache:
            return cache[key]
        out = FlagStore.verdict(self, req)
        cache[key] = out
        return out

    return _sub(verdict=verdict)


def _m_subclass_str_ok(monkeypatch):
    return _patch(monkeypatch, _exact_str=lambda v: isinstance(v, str))


def _m_bool_revision_ok(monkeypatch):
    return _patch(monkeypatch, _exact_rev=lambda v: isinstance(v, int) and 0 <= v <= MAX_REV)


def _m_int_for_bool(monkeypatch):
    def transition(self, req):
        if type(req) is dict and req.get("sensitive") in (0, 1) and type(req["sensitive"]) is int:
            req = {**req, "sensitive": bool(req["sensitive"])}
        return FlagStore.transition(self, req)

    return _sub(transition=transition)


def _m_exhausted_first(monkeypatch):
    def transition(self, req):
        state = self._state.get(req.get("collection_id")) if type(req) is dict else None
        if state is not None and state[1] >= MAX_REV:
            _ref._fail("revision_exhausted")
        return FlagStore.transition(self, req)

    return _sub(transition=transition)


def _m_unknown_before_malformed(monkeypatch):
    def transition(self, req):
        if (
            type(req) is dict
            and type(req.get("collection_id")) is str
            and req["collection_id"] not in self._state
            and _ref._COL_RE.fullmatch(req["collection_id"])
        ):
            _ref._fail("unknown_collection")
        return FlagStore.transition(self, req)

    return _sub(transition=transition)


def _m_register_accepts_bad_id(monkeypatch):
    def register(self, cid):
        self._state.setdefault(cid, (True, 0, []))
        return self.record(cid)

    return _sub(register=register)


MUTANTS = {
    "default_open": _m_default_open,
    "reregister_resets": _m_reregister_resets,
    "approval_ignored": _m_approval_ignored,
    "approval_unbound_revision": _m_approval_unbound_revision,
    "approval_unbound_collection": _m_approval_unbound_collection,
    "raise_needs_approval": _m_raise_needs_approval,
    "cas_off": _m_cas_off,
    "same_value_bumps": _m_same_value_bumps,
    "history_not_pushed": _m_history_not_pushed,
    "rollback_pushes": _m_rollback_pushes,
    "rollback_no_approval": _m_rollback_no_approval,
    "empty_rollback_noop": _m_empty_rollback_noop,
    "saturates": _m_saturates,
    "wraps": _m_wraps,
    "verdict_unknown_allows": _m_verdict_unknown_allows,
    "verdict_cached": _m_verdict_cached,
    "subclass_str_ok": _m_subclass_str_ok,
    "bool_revision_ok": _m_bool_revision_ok,
    "int_for_bool": _m_int_for_bool,
    "exhausted_first": _m_exhausted_first,
    "unknown_before_malformed": _m_unknown_before_malformed,
    "register_accepts_bad_id": _m_register_accepts_bad_id,
}


def kill_evidence(factory):
    """(semantic, crashes): semantic evidence is a pinned mismatch or a hostile
    variant that was accepted, refused with the wrong typed class, ran user code
    or changed an input or the state. A raw crash is recorded apart and is never
    a kill by itself."""
    semantic, crashes = [], []
    for entry in replay(CASES, factory):
        (crashes if entry[3] == "crash" else semantic).append(entry)
    for entry in _hostile_failures(factory):
        (crashes if "raw" in entry[2] else semantic).append(entry)
    return semantic, crashes


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_reference_mutants_turn_the_fixture_red(name, monkeypatch):
    factory = MUTANTS[name](monkeypatch)
    semantic, _crashes = kill_evidence(factory)
    assert semantic, f"mutant {name} was not killed by a semantic assertion"


def _crash_only(trigger):
    class CrashOnly(FlagStore):
        def transition(self, req):
            if trigger(req):
                raise KeyError("boom")
            return FlagStore.transition(self, req)

    return CrashOnly


def _has_non_exact_str_key(req):
    return type(req) is dict and any(type(k) is not str for k in req)


def _always(req):
    return True


@pytest.mark.parametrize("trigger", [_has_non_exact_str_key, _always], ids=["bad-keys", "always"])
def test_crash_only_mutants_are_not_semantic_kills(trigger):
    semantic, crashes = kill_evidence(_crash_only(trigger))
    # a raw error leaves no semantic evidence, whatever it triggers on: replay
    # stops a scenario at its first crash, so downstream effects never count
    assert crashes
    assert semantic == []


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
    "rename_row": lambda r: _row_of(r, "happy", "same_value_set_is_a_noop").update(name="x"),
    "flip_pinned_sensitive": lambda r: _row_of(r, "happy", "same_value_set_is_a_noop")["steps"][1][
        "expect"
    ]["record"].update(sensitive=False),
    "bump_pinned_revision": lambda r: _row_of(r, "happy", "lower_with_bound_approval_opens_cloud")[
        "steps"
    ][1]["expect"]["record"].update(revision=2),
    "change_expected_failure": lambda r: r["malformed"][0].update(expect_failure="stale_revision"),
    "valid_request_in_malformed": lambda r: r["malformed"][0].update(
        request=copy.deepcopy(r["malformed"][0]["minimal_repair"]["request"])
    ),
    "broken_repair": lambda r: r["malformed"][0]["minimal_repair"].update(
        request=copy.deepcopy(r["malformed"][0]["request"])
    ),
    "unrelated_follow_up": lambda r: r["rollback"][0]["follow_up"]["expect"]["record"].update(
        revision=9
    ),
    "drop_rollback_row": lambda r: r["rollback"].pop(),
    "retype_revision_edge": lambda r: _row_of(r, "boundary", "commit_just_below_max_reaches_max")[
        "steps"
    ][0]["request"].update(revision=2**62),
    "change_contract": lambda r: r.update(contract="x"),
    "extra_top_key": lambda r: r.update(extra=1),
}


@pytest.mark.parametrize("edit", sorted(FIXTURE_EDITS))
@pytest.mark.parametrize("digests", [True, False], ids=["with-digests", "closure-only"])
def test_fixture_edits_are_detected(edit, digests):
    raw = _edit(FIXTURE_EDITS[edit])
    if digests is False and edit in ("rename_row",):
        pass
    with pytest.raises(AssertionError):
        validate_closure(_dec(raw), raw, digests=digests)


def test_digest_table_detects_a_silent_retype():
    raw = _edit(
        lambda r: _row_of(r, "happy", "same_value_set_is_a_noop")["steps"][0]["request"].update(
            note="x"
        )
    )
    cases = _dec(raw)
    # structure and replay still pass: only the digest table notices
    validate_closure(cases, raw, digests=False)
    with pytest.raises(AssertionError):
        validate_closure(cases, raw, digests=True)
