# Changelog

## [Unreleased]

## [0.2.0] - 2026-09-17

### Changed

- Compose publishes UDP 67 (DHCP), 69 (TFTP), and 4011 (proxyDHCP) alongside HTTP/HTTPS. dnsmasq uses `tftp-single-port` so TFTP transfers work through Docker port mapping.
- TFTP now includes a generated `boot.ipxe` chain script. Settings lists it as the DHCP filename for clients that already run iPXE (Proxmox/SeaBIOS); those clients cannot execute `ipxe.efi`.
- Ubuntu live-server ISO import extracts `casper/` and `.disk/` under `images/nfs/{id}/{rev}` and boots with `netboot=nfs nfsroot=host:/export/{id}/{rev}` so the guest does not wget the ISO into RAM. After each extract, Ganesha publishes that directory as its own NFSv3 Path (casper mounts the export root where `casper/` lives). Casper's `nfsmount` looks up mountd via rpcbind on port 111 (Compose publishes TCP/UDP 111, 2049, and 20048). HTTP `iso-url=` / `url=` remains the fallback when squashfs is missing. The uploaded ISO is deleted after a successful NFS or Windows media extract.
- Machine hostname lives under Actions (below the image selector). Saving guest-init on an existing host now persists the hostname on the machine record, not only in the overlay JSON.
- Files favorites jump to iPXE boot files, image uploads, NFS extracts, SMB media, and machine seeds. The browser can open the TFTP, Images, and Data volumes (paths stay confined to those roots). A single click opens a folder; the System iPXE file list is removed from the sidebar.

### Added

- Background ISO import: Ubuntu live-server extracts casper kernel/initrd; Windows Server publishes Setup media for WinPE. Image rows show queued/extracting/ready/failed.
- Editable cloud-init user-data (Ubuntu) and unattend.xml (Windows) on each image, with optional per-machine replacement files and `{{placeholder}}` vault substitution at serve time.
- Ubuntu HTTP ISO autoinstall (`url=` + escaped nocloud-net) and Windows WinPE startup that maps an authenticated read-only SMB share on Linux host networking.
- Image delete, ISO upload progress overlay, and Linux kernel/initrd fields collapsed under Advanced Settings after the ISO path.
- Delete a machine from the inventory list or its detail page. Related boot events, staged jobs, install attempts, local-account ciphertext, and seed files are removed.

## [0.1.2] - 2026-09-16

### Added

- **Files** in the header browses the TFTP volume (list, upload, download, delete) with stub warnings for placeholder iPXE binaries. TFTP enable stays in Settings.

### Changed

- `docker-compose.yml` always pulls `hometinker12/home-lab-pxe:latest` from Docker Hub instead of building a local image.
- Host LAN IPv4 is set with `PXE_HOST_LAN_IPV4` in `.env`. `scripts/host_lan_ipv4.py` and `.host-lan-ip.env` are removed.
- Settings → PXE explains external DHCP options 60 (PXEClient), 66 (next-server), and 67 (boot file), including when option 60 is required.
- Console pages share a tighter design system (nav, tables, forms); Files uses a file-manager layout for the TFTP volume.

### Removed

- `scripts/host_lan_ipv4.py` (auto-detect host LAN IPv4 for Compose).

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
