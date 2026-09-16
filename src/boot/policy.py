"""Boot-policy decision for a resolved machine."""

from __future__ import annotations

from enum import StrEnum

from sqlmodel import Session

from ..inventory.service import INSTALL_STATES, WAIT_STATES, get_image, get_open_attempt, image_extract_blocking
from ..models import Machine, MachineState, OsFamily
from .payload import BootPayload


class ScriptKind(StrEnum):
    wait = "wait"
    local = "local"
    install_linux = "install_linux"
    install_windows = "install_windows"
    image_not_ready = "image_not_ready"


def decide_script(db: Session, machine: Machine) -> ScriptKind:
    state = machine.state
    if state in WAIT_STATES or state == MachineState.pending.value:
        return ScriptKind.wait
    if state in INSTALL_STATES:
        image = get_image(db, machine.assigned_image_id)
        if image is None:
            return ScriptKind.wait
        attempt = get_open_attempt(db, machine)
        if attempt is None and image_extract_blocking(image):
            return ScriptKind.image_not_ready
        payload = BootPayload.from_attempt(attempt) if attempt is not None else BootPayload.from_image(image)
        if payload.os_family == OsFamily.windows.value:
            return ScriptKind.install_windows
        return ScriptKind.install_linux
    if state == MachineState.deployed.value:
        return ScriptKind.local
    return ScriptKind.wait
