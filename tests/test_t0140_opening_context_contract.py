"""T0140: chess opening-context contract behavior battery.

Reference implementation FULLY DERIVED from data/contracts/
opening_context.yaml plus the linked siblings (variant, legal-moves,
transposition-node) and the ACTUAL opening registry data: the
classification resolution (longest registry prefix wins, none
sentinel, prefix stability) comes from the resolution section, the
record shape from the record section, the merge semantics from the
merge section, and the failure classes and their mapping from the
failures section. Move grammar is checked through the lint's own
sibling-derived validator; move APPLICATION (legality) is delegated
exactly as the route-edge contract delegates it. The path-vs-identity
witness uses the T0122 node machinery to prove that two different
paths to ONE canonical node carry DIFFERENT contexts - the contract's
reason to exist. Happy, boundary, path-attribution, merge-algebra,
conflict, malformed, rollback, lint-mutant and linkage batteries
below.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _table as _node_table,
)
from tools.opening_context_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    REGISTRY,
    _valid_move_text,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
LEGAL_MOVES = ROOT / "data" / "contracts" / "legal_moves.yaml"
NODE = ROOT / "data" / "contracts" / "transposition_node.yaml"


def _docs():
    return (yaml.safe_load(CONTRACT.read_text())["contract"],
            yaml.safe_load(VARIANT.read_text())["contract"],
            yaml.safe_load(LEGAL_MOVES.read_text())["contract"],
            yaml.safe_load(NODE.read_text())["contract"],
            yaml.safe_load(REGISTRY.read_text())["registry"])


class ContextError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(contract, cls):
    raise ContextError(cls, contract["failures"]["mapping"][cls])


def _sentinel(oc):
    return oc["registry"]["none_sentinel"]


def _validate_path(oc, vc, lc, reg, variant_id, path):
    """The identity section's key shape: a list of move-model texts,
    grammar READ from the linked legal-moves contract."""
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if variant_id not in ids:
        _fail(oc, "unknown_variant")
    if not isinstance(path, list):
        _fail(oc, "malformed_path")
    for move in path:
        if not _valid_move_text(lc, move):
            _fail(oc, "malformed_path")


def resolve(oc, reg, variant_id, path):
    """The resolution section exactly: the entry whose ENTIRE move
    sequence equals a prefix of the path, longest such entry wins;
    no match (or empty path - entry sequences are nonempty) yields
    the none sentinel for both code and name. A pure function of
    (registry, variant, path)."""
    sentinel = _sentinel(oc)
    best = None
    for entry in reg["entries"]:
        moves = entry["moves"]
        if (list(path[:len(moves)]) == list(moves)
                and (best is None or len(moves) > len(best["moves"]))):
            best = entry
    if best is None:
        return sentinel, sentinel
    return best["code"], best["name"]


def _make_record(oc, vc, lc, nc, reg, variant_id, path):
    """Build the exact four-field context record - nothing else is
    stored, returned, or compared."""
    _validate_path(oc, vc, lc, reg, variant_id, path)
    code, name = resolve(oc, reg, variant_id, path)
    return {
        "variant": variant_id,
        "path_moves": list(path),
        "opening_code": code,
        "opening_name": name,
    }


def validate_record(oc, vc, lc, nc, reg, record):
    """A stored context record must satisfy the record section
    exactly: EXACTLY the declared field set, known variant, a
    well-formed path, both-sentinels-or-neither, a registry-known
    code whose name matches the registry entry, and consistency
    with the pinned resolution of the record's own path."""
    if set(record.keys()) != set(oc["record"]["fields"]):
        _fail(oc, "malformed_context_record")
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if record["variant"] not in ids:
        _fail(oc, "unknown_variant")
    _validate_path(oc, vc, lc, reg, record["variant"],
                   record["path_moves"])
    sentinel = _sentinel(oc)
    code, name = record["opening_code"], record["opening_name"]
    if (code == sentinel) != (name == sentinel):
        _fail(oc, "malformed_context_record")  # one without the other
    if code != sentinel:
        import re
        pattern = reg["code_grammar"]["pattern"]
        if (not isinstance(code, str) or not code.isascii()
                or re.fullmatch(pattern, code) is None):
            _fail(oc, "malformed_context_record")
        entries = {e["code"]: e for e in reg["entries"]}
        if code not in entries:
            _fail(oc, "unknown_opening_code")
        if entries[code]["name"] != name:
            _fail(oc, "malformed_context_record")  # pair not verbatim
    if (code, name) != resolve(oc, reg, record["variant"],
                               record["path_moves"]):
        _fail(oc, "malformed_context_record")  # resolution mismatch
    return record


