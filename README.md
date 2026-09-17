# home-lab-pxe

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.md) [![Release](https://img.shields.io/badge/release-0.2.0-blue)](VERSION) [![CI](https://github.com/hometinker12/home-lab-pxe/actions/workflows/pxe-smoke.yml/badge.svg?branch=develop)](https://github.com/hometinker12/home-lab-pxe/actions/workflows/pxe-smoke.yml) [![Publish](https://github.com/hometinker12/home-lab-pxe/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/hometinker12/home-lab-pxe/actions/workflows/docker-publish.yml) [![Docker](https://img.shields.io/badge/docker-hometinker12%2Fhome--lab--pxe-blue)](https://hub.docker.com/r/hometinker12/home-lab-pxe) [![Python](https://img.shields.io/badge/python-3.12-green)](https://www.python.org/) [![AI Assisted](https://img.shields.io/badge/AI%20Assisted-yes-blue)](https://cursor.com)

Docker-packaged PXE/iPXE boot server for a home lab. It discovers machines on the LAN, holds **unknown** systems in a wait menu until you act in the web console, and lets **already deployed** systems fall through to local disk. Linux installs get full [cloud-init](https://cloudinit.readthedocs.io/); Windows Server uses `unattend.xml` plus [Cloudbase-Init](https://cloudbase.it/cloudbase-init/). Linux **root** and Windows **local Administrator** credentials are stored encrypted. Changes you stage in the console apply on the **next PXE boot** as a new image.

## Run locally

```powershell
Copy-Item .env.example .env
python -c "from cryptography.fernet import Fernet; import secrets; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode()); print('SECRET_KEY=' + secrets.token_urlsafe(32))"
# paste those into .env, set ADMIN_PASSWORD and PXE_HOST_LAN_IPV4 (this computer's LAN IPv4), then:
docker compose up
```

Compose always pulls [`hometinker12/home-lab-pxe:latest`](https://hub.docker.com/r/hometinker12/home-lab-pxe) from Docker Hub. Open `http://127.0.0.1:8080/login` or `https://127.0.0.1:8443/login` (self-signed until you upload a PEM cert under Settings → HTTPS). Settings → PXE shows `PXE_HOST_LAN_IPV4`. HTTP PXE smoke (no registry push):

```powershell
python scripts/pxe_smoke.py --base-url http://127.0.0.1:8080 --user admin --password <ADMIN_PASSWORD>
```

Compose publishes HTTP `8080`, HTTPS `8443`, UDP `67` / `69` / `4011` (DHCP, TFTP, proxyDHCP), and NFS `111` / `2049` / `20048`. On a Linux lab host, add `network_mode: host` in `docker-compose.override.yml` so DHCP broadcasts, TFTP, SMB, and NFS see the LAN without NAT. Keep `PXE_BIND_INTERFACE` on the LAN NIC. Set `PXE_SMB_PASSWORD` in `.env` for Windows Setup media. Do not publish DHCP/TFTP/HTTP-boot to the internet.

## What it does

- Serve DHCP (proxyDHCP by default) and TFTP as **separate** Settings toggles, plus HTTP so BIOS/UEFI clients can iPXE-boot. The PXE settings section includes copy-paste DHCP options 60 (`PXEClient`), 66 (next-server), and 67 (boot file) for an existing LAN DHCP server, plus extra dnsmasq option lines. **Files** in the console browses TFTP iPXE binaries, image upload/extract folders (NFS casper, SMB Setup media), and machine seed files.
- Serve the operator console on HTTPS (`8443`) with a self-signed certificate created on first start (replace it under **Settings → HTTPS** with a PEM cert and key). Keep `PXE_PUBLIC_URL` as `http://` so iPXE and guest-init are not blocked by that certificate.
- Add machines by MAC from the console, or let unknown hosts register on first iPXE check-in. You can delete a machine from the list or its detail page; the next PXE boot with that MAC registers as unknown again.
- Upload Linux kernels/initrd, Windows WIM files, or Ubuntu/Windows Server ISO images in **Images** (kernel/initrd paths sit under Advanced Settings). ISO import shows upload progress, then returns to the image list while a background worker extracts netboot payloads. After a successful NFS (Ubuntu casper) or SMB (Windows Setup) extract, the uploaded ISO is deleted. You can delete an image from the list or its edit page. Each Linux image has editable cloud-init user-data; each Windows image has editable `unattend.xml`. A machine may override that file. Ubuntu live-server installs extract `casper/`, `.disk/`, `dists/`, and `pool/` into `images/nfs/{id}/{rev}` and boot unattended with `netboot=nfs` (each extract is its own NFS export so the installer mounts squashfs instead of wget-ing the ISO into RAM). HTTP `iso-url=` remains the fallback when squashfs is missing. Windows Setup maps an authenticated read-only SMB share (Linux `network_mode: host` — Docker Desktop cannot publish LAN TCP 445). Stock Windows ISOs do not include Cloudbase-Init.
- Skip the menu for known deployed hosts (immediate local-disk boot)
- Deploy Linux images with per-machine cloud-init user-data / meta-data
- Deploy Windows Server with `unattend.xml` and Cloudbase-Init
- Store Linux root and Windows local Administrator usernames/passwords encrypted at rest
- Re-image on the next PXE boot when the console has a staged job

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — container, console, and security diagrams
- [`PLAN.md`](PLAN.md) — architecture and implementation plan
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup, tests, PR expectations
- [`SECURITY.md`](SECURITY.md) — private vulnerability reporting
- [`CHANGELOG.md`](CHANGELOG.md) — user-visible changes
- [`.env.example`](.env.example) — environment variables

## License

MIT. See [`LICENSE.md`](LICENSE.md).
