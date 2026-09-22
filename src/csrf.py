"""Same-origin CSRF checks for cookie-authenticated form POSTs."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_EXEMPT_PREFIXES = (
    "/static/",
    "/ipxe/",
    "/cloud-init/",
    "/windows/",
    "/cloudbase-init/",
    "/boot-files/",
    "/install-files/",
    "/tftp/",
)
_EXEMPT_PATHS = re.compile(r"^/api/machines/\d+/(events|install-log)/?$")


def relax_csrf_for_tests() -> bool:
    return os.getenv("PXE_RELAX_CSRF", "").strip().lower() in {"1", "true", "yes"}


def _host_matches(url: str, host: str) -> bool:
    if not url or not host:
        return False
    parsed = urlparse(url)
    netloc = (parsed.netloc or "").lower()
    return netloc == host.lower()


def csrf_check_required(request: Request) -> bool:
    if request.method in _SAFE_METHODS:
        return False
    path = request.url.path or ""
    if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
        return False
    if _EXEMPT_PATHS.match(path):
        return False
    if path == "/health":
        return False
    return True


def csrf_origin_allowed(request: Request) -> bool:
    if not csrf_check_required(request):
        return True
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if origin:
        return _host_matches(origin, host)
    referer = request.headers.get("referer")
    if referer:
        return _host_matches(referer, host)
    return relax_csrf_for_tests()


def csrf_rejection_response(request: Request):
    message = "CSRF validation failed"
    if "application/json" in (request.headers.get("accept") or ""):
        return JSONResponse(status_code=403, content={"detail": {"error": "csrf_failed", "message": message}})
    return RedirectResponse(url="/login", status_code=303)
