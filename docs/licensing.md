# Licensing policy

The product (working title placeholder {{PRODUCT_NAME}}) is AGPL-3.0-only. Third-party dependencies must carry permissive
licenses so the product can ship without license conflicts and downstream
users keep maximum freedom.

Allowed (allowlist in `tools/license_audit.py`):

- MIT, MIT-0
- Apache-2.0
- BSD-2-Clause, BSD-3-Clause
- ISC
- Python-2.0
- MPL-2.0 (weak copyleft, file-level; compatible as a dependency)

Anything else (GPL, LGPL, AGPL, SSPL, proprietary, unknown) fails the audit.
The audit runs in CI on every push and PR, and as a release task (T3669)
on every release candidate.
