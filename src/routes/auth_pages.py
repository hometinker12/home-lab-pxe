from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import create_session_cookie, session_cookie_secure, session_cookie_settings
from ..db import session_scope
from ..inventory.service import record_activity
from ..models import User
from ..security import DUMMY_PASSWORD_HASH, verify_password
from ..web import render

router = APIRouter(tags=["auth"], include_in_schema=False)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    session_version = 0
    with session_scope() as db:
        user = db.exec(select(User).where(User.username == username)).first()
        password_hash = user.hashed_password if user is not None and not user.disabled else DUMMY_PASSWORD_HASH
        password_ok = verify_password(password, password_hash)
        if user is None or user.disabled or not password_ok:
            record_activity(db, actor=username, action="auth.login_failed")
            db.commit()
            return render(request, "login.html", error="Invalid credentials.")
        session_version = int(user.session_version or 0)
        record_activity(db, actor=username, action="auth.login_succeeded")
        db.commit()
    response = RedirectResponse(url="/machines", status_code=HTTP_303_SEE_OTHER)
    response.set_cookie(
        "session",
        create_session_cookie(username, session_version),
        **session_cookie_settings(secure=session_cookie_secure(request)),
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
    response.delete_cookie("session", path="/")
    return response
