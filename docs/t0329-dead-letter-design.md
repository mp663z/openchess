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

## Sibling tension (flagged; provisional pending owner ruling)
- data/contracts/retry.yaml, `semantics.permanent`:
  `a-permanent-failure-is-dead-at-any-attempt-count`.
- data/contracts/queue.yaml, `semantics.dead_letter`:
  `a-claimable-job-whose-attempts-reached-max-attempts-becomes-dead-and-is-skipped`.
  The queue state invariant holds a dead job only at attempts ==
  max_attempts (`state.max_attempts: 5`).

So a permanent failure at fewer attempts is not a valid dead queue job.
Coordinator ruling: no sibling changes now; this contract fails closed.
Such a job is refused as corrupt_job (no burial, no user code, input
unchanged), stated in `semantics.provisional` and pinned by a test row
labeled "provisional pending owner ruling on permanent-dead attempt
count". A permanent-dead job at attempts == max_attempts buries
normally. The owner picks between (a) relaxing the queue invariant to
attempts <= max_attempts when the last failure is permanent, or (b)
recording permanent failures with attempts forced to max_attempts. This
contract follows whichever is picked.

## Not covered (flagged)
Redrive of a cancelled job, and deduplication of repeated burials by
idempotency key, need the T0293/T0302 semantics. Those tasks are parked
for the owner.

## Failures
malformed_bury_request -> malformed_request; corrupt_job -> corrupt_job;
job_not_dead -> job_not_dead. `internal` is the only retryable code.
