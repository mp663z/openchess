"""T0149: graph provenance contract behavior battery.

Reference implementation FULLY DERIVED from data/contracts/
provenance.yaml plus the linked siblings (import, transposition-
node, route-edge, opening-context): the target-kind enum comes
from the targets section, each kind's target identity is computed
THROUGH the owning sibling contract's own machinery (imported,
never restated), the source-entry shapes come from the
source_entry section with the source registry READ from the
linked import contract, and the union merge algebra and failure
classes come from the merge and failures sections. Nothing about
provenance semantics is hardcoded here. Happy, boundary, set-
semantics, union, merge-algebra, malformed, rollback, laundering-
mutant, lint-mutant and sibling-linkage batteries below.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0086_fen_contract import (  # noqa: E402
    FenError,
    parse_fen,
)
from tests.test_t0113_position_digest_contract import (  # noqa: E402
    DigestError,
    digest_fen,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
    NodeError,
    _identity_tuple,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _docs as _node_docs,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    validate_record as _validate_node_record,
)
from tests.test_t0131_route_edge_contract import (  # noqa: E402
    EdgeError,
)
from tests.test_t0131_route_edge_contract import (  # noqa: E402
    _docs as _edge_docs,
)
from tests.test_t0131_route_edge_contract import (  # noqa: E402
    _record_identity as _edge_identity,
)
from tests.test_t0131_route_edge_contract import (  # noqa: E402
    validate_record as _validate_edge_record,
)
from tests.test_t0140_opening_context_contract import (  # noqa: E402
    ContextError,
    _validate_path,
)
from tests.test_t0140_opening_context_contract import (  # noqa: E402
    _docs as _ctx_docs,
)
from tools.provenance_contract_lint import (  # noqa: E402
    CONTEXT,
    CONTRACT,
    EDGE,
    ERROR_ENUM,
    FAILURE_MAPPING,
    IMPORT,
    NODE,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

NODE_KIND = "transposition_node"
EDGE_KIND = "route_edge"
CTX_KIND = "opening_context"


def _docs():
    return (yaml.safe_load(CONTRACT.read_text())["contract"],
            yaml.safe_load(IMPORT.read_text())["contract"])


class ProvenanceError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(pc, cls):
    raise ProvenanceError(cls, pc["failures"]["mapping"][cls])


def _registry_ids(ic):
    return [e["id"] for e in ic["sources"]["entries"]]


_TS_RE = re.compile(
    "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _leap_year(year):
    """Gregorian leap-year rule: divisible by 4, centuries only when
    divisible by 400."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _valid_timestamp(text):
    """The source_entry section's pinned timestamp grammar: RFC3339
    UTC, seconds precision, Z suffix, printable ASCII - validated as
    an ACTUAL calendar instant: Gregorian calendar rules with
    leap-year February 29 and 30/31-day months. LEAP-SECOND POLICY:
    the pinned NON-LEAP profile - second is 00-59, a :60 leap second
    is rejected, never normalized. ASCII digit classes only, never
    str.isdigit (Unicode)."""
    if type(text) is not str or not text.isascii():
        return False
    if _TS_RE.fullmatch(text) is None:
        return False
    year = int(text[0:4])
    month = int(text[5:7])
    day = int(text[8:10])
    hour = int(text[11:13])
    minute = int(text[14:16])
    second = int(text[17:19])
    if not 1 <= month <= 12:
        return False
    days = _DAYS_IN_MONTH[month - 1]
    if month == 2 and _leap_year(year):
        days += 1
    return (1 <= day <= days and hour <= 23
            and minute <= 59 and second <= 59)


def _valid_game_id(text):
    """Nonempty, delimiter-free printable ASCII: every byte in
    0x21-0x7E (printable, no space, no DEL, no control)."""
    if type(text) is not str or not text:
        return False
    return all(0x21 <= ord(ch) <= 0x7E for ch in text)


def _exact_dict(obj):
    """EXACT built-in dict whose every key is an EXACT str - checked
    before any set build, membership test or lookup, so a subclass
    cannot lie about its content and a key with a colliding hash and a
    raising __eq__ fails closed instead of escaping raw."""
    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))


def _validate_source_entry(pc, ic, entry):
    """A source entry satisfies the source_entry section exactly:
    the declared field set, a registry source id (fail closed), a
    pinned game_id storage form, and the pinned timestamp grammar."""
    if not _exact_dict(entry) or \
            set(entry.keys()) != set(pc["source_entry"]["fields"]):
        _fail(pc, "malformed_provenance_record")
    # exact-str type BEFORE membership: a hostile str subclass must
    # never reach a hash/== compare
    if type(entry["source_id"]) is not str:
        _fail(pc, "malformed_provenance_record")
    if entry["source_id"] not in _registry_ids(ic):
        _fail(pc, "unknown_source")
    if not _valid_game_id(entry["game_id"]):
        _fail(pc, "malformed_provenance_record")
    if not _valid_timestamp(entry["first_observed_at"]):
        _fail(pc, "malformed_provenance_record")
    return entry


def _source_key(entry):
    return (entry["source_id"], entry["game_id"],
            entry["first_observed_at"])


def _canonical_sources(pc, ic, sources):
    """Set semantics: order-free, exact duplicates collapse; the
    canonical stored form sorts by the full entry tuple."""
    if type(sources) is not list or not sources:
        _fail(pc, "malformed_provenance_record")  # nonempty pinned
    seen = {}
    for entry in sources:
        _validate_source_entry(pc, ic, entry)
        seen[_source_key(entry)] = {
            "source_id": entry["source_id"],
            "game_id": entry["game_id"],
            "first_observed_at": entry["first_observed_at"],
        }
    return [seen[key] for key in sorted(seen)]


