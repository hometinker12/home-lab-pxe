---
name: pxe-boot-policy
description: Implement or change PXE/iPXE boot policy, machine lifecycle, guest-init (cloud-init / Cloudbase-Init), and encrypted local accounts in home-lab-pxe. Use when touching src/boot/, src/cloudinit/, src/windows/, src/inventory/, iPXE scripts, or deploy/reimage flows.
---

# PXE boot policy

## Decision order

For every DHCP/iPXE request, resolve the machine then emit **one** script:

1. **Unknown** (no MAC/UUID in inventory) → register as `pending`, serve the wait/poll menu. Never install.
2. **Pending / ready** with no operator action → keep waiting.
3. **Deploying** or **staged reimage** → serve the assigned image:
   - Linux: kernel/initrd plus cloud-init seed URL. Ubuntu live-server with extracted casper squashfs uses `netboot=nfs nfsroot=host:/export/{id}/{rev}` and `NFSOPTS=vers=3,tcp,port=2049` (HTTP `iso-url=` is fallback). Casper `nfsmount` still needs rpcbind on TCP/UDP 111 for mountd. After each ISO extract, Ganesha gets a dedicated EXPORT Path at that generation directory so MNT is not a subdirectory of a parent export. Autoinstall user-data must cover locale/keyboard/source/storage/network/apt so Subiquity stays non-interactive; extract `dists/` + `pool/` with casper so apt is not an empty `file:/cdrom`.
   - Windows: wimboot/WinPE plus `unattend.xml` URL; Cloudbase-Init seed for first boot.
4. **Deployed** with no staged job → exit iPXE immediately to local disk (no menu).
5. **Quarantine / disabled** → wait menu or explicit refuse script; do not boot an image.

Identity: MAC is primary; SMBIOS UUID is secondary. If UUID matches a known machine but MAC changed, attach the new MAC and keep the existing record.

## iPXE contract

- Endpoint shape: `GET /ipxe/{mac}` returns `#!ipxe` text (`Content-Type: text/plain`).
- Wait menu must `chain`/`sleep` back to the same URL (operator action is picked up without a BIOS re-PXE).
- Deployed skip must `exit` (or equivalent local-disk handoff) with no interactive prompt.
- Linux install scripts pass `ds=nocloud-net;s=http://<pxe>/cloud-init/{machine_id}/` (trailing slash required).
- Windows install scripts fetch `unattend.xml` from `/windows/{machine_id}/unattend.xml`; do not embed Administrator passwords in the iPXE script.
- Do not embed passwords, private keys, or full user-data in the iPXE script.

## Guest-init contract

- Linux: serve `user-data`, `meta-data`, and `vendor-data` under `/cloud-init/{machine_id}/` **only** while the machine is `deploying` or `staged` (404 for pending/deployed).
- Windows: serve `unattend.xml` and Cloudbase-Init metadata under `/windows/{machine_id}/` and `/cloudbase-init/{machine_id}/` under the same install-state gate.
- Bump `instance-id` when a staged job should re-run guest-init / reimage.
- Staged console changes apply as a **new image on next PXE boot**, not as SSH/WinRM/config-management push.
- Inject Linux root and Windows local Administrator from the Fernet vault at render time. Do not persist plaintext in `StagedJob` rows.
- Sanitize rendered payloads in logs (redact passwords, `chpasswd`, `AutoLogon`, `ssh_authorized_keys`).

## Credential vault

- Kinds: `linux_root`, `windows_administrator`. Encrypt **username and password**.
- `ENCRYPTION_KEY` required outside tests. Decrypt only in memory at seed render.
- Password is write-only in UI/API. Never log ciphertext decryption results.

## Testing checklist

1. Unit-test policy transitions in `tests/test_boot_policy.py` (unknown / deployed / staged; linux vs windows).
2. Unit-test iPXE, cloud-init, unattend, and Cloudbase-Init renderers with fixtures; no live DHCP.
3. Unit-test Fernet round-trip and “password absent from JSON/logs.”
4. CI container smoke (`.github/workflows/pxe-smoke.yml`) must hit `/ipxe/{mac}` for pending vs deployed.

If the change is user-visible, update `CHANGELOG.md` `[Unreleased]` and `.env.example`. Update `README.md` only when install/run steps or operator-facing capabilities change (see `.cursor/rules/readme.mdc`).
