"""T0392: privacy redaction contract behavior battery.

Reference engine derived from data/contracts/redaction.yaml. Free text
bound for a log line or an error message is returned verbatim or replaced
whole by the fixed marker "redacted", never partially masked. It is
redacted when it is longer than 1024 characters, holds any character
outside 0x20-0x7e, contains (casefolded) any secret the caller holds, or
holds a run of 32 or more token characters [A-Za-z0-9+/=_%-]. The engine
is stateless. A malformed request fails typed with inputs unchanged and no
caller method called; at a sink it emits only the marker.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.redaction_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_CLASSES,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

REPO = CONTRACT.parents[2]  # data/contracts/<name>.yaml; valid for battery copies too
DOC = yaml.safe_load(CONTRACT.read_text())
_CC = DOC["contract"]
_KEYS = set(_CC["request"]["fields"])
MARKER = "redacted"
_MAX_TEXT = 1024
_MAX_SECRETS = 64
_MAX_SECRET = 4096
_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")


class RedactionError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail():
    raise RedactionError("malformed_redaction_request")


def _request(req):
    if type(req) is not dict:
        _fail()
    for k in list(req.keys()):
        if type(k) is not str:
            _fail()
    if set(req) != _KEYS:
        _fail()
    text = req["text"]
    if type(text) is not str:
        _fail()
    secrets = req["secrets"]
    if type(secrets) is not list or len(secrets) > _MAX_SECRETS:
        _fail()
    for s in secrets:
        if type(s) is not str or not 1 <= len(s) <= _MAX_SECRET:
            _fail()
    return text, secrets


def _hits(text, secrets):
    if len(text) > _MAX_TEXT:
        return True
    for ch in text:
        if not " " <= ch <= "~":
            return True
    folded = text.casefold()
    for s in secrets:
        if s.casefold() in folded:
            return True
    return _RUN_RE.search(text) is not None


def redact(req):
    text, secrets = _request(req)
    return MARKER if _hits(text, secrets) else text


def emit_text(req):
    """The sink path: never raises for a malformed request, never emits raw."""
    try:
        return redact(req)
    except RedactionError:
        return MARKER


def _r(text, secrets=()):
    return redact({"text": text, "secrets": list(secrets)})


def _raises(fn, *args):
    with pytest.raises(RedactionError) as ei:
        fn(*args)
    assert ei.value.failure_class == "malformed_redaction_request"
    assert ei.value.code == "malformed_request"
    assert ei.value.__cause__ is None
    return ei.value


KEY = "sk-" + "live" + "Q7x"  # short secret, below the run length
LONGKEY = "Zq9" * 11  # 33 token characters


# ---- contract / lint ------------------------------------------------------


def test_contract_lints_clean():
    lint()


def test_error_model_is_closed_and_consistent():
    assert set(FAILURE_MAPPING) == set(FAILURE_CLASSES) == {"malformed_redaction_request"}
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert _CC["errors"]["shape"]["retryable_true_only_for"] == ["internal"]


def test_linked_documents_exist_and_agree():
    adr = (REPO / _CC["links"]["architecture_decision"]).read_text()
    front = yaml.safe_load(adr.split("---")[1])
    never = front["external_providers"]["hosted-byom"]["never_receives"]
    assert {"account-keys", "sync-keys"} <= set(never)
    assert "server-never-content-or-keys" in front["invariants"]
    precedent = (REPO / _CC["links"]["control_plane_precedent"]).read_text()
    assert "replaced with `redacted`" in precedent
    assert 'message = "redacted"' in (REPO / "server/control_plane_client.py").read_text()


def _sem(key, value):
    return lambda c: c["semantics"].__setitem__(key, value)


LINT_MUTATIONS = {
    "outcome_partial_mask": _sem("outcome", "matching-spans-are-masked"),
    "length_loosened": _sem("length", "a-text-longer-than-4096-characters-is-redacted"),
    "charset_loosened": _sem("charset", "control-characters-are-redacted"),
    "secret_case_sensitive": _sem("secret", "a-text-containing-any-secret-is-redacted"),
    "token_run_dropped": _sem("token_run", "none"),
    "token_run_longer": _sem(
        "token_run",
        "a-text-with-a-run-of-40-or-more-characters-each-in-[A-Za-z0-9+/=_%-]-is-redacted",
    ),
    "precedence_content_first": _sem("precedence", "content-rules-then-malformed"),
    "cached": _sem("evaluation", "verdict-cached-per-text"),
    "disclosure_reason": _sem("disclosure", "the-output-names-the-rule"),
    "sink_raw": _sem("sink_fallback", "a-malformed-request-emits-the-raw-text"),
    "rollback_changed": _sem("rollback", "pops-history"),
    "reading_dropped": lambda c: c["semantics"]["readings"].pop("encoded_secret"),
    "reading_changed": lambda c: c["semantics"]["readings"].__setitem__(
        "whole_message", "spans-masked"
    ),
    "marker_changed": lambda c: c["identifiers"]["marker"].__setitem__("value", "[redacted]"),
    "marker_int": lambda c: c["identifiers"]["marker"].__setitem__("value", 0),
    "secrets_unbounded": lambda c: c["identifiers"]["secrets"].__setitem__(
        "grammar", "any-list-of-str"
    ),
    "secrets_source_stored": lambda c: c["identifiers"]["secrets"].__setitem__(
        "source", "stored-by-the-redactor"
    ),
    "text_grammar_loosened": lambda c: c["identifiers"]["text"].__setitem__(
        "grammar", "any-object-str-converted"
    ),
    "request_field_dropped": lambda c: c["request"]["fields"].remove("secrets"),
    "request_exact_int": lambda c: c["request"].__setitem__("exact", 1),
    "role_not_scope_changed": lambda c: c["role"].__setitem__("not_scope", "none"),
    "extra_section": lambda c: c.__setitem__("notes", {"x": 1}),
    "error_added": lambda c: c["errors"]["closed_enum"].append("redacted"),
    "mapping_swapped": lambda c: c["failures"]["mapping"].__setitem__(
        "malformed_redaction_request", "internal"
    ),
    "open_failures": lambda c: c["failures"].__setitem__("closed", False),
    "retryable_widened": lambda c: c["errors"]["shape"].__setitem__(
        "retryable_true_only_for", ["internal", "malformed_request"]
    ),
    "failure_class_renamed": lambda c: c["failures"]["classes"].__setitem__(0, "bad_request"),
    "trigger_reworded": lambda c: c["failures"]["triggers"].__setitem__(
        "malformed_redaction_request", "bad-shape"
    ),
    "failures_extra_key": lambda c: c["failures"].__setitem__("notes", "x"),
    "property_stable_dropped": lambda c: c["properties"].pop("stable"),
    "property_never_raw_changed": lambda c: c["properties"].__setitem__("never_raw", "best-effort"),
    "base_path_changed": lambda c: c["versioning"].__setitem__(
        "base_path", "/privacy/redaction/v2"
    ),
    "link_changed": lambda c: c["links"].__setitem__(
        "control_plane_precedent", "requirements/tasks/T0477.md"
    ),
    "schema_version_bool": lambda c: None,  # envelope mutation, see below
}


@pytest.mark.parametrize("name", sorted(LINT_MUTATIONS))
def test_lint_rejects_mutation(name, tmp_path):
    doc = copy.deepcopy(DOC)
    LINT_MUTATIONS[name](doc["contract"])
    if name == "schema_version_bool":
        doc["schema_version"] = True
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(doc))
    with pytest.raises(ContractError):
        lint(p)


# ---- happy: clean text is returned verbatim -------------------------------

CLEAN = [
    "",
    " ",
    "~",
    "import failed: timeout after 30s",
    " leading and trailing ",
    "a" * 31,
    "a" * 16 + ":" + "a" * 16,
    "a" * 16 + " " + "a" * 16,
    "a" * 16 + "." + "a" * 16,
    "".join(chr(c) for c in range(0x20, 0x7F)),
]


@pytest.mark.parametrize("text", CLEAN, ids=[f"clean{i}" for i in range(len(CLEAN))])
def test_clean_text_is_returned_verbatim(text):
    out = _r(text, [KEY])
    assert type(out) is str and out is text


def test_marker_known_answer():
    assert _CC["identifiers"]["marker"]["value"] == "redacted"
    assert _r("a" * 32) == "redacted"
    assert type(_r("a" * 32)) is str


# ---- length --------------------------------------------------------------

_FILL = "ab " * 400


@pytest.mark.parametrize("n,hit", [(0, False), (1023, False), (1024, False), (1025, True)])
def test_length_edges(n, hit):
    text = _FILL[:n]
    assert len(text) == n
    assert _r(text) == (MARKER if hit else text)


def test_long_text_is_redacted_even_when_clean_otherwise():
    assert _r("x " * 2000) == MARKER


# ---- charset -------------------------------------------------------------

BAD_CHARS = ["\x00", "\x1f", "\x7f", "\x80", "\n", "\r", "\t", "\u00e9", "\u200b", "\U0001f600"]


@pytest.mark.parametrize("ch", BAD_CHARS, ids=[f"u{ord(c):04x}" for c in BAD_CHARS])
@pytest.mark.parametrize("where", ["start", "middle", "end", "alone"])
def test_out_of_range_character_redacts(ch, where):
    text = {"start": ch + "ok", "middle": "o" + ch + "k", "end": "ok" + ch, "alone": ch}[where]
    assert _r(text) == MARKER


@pytest.mark.parametrize("c", [0x20, 0x21, 0x7D, 0x7E])
def test_printable_edges_are_kept(c):
    text = "x" + chr(c) + "y"
    assert _r(text) is text


# ---- secrets -------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        KEY,
        "key " + KEY,
        "key " + KEY + " used",
        KEY + " used",
        "KEY " + KEY.upper(),
        "key " + KEY.lower(),
        "key " + KEY.swapcase(),
    ],
    ids=["whole", "end", "middle", "start", "upper", "lower", "swapcase"],
)
def test_secret_anywhere_any_case_redacts(text):
    assert _r(text, [KEY]) == MARKER


def test_uppercase_secret_matches_lowercase_text():
    assert _r("key " + KEY.lower(), [KEY.upper()]) == MARKER


@pytest.mark.parametrize(
    "secret,text",
    [("stra\u00dfe", "STRASSE here"), ("\u212a9", "k9 here"), ("\u212a9", "K9 here")],
    ids=["sharp_s", "kelvin_lower", "kelvin_upper"],
)
def test_secret_match_is_casefold_not_lower(secret, text):
    assert _r(text, [secret]) == MARKER


@pytest.mark.parametrize("pos", ["first", "middle", "last"])
def test_every_secret_is_checked(pos):
    others = ["no1pe", "no2pe", "no3pe"]
    i = {"first": 0, "middle": 1, "last": 2}[pos]
    secrets = others[:i] + [KEY] + others[i + 1 :]
    assert _r("x " + KEY, secrets) == MARKER


def test_no_secret_hit_keeps_text():
    text = "sk-live only partly"
    assert _r(text, [KEY, "zzz"]) is text


def test_secret_longer_than_text_keeps_text():
    text = "sk-live"
    assert _r(text, [KEY]) is text


def test_text_inside_secret_is_not_a_hit():
    text = "live"
    assert _r(text, [KEY]) is text


@pytest.mark.parametrize("secret", ["z", "Z"])
def test_one_character_secret_is_honoured(secret):
    assert _r("zebra", [secret]) == MARKER
    assert _r("abc", [secret]) == "abc"


def test_max_length_secret_is_accepted():
    s = "q" * 4096
    assert _r("abc", [s]) == "abc"


def test_max_secret_count_is_accepted():
    secrets = [f"s{i:03d}" for i in range(64)]
    assert _r("x s063 y", secrets) == MARKER
    assert _r("x y", secrets) == "x y"


def test_empty_secret_list_keeps_clean_text():
    assert _r("plain", []) == "plain"


# ---- token runs ------------------------------------------------------------


@pytest.mark.parametrize("n,hit", [(31, False), (32, True), (33, True), (64, True)])
@pytest.mark.parametrize("where", ["alone", "start", "middle", "end"])
def test_token_run_edges(n, hit, where):
    run = "a" * n
    text = {"alone": run, "start": run + " x", "middle": "x " + run + " y", "end": "x " + run}[
        where
    ]
    assert _r(text) == (MARKER if hit else text)


@pytest.mark.parametrize("ch", list("+/=_%-") + ["Z", "0"])
def test_each_run_character_joins_the_run(ch):
    assert _r("a" * 16 + ch + "a" * 15) == MARKER
    assert _r("x " + "a" * 16 + ch + "a" * 15 + " y") == MARKER


@pytest.mark.parametrize("ch", list(":. ,;!@#$^&*()[]{}|\\'\"<>?`~"))
def test_other_characters_break_the_run(ch):
    text = "a" * 16 + ch + "a" * 16
    assert _r(text) is text


def test_unregistered_install_id_and_digest_are_redacted():
    assert _r("install ins1:" + "1" * 64) == MARKER
    assert _r("terms trm1:" + "ab" * 32) == MARKER
    assert _r("key " + LONGKEY) == MARKER


# ---- whole-message, disclosure, stability, statelessness --------------------


def test_output_never_names_the_rule_or_secret():
    outs = {
        _r("x" * 2000),
        _r("bad\n"),
        _r("k " + KEY, [KEY]),
        _r("a" * 40),
    }
    assert outs == {MARKER}


@pytest.mark.parametrize(
    "text,secrets",
    [
        ("plain", []),
        ("k " + KEY, [KEY]),
        ("red flag", ["red"]),
        ("a" * 40, []),
        ("bad\n", ["red"]),
        ("redacted", ["redacted"]),
    ],
    ids=["clean", "secret", "marker_word", "run", "charset_with_marker_secret", "marker_secret"],
)
def test_redaction_is_stable(text, secrets):
    once = _r(text, secrets)
    assert _r(once, secrets) == once


def test_marker_is_emitted_even_when_it_contains_a_short_secret():
    assert _r("red " + "x", ["red"]) == MARKER


def test_verdict_is_stateless_and_never_cached():
    text = "k " + KEY
    assert _r(text, [KEY]) == MARKER
    assert _r(text, []) is text
    assert _r(text, [KEY]) == MARKER


def test_inputs_are_never_mutated_on_the_happy_path():
    secrets = [KEY, "zz"]
    req = {"text": "k " + KEY, "secrets": secrets}
    snap = copy.deepcopy(req)
    redact(req)
    assert req == snap and req["secrets"] is secrets


# ---- sink fallback -----------------------------------------------------------


@pytest.mark.parametrize(
    "req,out",
    [
        ({"text": "fine", "secrets": []}, "fine"),
        ({"text": "k " + KEY, "secrets": [KEY]}, MARKER),
        ({"text": "fine", "secrets": ["", "x"]}, MARKER),
        ({"text": b"fine", "secrets": []}, MARKER),
        (None, MARKER),
    ],
    ids=["clean", "secret", "empty_secret", "bytes", "none"],
)
def test_emit_text_never_raises_and_never_emits_raw(req, out):
    assert emit_text(req) == out


# ---- malformed boundaries ----------------------------------------------------


@pytest.mark.parametrize(
    "secrets",
    [
        [""],
        ["q" * (4097)],
        [f"s{i}" for i in range(65)],
        [KEY, ""],
        ["", KEY],
    ],
    ids=["empty", "too_long", "too_many", "empty_last", "empty_first"],
)
def test_secret_bounds_are_malformed(secrets):
    req = {"text": "k " + KEY, "secrets": secrets}
    snap = copy.deepcopy(req)
    _raises(redact, req)
    assert req == snap


def test_malformed_precedes_content_rules():
    # a long, non-ascii, secret-bearing text with a bad secret list is malformed
    _raises(redact, {"text": "\n" * 2000 + KEY, "secrets": (KEY,)})


# ---- hostile rows ----------------------------------------------------------

CALLS = []


class StrSub(str):
    pass


class DictSub(dict):
    pass


class ListSub(list):
    pass


class LoudStr(str):
    def casefold(self):
        CALLS.append("casefold")
        return str.casefold(self)

    def lower(self):
        CALLS.append("lower")
        return str.lower(self)

    def __len__(self):
        CALLS.append("len")
        return str.__len__(self)

    def __iter__(self):
        CALLS.append("iter")
        return str.__iter__(self)

    def __contains__(self, item):
        CALLS.append("contains")
        return str.__contains__(self, item)

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)

    __hash__ = str.__hash__


class LoudList(list):
    def __iter__(self):
        CALLS.append("iter")
        return list.__iter__(self)

    def __len__(self):
        CALLS.append("len")
        return list.__len__(self)


class LoudDict(dict):
    def keys(self):
        CALLS.append("keys")
        return dict.keys(self)

    def __iter__(self):
        CALLS.append("iter")
        return dict.__iter__(self)

    def __getitem__(self, k):
        CALLS.append("getitem")
        return dict.__getitem__(self, k)


class EqRaises(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("eq")

    __hash__ = str.__hash__


class HashCollide(str):
    def __hash__(self):
        CALLS.append("hash")
        return hash("text")

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)


def _base():
    return {"text": "k " + KEY, "secrets": [KEY]}


def _hostile_rows():
    rows = []
    base = _base()
    rows.append(("dict_sub", DictSub(base)))
    rows.append(("loud_dict", LoudDict(base)))
    rows.append(("list", list(base.items())))
    rows.append(("str", "text"))
    rows.append(("none", None))
    for key in base:
        r = dict(base)
        r[key] = StrSub(r[key]) if type(r[key]) is str else ListSub(r[key])
        rows.append((f"{key}_sub", r))
        r = dict(base)
        r[key] = ListSub([r[key]])
        rows.append((f"{key}_listsub", r))
        r = dict(base)
        r[key] = DictSub({"v": r[key]})
        rows.append((f"{key}_dictsub", r))
        r = dict(base)
        del r[key]
        rows.append((f"missing_{key}", r))
        r = dict(base)
        r[StrSub(key)] = r.pop(key)
        rows.append((f"real_key_strsub_{key}", r))
        r = dict(base)
        r[LoudStr(key)] = r.pop(key)
        rows.append((f"real_key_loud_{key}", r))
        r = dict(base)
        r[key + "_x"] = r.pop(key)
        rows.append((f"renamed_key_{key}", r))
    for form, cls in (("plain", str), ("eq_raises", EqRaises), ("hash_collide", HashCollide)):
        r = dict(base)
        r[cls("extra")] = 1
        rows.append((f"extra_key_{form}", r))
    r = dict(base)
    r[b"text"] = r.pop("text")
    rows.append(("bytes_key", r))
    r = dict(base)
    r[1] = r.pop("text")
    rows.append(("int_key", r))
    for tag, text in (
        ("bytes", b"fine"),
        ("none", None),
        ("int", 7),
        ("bool", True),
        ("list", ["fine"]),
        ("loud", LoudStr("k " + KEY)),
        ("loud_long", LoudStr("a" * 2000)),
        ("loud_clean", LoudStr("fine")),
        ("eq_raises", EqRaises("fine")),
        ("hash_collide", HashCollide("fine")),
    ):
        rows.append((f"text_{tag}", dict(base, text=text)))
    for tag, secrets in (
        ("tuple", (KEY,)),
        ("set", {KEY}),
        ("frozenset", frozenset({KEY})),
        ("dict", {KEY: 1}),
        ("str", KEY),
        ("none", None),
        ("loud_list", LoudList([KEY])),
        ("loud_list_empty", LoudList()),
        ("item_strsub", [StrSub(KEY)]),
        ("item_loud", [LoudStr(KEY)]),
        ("item_loud_after_good", [KEY, LoudStr("zz")]),
        ("item_eq_raises", [EqRaises(KEY)]),
        ("item_hash_collide", [HashCollide(KEY)]),
        ("item_bytes", [KEY.encode()]),
        ("item_none", [None]),
        ("item_int", [1]),
        ("item_list", [[KEY]]),
        ("item_empty", [""]),
        ("item_too_long", ["q" * (4097)]),
        ("too_many", ["s"] * (65)),
    ):
        rows.append((f"secrets_{tag}", dict(base, secrets=secrets)))
    return rows


ROWS = _hostile_rows()


def _snap(req):
    if isinstance(req, dict):
        return [(k, copy.deepcopy(v)) for k, v in dict.items(req)]
    return copy.deepcopy(req)


def _same(v1, v2):
    """Equality that never calls a caller method."""
    if type(v1) is not type(v2):
        return False
    if isinstance(v1, str):
        return str.__eq__(v1, v2) is True
    if isinstance(v1, list):
        a, b = list.__iter__(v1), list.__iter__(v2)
        la, lb = list(a), list(b)
        return len(la) == len(lb) and all(_same(x, y) for x, y in zip(la, lb, strict=True))
    return v1 == v2


def _unchanged(req, snap):
    if isinstance(req, dict):
        now = list(dict.items(req))
        return len(now) == len(snap) and all(
            k1 is k2 and _same(v1, v2) for (k1, v1), (k2, v2) in zip(now, snap, strict=True)
        )
    return _same(req, snap)


@pytest.mark.parametrize("name,req", ROWS, ids=[n for n, _ in ROWS])
def test_hostile_request_fails_closed_typed(name, req):
    snap = _snap(req)
    CALLS.clear()
    _raises(redact, req)
    assert CALLS == []
    assert _unchanged(req, snap)


@pytest.mark.parametrize("name,req", ROWS, ids=[n for n, _ in ROWS])
def test_hostile_request_at_a_sink_emits_only_the_marker(name, req):
    snap = _snap(req)
    CALLS.clear()
    out = emit_text(req)
    assert type(out) is str and str.__eq__(out, MARKER) is True
    assert CALLS == []
    assert _unchanged(req, snap)


# ---- executable kill check: one-edit mutants of the reference engine ------
# Each edit is applied to THIS file's source; the mutated copy's battery
# (minus this section) must go red. The identity edit must stay green.

MUTANTS = {
    "req_isinstance": (
        "    if type(req) is not dict:",
        "    if not isinstance(req, dict):",
    ),
    "key_type_skip": (
        "        if type(k) is not str:",
        "        if not isinstance(k, str):",
    ),
    "keys_subset": (
        "    if set(req) != _KEYS:",
        "    if not _KEYS <= set(req):",
    ),
    "keys_same_arity": (
        "    if set(req) != _KEYS:",
        "    if len(req) != len(_KEYS):",
    ),
    "text_isinstance": (
        "    if type(text) is not str:",
        "    if not isinstance(text, str):",
    ),
    "text_type_after_len": (
        "    if type(text) is not str:",
        "    if type(text) is not str and len(text) <= _MAX_TEXT:",
    ),
    "secrets_isinstance": (
        "    if type(secrets) is not list or len(secrets) > _MAX_SECRETS:",
        "    if not isinstance(secrets, list) or len(secrets) > _MAX_SECRETS:",
    ),
    "secrets_tuple_ok": (
        "    if type(secrets) is not list or len(secrets) > _MAX_SECRETS:",
        "    if type(secrets) not in (list, tuple) or len(secrets) > _MAX_SECRETS:",
    ),
    "secrets_upper_edge": (
        "    if type(secrets) is not list or len(secrets) > _MAX_SECRETS:",
        "    if type(secrets) is not list or len(secrets) >= _MAX_SECRETS:",
    ),
    "secrets_count_const": (
        "_MAX_SECRETS = 64",
        "_MAX_SECRETS = 65",
    ),
    "secret_isinstance": (
        "        if type(s) is not str or not 1 <= len(s) <= _MAX_SECRET:",
        "        if not isinstance(s, str) or not 1 <= len(s) <= _MAX_SECRET:",
    ),
    "secret_empty_ok": (
        "        if type(s) is not str or not 1 <= len(s) <= _MAX_SECRET:",
        "        if type(s) is not str or not 0 <= len(s) <= _MAX_SECRET:",
    ),
    "secret_upper_edge": (
        "        if type(s) is not str or not 1 <= len(s) <= _MAX_SECRET:",
        "        if type(s) is not str or not 1 <= len(s) < _MAX_SECRET:",
    ),
    "secret_len_const": (
        "_MAX_SECRET = 4096",
        "_MAX_SECRET = 4097",
    ),
    "secret_first_only_checked": (
        "    for s in secrets:\n        if type(s)",
        "    for s in secrets[:1]:\n        if type(s)",
    ),
    "text_upper_edge": (
        "    if len(text) > _MAX_TEXT:",
        "    if len(text) >= _MAX_TEXT:",
    ),
    "text_len_const": (
        "_MAX_TEXT = 1024",
        "_MAX_TEXT = 1025",
    ),
    "length_ignored": (
        "    if len(text) > _MAX_TEXT:\n        return True",
        "    if len(text) > _MAX_TEXT:\n        pass",
    ),
    "low_char_edge": (
        '        if not " " <= ch <= "~":',
        '        if not " " < ch <= "~":',
    ),
    "high_char_edge": (
        '        if not " " <= ch <= "~":',
        '        if not " " <= ch < "~":',
    ),
    "high_char_del": (
        '        if not " " <= ch <= "~":',
        '        if not " " <= ch <= "\\x7f":',
    ),
    "low_char_tab": (
        '        if not " " <= ch <= "~":',
        '        if not "\\t" <= ch <= "~":',
    ),
    "charset_ignored": (
        '        if not " " <= ch <= "~":',
        "        if False:",
    ),
    "casefold_lower": (
        "        if s.casefold() in folded:",
        "        if s.lower() in folded:",
    ),
    "case_sensitive": (
        "        if s.casefold() in folded:",
        "        if s in text:",
    ),
    "secret_reversed": (
        "        if s.casefold() in folded:",
        "        if folded in s.casefold():",
    ),
    "secret_prefix_only": (
        "        if s.casefold() in folded:",
        "        if folded.startswith(s.casefold()):",
    ),
    "secret_first_only_matched": (
        "    for s in secrets:\n        if s.casefold()",
        "    for s in secrets[:1]:\n        if s.casefold()",
    ),
    "secret_last_skipped": (
        "    for s in secrets:\n        if s.casefold()",
        "    for s in secrets[:-1]:\n        if s.casefold()",
    ),
    "run_longer": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{33}")',
    ),
    "run_shorter": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{31}")',
    ),
    "run_no_plus": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9/=_%-]{32}")',
    ),
    "run_no_slash": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9+=_%-]{32}")',
    ),
    "run_no_eq": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/_%-]{32}")',
    ),
    "run_no_underscore": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=%-]{32}")',
    ),
    "run_no_pct": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_-]{32}")',
    ),
    "run_no_dash": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%]{32}")',
    ),
    "run_no_upper": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[a-z0-9+/=_%-]{32}")',
    ),
    "run_no_digit": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[A-Za-z+/=_%-]{32}")',
    ),
    "run_any_char": (
        '_RUN_RE = re.compile(r"[A-Za-z0-9+/=_%-]{32}")',
        '_RUN_RE = re.compile(r"[^\\n]{32}")',
    ),
    "run_match": (
        "    return _RUN_RE.search(text) is not None",
        "    return _RUN_RE.match(text) is not None",
    ),
    "run_ignored": (
        "    return _RUN_RE.search(text) is not None",
        "    return False",
    ),
    "marker_changed": (
        'MARKER = "redacted"',
        'MARKER = "[redacted]"',
    ),
    "verdict_inverted": (
        "    return MARKER if _hits(text, secrets) else text",
        "    return text if _hits(text, secrets) else MARKER",
    ),
    "clean_copy": (
        "    return MARKER if _hits(text, secrets) else text",
        "    return MARKER if _hits(text, secrets) else text.strip()",
    ),
    "partial_mask": (
        "    return MARKER if _hits(text, secrets) else text",
        "    return text.replace(secrets[0], MARKER) if secrets else (MARKER if _hits(text, secrets) else text)",  # noqa: E501
    ),
    "emit_empty": (
        "    except RedactionError:\n        return MARKER",
        '    except RedactionError:\n        return ""',
    ),
    "emit_reraises": (
        "    except RedactionError:\n        return MARKER",
        "    except KeyError:\n        return MARKER",
    ),
    "emit_raw": (
        "    except RedactionError:\n        return MARKER",
        (
            "    except RedactionError:\n"
            '        return req.get("text", MARKER) if type(req) is dict else MARKER'
        ),
    ),
    "error_code": (
        "        self.code = FAILURE_MAPPING[failure_class]",
        '        self.code = "internal"',
    ),
    "error_chained": (
        '    raise RedactionError("malformed_redaction_request")',
        '    raise RedactionError("malformed_redaction_request") from ValueError()',
    ),
}

EQUIVALENT = {
    # by the time the text is casefolded every character is in 0x20-0x7e,
    # where casefold and lower agree
    "text_fold_lower": ("    folded = text.casefold()", "    folded = text.lower()"),
}
_SELF = Path(__file__)


# the battery without this section: every test above, parametrized rows
# expanded; a green copy must run exactly this many and pass them all
EXPECTED_BATTERY = 300


def _battery(tmp_dir, source):
    """Run the copy (kill-check section stripped, so no selection flags are
    needed) from a neutrally named dir; return (returncode, passed, failed)."""
    copy_ = tmp_dir / "test_battery_copy.py"
    copy_.write_text(source)
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
            str(copy_),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    passed = re.search(r"(\d+) passed", tail)
    failed = re.search(r"(\d+) failed", tail)
    return (
        r.returncode,
        int(passed.group(1)) if passed else 0,
        int(failed.group(1)) if failed else 0,
    )


def _green(tmp_dir, source):
    rc, passed, failed = _battery(tmp_dir, source)
    return rc == 0 and passed == EXPECTED_BATTERY and failed == 0


def _red(tmp_dir, source):
    # tests ran and at least one FAILED; rc 2/4/5 (errors, usage, nothing
    # collected) is never a kill
    rc, passed, failed = _battery(tmp_dir, source)
    return rc == 1 and failed >= 1 and passed + failed == EXPECTED_BATTERY


def _edited(before, after):
    source = _SELF.read_text()
    head, _, _ = source.partition("# ---- executable kill check")
    assert head.count(before) == 1, before
    return head.replace(before, after)


def test_identity_mutant_is_green(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("battery")
    assert _green(tmp, _edited("_MAX_TEXT = 1024", "_MAX_TEXT = 1024"))


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_source_mutant_is_killed(name, tmp_path_factory):
    assert _red(tmp_path_factory.mktemp("battery"), _edited(*MUTANTS[name]))


@pytest.mark.parametrize("name", sorted(EQUIVALENT))
def test_equivalent_edit_stays_green(name, tmp_path_factory):
    assert _green(tmp_path_factory.mktemp("battery"), _edited(*EQUIVALENT[name]))
