from pathlib import Path

from src.ganesha_exports import (
    cleanup_legacy_export_root,
    clear_reload_request,
    export_id_for,
    reload_requested,
    render_generation_exports,
    request_export_reload,
    sync_generation_exports,
)
from src.nfs_media import advertised_nfsroot, nfs_subpath


def test_nfs_subpath_accepts_generation():
    assert nfs_subpath("nfs/3/1") == "3/1"
    assert nfs_subpath(r"nfs\3\1") == "3/1"
    assert nfs_subpath("uploads/3/extracts/1") is None
    assert nfs_subpath("nfs/../etc") is None
    assert nfs_subpath("nfs/3/1/extra") is None


def test_advertised_nfsroot_uses_generation_path():
    root = advertised_nfsroot("nfs/3/1", host="192.168.2.223", export="/var/lib/pxe/images/nfs")
    assert root == "192.168.2.223:/var/lib/pxe/images/nfs/3/1"


def test_advertised_nfsroot_rejects_windows_export():
    assert advertised_nfsroot("nfs/3/1", host="pxe.test", export="C:/tmp/nfs") is None
    assert advertised_nfsroot("nfs/3/1", host="pxe.test", export="/var/lib/pxe/images/../etc") is None


def test_ganesha_conf_includes_generation_exports():
    text = (Path(__file__).resolve().parents[1] / "config" / "ganesha.conf").read_text(encoding="utf-8")
    assert "%include /var/run/ganesha/pxe-generations.conf" in text
    assert "SecType = none, sys" in text
    assert "Squash = None" in text


def test_export_id_stable_per_generation():
    assert export_id_for(3, 3) == 3013
    assert export_id_for(3, 6) == 3016


def test_render_generation_exports_paths(tmp_path: Path):
    dest = tmp_path / "nfs" / "3" / "3"
    casper = dest / "casper"
    casper.mkdir(parents=True)
    (casper / "filesystem.squashfs").write_bytes(b"sqsh")
    (dest / ".disk").mkdir()
    text = render_generation_exports(tmp_path)
    assert f'Path = "{dest.resolve().as_posix()}"' in text
    assert "Export_Id = 3013" in text
    assert "Clients = *" in text
    assert "Protocols = 3" in text


def test_sync_generation_exports_writes_once(tmp_path: Path):
    dest = tmp_path / "nfs" / "1" / "2"
    casper = dest / "casper"
    casper.mkdir(parents=True)
    (casper / "filesystem.squashfs").write_bytes(b"sqsh")
    out = tmp_path / "pxe-generations.conf"
    assert sync_generation_exports(tmp_path, dest=out) is True
    assert sync_generation_exports(tmp_path, dest=out) is False
    assert "Path =" in out.read_text(encoding="utf-8")


def test_cleanup_legacy_export_root(tmp_path: Path):
    nfs = tmp_path / "nfs"
    (nfs / "casper").mkdir(parents=True)
    (nfs / ".disk").mkdir()
    (nfs / "3" / "3" / "casper").mkdir(parents=True)
    (nfs / ".pxe-generation").write_text("3/3\n", encoding="utf-8")
    cleanup_legacy_export_root(tmp_path)
    assert not (nfs / "casper").exists()
    assert not (nfs / ".disk").exists()
    assert not (nfs / ".pxe-generation").exists()
    assert (nfs / "3" / "3" / "casper").is_dir()


def test_request_export_reload_stamp(tmp_path: Path):
    request_export_reload(tmp_path)
    assert reload_requested(tmp_path)
    clear_reload_request(tmp_path)
    assert not reload_requested(tmp_path)
