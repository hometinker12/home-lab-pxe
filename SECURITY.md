# Security Policy

## Supported versions

Security fixes are applied on the active development and release lines:

| Version / branch | Supported |
| ---------------- | --------- |
| Latest release on `main` | Yes |
| `release` (integration candidate) | Yes, for confirmed vulnerabilities |
| `develop` | Yes, as the primary fix landing branch |
| Older published tags | Best effort only |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Prefer one of these private channels:

1. **GitHub Security Advisories** (preferred): use
   [Report a vulnerability](../../security/advisories/new) on this repository
   if private vulnerability reporting is enabled.
2. **Private maintainer contact**: message
   [@hometinker12](https://github.com/hometinker12) on GitHub and note that you
   have a security report for home-lab-pxe.

Please include:

- Affected version, commit SHA, or branch
- Description of the issue and impact
- Steps to reproduce, or a minimal proof of concept
- Whether you are aware of public disclosure or active exploitation
- Your preferred credit name (or a request to remain anonymous)

## Scope

High-priority areas for this project include:

- Authentication and session handling
- CSRF protection for browser form posts
- Secret handling (`SECRET_KEY`, `ENCRYPTION_KEY`, TLS private keys, cloud-init / unattend / Cloudbase-Init payloads, SSH keys, local root/Administrator accounts)
- Logging that might leak credentials or user-data
- Boot-policy abuse (forcing a reimage, skipping the unknown-host wait menu)
- Path traversal in image / TFTP / HTTP boot file serving
- Container hardening (privilege, host network, exposed ports)

Out of scope unless there is a clear project-specific impact:

- Issues that require already-compromised admin credentials
- MAC spoofing on a trusted home-lab LAN (documented residual risk)
- Reports that depend only on insecure local test flags
  (`PXE_ALLOW_INSECURE_DEFAULTS`)

## What to expect

- Acknowledgement when practical, typically within a few days
- An initial severity assessment and next steps
- A coordinated fix on `develop`, then promotion through `release` as needed
- Credit in release notes or the advisory when you want attribution

Please avoid public disclosure until a fix is available or the maintainers agree
on a disclosure date.

## Safe local testing

When validating fixes locally:

- Use disposable credentials and non-production machines
- Never commit `.env`, certificates, SSH private keys, real cloud-init user-data, or unattend files with passwords
- Prefer mocked DHCP/TFTP tests under `tests/` over live mutation of lab hosts
