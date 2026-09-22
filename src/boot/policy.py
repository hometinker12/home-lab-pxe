"""Boot-policy decision for a resolved machine."""

from __future__ import annotations

from enum import StrEnum

from sqlmodel import Session

from ..inventory.service import INSTALL_STATES, get_image, get_open_attempt, image_extract_blocking
from ..models import Machine, MachineState, OsFamily
from .payload import BootPayload


class ScriptKind(StrEnum):
    unknown_local = "unknown_local"
    menu = "menu"
    install_linux = "install_linux"
    install_windows = "install_windows"
    image_not_ready = "image_not_ready"


def machine_menu_eligible(machine: Machine) -> bool:
    """Named or previously managed hosts see the folder menu. Unknown and disabled do not."""
    if machine.state == MachineState.disabled.value:
        return False
    if machine.state in INSTALL_STATES:
        return False
    if (machine.hostname or "").strip():
        return True
    return machine.state in {
        MachineState.ready.value,
        MachineState.deployed.value,
        MachineState.timeout_error.value,
        MachineState.failed.value,
    }


def decide_script(db: Session, machine: Machine) -> ScriptKind:
    state = machine.state
    if state in INSTALL_STATES:
        image = get_image(db, machine.assigned_image_id)
        if image is None:
            return ScriptKind.unknown_local if not machine_menu_eligible(machine) else ScriptKind.menu
        if image.os_family == OsFamily.tool.value:
            return ScriptKind.menu if machine_menu_eligible(machine) else ScriptKind.unknown_local
        attempt = get_open_attempt(db, machine)
        if attempt is None and image_extract_blocking(image):
            return ScriptKind.image_not_ready
        payload = BootPayload.from_attempt(attempt) if attempt is not None else BootPayload.from_image(image)
        if payload.os_family == OsFamily.windows.value:
            return ScriptKind.install_windows
        return ScriptKind.install_linux
    if machine_menu_eligible(machine):
        return ScriptKind.menu
    return ScriptKind.unknown_local
