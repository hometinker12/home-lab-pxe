"""Environment-backed settings for home-lab-pxe."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    return default


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


def _max_upload_bytes() -> int:
    raw = (os.getenv("PXE_MAX_UPLOAD_BYTES") or "").strip()
    if not raw:
        return 8 * 1024 * 1024 * 1024
    try:
        value = int(raw)
    except ValueError:
        return 8 * 1024 * 1024 * 1024
    return max(1, value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    public = (os.getenv("PXE_PUBLIC_URL") or "http://127.0.0.1:8080").rstrip("/")
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
        max_upload_bytes=_max_upload_bytes(),
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()
