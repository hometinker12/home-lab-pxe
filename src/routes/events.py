from fastapi import APIRouter, HTTPException, Request

from ..db import session_scope
from ..inventory.service import get_machine, mark_deployed
from ..models import MachineState

router = APIRouter(tags=["events"])

_SUCCESS_EVENTS = frozenset({"deployed", "phone_home", "success"})


@router.post("/api/machines/{machine_id}/events", include_in_schema=False)
async def machine_event(machine_id: int, request: Request):
    event = "deployed"
    content_type = (request.headers.get("content-type") or "").lower()
    if "json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict) and body.get("event"):
            event = str(body.get("event")).strip().lower()
    if event not in _SUCCESS_EVENTS:
        raise HTTPException(status_code=400, detail="unsupported event")
    with session_scope() as db:
        machine = get_machine(db, machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        if machine.state not in {MachineState.deploying.value, MachineState.staged.value}:
            raise HTTPException(status_code=409, detail="machine is not installing")
        mark_deployed(db, machine, actor="installer")
        db.commit()
        return {"status": "ok", "state": machine.state}
