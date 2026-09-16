from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from ..boot.ipxe import render_script
from ..boot.payload import BootPayload
from ..boot.policy import decide_script
from ..db import session_scope
from ..inventory.mac import InvalidMacError, mac_hyphen, normalize_mac
from ..inventory.service import get_image, get_open_attempt, record_boot_event, touch_machine
from ..settings import get_settings
from ..web import client_ip

router = APIRouter(tags=["ipxe"])


@router.get("/boot.ipxe", include_in_schema=False)
def boot_ipxe():
    base = get_settings().public_url
    body = f"#!ipxe\nchain --replace {base}/ipxe/${{mac:hexhyp}}?uuid=${{uuid}}&ip=${{ip}}\n"
    return PlainTextResponse(body, media_type="text/plain")


@router.get("/ipxe/{mac}", include_in_schema=False)
def ipxe_script(
    mac: str,
    request: Request,
    uuid: str | None = Query(default=None),
    ip: str | None = Query(default=None),
):
    try:
        mac_n = normalize_mac(mac)
    except InvalidMacError:
        return PlainTextResponse("#!ipxe\necho invalid mac\nsleep 5\n", status_code=400)
    client = ip or client_ip(request)
    with session_scope() as db:
        machine = touch_machine(db, mac=mac_n, uuid=uuid, client_ip=client)
        kind = decide_script(db, machine)
        image = get_image(db, machine.assigned_image_id)
        attempt = get_open_attempt(db, machine)
        payload = None
        if attempt is not None:
            payload = BootPayload.from_attempt(attempt)
        elif image is not None:
            payload = BootPayload.from_image(image)
        script = render_script(kind, mac_hyphen=mac_hyphen(mac_n), machine=machine, payload=payload)
        record_boot_event(db, machine, client_ip=client, script_kind=kind.value)
        db.commit()
    return PlainTextResponse(script, media_type="text/plain")
