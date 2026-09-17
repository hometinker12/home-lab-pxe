from pathlib import Path

from src.nfs_media import advertised_nfsroot, casper_live_media_path, nfs_subpath, publish_nfs_export_root


def test_nfs_subpath_accepts_generation():
    assert nfs_subpath("nfs/3/1") == "3/1"
    assert nfs_subpath(r"nfs\3\1") == "3/1"
    assert nfs_subpath("uploads/3/extracts/1") is None
    assert nfs_subpath("nfs/../etc") is None
    assert nfs_subpath("nfs/3/1/extra") is None


def test_advertised_nfsroot_uses_container_export():
    root = advertised_nfsroot("nfs/3/1", host="192.168.2.223", export="/var/lib/pxe/images/nfs")
    assert root == "192.168.2.223:/var/lib/pxe/images/nfs"
    assert casper_live_media_path("nfs/3/1") == "3/1/casper"


def test_advertised_nfsroot_rejects_windows_export():
    assert advertised_nfsroot("nfs/3/1", host="pxe.test", export="C:/tmp/nfs") is None
    assert advertised_nfsroot("nfs/3/1", host="pxe.test", export="/var/lib/pxe/images/../etc") is None


def test_ganesha_export_allows_casper_anon_mount():
    text = (Path(__file__).resolve().parents[1] / "config" / "ganesha.conf").read_text(encoding="utf-8")
    assert "Clients = *" in text
    assert "SecType = none, sys" in text
    assert "Squash = None" in text
    assert "Path = /var/lib/pxe/images/nfs;" in text


def test_publish_nfs_export_root_hardlinks_casper(tmp_path: Path):
    src_casper = tmp_path / "nfs" / "3" / "3" / "casper"
    src_disk = tmp_path / "nfs" / "3" / "3" / ".disk"
    src_casper.mkdir(parents=True)
    src_disk.mkdir()
    squash = src_casper / "filesystem.squashfs"
    squash.write_bytes(b"sqsh")
    (src_disk / "info").write_text("iso\n", encoding="utf-8")
    assert publish_nfs_export_root(tmp_path, "nfs/3/3") is True
    dest = tmp_path / "nfs" / "casper" / "filesystem.squashfs"
    assert dest.is_file()
    assert dest.read_bytes() == b"sqsh"
    assert publish_nfs_export_root(tmp_path, "nfs/3/3") is False
