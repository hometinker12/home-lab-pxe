from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response

from ..db import session_scope
from ..inventory.service import get_machine_for_guest_init
from ..security import VaultError
from ..windows.render import (
    render_cloudbase_meta,
    render_cloudbase_user_data,
    render_startnet,
    render_unattend,
    render_winpeshl,
)

router = APIRouter(tags=["windows"])

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/windows/{machine_id}/{instance_id}/unattend.xml", include_in_schema=False)
def unattend(machine_id: int, instance_id: str):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id, instance_id=instance_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        try:
            body = render_unattend(db, machine)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail="unknown machine") from exc
    return Response(content=body, media_type="application/xml", headers=_NO_STORE)


@router.get("/windows/{machine_id}/{instance_id}/winpeshl.ini", include_in_schema=False)
def winpeshl(machine_id: int, instance_id: str):
    with session_scope() as db:
        if get_machine_for_guest_init(db, machine_id, instance_id=instance_id) is None:
            raise HTTPException(status_code=404, detail="unknown machine")
    return PlainTextResponse(render_winpeshl(), media_type="text/plain", headers=_NO_STORE)


@router.get("/windows/{machine_id}/{instance_id}/startnet.cmd", include_in_schema=False)
def startnet(machine_id: int, instance_id: str):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id, instance_id=instance_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        try:
            body = render_startnet(db, machine)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail="unknown machine") from exc
    return PlainTextResponse(body, media_type="text/plain", headers=_NO_STORE)


@router.get("/cloudbase-init/{machine_id}/{instance_id}/meta-data", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/{instance_id}/meta-data/", include_in_schema=False)
def cbi_meta(machine_id: int, instance_id: str):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id, instance_id=instance_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        try:
            body = render_cloudbase_meta(machine)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail="unknown machine") from exc
        return PlainTextResponse(body, headers=_NO_STORE)


@router.get("/cloudbase-init/{machine_id}/{instance_id}/user-data", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/{instance_id}/user-data/", include_in_schema=False)
def cbi_user(machine_id: int, instance_id: str):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id, instance_id=instance_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        try:
            body = render_cloudbase_user_data(db, machine)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail="unknown machine") from exc
        return PlainTextResponse(body, media_type="text/cloud-config", headers=_NO_STORE)


@router.get("/cloudbase-init/{machine_id}/{instance_id}/", include_in_schema=False)
def cbi_index(machine_id: int, instance_id: str):
    with session_scope() as db:
        if get_machine_for_guest_init(db, machine_id, instance_id=instance_id) is None:
            raise HTTPException(status_code=404, detail="unknown machine")
    return PlainTextResponse("meta-data\nuser-data\n")


@router.get("/windows/{machine_id}/unattend.xml", include_in_schema=False)
@router.get("/windows/{machine_id}/winpeshl.ini", include_in_schema=False)
@router.get("/windows/{machine_id}/startnet.cmd", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/meta-data", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/meta-data/", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/user-data", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/user-data/", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/", include_in_schema=False)
def legacy_windows(machine_id: int):
    raise HTTPException(status_code=404, detail="unknown machine")
