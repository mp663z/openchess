# T0185 graph conflict contract - design draft (v0)

Mirror the T0122-T0176 gold standard: structured yaml + exact lint
pins + fully contract-derived reference + mutation battery.

## Product + plan grounding

- Dev plan row T0185 (O2 trusted diagnosis): Graph/conflict/
  contract, deps T0018 + T2357 (both done).
- The graph chain so far: node/edge identity (T0122/T0131),
  collision (T0158), version (T0167), diff (T0176). What is NOT
  pinned: what happens when TWO edits derived from the same base
  meet. The approval gate (no silent writes) demands conflicts
  surface with witnesses, never auto-resolve silently.

## Scope decision

A conflict is a property of TWO states derived from ONE base
(three-way). Inputs are (base_id, base, left_id, left, right_id,
right) - NOT diff records, because the plan's dependency list
(T0018 + T2357) keeps this contract independent of the T0176
diff chain; change derivation per side is the identity-keyed
symmetric difference vs base (same semantics the diff contract
pins, derived here from the linked identity rules). Detection is
exact and total over canonical identity; resolution policy is
fail-closed: conflicts are reported with both-side witnesses,
never silently merged. Compatible edits (disjoint identities, or
byte-identical outcomes) merge cleanly. divergent_base: the
three state ids must be distinct (an id collision means the
caller is not presenting a real three-way).

## Design decisions (draft)

- Conflict record: EXACTLY [base_id, left_id, right_id,
  conflicts]; conflicts maps canonical identity -> witness
  {kind, left, right} where kind is one of
  [both_changed_differently, changed_vs_removed,
  added_differently] and left/right are the exact change
  payloads (or an ABSENT sentinel for no touch).
- Incompatibility kinds (total): same identity changed to
  unequal targets; changed on one side, removed on the other;
  added on both sides with unequal records.
- Compatible (never conflicts): disjoint identities; same
  identity with byte-identical outcomes (both add the same
  record, both remove, both change to the same target).
- divergent_base: the two diffs must name the SAME base_id and
  that base must match the provided base state exactly - fail
  closed otherwise.
- Guarantees: determinism (canonical order), symmetry
  (left/right swap yields flipped witnesses), completeness
  (every incompatible overlap surfaces; empty conflicts iff the
  diffs are compatible), no silent auto-resolution.
- Failures (closed): [malformed_conflict_record,
  divergent_base] with pinned triggers.
- Witness model: real node records via the linked contracts;
  adversarial batteries prove no silent merge.
- Versioning: /graph/conflict/v1.

## Test plan (>=25 mutants)

- Happy: compatible disjoint edits merge; identical outcomes
  compatible; each incompatibility kind detected with witness.
- Completeness permutations over mutation pairs; symmetry.
- divergent_base by id and by content.
- Malformed Cartesian across every field; rollback bit-identical.
- Linkage: diff sections, node identity, collision separation.
