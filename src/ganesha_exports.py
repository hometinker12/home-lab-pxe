"""Render nfs-ganesha EXPORT blocks for each extracted Ubuntu generation.

Ganesha NFSv3 MNT of a subdirectory of an export Path returns AUTH_NULL EACCES.
Each nfs/{id}/{rev} tree with casper squashfs gets its own Path so casper can
mount that directory (casper/ + .disk/ at the export root).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .nfs_media import casper_has_squashfs

GENERATIONS_CONF = Path("/var/run/ganesha/pxe-generations.conf")
RELOAD_STAMP = ".pxe-reload-exports"

_EXPORT_TEMPLATE = """
EXPORT {{
    Export_Id = {export_id};
    Path = "{path}";
    Pseudo = "{path}";
    Access_Type = RO;
    Protocols = 3;
    Transports = TCP;
    SecType = none, sys;
    Squash = All;
    Anonymous_Uid = 65534;
    Anonymous_Gid = 65534;
    Disable_ACL = true;
    CLIENT {{
        Clients = *;
        Access_Type = RO;
        Protocols = 3;
        Transports = TCP;
        SecType = none, sys;
        Squash = All;
        Anonymous_Uid = 65534;
        Anonymous_Gid = 65534;
    }}
    FSAL {{
        Name = VFS;
    }}
}}
"""


def export_id_for(image_id: int, revision: int) -> int:
    n = 10 + int(image_id) * 1000 + int(revision)
    if n < 2:
        return 2
    if n > 65535:
        return 2 + (int(image_id) * 1000 + int(revision)) % 65533
    return n


def iter_nfs_generations(image_root: Path) -> list[tuple[int, int, Path]]:
    root = (image_root / "nfs").resolve()
    if not root.is_dir():
        return []
    found: list[tuple[int, int, Path]] = []
    for image_dir in sorted(root.iterdir()):
        if not image_dir.is_dir() or not image_dir.name.isdigit():
            continue
        for gen_dir in sorted(image_dir.iterdir()):
            if not gen_dir.is_dir() or not gen_dir.name.isdigit():
                continue
            resolved = gen_dir.resolve()
            if root not in resolved.parents:
                continue
            if not casper_has_squashfs(resolved):
                continue
            found.append((int(image_dir.name), int(gen_dir.name), resolved))
    return found


def render_generation_exports(image_root: Path) -> str:
    blocks = ["# Generated; do not edit."]
    for image_id, revision, path in iter_nfs_generations(image_root):
        posix = path.as_posix()
        if '"' in posix:
            continue
        blocks.append(
            _EXPORT_TEMPLATE.format(
                export_id=export_id_for(image_id, revision),
                path=posix,
            ).strip()
        )
    return "\n\n".join(blocks) + "\n"


def sync_generation_exports(image_root: Path, dest: Path = GENERATIONS_CONF) -> bool:
    text = render_generation_exports(image_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    previous = dest.read_text(encoding="utf-8") if dest.is_file() else None
    if previous == text:
        return False
    dest.write_text(text, encoding="utf-8")
    return True


def request_export_reload(image_root: Path) -> None:
    stamp = image_root / "nfs" / RELOAD_STAMP
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("1\n", encoding="utf-8")


def reload_requested(image_root: Path) -> bool:
    return (image_root / "nfs" / RELOAD_STAMP).is_file()


def clear_reload_request(image_root: Path) -> None:
    stamp = image_root / "nfs" / RELOAD_STAMP
    try:
        stamp.unlink()
    except OSError:
        return


def cleanup_legacy_export_root(image_root: Path) -> None:
    """Remove casper/.disk hardlinks left at nfs/ from the old single-export layout."""
    nfs = image_root / "nfs"
    marker = nfs / ".pxe-generation"
    if not marker.is_file():
        return
    for name in ("casper", ".disk"):
        path = nfs / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
    try:
        marker.unlink()
    except OSError:
        return
