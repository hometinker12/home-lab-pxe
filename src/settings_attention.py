"""Operator attention flags for unset console settings."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from .inventory.service import LAB_DEFAULT_MACHINE_ID, local_account_status
from .models import AccountKind
from .smb_runtime import smb_password_configured


@dataclass(frozen=True)
class SettingsAttention:
    smb_password: bool
    linux_account: bool
    windows_account: bool

    @property
    def accounts(self) -> bool:
        return self.linux_account or self.windows_account

    @property
    def count(self) -> int:
        return int(self.smb_password) + int(self.accounts)

    @property
    def label(self) -> str:
        n = self.count
        if n == 1:
            return "1 setting needs attention"
        return f"{n} settings need attention"


def empty_settings_attention() -> SettingsAttention:
    return SettingsAttention(smb_password=False, linux_account=False, windows_account=False)


def load_settings_attention(db: Session) -> SettingsAttention:
    linux = local_account_status(db, LAB_DEFAULT_MACHINE_ID, AccountKind.linux_root)
    windows = local_account_status(db, LAB_DEFAULT_MACHINE_ID, AccountKind.windows_administrator)
    return SettingsAttention(
        smb_password=not smb_password_configured(),
        linux_account=not bool(linux.get("set")),
        windows_account=not bool(windows.get("set")),
    )
