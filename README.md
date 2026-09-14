# home-lab-pxe

Docker-packaged PXE/iPXE boot server for a home lab. It discovers machines on the LAN, holds **unknown** systems in a wait menu until you act in the web console, and lets **already deployed** systems fall through to local disk. Linux installs get full [cloud-init](https://cloudinit.readthedocs.io/); Windows Server uses `unattend.xml` plus [Cloudbase-Init](https://cloudbase.it/cloudbase-init/). Linux **root** and Windows **local Administrator** credentials are stored encrypted. Changes you stage in the console apply on the **next PXE boot** as a new image.

## Status

| Item | State |
|------|--------|
| Version | `0.1.0` |
| Application | FastAPI console + iPXE/cloud-init/Windows seeds |
| Promotion path | `develop` → `release` (Docker **publish** from `main` is a separate manual step) |

## Run locally

```powershell
Copy-Item .env.example .env
python -c "from cryptography.fernet import Fernet; import secrets; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode()); print('SECRET_KEY=' + secrets.token_urlsafe(32))"
# paste those into .env, set ADMIN_PASSWORD, then:
docker compose up --build
```

Open `http://127.0.0.1:8080/login`. HTTP PXE smoke (no registry push):

```powershell
python scripts/pxe_smoke.py --base-url http://127.0.0.1:8080 --user admin --password <ADMIN_PASSWORD>
```

On a Linux lab host, add `network_mode: host` in `docker-compose.override.yml` so DHCP/TFTP see LAN broadcasts. Keep `PXE_BIND_INTERFACE` on the LAN NIC. Do not publish DHCP/TFTP/HTTP-boot to the internet.

## What it does

- Serve DHCP (proxyDHCP by default) + TFTP + HTTP so BIOS/UEFI clients can iPXE-boot
- Register new MAC/UUID pairs and keep them on a polling wait menu
- Skip the menu for known deployed hosts (immediate local-disk boot)
- Deploy Linux images with per-machine cloud-init user-data / meta-data
- Deploy Windows Server with `unattend.xml` and Cloudbase-Init
- Store Linux root and Windows local Administrator usernames/passwords encrypted at rest
- Re-image on the next PXE boot when the console has a staged job

## Tests and CI

- Unit: `python -m pytest` (also `ruff` on `develop`)
- Container PXE smoke: GitHub Actions **builds the image on the runner and never `docker push`**. `scripts/pxe_smoke.py` covers `/health`, `/boot.ipxe`, `/ipxe/{mac}` pending/deploy/deployed/staged, Linux cloud-init, Windows unattend/Cloudbase-Init, and phone-home. On `develop`, `develop commit smoke gate` requires both pytest and that container job.

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — container, console, and security diagrams
- [`PLAN.md`](PLAN.md) — architecture and implementation plan
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup, tests, PR expectations
- [`SECURITY.md`](SECURITY.md) — private vulnerability reporting
- [`CHANGELOG.md`](CHANGELOG.md) — user-visible changes
- [`.env.example`](.env.example) — environment variables

## License

MIT. See [`LICENSE.md`](LICENSE.md).
