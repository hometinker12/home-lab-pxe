"""Persisted Windows SMB share password and Samba apply handshake."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import InvalidToken
from sqlmodel import Session

from .security import decrypt_value, encrypt_value
from .settings import get_settings, validate_smb_password

LOGGER = logging.getLogger("home_lab_pxe")

PASSWORD_NAME = "smb.password"
CMD_NAME = "smb.cmd"


def data_dir() -> Path:
    path = get_settings().data_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def password_path() -> Path:
    return data_dir() / PASSWORD_NAME


def cmd_path() -> Path:
    return data_dir() / CMD_NAME


def generate_smb_password() -> str:
    for _ in range(8):
        candidate = secrets.token_urlsafe(18)
        try:
            return validate_smb_password(candidate)
        except ValueError:
            continue
    raise RuntimeError("could not generate an SMB password")


def _load_persisted_password() -> str:
    path = password_path()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not raw:
        return ""
    try:
        secret = decrypt_value(raw)
    except (InvalidToken, ValueError, TypeError):
        LOGGER.warning("could not decrypt persisted SMB password")
        return ""
    try:
        return validate_smb_password(secret)
    except ValueError:
        return ""


def effective_smb_password() -> str:
    persisted = _load_persisted_password()
    if persisted:
        return persisted
    return get_settings().smb_password


def smb_password_configured() -> bool:
    return bool(effective_smb_password())


def save_persisted_password(password: str) -> None:
    secret = validate_smb_password(password)
    path = password_path()
    tmp = path.with_name(f"{PASSWORD_NAME}.tmp")
    tmp.write_text(encrypt_value(secret) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def request_smb_apply() -> None:
    cmd_path().write_text("apply\n", encoding="utf-8")


def rotate_smb_password(db: Session, *, actor: str) -> None:
    from .inventory.service import record_activity

    save_persisted_password(generate_smb_password())
    record_activity(db, actor=actor, action="smb.rotate", detail="pxe-media share password rotated")
    db.commit()
    request_smb_apply()


def sync_samba_account() -> bool:
    """Set the Samba user password. Requires smbpasswd (typically root). Never logs the secret."""
    password = effective_smb_password()
    if not password:
        return False
    user = get_settings().smb_user
    smbpasswd = shutil.which("smbpasswd")
    if not smbpasswd:
        return False
    result = subprocess.run(
        [smbpasswd, "-s", "-a", user],
        input=f"{password}\n{password}\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        LOGGER.warning("smbpasswd failed")
        return False
    return True


def main(argv: list[str]) -> int:
    if argv[:1] != ["apply"]:
        return 2
    if not effective_smb_password():
        return 2
    return 0 if sync_samba_account() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
