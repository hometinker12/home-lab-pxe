"""Render Windows unattend.xml, WinPE startup, and Cloudbase-Init metadata."""

from __future__ import annotations

import html
import re

from sqlmodel import Session

from ..inventory.service import get_image, get_open_attempt, load_overlay, resolve_local_account
from ..models import AccountKind, Image, InstallAttempt, Machine
from ..seed_render import SeedRenderError, render_selected_seed
from ..settings import get_settings

_SAFE_CMD = re.compile(r"^[A-Za-z0-9._~\\:/-]+$")


def _xml_escape(value: str) -> str:
    return html.escape(value, quote=True)


def render_unattend_legacy(db: Session, machine: Machine) -> str:
    overlay = load_overlay(machine.guest_overlay)
    hostname = (machine.hostname or overlay.get("hostname") or f"PXE{machine.id}")[:15]
    creds = resolve_local_account(db, int(machine.id), AccountKind.windows_administrator)
    username = "Administrator"
    password = ""
    if creds:
        username, password = creds
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <ComputerName>{_xml_escape(hostname)}</ComputerName>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <UserAccounts>
        <AdministratorPassword>
          <Value>{_xml_escape(password)}</Value>
          <PlainText>true</PlainText>
        </AdministratorPassword>
      </UserAccounts>
      <AutoLogon>
        <Username>{_xml_escape(username)}</Username>
        <Password>
          <Value>{_xml_escape(password)}</Value>
          <PlainText>true</PlainText>
        </Password>
        <Enabled>false</Enabled>
      </AutoLogon>
    </component>
  </settings>
</unattend>
"""


def render_unattend(db: Session, machine: Machine) -> str:
    attempt = get_open_attempt(db, machine)
    try:
        return render_selected_seed(db, machine, attempt=attempt)
    except SeedRenderError:
        return render_unattend_legacy(db, machine)


def render_winpeshl() -> str:
    return "[LaunchApps]\n%SYSTEMDRIVE%\\Windows\\System32\\cmd.exe, /c X:\\Windows\\System32\\startnet.cmd\n"


def _cmd_safe(value: str) -> str:
    text = (value or "").strip()
    if not text or not _SAFE_CMD.match(text):
        return ""
    return text


def render_startnet(db: Session, machine: Machine) -> str:
    settings = get_settings()
    attempt = get_open_attempt(db, machine)
    image = get_image(db, machine.assigned_image_id)
    image_id, revision, install_name = _media_coords(attempt, image)
    host = _cmd_safe(settings.smb_host)
    user = _cmd_safe(settings.smb_user)
    password = settings.smb_password
    if not _cmd_safe(password):
        password = ""
    media = f"{image_id}\\{revision}" if image_id and revision else ""
    install_from = f"Z:\\{media}\\sources\\{install_name}" if media and install_name else ""
    setup = f"Z:\\{media}\\setup.exe" if media else ""
    lines = [
        "@echo off",
        "wpeinit",
    ]
    if machine.id:
        imaging = f"{settings.public_url.rstrip('/')}/api/machines/{int(machine.id)}/events?event=imaging"
        lines.append(f'curl.exe -s -o NUL -X POST "{imaging}"')
    if host and user and password:
        share = f"\\\\{host}\\pxe-media"
        lines.extend(
            [
                "set /a _n=0",
                ":map",
                f"net use Z: {share} /user:{user} {password}",
                "if %ERRORLEVEL%==0 goto mapped",
                "set /a _n+=1",
                "if %_n% GEQ 30 goto fail",
                "ping -n 4 127.0.0.1 >NUL",
                "goto map",
                ":mapped",
            ]
        )
        if setup and install_from:
            lines.append(f'"{setup}" /unattend:X:\\Windows\\System32\\unattend.xml /installfrom:{install_from}')
        else:
            lines.append("echo Installation media path is not published.")
        lines.extend(
            [
                "goto end",
                ":fail",
                "echo Unable to map installation media.",
            ]
        )
    else:
        lines.append("echo Installation media path is not published.")
    lines.append(":end")
    return "\r\n".join(lines) + "\r\n"


def _media_coords(attempt: InstallAttempt | None, image: Image | None) -> tuple[int, int, str]:
    media = ""
    install = ""
    if attempt is not None:
        media = (attempt.media_relative or "").strip()
        install = (attempt.install_wim_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not media and image is not None:
        media = (image.extract_generation or "").strip()
        install = (image.install_wim_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not install:
        install = "install.wim"
    if install not in {"install.wim", "install.esd"}:
        install = "install.wim"
    if "/" in media:
        left, right = media.split("/", 1)
        try:
            return int(left), int(right.split("/")[0]), install
        except ValueError:
            return 0, 0, install
    return 0, 0, install


def render_cloudbase_meta(machine: Machine) -> str:
    hostname = (machine.hostname or f"pxe-{machine.id}").strip()
    return f"instance-id: {machine.instance_id}\nlocal-hostname: {hostname}\n"


def render_cloudbase_user_data(db: Session, machine: Machine) -> str:
    overlay = load_overlay(machine.guest_overlay)
    hostname = (machine.hostname or overlay.get("hostname") or f"pxe-{machine.id}").strip()
    creds = resolve_local_account(db, int(machine.id), AccountKind.windows_administrator)
    lines = ["#cloud-config", f"hostname: {hostname}"]
    if creds:
        username, password = creds
        lines.append("users:")
        lines.append(f"  - name: {username}")
        lines.append(f"    passwd: {password}")
        lines.append("    primary_group: Administrators")
    raw = str(overlay.get("raw_overlay") or "").strip()
    if raw:
        lines.append(raw)
    public = get_settings().public_url
    lines.append("phone_home:")
    lines.append(f"  url: {public}/api/machines/{machine.id}/events")
    return "\n".join(lines) + "\n"
