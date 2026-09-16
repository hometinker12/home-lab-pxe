"""Dedicated ISO extraction worker. Claim jobs via SQLite, one at a time."""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, select

from .db import get_engine, init_db, session_scope
from .iso_extract import ArchiveRunner, ExtractError, extract_linux_payloads, extract_windows_media
from .models import ExtractStatus, Image, InstallAttempt, OsFamily
from .paths import UnsafePathError, resolve_under
from .seed_store import ensure_image_seed
from .settings import get_settings

LOGGER = logging.getLogger("home_lab_pxe.extract")
_ABS_PATH = re.compile(r"(?:[A-Za-z]:)?[/\\][^\s:]+")


def sanitize_error(message: str) -> str:
    text = _ABS_PATH.sub("<path>", message or "Extraction failed")
    return text[:300]


def iso_on_disk(image: Image) -> Path | None:
    relative = (image.iso_path or "").strip()
    if not relative:
        return None
    try:
        path = resolve_under(get_settings().image_root, relative)
    except UnsafePathError:
        return None
    return path if path.is_file() else None


def generated_linux_prefix(image_id: int) -> str:
    return f"uploads/{int(image_id)}/extracts/"


def generated_windows_prefix(image_id: int) -> str:
    return f"smb/{int(image_id)}/"


def _is_generated(path: str, prefix: str) -> bool:
    text = (path or "").replace("\\", "/")
    return text.startswith(prefix) or not text


def schedule_extract(db: Session, image: Image) -> bool:
    if iso_on_disk(image) is None:
        return False
    image.extract_revision = int(image.extract_revision or 0) + 1
    image.extract_status = ExtractStatus.queued.value
    image.extract_error = ""
    db.add(image)
    return True


def requeue_stale_extracting() -> None:
    with session_scope() as db:
        rows = db.exec(select(Image).where(Image.extract_status == ExtractStatus.extracting.value)).all()
        for image in rows:
            image.extract_status = ExtractStatus.queued.value
            db.add(image)
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
            text("SELECT id, extract_revision FROM image WHERE extract_status = :queued ORDER BY id ASC LIMIT 1"),
            {"queued": ExtractStatus.queued.value},
        ).fetchone()
        if row is None:
            return None
        image_id, revision = int(row[0]), int(row[1])
        result = conn.execute(
            text(
                "UPDATE image SET extract_status = :extracting "
                "WHERE id = :id AND extract_revision = :rev AND extract_status = :queued"
            ),
            {
                "extracting": ExtractStatus.extracting.value,
                "id": image_id,
                "rev": revision,
                "queued": ExtractStatus.queued.value,
            },
        )
        if result.rowcount != 1:
            return None
        return image_id, revision


def _relative_under_root(path: Path) -> str:
    root = get_settings().image_root.resolve()
    return path.resolve().relative_to(root).as_posix()


def _replace_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))


def gc_extract_generations(db: Session, image: Image) -> None:
    if image.id is None:
        return
    image_id = int(image.id)
    open_revs = {
        int(row.extract_revision)
        for row in db.exec(
            select(InstallAttempt).where(
                InstallAttempt.image_id == image_id,
                InstallAttempt.completed_at == None,  # noqa: E711
            )
        ).all()
    }
    keep = {int(image.extract_revision or 0), *open_revs}
    root = get_settings().image_root
    for base in (root / "uploads" / str(image_id) / "extracts", root / "smb" / str(image_id)):
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


def run_one_job(image_id: int, revision: int, runner: ArchiveRunner | None = None) -> None:
    settings = get_settings()
    root = settings.image_root
    with session_scope() as db:
        image = db.get(Image, image_id)
        if image is None or int(image.extract_revision or 0) != revision:
            return
        iso = iso_on_disk(image)
        if iso is None:
            image.extract_status = ExtractStatus.idle.value
            image.extract_error = ""
            db.add(image)
            db.commit()
            return
        family = image.os_family
        staging = root / "uploads" / str(image_id) / "extracts" / f"{revision}.tmp"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            if family == OsFamily.windows.value:
                result = extract_windows_media(iso, staging, image_root=root, runner=runner)
                published = root / "smb" / str(image_id) / str(revision)
                _replace_dir(staging, published)
                media_rel = f"{image_id}/{revision}"
                boot_rel = f"smb/{image_id}/{revision}/{result.boot_wim_relative}"
                install_rel = f"smb/{image_id}/{revision}/{result.install_wim_relative}"
            else:
                result = extract_linux_payloads(iso, staging, image_root=root, runner=runner)
                published = root / "uploads" / str(image_id) / "extracts" / str(revision)
                _replace_dir(staging, published)
                media_rel = ""
                boot_rel = ""
                install_rel = ""
        except ExtractError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            image = db.get(Image, image_id)
            if image is None or int(image.extract_revision or 0) != revision:
                return
            image.extract_status = ExtractStatus.failed.value
            image.extract_error = sanitize_error(str(exc))
            db.add(image)
            db.commit()
            return
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            image = db.get(Image, image_id)
            if image is None or int(image.extract_revision or 0) != revision:
                return
            image.extract_status = ExtractStatus.failed.value
            image.extract_error = "Extraction failed"
            db.add(image)
            db.commit()
            LOGGER.exception("extract failed image_id=%s revision=%s", image_id, revision)
            return

        image = db.get(Image, image_id)
        if image is None or int(image.extract_revision or 0) != revision:
            shutil.rmtree(published, ignore_errors=True)
            return
        if family == OsFamily.linux.value:
            linux_prefix = generated_linux_prefix(image_id)
            if _is_generated(image.kernel_path, linux_prefix):
                image.kernel_path = _relative_under_root(published / "kernel")
            if _is_generated(image.initrd_path, linux_prefix):
                image.initrd_path = _relative_under_root(published / "initrd")
            image.extract_generation = f"uploads/{image_id}/extracts/{revision}"
        else:
            win_prefix = generated_windows_prefix(image_id)
            if _is_generated(image.boot_wim_path, win_prefix):
                image.boot_wim_path = boot_rel.replace("\\", "/")
            if _is_generated(image.install_wim_path, win_prefix):
                image.install_wim_path = install_rel.replace("\\", "/")
            image.extract_generation = media_rel
        image.extract_status = ExtractStatus.ready.value
        image.extract_error = ""
        db.add(image)
        gc_extract_generations(db, image)
        db.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    requeue_stale_extracting()
    backfill_image_seeds()
    LOGGER.info("extract worker ready")
    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(1)
            continue
        image_id, revision = job
        run_one_job(image_id, revision)


if __name__ == "__main__":
    main()
