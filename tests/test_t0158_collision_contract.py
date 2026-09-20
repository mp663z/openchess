"""T0158: graph collision contract behavior battery.

Reference probe FULLY DERIVED from data/contracts/collision.yaml
plus the linked siblings (variant, position-digest, transposition-
node): the collision definition, separation rule, TRUST BOUNDARY
and guarantees come from the definition/separation/guarantees
sections. The probe composes the REAL transposition-node machinery
(record construction, canonical identity, record validation -
imported, never reimplemented) with the contract's pinned table
model: buckets as an accelerator ONLY, plus a canonical-identity
index INDEPENDENT of buckets as the equality search domain, and
insert-time consistency validation of the oracle (equal canonical
identity must yield the same valid-format bucket key; divergence
fails closed as accelerator_inconsistent). Happy, forced-
collision, trust-boundary repro, adversarial-twin permutation,
cross-oracle merge, malformed, rollback, behavioral-mutant,
lint-mutant and sibling-linkage batteries below.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0113_position_digest_contract import (  # noqa: E402
    digest_fen,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    LEGAL_EP,
    STARTPOS,
    NodeError,
    _make_record,
    _record_identity,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _docs as _node_docs,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    validate_record as _validate_node_record,
)
from tools.collision_contract_lint import (  # noqa: E402
    CONTRACT,
    DIGEST,
    ERROR_ENUM,
    FAILURE_MAPPING,
    NODE,
    VARIANT,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402


def _docs():
    return yaml.safe_load(CONTRACT.read_text())["contract"]


_NODE_DOCS = _node_docs()


def _digest_re():
    """The bucket-key format READ from the linked digest contract,
    never restated; compiled once."""
    return re.compile(
        yaml.safe_load(DIGEST.read_text())["contract"]["digest"]
        ["format"]["regex"])


_DIGEST_RE = _digest_re()


class CollisionError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cc, cls):
    raise CollisionError(cls, cc["failures"]["mapping"][cls])


# VALID-format oracles for the witness batteries:
def constant_oracle(variant, fen):
    """Total collision: everything into one valid bucket."""
    return "pdv1:" + "0" * 64


def raw_oracle(variant, fen):
    """UNTRUSTED: hashes the raw FEN text INCLUDING the identity-
    excluded clocks and the raw EP field - equal canonical
    identities get different bucket keys."""
    return "pdv1:" + hashlib.sha256(
        f"{variant}\n{fen}".encode()).hexdigest()


def stateful_oracle():
    """UNTRUSTED: a fresh valid-format digest per call - even
    byte-identical input never repeats a key."""
    counter = itertools.count()

    def oracle(variant, fen):
        return "pdv1:" + hashlib.sha256(
            f"call-{next(counter)}".encode()).hexdigest()
    return oracle


def raising_oracle(fail_on):
    """UNTRUSTED: raises ValueError on the Nth call (1-based)."""
    counter = itertools.count(1)

    def oracle(variant, fen):
        if next(counter) >= fail_on:
            raise ValueError("oracle exploded")
        return "pdv1:" + hashlib.sha256(
            f"{variant}\n{fen}".encode()).hexdigest()
    return oracle


def midway_oracle(fail_on):
    """UNTRUSTED: real digest for the first fail_on-1 calls, then
    raises - under SINGLE EVALUATION (one oracle call per incoming
    record per merge), fail_on=2 means the FIRST record's call
    succeeds and the SECOND record's only call explodes."""
    counter = itertools.count(1)

    def oracle(variant, fen):
        if next(counter) >= fail_on:
            raise ValueError("midway failure")
        return digest_fen(variant, fen)
    return oracle


class _HostileHashStr(str):
    """Valid text, hostile hash: explodes as a dict key."""

    def __hash__(self):
        raise ValueError("hostile hash")


class _HostileEqStr(str):
    """Valid text, hostile equality."""

    def __eq__(self, other):
        raise ValueError("hostile eq")

    def __hash__(self):
        return str.__hash__(self)


class _DeceptiveStr(str):
    """Valid text, deceptive semantics: never equal, constant
    hash - would silently misbucket every lookup."""

    def __eq__(self, other):
        return False

    def __hash__(self):
        return 0


HOSTILE_SUBCLASSES = [_HostileHashStr, _HostileEqStr,
                      _DeceptiveStr]


def subclass_oracle(cls):
    """UNTRUSTED: every call returns a VALID-TEXT key built from a
    hostile str subclass."""
    def oracle(variant, fen):
        return cls("pdv1:" + "0" * 64)
    return oracle


def escalating_subclass_oracle(cls):
    """UNTRUSTED: plain built-in str on the first call, hostile
    subclass from the second."""
    counter = itertools.count(1)

    def oracle(variant, fen):
        if next(counter) == 1:
            return "pdv1:" + hashlib.sha256(
                f"{variant}\n{fen}".encode()).hexdigest()
        return cls("pdv1:" + "0" * 64)
    return oracle


_RECORD_FIELDS = frozenset({"variant", "digest", "snapshot_fen"})


