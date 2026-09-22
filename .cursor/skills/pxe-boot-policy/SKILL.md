---
name: pxe-boot-policy
description: Implement or change PXE/iPXE boot policy, machine lifecycle, guest-init (cloud-init / Cloudbase-Init), and encrypted local accounts in home-lab-pxe. Use when touching src/boot/, src/cloudinit/, src/windows/, src/inventory/, iPXE scripts, or deploy/reimage flows.
---

# PXE boot policy

## Decision order

For every DHCP/iPXE request, resolve the machine then emit **one** script:

1. **Unknown** (no MAC/UUID in inventory, or unnamed pending) → register as `pending`, continue to the next boot device after the Boot menu unknown timeout. Never install.
2. **Disabled** → same as unknown (no folder menu, no client-initiated install).
3. **Pending / ready** with a hostname (or Ready / Deployed / Timeout Error / Install failed) and no in-progress install → serve the operator-built iPXE folder menu. Default item continues to disk after the menu countdown. Selecting a Linux/Windows image deploys or stages that image. Tool images boot without changing lifecycle.
4. **Deploying** or **staged reimage** → serve the assigned image:
   - Linux: kernel/initrd plus cloud-init seed URL. Ubuntu live-server with extracted casper squashfs uses `netboot=nfs nfsroot=host:/export/{id}/{rev}` and `NFSOPTS=vers=3,tcp,port=2049` (HTTP `iso-url=` is fallback). Casper `nfsmount` still needs rpcbind on TCP/UDP 111 for mountd. After each ISO extract, Ganesha gets a dedicated EXPORT Path at that generation directory so MNT is not a subdirectory of a parent export. NFS cmdline must pass `autoinstall` plus `cloud-config-url=${seed-url}user-data` (never `/dev/null` on NFS — that blanks casper cloud-config and Subiquity stays interactive). Use `ds=nocloud;s=` for the nocloud directory. Autoinstall user-data must cover locale/keyboard/source/storage/network/apt/`identity` so Subiquity stays non-interactive; `source.id` comes from `casper/install-sources.yaml` via `{{source_id}}`. Timezone lives under cloud-init `user-data`, not the autoinstall root. Extract `dists/` + `pool/` with casper so apt is not an empty `file:/cdrom`. Autoinstall `early-commands` unbind live Wi-Fi NICs (`phy80211`) before Subiquity runs `netplan apply` — an unconfigured `wlp*` card keeps `udevadm settle` from finishing and aborts the install — then POST `{{imaging_url}}` (`/api/machines/{id}/events?event=imaging`); `late-commands` POST `{{phone_home_url}}` for deployed.
   - Windows: wimboot/WinPE plus `unattend.xml` URL; Cloudbase-Init seed for first boot. Install editions are read from `install.wim` XML (`{{source_id}}` / `{{wim_index}}`). WinPE `startnet.cmd` POSTs imaging; specialize/phone_home marks deployed.
5. **Imaging** (installer reported start) → keep serving the install script and guest-init until phone-home or the imaging timeout. Console shows **Imaging**.
6. **Timeout Error** (Imaging longer than Settings → Machines timeout, default 60 minutes) → folder menu if the host is named/managed; guest-init 404. Operator Deploy or a client menu pick retries. 0 minutes disables the timer. Guest-init, install-file, and imaging callbacks refresh `imaging_started_at` and must not expire the machine in that same request.
7. **Install failed** (Ubuntu `error-commands` posted a redacted log) → same PXE behavior as Timeout Error, with badge **Install failed**. Distinct from the imaging timer.
8. **Deployed** with no staged job → folder menu (countdown to local disk), not an immediate silent skip.
9. **Disabled** (operator) → unknown path (timeout then next boot device).

Identity: MAC is primary; SMBIOS UUID is secondary. If UUID matches a known machine but MAC changed, attach the new MAC only when that machine has not been seen in the last hour. A live UUID collision keeps the existing record and registers the new MAC as its own pending host.

## iPXE contract

