# T0302: jobs cancel contract - design notes (docs-only; the contract yaml is normative)

## Scope
One cancellation decision for one queue job, over an explicit logical
clock (`now` in milliseconds, supplied by the caller). The cancel
contract owns cancellation eligibility and the decision record. Queue
state transitions stay with the queue contract
(data/contracts/queue.yaml), retry decisions with the retry contract
(data/contracts/retry.yaml), dead-letter admission with the dead-letter
contract (data/contracts/dead_letter.yaml). Applying a decision to the
queue, aborting a worker, idempotency keys, redrive of a cancelled job
and deduplication of repeated cancellations are out of scope.

## Model
- Request (closed, exact keys): `cancel {op, job, requester, now}`.
  `job` is exactly one queue job with the queue contract's job_fields.
- requester is a caller-supplied cancellation requester id with the
  queue contract's worker grammar.
- Bounds: now 0..2^53-1; seq 0..2^53-2 (a stored seq is below next_seq,
  itself at most 2^53-1); priority 0..9; payload is exact canonical
  JSON, acyclic, alias-free, depth at most 64, ints of at most 4000
  digits, finite floats, UTF-8 encodable strings.
- max_attempts, the job_id grammar, the worker grammar, the job fields,
  the statuses and the priority and payload bounds are the queue
  contract's. The lint fails if any of them drifts from the queue
  contract.
- Record: exactly `{op, job_id, seq, priority, payload_digest,
  status_before, attempts, requester, cancelled_at, decision_id}` with
  status_before in {ready, leased}.
- payload_digest is `pd1:` + hex sha256 of `b"pd1\x00"` + canonical
  JSON (sort_keys, separators `,` `:`, ensure_ascii) of the payload.
  decision_id is `cd1:` + hex sha256 of `b"cd1\x00"` + canonical JSON
  of the record without decision_id. Golden vectors pin both.

## Fail-closed readings (the spec is silent)
1. The job must satisfy every queue job invariant: grammar, bounds,
   status enum, lease fields, attempts rules and payload admission.
   Otherwise it is corrupt_job. It is never repaired.
2. Only a ready or leased job is cancelled. A valid done or dead job is
   job_not_cancellable.
3. A ready job has not started: cancellation withdraws it before any
   claim.
4. A leased job is claimed: the decision is recorded against the live
   lease. The record carries status_before and attempts, never the
   lease fields; applying the decision to the queue or aborting the
   worker is outside this contract.
5. The presented status governs. An expired lease is still a leased
   job here; lease expiry and reclaim are the queue contract's concern,
   never recomputed by cancel.
6. A recorded cancellation is terminal; this contract never reverses
   it.
7. The record stores only the payload digest, never a copy of the
   payload.
8. Validation order is request shape -> job integrity -> cancellable
   status.
9. Every key must be an exact str before any key-set comparison, on
   the request, the job and every payload mapping.

## Not covered (flagged)
Applying the decision to queue state, worker abort, deduplication of
repeated cancellations and redrive of a cancelled job need the T0293
idempotency semantics and the composition layer. Those are parked for
the owner, matching the parked notes in the retry (T0311), progress
(T0320), dead-letter (T0329), limit (T0338) and restart (T0347)
siblings that name T0293/T0302.

## Failures
malformed_cancel_request -> malformed_request; corrupt_job ->
corrupt_job; job_not_cancellable -> job_not_cancellable. `internal` is
the only retryable code.
