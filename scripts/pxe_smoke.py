#!/usr/bin/env python3
"""HTTP PXE smoke against a running home-lab-pxe instance. Does not push images."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, *, data: bytes | None = None, headers: dict | None = None, form: dict | None = None):
        url = f"{self.base}{path}"
        hdrs = {"Origin": self.base, "Referer": f"{self.base}/login"}
        if headers:
            hdrs.update(headers)
        body = data
        if form is not None:
            from urllib.parse import urlencode

            body = urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=20) as resp:
                payload = resp.read()
                return resp.status, dict(resp.headers), payload
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def multipart(self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]):
        boundary = f"pxesmoke{int(time.time())}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.append(
                (
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
                ).encode()
            )
        for name, (filename, content, ctype) in files.items():
            chunks.append(
                (
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                    f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n"
                ).encode()
                + content
                + b"\r\n"
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return self.request(
            "POST",
            path,
            data=b"".join(chunks),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )


def fail(message: str) -> None:
    print(f"SMOKE FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def expect(cond: bool, message: str) -> None:
    if not cond:
        fail(message)


def seed_nfs_generation(container: str, image_id: int) -> None:
    iid = int(image_id)
    code = (
        "from src.db import session_scope\n"
        "from src.models import Image\n"
        f"iid = {iid}\n"
        "with session_scope() as db:\n"
        "    img = db.get(Image, iid)\n"
        "    if img is None:\n"
        "        raise SystemExit('missing image')\n"
        "    img.extract_generation = f'nfs/{iid}/1'\n"
        "    db.add(img)\n"
        "    db.commit()\n"
        "from pathlib import Path\n"
        f"dest = Path('/var/lib/pxe/images/nfs/{iid}/1')\n"
        "casper = dest / 'casper'\n"
        "casper.mkdir(parents=True, exist_ok=True)\n"
        "(dest / '.disk').mkdir(exist_ok=True)\n"
        "(casper / 'filesystem.squashfs').write_bytes(b'sqsh')\n"
        "from src.nfs_media import publish_nfs_export_root\n"
        "from src.settings import get_settings\n"
        "publish_nfs_export_root(get_settings().image_root, f'nfs/{iid}/1')\n"
    )
    proc = subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        capture_output=True,
        text=True,
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    expect(proc.returncode == 0, f"seed nfs generation failed rc={proc.returncode} {detail}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="smokepass")
    parser.add_argument("--smb-password", default="")
    parser.add_argument("--container", default="", help="docker container name for NFS generation seed")
    args = parser.parse_args()
    c = Client(args.base_url)
    smb_secret = (args.smb_password or "").strip()

    status, _, body = c.request("GET", "/health")
    expect(status == 200, f"/health {status}")
    health = json.loads(body)
    expect(health.get("status") == "ok", f"health body {health}")

    status, _, body = c.request("GET", "/login")
    expect(status == 200, f"/login {status}")
    expect(b"/static/logo.png" in body, "login missing logo")
    expect(b'rel="icon"' in body, "login missing favicon link")
    expect(b"brand-mark" in body, "header missing brand mark")
    status, _, body = c.request("GET", "/static/logo.png")
    expect(status == 200 and body.startswith(b"\x89PNG"), "logo png")
    status, _, body = c.request("GET", "/static/favicon.png")
    expect(status == 200 and body.startswith(b"\x89PNG"), "favicon png")
    status, _, body = c.request("GET", "/favicon.ico")
    expect(status == 200 and len(body) > 32, "favicon.ico")

    status, _, body = c.request("GET", "/boot.ipxe")
    expect(status == 200 and body.startswith(b"#!ipxe"), "/boot.ipxe")
    expect(b"chain" in body, "boot.ipxe missing chain")

    mac = "de-ad-be-ef-00-01"
    status, _, body = c.request("GET", f"/ipxe/{mac}")
    text = body.decode()
    expect(status == 200 and text.startswith("#!ipxe"), f"/ipxe pending {status}")
    expect("Waiting for operator" in text, "expected wait menu")
    expect("sleep" in text, "wait menu should poll")
    expect("smokepass" not in text, "password leaked into iPXE")
    expect(not smb_secret or smb_secret not in text, "SMB password leaked into pending iPXE")

    status, _, _ = c.request("POST", "/login", form={"username": args.user, "password": args.password})
    expect(status in {200, 303, 302}, f"login {status}")

    status, _, body = c.request("GET", "/settings")
    expect(status == 200 and b"DHCP" in body, "settings DHCP form")
    expect(b"dhcp-form" in body, "settings DHCP mode-aware form")
    expect(b'data-dhcp-mode="authoritative"' in body, "authoritative DHCP fields grouped")
    expect(b"<details" in body, "settings sections should be collapsible")
    expect(b"TFTP" in body, "settings TFTP form")
    expect(b'href="/files"' in body, "files nav link")
    expect(b"HTTPS" in body, "settings HTTPS form")
    expect(b"self-signed" in body, "first-boot TLS certificate")
    expect(b"BEGIN PRIVATE" not in body, "TLS private key leaked into settings")
    expect(b"Option 60 (PXEClient)" in body, "external DHCP option 60 hint")
    expect(b"Option 66 (Next Server)" in body, "external DHCP option 66 hint")
    expect(b"Option 67 (Boot File Name)" in body, "external DHCP option 67 hint")
    expect(b"Host LAN IPv4" in body, "settings host LAN address")
    expect(b"/boot.ipxe" in body, "settings advertised PXE URL")
    expect(b"Already iPXE" in body, "settings already-iPXE TFTP filename hint")
    expect(b"Ubuntu installation media (NFS)" in body, "settings NFS casper export")
    expect(b"netboot=nfs" in body, "settings NFS netboot hint")
    expect(b"This page" not in body, "settings should not show request host")
    expect(b"Docker Desktop gateway" not in body, "settings should not show Docker gateway")
    status, _, _ = c.request(
        "POST",
        "/settings/pxe",
        form={
            "bind_interface": "eth0",
            "extra_options": "dhcp-option=15,smoke.home",
        },
    )
    expect(status in {200, 303, 302}, f"pxe save {status}")
    status, _, _ = c.request(
        "POST",
        "/settings/dhcp",
        form={
            "mode": "proxy",
            "dhcp_range": "192.168.1.200,192.168.1.250,12h",
        },
    )
    expect(status in {200, 303, 302}, f"dhcp disable {status}")
    status, _, body = c.request("GET", "/settings")
    expect(b"smoke.home" in body, "pxe extra option not saved")
    status, _, _ = c.request(
        "POST",
        "/settings/dhcp",
        form={
            "enabled": "1",
            "mode": "proxy",
            "dhcp_range": "192.168.1.200,192.168.1.250,12h",
        },
    )
    expect(status in {200, 303, 302}, f"dhcp enable {status}")
    status, _, _ = c.request("POST", "/settings/tftp", form={"tftp_enabled": "1"})
    expect(status in {200, 303, 302}, f"tftp enable {status}")
    status, _, body = c.request("GET", "/files")
    expect(status == 200 and b'class="fm"' in body, "TFTP file manager page")
    expect(b"iPXE boot files" in body and b"NFS extracts" in body, "files favorites missing boot/extract shortcuts")
    expect(b"SMB media" in body and b"Machine seeds" in body, "files favorites missing SMB/seeds shortcuts")
    status, _, body = c.request("GET", "/files?root=images&dir=nfs")
    expect(status == 200 and b"images:/nfs" in body, "files NFS extract folder")
    status, _, body = c.request("GET", "/files/download?path=undionly.kpxe")
    expect(status == 200 and len(body) > 0, "authenticated tftp download")
    status, _, body = c.request("GET", "/tftp/wimboot")
    expect(status == 200 and len(body) > 1024 and body.strip() != b"ipxe-stub", "http wimboot must be real")

    status, _, body = c.request("GET", "/api/machines")
    pending_linux = next(m for m in json.loads(body) if m.get("mac") == "de:ad:be:ef:00:01")
    pmid = pending_linux["id"]
    status, _, body = c.request("GET", f"/windows/{pmid}/startnet.cmd")
    expect(status == 404, "pending must not receive startnet")
    expect(b"pxe-media" not in body, "pending startnet leaked media")
    status, _, _ = c.request("GET", f"/install-files/{pmid}/kernel")
    expect(status == 404, "pending must not receive install-files")

    status, _, body = c.request("GET", "/images")
    expect(status == 200 and b"Advanced Settings" in body, "images missing Advanced Settings")
    expect(b'name="kernel_path"' in body, "kernel path missing from advanced settings")

    dummy_iso = f"smoke-extract-{int(time.time())}"
    status, _, _ = c.multipart(
        "/images",
        {"name": dummy_iso, "os_family": "linux", "arch": "x86_64"},
        {"iso_file": ("dummy.iso", b"not-an-iso", "application/octet-stream")},
    )
    expect(status in {200, 303, 302}, f"dummy iso create {status}")
    dummy_row = None
    for _ in range(60):
        status, _, body = c.request("GET", "/api/images")
        dummy_row = next((img for img in json.loads(body) if img.get("name") == dummy_iso), None)
        if dummy_row and dummy_row.get("extract_status") in {"failed", "ready", "idle"}:
            break
        time.sleep(1)
    expect(dummy_row is not None, "dummy iso image missing")
    expect(dummy_row.get("extract_status") == "failed", f"dummy iso extract {dummy_row}")
    status, _, body = c.request("GET", "/images")
    expect(b"Delete" in body, "image delete control missing")
    err = dummy_row.get("extract_error") or ""
    expect("/var/lib" not in err and "C:\\" not in err, "extract_error leaked a host path")
    status, _, body = c.request(
        "POST",
        f"/machines/{pmid}/deploy",
        form={"image_id": str(dummy_row["id"]), "username": "root", "password": "iso-extract-secret"},
    )
    expect(status == 200 and b"not ready" in body.lower(), "failed extract must block deploy")
    status, _, body = c.request("POST", f"/images/{dummy_row['id']}/extract")
    expect(status in {200, 303, 302}, f"retry extract {status}")
    status, _, body = c.request("POST", f"/images/{dummy_row['id']}/seed/reset")
    expect(status in {200, 303, 302}, f"reset seed {status}")
    dummy_id = dummy_row["id"]
    status, _, _ = c.request("POST", f"/images/{dummy_id}/delete")
    expect(status in {200, 303, 302}, f"delete dummy iso {status}")
    status, _, body = c.request("GET", "/api/images")
    expect(all(img.get("id") != dummy_id for img in json.loads(body)), "deleted dummy iso still listed")

    status, _, _ = c.request(
        "POST",
        "/machines",
        form={"mac": "aa-bb-cc-dd-ee-0f", "hostname": "smoke-added"},
    )
    expect(status in {200, 303, 302}, f"add machine {status}")
    status, _, body = c.request("GET", "/api/machines")
    expect(any(m.get("mac") == "aa:bb:cc:dd:ee:0f" for m in json.loads(body)), "manually added MAC missing")
    added = next(m for m in json.loads(body) if m.get("mac") == "aa:bb:cc:dd:ee:0f")
    status, _, body = c.request("GET", f"/machines/{added['id']}")
    expect(status == 200 and b"Delete" in body, "machine delete control missing")
    expect(b'name="hostname"' in body and b'form="machine-save"' in body, "hostname field missing from actions")
    status, _, _ = c.request("POST", f"/machines/{added['id']}/delete")
    expect(status in {200, 303, 302}, f"delete added machine {status}")
    status, _, body = c.request("GET", "/api/machines")
    expect(all(m.get("id") != added["id"] for m in json.loads(body)), "deleted machine still listed")

    ubuntu_name = f"smoke-ubuntu-{int(time.time())}"
    status, _, body = c.request(
        "POST",
        "/images",
        form={
            "name": ubuntu_name,
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
        },
    )
    expect(status in {200, 303, 302}, f"create image {status}")

    status, _, body = c.request("GET", "/api/images")
    expect(status == 200, f"/api/images after create {status}")
    linux_image = next((img for img in json.loads(body) if img.get("name") == ubuntu_name), None)
    expect(linux_image is not None, "linux image missing after create")
    linux_image_id = linux_image["id"]
    expect("extract_status" in linux_image, "extract_status missing from /api/images")
    status, _, body = c.request("GET", f"/images/{linux_image_id}")
    expect(status == 200 and b"Save image" in body, "image edit page")
    status, _, _ = c.request(
        "POST",
        f"/images/{linux_image_id}",
        form={
            "name": ubuntu_name,
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
            "cmdline": "smoke-edit",
        },
    )
    expect(status in {200, 303, 302}, f"edit image {status}")
    status, _, body = c.request("GET", f"/images/{linux_image_id}")
    expect(b"smoke-edit" in body, "edited cmdline missing")
    token = f"pxe-smoke-token-{int(time.time())}"
    seed = (
        "#cloud-config\n"
        f"# {token}\n"
        "autoinstall:\n"
        "  version: 1\n"
        "  ssh:\n"
        "    install-server: true\n"
        "    allow-pw: true\n"
        "    authorized-keys: []\n"
        "  user-data:\n"
        "    hostname: {{hostname}}\n"
        "    manage_etc_hosts: true\n"
        "    timezone: {{timezone}}\n"
        "    disable_root: false\n"
        "    ssh_pwauth: true\n"
        "    chpasswd:\n"
        "      expire: false\n"
        "      users:\n"
        "        - name: {{username}}\n"
        "          password: {{password_hash}}\n"
        "          type: hash\n"
        "  late-commands:\n"
        "    - [wget, -q, --post-data=, -O, /dev/null, {{phone_home_url}}]\n"
    )
    status, _, _ = c.request(
        "POST",
        f"/images/{linux_image_id}",
        form={
            "name": ubuntu_name,
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
            "cmdline": "smoke-edit",
            "user_data": seed,
        },
    )
    expect(status in {200, 303, 302}, f"edit image seed {status}")
    expect(b'data-os="linux"' in body, "linux image fields")
    expect(b'data-os="windows"' in body, "windows image fields")

    iso_name = f"smoke-iso-{int(time.time())}"
    status, _, _ = c.request(
        "POST",
        "/images",
        form={
            "name": iso_name,
            "os_family": "linux",
            "arch": "x86_64",
            "iso_path": "ubuntu/live.iso",
        },
    )
    expect(status in {200, 303, 302}, f"create iso image {status}")
    status, _, body = c.request("GET", "/api/images")
    iso_images = [img for img in json.loads(body) if img["name"] == iso_name]
    expect(iso_images and iso_images[0].get("iso_path") == "ubuntu/live.iso", "iso path missing")

    iso_mac = "de-ad-be-ef-00-aa"
    status, _, body = c.request("GET", f"/ipxe/{iso_mac}")
    expect("Waiting for operator" in body.decode(), "iso MAC should wait first")
    status, _, body = c.request("GET", "/api/machines")
    iso_machine = next(m for m in json.loads(body) if m["mac"] == "de:ad:be:ef:00:aa")
    status, _, _ = c.request(
        "POST",
        f"/machines/{iso_machine['id']}/deploy",
        form={"image_id": str(iso_images[0]["id"]), "username": "root", "password": "iso-smoke-secret"},
    )
    expect(status in {200, 303, 302}, f"iso deploy {status}")
    status, _, body = c.request("GET", f"/ipxe/{iso_mac}")
    iso_text = body.decode()
    expect("sanboot" in iso_text and "/boot-files/" in iso_text, "expected iso sanboot iPXE")
    expect("kernel" not in iso_text, "iso-only install should not chain kernel")
    expect("iso-smoke-secret" not in iso_text, "password leaked into iso iPXE")

    if os.environ.get("CI") == "true" and not args.container.strip():
        fail("NFS iPXE smoke requires --container in CI")
    if args.container.strip():
        nfs_name = f"smoke-nfs-{int(time.time())}"
        status, _, _ = c.request(
            "POST",
            "/images",
            form={
                "name": nfs_name,
                "os_family": "linux",
                "arch": "x86_64",
                "kernel_path": "ubuntu/vmlinuz",
                "initrd_path": "ubuntu/initrd",
            },
        )
        expect(status in {200, 303, 302}, f"create nfs image {status}")
        status, _, body = c.request("GET", "/api/images")
        nfs_image = next((img for img in json.loads(body) if img.get("name") == nfs_name), None)
        expect(nfs_image is not None, "nfs linux image missing after create")
        nfs_image_id = int(nfs_image["id"])
        seed_nfs_generation(args.container.strip(), nfs_image_id)
        nfs_mac = "de-ad-be-ef-00-cc"
        status, _, body = c.request("GET", f"/ipxe/{nfs_mac}")
        expect("Waiting for operator" in body.decode(), "nfs MAC should wait first")
        status, _, body = c.request("GET", "/api/machines")
        nfs_machine = next(m for m in json.loads(body) if m["mac"] == "de:ad:be:ef:00:cc")
        status, _, _ = c.request(
            "POST",
            f"/machines/{nfs_machine['id']}/deploy",
            form={"image_id": str(nfs_image_id), "username": "root", "password": "nfs-smoke-secret"},
        )
        expect(status in {200, 303, 302}, f"nfs deploy {status}")
        status, _, body = c.request("GET", f"/ipxe/{nfs_mac}")
        nfs_text = body.decode()
        expect("netboot=nfs" in nfs_text and "boot=casper" in nfs_text, "expected nfs casper iPXE")
        expect(f"nfsroot=" in nfs_text and "/var/lib/pxe/images/nfs" in nfs_text, "expected nfs export root")
        expect(f"/var/lib/pxe/images/nfs/{nfs_image_id}/1" not in nfs_text, "nfsroot must be export root not generation")
        expect("live-media-path=" not in nfs_text, "export-root casper/ should use default live-media-path")
        expect("NFSOPTS=vers=3,tcp,port=2049" in nfs_text, "expected casper NFSOPTS")
        ls_proc = subprocess.run(
            ["docker", "exec", args.container.strip(), "test", "-f", "/var/lib/pxe/images/nfs/casper/filesystem.squashfs"],
            capture_output=True,
            text=True,
        )
        expect(ls_proc.returncode == 0, "casper squashfs missing at NFS export root")
        expect(",vers=" not in nfs_text and "mountport=" not in nfs_text, "nfsroot must not swallow mount options")
        expect("iso-url=" not in nfs_text and "ramdisk_size" not in nfs_text, "nfs install should not wget ISO")
        expect("nfs-smoke-secret" not in nfs_text, "password leaked into nfs iPXE")

    status, _, body = c.request("GET", "/api/machines")
    expect(status == 200, f"/api/machines {status} {body[:200]!r}")
    machines = json.loads(body)
    linux_mac_colon = "de:ad:be:ef:00:01"
    machine = next((m for m in machines if m.get("mac") == linux_mac_colon), None)
    expect(machine is not None, "linux MAC missing before deploy")
    mid = machine["id"]
    expect(machine["state"] == "pending", f"state {machine['state']}")
    status, _, body = c.request("GET", f"/cloud-init/{mid}/user-data")
    expect(status == 404 and b"lab-default" not in body and b"root:" not in body, "pending must not serve user-data")

    status, _, _ = c.request(
        "POST",
        f"/machines/{mid}/deploy",
        form={"image_id": str(linux_image_id), "username": "root", "password": "root-smoke-secret"},
    )
    expect(status in {200, 303, 302}, f"deploy {status}")

    status, _, body = c.request("GET", f"/ipxe/{mac}")
    text = body.decode()
    expect("kernel" in text and "nocloud-net" in text, "expected linux install iPXE")
    expect(r"\;s=" in text or "seed-url" in text, "nocloud-net semicolon must be escaped")
    expect("root-smoke-secret" not in text, "password leaked into install iPXE")
    expect(not smb_secret or smb_secret not in text, "SMB password leaked into linux iPXE")
    expect(token not in text, "user-data token leaked into iPXE")

    status, _, body = c.request("GET", f"/cloud-init/{mid}/user-data")
    expect(status == 200, f"user-data {status}")
    user_data = body.decode()
    expect(token in user_data, "cloud-init missing image seed token")
    expect("root" in user_data, "cloud-init missing vault user")
    expect("root-smoke-secret" not in user_data, "plaintext password leaked into user-data")
    expect("$6$" in user_data, "cloud-init missing password_hash")
    expect("instance-id" in c.request("GET", f"/cloud-init/{mid}/meta-data")[2].decode(), "meta-data")

    status, _, body = c.request("GET", f"/api/machines/{mid}")
    detail = json.loads(body)
    expect("root-smoke-secret" not in json.dumps(detail), "password in JSON")
    expect(detail.get("account_password_set") is True, "password_set flag")

    status, _, body = c.request(
        "POST",
        f"/api/machines/{mid}/events",
        data=json.dumps({"event": "deployed"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    expect(status == 200, f"phone_home {status} {body!r}")

    status, _, body = c.request("GET", f"/ipxe/{mac}")
    expect("exit" in body.decode() and "Waiting" not in body.decode(), "deployed should skip menu")
    status, _, body = c.request("GET", f"/cloud-init/{mid}/user-data")
    expect(status == 404, "deployed must not keep serving user-data")

    status, _, body = c.request("GET", f"/machines/{mid}")
    expect(status == 200 and b'form="machine-save"' in body, "hostname field missing from machine actions")
    status, _, _ = c.request(
        "POST",
        f"/machines/{mid}/save",
        form={"hostname": "smoke-linux-named", "timezone": "UTC"},
    )
    expect(status in {200, 303, 302}, f"deployed hostname save {status}")
    status, _, body = c.request("GET", f"/api/machines/{mid}")
    expect(json.loads(body).get("hostname") == "smoke-linux-named", "deployed hostname did not persist")

    status, _, _ = c.request("POST", f"/machines/{mid}/stage", form={})
    expect(status in {200, 303, 302}, f"stage {status}")
    status, _, body = c.request("GET", f"/ipxe/{mac}")
    expect("kernel" in body.decode(), "staged should install again")

    status, _, body = c.request("GET", "/tftp/undionly.kpxe")
    expect(status == 200 and len(body) > 0, f"tftp http {status}")

    status, _, body = c.request("GET", "/api/machines")
    expect(status == 200, "machines after linux flow")
    linux_mac = "de:ad:be:ef:00:01"
    expect(any(m.get("mac") == linux_mac for m in json.loads(body)), "linux MAC missing")

    win_mac = "de-ad-be-ef-00-02"
    status, _, body = c.request("GET", f"/ipxe/{win_mac}")
    expect("Waiting for operator" in body.decode(), "windows MAC should wait first")

    status, _, _ = c.request(
        "POST",
        "/images",
        form={
            "name": f"smoke-windows-{int(time.time())}",
            "os_family": "windows",
            "arch": "x86_64",
            "boot_wim_path": "windows/boot.wim",
            "install_wim_path": "windows/install.wim",
        },
    )
    expect(status in {200, 303, 302}, f"create windows image {status}")

    status, _, body = c.request("GET", "/api/machines")
    machines = json.loads(body)
    win_machine = next(m for m in machines if m["mac"] == "de:ad:be:ef:00:02")
    status, _, body = c.request("GET", "/api/images")
    images = json.loads(body)
    win_image = next(img for img in images if img["os_family"] == "windows")
    status, _, _ = c.request(
        "POST",
        f"/machines/{win_machine['id']}/deploy",
        form={"image_id": str(win_image["id"]), "username": "Administrator", "password": "Win-smoke-secret"},
    )
    expect(status in {200, 303, 302}, f"windows deploy {status}")

    status, _, body = c.request("GET", f"/ipxe/{win_mac}")
    text = body.decode()
    expect("unattend.xml" in text and "wimboot" in text, "expected windows install iPXE")
    expect("winpeshl.ini" in text and "startnet.cmd" in text, "windows script missing WinPE startup files")
    expect("install.wim" not in text, "install.wim must not be an iPXE initrd")
    expect("Win-smoke-secret" not in text, "password leaked into windows iPXE")
    expect(not smb_secret or smb_secret not in text, "SMB password leaked into windows iPXE")

    status, _, body = c.request("GET", f"/windows/{win_machine['id']}/unattend.xml")
    expect(status == 200 and b"Win-smoke-secret" in body, "unattend missing Administrator password")
    expect(b"windowsPE" in body, "unattend missing windowsPE")
    status, _, body = c.request("GET", f"/windows/{win_machine['id']}/startnet.cmd")
    expect(status == 200 and b"pxe-media" in body, "startnet missing SMB share")
    expect(b"Win-smoke-secret" not in body, "windows local password leaked into startnet")
    if smb_secret:
        expect(smb_secret.encode() in body, "startnet missing SMB password")
    status, _, body = c.request("GET", f"/cloudbase-init/{win_machine['id']}/user-data")
    expect(status == 200 and b"Win-smoke-secret" in body, "cloudbase-init missing password")
    expect(b"phone_home" in body, "cloudbase-init missing phone_home")

    print("SMOKE PASS")


if __name__ == "__main__":
    main()