class CollisionProbe:
    """The contract's pinned table model: buckets accelerate lookup
    ONLY; the canonical-identity index (INDEPENDENT of buckets) is
    the equality search domain; and the oracle trust boundary is
    enforced at insert - an equal canonical identity yielding a
    DIFFERENT bucket key, or an invalid-format key, fails closed
    as accelerator_inconsistent. Record construction, identity and
    validation all compose the REAL node machinery."""

    def __init__(self, oracle, docs=None):
        self.cc = docs if docs is not None else _docs()
        self.oracle = oracle
        self.buckets = {}         # accelerator ONLY
        self.identity_index = {}  # canonical identity -> record

    def _identity(self, record):
        nc, vc, dc, epc, fc = _NODE_DOCS
        return _record_identity(nc, vc, dc, epc, fc, record)

    def _call_oracle(self, variant, fen):
        """THE single trust-boundary crossing - EVERY oracle
        invocation (insert path and merge revalidation) goes
        through here. An oracle that RAISES, or returns an
        invalid-format key, maps to the pinned
        accelerator_inconsistent outcome. The contract's own typed
        validation errors are raised by sibling machinery OUTSIDE
        this wrapper and are never caught or relabeled here."""
        try:
            key = self.oracle(variant, fen)
        except Exception:
            _fail(self.cc, "accelerator_inconsistent")
        # EXACT built-in str only: a valid-text str subclass stays
        # hostile (raising/deceptive __hash__ or __eq__) past a
        # mere isinstance check - reject it as untrusted output.
        if type(key) is not str or \
                _DIGEST_RE.fullmatch(key) is None:
            _fail(self.cc, "accelerator_inconsistent")
        return key

    def insert(self, variant_id, fen_text):
        nc, vc, dc, epc, fc = _NODE_DOCS
        try:
            rec = _make_record(nc, vc, dc, epc, fc,
                               self._call_oracle,
                               variant_id, fen_text)
        except NodeError:
            _fail(self.cc, "malformed_collision_record")
        identity = self._identity(rec)
        return self._staged_insert(rec, identity, rec["digest"])

    def _staged_insert(self, rec, identity, key):
        """THE single commit path: accepts ONLY a prevalidated
        (record, canonical identity, bucket key) tuple - the exact
        built-in key RETAINED from one boundary evaluation. No
        untrusted input is re-queried here."""
        existing = self.identity_index.get(identity)
        if existing is not None:
            # TRUST BOUNDARY: equal canonical identity MUST yield
            # the same bucket key - divergence fails closed and
            # the insert changes nothing (caller rolls back)
            if existing["digest"] != key:
                _fail(self.cc, "accelerator_inconsistent")
            return existing  # same identity, never a second record
        # TRANSACTIONAL: both structures are staged; the commit
        # happens only after EVERY fallible operation on the
        # bucket key has succeeded - a hostile key can never
        # leave index written and buckets unwritten.
        staged_index = dict(self.identity_index)
        staged_buckets = {k: list(v) for k, v in
                          self.buckets.items()}
        staged_index[identity] = rec
        staged_buckets.setdefault(key, []).append(rec)
        # exercise the key's dict behavior BEFORE commit
        _ = key in staged_buckets
        _ = staged_buckets[key]
        self.identity_index = staged_index
        self.buckets = staged_buckets
        return rec

    def lookup_by_bucket_key(self, bucket_key):
        """A bucket key presented AS a record identity: fail closed,
        never resolved by bucket key alone."""
        _fail(self.cc, "accelerator_as_identity")

    def records(self):
        return list(self.identity_index.values())

    def canonical_view(self):
        """The observable table in CANONICAL-IDENTITY order - never
        bucket-arrival order."""
        return sorted(repr(identity)
                      for identity in self.identity_index)

    @staticmethod
    def _validate_record_shape(cc, rec):
        """Oracle-independent shape validation at the merge
        boundary: BEFORE any field dereference or receiver-oracle
        call, the incoming value must be a PLAIN dict with EXACTLY
        the declared record keys and exact built-in str values.
        A non-dict mapping (hostile accessors), missing/extra keys,
        or non-string / str-subclass fields fail closed as
        malformed_collision_record with ZERO oracle calls."""
        if type(rec) is not dict or \
                set(rec) != _RECORD_FIELDS or \
                any(type(rec[field]) is not str
                    for field in _RECORD_FIELDS):
            _fail(cc, "malformed_collision_record")

    def _validate_record_semantics(self, rec):
        """ORACLE-INDEPENDENT semantic validation (phase one):
        the linked table's own semantic rules - known variant,
        pinned digest format, FEN grammar, canonical snapshot with
        identity EP value and normalized clocks - with digest
        consistency EXCLUDED (the receiver oracle alone owns
        cross-oracle consistency; the real digest is never
        precomputed). Every rejection maps to
        malformed_collision_record with ZERO oracle calls:
        receiver behavior can never launder the failure class of
        a semantically malformed record."""
        nc, vc, dc, epc, fc = _NODE_DOCS
        try:
            _validate_node_record(nc, vc, dc, epc, fc, dict(rec),
                                  lambda v, f, _d=rec["digest"]: _d)
        except NodeError:
            _fail(self.cc, "malformed_collision_record")

    def merge(self, other):
        """ATOMIC: every incoming EXACT stored record is validated
        against the RECEIVER's oracle and linked docs (a stored
        bucket key disagreeing with the receiver's oracle is
        malformed_collision_record - cross-oracle merges fail
        closed, never silently re-bucket), staged into a copy,
        committed only when the whole batch validates."""
        nc, vc, dc, epc, fc = _NODE_DOCS
        staged = CollisionProbe(self.oracle, self.cc)
        staged.buckets = copy.deepcopy(self.buckets)
        staged.identity_index = copy.deepcopy(self.identity_index)
        for rec in other.records():
            # SHAPE FIRST: validate the incoming record's shape
            # BEFORE any field dereference or oracle call - a raw
            # KeyError/AttributeError can never escape the closed
            # failure enum at this boundary.
            self._validate_record_shape(self.cc, rec)
            # FREEZE FIRST: the shape check proved an exact plain
            # dict with exact built-in str values, so this capture
            # cannot execute hostile code. Every later phase -
            # semantic validation, oracle arguments, linked
            # validation, identity derivation, staged insertion -
            # reads ONLY this detached snapshot; the live incoming
            # record is never re-read after untrusted code runs
            # (cross-phase TOCTOU closed structurally).
            frozen = {"variant": rec["variant"],
                      "digest": rec["digest"],
                      "snapshot_fen": rec["snapshot_fen"]}
            # PHASE ONE: oracle-independent semantic validation -
            # a semantically malformed record fails closed HERE
            # and never reaches the receiver oracle.
            self._validate_record_semantics(frozen)
            # SINGLE EVALUATION: the receiver oracle is invoked
            # EXACTLY ONCE per incoming record, at the boundary;
            # the retained exact built-in key validates the source
            # record AND stages the insert - nothing re-queries
            # untrusted input (semantic TOCTOU is closed
            # structurally, not by transaction alone)
            key = self._call_oracle(frozen["variant"],
                                    frozen["snapshot_fen"])
            try:
                _validate_node_record(nc, vc, dc, epc, fc,
                                      dict(frozen),
                                      lambda v, f, _k=key: _k)
            except NodeError:
                _fail(self.cc, "malformed_collision_record")
            staged._staged_insert(frozen, staged._identity(frozen),
                                  key)
        self.buckets = staged.buckets
        self.identity_index = staged.identity_index
        return self


def _probe(oracle=constant_oracle):
    return CollisionProbe(oracle)


def _assert_one_record_per_identity(probe, expected_count):
    """Exactly one record per canonical identity: count exact, no
    duplicate identities, and the sorted view carries no duplicate
    either (a forked table can produce a stable sorted view WITH
    duplicates - uniqueness is asserted, never assumed)."""
    recs = probe.records()
    assert len(recs) == expected_count
    identities = [probe._identity(r) for r in recs]
    assert len(set(identities)) == expected_count
    view = probe.canonical_view()
    assert len(view) == len(set(view)) == expected_count
    return identities


# -- pinned vectors: valid positions with pairwise-distinct identities ------

PROMO_FROM = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
PAWN_E2 = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
COLLIDING_SET = [STARTPOS, AFTER_E4, KINGS, LEGAL_EP, PROMO_FROM,
                 PAWN_E2]
# canonical twins: raw-clock variants (identity-excluded) and the
# phantom-EP twin (identity collapses to the none sentinel)
TWIN_FORMS = {
    STARTPOS: [STARTPOS, STARTPOS.replace(" 0 1", " 7 42")],
    AFTER_E4: [AFTER_E4, AFTER_E4.replace(" e3 ", " - "),
               AFTER_E4.replace(" e3 ", " - ").replace(" 0 1",
                                                       " 3 9")],
    KINGS: [KINGS, KINGS.replace(" 0 1", " 5 12")],
    # halfmove stays 0 with an EP target set; fullmove is also
    # identity-excluded, so the twin moves only the fullmove number
    LEGAL_EP: [LEGAL_EP, LEGAL_EP.replace(" 0 1", " 0 11")],
    PROMO_FROM: [PROMO_FROM, PROMO_FROM.replace(" 0 1", " 9 1")],
    PAWN_E2: [PAWN_E2, PAWN_E2.replace(" 0 1", " 1 30")],
}


def test_lint_clean():
    lint()


def test_happy_collision_free_baseline():
    """With the real digest oracle (no forced collisions) the probe
    behaves exactly as the linked node contract's table."""
    probe = _probe(digest_fen)
    for fen in COLLIDING_SET:
        probe.insert("standard", fen)
    _assert_one_record_per_identity(probe, len(COLLIDING_SET))
    rec = probe.insert("standard", STARTPOS)
    assert rec is probe.insert("standard", STARTPOS)
    _assert_one_record_per_identity(probe, len(COLLIDING_SET))


