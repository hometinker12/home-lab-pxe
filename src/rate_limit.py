"""In-memory login rate limiting (single uvicorn worker)."""

from __future__ import annotations

import os
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

_LOCK = threading.Lock()
_BUCKETS: dict[tuple[str, str, int], int] = {}
_LOGIN_MAX = 20
_LOGIN_WINDOW = 60


def rate_limit_exceeded(request: Request) -> bool:
    if os.getenv("PXE_DISABLE_RATE_LIMIT", "").strip().lower() in {"1", "true", "yes"}:
        return False
    path = request.url.path or ""
    if path != "/login" or request.method != "POST":
        return False
    client = getattr(request, "client", None)
    host = client.host if client else "unknown"
    now = int(time.time())
    window_start = now - (now % _LOGIN_WINDOW)
    key = (path, host, window_start)
    with _LOCK:
        expired = [k for k in _BUCKETS if k[2] < window_start]
        for old in expired:
            _BUCKETS.pop(old, None)
        count = _BUCKETS.get(key, 0) + 1
        _BUCKETS[key] = count
        return count > _LOGIN_MAX


def rate_limit_rejection_response(request: Request):
    message = "Rate limit exceeded. Try again later."
    headers = {"Retry-After": "60"}
    if "application/json" in (request.headers.get("accept") or ""):
        return JSONResponse(
            status_code=429,
            content={"detail": {"error": "rate_limited", "message": message}},
            headers=headers,
        )
    return PlainTextResponse(message, status_code=429, headers=headers)
