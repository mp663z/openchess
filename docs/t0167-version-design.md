# T0167 version contract - design draft (v0, authoring in progress)

Mirror the T0122-T0158 gold standard: structured yaml + exact lint
pins + fully contract-derived reference + mutation battery.

## Product + plan grounding

- docs/plan/development-plan-v9.md rows T0167-T0175: the
  Graph/version chain serves objective "O2 trusted diagnosis" -
  graph CONTENT versioning so a diagnosis computed against the
  repertoire graph is reproducible: which graph state did this
  diagnosis run against.
- docs/plan/product-report-v5.md line 399: "versioned lines with
  transposition-correct matching" (Repertoire + Delta + Diagnosis).
- Line 648: "every change versioned and replayed against the rig
  before shipping".
- Distinct from T0437 (Contracts/versioning): that chain versions
  CONTRACT schemas (base-path rules already pinned per contract);
  THIS contract versions GRAPH CONTENT - the user's evolving
  repertoire graph.

## Design decisions (draft)

- A version is an IMMUTABLE named snapshot reference to a graph
  state: once created it never changes in place (mirrors the
  per-contract versioning rule: never in place, always a new
  version).
- Version record: EXACTLY [version_id, parent_ids, graph_digest,
  created_at, label]. version_id: pinned grammar (v + zero-padded
  sequence? or content-addressed - DECIDE: content-addressed id
  from the graph state digest + parents gives dedup for free;
  sequence ids give readable ordering. Draft: content-addressed
  over (parents, graph_digest) - idempotence by construction).
- parent_ids: nonempty set except the ROOT version (exactly one,
  parent_ids empty) - lineage is a DAG, never a mutable pointer.
- graph_digest: the state digest of the whole graph at freeze
  time - the accelerator-format digest family already pinned
  (pdv1:), derived never identity... no: for VERSIONS the digest
  IS the content address. Pin: graph_digest is the content hash
  over the canonical serialization of the graph tables.
- Monotonicity: a child version's created_at >= every parent's;
  the DAG has exactly one root; every non-root's parents must
  exist in the version store (fail closed: unknown_parent).
- Merge: insert-or-return-existing (same id = same content, by
  content addressing); atomic; idempotent; commutative;
  associative. Conflicting id (same id, different content) is
  impossible BY CONSTRUCTION under content addressing - pinned as
  a theorem the battery proves (an injected digest collision
  surfaces as conflicting_version, fail closed, never overwrite).
- Failures: [malformed_version_record, unknown_parent,
  conflicting_version, root_violation, nonmonotonic_version]
  -> closed enum. nonmonotonic_version added beyond the first
  draft: a child older than a parent is a distinct closed
  failure, not malformedness. Equal timestamps are allowed
  (not-before, not strictly-after).
- created_at: RFC3339 UTC 'Z' only; REAL Gregorian calendar
  validation (leap-year rules); leap seconds REJECTED (second
  00-59), pinned and documented.
- id derivation: a record's version_id MUST equal the store's
  content address of its own (sorted parents, graph_digest) -
  callers never invent ids; the battery's injectable hasher
  forces a total collision to prove conflicting_version fails
  closed with a witness, never overwrites.
- Merge: topological staging - a record enters the staged copy
  once every parent is present; a batch that stops making
  progress surfaces its first stuck record's exact failure.
  Atomic commit; bit-identical rollback.
- Rollback: rejected insert leaves the version DAG bit-identical.
- Versioning: /graph/version/v1.

## Test plan (>=25 mutants)

- Happy: root + linear chain + branching DAG.
- Boundary: root uniqueness, single-parent, multi-parent merge
  version.
- Content addressing: same graph state + same parents = same id
  (dedup); different label never forks id? DECIDE whether label
  participates (draft: NO - label is metadata, identity is
  content+parents).
- Malformed: each field, grammar, timestamp, unknown parent,
  second root.
- Collision witness: injected digest oracle forces id collision
  with different content -> conflicting_version, never overwrite.
- Merge algebra: permutations/groupings of DAG inserts agree.
- Rollback: every rejection bit-identical.
- Linkage: digest format, import timestamp grammar family.
