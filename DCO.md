# Developer Certificate of Origin and De Minimis Contributions

This project accepts contributions under two regimes: the Contributor
License Agreement (CLA.md) for ordinary contributions, and the Developer
Certificate of Origin below for de minimis contributions only. This file
carries the certificate text, the sign-off mechanics, and the exact de
minimis bounds.

## Developer Certificate of Origin

Only de minimis contributions (within the bounds below) are made under the
Developer Certificate of Origin, Version 1.1 (the same certificate used by
the Linux kernel project). All other contributions are made under CLA.md
and carry no DCO sign-off obligation:

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.

Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

## Sign-off

A contribution is signed off by adding a `Signed-off-by: Name <email>`
trailer to each commit message. The sign-off certifies the DCO above.
Maintainers verify the trailer before merging a DCO-covered contribution
and record the check in the pull request review; unsigned contributions are
not merged. Contributions covered by a recorded CLA acceptance
(data/cla-acceptances.yaml) do not need the trailer. Maintainer mechanical
commits (release locks, SBOM regeneration, dataset pin hashes) carry the
maintainer's own sign-off like any other commit.

## De minimis contributions

Contributions below the de minimis threshold may be accepted under this DCO
alone, without the CLA (CLA.md). De minimis means ALL of the following:

1. no more than 10 changed lines across the whole pull request, and
2. no new file, no new API, no new dependency, no schema or contract change,
   no data or model addition, and
3. no change to licensing, governance, security, or rights-policy documents.

Anything beyond that threshold requires the CLA. When in doubt, the CLA
applies: de minimis is a convenience for typo-scale fixes, not a licensing
shortcut.
