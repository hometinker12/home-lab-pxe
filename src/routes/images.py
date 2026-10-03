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
from ..image_store import (
    UploadError,
    file_size,
    has_upload,
    relative_slot_path,
    remove_iso_tree,
    remove_template_files,
    save_upload_file,
)
from ..install_sources import catalog_from_json, refresh_image_sources, refresh_iso_sources, sanitize_source_id
from ..inventory.boot_menu import default_folder_ids, folder_options, folder_path, get_folder
from ..inventory.isos import (
    create_iso,
    delete_iso,
    get_iso,
    iso_extract_in_progress,
    iso_templates,
    linux_boot_mode,
    list_isos,
    sync_templates,
    template_counts,
    update_iso,
)
from ..inventory.service import (
    create_image,
    delete_image,
    get_image,
    image_extract_in_progress,
    list_images,
    update_image,
)
from ..models import Image, Iso, OsFamily
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
_MEDIA_SLOTS = (
    ("kernel", "kernel_file", "kernel_path"),
    ("initrd", "initrd_file", "initrd_path"),
    ("boot.wim", "boot_wim_file", "boot_wim_path"),
    ("install.wim", "install_wim_file", "install_wim_path"),
    ("iso", "iso_file", "iso_path"),
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


def _apply_uploads(owner_id: int, form, *, base: str = "uploads") -> dict[str, str]:
    paths = {key: _relative_path(_form_str(form, key)) for _slot, _field, key in _MEDIA_SLOTS}
    for slot, field, key in _MEDIA_SLOTS:
        upload = _form_file(form, field)
        if has_upload(upload):
            assert upload is not None
            relative = relative_slot_path(owner_id, slot, upload.filename or slot, base=base)
            paths[key] = save_upload_file(upload, relative)
    return paths


def _iso_from_form(db: Session, form) -> Iso | None:
    raw = _form_str(form, "iso_id").strip()
    if not raw or raw == OsFamily.tool.value:
        return None
    try:
        iso = get_iso(db, int(raw))
    except ValueError as exc:
        raise ValueError("Unknown ISO") from exc
    if iso is None:
        raise ValueError("Unknown ISO")
    return iso


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


_ISO_ADD_KEYS = (
    "name",
    "os_family",
    "arch",
    "iso_path",
    "cmdline",
    "kernel_path",
    "initrd_path",
    "boot_wim_path",
    "install_wim_path",
)
_TEMPLATE_ADD_KEYS = ("name", "iso_id", "folder_id", "arch", "iso_path", "cmdline", "kernel_path", "initrd_path")


def _empty_values(keys: tuple[str, ...]) -> dict[str, str]:
    values = dict.fromkeys(keys, "")
    if "arch" in values:
        values["arch"] = "x86_64"
    if "os_family" in values:
        values["os_family"] = OsFamily.linux.value
    return values


def _values_from_form(form, keys: tuple[str, ...]) -> dict[str, str]:
    values = _empty_values(keys)
    for key in values:
        values[key] = _form_str(form, key, values[key])
    return values


def _owned_paths(image: Image) -> tuple[str, ...]:
    if image.iso_id:
        return ()
    return (
        image.kernel_path or "",
        image.initrd_path or "",
        image.boot_wim_path or "",
        image.install_wim_path or "",
        image.iso_path or "",
    )


def _image_list_context(
    request: Request,
    db: Session,
    *,
    error=None,
    add_open: str = "",
    add: dict | None = None,
    tab: str = "",
):
    images = list_images(db)
    isos = list_isos(db)
    paths = {}
    for img in images:
        folder = get_folder(db, img.folder_id)
        paths[img.id] = folder_path(db, folder) if folder else "—"
    iso_add = add if add_open == "iso" and add else _empty_values(_ISO_ADD_KEYS)
    template_add = add if add_open == "template" and add else _empty_values(_TEMPLATE_ADD_KEYS)
    return render(
        request,
        "images.html",
        images=images,
        isos=isos,
        iso_names={iso.id: iso.name for iso in isos},
        template_counts=template_counts(db),
        folder_paths=paths,
        folder_options=folder_options(db),
        folder_defaults=default_folder_ids(db),
        error=error,
        add_open=add_open,
        active_tab="isos" if tab == "isos" or add_open == "iso" else "templates",
        iso_add=iso_add,
        template_add=template_add,
    )


def _image_source_options(db: Session, image: Image) -> list:
    if linux_boot_mode(image):
        return []
    options = catalog_from_json(image.source_options or "")
    if options:
        return options
    iso = get_iso(db, image.iso_id)
    if iso is not None:
        if refresh_iso_sources(iso):
            db.add(iso)
            sync_templates(db, iso)
            db.commit()
            db.refresh(image)
        return catalog_from_json(image.source_options or "")
    if refresh_image_sources(image):
        db.add(image)
        db.commit()
        db.refresh(image)
        return catalog_from_json(image.source_options or "")
    return []


def _image_detail_context(request: Request, db: Session, image: Image, *, error=None):
    family = OsFamily(image.os_family) if image.os_family in {e.value for e in OsFamily} else OsFamily.linux
    seed = "" if family == OsFamily.tool else read_image_seed(int(image.id), family)
    cc_view = None
    cc_schema: list = []
    if family == OsFamily.linux:
        cc_view = safe_cloud_config_view(seed)
        cc_schema = NODES
    iso = get_iso(db, image.iso_id)
    iso_choices = [row for row in list_isos(db) if row.os_family == family.value] if family != OsFamily.tool else []
    return render(
        request,
        "image_detail.html",
        image=image,
        family=family.value,
        iso=iso,
        iso_choices=iso_choices,
        seed_text=seed,
        seed_field="unattend_xml" if family == OsFamily.windows else "user_data",
        placeholder_help=_PLACEHOLDER_HELP,
        folder_options=folder_options(db),
        folder_defaults=default_folder_ids(db),
        source_options=_image_source_options(db, image),
        boot_mode=linux_boot_mode(image),
        extract_busy=image_extract_in_progress(image),
        error=error,
        cc_view=cc_view,
        cc_schema=cc_schema,
    )


def _iso_detail_context(request: Request, db: Session, iso: Iso, *, error=None):
    return render(
        request,
        "iso_detail.html",
        iso=iso,
        templates_using=iso_templates(db, iso.id),
        boot_mode=linux_boot_mode(iso),
        extract_busy=iso_extract_in_progress(iso),
        error=error,
    )


def _iso_api_row(iso: Iso, counts: dict[int, int]) -> dict:
    return {
        "id": iso.id,
        "name": iso.name,
        "os_family": iso.os_family,
        "arch": iso.arch,
        "iso_path": iso.iso_path,
        "size_bytes": int(iso.size_bytes or 0),
        "cmdline": iso.cmdline,
        "kernel_path": iso.kernel_path,
        "initrd_path": iso.initrd_path,
        "boot_wim_path": iso.boot_wim_path,
        "install_wim_path": iso.install_wim_path,
        "extract_status": iso.extract_status or "idle",
        "extract_error": iso.extract_error or "",
        "source_options": [item.as_dict() for item in catalog_from_json(iso.source_options or "")],
        "template_count": counts.get(int(iso.id or 0), 0),
    }


@router.get("/api/isos")
def api_isos(db: Session = Depends(get_db), user: str = Depends(require_user)):
    counts = template_counts(db)
    return [_iso_api_row(iso, counts) for iso in list_isos(db)]


@router.get("/api/images")
def api_images(db: Session = Depends(get_db), user: str = Depends(require_user)):
    isos = {iso.id: iso for iso in list_isos(db)}
    rows = []
    for img in list_images(db):
        iso = isos.get(img.iso_id)
        rows.append(
            {
                "id": img.id,
                "name": img.name,
                "os_family": img.os_family,
                "arch": img.arch,
                "iso_id": img.iso_id,
                "iso_name": iso.name if iso else "",
                "iso_extract_status": (iso.extract_status or "idle") if iso else "",
                "kernel_path": img.kernel_path,
                "initrd_path": img.initrd_path,
                "boot_wim_path": img.boot_wim_path,
                "install_wim_path": img.install_wim_path,
                "iso_path": img.iso_path,
                "cmdline": img.cmdline,
                "extract_status": img.extract_status or "idle",
                "extract_error": img.extract_error or "",
                "wim_index": img.wim_index or 1,
                "source_id": img.source_id or "",
                "source_options": [item.as_dict() for item in catalog_from_json(img.source_options or "")],
                "folder_id": img.folder_id,
            }
        )
    return rows


@router.get("/images", response_class=HTMLResponse)
def images_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    return _image_list_context(request, db, tab=request.query_params.get("tab", ""))


@router.post("/images/isos")
async def isos_create(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    form = await request.form()
    try:
        family = _family(_form_str(form, "os_family"))
        iso = create_iso(
            db,
            name=_form_str(form, "name"),
            os_family=family,
            arch=_form_str(form, "arch", "x86_64"),
            actor=user,
        )
        paths = _apply_uploads(int(iso.id), form, base="isos")
        update_iso(
            db,
            iso,
            name=iso.name,
            arch=iso.arch,
            cmdline=_form_str(form, "cmdline"),
            size_bytes=file_size(paths["iso_path"]),
            actor=user,
            **paths,
        )
        schedule_extract(db, iso)
        db.commit()
    except (ValueError, UploadError) as exc:
        db.rollback()
        return _image_list_context(
            request, db, error=str(exc), add_open="iso", add=_values_from_form(form, _ISO_ADD_KEYS)
        )
    if has_upload(_form_file(form, "iso_file")):
        return RedirectResponse(url="/images?tab=isos", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse(url=f"/images/isos/{iso.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/images/isos/{iso_id}", response_class=HTMLResponse)
def iso_detail(request: Request, iso_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    iso = get_iso(db, iso_id)
    if iso is None:
        raise HTTPException(status_code=404, detail="unknown iso")
    return _iso_detail_context(request, db, iso)


@router.post("/images/isos/{iso_id}")
async def isos_update(request: Request, iso_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    iso = get_iso(db, iso_id)
    if iso is None:
        raise HTTPException(status_code=404, detail="unknown iso")
    if iso_extract_in_progress(iso):
        return _iso_detail_context(request, db, iso, error="Wait until extraction finishes before editing this ISO")
    form = await request.form()
    try:
        previous_iso = (iso.iso_path or "").strip()
        paths = _apply_uploads(int(iso.id), form, base="isos")
        new_file = has_upload(_form_file(form, "iso_file")) or paths["iso_path"] != previous_iso
        update_iso(
            db,
            iso,
            name=_form_str(form, "name"),
            arch=_form_str(form, "arch", "x86_64"),
            cmdline=_form_str(form, "cmdline"),
            size_bytes=file_size(paths["iso_path"]) if new_file else None,
            actor=user,
            **paths,
        )
        if new_file:
            schedule_extract(db, iso)
        db.commit()
    except (ValueError, UploadError) as exc:
        db.rollback()
        return _iso_detail_context(request, db, iso, error=str(exc))
    if has_upload(_form_file(form, "iso_file")):
        return RedirectResponse(url="/images?tab=isos", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse(url=f"/images/isos/{iso_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/isos/{iso_id}/extract")
def iso_retry_extract(request: Request, iso_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    iso = get_iso(db, iso_id)
    if iso is None:
        raise HTTPException(status_code=404, detail="unknown iso")
    if iso_extract_in_progress(iso):
        return _iso_detail_context(request, db, iso, error="Extraction is already running")
    if not schedule_extract(db, iso):
        db.rollback()
        return _iso_detail_context(request, db, iso, error="ISO file is not on disk")
    db.commit()
    return RedirectResponse(url=f"/images/isos/{iso_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/isos/{iso_id}/delete")
def iso_delete(request: Request, iso_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    iso = get_iso(db, iso_id)
    if iso is None:
        raise HTTPException(status_code=404, detail="unknown iso")
    legacy = (iso.iso_path or "", iso.kernel_path or "", iso.initrd_path or "")
    try:
        delete_iso(db, iso, actor=user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _image_list_context(request, db, error=str(exc), tab="isos")
    remove_iso_tree(iso_id, legacy)
    return RedirectResponse(url="/images?tab=isos", status_code=HTTP_303_SEE_OTHER)


@router.get("/images/{image_id}", response_class=HTMLResponse)
def image_detail(request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    return _image_detail_context(request, db, image)


@router.post("/images")
async def templates_create(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    form = await request.form()
    try:
        iso = _iso_from_form(db, form)
        if iso is not None:
            image = create_image(
                db,
                name=_form_str(form, "name"),
                os_family=OsFamily(iso.os_family),
                folder_id=_folder_id(form),
                iso=iso,
                actor=user,
            )
        else:
            image = create_image(
                db,
                name=_form_str(form, "name"),
                os_family=OsFamily.tool,
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
                os_family=OsFamily.tool,
                arch=image.arch,
                kernel_path=paths["kernel_path"],
                initrd_path=paths["initrd_path"],
                iso_path=paths["iso_path"],
                cmdline=_form_str(form, "cmdline"),
                folder_id=_folder_id(form),
                actor=user,
            )
        db.commit()
    except (ValueError, UploadError, SeedError) as exc:
        db.rollback()
        return _image_list_context(
            request, db, error=str(exc), add_open="template", add=_values_from_form(form, _TEMPLATE_ADD_KEYS)
        )
    if has_upload(_form_file(form, "iso_file")):
        return RedirectResponse(url="/images", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse(url=f"/images/{image.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}")
async def templates_update(
    request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)
):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    form = await request.form()
    try:
        family = OsFamily(image.os_family) if image.os_family in {e.value for e in OsFamily} else OsFamily.linux
        iso = _iso_from_form(db, form) if family != OsFamily.tool else None
        if iso is not None and iso.os_family != family.value:
            raise ValueError("A template can only use an ISO of the same OS")
        if iso is not None or image.iso_id:
            update_image(
                db,
                image,
                name=_form_str(form, "name"),
                os_family=family,
                wim_index=_wim_index(form) if _form_str(form, "wim_index") else None,
                source_id=_source_id(form),
                folder_id=_folder_id(form),
                iso=iso,
                actor=user,
            )
        else:
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
    owned = _owned_paths(image)
    try:
        deleted_id = delete_image(db, image, actor=user)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _image_list_context(request, db, error=str(exc))
    remove_template_files(deleted_id, owned)
    return RedirectResponse(url="/images", status_code=HTTP_303_SEE_OTHER)


@router.post("/images/{image_id}/extract")
def image_retry_extract(
    request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)
):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    if not image.iso_id:
        return _image_detail_context(request, db, image, error="Only templates linked to an ISO can be extracted")
    return iso_retry_extract(request, int(image.iso_id), db=db, user=user)


@router.post("/images/{image_id}/seed/reset")
def image_reset_seed(request: Request, image_id: int, db: Session = Depends(get_db), user: str = Depends(require_user)):
    image = get_image(db, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="unknown image")
    if image.os_family == OsFamily.tool.value:
        return _image_detail_context(request, db, image, error="Tool images do not have guest-init seeds")
    try:
        reset_image_seed(int(image.id), image.os_family)
    except SeedError as exc:
        return _image_detail_context(request, db, image, error=str(exc))
    return RedirectResponse(url=f"/images/{image_id}", status_code=HTTP_303_SEE_OTHER)
