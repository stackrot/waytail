# Security Policy

## Supported Versions

Security fixes target the latest released version of Waytail. Older releases
are not maintained separately; users should upgrade to the latest release.

## Reporting a Vulnerability

Report suspected vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/stackrot/waytail/security/advisories/new).
Do not disclose vulnerabilities in public issues, discussions or pull requests.

Include, where relevant:

- The affected Waytail version or commit.
- Steps to reproduce the issue and a minimal proof of concept.
- The expected and observed behaviour, and the potential security impact.
- Your Linux distribution and Python, Tailscale, GTK, Hyprland and Waybar versions.

Remove credentials, authentication links and unrelated personal or tailnet data
from logs and screenshots. Use test data where possible.

Reports are reviewed on a best-effort basis, with no guaranteed response or
resolution time. Follow-up questions and progress updates are shared through
the private report. If a vulnerability is confirmed, the maintainer will
coordinate a fix and disclosure with the reporter. If it is not accepted as a
security issue, the reason will be explained in the private report.

## Scope

This policy covers Waytail's code, packaging and desktop integration.
Vulnerabilities in Tailscale, GTK, Hyprland, Waybar or other dependencies should
be reported through the affected upstream project's security process. If you
are unsure whether the issue originates in Waytail, use the private report.
