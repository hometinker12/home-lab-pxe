from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.boot.uefi_order import (
    DISK_WORDS,
    NETWORK_WORDS,
    classify,
    is_ipv4_network,
    linux_helper_program,
    parse_efibootmgr,
    plan_efibootmgr,
    reorder,
    windows_helper_script,
    word_pattern,
)

PXE4 = "UEFI: PXE IPv4 Intel(R) Ethernet Connection (7) I219-LM"
PXE6 = "UEFI: PXE IPv6 Intel(R) Ethernet Connection (7) I219-LM"
HTTP4 = "UEFI: HTTP IPv4 Intel(R) Ethernet Connection (7) I219-LM"
USB = "UEFI: SanDisk USB"

OLD_FORMAT = (
    "BootCurrent: 0003\n"
    "Timeout: 1 seconds\n"
    "BootOrder: 0000,0001,0002,0003,0004\n"
    "Boot0000* ubuntu\tHD(1,GPT,0f1e,0x800,0x100000)/File(\\EFI\\ubuntu\\shimx64.efi)\n"
    f"Boot0001* {PXE6}\tPciRoot(0x0)/Pci(0x1f,0x6)/MAC(001122334455,0)/IPv6([::]:<->[::]:,0,0)\n"
    f"Boot0002* {HTTP4}\tPciRoot(0x0)/Pci(0x1f,0x6)/MAC(001122334455,0)/IPv4(0.0.0.0,0,DHCP)/Uri()\n"
    f"Boot0003* {PXE4}\tPciRoot(0x0)/Pci(0x1f,0x6)/MAC(001122334455,0)/IPv4(0.0.0.0,0,DHCP)\n"
    f"Boot0004  {USB}\tPciRoot(0x0)/Pci(0x14,0x0)/USB(1,0)\n"
)

V18_FORMAT = (
    "Timeout: 2 seconds\n"
    "BootOrder: 0002,0000,0001,0005\n"
    "Boot0000* Windows Boot Manager  HD(1,GPT,aa11,0x800,0x32000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n"
    "      dp: 04 01 2a 00 01 00 00 00 00 08 00 00 00 00 00 00\n"
    "Boot0001* UEFI: PXE IPv4 Realtek PCIe GBE Family Controller  "
    "PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)/MAC(aabbccddeeff,0)/IPv4(0.0.0.0,0,DHCP)\n"
    "Boot0002* ubuntu  HD(1,GPT,bb22,0x800,0x100000)/File(\\EFI\\ubuntu\\shimx64.efi)\n"
    "Boot0003  Old entry  VenHw(99e275e7-75a0-4b37-a2e6-c5385e6c00cb)\n"
    "      data: 01 00 00 00\n"
)


# --- classify / is_ipv4_network ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (PXE4, "network"),
        (PXE6, "network"),
        (HTTP4, "network"),
        ("ubuntu", "disk"),
        ("Windows Boot Manager", "disk"),
        (USB, "other"),
        ("Onboard NIC", "network"),
        ("PLANET drive", "other"),
        ("Orlando", "other"),
        ("Picnic", "other"),
        ("UEFI Network boot via shim", "network"),
    ],
)
def test_classify_descriptions(description, expected):
    assert classify(description) == expected


def test_classify_device_paths_and_precedence():
    assert classify("Boot0007", "PciRoot(0x0)/MAC(001122334455,0)") == "network"
    assert classify("", "PciRoot(0x0)/Uri(http://x/boot.efi)") == "network"
    assert classify("mystery", "HD(1,GPT,x)/File(\\EFI\\boot.efi)") == "disk"
    # Network checked first even when the path also points at a disk.
    assert classify("UEFI: PXE IPv4", "HD(1,GPT,x)/MAC(00,0)") == "network"
    assert classify("", "") == "other"


