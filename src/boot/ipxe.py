"""Render #!ipxe scripts. Never embed passwords or user-data."""

from __future__ import annotations

from ..models import Image, Machine
from ..settings import get_settings
from .policy import ScriptKind


def _header() -> str:
    return "#!ipxe\n"


def wait_script(mac_hyphen: str) -> str:
    base = get_settings().public_url
    url = f"{base}/ipxe/{mac_hyphen}"
    return (
        _header()
        + "isset ${cls} && cls ||\n"
        + "echo home-lab-pxe\n"
        + "echo Unknown or waiting machine ${mac}\n"
        + "echo Waiting for operator in the web console. No disk install.\n"
        + "sleep 5\n"
        + f"chain --replace {url} || sleep 5 && chain --replace {url}\n"
    )


def local_disk_script() -> str:
    return (
        _header()
        + "# deployed — no staged job; continue to local disk\n"
        + "exit 1 || sanboot --no-describe --drive 0x80 || exit\n"
    )


def linux_install_script(machine: Machine, image: Image) -> str:
    settings = get_settings()
    base = settings.public_url
    seed = f"{base}/cloud-init/{machine.id}/"
    kernel = f"{base}/boot-files/{image.id}/kernel"
    initrd = f"{base}/boot-files/{image.id}/initrd"
    extra = image.cmdline.strip()
    cmdline = f"ds=nocloud-net;s={seed}"
    if extra:
        cmdline = f"{extra} {cmdline}"
    return _header() + f"kernel {kernel} {cmdline}\n" + f"initrd {initrd}\n" + "boot\n"


def windows_install_script(machine: Machine, image: Image) -> str:
    settings = get_settings()
    base = settings.public_url
    wimboot = f"{base}/tftp/wimboot"
    boot_wim = f"{base}/boot-files/{image.id}/boot.wim"
    unattend = f"{base}/windows/{machine.id}/unattend.xml"
    return (
        _header()
        + f"kernel {wimboot}\n"
        + f"initrd {boot_wim} boot.wim\n"
        + f"initrd {unattend} unattend.xml\n"
        + "boot\n"
    )


def render_script(
    kind: ScriptKind, *, mac_hyphen: str, machine: Machine | None = None, image: Image | None = None
) -> str:
    if kind == ScriptKind.wait:
        return wait_script(mac_hyphen)
    if kind == ScriptKind.local:
        return local_disk_script()
    if kind == ScriptKind.install_linux and machine is not None and image is not None:
        return linux_install_script(machine, image)
    if kind == ScriptKind.install_windows and machine is not None and image is not None:
        return windows_install_script(machine, image)
    return wait_script(mac_hyphen)
