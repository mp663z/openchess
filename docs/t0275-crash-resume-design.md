# T0275: store crash-resume contract - design notes (docs-only; the contract yaml is normative)

## Scope
Recovering a WAL-logged store after a crash: keep every acknowledged
entry, and quarantine and remove the torn tail. WAL owns the journal,
backup/restore own snapshots and rollback owns deliberate truncation.
Crash resume OWNS the resume receipt and the torn-tail quarantine.
Backup, restore, rollback and multi-log replication are not in scope.

## Model
- Request: exactly `{checkpoint_sequence}`, an exact built-in int. It
  is the last sequence acknowledged as durable before the crash (0 =
  genesis).
- Receipt: the exact six-field record {resume_id, head, state_id,
  resumed_count, discarded_count, quarantine_token}. resume_id is
  content-addressed (rsm1: sha256 over head, state_id, resumed_count,
  discarded_count and quarantine_token) and DERIVED.
- resume(log, request):
  1. Shape: log must be a list and the request exact. A negative
     checkpoint is unknown_checkpoint. A checkpoint beyond the end of
     the log means acknowledged entries are MISSING, which is
     corrupt_source.
  2. Prefix: the surviving log is the LONGEST prefix that passes
     linked WAL validation and replay.
  3. Durability: if that prefix is shorter than the checkpoint, the
     crash destroyed acknowledged data. That is corrupt_source, and
     nothing is repaired.
  4. Torn tail: everything from the first invalid entry onward,
     including entries that look valid again later (splicing over a
     tear would fake a chain). Every discarded entry must be exact
     canonical JSON, else malformed_resume_record.
  5. Quarantine: log and request are frozen and snapshotted
     (reference-preserving, every nested container). The UNTRUSTED
     sink is called EXACTLY ONCE with a detached copy of the tail,
     even an empty one. Its token must equal the local canonical
     serialization byte-for-byte (sha256 over the domain-separated,
     length-framed canonical JSON of each discarded entry).
  6. Commit LAST: remove exactly the torn tail.

## Failures (closed)
- malformed_resume_record: bad request or log shape/type, or a
  non-canonical-JSON discarded entry.
- unknown_checkpoint: a negative checkpoint.
- corrupt_source: an entry at or before the checkpoint is missing (the
  log is shorter than the checkpoint) or fails linked WAL validation.
- divergent_quarantine: the sink raises any BaseException, returns a
  non-exact-str or wrong-grammar value, or returns a token that
  diverges from the tail.

## Properties
Total, atomic (a rejected resume leaves inputs bit-identical),
deterministic, idempotent (resuming a resumed log discards nothing
and keeps head and state), and rollback (a committed resume removes
exactly the torn tail).

## Decisions and known consequences
- Truncated durable log. A log shorter than its checkpoint (for
  example 2 entries with checkpoint 3) is corrupt_source, not
  unknown_checkpoint. Truncation is the most common crash damage, and
  it destroys acknowledged data exactly like a damaged entry does, so
  the durability rule decides it. unknown_checkpoint covers only a
  checkpoint that can never be a position (negative).
- Total admission. Discarded entries are admitted by an iterative
  walk with a global seen set. Cycles, aliases (any container reached
  twice, which would make the walk and the encoding exponential),
  nesting deeper than 64 and ints with
  more than 4000 decimal digits are rejected as
  malformed_resume_record, so hostile tails can never escape raw as
  RecursionError or ValueError. Request keys are type-checked as
  exact str before any set comparison, so a key with a colliding hash
  and a raising __eq__ fails closed.
- Non-canonical torn values fail closed. A torn tail holding values
  outside exact canonical JSON (NaN/Infinity, lone surrogates, bytes,
  cycles, over-deep nesting) is rejected as malformed_resume_record
  instead of being quarantined. So a JSON-lines tail torn into such
  values makes the store unrecoverable by this contract, and it needs
  manual repair. This is deliberate: the quarantine token must be a
  deterministic byte-exact serialization, which those values do not
  have. A later minor version may add a raw-bytes quarantine path.
