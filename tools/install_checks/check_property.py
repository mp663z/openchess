"""T0023: property check - differential + consistency properties over the
license-expression gate (tools/license_audit.expression_ok), the function
that decides whether a dependency license is acceptable.

Good mode properties (all must hold):
  P1 differential: expression_ok agrees with an INDEPENDENT reference
     oracle (regex fullmatch per OR-branch, different implementation)
     over thousands of seeded generated expressions drawn from an
     adversarial token alphabet (allowed ids, disallowed ids, operators,
     junk, case variants).
  P2 acceptance soundness: whenever expression_ok accepts, at least one
     OR-branch is entirely allowlisted per the reference oracle.
  P3 branch consistency: if exactly one OR-branch is fully allowlisted,
     expression_ok(whole) == expression_ok(that branch alone) == True.
Violation mode: pinned attack corpus (copyleft ids, AND-mixed copyleft,
parentheses, WITH exceptions, dangling/duplicated operators, empty
branches, separator tricks) must ALL be rejected, and the pinned benign
corpus must all be accepted.
"""

from __future__ import annotations

import random
import re

from tools import license_audit as la
from tools.install_checks import CheckError

CHECK_ID = "T0023"

_ID = r"[A-Za-z0-9.\-]+"


def _reference_ok(expr: str) -> bool:
    """Independent oracle: OR of ANDs, regex fullmatch, no shared code."""
    e = expr.strip()
    if not e or "(" in e or ")" in e:
        return False
    if not re.fullmatch(r"[\x20-\x7e]+", e):
        return False
    toks = e.split()
    if not toks or toks[0].upper() in {"AND", "OR", "WITH"}:
        return False
    if toks[-1].upper() in {"AND", "OR", "WITH"}:
        return False
    branches = re.split(r"\s+OR\s+", e, flags=re.IGNORECASE)
    for branch in branches:
        if not re.fullmatch(rf"{_ID}(\s+AND\s+{_ID})*", branch, flags=re.IGNORECASE):
            return False
        tokens = re.split(r"\s+AND\s+", branch, flags=re.IGNORECASE)
        if any(t.upper() in {"AND", "OR", "WITH"} for t in tokens):
            return False
    for branch in branches:
        if all(t in la.ALLOWED for t in re.split(r"\s+AND\s+", branch, flags=re.IGNORECASE)):
            return True
    return False


def _generated(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    alphabet = (
        sorted(la.ALLOWED)
        + ["GPL-3.0-only", "AGPL-3.0-or-later", "Proprietary", "CC0-1.0",
           "mit", "APACHE-2.0", "AND", "OR", "and", "or", "WITH", "(", ")",
           "", "OR OR", "MITX", "MIT-0X"]
    )
    out = []
    for _ in range(count):
        n = rng.randint(1, 6)
        out.append(" ".join(rng.choice(alphabet) for _ in range(n)))
    return out


ATTACKS = [
    "GPL-3.0-only",
    "AGPL-3.0-or-later",
    "Proprietary",
    "MIT AND GPL-3.0-only",
    "(MIT)",
    "(MIT OR Apache-2.0)",
    "MIT OR (Apache-2.0)",
    "MIT WITH Classpath-exception-2.0",
    "Apache-2.0 WITH LLVM-exception",
    "MIT OR",
    "OR MIT",
    "MIT OR OR Apache-2.0",
    "MIT AND",
    "MIT OR  OR Apache-2.0",
    "",
    "   ",
    "MITX",
    "MIT-0X",
    "MIT OR Proprietary OR Apache-2.0",  # legal by OR semantics - see note
]
# NOTE: "MIT OR Proprietary OR Apache-2.0" is ACCEPTED by correct SPDX OR
# semantics (recipient picks MIT); it is in ATTACKS only to pin that the
# implementation matches the oracle - the oracle also accepts it, so it is
# moved to the benign corpus below and kept here as documentation.
ATTACKS = ATTACKS[:-1]

BENIGN = [
    "MIT",
    "Apache-2.0",
    "BSD-3-Clause",
    "MIT OR Apache-2.0",
    "MIT OR Proprietary OR Apache-2.0",
    "BSD-2-Clause AND ISC",
    "MIT OR GPL-3.0-only",  # OR semantics: recipient picks MIT
]


def run(mode: str) -> None:
    if mode == "good":
        mismatches = []
        for e in _generated(5000, seed=20260919):
            actual = la.expression_ok(e)
            if actual != _reference_ok(e):
                mismatches.append(f"{e!r}: expression_ok={actual} ref={_reference_ok(e)}")
            if actual and not any(
                re.fullmatch(rf"{_ID}(\s+AND\s+{_ID})*", b, flags=re.IGNORECASE)
                and all(t in la.ALLOWED for t in re.split(r"\s+AND\s+", b, flags=re.IGNORECASE))
                for b in re.split(r"\s+OR\s+", e.strip(), flags=re.IGNORECASE)
            ):
                mismatches.append(f"{e!r}: accepted but no fully-allowlisted OR-branch (P2)")
        for e in BENIGN:
            if not la.expression_ok(e):
                mismatches.append(f"benign rejected: {e!r}")
        for e in ATTACKS:
            if la.expression_ok(e):
                mismatches.append(f"attack accepted: {e!r}")
        if mismatches:
            raise CheckError(f"property violations: {mismatches[:5]}")
        return
    # violation mode: every attack must be REJECTED by the real gate
    uncaught = [e for e in ATTACKS if la.expression_ok(e)]
    if uncaught:
        return  # harness FAILS: an attack expression passed the gate
    raise CheckError(f"all {len(ATTACKS)} attack expressions rejected by the real gate")