def test_forced_collision_count_exact_no_merge_no_fork():
    """COUNT-EXACT under total collision: N pairwise-distinct
    identities in ONE bucket yield exactly N records - NO-MERGE and
    NO-FORK, one record per canonical identity."""
    probe = _probe()
    inserted = [probe.insert("standard", fen) for fen in
                COLLIDING_SET]
    assert len(probe.buckets) == 1
    _assert_one_record_per_identity(probe, len(COLLIDING_SET))
    for fen, rec in zip(COLLIDING_SET, inserted, strict=True):
        assert probe.insert("standard", fen) is rec
    _assert_one_record_per_identity(probe, len(COLLIDING_SET))


def test_separation_completeness_under_collision():
    """Inside the single bucket: equal bucket keys never merge
    unequal identities; the phantom-EP twin and clock twins fold
    by FIELD comparison through the identity index."""
    probe = _probe()
    a = probe.insert("standard", AFTER_E4)
    collapsed = AFTER_E4.replace(" e3 ", " - ")
    assert probe.insert("standard", collapsed) is a
    b = probe.insert("standard", STARTPOS)
    assert b is not a
    assert probe.insert("standard",
                        STARTPOS.replace(" 0 1", " 7 42")) is b
    _assert_one_record_per_identity(probe, 2)


def test_collision_transparency():
    """TRANSPARENCY: identical canonical view, identity count and
    record count collision-free vs total collision."""
    free = _probe(digest_fen)
    colliding = _probe()
    for fen in COLLIDING_SET:
        free.insert("standard", fen)
        colliding.insert("standard", fen)
    assert free.canonical_view() == colliding.canonical_view()
    _assert_one_record_per_identity(free, len(COLLIDING_SET))
    _assert_one_record_per_identity(colliding, len(COLLIDING_SET))
    assert len(free.buckets) > 1
    assert len(colliding.buckets) == 1


def test_order_insensitivity_under_collision():
    """Every insertion permutation yields the same canonical table
    with exactly one record per identity - arrival order inside the
    bucket is never observable, and a stable sorted view WITH
    duplicates would be caught by the uniqueness pin."""
    views = set()
    for perm in itertools.permutations(COLLIDING_SET):
        probe = _probe()
        for fen in perm:
            probe.insert("standard", fen)
        _assert_one_record_per_identity(probe, len(COLLIDING_SET))
        views.add(tuple(probe.canonical_view()))
    assert len(views) == 1


def test_adversarial_twin_permutations():
    """Large adversarial battery: all 720 permutations of the base
    set, each position inserted in EVERY canonical-twin form
    (raw-clock variants, the phantom-EP twin) - exactly one record
    per canonical identity in every permutation."""
    expected_view = None
    for index, perm in enumerate(
            itertools.permutations(COLLIDING_SET)):
        probe = _probe()
        for position in perm:
            twins = TWIN_FORMS[position]
            # rotate which twin form leads, then insert the rest
            forms = twins[index % len(twins):] + \
                twins[:index % len(twins)]
            for fen in forms:
                probe.insert("standard", fen)
        _assert_one_record_per_identity(probe, len(COLLIDING_SET))
        view = tuple(probe.canonical_view())
        if expected_view is None:
            expected_view = view
        assert view == expected_view


def test_merge_algebra_under_collision():
    """MERGE ALGEBRA PRESERVED: idempotent, commutative, associative
    even when every record collides into one bucket."""
    groups = [COLLIDING_SET[:2], COLLIDING_SET[2:4],
              COLLIDING_SET[4:]]

    def built(fens):
        probe = _probe()
        for fen in fens:
            probe.insert("standard", fen)
        return probe

    full = built(COLLIDING_SET)
    full.insert("standard", COLLIDING_SET[0])
    results = set()
    for order in itertools.permutations(groups):
        probe = _probe()
        for group in order:
            probe.merge(built(group))
        _assert_one_record_per_identity(probe, len(COLLIDING_SET))
        results.add(tuple(probe.canonical_view()))
    assert len(results) == 1
    assert results.pop() == tuple(full.canonical_view())


def test_accelerator_as_identity_rejected():
    probe = _probe()
    probe.insert("standard", STARTPOS)
    before = probe.canonical_view()
    before_index = copy.deepcopy(probe.identity_index)
    with pytest.raises(CollisionError) as exc:
        probe.lookup_by_bucket_key("pdv1:" + "0" * 64)
    assert exc.value.failure_class == "accelerator_as_identity"
    assert exc.value.code == FAILURE_MAPPING[
        "accelerator_as_identity"]
    assert exc.value.code in ERROR_ENUM
    assert probe.canonical_view() == before
    assert probe.identity_index == before_index


# -- trust-boundary repros (verifier #2 remediation) -------------------------


def test_repro_oracle_hashing_raw_clocks_rejected():
    """REPRO 1: an oracle hashing the raw FEN (including the
    identity-excluded clocks) hands equal canonical identities
    different bucket keys - the trust boundary catches it:
    accelerator_inconsistent, no fork, exactly one record."""
    probe = _probe(raw_oracle)
    probe.insert("standard", STARTPOS)
    before = probe.canonical_view()
    before_index = copy.deepcopy(probe.identity_index)
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS.replace(" 0 1", " 7 42"))
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert exc.value.code == FAILURE_MAPPING[
        "accelerator_inconsistent"]
    assert exc.value.code in ERROR_ENUM
    assert probe.canonical_view() == before
    assert probe.identity_index == before_index
    _assert_one_record_per_identity(probe, 1)


def test_repro_oracle_hashing_phantom_ep_rejected():
    """REPRO 2: the same raw oracle hands the uncapturable-EP twin
    (raw 'e3' vs canonical '-') a different bucket key for an equal
    canonical identity - caught, rejected, no fork."""
    probe = _probe(raw_oracle)
    probe.insert("standard", AFTER_E4)
    before_index = copy.deepcopy(probe.identity_index)
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", AFTER_E4.replace(" e3 ", " - "))
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert probe.identity_index == before_index
    _assert_one_record_per_identity(probe, 1)


def test_repro_stateful_oracle_rejected():
    """REPRO 3: a stateful oracle returning fresh valid-format
    digests hands byte-identical STARTPOS a new key - caught,
    rejected, the equal identity is never stored twice."""
    probe = _probe(stateful_oracle())
    probe.insert("standard", STARTPOS)
    before_index = copy.deepcopy(probe.identity_index)
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert probe.identity_index == before_index
    _assert_one_record_per_identity(probe, 1)


def test_invalid_format_bucket_key_rejected():
    """The oracle owes a VALID-FORMAT bucket key per the linked
    digest contract - anything else is accelerator_inconsistent."""
    probe = _probe(lambda variant, fen: "not-a-digest")
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert probe.records() == []
    assert probe.buckets == {}
    assert probe.identity_index == {}


def test_repro_reverse_insertion_order():
    """Reverse order with the raw-clocking oracle: the FIRST twin
    form wins the insert and the SECOND form is the rejected
    divergence - direction changes, the outcome class does not."""
    probe = _probe(raw_oracle)
    probe.insert("standard", STARTPOS.replace(" 0 1", " 7 42"))
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS)
    assert exc.value.failure_class == "accelerator_inconsistent"
    _assert_one_record_per_identity(probe, 1)
    # the stored record is the first form's, canonically equal
    rec = probe.records()[0]
    assert rec["snapshot_fen"] == STARTPOS  # canonical storage


