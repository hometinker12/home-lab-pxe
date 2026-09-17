"""Machine registry, lifecycle, images, and encrypted local accounts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlmodel import Session, col, select

from ..models import (
    AccountKind,
    ActivityLog,
    BootEvent,
    ExtractStatus,
    Image,
    InstallAttempt,
    LocalAccount,
    Machine,
    MachineState,
    OsFamily,
    StagedJob,
)
from ..security import decrypt_value, encrypt_value
from ..seed_store import (
    SeedError,
    copy_image_seed_to_machine,
    delete_seed,
    ensure_image_seed,
    factory_seed_text,
    prune_install_seeds,
    snapshot_seed_relative,
    write_seed,
)
from ..settings import get_settings, smb_password_configured
from .mac import normalize_mac, normalize_uuid

LAB_DEFAULT_MACHINE_ID = 0

INSTALL_STATES = frozenset(
    {
        MachineState.deploying.value,
        MachineState.staged.value,
        MachineState.imaging.value,
    }
)
EDIT_LOCKED_STATES = frozenset({MachineState.deploying.value, MachineState.imaging.value})
WAIT_STATES = frozenset(
    {
        MachineState.pending.value,
        MachineState.ready.value,
        MachineState.timeout_error.value,
    }
)
EXTRACT_IN_PROGRESS = frozenset({ExtractStatus.queued.value, ExtractStatus.extracting.value})
EXTRACT_BLOCKING = frozenset({*EXTRACT_IN_PROGRESS, ExtractStatus.failed.value})


def guest_init_allowed(machine: Machine) -> bool:
    """Installer seeds with vault secrets are only served during deploy/reimage."""
    return machine.state in INSTALL_STATES


def edit_locked(machine: Machine) -> bool:
    """Block console edits while PXE install or disk imaging is running."""
    return machine.state in EDIT_LOCKED_STATES


def now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clear_imaging_clock(machine: Machine) -> None:
    machine.imaging_started_at = None


def load_overlay(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def dump_overlay(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def apply_lab_timezone(db: Session, machine: Machine) -> None:
    overlay = load_overlay(machine.guest_overlay)
    if str(overlay.get("timezone") or "").strip():
        return
    from ..dhcp_runtime import default_timezone

    overlay["timezone"] = default_timezone(db)
    machine.guest_overlay = dump_overlay(overlay)


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


def get_machine_for_guest_init(db: Session, machine_id: int) -> Machine | None:
    expire_stale_imaging(db)
    machine = get_machine(db, machine_id)
    if machine is None or not guest_init_allowed(machine):
        return None
    return machine


def find_by_mac(db: Session, mac: str) -> Machine | None:
    return db.exec(select(Machine).where(Machine.mac == normalize_mac(mac))).first()


def get_open_attempt(db: Session, machine: Machine) -> InstallAttempt | None:
    if not machine.id:
        return None
    return db.exec(
        select(InstallAttempt).where(
            InstallAttempt.machine_id == machine.id,
            InstallAttempt.instance_id == machine.instance_id,
            InstallAttempt.completed_at == None,  # noqa: E711
        )
    ).first()


def close_open_attempts(db: Session, machine: Machine) -> None:
    if not machine.id:
        return
    rows = db.exec(
        select(InstallAttempt).where(
            InstallAttempt.machine_id == machine.id,
            InstallAttempt.completed_at == None,  # noqa: E711
        )
    ).all()
    for row in rows:
        row.completed_at = now()
        db.add(row)


def image_extract_in_progress(image: Image) -> bool:
    return (image.extract_status or ExtractStatus.idle.value) in EXTRACT_IN_PROGRESS


def image_extract_blocking(image: Image) -> bool:
    return (image.extract_status or ExtractStatus.idle.value) in EXTRACT_BLOCKING


def image_deploy_reason(image: Image) -> str:
    status = image.extract_status or ExtractStatus.idle.value
    if status == ExtractStatus.queued.value or status == ExtractStatus.extracting.value:
        return "extraction in progress"
    if status == ExtractStatus.failed.value:
        return "extraction failed"
    if image.os_family == OsFamily.tool.value:
        if (image.kernel_path or "").strip() and (image.initrd_path or "").strip():
            return ""
        if (image.iso_path or "").strip():
            return ""
        return "Tool image needs a kernel and initrd, or an ISO"
    if image.os_family == OsFamily.linux.value:
        if (image.kernel_path or "").strip() and (image.initrd_path or "").strip():
            return ""
        if (image.iso_path or "").strip():
            return ""
        return "Linux image needs a kernel and initrd, or an ISO"
    if (image.extract_generation or "").strip() and (image.boot_wim_path or "").strip():
        from ..tftp_store import wimboot_available

        if not wimboot_available():
            return "valid wimboot is not installed"
        if not smb_password_configured():
            return "Windows installation media share is not configured"
        return ""
    if (image.iso_path or "").strip() or (image.boot_wim_path or "").strip():
        return ""
    return "Windows image needs extracted Setup media or an ISO"


def image_deploy_blocked(image: Image) -> bool:
    return bool(image_deploy_reason(image))


def assert_image_deployable(image: Image) -> None:
    reason = image_deploy_reason(image)
    if reason:
        if image_extract_blocking(image):
            raise ValueError("This image is not ready to deploy until extraction succeeds")
        raise ValueError(reason)


def create_install_attempt(db: Session, machine: Machine, image: Image) -> InstallAttempt:
    from ..seed_render import select_seed_text

    close_open_attempts(db, machine)
    text = select_seed_text(image, machine, image.os_family)
    if not text.strip():
        text = factory_seed_text(image.os_family)
    relative = snapshot_seed_relative(int(machine.id), machine.instance_id, image.os_family)
    try:
        write_seed(get_settings().data_dir, relative, text)
    except SeedError as exc:
        raise ValueError(str(exc)) from exc
    prune_install_seeds(int(machine.id), keep_instance_id=machine.instance_id)
    media = (image.extract_generation or "").strip()
    attempt = InstallAttempt(
        machine_id=int(machine.id),
        instance_id=machine.instance_id,
        image_id=image.id,
        os_family=image.os_family,
        extract_revision=int(image.extract_revision or 0),
        kernel_path=image.kernel_path or "",
        initrd_path=image.initrd_path or "",
        boot_wim_path=image.boot_wim_path or "",
        install_wim_path=image.install_wim_path or "",
        iso_path=image.iso_path or "",
        cmdline=image.cmdline or "",
        wim_index=int(image.wim_index or 1),
        media_relative=media,
        seed_snapshot_path=relative,
    )
    db.add(attempt)
    db.flush()
    return attempt


def refresh_install_attempt(db: Session, machine: Machine, image: Image) -> InstallAttempt:
    close_open_attempts(db, machine)
    bump_instance_id(machine)
    db.add(machine)
    return create_install_attempt(db, machine, image)


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
        apply_lab_timezone(db, machine)
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


def apply_hostname(machine: Machine, hostname: str) -> str:
    host = (hostname or "").strip()
    if len(host) > 253:
        raise ValueError("Hostname is too long")
    machine.hostname = host
    return host


def register_machine(db: Session, *, mac: str, hostname: str = "", actor: str) -> Machine:
    mac_n = normalize_mac(mac)
    if find_by_mac(db, mac_n) is not None:
        raise ValueError("A machine with that MAC already exists")
    machine = Machine(
        mac=mac_n,
        hostname="",
        state=MachineState.pending.value,
        last_seen_at=now(),
    )
    apply_hostname(machine, hostname)
    apply_lab_timezone(db, machine)
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
    if image.os_family == OsFamily.tool.value:
        raise ValueError("Tool images boot from the PXE menu and cannot be deployed")
    assert_image_deployable(image)
    machine.assigned_image_id = image.id
    machine.state = MachineState.deploying.value
    _clear_imaging_clock(machine)
    bump_instance_id(machine)
    if machine.id is not None:
        copy_image_seed_to_machine(int(machine.id), int(image.id), image.os_family, overwrite=False)
    db.add(machine)
    create_install_attempt(db, machine, image)
    record_activity(db, actor=actor, action="machine.deploy", detail=f"image={image.name}")
    return machine


def mark_ready(db: Session, machine: Machine, *, hostname: str, actor: str) -> Machine:
    apply_hostname(machine, hostname)
    if machine.state == MachineState.pending.value:
        machine.state = MachineState.ready.value
    db.add(machine)
    record_activity(db, actor=actor, action="machine.ready", detail=machine.hostname)
    return machine


def mark_imaging(db: Session, machine: Machine, *, actor: str = "installer") -> Machine:
    """Installer early-command: disk imaging has started."""
    if machine.state != MachineState.imaging.value:
        machine.state = MachineState.imaging.value
        machine.imaging_started_at = now()
        db.add(machine)
        record_activity(db, actor=actor, action="machine.imaging", detail=machine.mac)
    return machine


def mark_timeout_error(db: Session, machine: Machine, *, actor: str = "timeout") -> Machine:
    machine.state = MachineState.timeout_error.value
    _clear_imaging_clock(machine)
    close_open_attempts(db, machine)
    db.add(machine)
    record_activity(db, actor=actor, action="machine.timeout", detail=machine.mac)
    return machine


def expire_stale_imaging(db: Session) -> int:
    """Move Imaging machines past the Settings timeout into Timeout Error. 0 minutes disables."""
    from ..dhcp_runtime import imaging_timeout_minutes

    minutes = imaging_timeout_minutes(db)
    if minutes <= 0:
        return 0
    cutoff = now() - timedelta(minutes=minutes)
    expired = 0
    rows = db.exec(select(Machine).where(Machine.state == MachineState.imaging.value)).all()
    for machine in rows:
        started = machine.imaging_started_at
        if started is None:
            machine.imaging_started_at = now()
            db.add(machine)
            continue
        if _aware(started) <= cutoff:
            mark_timeout_error(db, machine, actor="timeout")
            expired += 1
    return expired


def mark_deployed(db: Session, machine: Machine, *, actor: str = "installer") -> Machine:
    machine.state = MachineState.deployed.value
    _clear_imaging_clock(machine)
    open_jobs = db.exec(
        select(StagedJob).where(StagedJob.machine_id == machine.id, StagedJob.applied_at == None)  # noqa: E711
    ).all()
    for job in open_jobs:
        job.applied_at = now()
        db.add(job)
    attempts = db.exec(
        select(InstallAttempt).where(
            InstallAttempt.machine_id == machine.id,
            InstallAttempt.completed_at == None,  # noqa: E711
        )
    ).all()
    image_ids: set[int] = set()
    for attempt in attempts:
        attempt.completed_at = now()
        db.add(attempt)
        if attempt.image_id:
            image_ids.add(int(attempt.image_id))
        if attempt.seed_snapshot_path:
            delete_seed(get_settings().data_dir, attempt.seed_snapshot_path)
    prune_install_seeds(int(machine.id))
    db.add(machine)
    record_activity(db, actor=actor, action="machine.deployed", detail=machine.mac)
    from ..extract_worker import gc_extract_generations

    for image_id in image_ids:
        image = get_image(db, image_id)
        if image is not None:
            gc_extract_generations(db, image)
    return machine


def disable_machine(db: Session, machine: Machine, *, actor: str, disabled: bool) -> Machine:
    machine.state = MachineState.disabled.value if disabled else MachineState.ready.value
    _clear_imaging_clock(machine)
    if disabled:
        close_open_attempts(db, machine)
    db.add(machine)
    record_activity(db, actor=actor, action="machine.disabled" if disabled else "machine.enabled", detail=machine.mac)
    return machine


def stage_machine(
    db: Session, machine: Machine, *, actor: str, image: Image | None = None, overlay: dict | None = None
) -> Machine:
    target = image or get_image(db, machine.assigned_image_id)
    if target is None:
        raise ValueError("Assign an image before staging a reimage")
    if target.os_family == OsFamily.tool.value:
        raise ValueError("Tool images boot from the PXE menu and cannot be staged")
    assert_image_deployable(target)
    if image is not None:
        machine.assigned_image_id = image.id
    if overlay is not None:
        machine.guest_overlay = dump_overlay(overlay)
    machine.state = MachineState.staged.value
    _clear_imaging_clock(machine)
    bump_instance_id(machine)
    if machine.id is not None:
        copy_image_seed_to_machine(int(machine.id), int(target.id), target.os_family, overwrite=False)
    db.add(
        StagedJob(
            machine_id=int(machine.id),
            image_id=machine.assigned_image_id,
            guest_overlay=machine.guest_overlay,
            created_by=actor,
        )
    )
    db.add(machine)
    create_install_attempt(db, machine, target)
    record_activity(db, actor=actor, action="machine.staged", detail=machine.mac)
    return machine


def update_staged_attempt(db: Session, machine: Machine, *, actor: str, image: Image) -> Machine:
    assert_image_deployable(image)
    refresh_install_attempt(db, machine, image)
    record_activity(db, actor=actor, action="machine.staged-update", detail=machine.mac)
    return machine


def delete_machine(db: Session, machine: Machine, *, actor: str) -> int:
    machine_id = int(machine.id or 0)
    if machine_id < 1:
        raise ValueError("Unknown machine")
    mac = machine.mac
    close_open_attempts(db, machine)
    for row in db.exec(select(LocalAccount).where(LocalAccount.machine_id == machine_id)).all():
        db.delete(row)
    for row in db.exec(select(StagedJob).where(StagedJob.machine_id == machine_id)).all():
        db.delete(row)
    for row in db.exec(select(InstallAttempt).where(InstallAttempt.machine_id == machine_id)).all():
        if row.seed_snapshot_path:
            delete_seed(get_settings().data_dir, row.seed_snapshot_path)
        db.delete(row)
    for row in db.exec(select(BootEvent).where(BootEvent.machine_id == machine_id)).all():
        db.delete(row)
    db.delete(machine)
    record_activity(db, actor=actor, action="machine.delete", detail=mac)
    return machine_id


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
    wim_index: int = 1,
    folder_id: int | None = None,
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
        wim_index=max(1, int(wim_index or 1)),
    )
    db.add(image)
    db.flush()
    from .boot_menu import place_new_image

    place_new_image(db, image, folder_id=folder_id, os_family=os_family)
    if image.id is not None and os_family != OsFamily.tool:
        ensure_image_seed(int(image.id), os_family)
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
    wim_index: int | None = None,
    folder_id: int | None = None,
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
    if wim_index is not None:
        image.wim_index = max(1, int(wim_index))
    if folder_id is not None:
        from .boot_menu import get_folder, set_image_folder

        folder = get_folder(db, folder_id)
        if folder is None:
            raise ValueError("Unknown boot-menu folder")
        set_image_folder(db, image, folder, actor=actor)
    db.add(image)
    if image.id is not None and os_family != OsFamily.tool:
        ensure_image_seed(int(image.id), os_family)
    record_activity(db, actor=actor, action="image.update", detail=image.name)
    return image


def delete_image(db: Session, image: Image, *, actor: str) -> int:
    image_id = int(image.id or 0)
    if image_id < 1:
        raise ValueError("Unknown image")
    open_attempts = db.exec(
        select(InstallAttempt).where(
            InstallAttempt.image_id == image_id,
            InstallAttempt.completed_at == None,  # noqa: E711
        )
    ).all()
    if open_attempts:
        raise ValueError("Cannot delete this image while a machine is installing it")
    busy = db.exec(
        select(Machine).where(
            Machine.assigned_image_id == image_id,
            col(Machine.state).in_(list(INSTALL_STATES)),
        )
    ).all()
    if busy:
        raise ValueError("Cannot delete this image while a machine is deploying or staged")
    for machine in db.exec(select(Machine).where(Machine.assigned_image_id == image_id)).all():
        machine.assigned_image_id = None
        db.add(machine)
    for job in db.exec(select(StagedJob).where(StagedJob.image_id == image_id)).all():
        db.delete(job)
    for attempt in db.exec(select(InstallAttempt).where(InstallAttempt.image_id == image_id)).all():
        attempt.image_id = None
        db.add(attempt)
    name = image.name
    db.delete(image)
    record_activity(db, actor=actor, action="image.delete", detail=name)
    return image_id


def os_family_for(db: Session, machine: Machine) -> OsFamily:
    image = get_image(db, machine.assigned_image_id)
    if image is not None:
        try:
            return OsFamily(image.os_family)
        except ValueError:
            return OsFamily.linux
    overlay = load_overlay(machine.guest_overlay)
    raw = str(overlay.get("os_family") or "").lower()
    if raw in {OsFamily.linux.value, OsFamily.windows.value, OsFamily.tool.value}:
        return OsFamily(raw)
    return OsFamily.linux
