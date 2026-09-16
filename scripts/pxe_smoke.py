#!/usr/bin/env python3
"""HTTP PXE smoke against a running home-lab-pxe instance. Does not push images."""

from __future__ import annotations

import argparse
import json
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


def fail(message: str) -> None:
    print(f"SMOKE FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def expect(cond: bool, message: str) -> None:
    if not cond:
        fail(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="smokepass")
    args = parser.parse_args()
    c = Client(args.base_url)

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
    status, _, body = c.request("GET", "/files/download?path=undionly.kpxe")
    expect(status == 200 and len(body) > 0, "authenticated tftp download")

    status, _, _ = c.request(
        "POST",
        "/machines",
        form={"mac": "aa-bb-cc-dd-ee-0f", "hostname": "smoke-added"},
    )
    expect(status in {200, 303, 302}, f"add machine {status}")
    status, _, body = c.request("GET", "/api/machines")
    expect(any(m.get("mac") == "aa:bb:cc:dd:ee:0f" for m in json.loads(body)), "manually added MAC missing")

    status, _, body = c.request(
        "POST",
        "/images",
        form={
            "name": f"smoke-ubuntu-{int(time.time())}",
            "os_family": "linux",
            "arch": "x86_64",
            "kernel_path": "ubuntu/vmlinuz",
            "initrd_path": "ubuntu/initrd",
        },
    )
    expect(status in {200, 303, 302}, f"create image {status}")

    status, _, body = c.request("GET", "/api/images")
    expect(status == 200, f"/api/images after create {status}")
    linux_images = [img for img in json.loads(body) if img["os_family"] == "linux"]
    expect(linux_images, "linux image missing after create")
    linux_image_id = linux_images[-1]["id"]
    status, _, body = c.request("GET", f"/images/{linux_image_id}")
    expect(status == 200 and b"Save image" in body, "image edit page")
    status, _, _ = c.request(
        "POST",
        f"/images/{linux_image_id}",
        form={
            "name": linux_images[-1]["name"],
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
    expect("root-smoke-secret" not in text, "password leaked into install iPXE")

    status, _, body = c.request("GET", f"/cloud-init/{mid}/user-data")
    expect(status == 200, f"user-data {status}")
    user_data = body.decode()
    expect("root:root-smoke-secret" in user_data, "cloud-init missing injected root")
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
    expect("Win-smoke-secret" not in text, "password leaked into windows iPXE")

    status, _, body = c.request("GET", f"/windows/{win_machine['id']}/unattend.xml")
    expect(status == 200 and b"Win-smoke-secret" in body, "unattend missing Administrator password")
    status, _, body = c.request("GET", f"/cloudbase-init/{win_machine['id']}/user-data")
    expect(status == 200 and b"Win-smoke-secret" in body, "cloudbase-init missing password")
    expect(b"phone_home" in body, "cloudbase-init missing phone_home")

    print("SMOKE PASS")


if __name__ == "__main__":
    main()
