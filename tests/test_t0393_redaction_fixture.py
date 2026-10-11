"""T0393 closed redaction privacy fixture.

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

from tests import test_t0392_redaction_contract as ref

FIXTURE = Path(__file__).parent / "fixtures" / "redaction" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
PINNED = [
    ("empty", "72113c2ca0fbe50848652c4cbc3f02835afe410fd856164113238f7f690e1716"),
    ("ordinary", "745962ee3681f932a2d2d0dc414eebdaac7db5b90c0d73255ba02a6e26c2d3eb"),
    ("ascii-edges", "3dd8754df8f1669e079a2d84c771b901d5f272d5c97a7102a230b2a634f942a4"),
    ("marker", "5a2d8ccac3aba0ee496ea470615c968bae8594d830152f490ab55150167b4921"),
    ("token-31", "2e3ddc2e8b8dc80601fc922efcd547cf760c066edcf2ac44dcd48c66d8c77466"),
    ("token-32", "d93672e61f92405d5300f3b29c7b66fafb0a263dbc6870cc635b3c7c33ac8eab"),
    ("token-33", "b8e63c3c39a8a7e4296af1263e5e9a0ca78af9691e3dd01777cb2b3bee6d6a2d"),
    ("length-1023", "73484107c90cd9de0d404d22fa2bbd878c636cd9572ccb96b5e32b564086c2f2"),
    ("length-1024", "1958bb048d185ebf68bc09123f801e8dd18567f39f5bc4991277bc50f7c13005"),
    ("length-1025", "ded59ec9c70f1090780ea149db8b80fa22f6a93ec187d7ebd8c64d71f4e64d73"),
    ("secret-casefold", "6d571b3801392c79c76c8cd848b03e66bc93a6b2a176344baa643677546d2eda"),
    ("casefold-not-lower", "dc07f2595937426d53a9ff4214f54c65a7c5321131cd0eb47169bc2311a0c039"),
    ("secret-last", "aca3382bb3273f2aa9755964ba41a53b555f154517ed860cae42232097261e37"),
    ("one-char-secret", "ce398d47609d429b5ffa2d8421150c50ffa5d64cebc03932e1d1f594f1be8746"),
    ("marker-with-secret", "89aebe33649e3a2637c1617f6653d0cd5d5ca500680c6144259db0d15f11b6dd"),
    ("charset-10", "3133bf96aaefa5ce1ffdf648f325303030e8095245811cab2a9cb4a941d6daf2"),
    ("charset-9", "dac7bbc48c2711d23fed539b73d738018c203bafcb21e195f29404cc3281ea6f"),
    ("charset-0", "6de2849050010b852bde5e79698b248d581c63a47c0586b0c0aaf282cdee2325"),
    ("charset-31", "dc94f2d3d7a6d772ef65a0ff072456ad47f83e059b03fb48c4cfb93a6dbad777"),
    ("charset-127", "dd4d3ebe55f8e9bb1699cf36f6f4396e49d434dc68c98c2df7259cd100e02f92"),
    ("charset-233", "f11ffb29d4826859ba6676ffc6a7c5206316e50dc33ee79ced88db05799cc906"),
    ("charset-128578", "f04be255908438742a9712c604907e6d5dc16c7c6985ae25db7e9e182d396036"),
    ("run-char-43", "14417556be035324199104b02ef8b036999b32b2c8d333cbfe47bf7967c14f4c"),
    ("run-char-47", "a9aa9afdccf721ae6d50d330ae05fb4609a571443f29d0db1c4ea99f34b34357"),
    ("run-char-61", "a08b5009077daa9c9c96905b5e8ced1ec1fa3fd01b4e8d77cb1d913f4162429c"),
    ("run-char-95", "a7c1ad742bbfed12ac24910e10a8458fa77da52bfa8ac2c26fa1a6273d2e3889"),
    ("run-char-37", "752b338bb668ff294eca9431ea863b0d62ebb160e8240dee5a0f6d511e38e0c6"),
    ("run-char-45", "9b6d74866504f0b13a3f680683a94dc8b7c6312ad9958bc24926581c95f0e357"),
    ("break-run", "b9c775113c139000b8a49734d80fbef7676e76ad169dd0f3952f24bbe873c887"),
    ("64-secrets", "3378e0b9b552539913c642f4cef265ee0f91594be9eb78967f03017217b45ae7"),
    ("4096-secret", "0496f2e9b935d13204328aeb9cd10073c16c87f947738db6a5b630093402daf2"),
    ("none", "f5fde7602fbf02330872455d6c712e6a1632f05e1a9d810c51da351358477465"),
    ("list", "5ef53f1783c6355d1918297aef6eed0d22c1c36683e0e14c9331299f83806947"),
    ("missing", "e244e05af57330efac782fc0c7432946f8974e7d8c227deb754fbc8047485db1"),
    ("extra", "d74dc11882e0eb0ea9dba32cd9306c61a8d44f76a42787a0ad7a9bfd2e0b68ad"),
    ("text-int", "6b7ac5fbe55c60113e93d80e7f0d8ab0c60943307f2ea6ab8a0ac22038d3cb2c"),
    ("secrets-str", "d427d4e2af376d00839f88c746ca22030faa75e1757ee1d23cc2b6c6af87e280"),
    ("secret-empty", "d714d3097bb52e205543e0d69c9fd67c504e476ecd31fddbe94438f281287727"),
    ("secret-long", "53a2ef6f1e9a8adcc15f9d9c03e62b644e6101d61737c5bbb1df65044a941711"),
    ("secret-count", "669ebdc10bd48ea6243ec2eca171c2fa83d7376e870e6568e0c3e0c97595bf91"),
    ("secret-int", "74aa7fbf61fae13d7b401edee0534b3a158e3cd65640b8107bda7a3a3f31924b"),
    ("no-cached-secret", "23aaa4be5ceb12afbe1b2ee8df3560100b2b54711faa7a597f7aa12d76154aab"),
]


def digest(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def closure(doc):
    assert type(doc) is dict and set(doc) == {"schema", "contract", "sections", "rows"}
    assert type(doc["schema"]) is int and doc["schema"] == 1
    assert doc["contract"] == "privacy-redaction"
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
    assert config == {}
    return (ref.redact, ref.emit_text) if binding is None else binding


def state(context):
    return None


def execute(context, op, request):
    fn = context[0] if op == "redact" else context[1]
    return {"text": fn(request)}


class ForeignCrash(Exception):
    """An implementation exception, never a semantic kill."""


def call(fn, *args):
    try:
        return fn(*args)
    except ref.RedactionError:
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
        except ref.RedactionError as exc:
            assert type(exc) is ref.RedactionError
            assert exc.failure_class in ref.FAILURE_CLASSES
            assert exc.code == ref.FAILURE_MAPPING[exc.failure_class]
            assert exc.__cause__ is None and exc.__context__ is None
            got = {"failure": exc.failure_class}
            exact(state(context), prior)
        exact(rq, before)
        exact(got, step["expect"])
        # Stateless, no persistent data.


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


def identity(request):
    return request["text"]


def unsafe_sink(request):
    if type(request) is dict and type(request.get("text")) is str:
        return request["text"]
    return "redacted"


def always_redact(request):
    return "redacted"


def off_by_one(request):
    if len(request["text"]) == 32:
        return request["text"]
    return ref.redact(request)


def mutate(request):
    output = ref.redact(request)
    request["secrets"].append("synthetic")
    return output


def crash(request):
    raise KeyError("synthetic crash")


MUTANTS = {
    "raw": (identity, ref.emit_text),
    "overredact": (always_redact, ref.emit_text),
    "run-edge": (off_by_one, ref.emit_text),
    "input": (mutate, ref.emit_text),
    "sink": (ref.redact, unsafe_sink),
}
CrashOnly = (crash, crash)


def test_foreign_crash_does_not_count_as_a_semantic_kill():
    with pytest.raises(ForeignCrash):
        battery(CrashOnly)


HOSTILE_NAMES = [
    "dict_sub",
    "loud_dict",
    "list",
    "str",
    "none",
    "text_sub",
    "text_listsub",
    "text_dictsub",
    "missing_text",
    "real_key_strsub_text",
    "real_key_loud_text",
    "renamed_key_text",
    "secrets_sub",
    "secrets_listsub",
    "secrets_dictsub",
    "missing_secrets",
    "real_key_strsub_secrets",
    "real_key_loud_secrets",
    "renamed_key_secrets",
    "extra_key_plain",
    "extra_key_eq_raises",
    "extra_key_hash_collide",
    "bytes_key",
    "int_key",
    "text_bytes",
    "text_none",
    "text_int",
    "text_bool",
    "text_list",
    "text_loud",
    "text_loud_long",
    "text_loud_clean",
    "text_eq_raises",
    "text_hash_collide",
    "secrets_tuple",
    "secrets_set",
    "secrets_frozenset",
    "secrets_dict",
    "secrets_str",
    "secrets_none",
    "secrets_loud_list",
    "secrets_loud_list_empty",
    "secrets_item_strsub",
    "secrets_item_loud",
    "secrets_item_loud_after_good",
    "secrets_item_eq_raises",
    "secrets_item_hash_collide",
    "secrets_item_bytes",
    "secrets_item_none",
    "secrets_item_int",
    "secrets_item_list",
    "secrets_item_empty",
    "secrets_item_too_long",
    "secrets_too_many",
]


@pytest.mark.parametrize("name,rq", ref.ROWS, ids=[n for n, _ in ref.ROWS])
def test_hostile_fixture(name, rq):
    assert [n for n, _ in ref.ROWS] == HOSTILE_NAMES
    snap = ref._snap(rq)
    ref.CALLS.clear()
    with pytest.raises(ref.RedactionError) as caught:
        ref.redact(rq)
    assert type(caught.value) is ref.RedactionError
    assert caught.value.failure_class == "malformed_redaction_request"
    assert caught.value.code == "malformed_request"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert ref.CALLS == [] and ref._unchanged(rq, snap)
    assert ref.emit_text(rq) == "redacted"
    assert ref.CALLS == [] and ref._unchanged(rq, snap)


def ascii_only(request):
    if "é" in request["text"]:
        return request["text"]
    return ref.redact(request)


def ignore_secrets(request):
    return ref.redact(dict(request, secrets=[]))


def lower_not_casefold(request):
    if request["text"] == "SS" and request["secrets"] == ["ß"]:
        return "SS"
    return ref.redact(request)


MUTANTS.update(
    unicode=(ascii_only, ref.emit_text),
    secrets=(ignore_secrets, ref.emit_text),
    casefold=(lower_not_casefold, ref.emit_text),
)


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_semantic_mutants_fail_pinned_replay(name):
    with pytest.raises(AssertionError):
        battery(MUTANTS[name])


@pytest.mark.parametrize("exception", [AssertionError, KeyError])
def test_implementation_exceptions_are_foreign(exception):
    def broken(request):
        raise exception("implementation-only crash")

    with pytest.raises(ForeignCrash):
        battery((broken, broken))
