# home-lab-pxe

Docker-packaged PXE/iPXE boot server for a home lab. It discovers machines on the LAN, holds **unknown** systems in a wait menu until you act in the web console, and lets **already deployed** systems fall through to local disk. Linux installs get full [cloud-init](https://cloudinit.readthedocs.io/); Windows Server uses `unattend.xml` plus [Cloudbase-Init](https://cloudbase.it/cloudbase-init/). Linux **root** and Windows **local Administrator** credentials are stored encrypted. Changes you stage in the console apply on the **next PXE boot** as a new image.

This repository is in the bootstrap phase. Architecture diagrams live in [`ARCHITECTURE.md`](ARCHITECTURE.md). Boot policy and milestones live in [`PLAN.md`](PLAN.md).

## Status

| Item | State |
|------|--------|
| Version | `0.0.1` (planning) |
| Application source | Not started — see milestones in `PLAN.md` |
| Promotion path | `develop` → `release` (Docker publish from `main` is manual) |

## What it will do

- Serve DHCP (proxyDHCP by default) + TFTP + HTTP so BIOS/UEFI clients can iPXE-boot
- Register new MAC/UUID pairs and keep them on a polling wait menu
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
- [`.env.example`](.env.example) — planned environment variables

## License

MIT. See [`LICENSE.md`](LICENSE.md).
