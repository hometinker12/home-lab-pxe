"""Dedicated ISO extraction worker. Claim jobs via SQLite, one at a time."""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, col, select

from .db import get_engine, init_db, session_scope
from .ganesha_exports import request_export_reload
from .install_sources import refresh_image_sources, refresh_iso_sources
from .inventory.isos import sync_templates
from .inventory.service import expire_stale_imaging
from .iso_extract import ArchiveRunner, ExtractError, extract_linux_payloads, extract_windows_media
from .models import ExtractStatus, Image, InstallAttempt, Iso, OsFamily
from .nfs_media import casper_has_squashfs, linux_http_generation, nfs_generation
from .paths import UnsafePathError, resolve_under
from .seed_store import ensure_image_seed
from .settings import get_settings

LOGGER = logging.getLogger("home_lab_pxe.extract")
_ABS_PATH = re.compile(r"(?:[A-Za-z]:)?[/\\][^\s:]+")


def sanitize_error(message: str) -> str:
    text = _ABS_PATH.sub("<path>", message or "Extraction failed")
    return text[:300]


def iso_on_disk(media: Iso | Image) -> Path | None:
    relative = (media.iso_path or "").strip()
    if not relative:
        return None
    try:
        path = resolve_under(get_settings().image_root, relative)
    except UnsafePathError:
        return None
    return path if path.is_file() else None


def generated_linux_prefixes(iso_id: int) -> tuple[str, ...]:
    return (f"isos/{int(iso_id)}/extracts/", f"uploads/{int(iso_id)}/extracts/")


def generated_windows_prefix(iso_id: int) -> str:
    return f"smb/{int(iso_id)}/"


def _is_generated(path: str, prefixes: tuple[str, ...]) -> bool:
    text = (path or "").replace("\\", "/")
    return not text or text.startswith(prefixes)


def schedule_extract(db: Session, iso: Iso) -> bool:
    if iso.os_family not in {OsFamily.linux.value, OsFamily.windows.value}:
        return False
    if iso_on_disk(iso) is None:
        return False
    iso.extract_revision = int(iso.extract_revision or 0) + 1
    iso.extract_status = ExtractStatus.queued.value
    iso.extract_error = ""
    db.add(iso)
    sync_templates(db, iso)
    return True


def requeue_stale_extracting() -> None:
    with session_scope() as db:
        rows = db.exec(select(Iso).where(Iso.extract_status == ExtractStatus.extracting.value)).all()
        for iso in rows:
            iso.extract_status = ExtractStatus.queued.value
            db.add(iso)
            sync_templates(db, iso)
        db.commit()


def backfill_image_seeds() -> None:
    with session_scope() as db:
        for image in db.exec(select(Image)).all():
            if image.id is None:
                continue
            try:
                ensure_image_seed(int(image.id), image.os_family)
            except Exception:
                LOGGER.warning("seed backfill failed image_id=%s", image.id)


def claim_next_job() -> tuple[int, int] | None:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, extract_revision FROM iso WHERE extract_status = :queued ORDER BY id ASC LIMIT 1"),
            {"queued": ExtractStatus.queued.value},
        ).fetchone()
        if row is None:
            return None
        iso_id, revision = int(row[0]), int(row[1])
        result = conn.execute(
            text(
                "UPDATE iso SET extract_status = :extracting "
                "WHERE id = :id AND extract_revision = :rev AND extract_status = :queued"
            ),
            {
                "extracting": ExtractStatus.extracting.value,
                "id": iso_id,
                "rev": revision,
                "queued": ExtractStatus.queued.value,
            },
        )
        if result.rowcount != 1:
            return None
        conn.execute(
            text("UPDATE image SET extract_status = :extracting WHERE iso_id = :id"),
            {"extracting": ExtractStatus.extracting.value, "id": iso_id},
        )
        return iso_id, revision


def _relative_under_root(path: Path) -> str:
    root = get_settings().image_root.resolve()
    return path.resolve().relative_to(root).as_posix()


def _replace_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))


def _world_readable(path: Path) -> None:
    if not path.exists():
        return
    targets = [path, *path.rglob("*")] if path.is_dir() else [path]
    for child in targets:
        try:
            child.chmod(0o755 if child.is_dir() else 0o644)
        except OSError:
            continue


