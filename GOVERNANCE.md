# Governance

This file describes how the project makes decisions. It is deliberately
small: one owner, a maintainer group, and written records.

## Roles

- **Project owner**: the original author and trademark holder. The owner
  holds final authority over licensing, releases, governance changes, and
  the public roadmap, and approves every governance document (LICENSE,
  NOTICE, CLA, DCO, CONTRIBUTING, GOVERNANCE, TRADEMARK, SECURITY, rights
  policy, CODEOWNERS) personally or through engaged counsel.
- **Maintainers**: contributors with merge rights, listed in
  .github/CODEOWNERS. Maintainers review and merge within their areas and
  keep the evidence registry honest. The owner is the sole maintainer at
  project bootstrap; new maintainers are named in this file as they join,
  with the appointment recorded in the pull request that adds them.
- **Contributors**: everyone who submits pull requests under CLA.md or
  DCO.md.

## Decision making

- Routine changes (bug fixes, docs, refactors with green CI): lazy
  consensus among maintainers; silence after a reasonable review window is
  consent.
- Substantial changes (new components, schemas, contracts, dataset or model
  additions, policy changes): a written proposal in the pull request,
  review by the affected code owners, and a recorded decision. Disagreement
  is resolved by the owner.
- Evidence gates are not subject to convenience overrides: an independent
  review FAIL, a license-audit rejection, or a broken evidence chain blocks
  the change until remediated. Nobody, including the owner, marks UNVERIFIED
  work as done.

## Moderation and appeals

Maintainers may remove content and suspend participation for
code-of-conduct violations (CONTRIBUTING.md). A moderated contributor may
appeal to the project owner (contact in the README); the owner's decision
is final.

## Delegation

The owner may delegate specific decisions (for example, merge judgment on a
defined set of pull requests) in writing. A delegation names its scope, is
recorded in the affected task evidence, and is revocable at any time. A
delegation never bypasses the evidence gates: independent review and
recorded verification still apply to every delegated decision.

## Amending this file

Amendments require a pull request with the owner's explicit approval
recorded in the task evidence. Amendments apply only after merge; no
governance change is retroactive.

## Trademarks and assets

Project names and logos are owned by the project owner (see TRADEMARK.md).
Domain names, hosted infrastructure, signing keys, and publishing
credentials are held by the owner and are not project assets by default.
