# Changelog

## [Unreleased]

## [0.1.1] - 2026-09-14

### Added

- Console PXE lettermark on the header and login page, plus a browser favicon.
- Settings → PXE shows host LAN IPv4, container IPv4, and advertised PXE URL `http://<lan>:8080/boot.ipxe`. Detect the LAN address on the host with `python scripts/host_lan_ipv4.py --write` (`PXE_HOST_LAN_IPV4`).
- HTTPS console on port 8443 with a dedicated `pxe-ssl` volume. First start writes a self-signed certificate; Settings can replace it with a PEM cert and key.
- FastAPI PXE control plane: machine inventory, iPXE wait/skip/install policy, Linux cloud-init, Windows unattend + Cloudbase-Init seeds, Fernet local-account vault, and admin console.
- Console DHCP controls: enable/disable the in-container dnsmasq helper, edit mode/interface/range/router/DNS, and add validated extra dhcp-option lines.
- Image library: multipart upload of kernel/initrd/WIM payloads plus edit of existing image metadata and files.
- Manual machine registration by MAC, ISO image upload/register, and OS-specific image form fields (Linux hides WIM; Windows hides kernel/initrd).
- Guest-init HTTP (cloud-init, unattend, Cloudbase-Init) is served only while a machine is `deploying` or `staged`. Pending and deployed hosts return 404 so vault passwords are not enumerable by id.
- Settings sections are collapsed by default. PXE includes external DHCP next-server/filename hints; DHCP and TFTP are separate toggles.
- Docker image + Compose stack (dnsmasq optional; HTTP PXE always on). TFTP iPXE files are stubbed at image build when boot.ipxe.org downloads fail, so a read-only rootfs can start.
- `scripts/pxe_smoke.py` and GitHub Actions container smoke that **builds locally and never pushes** an image.
- Docker Hub publish on push to `main`: tests, Trivy, PXE HTTP smoke, multi-arch image (`latest`, version, `sha-*`), Cosign keyless signature, and a GitHub Release `v*` when the tag is new.
- README status badges (license, release, CI, publish, Docker, Python, AI assisted).
- Repository bootstrap: Cursor rules/agents, GitHub issue/PR templates, contributing and security docs, `PLAN.md`, and `ARCHITECTURE.md`.

### Changed

- License is MIT only (Commons Clause removed).
- DHCP Settings hide range, router, and DNS unless mode is authoritative (proxyDHCP does not own LAN leases).
