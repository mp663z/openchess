# T0194: store migration contract - design notes (docs-only; the contract yaml is normative)

## Scope
Store schema migration for the position graph: transform a content-addressed
canonical state (identity -> exact node record map, gs1: state ids per the
graph-diff contract) from one pinned STORE SCHEMA to the next. Detection,
diffing, versioning already exist; migration OWNS the schema-version step.

## Model
- Schema registry pinned in the contract: store-v1 (current node record
  schema: variant/digest/snapshot_fen, pdv1 digest grammar) and store-v2
  (same fields, pdv2 digest grammar - the realistic re-digest migration).
- One registered step: store-v1 -> store-v2, transform = re-digest every
  record under the target oracle; canonical identities (variant + canonical
  snapshot) unchanged; record set cardinality preserved; nothing added or
  dropped.
- migrate(request, source_state): request = {from_schema, to_schema,
  source_id}. Source state validated through the linked node machinery
  (same totality family as conflict/diff); source_id recomputed and
  compared structurally BEFORE any migration; the step registry resolves
  (from_schema, to_schema) or fails unknown_migration; the transform runs
  on a staged copy; the target oracle is UNTRUSTED input (single
  evaluation per record, exact built-in-str pinned-format returns,
  frozen record snapshots - the collision-contract boundary lessons);
  the migrated state's recomputed id is returned in the receipt.

## Failures (closed)
- malformed_migration_record: request grammar/state/record violations.
- unknown_migration: no registered step for (from_schema, to_schema),
  including no-op (from == to) and downgrade requests.
- conflicting_source: recomputed source id differs from request.source_id.
- divergent_target: recomputed migrated-state id differs from the
  transform's claimed result (target oracle inconsistency surfaces here
  or as malformed, pinned).

## Properties
- total over hostile requests/states/oracles; atomic (source never
  mutated; staged copy only); deterministic; cardinality preserving;
  identity preserving under re-digest; rollback: rejected migration
  leaves every input bit-identical.