def _validate_target(pc, kind, target):
    """The target identity is validated THROUGH the owning sibling
    contract's own machinery, imported never restated; any sibling
    rejection is a malformed_target_identity here."""
    if not _exact_dict(target):
        _fail(pc, "malformed_target_identity")
    if kind == NODE_KIND:
        if set(target.keys()) != {"variant", "snapshot_fen"}:
            _fail(pc, "malformed_target_identity")
        # total: explicit field-type guards BEFORE the sibling
        # calls - a non-string field never reaches sibling machinery
        if type(target["variant"]) is not str or \
                type(target["snapshot_fen"]) is not str:
            _fail(pc, "malformed_target_identity")
        nc, vc, dc, epc, fc = _node_docs()
        try:
            node_record = {
                "variant": target["variant"],
                "digest": digest_fen(target["variant"],
                                     target["snapshot_fen"]),
                "snapshot_fen": target["snapshot_fen"],
            }
            _validate_node_record(nc, vc, dc, epc, fc, node_record)
        except (NodeError, DigestError, FenError, TypeError):
            _fail(pc, "malformed_target_identity")
    elif kind == EDGE_KIND:
        if set(target.keys()) != {"variant", "move",
                                  "from_snapshot_fen",
                                  "to_snapshot_fen"}:
            _fail(pc, "malformed_target_identity")
        if not all(type(target[key]) is str for key in (
                "variant", "move", "from_snapshot_fen",
                "to_snapshot_fen")):
            _fail(pc, "malformed_target_identity")
        try:
            _validate_edge_record(*_edge_docs(), dict(target))
        except (EdgeError, TypeError):
            _fail(pc, "malformed_target_identity")
    elif kind == CTX_KIND:
        if set(target.keys()) != {"variant", "path_moves"}:
            _fail(pc, "malformed_target_identity")
        if type(target["variant"]) is not str or \
                type(target["path_moves"]) is not list or \
                not all(type(m) is str
                        for m in target["path_moves"]):
            _fail(pc, "malformed_target_identity")
        oc, vc, lc, nc, reg = _ctx_docs()
        try:
            _validate_path(oc, vc, lc, reg, target["variant"],
                           target["path_moves"])
        except (ContextError, TypeError):
            _fail(pc, "malformed_target_identity")
    else:
        _fail(pc, "unknown_target_kind")


def _target_identity(kind, target):
    """The canonical identity tuple per kind, computed THROUGH the
    sibling machinery - the only equality basis; no cache can
    disagree with the record because none is kept."""
    if kind == NODE_KIND:
        nc, vc, dc, epc, fc = _node_docs()
        position = parse_fen(fc, target["snapshot_fen"])
        return (NODE_KIND, target["variant"],
                _identity_tuple(nc, vc, dc, epc, fc,
                                target["variant"], position))
    if kind == EDGE_KIND:
        return (EDGE_KIND,) + tuple(_edge_identity(*_edge_docs(),
                                                   target))
    return (CTX_KIND, target["variant"], tuple(target["path_moves"]))


def validate_record(pc, ic, record):
    """A stored provenance record must satisfy the record section
    exactly: EXACTLY the declared field set, a closed-enum target
    kind, a NONEMPTY sources set of valid entries, and a target
    passing the owning sibling's validation. Returns the canonical
    record (sources deduplicated and sorted)."""
    if not _exact_dict(record) or \
            set(record.keys()) != set(pc["record"]["fields"]):
        _fail(pc, "malformed_provenance_record")
    kind = record["target_kind"]
    if type(kind) is not str:
        _fail(pc, "malformed_provenance_record")
    if kind not in pc["targets"]["kinds"]:
        _fail(pc, "unknown_target_kind")
    canonical_sources = _canonical_sources(pc, ic, record["sources"])
    _validate_target(pc, kind, record["target"])
    return {
        "target_kind": kind,
        "target": copy.deepcopy(record["target"]),
        "sources": canonical_sources,
    }


def _validate(record):
    return validate_record(*_docs(), record)


def _make_record(kind, target, sources):
    return validate_record(*_docs(), {
        "target_kind": kind,
        "target": target,
        "sources": sources,
    })


class ProvenanceTable:
    """The merge semantics of the contract's merge section: keyed by
    (target_kind, target identity) computed from the stored records
    ONLY, one record per key, insert-or-union-sources, and the
    pinned algebra: union makes merge idempotent, commutative and
    associative, and NO same-key conflict class exists - a same-key
    pair always merges. Table merge is ATOMIC: the exact stored
    source records are validated against the destination's linked
    docs before staging into a copy, committed only when the whole
    batch is valid."""

    def __init__(self, docs=None):
        self.pc, self.ic = docs if docs is not None else _docs()
        self.by_key = {}

    def insert(self, record):
        rec = validate_record(self.pc, self.ic, record)
        key = (rec["target_kind"],
               _target_identity(rec["target_kind"], rec["target"]))
        existing = self.by_key.get(key)
        if existing is None:
            self.by_key[key] = rec
            return rec
        merged_sources = _canonical_sources(
            self.pc, self.ic, existing["sources"] + rec["sources"])
        if merged_sources == existing["sources"]:
            return existing  # subset reinsert: a true no-op
        merged = {**existing}
        merged["sources"] = merged_sources
        self.by_key[key] = merged
        return merged

    def merge(self, other):
        """Table-to-table merge is iterated insertion of the EXACT
        stored records into a STAGED COPY - never records
        reconstructed under the receiver's own linked docs -
        committed only when the whole batch validates: the
        contract's atomic pin leaves this table bit-identical on
        any rejection, over both merge orders."""
        staged = ProvenanceTable((self.pc, self.ic))
        staged.by_key = copy.deepcopy(self.by_key)
        source = other.records()
        if type(source) is not list:
            _fail(self.pc, "malformed_provenance_record")
        for rec in source:
            validate_record(self.pc, self.ic, rec)
            staged.insert(rec)
        self.by_key = staged.by_key
        return self

    def records(self):
        return list(self.by_key.values())

    def serialize(self):
        out = []
        for key, rec in self.by_key.items():
            out.append((rec["target_kind"], repr(key),
                        tuple(_source_key(e) for e in rec["sources"])))
        return sorted(out)