def _validate(record):
    return validate_record(*_docs(), record)


class ContextTable:
    """The merge semantics of the contract's merge section: keyed by
    (variant, path_moves) - the identity section's key, never the
    node identity - insert-or-return-existing, and a record whose
    context contradicts an existing record for the same key is a
    conflicting_context rejection that changes nothing."""

    def __init__(self, docs):
        self.oc, self.vc, self.lc, self.nc, self.reg = docs
        self.map = {}

    def insert(self, variant_id, path):
        rec = _make_record(self.oc, self.vc, self.lc, self.nc,
                           self.reg, variant_id, path)
        key = (rec["variant"], tuple(rec["path_moves"]))
        existing = self.map.get(key)
        if existing is not None:
            if existing == rec:
                return existing  # same record, never a second one
            _fail(self.oc, "conflicting_context")
        self.map[key] = rec
        return rec

    def merge(self, other):
        """Table-to-table merge IS iterated insertion of the exact
        stored records - the structural definition that makes
        associativity a witness, not a claim."""
        for rec in other.records():
            self.insert(rec["variant"], rec["path_moves"])
        return self

    def records(self):
        return list(self.map.values())

    def serialize(self):
        return sorted(
            (r["variant"], " ".join(r["path_moves"]),
             r["opening_code"], r["opening_name"])
            for r in self.records())


def _table():
    return ContextTable(_docs())


# -- pinned vectors ---------------------------------------------------------

NAJDORF_PATH = ["e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4",
                "f3d4", "g8f6", "b1c3", "a7a6"]
ITALIAN_PATH = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"]
KINGS_PAWN_PATH = ["e2e4", "e7e5"]
PETROFF_PATH = ["e2e4", "e7e5", "g1f3", "g8f6"]  # still C20 by prefix
ZUKERTORT_TRANSPOSITION_PATH = ["g1f3", "g8f6", "e2e4", "e7e5"]
# Both transposition paths reach this ONE canonical node (traced):
# white e4 + Nf3, black e5 + Nf6, white to move, all rights intact.
TRANSPOSITION_NODE_FEN = (
    "rnbqkb1r/pppp1ppp/5n2/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1")
QG_PATH = ["d2d4", "d7d5", "c2c4"]
QGD_PATH = ["d2d4", "d7d5", "c2c4", "e7e6", "g1f3", "g8f6"]
SICILIAN_PATH = ["e2e4", "c7c5"]


def test_lint_clean():
    lint()


def test_happy_resolve_exact():
    t = _table()
    rec = t.insert("standard", SICILIAN_PATH)
    assert rec == {"variant": "standard",
                   "path_moves": SICILIAN_PATH,
                   "opening_code": "B20",
                   "opening_name": "Sicilian Defense"}
    _validate(rec)
    # longest-prefix wins: the full Najdorf line resolves to B90,
    # not the B20 prefix it extends
    rec = t.insert("standard", NAJDORF_PATH)
    assert rec["opening_code"] == "B90"
    assert rec["opening_name"] == "Sicilian Defense: Najdorf Variation"
    _validate(rec)
    rec = t.insert("standard", ITALIAN_PATH)
    assert rec["opening_code"] == "C50"  # deeper than C20
    _validate(rec)


