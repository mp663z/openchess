# Contributing

Contributions are welcome. This project is evidence-gated: code, data, and
documents land only with their checks green and their task evidence filed.
This guide is the short path from idea to merged pull request.

## Ground rules

1. Every change references a task ID (TNNNN) from the task registry. Work
   without a task is not merged.
2. Every merged task files evidence under `evidence/TNNNN.md` in the strict
   format enforced by `tools/evidence_lint.py`: status, verification mode
   matching the registry, reproducing commands, environment, and (when done)
   the exact merge SHA. UNVERIFIED is never done.
3. Licensing fails closed. Third-party code, datasets, or models enter only
   through the license audit (`tools/license_lint.py` and the rights policy
   in docs/rights-policy.md). Unidentified or disallowed material is not
   merged, however useful.
4. Naming stays neutral and swappable; product codenames do not enter the
   tree.

## Pull request flow

1. Branch per task cluster; keep the tree lint-clean before pushing (the
   pre-push hook runs the full gate: style, tests, registry verify, license
   audit, evidence lint).
2. CI must be green: unit and contract tests, style checks, release-lock
   freshness, SBOM regeneration when locked inputs change, evidence lint,
   naming scan.
3. One logical change per pull request. Squash-merged after review.
4. Independent-review tasks are not marked done by the author. They are
   routed to the independent verifier with the merge SHA and done only on
   its PASS at that SHA.

## Legal

- Most contributions require the Contributor License Agreement (CLA.md);
  the automated CLA check records acceptance on your first pull request.
- De minimis contributions (typo scale; see DCO.md for the exact bounds)
  may instead carry a `Signed-off-by:` trailer certifying the Developer
  Certificate of Origin.
- All contributions are distributed under the project's outbound license,
  AGPL-3.0-or-later.

## Code of conduct

Be direct, be kind, assume competence. Harassment, personal attacks, and
bad-faith review are not acceptable. Maintainers may remove content and
suspend participation; see GOVERNANCE.md for appeals.

## Development setup

Python 3.12, `pip install -r requirements-dev.txt`, then `pytest -q` and
`ruff check .` must pass before any push. Dataset work follows the snapshot
pins in data/datasets/; pinned inputs change only with their SBOM
regenerated in the same pull request.
