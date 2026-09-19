"""T0044: variant runtime - the reference implementation of the T0041
chess variant + position-identity contract (offline tooling per
ADR-0001; the product runtime target is the Rust core, which this
module mirrors and the fixture pins).

API:
- parse_position(variant, fen) -> position record (exact canonical
  five-field dict); rejects unknown variant ids and malformed/illegal
  FENs with VariantError carrying a closed-enum code and, for FEN
  failures, the contract's declared failure_class.
- identity(record) -> exact canonical five-field projection; additive
  fields are ignored (MINOR-tolerant), missing canonical fields fail.
- VariantError: .code (closed error enum), .failure_class (declared
  FEN failure class or None), .retryable.
"""

from __future__ import annotations

import yaml

from tools.variant_contract_lint import (
    CANONICAL_FIELDS,
    CONTRACT,
    FAILURE_CLASSES,
    _check_fen,
    check_variant_id,
)
from tools.variant_contract_lint import (
    ContractError as _ContractError,
)

_DOC = yaml.safe_load(CONTRACT.read_text())
_REGISTRY = {e["id"]: e for e in _DOC["contract"]["variants"]["entries"]}
_ERROR_ENUM = set(_DOC["contract"]["errors"]["closed_enum"])

# failure class -> closed error code: grammatical FEN failures are
# request-shape errors; a grammatical but nonsensical position is its
# own code. Unknown variant ids fail closed per the registry rule.
_CODE_FOR_CLASS = {"illegal_position": "illegal_position"}


class VariantError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        failure_class: str | None = None,
        retryable: bool = False,
    ) -> None:
        if code not in _ERROR_ENUM:
            raise ValueError(f"undeclared error code {code!r}")
        if failure_class is not None and failure_class not in FAILURE_CLASSES:
            raise ValueError(f"undeclared failure class {failure_class!r}")
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class
        self.retryable = retryable


def parse_position(variant: object, fen: object) -> dict:
    """Validate variant id and FEN against the contract; return the
    canonical position record. Fails closed, never coerces."""
    try:
        check_variant_id(variant, set(_REGISTRY))
    except _ContractError as exc:
        code = str(exc).split(":", 1)[0]
        raise VariantError(code=code, message=str(exc)) from exc
    kind = _REGISTRY[variant]["castling"]
    try:
        _check_fen(fen, variant, kind)
    except _ContractError as exc:
        cls = exc.failure_class
        raise VariantError(
            code=_CODE_FOR_CLASS.get(cls, "malformed_request"),
            failure_class=cls,
            message=str(exc),
        ) from exc
    board, side, castling, ep, _half, _full = fen.split(" ")
    return dict(zip(CANONICAL_FIELDS, (variant, board, side, castling, ep), strict=True))


def identity(record: object) -> dict:
    """The exact canonical identity tuple of a record: the five declared
    fields in declared order. Additive fields never participate."""
    if type(record) is not dict:
        raise VariantError(code="malformed_request", message="identity: record mapping required")
    missing = [f for f in CANONICAL_FIELDS if f not in record]
    if missing:
        raise VariantError(
            code="malformed_request",
            message=f"identity: record missing canonical fields {missing}",
        )
    return {f: record[f] for f in CANONICAL_FIELDS}
