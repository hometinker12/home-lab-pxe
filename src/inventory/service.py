"""Machine registry, lifecycle, images, and encrypted local accounts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Session, col, select

from ..models import (
    AccountKind,
    ActivityLog,
    BootEvent,
    Image,
    LocalAccount,
    Machine,
    MachineState,
    OsFamily,
    StagedJob,
)
from ..security import decrypt_value, encrypt_value
from .mac import normalize_mac, normalize_uuid

LAB_DEFAULT_MACHINE_ID = 0

INSTALL_STATES = frozenset({MachineState.deploying.value, MachineState.staged.value})
WAIT_STATES = frozenset(
    {
        MachineState.pending.value,
        MachineState.ready.value,
        MachineState.disabled.value,
    }
)


def now() -> datetime:
    return datetime.now(UTC)


def load_overlay(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def dump_overlay(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def record_activity(db: Session, *, actor: str, action: str, detail: str = "") -> None:
    db.add(ActivityLog(actor=actor, action=action, detail=detail[:500]))


def get_image(db: Session, image_id: int | None) -> Image | None:
    if not image_id:
        return None
    return db.get(Image, image_id)


def find_image_by_name(db: Session, name: str) -> Image | None:
    return db.exec(select(Image).where(Image.name == name.strip())).first()


def list_images(db: Session) -> list[Image]:
    return list(db.exec(select(Image).order_by(col(Image.name))).all())


def list_machines(db: Session) -> list[Machine]:
    return list(db.exec(select(Machine).order_by(col(Machine.last_seen_at).desc())).all())


def list_activity(db: Session, limit: int = 100) -> list[ActivityLog]:
    return list(db.exec(select(ActivityLog).order_by(col(ActivityLog.at).desc()).limit(limit)).all())


def list_boot_events(db: Session, machine_id: int, limit: int = 25) -> list[BootEvent]:
    return list(
        db.exec(
            select(BootEvent).where(BootEvent.machine_id == machine_id).order_by(col(BootEvent.at).desc()).limit(limit)
        ).all()
    )


def get_machine(db: Session, machine_id: int) -> Machine | None:
    return db.get(Machine, machine_id)


def find_by_mac(db: Session, mac: str) -> Machine | None:
    return db.exec(select(Machine).where(Machine.mac == normalize_mac(mac))).first()


def touch_machine(
    db: Session,
    *,
    mac: str,
    uuid: str | None,
    client_ip: str,
) -> Machine:
    mac_n = normalize_mac(mac)
    uuid_n = normalize_uuid(uuid)
    machine = db.exec(select(Machine).where(Machine.mac == mac_n)).first()
    if machine is None and uuid_n:
        machine = db.exec(select(Machine).where(Machine.uuid == uuid_n)).first()
        if machine is not None:
            machine.mac = mac_n
    if machine is None:
        machine = Machine(
            mac=mac_n, uuid=uuid_n, state=MachineState.pending.value, last_ip=client_ip, last_seen_at=now()
        )
        db.add(machine)
        db.flush()
        record_activity(db, actor="system", action="machine.discovered", detail=mac_n)
    else:
        machine.last_seen_at = now()
        machine.last_ip = client_ip or machine.last_ip
        if uuid_n and not machine.uuid:
            machine.uuid = uuid_n
    db.add(machine)
    return machine


def register_machine(db: Session, *, mac: str, hostname: str = "", actor: str) -> Machine:
    mac_n = normalize_mac(mac)
    if find_by_mac(db, mac_n) is not None:
        raise ValueError("A machine with that MAC already exists")
    host = hostname.strip()
    if len(host) > 253:
        raise ValueError("Hostname is too long")
    machine = Machine(
        mac=mac_n,
        hostname=host,
        state=MachineState.pending.value,
        last_seen_at=now(),
    )
    db.add(machine)
    db.flush()
    record_activity(db, actor=actor, action="machine.register", detail=mac_n)
    return machine


def record_boot_event(db: Session, machine: Machine, *, client_ip: str, script_kind: str) -> None:
    db.add(BootEvent(machine_id=int(machine.id), client_ip=client_ip, script_kind=script_kind))


def upsert_local_account(
    db: Session,
    *,
    machine_id: int,
    kind: AccountKind,
    username: str,
    password: str | None,
) -> LocalAccount:
    row = db.exec(
        select(LocalAccount).where(LocalAccount.machine_id == machine_id, LocalAccount.kind == kind.value)
    ).first()
    if row is None:
        if not password:
            raise ValueError("Password is required when creating a local account")
        row = LocalAccount(
            machine_id=machine_id,
            kind=kind.value,
            encrypted_username=encrypt_value(username),
            encrypted_password=encrypt_value(password),
        )
    else:
        row.encrypted_username = encrypt_value(username)
        if password:
            row.encrypted_password = encrypt_value(password)
    db.add(row)
    return row


def local_account_status(db: Session, machine_id: int, kind: AccountKind) -> dict:
    row = db.exec(
        select(LocalAccount).where(LocalAccount.machine_id == machine_id, LocalAccount.kind == kind.value)
    ).first()
    if row is None:
        return {"set": False, "username": ""}
    return {"set": True, "username": decrypt_value(row.encrypted_username)}


def resolve_local_account(db: Session, machine_id: int, kind: AccountKind) -> tuple[str, str] | None:
    row = db.exec(
        select(LocalAccount).where(LocalAccount.machine_id == machine_id, LocalAccount.kind == kind.value)
    ).first()
    if row is None:
        row = db.exec(
            select(LocalAccount).where(
                LocalAccount.machine_id == LAB_DEFAULT_MACHINE_ID,
                LocalAccount.kind == kind.value,
            )
        ).first()
    if row is None:
        return None
    return decrypt_value(row.encrypted_username), decrypt_value(row.encrypted_password)


def bump_instance_id(machine: Machine) -> None:
    machine.instance_id = uuid4().hex


def deploy_machine(db: Session, machine: Machine, *, image: Image, actor: str) -> Machine:
    machine.assigned_image_id = image.id
    machine.state = MachineState.deploying.value
    bump_instance_id(machine)
    db.add(machine)
    record_activity(db, actor=actor, action="machine.deploy", detail=f"image={image.name}")
    return machine


def mark_ready(db: Session, machine: Machine, *, hostname: str, actor: str) -> Machine:
    machine.hostname = hostname.strip()
    if machine.state == MachineState.pending.value:
        machine.state = MachineState.ready.value
    db.add(machine)
    record_activity(db, actor=actor, action="machine.ready", detail=machine.hostname)
    return machine


def mark_deployed(db: Session, machine: Machine, *, actor: str = "installer") -> Machine:
    machine.state = MachineState.deployed.value
    open_jobs = db.exec(
        select(StagedJob).where(StagedJob.machine_id == machine.id, StagedJob.applied_at == None)  # noqa: E711
    ).all()
    for job in open_jobs:
        job.applied_at = now()
        db.add(job)
    db.add(machine)
    record_activity(db, actor=actor, action="machine.deployed", detail=machine.mac)
    return machine


def disable_machine(db: Session, machine: Machine, *, actor: str, disabled: bool) -> Machine:
    machine.state = MachineState.disabled.value if disabled else MachineState.ready.value
    db.add(machine)
    record_activity(db, actor=actor, action="machine.disabled" if disabled else "machine.enabled", detail=machine.mac)
    return machine


def stage_machine(
    db: Session, machine: Machine, *, actor: str, image: Image | None = None, overlay: dict | None = None
) -> Machine:
    if image is not None:
        machine.assigned_image_id = image.id
    if overlay is not None:
        machine.guest_overlay = dump_overlay(overlay)
    machine.state = MachineState.staged.value
    bump_instance_id(machine)
    db.add(
        StagedJob(
            machine_id=int(machine.id),
            image_id=machine.assigned_image_id,
            guest_overlay=machine.guest_overlay,
            created_by=actor,
        )
    )
    db.add(machine)
    record_activity(db, actor=actor, action="machine.staged", detail=machine.mac)
    return machine


def create_image(
    db: Session,
    *,
    name: str,
    os_family: OsFamily,
    arch: str = "x86_64",
    kernel_path: str = "",
    initrd_path: str = "",
    boot_wim_path: str = "",
    install_wim_path: str = "",
    iso_path: str = "",
    cmdline: str = "",
    actor: str,
) -> Image:
    trimmed = name.strip()
    if not trimmed:
        raise ValueError("Image name is required")
    if find_image_by_name(db, trimmed) is not None:
        raise ValueError("An image with that name already exists")
    image = Image(
        name=trimmed,
        os_family=os_family.value,
        arch=arch.strip() or "x86_64",
        kernel_path=kernel_path.strip(),
        initrd_path=initrd_path.strip(),
        boot_wim_path=boot_wim_path.strip(),
        install_wim_path=install_wim_path.strip(),
        iso_path=iso_path.strip(),
        cmdline=cmdline.strip(),
    )
    db.add(image)
    db.flush()
    record_activity(db, actor=actor, action="image.create", detail=image.name)
    return image


def update_image(
    db: Session,
    image: Image,
    *,
    name: str,
    os_family: OsFamily,
    arch: str = "x86_64",
    kernel_path: str | None = None,
    initrd_path: str | None = None,
    boot_wim_path: str | None = None,
    install_wim_path: str | None = None,
    iso_path: str | None = None,
    cmdline: str | None = None,
    actor: str,
) -> Image:
    new_name = name.strip()
    if not new_name:
        raise ValueError("Image name is required")
    clash = find_image_by_name(db, new_name)
    if clash is not None and clash.id != image.id:
        raise ValueError("An image with that name already exists")
    image.name = new_name
    image.os_family = os_family.value
    image.arch = arch.strip() or "x86_64"
    if kernel_path is not None:
        image.kernel_path = kernel_path.strip()
    if initrd_path is not None:
        image.initrd_path = initrd_path.strip()
    if boot_wim_path is not None:
        image.boot_wim_path = boot_wim_path.strip()
    if install_wim_path is not None:
        image.install_wim_path = install_wim_path.strip()
    if iso_path is not None:
        image.iso_path = iso_path.strip()
    if cmdline is not None:
        image.cmdline = cmdline.strip()
    db.add(image)
    record_activity(db, actor=actor, action="image.update", detail=image.name)
    return image


def os_family_for(db: Session, machine: Machine) -> OsFamily:
    image = get_image(db, machine.assigned_image_id)
    if image is not None:
        return OsFamily(image.os_family)
    overlay = load_overlay(machine.guest_overlay)
    raw = str(overlay.get("os_family") or "").lower()
    if raw in {OsFamily.linux.value, OsFamily.windows.value}:
        return OsFamily(raw)
    return OsFamily.linux
