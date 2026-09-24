# T0329: jobs dead-letter contract - design notes (docs-only; the contract yaml is normative)

## Scope
One dead-letter entry for one dead queue job, over an explicit logical
clock (`now` in milliseconds, supplied by the caller). The dead-letter
contract owns dead-letter admission and the entry record. Queue state
transitions stay with the queue contract (data/contracts/queue.yaml),
and retry decisions with the retry contract (data/contracts/retry.yaml).
Redrive, idempotency keys and cancellation are out of scope.

## Model
- Request (closed, exact keys): `bury {op, job, reason, now}`. `job` is
  exactly one queue job with the queue contract's job_fields.
- reason is `exhausted` or `permanent`.
- Bounds: now 0..2^53-1; seq 0..2^53-2 (a stored seq is below next_seq,
  itself at most 2^53-1); priority 0..9; payload is exact canonical
  JSON, acyclic, alias-free, depth at most 64, ints of at most 4000
  digits, finite floats, UTF-8 encodable strings.
- max_attempts, the job_id grammar, the job fields and the priority and
  payload bounds are the queue contract's. The lint fails if any of
  them drifts from the queue contract.
- Record: exactly `{op, job_id, seq, priority, payload_digest, attempts,
  reason, buried_at, entry_id}`.
- payload_digest is `pd1:` + hex sha256 of `b"pd1\x00"` + canonical
  JSON (sort_keys, separators `,` `:`, ensure_ascii) of the payload.
  entry_id is `dl1:` + hex sha256 of `b"dl1\x00"` + canonical JSON of
  the record without entry_id. Golden vectors pin both.

## Fail-closed readings (the spec is silent)
1. The job must satisfy every queue job invariant: grammar, bounds,
   status enum, lease fields, attempts rules and payload admission.
   Otherwise it is corrupt_job. It is never repaired.
2. Only a dead job is buried. A valid ready, leased or done job is
   job_not_dead.
3. A dead job carries attempts == max_attempts (the queue's dead
   invariant) and no lease, whatever the reason.
4. reason is caller-attested. Both reasons need the queue dead
   invariant.
5. The entry stores only the payload digest, never a copy of the
   payload.
6. Validation order is request shape -> job integrity -> dead status.
7. Every key must be an exact str before any key-set comparison, on
   the request, the job and every payload mapping.

## Sibling tension (flagged)
The retry contract makes a permanent failure dead at any attempt count,
but the queue contract only holds dead jobs with attempts ==
max_attempts. So a permanent failure at fewer attempts cannot appear as
a dead queue job, and this contract cannot bury it. Resolving that
belongs to the queue contract, not here.

## Not covered (flagged)
Redrive of a cancelled job, and deduplication of repeated burials by
idempotency key, need the T0293/T0302 semantics. Those tasks are parked
for the owner.

## Failures
malformed_bury_request -> malformed_request; corrupt_job -> corrupt_job;
job_not_dead -> job_not_dead. `internal` is the only retryable code.
