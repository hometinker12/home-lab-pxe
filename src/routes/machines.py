from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..dhcp_runtime import default_timezone
from ..inventory.mac import InvalidMacError
from ..inventory.service import (
    abort_deploy,
    apply_hostname,
    delete_machine,
    deploy_machine,
    disable_machine,
    dump_overlay,
    edit_locked,
    expire_stale_imaging,
    get_image,
    get_machine,
    get_open_attempt,
    image_deploy_blocked,
    image_deploy_reason,
    list_boot_events,
    list_images,
    list_machines,
    load_overlay,
    local_account_status,
    mark_deployed,
    mark_ready,
    os_family_for,
    record_activity,
    register_machine,
    resolve_local_account,
    stage_machine,
    update_staged_attempt,
    upsert_local_account,
)
from ..models import AccountKind, MachineState, OsFamily
from ..security import VaultError
from ..seed_render import validate_seed_template
from ..seed_store import (
    SeedError,
    copy_image_seed_to_machine,
    default_seed_text,
    delete_machine_seed,
    read_machine_seed,
    remove_machine_seed_tree,
    write_machine_seed,
)
from ..timezones import is_valid_timezone, timezone_choices
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


def _apply_imaging_timeouts(db: Session) -> None:
    expire_stale_imaging(db)
    db.commit()


_PLACEHOLDER_HELP = (
    "Saving a non-empty file replaces the image document for this machine only. "
    "Deploy copies the image template here if this field is still empty. Copy Default pulls the latest image file. "
    "Placeholders: {{hostname}} {{username}} {{password}} {{password_hash}} {{instance_id}} {{machine_id}} "
    "{{public_url}} {{phone_home_url}} {{imaging_url}} {{install_log_url}} {{timezone}} {{ssh_keys}} {{packages}} "
    "{{source_id}} {{wim_index}} {{install_media_path}}"
)


def _machine_or_404(db: Session, machine_id: int):
    machine = get_machine(db, machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="unknown machine")
    return machine


def _image_options(images):
    rows = []
    for img in images:
        if img.os_family == OsFamily.tool.value:
            continue
        blocked = image_deploy_blocked(img)
        rows.append(
            {
                "image": img,
                "blocked": blocked,
                "reason": image_deploy_reason(img) if blocked else "",
            }
        )
    return rows


def _resolved_timezone(db: Session, timezone: str, overlay: dict) -> str:
    tz = (timezone or "").strip()
    if not tz:
        tz = str(overlay.get("timezone") or "").strip() or default_timezone(db)
    if not is_valid_timezone(tz):
        raise ValueError("Choose a valid IANA timezone")
    return tz


def _apply_guest_fields(
    db: Session,
    machine,
    *,
    hostname: str,
    timezone: str,
    packages: str,
    ssh_keys: str,
    user_data: str,
    unattend_xml: str,
    image_id: int | None,
    allow_blank_hostname: bool,
    write_seed: bool,
    delete_empty_seed: bool,
) -> None:
    overlay = load_overlay(machine.guest_overlay)
    tz = _resolved_timezone(db, timezone, overlay)
    if hostname.strip() or allow_blank_hostname:
        apply_hostname(machine, hostname)
    overlay.update(
        {
            "hostname": machine.hostname or "",
            "timezone": tz,
            "packages": [p.strip() for p in packages.split(",") if p.strip()],
            "ssh_keys": [k.strip() for k in ssh_keys.splitlines() if k.strip()],
        }
    )
    if image_id:
        img = get_image(db, image_id)
        if img:
            machine.assigned_image_id = img.id
    family = os_family_for(db, machine)
    seed_body = unattend_xml if family == OsFamily.windows else user_data
    assigned = get_image(db, machine.assigned_image_id)
    if write_seed and assigned is not None:
        if seed_body.strip():
            validate_seed_template(seed_body, family)
            write_machine_seed(int(machine.id), family, seed_body)
            overlay.pop("raw_overlay", None)
        elif delete_empty_seed:
            delete_machine_seed(int(machine.id), family)
            overlay.pop("raw_overlay", None)
    machine.guest_overlay = dump_overlay(overlay)
    db.add(machine)


_NOTICES = {
    "saved": "Saved.",
    "deployed": "Deploy started. The next PXE boot installs this image.",
    "aborted": "Install aborted.",
}

