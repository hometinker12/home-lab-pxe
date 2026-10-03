"""Uploaded install media (ISOs) shared by boot templates."""

from __future__ import annotations

from sqlmodel import Session, col, func, select

from ..install_sources import catalog_from_json, pick_source_id, wim_index_for_source
from ..models import ExtractStatus, Image, Iso, OsFamily
from ..nfs_media import live_boot_generation
from ..settings import get_settings
from .service import EXTRACT_IN_PROGRESS, record_activity

MIRRORED_FIELDS = (
    "arch",
    "iso_path",
    "cmdline",
    "kernel_path",
    "initrd_path",
    "boot_wim_path",
    "install_wim_path",
    "extract_status",
    "extract_error",
    "extract_revision",
    "extract_generation",
    "source_options",
)
ISO_FAMILIES = (OsFamily.linux, OsFamily.windows)


def get_iso(db: Session, iso_id: int | None) -> Iso | None:
    if not iso_id:
        return None
    return db.get(Iso, iso_id)


def find_iso_by_name(db: Session, name: str) -> Iso | None:
    return db.exec(select(Iso).where(Iso.name == name.strip())).first()


def list_isos(db: Session) -> list[Iso]:
    return list(db.exec(select(Iso).order_by(col(Iso.name))).all())


def iso_templates(db: Session, iso_id: int | None) -> list[Image]:
    if not iso_id:
        return []
    return list(db.exec(select(Image).where(Image.iso_id == iso_id).order_by(col(Image.name))).all())


def template_counts(db: Session) -> dict[int, int]:
    rows = db.exec(select(Image.iso_id, func.count()).where(Image.iso_id != None).group_by(Image.iso_id)).all()  # noqa: E711
    return {int(iso_id): int(count) for iso_id, count in rows}


def iso_extract_in_progress(iso: Iso) -> bool:
    return (iso.extract_status or ExtractStatus.idle.value) in EXTRACT_IN_PROGRESS


def _next_iso_id(db: Session) -> int:
    """New ids clear every image id too, so nfs/{id} and smb/{id} never collide with legacy media."""
    top_iso = db.exec(select(func.max(Iso.id))).one() or 0
    top_image = db.exec(select(func.max(Image.id))).one() or 0
    return max(int(top_iso), int(top_image)) + 1


def linux_boot_mode(media: Iso | Image) -> str:
    """'sanboot' or 'live-boot' for ready Linux media that runs its own installer; '' for Ubuntu casper."""
    if media.os_family != OsFamily.linux.value or (media.extract_status or "") != ExtractStatus.ready.value:
        return ""
    if not ((media.kernel_path or "").strip() and (media.initrd_path or "").strip()):
        return "sanboot" if (media.iso_path or "").strip() else ""
    if live_boot_generation(media.extract_generation or "", get_settings().image_root):
        return "live-boot"
    return ""


def sync_template(image: Image, iso: Iso) -> None:
    """Copy shared media onto a template; keep its install source when the catalog still offers it."""
    image.iso_id = iso.id
    image.os_family = iso.os_family
    for field in MIRRORED_FIELDS:
        setattr(image, field, getattr(iso, field))
    if linux_boot_mode(iso):
        image.source_id = ""
        return
    options = catalog_from_json(iso.source_options or "")
    if not options:
        return
    chosen = pick_source_id(options, image.source_id or "")
    image.source_id = chosen
    mapped = wim_index_for_source(options, chosen)
    if mapped is not None:
        image.wim_index = int(mapped)


def sync_templates(db: Session, iso: Iso) -> None:
    for image in iso_templates(db, iso.id):
        sync_template(image, iso)
        db.add(image)


def create_iso(db: Session, *, name: str, os_family: OsFamily, arch: str = "x86_64", actor: str) -> Iso:
    trimmed = name.strip()
    if not trimmed:
        raise ValueError("ISO name is required")
    if os_family not in ISO_FAMILIES:
        raise ValueError("ISOs are Linux or Windows install media")
    if find_iso_by_name(db, trimmed) is not None:
        raise ValueError("An ISO with that name already exists")
    iso = Iso(id=_next_iso_id(db), name=trimmed, os_family=os_family.value, arch=arch.strip() or "x86_64")
    db.add(iso)
    db.flush()
    record_activity(db, actor=actor, action="iso.create", detail=iso.name)
    return iso


def update_iso(
    db: Session,
    iso: Iso,
    *,
    name: str,
    arch: str = "x86_64",
    cmdline: str | None = None,
    kernel_path: str | None = None,
    initrd_path: str | None = None,
    boot_wim_path: str | None = None,
    install_wim_path: str | None = None,
    iso_path: str | None = None,
    size_bytes: int | None = None,
    actor: str,
) -> Iso:
    new_name = name.strip()
    if not new_name:
        raise ValueError("ISO name is required")
    clash = find_iso_by_name(db, new_name)
    if clash is not None and clash.id != iso.id:
        raise ValueError("An ISO with that name already exists")
    iso.name = new_name
    iso.arch = arch.strip() or "x86_64"
    for field, value in (
        ("cmdline", cmdline),
        ("kernel_path", kernel_path),
        ("initrd_path", initrd_path),
        ("boot_wim_path", boot_wim_path),
        ("install_wim_path", install_wim_path),
        ("iso_path", iso_path),
    ):
        if value is not None:
            setattr(iso, field, value.strip())
    if size_bytes is not None:
        iso.size_bytes = max(0, int(size_bytes))
    db.add(iso)
    sync_templates(db, iso)
    record_activity(db, actor=actor, action="iso.update", detail=iso.name)
    return iso


def delete_iso(db: Session, iso: Iso, *, actor: str) -> None:
    if iso_extract_in_progress(iso):
        raise ValueError("Wait until extraction finishes before deleting this ISO")
    users = iso_templates(db, iso.id)
    if users:
        names = ", ".join(image.name for image in users[:5])
        raise ValueError(f"Delete the templates that use this ISO first: {names}")
    name = iso.name
    db.delete(iso)
    record_activity(db, actor=actor, action="iso.delete", detail=name)
