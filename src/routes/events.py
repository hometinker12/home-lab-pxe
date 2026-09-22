from fastapi import APIRouter, HTTPException, Request

from ..db import session_scope
from ..inventory.service import (
    INSTALL_LOG_MAX_BYTES,
    INSTALL_STATES,
    expire_stale_imaging,
    get_machine,
    mark_deployed,
    mark_imaging,
    mark_install_failed,
)

router = APIRouter(tags=["events"])

_SUCCESS_EVENTS = frozenset({"deployed", "phone_home", "success"})
_IMAGING_EVENTS = frozenset({"imaging", "installing"})
_ALLOWED_EVENTS = _SUCCESS_EVENTS | _IMAGING_EVENTS


async def _event_name(request: Request) -> str:
    event = (request.query_params.get("event") or "").strip().lower()
    content_type = (request.headers.get("content-type") or "").lower()
    if "json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict) and body.get("event"):
            event = str(body.get("event")).strip().lower()
    elif "form" in content_type or content_type.startswith("application/x-www-form-urlencoded"):
        form = await request.form()
        if form.get("event"):
            event = str(form.get("event")).strip().lower()
    return event or "deployed"


@router.post("/api/machines/{machine_id}/events", include_in_schema=False)
async def machine_event(machine_id: int, request: Request):
    event = await _event_name(request)
    if event not in _ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="unsupported event")
    with session_scope() as db:
        machine = get_machine(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        if event in _IMAGING_EVENTS:
            if machine.state not in INSTALL_STATES:
                raise HTTPException(status_code=409, detail="machine is not installing")
            mark_imaging(db, machine, actor="installer")
        else:
            expire_stale_imaging(db)
            if machine.state not in INSTALL_STATES:
                raise HTTPException(status_code=409, detail="machine is not installing")
            mark_deployed(db, machine, actor="installer")
        db.commit()
        return {"status": "ok", "state": machine.state}


@router.post("/api/machines/{machine_id}/install-log", include_in_schema=False)
async def machine_install_log(machine_id: int, request: Request):
    raw = await request.body()
    if len(raw) > INSTALL_LOG_MAX_BYTES:
        raw = raw[-INSTALL_LOG_MAX_BYTES:]
    text = raw.decode("utf-8", errors="replace")
    with session_scope() as db:
        machine = get_machine(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        if machine.state not in INSTALL_STATES:
            raise HTTPException(status_code=409, detail="machine is not installing")
        mark_install_failed(db, machine, log=text, actor="installer")
        db.commit()
        return {"status": "ok", "state": machine.state}
