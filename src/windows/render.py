"""Render Windows unattend.xml and Cloudbase-Init metadata at request time."""

from __future__ import annotations

import html

from sqlmodel import Session

from ..inventory.service import load_overlay, resolve_local_account
from ..models import AccountKind, Machine
from ..settings import get_settings


def _xml_escape(value: str) -> str:
    return html.escape(value, quote=True)


def render_unattend(db: Session, machine: Machine) -> str:
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
        lines.append("    passwd: '********'")
        lines.append("    primary_group: Administrators")
        # Cloudbase-Init accepts passwd; inject real password only in this in-memory render.
        lines[-2] = f"    passwd: {password}"
    raw = str(overlay.get("raw_overlay") or "").strip()
    if raw:
        lines.append(raw)
    public = get_settings().public_url
    lines.append("phone_home:")
    lines.append(f"  url: {public}/api/machines/{machine.id}/events")
    return "\n".join(lines) + "\n"
