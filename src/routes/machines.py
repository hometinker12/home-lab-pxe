from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..inventory.mac import InvalidMacError
from ..inventory.service import (
    deploy_machine,
    disable_machine,
    dump_overlay,
    get_image,
    get_machine,
    list_boot_events,
    list_images,
    list_machines,
    load_overlay,
    local_account_status,
    mark_deployed,
    mark_ready,
    os_family_for,
    register_machine,
    stage_machine,
    upsert_local_account,
)
from ..models import AccountKind, MachineState, OsFamily
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


def _machine_or_404(db: Session, machine_id: int):
    machine = get_machine(db, machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="unknown machine")
    return machine


@router.get("/api/machines")
def api_machines(db: Session = Depends(get_db), user: str = Depends(require_user)):
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
    }


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/machines", status_code=HTTP_303_SEE_OTHER)


@router.get("/machines", response_class=HTMLResponse)
def machines_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    machines = list_machines(db)
    pending = [m for m in machines if m.state in {MachineState.pending.value, MachineState.ready.value}]
    images = {img.id: img for img in list_images(db)}
    return render(request, "machines.html", machines=machines, pending=pending, images=images, error=None)


def _machines_error(request: Request, db: Session, error: str):
    machines = list_machines(db)
    pending = [m for m in machines if m.state in {MachineState.pending.value, MachineState.ready.value}]
    images = {img.id: img for img in list_images(db)}
    return render(request, "machines.html", machines=machines, pending=pending, images=images, error=error)


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
        return _machines_error(request, db, str(exc))
    except ValueError as exc:
        return _machines_error(request, db, str(exc))
    return RedirectResponse(url=f"/machines/{machine.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/machines/{machine_id}", response_class=HTMLResponse)
def machine_detail(request: Request, machine_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    machine = _machine_or_404(db, machine_id)
    images = list_images(db)
    image = get_image(db, machine.assigned_image_id)
    family = os_family_for(db, machine)
    kind = AccountKind.windows_administrator if family == OsFamily.windows else AccountKind.linux_root
    account = local_account_status(db, int(machine.id), kind)
    overlay = load_overlay(machine.guest_overlay)
    events = list_boot_events(db, int(machine.id))
    return render(
        request,
        "machine_detail.html",
        machine=machine,
        images=images,
        image=image,
        family=family.value,
        account=account,
        overlay=overlay,
        events=events,
        error=None,
    )


@router.post("/machines/{machine_id}/save")
def machine_save(
    request: Request,
    machine_id: int,
    hostname: str = Form(""),
    timezone: str = Form("UTC"),
    packages: str = Form(""),
    ssh_keys: str = Form(""),
    raw_overlay: str = Form(""),
    image_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    overlay = load_overlay(machine.guest_overlay)
    overlay.update(
        {
            "hostname": hostname.strip(),
            "timezone": timezone.strip() or "UTC",
            "packages": [p.strip() for p in packages.split(",") if p.strip()],
            "ssh_keys": [k.strip() for k in ssh_keys.splitlines() if k.strip()],
            "raw_overlay": raw_overlay,
        }
    )
    mark_ready(db, machine, hostname=hostname, actor=user)
    if image_id:
        img = get_image(db, image_id)
        if img:
            machine.assigned_image_id = img.id
    machine.guest_overlay = dump_overlay(overlay)
    db.add(machine)
    db.commit()
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/deploy")
def machine_deploy(
    machine_id: int,
    image_id: int = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=400, detail="unknown image")
    kind = AccountKind.windows_administrator if image.os_family == OsFamily.windows.value else AccountKind.linux_root
    default_user = "Administrator" if kind == AccountKind.windows_administrator else "root"
    if password:
        upsert_local_account(
            db,
            machine_id=int(machine.id),
            kind=kind,
            username=(username.strip() or default_user),
            password=password,
        )
    deploy_machine(db, machine, image=image, actor=user)
    db.commit()
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/machines/{machine_id}/stage")
def machine_stage(
    machine_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    image = get_image(db, machine.assigned_image_id)
    stage_machine(db, machine, actor=user, image=image)
    db.commit()
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


@router.post("/machines/{machine_id}/account")
def machine_account(
    machine_id: int,
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    machine = _machine_or_404(db, machine_id)
    family = os_family_for(db, machine)
    kind = AccountKind.windows_administrator if family == OsFamily.windows else AccountKind.linux_root
    default_user = "Administrator" if kind == AccountKind.windows_administrator else "root"
    upsert_local_account(
        db,
        machine_id=int(machine.id),
        kind=kind,
        username=(username.strip() or default_user),
        password=password or None,
    )
    db.commit()
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=HTTP_303_SEE_OTHER)
