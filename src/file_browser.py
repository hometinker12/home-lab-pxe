"""Operator file browser for allowlisted PXE volumes (path-safe)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from starlette.datastructures import UploadFile

from .paths import UnsafePathError, resolve_under
from .settings import get_settings
from .tftp_store import TftpStoreError, format_bytes, is_stub, safe_tftp_name, source_url_for, tftp_root

DEFAULT_VOLUME = "tftp"
VOLUME_IDS = ("tftp", "images", "data")
_MAX_ENTRIES = 500
_MAX_PARTS = 16
_PROTECTED_DATA = frozenset(
    {
        "pxe.db",
        "dhcp.enabled",
        "tftp.enabled",
        "dnsmasq-pxe.conf",
        "dhcp.cmd",
        "dhcp.status",
        "smb.password",
        "smb.password.tmp",
        "smb.cmd",
    }
)
FAVORITES = (
    {
        "id": "ipxe",
        "label": "iPXE boot files",
        "hint": "ipxe.efi (default UEFI NBP), undionly.kpxe, snponly.efi, wimboot, boot.ipxe",
        "volume": "tftp",
        "dir": "",
    },
    {
        "id": "uploads",
        "label": "Image uploads",
        "hint": "ISO, kernel/initrd, WIM, and per-image extracts",
        "volume": "images",
        "dir": "uploads",
    },
    {
        "id": "nfs",
        "label": "NFS extracts",
        "hint": "Ubuntu casper / .disk published over NFS",
        "volume": "images",
        "dir": "nfs",
    },
    {
        "id": "smb",
        "label": "SMB media",
        "hint": "Windows Setup trees published over SMB",
        "volume": "images",
        "dir": "smb",
    },
    {
        "id": "seeds",
        "label": "Machine seeds",
        "hint": "Optional per-machine override of the image seed",
        "volume": "data",
        "dir": "seeds",
    },
    {
        "id": "install-seeds",
        "label": "Install snapshots",
        "hint": "Pinned image seed at Deploy (placeholders filled over HTTP for the installer)",
        "volume": "data",
        "dir": "install-seeds",
    },
)


class FileBrowserError(ValueError):
    pass


def normalize_volume(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    return text if text in VOLUME_IDS else DEFAULT_VOLUME


def parse_location(volume: str | None, directory: str | None) -> tuple[str, str]:
    vol = normalize_volume(volume)
    text = (directory or "").strip().replace("\\", "/")
    if ":" in text:
        prefix, rest = text.split(":", 1)
        if prefix.strip().lower() in VOLUME_IDS:
            vol = normalize_volume(prefix)
            text = rest
    if text in {"", "/", ".", "./"}:
        return vol, ""
    return vol, text.lstrip("/")


def volume_root(volume: str) -> Path:
    settings = get_settings()
    if volume == "images":
        root = settings.image_root
    elif volume == "data":
        root = settings.data_dir
    else:
        root = tftp_root()
        return root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def ensure_volume_layout(volume: str) -> Path:
    root = volume_root(volume)
    if volume == "images":
        for name in ("uploads", "nfs", "smb"):
            (root / name).mkdir(parents=True, exist_ok=True)
    elif volume == "data":
        (root / "seeds").mkdir(parents=True, exist_ok=True)
        (root / "install-seeds").mkdir(parents=True, exist_ok=True)
    return root


def files_href(volume: str = DEFAULT_VOLUME, directory: str = "") -> str:
    params: dict[str, str] = {}
    if volume != DEFAULT_VOLUME:
        params["root"] = volume
    if directory:
        params["dir"] = directory
    if not params:
        return "/files"
    return "/files?" + urlencode(params)


def download_href(volume: str, relative: str) -> str:
    params = {"path": relative}
    if volume != DEFAULT_VOLUME:
        params["root"] = volume
    return "/files/download?" + urlencode(params)


def path_display(volume: str, directory: str) -> str:
    rel = f"/{directory}" if directory else "/"
    return f"{volume}:{rel}"


def resolve_volume_path(volume: str, relative: str) -> Path:
    root = volume_root(normalize_volume(volume))
    text = (relative or "").strip().replace("\\", "/")
    if not text or text in {".", "./", "/"}:
        return root
    parts = [p for p in text.split("/") if p and p != "."]
    if any(p == ".." for p in parts) or len(parts) > _MAX_PARTS:
        raise FileBrowserError("Path is not allowed")
    joined = "/".join(parts)
    try:
        return resolve_under(root, joined)
    except UnsafePathError as extra:
        raise FileBrowserError("Path is not allowed") from extra


def relative_from_volume(volume: str, path: Path) -> str:
    rel = path.resolve().relative_to(volume_root(normalize_volume(volume)))
    posix = rel.as_posix()
    return "" if posix == "." else posix


def join_relative(volume: str, directory: str, name: str) -> str:
    parent = relative_from_volume(volume, resolve_volume_path(volume, directory))
    try:
        leaf = safe_tftp_name(name)
    except TftpStoreError as extra:
        raise FileBrowserError(str(extra)) from extra
    return f"{parent}/{leaf}" if parent else leaf


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


def is_protected(volume: str, relative: str) -> bool:
    text = (relative or "").strip().replace("\\", "/")
    if not text:
        return True
    if volume == "data" and text in _PROTECTED_DATA:
        return True
    return False


def _dir_is_under(current: str, favorite_dir: str) -> bool:
    if current == favorite_dir:
        return True
    if not favorite_dir:
        return False
    return current == favorite_dir or current.startswith(favorite_dir + "/")


def list_volume(volume: str, relative: str = "") -> dict:
    volume = normalize_volume(volume)
    ensure_volume_layout(volume)
    directory = resolve_volume_path(volume, relative)
    if not directory.exists():
        raise FileBrowserError("Directory not found")
    if not directory.is_dir():
        raise FileBrowserError("Not a directory")
    dir_rel = relative_from_volume(volume, directory)
    parent = "/".join(dir_rel.split("/")[:-1]) if dir_rel else ""
    entries: list[dict] = []
    truncated = False
    try:
        children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as extra:
        raise FileBrowserError("Cannot read directory") from extra
    for item in children:
        if len(entries) >= _MAX_ENTRIES:
            truncated = True
            break
        name = item.name
        if name in {".", ".."}:
            continue
        rel = f"{dir_rel}/{name}" if dir_rel else name
        if is_protected(volume, rel):
            continue
        try:
            resolved = resolve_volume_path(volume, rel)
        except FileBrowserError:
            continue
        is_dir = resolved.is_dir()
        try:
            size = 0 if is_dir else resolved.stat().st_size
        except OSError:
            continue
        entry_rel = relative_from_volume(volume, resolved)
        entries.append(
            {
                "name": name,
                "relative": entry_rel,
                "is_dir": is_dir,
                "size": size,
                "size_label": "—" if is_dir else format_bytes(size),
                "mtime": _mtime_label(resolved),
                "mtime_sort": _mtime_sort(resolved),
                "is_stub": False if is_dir else is_stub(resolved),
                "source_url": None if is_dir else source_url_for(name),
                "href": files_href(volume, entry_rel) if is_dir else download_href(volume, entry_rel),
            }
        )
    return {
        "volume": volume,
        "dir": dir_rel,
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
        "up_href": files_href(volume, parent) if dir_rel else "",
        "refresh_href": files_href(volume, dir_rel),
    }


def _volume_rows(current_volume: str) -> list[dict]:
    rows = []
    for vol_id, label, hint in (
        ("tftp", "TFTP", "iPXE boot files"),
        ("images", "Images", "ISO uploads and extracts"),
        ("data", "Data", "Machine seeds and runtime files"),
    ):
        rows.append(
            {
                "id": vol_id,
                "label": label,
                "hint": hint,
                "href": files_href(vol_id),
                "is_active": vol_id == current_volume,
            }
        )
    return rows


def _favorite_rows(current_volume: str, current_dir: str) -> list[dict]:
    rows = []
    for item in FAVORITES:
        vol = item["volume"]
        directory = item["dir"]
        rows.append(
            {
                **item,
                "href": files_href(vol, directory),
                "is_active": vol == current_volume and _dir_is_under(current_dir, directory),
            }
        )
    return rows


def browser_context(volume: str = DEFAULT_VOLUME, relative: str = "") -> dict:
    volume = normalize_volume(volume)
    error = None
    try:
        listing = list_volume(volume, relative)
    except FileBrowserError as extra:
        error = str(extra)
        listing = list_volume(DEFAULT_VOLUME, "")
        volume = listing["volume"]
    listing["error"] = error
    listing["volumes"] = _volume_rows(listing["volume"])
    listing["favorites"] = _favorite_rows(listing["volume"], listing["dir"])
    listing["path_display"] = path_display(listing["volume"], listing["dir"])
    listing["root"] = str(volume_root(listing["volume"]))
    return listing


def save_upload(upload: UploadFile, *, volume: str, directory: str = "") -> str:
    volume = normalize_volume(volume)
    relative = join_relative(volume, directory, upload.filename or "")
    if is_protected(volume, relative):
        raise FileBrowserError("That path is protected")
    dest = resolve_volume_path(volume, relative)
    if dest.exists() and dest.is_dir():
        raise FileBrowserError("A folder with that name already exists")
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
                    raise FileBrowserError("Upload exceeds PXE_MAX_UPLOAD_BYTES")
                handle.write(chunk)
    except FileBrowserError:
        dest.unlink(missing_ok=True)
        raise
    if written == 0:
        dest.unlink(missing_ok=True)
        raise FileBrowserError("Uploaded file was empty")
    return relative


def mkdir_volume(volume: str, directory: str, name: str) -> str:
    volume = normalize_volume(volume)
    relative = join_relative(volume, directory, name)
    dest = resolve_volume_path(volume, relative)
    if dest.exists():
        raise FileBrowserError("That name already exists")
    dest.mkdir(parents=False)
    return relative


def delete_entry(volume: str, relative: str) -> None:
    volume = normalize_volume(volume)
    text = (relative or "").strip().replace("\\", "/")
    if not text:
        raise FileBrowserError("Cannot delete the TFTP root" if volume == "tftp" else "Cannot delete the volume root")
    if is_protected(volume, text):
        raise FileBrowserError("Cannot delete this path")
    path = resolve_volume_path(volume, text)
    if path == volume_root(volume):
        raise FileBrowserError("Cannot delete the volume root")
    if not path.exists():
        raise FileBrowserError("File not found")
    if path.is_dir():
        try:
            path.rmdir()
        except OSError as extra:
            raise FileBrowserError("Folder is not empty") from extra
        return
    path.unlink()
