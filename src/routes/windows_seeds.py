from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response

from ..db import session_scope
from ..inventory.service import get_machine_for_guest_init
from ..windows.render import render_cloudbase_meta, render_cloudbase_user_data, render_unattend

router = APIRouter(tags=["windows"])


@router.get("/windows/{machine_id}/unattend.xml", include_in_schema=False)
def unattend(machine_id: int):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        body = render_unattend(db, machine)
    return Response(content=body, media_type="application/xml")


@router.get("/cloudbase-init/{machine_id}/meta-data", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/meta-data/", include_in_schema=False)
def cbi_meta(machine_id: int):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        return PlainTextResponse(render_cloudbase_meta(machine))


@router.get("/cloudbase-init/{machine_id}/user-data", include_in_schema=False)
@router.get("/cloudbase-init/{machine_id}/user-data/", include_in_schema=False)
def cbi_user(machine_id: int):
    with session_scope() as db:
        machine = get_machine_for_guest_init(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        return PlainTextResponse(render_cloudbase_user_data(db, machine), media_type="text/cloud-config")


@router.get("/cloudbase-init/{machine_id}/", include_in_schema=False)
def cbi_index(machine_id: int):
    with session_scope() as db:
        if get_machine_for_guest_init(db, machine_id) is None:
            raise HTTPException(status_code=404, detail="unknown machine")
    return PlainTextResponse("meta-data\nuser-data\n")