def test_merge_across_inconsistent_oracles():
    """Cross-oracle merge: an incoming record whose stored bucket
    key disagrees with the RECEIVER's oracle fails closed as
    malformed_collision_record - BOTH merge orders reject, no
    re-bucketing, destinations bit-identical."""
    a = _probe(digest_fen)
    a.insert("standard", STARTPOS)
    b = _probe()
    b.insert("standard", STARTPOS)  # same canonical identity
    a_index = copy.deepcopy(a.identity_index)
    b_index = copy.deepcopy(b.identity_index)
    with pytest.raises(CollisionError) as exc:
        a.merge(b)
    assert exc.value.failure_class == "malformed_collision_record"
    with pytest.raises(CollisionError) as exc:
        b.merge(a)
    assert exc.value.failure_class == "malformed_collision_record"
    assert a.identity_index == a_index
    assert b.identity_index == b_index
    # a merge across CONSISTENT oracles still succeeds
    c = _probe()
    c.insert("standard", KINGS)
    d = _probe()
    d.insert("standard", STARTPOS)
    d.merge(c)
    _assert_one_record_per_identity(d, 2)


def test_mutant_bucket_only_trust_forks():
    """Behavioral mutant: the v1 shape (bucket-scoped search, no
    identity index, no consistency validation) FORKS one canonical
    identity under the raw-clocking oracle - pinned here so the
    battery proves the trust boundary is load-bearing.
    Counter-test: the same inserts through the real probe are
    caught and never fork."""
    def mutant_insert(probe, variant_id, fen_text):
        nc, vc, dc, epc, fc = _NODE_DOCS
        rec = _make_record(nc, vc, dc, epc, fc, probe.oracle,
                           variant_id, fen_text)
        bucket = probe.buckets.setdefault(rec["digest"], [])
        identity = probe._identity(rec)
        for existing in bucket:
            if probe._identity(existing) == identity:
                return existing
        bucket.append(rec)
        return rec

    mutant_probe = _probe(raw_oracle)
    mutant_insert(mutant_probe, "standard", STARTPOS)
    mutant_insert(mutant_probe, "standard",
                  STARTPOS.replace(" 0 1", " 7 42"))
    forked = [r for bucket in mutant_probe.buckets.values()
              for r in bucket]
    assert len(forked) == 2  # the mutant forks the one identity
    nc, vc, dc, epc, fc = _NODE_DOCS
    assert len({_record_identity(nc, vc, dc, epc, fc, r)
                for r in forked}) == 1  # ...of ONE identity
    # counter-test: the real probe rejects and never forks
    real = _probe(raw_oracle)
    real.insert("standard", STARTPOS)
    with pytest.raises(CollisionError):
        real.insert("standard", STARTPOS.replace(" 0 1", " 7 42"))
    _assert_one_record_per_identity(real, 1)


def test_repro_raising_oracle_first_call():
    """An oracle that raises on the FIRST call is the same
    inability to supply a key as a bad return: typed
    accelerator_inconsistent, never a raw escape; table
    bit-identical (empty)."""
    probe = _probe(raising_oracle(1))
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert exc.value.code == FAILURE_MAPPING[
        "accelerator_inconsistent"]
    assert exc.value.code in ERROR_ENUM
    assert probe.records() == []
    assert probe.buckets == {}
    assert probe.identity_index == {}


def test_repro_raising_oracle_on_equal_identity():
    """First call succeeds (STARTPOS stored); the oracle raises on
    the SECOND call for an equal canonical identity (clock twin)
    - typed rejection, one record, bit-identical rollback."""
    probe = _probe(raising_oracle(2))
    probe.insert("standard", STARTPOS)
    before_index = copy.deepcopy(probe.identity_index)
    before_buckets = copy.deepcopy(probe.buckets)
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS.replace(" 0 1", " 7 42"))
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert probe.identity_index == before_index
    assert probe.buckets == before_buckets
    _assert_one_record_per_identity(probe, 1)


def test_repro_oracle_raises_during_merge_revalidation():
    """The RECEIVER's oracle raises while revalidating an incoming
    exact record: typed accelerator_inconsistent, destination
    bit-identical."""
    def fen_sensitive(variant, fen):
        if fen == KINGS:
            raise ValueError("oracle refuses this position")
        return digest_fen(variant, fen)

    source = _probe(digest_fen)
    source.insert("standard", KINGS)
    dest = _probe(fen_sensitive)
    dest.insert("standard", STARTPOS)
    before_index = copy.deepcopy(dest.identity_index)
    before_buckets = copy.deepcopy(dest.buckets)
    with pytest.raises(CollisionError) as exc:
        dest.merge(source)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert dest.identity_index == before_index
    assert dest.buckets == before_buckets


def test_repro_oracle_raises_midway_through_merge():
    """Multi-record merge: the first incoming record validates AND
    stages cleanly, the oracle raises on the SECOND record's
    revalidation - the atomic merge commits nothing; destination
    bit-identical."""
    source = _probe(digest_fen)
    source.insert("standard", STARTPOS)
    source.insert("standard", KINGS)
    dest = _probe(midway_oracle(2))  # record 1 ok, record 2 boom
    dest_index = copy.deepcopy(dest.identity_index)
    dest_buckets = copy.deepcopy(dest.buckets)
    with pytest.raises(CollisionError) as exc:
        dest.merge(source)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert dest.identity_index == dest_index
    assert dest.buckets == dest_buckets
    assert dest.records() == []


K1 = "pdv1:" + "1" * 64
K2 = "pdv1:" + "2" * 64


def _sequence_oracle(keys):
    """UNTRUSTED: returns each pinned key per call in order -
    two inconsistent SUCCESSFUL outputs inside one merge."""
    counter = itertools.count(0)

    def oracle(variant, fen):
        return keys[min(next(counter), len(keys) - 1)]
    return oracle


def test_repro_k1_then_k2_merge_single_evaluation():
    """K1-then-K2 repro: the source stores STARTPOS under K1; the
    receiver oracle would answer K1 then K2. SINGLE EVALUATION
    neutralizes the TOCTOU: exactly ONE call per incoming record,
    the retained K1 validates AND stages - the exact source record
    is never silently re-bucketed to K2."""
    source = _probe(_sequence_oracle([K1]))
    source.insert("standard", STARTPOS)
    counter = {"n": 0}

    def count_oracle(variant, fen):
        counter["n"] += 1
        return K1 if counter["n"] == 1 else K2

    dest = _probe(count_oracle)
    dest.merge(source)
    assert counter["n"] == 1  # ONE call per incoming record
    assert dest.records() == source.records()  # exact record
    assert list(dest.buckets) == [K1]  # retained key, never K2
    _assert_one_record_per_identity(dest, 1)


def test_repro_k2_then_k1_reverse_rejected():
    """Reverse: the source stored STARTPOS under K1 but the
    receiver's single evaluation answers K2 - source key !=
    retained key rejects as malformed_collision_record
    (cross-oracle disagreement), destination bit-identical."""
    source = _probe(_sequence_oracle([K1]))
    source.insert("standard", STARTPOS)
    dest = _probe(_sequence_oracle([K2, K1]))
    dest_index = copy.deepcopy(dest.identity_index)
    with pytest.raises(CollisionError) as exc:
        dest.merge(source)
    assert exc.value.failure_class == "malformed_collision_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_collision_record"]
    assert dest.identity_index == dest_index
    assert dest.buckets == {}
    assert dest.records() == []


