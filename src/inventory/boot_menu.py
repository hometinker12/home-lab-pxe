"""PXE boot-menu folders, settings, and image placement."""

from __future__ import annotations

import os

from sqlmodel import Session, col, select

from ..models import BootMenuFolder, BootMenuSettings, Image, OsFamily, utcnow
from .service import record_activity

BOOT_MENU_SETTINGS_ID = 1
DEFAULT_TITLE = "Network Installation Options"
DEFAULT_CONTINUE_LABEL = "Continue to next boot device"
DEFAULT_UNKNOWN_TIMEOUT_SECONDS = 5
DEFAULT_MENU_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 300
MAX_FOLDER_NAME = 80
MAX_FOLDER_DEPTH = 8
DEFAULT_ROOT_FOLDERS = ("Windows", "Linux", "Tools")
FAMILY_FOLDER_NAMES = {
    OsFamily.windows.value: "Windows",
    OsFamily.linux.value: "Linux",
    OsFamily.tool.value: "Tools",
}


def _env_timeout(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, min(MAX_TIMEOUT_SECONDS, value))


def default_unknown_timeout_seconds() -> int:
    return _env_timeout("PXE_UNKNOWN_BOOT_TIMEOUT_SECONDS", DEFAULT_UNKNOWN_TIMEOUT_SECONDS)


def default_menu_timeout_seconds() -> int:
    return _env_timeout("PXE_MENU_TIMEOUT_SECONDS", DEFAULT_MENU_TIMEOUT_SECONDS)


def clamp_timeout(value: int) -> int:
    return max(0, min(MAX_TIMEOUT_SECONDS, int(value)))


def get_or_create_settings(db: Session) -> BootMenuSettings:
    row = db.get(BootMenuSettings, BOOT_MENU_SETTINGS_ID)
    if row is not None:
        return row
    row = BootMenuSettings(
        id=BOOT_MENU_SETTINGS_ID,
        title=DEFAULT_TITLE,
        continue_label=DEFAULT_CONTINUE_LABEL,
        unknown_timeout_seconds=default_unknown_timeout_seconds(),
        menu_timeout_seconds=default_menu_timeout_seconds(),
    )
    db.add(row)
    db.flush()
    return row


def save_settings(
    db: Session,
    *,
    title: str,
    continue_label: str,
    unknown_timeout_seconds: int,
    menu_timeout_seconds: int,
    actor: str,
) -> BootMenuSettings:
    row = get_or_create_settings(db)
    heading = title.strip() or DEFAULT_TITLE
    label = continue_label.strip() or DEFAULT_CONTINUE_LABEL
    row.title = heading[:120]
    row.continue_label = label[:120]
    row.unknown_timeout_seconds = clamp_timeout(unknown_timeout_seconds)
    row.menu_timeout_seconds = clamp_timeout(menu_timeout_seconds)
    row.updated_at = utcnow()
    db.add(row)
    record_activity(db, actor=actor, action="boot-menu.settings", detail=row.title)
    return row


def list_folders(db: Session) -> list[BootMenuFolder]:
    return list(db.exec(select(BootMenuFolder).order_by(col(BootMenuFolder.sort_order), col(BootMenuFolder.id))).all())


def get_folder(db: Session, folder_id: int | None) -> BootMenuFolder | None:
    if not folder_id:
        return None
    return db.get(BootMenuFolder, folder_id)


def list_child_folders(db: Session, parent_id: int | None) -> list[BootMenuFolder]:
    return list(
        db.exec(
            select(BootMenuFolder)
            .where(BootMenuFolder.parent_id == parent_id)
            .order_by(col(BootMenuFolder.sort_order), col(BootMenuFolder.id))
        ).all()
    )


def folder_depth(db: Session, folder: BootMenuFolder | None) -> int:
    depth = 0
    current = folder
    seen: set[int] = set()
    while current is not None and current.parent_id:
        if current.id in seen:
            break
        if current.id is not None:
            seen.add(current.id)
        current = get_folder(db, current.parent_id)
        depth += 1
        if depth > MAX_FOLDER_DEPTH + 2:
            break
    return depth


