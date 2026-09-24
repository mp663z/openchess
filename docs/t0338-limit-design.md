# T0338: jobs limit contract - design notes (docs-only; the contract yaml is normative)

## Scope
One admit-or-deny decision for one keyed token bucket, over an explicit
logical clock (`now` in milliseconds, supplied by the caller). The
bucket state is the previous decision record, passed back in by the
caller, so the function is pure. Queue transitions, leases, retry
backoff, idempotency and cross-key fairness are out of scope.

## Model
- Request (closed, exact keys): `admit {op, key, cost, now, policy, previous}`.
- policy is exactly `{capacity, refill_ms}`; capacity 1..1000000,
  refill_ms 1..86400000. cost 1..capacity. now 0..2^53-1.
- key uses the queue contract's worker grammar `^[A-Za-z0-9._:-]{1,64}$`;
  the lint fails if it drifts.
- previous is null (a fresh, full bucket) or exactly the previous record.
- Record: exactly `{op, key, capacity, refill_ms, cost, decision, tokens,
  updated_at, retry_after_ms, previous_id, limit_id}`.
- limit_id is `lm1:` + hex sha256 of `b"lm1\x00"` + canonical JSON
  (sort_keys, separators `,` `:`, ensure_ascii) of the record without
  limit_id. A golden chain pins full records and ids.

## Arithmetic
- gained = (now - prev.updated_at) // refill_ms.
- If prev.tokens + gained >= capacity: refilled = capacity, base = now.
- Else refilled = prev.tokens + gained, base = prev.updated_at + gained * refill_ms.
- Admit when refilled >= cost: tokens = refilled - cost, retry null.
- Deny: tokens = refilled, retry = (cost - refilled) * refill_ms - (now - base).
- updated_at = base.

## Fail-closed readings (the spec is silent)
1. No partial refill is banked at capacity.
2. cost > capacity is malformed_limit_request, never a deny that could
   never clear.
3. A policy or key change against the previous record is limit_conflict;
   the caller starts a fresh bucket.
4. now == previous updated_at is accepted; now < updated_at is limit_conflict.
5. A deny whose retry time passes 2^53-1 is clock_overflow, never clamped.
6. A previous record is re-validated in full: shape, grammar, bounds,
   decision invariants and a recomputed limit_id. It is never repaired.
7. Every key must be an exact str before any key-set comparison, on
   the request, the policy and the previous record.

## Flagged
Replay idempotency and cross-key fairness need T0293/T0302 (parked).
