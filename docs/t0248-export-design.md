# T0248: store export contract - design notes (docs-only; the contract yaml is normative)

## Scope
A canonical, read-only export of the current state of a WAL-logged
graph store into a pinned portable format. WAL owns the journal,
backup/restore own restorable snapshots, and rollback owns log
truncation. Export OWNS the export receipt and binds the rendered
document to it. Import, backup, restore and partial or filtered
export are explicitly not in scope.

## Model
- Request: exactly `{format}`. `format` is an exact built-in
  string from the closed list `[jsonl-v1]`.
- Receipt: the exact six-field record {export_id, head, state_id,
  record_count, format, document}. `export_id` is content-addressed
  (exp1: sha256 over head, state_id, record_count, format and
  document) and DERIVED, never caller-supplied. `head` and
  `state_id` come from the linked WAL replay and the migration
  state digest. `record_count` counts live records after replay,
  not log entries.
- export(log, request): the request shape is checked first, then
  the format. The source log validates and replays through the
  LINKED WAL machinery (any rejection surfaces as corrupt_source).
  Log, replayed state and request are FROZEN before the untrusted
  exporter runs. The exporter is called EXACTLY ONCE, on a
  DETACHED state copy, so mutating its argument has no effect.
  Its output must be an exact built-in string that is
  UTF-8-encodable and equal BYTE-FOR-BYTE to the LOCAL canonical
  rendering: one line per live record, sorted by identity, each
  line canonical JSON (sorted keys, compact separators) of
  {identity, record}, newline-terminated. A valid-looking but
  divergent document (other state, dropped or reordered records,
  other encoding, stateful variation) fails closed.
- Read-only: export never mutates the log or the request.
  Accepted or rejected, both come back bit-identical, with the
  original references preserved.

## Failures (closed)
- malformed_export_request: request shape/type violation, or a
  source log that is not a list.
- unsupported_format: an exact-string format outside the closed
  list.
- corrupt_source: the source log fails linked WAL validation or
  chain re-derivation.
- divergent_export: the exporter raises ANY BaseException
  (KeyboardInterrupt/SystemExit/GeneratorExit included), returns
  a non-exact-string or UTF-8-inencodable value, or returns a
  document that diverges from the local canonical rendering.

## Properties
- total over hostile requests, logs and exporters
- atomic: every export leaves log and request bit-identical
- deterministic: same log and format give the same receipt; the
  same state through a different history gives the same document
  and state_id but a different head and export_id
- rollback: nothing is committed, so there is nothing to undo
