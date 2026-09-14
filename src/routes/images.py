from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..inventory.service import create_image, list_images
from ..models import OsFamily
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


@router.get("/api/images")
def api_images(db: Session = Depends(get_db), user: str = Depends(require_user)):
    return [{"id": img.id, "name": img.name, "os_family": img.os_family, "arch": img.arch} for img in list_images(db)]


@router.get("/images", response_class=HTMLResponse)
def images_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    return render(request, "images.html", images=list_images(db), error=None)


@router.post("/images")
def images_create(
    name: str = Form(...),
    os_family: str = Form(...),
    arch: str = Form("x86_64"),
    kernel_path: str = Form(""),
    initrd_path: str = Form(""),
    boot_wim_path: str = Form(""),
    install_wim_path: str = Form(""),
    cmdline: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    family = OsFamily.windows if os_family.strip().lower() == OsFamily.windows.value else OsFamily.linux
    create_image(
        db,
        name=name,
        os_family=family,
        arch=arch,
        kernel_path=kernel_path,
        initrd_path=initrd_path,
        boot_wim_path=boot_wim_path,
        install_wim_path=install_wim_path,
        cmdline=cmdline,
        actor=user,
    )
    db.commit()
    return RedirectResponse(url="/images", status_code=HTTP_303_SEE_OTHER)
