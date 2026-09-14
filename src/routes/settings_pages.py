from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..inventory.service import LAB_DEFAULT_MACHINE_ID, local_account_status, upsert_local_account
from ..models import AccountKind
from ..settings import get_settings
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    settings = get_settings()
    return render(
        request,
        "settings.html",
        settings=settings,
        linux=local_account_status(db, LAB_DEFAULT_MACHINE_ID, AccountKind.linux_root),
        windows=local_account_status(db, LAB_DEFAULT_MACHINE_ID, AccountKind.windows_administrator),
    )


@router.post("/settings/accounts")
def settings_accounts(
    linux_username: str = Form("root"),
    linux_password: str = Form(""),
    windows_username: str = Form("Administrator"),
    windows_password: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    if linux_password or linux_username:
        try:
            upsert_local_account(
                db,
                machine_id=LAB_DEFAULT_MACHINE_ID,
                kind=AccountKind.linux_root,
                username=linux_username.strip() or "root",
                password=linux_password or None,
            )
        except ValueError:
            pass
    if windows_password or windows_username:
        try:
            upsert_local_account(
                db,
                machine_id=LAB_DEFAULT_MACHINE_ID,
                kind=AccountKind.windows_administrator,
                username=windows_username.strip() or "Administrator",
                password=windows_password or None,
            )
        except ValueError:
            pass
    db.commit()
    return RedirectResponse(url="/settings", status_code=HTTP_303_SEE_OTHER)
