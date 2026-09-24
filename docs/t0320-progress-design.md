# T0320: jobs progress contract - design notes (docs-only; the contract yaml is normative)

## Scope
One progress report for one leased queue job, chained to the job's
previous report, over an explicit logical clock (`now` in milliseconds,
supplied by the caller). The progress contract owns progress
monotonicity, the completion percent and the chained progress record.
Queue state, lease validity and retry stay with the queue and retry
contracts (data/contracts/queue.yaml, data/contracts/retry.yaml).
Idempotency keys, cancellation and ETA are out of scope.

## Model
- Request (closed, exact keys): `report {op, job_id, worker, done,
  total, now, previous}`. `previous` is null for the first report and
  otherwise exactly the previous progress record.
- Bounds: total 1..2^53-1 work units; done 0..total; now 0..2^53-1.
- job_id and worker grammars come from the queue contract. The lint
  fails if either drifts from the queue contract.
- Record: exactly `{op, job_id, worker, done, total, percent_bp,
  reported_at, previous_id, progress_id}`. percent_bp is
  `done * 10000 // total` (exact integer floor), so it is 10000 only
  when done == total. previous_id is null on the first report and
  otherwise the previous record's progress_id.
- progress_id is `pg1:` + hex sha256 of the exact preimage `b"pg1\x00"`
  + canonical JSON (sort_keys, separators `,` `:`, ensure_ascii) of the
  record without progress_id. Because the record carries previous_id,
  every record verifies on its own and the chain links cannot be
  swapped. Golden vectors pin a four-record chain.

## Fail-closed readings (the spec is silent)
1. total is fixed by the first report. A changed total is a conflict,
   never a rescale.
2. done never decreases. An equal done is a heartbeat: it is accepted
   and advances reported_at.
3. Completion is terminal: no report follows a record with
   done == total, not even a heartbeat.
4. now never precedes the previous reported_at. An equal now is
   accepted.
5. job_id and worker must equal the previous record's. A new worker
   (after a lease moves) starts a new chain with previous null.
6. A previous record whose shape, grammar, bounds, percent or
   progress_id do not recompute is corrupt_previous_record. It is never
   trusted or repaired.
7. Validation order is request shape -> previous integrity -> chain
   consistency. A malformed request wins over a corrupt previous, and a
   corrupt previous wins over a conflict.
8. The first report may carry any done in bounds, including
   done == total.
9. Every key must be an exact str before any key-set comparison, on
   both the request and the previous record.

## Not covered (flagged)
Progress of a cancelled job, and deduplication of replayed reports by
idempotency key, need the T0293/T0302 semantics. Those tasks are
parked for the owner. Whether the reporting worker still holds an
unexpired lease is the queue contract's check, not this one.

## Failures
malformed_progress_request -> malformed_request; corrupt_previous_record
-> corrupt_record; progress_conflict -> progress_conflict. `internal` is
the only retryable code.