def test_is_ipv4_network():
    assert is_ipv4_network(PXE4)
    assert is_ipv4_network("UEFI PXE")
    assert not is_ipv4_network(PXE6)
    assert not is_ipv4_network(HTTP4)
    assert not is_ipv4_network("Network")


# --- parse_efibootmgr ---------------------------------------------------------------------------------------


def test_parse_old_format():
    order, current, entries = parse_efibootmgr(OLD_FORMAT)
    assert order == ["0000", "0001", "0002", "0003", "0004"]
    assert current == "0003"
    assert entries[0] == ("0000", "ubuntu", "HD(1,GPT,0f1e,0x800,0x100000)/File(\\EFI\\ubuntu\\shimx64.efi)")
    assert entries[3][1] == PXE4
    assert entries[4] == ("0004", USB, "PciRoot(0x0)/Pci(0x14,0x0)/USB(1,0)")


def test_parse_v18_format_missing_current_and_inactive():
    order, current, entries = parse_efibootmgr(V18_FORMAT)
    assert order == ["0002", "0000", "0001", "0005"]
    assert current is None
    ids = [entry[0] for entry in entries]
    assert ids == ["0000", "0001", "0002", "0003"]
    assert entries[0][1] == "Windows Boot Manager"
    assert entries[0][2].startswith("HD(1,GPT")
    assert entries[1][1] == "UEFI: PXE IPv4 Realtek PCIe GBE Family Controller"
    assert entries[1][2].startswith("PciRoot(")
    assert entries[3] == ("0003", "Old entry", "VenHw(99e275e7-75a0-4b37-a2e6-c5385e6c00cb)")


def test_parse_description_without_path():
    _, _, entries = parse_efibootmgr("BootOrder: 0001\nBoot0001* UEFI: PXE IPv4\n")
    assert entries == [("0001", "UEFI: PXE IPv4", "")]


# --- reorder ------------------------------------------------------------------------------------------------

DELL = [
    ("0000", "ubuntu"),
    ("0001", PXE6),
    ("0002", HTTP4),
    ("0003", PXE4),
    ("0004", USB),
]


def test_reorder_pxe_without_current():
    assert reorder(DELL, "pxe") == ["0003", "0000", "0001", "0002", "0004"]


def test_reorder_pxe_current_ipv4():
    assert reorder(DELL, "pxe", current="0003") == ["0003", "0000", "0001", "0002", "0004"]


def test_reorder_pxe_current_ipv6_promoted():
    assert reorder(DELL, "pxe", current="0001") == ["0001", "0003", "0000", "0002", "0004"]


def test_reorder_pxe_current_not_network_ignored():
    assert reorder(DELL, "pxe", current="0000") == ["0003", "0000", "0001", "0002", "0004"]


def test_reorder_disk():
    assert reorder(DELL, "disk", current="0003") == ["0000", "0004", "0001", "0002", "0003"]


def test_reorder_no_change_cases():
    assert reorder([("0000", "ubuntu"), ("0001", USB)], "pxe") is None
    assert reorder([("0000", "ubuntu"), ("0001", USB)], "disk") is None
    already = [("0003", PXE4), ("0000", "ubuntu"), ("0001", PXE6)]
    assert reorder(already, "pxe") is None
    assert reorder(DELL, "bogus") is None


def test_plan_keeps_unlisted_ids():
    assert plan_efibootmgr(V18_FORMAT, "pxe") == ["0001", "0002", "0000", "0005"]
    assert plan_efibootmgr(V18_FORMAT, "disk") == ["0002", "0000", "0005", "0001"]
    assert plan_efibootmgr(OLD_FORMAT, "pxe") == ["0003", "0000", "0001", "0002", "0004"]
    assert plan_efibootmgr("Boot0001* ubuntu\tHD(1)\n", "pxe") is None


# --- Linux helper program -----------------------------------------------------------------------------------


def test_linux_helper_compiles_and_bakes_url():
    program = linux_helper_program("http://pxe.test:8080/")
    compile(program, "uefi-boot-order.py", "exec")
    assert 'PUBLIC_URL = "http://pxe.test:8080"' in program
    assert "src." not in program