def _table():
    return ProvenanceTable()


class _Batch:
    """A merge source that is just a bag of stored records - the
    contract's merge input type, as any deserialized batch could
    be."""

    def __init__(self, records):
        self._records = records

    def records(self):
        return list(self._records)


# -- pinned vectors ---------------------------------------------------------

E2E4_TO = AFTER_E4.replace(" e3 ", " - ")  # phantom target collapses
KINGS_E1E2_TO = "4k3/8/8/8/8/8/4K3/8 b - - 0 1"

NODE_TARGET = {"variant": "standard", "snapshot_fen": STARTPOS}
NODE_TARGET_2 = {"variant": "standard", "snapshot_fen": E2E4_TO}
EDGE_TARGET = {"variant": "standard", "move": "e2e4",
               "from_snapshot_fen": STARTPOS,
               "to_snapshot_fen": E2E4_TO}
EDGE_TARGET_2 = {"variant": "standard", "move": "e1e2",
                 "from_snapshot_fen": KINGS,
                 "to_snapshot_fen": KINGS_E1E2_TO}
CTX_TARGET = {"variant": "standard", "path_moves": ["e2e4", "c7c5"]}
CTX_TARGET_2 = {"variant": "standard",
                "path_moves": ["e2e4", "e7e5", "g1f3"]}

S1 = {"source_id": "lichess-public", "game_id": "lichess:abcdefgh",
      "first_observed_at": "2026-09-01T12:00:00Z"}
S2 = {"source_id": "pgn-file", "game_id": "pgn:local-game-1",
      "first_observed_at": "2026-09-02T08:30:00Z"}
S3 = {"source_id": "cbh-licensed", "game_id": "cbh:base42-game7",
      "first_observed_at": "2026-09-03T23:59:59Z"}


def test_lint_clean():
    lint()


def test_happy_insert_records_exact():
    t = _table()
    rec = t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
                    "sources": [S2, S1]})  # unsorted input
    assert set(rec.keys()) == {"target_kind", "target", "sources"}
    assert rec["target"] == NODE_TARGET
    # canonical form: sources sorted by the full entry tuple
    assert [_source_key(e) for e in rec["sources"]] == sorted(
        [_source_key(S1), _source_key(S2)])
    _validate(rec)
    edge_rec = t.insert({"target_kind": EDGE_KIND,
                         "target": EDGE_TARGET, "sources": [S1]})
    assert edge_rec["target"] == EDGE_TARGET
    _validate(edge_rec)
    ctx_rec = t.insert({"target_kind": CTX_KIND,
                        "target": CTX_TARGET, "sources": [S3]})
    assert ctx_rec["target"]["path_moves"] == ["e2e4", "c7c5"]
    _validate(ctx_rec)
    assert len(t.records()) == 3


def test_boundary_single_source_minimum():
    t = _table()
    rec = t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
                    "sources": [S1]})
    assert len(rec["sources"]) == 1
    # the sibling-legal EMPTY path is a valid context target - the
    # failure model never over-rejects
    ctx = t.insert({"target_kind": CTX_KIND,
                    "target": {"variant": "standard",
                               "path_moves": []},
                    "sources": [S1]})
    assert ctx["target"]["path_moves"] == []
    before = t.serialize()
    with pytest.raises(ProvenanceError) as exc:
        t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET_2,
                  "sources": []})
    assert exc.value.failure_class == "malformed_provenance_record"
    assert t.serialize() == before


def test_set_semantics_order_free_duplicates_collapse():
    t = _table()
    a = t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
                  "sources": [S1, S2, S3]})
    b = t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
                  "sources": [S3, S1, S2]})  # same set, other order
    assert a is b
    assert len(t.records()) == 1
    # exact duplicates within one record collapse
    c = t.insert({"target_kind": EDGE_KIND, "target": EDGE_TARGET,
                  "sources": [S1, S1, S2]})
    assert len(c["sources"]) == 2
    assert [_source_key(e) for e in c["sources"]] == sorted(
        [_source_key(S1), _source_key(S2)])


def test_union_merge_same_target_monotone():
    """One target carries exactly one record; the source set is the
    union and only grows - reinserting a subset is a no-op."""
    t = _table()
    t.insert({"target_kind": CTX_KIND, "target": CTX_TARGET,
              "sources": [S1]})
    rec = t.insert({"target_kind": CTX_KIND, "target": CTX_TARGET,
                    "sources": [S2]})
    assert len(t.records()) == 1
    assert [_source_key(e) for e in rec["sources"]] == sorted(
        [_source_key(S1), _source_key(S2)])
    before = t.serialize()
    again = t.insert({"target_kind": CTX_KIND, "target": CTX_TARGET,
                      "sources": [S1]})  # subset: no-op
    assert len(again["sources"]) == 2
    assert t.serialize() == before


