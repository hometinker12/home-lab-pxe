"""Render Linux nocloud-net payloads. Inject vault credentials at serve time."""

from __future__ import annotations

from sqlmodel import Session

from ..inventory.service import get_open_attempt, load_overlay, resolve_local_account
from ..models import AccountKind, Machine
from ..seed_render import SeedRenderError, render_selected_seed
from ..settings import get_settings


def render_meta_data(machine: Machine) -> str:
    hostname = (machine.hostname or f"pxe-{machine.id}").strip() or f"pxe-{machine.id}"
    return f"instance-id: {machine.instance_id}\nlocal-hostname: {hostname}\n"


def render_vendor_data() -> str:
    return "#cloud-config\n"


def render_user_data_legacy(db: Session, machine: Machine) -> str:
    overlay = load_overlay(machine.guest_overlay)
    hostname = (machine.hostname or overlay.get("hostname") or f"pxe-{machine.id}").strip()
    timezone = str(overlay.get("timezone") or "UTC")
    packages = overlay.get("packages") or []
    if not isinstance(packages, list):
        packages = []
    ssh_keys = overlay.get("ssh_keys") or []
    if not isinstance(ssh_keys, list):
        ssh_keys = []
    raw = str(overlay.get("raw_overlay") or "").strip()
    creds = resolve_local_account(db, int(machine.id), AccountKind.linux_root)
    lines = ["#cloud-config", f"hostname: {hostname}", "manage_etc_hosts: true", f"timezone: {timezone}"]
    if creds:
        username, password = creds
        lines.append("ssh_pwauth: true")
        lines.append("disable_root: false")
        lines.append("chpasswd:")
        lines.append("  expire: false")
        lines.append("  list: |")
        lines.append(f"    {username}:{password}")
        if username != "root":
            lines.append("users:")
            lines.append(f"  - name: {username}")
            lines.append("    sudo: ALL=(ALL) NOPASSWD:ALL")
            lines.append("    lock_passwd: false")
    if ssh_keys:
        lines.append("ssh_authorized_keys:")
        for key in ssh_keys:
            text = str(key).strip()
            if text:
                lines.append(f"  - {text}")
    pkg_names = [str(p).strip() for p in packages if str(p).strip()]
    if pkg_names:
        lines.append("packages:")
        for pkg in pkg_names:
            lines.append(f"  - {pkg}")
    phone = str(overlay.get("phone_home_url") or "").strip()
    if not phone:
        phone = f"{get_settings().public_url}/api/machines/{machine.id}/events"
    lines.append("phone_home:")
    lines.append(f"  url: {phone}")
    lines.append("  tries: 5")
    if raw:
        lines.append(raw)
    return "\n".join(lines) + "\n"


def render_user_data(db: Session, machine: Machine) -> str:
    attempt = get_open_attempt(db, machine)
    try:
        return render_selected_seed(db, machine, attempt=attempt)
    except SeedRenderError:
        return render_user_data_legacy(db, machine)
