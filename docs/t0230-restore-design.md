# T0230: store restore contract - design notes (docs-only; the contract yaml is normative)

## Scope
Verified restore of content-addressed backup bundles into a
canonical graph state. Backup owns the snapshot receipt; restore
OWNS turning a verified receipt back into a validated state.
Incremental restore and WAL log reconstruction are explicitly
not scope.

## Model
- Receipt: exact three-field record {restore_id, backup_id,
  state_id} plus the restored state. restore_id is
  content-addressed (rst1: sha256 over backup_id + state_id),
  DERIVED, never caller-supplied.
- restore(receipt): the receipt verifies through the LINKED
  backup machinery FIRST - a grammatically invalid receipt is
  malformed_restore_record, a grammatically valid but
  unverifiable one is unverified_backup, and no parser call
  happens before verification. The validated fields are FROZEN;
  the canonical bundle is parsed EXACTLY ONCE through the
  untrusted parser boundary (raising or a non-exact-built-in-dict
  output fails closed as divergent_parse); the parsed mapping is
  validated record-by-record through the linked node machinery
  (exact str keys, exact records, identity == key) and its
  recomputed gs1: id must equal the receipt's state_id - anything
  less is divergent_state. The input receipt is restored
  bit-identical on every exit.

## Failures (closed)
- malformed_restore_record: receipt grammar/type violations.
- unverified_backup: receipt fails linked backup verification.
- divergent_parse: parser raising ANY BaseException (KeyboardInterrupt/SystemExit/GeneratorExit included - the boundary catches BaseException so the untrusted oracle can never escape raw) or non-exact-mapping output.
- divergent_state: parsed content invalid (record, identity) or
  recomputed state id differs from the receipt's.

## Properties
- total over hostile receipts and parsers; atomic (rejected
  restore leaves the receipt bit-identical); deterministic (same
  receipt, same restored state); rollback: staged state only,
  committed after full validation.
