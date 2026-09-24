from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_db
from ..inventory.mac import InvalidMacError, normalize_mac
from ..inventory.service import find_by_mac, list_activity
from ..models import Image
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)

_PAGE_SIZE = 50
_LABELS = {
    "machine.discovered": "Discovered a machine",
    "machine.register": "Registered a machine",
    "machine.ready": "Marked ready",
    "machine.save": "Saved a machine",
    "machine.deploy": "Started a deploy",
    "machine.imaging": "Imaging started",
    "machine.deployed": "Marked deployed",
    "machine.timeout": "Imaging timed out",
    "machine.install_failed": "Install failed",
    "machine.abort": "Aborted an install",
    "machine.disabled": "Disabled a machine",
    "machine.enabled": "Enabled a machine",
    "machine.delete": "Deleted a machine",
    "machine.seed-copy": "Copied the image seed",
    "machine.uuid_collision": "UUID matched a live machine",
    "auth.login_failed": "Failed login",
    "auth.password": "Changed the console password",
    "smb.rotate": "Rotated the SMB password",
    "image.create": "Added an image",
    "image.delete": "Deleted an image",
    "image.update": "Updated an image",
}


def _href(db: Session, action: str, detail: str) -> str:
    text = (detail or "").strip()
    if text.startswith("image="):
        name = text.split("=", 1)[1].strip()
        image = db.exec(select(Image).where(Image.name == name)).first()
        if image is not None and image.id:
            return f"/images/{image.id}"
    if action.startswith("machine.") or action.startswith("boot"):
        try:
            mac = normalize_mac(text.split()[0] if text else "")
        except (InvalidMacError, IndexError):
            return ""
        machine = find_by_mac(db, mac)
        if machine is not None and machine.id:
            return f"/machines/{machine.id}"
    return ""


@router.get("/activity", response_class=HTMLResponse)
def activity_page(
    request: Request,
    q: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    current = max(1, page)
    rows = list_activity(db, _PAGE_SIZE + 1, offset=(current - 1) * _PAGE_SIZE, prefix=q.strip())
    has_next = len(rows) > _PAGE_SIZE
    entries = []
    for row in rows[:_PAGE_SIZE]:
        action = row.action or ""
        detail = row.detail or ""
        entries.append(
            {
                "at": row.at,
                "actor": row.actor,
                "action": action,
                "label": _LABELS.get(action, action.replace(".", " ").replace("_", " ")),
                "detail": detail,
                "href": _href(db, action, detail),
            }
        )
    return render(
        request,
        "activity.html",
        entries=entries,
        q=q.strip(),
        page=current,
        has_next=has_next,
        has_prev=current > 1,
    )