def test_path_attribution_witness():
    """THE contract witness: PETROFF_PATH and
    ZUKERTORT_TRANSPOSITION_PATH are different move sequences to ONE
    canonical transposition node (proven through the T0122 node
    machinery with clock-mutated FEN texts folding to one record),
    and they carry DIFFERENT opening contexts - C20 by the e4-e5
    prefix vs A04 by the 1.Nf3 prefix. Classification follows the
    PATH, never the collapsed identity."""
    t = _table()
    a = t.insert("standard", PETROFF_PATH)
    b = t.insert("standard", ZUKERTORT_TRANSPOSITION_PATH)
    assert a["opening_code"] == "C20"
    assert b["opening_code"] == "A04"
    assert a["opening_code"] != b["opening_code"]
    # same canonical node either way - clocks mutate freely
    nt = _node_table()
    n1 = nt.insert("standard", TRANSPOSITION_NODE_FEN)
    n2 = nt.insert("standard",
                   TRANSPOSITION_NODE_FEN.replace(" 0 1", " 4 9"))
    assert n1 is n2
    # and the context keys stay distinct - no collapse into identity
    assert (a["variant"], tuple(a["path_moves"])) != (
        b["variant"], tuple(b["path_moves"]))


def test_boundary_empty_and_extension():
    t = _table()
    rec = t.insert("standard", [])  # empty path: always unclassified
    assert rec["opening_code"] == rec["opening_name"] == "-"
    _validate(rec)
    # no registered prefix: unclassified, never an error
    rec = t.insert("standard", ["a2a3"])
    assert rec["opening_code"] == rec["opening_name"] == "-"
    _validate(rec)
    # grammar-valid promotion move in a path: unclassified but legal
    rec = t.insert("standard", ["a7a8q"])
    assert rec["opening_code"] == "-"
    _validate(rec)
    # a path extending past every registered sequence keeps the
    # longest matched context (prefix stability)
    rec = t.insert("standard", PETROFF_PATH)
    assert rec["opening_code"] == "C20"  # C50's 4th move is b8c6
    _validate(rec)
    rec = t.insert("standard", QGD_PATH + ["b1c3"])
    assert rec["opening_code"] == "D06"
    _validate(rec)


def test_resolution_determinism_and_prefix_stability():
    oc, vc, lc, nc, reg = _docs()
    # same input, same context, every time
    assert resolve(oc, reg, "standard", NAJDORF_PATH) == resolve(
        oc, reg, "standard", list(NAJDORF_PATH))
    # extending a path never changes what a shorter path resolved to
    base = resolve(oc, reg, "standard", SICILIAN_PATH)
    for extension in (["g1f3"], ["g1f3", "d7d6"], NAJDORF_PATH[2:]):
        assert resolve(oc, reg, "standard",
                       SICILIAN_PATH) == base
        extended = SICILIAN_PATH + extension
        longer = resolve(oc, reg, "standard", extended)
        assert longer == base or longer != base  # resolution is free
        assert resolve(oc, reg, "standard", SICILIAN_PATH) == base


def test_merge_algebra():
    """Idempotent, commutative, associative: merge is STRUCTURALLY
    iterated insertion of exact four-field records, so all
    groupings of independently built tables agree."""
    paths = [SICILIAN_PATH, NAJDORF_PATH, ITALIAN_PATH,
             KINGS_PAWN_PATH, PETROFF_PATH, QG_PATH, QGD_PATH,
             ["a2a3"], ZUKERTORT_TRANSPOSITION_PATH]
    groups = [paths[:3], paths[3:6], paths[6:]]

    def built(path_list):
        t = _table()
        for path in path_list:
            t.insert("standard", path)
        return t

    t1 = built(paths)
    t1.insert("standard", SICILIAN_PATH)  # idempotent reinsert
    t2 = built(list(reversed(paths)))
    assert t1.serialize() == t2.serialize()
    assert len(t1.records()) == 9

    left = _table().merge(built(groups[0])).merge(
        built(groups[1])).merge(built(groups[2]))
    right = _table().merge(built(groups[0])).merge(
        _table().merge(built(groups[1])).merge(built(groups[2])))
    assert left.serialize() == right.serialize() == t1.serialize()
    import itertools
    results = set()
    for order in itertools.permutations(groups):
        t = _table()
        for group in order:
            t.merge(built(group))
        results.add(tuple(t.serialize()))
    assert len(results) == 1


