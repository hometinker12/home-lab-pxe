from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from ..boot.ipxe import render_script, tool_boot_script
from ..boot.menu import folder_menu_script
from ..boot.payload import BootPayload
from ..boot.policy import ScriptKind, decide_script, machine_menu_eligible
from ..db import session_scope
from ..inventory.boot_menu import get_or_create_settings
from ..inventory.mac import InvalidMacError, mac_hyphen, normalize_mac
from ..inventory.service import (
    INSTALL_STATES,
    deploy_machine,
    expire_stale_imaging,
    get_image,
    get_open_attempt,
    image_deploy_blocked,
    record_boot_event,
    stage_machine,
    touch_machine,
)
from ..models import Machine, MachineState, OsFamily
from ..settings import get_settings
from ..tftp_store import boot_chain_script_body
from ..web import client_ip, reported_client_ip

router = APIRouter(tags=["ipxe"])


def _boot_chain_response() -> PlainTextResponse:
    return PlainTextResponse(boot_chain_script_body(get_settings().public_url), media_type="text/plain")


@router.get("/boot.ipxe", include_in_schema=False)
def boot_ipxe():
    return _boot_chain_response()


@router.get("/autoexec.ipxe", include_in_schema=False)
def autoexec_ipxe():
    return _boot_chain_response()


def _invalid_mac() -> PlainTextResponse:
    return PlainTextResponse("#!ipxe\necho invalid mac\nsleep 5\n", status_code=400)


def _client_addr(
    request: Request,
    ip: str | None,
) -> str:
    return reported_client_ip(client_ip(request), ip)


def _payload_for(db: Session, machine: Machine) -> BootPayload | None:
    image = get_image(db, machine.assigned_image_id)
    attempt = get_open_attempt(db, machine)
    if attempt is not None:
        return BootPayload.from_attempt(attempt)
    if image is not None:
        return BootPayload.from_image(image)
    return None


def _render_policy_script(db: Session, machine: Machine, mac_n: str) -> tuple[str, str]:
    kind = decide_script(db, machine)
    hyphen = mac_hyphen(mac_n)
    if kind == ScriptKind.menu:
        return folder_menu_script(db, hyphen, None), kind.value
    settings = get_or_create_settings(db)
    script = render_script(
        kind,
        mac_hyphen=hyphen,
        machine=machine,
        payload=_payload_for(db, machine),
        unknown_timeout_seconds=settings.unknown_timeout_seconds,
    )
    return script, kind.value


@router.get("/ipxe/{mac}", include_in_schema=False)
def ipxe_script(
    mac: str,
    request: Request,
    uuid: str | None = Query(default=None),
    ip: str | None = Query(default=None),
    manufacturer: str | None = Query(default=None),
    product: str | None = Query(default=None),
    serial: str | None = Query(default=None),
):
    try:
        mac_n = normalize_mac(mac)
    except InvalidMacError:
        return _invalid_mac()
    client = _client_addr(request, ip)
    with session_scope() as db:
        machine = touch_machine(
            db,
            mac=mac_n,
            uuid=uuid,
            client_ip=client,
            manufacturer=manufacturer,
            product=product,
            serial=serial,
        )
        expire_stale_imaging(db)
        script, kind = _render_policy_script(db, machine, mac_n)
        record_boot_event(db, machine, client_ip=client, script_kind=kind)
        db.commit()
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/ipxe/{mac}/menu/{folder_id}", include_in_schema=False)
def ipxe_folder_menu(
    mac: str,
    folder_id: int,
    request: Request,
    uuid: str | None = Query(default=None),
    ip: str | None = Query(default=None),
    manufacturer: str | None = Query(default=None),
    product: str | None = Query(default=None),
    serial: str | None = Query(default=None),
):
    try:
        mac_n = normalize_mac(mac)
    except InvalidMacError:
        return _invalid_mac()
    client = _client_addr(request, ip)
    with session_scope() as db:
        machine = touch_machine(
            db,
            mac=mac_n,
            uuid=uuid,
            client_ip=client,
            manufacturer=manufacturer,
            product=product,
            serial=serial,
        )
        expire_stale_imaging(db)
        hyphen = mac_hyphen(mac_n)
        kind = decide_script(db, machine)
        if kind != ScriptKind.menu:
            script, script_kind = _render_policy_script(db, machine, mac_n)
        else:
            script = folder_menu_script(db, hyphen, folder_id)
            script_kind = kind.value
        record_boot_event(db, machine, client_ip=client, script_kind=script_kind)
        db.commit()
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/ipxe/{mac}/boot/{image_id}", include_in_schema=False)
def ipxe_boot_image(
    mac: str,
    image_id: int,
    request: Request,
    uuid: str | None = Query(default=None),
    ip: str | None = Query(default=None),
    manufacturer: str | None = Query(default=None),
    product: str | None = Query(default=None),
    serial: str | None = Query(default=None),
):
    try:
        mac_n = normalize_mac(mac)
    except InvalidMacError:
        return _invalid_mac()
    client = _client_addr(request, ip)
    with session_scope() as db:
        machine = touch_machine(
            db,
            mac=mac_n,
            uuid=uuid,
            client_ip=client,
            manufacturer=manufacturer,
            product=product,
            serial=serial,
        )
        expire_stale_imaging(db)
        hyphen = mac_hyphen(mac_n)
        kind = decide_script(db, machine)
        if kind in {ScriptKind.install_linux, ScriptKind.install_windows, ScriptKind.image_not_ready}:
            script, script_kind = _render_policy_script(db, machine, mac_n)
            record_boot_event(db, machine, client_ip=client, script_kind=script_kind)
            db.commit()
            return PlainTextResponse(script, media_type="text/plain")
        if kind != ScriptKind.menu or not machine_menu_eligible(machine):
            script, script_kind = _render_policy_script(db, machine, mac_n)
            record_boot_event(db, machine, client_ip=client, script_kind=script_kind)
            db.commit()
            return PlainTextResponse(script, media_type="text/plain")
        image = get_image(db, image_id)
        if image is None or image_deploy_blocked(image):
            script = folder_menu_script(db, hyphen, None)
            record_boot_event(db, machine, client_ip=client, script_kind=ScriptKind.menu.value)
            db.commit()
            return PlainTextResponse(script, media_type="text/plain")
        if image.os_family == OsFamily.tool.value:
            script = tool_boot_script(BootPayload.from_image(image))
            record_boot_event(db, machine, client_ip=client, script_kind="tool")
            db.commit()
            return PlainTextResponse(script, media_type="text/plain")
        try:
            if machine.state == MachineState.deployed.value:
                stage_machine(db, machine, actor="pxe-menu", image=image)
            elif machine.state not in INSTALL_STATES:
                deploy_machine(db, machine, image=image, actor="pxe-menu")
            db.commit()
        except ValueError:
            db.rollback()
            script = folder_menu_script(db, hyphen, None)
            return PlainTextResponse(script, media_type="text/plain")
        script, script_kind = _render_policy_script(db, machine, mac_n)
        record_boot_event(db, machine, client_ip=client, script_kind=script_kind)
        db.commit()
    return PlainTextResponse(script, media_type="text/plain")
