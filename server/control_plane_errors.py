"""Pure classifier for control-plane HTTP status plus JSON error payload.

Implements data/contracts/control_plane_errors.yaml. No reference test or fixture
is used at runtime. A refusal is a fresh, context-free ErrorsError and the
classifier never mutates, retains or echoes its inputs. Validation order is
source, status, operation, payload; the first failure wins.
"""

from __future__ import annotations

import re

from server.control_plane_versioning import CHESS_TOKENS
from server.control_plane_versioning import _source as _validate_source

_FEN_PIECES = frozenset("pnbrqkPNBRQK")
_FEN_DIGITS = frozenset("12345678")
_TOKEN_RUN = re.compile(r"[A-Za-z0-9+/=_%-]{32}")
_CAMEL_A = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_B = re.compile(r"([a-z0-9])([A-Z])")
_SPLIT = re.compile(r"[._-]")
_SECRET_KEYS = frozenset({"key_material", "password_hash_client", "token", "secret"})
_SECRET_IN_MESSAGE = ("key_material", "password_hash_client")
_MAX_DEPTH = 8
_MAX_ITEMS = 256


class ErrorsError(Exception):
    """Fresh, context-free malformed-error-result refusal."""

    def __init__(self, message="invalid error result"):
        super().__init__(message)
        self.failure_class = "malformed_error_result"
        self.code = "malformed_request"
        self.retryable = False


def _refuse():
    raise ErrorsError()


def _closed_enum(source):
    enum = None
    try:
        _validate_source(source)
        enum = tuple(source["contract"]["transport"]["errors"]["closed_enum"])
    except Exception:  # noqa: BLE001 - any malformed source is one refusal
        enum = None
    if enum is None:
        _refuse()  # raised outside the handler: no __context__ leaks
    return enum


def _text_ok(text):
    return type(text) is str and not any("\ud800" <= ch <= "\udfff" for ch in text)


def _key_tokens(key):
    spaced = _CAMEL_B.sub(r"\1_\2", _CAMEL_A.sub(r"\1_\2", key))
    return {part.lower() for part in _SPLIT.split(spaced) if part}


def _rank_ok(rank):
    if not 1 <= len(rank) <= 8:
        return False
    total, prev_digit = 0, False
    for ch in rank:
        if ch in _FEN_PIECES:
            total, prev_digit = total + 1, False
        elif ch in _FEN_DIGITS and not prev_digit:
            total, prev_digit = total + int(ch), True
        else:
            return False
    return total == 8


def _has_fen_placement(text):
    """True if any substring is a valid eight-rank placement (linear time)."""
    segments = text.split("/")
    if len(segments) < 8:
        return False
    full = [_rank_ok(s) for s in segments]
    prefix = [any(_rank_ok(s[:n]) for n in range(1, min(len(s), 8) + 1)) for s in segments]
    suffix = [any(_rank_ok(s[-n:]) for n in range(1, min(len(s), 8) + 1)) for s in segments]
    bad = [0]
    for ok in full:
        bad.append(bad[-1] + (not ok))
    return any(
        suffix[i] and prefix[i + 7] and bad[i + 7] == bad[i + 1] for i in range(len(segments) - 7)
    )


def _key_ok(key, declared, error_response):
    if not _text_ok(key) or CHESS_TOKENS & _key_tokens(key):
        return False
    if not declared and (key.lower() in _SECRET_KEYS or {"provider", "key"} <= _key_tokens(key)):
        return False
    return not (error_response and _TOKEN_RUN.search(key))


def _value_ok(value, depth, declared, error_response):
    if depth > _MAX_DEPTH:
        return False
    kind = type(value)
    if value is None or kind is bool or kind is int:
        return True
    if kind is float:
        return value == value and abs(value) != float("inf")
    if kind is str:
        return (
            _text_ok(value)
            and not _has_fen_placement(value)
            and not (error_response and _TOKEN_RUN.search(value))
        )
    if kind is list:
        return len(value) <= _MAX_ITEMS and all(
            _value_ok(v, depth + 1, frozenset(), error_response) for v in list.__iter__(value)
        )
    if kind is dict:
        if len(value) > _MAX_ITEMS:
            return False
        for k, v in dict.items(value):
            if type(k) is not str or not _key_ok(k, k in declared, error_response):
                return False
            if not _value_ok(v, depth + 1, frozenset(), error_response):
                return False
        return True
    return False


def _declared_fields(source, status, operation):
    if operation is None:
        return frozenset()
    if type(operation) is not str:
        _refuse()
    try:
        area, name = operation.split(".")
        fields = frozenset(source["areas"][area]["ops"][name]["response"]["fields"])
    except Exception:  # noqa: BLE001
        # The one context-carrying refusal path is the operation-parse guard
        # (data/contracts/control_plane_errors.yaml, pinned by the closed corpus).
        _refuse()
    return fields if status < 300 else frozenset()


def classify(source, status, payload, operation=None):
    """Return {"kind": "success"} or the error verdict; raise ErrorsError."""
    enum = _closed_enum(source)
    if type(status) is not int or not (200 <= status <= 299 or 400 <= status <= 599):
        _refuse()
    declared = _declared_fields(source, status, operation)
    if type(payload) is not dict or not _value_ok(payload, 0, declared, status >= 400):
        _refuse()
    if status < 300:
        return {"kind": "success"}
    error = dict.get(payload, "error")
    if type(error) is not dict:
        _refuse()
    for field in ("code", "message", "retryable"):
        if field not in error:
            _refuse()
    code, message, retryable = error["code"], error["message"], error["retryable"]
    if type(code) is not str or code not in enum:
        _refuse()
    if not _text_ok(message) or type(retryable) is not bool:
        _refuse()
    lowered = message.lower()
    if any(token in lowered for token in _SECRET_IN_MESSAGE):
        _refuse()
    return {"kind": "error", "code": code, "message": str(message), "retryable": retryable}