def test_merge_algebra():
    """Idempotent, commutative, associative: merge is STRUCTURALLY
    union of validated exact records, so all groupings agree and a
    same-key pair never conflicts."""
    specs = [
        (NODE_KIND, NODE_TARGET, [S1]),
        (NODE_KIND, NODE_TARGET, [S2]),      # same key, union
        (EDGE_KIND, EDGE_TARGET, [S1, S3]),
        (CTX_KIND, CTX_TARGET, [S3]),
        (CTX_KIND, CTX_TARGET_2, [S2]),
        (EDGE_KIND, EDGE_TARGET_2, [S1]),
        (NODE_KIND, NODE_TARGET_2, [S2, S3]),
    ]
    groups = [specs[:3], specs[3:5], specs[5:]]

    def built(spec_list):
        t = _table()
        for kind, target, sources in spec_list:
            t.insert({"target_kind": kind, "target": target,
                      "sources": sources})
        return t

    t1 = built(specs)
    t1.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
               "sources": [S1]})  # reinsert: no-op
    t2 = built(list(reversed(specs)))
    assert t1.serialize() == t2.serialize()
    assert len(t1.records()) == 6  # the same-key pair folds

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
    # the unioned node record holds both sources under every grouping
    for grouped in (t1, t2, left, right):
        node_rec = [r for r in grouped.records()
                    if r["target_kind"] == NODE_KIND
                    and r["target"] == NODE_TARGET]
        assert len(node_rec) == 1
        assert [_source_key(e) for e in node_rec[0]["sources"]] == \
            sorted([_source_key(S1), _source_key(S2)])


def test_unknown_target_kind_rejected():
    t = _table()
    before = t.serialize()
    with pytest.raises(ProvenanceError) as exc:
        t.insert({"target_kind": "san_position",
                  "target": NODE_TARGET, "sources": [S1]})
    assert exc.value.failure_class == "unknown_target_kind"
    assert exc.value.code == FAILURE_MAPPING["unknown_target_kind"]
    assert exc.value.code in ERROR_ENUM
    assert t.serialize() == before


def test_unknown_source_rejected_fail_closed():
    """The rights surface fails closed: a source id absent from the
    import registry is unknown_source, never coerced."""
    t = _table()
    before = t.serialize()
    bad = dict(S1, source_id="chessdotcom-scrape")
    with pytest.raises(ProvenanceError) as exc:
        t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
                  "sources": [bad]})
    assert exc.value.failure_class == "unknown_source"
    assert exc.value.code == FAILURE_MAPPING["unknown_source"]
    assert exc.value.code in ERROR_ENUM
    assert t.serialize() == before


MALFORMED_TARGETS = [
    (NODE_KIND, {"variant": "standard",
                 "snapshot_fen": "garbage w - - 0 1"}),
    (NODE_KIND, {"variant": "c960", "snapshot_fen": STARTPOS}),
    (NODE_KIND, {"variant": "standard",
                 "snapshot_fen": AFTER_E4}),  # phantom ep in storage
    (NODE_KIND, {"variant": "standard"}),  # missing field
    (EDGE_KIND, {"variant": "standard", "move": "e2e2",
                 "from_snapshot_fen": STARTPOS,
                 "to_snapshot_fen": E2E4_TO}),
    (EDGE_KIND, {"variant": "standard", "move": "e2e4",
                 "from_snapshot_fen": STARTPOS,
                 "to_snapshot_fen": AFTER_E4}),  # phantom ep
    (EDGE_KIND, {"variant": "standard", "move": "e2e4",
                 "from_snapshot_fen": STARTPOS}),  # missing field
    (CTX_KIND, {"variant": "standard", "path_moves": ["e2e4", "x7c5"]}),
    (CTX_KIND, {"variant": "standard", "path_moves": "e2e4"}),
    (CTX_KIND, {"variant": "chess960",
                "path_moves": ["e2e4", "c7c5"]}),
    (CTX_KIND, {"variant": "standard"}),  # missing field
]


@pytest.mark.parametrize("kind,target", MALFORMED_TARGETS)
def test_malformed_target_identity_rejected(kind, target):
    t = _table()
    before = t.serialize()
    with pytest.raises(ProvenanceError) as exc:
        t.insert({"target_kind": kind, "target": target,
                  "sources": [S1]})
    assert exc.value.failure_class == "malformed_target_identity"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_target_identity"]
    assert exc.value.code in ERROR_ENUM
    assert t.serialize() == before


TIMESTAMP_VALID = [
    "2024-02-29T00:00:00Z",  # leap-year February 29
    "2000-02-29T12:00:00Z",  # divisible-by-400 century leap
    "2026-01-31T23:59:59Z",  # 31-day month, max day
    "2026-04-30T12:00:00Z",  # 30-day month, max day
    "2026-12-31T23:59:59Z",  # year boundary
]
TIMESTAMP_INVALID = [
    "2026-02-29T00:00:00Z",  # non-leap February 29
    "2024-02-30T00:00:00Z",  # February 30 even in a leap year
    "2100-02-29T00:00:00Z",  # century non-leap (not div by 400)
    "2026-04-31T00:00:00Z",  # 30-day month, day 31
    "2026-06-31T00:00:00Z",  # June has 30 days
    "2026-09-01T12:00:60Z",  # :60 leap second: pinned NON-LEAP
                             # profile rejects, never normalizes
]


