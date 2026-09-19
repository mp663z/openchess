"""T0044: variant runtime - the reference implementation of the T0041
chess variant + position-identity contract (offline tooling per
ADR-0001; the product runtime target is the Rust core, which this
module mirrors and the fixture pins).

API:
- parse_position(variant, fen) -> Position: a typed immutable position
  record constructible ONLY through this function (private construction
  token). Rejects unknown variant ids and malformed/illegal FENs with
  VariantError carrying a closed-enum code and, for FEN failures, the
  contract's declared failure_class.
- identity(position) -> exact canonical five-field projection (dict in
  declared field order). Requires a Position AND revalidates its five
  fields through the same registry + _check_fen path as every other
  entry point: the construction token is defense-in-depth, never the
  only gate, so a token-copying dataclasses.replace or an
  object.__setattr__ mutation cannot smuggle an invalid record through.
- project_additive(record) -> the same canonical projection for an
  arbitrary additive mapping (e.g. a record carrying extra fields).
  Fully validates first: exact scalar types, registered variant, and
  canonical board/side/castling/en-passant syntax with variant
  compatibility. Additive fields are ignored (MINOR-tolerant).
- VariantError: .code (closed error enum), .failure_class (declared
  FEN failure class or None), .retryable (exact bool), .args[0]/
  str() message (nonempty str). Constructor enforces the contract's
  exact error shape - it cannot be built out of shape.

Provenance: the contract document is LINTED at import time before any
runtime state is derived from it; a malformed contract fails the import
closed instead of silently reshaping runtime behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
from tools.variant_contract_lint import (
    lint as _lint,
)

_DOC = yaml.safe_load(CONTRACT.read_text())
_lint(_DOC)  # provenance gate: malformed contract -> import fails closed
_REGISTRY = {e["id"]: e for e in _DOC["contract"]["variants"]["entries"]}
_ERROR_ENUM = set(_DOC["contract"]["errors"]["closed_enum"])

# failure class -> closed error code: grammatical FEN failures are
# request-shape errors; a grammatical but nonsensical position is its
# own code. Unknown variant ids fail closed per the registry rule.
_CODE_FOR_CLASS = {"illegal_position": "illegal_position"}

_SYNTHETIC_COUNTERS = "0 1"  # legality never depends on counters


class VariantError(Exception):
    def __init__(
        self,
        code: object,
        message: object,
        failure_class: object = None,
        retryable: object = False,
    ) -> None:
        if type(code) is not str or code not in _ERROR_ENUM:
            raise ValueError(f"undeclared error code {code!r}")
        if type(message) is not str or not message.strip():
            raise ValueError("error message must be a nonempty string")
        if failure_class is not None and (
            type(failure_class) is not str or failure_class not in FAILURE_CLASSES
        ):
            raise ValueError(f"undeclared failure class {failure_class!r}")
        if type(retryable) is not bool:
            raise ValueError("retryable must be an exact bool")
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class
        self.retryable = retryable


_TOKEN = object()


@dataclass(frozen=True)
class Position:
    """Immutable validated position record. The private construction
    token makes casual forgery fail closed, but ordinary Python can
    copy it (dataclasses.replace) or mutate under frozen=True
    (object.__setattr__) - so identity() REVALIDATES the fields on
    every call and the token is defense-in-depth, not the gate."""

    variant: str
    board: str
    side_to_move: str
    castling_rights: str
    en_passant: str
    _token: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._token is not _TOKEN:
            raise VariantError(
                code="malformed_request",
                message="Position is constructible only via parse_position",
            )


def _projection(variant: str, board: str, side: str, castling: str, ep: str) -> dict:
    return dict(zip(CANONICAL_FIELDS, (variant, board, side, castling, ep), strict=True))


def parse_position(variant: object, fen: object) -> Position:
    """Validate variant id and FEN against the contract; return the
    canonical Position. Fails closed, never coerces."""
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
    return Position(variant, board, side, castling, ep, _token=_TOKEN)


def _validate_fields(variant: object, board: object, side: object,
                     castling: object, ep: object) -> None:
    """The single validation path every public entry point funnels
    through: exact str scalars, registered variant, canonical syntax +
    variant compatibility via _check_fen (synthetic counters; legality
    never depends on counters)."""
    for name, v in (("variant", variant), ("board", board), ("side_to_move", side),
                    ("castling_rights", castling), ("en_passant", ep)):
        if type(v) is not str:
            raise VariantError(
                code="malformed_request",
                message=f"field {name} must be an exact string",
            )
    try:
        check_variant_id(variant, set(_REGISTRY))
    except _ContractError as exc:
        code = str(exc).split(":", 1)[0]
        raise VariantError(code=code, message=str(exc)) from exc
    kind = _REGISTRY[variant]["castling"]
    fen = " ".join((board, side, castling, ep, *_SYNTHETIC_COUNTERS.split(" ")))
    try:
        _check_fen(fen, variant, kind)
    except _ContractError as exc:
        cls = exc.failure_class
        raise VariantError(
            code=_CODE_FOR_CLASS.get(cls, "malformed_request"),
            failure_class=cls,
            message=str(exc),
        ) from exc


def identity(position: object) -> dict:
    """The exact canonical identity tuple of a Position: the five
    declared fields in declared order. REVALIDATES every field through
    _validate_fields before projecting - a Position whose fields were
    mutated or token-copied after construction fails closed here."""
    if type(position) is not Position:
        raise VariantError(
            code="malformed_request",
            message="identity: a Position from parse_position is required",
        )
    _validate_fields(
        position.variant,
        position.board,
        position.side_to_move,
        position.castling_rights,
        position.en_passant,
    )
    return _projection(
        position.variant,
        position.board,
        position.side_to_move,
        position.castling_rights,
        position.en_passant,
    )


def project_additive(record: object) -> dict:
    """Canonical projection of an additive mapping: the five declared
    fields in declared order, additive fields ignored. The record is
    fully validated first - exact scalar types, registered variant, and
    canonical syntax with variant compatibility - so a projection can
    never launder an invalid position into an identity."""
    if type(record) is not dict:
        raise VariantError(code="malformed_request", message="project_additive: mapping required")
    missing = [f for f in CANONICAL_FIELDS if f not in record]
    if missing:
        raise VariantError(
            code="malformed_request",
            message=f"project_additive: record missing canonical fields {missing}",
        )
    for f in CANONICAL_FIELDS:
        if type(record[f]) is not str:
            raise VariantError(
                code="malformed_request",
                message=f"project_additive: field {f} must be an exact string",
            )
    _validate_fields(
        record["variant"],
        record["board"],
        record["side_to_move"],
        record["castling_rights"],
        record["en_passant"],
    )
    return _projection(record["variant"], record["board"], record["side_to_move"],
                       record["castling_rights"], record["en_passant"])