_SCRIPT_LABELS = {
    "unknown_local": "Continue to disk",
    "menu": "Boot menu",
    "install_linux": "Linux install",
    "install_windows": "Windows install",
    "image_not_ready": "Image not ready",
    "tool": "Tool image",
}


def _account_kind(db: Session, machine, image=None):
    family = image.os_family if image is not None else os_family_for(db, machine).value
    if family == OsFamily.windows.value:
        return AccountKind.windows_administrator, "Administrator"
    return AccountKind.linux_root, "root"


def _persist_account(db: Session, machine, *, username: str, password: str, image=None) -> None:
    kind, default_user = _account_kind(db, machine, image)
    secret = (password or "").strip()
    name = (username or "").strip() or default_user
    status = local_account_status(db, int(machine.id), kind)
    if not secret:
        if not status.get("set") or status.get("unreadable"):
            return
    upsert_local_account(
        db,
        machine_id=int(machine.id),
        kind=kind,
        username=name,
        password=secret or None,
    )


def _require_account(db: Session, machine, image) -> None:
    kind, _default = _account_kind(db, machine, image)
    try:
        creds = resolve_local_account(db, int(machine.id), kind)
    except VaultError as exc:
        raise ValueError(str(exc)) from exc
    if creds is None or not (creds[1] or "").strip():
        raise ValueError("Set a local account password before deploy, or save an imaging default in Settings")


def _detail(request: Request, db: Session, machine, *, error=None, notice=None):
    images = list_images(db)
    image = get_image(db, machine.assigned_image_id)
    family = os_family_for(db, machine)
    kind = AccountKind.windows_administrator if family == OsFamily.windows else AccountKind.linux_root
    account = local_account_status(db, int(machine.id), kind)
    overlay = load_overlay(machine.guest_overlay)
    selected_timezone = str(overlay.get("timezone") or "").strip() or default_timezone(db)
    events = list_boot_events(db, int(machine.id))
    machine_seed = ""
    if image is not None:
        machine_seed = read_machine_seed(int(machine.id), family)
        if not machine_seed.strip():
            machine_seed = default_seed_text(int(image.id), family)
    image_seed_path = ""
    served_hint = "Assign an image first"
    if image is not None:
        image_seed_path = (
            f"uploads/{image.id}/unattend.xml" if family == OsFamily.windows else f"uploads/{image.id}/user-data"
        )
        if machine_seed.strip():
            served_hint = (
                f"machine override seeds/{machine.id}/unattend.xml"
                if family == OsFamily.windows
                else f"machine override seeds/{machine.id}/user-data"
            )
        else:
            served_hint = f"image template {image_seed_path}"
        attempt = get_open_attempt(db, machine)
        if attempt is not None and (attempt.seed_snapshot_path or "").strip():
            served_hint = (
                f"deploy snapshot {attempt.seed_snapshot_path}; installer fetches "
                f"/cloud-init/{machine.id}/{machine.instance_id}/user-data with placeholders filled"
            )
    return render(
        request,
        "machine_detail.html",
        machine=machine,
        images=images,
        image_options=_image_options(images),
        image=image,
        family=family.value,
        account=account,
        overlay=overlay,
        events=events,
        machine_seed=machine_seed,
        served_hint=served_hint,
        placeholder_help=_PLACEHOLDER_HELP,
        deploying=edit_locked(machine),
        timezones=timezone_choices(selected_timezone),
        selected_timezone=selected_timezone,
        error=error or ("encryption key does not match stored accounts" if account.get("unreadable") else None),
        notice=notice if error is None else None,
        script_labels=_SCRIPT_LABELS,
        can_abort=machine.state
        in {MachineState.deploying.value, MachineState.imaging.value, MachineState.staged.value},
    )


@router.get("/api/machines")
def api_machines(db: Session = Depends(get_db), user: str = Depends(require_user)):
    _apply_imaging_timeouts(db)
    return [
        {
            "id": m.id,
            "mac": m.mac,
            "hostname": m.hostname,
            "state": m.state,
            "last_ip": m.last_ip,
            "assigned_image_id": m.assigned_image_id,
            "instance_id": m.instance_id,
        }
        for m in list_machines(db)
    ]


