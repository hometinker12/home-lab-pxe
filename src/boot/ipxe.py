"""Render #!ipxe scripts. Never embed passwords or user-data."""

from __future__ import annotations

from ..models import Machine
from ..nfs_media import CASPER_NFSOPTS, advertised_nfsroot, publish_nfs_export_root
from ..settings import get_settings
from .payload import BootPayload
from .policy import ScriptKind


def _header() -> str:
    return "#!ipxe\n"


def _tokens(cmdline: str) -> list[str]:
    return [part for part in (cmdline or "").split() if part]


def _has_token(cmdline: str, prefix: str) -> bool:
    needle = prefix.lower()
    return any(token.lower().startswith(needle) for token in _tokens(cmdline))


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


def image_not_ready_script(mac_hyphen: str) -> str:
    base = get_settings().public_url
    url = f"{base}/ipxe/{mac_hyphen}"
    return (
        _header()
        + "isset ${cls} && cls ||\n"
        + "echo home-lab-pxe\n"
        + "echo Image is not ready yet. Waiting for operator.\n"
        + "sleep 5\n"
        + f"chain --replace {url} || sleep 5 && chain --replace {url}\n"
    )


def local_disk_script() -> str:
    return (
        _header()
        + "# deployed — no staged job; continue to local disk\n"
        + "exit 1 || sanboot --no-describe --drive 0x80 || exit\n"
    )


def _boot_file_url(machine: Machine, payload: BootPayload, slot: str) -> str:
    base = get_settings().public_url
    path_slot = "image.iso" if slot == "iso" else slot
    if machine.id:
        return f"{base}/install-files/{machine.id}/{path_slot}"
    return f"{base}/boot-files/{payload.image_id}/{path_slot}"


def linux_install_script(machine: Machine, payload: BootPayload) -> str:
    settings = get_settings()
    base = settings.public_url
    kernel_ok = bool((payload.kernel_path or "").strip() and (payload.initrd_path or "").strip())
    iso_ok = bool((payload.iso_path or "").strip())
    if not kernel_ok and iso_ok:
        iso = f"{base}/boot-files/{payload.image_id}/iso"
        return _header() + f"sanboot --no-describe {iso} || sanboot {iso}\n"
    seed = f"{base}/cloud-init/{machine.id}/"
    kernel = _boot_file_url(machine, payload, "kernel")
    initrd = _boot_file_url(machine, payload, "initrd")
    extra = payload.cmdline.strip()
    nfsroot = advertised_nfsroot(payload.media_relative, host=settings.nfs_host, export=settings.nfs_export)
    if nfsroot:
        try:
            publish_nfs_export_root(settings.image_root, payload.media_relative)
        except OSError:
            pass
    defaults: list[str] = []
    if nfsroot:
        if not _has_token(extra, "boot="):
            defaults.append("boot=casper")
        if not _has_token(extra, "netboot="):
            defaults.append("netboot=nfs")
        if not _has_token(extra, "nfsroot="):
            defaults.append(f"nfsroot={nfsroot}")
        if not _has_token(extra, "NFSOPTS="):
            defaults.append(f"NFSOPTS={CASPER_NFSOPTS}")
        if not _has_token(extra, "ip="):
            defaults.append("ip=dhcp")
        if not _has_token(extra, "autoinstall"):
            defaults.append("autoinstall")
        if not _has_token(extra, "cloud-config-url="):
            defaults.append("cloud-config-url=/dev/null")
    elif iso_ok:
        iso_url = _boot_file_url(machine, payload, "iso")
        if not _has_token(extra, "root="):
            defaults.append("root=/dev/ram0")
        if not _has_token(extra, "ramdisk_size="):
            defaults.append("ramdisk_size=1500000")
        if not _has_token(extra, "ip="):
            defaults.append("ip=dhcp")
        if not _has_token(extra, "iso-url="):
            defaults.append(f"iso-url={iso_url}")
        if not _has_token(extra, "url="):
            defaults.append(f"url={iso_url}")
        if not _has_token(extra, "autoinstall"):
            defaults.append("autoinstall")
        if not _has_token(extra, "cloud-config-url="):
            defaults.append("cloud-config-url=/dev/null")
    args = " ".join([part for part in (*_tokens(extra), *defaults) if part])
    nocloud = r"ds=nocloud-net\;s=${seed-url}"
    cmdline = f"{args} {nocloud}".strip() if args else nocloud
    return _header() + f"set seed-url {seed}\n" + f"kernel {kernel} {cmdline}\n" + f"initrd {initrd}\n" + "boot\n"


def windows_install_script(machine: Machine, payload: BootPayload) -> str:
    settings = get_settings()
    base = settings.public_url
    boot_ok = bool((payload.boot_wim_path or "").strip())
    iso_ok = bool((payload.iso_path or "").strip())
    if not boot_ok and iso_ok:
        iso = f"{base}/boot-files/{payload.image_id}/iso"
        return _header() + f"sanboot --no-describe {iso} || sanboot {iso}\n"
    wimboot = f"{base}/tftp/wimboot"
    boot_wim = _boot_file_url(machine, payload, "boot.wim")
    unattend = f"{base}/windows/{machine.id}/unattend.xml"
    winpeshl = f"{base}/windows/{machine.id}/winpeshl.ini"
    startnet = f"{base}/windows/{machine.id}/startnet.cmd"
    return (
        _header()
        + f"kernel {wimboot}\n"
        + f"initrd {winpeshl} winpeshl.ini\n"
        + f"initrd {startnet} startnet.cmd\n"
        + f"initrd {unattend} unattend.xml\n"
        + f"initrd {boot_wim} boot.wim\n"
        + "boot\n"
    )


def render_script(
    kind: ScriptKind,
    *,
    mac_hyphen: str,
    machine: Machine | None = None,
    payload: BootPayload | None = None,
) -> str:
    if kind == ScriptKind.wait:
        return wait_script(mac_hyphen)
    if kind == ScriptKind.image_not_ready:
        return image_not_ready_script(mac_hyphen)
    if kind == ScriptKind.local:
        return local_disk_script()
    if kind == ScriptKind.install_linux and machine is not None and payload is not None:
        return linux_install_script(machine, payload)
    if kind == ScriptKind.install_windows and machine is not None and payload is not None:
        return windows_install_script(machine, payload)
    return wait_script(mac_hyphen)
