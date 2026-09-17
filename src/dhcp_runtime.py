"""Persisted DHCP console settings and apply handshake for the entrypoint helper."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from sqlmodel import Session, select

from .dhcp_config import DnsmasqSpec, render_dnsmasq_conf
from .models import DhcpRuntime, utcnow
from .settings import get_settings
from .timezones import is_valid_timezone

DHCP_RUNTIME_ID = 1
DEFAULT_IMAGING_TIMEOUT_MINUTES = 15
MAX_IMAGING_TIMEOUT_MINUTES = 1440
DEFAULT_TIMEZONE = "UTC"
_IFACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")
_RANGE = re.compile(r"^[0-9A-Za-z.,:/ _-]{3,80}$")
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_ALLOWED_KEYS = (
    "dhcp-option",
    "dhcp-option-force",
    "dhcp-range",
    "dhcp-host",
    "dhcp-boot",
    "dhcp-ignore",
    "dhcp-match",
    "dhcp-vendorclass",
    "dhcp-userclass",
    "pxe-service",
    "pxe-prompt",
)
_LINE = re.compile(r"^(" + "|".join(re.escape(k) for k in _ALLOWED_KEYS) + r")=.+$")
_BANNED = ("..", "conf-file", "dhcp-script", "resolv-file", "servers-file", "addn-hosts", "`", "$(", "\x00")


class DhcpConfigError(ValueError):
    pass


def data_dir():
    path = get_settings().data_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def conf_path():
    return data_dir() / "dnsmasq-pxe.conf"


def enabled_path():
    return data_dir() / "dhcp.enabled"


def tftp_enabled_path():
    return data_dir() / "tftp.enabled"


def cmd_path():
    return data_dir() / "dhcp.cmd"


def status_path():
    return data_dir() / "dhcp.status"


def helper_status() -> str:
    try:
        raw = status_path().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "unknown"
    if raw in {"running", "stopped", "failed"}:
        return raw
    return "unknown"


def parse_extra_options(raw: str) -> list[str]:
    lines: list[str] = []
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if len(text) > 256:
            raise DhcpConfigError("DHCP option lines must be 256 characters or less")
        lowered = text.lower()
        if any(token in lowered for token in _BANNED):
            raise DhcpConfigError(f"DHCP option is not allowed: {text}")
        if not _LINE.match(text):
            raise DhcpConfigError(
                "Each extra option must be key=value using dhcp-option, dhcp-range, dhcp-host, "
                "dhcp-boot, dhcp-ignore, dhcp-match, dhcp-vendorclass, dhcp-userclass, pxe-service, or pxe-prompt"
            )
        lines.append(text)
        if len(lines) > 40:
            raise DhcpConfigError("At most 40 extra DHCP option lines are allowed")
    return lines


def _ipv4_list(value: str, *, label: str) -> str:
    text = value.strip()
    if not text:
        return ""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    for part in parts:
        if not _IPV4.match(part):
            raise DhcpConfigError(f"Invalid {label}: {part}")
        if any(int(octet) > 255 for octet in part.split(".")):
            raise DhcpConfigError(f"Invalid {label}: {part}")
    return ",".join(parts)


def validate_fields(
    *,
    mode: str,
    bind_interface: str,
    dhcp_range: str,
    dhcp_router: str,
    dhcp_dns: str,
    extra_options: str,
) -> dict:
    mode_n = mode.strip().lower()
    if mode_n not in {"proxy", "authoritative"}:
        raise DhcpConfigError("DHCP mode must be proxy or authoritative")
    iface = bind_interface.strip()
    if not _IFACE.match(iface):
        raise DhcpConfigError("Bind interface is invalid")
    range_n = dhcp_range.strip() or "192.168.1.200,192.168.1.250,12h"
    if not _RANGE.match(range_n):
        raise DhcpConfigError("DHCP range is invalid")
    extras = parse_extra_options(extra_options)
    return {
        "mode": mode_n,
        "bind_interface": iface,
        "dhcp_range": range_n,
        "dhcp_router": _ipv4_list(dhcp_router, label="router"),
        "dhcp_dns": _ipv4_list(dhcp_dns, label="DNS"),
        "extra_options": "\n".join(extras),
        "extra_list": extras,
    }


def external_dhcp_hints(snapshot=None) -> dict[str, str]:
    public = get_settings().public_url.rstrip("/")
    host = urlparse(public).hostname or public
    lan = getattr(snapshot, "host_lan_ipv4", "") if snapshot is not None else ""
    if lan and host in {"127.0.0.1", "localhost", "::1", ""}:
        next_server = lan
        ipxe_base = f"http://{lan}:{urlparse(public).port or 8080}"
    else:
        next_server = host
        ipxe_base = public
    return {
        "vendor_class": "PXEClient",
        "next_server": next_server,
        "bios_filename": "undionly.kpxe",
        "efi_filename": "ipxe.efi",
        "arm_filename": "snponly.efi",
        "ipxe_filename": "boot.ipxe",
        "ipxe_script": f"{ipxe_base}/boot.ipxe",
    }


def default_imaging_timeout_minutes() -> int:
    raw = (os.getenv("PXE_IMAGING_TIMEOUT_MINUTES") or "").strip()
    if not raw:
        return DEFAULT_IMAGING_TIMEOUT_MINUTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_IMAGING_TIMEOUT_MINUTES
    return max(0, min(MAX_IMAGING_TIMEOUT_MINUTES, value))


def imaging_timeout_minutes(db: Session) -> int:
    row = get_or_create_runtime(db)
    minutes = row.imaging_timeout_minutes
    if minutes is None:
        return DEFAULT_IMAGING_TIMEOUT_MINUTES
    return int(minutes)


def env_default_timezone() -> str:
    raw = (os.getenv("PXE_DEFAULT_TIMEZONE") or "").strip() or DEFAULT_TIMEZONE
    return raw if is_valid_timezone(raw) else DEFAULT_TIMEZONE


def default_timezone(db: Session) -> str:
    row = get_or_create_runtime(db)
    tz = (row.default_timezone or "").strip() or env_default_timezone()
    return tz if is_valid_timezone(tz) else DEFAULT_TIMEZONE


def get_or_create_runtime(db: Session) -> DhcpRuntime:
    row = db.get(DhcpRuntime, DHCP_RUNTIME_ID)
    if row is not None:
        return row
    settings = get_settings()
    row = DhcpRuntime(
        id=DHCP_RUNTIME_ID,
        enabled=settings.enable_dhcp,
        tftp_enabled=settings.enable_tftp,
        mode=settings.dhcp_mode if settings.dhcp_mode in {"proxy", "authoritative"} else "proxy",
        bind_interface=settings.bind_interface,
        dhcp_range=settings.dhcp_range,
        dhcp_router=settings.dhcp_router,
        dhcp_dns=settings.dhcp_dns,
        extra_options="",
        imaging_timeout_minutes=default_imaging_timeout_minutes(),
        default_timezone=env_default_timezone(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    write_runtime_files(row, request_apply=False)
    return row


def spec_from_runtime(row: DhcpRuntime) -> DnsmasqSpec:
    return DnsmasqSpec(
        public_url=get_settings().public_url,
        dhcp_mode=row.mode,
        bind_interface=row.bind_interface,
        dhcp_range=row.dhcp_range,
        dhcp_router=row.dhcp_router,
        dhcp_dns=row.dhcp_dns,
        extra_options=tuple(parse_extra_options(row.extra_options)),
        dhcp_enabled=bool(row.enabled),
        tftp_enabled=True if row.tftp_enabled is None else bool(row.tftp_enabled),
    )


def write_runtime_files(row: DhcpRuntime, *, request_apply: bool) -> None:
    from .tftp_store import write_boot_chain_script

    settings = get_settings()
    render_dnsmasq_conf(spec_from_runtime(row), tftp_root=settings.tftp_root, conf_path=conf_path())
    enabled_path().write_text("1\n" if row.enabled else "0\n", encoding="utf-8")
    tftp_on = True if row.tftp_enabled is None else bool(row.tftp_enabled)
    tftp_enabled_path().write_text("1\n" if tftp_on else "0\n", encoding="utf-8")
    write_boot_chain_script()
    if request_apply:
        cmd_path().write_text("apply\n", encoding="utf-8")


def _commit_runtime(db: Session, row: DhcpRuntime, *, actor: str, action: str, detail: str) -> DhcpRuntime:
    from .inventory.service import record_activity

    row.updated_at = utcnow()
    db.add(row)
    record_activity(db, actor=actor, action=action, detail=detail)
    db.commit()
    db.refresh(row)
    write_runtime_files(row, request_apply=True)
    return row


def save_pxe(db: Session, *, bind_interface: str, extra_options: str, actor: str) -> DhcpRuntime:
    row = get_or_create_runtime(db)
    parsed = validate_fields(
        mode=row.mode,
        bind_interface=bind_interface,
        dhcp_range=row.dhcp_range,
        dhcp_router=row.dhcp_router,
        dhcp_dns=row.dhcp_dns,
        extra_options=extra_options,
    )
    row.bind_interface = parsed["bind_interface"]
    row.extra_options = parsed["extra_options"]
    return _commit_runtime(db, row, actor=actor, action="pxe.save", detail=f"iface={row.bind_interface}")


def save_dhcp(
    db: Session,
    *,
    enabled: bool,
    mode: str,
    dhcp_range: str,
    dhcp_router: str,
    dhcp_dns: str,
    actor: str,
) -> DhcpRuntime:
    row = get_or_create_runtime(db)
    parsed = validate_fields(
        mode=mode,
        bind_interface=row.bind_interface,
        dhcp_range=dhcp_range,
        dhcp_router=dhcp_router,
        dhcp_dns=dhcp_dns,
        extra_options=row.extra_options,
    )
    row.enabled = enabled
    row.mode = parsed["mode"]
    row.dhcp_range = parsed["dhcp_range"]
    row.dhcp_router = parsed["dhcp_router"]
    row.dhcp_dns = parsed["dhcp_dns"]
    return _commit_runtime(db, row, actor=actor, action="dhcp.save", detail=f"enabled={int(enabled)} mode={row.mode}")


def save_tftp(db: Session, *, tftp_enabled: bool, actor: str) -> DhcpRuntime:
    row = get_or_create_runtime(db)
    row.tftp_enabled = tftp_enabled
    return _commit_runtime(db, row, actor=actor, action="tftp.save", detail=f"enabled={int(tftp_enabled)}")


def save_imaging_timeout(db: Session, *, minutes: int, actor: str, timezone: str | None = None) -> DhcpRuntime:
    from .inventory.service import record_activity

    if minutes < 0 or minutes > MAX_IMAGING_TIMEOUT_MINUTES:
        raise DhcpConfigError(
            f"Imaging timeout must be between 0 and {MAX_IMAGING_TIMEOUT_MINUTES} minutes (0 disables the timer)"
        )
    row = get_or_create_runtime(db)
    row.imaging_timeout_minutes = minutes
    if timezone is not None:
        tz = timezone.strip() or DEFAULT_TIMEZONE
        if not is_valid_timezone(tz):
            raise DhcpConfigError("Choose a valid IANA timezone")
        row.default_timezone = tz
    row.updated_at = utcnow()
    db.add(row)
    detail = f"minutes={minutes}"
    if timezone is not None:
        detail = f"{detail} timezone={row.default_timezone}"
    record_activity(db, actor=actor, action="machines.settings", detail=detail)
    db.commit()
    db.refresh(row)
    return row


def load_runtime(db: Session) -> DhcpRuntime:
    return get_or_create_runtime(db)


def seed_dhcp_runtime() -> None:
    from .db import session_scope

    with session_scope() as db:
        if db.exec(select(DhcpRuntime)).first() is None:
            get_or_create_runtime(db)
        else:
            write_runtime_files(load_runtime(db), request_apply=False)
