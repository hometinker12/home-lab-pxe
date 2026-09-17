"""7-Zip ISO member listing and confined extraction."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .nfs_media import casper_has_squashfs
from .settings import get_settings

LINUX_KERNELS = ("casper/vmlinuz", "casper/vmlinuz.efi")
LINUX_INITRDS = ("casper/initrd", "casper/initrd.lz", "casper/initrd.gz")


class ExtractError(ValueError):
    pass


LINUX_MEDIA_PREFIXES = ("casper", ".disk")


class ArchiveRunner(Protocol):
    def list_entries(self, iso: Path) -> list[tuple[str, int, bool]]: ...

    def extract_member_bytes(self, iso: Path, member: str) -> bytes: ...

    def extract_tree(self, iso: Path, dest: Path, prefixes: tuple[str, ...] = ()) -> None: ...


@dataclass(frozen=True)
class WindowsMediaSpec:
    setup_exe: str
    boot_wim: str
    install_media: str
    total_bytes: int


@dataclass(frozen=True)
class ExtractResult:
    kernel_relative: str = ""
    initrd_relative: str = ""
    boot_wim_relative: str = ""
    install_wim_relative: str = ""
    media_relative: str = ""
    install_media_name: str = ""


def normalize_member(name: str) -> str:
    text = (name or "").replace("\\", "/").strip()
    if not text or "\x00" in text:
        raise ExtractError("Unsafe archive member path")
    if text.startswith("/") or text.startswith("//") or (len(text) > 1 and text[1] == ":"):
        raise ExtractError("Unsafe archive member path")
    parts = [p for p in text.split("/") if p not in {"", "."}]
    if not parts or any(p == ".." for p in parts):
        raise ExtractError("Unsafe archive member path")
    return "/".join(parts)


def _lookup(members: list[str], wanted: str) -> str | None:
    target = wanted.lower()
    for member in members:
        if member.lower() == target:
            return member
    return None


def select_linux_members(members: list[str]) -> tuple[str, str]:
    normalized = [normalize_member(m) for m in members]
    kernel = next((m for name in LINUX_KERNELS if (m := _lookup(normalized, name))), None)
    initrd = next((m for name in LINUX_INITRDS if (m := _lookup(normalized, name))), None)
    if kernel is None or initrd is None:
        raise ExtractError("Ubuntu live-server payloads not found (casper/vmlinuz + casper/initrd)")
    return kernel, initrd


def _is_linux_media_member(member: str) -> bool:
    lower = member.lower()
    return lower.startswith("casper/") or lower.startswith(".disk/")


def linux_media_bytes(sizes: dict[str, int]) -> int:
    return sum(int(size) for name, size in sizes.items() if _is_linux_media_member(name))


def validate_windows_media(members: list[str], sizes: dict[str, int] | None = None) -> WindowsMediaSpec:
    normalized = [normalize_member(m) for m in members]
    setup = _lookup(normalized, "setup.exe")
    boot = _lookup(normalized, "sources/boot.wim")
    install = _lookup(normalized, "sources/install.wim") or _lookup(normalized, "sources/install.esd")
    if setup is None or boot is None or install is None:
        raise ExtractError("Windows Server payloads not found (sources/boot.wim + install.wim or install.esd)")
    size_map = sizes or {}
    total = 0
    for member in normalized:
        total += int(size_map.get(member, 0))
    return WindowsMediaSpec(setup_exe=setup, boot_wim=boot, install_media=install, total_bytes=total)


class SevenZipRunner:
    def __init__(self, binary: str | None = None, timeout: int | None = None) -> None:
        settings = get_settings()
        self.binary = binary or settings.seven_z_bin
        self.timeout = timeout if timeout is not None else settings.extract_timeout_seconds

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.binary, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise ExtractError("ISO extractor is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExtractError("ISO extraction timed out") from exc

    def list_entries(self, iso: Path) -> list[tuple[str, int, bool]]:
        proc = self._run(["l", "-ba", "-slt", str(iso)])
        if proc.returncode != 0:
            raise ExtractError("ISO is not a readable archive")
        entries: list[tuple[str, int, bool]] = []
        path = ""
        size = 0
        is_dir = False
        for raw in (proc.stdout or "").splitlines():
            line = raw.strip()
            if not line:
                if path:
                    entries.append((path, size, is_dir))
                path, size, is_dir = "", 0, False
                continue
            if line.startswith("Path = "):
                path = line[7:].strip()
            elif line.startswith("Size = "):
                try:
                    size = int(line[7:].strip() or "0")
                except ValueError:
                    size = 0
            elif line.startswith("Folder = "):
                is_dir = line[9:].strip() in {"+", "true", "True"}
        if path:
            entries.append((path, size, is_dir))
        if not entries:
            raise ExtractError("ISO is not a readable archive")
        return entries

    def extract_member_bytes(self, iso: Path, member: str) -> bytes:
        try:
            proc = subprocess.run(
                [self.binary, "e", "-so", str(iso), member],
                check=False,
                capture_output=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise ExtractError("ISO extractor is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExtractError("ISO extraction timed out") from exc
        if proc.returncode != 0 or not proc.stdout:
            raise ExtractError("ISO is not a readable archive")
        return proc.stdout

    def extract_tree(self, iso: Path, dest: Path, prefixes: tuple[str, ...] = ()) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        args = ["x", "-y", f"-o{dest}", str(iso), *prefixes]
        proc = self._run(args)
        if proc.returncode != 0:
            raise ExtractError("ISO is not a readable archive")


def list_members(iso: Path, runner: ArchiveRunner | None = None) -> list[str]:
    runner = runner or SevenZipRunner()
    names: list[str] = []
    for raw, _size, is_dir in runner.list_entries(iso):
        if is_dir:
            continue
        names.append(normalize_member(raw))
    return names


def extract_member(iso: Path, member: str, dest: Path, runner: ArchiveRunner | None = None) -> None:
    runner = runner or SevenZipRunner()
    safe_member = normalize_member(member)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = runner.extract_member_bytes(iso, safe_member)
    if not data:
        raise ExtractError("ISO is not a readable archive")
    dest.write_bytes(data)


def _copy_extracted_slot(dest: Path, member: str, slot: str) -> None:
    source = dest.joinpath(*member.split("/"))
    target = dest / slot
    if source.is_file() and source.stat().st_size > 0:
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return
    raise ExtractError("Ubuntu live-server payloads not found (casper/vmlinuz + casper/initrd)")


def extract_linux_payloads(
    iso: Path,
    dest_dir: Path,
    *,
    image_root: Path | None = None,
    runner: ArchiveRunner | None = None,
) -> ExtractResult:
    runner = runner or SevenZipRunner()
    settings = get_settings()
    root = (image_root or settings.image_root).resolve()
    dest = dest_dir.resolve()
    try:
        dest.relative_to(root)
        iso.resolve().relative_to(root)
    except ValueError as exc:
        raise ExtractError("ISO path is not allowed") from exc
    entries = runner.list_entries(iso)
    sizes: dict[str, int] = {}
    members: list[str] = []
    for name, size, is_dir in entries:
        if is_dir:
            continue
        member = normalize_member(name)
        members.append(member)
        sizes[member] = int(size)
    kernel_member, initrd_member = select_linux_members(members)
    dest.mkdir(parents=True, exist_ok=True)
    squashfs = [name for name in members if name.lower().startswith("casper/") and name.lower().endswith(".squashfs")]
    if squashfs:
        total = linux_media_bytes(sizes)
        if total > settings.max_extract_bytes:
            raise ExtractError("ISO contents exceed PXE_MAX_EXTRACT_BYTES")
        usage = shutil.disk_usage(dest.parent if dest.parent.exists() else root)
        if total and usage.free < total:
            raise ExtractError("Not enough free space to extract Ubuntu casper media")
        runner.extract_tree(iso, dest, prefixes=LINUX_MEDIA_PREFIXES)
        _copy_extracted_slot(dest, kernel_member, "kernel")
        _copy_extracted_slot(dest, initrd_member, "initrd")
    else:
        kernel_dest = dest / "kernel"
        initrd_dest = dest / "initrd"
        extract_member(iso, kernel_member, kernel_dest, runner=runner)
        extract_member(iso, initrd_member, initrd_dest, runner=runner)
    kernel_dest = dest / "kernel"
    initrd_dest = dest / "initrd"
    kernel_ok = kernel_dest.is_file() and kernel_dest.stat().st_size > 0
    initrd_ok = initrd_dest.is_file() and initrd_dest.stat().st_size > 0
    if not kernel_ok or not initrd_ok:
        raise ExtractError("Ubuntu live-server payloads not found (casper/vmlinuz + casper/initrd)")
    media = "casper" if casper_has_squashfs(dest) else ""
    return ExtractResult(kernel_relative=kernel_dest.name, initrd_relative=initrd_dest.name, media_relative=media)


def extract_windows_media(
    iso: Path,
    dest_dir: Path,
    *,
    image_root: Path | None = None,
    runner: ArchiveRunner | None = None,
) -> ExtractResult:
    runner = runner or SevenZipRunner()
    settings = get_settings()
    root = (image_root or settings.image_root).resolve()
    dest = dest_dir.resolve()
    try:
        dest.relative_to(root)
        iso.resolve().relative_to(root)
    except ValueError as exc:
        raise ExtractError("ISO path is not allowed") from exc
    entries = runner.list_entries(iso)
    sizes: dict[str, int] = {}
    members: list[str] = []
    for name, size, is_dir in entries:
        if is_dir:
            continue
        member = normalize_member(name)
        members.append(member)
        sizes[member] = size
    spec = validate_windows_media(members, sizes)
    total = spec.total_bytes or sum(sizes.values())
    if total > settings.max_extract_bytes:
        raise ExtractError("ISO contents exceed PXE_MAX_EXTRACT_BYTES")
    usage = shutil.disk_usage(dest.parent if dest.parent.exists() else root)
    if total and usage.free < total:
        raise ExtractError("Not enough free space to extract Windows media")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    runner.extract_tree(iso, dest)
    setup = dest.joinpath(*spec.setup_exe.split("/"))
    boot = dest.joinpath(*spec.boot_wim.split("/"))
    install = dest.joinpath(*spec.install_media.split("/"))
    if not setup.is_file() or not boot.is_file() or not install.is_file():
        raise ExtractError("Windows Server payloads not found (sources/boot.wim + install.wim or install.esd)")
    return ExtractResult(
        boot_wim_relative=spec.boot_wim,
        install_wim_relative=spec.install_media,
        install_media_name=Path(spec.install_media).name,
    )