def test_repro_divergence_after_valid_staged_prefix():
    """Three incoming records: the first two validate and stage
    (source keys match the receiver's single answers), the THIRD
    source key disagrees with the receiver's answer - atomic merge
    commits nothing, destination bit-identical."""
    source = _probe(digest_fen)
    source.insert("standard", STARTPOS)
    source.insert("standard", KINGS)
    source.insert("standard", AFTER_E4)
    # answers keyed by the CANONICAL stored snapshots; the third
    # record's answer diverges from its stored key
    answers = {r["snapshot_fen"]: r["digest"]
               for r in source.records()}
    answers[source.records()[2]["snapshot_fen"]] = K1

    def oracle(variant, fen):
        return answers[fen]

    dest = _probe(oracle)
    dest_index = copy.deepcopy(dest.identity_index)
    dest_buckets = copy.deepcopy(dest.buckets)
    with pytest.raises(CollisionError) as exc:
        dest.merge(source)
    assert exc.value.failure_class == "malformed_collision_record"
    assert dest.identity_index == dest_index
    assert dest.buckets == dest_buckets
    assert dest.records() == []  # valid prefix NOT committed


def test_repro_equal_canonical_twin_divergence_on_merge():
    """Equal canonical twins under different keys: the destination
    already holds STARTPOS under K2; the incoming STARTPOS is
    stored under K1 and the receiver's single answer is K1 - the
    staged insert meets an equal identity with a divergent key:
    accelerator_inconsistent, destination bit-identical."""
    source = _probe(_sequence_oracle([K1]))
    source.insert("standard", STARTPOS)
    dest = _probe(_sequence_oracle([K2, K1]))
    dest.insert("standard", STARTPOS)  # consumes K2
    before_index = copy.deepcopy(dest.identity_index)
    before_buckets = copy.deepcopy(dest.buckets)
    with pytest.raises(CollisionError) as exc:
        dest.merge(source)  # single answer K1 != stored K2
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert exc.value.code == FAILURE_MAPPING[
        "accelerator_inconsistent"]
    assert dest.identity_index == before_index
    assert dest.buckets == before_buckets
    _assert_one_record_per_identity(dest, 1)


def test_call_count_one_oracle_call_per_incoming_record():
    """The single-evaluation principle, counted: merging N records
    invokes the receiver oracle EXACTLY N times."""
    source = _probe(digest_fen)
    for fen in [STARTPOS, KINGS, AFTER_E4]:
        source.insert("standard", fen)
    counter = {"n": 0}

    def counting(variant, fen):
        counter["n"] += 1
        return digest_fen(variant, fen)

    dest = _probe(counting)
    dest.merge(source)
    assert counter["n"] == 3
    _assert_one_record_per_identity(dest, 3)


def test_mutant_validate_then_requery_rebuckets():
    """Behavioral mutant: the v4 merge shape validates with one
    call then RE-QUERIES for staging - under K1-then-K2 the exact
    record is silently re-bucketed to K2. Pinned to prove single
    evaluation is load-bearing. Counter-test: the real merge
    retains K1 and never re-queries."""
    def mutant_merge(dest, other):
        nc, vc, dc, epc, fc = _NODE_DOCS
        for rec in other.records():
            _validate_node_record(nc, vc, dc, epc, fc, dict(rec),
                                  dest._call_oracle)   # call 1
            dest.insert(rec["variant"], rec["snapshot_fen"])
        # insert re-queries the oracle (call 2)
        return dest

    source = _probe(_sequence_oracle([K1]))
    source.insert("standard", STARTPOS)
    counter = {"n": 0}

    def oracle(variant, fen):
        counter["n"] += 1
        return K1 if counter["n"] == 1 else K2

    mutant_dest = _probe(oracle)
    mutant_merge(mutant_dest, source)
    assert counter["n"] == 2  # the mutant queried twice
    assert list(mutant_dest.buckets) == [K2]  # silent re-bucket
    # counter-test
    counter["n"] = 0
    real_dest = _probe(oracle)
    real_dest.merge(source)
    assert counter["n"] == 1
    assert list(real_dest.buckets) == [K1]


def test_mutant_direct_oracle_call_leaks():
    """Behavioral mutant: invoking the oracle DIRECTLY (no
    boundary helper) leaks the raw ValueError - pinned so the
    battery proves the boundary is load-bearing. Counter-test:
    the same failure through the real probe maps to typed
    accelerator_inconsistent."""
    probe = _probe(raising_oracle(1))
    with pytest.raises(ValueError):
        probe.oracle("standard", STARTPOS)  # mutant: no boundary
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS)  # real boundary
    assert exc.value.failure_class == "accelerator_inconsistent"


@pytest.mark.parametrize("cls", HOSTILE_SUBCLASSES)
def test_repro_hostile_str_subclass_first_insert(cls):
    """A valid-text str subclass (raising __hash__, raising __eq__,
    deceptive eq/hash) must not pass the boundary: exact built-in
    str required, typed accelerator_inconsistent, table
    bit-identical (empty)."""
    probe = _probe(subclass_oracle(cls))
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", STARTPOS)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert exc.value.code == FAILURE_MAPPING[
        "accelerator_inconsistent"]
    assert probe.records() == []
    assert probe.buckets == {}
    assert probe.identity_index == {}


@pytest.mark.parametrize("cls", HOSTILE_SUBCLASSES)
def test_repro_hostile_str_subclass_second_insert(cls):
    """First call returns a plain str (STARTPOS stored); the second
    call turns hostile - typed rejection, one record, index and
    buckets bit-identical."""
    probe = _probe(escalating_subclass_oracle(cls))
    probe.insert("standard", STARTPOS)
    before_index = copy.deepcopy(probe.identity_index)
    before_buckets = copy.deepcopy(probe.buckets)
    with pytest.raises(CollisionError) as exc:
        probe.insert("standard", KINGS)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert probe.identity_index == before_index
    assert probe.buckets == before_buckets
    _assert_one_record_per_identity(probe, 1)


@pytest.mark.parametrize("cls", HOSTILE_SUBCLASSES)
def test_repro_hostile_str_subclass_midway_merge(cls):
    """Merge: the first incoming record validates AND stages with
    plain keys; the second record's revalidation turns hostile -
    atomic merge commits nothing, destination bit-identical."""
    counter = itertools.count(1)

    def oracle(variant, fen):
        # single evaluation: record 1's only call is plain,
        # record 2's only call turns hostile
        if next(counter) <= 1:
            return digest_fen(variant, fen)
        return cls("pdv1:" + "0" * 64)

    source = _probe(digest_fen)
    source.insert("standard", STARTPOS)
    source.insert("standard", KINGS)
    dest = _probe(oracle)
    with pytest.raises(CollisionError) as exc:
        dest.merge(source)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert dest.records() == []
    assert dest.buckets == {}
    assert dest.identity_index == {}


