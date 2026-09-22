"""SQLite engine, sessions, and idempotent bootstrap."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

from sqlalchemy import text
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
        if url.startswith("sqlite"):
            from sqlalchemy import event

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA busy_timeout=5000")
                if ":memory:" not in url:
                    cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

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
    _migrate_schema()
    _seed_admin()
    from .dhcp_runtime import seed_dhcp_runtime
    from .inventory.boot_menu import seed_boot_menu

    seed_dhcp_runtime()
    seed_boot_menu()


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def _dedupe_machine_uuids(conn) -> None:
    rows = conn.execute(
        text("SELECT uuid FROM machine WHERE uuid IS NOT NULL AND uuid != '' GROUP BY uuid HAVING COUNT(*) > 1")
    ).fetchall()
    for (uuid,) in rows:
        ids = conn.execute(
            text("SELECT id FROM machine WHERE uuid = :uuid ORDER BY id"),
            {"uuid": uuid},
        ).fetchall()
        for (machine_id,) in ids[1:]:
            conn.execute(text("UPDATE machine SET uuid = NULL WHERE id = :id"), {"id": machine_id})


def _dedupe_local_accounts(conn) -> None:
    rows = conn.execute(
        text("SELECT machine_id, kind FROM localaccount GROUP BY machine_id, kind HAVING COUNT(*) > 1")
    ).fetchall()
    for machine_id, kind in rows:
        ids = conn.execute(
            text("SELECT id FROM localaccount WHERE machine_id = :machine_id AND kind = :kind ORDER BY id"),
            {"machine_id": machine_id, "kind": kind},
        ).fetchall()
        for (row_id,) in ids[1:]:
            conn.execute(text("DELETE FROM localaccount WHERE id = :id"), {"id": row_id})


def _migrate_schema() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "image" in tables:
            _add_column_if_missing(conn, "image", "iso_path", "iso_path VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "image", "extract_status", "extract_status VARCHAR DEFAULT 'idle'")
            _add_column_if_missing(conn, "image", "extract_error", "extract_error VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "image", "extract_revision", "extract_revision INTEGER DEFAULT 0")
            _add_column_if_missing(conn, "image", "extract_generation", "extract_generation VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "image", "wim_index", "wim_index INTEGER DEFAULT 1")
            _add_column_if_missing(conn, "image", "source_id", "source_id VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "image", "source_options", "source_options VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "image", "folder_id", "folder_id INTEGER")
            _add_column_if_missing(conn, "image", "sort_order", "sort_order INTEGER DEFAULT 0")
        if "installattempt" in tables:
            _add_column_if_missing(conn, "installattempt", "source_id", "source_id VARCHAR DEFAULT ''")
        if "dhcpruntime" in tables:
            _add_column_if_missing(conn, "dhcpruntime", "tftp_enabled", "tftp_enabled BOOLEAN DEFAULT 1")
            _add_column_if_missing(
                conn, "dhcpruntime", "imaging_timeout_minutes", "imaging_timeout_minutes INTEGER DEFAULT 60"
            )
            _add_column_if_missing(conn, "dhcpruntime", "default_timezone", "default_timezone VARCHAR DEFAULT 'UTC'")
        if "machine" in tables:
            _add_column_if_missing(conn, "machine", "imaging_started_at", "imaging_started_at DATETIME")
            _add_column_if_missing(conn, "machine", "manufacturer", "manufacturer VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "machine", "product", "product VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "machine", "serial", "serial VARCHAR DEFAULT ''")
            _add_column_if_missing(conn, "machine", "install_log", "install_log VARCHAR DEFAULT ''")
            _dedupe_machine_uuids(conn)
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_machine_uuid ON machine(uuid)"))
        if "localaccount" in tables:
            _dedupe_local_accounts(conn)
            conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS uq_localaccount_machine_kind ON localaccount(machine_id, kind)")
            )


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
