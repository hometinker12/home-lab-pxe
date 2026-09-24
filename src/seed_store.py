"""Atomic image and machine guest-init seed files."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .models import OsFamily
from .paths import UnsafePathError, resolve_under
from .settings import get_settings

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"

# crypt(3) hash such as $6$rounds=5000$salt$hash, $y$j9T$salt$hash or $2b$12$...: the only literal a
# ``hashed_passwd`` credential key may hold (editor and save validation share this).
CRYPT_HASH_RE = re.compile(r"\$[0-9A-Za-z]{1,8}\$[./0-9A-Za-z$=,+-]+")

SECRET_KEYS = (
    "password",
    "passwd",
    "hashed_password",
    "hashed_passwd",
    "hashed-passwd",
    "plain_text_passwd",
    "plain-text-passwd",
    "secret",
)
# A block-mapping key line, optionally a list item (``- key:``, ``- - key:``) and optionally quoted.
SECRET_LINE_RE = re.compile(
    r"(?:-\s+)*([\"']?)(" + "|".join(re.escape(key) for key in SECRET_KEYS) + r")\1\s*:",
    flags=re.IGNORECASE,
)
# Keys that may also hold a crypt hash (same rule as the cloud-init editor). Everything else is
# placeholder-only.
HASH_SECRET_KEYS = frozenset({"hashed_passwd", "hashed-passwd"})


def _line_scalar(rest: str) -> str:
    """Plain or quoted scalar after ``key:`` on one line, without a trailing comment."""
    text = re.split(r"\s+#", rest.strip(), maxsplit=1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def line_has_literal_secret(line: str) -> bool:
    """True when one seed line looks like ``password: <literal>`` (the line rule seeds are saved under)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    match = SECRET_LINE_RE.match(stripped)
    if not match or "{{password" in stripped:
        return False
    if match.group(2).lower() in HASH_SECRET_KEYS and CRYPT_HASH_RE.fullmatch(_line_scalar(stripped[match.end() :])):
        return False
    return True


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


def prune_install_seeds(machine_id: int, *, keep_instance_id: str = "") -> None:
    """Keep at most the current deploy snapshot; drop leftover instance directories."""
    relative = f"install-seeds/{int(machine_id)}"
    try:
        base = resolve_under(get_settings().data_dir, relative)
    except UnsafePathError:
        return
    if not base.is_dir():
        return
    keep = (keep_instance_id or "").strip()
    for child in list(base.iterdir()):
        if keep and child.name == keep:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    if keep:
        return
    try:
        next(base.iterdir())
    except StopIteration:
        base.rmdir()
    except OSError:
        return


def write_image_seed(image_id: int, os_family: OsFamily | str, body: str) -> str:
    if not body.strip():
        raise SeedError("Image seed file cannot be empty")
    return write_seed(get_settings().image_root, image_seed_relative(image_id, os_family), body)


def default_seed_text(image_id: int, os_family: OsFamily | str) -> str:
    """Image template, or factory starter if the image file is empty."""
    text = read_image_seed(image_id, os_family)
    if text.strip():
        return text
    return factory_seed_text(os_family)


def copy_image_seed_to_machine(
    machine_id: int,
    image_id: int,
    os_family: OsFamily | str,
    *,
    overwrite: bool = False,
) -> bool:
    """Copy the image (or factory) seed onto the machine. Returns True if a write happened."""
    if not overwrite and read_machine_seed(machine_id, os_family).strip():
        return False
    text = default_seed_text(image_id, os_family)
    if not text.strip():
        raise SeedError("Default seed file cannot be empty")
    write_machine_seed(machine_id, os_family, text)
    return True


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
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    if family == OsFamily.tool.value:
        return
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