def test_mutant_isinstance_acceptance_index_first_leaks():
    """Behavioral mutant: the v3 shape (mere isinstance acceptance
    + index mutated BEFORE the bucket write) lets a hostile
    subclass through the boundary and explodes MID-WRITE: index
    holds the record, buckets do not - rollback false, surface
    broken. Pinned to prove the v4 exact-str boundary and
    transactional insert are load-bearing. Counter-test: the real
    probe rejects typed with bit-identical state."""
    def mutant_insert(probe, variant_id, fen_text):
        nc, vc, dc, epc, fc = _NODE_DOCS
        key = probe.oracle(variant_id, fen_text)
        if not isinstance(key, str) or \
                _DIGEST_RE.fullmatch(key) is None:
            _fail(probe.cc, "accelerator_inconsistent")
        rec = _make_record(nc, vc, dc, epc, fc,
                           lambda v, f: key, variant_id, fen_text)
        identity = probe._identity(rec)
        probe.identity_index[identity] = rec  # index FIRST (v3)
        probe.buckets.setdefault(
            rec["digest"], []).append(rec)    # hostile hash boom
        return rec

    probe = _probe(subclass_oracle(_HostileHashStr))
    with pytest.raises(ValueError):
        mutant_insert(probe, "standard", STARTPOS)
    # the v3 shape left the table forked: written index, empty
    # buckets
    assert len(probe.identity_index) == 1
    assert len(probe.buckets) == 0
    # counter-test: the real probe rejects typed, nothing written
    real = _probe(subclass_oracle(_HostileHashStr))
    with pytest.raises(CollisionError) as exc:
        real.insert("standard", STARTPOS)
    assert exc.value.failure_class == "accelerator_inconsistent"
    assert real.identity_index == {}
    assert real.buckets == {}


MALFORMED_INSERTS = [
    ("c960", STARTPOS),                       # unknown variant
    ("standard", "garbage w - - 0 1"),        # malformed FEN
    ("standard", "8/8/8/8/8/8/8/4K3 w - - 0 1"),  # one king
    ("standard", AFTER_E4.replace(" 0 1", " 3 9").replace(
        " e3 ", " - ")[:-2] + "x"),           # grammar drift
]


@pytest.mark.parametrize("variant,fen", MALFORMED_INSERTS)
def test_malformed_collision_record_rejected(variant, fen):
    probe = _probe()
    with pytest.raises(CollisionError) as exc:
        probe.insert(variant, fen)
    assert exc.value.failure_class == "malformed_collision_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_collision_record"]
    assert exc.value.code in ERROR_ENUM


def test_rollback_bit_identical_under_collision():
    probe = _probe()
    probe.insert("standard", STARTPOS)
    probe.insert("standard", KINGS)
    before = probe.canonical_view()
    before_index = copy.deepcopy(probe.identity_index)
    before_buckets = copy.deepcopy(probe.buckets)
    for variant, fen in MALFORMED_INSERTS:
        with pytest.raises(CollisionError):
            probe.insert(variant, fen)
    with pytest.raises(CollisionError):
        probe.lookup_by_bucket_key("pdv1:" + "1" * 64)
    raw = _probe(raw_oracle)
    raw.insert("standard", STARTPOS)
    raw_index = copy.deepcopy(raw.identity_index)
    with pytest.raises(CollisionError):
        raw.insert("standard", STARTPOS.replace(" 0 1", " 7 42"))
    assert probe.canonical_view() == before
    assert probe.identity_index == before_index
    assert probe.buckets == before_buckets
    assert raw.identity_index == raw_index


