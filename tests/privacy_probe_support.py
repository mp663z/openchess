"""Boundary-only test helpers; never decide expected privacy semantics."""

from __future__ import annotations

import types
from pathlib import Path


class ForeignCrash(Exception):
    """Candidate exception outside its closed refusal class."""


def outcome(fn, error, *args):
    try:
        result = fn(*args)
    except error as exc:
        assert type(exc) is error
        assert exc.__cause__ is None and exc.__context__ is None
        return ("failure", exc.failure_class, exc.code)
    except Exception as exc:  # noqa: BLE001 - no raw exception is a semantic result
        raise ForeignCrash(type(exc).__name__) from exc
    return ("ok", result)


def load_mutant(module, old, new):
    source = Path(module.__file__).read_text()
    assert source.count(old) == 1, old
    mutant = types.ModuleType("synthetic_privacy_mutant")
    mutant.__file__ = module.__file__
    exec(compile(source.replace(old, new), module.__file__, "exec"), vars(mutant))
    return mutant