def folder_path(db: Session, folder: BootMenuFolder | None, *, separator: str = " / ") -> str:
    if folder is None:
        return ""
    parts = [folder.name]
    current = folder
    seen: set[int] = set()
    while current is not None and current.parent_id:
        if current.id in seen:
            break
        if current.id is not None:
            seen.add(current.id)
        current = get_folder(db, current.parent_id)
        if current is None:
            break
        parts.append(current.name)
    return separator.join(reversed(parts))


def folder_options(db: Session) -> list[tuple[int, str]]:
    rows = list_folders(db)
    by_parent: dict[int | None, list[BootMenuFolder]] = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row)
    for children in by_parent.values():
        children.sort(key=lambda item: (item.sort_order, item.id or 0))
    options: list[tuple[int, str]] = []

    def walk(parent_id: int | None) -> None:
        for child in by_parent.get(parent_id, []):
            if child.id is None:
                continue
            options.append((int(child.id), folder_path(db, child, separator="/")))
            walk(child.id)

    walk(None)
    return options


def _descendant_ids(db: Session, folder_id: int) -> set[int]:
    ids: set[int] = set()

    def walk(parent_id: int) -> None:
        for child in list_child_folders(db, parent_id):
            if child.id is None:
                continue
            ids.add(int(child.id))
            walk(int(child.id))

    walk(folder_id)
    return ids


def _subtree_extra_depth(db: Session, folder: BootMenuFolder) -> int:
    children = list_child_folders(db, folder.id)
    if not children:
        return 0
    return 1 + max(_subtree_extra_depth(db, child) for child in children)


def _is_self_or_descendant(db: Session, node: BootMenuFolder, folder_id: int) -> bool:
    current: BootMenuFolder | None = node
    seen: set[int] = set()
    while current is not None:
        if current.id == folder_id:
            return True
        if current.id is not None:
            if current.id in seen:
                break
            seen.add(current.id)
        if not current.parent_id:
            break
        current = get_folder(db, current.parent_id)
    return False


def parent_folder_options(db: Session, folder: BootMenuFolder | None) -> list[tuple[int, str]]:
    if folder is None or folder.id is None:
        return folder_options(db)
    blocked = {int(folder.id)} | _descendant_ids(db, int(folder.id))
    return [(fid, path) for fid, path in folder_options(db) if fid not in blocked]


def _normalize_name(name: str) -> str:
    text = " ".join((name or "").split())
    if not text:
        raise ValueError("Folder name is required")
    if len(text) > MAX_FOLDER_NAME:
        raise ValueError(f"Folder name must be {MAX_FOLDER_NAME} characters or less")
    return text


def _sibling_clash(db: Session, *, parent_id: int | None, name: str, exclude_id: int | None = None) -> bool:
    needle = name.casefold()
    for row in list_child_folders(db, parent_id):
        if exclude_id is not None and row.id == exclude_id:
            continue
        if row.name.casefold() == needle:
            return True
    return False


def _next_folder_sort(db: Session, parent_id: int | None) -> int:
    siblings = list_child_folders(db, parent_id)
    if not siblings:
        return 0
    return max(row.sort_order for row in siblings) + 1


def _next_image_sort(db: Session, folder_id: int) -> int:
    rows = list(db.exec(select(Image).where(Image.folder_id == folder_id)).all())
    if not rows:
        return 0
    return max(int(row.sort_order or 0) for row in rows) + 1


def root_folder_named(db: Session, name: str) -> BootMenuFolder | None:
    needle = name.casefold()
    for row in list_child_folders(db, None):
        if row.name.casefold() == needle:
            return row
    return None


def first_folder(db: Session) -> BootMenuFolder | None:
    return db.exec(select(BootMenuFolder).order_by(col(BootMenuFolder.id))).first()


