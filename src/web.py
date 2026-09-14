"""Jinja2 helpers and HTML error pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER

from .security import allow_insecure_defaults
from .settings import get_settings
from .version import get_app_version

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def client_ip(request: Request) -> str:
    client = getattr(request, "client", None)
    return client.host if client else ""


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
    }
    ctx.update(extra)
    return ctx


def render(request: Request, name: str, **extra) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name=name, context=base_context(request, **extra))
