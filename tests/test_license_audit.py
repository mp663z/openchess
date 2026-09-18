"""License audit: SPDX-subset parsing, fail-closed (T0032 regression cover)."""

import pytest

from tools.license_audit import candidate_ok, expression_ok


@pytest.mark.parametrize("expr", [
    "MIT",
    "Apache-2.0 OR BSD-2-Clause",
    "BSD-3-Clause",
    "GPL-2.0-only OR MIT",
    "MIT AND Apache-2.0",
    "MIT OR Apache-2.0 OR BSD-3-Clause",
])
def test_allowed_expressions(expr):
    assert expression_ok(expr)


@pytest.mark.parametrize("expr", [
    "MIT AND GPL-2.0-only",        # historical substring bypass
    "GPL-2.0-only",
    "GPL-2.0-only OR CC-BY-NC-4.0",  # OR with no allowed branch
    "UNKNOWN",
    "MIT-like",
    "NotMIT",
    "MIT OR",                       # dangling operator
    "OR MIT",
    "MIT AND",
    "(MIT OR Apache-2.0)",          # parens: unparsed, fail closed
    "MIT WITH Classpath-exception-2.0",  # exceptions: unparsed, fail closed
    "",
])
def test_rejected_expressions(expr):
    assert not expression_ok(expr)


def test_free_text_with_allowed_token_is_rejected():
    assert not candidate_ok("This package is not MIT licensed but its text mentions MIT")
    assert not candidate_ok("MIT-style terms, see website")


def test_trailing_license_word_tolerated_on_clean_ids():
    assert candidate_ok("MIT License")
    assert not candidate_ok("GPL License")
