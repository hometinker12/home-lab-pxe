from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from ..auth import require_user
from ..db import get_db
from ..inventory.service import list_activity
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    return render(request, "activity.html", entries=list_activity(db))
