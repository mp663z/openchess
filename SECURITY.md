# Security Policy

## Reporting a vulnerability

Report security vulnerabilities privately through the repository's private
vulnerability reporting on GitHub (see the Contact section of the README).
Do not open a public issue for an unpatched vulnerability. Include: affected version or commit,
reproduction steps, impact, and whether you believe the issue is already
being exploited.

You will receive an acknowledgement within 72 hours and an assessment or
request for more information within 7 days. We credit reporters in release
notes unless you ask to remain anonymous.

## Supported versions

Security fixes are provided for the latest tagged release and for the main
branch. Older tags do not receive backports while the project is in its
pre-release phase; upgrade to the latest tag. The supported-versions rule
tightens to the two most recent minor releases once the project declares a
stable release.

## Scope and disclosure

In scope: the application code in this repository (desktop, server, web,
workers, importers), the CI/release pipeline, and the signed release
artifacts. Out of scope: third-party services you point the software at
(your own model providers, your own infrastructure), vulnerabilities in
dependencies already publicly disclosed (we track and bump; report only if
our pin blocks the fix), and issues requiring physical access.

Coordinated disclosure: we aim to disclose within 90 days of a fix being
available, or sooner if exploitation is observed. We ask reporters not to
disclose before the fix ships; we will not threaten good-faith research
conducted within this policy.

## Hardening notes

- Tagged releases build an SBOM (tools/component_inventory.py) and
  checksums.sha256 in .github/workflows/release.yml; verify artifacts
  before running.
- License and dataset/model rights audits fail closed in CI.
- User data stored locally stays local; telemetry, when added, will be
  opt-in and documented.
