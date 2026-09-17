# home-lab-pxe plan

Initial architecture and implementation plan for a Docker-packaged PXE boot server that pushes OS images to machines from a web console.

## 1. Goal

Run one Compose stack on a Linux home-lab host. Clients that network-boot:

1. **Unknown systems** continue to the next boot device after a short timeout. Nothing auto-installs until the host is named in the console (or a named host picks an image from the folder menu).
2. **Named / ready / deployed systems** see an iPXE folder menu with a countdown to local disk. A staged reimage still hijacks the next PXE boot.
3. **Staged console changes** (new image, hostname, users, packages) apply on the **next PXE boot** as a fresh install — not as an in-place SSH/WinRM/config-management push.

Guest initialization in v1:

- **Linux:** full [cloud-init](https://cloudinit.readthedocs.io/) (`user-data`, `meta-data`, `vendor-data` over HTTP).
- **Windows Server:** unattended Setup via `unattend.xml`, then [Cloudbase-Init](https://cloudbase.it/cloudbase-init/) for first-boot customization (the Windows equivalent of cloud-init).

**Local accounts at rest:** Linux **root** and Windows **local Administrator** usernames and passwords are stored **encrypted** (Fernet). They are decrypted only when rendering guest payloads for the current boot. They never appear in iPXE scripts, logs, git, or API/HTML responses.

## 2. Product principles

- **PXE-first boot order** on managed machines. That is what makes “pick this up on next reboot” work without BMC/IPMI in v1.
- **“Skip PXE” means skip the menu**, not “never talk to the server.” Deployed hosts still iPXE-check-in so a staged job can hijack the next boot.
- **Operator is the only deployer.** Discovery never implies consent to wipe a disk.
- **LAN-trust network.** MAC spoofing can impersonate a host; document that. Do not expose DHCP/TFTP/HTTP-boot to the internet.
- **Secrets stay out of iPXE scripts.** Passwords, usernames for privileged local accounts, and SSH keys live in encrypted storage and in per-machine guest payloads, and are never logged.
- **Encrypted at rest.** `ENCRYPTION_KEY` is required in production. Plaintext root/Administrator credentials must not exist in SQLite.

## 3. Non-goals (v1)

- BMC / IPMI / Redfish power control (nice follow-on so the console can reboot a host)
- Being the only DHCP server on the LAN (proxyDHCP is the default)
- Multi-site or HA
- In-place configuration management (Ansible/Salt/WinRM push). Staged changes **reimage**.
- Running the PXE data plane on Docker Desktop for Windows (no usable LAN DHCP/TFTP). Dev of the web app can happen on Windows; the container is meant for a Linux host or VM.
- Domain join / AD / Autopilot (local Administrator only for Windows v1)

## 4. Architecture

Visual diagrams of the container, console, and security model: [`ARCHITECTURE.md`](ARCHITECTURE.md).

### 4.1 Boot stack

| Layer | Choice | Role |
|-------|--------|------|
| DHCP | **dnsmasq** in `proxy` mode by default | Point BIOS/UEFI at iPXE without replacing the existing DHCP server |
| TFTP | dnsmasq | First-stage `ipxe.efi` / `snponly.efi` / `undionly.kpxe` |
| Second stage | **iPXE** | HTTP `chain` to `GET /ipxe/{mac}` so policy is dynamic |
| HTTP | FastAPI (uvicorn) | Admin UI, inventory API, iPXE scripts, guest seeds, kernels/initrd/WIM |
| Inventory | SQLite | Machines, images, staged jobs, **encrypted local accounts**, audit log |

iPXE is required. Classic PXE menus cannot poll the API when an operator clicks Deploy.

### 4.2 Control plane (Python)

Same general shape as other hometinker12 services so Cursor rules stay familiar:

- FastAPI + Jinja2 admin UI
- SQLModel / SQLAlchemy + SQLite
- Session auth + CSRF on browser POSTs
- Fernet (`ENCRYPTION_KEY`) for local-account ciphertext
- Modules: `src/inventory/`, `src/boot/`, `src/cloudinit/`, `src/windows/`, `src/security.py`, `src/routes/`

### 4.3 Container and networking

One image, one Compose service for v1 (dnsmasq + uvicorn via `scripts/entrypoint.sh`).

- **`network_mode: host`** on Linux (DHCP broadcasts do not traverse a user-defined bridge). Default Compose still publishes UDP 67/69/4011 plus HTTP/HTTPS for Docker Desktop; dnsmasq `tftp-single-port` keeps TFTP on 69 through NAT.
- Capabilities: `NET_ADMIN`, `NET_RAW`, and bind to 67/69 — **not** `privileged: true` unless a later milestone proves it is required.
- Volumes: `/var/lib/pxe/data` (SQLite), `/var/lib/pxe/images` (operator-imported payloads), `/var/lib/pxe/tftp` (iPXE binaries), `/var/lib/pxe/ssl` (TLS cert + key).
- Web process runs as non-root after dnsmasq is started; document the split if a single PID 1 supervisor is cleaner.

`PXE_BIND_INTERFACE` selects the LAN NIC so we do not answer DHCP on a WAN or management interface.

### 4.4 DHCP modes

| `PXE_DHCP_MODE` | Behavior |
|-----------------|----------|
| `proxy` (default) | proxyDHCP / `dhcp-range=...,proxy` — existing router/Windows DHCP stays authoritative |
| `authoritative` | dnsmasq owns the range (`PXE_DHCP_RANGE`, router, DNS) |

First-boot defaults come from env. After that, **Settings** in the console is the source of truth (SQLite). **PXE**, **DHCP**, **TFTP**, and **Machines** are separate collapsed sections. PXE shows the Docker **host** LAN IPv4 (`PXE_HOST_LAN_IPV4` in `.env`) plus bind interface, extra allowlisted dnsmasq lines, and copy-paste DHCP options 60/66/67 for an existing LAN DHCP server. DHCP and TFTP each have their own enable toggle. Machines holds the imaging timeout (default 15 minutes; `PXE_IMAGING_TIMEOUT_MINUTES` seeds the first row) and the default IANA timezone for new machines (`PXE_DEFAULT_TIMEZONE`, else UTC). **Files** in the header browses the TFTP, Images, and Data volumes (list/upload/download/delete; paths stay inside those roots). Saving PXE/DHCP/TFTP writes `dnsmasq-pxe.conf` plus `dhcp.enabled` / `tftp.enabled`; `scripts/entrypoint.sh` starts, stops, or reloads dnsmasq when either service is on. Extra option lines are allowlisted (`dhcp-option`, `dhcp-host`, …); `dhcp-script` and `conf-file` are rejected.

## 5. Machine identity and lifecycle

**Identity:** MAC address is primary. SMBIOS UUID (`${uuid}` in iPXE) is secondary. If a known UUID appears with a new MAC (NIC swap), attach the MAC and keep the record.

| State | PXE behavior | How it gets here |
|-------|----------------|------------------|
| `pending` | Timeout then next boot device (no folder menu) | First DHCP/iPXE seen for an unknown MAC, or operator added the MAC in the console |
| `ready` | Folder menu (countdown to disk) | Operator named it / tagged it; no deploy yet |
| `deploying` | Installer + guest init | Operator clicked Deploy or the client picked an OS image |
| `imaging` | Same as deploying | Installer early-command / WinPE startnet |
| `timeout_error` | Folder menu; no guest-init | Imaging longer than Settings timeout (default 15 minutes) |
| `deployed` | Folder menu (countdown to disk) | Installer reported success, or operator marked deployed |
| `staged` | Same as `deploying` on next PXE | Operator saved image/guest-init changes |
| `disabled` | Same as unknown (timeout then disk) | Operator quarantined the MAC |

Unknown → `pending` is automatic. Every other transition is an operator action, a client menu pick, or a callback from the installer (`/api/machines/{id}/events`).

`Image.os_family` is `linux`, `windows`, or `tool`. Boot policy and seed URLs follow that family. Tool images boot from the menu without a deploy.

## 6. Boot policy

`GET /ipxe/{mac}` returns a `#!ipxe` script. Decision order:

1. Unknown / unnamed pending / disabled → insert `pending` if needed, sleep the Boot menu unknown timeout, then `exit` / `sanboot` to the next boot device.
2. Named, `ready`, `deployed`, or `timeout_error` → iPXE folder menu (`GET /ipxe/{mac}/menu/{id}` for nested folders). Default item continues to disk after the menu countdown. `GET /ipxe/{mac}/boot/{image_id}` starts deploy/stage for Linux/Windows, or boots a tool image without changing state.
3. `deploying`, `staged`, or `imaging`:
   - **Linux:** kernel + initrd plus `autoinstall`, `cloud-config-url=` to `/cloud-init/{machine_id}/user-data` on NFS, and `ds=nocloud;s=${PXE_PUBLIC_URL}/cloud-init/{machine_id}/`.
   - **Windows:** iPXE `wimboot` (or equivalent) into WinPE / Setup, with `unattend.xml` from `${PXE_PUBLIC_URL}/windows/{machine_id}/unattend.xml`.

The folder menu is built in the console **Boot menu** page (nested folders, default Windows / Linux / Tools). Extracting images are omitted from the client list.

Installer progress: Linux autoinstall `early-commands` (and WinPE `startnet.cmd`) POST `?event=imaging`. Success: late-command / specialize `phone_home` marks `deployed`. If Imaging lasts longer than the Settings timeout, the machine becomes `timeout_error` (folder menu). Failure otherwise leaves `deploying` or `imaging` and the next PXE retries.

## 7. Images and guest initialization

### 7.1 Image library

Operator-imported artifacts under `PXE_IMAGE_ROOT` (not git). The console can **upload** kernel/initrd/`boot.wim`/`install.wim`/ISO files or register relative paths already on the volume, and **edit** existing image records (metadata, replacement uploads, and the image seed file). Linux image forms hide WIM fields; Windows forms hide kernel/initrd.

A registered ISO is saved immediately. A dedicated extractor process unpacks Ubuntu live-server `casper/` + `.disk/` + `dists/` + `pool/` onto a read-only NFS tree, or a full Windows Server media tree onto SMB. Image status is `idle | queued | extracting | ready | failed`. Managed Ubuntu installs use kernel/initrd plus `netboot=nfs nfsroot=host:/export/{id}/{rev}` (one Ganesha Path per extract directory; rpcbind on TCP/UDP 111) when casper squashfs is present; HTTP `iso-url=` / `url=` remains the fallback. Subiquity autoinstall includes locale, keyboard, storage, source, and apt so the guest does not prompt. Managed Windows installs boot WinPE via `wimboot` and run Setup from an authenticated read-only SMB share. ISO-only `sanboot` remains the fallback when there is no kernel/WIM pair and extraction did not fail.

Each Linux image has a cloud-init **user-data** file; each Windows image has **unattend.xml**. A machine may store its own file of the same kind. Deploy copies the image template onto the machine if that file is empty; **Copy Default** on the machine page overwrites it with the latest image file. A non-empty machine file replaces the image file (no YAML/XML merge). Vault credentials are substituted at serve time through allowlisted `{{placeholders}}`.

- Ubuntu live-server kernel/initrd + autoinstall (first Linux distro)
- Windows Server install WIM + WinPE `boot.wim` (first Windows target: Server 2022 or 2025)
- Later: Debian, generic cloud images, custom squashfs

Each image record: name, `os_family`, architecture (`x86_64` / `aarch64`), kernel/initrd or WIM paths, cmdline/unattend template, guest-init template.

### 7.2 Linux — cloud-init

Per machine, HTTP nocloud (`ds=nocloud;s=` plus `cloud-config-url=` to `user-data` on NFS casper):

- `GET /cloud-init/{machine_id}/user-data`
- `GET /cloud-init/{machine_id}/meta-data`
- `GET /cloud-init/{machine_id}/vendor-data`

These URLs are unauthenticated (installers have no console session) and return **404** unless the machine is `deploying` or `staged`.

`meta-data.instance-id` **must change** when a staged job should re-run cloud-init / reimage.

Rendered `user-data` injects the decrypted Linux **root** username/password (and SSH keys) from the credential vault. Do not leave root password as a plaintext console field stored in SQLite.

### 7.3 Windows Server — unattend.xml + Cloudbase-Init

Two layers, both in v1:

| Layer | Analog | Role |
|-------|--------|------|
| `unattend.xml` | Ubuntu autoinstall | Windows Setup: disk, product key (if any), local Administrator |
| **Cloudbase-Init** | cloud-init | First-boot: hostname, users, WinRM, user-data scripts, later staged jobs |

Per machine:

- `GET /windows/{machine_id}/unattend.xml` — generated at request time; Administrator name/password decrypted into the answer file, never cached on disk in plaintext.
- `GET /cloudbase-init/{machine_id}/` — HTTP metadata/user-data for Cloudbase-Init (NoCloud-style files or the HTTP service Cloudbase-Init expects). Bump instance-id on staged reimage.

Windows images used for deploy **must include Cloudbase-Init** (sysprep’d WIM/template). Raw ISO-only install without Cloudbase-Init can complete Setup via `unattend.xml` but cannot apply later staged guest-init the same way.

Console-editable fields (v1): hostname, FQDN, timezone, network (DHCP or static), SSH/WinRM keys, packages (Linux), optional raw cloud-init or Cloudbase-Init overlay.

Staged job = “on next PXE, install image X with this rendered guest-init.” Saving those fields on a `deployed` host moves it to `staged`.

### 7.4 Credential vault (root + local Administrator)

**What is stored**

| Kind | Typical username | Password |
|------|------------------|----------|
| `linux_root` | `root` (operator may rename) | required |
| `windows_administrator` | `Administrator` (operator may rename) | required |

Both **username and password** are Fernet-encrypted at rest. Optional lab-wide defaults exist at settings level; a machine may override them.

**Crypto**

- Algorithm: Fernet (symmetric) with `ENCRYPTION_KEY` (url-safe base64 32-byte key).
- Required in production; `PXE_ALLOW_INSECURE_DEFAULTS=1` may generate an ephemeral key in tests only.
- Encrypt on write. Decrypt only in memory while rendering cloud-init, `unattend.xml`, or Cloudbase-Init user-data.
- Rotating `ENCRYPTION_KEY` is a documented maintenance operation (re-encrypt all rows); do not dual-write plaintext “for convenience.”

**Console / API**

- Authenticated operators may see the **username** after decrypt.
- **Password is write-only:** set or rotate; never display, never return in JSON, never echo in HTML. Show “set” vs “not set” only.
- Activity log records who changed a local account, not the values.

**Must not**

- Put vault values in iPXE scripts, TFTP files, git, `.env` (except `ENCRYPTION_KEY` itself), or logs.
- Store a second plaintext copy in `StagedJob.user_data_yaml` — jobs hold ciphertext or a reference; render at serve time.

## 8. Web console (v1)

- Login
- **Machines:** last seen, MAC, UUID, IP, state, OS family, assigned image; actions Deploy, Stage reimage, Mark deployed, Disable. Add machine is a popup.
- **Boot menu:** nested iPXE folders, timeouts, image placement and reorder
- **New / pending** highlight so unknown hardware is obvious
- **Images:** import metadata + paths; Linux vs Windows vs tool; add is a popup; edit is disabled while ISO extract is running
- **Machine detail:** hostname and guest-init (IANA timezone dropdown, packages, SSH keys, cloud-init or unattend) share one form; Deploy saves then starts the install. Local account username + password rotate, staged vs applied, recent boot events
- **Settings:** HTTPS certificate (self-signed on first start, or upload PEM cert + key), optional default Linux root and Windows Administrator credentials (encrypted), imaging timeout, default timezone for new machines
- **Activity log:** who deployed what, redacted

## 9. Data model (sketch)

- `Machine` — mac, uuid, hostname, state, last_seen_at, last_ip, assigned_image_id, instance_id
- `Image` — name, os_family (`linux` \| `windows` \| `tool`), arch, `folder_id`, payload paths, cmdline/unattend template
- `BootMenuFolder` / `BootMenuSettings` — nested iPXE menu tree and timeouts
- `LocalAccount` — machine_id (nullable for lab defaults), kind (`linux_root` \| `windows_administrator`), `encrypted_username`, `encrypted_password`
- `StagedJob` — machine_id, image_id, guest_overlay (no plaintext passwords), created_by, created_at, applied_at
- `BootEvent` — machine_id, at, client_ip, script_kind (`unknown_local` / `menu` / `install`)
- `User` / session tables — same pattern as other hometinker12 apps

SQLite only. Idempotent `_migrate_*` helpers in `src/db.py`, no Alembic.

## 10. Security

- Session auth + CSRF on all browser POSTs
- `SECRET_KEY` and `ENCRYPTION_KEY` required in production; `PXE_ALLOW_INSECURE_DEFAULTS=1` is test-only
- Rate-limit login
- Do not put secrets in iPXE text (LAN-readable)
- Redact user-data, unattend, passwords, usernames of privileged accounts, and SSH keys in logs and HTML
- Bind DHCP to `PXE_BIND_INTERFACE`
- Path-safe image/TFTP serving (no `../` escape from `PXE_IMAGE_ROOT`)
- Residual risk: anyone on the LAN who can PXE can also fetch that machine’s guest seed URLs if they spoof the MAC — accept for home lab, keep privileged passwords out of iPXE, document in README

## 11. Testing and CI

| Stage | Branch | Job |
|-------|--------|-----|
| Unit | `develop` | `pytest (ubuntu)` in `.github/workflows/pxe-smoke.yml` (ruff + pytest) |
| Container PXE | `develop` and `release` | `PXE and Docker image smoke` — local `docker build` (never push), Trivy High/Critical, `scripts/pxe_smoke.py` |
| Publish | `main` | `.github/workflows/docker-publish.yml` — pytest, Trivy, PXE HTTP smoke, Docker Hub push, Cosign, GitHub Release. **Not** part of PXE smoke on `develop`/`release`. |

Unit tests mock dnsmasq and image I/O. They must cover: unknown → wait, deployed → local, staged → install, instance-id bump, cloud-init/unattend redaction, Fernet round-trip for `linux_root` and `windows_administrator`, password never present in API/log fixtures.

Promotion: `develop` → `release` after green pytest **and** green container PXE smoke on `develop`, then a `security-reviewer` PASS. Do not promote to `main` as part of the default commit workflow. Container smoke on `develop`/`release` **builds the image on the runner and never `docker push`**. Merging `release` → `main` runs Docker Hub publish.

## 12. Milestones

### M0 — Repo bootstrap

Cursor rules/agents/skill, GitHub templates, CI stub, CONTRIBUTING/SECURITY/LICENSE, README, `.env.example`, this plan, `ARCHITECTURE.md`.

### M1 — App skeleton

`src/` FastAPI app, settings from env, SQLite, login, `/health`, Fernet helpers + `ENCRYPTION_KEY`, Dockerfile + Compose (host network), `requirements-dev.txt`, pytest, Ruff, replace CI bootstrap skips with real jobs, local Trivy script.

**Exit:** app boots; missing `ENCRYPTION_KEY` fails closed outside tests.

### M2 — Discovery and continue-to-disk

dnsmasq + iPXE binaries, machine registration, `/ipxe/{mac}` unknown vs folder menu vs install, machines list in the UI, boot events.

**Exit:** a new VM continues to the next boot device after the unknown timeout; a named or deployed host sees the folder menu (countdown to disk).

### M3 — Linux deploy (Ubuntu first)

Image records, kernel/initrd serving, install iPXE script, cloud-init seed URLs, `LocalAccount` for `linux_root`, deploy action, installer callback → `deployed`.

**Exit:** one Ubuntu VM installs unattended; root password comes from the vault, not plaintext SQLite.

### M4 — Windows Server deploy

WinPE/`wimboot`, `install.wim`, per-machine `unattend.xml`, Cloudbase-Init HTTP seed, `LocalAccount` for `windows_administrator`.

**Exit:** one Windows Server VM installs unattended; local Administrator comes from the vault; Cloudbase-Init runs on first boot.

### M5 — Staged reimage + guest-init editors

Console fields + raw overlay for cloud-init and Cloudbase-Init, `staged` state, instance-id bump, next PXE applies the new image for both OS families.

**Exit:** change hostname or rotate a vault password on a deployed host, reboot, get a new install with those settings.

### M6 — Polish

More distros, image upload UI, better hardware inventory, optional BMC reboot, harden Compose. Docker publish workflow is in `.github/workflows/docker-publish.yml`.

## 13. Open decisions

Resolve during the matching milestone; do not block M0–M2.

| Topic | Default if unspecified | When |
|-------|------------------------|------|
| First Linux distro | Ubuntu 24.04 live-server autoinstall | M3 |
| First Windows SKU | Windows Server 2022 (eval ISO acceptable in lab) | M4 |
| Windows boot mechanism | iPXE `wimboot` + `boot.wim` / `install.wim` | M4 |
| Cloudbase-Init datasource | HTTP service from `/cloudbase-init/{machine_id}/` | M4 |
| How Linux reports success | cloud-init `phone_home` to the API | M3 |
| How Windows reports success | Cloudbase-Init/Setup script POST to the API | M4 |
| Local-disk iPXE command | `exit` then `sanboot --no-describe --drive 0x80` fallback | M2 |
| aarch64 / Raspberry Pi | x86_64 only until M6 | M6 |
| Image import | Paths on the volume first; HTTP upload later | M3–M6 |
| HTTPS for the console | HTTPS on `:8443`; HTTP `:8080` remains for iPXE/guest-init | M6 |

## 14. Success criteria (v1 done)

- Compose up on a Linux host; existing LAN DHCP kept (proxyDHCP).
- Brand-new machine appears as `pending` and continues to disk after the unknown timeout (no install menu).
- Named and deployed machines PXE-check in and see the folder menu unless a deploy is already in progress.
- Ubuntu install completes with operator-supplied cloud-init; **root** username/password stored encrypted.
- Windows Server install completes with `unattend.xml` + Cloudbase-Init; **local Administrator** username/password stored encrypted.
- Staging a new image or guest-init on a deployed host applies on the next PXE boot (Linux and Windows).
- Vault secrets never land in git, logs, iPXE scripts, or API responses; `ENCRYPTION_KEY` is required outside tests.
- `develop` pytest **and** container PXE smoke (local `docker build`, no registry push) are green; `release` container smoke is green; merge to `main` publishes `hometinker12/home-lab-pxe` and a GitHub Release.