def test_timestamp_calendar_boundaries():
    """first_observed_at is validated as an ACTUAL RFC3339 UTC
    instant: Gregorian leap-year February, 30/31-day months, and
    the pinned non-leap-second profile - impossible calendar dates
    are malformed, never stored."""
    for text in TIMESTAMP_VALID:
        entry = dict(S1, first_observed_at=text)
        rec = _make_record(NODE_KIND, NODE_TARGET, [entry])
        assert rec["sources"][0]["first_observed_at"] == text
    t = _table()
    for text in TIMESTAMP_INVALID:
        before = t.serialize()
        entry = dict(S1, first_observed_at=text)
        with pytest.raises(ProvenanceError) as exc:
            t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
                      "sources": [entry]})
        assert exc.value.failure_class == \
            "malformed_provenance_record", text
        assert t.serialize() == before, text


# Cartesian target-field mutation battery: every non-string (or
# non-list) shape escapes nowhere - each lands as
# malformed_target_identity with the pinned code and a bit-identical
# table, never as a raw sibling exception.
_BAD_SHAPES = [None, True, 1, [], {}]


def _cartesian_cases():
    cases = []

    def add(kind, target):
        cases.append((kind, target))

    for bad in _BAD_SHAPES:
        add(NODE_KIND, {"variant": "standard", "snapshot_fen": bad})
        add(EDGE_KIND, {"variant": "standard", "move": "e2e4",
                        "from_snapshot_fen": bad,
                        "to_snapshot_fen": E2E4_TO})
        add(EDGE_KIND, {"variant": "standard", "move": "e2e4",
                        "from_snapshot_fen": STARTPOS,
                        "to_snapshot_fen": bad})
        add(EDGE_KIND, {"variant": "standard", "move": bad,
                        "from_snapshot_fen": STARTPOS,
                        "to_snapshot_fen": E2E4_TO})
        add(EDGE_KIND, {"variant": bad, "move": "e2e4",
                        "from_snapshot_fen": STARTPOS,
                        "to_snapshot_fen": E2E4_TO})
        add(NODE_KIND, {"variant": bad, "snapshot_fen": STARTPOS})
        add(CTX_KIND, {"variant": bad, "path_moves": ["e2e4"]})
        if not isinstance(bad, list):
            # [] is NOT a defect here: the sibling context contract
            # pins the empty path as legal (none-sentinel
            # resolution) - every other non-list shape fails closed
            add(CTX_KIND, {"variant": "standard", "path_moves": bad})
        add(CTX_KIND, {"variant": "standard",
                       "path_moves": ["e2e4", bad]})
    return cases


def test_cartesian_target_mutation_battery():
    """Totality: (None, bool, int, list, mapping) across node
    snapshot_fen and variant, both edge snapshot fields, edge move
    and variant, and context variant / path_moves / path contents -
    every case is malformed_target_identity with the pinned code and
    a bit-identical rollback, never a raw AttributeError."""
    t = _table()
    t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
              "sources": [S1]})
    before = t.serialize()
    before_keys = copy.deepcopy(t.by_key)
    for kind, target in _cartesian_cases():
        with pytest.raises(ProvenanceError) as exc:
            t.insert({"target_kind": kind, "target": target,
                      "sources": [S2]})
        assert exc.value.failure_class == \
            "malformed_target_identity", (kind, target)
        assert exc.value.code == FAILURE_MAPPING[
            "malformed_target_identity"], (kind, target)
        assert exc.value.code in ERROR_ENUM
    assert t.serialize() == before
    assert t.by_key == before_keys


