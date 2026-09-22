from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..cloudinit.render import render_meta_data, render_user_data, render_vendor_data
from ..db import session_scope
from ..inventory.service import get_machine_for_guest_init
from ..security import VaultError
from ..settings import get_settings

router = APIRouter(tags=["cloudinit"])

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/cloud-init/{machine_id}/{instance_id}/meta-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/{instance_id}/meta-data/", include_in_schema=False)
def meta_data(machine_id: int, instance_id: str):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id, instance_id=instance_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        try:
            body = render_meta_data(machine)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail="unknown machine") from exc
    return PlainTextResponse(body, media_type="text/plain", headers=_NO_STORE)


@router.get("/cloud-init/{machine_id}/{instance_id}/user-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/{instance_id}/user-data/", include_in_schema=False)
def user_data(machine_id: int, instance_id: str):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id, instance_id=instance_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        try:
            body = render_user_data(db, machine)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail="unknown machine") from exc
    return PlainTextResponse(body, media_type="text/cloud-config", headers=_NO_STORE)


@router.get("/cloud-init/{machine_id}/{instance_id}/vendor-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/{instance_id}/vendor-data/", include_in_schema=False)
def vendor_data(machine_id: int, instance_id: str):
    with session_scope() as db:
        if get_machine_for_guest_init(db, machine_id, instance_id=instance_id) is None:
            raise HTTPException(status_code=404, detail="unknown machine")
    return PlainTextResponse(render_vendor_data(), media_type="text/cloud-config", headers=_NO_STORE)


@router.get("/cloud-init/{machine_id}/{instance_id}/", include_in_schema=False)
def seed_index(machine_id: int, instance_id: str):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id, instance_id=instance_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
    base = get_settings().public_url.rstrip("/")
    prefix = f"{base}/cloud-init/{machine_id}/{instance_id}"
    body = f"instance-id from {prefix}/meta-data\nuser-data {prefix}/user-data\n"
    return PlainTextResponse(body)


@router.get("/cloud-init/{machine_id}/meta-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/meta-data/", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/user-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/user-data/", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/vendor-data", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/vendor-data/", include_in_schema=False)
@router.get("/cloud-init/{machine_id}/", include_in_schema=False)
def legacy_seed(machine_id: int):
    raise HTTPException(status_code=404, detail="unknown machine")