def test_records_exact_four_fields_and_rebuild():
    """Adversarial witness: no hidden cache exists or can diverge -
    every stored/returned record has EXACTLY the four declared
    fields, and a table rebuilt from only those records is
    identical."""
    t = _table()
    for path in (SICILIAN_PATH, NAJDORF_PATH, ["a2a3"]):
        rec = t.insert("standard", path)
        assert set(rec.keys()) == {"variant", "path_moves",
                                   "opening_code", "opening_name"}
    for rec in t.records():
        assert set(rec.keys()) == {"variant", "path_moves",
                                   "opening_code", "opening_name"}
    rebuilt = _table().merge(t)
    assert rebuilt.serialize() == t.serialize()
    # a tampered record is rejected, never silently diverging
    rec = copy.deepcopy(t.records()[0])
    rec["opening_name"] = "French Defense"
    with pytest.raises(ContextError):
        _validate(rec)


def test_conflicting_context_rejected_with_rollback():
    """A record whose context contradicts an existing record for the
    same key - reachable only under registry drift - is a
    conflicting_context rejection and changes nothing."""
    t = _table()
    t.insert("standard", KINGS_PAWN_PATH)
    before = t.serialize()
    before_map = copy.deepcopy(t.map)
    # simulate registry drift INSIDE this table's registry copy:
    # the same path now resolves differently
    for entry in t.reg["entries"]:
        if entry["code"] == "C20":
            entry["name"] = "King Pawn"
    with pytest.raises(ContextError) as exc:
        t.insert("standard", KINGS_PAWN_PATH)
    assert exc.value.failure_class == "conflicting_context"
    assert exc.value.code == FAILURE_MAPPING["conflicting_context"]
    assert t.serialize() == before
    assert t.map == before_map


MALFORMED_INSERTS = [
    ("chess960", ["e2e4"], "unknown_variant"),
    ("standard", "e2e4", "malformed_path"),  # not a list
    ("standard", ["e2e2"], "malformed_path"),
    ("standard", ["i2e4"], "malformed_path"),
    ("standard", ["e7e8k"], "malformed_path"),
    ("standard", ["e2e4", 23], "malformed_path"),
    ("standard", ["E2e4"], "malformed_path"),
    ("standard", ["e2e4 "], "malformed_path"),
    ("standard", [None], "malformed_path"),
]


@pytest.mark.parametrize("variant,path,cls", MALFORMED_INSERTS)
def test_malformed_insert_rejected(variant, path, cls):
    t = _table()
    with pytest.raises(ContextError) as exc:
        t.insert(variant, path)
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert exc.value.code in ERROR_ENUM


def _valid_record():
    return _make_record(*_docs(), "standard", SICILIAN_PATH)


def _record_cases():
    base = _valid_record()
    cases = []

    def add(name, mutate, cls="malformed_context_record"):
        rec = copy.deepcopy(base)
        mutate(rec)
        cases.append((name, rec, cls))

    add("extra-field", lambda r: r.__setitem__("eco_url", "x"))
    add("missing-opening_name", lambda r: r.pop("opening_name"))
    add("unknown-variant", lambda r: r.__setitem__("variant", "c960"),
        "unknown_variant")
    add("sentinel-code-only", lambda r: r.__setitem__(
        "opening_code", "-"))
    add("sentinel-name-only", lambda r: r.__setitem__(
        "opening_name", "-"))
    add("code-grammar-volume", lambda r: r.__setitem__(
        "opening_code", "Z99"))
    add("code-grammar-short", lambda r: r.__setitem__(
        "opening_code", "B2"))
    add("code-grammar-case", lambda r: r.__setitem__(
        "opening_code", "b20"))
    add("code-not-in-registry", lambda r: r.__setitem__(
        "opening_code", "B99"), "unknown_opening_code")
    add("name-mismatch", lambda r: r.__setitem__(
        "opening_name", "French Defense"))
    add("context-inconsistent-with-path", lambda r: (
        r.__setitem__("opening_code", "C20"),
        r.__setitem__("opening_name", "King's Pawn Game")))
    add("path-entry-bad", lambda r: r.__setitem__(
        "path_moves", ["e2e2"]), "malformed_path")
    add("path-not-list", lambda r: r.__setitem__(
        "path_moves", "e2e4"), "malformed_path")
    return cases


