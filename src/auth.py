"""Signed session cookies for the admin console."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session, select

from .db import get_db
from .security import allow_insecure_defaults

_INSECURE_SECRET_DEFAULTS = frozenset(
    {
        "",
        "please-change-this-secret",
        "change-me-before-production",
        "change-me-to-a-long-random-string",
    }
)

SESSION_IDLE_TIMEOUT_SECONDS = 900
_serializer: URLSafeTimedSerializer | None = None


def _require_secret_key() -> str:
    candidate = (os.getenv("SECRET_KEY") or "").strip()
    if candidate in _INSECURE_SECRET_DEFAULTS:
        if allow_insecure_defaults():
            return "test-secret-key-for-pytest-only"
        raise RuntimeError("SECRET_KEY must be set to a non-default random value before starting the application.")
    return candidate


def get_serializer() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(_require_secret_key(), salt="session-cookie")
    return _serializer


def reset_serializer_for_tests() -> None:
    global _serializer
    _serializer = None


def _env_flag(name: str) -> bool | None:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    return None


def session_cookie_secure(request: Request | None = None) -> bool:
    forced = _env_flag("SESSION_COOKIE_SECURE")
    if forced is not None:
        return forced
    if request is not None and request.url.scheme == "https":
        return True
    return False


def session_cookie_settings(*, secure: bool = False) -> dict:
    return {
        "path": "/",
        "httponly": True,
        "max_age": SESSION_IDLE_TIMEOUT_SECONDS,
        "samesite": "lax",
        "secure": secure,
    }


def create_session_cookie(username: str, session_version: int = 0) -> str:
    return get_serializer().dumps({"u": username, "v": int(session_version)})


def verify_session_cookie(token: str) -> tuple[str, int]:
    data: str | dict = get_serializer().loads(token, max_age=SESSION_IDLE_TIMEOUT_SECONDS)
    if isinstance(data, dict):
        username = str(data.get("u") or "").strip()
        if not username:
            raise BadSignature("session cookie missing username")
        try:
            version = int(data.get("v") or 0)
        except (TypeError, ValueError) as exc:
            raise BadSignature("session cookie has invalid version") from exc
        return username, version
    raise BadSignature("session cookie has unexpected payload")


def load_current_user(request: Request, db: Session) -> str:
    session_token = request.cookies.get("session")
    if not session_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        username, cookie_version = verify_session_cookie(session_token)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    from .models import User

    user = db.exec(select(User).where(User.username == username)).first()
    if user is None or user.disabled:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    db_version = int(getattr(user, "session_version", 0) or 0)
    if cookie_version != db_version:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    request.state.session_user = username
    return username


def require_user(request: Request, db: Session = Depends(get_db)) -> str:
    return load_current_user(request, db)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