def _entry_cases():
    cases = []

    def add(name, mutate, cls="malformed_provenance_record"):
        entry = copy.deepcopy(S1)
        mutate(entry)
        cases.append((name, entry, cls))

    add("extra-field", lambda e: e.__setitem__("note", "x"))
    add("missing-game_id", lambda e: e.pop("game_id"))
    add("unknown-source", lambda e: e.__setitem__(
        "source_id", "shadow-corpus"), "unknown_source")
    add("empty-game_id", lambda e: e.__setitem__("game_id", ""))
    add("game_id-with-space", lambda e: e.__setitem__(
        "game_id", "pgn:game 1"))
    add("game_id-non-ascii", lambda e: e.__setitem__(
        "game_id", "pgn:partie-é"))
    add("game_id-not-string", lambda e: e.__setitem__("game_id", 42))
    add("ts-month-13", lambda e: e.__setitem__(
        "first_observed_at", "2026-13-01T12:00:00Z"))
    add("ts-day-00", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-00T12:00:00Z"))
    add("ts-hour-24", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-01T24:00:00Z"))
    add("ts-second-60", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-01T12:00:60Z"))
    add("ts-missing-Z", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-01T12:00:00"))
    add("ts-lowercase-z", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-01T12:00:00z"))
    add("ts-offset-form", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-01T12:00:00+00:00"))
    add("ts-fractional", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-01T12:00:00.5Z"))
    add("ts-space-separator", lambda e: e.__setitem__(
        "first_observed_at", "2026-09-01 12:00:00Z"))
    return cases


def test_malformed_source_entries_rejected():
    for name, entry, cls in _entry_cases():
        t = _table()
        before = t.serialize()
        with pytest.raises(ProvenanceError) as exc:
            t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
                      "sources": [entry]})
        assert exc.value.failure_class == cls, name
        assert exc.value.code == FAILURE_MAPPING[cls], name
        assert t.serialize() == before, name


def test_malformed_record_shape_rejected():
    t = _table()
    before = t.serialize()
    base = {"target_kind": NODE_KIND, "target": NODE_TARGET,
            "sources": [S1]}
    for name, mutate in (
            ("extra-field", lambda r: r.__setitem__("weight", 1)),
            ("missing-target", lambda r: r.pop("target")),
            ("sources-not-list",
             lambda r: r.__setitem__("sources", "pgn-file"))):
        rec = copy.deepcopy(base)
        mutate(rec)
        with pytest.raises(ProvenanceError) as exc:
            t.insert(rec)
        assert exc.value.failure_class == \
            "malformed_provenance_record", name
    assert t.serialize() == before


def test_records_exact_fields_and_rebuild():
    """Adversarial witness: no hidden cache exists or can diverge -
    every stored record has EXACTLY the three declared fields, and
    a table rebuilt from only those records is identical."""
    t = _table()
    for kind, target, sources in (
            (NODE_KIND, NODE_TARGET, [S1, S2]),
            (EDGE_KIND, EDGE_TARGET, [S3]),
            (CTX_KIND, CTX_TARGET, [S1])):
        rec = t.insert({"target_kind": kind, "target": target,
                        "sources": sources})
        assert set(rec.keys()) == {"target_kind", "target", "sources"}
    for rec in t.records():
        assert set(rec.keys()) == {"target_kind", "target", "sources"}
    rebuilt = _table().merge(t)
    assert rebuilt.serialize() == t.serialize()


def test_rollback_bit_identical():
    t = _table()
    t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
              "sources": [S1]})
    before = t.serialize()
    before_keys = copy.deepcopy(t.by_key)
    rejected = [
        {"target_kind": "nope", "target": NODE_TARGET,
         "sources": [S1]},
        {"target_kind": NODE_KIND, "target": NODE_TARGET,
         "sources": []},
        {"target_kind": NODE_KIND, "target": NODE_TARGET,
         "sources": [dict(S1, source_id="shadow")]},
        {"target_kind": EDGE_KIND,
         "target": dict(EDGE_TARGET, move="e2e2"), "sources": [S2]},
        {"target_kind": CTX_KIND,
         "target": {"variant": "standard"}, "sources": [S3]},
    ]
    for rec in rejected:
        with pytest.raises(ProvenanceError):
            t.insert(rec)
    assert t.serialize() == before
    assert t.by_key == before_keys


def test_batch_rollback_no_partial_commit():
    """A merge whose batch first yields a valid unrelated record and
    THEN a malformed one commits NOTHING - not even the valid
    prefix."""
    t = _table()
    t.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
              "sources": [S1]})
    before = t.serialize()
    before_keys = copy.deepcopy(t.by_key)
    bad = _make_record(EDGE_KIND, EDGE_TARGET, [S2])
    bad["sources"][0]["note"] = "hidden"
    batch = _Batch([
        _make_record(CTX_KIND, CTX_TARGET, [S3]),  # unrelated valid
        bad,
    ])
    with pytest.raises(ProvenanceError) as exc:
        t.merge(batch)
    assert exc.value.failure_class == "malformed_provenance_record"
    assert t.serialize() == before
    assert t.by_key == before_keys


def test_failing_batch_both_orders_reject():
    """Over a failing batch BOTH merge orders reject and leave the
    same pre-merge destinations - never first-writer residue."""
    a = _table()
    a.insert({"target_kind": NODE_KIND, "target": NODE_TARGET,
              "sources": [S1]})
    b = _table()
    b.insert({"target_kind": EDGE_KIND, "target": EDGE_TARGET,
              "sources": [S2]})
    a_before, b_before = a.serialize(), b.serialize()
    a_keys = copy.deepcopy(a.by_key)
    b_keys = copy.deepcopy(b.by_key)
    bad = _make_record(CTX_KIND, CTX_TARGET, [S3])
    bad["sources"] = []  # empty sources: malformed
    batch = _Batch([_make_record(NODE_KIND, NODE_TARGET_2, [S3]),
                    bad])
    with pytest.raises(ProvenanceError):
        a.merge(batch)
    with pytest.raises(ProvenanceError):
        b.merge(batch)
    assert a.serialize() == a_before and a.by_key == a_keys
    assert b.serialize() == b_before and b.by_key == b_keys


def test_mixed_batch_failure_precedence():
    """Pinned externally observable precedence: the FIRST defect in
    batch order wins."""
    unknown = _make_record(NODE_KIND, NODE_TARGET_2, [S1])
    unknown["sources"] = [dict(S1, source_id="shadow")]
    malformed = _make_record(EDGE_KIND, EDGE_TARGET, [S2])
    malformed["extra"] = "field"
    t = _table()
    before = t.serialize()
    with pytest.raises(ProvenanceError) as exc:
        t.merge(_Batch([unknown, malformed]))
    assert exc.value.failure_class == "unknown_source"
    assert t.serialize() == before
    with pytest.raises(ProvenanceError) as exc:
        t.merge(_Batch([malformed, unknown]))
    assert exc.value.failure_class == "malformed_provenance_record"
    assert t.serialize() == before


def test_mutant_reconstruction_merge_would_launder():
    """Behavioral mutant: a merge that rebuilds provenance from
    extracted values WITHOUT validating the exact stored record
    launders a malformed record into a valid one - pinned here so
    the battery proves the real merge is not that shape.
    Counter-test: a valid exact record still merges."""
    def mutant_merge(self, other):
        for rec in other.records():
            self.insert({"target_kind": rec["target_kind"],
                         "target": rec["target"],
                         "sources": [
                             {k: v for k, v in e.items()
                              if k in ("source_id", "game_id",
                                       "first_observed_at")}
                             for e in rec["sources"]]})
        return self

    bad = _make_record(NODE_KIND, NODE_TARGET_2, [S1])
    bad["sources"][0]["note"] = "hidden"  # extra field: reject
    t = _table()
    with pytest.raises(ProvenanceError) as exc:
        t.merge(_Batch([bad]))
    assert exc.value.failure_class == "malformed_provenance_record"
    assert t.records() == []
    # the mutant accepts and silently launders the same record
    t2 = _table()
    mutant_merge(t2, _Batch([bad]))
    assert len(t2.records()) == 1
    assert set(t2.records()[0]["sources"][0].keys()) == {
        "source_id", "game_id", "first_observed_at"}
    # counter-test: the valid exact record still merges for real
    t3 = _table()
    good = _make_record(NODE_KIND, NODE_TARGET_2, [S1])
    t3.merge(_Batch([good]))
    assert len(t3.records()) == 1
    assert t3.records()[0] == good


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
        "content-attribution")
    add("attribution by path", ["contract", "role", "attribution"],
        "by-first-path")
    add("completeness drift", ["contract", "role", "completeness"],
        "first-source-only")
    add("removal allowed", ["contract", "role", "additive_only"],
        "entries-removable")
    add("rights open", ["contract", "role", "rights_surface"],
        "unknown-sources-coerced")
    add("target kinds drift", ["contract", "targets", "kinds"],
        ["transposition_node", "route_edge"])
    add("target kinds open", ["contract", "targets", "closed"],
        False)
    add("identity restated", ["contract", "targets",
                              "identity_source"], "restated-here")
    add("node identity drift", ["contract", "targets",
                                "transposition_node_identity"],
        "digest-equality")
    add("source entry fields drift",
        ["contract", "source_entry", "fields"],
        ["source_id", "game_id"])
    add("source entry extra field",
        ["contract", "source_entry", "fields"],
        ["source_id", "game_id", "first_observed_at", "note"])
    add("registry drift", ["contract", "source_entry",
                           "source_id_form"], "any-string")
    add("game_id recomputed", ["contract", "source_entry",
                               "game_id_grammar"],
        "recomputed-from-content")
    add("timestamp grammar drift", ["contract", "source_entry",
                                    "timestamp_grammar"],
        "iso8601-any-offset")
    add("set semantics drift", ["contract", "source_entry",
                                "set_semantics"], "ordered-list")
    add("record fields drift", ["contract", "record", "fields"],
        ["target_kind", "sources"])
    add("sources optional", ["contract", "record",
                             "sources_nonempty"], False)
    add("derived stored drift", ["contract", "record",
                                 "derived_fields_stored"],
        "source-count")
    add("insert drift", ["contract", "merge", "insert"],
        "always-create")
    add("atomic dropped", ["contract", "merge", "atomic"], False)
    add("idempotence dropped", ["contract", "merge", "idempotent"],
        False)
    add("commutativity dropped", ["contract", "merge",
                                  "commutative"], False)
    add("associativity dropped", ["contract", "merge",
                                  "associative"], False)
    add("two records allowed", ["contract", "merge",
                                "same_target_never_two_records"],
        False)
    add("same-key conflict invented", ["contract", "merge",
                                       "same_key_conflict"],
        "rejected-as-conflicting_provenance")
    add("batch conflict partial commit", ["contract", "merge",
                                          "conflict_in_batch"],
        "valid-prefix-commits")
    add("source validation dropped", ["contract", "merge",
                                      "source_validation"],
        "rebuild-from-values")
    add("rejected insert mutates", ["contract", "merge",
                                    "rejected_insert_changes_nothing"],
        False)
    add("failure class dropped", ["contract", "failures", "classes"],
        ["malformed_provenance_record", "unknown_source"])
    add("failure mapping drift", ["contract", "failures", "mapping",
                                  "unknown_source"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"], False)
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "unknown_source"])
    add("property drift", ["contract", "properties",
                           "union_total"],
        "same-key-rejected")
    add("rollback property drift", ["contract", "properties",
                                    "rollback"], "best-effort")
    add("link drift", ["contract", "links", "import_contract"],
        "data/contracts/lichess_response.yaml")
    add("base path drift", ["contract", "versioning", "base_path"],
        "/graph/provenance/v0")
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
    assert covered >= {"role", "targets", "source_entry", "record",
                       "merge", "failures", "errors", "properties",
                       "links", "versioning"}


