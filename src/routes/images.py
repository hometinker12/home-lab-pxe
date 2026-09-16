from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.datastructures import UploadFile
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..extract_worker import schedule_extract
from ..image_store import UploadError, has_upload, relative_slot_path, save_upload_file
from ..inventory.service import create_image, get_image, list_images, update_image
from ..models import OsFamily
from ..paths import UnsafePathError, resolve_under
from ..seed_render import validate_seed_template
from ..seed_store import SeedError, read_image_seed, reset_image_seed, write_image_seed
from ..settings import get_settings
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)

_PLACEHOLDER_HELP = (
    "{{hostname}} {{username}} {{password}} {{password_hash}} {{instance_id}} {{machine_id}} "
    "{{public_url}} {{phone_home_url}} {{timezone}} {{ssh_keys}} {{packages}} {{wim_index}} {{install_media_path}}"
)


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


def _wim_index(form) -> int:
    raw = _form_str(form, "wim_index", "1")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("WIM index must be a positive integer") from exc
    if value < 1:
        raise ValueError("WIM index must be a positive integer")
    return value


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


def _save_image_seed(image_id: int, os_family: OsFamily, form) -> None:
    field = "unattend_xml" if os_family == OsFamily.windows else "user_data"
    if field not in form:
        return
    body = _form_str(form, field)
    if not body.strip():
        return
    validate_seed_template(body, os_family)
    write_image_seed(image_id, os_family, body)


def _image_detail_context(request: Request, db: Session, image, *, error=None):
    family = OsFamily(image.os_family)
    seed = read_image_seed(int(image.id), family)
    return render(
        request,
        "image_detail.html",
        image=image,
        seed_text=seed,
        seed_field="unattend_xml" if family == OsFamily.windows else "user_data",
        placeholder_help=_PLACEHOLDER_HELP,
        error=error,
    )


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
            "extract_status": img.extract_status or "idle",
            "extract_error": img.extract_error or "",
            "wim_index": img.wim_index or 1,
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
    return _image_detail_context(request, db, image)


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
            wim_index=_wim_index(form) if _form_str(form, "wim_index") else 1,
            actor=user,
        )
        schedule_extract(db, image)
        db.commit()
    except (ValueError, UploadError, SeedError) as exc:
        db.rollback()
        return render(request, "images.html", images=list_images(db), error=str(exc))
    return RedirectResponse(url=f"/images/{image.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}")
async def images_update(
    request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)
):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    form = await request.form()
    try:
        family = _family(_form_str(form, "os_family"))
        paths = _apply_uploads(int(image.id), form)
        update_image(
            db,
            image,
            name=_form_str(form, "name"),
            os_family=family,
            arch=_form_str(form, "arch", "x86_64"),
            kernel_path=paths["kernel_path"],
            initrd_path=paths["initrd_path"],
            boot_wim_path=paths["boot_wim_path"],
            install_wim_path=paths["install_wim_path"],
            iso_path=paths["iso_path"],
            cmdline=_form_str(form, "cmdline"),
            wim_index=_wim_index(form),
            actor=user,
        )
        _save_image_seed(int(image.id), family, form)
        schedule_extract(db, image)
        db.commit()
    except (ValueError, UploadError, SeedError) as exc:
        db.rollback()
        return _image_detail_context(request, db, image, error=str(exc))
    return RedirectResponse(url=f"/images/{image_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}/extract")
def image_retry_extract(
    request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)
):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    if not schedule_extract(db, image):
        db.rollback()
        return _image_detail_context(request, db, image, error="ISO file is not on disk")
    db.commit()
    return RedirectResponse(url=f"/images/{image_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}/seed/reset")
def image_reset_seed(request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    try:
        reset_image_seed(int(image.id), image.os_family)
    except SeedError as exc:
        return _image_detail_context(request, db, image, error=str(exc))
    return RedirectResponse(url=f"/images/{image_id}", status_code=HTTP_303_SEE_OTHER)
