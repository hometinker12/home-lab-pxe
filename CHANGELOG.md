# Changelog

## [Unreleased]

## [0.3.8] - 2026-09-24

### Added

- The cloud-init editor can now change the Ubuntu installer section of autoinstall seeds. A new **Installer (autoinstall)** sidebar group has sections for version and interactive sections, locale, refresh-installer, keyboard, source, network (one card per ethernet interface, with YAML boxes for Wi-Fi, bonds, bridges and VLANs), proxy, apt (fallback, mirror selection and components), storage (layout, sizing policy, disk match, encryption and reset partition), identity, Active Directory, Ubuntu Pro, SSH, codecs, drivers, OEM, snaps, debconf selections, packages, kernel, kernel crash dumps, timezone, updates, shutdown, reporting, and early, late and error commands. Installer keys without a section are kept in **Other installer keys (YAML)** with a notice.
- Early, late and error commands open locked. **Edit** explains which commands home-lab-pxe relies on (imaging callback, phone-home, failure log upload, Wi-Fi quieting, forced reboot, UEFI boot order), which ones are added back when the seed is served, and that the phone-home callback is not. **Unlock and edit** unlocks the list until the editor is closed. Commands home-lab-pxe relies on carry a **Managed** chip.
- List placeholders such as `{{ssh_keys}}` and `{{packages}}` show a chip that says where the value comes from at deploy.

### Changed

- The editor overview lists the configured installer sections, and **User data (cloud-config)** in the installer group links to the cloud-config modules. For autoinstall seeds, Preview shows the whole seed file.
- The default Ubuntu seed now installs with the **direct** storage layout on the largest disk: a GPT table with an EFI system partition (vfat, `/boot/efi`) and an ext4 root, instead of LVM. It is still matched by size, so it works on SATA, NVMe and virtio disks. New images and **Reset to default** use it; existing image and machine seed files keep their layout.
- **Save** on a machine page now only stores changes. It no longer stages a reimage on a deployed machine or marks a pending machine ready; only **Deploy** and **Stage reimage** queue an install. Saving a machine that is already staged still refreshes the staged install with the new seed. Saving a machine's local account no longer stages a reimage either.
- The machine page shows **Lifecycle** directly under **Deployment**. Lifecycle, Guest init and Recent boots open collapsed.
- Every button uses the same size as **Edit folder**, including Log out and the primary (blue) buttons. Colours are unchanged.
- Apply rewrites only the top-level autoinstall keys you changed. The other keys, including multi-line `- |` commands and placeholders, keep their original text. Apply with no changes still leaves the file byte-for-byte unchanged.

### Fixed

- The editor now rejects a literal password in `identity.password` and `storage.layout.password`, in Other installer keys, and on a `password:` line inside a command or script. Save already refused these; the editor now names the field instead.

## [0.3.7] - 2026-09-24

### Added

- Linux machine and image pages keep the raw user-data file. **Editor**, next to Copy Default on a machine, opens the cloud-config editor in a dialog. Apply writes the result back into the file and leaves the autoinstall installer section unchanged. Empty optional keys are omitted.
- The cloud-init editor covers every cloud-config key in the cloud-init 26.2 schema. Modules that were a single YAML box now have real fields: the default `user`, `apt` sources, `groups`, `runcmd`/`bootcmd` in shell or argv form, packages with versions and apt/snap sections, `ssh.emit_keys_to_console`, resolv.conf options, rsyslog configs, Ansible, Puppet, and the disk and network modules. Deprecated keys are hidden unless the seed uses them, and deprecated aliases such as `apt_update` are renamed with a notice.
- A **Next Boot Device** dropdown on the machine page (**PXE** by default, or **Local disk**) sets the UEFI boot order at the end of the next Linux or Windows install. With PXE, IPv4 network boot stays first and the installed OS comes right after it, so the host keeps getting the boot menu. With Local disk, the installed OS boots first and network entries go last. PXE-first is only applied after the server confirms phone-home, so a lost callback cannot loop the host back into the installer. With PXE, Windows first-boot reboots (after specialize and from Cloudbase-Init) wait out the menu countdown. Legacy BIOS installs are unchanged.