class _Stub:
    def __init__(self, status: int):
        self.status = status
        self.requests: list[tuple[str, str]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                stub.requests.append(("POST", self.path))
                self.send_response(stub.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _run_linux_helper(tmp_path, url, policy, *, efi_dir=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    listing = tmp_path / "efibootmgr.txt"
    listing.write_text(OLD_FORMAT, encoding="utf-8")
    log = tmp_path / "efibootmgr.log"
    fake = bin_dir / "efibootmgr"
    fake.write_text(
        '#!/bin/sh\necho "$@" >> "$FAKE_EFI_LOG"\nif [ "$1" = "-v" ]; then cat "$FAKE_EFI_OUTPUT"; fi\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    script = tmp_path / "helper.py"
    script.write_text(linux_helper_program(url), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["FAKE_EFI_LOG"] = str(log)
    env["FAKE_EFI_OUTPUT"] = str(listing)
    env["PXE_UEFI_ORDER_EFI_DIR"] = str(tmp_path if efi_dir else tmp_path / "no-efi")
    result = subprocess.run(
        [sys.executable, str(script), policy, "7"], env=env, capture_output=True, text=True, timeout=60
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


linux_only = pytest.mark.skipif(os.name == "nt", reason="fake efibootmgr is a POSIX shell script")


@linux_only
@pytest.mark.parametrize("status", [200, 409])
def test_linux_helper_pxe_applies_after_gate(tmp_path, status):
    with _Stub(status) as stub:
        result, calls = _run_linux_helper(tmp_path, stub.url + "/", "pxe")
    assert result.returncode == 0
    assert stub.requests == [("POST", "/api/machines/7/events")]
    assert calls == ["-v", "-o 0003,0000,0001,0002,0004"]


@linux_only
def test_linux_helper_pxe_gate_failure_leaves_order(tmp_path):
    with _Stub(500) as stub:
        result, calls = _run_linux_helper(tmp_path, stub.url, "pxe")
    assert result.returncode == 0
    assert stub.requests == [("POST", "/api/machines/7/events")]
    assert calls == ["-v"]


@linux_only
def test_linux_helper_disk_skips_gate(tmp_path):
    with _Stub(200) as stub:
        result, calls = _run_linux_helper(tmp_path, stub.url, "disk")
    assert result.returncode == 0
    assert stub.requests == []
    assert calls == ["-v", "-o 0000,0004,0001,0002,0003"]


@linux_only
def test_linux_helper_without_efi_is_noop(tmp_path):
    with _Stub(200) as stub:
        result, calls = _run_linux_helper(tmp_path, stub.url, "pxe", efi_dir=False)
    assert result.returncode == 0
    assert stub.requests == []
    assert calls == []


@linux_only
def test_linux_helper_rejects_bad_args(tmp_path):
    with _Stub(200) as stub:
        result, calls = _run_linux_helper(tmp_path, stub.url, "wipe")
    assert result.returncode == 0
    assert calls == []


# --- Windows helper script ----------------------------------------------------------------------------------

BCDEDIT_FIRMWARE = r"""
Firmware Boot Manager
---------------------
identifier              {fwbootmgr}
displayorder            {bootmgr}
                        {aaaa-1}
                        {aaaa-2}
                        {aaaa-3}
timeout                 2

Windows Boot Manager
--------------------
identifier              {bootmgr}
device                  partition=\Device\HarddiskVolume1
path                    \EFI\Microsoft\Boot\bootmgfw.efi
description             Windows Boot Manager
locale                  en-US

Firmware Application (101fffff)
-------------------------------
identifier              {aaaa-1}
description             UEFI: PXE IPv6 Intel(R) Ethernet Connection

Firmware Application (101fffff)
-------------------------------
identifier              {aaaa-2}
description             UEFI: PXE IPv4 Intel(R) Ethernet Connection

Firmware Application (101fffff)
-------------------------------
identifier              {aaaa-3}
description             UEFI: SanDisk USB
"""


def test_windows_script_uses_shared_patterns():
    script = windows_helper_script("http://pxe.test:8080/")
    assert "'(?i)" + word_pattern(NETWORK_WORDS) + "'" in script
    assert "'(?i)" + word_pattern(DISK_WORDS) + "'" in script
    assert "$PxeBase = 'http://pxe.test:8080'" in script
    assert "exit 0" in script


_POWERSHELL = shutil.which("pwsh") or (shutil.which("powershell") if os.name == "nt" else None)


def _run_windows_helper(tmp_path, url, policy):
    helper = tmp_path / "helper.ps1"
    helper.write_text(windows_helper_script(url), encoding="utf-8")
    fixture = tmp_path / "bcdedit.txt"
    fixture.write_text(BCDEDIT_FIRMWARE, encoding="utf-8")
    log = tmp_path / "bcdedit.log"
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        "$env:PXE_UEFI_ORDER_NO_MAIN = '1'\n"
        f". '{helper}'\n"
        "function Invoke-Bcd {\n"
        "    param([string[]]$BcdArgs)\n"
        f"    if ($BcdArgs[0] -eq '/enum') {{ Get-Content -LiteralPath '{fixture}' }}\n"
        f"    else {{ Add-Content -LiteralPath '{log}' -Value ($BcdArgs -join ' ') }}\n"
        "}\n"
        f"try {{ Invoke-UefiBootOrder -Policy '{policy}' -MachineId '7' }}\n"
        f"catch {{ Add-Content -LiteralPath '{log}' -Value ('ERROR ' + $_) }}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    calls = log.read_text(encoding="utf-8-sig").splitlines() if log.exists() else []
    return result, calls


powershell_only = pytest.mark.skipif(_POWERSHELL is None, reason="PowerShell not installed")


@powershell_only
@pytest.mark.parametrize("status", [200, 409])
def test_windows_helper_pxe_applies_after_gate(tmp_path, status):
    with _Stub(status) as stub:
        result, calls = _run_windows_helper(tmp_path, stub.url, "pxe")
    assert result.returncode == 0, result.stderr
    assert stub.requests == [("POST", "/api/machines/7/events")]
    assert calls == ["/set {fwbootmgr} displayorder {aaaa-2} {bootmgr} {aaaa-1} {aaaa-3}"]


@powershell_only
def test_windows_helper_pxe_gate_failure_leaves_order(tmp_path):
    with _Stub(500) as stub:
        result, calls = _run_windows_helper(tmp_path, stub.url, "pxe")
    assert result.returncode == 0, result.stderr
    assert stub.requests == [("POST", "/api/machines/7/events")]
    assert calls == []


@powershell_only
def test_windows_helper_disk_skips_gate(tmp_path):
    with _Stub(200) as stub:
        result, calls = _run_windows_helper(tmp_path, stub.url, "disk")
    assert result.returncode == 0, result.stderr
    assert stub.requests == []
    assert calls == ["/set {fwbootmgr} displayorder {bootmgr} {aaaa-3} {aaaa-1} {aaaa-2}"]


# --- routes -------------------------------------------------------------------------------------------------


def test_uefi_boot_order_routes(client):
    py = client.get("/boot-files/uefi-boot-order.py")
    assert py.status_code == 200
    assert py.headers["cache-control"] == "no-store"
    compile(py.text, "uefi-boot-order.py", "exec")
    assert "http://pxe.test:8080" in py.text

    ps1 = client.get("/boot-files/uefi-boot-order.ps1")
    assert ps1.status_code == 200
    assert ps1.headers["cache-control"] == "no-store"
    assert ps1.headers["content-type"].startswith("text/plain")
    assert "http://pxe.test:8080" in ps1.text
