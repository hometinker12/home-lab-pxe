"""Render interactive iPXE folder menus. Never embed passwords or user-data."""

from __future__ import annotations

import re

from sqlmodel import Session

from ..inventory.boot_menu import (
    folder_path,
    get_folder,
    get_or_create_settings,
    list_child_folders,
    list_images_in_folder,
)
from ..inventory.service import image_deploy_blocked
from ..models import BootMenuFolder, Image
from ..settings import get_settings
from .ipxe import local_disk_lines

_UNSAFE = re.compile(r"[\x00-\x1f|\\]")


def _label(value: str) -> str:
    text = _UNSAFE.sub(" ", value or "").replace("${", "$ {").strip() or "item"
    return text[:80]


def _menu_images(db: Session, folder_id: int) -> list[Image]:
    visible: list[Image] = []
    for image in list_images_in_folder(db, folder_id):
        if image_deploy_blocked(image):
            continue
        visible.append(image)
    return visible


def folder_menu_script(db: Session, mac_hyphen: str, folder_id: int | None) -> str:
    settings = get_or_create_settings(db)
    base = get_settings().public_url.rstrip("/")
    folder: BootMenuFolder | None = get_folder(db, folder_id) if folder_id else None
    parent_id = folder.parent_id if folder is not None else None
    children = list_child_folders(db, folder.id if folder is not None else None)
    images = _menu_images(db, int(folder.id)) if folder is not None and folder.id else []

    if folder is None:
        title = _label(settings.title)
    else:
        title = _label(folder_path(db, folder) or folder.name)
    timeout_ms = max(0, int(settings.menu_timeout_seconds or 0)) * 1000
    continue_label = _label(settings.continue_label)
    root_url = f"{base}/ipxe/{mac_hyphen}"
    lines = [
        "#!ipxe",
        "isset ${cls} && cls ||",
        f"menu {title}",
    ]
    if folder is None:
        lines.append(f"item local {continue_label}")
    else:
        lines.append("item back Back")
    for child in children:
        if child.id is None:
            continue
        lines.append(f"item f{child.id} {_label(child.name)}")
    for image in images:
        if image.id is None:
            continue
        lines.append(f"item i{image.id} {_label(image.name)}")
    if folder is None and timeout_ms > 0:
        lines.append(f"choose --timeout {timeout_ms} --default local selected || goto local")
    elif folder is None:
        lines.append("choose --default local selected || goto local")
    else:
        lines.append("choose selected || goto back")
    lines.append("goto ${selected}")
    lines.append(":local")
    lines.append(local_disk_lines().rstrip("\n"))
    if folder is None:
        lines.append(":back")
        lines.append(local_disk_lines().rstrip("\n"))
    else:
        back = f"{root_url}/menu/{parent_id}" if parent_id else root_url
        lines.append(":back")
        lines.append(f"chain --replace {back} || goto local")
    for child in children:
        if child.id is None:
            continue
        lines.append(f":f{child.id}")
        lines.append(f"chain --replace {root_url}/menu/{child.id} || goto local")
    for image in images:
        if image.id is None:
            continue
        lines.append(f":i{image.id}")
        lines.append(f"chain --replace {root_url}/boot/{image.id} || goto local")
    return "\n".join(lines) + "\n"
