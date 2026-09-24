#!/usr/bin/env python3
"""HTTP PXE smoke against a running home-lab-pxe instance. Does not push images."""

from __future__ import annotations

import argparse
import json
import os
import re
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


def machine_record(client, machine_id: int) -> dict:
    status, _, body = client.request("GET", "/api/machines")
    expect(status == 200, "machine list for seed URL")
    return next(row for row in json.loads(body) if row["id"] == machine_id)


def guest_path(machine: dict, family: str, leaf: str) -> str:
    return f"/{family}/{machine['id']}/{machine['instance_id']}/{leaf}"


def folder_ids_from_tree(body: bytes) -> dict[str, int]:
    found: dict[str, int] = {}
    for match in re.finditer(
        rb'href="/boot-menu\?folder=(\d+)" class="boot-tree-name[^"]*"[^>]*>([^<]+)',
        body,
    ):
        found[match.group(2).decode().strip()] = int(match.group(1))
    return found


def seed_nfs_generation(container: str, image_id: int) -> None:
    iid = int(image_id)
    code = (
        "from pathlib import Path\n"
        "from src.db import session_scope\n"
        "from src.install_sources import refresh_image_sources\n"
        "from src.models import ExtractStatus, Image\n"
        f"iid = {iid}\n"
        f"dest = Path('/var/lib/pxe/images/nfs/{iid}/1')\n"
        "casper = dest / 'casper'\n"
        "casper.mkdir(parents=True, exist_ok=True)\n"
        "(dest / '.disk').mkdir(exist_ok=True)\n"
        "(casper / 'filesystem.squashfs').write_bytes(b'sqsh')\n"
        "yaml = '\\n'.join([\n"
        "    'sources:',\n"
        "    '- id: ubuntu-server-minimal',\n"
        "    '  name:',\n"
        "    '    en: Minimal',\n"
        "    '- default: true',\n"
        "    '  id: ubuntu-server',\n"
        "    '  name:',\n"
        "    '    en: Server',\n"
        "    '',\n"
        "])\n"
        "(casper / 'install-sources.yaml').write_text(yaml, encoding='utf-8')\n"
        "with session_scope() as db:\n"
        "    img = db.get(Image, iid)\n"
        "    if img is None:\n"
        "        raise SystemExit('missing image')\n"
        "    img.extract_generation = f'nfs/{iid}/1'\n"
        "    img.extract_status = ExtractStatus.ready.value\n"
        "    db.add(img)\n"
        "    db.commit()\n"
        "    db.refresh(img)\n"
        "    if not refresh_image_sources(img):\n"
        "        raise SystemExit(f'catalog refresh failed status={img.extract_status!r} gen={img.extract_generation!r}')\n"
        "    db.add(img)\n"
        "    db.commit()\n"
    )
    proc = subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        capture_output=True,
        text=True,
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    expect(proc.returncode == 0, f"seed nfs generation failed rc={proc.returncode} {detail}")
    proc = subprocess.run(
        ["docker", "exec", container, "python", "./scripts/sync_ganesha_exports.py", "--reload"],
        capture_output=True,
        text=True,
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    expect(proc.returncode == 0, f"sync ganesha exports failed rc={proc.returncode} {detail}")


def expect_nfs_fhandle(container: str, path: str) -> None:
    """Fail if seccomp/caps block open_by_handle_at (casper: Operation not permitted)."""
    code = f"""
import ctypes
import ctypes.util
import errno
import os
import sys

path = {path!r}
lib = ctypes.util.find_library("c")
if not lib:
    raise SystemExit("libc not found")
libc = ctypes.CDLL(lib, use_errno=True)
libc.name_to_handle_at.restype = ctypes.c_int
libc.name_to_handle_at.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
]
libc.open_by_handle_at.restype = ctypes.c_int
libc.open_by_handle_at.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]

class FileHandle(ctypes.Structure):
    _fields_ = [
        ("handle_bytes", ctypes.c_uint),
        ("handle_type", ctypes.c_int),
        ("f_handle", ctypes.c_ubyte * 128),
    ]

AT_FDCWD = -100
fh = FileHandle()
fh.handle_bytes = 128
mount_id = ctypes.c_int()
ctypes.set_errno(0)
rc = libc.name_to_handle_at(AT_FDCWD, path.encode(), ctypes.byref(fh), ctypes.byref(mount_id), 0)
err = ctypes.get_errno()
if rc != 0:
    raise SystemExit(f"name_to_handle_at errno={{err}} ({{errno.errorcode.get(err, err)}})")
dirfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
try:
    ctypes.set_errno(0)
    got = libc.open_by_handle_at(dirfd, ctypes.byref(fh), os.O_RDONLY)
finally:
    os.close(dirfd)
err = ctypes.get_errno()
if got < 0:
    if err == errno.EPERM:
        raise SystemExit(
            "open_by_handle_at EPERM: Docker seccomp or missing CAP_DAC_READ_SEARCH "
            "(casper mount: Operation not permitted)"
        )
    raise SystemExit(f"open_by_handle_at errno={{err}} ({{errno.errorcode.get(err, err)}})")
os.close(got)
print("ok")
"""
    proc = subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        capture_output=True,
        text=True,
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    expect(proc.returncode == 0, f"nfs fhandle check failed rc={proc.returncode} {detail}")


def expect_dnsmasq_ipxe_handoff(container: str) -> None:
    conf = ""
    for _ in range(15):
        proc = subprocess.run(
            ["docker", "exec", container, "cat", "/var/lib/pxe/data/dnsmasq-pxe.conf"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and "dhcp-userclass=set:ipxe,iPXE" in (proc.stdout or ""):
            conf = proc.stdout
            break
        time.sleep(1)
    expect(bool(conf), "dnsmasq conf missing iPXE user-class after DHCP enable")
    expect("dhcp-boot=tag:ipxe," in conf and "boot.ipxe" in conf, "dnsmasq missing iPXE HTTP boot.ipxe")


def editor_call(client, payload: dict):
    status, headers, body = client.request(
        "POST",
        "/api/cloud-init/editor",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        parsed = json.loads(body or b"{}")
    except ValueError:
        parsed = {}
    return status, headers, parsed


def check_cloudinit_editor(client, detail_path: str) -> None:
    """Editor API view/preview/apply round trip (nothing is saved) plus the detail page's schema island."""
    status, _, body = client.request("GET", detail_path)
    expect(status == 200, f"{detail_path} {status}")
    match = re.search(rb'<script type="application/json" class="cc-schema">(.*?)</script>', body, flags=re.S)
    expect(match is not None, f"{detail_path} missing cc-schema island")
    try:
        schema = json.loads(match.group(1))
    except ValueError:
        fail(f"{detail_path} cc-schema island is not JSON")
    expect(isinstance(schema, list) and any(node.get("id") == "users" for node in schema), "cc-schema nodes")

    host = f"smoke-cc-{int(time.time())}"
    seed = f"#cloud-config\nhostname: {host}\nusers:\n  - name: smoke\n    shell: /bin/bash\n"
    status, headers, view = editor_call(client, {"action": "view", "seed": seed})
    expect(status == 200, f"editor view {status}")
    cache = {key.lower(): value for key, value in headers.items()}.get("cache-control", "")
    expect("no-store" in cache, f"editor view Cache-Control {cache!r}")
    doc = view.get("doc") or {}
    expect(doc.get("hostname") == host, "editor view doc missing hostname")

    doc["hostname"] = f"{host}-edited"
    request = {"seed": seed, "cc_json": json.dumps(doc), "cc_extra_yaml": view.get("extra_yaml") or ""}
    status, headers, preview = editor_call(client, {"action": "preview", **request})
    expect(status == 200, f"editor preview {status}")
    expect(f"hostname: {host}-edited" in (preview.get("seed") or ""), "editor preview missing change")
    expect(f"{host}-edited" in ((preview.get("node_yaml") or {}).get("hostname") or ""), "editor preview node_yaml")
    status, _, applied = editor_call(client, {"action": "apply", **request})
    expect(status == 200, f"editor apply {status}")
    applied_seed = applied.get("seed") or ""
    expect(f"hostname: {host}-edited" in applied_seed and "name: smoke" in applied_seed, "editor apply missing change")

    literal = "SmokeLiteral-not-a-placeholder"
    doc["users"][0]["__extra__"] = f"plain_text_passwd: {literal}\n"
    status, _, rejected = editor_call(
        client, {"action": "apply", "seed": seed, "cc_json": json.dumps(doc), "cc_extra_yaml": ""}
    )
    expect(status == 400, f"editor apply must reject a literal in Other keys YAML ({status})")
    expect(rejected.get("path") == "users[0].__extra__", f"editor rejection path {rejected.get('path')!r}")
    expect(literal not in json.dumps(rejected), "editor rejection echoed the literal")


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
    status, _, autoexec = c.request("GET", "/autoexec.ipxe")
    expect(status == 200 and autoexec == body, "/autoexec.ipxe must match /boot.ipxe")

    status, _, body = c.request("GET", "/boot-files/autoinstall-confirm.py")
    expect(status == 200 and b"/meta/confirm" in body, "autoinstall confirm helper")
    try:
        compile(body.decode(), "autoinstall-confirm.py", "exec")
    except SyntaxError as exc:
        fail(f"autoinstall confirm helper is not valid Python: {exc}")

    status, _, body = c.request("GET", "/boot-files/uefi-boot-order.py")
    expect(status == 200 and b"efibootmgr" in body, "uefi boot order python helper")
    try:
        compile(body.decode(), "uefi-boot-order.py", "exec")
    except SyntaxError as exc:
        fail(f"uefi boot order helper is not valid Python: {exc}")
    status, _, body = c.request("GET", "/boot-files/uefi-boot-order.ps1")
    expect(status == 200 and b"bcdedit" in body, "uefi boot order PowerShell helper")

    mac = "de-ad-be-ef-00-01"
    status, _, body = c.request("GET", f"/ipxe/{mac}")
    text = body.decode()
    expect(status == 200 and text.startswith("#!ipxe"), f"/ipxe pending {status}")
    expect("Continuing to next boot device" in text, "expected unknown disk continue")
    expect("exit" in text, "unknown iPXE should exit to next device")
    expect("choose" not in text, "unknown host must not see the folder menu")
    expect("sleep" in text, "unknown continue should honor timeout")
    expect("smokepass" not in text, "password leaked into iPXE")
    expect(not smb_secret or smb_secret not in text, "SMB password leaked into pending iPXE")
    status, _, body = c.request(
        "GET",
        f"/ipxe/{mac}?manufacturer=Dell&product=OptiPlex&serial=ABC-1",
    )
    expect(status == 200 and b"Continuing to next boot device" in body, "hardware query still continues to disk")

    status, _, _ = c.request("POST", "/login", form={"username": args.user, "password": args.password})
    expect(status in {200, 303, 302}, f"login {status}")
    session = next((cookie for cookie in c.jar if cookie.name == "session"), None)
    expect(session is not None and session.expires is not None, "session cookie missing expiry")
    remaining = session.expires - time.time()
    expect(21000 < remaining < 22000, f"session idle timeout {remaining}")

    status, _, body = c.request("GET", "/machines")
    expect(status == 200 and b"data-open-dialog=\"add-machine\"" in body, "machines Add machine control")
    expect(b'id="add-machine"' in body, "add machine overlay dialog")
    expect(b'<section class="card">' not in body, "add machine form should not be an on-page card")
    expect(b">Refresh<" in body and b'href="/machines"' in body, "machines list refresh control")
    expect(b'href="/boot-menu"' in body, "boot menu nav link")

    status, _, body = c.request("GET", "/boot-menu")
    expect(status == 200 and b"Windows" in body and b"Linux" in body and b"Tools" in body, "boot menu default folders")
    expect(b"iPXE build" in body and b'name="usb_keyboard"' in body, "boot menu iPXE build section")
    expect(b"GPL-2.0-only" in body, "iPXE build licence notice")
    expect(b"unknown_timeout_seconds" in body, "unknown/disabled timeout field")
    expect(b'id="edit-folder"' in body, "folder editor overlay")
    expect(b'data-open-dialog="edit-folder"' in body, "edit folder control")
    expect(b'id="folder-edit"' in body, "edit folder form")
    expect(b">Root<" in body, "root parent option")
    expect(b'id="move-image"' in body, "move image overlay")
    expect(b">Move up<" not in body, "folder editor should not list Move up")
    folder_ids = folder_ids_from_tree(body)
    expect("Linux" in folder_ids and "Windows" in folder_ids, "default folder ids missing from tree")
    linux_folder_id = folder_ids["Linux"]
    status, _, _ = c.request(
        "POST",
        "/boot-menu/folders",
        form={"name": "SmokeNested", "parent_id": str(linux_folder_id)},
    )
    expect(status in {200, 303, 302}, f"nested folder {status}")
    status, _, body = c.request("GET", "/boot-menu")
    folder_ids = folder_ids_from_tree(body)
    expect("SmokeNested" in folder_ids, "nested folder missing from tree")
    nested_folder_id = folder_ids["SmokeNested"]
    status, _, _ = c.request(
        "POST",
        f"/boot-menu/folders/{nested_folder_id}",
        form={"name": "SmokeNested", "parent_id": ""},
    )
    expect(status in {200, 303, 302}, f"reparent nested to root {status}")
    status, _, body = c.request("GET", "/boot-menu")
    expect(b">Linux/SmokeNested<" not in body, "reparented folder still listed under Linux")
    expect("SmokeNested" in folder_ids_from_tree(body), "reparented folder missing from tree")
    status, _, _ = c.request(
        "POST",
        f"/boot-menu/folders/{nested_folder_id}",
        form={"name": "SmokeNested", "parent_id": str(linux_folder_id)},
    )
    expect(status in {200, 303, 302}, f"restore nested under Linux {status}")
    status, _, body = c.request("GET", "/boot-menu")
    expect(b">Linux/SmokeNested<" in body, "nested folder should be under Linux again")

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
    expect(b"Default / UEFI" in body, "settings default UEFI filename")
    expect(b"<code>ipxe.efi</code>" in body, "settings should advertise ipxe.efi")
    expect(b"Ubuntu installation media (NFS)" in body, "settings NFS casper export")
    expect(b"netboot=nfs" in body, "settings NFS netboot hint")
    expect(b"Windows installation media (SMB)" in body, "settings Windows SMB share")
    expect(b'action="/settings/smb/rotate"' in body, "settings SMB rotate control")
    expect(b"Rotate password" in body, "settings SMB rotate button")
    expect(b"Imaging default local/root account" in body, "settings imaging default accounts")
    expect(b"Lab default local accounts" not in body, "old accounts section title")
    expect(b'class="nav-alert"' in body, "settings attention badge")
    expect(b"1 setting needs attention" in body, "unset imaging default accounts badge")
    expect(body.find(b'data-section="accounts"') < body.find(b'data-section="machines"'), "accounts section should be first")
    expect(body.find(b'data-section="machines"') < body.find(b'data-section="pxe"'), "machines section should be second")
    expect(b"Imaging timeout" in body, "settings imaging timeout")
    expect(b'name="imaging_timeout_minutes"' in body, "imaging timeout field")
    expect(b'name="default_timezone"' in body, "settings default timezone")
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
    if args.container.strip():
        expect_dnsmasq_ipxe_handoff(args.container.strip())
    status, _, chain = c.request("GET", "/tftp/boot.ipxe")
    expect(status == 200 and chain.startswith(b"#!ipxe") and b"chain" in chain, "TFTP HTTP boot.ipxe")
    status, _, autoexec_tftp = c.request("GET", "/tftp/autoexec.ipxe")
    expect(status == 200 and autoexec_tftp == chain, "TFTP HTTP autoexec.ipxe must match boot.ipxe")
    status, _, _ = c.request(
        "POST", "/settings/machines", form={"imaging_timeout_minutes": "15", "default_timezone": "UTC"}
    )
    expect(status in {200, 303, 302}, f"imaging timeout save {status}")
    status, _, body = c.request("GET", "/settings")
    expect(b'name="default_timezone"' in body and b'value="UTC" selected' in body, "default timezone did not persist")
    status, _, body = c.request("GET", "/files")
    expect(status == 200 and b'class="fm"' in body, "TFTP file manager page")
    expect(b"iPXE boot files" in body and b"NFS extracts" in body, "files favorites missing boot/extract shortcuts")
    expect(b"SMB media" in body and b"Machine seeds" in body, "files favorites missing SMB/seeds shortcuts")
    expect(b"Install snapshots" in body, "files favorites missing install snapshots")
    if b"ipxe-source" in body or b">stub<" in body:
        expect(b"https://boot.ipxe.org/" in body, "stub iPXE files should link to boot.ipxe.org")
        expect(b"need replacing" in body or b"needs replacing" in body, "files attention banner")
    status, _, body = c.request("GET", "/files?root=data")
    expect(status == 200, "data volume files")
    expect(b"smb.password" not in body, "persisted SMB password must not be listed")
    status, _, _ = c.request("GET", "/files/download?root=data&path=smb.password")
    expect(status == 404, "smb.password download must 404")
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
    status, _, _ = c.request("POST", f"/api/machines/{pmid}/install-log", data=b"installer failed")
    expect(status == 409, "pending must not accept an install log")
    status, _, body = c.request("GET", f"/machines/{pmid}")
    expect(b"Dell" in body and b"OptiPlex" in body and b"ABC-1" in body, "iPXE hardware fields on the machine page")
    expect(b"pxe-media" not in body, "pending startnet leaked media")
    status, _, _ = c.request("GET", f"/install-files/{pmid}/kernel")
    expect(status == 404, "pending must not receive install-files")

    status, _, body = c.request("GET", "/images")
    expect(status == 200 and b"Advanced Settings" in body, "images missing Advanced Settings")
    expect(b'name="kernel_path"' in body, "kernel path missing from advanced settings")
    expect(b"data-open-dialog=\"add-image\"" in body, "images Add image control")
    expect(b'id="add-image"' in body, "add image overlay dialog")
    expect(b'<section class="card">' not in body, "add image form should not be an on-page card")
    status, _, js_body = c.request("GET", "/static/app.js")
    expect(status == 200 and b'form.closest("dialog")' in js_body, "ISO upload must close the add dialog")
    expect(b"dialog.upload-overlay" in js_body, "ISO upload progress overlay")

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
        "/settings/machines",
        form={"imaging_timeout_minutes": "15", "default_timezone": "America/Los_Angeles"},
    )
    expect(status in {200, 303, 302}, f"default timezone save {status}")
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
    expect(b'name="hostname"' in body and b'id="machine-save"' in body, "hostname field missing from actions")
    expect(b'name="timezone"' in body and b"<select" in body, "timezone should be a dropdown")
    expect(b'form="machine-save"' in body and b'name="packages"' in body, "guest-init fields missing from save form")
    expect(b'formaction="/machines/' in body and b"/deploy" in body, "deploy should post the save form")
    expect(b'value="America/Los_Angeles" selected' in body, "new machine should inherit settings timezone")
    status, _, _ = c.request("POST", f"/machines/{added['id']}/delete")
    expect(status in {200, 303, 302}, f"delete added machine {status}")
    status, _, body = c.request("GET", "/api/machines")
    expect(all(m.get("id") != added["id"] for m in json.loads(body)), "deleted machine still listed")
    status, _, _ = c.request(
        "POST", "/settings/machines", form={"imaging_timeout_minutes": "15", "default_timezone": "UTC"}
    )
    expect(status in {200, 303, 302}, f"restore default timezone {status}")

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

    pick_mac = "de-ad-be-ef-00-10"
    status, _, body = c.request("GET", f"/ipxe/{pick_mac}")
    expect("Continuing to next boot device" in body.decode(), "pick MAC should start unknown")
    status, _, body = c.request("GET", f"/ipxe/{pick_mac}/boot/{linux_image_id}")
    unknown_boot = body.decode()
    expect("kernel" not in unknown_boot, "unknown must not install from /boot/image")
    expect("Continuing to next boot device" in unknown_boot, "unknown /boot/image should continue to disk")
    status, _, body = c.request("GET", f"/ipxe/{pick_mac}/menu/{nested_folder_id}")
    unknown_menu = body.decode()
    expect("choose" not in unknown_menu, "unknown must not see nested folder menu")
    expect("Continuing to next boot device" in unknown_menu, "unknown /menu/folder should continue to disk")
    status, _, body = c.request("GET", "/api/machines")
    pick = next(m for m in json.loads(body) if m.get("mac") == "de:ad:be:ef:00:10")
    pick_id = pick["id"]
    status, _, _ = c.request(
        "POST",
        f"/machines/{pick_id}/save",
        form={"hostname": "smoke-menu", "timezone": "UTC"},
    )
    expect(status in {200, 303, 302}, f"name pick host {status}")
    status, _, body = c.request("GET", f"/ipxe/{pick_mac}")
    named_menu = body.decode()
    expect("menu " in named_menu and "choose" in named_menu, "named host should see folder menu")
    expect("kernel" not in named_menu, "named folder menu should not start an install")
    expect(f"/menu/{linux_folder_id}" in named_menu, "root menu should chain to Linux folder")
    status, _, body = c.request("GET", f"/ipxe/{pick_mac}/menu/{nested_folder_id}")
    nested_menu = body.decode()
    expect("menu " in nested_menu and "Back" in nested_menu, "nested folder menu missing")
    expect("choose" in nested_menu, "nested folder menu missing choose")
    expect("SmokeNested" in nested_menu, "nested folder title missing")
    expect(f"/menu/{linux_folder_id}" in nested_menu, "nested Back should chain to parent folder")
    tool_name = f"smoke-tool-{int(time.time())}"
    status, _, _ = c.request(
        "POST",
        "/images",
        form={
            "name": tool_name,
            "os_family": "tool",
            "arch": "x86_64",
            "kernel_path": "tools/memtest",
            "initrd_path": "tools/initrd",
        },
    )
    expect(status in {200, 303, 302}, f"create tool image {status}")
    status, _, body = c.request("GET", "/api/images")
    tool_image = next((img for img in json.loads(body) if img.get("name") == tool_name), None)
    expect(tool_image is not None, "tool image missing after create")
    status, _, body = c.request("GET", f"/ipxe/{pick_mac}/boot/{tool_image['id']}")
    tool_boot = body.decode()
    expect("kernel" in tool_boot, "tool boot missing kernel")
    expect("autoinstall" not in tool_boot, "tool boot must not autoinstall")
    status, _, body = c.request("GET", f"/api/machines/{pick_id}")
    expect(json.loads(body).get("state") != "deploying", "tool boot must not change lifecycle")
    status, _, body = c.request("GET", f"/ipxe/{pick_mac}/boot/{linux_image_id}")
    client_boot = body.decode()
    expect("kernel" in client_boot and "autoinstall" in client_boot, "client OS pick should start linux install")
    status, _, body = c.request("GET", f"/api/machines/{pick_id}")
    expect(json.loads(body).get("state") == "deploying", "client OS pick should deploy")
    dis_mac = "de-ad-be-ef-00-11"
    status, _, _ = c.request("GET", f"/ipxe/{dis_mac}")
    status, _, body = c.request("GET", "/api/machines")
    disabled = next(m for m in json.loads(body) if m.get("mac") == "de:ad:be:ef:00:11")
    status, _, _ = c.request(
        "POST",
        f"/machines/{disabled['id']}/save",
        form={"hostname": "smoke-disabled", "timezone": "UTC"},
    )
    expect(status in {200, 303, 302}, f"name disabled host {status}")
    status, _, _ = c.request("POST", f"/machines/{disabled['id']}/disable")
    expect(status in {200, 303, 302}, f"disable host {status}")
    status, _, body = c.request("GET", f"/ipxe/{dis_mac}")
    disabled_text = body.decode()
    expect("Continuing to next boot device" in disabled_text, "disabled host should continue to disk")
    expect("choose" not in disabled_text, "disabled host must not see the folder menu")
    status, _, body = c.request("GET", f"/ipxe/{dis_mac}/boot/{linux_image_id}")
    expect("kernel" not in body.decode(), "disabled host must not install from /boot/image")

    status, _, body = c.request("GET", f"/images/{linux_image_id}")
    expect(status == 200 and b"Save image" in body, "image edit page")
    expect(b"Choose file" in body and b"data-iso-path-display" in body, "image edit ISO path should be disabled with Choose file")
    expect(b"Install source" in body, "image edit install source")
    expect(b"Advanced Settings" in body, "image edit advanced settings")
    expect(b'data-os="linux,tool" open' not in body, "image edit advanced settings should be collapsed")
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
            "source_id": "ubuntu-server-minimal",
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
        "  source:\n"
        "    id: {{source_id}}\n"
        "    search_drivers: false\n"
        "  ssh:\n"
        "    install-server: true\n"
        "    allow-pw: true\n"
        "    authorized-keys:\n"
        "      {{ssh_keys}}\n"
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
        "    - [wget, -q, --post-file=/dev/null, -O, /dev/null, {{phone_home_url}}]\n"
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
            "source_id": "ubuntu-server-minimal",
            "user_data": seed,
        },
    )
    expect(status in {200, 303, 302}, f"edit image seed {status}")
    check_cloudinit_editor(c, f"/images/{linux_image_id}")
    status, _, body = c.request("GET", f"/images/{linux_image_id}")
    expect(b'data-os="linux,tool"' in body or b'data-os="linux"' in body, "linux image fields")
    expect(b'data-os="windows"' in body, "windows image fields")
    status, _, body = c.request("GET", "/api/images")
    saved_linux = next((img for img in json.loads(body) if img.get("id") == linux_image_id), None)
    expect(saved_linux is not None, "linux image missing after source save")
    expect(saved_linux.get("source_id") == "ubuntu-server-minimal", "linux image source_id did not persist")

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
    expect("Continuing to next boot device" in body.decode(), "iso MAC should continue to disk first")
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
        status, _, body = c.request("GET", "/api/images")
        nfs_image = next((img for img in json.loads(body) if img.get("id") == nfs_image_id), None)
        expect(nfs_image is not None, "nfs linux image missing after catalog seed")
        expect(nfs_image.get("source_id") == "ubuntu-server", "nfs extract catalog should pick default source")
        expect(
            any(opt.get("id") == "ubuntu-server-minimal" for opt in (nfs_image.get("source_options") or [])),
            "nfs extract catalog missing ubuntu-server-minimal",
        )
        status, _, body = c.request("GET", f"/images/{nfs_image_id}")
        expect(b'name="source_id"' in body and b"ubuntu-server-minimal" in body, "nfs image edit missing install sources")
        status, _, _ = c.request(
            "POST",
            f"/images/{nfs_image_id}",
            form={
                "name": nfs_name,
                "os_family": "linux",
                "arch": "x86_64",
                "kernel_path": "ubuntu/vmlinuz",
                "initrd_path": "ubuntu/initrd",
                "source_id": "ubuntu-server-minimal",
            },
        )
        expect(status in {200, 303, 302}, f"nfs source save {status}")
        status, _, body = c.request("GET", "/api/images")
        nfs_image = next((img for img in json.loads(body) if img.get("id") == nfs_image_id), None)
        expect(
            nfs_image is not None and nfs_image.get("source_id") == "ubuntu-server-minimal",
            "nfs image source_id did not persist",
        )
        nfs_mac = "de-ad-be-ef-00-cc"
        status, _, body = c.request("GET", f"/ipxe/{nfs_mac}")
        expect("Continuing to next boot device" in body.decode(), "nfs MAC should continue to disk first")
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
        expect("noprompt" in nfs_text and "quickreboot" in nfs_text, "casper must skip media-eject wait")
        expect("reboot=force" in nfs_text, "installer kernel must force reboot")
        expect(f"nfsroot=" in nfs_text and f"/var/lib/pxe/images/nfs/{nfs_image_id}/1" in nfs_text, "expected generation nfsroot")
        expect("live-media-path=" not in nfs_text, "generation nfsroot should use default casper/")
        expect("NFSOPTS=vers=3,tcp,port=2049" in nfs_text, "expected casper NFSOPTS")
        expect("cloud-config-url=${seed-url}user-data" in nfs_text, "NFS casper must fetch HTTP user-data")
        expect("cloud-config-url=/dev/null" not in nfs_text, "NFS /dev/null blanks autoinstall")
        nfs_kernel = next(line for line in nfs_text.splitlines() if line.startswith("kernel "))
        expect(nfs_kernel.split()[3] == "autoinstall", "NFS autoinstall must be the first kernel arg")
        expect(nfs_kernel.rstrip().endswith("---"), "NFS kernel cmdline must end with ---")
        expect("imgargs vmlinuz " in nfs_text, "NFS iPXE imgargs must carry autoinstall")
        conf_proc = subprocess.run(
            ["docker", "exec", args.container.strip(), "cat", "/var/run/ganesha/pxe-generations.conf"],
            capture_output=True,
            text=True,
        )
        expect(conf_proc.returncode == 0, "ganesha generations conf missing")
        expect(f'Path = "/var/lib/pxe/images/nfs/{nfs_image_id}/1"' in (conf_proc.stdout or ""), "generation export Path missing")
        expect("Squash = All" in (conf_proc.stdout or "") and "Anonymous_Uid = 65534" in (conf_proc.stdout or ""), "generation export must squash to nobody")
        expect_nfs_fhandle(args.container.strip(), f"/var/lib/pxe/images/nfs/{nfs_image_id}/1")
        expect(",vers=" not in nfs_text and "mountport=" not in nfs_text, "nfsroot must not swallow mount options")
        expect("iso-url=" not in nfs_text and "ramdisk_size" not in nfs_text, "nfs install should not wget ISO")
        expect("nfs-smoke-secret" not in nfs_text, "password leaked into nfs iPXE")
        nfs_live = machine_record(c, nfs_machine["id"])
        status, _, body = c.request("GET", guest_path(nfs_live, "cloud-init", "user-data"))
        expect(status == 200, f"nfs user-data {status}")
        expect("ubuntu-server-minimal" in body.decode(), "nfs user-data missing selected source.id")

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
        f"/machines/{mid}/save",
        form={
            "hostname": "smoke-linux",
            "timezone": "America/New_York",
            "username": "root",
            "password": "root-smoke-secret",
        },
    )
    expect(status in {200, 303, 302}, f"save account {status}")
    status, _, _ = c.request(
        "POST",
        f"/machines/{mid}/deploy",
        form={"image_id": str(linux_image_id)},
    )
    expect(status in {200, 303, 302}, f"deploy {status}")
    status, _, body = c.request("GET", f"/api/machines/{mid}")
    expect(json.loads(body).get("hostname") == "smoke-linux", "save then deploy should persist hostname")
    status, _, body = c.request("GET", f"/machines/{mid}")
    expect(b">Deploying<" in body, "console should show Deploying after assign")
    expect(b"Copy Default" in body, "machine guest-init Copy Default")
    expect(token.encode() in body, "deploy should copy image seed into machine user-data")
    expect(b'value="America/New_York" selected' in body, "save then deploy should persist timezone")

    status, _, body = c.request("GET", f"/ipxe/{mac}")
    text = body.decode()
    expect("kernel" in text and "ds=nocloud" in text, "expected linux install iPXE")
    expect("nocloud-net" not in text, "deprecated nocloud-net datasource")
    expect(r"\;s=" in text or "seed-url" in text, "nocloud semicolon must be escaped")
    expect("cloud-config-url=${seed-url}user-data" in text, "linux kernel boot must fetch HTTP user-data")
    kline = next(line for line in text.splitlines() if line.startswith("kernel "))
    expect(kline.split()[3] == "autoinstall", "autoinstall must be the first kernel arg")
    expect(kline.rstrip().endswith("---"), "kernel cmdline must end with ---")
    expect("imgargs vmlinuz " in text, "iPXE imgargs must carry autoinstall")
    expect("root-smoke-secret" not in text, "password leaked into install iPXE")
    expect(not smb_secret or smb_secret not in text, "SMB password leaked into linux iPXE")
    expect(token not in text, "user-data token leaked into iPXE")

    linux_live = machine_record(c, mid)
    status, _, _ = c.request("GET", f"/cloud-init/{mid}/user-data")
    expect(status == 404, "legacy cloud-init path must 404 while installing")
    status, _, body = c.request("GET", guest_path(linux_live, "cloud-init", "user-data"))
    expect(status == 200, f"user-data {status}")
    user_data = body.decode()
    expect(token in user_data, "cloud-init missing image seed token")
    expect("root" in user_data, "cloud-init missing vault user")
    expect("root-smoke-secret" not in user_data, "plaintext password leaked into user-data")
    expect("$6$" in user_data, "cloud-init missing password_hash")
    expect("event=imaging" in user_data, "autoinstall early-commands must ping imaging")
    expect("phy80211" in user_data, "autoinstall must unbind live Wi-Fi before netplan apply")
    expect("autoinstall-confirm.py" in user_data, "autoinstall must fetch the confirmation helper")
    expect("uefi-boot-order.py" in user_data, "autoinstall must run the UEFI boot order helper")
    expect("optional: true" in user_data, "netplan catch-all NICs must be optional")
    expect("\n  identity:" in user_data, "autoinstall must include identity")
    expect("\n  timezone:" not in user_data, "timezone must not be an autoinstall root key")
    expect("America/New_York" in user_data, "deploy timezone missing from rendered user-data")
    expect("ubuntu-server-minimal" in user_data, "linux user-data missing selected source.id")
    expect("sysrq-trigger" in user_data, "autoinstall must force reboot after phone-home")
    expect("ssh_authorized_keys: []" not in user_data, "empty ssh_authorized_keys fails Subiquity")
    expect("authorized-keys: []" not in user_data, "empty authorized-keys fails Subiquity")
    expect("--post-file=/dev/null" in user_data, "imaging wget must POST empty body")
    expect("install-log" in user_data, "autoinstall error-commands must post the installer log")
    expect("--post-data=" not in user_data, "empty --post-data= is a wget error")
    expect(
        "instance-id" in c.request("GET", guest_path(linux_live, "cloud-init", "meta-data"))[2].decode(),
        "meta-data",
    )

    status, _, body = c.request("GET", f"/api/machines/{mid}")
    detail = json.loads(body)
    expect("root-smoke-secret" not in json.dumps(detail), "password in JSON")
    expect(detail.get("account_password_set") is True, "password_set flag")

    status, _, body = c.request("POST", f"/api/machines/{mid}/events?event=imaging")
    expect(status == 200, f"imaging {status} {body!r}")
    expect(json.loads(body).get("state") == "imaging", "early-command should set imaging")
    status, _, body = c.request("GET", f"/machines/{mid}")
    expect(b">Imaging<" in body, "console should show Imaging")
    status, _, body = c.request("GET", guest_path(linux_live, "cloud-init", "user-data"))
    expect(status == 200, "imaging must keep serving user-data")
    status, _, body = c.request("GET", f"/ipxe/{mac}")
    expect("kernel" in body.decode() and "Waiting" not in body.decode(), "imaging must keep serving install iPXE")

    status, _, body = c.request(
        "POST",
        f"/api/machines/{mid}/events",
        data=json.dumps({"event": "deployed"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    expect(status == 200, f"phone_home {status} {body!r}")

    status, _, body = c.request("GET", f"/ipxe/{mac}")
    deployed_text = body.decode()
    expect("menu " in deployed_text and "choose" in deployed_text, "deployed named host should show folder menu")
    expect("Continue to next boot device" in deployed_text, "deployed menu missing continue item")
    expect("kernel" not in deployed_text, "deployed menu should not start an install")
    status, _, body = c.request("GET", f"/cloud-init/{mid}/user-data")
    expect(status == 404, "deployed must not keep serving user-data")
    status, _, body = c.request("GET", f"/machines/{mid}")
    expect(status == 200 and b">Deployed<" in body, "console should show Deployed after phone-home")
    expect(b'id="machine-save"' in body and b'name="hostname"' in body, "hostname field missing from machine actions")
    expect(b'name="timezone"' in body, "timezone dropdown missing after deploy")
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
    status, _, body = c.request("GET", f"/machines/{mid}")
    expect(b">Staged<" in body, "staged reimage should show Staged")
    status, _, body = c.request("GET", f"/ipxe/{mac}")
    expect("kernel" in body.decode(), "staged should install again")
    status, _, _ = c.request("POST", f"/machines/{mid}/abort", form={})
    expect(status in {200, 303, 302}, f"abort staged {status}")
    status, _, body = c.request("GET", f"/api/machines/{mid}")
    expect(json.loads(body).get("state") == "deployed", "aborting a staged install returns to deployed")
    status, _, body = c.request("GET", f"/ipxe/{mac}")
    aborted = body.decode()
    expect("menu " in aborted and "kernel" not in aborted, "aborted staged host should show the folder menu")
    linux_live = machine_record(c, mid)
    status, _, _ = c.request("GET", guest_path(linux_live, "cloud-init", "user-data"))
    expect(status == 404, "aborted install must not serve guest-init")
    status, _, _ = c.request(
        "POST",
        f"/machines/{mid}/deploy",
        form={"image_id": str(linux_image_id)},
    )
    expect(status in {200, 303, 302}, f"redeploy after abort {status}")
    status, _, body = c.request("POST", f"/api/machines/{mid}/events?event=imaging")
    expect(status == 200 and json.loads(body).get("state") == "imaging", "redeploy imaging callback")
    status, _, body = c.request("POST", f"/api/machines/{mid}/install-log", data=b"subiquity crashed")
    expect(status == 200 and json.loads(body).get("state") == "failed", "install log should mark the machine failed")
    status, _, body = c.request("GET", f"/ipxe/{mac}")
    failed_text = body.decode()
    expect("menu " in failed_text and "kernel" not in failed_text, "failed install should show the folder menu")
    linux_live = machine_record(c, mid)
    status, _, _ = c.request("GET", guest_path(linux_live, "cloud-init", "user-data"))
    expect(status == 404, "failed install must not serve guest-init")
    status, _, body = c.request("GET", f"/machines/{mid}")
    expect(b"Install failed" in body and b"subiquity crashed" in body, "console should show the install failure")

    status, _, body = c.request("GET", "/tftp/undionly.kpxe")
    expect(status == 200 and len(body) > 0, f"tftp http {status}")

    status, _, body = c.request("GET", "/api/machines")
    expect(status == 200, "machines after linux flow")
    linux_mac = "de:ad:be:ef:00:01"
    expect(any(m.get("mac") == linux_mac for m in json.loads(body)), "linux MAC missing")

    win_mac = "de-ad-be-ef-00-02"
    status, _, body = c.request("GET", f"/ipxe/{win_mac}")
    expect("Continuing to next boot device" in body.decode(), "windows MAC should continue to disk first")

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
    status, _, body = c.request("GET", "/api/images")
    win_created = next((img for img in json.loads(body) if img.get("os_family") == "windows"), None)
    expect(win_created is not None, "windows image missing after create")
    status, _, _ = c.request(
        "POST",
        f"/images/{win_created['id']}",
        form={
            "name": win_created["name"],
            "os_family": "windows",
            "arch": "x86_64",
            "boot_wim_path": "windows/boot.wim",
            "install_wim_path": "windows/install.wim",
            "source_id": "Windows Server 2022 SERVERSTANDARD",
            "wim_index": "2",
        },
    )
    expect(status in {200, 303, 302}, f"windows source save {status}")
    status, _, body = c.request("GET", "/api/images")
    win_saved = next((img for img in json.loads(body) if img.get("id") == win_created["id"]), None)
    expect(win_saved is not None, "windows image missing after source save")
    expect(win_saved.get("source_id") == "Windows Server 2022 SERVERSTANDARD", "windows source_id did not persist")
    expect(int(win_saved.get("wim_index") or 0) == 2, "windows wim_index did not persist")

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

    win_live = machine_record(c, win_machine["id"])
    status, _, _ = c.request("GET", f"/windows/{win_machine['id']}/unattend.xml")
    expect(status == 404, "legacy windows path must 404 while installing")
    status, _, body = c.request("GET", guest_path(win_live, "windows", "unattend.xml"))
    expect(status == 200 and b"Win-smoke-secret" in body, "unattend missing Administrator password")
    expect(b"windowsPE" in body, "unattend missing windowsPE")
    expect(b"/IMAGE/NAME" in body and b"Windows Server 2022 SERVERSTANDARD" in body, "unattend missing selected image name")
    expect(b"<Value>2</Value>" in body, "unattend missing selected wim index")
    expect(b"uefi-boot-order.ps1" in body, "unattend must run the UEFI boot order helper in specialize")
    status, _, body = c.request("GET", guest_path(win_live, "windows", "startnet.cmd"))
    expect(status == 200 and b"pxe-media" in body, "startnet missing SMB share")
    expect(b"event=imaging" in body, "WinPE startnet must ping imaging")
    expect(b"Win-smoke-secret" not in body, "windows local password leaked into startnet")
    if smb_secret:
        expect(smb_secret.encode() in body, "startnet missing SMB password")
    status, _, body = c.request("GET", guest_path(win_live, "cloudbase-init", "user-data"))
    expect(status == 200 and b"Win-smoke-secret" in body, "cloudbase-init missing password")
    expect(b"phone_home" in body, "cloudbase-init missing phone_home")

    wid = win_machine["id"]
    status, _, body = c.request("POST", f"/api/machines/{wid}/events?event=imaging")
    expect(status == 200 and json.loads(body).get("state") == "imaging", "windows imaging callback")
    status, _, body = c.request("GET", guest_path(win_live, "windows", "unattend.xml"))
    expect(status == 200, "windows unattend while imaging")
    status, _, body = c.request("GET", guest_path(win_live, "windows", "startnet.cmd"))
    expect(status == 200, "windows startnet while imaging")
    status, _, body = c.request("GET", guest_path(win_live, "cloudbase-init", "user-data"))
    expect(status == 200, "cloudbase-init while imaging")
    status, _, body = c.request("GET", f"/ipxe/{win_mac}")
    expect("wimboot" in body.decode(), "windows iPXE while imaging")

    status, _, body = c.request("POST", "/settings/smb/rotate", form={})
    expect(status in {200, 303, 302}, f"smb rotate {status}")
    status, _, body = c.request("GET", "/settings?section=smb&notice=smb-rotated")
    expect(status == 200 and b"Windows SMB password rotated" in body, "smb rotate notice missing")
    if smb_secret:
        expect(smb_secret.encode() not in body, "old SMB password leaked after rotate")
    status, _, body = c.request("GET", guest_path(win_live, "windows", "startnet.cmd"))
    expect(status == 200 and b"pxe-media" in body, "startnet after smb rotate")
    if smb_secret:
        expect(smb_secret.encode() not in body, "startnet still has old SMB password after rotate")
    status, _, body = c.request("GET", "/files?root=data")
    expect(status == 200 and b"smb.password" not in body, "smb.password listed after rotate")
    status, _, _ = c.request("GET", "/files/download?root=data&path=smb.password")
    expect(status == 404, "smb.password download after rotate")

    status, _, body = c.request(
        "POST",
        "/settings/password",
        form={
            "current_password": args.password,
            "new_password": "smokepass2",
            "confirm_password": "smokepass2",
        },
    )
    expect(status == 200 and b'action="/login"' in body, "password change must return to login")
    status, _, body = c.request("GET", "/machines")
    expect(b'data-open-dialog="add-machine"' not in body, "password change must sign the session out")

    print("SMOKE PASS")


if __name__ == "__main__":
    main()
