from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..db import session_scope
from ..inventory.service import get_image
from ..paths import UnsafePathError, resolve_under
from ..settings import get_settings

router = APIRouter(tags=["boot-files"])

_SLOT = {
    "kernel": "kernel_path",
    "initrd": "initrd_path",
    "boot.wim": "boot_wim_path",
    "install.wim": "install_wim_path",
    "iso": "iso_path",
}


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
    if not relative:
        raise HTTPException(status_code=404, detail="file not registered")
    try:
        path = resolve_under(get_settings().image_root, relative)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path)


@router.get("/tftp/{name}", include_in_schema=False)
def tftp_http(name: str):
    try:
        path = resolve_under(get_settings().tftp_root, name)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path)
