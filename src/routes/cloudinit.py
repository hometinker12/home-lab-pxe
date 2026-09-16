from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..cloudinit.render import render_meta_data, render_user_data, render_vendor_data
from ..db import session_scope
from ..inventory.service import get_machine_for_guest_init
from ..settings import get_settings

router = APIRouter(tags=["cloudinit"])

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/cloud-init/{machine_id}/meta-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/meta-data/", include_in_schema=False)
def meta_data(machine_id: int):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        body = render_meta_data(machine)
    return PlainTextResponse(body, media_type="text/plain", headers=_NO_STORE)


@router.get("/cloud-init/{machine_id}/user-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/user-data/", include_in_schema=False)
def user_data(machine_id: int):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        body = render_user_data(db, machine)
    return PlainTextResponse(body, media_type="text/cloud-config", headers=_NO_STORE)


@router.get("/cloud-init/{machine_id}/vendor-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/vendor-data/", include_in_schema=False)
def vendor_data(machine_id: int):
    with session_scope() as db:
        if get_machine_for_guest_init(db, machine_id) is None:
            raise HTTPException(status_code=404, detail="unknown machine")
    return PlainTextResponse(render_vendor_data(), media_type="text/cloud-config", headers=_NO_STORE)


@router.get("/cloud-init/{machine_id}/", include_in_schema=False)
def seed_index(machine_id: int):
    with session_scope() as db:
        if get_machine_for_guest_init(db, machine_id) is None:
            raise HTTPException(status_code=404, detail="unknown machine")
    base = get_settings().public_url
    body = (
        f"instance-id from {base}/cloud-init/{machine_id}/meta-data\n"
        f"user-data {base}/cloud-init/{machine_id}/user-data\n"
    )
    return PlainTextResponse(body)