def _publish_linux_tree(staging: Path, iso_id: int, revision: int, media_relative: str) -> tuple[Path, str]:
    root = get_settings().image_root
    extracts_pub = root / "isos" / str(iso_id) / "extracts" / str(revision)
    extracts_pub.parent.mkdir(parents=True, exist_ok=True)
    if extracts_pub.exists():
        shutil.rmtree(extracts_pub)
    extracts_pub.mkdir(parents=True)
    kernel = staging / "kernel"
    initrd = staging / "initrd"
    if not kernel.is_file() or not initrd.is_file():
        raise ExtractError("Ubuntu live-server payloads not found (casper/vmlinuz + casper/initrd)")
    shutil.move(str(kernel), str(extracts_pub / "kernel"))
    shutil.move(str(initrd), str(extracts_pub / "initrd"))
    nfs_pub = root / "nfs" / str(iso_id) / str(revision)
    if media_relative == "casper" or casper_has_squashfs(staging):
        if nfs_pub.exists():
            shutil.rmtree(nfs_pub)
        nfs_pub.mkdir(parents=True)
        for child in list(staging.iterdir()):
            shutil.move(str(child), str(nfs_pub / child.name))
        _world_readable(nfs_pub)
        shutil.rmtree(staging, ignore_errors=True)
        return extracts_pub, nfs_generation(iso_id, revision)
    shutil.rmtree(staging, ignore_errors=True)
    return extracts_pub, linux_http_generation(iso_id, revision)


def gc_extract_generations(db: Session, media: Iso | Image) -> None:
    """Drop old extract revisions, keeping the current one and any a running install still pins."""
    if isinstance(media, Image) and media.iso_id:
        linked = db.get(Iso, media.iso_id)
        if linked is not None:
            media = linked
    if media.id is None:
        return
    owner_id = int(media.id)
    if isinstance(media, Iso):
        image_ids = [int(image.id) for image in db.exec(select(Image).where(Image.iso_id == owner_id)).all()]
    else:
        image_ids = [owner_id]
    open_revs: set[int] = set()
    if image_ids:
        open_revs = {
            int(row.extract_revision)
            for row in db.exec(
                select(InstallAttempt).where(
                    col(InstallAttempt.image_id).in_(image_ids),
                    InstallAttempt.completed_at == None,  # noqa: E711
                )
            ).all()
        }
    keep = {int(media.extract_revision or 0), *open_revs}
    root = get_settings().image_root
    for base in (
        root / "isos" / str(owner_id) / "extracts",
        root / "uploads" / str(owner_id) / "extracts",
        root / "smb" / str(owner_id),
        root / "nfs" / str(owner_id),
    ):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            name = child.name
            if name.endswith(".tmp"):
                shutil.rmtree(child, ignore_errors=True)
                continue
            if not name.isdigit():
                continue
            if int(name) in keep:
                continue
            shutil.rmtree(child, ignore_errors=True)


def _mark_failed(db: Session, iso_id: int, revision: int, message: str) -> None:
    iso = db.get(Iso, iso_id)
    if iso is None or int(iso.extract_revision or 0) != revision:
        return
    iso.extract_status = ExtractStatus.failed.value
    iso.extract_error = message
    db.add(iso)
    sync_templates(db, iso)
    db.commit()


