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

    status, _, body = c.request("GET", "/api/machines")
    expect(status == 200, f"/api/machines {status} {body[:200]!r}")
    machines = json.loads(body)
    expect(machines, "no machines registered")
    machine = machines[0]
    mid = machine["id"]
    expect(machine["state"] == "pending", f"state {machine['state']}")

    status, _, body = c.request("GET", "/api/images")
    expect(status == 200, f"/api/images {status}")
    images = json.loads(body)
    expect(images, "no images")
    image_id = str(images[-1]["id"])

    status, _, _ = c.request(
        "POST",
        f"/machines/{mid}/deploy",
        form={"image_id": image_id, "username": "root", "password": "root-smoke-secret"},
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
