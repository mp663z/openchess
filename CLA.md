# Contributor License Agreement

Thank you for your interest in contributing to this project (the "Project").
This Contributor License Agreement ("CLA") documents the rights you grant to
the Project and its users. It protects contributors, maintainers, and users
by keeping the licensing of every contribution unambiguous. It does not
change your rights to use your own contributions for any other purpose.

## 1. Definitions

"You" (or "Your") means the copyright owner, or the legal entity authorized
by the copyright owner, that submits a Contribution. "Contribution" means
any original work of authorship, including any modification or addition to
an existing work, that You intentionally submit to the Project for inclusion
in, or documentation of, the Project. "Submit" means any form of electronic,
verbal, or written communication sent to the Project or its maintainers,
including pull requests, patches, issues, and mailing-list posts, that is
marked or otherwise designated in writing by You as a Contribution.

## 2. Grant of copyright license

Subject to the terms of this CLA, You grant to the Project's maintainers and
to recipients of software distributed by the Project a perpetual, worldwide,
non-exclusive, no-charge, royalty-free, irrevocable copyright license to
reproduce, prepare derivative works of, publicly display, publicly perform,
sublicense, and distribute Your Contributions and derivative works of them.
The Project will sublicense Contributions only under AGPL-3.0-or-later (the
Project's outbound license). This CLA grants no right to relicense
Contributions under any other license.

## 3. Grant of patent license

Subject to the terms of this CLA, You grant to the Project's maintainers and
to recipients of software distributed by the Project a perpetual, worldwide,
non-exclusive, no-charge, royalty-free, irrevocable (except as stated below)
patent license to make, have made, use, offer to sell, sell, import, and
otherwise transfer Your Contributions, alone or in combination with the
Project, where that license applies only to patent claims licensable by You
that are necessarily infringed by Your Contribution(s) alone or by
combination with the Project. If any entity institutes patent litigation
against You or any other entity (including a cross-claim or counterclaim)
alleging that Your Contribution, or the Project, constitutes direct or
contributory patent infringement, then any patent licenses granted to that
entity under this CLA for that Contribution or the Project terminate as of
the date such litigation is filed.

## 4. Representations

You represent that:

1. each of Your Contributions is an original creation and You are legally
   entitled to submit it and to grant this CLA;
2. if Your employer has rights to intellectual property You create, You have
   received permission to submit the Contribution on its behalf, or Your
   employer has waived such rights;
3. each of Your Contributions is submitted without any expectation of
   support, warranty, or indemnity, all of which are disclaimed to the
   extent permitted by law;
4. You will identify to the Project any third-party code, dataset, or model
   included in Your Contribution, with its license, so the Project's
   license audit can classify it (fail closed: unidentified third-party
   material is not merged).

## 5. No obligation

You are not expected to provide support for Your Contributions, except to
the extent You desire to provide it. Unless required by applicable law or
agreed to in writing, You provide Your Contributions on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.

## 6. Outbound license (AGPL)

The Project is licensed under AGPL-3.0-or-later. Contributions are accepted
on the understanding that they will be distributed under that license. By
submitting a Contribution You agree that the Project may include it under
AGPL-3.0-or-later and that network users of the software will receive the
Corresponding Source, consistent with the NOTICE file.

## 7. Acceptance

You accept this CLA by stating your acceptance in your first pull request
("I accept the CLA in CLA.md"). Before that pull request merges, a
maintainer records your GitHub handle, acceptance timestamp, and the pull
request containing your acceptance statement in data/cla-acceptances.yaml
on the base branch. The pull request CI gate (tools/cla_check.py) reads the
PR author from the trusted GitHub event payload, classifies the change
against the de minimis bounds in DCO.md from the actual diff, and fails a
CLA-required change whose author has no registry entry loaded from the
trusted base ref - an entry added inside the pull request itself does not
count. The gate verifies each referenced pull request through the GitHub
API: it must be authored by the registered handle and contain the exact
acceptance statement in its body or in a comment by that author.