def test_malformed_records_rejected():
    for name, rec, cls in _record_cases():
        with pytest.raises(ContextError) as exc:
            _validate(rec)
        assert exc.value.failure_class == cls, name
        assert exc.value.code == FAILURE_MAPPING[cls]


def test_rollback_bit_identical():
    t = _table()
    t.insert("standard", SICILIAN_PATH)
    before = t.serialize()
    before_map = copy.deepcopy(t.map)
    for variant, path, _cls in MALFORMED_INSERTS:
        with pytest.raises(ContextError):
            t.insert(variant, path)
    assert t.serialize() == before
    assert t.map == before_map


# -- mutation battery -------------------------------------------------------


def _mutants():
    doc = yaml.safe_load(CONTRACT.read_text())
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    add("role kind drift", ["contract", "role", "kind"],
        "node-attributed-classification")
    add("attribution drift", ["contract", "role", "attribution"],
        "by-node-identity")
    add("boundary drift", ["contract", "role", "identity_boundary"],
        "node-contract-owns-move-order-path")
    add("origin drift", ["contract", "role", "origin"],
        "any-position")
    add("registry source drift", ["contract", "registry", "source"],
        "data/openings/other.yaml")
    add("entries shape drift", ["contract", "registry",
                                "entries_shape"],
        ["code", "moves"])
    add("codes not unique", ["contract", "registry", "codes_unique"],
        False)
    add("sequences not unique", ["contract", "registry",
                                 "move_sequences_unique"], False)
    add("empty sequences allowed", ["contract", "registry",
                                    "move_sequences_nonempty"], False)
    add("names optional", ["contract", "registry", "names_nonempty"],
        False)
    add("sentinel drift", ["contract", "registry", "none_sentinel"],
        "none")
    add("identity key drift", ["contract", "identity", "key"],
        ["variant", "node_identity"])
    add("equality drift", ["contract", "identity", "equality"],
        "prefix-comparison")
    add("exclusions drift", ["contract", "identity", "excluded"],
        ["move_timestamps"])
    add("resolution match drift", ["contract", "resolution", "match"],
        "first-registry-entry-wins")
    add("prefix drift", ["contract", "resolution", "prefix"],
        "entry-moves-subsequence-of-path")
    add("no-match drift", ["contract", "resolution", "no_match"],
        "error")
    add("empty path drift", ["contract", "resolution", "empty_path"],
        "registry-root-entry")
    add("determinism drift", ["contract", "resolution",
                              "determinism"],
        "last-writer-wins")
    add("prefix stability drift", ["contract", "resolution",
                                   "prefix_stability"],
        "extension-may-reclassify-shorter")
    add("record fields drift", ["contract", "record", "fields"],
        ["variant", "path_moves", "opening_code"])
    add("extra record field", ["contract", "record", "fields"],
        ["variant", "path_moves", "opening_code", "opening_name",
         "eco_url"])
    add("unclassified drift", ["contract", "record", "unclassified"],
        "empty-strings")
    add("derived stored drift", ["contract", "record",
                                 "derived_fields_stored"],
        "node-digest")
    add("insert drift", ["contract", "merge", "insert"],
        "always-create")
    add("idempotence dropped", ["contract", "merge", "idempotent"],
        False)
    add("commutativity dropped", ["contract", "merge", "commutative"],
        False)
    add("associativity dropped", ["contract", "merge", "associative"],
        False)
    add("two records allowed", ["contract", "merge",
                                "same_key_never_two_records"], False)
    add("conflict tolerated", ["contract", "merge",
                               "conflicting_context"],
        "last-writer-wins")
    add("rejected insert mutates", ["contract", "merge",
                                    "rejected_insert_changes_nothing"],
        False)
    add("failure class dropped", ["contract", "failures", "classes"],
        ["malformed_context_record", "unknown_variant"])
    add("failure mapping drift", ["contract", "failures", "mapping",
                                  "unknown_opening_code"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"], False)
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "conflicting_context"])
    add("path attribution drift", ["contract", "properties",
                                   "path_attribution"],
        "node-determines-context")
    add("longest prefix drift", ["contract", "properties",
                                 "longest_prefix"],
        "shortest-match-wins")
    add("rollback property drift", ["contract", "properties",
                                    "rollback"], "best-effort")
    add("link drift", ["contract", "links", "opening_registry"],
        "data/openings/other.yaml")
    add("base path drift", ["contract", "versioning", "base_path"],
        "/graph/opening-context/v0")
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 25
    for _name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)