- Endpoint shape: `GET /ipxe/{mac}` returns `#!ipxe` text (`Content-Type: text/plain`). Nested folders chain to `GET /ipxe/{mac}/menu/{folder_id}`. Client image picks use `GET /ipxe/{mac}/boot/{image_id}`.
- Unknown/disabled scripts `sleep` (if timeout > 0) then `exit` / `sanboot` to the next boot device. They must not list OS images.
- Configured-host menus use iPXE `menu` / `item` / `choose --timeout` with continue-to-disk as the default.
- Deployed skip must `exit` (or equivalent local-disk handoff) with no interactive prompt when the continue item fires — not as the only path for named hosts.
- Linux install scripts pass `autoinstall` as the first kernel argument (and again before `---`) plus `ds=nocloud;s=http://<pxe>/cloud-init/{machine_id}/{instance_id}/` (trailing slash required). For NFS casper, `cloud-config-url=` points at that directory's `user-data`. Subiquity only skips the disk-wipe prompt if `autoinstall` is a bare `/proc/cmdline` token. The text client can still print “Continue with autoinstall?” after apt configuration starts. The early-command downloads `/boot-files/autoinstall-confirm.py` and runs it; that helper answers `POST /meta/confirm` and removes the prompt from the client. Do not inline the helper in user-data — Subiquity prints each early-command on the installer console.
- Windows install scripts fetch `unattend.xml` from `/windows/{machine_id}/{instance_id}/unattend.xml`; do not embed Administrator passwords in the iPXE script.
- Do not embed passwords, private keys, or full user-data in the iPXE script.

## Guest-init contract

- Linux: serve `user-data`, `meta-data`, and `vendor-data` under `/cloud-init/{machine_id}/{instance_id}/` **only** while the machine is `deploying`, `staged`, or `imaging` (404 for pending/deployed/timeout_error/failed, and for URLs that omit `instance_id`).
- Windows: serve `unattend.xml` and Cloudbase-Init metadata under `/windows/{machine_id}/{instance_id}/` and `/cloudbase-init/{machine_id}/{instance_id}/` under the same install-state gate.
- Bump `instance-id` when a staged job should re-run guest-init / reimage.
- Staged console changes apply as a **new image on next PXE boot**, not as SSH/WinRM/config-management push.
- Inject Linux root and Windows local Administrator from the Fernet vault at render time. Do not persist plaintext in `StagedJob` rows.
- Deploy copies the image seed onto the machine if the machine file is empty. Console **Copy Default** overwrites the machine file from the current image template. Deploy persists hostname, timezone, packages, SSH keys, and the seed textarea from the same form before creating the install attempt.
- Timezone is an IANA dropdown. New and PXE-discovered machines inherit Settings → Machines default timezone (`PXE_DEFAULT_TIMEZONE` on first boot, else UTC).
- After phone-home, Ubuntu autoinstall late-commands sysrq-reboot so casper NFS does not hang on a blank cursor waiting to unmount nfsroot. Linux iPXE adds `noprompt`, `quickreboot`, and `reboot=force`.
- Sanitize rendered payloads in logs (redact passwords, `chpasswd`, `AutoLogon`, `ssh_authorized_keys`).

## Credential vault

- Kinds: `linux_root`, `windows_administrator`. Encrypt **username and password**.
- `ENCRYPTION_KEY` required outside tests. Decrypt only in memory at seed render.
- Password is write-only in UI/API. Never log ciphertext decryption results.

## Testing checklist

1. Unit-test policy transitions in `tests/test_boot_policy.py` (unknown / deployed / staged; linux vs windows).
2. Unit-test iPXE, cloud-init, unattend, and Cloudbase-Init renderers with fixtures; no live DHCP.
3. Unit-test Fernet round-trip and “password absent from JSON/logs.”
4. CI container smoke (`.github/workflows/pxe-smoke.yml`) must hit `/ipxe/{mac}` for pending vs deployed, plus `/boot-menu`.

If the change is user-visible, update `CHANGELOG.md` `[Unreleased]` and `.env.example`. Do **not** update `README.md` unless the user explicitly asked to change it in this request (see `.cursor/rules/readme.mdc`).
