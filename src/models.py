"""SQLModel tables for inventory, images, vault, and audit."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class MachineState(StrEnum):
    pending = "pending"
    ready = "ready"
    deploying = "deploying"
    deployed = "deployed"
    staged = "staged"
    disabled = "disabled"


class OsFamily(StrEnum):
    linux = "linux"
    windows = "windows"


class AccountKind(StrEnum):
    linux_root = "linux_root"
    windows_administrator = "windows_administrator"


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    disabled: bool = False
    session_version: int = 0


class Image(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    os_family: str
    arch: str = "x86_64"
    kernel_path: str = ""
    initrd_path: str = ""
    boot_wim_path: str = ""
    install_wim_path: str = ""
    cmdline: str = ""


class Machine(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    mac: str = Field(unique=True, index=True)
    uuid: str | None = Field(default=None, index=True)
    hostname: str = ""
    state: str = MachineState.pending.value
    last_seen_at: datetime | None = None
    last_ip: str = ""
    assigned_image_id: int | None = Field(default=None, foreign_key="image.id")
    instance_id: str = Field(default_factory=lambda: uuid4().hex)
    guest_overlay: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class LocalAccount(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    machine_id: int = Field(default=0, index=True)
    kind: str
    encrypted_username: str
    encrypted_password: str


class StagedJob(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    machine_id: int = Field(foreign_key="machine.id", index=True)
    image_id: int | None = Field(default=None, foreign_key="image.id")
    guest_overlay: str = "{}"
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    applied_at: datetime | None = None


class BootEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    machine_id: int = Field(foreign_key="machine.id", index=True)
    at: datetime = Field(default_factory=utcnow)
    client_ip: str = ""
    script_kind: str


class ActivityLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow)
    actor: str = ""
    action: str
    detail: str = ""