def resolve_folder_for_image(db: Session, folder_id: int | None, os_family: OsFamily | str) -> BootMenuFolder:
    folder = get_folder(db, folder_id)
    if folder is not None:
        return folder
    family = os_family.value if isinstance(os_family, OsFamily) else os_family
    named = root_folder_named(db, FAMILY_FOLDER_NAMES.get(family, "Linux"))
    if named is not None:
        return named
    fallback = first_folder(db)
    if fallback is None:
        raise ValueError("Create a boot-menu folder before adding an image")
    return fallback


def create_folder(db: Session, *, name: str, parent_id: int | None, actor: str) -> BootMenuFolder:
    trimmed = _normalize_name(name)
    parent = None
    if parent_id:
        parent = get_folder(db, parent_id)
        if parent is None:
            raise ValueError("Unknown parent folder")
        if folder_depth(db, parent) + 1 >= MAX_FOLDER_DEPTH:
            raise ValueError("Folder nesting is too deep")
    if _sibling_clash(db, parent_id=parent.id if parent else None, name=trimmed):
        raise ValueError("A folder with that name already exists here")
    row = BootMenuFolder(
        name=trimmed,
        parent_id=parent.id if parent else None,
        sort_order=_next_folder_sort(db, parent.id if parent else None),
    )
    db.add(row)
    db.flush()
    record_activity(db, actor=actor, action="boot-menu.folder-create", detail=folder_path(db, row))
    return row


def update_folder(
    db: Session,
    folder: BootMenuFolder,
    *,
    name: str,
    parent_id: int | None,
    actor: str,
    parent_specified: bool = True,
) -> BootMenuFolder:
    trimmed = _normalize_name(name)
    target_parent_id = parent_id if parent_specified else folder.parent_id
    parent: BootMenuFolder | None = None
    if target_parent_id:
        parent = get_folder(db, target_parent_id)
        if parent is None:
            raise ValueError("Unknown parent folder")
        if folder.id is not None and _is_self_or_descendant(db, parent, int(folder.id)):
            raise ValueError("A folder cannot be moved into itself or a nested folder")
        extra = _subtree_extra_depth(db, folder)
        if folder_depth(db, parent) + 1 + extra >= MAX_FOLDER_DEPTH:
            raise ValueError("Folder nesting is too deep")
        target_parent_id = parent.id
    else:
        target_parent_id = None
    if _sibling_clash(db, parent_id=target_parent_id, name=trimmed, exclude_id=folder.id):
        raise ValueError("A folder with that name already exists here")
    parent_changed = target_parent_id != folder.parent_id
    name_changed = trimmed != folder.name
    folder.name = trimmed
    if parent_changed:
        folder.parent_id = target_parent_id
        folder.sort_order = _next_folder_sort(db, target_parent_id)
    db.add(folder)
    detail = folder_path(db, folder)
    if parent_changed:
        record_activity(db, actor=actor, action="boot-menu.folder-reparent", detail=detail)
    elif name_changed:
        record_activity(db, actor=actor, action="boot-menu.folder-rename", detail=detail)
    return folder


def rename_folder(db: Session, folder: BootMenuFolder, *, name: str, actor: str) -> BootMenuFolder:
    return update_folder(db, folder, name=name, parent_id=None, actor=actor, parent_specified=False)


def folder_delete_blocked(db: Session, folder: BootMenuFolder | None) -> str:
    if folder is None or folder.id is None:
        return "Unknown folder"
    if len(list_folders(db)) <= 1:
        return "Cannot delete the last remaining folder"
    if list_child_folders(db, folder.id):
        return "Move or delete nested folders first"
    if list_images_in_folder(db, int(folder.id)):
        return "Move images out of this folder first"
    return ""


def delete_folder(db: Session, folder: BootMenuFolder, *, actor: str) -> None:
    reason = folder_delete_blocked(db, folder)
    if reason:
        raise ValueError(reason)
    detail = folder_path(db, folder)
    db.delete(folder)
    db.flush()
    record_activity(db, actor=actor, action="boot-menu.folder-delete", detail=detail)