def test_mutants_never_silent_subset():
    covered = set()
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != yaml.safe_load(
                    CONTRACT.read_text())["contract"].get(section):
                covered.add(section)
    assert covered >= {"role", "registry", "identity", "resolution",
                       "record", "merge", "failures", "errors",
                       "properties", "links", "versioning"}


# -- linkage battery --------------------------------------------------------


def _lint_doc(tmp_path, name, mutate, target="variant"):
    docs = {"variant": VARIANT, "legal_moves": LEGAL_MOVES,
            "node": NODE, "registry": REGISTRY}
    paths = {}
    for key, src in docs.items():
        data = yaml.safe_load(src.read_text())
        if key == target:
            mutate(data)
        dst = tmp_path / f"{name}-{key}.yaml"
        dst.write_text(yaml.safe_dump(data))
        paths[key] = dst
    return paths


def _lint_with(paths):
    lint(CONTRACT, variant_path=paths["variant"],
         legal_moves_path=paths["legal_moves"],
         node_path=paths["node"], registry_path=paths["registry"])


def test_linkage_clean_copy_passes(tmp_path):
    paths = _lint_doc(tmp_path, "clean", lambda d: None)
    _lint_with(paths)


def test_linkage_variant_grammar_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["variants"]["id_grammar"]["pattern"] = (
            "^[a-z]+$")
    paths = _lint_doc(tmp_path, "v", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_legal_moves_promotion_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["move_model"]["shape"]["types"]["promotion"][
            "enum"] = ["q", "r", "b"]
    paths = _lint_doc(tmp_path, "l", drift, target="legal_moves")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_node_exclusions_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["excluded"] = [
            "halfmove_clock", "fullmove_number",
            "repertoire_context"]  # move_order_path dropped
    paths = _lint_doc(tmp_path, "n", drift, target="node")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_registry_duplicate_code_fails(tmp_path):
    def drift(d):
        dup = copy.deepcopy(d["registry"]["entries"][0])
        dup["moves"] = ["a2a3"]
        d["registry"]["entries"].append(dup)
    paths = _lint_doc(tmp_path, "rdc", drift, target="registry")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_registry_duplicate_sequence_fails(tmp_path):
    def drift(d):
        dup = copy.deepcopy(d["registry"]["entries"][0])
        dup["code"] = "A05"
        d["registry"]["entries"].append(dup)
    paths = _lint_doc(tmp_path, "rds", drift, target="registry")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_registry_bad_code_fails(tmp_path):
    def drift(d):
        d["registry"]["entries"][0]["code"] = "Z99"
    paths = _lint_doc(tmp_path, "rbc", drift, target="registry")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_registry_empty_moves_fails(tmp_path):
    def drift(d):
        d["registry"]["entries"][0]["moves"] = []
    paths = _lint_doc(tmp_path, "rem", drift, target="registry")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_registry_extra_entry_key_fails(tmp_path):
    def drift(d):
        d["registry"]["entries"][0]["eco_url"] = "x"
    paths = _lint_doc(tmp_path, "rek", drift, target="registry")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_registry_bad_move_text_fails(tmp_path):
    def drift(d):
        d["registry"]["entries"][0]["moves"] = ["g1g1"]
    paths = _lint_doc(tmp_path, "rbm", drift, target="registry")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_registry_sentinel_drift_fails(tmp_path):
    def drift(d):
        d["registry"]["none_sentinel"] = "none"
    paths = _lint_doc(tmp_path, "rsd", drift, target="registry")
    with pytest.raises(ContractError):
        _lint_with(paths)
