# Changelog

## [Unreleased]

## [0.3.3] - 2026-09-18

### Added

- Settings → Windows installation media (SMB) can rotate the `pxe-media` share password. The secret is write-only (never shown), stored encrypted, and applied to Samba. `PXE_SMB_PASSWORD` remains the first-boot default.
- Settings shows an orange attention count in the header when the SMB password or imaging default local/root account is unset. Matching sections are marked.

### Changed

- Renamed **Lab default local accounts** to **Imaging default local/root account** and moved it to the top of Settings, with **Machines** second.

### Fixed

- Ubuntu autoinstall no longer emits empty `ssh_authorized_keys: []` / `authorized-keys: []` when no SSH keys are set. Subiquity treated that as a schema error (`[] is too short`) and dropped to a shell.
- Imaging and phone-home wget callbacks POST an empty body with `--post-file=/dev/null` instead of `--post-data=` (which some wget builds treat as a bad argument).
- Add image closes the popup and shows the ISO upload progress bar. The add dialog stayed in front of the overlay, so the form never left the screen during the upload.

## [0.3.2] - 2026-09-17

### Added

- Edit folder can change the parent folder, with **Root** listed first as the top of the tree.
- After ISO extract, Images → Edit lists Ubuntu `casper/install-sources.yaml` IDs (for example `ubuntu-server-minimal`) and Windows `install.wim` editions under **Install source**. Cloud-init `source.id` and unattend `/IMAGE/NAME` use `{{source_id}}`; Windows also keeps `/IMAGE/INDEX` as `{{wim_index}}` from the same catalog.

### Changed

- Image edit keeps Advanced Settings collapsed. ISO path is a disabled current-path field with Choose file on the right to upload a replacement.
- README is a short product intro, step-by-step install with the full environment reference, and a console user guide with screenshots of each tab.

### Fixed

- Boot menu folder clicks, up/down actions, and edit/move popups keep the current scroll position instead of jumping to the top of the page.

## [0.3.1] - 2026-09-17

### Added

- **Boot menu** console page (header nav) to edit nested iPXE folders, menu title, and timeouts. First boot seeds Windows, Linux, and Tools. Images must belong to a folder. The last remaining folder cannot be deleted.
- Tool `os_family` for utility kernels/ISOs that boot from the client menu without deploying.

### Changed

- Unknown and disabled machines no longer poll a wait menu. They sleep the Boot menu unknown/disabled timeout (default 5s, 0 is immediate) and continue to the next boot device.
- Named, ready, deployed, and timeout-error hosts see an iPXE folder menu with a countdown continue-to-disk default. Selecting a Linux or Windows image starts deploy/stage from the client. Staged/imaging installs still skip the menu.
- Add image is a popup next to the Images heading, matching Add machine. Edit stays disabled while ISO extraction is queued or running. The add popup no longer includes the long ISO/NFS extract explainer.
- Boot menu folder rename/delete is a popup (Delete stays disabled while the folder has images or nested folders). Folder up/down controls sit on the tree. Images can be moved to another folder from a popup listed in menu order.

### Fixed

- Starting the container with a read-only rootfs no longer crashes when TFTP cannot receive the generated `boot.ipxe` chain script. HTTP still serves `/boot.ipxe`. Samba passdb, cache, and `/run` are initialized so SMB, nfs-ganesha, and dnsmasq can start when those paths are empty tmpfs.

## [0.3.0] - 2026-09-17

### Changed

- Ubuntu live-server autoinstall now fills locale, keyboard, network, storage, source, and apt so Subiquity does not prompt. NFS extracts also include `dists/` and `pool/` so `file:/cdrom` apt has a Release file; re-upload the ISO to refresh an existing casper-only extract.
- NFS casper boots fetch autoinstall from HTTP `cloud-config-url=${seed-url}user-data` instead of `/dev/null`, and use `ds=nocloud` so Subiquity actually consumes the seed. Redeploy keeps only the current `data/install-seeds/{machine}/{instance}` snapshot.
- Linux iPXE now puts the bare `autoinstall` token first on the kernel command line (and again before `---`) so Subiquity does not stop at “Continue with autoinstall?”.
- Machine console status is **Deploying** (image assigned), **Imaging** (installer early-command / WinPE start), **Deployed** (phone-home), **Timeout Error** (imaging past the Settings timeout), or **Disabled** (operator). Ubuntu autoinstall `early-commands` and WinPE `startnet.cmd` POST `?event=imaging` like the existing phone-home wget.
- Ubuntu autoinstall Netplan catch-all NICs (`en*` / `eth*`) now set `optional: true` so first boot does not stall in `cloud-init-network.service`.
- Ubuntu autoinstall drops invalid root `timezone`, adds a Subiquity `identity` block, and keeps `{{placeholders}}` unquoted because the console fills them before the guest sees the file.
- Default Ubuntu user-data now interpolates `{{ssh_keys}}` / `{{packages}}`, leaves ISO apt sources intact for offline NFS installs, omits a pinned `ubuntu-server-minimal` source id, and treats imaging/phone-home wget failures as non-fatal. Subiquity `identity.username` of `root` is remapped to `ubuntu` (root password still applied via cloud-init). LVM uses `sizing-policy: all` on the largest disk.
- Add machine is a popup next to the Machines heading instead of a form on the inventory list.
- The Machines inventory table has a **Refresh** control in the header so you can reload status without leaving the page.
- Ubuntu autoinstall force-reboots after phone-home (`sysrq` + casper `noprompt`/`quickreboot`/`reboot=force`) so NFS installs do not hang on a blank cursor waiting to unmount the live media.

### Added

- Settings → Machines imaging timeout (default 15 minutes, 0 disables). A machine that stays **Imaging** that long moves to **Timeout Error**, drops guest-init, and returns to the wait menu until you Deploy again.
- Deploy copies the image cloud-init / unattend template into the machine Guest init file when that file is empty. **Copy Default** next to Machine user-data pulls the latest image template.
- Deploy on a machine saves hostname, timezone, packages, SSH keys, and the seed file from the same form before starting the install, so you do not have to Save first.
- Settings → Machines default IANA timezone (first-boot `PXE_DEFAULT_TIMEZONE`, else UTC). New and discovered machines inherit it; the machine timezone field is a dropdown.

## [0.2.0] - 2026-09-17

### Changed

- Compose publishes UDP 67 (DHCP), 69 (TFTP), and 4011 (proxyDHCP) alongside HTTP/HTTPS. dnsmasq uses `tftp-single-port` so TFTP transfers work through Docker port mapping.
- TFTP now includes a generated `boot.ipxe` chain script. Settings lists it as the DHCP filename for clients that already run iPXE (Proxmox/SeaBIOS); those clients cannot execute `ipxe.efi`.
- Ubuntu live-server ISO import extracts `casper/` and `.disk/` under `images/nfs/{id}/{rev}` and boots with `netboot=nfs nfsroot=host:/export/{id}/{rev}` so the guest does not wget the ISO into RAM. After each extract, Ganesha publishes that directory as its own NFSv3 Path (casper mounts the export root where `casper/` lives). Casper AUTH_NULL is allowed; all NFS UIDs squash to nobody. Casper's `nfsmount` looks up mountd via rpcbind on port 111 (Compose publishes TCP/UDP 111, 2049, and 20048). HTTP `iso-url=` / `url=` remains the fallback when squashfs is missing. The uploaded ISO is deleted after a successful NFS or Windows media extract.
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
