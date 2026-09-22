"""Jinja2 helpers and HTML error pages."""

from __future__ import annotations

import ipaddress
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER

from .db import session_scope
from .models import state_label
from .netinfo import in_container
from .security import allow_insecure_defaults
from .settings import get_settings
from .settings_attention import empty_settings_attention, load_settings_attention
from .tftp_store import empty_files_attention, load_files_attention
from .version import get_app_version

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["state_label"] = state_label


def client_ip(request: Request) -> str:
    client = getattr(request, "client", None)
    return client.host if client else ""


# docker0 is 172.17/16. Compose and Docker Desktop also use 172.18–172.31.
_DOCKER_BRIDGE_ALWAYS = (
    ipaddress.ip_network("172.17.0.0/16"),
    ipaddress.ip_network("172.18.0.0/16"),
)
_DOCKER_BRIDGE = ipaddress.ip_network("172.16.0.0/12")
_DOCKER_DESKTOP_GW = ipaddress.IPv4Address("192.168.65.254")


def _parse_ip(value: str | None):
    text = (value or "").strip()
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _direct_client(addr) -> bool:
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        return True
    if addr == _DOCKER_DESKTOP_GW or any(addr in net for net in _DOCKER_BRIDGE_ALWAYS):
        return False
    # Published container ports see the bridge gateway, not the PXE client.
    return not (in_container() and addr in _DOCKER_BRIDGE)


def reported_client_ip(peer: str, query: str | None) -> str:
    """Use the TCP peer on a direct LAN connection. Otherwise keep a valid iPXE ${ip}.

    A Docker bridge peer with no ${ip} returns "" so a later boot request does not
    replace a stored LAN address.
    """
    query_ip = _parse_ip(query)
    peer_ip = _parse_ip(peer)
    if peer_ip is not None and _direct_client(peer_ip):
        return str(peer_ip)
    if query_ip is not None:
        return str(query_ip)
    if peer_ip is not None and not _direct_client(peer_ip):
        return ""
    return peer or ""


def wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept and "application/json" not in accept.split(",")[0]


def redirect_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)


def base_context(request: Request, **extra) -> dict:
    user = getattr(request.state, "session_user", None)
    ctx = {
        "request": request,
        "version": get_app_version(),
        "user": user,
        "public_url": get_settings().public_url,
        "insecure_defaults": allow_insecure_defaults(),
        "settings_attention": empty_settings_attention(),
        "files_attention": empty_files_attention(),
    }
    if user:
        with session_scope() as db:
            ctx["settings_attention"] = load_settings_attention(db)
        ctx["files_attention"] = load_files_attention()
    ctx.update(extra)
    return ctx


def render(request: Request, name: str, **extra) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name=name, context=base_context(request, **extra))