# -- linkage battery ---------------------------------------------------------


def _lint_doc(tmp_path, name, mutate, target="import"):
    docs = {"import": IMPORT, "node": NODE, "edge": EDGE,
            "context": CONTEXT}
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
    lint(CONTRACT, import_path=paths["import"],
         node_path=paths["node"], edge_path=paths["edge"],
         context_path=paths["context"])


def test_linkage_clean_copy_passes(tmp_path):
    paths = _lint_doc(tmp_path, "clean", lambda d: None)
    _lint_with(paths)


def test_linkage_import_registry_shrinks_fails(tmp_path):
    def drift(d):
        d["contract"]["sources"]["entries"] = \
            d["contract"]["sources"]["entries"][:-1]
    paths = _lint_doc(tmp_path, "i", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_import_rights_class_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["sources"]["entries"][0]["rights_class"] = \
            "public-domain"
    paths = _lint_doc(tmp_path, "r", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_import_dedup_key_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["idempotency"]["dedup_key"] = "content_sha256"
    paths = _lint_doc(tmp_path, "d", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_import_unsafe_source_id_fails(tmp_path):
    def drift(d):
        d["contract"]["sources"]["entries"].append(
            {"id": "Bad Source", "kind": "user-file",
             "rights_class": "user-own"})
    paths = _lint_doc(tmp_path, "u", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_node_identity_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["excluded"] = ["halfmove_clock"]
    paths = _lint_doc(tmp_path, "n", drift, target="node")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_edge_identity_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["components"] = [
            "variant", "from_node", "to_node"]
    paths = _lint_doc(tmp_path, "e", drift, target="edge")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_context_identity_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["key"] = ["variant", "node"]
    paths = _lint_doc(tmp_path, "c", drift, target="context")
    with pytest.raises(ContractError):
        _lint_with(paths)


# -- totality sweep: hostile keys, values and container subclasses -----------


class _SK(str):
    """str subclass: hashes like the text it imitates, raises on ==."""

    def __hash__(self):
        return str.__hash__(str(self))

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    __ne__ = __eq__


class _HK:
    """Non-str key with a colliding hash and a raising ==."""

    def __init__(self, text):
        self.text = text

    def __hash__(self):
        return hash(self.text)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    __ne__ = __eq__


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


def _rekey(d, field, key_type):
    return {key_type(k) if k == field else k: v for k, v in d.items()}


_TARGETS = {NODE_KIND: NODE_TARGET, EDGE_KIND: EDGE_TARGET, CTX_KIND: CTX_TARGET}
MPR = "malformed_provenance_record"
MTI = "malformed_target_identity"


def _rec(kind=NODE_KIND, target=None, sources=None):
    return {"target_kind": kind,
            "target": copy.deepcopy(_TARGETS[kind]) if target is None else target,
            "sources": [dict(S1)] if sources is None else sources}


def _hostile_cases():
    cases = []
    for kt in (_SK, _HK):
        n = kt.__name__[1:]
        for f in ("target_kind", "target", "sources"):
            cases.append((f"{n}-record-key-{f}", _rekey(_rec(), f, kt), MPR))
        for f in S1:
            cases.append((f"{n}-source-key-{f}", _rec(sources=[_rekey(S1, f, kt)]), MPR))
        for kind, target in _TARGETS.items():
            for f in target:
                cases.append((f"{n}-{kind}-target-key-{f}",
                              _rec(kind, target=_rekey(target, f, kt)), MTI))
    cases.append(("SK-target_kind", dict(_rec(), target_kind=_SK(NODE_KIND)), MPR))
    for f in S1:
        cases.append((f"SK-source-{f}", _rec(sources=[dict(S1, **{f: _SK(S1[f])})]), MPR))
    for kind, target in _TARGETS.items():
        for f, v in target.items():
            if type(v) is str:
                hostile = dict(target, **{f: _SK(v)})
                cases.append((f"SK-{kind}-{f}", _rec(kind, target=hostile), MTI))
    cases.append(("SK-path-move", _rec(CTX_KIND, target=dict(
        CTX_TARGET, path_moves=[_SK("e2e4"), "c7c5"])), MTI))
    cases.append(("listsub-path", _rec(CTX_KIND, target=dict(
        CTX_TARGET, path_moves=_ListSub(CTX_TARGET["path_moves"]))), MTI))
    cases.append(("dictsub-record", _DictSub(_rec()), MPR))
    cases.append(("dictsub-source", _rec(sources=[_DictSub(S1)]), MPR))
    cases.append(("listsub-sources", _rec(sources=_ListSub([dict(S1)])), MPR))
    for kind, target in _TARGETS.items():
        cases.append((f"dictsub-{kind}-target", _rec(kind, target=_DictSub(target)), MTI))
    return cases


_CASES = _hostile_cases()


def _expect(call, table, cls):
    before = table.serialize()
    with pytest.raises(ProvenanceError) as exc:
        call()
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert table.serialize() == before


@pytest.mark.parametrize("name,record,cls", _CASES, ids=[c[0] for c in _CASES])
def test_insert_total_over_hostile_records(name, record, cls):
    t = _table()
    t.insert(_rec(sources=[dict(S2)]))
    _expect(lambda: t.insert(record), t, cls)


@pytest.mark.parametrize("name,record,cls", _CASES, ids=[c[0] for c in _CASES])
def test_merge_total_over_hostile_records(name, record, cls):
    t = _table()
    t.insert(_rec(sources=[dict(S2)]))
    _expect(lambda: t.merge(_Batch([_rec(EDGE_KIND), record])), t, cls)


@pytest.mark.parametrize("key_type", [_SK, _HK], ids=["SK", "HK"])
def test_hostile_record_key_escapes_raw_without_guard(key_type):
    hostile = _rekey(_rec(), "target_kind", key_type)
    with pytest.raises(RuntimeError):
        set(hostile.keys()) != {"target_kind", "target", "sources"}  # noqa: B015


class _RaisingList(list):
    def __iter__(self):
        raise RuntimeError("hostile __iter__")


class _BadSource:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


@pytest.mark.parametrize(
    "records",
    [None, _RaisingList([1]), (), {}],
    ids=["None", "raising-list", "tuple", "dict"],
)
def test_merge_rejects_non_list_sources(records):
    t = _table()
    t.insert(_rec(sources=[dict(S2)]))
    _expect(lambda: t.merge(_BadSource(records)), t, MPR)


def test_non_list_source_escapes_raw_without_guard():
    """Guardless demo: iterating the source without the exact-list check
    raises raw on None and on a raising list subclass."""
    for records in (None, _RaisingList([1])):
        with pytest.raises((TypeError, RuntimeError)):
            for _ in records:
                pass