@router.get("/api/machines/{machine_id}")
def api_machine(machine_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    _apply_imaging_timeouts(db)
    machine = _machine_or_404(db, machine_id)
    family = os_family_for(db, machine)
    kind = AccountKind.windows_administrator if family == OsFamily.windows else AccountKind.linux_root
    account = local_account_status(db, int(machine.id), kind)
    return {
        "id": machine.id,
        "mac": machine.mac,
        "hostname": machine.hostname,
        "state": machine.state,
        "account_username": account["username"],
        "account_password_set": account["set"],
        "instance_id": machine.instance_id,
        "last_ip": machine.last_ip,
    }


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/machines", status_code=HTTP_303_SEE_OTHER)


def _machine_groups(machines):
    return {
        "pending": [m for m in machines if m.state == MachineState.pending.value],
        "timeouts": [m for m in machines if m.state == MachineState.timeout_error.value],
        "failed": [m for m in machines if m.state == MachineState.failed.value],
    }


@router.get("/machines", response_class=HTMLResponse)
def machines_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    _apply_imaging_timeouts(db)
    machines = list_machines(db)
    groups = _machine_groups(machines)
    images = {img.id: img for img in list_images(db)}
    return render(
        request,
        "machines.html",
        machines=machines,
        images=images,
        error=None,
        add_mac="",
        add_hostname="",
        **groups,
    )


def _machines_error(request: Request, db: Session, error: str, *, add_mac: str = "", add_hostname: str = ""):
    _apply_imaging_timeouts(db)
    machines = list_machines(db)
    images = {img.id: img for img in list_images(db)}
    return render(
        request,
        "machines.html",
        machines=machines,
        images=images,
        error=error,
        add_mac=add_mac,
        add_hostname=add_hostname,
        **_machine_groups(machines),
    )


@router.post("/machines")
def machines_create(
    request: Request,
    mac: str = Form(...),
    hostname: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        machine = register_machine(db, mac=mac, hostname=hostname, actor=user)
        db.commit()
    except InvalidMacError as exc:
        return _machines_error(request, db, str(exc), add_mac=mac, add_hostname=hostname)
    except ValueError as exc:
        return _machines_error(request, db, str(exc), add_mac=mac, add_hostname=hostname)
    return RedirectResponse(url=f"/machines/{machine.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/machines/{machine_id}", response_class=HTMLResponse)
def machine_detail(request: Request, machine_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    _apply_imaging_timeouts(db)
    machine = _machine_or_404(db, machine_id)
    notice = _NOTICES.get((request.query_params.get("notice") or "").strip())
    return _detail(request, db, machine, notice=notice)


@router.post("/machines/{machine_id}/save")
def machine_save(
    request: Request,
    machine_id: int,
    hostname: str = Form(""),
    timezone: str = Form(""),
    packages: str = Form(""),
    ssh_keys: str = Form(""),
    user_data: str = Form(""),
    unattend_xml: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    image_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    if edit_locked(machine):
        return _detail(request, db, machine, error="Cannot edit guest-init while a deploy is in progress")
    try:
        _persist_account(db, machine, username=username, password=password)
        _apply_guest_fields(
            db,
            machine,
            hostname=hostname,
            timezone=timezone,
            packages=packages,
            ssh_keys=ssh_keys,
            user_data=user_data,
            unattend_xml=unattend_xml,
            image_id=image_id,
            allow_blank_hostname=True,
            write_seed=True,
            delete_empty_seed=True,
        )
        assigned = get_image(db, machine.assigned_image_id)
        if machine.state == MachineState.disabled.value:
            db.commit()
            return RedirectResponse(url=f"/machines/{machine_id}?notice=saved", status_code=HTTP_303_SEE_OTHER)
        if machine.state == MachineState.deployed.value:
            if assigned is None:
                db.commit()
                return RedirectResponse(url=f"/machines/{machine_id}?notice=saved", status_code=HTTP_303_SEE_OTHER)
            stage_machine(db, machine, actor=user, image=assigned)
        elif machine.state == MachineState.staged.value:
            if assigned is None:
                raise ValueError("Assign an image first")
            update_staged_attempt(db, machine, actor=user, image=assigned)
        else:
            mark_ready(db, machine, hostname=hostname, actor=user)
        db.commit()
    except (ValueError, SeedError) as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    return RedirectResponse(url=f"/machines/{machine_id}?notice=saved", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/deploy")
def machine_deploy(
    request: Request,
    machine_id: int,
    image_id: int = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    hostname: str = Form(""),
    timezone: str = Form(""),
    packages: str = Form(""),
    ssh_keys: str = Form(""),
    user_data: str = Form(""),
    unattend_xml: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    expire_stale_imaging(db)
    machine = _machine_or_404(db, machine_id)
    if machine.state == MachineState.imaging.value:
        return _detail(request, db, machine, error="Cannot deploy while imaging is in progress")
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=400, detail="unknown image")
    kind = AccountKind.windows_administrator if image.os_family == OsFamily.windows.value else AccountKind.linux_root
    default_user = "Administrator" if kind == AccountKind.windows_administrator else "root"
    try:
        _apply_guest_fields(
            db,
            machine,
            hostname=hostname,
            timezone=timezone,
            packages=packages,
            ssh_keys=ssh_keys,
            user_data=user_data,
            unattend_xml=unattend_xml,
            image_id=image_id,
            allow_blank_hostname=False,
            write_seed=True,
            delete_empty_seed=False,
        )
        if password:
            upsert_local_account(
                db,
                machine_id=int(machine.id),
                kind=kind,
                username=(username.strip() or default_user),
                password=password,
            )
        _require_account(db, machine, image)
        deploy_machine(db, machine, image=image, actor=user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    except SeedError as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    return RedirectResponse(url=f"/machines/{machine_id}?notice=deployed", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/abort")
def machine_abort(
    request: Request,
    machine_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    try:
        abort_deploy(db, machine, actor=user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    return RedirectResponse(url=f"/machines/{machine_id}?notice=aborted", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/seed/copy-default")
def machine_copy_default_seed(
    request: Request,
    machine_id: int,
    image_id: int | None = Form(default=None),
    hostname: str = Form(""),
    timezone: str = Form(""),
    packages: str = Form(""),
    ssh_keys: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    if edit_locked(machine):
        return _detail(request, db, machine, error="Cannot copy guest-init while a deploy is in progress")
    image = get_image(db, image_id) if image_id else get_image(db, machine.assigned_image_id)
    if image is None:
        return _detail(request, db, machine, error="Select an image first")
    try:
        _apply_guest_fields(
            db,
            machine,
            hostname=hostname,
            timezone=timezone,
            packages=packages,
            ssh_keys=ssh_keys,
            user_data="",
            unattend_xml="",
            image_id=int(image.id),
            allow_blank_hostname=False,
            write_seed=False,
            delete_empty_seed=False,
        )
        text = default_seed_text(int(image.id), image.os_family)
        validate_seed_template(text, image.os_family)
        copy_image_seed_to_machine(int(machine.id), int(image.id), image.os_family, overwrite=True)
        machine.assigned_image_id = image.id
        db.add(machine)
        record_activity(db, actor=user, action="machine.seed-copy", detail=f"image={image.name}")
        db.commit()
    except (ValueError, SeedError) as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/stage")
def machine_stage(
    request: Request,
    machine_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    if machine.state == MachineState.imaging.value:
        return _detail(request, db, machine, error="Cannot stage a reimage while imaging is in progress")
    image = get_image(db, machine.assigned_image_id)
    try:
        stage_machine(db, machine, actor=user, image=image)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/mark-deployed")
def machine_mark_deployed(machine_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    machine = _machine_or_404(db, machine_id)
    mark_deployed(db, machine, actor=user)
    db.commit()
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/disable")
def machine_disable(machine_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    machine = _machine_or_404(db, machine_id)
    disable_machine(db, machine, actor=user, disabled=True)
    db.commit()
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/enable")
def machine_enable(machine_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    machine = _machine_or_404(db, machine_id)
    disable_machine(db, machine, actor=user, disabled=False)
    db.commit()
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/delete")
def machine_delete(
    request: Request,
    machine_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    try:
        deleted_id = delete_machine(db, machine, actor=user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    remove_machine_seed_tree(deleted_id)
    return RedirectResponse(url="/machines", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/account")
def machine_account(
    request: Request,
    machine_id: int,
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    if edit_locked(machine):
        return _detail(request, db, machine, error="Cannot change the local account while a deploy is in progress")
    family = os_family_for(db, machine)
    kind = AccountKind.windows_administrator if family == OsFamily.windows else AccountKind.linux_root
    default_user = "Administrator" if kind == AccountKind.windows_administrator else "root"
    try:
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=kind,
            username=(username.strip() or default_user),
            password=password or None,
        )
        if machine.state == MachineState.deployed.value:
            image = get_image(db, machine.assigned_image_id)
            if image is not None:
                stage_machine(db, machine, actor=user, image=image)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _detail(request, db, machine, error=str(exc))
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)
