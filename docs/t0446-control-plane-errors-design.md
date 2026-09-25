# T0446 control-plane errors

The source contract declares the closed error-code enum, `{error: {code, message, retryable}}`, additive extra fields and status-only error signalling. The merged mock supplies an executable reading for some status codes, but it does not supply every code; T0419's mapping is linked, not duplicated here. The conformance harness checks exact value types but does not forbid extras. The classifier checks declared shape and privacy without inferring a retry schedule or overriding the declared retryable flag.

Fail-closed readings where the source is silent: unsupported 1xx/3xx have no payload classification; error messages must be UTF-8 encodable, and extra field values must be JSON-like and privacy safe. A 2xx object may contain `error` as ordinary data. This is a reference contract, not a live HTTP middleware implementation.
