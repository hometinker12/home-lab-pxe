"""Save operator-uploaded boot payloads under the image root."""

from __future__ import annotations

import os
import re
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


def relative_slot_path(image_id: int, slot: str, original_name: str) -> str:
    dest_name = _SLOTS.get(slot)
    if dest_name is None:
        raise UploadError("Unknown image slot")
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
    return f"uploads/{int(image_id)}/{filename}"


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