# -- mutation battery --------------------------------------------------------


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
        "bucket-replacement-semantics")
    add("collision as error", ["contract", "role", "collision_is"],
        "rejected-as-conflict")
    add("scope drift", ["contract", "role", "scope"],
        "node-table-only")
    add("owns drift", ["contract", "role", "owns"],
        "identity-semantics")
    add("definition drift", ["contract", "definition", "collision"],
        "bucket-key-equal-implies-record-equal")
    add("bucket key stored", ["contract", "definition",
                              "bucket_key"],
        "stored-as-identity-field")
    add("identity restated", ["contract", "definition", "identity"],
        "restated-here")
    add("separation drift", ["contract", "separation", "rule_kind"],
        "first-in-bucket-wins")
    add("bucket role drift", ["contract", "separation",
                              "bucket_role"], "bucket-decides")
    add("digest equality decides", ["contract", "separation",
                                    "digest_equality"],
        "decides-record-equality")
    add("digest inequality decides", ["contract", "separation",
                                      "digest_inequality"],
        "decides-record-inequality")
    add("trust boundary dropped", ["contract", "separation",
                                   "trust_boundary"],
        "oracle-always-trusted")
    add("enforcement dropped", ["contract", "separation",
                                "enforcement"],
        "bucket-search-only")
    add("oracle output drift", ["contract", "separation",
                                "oracle_output"], "any-string")
    add("no_merge dropped", ["contract", "guarantees", "no_merge"],
        "first-writer-wins")
    add("no_fork dropped", ["contract", "guarantees", "no_fork"],
        "split-allowed")
    add("count drift", ["contract", "guarantees", "count_exact"],
        "at-most-n-records")
    add("arrival order", ["contract", "guarantees",
                          "order_insensitivity"],
        "bucket-arrival-order")
    add("algebra dropped", ["contract", "guarantees",
                            "merge_algebra_preserved"],
        "commutativity-lost-under-collision")
    add("guarantee trust dropped", ["contract", "guarantees",
                                    "trust_boundary"],
        "misbucketed-twins-may-fork")
    add("failure class dropped", ["contract", "failures", "classes"],
        ["malformed_collision_record", "accelerator_as_identity"])
    add("inconsistency mapped away", ["contract", "failures",
                                      "mapping",
                                      "accelerator_inconsistent"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"], False)
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "accelerator_as_identity",
         "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "accelerator_inconsistent"])
    add("transparency drift", ["contract", "properties",
                               "collision_transparency"],
        "collision-visible")
    add("rollback drift", ["contract", "properties", "rollback"],
        "best-effort")
    add("property trust dropped", ["contract", "properties",
                                   "trust_boundary"],
        "oracle-divergence-tolerated")
    add("oracle boundary dropped", ["contract", "properties",
                                    "oracle_boundary"],
        "oracle-called-directly-anywhere")
    add("transactional insert dropped", ["contract", "properties",
                                         "transactional_insert"],
        "index-mutated-before-bucket-write")
    add("non-exact strings accepted", ["contract", "separation",
                                       "oracle_output"],
        "any-str-subclass-accepted")
    add("trigger drops non-exact", ["contract", "failures",
                                    "triggers",
                                    "accelerator_inconsistent"],
        "oracle-raised-or-invalid-format-key")
    add("inconsistency trigger drift", ["contract", "failures",
                                        "triggers",
                                        "accelerator_inconsistent"],
        "oracle-always-trusted")
    add("trigger set incomplete", ["contract", "failures",
                                   "triggers"],
        {"malformed_collision_record":
         "record-fails-linked-table-shape-or-receiver-oracle-"
         "revalidation"})
    add("link drift", ["contract", "links", "variant_contract"],
        "data/contracts/san.yaml")
    add("base path drift", ["contract", "versioning", "base_path"],
        "/graph/collision/v0")
    add("shape_first dropped", ["contract", "properties",
                                "shape_first"],
        "oracle-called-before-shape-validation")
    add("semantic_first dropped", ["contract", "properties",
                                   "semantic_first"],
        "semantics-checked-after-oracle")
    add("frozen_snapshot dropped", ["contract", "properties",
                                    "frozen_snapshot"],
        "live-record-re-read-after-oracle")
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
    assert covered >= {"role", "definition", "separation",
                       "guarantees", "failures", "errors",
                       "properties", "links", "versioning"}


# -- linkage battery ---------------------------------------------------------


def _lint_doc(tmp_path, name, mutate, target="variant"):
    docs = {"variant": VARIANT, "digest": DIGEST, "node": NODE}
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
         digest_path=paths["digest"], node_path=paths["node"])


def test_linkage_clean_copy_passes(tmp_path):
    paths = _lint_doc(tmp_path, "clean", lambda d: None)
    _lint_with(paths)


def test_linkage_variant_hash_rule_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["hash_rule"] = (
            "The hash decides identity.")
    paths = _lint_doc(tmp_path, "v", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_digest_format_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["digest"]["format"]["regex"] = (
            "^pdv2:[0-9a-f]{64}$")
    paths = _lint_doc(tmp_path, "d", drift, target="digest")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_digest_role_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["role"]["kind"] = "identity"
    paths = _lint_doc(tmp_path, "r", drift, target="digest")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_node_equality_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["equality"] = (
            "digest-comparison")
    paths = _lint_doc(tmp_path, "n", drift, target="node")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_node_exclusions_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["excluded"] = ["halfmove_clock"]
    paths = _lint_doc(tmp_path, "x", drift, target="node")
    with pytest.raises(ContractError):
        _lint_with(paths)


# -- shape-first boundary battery (v6) ---------------------------------------


def _raw_source(records):
    """A merge source serving RAW (possibly hostile) records - the
    merge boundary must never assume probe-built input."""
    class _Source:
        def records(self):
            return list(records)
    return _Source()


def _counting_oracle():
    calls = {"n": 0}

    def oracle(variant, fen):
        calls["n"] += 1
        return constant_oracle(variant, fen)
    return oracle, calls


def _bad_shape_records():
    nc, vc, dc, epc, fc = _NODE_DOCS
    good = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", STARTPOS)
    bads = []
    for field in sorted(_RECORD_FIELDS):
        missing = {k: v for k, v in good.items() if k != field}
        bads.append((f"missing-{field}", missing))
        for value in (None, [], 5):
            wrong = dict(good)
            wrong[field] = value
            bads.append((f"{field}-type-{type(value).__name__}",
                         wrong))
    extra = dict(good)
    extra["label"] = "x"
    bads.append(("extra-field", extra))
    for value in (None, [], "text", 5):
        bads.append((f"nonmapping-{type(value).__name__}", value))

    class HostileMap(dict):
        def __getitem__(self, key):
            raise RuntimeError("hostile access")
    bads.append(("hostile-accessor", HostileMap(good)))

    class EvilStr(str):
        def __hash__(self):
            raise RuntimeError("hostile hash")
    evil = dict(good)
    evil["digest"] = EvilStr(good["digest"])
    bads.append(("evil-str-subclass-value", evil))
    return bads


BAD_SHAPES = _bad_shape_records()


@pytest.mark.parametrize("bad", [b for _, b in BAD_SHAPES],
                         ids=[n for n, _ in BAD_SHAPES])
def test_merge_shape_validated_before_boundary(bad):
    """A malformed incoming merge record fails closed BEFORE any
    field dereference or receiver-oracle call: typed
    malformed_collision_record, ZERO oracle calls for the bad
    record, and an atomic rollback leaving the destination
    bit-identical - even after a valid staged prefix."""
    nc, vc, dc, epc, fc = _NODE_DOCS
    oracle, calls = _counting_oracle()
    good = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", STARTPOS)
    dest = _probe(oracle)
    before = (copy.deepcopy(dest.buckets),
              copy.deepcopy(dest.identity_index))
    with pytest.raises(CollisionError) as exc:
        dest.merge(_raw_source([good, bad]))
    assert exc.value.failure_class == "malformed_collision_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_collision_record"]
    assert exc.value.code in ERROR_ENUM
    # exactly ONE oracle call: the valid staged prefix; ZERO for
    # the shape-hostile record
    assert calls["n"] == 1
    assert (dest.buckets, dest.identity_index) == before


def test_mutant_dereference_before_shape_validation():
    """Behavioral mutant: the v5 merge dereferenced
    rec["variant"]/rec["snapshot_fen"] and called the receiver
    oracle BEFORE shape validation - a missing key escaped as a
    raw KeyError OUTSIDE the closed enum. Pinned to prove the
    shape-first boundary is load-bearing. Counter-test: the real
    merge maps it to typed malformed_collision_record with ZERO
    oracle calls."""
    def mutant_merge(dest, other):
        for rec in other.records():
            key = dest._call_oracle(rec["variant"],
                                    rec["snapshot_fen"])
            dest._staged_insert(rec, dest._identity(rec), key)
        return dest

    nc, vc, dc, epc, fc = _NODE_DOCS
    good = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", STARTPOS)
    bad = {k: v for k, v in good.items() if k != "snapshot_fen"}
    with pytest.raises(KeyError):
        mutant_merge(_probe(constant_oracle), _raw_source([bad]))
    oracle, calls = _counting_oracle()
    real = _probe(oracle)
    before = (copy.deepcopy(real.buckets),
              copy.deepcopy(real.identity_index))
    with pytest.raises(CollisionError) as exc:
        real.merge(_raw_source([bad]))
    assert exc.value.failure_class == "malformed_collision_record"
    assert calls["n"] == 0
    assert (real.buckets, real.identity_index) == before


# -- v7: oracle-independent semantic phase one -------------------------------


def _semantic_bad_records():
    """Well-typed, exact-string records that are SEMANTICALLY
    malformed under the linked table's own rules."""
    nc, vc, dc, epc, fc = _NODE_DOCS
    good = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", STARTPOS)
    return [
        ("unknown-variant", dict(good, variant="c960")),
        ("bad-fen", dict(good, snapshot_fen="garbage w - - 0 1")),
        ("noncanonical-clocks",
         dict(good, snapshot_fen=STARTPOS.replace(" 0 1", " 7 42"))),
        ("nonidentity-ep",
         dict(good, snapshot_fen=STARTPOS.replace(" - 0 1",
                                                  " e3 0 1"))),
        ("malformed-digest", dict(good, digest="bad")),
    ]


SEMANTIC_BAD = _semantic_bad_records()


def _side_effect_oracle(log):
    def oracle(variant, fen):
        log.append((variant, fen))
        return constant_oracle(variant, fen)
    return oracle


@pytest.mark.parametrize("name,bad", SEMANTIC_BAD,
                         ids=[n for n, _ in SEMANTIC_BAD])
def test_semantic_phase_one_raising_oracle(name, bad):
    """Against a RAISING receiver oracle a semantically malformed
    record still fails as malformed_collision_record - the
    oracle's behavior never launders the failure class."""
    dest = _probe(raising_oracle(1))
    before = (copy.deepcopy(dest.buckets),
              copy.deepcopy(dest.identity_index))
    with pytest.raises(CollisionError) as exc:
        dest.merge(_raw_source([bad]))
    assert exc.value.failure_class == "malformed_collision_record"
    assert exc.value.code == FAILURE_MAPPING[
        "malformed_collision_record"]
    assert exc.value.code in ERROR_ENUM
    assert (dest.buckets, dest.identity_index) == before


@pytest.mark.parametrize("name,bad", SEMANTIC_BAD,
                         ids=[n for n, _ in SEMANTIC_BAD])
def test_semantic_phase_one_counting_oracle(name, bad):
    """Against a counting receiver oracle a semantically malformed
    record consumes ZERO oracle calls; a valid staged prefix
    before it still rolls back bit-identical."""
    nc, vc, dc, epc, fc = _NODE_DOCS
    oracle, calls = _counting_oracle()
    good = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", AFTER_E4)
    dest = _probe(oracle)
    before = (copy.deepcopy(dest.buckets),
              copy.deepcopy(dest.identity_index))
    with pytest.raises(CollisionError) as exc:
        dest.merge(_raw_source([good, bad]))
    assert exc.value.failure_class == "malformed_collision_record"
    assert calls["n"] == 1  # the valid prefix only
    assert (dest.buckets, dest.identity_index) == before


@pytest.mark.parametrize("name,bad", SEMANTIC_BAD,
                         ids=[n for n, _ in SEMANTIC_BAD])
def test_semantic_phase_one_side_effect_oracle(name, bad):
    """Against a side-effecting receiver oracle a semantically
    malformed record triggers NO side effect - the oracle is
    never invoked for it."""
    nc, vc, dc, epc, fc = _NODE_DOCS
    log = []
    oracle = _side_effect_oracle(log)
    good = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", AFTER_E4)
    dest = _probe(oracle)
    before = (copy.deepcopy(dest.buckets),
              copy.deepcopy(dest.identity_index))
    with pytest.raises(CollisionError) as exc:
        dest.merge(_raw_source([good, bad]))
    assert exc.value.failure_class == "malformed_collision_record"
    assert len(log) == 1  # the valid prefix only
    assert (dest.buckets, dest.identity_index) == before


def test_mutant_semantics_after_oracle_launders_failure_class():
    """Behavioral mutant: shallow shape-first with semantic
    validation moved AFTER the oracle - a malformed FEN against a
    raising oracle is laundered into accelerator_inconsistent.
    Pinned to prove phase one is load-bearing. Counter-test: the
    real merge rejects malformed_collision_record with ZERO
    calls."""
    def mutant_merge(dest, other):
        for rec in other.records():
            dest._validate_record_shape(dest.cc, rec)
            key = dest._call_oracle(rec["variant"],
                                    rec["snapshot_fen"])
            dest._validate_record_semantics(rec)  # too late
            dest._staged_insert(rec, dest._identity(rec), key)
        return dest

    nc, vc, dc, epc, fc = _NODE_DOCS
    good = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", STARTPOS)
    bad = dict(good, snapshot_fen="garbage w - - 0 1")
    with pytest.raises(CollisionError) as exc:
        mutant_merge(_probe(raising_oracle(1)), _raw_source([bad]))
    assert exc.value.failure_class == "accelerator_inconsistent"
    oracle, calls = _counting_oracle()
    real = _probe(oracle)
    before = (copy.deepcopy(real.buckets),
              copy.deepcopy(real.identity_index))
    with pytest.raises(CollisionError) as exc:
        real.merge(_raw_source([bad]))
    assert exc.value.failure_class == "malformed_collision_record"
    assert calls["n"] == 0
    assert (real.buckets, real.identity_index) == before


# -- v8: frozen snapshot across the oracle boundary --------------------------


def _mutating_oracle(source_ref, field, mutation, calls):
    """UNTRUSTED: mutates the LIVE source record during its call,
    then returns the valid constant key."""
    def oracle(variant, fen):
        calls["n"] += 1
        rec = source_ref[0]
        if mutation == "delete":
            del rec[field]
        elif mutation == "hostile":
            rec[field] = []
        elif mutation == "valid-substitute":
            rec[field] = {"variant": "standard",
                          "snapshot_fen": AFTER_E4,
                          "digest": rec["digest"]}[field]
        return constant_oracle(variant, fen)
    return oracle


@pytest.mark.parametrize("field", ["variant", "digest",
                                   "snapshot_fen"])
@pytest.mark.parametrize("mutation", ["delete", "hostile",
                                      "valid-substitute"])
def test_frozen_snapshot_survives_mutating_oracle(field, mutation):
    """An oracle mutating/deleting/replacing the LIVE source record
    during its call cannot affect the merge: the PRE-CALL frozen
    record is what validates and inserts, the destination is
    bit-identical to a clean merge, and no raw exception
    escapes."""
    nc, vc, dc, epc, fc = _NODE_DOCS
    original = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                            "standard", STARTPOS)
    source_ref = [dict(original)]
    calls = {"n": 0}
    oracle = _mutating_oracle(source_ref, field, mutation, calls)
    dest = _probe(oracle)
    dest.merge(_raw_source(source_ref))
    assert calls["n"] == 1
    # the destination holds the PRE-CALL frozen record, whatever
    # the oracle did to the live one
    assert dest.records() == [original]
    _assert_one_record_per_identity(dest, 1)
    # clean-merge equivalence
    clean = _probe(constant_oracle)
    clean.merge(_raw_source([dict(original)]))
    assert (dest.buckets, dest.identity_index) == \
        (clean.buckets, clean.identity_index)


