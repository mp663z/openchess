# T0446 control-plane errors

The source contract declares the closed error-code enum, `{error: {code, message, retryable}}`, additive extra fields and status-only error signalling. The merged mock supplies an executable reading for some status codes, but it does not supply every code; T0419's mapping is linked, not duplicated here. The conformance harness checks exact value types but does not forbid extras. The classifier checks declared shape and privacy without inferring a retry schedule or overriding the declared retryable flag.

Fail-closed readings where the source is silent: unsupported 1xx/3xx have no payload classification; error messages must be UTF-8 encodable, and extra field values must be JSON-like and privacy safe. A 2xx object may contain `error` as ordinary data. This is a reference contract, not a live HTTP middleware implementation.

A declared success field such as identity.login's token is a narrow name-only exemption, not a blanket exemption from structure or chess checks. Undeclared extras and the entire error path cannot carry secret-named fields. T0419's source derivation gates the error shape; CHESS_TOKENS comes from its pinned privacy rule, not a second local list.

FEN detection builds from fen.yaml placement rank count, separator, piece letters, run digits and rank sum; eight ranks trigger refusal even inside a longer message or nested extra. Seven ranks are a near miss. This does not assert general detection of SAN/move text, nor key-shaped values. Those are open gaps (T0392 token-run rule pending for SAN/moves, owner pattern needed for key-shaped values).

Chess-content checks (key tokens and FEN values) apply to every response; secret-name checks apply to all but declared fields. FEN detection scans every start offset for an eight-rank window and checks the rank sum, piece letters and run digits against fen.yaml. Prefix glue or punctuation does not hide a placement; a nine-rank run with any valid eight-rank window is refused. A malformed candidate passes only when no valid eight-rank window exists. SAN/move text and key-shaped values remain gaps.
