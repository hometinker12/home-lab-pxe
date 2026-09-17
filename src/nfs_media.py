"""Linux casper media published under image_root/nfs and advertised as nfsroot=."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_NFS_GENERATION = re.compile(r"^nfs/(\d+)/(\d+)$")
NFS_GENERATION_PREFIX = "nfs/"
LINUX_HTTP_GENERATION_PREFIX = "linux/"
# Casper's klibc nfsmount treats commas in nfsroot= as the export path and rejects
# mountport=. Mountd is always looked up via rpcbind on TCP/UDP 111.
CASPER_NFSOPTS = "vers=3,tcp,port=2049"
_EXPORT_ROOT_DIRS = ("casper", ".disk")
_GENERATION_MARKER = ".pxe-generation"


def nfs_generation(image_id: int, revision: int) -> str:
    return f"nfs/{int(image_id)}/{int(revision)}"


def linux_http_generation(image_id: int, revision: int) -> str:
    return f"linux/{int(image_id)}/{int(revision)}"


def nfs_subpath(media_relative: str) -> str | None:
    text = (media_relative or "").replace("\\", "/").strip()
    match = _NFS_GENERATION.fullmatch(text)
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def advertised_nfsroot(media_relative: str, *, host: str, export: str) -> str | None:
    """Return host:export for casper nfsroot= (export root, not nfs/{id}/{rev}).

    Ganesha NFSv3 MNT of a subdirectory of Path is AUTH_NULL EACCES, and Linux
    nfsmount of a nested Ganesha Path returns EPERM. Casper files are hardlinked
    at the export root so default LIVE_MEDIA_PATH=casper works.
    """
    if nfs_subpath(media_relative) is None:
        return None
    server = (host or "").strip()
    base = (export or "").replace("\\", "/").strip().rstrip("/")
    if not server or not base.startswith("/") or ".." in base.split("/"):
        return None
    return f"{server}:{base}"


def casper_live_media_path(media_relative: str) -> str | None:
    sub = nfs_subpath(media_relative)
    if sub is None:
        return None
    return f"{sub}/casper"


def casper_has_squashfs(dest: Path) -> bool:
    casper = dest / "casper"
    if not casper.is_dir():
        return False
    return any(child.is_file() and child.suffix.lower() == ".squashfs" for child in casper.iterdir())


def _remove_export_dir(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)


def _link_or_copy(src: Path, dest: Path) -> None:
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def publish_nfs_export_root(image_root: Path, media_relative: str) -> bool:
    """Hardlink nfs/{id}/{rev}/casper and .disk onto the Ganesha export root."""
    sub = nfs_subpath(media_relative)
    if sub is None:
        return False
    nfs_root = image_root / "nfs"
    src = nfs_root / sub
    if not casper_has_squashfs(src):
        return False
    marker = nfs_root / _GENERATION_MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == sub and casper_has_squashfs(nfs_root):
        return False
    for name in _EXPORT_ROOT_DIRS:
        origin = src / name
        dest = nfs_root / name
        if dest.exists() or dest.is_symlink():
            _remove_export_dir(dest)
        if not origin.is_dir():
            continue
        dest.mkdir(parents=True)
        for child in origin.iterdir():
            if child.is_file() and not child.is_symlink():
                _link_or_copy(child, dest / child.name)
    marker.write_text(sub + "\n", encoding="utf-8")
    return True
