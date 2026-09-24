from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.datastructures import UploadFile
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..cloudinit.editor import apply_cloudinit_editor, safe_cloud_config_view
from ..cloudinit.schema import NODES
from ..db import get_db
from ..extract_worker import schedule_extract
from ..image_store import UploadError, has_upload, relative_slot_path, remove_image_tree, save_upload_file
from ..install_sources import catalog_from_json, refresh_image_sources, sanitize_source_id
from ..inventory.boot_menu import default_folder_ids, folder_options, folder_path, get_folder
from ..inventory.service import (
    create_image,
    delete_image,
    get_image,
    image_extract_in_progress,
    list_images,
    update_image,
)
from ..models import OsFamily
from ..paths import UnsafePathError, resolve_under
from ..seed_render import validate_seed_template
from ..seed_store import SeedError, read_image_seed, reset_image_seed, write_image_seed
from ..settings import get_settings
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)

_PLACEHOLDER_HELP = (
    "{{hostname}} {{username}} {{password}} {{password_hash}} {{instance_id}} {{machine_id}} "
    "{{public_url}} {{phone_home_url}} {{imaging_url}} {{install_log_url}} {{timezone}} {{ssh_keys}} {{packages}} "
    "{{source_id}} {{wim_index}} {{install_media_path}}"
)


def _family(os_family: str) -> OsFamily:
    raw = os_family.strip().lower()
    if raw == OsFamily.windows.value:
        return OsFamily.windows
    if raw == OsFamily.tool.value:
        return OsFamily.tool
    return OsFamily.linux


def _folder_id(form) -> int | None:
    raw = _form_str(form, "folder_id")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("Folder is required") from exc


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


def _source_id(form) -> str:
    return sanitize_source_id(_form_str(form, "source_id"))


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
    if os_family == OsFamily.tool:
        return
    if os_family == OsFamily.linux:
        cc_json = _form_str(form, "cc_json")
        if cc_json.strip():
            existing = read_image_seed(image_id, os_family)
            body = apply_cloudinit_editor(existing or "", cc_json, _form_str(form, "cc_extra_yaml"))
            validate_seed_template(body, os_family)
            write_image_seed(image_id, os_family, body)
            return
    field = "unattend_xml" if os_family == OsFamily.windows else "user_data"
    if field not in form:
        return
    body = _form_str(form, field)
    if not body.strip():
        return
    validate_seed_template(body, os_family)
    write_image_seed(image_id, os_family, body)


def _empty_add_values() -> dict[str, str]:
    return {
        "name": "",
        "os_family": "linux",
        "arch": "x86_64",
        "folder_id": "",
        "boot_wim_path": "",
        "install_wim_path": "",
        "wim_index": "1",
        "iso_path": "",
        "cmdline": "",
        "kernel_path": "",
        "initrd_path": "",
    }


def _add_values_from_form(form) -> dict[str, str]:
    values = _empty_add_values()
    for key in values:
        values[key] = _form_str(form, key, values[key])
    return values


def _image_list_context(request: Request, db: Session, *, error=None, add_open: bool = False, add: dict | None = None):
    images = list_images(db)
    paths = {}
    for img in images:
        folder = get_folder(db, img.folder_id)
        paths[img.id] = folder_path(db, folder) if folder else "—"
    return render(
        request,
        "images.html",
        images=images,
        folder_paths=paths,
        folder_options=folder_options(db),
        folder_defaults=default_folder_ids(db),
        error=error,
        add_open=add_open,
        add=add or _empty_add_values(),
    )


def _image_source_options(db: Session, image) -> list:
    options = catalog_from_json(image.source_options or "")
    if options:
        return options
    if refresh_image_sources(image):
        db.add(image)
        db.commit()
        db.refresh(image)
        return catalog_from_json(image.source_options or "")
    return []


def _image_detail_context(request: Request, db: Session, image, *, error=None):
    family = OsFamily(image.os_family) if image.os_family in {e.value for e in OsFamily} else OsFamily.linux
    seed = "" if family == OsFamily.tool else read_image_seed(int(image.id), family)
    cc_view = None
    cc_schema: list = []
    if family == OsFamily.linux:
        cc_view = safe_cloud_config_view(seed)
        cc_schema = NODES
    return render(
        request,
        "image_detail.html",
        image=image,
        seed_text=seed,
        seed_field="unattend_xml" if family == OsFamily.windows else "user_data",
        placeholder_help=_PLACEHOLDER_HELP,
        folder_options=folder_options(db),
        folder_defaults=default_folder_ids(db),
        source_options=_image_source_options(db, image),
        extract_busy=image_extract_in_progress(image),
        error=error,
        cc_view=cc_view,
        cc_schema=cc_schema,
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
            "source_id": img.source_id or "",
            "source_options": [item.as_dict() for item in catalog_from_json(img.source_options or "")],
            "folder_id": img.folder_id,
        }
        for img in list_images(db)
    ]


@router.get("/images", response_class=HTMLResponse)
def images_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    return _image_list_context(request, db)


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
            folder_id=_folder_id(form),
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
            folder_id=_folder_id(form),
            actor=user,
        )
        schedule_extract(db, image)
        db.commit()
    except (ValueError, UploadError, SeedError) as exc:
        db.rollback()
        return _image_list_context(request, db, error=str(exc), add_open=True, add=_add_values_from_form(form))
    if has_upload(_form_file(form, "iso_file")):
        return RedirectResponse(url="/images", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse(url=f"/images/{image.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}")
async def images_update(
    request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)
):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    if image_extract_in_progress(image):
        return _image_detail_context(
            request, db, image, error="Wait until extraction finishes before editing this image"
        )
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
            source_id=_source_id(form),
            folder_id=_folder_id(form),
            actor=user,
        )
        _save_image_seed(int(image.id), family, form)
        schedule_extract(db, image)
        db.commit()
    except (ValueError, UploadError, SeedError) as exc:
        db.rollback()
        return _image_detail_context(request, db, image, error=str(exc))
    if has_upload(_form_file(form, "iso_file")):
        return RedirectResponse(url="/images", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse(url=f"/images/{image_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}/delete")
def image_delete(request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    try:
        deleted_id = delete_image(db, image, actor=user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _image_list_context(request, db, error=str(exc))
    remove_image_tree(deleted_id)
    return RedirectResponse(url="/images", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}/extract")
def image_retry_extract(
    request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)
):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    if image.os_family == OsFamily.tool.value:
        return _image_detail_context(request, db, image, error="Tool images are not extracted")
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
    if image.os_family == OsFamily.tool.value:
        return _image_detail_context(request, db, image, error="Tool images do not have guest-init seeds")
    if image_extract_in_progress(image):
        return _image_detail_context(
            request, db, image, error="Wait until extraction finishes before editing this image"
        )
    try:
        reset_image_seed(int(image.id), image.os_family)
    except SeedError as exc:
        return _image_detail_context(request, db, image, error=str(exc))
    return RedirectResponse(url=f"/images/{image_id}", status_code=HTTP_303_SEE_OTHER)