def run_one_job(iso_id: int, revision: int, runner: ArchiveRunner | None = None) -> None:
    settings = get_settings()
    root = settings.image_root
    with session_scope() as db:
        iso = db.get(Iso, iso_id)
        if iso is None or int(iso.extract_revision or 0) != revision:
            return
        source = iso_on_disk(iso)
        if source is None:
            iso.extract_status = ExtractStatus.idle.value
            iso.extract_error = ""
            db.add(iso)
            sync_templates(db, iso)
            db.commit()
            return
        family = iso.os_family
        staging = root / "isos" / str(iso_id) / "extracts" / f"{revision}.tmp"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            if family == OsFamily.windows.value:
                result = extract_windows_media(source, staging, image_root=root, runner=runner)
                published = root / "smb" / str(iso_id) / str(revision)
                _replace_dir(staging, published)
                media_rel = f"{iso_id}/{revision}"
                boot_rel = f"smb/{iso_id}/{revision}/{result.boot_wim_relative}"
                install_rel = f"smb/{iso_id}/{revision}/{result.install_wim_relative}"
            else:
                result = extract_linux_payloads(source, staging, image_root=root, runner=runner)
                published, media_rel = _publish_linux_tree(staging, iso_id, revision, result.media_relative)
                boot_rel = ""
                install_rel = ""
        except ExtractError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            _mark_failed(db, iso_id, revision, sanitize_error(str(exc)))
            return
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            _mark_failed(db, iso_id, revision, "Extraction failed")
            LOGGER.exception("extract failed iso_id=%s revision=%s", iso_id, revision)
            return

        iso = db.get(Iso, iso_id)
        if iso is None or int(iso.extract_revision or 0) != revision:
            shutil.rmtree(published, ignore_errors=True)
            if family == OsFamily.linux.value:
                shutil.rmtree(root / "nfs" / str(iso_id) / str(revision), ignore_errors=True)
            return
        if family == OsFamily.linux.value:
            prefixes = generated_linux_prefixes(iso_id)
            if _is_generated(iso.kernel_path, prefixes):
                iso.kernel_path = _relative_under_root(published / "kernel")
            if _is_generated(iso.initrd_path, prefixes):
                iso.initrd_path = _relative_under_root(published / "initrd")
        else:
            win_prefix = (generated_windows_prefix(iso_id),)
            if _is_generated(iso.boot_wim_path, win_prefix):
                iso.boot_wim_path = boot_rel.replace("\\", "/")
            if _is_generated(iso.install_wim_path, win_prefix):
                iso.install_wim_path = install_rel.replace("\\", "/")
        iso.extract_generation = media_rel
        iso.extract_status = ExtractStatus.ready.value
        iso.extract_error = ""
        refresh_iso_sources(iso, image_root=root)
        db.add(iso)
        sync_templates(db, iso)
        gc_extract_generations(db, iso)
        db.commit()
        if family == OsFamily.linux.value:
            try:
                request_export_reload(root)
            except OSError:
                pass


def _nfs_tree(iso: Iso) -> Path | None:
    if iso.id is None:
        return None
    revision = int(iso.extract_revision or 0)
    if revision < 1:
        return None
    return get_settings().image_root / "nfs" / str(int(iso.id)) / str(revision)


def nfs_tree_has_squashfs(iso: Iso) -> bool:
    tree = _nfs_tree(iso)
    return tree is not None and casper_has_squashfs(tree)


def nfs_tree_has_apt_repo(iso: Iso) -> bool:
    tree = _nfs_tree(iso)
    if tree is None:
        return False
    dists = tree / "dists"
    if not dists.is_dir():
        return False
    return any((child / "Release").is_file() for child in dists.iterdir() if child.is_dir())


def linux_needs_nfs_backfill(iso: Iso) -> bool:
    if iso.os_family != OsFamily.linux.value:
        return False
    if iso.extract_status != ExtractStatus.ready.value:
        return False
    if iso_on_disk(iso) is None:
        return False
    generation = (iso.extract_generation or "").replace("\\", "/").strip()
    if generation.startswith("nfs/"):
        return not nfs_tree_has_squashfs(iso) or not nfs_tree_has_apt_repo(iso)
    if generation.startswith("linux/"):
        return False
    return True


def requeue_linux_nfs_backfill() -> None:
    with session_scope() as db:
        changed = False
        for iso in db.exec(select(Iso)).all():
            if not linux_needs_nfs_backfill(iso):
                continue
            if schedule_extract(db, iso):
                changed = True
        if changed:
            db.commit()


def backfill_image_sources() -> None:
    with session_scope() as db:
        for iso in db.exec(select(Iso)).all():
            if refresh_iso_sources(iso):
                db.add(iso)
            sync_templates(db, iso)
        for image in db.exec(select(Image).where(Image.iso_id == None)).all():  # noqa: E711
            if refresh_image_sources(image):
                db.add(image)
        db.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    requeue_stale_extracting()
    backfill_image_seeds()
    backfill_image_sources()
    requeue_linux_nfs_backfill()
    LOGGER.info("extract worker ready")
    while True:
        job = claim_next_job()
        if job is None:
            try:
                with session_scope() as db:
                    expire_stale_imaging(db)
                    db.commit()
            except Exception:
                LOGGER.exception("imaging timeout expire failed")
            time.sleep(1)
            continue
        iso_id, revision = job
        run_one_job(iso_id, revision)


if __name__ == "__main__":
    main()
