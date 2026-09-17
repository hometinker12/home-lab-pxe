from fastapi import APIRouter, HTTPException, Request

from ..db import session_scope
from ..inventory.service import INSTALL_STATES, expire_stale_imaging, get_machine, mark_deployed, mark_imaging

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
        expire_stale_imaging(db)
        if machine.state not in INSTALL_STATES:
            raise HTTPException(status_code=409, detail="machine is not installing")
        if event in _IMAGING_EVENTS:
            mark_imaging(db, machine, actor="installer")
        else:
            mark_deployed(db, machine, actor="installer")
        db.commit()
        return {"status": "ok", "state": machine.state}
