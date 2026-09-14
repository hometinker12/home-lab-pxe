# home-lab-pxe

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.md) [![Release](https://img.shields.io/badge/release-0.1.1-blue)](VERSION) [![CI](https://github.com/hometinker12/home-lab-pxe/actions/workflows/pxe-smoke.yml/badge.svg?branch=develop)](https://github.com/hometinker12/home-lab-pxe/actions/workflows/pxe-smoke.yml) [![Publish](https://github.com/hometinker12/home-lab-pxe/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/hometinker12/home-lab-pxe/actions/workflows/docker-publish.yml) [![Docker](https://img.shields.io/badge/docker-hometinker12%2Fhome--lab--pxe-blue)](https://hub.docker.com/r/hometinker12/home-lab-pxe) [![Python](https://img.shields.io/badge/python-3.12-green)](https://www.python.org/) [![AI Assisted](https://img.shields.io/badge/AI%20Assisted-yes-blue)](https://cursor.com)

Docker-packaged PXE/iPXE boot server for a home lab. It discovers machines on the LAN, holds **unknown** systems in a wait menu until you act in the web console, and lets **already deployed** systems fall through to local disk. Linux installs get full [cloud-init](https://cloudinit.readthedocs.io/); Windows Server uses `unattend.xml` plus [Cloudbase-Init](https://cloudbase.it/cloudbase-init/). Linux **root** and Windows **local Administrator** credentials are stored encrypted. Changes you stage in the console apply on the **next PXE boot** as a new image.

## Status

| Item | State |
|------|--------|
| Version | `0.1.1` |
| Application | FastAPI console + iPXE/cloud-init/Windows seeds |
| Promotion path | `develop` → `release` → `main` (push to `main` publishes Docker Hub and a GitHub Release) |

## Run locally

```powershell
Copy-Item .env.example .env
python -c "from cryptography.fernet import Fernet; import secrets; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode()); print('SECRET_KEY=' + secrets.token_urlsafe(32))"
# paste those into .env, set ADMIN_PASSWORD, then:
python scripts/host_lan_ipv4.py --write
docker compose up --build
```

Open `http://127.0.0.1:8080/login` or `https://127.0.0.1:8443/login` (self-signed until you upload a PEM cert under Settings → HTTPS). Settings → PXE shows this computer's LAN IPv4 (detected on the host, not the Docker 172.x address). HTTP PXE smoke (no registry push):

```powershell
python scripts/pxe_smoke.py --base-url http://127.0.0.1:8080 --user admin --password <ADMIN_PASSWORD>
```

Published image (after a `main` release):

```powershell
docker pull hometinker12/home-lab-pxe:0.1.1
```

On a Linux lab host, add `network_mode: host` in `docker-compose.override.yml` so DHCP/TFTP see LAN broadcasts. Keep `PXE_BIND_INTERFACE` on the LAN NIC. Do not publish DHCP/TFTP/HTTP-boot to the internet.

## What it does

- Serve DHCP (proxyDHCP by default) and TFTP as **separate** Settings toggles, plus HTTP so BIOS/UEFI clients can iPXE-boot. The PXE settings section includes copy-paste values for an existing LAN DHCP server (next-server / filenames) and extra dnsmasq option lines.
- Serve the operator console on HTTPS (`8443`) with a self-signed certificate created on first start (replace it under **Settings → HTTPS** with a PEM cert and key). Keep `PXE_PUBLIC_URL` as `http://` so iPXE and guest-init are not blocked by that certificate.
- Add machines by MAC from the console, or let unknown hosts register on first iPXE check-in.
- Upload Linux kernels/initrd, Windows WIM files, or ISO images in **Images**, or register paths already on the image volume; existing images can be edited. Linux forms hide WIM fields; Windows forms hide kernel/initrd.
- Skip the menu for known deployed hosts (immediate local-disk boot)
- Deploy Linux images with per-machine cloud-init user-data / meta-data
- Deploy Windows Server with `unattend.xml` and Cloudbase-Init
- Store Linux root and Windows local Administrator usernames/passwords encrypted at rest
- Re-image on the next PXE boot when the console has a staged job

## Tests and CI

- Unit: `python -m pytest` (also `ruff` on `develop` and on the Docker publish workflow)
- Container PXE smoke: GitHub Actions **builds the image on the runner and never `docker push`**. `scripts/pxe_smoke.py` covers `/health`, `/login` brand assets and `/favicon.ico`, `/boot.ipxe`, `/ipxe/{mac}` pending/deploy/deployed/staged, ISO-only `sanboot`, Linux cloud-init, Windows unattend/Cloudbase-Init, phone-home, PXE/DHCP/TFTP/HTTPS settings, manual MAC add, ISO image register, and image edit. On `develop`, `develop commit smoke gate` requires both pytest and that container job.
- Publish: push (or merge) to `main` runs [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml) — tests, Trivy, PXE HTTP smoke, then Docker Hub (`latest`, `0.1.1`, `sha-*`) and a GitHub Release `v0.1.1` when that tag is new.

## Publishing a release

Promotion to production is a manual **`release` → `main`** pull request. Before merging:

1. Bump [`VERSION`](VERSION) (keep `pyproject.toml`, Dockerfile `ARG VERSION`, Compose build-arg, README badge, and version tests in sync).
2. Cut a matching `## [X.Y.Z] - YYYY-MM-DD` section in [`CHANGELOG.md`](CHANGELOG.md) out of `[Unreleased]`.

Push to `main` runs [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml): tests, container smoke, Docker Hub publish (`latest`, `${VERSION}`, `sha-*`), Cosign keyless signing, then a GitHub Release `v${VERSION}` when that tag does not already exist. Release notes are taken from the matching CHANGELOG section (or a short fallback naming the image and commit).

Required GitHub Actions secrets are listed under [GitHub secrets](#github-secrets).

## GitHub secrets

Set these on the repository (**Settings → Secrets and variables → Actions**) before the first `main` publish:

| Secret | Value |
|--------|--------|
| `DOCKERHUB_USERNAME` | Docker Hub username that owns `hometinker12/home-lab-pxe` |
| `DOCKERHUB_TOKEN` | Docker Hub [access token](https://hub.docker.com/settings/security) with permission to push that image |

No Cosign key is stored in GitHub. Signing uses GitHub Actions OIDC (`id-token: write` on the publish job).

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — container, console, and security diagrams
- [`PLAN.md`](PLAN.md) — architecture and implementation plan
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup, tests, PR expectations
- [`SECURITY.md`](SECURITY.md) — private vulnerability reporting
- [`CHANGELOG.md`](CHANGELOG.md) — user-visible changes
- [`.env.example`](.env.example) — environment variables

## License

MIT. See [`LICENSE.md`](LICENSE.md).
