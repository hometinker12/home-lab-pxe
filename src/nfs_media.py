"""Linux casper media published under image_root/nfs and advertised as nfsroot=."""

from __future__ import annotations

import re
from pathlib import Path

_NFS_GENERATION = re.compile(r"^nfs/(\d+)/(\d+)$")
NFS_GENERATION_PREFIX = "nfs/"
LINUX_HTTP_GENERATION_PREFIX = "linux/"
# Casper's klibc nfsmount treats commas in nfsroot= as the export path and rejects
# mountport=. Mountd is always looked up via rpcbind on TCP/UDP 111.
CASPER_NFSOPTS = "vers=3,tcp,port=2049"


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
    """Return host:export/{id}/{rev} matching a per-generation Ganesha Path."""
    sub = nfs_subpath(media_relative)
    if sub is None:
        return None
    server = (host or "").strip()
    base = (export or "").replace("\\", "/").strip().rstrip("/")
    if not server or not base.startswith("/") or ".." in base.split("/"):
        return None
    return f"{server}:{base}/{sub}"


def casper_has_squashfs(dest: Path) -> bool:
    casper = dest / "casper"
    if not casper.is_dir():
        return False
    return any(child.is_file() and child.suffix.lower() == ".squashfs" for child in casper.iterdir())
