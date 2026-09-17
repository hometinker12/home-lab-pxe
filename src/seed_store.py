"""Atomic image and machine guest-init seed files."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import OsFamily
from .paths import UnsafePathError, resolve_under
from .settings import get_settings

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"


class SeedError(ValueError):
    pass


def factory_seed_text(os_family: OsFamily | str) -> str:
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    name = "windows-unattend.xml" if family == OsFamily.windows.value else "ubuntu-user-data"
    return (_DEFAULTS_DIR / name).read_text(encoding="utf-8")


def image_seed_relative(image_id: int, os_family: OsFamily | str) -> str:
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    name = "unattend.xml" if family == OsFamily.windows.value else "user-data"
    return f"uploads/{int(image_id)}/{name}"


def machine_seed_relative(machine_id: int, os_family: OsFamily | str) -> str:
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    name = "unattend.xml" if family == OsFamily.windows.value else "user-data"
    return f"seeds/{int(machine_id)}/{name}"


def snapshot_seed_relative(machine_id: int, instance_id: str, os_family: OsFamily | str) -> str:
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    name = "unattend.xml" if family == OsFamily.windows.value else "user-data"
    return f"install-seeds/{int(machine_id)}/{instance_id}/{name}"


def _read(root: Path, relative: str) -> str:
    if not relative:
        return ""
    try:
        path = resolve_under(root, relative)
    except UnsafePathError as exc:
        raise SeedError("Seed path is not allowed") from exc
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SeedError("Cannot read seed file") from exc


def read_image_seed(image_id: int, os_family: OsFamily | str) -> str:
    return _read(get_settings().image_root, image_seed_relative(image_id, os_family))


def read_machine_seed(machine_id: int, os_family: OsFamily | str) -> str:
    return _read(get_settings().data_dir, machine_seed_relative(machine_id, os_family))


def read_seed(root: Path, relative: str) -> str:
    return _read(root, relative)


def write_seed(root: Path, relative: str, body: str, *, max_bytes: int | None = None) -> str:
    limit = max_bytes if max_bytes is not None else get_settings().max_seed_bytes
    data = body.encode("utf-8")
    if len(data) > limit:
        raise SeedError("Seed file exceeds PXE_MAX_SEED_BYTES")
    try:
        dest = resolve_under(root, relative)
    except UnsafePathError as exc:
        raise SeedError("Seed path is not allowed") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise SeedError("Cannot write seed file") from exc
    return relative.replace("\\", "/")


def delete_seed(root: Path, relative: str) -> None:
    if not relative:
        return
    try:
        path = resolve_under(root, relative)
    except UnsafePathError:
        return
    path.unlink(missing_ok=True)


def write_image_seed(image_id: int, os_family: OsFamily | str, body: str) -> str:
    if not body.strip():
        raise SeedError("Image seed file cannot be empty")
    return write_seed(get_settings().image_root, image_seed_relative(image_id, os_family), body)


def write_machine_seed(machine_id: int, os_family: OsFamily | str, body: str) -> str:
    return write_seed(get_settings().data_dir, machine_seed_relative(machine_id, os_family), body)


def delete_machine_seed(machine_id: int, os_family: OsFamily | str) -> None:
    delete_seed(get_settings().data_dir, machine_seed_relative(machine_id, os_family))


def remove_machine_seed_tree(machine_id: int) -> None:
    root = get_settings().data_dir
    for relative in (f"seeds/{int(machine_id)}", f"install-seeds/{int(machine_id)}"):
        try:
            path = resolve_under(root, relative)
        except UnsafePathError:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            path.unlink(missing_ok=True)


def ensure_image_seed(image_id: int, os_family: OsFamily | str) -> None:
    relative = image_seed_relative(image_id, os_family)
    try:
        dest = resolve_under(get_settings().image_root, relative)
    except UnsafePathError as exc:
        raise SeedError("Seed path is not allowed") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return
    try:
        os.write(fd, factory_seed_text(os_family).encode("utf-8"))
    finally:
        os.close(fd)


def reset_image_seed(image_id: int, os_family: OsFamily | str) -> str:
    return write_image_seed(image_id, os_family, factory_seed_text(os_family))