### Changed

- The cloud-init editor is laid out as a full-height dialog: a module sidebar with search (`/`), a **Configured only** filter, configured dots and per-group counts; a fixed header; and a footer that keeps Cancel and Apply in view. Lists are edited as cards with move and remove buttons instead of a draft form and a YAML text box. Each module has Form and YAML tabs, a Docs link, Clear, and the documentation example. A Preview column shows the resulting user-data. Narrow screens get a full-screen editor with a module dropdown.
- Closing the editor with unsaved changes (Cancel, Esc, or a click outside) asks first. Apply errors jump to the field that failed.
- Autoinstall seeds show the installer section keys read-only on the editor overview.
- The rest of the console uses the editor's layout: cards with a header and a footer action bar, page headers with the primary action on the right, grouped two-column forms with short key hints next to labels, status pills, icon buttons for row actions, and dashed empty states. Dialogs share one header/footer style. Settings, Boot menu, and Files no longer scroll sideways on phones.

### Fixed

- Opening the cloud-init editor and pressing Apply no longer drops or rewrites values the form could not show: the default `user`, `keyboard.layout`, `apt_pipelining`, string or mapping `groups` and `users`, list or `false` `sudo`, numeric `uid`, argv commands, package versions, resolv.conf option types, `write_files` gzip and `text/plain` encodings, integer file permissions, `growpart` mode `gpart`, `null` mount fields, and `chpasswd` keys. Anything the form cannot represent stays in Other keys (YAML) with a notice.
- The SSH module no longer writes an invalid top-level `emit_keys_to_console`, and the editor no longer offers a `zypper_repos` key.
- Machine and image pages still open when the stored user-data does not parse. Editor API responses are not cached.
- Credential keys (`password`, `passwd`, `hashed_passwd`, `plain_text_passwd`, `chpasswd.users[].password`, legacy `chpasswd.list`) must be placeholders wherever they appear in the editor, including Other keys (YAML), Advanced YAML, and YAML-valued fields. Before, a literal typed there was written to the seed.
- Saving user-data now rejects literal `plain_text_passwd` values and `hashed_passwd` values that are not a crypt hash (`$6$...`). It also checks list-item and quoted forms of credential keys, and credential keys inside flow mappings such as `{name: a, plain_text_passwd: ...}`. The editor uses the same rule: only `hashed_passwd` takes a crypt hash; every other credential key needs a placeholder.
- Apply with no changes leaves the user-data file byte-for-byte unchanged. In autoinstall seeds, an edit rewrites only the `user-data` block, so the installer section keeps its original formatting.

## [0.3.6] - 2026-09-22

### Added

- Boot menu has a collapsed iPXE build section. USB options, including the keyboard driver, rebuild the pinned iPXE tree into `ipxe-custom.efi` and copy it over `ipxe.efi`. The first rebuild keeps the previous file as `ipxe-native.efi`, and Use stock puts that backup back.

### Fixed

- The iPXE rebuild copies the image's pinned source into a private work directory. It does not run `make` on a tree the Files browser can overwrite.
- Ubuntu autoinstall flushes the disk before the forced reboot. The old late-command used sysrq sync, which returns before the EFI partition is written, so Intel NUCs came back with an empty EFI system partition and no boot loader.

## [0.3.5] - 2026-09-22

### Added

