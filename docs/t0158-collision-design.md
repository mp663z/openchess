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
  closed), accelerator_inconsistent (the oracle trust boundary,
  below), plus internal. Equality is never a failure class: a
  collision is normal operation, never an error.

## Oracle trust boundary (v2 - verifier #2 remediation)

The injectable digest oracle is UNTRUSTED input. v1 left the
boundary unguarded: an oracle hashing raw FEN text (including the
identity-excluded clocks or the raw uncapturable EP target), or a
stateful oracle, could silently fork one canonical identity into
two records across two buckets - bucket-scoped search would never
see the twin.

Enforcement, both halves together:

1. Independent canonical-identity index: the equality search
   domain is a canonical-identity index maintained INDEPENDENTLY
   of the bucket structure. Equal canonical identities dedupe
   against the index even when the oracle misbuckets them, so a
   fork can never silently persist.
2. Insert-time consistency validation, fail closed: the oracle
   owes a bucket key in the linked digest contract's exact format,
   and an equal canonical identity MUST yield the same bucket key
   as the already-stored record. Divergence (equal identity,
   different key) or an invalid-format key rejects the insert as
   accelerator_inconsistent with bit-identical rollback - the
   fork is surfaced, never absorbed.

## Oracle invocation boundary (v3 - verifier #2 remediation)

v2 still let a RAISING oracle escape the closed failure surface
(raw ValueError through _make_record, which catches only
NodeError). v3 puts EVERY oracle invocation - the insert path AND
merge revalidation - behind one boundary helper
(_call_oracle). An oracle that raises is the same inability to
supply a key as an invalid-format return: both map to
accelerator_inconsistent, with the structured trigger pinned in
failures.triggers (oracle-raised-or-invalid-format-key-or-equal-
identity-divergent-key). The contract's own typed validation
errors are raised by sibling machinery OUTSIDE the boundary and
are never caught or relabeled by it. Atomic merge commits nothing
whether the oracle raises on the first call or midway through the
batch - destinations stay bit-identical. A behavioral mutant
calling the oracle directly leaks the raw exception, pinned to
prove the boundary is load-bearing.

Cross-oracle merges fail closed as malformed_collision_record
when an incoming record's stored bucket key disagrees with the
receiver's oracle (validated through the receiver's oracle over
the exact incoming record, never re-bucketed silently). Merges
across consistent oracles succeed.

Sorted canonical views pin BOTH count and uniqueness against the
set of canonical identities in every permutation - a forked table
can produce a stable sorted view WITH duplicates, so uniqueness
is asserted, never assumed.
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
- Trust-boundary repros (v2): raw-clock-hashing oracle,
  phantom-EP-hashing oracle, stateful fresh-key oracle - each
  rejected accelerator_inconsistent with one record per identity
  and bit-identical rollback; reverse insertion order; an
  invalid-format bucket key rejected; 720 adversarial permutations
  x canonical twins (raw-clock + phantom-EP forms) asserting
  count-exactness AND per-identity uniqueness; cross-oracle merge
  rejection both directions with consistent-oracle merge success;
  a behavioral mutant (v1-shaped bucket-only search) pinned to
  fork under the raw oracle while the real probe rejects.
