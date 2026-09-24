# T0419: control-plane OpenAPI contract - design notes (docs-only; the contract yaml is normative)

## Scope
A pure, deterministic derivation from data/contracts/control-plane.yaml
(schema_version 2) to one OpenAPI 3.1.0 document. Serving, validation
middleware, events, versioning policy beyond the base path, error
catalog wording, rate limits and the AI adapter are later tasks
(T0428-T0464).

## Mapping
- info.version is the base path's major; servers holds the base path.
- One path item per op, source order; operationId is `area.op`.
- security: [] for public_operations, bearerAuth otherwise.
- parameters: the IdempotencyKey header for mutating ops, [] otherwise.
  The header schema is string, minLength 1, pattern \S: control-plane.yaml
  says "nonempty" and the merged mock (sha256 pinned) answers 400 to a
  whitespace-only key.
- POST ops get a required JSON requestBody; GET ops must be read-only
  with no request fields.
- Field tables become object schemas: type, items (scalar arrays only),
  nested objects (depth <= 8), required list, examples as a one-item
  list, writeOnly for write_only request fields,
  additionalProperties true.
- responses: 200 with the response schema, then one error response per
  status (ascending) listing its codes in x-error-codes, all pointing at
  components.schemas.Error.
- Error: control-plane shape exactly; code gets enum = closed_enum.

## Status readings
- contract_fixed: auth_expired/auth_invalid 401, idempotency_conflict
  409 (control-plane rule text).
- mock_reading: taken from tools/control_plane_mock.py (sha256 pinned):
  quota_exhausted 429, quota_reservation_expired 409,
  provider_key_invalid 400, malformed_request 400, not_found 404,
  conflict 409. The unknown-route 404 is excluded.
- unmapped: entitlement_missing, provider_unavailable,
  cost_cap_exceeded, rate_limited, internal. Never given an invented
  status; listed for the owner batch.

## Fail-closed readings (the spec is silent)
1. Any malformed or hostile-typed input is malformed_request, no
   partial document, source untouched.
2. Example integers within 2**53 - 1; float examples finite; string
   examples and string items UTF-8 encodable (no lone surrogate); the
   error code example is a closed-enum code.
3. Area extras only as plain strings.
4. Exact str keys are checked before any key-set comparison.

## Caution
The reference derive() lives in the battery and comes from the same
contract, so the battery proves consistency; the implement task must run
the same rows against a separately built generator.