- Ubuntu autoinstall `error-commands` post a capped, redacted Subiquity log to the console. The machine moves to **Install failed** (folder menu, no guest-init) instead of waiting out the imaging timer with no reason.
- Console password change under Settings. Changing it signs the current session out.
- Machines list search and state filters, with a short poll while a host is deploying or imaging. Machine detail shows the install log and confirmations for deploy, stage, mark deployed, and abort.
- Abort install stops a deploying, imaging, or staged job and closes guest-init.
- iPXE can report manufacturer, product, and serial. Those show on the machine page.
- Guest-init URLs include the install `instance_id` (`/cloud-init/{id}/{instance_id}/…`, and the same for Windows and Cloudbase-Init). Paths without it return 404.
- Activity labels, links, filter, and pages of 50.

### Changed

- Console sessions last 6 hours of inactivity. Each authenticated request refreshes the cookie.
- Imaging timeout default is 60 minutes (`PXE_IMAGING_TIMEOUT_MINUTES`). Guest-init, install files, and imaging callbacks refresh the clock and are not expired in that same request.
- Save on a machine stores the local account. Deploy requires that password or the Settings imaging default.
- **Staged** is labeled Staged. The machines banner counts unnamed pending hosts only.
- Boot menu timeouts and labels start collapsed. The folder editor is unchanged.
- A live SMBIOS UUID (seen within the last hour) is not stolen by a second MAC. A quiet NIC still remaps. SQLite uses WAL, and each machine has one local account per kind.

### Fixed

- The machines list keeps the iPXE-reported LAN address when Docker publishes the port. A bridge peer such as `172.22.0.1` no longer replaces that address.
- Ubuntu autoinstall no longer stops at “Continue with autoinstall?”. Subiquity’s text client still prints that prompt after apt configuration starts, even when `autoinstall` is already on the kernel command line. The early-command downloads a helper and runs it, so the installer console does not print that program.
- Wrong `ENCRYPTION_KEY` shows “encryption key does not match stored accounts” on the machine page and returns 404 for guest-init.
- iPXE `?ip=` is kept for Docker bridge NAT and ignored when it is not an IP. A direct LAN peer is preferred.
- An assigned image that is still extracting can be submitted again; the console explains that it is not ready.
- Settings imaging-account errors stay on the Accounts section.
- Ubuntu autoinstall unbinds live Wi-Fi NICs in `early-commands` before Subiquity applies netplan. On machines with a wireless card (for example `wlp0s20f3` next to `eno1`), `netplan apply` runs `udevadm settle` while that card is still probing, settle exits 1, and the installer stops with `network_fail`.

## [0.3.4] - 2026-09-18

### Added

- Files nav shows an orange attention count when TFTP iPXE binaries are missing or still the `ipxe-stub` placeholder. Stub badges and the Files banner link to [boot.ipxe.org](https://boot.ipxe.org/) so the matching `ipxe.efi` / `undionly.kpxe` / `snponly.efi` can be downloaded and uploaded.
- Settings lists `ipxe.efi` as the default option 67 / UniFi Network Boot filename for UEFI-only LANs.
- README troubleshooting for Ubuntu casper `Permission denied` when the `pxe-images` volume is already an NFS mount (Ganesha cannot re-export a NAS/SAN Docker volume). Bind a local directory over `/var/lib/pxe/images/nfs`.

### Fixed

- iPXE 2.0 UEFI chainloading no longer stops at `autoexec.ipxe not found`. TFTP writes `autoexec.ipxe` next to `boot.ipxe` (same `GET /ipxe/{mac}` handoff), HTTP serves `/autoexec.ipxe` and `/tftp/autoexec.ipxe` even when the TFTP root is read-only, and proxyDHCP matches user-class `iPXE` as well as option 175 so already-iPXE clients (Proxmox/SeaBIOS) get the HTTP `boot.ipxe` URL instead of `ipxe.efi`.
- Compose sets `seccomp:unconfined` (kept with `no-new-privileges`) so nfs-ganesha can use `open_by_handle_at`. Docker's default seccomp profile blocked that syscall and Ubuntu casper failed with `mount: Operation not permitted` after the export was published.

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
