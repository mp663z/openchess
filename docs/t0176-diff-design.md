# T0176 graph diff contract - design draft (v0, authoring in progress)

Mirror the T0122-T0167 gold standard: structured yaml + exact lint
pins + fully contract-derived reference + mutation battery.

## Product + plan grounding

- Dev plan row T0176 (O2 trusted diagnosis): Graph/diff/contract,
  deps T0018 (public monorepo, done) + T2357 (week-1 gate, done).
- Product report: the approval gate is the trust primitive -
  "visible diffs, cited evidence, no silent writes" (line 61);
  "review with diffs" for deltas (line 151). T0006 already pins
  "no silent writes: executable invariant requires visible diff
  and approval" - the graph diff contract is the GRAPH half of
  that invariant: a complete, exact, deterministic record of what
  changed between two graph states.
- Line 224: position identity vs move-order path vs repertoire
  context - a diff must key records by CANONICAL identity from the
  linked table contracts, never by accelerator digests.

## Scope decision

A diff is a first-class, exact description of the difference
between two graph states over the linked table family
(transposition nodes now; the model is table-generic). NOT in
scope: scoring/ranking changes (delta engine, later chain),
version lineage (T0167 chain - a diff is over STATES, not
versions; versions can name the states later).

## Design decisions (draft)

- Diff record: EXACTLY [base_id, target_id, added, removed,
  changed] where added/removed are exact record sets keyed by
  canonical identity and changed carries BOTH-side witnesses
  (base record, target record) for identities present in both
  with unequal exact content.
- Identity keys come from the LINKED table contracts' canonical
  identity rules - derived, never restated; accelerator digests
  never key a diff (collision contract).
- Completeness (the no-silent-writes half): EVERY difference
  between base and target surfaces in the diff; diff(A,B) empty
  iff A and B hold equal exact records. Pinned as a theorem the
  battery proves by permutation.
- Determinism: canonical-identity ordering inside each section;
  diff(A,B) is a pure function of (A, B) - same inputs, same diff,
  byte-identical serialization.
- Symmetry: reverse(diff(A,B)) == diff(B,A) with added/removed
  swapped and changed witnesses flipped.
- Apply semantics: apply(diff(A,B), A) == B exactly; applying to
  a non-matching base fails closed (conflicting_base) - never a
  silent partial apply. Rollback: failed apply leaves base
  bit-identical.
- Failures (closed): [malformed_diff_record, conflicting_base,
  unknown_identity] -> closed enum; retryable only internal.
- Witness model: real transposition-node table records via the
  linked contracts' machinery; adversarial mutation batteries
  (remove a change silently -> completeness theorem catches it).
- Versioning: /graph/diff/v1.

## Test plan (>=25 mutants)

- Happy: empty diff on equal states; add-only; remove-only;
  change-only; mixed.
- Completeness: every permutation of a mutation set surfaces
  exactly - no silent difference.
- Symmetry + apply round-trips across permutations.
- Malformed: Cartesian battery across every diff field;
  conflicting_base; unknown_identity.
- Rollback bit-identical on every rejection.
- Mutants: drop completeness, key by digest, nondeterministic
  order, partial apply, each failure class, error enum.
- Linkage: node identity section, collision separation,
  digest format re-verified against actual siblings.
