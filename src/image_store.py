"""Save operator-uploaded boot payloads under the image root."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from starlette.datastructures import UploadFile

from .paths import UnsafePathError, resolve_under
from .settings import get_settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]{1,120}$")
_SLOTS = {
    "kernel": "kernel",
    "initrd": "initrd",
    "boot.wim": "boot.wim",
    "install.wim": "install.wim",
    "iso": "image",
}


class UploadError(ValueError):
    pass


def safe_upload_filename(name: str) -> str:
    base = Path(name or "").name
    if not _SAFE_NAME.match(base):
        raise UploadError("Upload filename must be a simple relative name")
    return base


def relative_slot_path(owner_id: int, slot: str, original_name: str, *, base: str = "uploads") -> str:
    if base not in {"uploads", "isos"}:
        raise UploadError("Unknown upload area")
    dest_name = _SLOTS.get(slot)
    if dest_name is None:
        raise UploadError("Unknown image slot")
    if base == "isos" and slot == "iso":
        dest_name = "source"
    suffix = Path(safe_upload_filename(original_name)).suffix
    if slot in {"boot.wim", "install.wim"}:
        filename = dest_name
    elif slot == "iso":
        ext = suffix.lower() if suffix.lower() in {".iso", ".img"} else ".iso"
        filename = dest_name + ext
    elif suffix and _SAFE_NAME.match(dest_name + suffix):
        filename = dest_name + suffix
    else:
        filename = dest_name
    return f"{base}/{int(owner_id)}/{filename}"


def file_size(relative: str) -> int:
    if not relative:
        return 0
    try:
        path = resolve_under(get_settings().image_root, relative)
        return path.stat().st_size if path.is_file() else 0
    except (UnsafePathError, OSError):
        return 0


def save_upload_file(upload: UploadFile, relative: str) -> str:
    settings = get_settings()
    try:
        dest = resolve_under(settings.image_root, relative)
    except UnsafePathError as exc:
        raise UploadError("Upload path is not allowed") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    limit = settings.max_upload_bytes
    written = 0
    try:
        with tmp.open("wb") as handle:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise UploadError("Upload exceeds PXE_MAX_UPLOAD_BYTES")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if written == 0:
            raise UploadError("Uploaded file was empty")
        os.replace(tmp, dest)
    except UploadError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return relative.replace("\\", "/")


def has_upload(upload: UploadFile | None) -> bool:
    if upload is None:
        return False
    name = (upload.filename or "").strip()
    return bool(name)


def _remove(relative: str) -> None:
    try:
        path = resolve_under(get_settings().image_root, relative)
    except UnsafePathError:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.is_file():
        path.unlink(missing_ok=True)


def _prune_empty(relative: str) -> None:
    try:
        resolve_under(get_settings().image_root, relative).rmdir()
    except (UnsafePathError, OSError):
        pass


def _owned_upload(relative: str, prefix: str) -> str:
    text = (relative or "").replace("\\", "/").strip()
    if not text.startswith(prefix) or "/extracts/" in text:
        return ""
    # The prefix only proves ownership when no segment can climb out of it.
    if any(part in {"", ".", ".."} for part in text[len(prefix) :].split("/")):
        return ""
    return text


def remove_template_files(image_id: int, owned_paths: tuple[str, ...] = ()) -> None:
    """Delete a template's seed and its own uploads. Shared ISO media under uploads/{id}/extracts stays."""
    prefix = f"uploads/{int(image_id)}/"
    for name in ("user-data", "unattend.xml"):
        _remove(prefix + name)
    for relative in owned_paths:
        owned = _owned_upload(relative, prefix)
        if owned:
            _remove(owned)
    _prune_empty(prefix)


def remove_iso_tree(iso_id: int, legacy_paths: tuple[str, ...] = ()) -> None:
    """Delete an ISO's upload, extracts, NFS tree, and SMB tree."""
    ident = int(iso_id)
    for relative in (f"isos/{ident}", f"smb/{ident}", f"nfs/{ident}"):
        _remove(relative)
    legacy = f"uploads/{ident}/"
    if any((path or "").replace("\\", "/").startswith(legacy + "extracts/") for path in legacy_paths):
        _remove(legacy + "extracts")
    for relative in legacy_paths:
        owned = _owned_upload(relative, legacy)
        if owned:
            _remove(owned)
    _prune_empty(legacy)
