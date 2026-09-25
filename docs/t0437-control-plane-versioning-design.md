# T0437 control-plane versioning

The source rule is `data/contracts/control-plane.yaml`'s `contract.versioning`: clients pin MAJOR, MINOR adds optional request fields, response fields, operations or provider_kind values, unknown request fields are ignored, and a same-major client downgrade needs no migration. A MAJOR change needs a new base path.

The pure reference comparison accepts two validated snapshots and explicit minor numbers because the current source has only a major base path and no encoded minor. It checks declared shape compatibility, not semantic implementation behavior. OpenAPI document structure belongs to T0419; graph-content snapshots belong to `data/contracts/version.yaml`.

Reading of control-plane.yaml lines 13-19: the MINOR enumeration is closed. A new response field must be optional because a server MUST accept a client from any earlier minor within its major; that older server cannot guarantee a later field. An existing optional response field becoming required is not in the enumeration and therefore needs MAJOR with a new base path. The same is true for request fields. Rollback permits response extras, not guaranteed presence. Runtime semantic compatibility still needs independent conformance checks.

T0419's strict source derivation is used as a validation gate without copying its OpenAPI rules. The older control-plane lint is not enough for hostile types. The T0437 battery is a reference only; the later implement task must run these rows against its separately built implementation.
