# Changelog

## [Unreleased]

### Added

- Console PXE lettermark on the header and login page, plus a browser favicon.
- FastAPI PXE control plane: machine inventory, iPXE wait/skip/install policy, Linux cloud-init, Windows unattend + Cloudbase-Init seeds, Fernet local-account vault, and admin console.
- Docker image + Compose stack (dnsmasq optional; HTTP PXE always on).
- `scripts/pxe_smoke.py` and GitHub Actions container smoke that **builds locally and never pushes** an image.
- Repository bootstrap: Cursor rules/agents, GitHub issue/PR templates, contributing and security docs, `PLAN.md`, and `ARCHITECTURE.md`.

### Changed

- License is MIT only (Commons Clause removed).
