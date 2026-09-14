from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.datastructures import UploadFile
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..image_store import UploadError, has_upload, relative_slot_path, save_upload_file
from ..inventory.service import create_image, get_image, list_images, update_image
from ..models import OsFamily
from ..paths import UnsafePathError, resolve_under
from ..settings import get_settings
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


def _family(os_family: str) -> OsFamily:
    return OsFamily.windows if os_family.strip().lower() == OsFamily.windows.value else OsFamily.linux


def _form_str(form, name: str, default: str = "") -> str:
    value = form.get(name)
    if value is None or isinstance(value, UploadFile):
        return default
    return str(value)


def _form_file(form, name: str) -> UploadFile | None:
    value = form.get(name)
    if isinstance(value, UploadFile):
        return value
    return None


def _relative_path(value: str) -> str:
    text = (value or "").strip().replace("\\", "/")
    if not text:
        return ""
    try:
        resolve_under(get_settings().image_root, text)
    except UnsafePathError as exc:
        raise ValueError("Image paths must stay under the image volume") from exc
    return text


def _apply_uploads(
    image_id: int,
    form,
) -> dict[str, str]:
    paths = {
        "kernel_path": _relative_path(_form_str(form, "kernel_path")),
        "initrd_path": _relative_path(_form_str(form, "initrd_path")),
        "boot_wim_path": _relative_path(_form_str(form, "boot_wim_path")),
        "install_wim_path": _relative_path(_form_str(form, "install_wim_path")),
        "iso_path": _relative_path(_form_str(form, "iso_path")),
    }
    slots = (
        ("kernel", "kernel_file", "kernel_path"),
        ("initrd", "initrd_file", "initrd_path"),
        ("boot.wim", "boot_wim_file", "boot_wim_path"),
        ("install.wim", "install_wim_file", "install_wim_path"),
        ("iso", "iso_file", "iso_path"),
    )
    for slot, field, key in slots:
        upload = _form_file(form, field)
        if has_upload(upload):
            assert upload is not None
            relative = relative_slot_path(image_id, slot, upload.filename or slot)
            paths[key] = save_upload_file(upload, relative)
    return paths


@router.get("/api/images")
def api_images(db: Session = Depends(get_db), user: str = Depends(require_user)):
    return [
        {
            "id": img.id,
            "name": img.name,
            "os_family": img.os_family,
            "arch": img.arch,
            "kernel_path": img.kernel_path,
            "initrd_path": img.initrd_path,
            "boot_wim_path": img.boot_wim_path,
            "install_wim_path": img.install_wim_path,
            "iso_path": img.iso_path,
        }
        for img in list_images(db)
    ]


@router.get("/images", response_class=HTMLResponse)
def images_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    return render(request, "images.html", images=list_images(db), error=None)


@router.get("/images/{image_id}", response_class=HTMLResponse)
def image_detail(request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    return render(request, "image_detail.html", image=image, error=None)


@router.post("/images")
async def images_create(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    form = await request.form()
    try:
        image = create_image(
            db,
            name=_form_str(form, "name"),
            os_family=_family(_form_str(form, "os_family")),
            arch=_form_str(form, "arch", "x86_64"),
            actor=user,
        )
        db.flush()
        paths = _apply_uploads(int(image.id), form)
        update_image(
            db,
            image,
            name=image.name,
            os_family=_family(image.os_family),
            arch=image.arch,
            kernel_path=paths["kernel_path"],
            initrd_path=paths["initrd_path"],
            boot_wim_path=paths["boot_wim_path"],
            install_wim_path=paths["install_wim_path"],
            iso_path=paths["iso_path"],
            cmdline=_form_str(form, "cmdline"),
            actor=user,
        )
        db.commit()
    except (ValueError, UploadError) as exc:
        db.rollback()
        return render(request, "images.html", images=list_images(db), error=str(exc))
    return RedirectResponse(url="/images", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}")
async def images_update(
    request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)
):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    form = await request.form()
    try:
        paths = _apply_uploads(int(image.id), form)
        update_image(
            db,
            image,
            name=_form_str(form, "name"),
            os_family=_family(_form_str(form, "os_family")),
            arch=_form_str(form, "arch", "x86_64"),
            kernel_path=paths["kernel_path"],
            initrd_path=paths["initrd_path"],
            boot_wim_path=paths["boot_wim_path"],
            install_wim_path=paths["install_wim_path"],
            iso_path=paths["iso_path"],
            cmdline=_form_str(form, "cmdline"),
            actor=user,
        )
        db.commit()
    except (ValueError, UploadError) as exc:
        db.rollback()
        return render(request, "image_detail.html", image=image, error=str(exc))
    return RedirectResponse(url=f"/images/{image_id}", status_code=HTTP_303_SEE_OTHER)
