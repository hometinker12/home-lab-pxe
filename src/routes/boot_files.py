from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..db import session_scope
from ..inventory.service import get_image, get_machine_for_guest_init, get_open_attempt
from ..models import InstallAttempt
from ..paths import UnsafePathError, resolve_under
from ..settings import get_settings

router = APIRouter(tags=["boot-files"])

_SLOT = {
    "kernel": "kernel_path",
    "initrd": "initrd_path",
    "boot.wim": "boot_wim_path",
    "install.wim": "install_wim_path",
    "iso": "iso_path",
    "image.iso": "iso_path",
}


def _file_response(relative: str):
    if not relative:
        raise HTTPException(status_code=404, detail="file not registered")
    try:
        path = resolve_under(get_settings().image_root, relative)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path)


def _slot_from_attempt(attempt: InstallAttempt, slot: str) -> str:
    attr = _SLOT.get(slot)
    if attr is None:
        raise HTTPException(status_code=404, detail="unknown boot file")
    return getattr(attempt, attr) or ""


@router.get("/boot-files/{image_id}/{slot}", include_in_schema=False)
def boot_file(image_id: int, slot: str):
    attr = _SLOT.get(slot)
    if attr is None:
        raise HTTPException(status_code=404, detail="unknown boot file")
    with session_scope() as db:
        image = get_image(db, image_id)
        if image is None:
            raise HTTPException(status_code=404, detail="unknown image")
        relative = getattr(image, attr) or ""
    return _file_response(relative)


@router.get("/install-files/{machine_id}/{slot}", include_in_schema=False)
def install_file(machine_id: int, slot: str):
    if slot not in _SLOT:
        raise HTTPException(status_code=404, detail="unknown boot file")
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        attempt = get_open_attempt(db, machine)
        if attempt is not None:
            relative = _slot_from_attempt(attempt, slot)
        else:
            image = get_image(db, machine.assigned_image_id)
            if image is None:
                raise HTTPException(status_code=404, detail="unknown image")
            relative = getattr(image, _SLOT[slot]) or ""
    return _file_response(relative)


@router.get("/tftp/{name}", include_in_schema=False)
def tftp_http(name: str):
    try:
        path = resolve_under(get_settings().tftp_root, name)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path)