def test_aliased_source_record_mutated_between_iterations():
    """The SAME live dict yielded twice: the oracle mutates it to
    a hostile shape on the first call, so the second iteration's
    shape check fails typed - never raw - with full atomic
    rollback of the first insertion."""
    nc, vc, dc, epc, fc = _NODE_DOCS
    live = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                        "standard", STARTPOS)
    calls = {"n": 0}

    def oracle(variant, fen):
        calls["n"] += 1
        live["snapshot_fen"] = []  # hostile mutation mid-merge
        return constant_oracle(variant, fen)

    dest = _probe(oracle)
    before = (copy.deepcopy(dest.buckets),
              copy.deepcopy(dest.identity_index))
    with pytest.raises(CollisionError) as exc:
        dest.merge(_raw_source([live, live]))
    assert exc.value.failure_class == "malformed_collision_record"
    assert calls["n"] == 1
    assert (dest.buckets, dest.identity_index) == before


def test_mutant_validate_live_then_reread_after_oracle():
    """Behavioral mutant: the v7 merge validated the live record,
    invoked the oracle, then DEEP-COPIED the live record - an
    oracle mutation to [] escapes as raw AttributeError. Pinned
    to prove freezing is load-bearing. Counter-test: the real
    merge inserts the pre-call frozen record cleanly."""
    def mutant_merge(dest, other):
        nc, vc, dc, epc, fc = _NODE_DOCS
        for rec in other.records():
            dest._validate_record_shape(dest.cc, rec)
            dest._validate_record_semantics(rec)
            key = dest._call_oracle(rec["variant"],
                                    rec["snapshot_fen"])
            exact = copy.deepcopy(rec)  # mutant: re-read live
            dest._staged_insert(exact, dest._identity(exact), key)
        return dest

    nc, vc, dc, epc, fc = _NODE_DOCS
    original = _make_record(nc, vc, dc, epc, fc, constant_oracle,
                            "standard", STARTPOS)
    source_ref = [dict(original)]
    calls = {"n": 0}
    oracle = _mutating_oracle(source_ref, "snapshot_fen",
                              "hostile", calls)
    with pytest.raises(AttributeError):
        mutant_merge(_probe(oracle), _raw_source(source_ref))
    calls["n"] = 0
    source_ref = [dict(original)]
    oracle = _mutating_oracle(source_ref, "snapshot_fen",
                              "hostile", calls)
    real = _probe(oracle)
    real.merge(_raw_source(source_ref))
    assert real.records() == [original]
