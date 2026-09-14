"""SQLite engine, sessions, and idempotent bootstrap."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from .settings import get_settings

LOGGER = logging.getLogger("home_lab_pxe")

_engine = None
SessionLocal = None


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite:///"):
        return
    raw = url.replace("sqlite:///", "", 1)
    if raw in {":memory:", ""}:
        return
    data_dir = os.path.dirname(raw)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)


def get_engine():
    global _engine, SessionLocal
    if _engine is None:
        url = get_settings().database_url
        _ensure_sqlite_dir(url)
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, echo=False, connect_args=connect_args)
        SessionLocal = sessionmaker(class_=Session, autoflush=False, bind=_engine)
    return _engine


def reset_engine_for_tests() -> None:
    global _engine, SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    SessionLocal = None


def get_db() -> Iterator[Session]:
    get_engine()
    assert SessionLocal is not None
    with SessionLocal() as db:
        yield db


def session_scope() -> Session:
    get_engine()
    assert SessionLocal is not None
    return SessionLocal()


def init_db() -> None:
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(get_engine())
    _seed_admin()


def _seed_admin() -> None:
    from .models import User
    from .security import hash_password

    settings = get_settings()
    get_engine()
    assert SessionLocal is not None
    with SessionLocal() as db:
        existing = db.exec(select(User)).first()
        if existing is not None:
            return
        password = settings.admin_password
        if not password:
            if os.getenv("PXE_ALLOW_INSECURE_DEFAULTS", "").strip().lower() in {"1", "true", "yes"}:
                password = "admin"
            else:
                LOGGER.warning(
                    "No users in database and ADMIN_PASSWORD is unset; console login will fail until a user is created."
                )
                return
        user = User(username=settings.admin_user, hashed_password=hash_password(password))
        db.add(user)
        db.commit()
