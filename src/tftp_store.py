"""Operator file browser for the TFTP root (path-safe)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.datastructures import UploadFile

from .paths import UnsafePathError, resolve_under
from .settings import get_settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]{1,120}$")
_MAX_ENTRIES = 500
_MAX_PARTS = 8
_STUB_MARK = b"ipxe-stub"
EXPECTED_FILES = ("undionly.kpxe", "ipxe.efi", "snponly.efi", "wimboot")


class TftpStoreError(ValueError):
    pass


def tftp_root() -> Path:
    root = get_settings().tftp_root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_tftp(relative: str) -> Path:
    text = (relative or "").strip().replace("\\", "/")
    if not text or text in {".", "./"}:
        return tftp_root()
    parts = [p for p in text.split("/") if p and p != "."]
    if any(p == ".." for p in parts) or len(parts) > _MAX_PARTS:
        raise TftpStoreError("Path is not allowed")
    joined = "/".join(parts)
    try:
        return resolve_under(tftp_root(), joined)
    except UnsafePathError as exc:
        raise TftpStoreError("Path is not allowed") from exc


def relative_from_root(path: Path) -> str:
    rel = path.resolve().relative_to(tftp_root())
    posix = rel.as_posix()
    return "" if posix == "." else posix


def safe_tftp_name(name: str) -> str:
    base = Path(name or "").name
    if base in {".", ".."} or not _SAFE_NAME.match(base):
        raise TftpStoreError("Name must be a simple relative name")
    return base


def join_relative(directory: str, name: str) -> str:
    parent = relative_from_root(resolve_tftp(directory))
    leaf = safe_tftp_name(name)
    return f"{parent}/{leaf}" if parent else leaf


def format_bytes(n: int) -> str:
    value = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if unit == "B":
            if value < 1024:
                return f"{int(value)} B"
        elif value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def is_stub(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size > 64:
            return False
        return path.read_bytes().strip() == _STUB_MARK
    except OSError:
        return False


def _mtime_label(path: Path) -> str:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return "—"
    dt = datetime.fromtimestamp(stamp)
    now = datetime.now()
    if dt.date() == now.date():
        return f"Today {dt:%H:%M}"
    if dt.date() == (now - timedelta(days=1)).date():
        return f"Yesterday {dt:%H:%M}"
    return dt.strftime("%d %b %Y %H:%M")


def _mtime_sort(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except OSError:
        return ""


def expected_files() -> list[dict]:
    root = tftp_root()
    rows: list[dict] = []
    for name in EXPECTED_FILES:
        path = root / name
        try:
            exists = path.is_file()
            size = path.stat().st_size if exists else 0
        except OSError:
            exists = False
            size = 0
        if not exists:
            state = "missing"
        elif is_stub(path):
            state = "stub"
        else:
            state = "ok"
        rows.append({"name": name, "state": state, "size_label": format_bytes(size) if exists else "—"})
    return rows


def breadcrumbs(relative: str) -> list[dict]:
    rel = relative_from_root(resolve_tftp(relative))
    crumbs = [{"name": "TFTP root", "dir": ""}]
    acc: list[str] = []
    for part in [p for p in rel.split("/") if p]:
        acc.append(part)
        crumbs.append({"name": part, "dir": "/".join(acc)})
    return crumbs


def list_tftp(relative: str = "") -> dict:
    directory = resolve_tftp(relative)
    if not directory.exists():
        raise TftpStoreError("Directory not found")
    if not directory.is_dir():
        raise TftpStoreError("Not a directory")
    dir_rel = relative_from_root(directory)
    parent = "/".join(dir_rel.split("/")[:-1]) if dir_rel else ""
    entries: list[dict] = []
    truncated = False
    try:
        children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise TftpStoreError("Cannot read TFTP directory") from exc
    for item in children:
        if len(entries) >= _MAX_ENTRIES:
            truncated = True
            break
        name = item.name
        if name in {".", ".."}:
            continue
        rel = f"{dir_rel}/{name}" if dir_rel else name
        try:
            resolved = resolve_tftp(rel)
        except TftpStoreError:
            continue
        is_dir = resolved.is_dir()
        try:
            size = 0 if is_dir else resolved.stat().st_size
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "relative": relative_from_root(resolved),
                "is_dir": is_dir,
                "size": size,
                "size_label": "—" if is_dir else format_bytes(size),
                "mtime": _mtime_label(resolved),
                "mtime_sort": _mtime_sort(resolved),
                "is_stub": False if is_dir else is_stub(resolved),
            }
        )
    return {"dir": dir_rel, "parent": parent, "entries": entries, "truncated": truncated}


def browser_context(relative: str = "") -> dict:
    error = None
    try:
        listing = list_tftp(relative)
    except TftpStoreError as exc:
        error = str(exc)
        listing = list_tftp("")
    listing["error"] = error
    listing["expected"] = expected_files()
    listing["breadcrumbs"] = breadcrumbs(listing["dir"])
    listing["path_display"] = "/" if not listing["dir"] else "/" + listing["dir"]
    listing["root"] = str(tftp_root())
    return listing


def save_tftp_upload(upload: UploadFile, *, directory: str = "") -> str:
    name = safe_tftp_name(upload.filename or "")
    relative = join_relative(directory, name)
    dest = resolve_tftp(relative)
    if dest.exists() and dest.is_dir():
        raise TftpStoreError("A folder with that name already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit = get_settings().max_upload_bytes
    written = 0
    try:
        with dest.open("wb") as handle:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise TftpStoreError("Upload exceeds PXE_MAX_UPLOAD_BYTES")
                handle.write(chunk)
    except TftpStoreError:
        dest.unlink(missing_ok=True)
        raise
    if written == 0:
        dest.unlink(missing_ok=True)
        raise TftpStoreError("Uploaded file was empty")
    return relative


def mkdir_tftp(directory: str, name: str) -> str:
    relative = join_relative(directory, name)
    dest = resolve_tftp(relative)
    if dest.exists():
        raise TftpStoreError("That name already exists")
    dest.mkdir(parents=False)
    return relative


def delete_tftp_entry(relative: str) -> None:
    path = resolve_tftp(relative)
    if path == tftp_root():
        raise TftpStoreError("Cannot delete the TFTP root")
    if not path.exists():
        raise TftpStoreError("File not found")
    if path.is_dir():
        try:
            path.rmdir()
        except OSError as exc:
            raise TftpStoreError("Folder is not empty") from exc
        return
    path.unlink()
