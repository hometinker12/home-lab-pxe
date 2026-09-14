"""Boot-policy decision for a resolved machine."""

from __future__ import annotations

from enum import StrEnum

from sqlmodel import Session

from ..inventory.service import INSTALL_STATES, WAIT_STATES, get_image, os_family_for
from ..models import Machine, MachineState, OsFamily


class ScriptKind(StrEnum):
    wait = "wait"
    local = "local"
    install_linux = "install_linux"
    install_windows = "install_windows"


def decide_script(db: Session, machine: Machine) -> ScriptKind:
    state = machine.state
    if state in WAIT_STATES or state == MachineState.pending.value:
        return ScriptKind.wait
    if state in INSTALL_STATES:
        family = os_family_for(db, machine)
        image = get_image(db, machine.assigned_image_id)
        if image is None:
            return ScriptKind.wait
        if family == OsFamily.windows:
            return ScriptKind.install_windows
        return ScriptKind.install_linux
    if state == MachineState.deployed.value:
        return ScriptKind.local
    return ScriptKind.wait
