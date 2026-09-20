# T0158 collision contract - design draft (v0, authoring in progress)

Mirror the T0122/T0131/T0140/T0149 gold standard: structured yaml +
exact lint pins + fully contract-derived reference + mutation battery.

## Product grounding

- docs/plan/product-report-v5.md line 224: two records are the same
  position only when the canonical fields all match - "never
  identified by a single collision-prone hash".
- Linked variant contract identity.hash_rule: any storage hash is a
  lookup accelerator ONLY; collisions MUST fall back to full
  canonical field comparison; a hash mismatch never decides
  inequality without field comparison.
- Linked position-digest contract: role kind lookup-accelerator-only,
  digest format pdv1: + 64 hex.

## Scope decision

Every graph table already pins accelerator-only lookup (node, edge,
provenance). What is NOT yet pinned anywhere: what a COLLISION is,
how a bucket separates colliding records, and the cross-table
guarantees that hold when an adversarial/accelerated oracle maps
distinct identities to one bucket key. This contract pins the
collision semantics themselves, implementation-neutrally: the
bucket model, the separation rule, the no-merge/no-fork
guarantees, and the failure classes - so every table's
accelerator-only claim has one normative definition to point at.

## Design decisions (draft)

- A collision is: two records whose accelerator bucket keys compare
  EQUAL while their canonical identities compare UNEQUAL. Bucket
  keys are derived values, never stored as identity.
- Separation rule: bucket membership accelerates lookup ONLY; every
  equality/inequality decision inside a bucket is canonical field
  comparison over the exact stored records (linked per-table
  identity rules, never restated).
- No-merge: a collision never merges two distinct records into one
  (no silent dedup). No-fork: a collision never splits one record
  into two. Counts pinned: inserting N distinct-identity records
  into one bucket yields exactly N records.
- Determinism under collision: iteration/serialization order is
  canonical-identity order, never bucket-arrival order - collision
  resolution is order-insensitive.
- Merge algebra holds under collision: idempotent, commutative,
  associative even when all records share one bucket.
- Failure classes: malformed_collision_record (a record failing the
  linked table's own record shape), accelerator_as_identity
  (callers must not present a bucket key AS an identity - fail
  closed), plus internal. Equality is never a failure class: a
  collision is normal operation, never an error.
- Witness model: an injectable digest oracle (constant oracle maps
  everything to one valid bucket) exercised through the REAL table
  machinery (transposition node table), proving the battery tests
  behavior, not a mock.
- Versioning: /graph/collision/v1.

## Test plan (>=25 mutants)

- Happy: collision-free baseline mirrors the linked table behavior.
- Forced-collision battery through the constant oracle: N distinct
  records in one bucket stay N; reinsertion finds the right record;
  no-merge and no-fork counts.
- Order-insensitivity: every insertion permutation of colliding
  records yields the same canonical table.
- Merge algebra under collision (permutations, groupings).
- Malformed + rollback: rejected colliding inserts leave the table
  bit-identical.
- Linkage: variant hash_rule, digest format, node identity sections
  re-verified against the actual siblings.
