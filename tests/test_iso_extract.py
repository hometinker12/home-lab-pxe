from pathlib import Path

import pytest

from src.iso_extract import (
    ExtractError,
    extract_linux_payloads,
    extract_windows_media,
    select_linux_members,
    validate_windows_media,
)
from src.settings import clear_settings_cache


class FakeRunner:
    def __init__(self, members: list[tuple[str, int, bool]], blobs: dict[str, bytes] | None = None) -> None:
        self.members = members
        self.blobs = blobs or {}

    def list_entries(self, iso: Path) -> list[tuple[str, int, bool]]:
        return list(self.members)

    def extract_member_bytes(self, iso: Path, member: str) -> bytes:
        return self.blobs.get(member, b"payload")

    def extract_tree(self, iso: Path, dest: Path, prefixes: tuple[str, ...] = ()) -> None:
        wanted = tuple(item.replace("\\", "/").strip("/") for item in prefixes if item.strip())
        for name, _size, is_dir in self.members:
            if is_dir:
                continue
            rel = name.replace("\\", "/")
            if wanted and not any(rel == prefix or rel.startswith(prefix + "/") for prefix in wanted):
                continue
            path = dest.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.blobs.get(name, b"media"))


def test_select_linux_casper_names():
    kernel, initrd = select_linux_members(["casper/vmlinuz.efi", "casper/initrd.lz", "README"])
    assert kernel.lower().endswith("vmlinuz.efi")
    assert initrd.lower().endswith("initrd.lz")


def test_windows_install_esd_fallback():
    spec = validate_windows_media(["setup.exe", "sources/boot.wim", "sources/install.esd"])
    assert spec.install_media.endswith("install.esd")


def test_reject_parent_members():
    with pytest.raises(ExtractError, match="Unsafe"):
        select_linux_members(["../evil", "casper/vmlinuz", "casper/initrd"])


def test_linux_extract_writes_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("PXE_IMAGE_ROOT", str(tmp_path))
    clear_settings_cache()
    iso = tmp_path / "image.iso"
    iso.write_bytes(b"iso")
    dest = tmp_path / "out"
    dest.mkdir()
    runner = FakeRunner(
        [("casper/vmlinuz", 4, False), ("casper/initrd", 4, False)],
        {"casper/vmlinuz": b"kern", "casper/initrd": b"ird"},
    )
    result = extract_linux_payloads(iso, dest, image_root=tmp_path, runner=runner)
    assert (dest / "kernel").read_bytes() == b"kern"
    assert (dest / "initrd").read_bytes() == b"ird"
    assert result.kernel_relative == "kernel"
    assert result.media_relative == ""


def test_linux_extract_publishes_casper_media(tmp_path, monkeypatch):
    monkeypatch.setenv("PXE_IMAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("PXE_MAX_EXTRACT_BYTES", "100000")
    clear_settings_cache()
    iso = tmp_path / "image.iso"
    iso.write_bytes(b"iso")
    dest = tmp_path / "out"
    dest.mkdir()
    runner = FakeRunner(
        [
            ("casper/vmlinuz", 4, False),
            ("casper/initrd", 4, False),
            ("casper/filesystem.squashfs", 8, False),
            (".disk/casper-uuid-generic", 4, False),
        ],
        {
            "casper/vmlinuz": b"kern",
            "casper/initrd": b"ird",
            "casper/filesystem.squashfs": b"squashok",
            ".disk/casper-uuid-generic": b"uuid",
        },
    )
    result = extract_linux_payloads(iso, dest, image_root=tmp_path, runner=runner)
    assert result.media_relative == "casper"
    assert (dest / "kernel").read_bytes() == b"kern"
    assert (dest / "casper" / "filesystem.squashfs").read_bytes() == b"squashok"
    assert (dest / ".disk" / "casper-uuid-generic").read_bytes() == b"uuid"


def test_windows_extract_returns_media(tmp_path, monkeypatch):
    monkeypatch.setenv("PXE_IMAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("PXE_MAX_EXTRACT_BYTES", "100000")
    clear_settings_cache()
    iso = tmp_path / "win.iso"
    iso.write_bytes(b"iso")
    dest = tmp_path / "media"
    runner = FakeRunner(
        [
            ("setup.exe", 4, False),
            ("sources/boot.wim", 4, False),
            ("sources/install.wim", 4, False),
        ]
    )
    result = extract_windows_media(iso, dest, image_root=tmp_path, runner=runner)
    assert (dest / "setup.exe").is_file()
    assert result.boot_wim_relative.endswith("boot.wim")


def test_missing_linux_members(tmp_path, monkeypatch):
    monkeypatch.setenv("PXE_IMAGE_ROOT", str(tmp_path))
    clear_settings_cache()
    iso = tmp_path / "image.iso"
    iso.write_bytes(b"iso")
    dest = tmp_path / "out"
    dest.mkdir()
    runner = FakeRunner([("README", 1, False)])
    with pytest.raises(ExtractError, match="Ubuntu live-server"):
        extract_linux_payloads(iso, dest, image_root=tmp_path, runner=runner)


def test_seven_z_bin_uses_env(monkeypatch):
    monkeypatch.setenv("PXE_7Z_BIN", "/opt/custom-7z")
    from src.settings import _seven_z_bin

    assert _seven_z_bin() == "/opt/custom-7z"


def test_seven_z_bin_falls_back_to_7zz(monkeypatch):
    monkeypatch.delenv("PXE_7Z_BIN", raising=False)
    monkeypatch.setattr("src.settings.shutil.which", lambda name: "/usr/bin/7zz" if name == "7zz" else None)
    from src.settings import _seven_z_bin

    assert _seven_z_bin() == "7zz"