def _swap_sort(left, right) -> None:
    left.sort_order, right.sort_order = right.sort_order, left.sort_order


def reorder_folder(db: Session, folder: BootMenuFolder, *, direction: str, actor: str) -> None:
    siblings = list_child_folders(db, folder.parent_id)
    index = next((i for i, row in enumerate(siblings) if row.id == folder.id), -1)
    if index < 0:
        return
    delta = -1 if direction == "up" else 1
    neighbor_index = index + delta
    if neighbor_index < 0 or neighbor_index >= len(siblings):
        return
    neighbor = siblings[neighbor_index]
    if folder.sort_order == neighbor.sort_order:
        neighbor.sort_order = folder.sort_order + (1 if delta > 0 else -1)
    _swap_sort(folder, neighbor)
    db.add(folder)
    db.add(neighbor)
    record_activity(db, actor=actor, action="boot-menu.folder-reorder", detail=folder.name)


def list_images_in_folder(db: Session, folder_id: int) -> list[Image]:
    return list(
        db.exec(
            select(Image).where(Image.folder_id == folder_id).order_by(col(Image.sort_order), col(Image.name))
        ).all()
    )


def set_image_folder(db: Session, image: Image, folder: BootMenuFolder, *, actor: str) -> Image:
    if folder.id is None:
        raise ValueError("Unknown folder")
    if image.folder_id != folder.id:
        image.folder_id = folder.id
        image.sort_order = _next_image_sort(db, int(folder.id))
        db.add(image)
        record_activity(db, actor=actor, action="boot-menu.image-move", detail=image.name)
    return image


def reorder_image(db: Session, image: Image, *, direction: str, actor: str) -> None:
    if image.folder_id is None:
        return
    siblings = list_images_in_folder(db, int(image.folder_id))
    index = next((i for i, row in enumerate(siblings) if row.id == image.id), -1)
    if index < 0:
        return
    delta = -1 if direction == "up" else 1
    neighbor_index = index + delta
    if neighbor_index < 0 or neighbor_index >= len(siblings):
        return
    neighbor = siblings[neighbor_index]
    if image.sort_order == neighbor.sort_order:
        neighbor.sort_order = int(image.sort_order or 0) + (1 if delta > 0 else -1)
    _swap_sort(image, neighbor)
    db.add(image)
    db.add(neighbor)
    record_activity(db, actor=actor, action="boot-menu.image-reorder", detail=image.name)


def place_new_image(db: Session, image: Image, *, folder_id: int | None, os_family: OsFamily | str) -> BootMenuFolder:
    folder = resolve_folder_for_image(db, folder_id, os_family)
    image.folder_id = folder.id
    image.sort_order = _next_image_sort(db, int(folder.id))
    db.add(image)
    return folder


def default_folder_ids(db: Session) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for family, name in FAMILY_FOLDER_NAMES.items():
        row = root_folder_named(db, name)
        if row is not None and row.id is not None:
            mapping[family] = int(row.id)
    fallback = first_folder(db)
    if fallback is not None and fallback.id is not None:
        for family in FAMILY_FOLDER_NAMES:
            mapping.setdefault(family, int(fallback.id))
    return mapping


def backfill_image_folders(db: Session) -> None:
    fallback = first_folder(db)
    if fallback is None:
        return
    for image in db.exec(select(Image)).all():
        if image.folder_id:
            continue
        place_new_image(db, image, folder_id=None, os_family=image.os_family)


def seed_default_folders(db: Session) -> None:
    if list_folders(db):
        return
    for index, name in enumerate(DEFAULT_ROOT_FOLDERS):
        db.add(BootMenuFolder(name=name, parent_id=None, sort_order=index))
    db.flush()


def seed_boot_menu() -> None:
    from ..db import SessionLocal, get_engine

    get_engine()
    assert SessionLocal is not None
    with SessionLocal() as db:
        get_or_create_settings(db)
        seed_default_folders(db)
        backfill_image_folders(db)
        db.commit()
