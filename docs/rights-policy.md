# Rights Policy

How this project decides what code, data, and models it may use. The rule
is simple: when rights are unclear, the answer is no. Fail closed, always.

## Code

- The project's own code is AGPL-3.0-or-later (LICENSE, NOTICE).
- Dependencies are admitted only under licenses on the allowlist enforced
  by the license audit (permissive: MIT, Apache-2.0, BSD, ISC, and
  equivalents). Copyleft dependencies are admitted only where the license
  audit proves the combination keeps the product's AGPL obligations intact;
  GPL-2.0-only combinations are rejected.
- Distribution dependencies are pinned with cryptographic hashes in the
  release lock (data/release-lock.json, regenerated per target by
  tools/lock_release.py); the SBOM is regenerated in the same pull request
  whenever a pinned input changes. Development-only tools carry minimum
  versions in requirements-dev.txt and are not part of the distributed
  product.

## Datasets

A dataset enters the tree or the training/evaluation pipeline only when its
license or dedication explicitly permits the intended use, recorded in
data/datasets/ with the license name, source location, retrieval date, and
content hashes (snapshot pins). Known outcomes:

- Allowed: CC0 public dedications, MIT-licensed tools and converters, and
  the user's own files imported locally by that user (for example their own
  game collections), which the project never redistributes.
- Fail closed: scraped broadcast archives, subscription databases, "free
  for personal use" collections, and anything whose provenance cannot be
  documented. Popularity in the community is not permission.

## Models

Model weights and checkpoints are treated like datasets: license recorded,
use rights verified for both distribution and commercial service, hashes
pinned. Models with research-only or non-commercial terms are fail closed
for this product. Provider terms for API-accessed models are reviewed per
provider and recorded before integration.

## Models and outputs in the product

No claim is made that any diagnostic, evaluation, or generation capability
is validated until the corresponding evidence task passes independent
review. Marketing, docs, and the public README must distinguish shipped
behavior from planned behavior.

## Questions

Rights questions are decided by the project owner. When the owner cannot
determine an answer from the license text, the material stays out; counsel
may be engaged for decisions with durable consequences.
