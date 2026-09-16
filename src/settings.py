"""Environment-backed settings for home-lab-pxe."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    return default


_SMB_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_SMB_PASSWORD_RE = re.compile(r"^[A-Za-z0-9._~-]{20,128}$")


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


@dataclass(frozen=True)
class Settings:
    http_bind: str
    http_port: int
    https_port: int
    ssl_dir: Path
    public_url: str
    dhcp_mode: str
    bind_interface: str
    tftp_root: Path
    image_root: Path
    database_url: str
    enable_dhcp: bool
    enable_tftp: bool
    dhcp_optional: bool
    dhcp_range: str
    dhcp_router: str
    dhcp_dns: str
    admin_user: str
    admin_password: str
    openapi_enabled: bool
    debug_errors: bool
    data_dir: Path
    max_upload_bytes: int
    max_seed_bytes: int
    max_extract_bytes: int
    extract_timeout_seconds: int
    seven_z_bin: str
    smb_host: str
    smb_user: str
    smb_password: str


def _database_url() -> str:
    return (os.getenv("PXE_DATABASE_URL") or os.getenv("DATABASE_URL") or "sqlite:///./data/pxe.db").strip()


def _data_dir() -> Path:
    raw = (os.getenv("PXE_DATA_DIR") or "").strip()
    if raw:
        return Path(raw)
    url = _database_url()
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        parent = os.path.dirname(path)
        if parent:
            return Path(parent)
    return Path("./data")


def _seven_z_bin() -> str:
    configured = (os.getenv("PXE_7Z_BIN") or "").strip()
    if configured:
        return configured
    for name in ("7z", "7zz", "7za"):
        if shutil.which(name):
            return name
    return "7z"


def _host_from_public_url(public: str) -> str:
    parsed = urlparse(public)
    host = (parsed.hostname or "").strip()
    return host or "127.0.0.1"


def validate_smb_user(value: str) -> str:
    text = (value or "").strip()
    if not _SMB_USER_RE.match(text):
        raise ValueError("PXE_SMB_USER must be 1-32 letters, digits, dot, underscore, or hyphen")
    return text


def validate_smb_password(value: str) -> str:
    if not _SMB_PASSWORD_RE.match(value or ""):
        raise ValueError("PXE_SMB_PASSWORD must be 20-128 URL-safe characters (A-Z a-z 0-9 . _ - ~)")
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    public = (os.getenv("PXE_PUBLIC_URL") or "http://127.0.0.1:8080").rstrip("/")
    smb_user = (os.getenv("PXE_SMB_USER") or "pxemedia").strip() or "pxemedia"
    if not _SMB_USER_RE.match(smb_user):
        smb_user = "pxemedia"
    smb_password = os.getenv("PXE_SMB_PASSWORD") or ""
    if smb_password and not _SMB_PASSWORD_RE.match(smb_password):
        smb_password = ""
    smb_host = (os.getenv("PXE_SMB_HOST") or "").strip() or _host_from_public_url(public)
    return Settings(
        http_bind=os.getenv("PXE_HTTP_BIND", "0.0.0.0").strip() or "0.0.0.0",
        http_port=int(os.getenv("PXE_HTTP_PORT") or os.getenv("HTTP_PORT") or "8080"),
        https_port=int(os.getenv("PXE_HTTPS_PORT") or "8443"),
        ssl_dir=Path(os.getenv("PXE_SSL_DIR") or "/var/lib/pxe/ssl"),
        public_url=public,
        dhcp_mode=(os.getenv("PXE_DHCP_MODE") or "proxy").strip().lower() or "proxy",
        bind_interface=(os.getenv("PXE_BIND_INTERFACE") or "eth0").strip() or "eth0",
        tftp_root=Path(os.getenv("PXE_TFTP_ROOT") or "/var/lib/pxe/tftp"),
        image_root=Path(os.getenv("PXE_IMAGE_ROOT") or "/var/lib/pxe/images"),
        database_url=_database_url(),
        enable_dhcp=_flag("PXE_ENABLE_DHCP", default=True),
        enable_tftp=_flag("PXE_ENABLE_TFTP", default=True),
        dhcp_optional=_flag("PXE_DHCP_OPTIONAL", default=False),
        dhcp_range=(os.getenv("PXE_DHCP_RANGE") or "192.168.1.200,192.168.1.250,12h").strip(),
        dhcp_router=(os.getenv("PXE_DHCP_ROUTER") or "").strip(),
        dhcp_dns=(os.getenv("PXE_DHCP_DNS") or "").strip(),
        admin_user=(os.getenv("ADMIN_USER") or "admin").strip() or "admin",
        admin_password=os.getenv("ADMIN_PASSWORD") or "",
        openapi_enabled=_flag("OPENAPI_ENABLED", default=False),
        debug_errors=_flag("DEBUG_ERRORS", default=False),
        data_dir=_data_dir(),
        max_upload_bytes=_int_env("PXE_MAX_UPLOAD_BYTES", 8 * 1024 * 1024 * 1024),
        max_seed_bytes=_int_env("PXE_MAX_SEED_BYTES", 1024 * 1024),
        max_extract_bytes=_int_env("PXE_MAX_EXTRACT_BYTES", 20 * 1024 * 1024 * 1024),
        extract_timeout_seconds=_int_env("PXE_EXTRACT_TIMEOUT_SECONDS", 3600),
        seven_z_bin=_seven_z_bin(),
        smb_host=smb_host,
        smb_user=smb_user,
        smb_password=smb_password,
    )


def smb_password_configured() -> bool:
    return bool(get_settings().smb_password)


def clear_settings_cache() -> None:
    get_settings.cache_clear()
