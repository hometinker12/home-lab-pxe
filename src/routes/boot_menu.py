from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..inventory.boot_menu import (
    create_folder,
    delete_folder,
    folder_delete_blocked,
    folder_options,
    get_folder,
    get_or_create_settings,
    list_folders,
    list_images_in_folder,
    parent_folder_options,
    reorder_folder,
    reorder_image,
    save_settings,
    set_image_folder,
    update_folder,
)
from ..inventory.service import get_image, image_deploy_reason
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


def _optional_id(value: int | str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _tree(db: Session) -> list[dict]:
    rows = list_folders(db)
    by_parent: dict[int | None, list] = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row)
    for children in by_parent.values():
        children.sort(key=lambda item: (item.sort_order, item.id or 0))
    nodes: list[dict] = []

    def walk(parent_id: int | None, depth: int) -> None:
        siblings = by_parent.get(parent_id, [])
        count = len(siblings)
        for index, child in enumerate(siblings):
            nodes.append(
                {
                    "folder": child,
                    "depth": depth,
                    "can_move_up": index > 0,
                    "can_move_down": index < count - 1,
                }
            )
            walk(child.id, depth + 1)

    walk(None, 0)
    return nodes


def _page(
    request: Request,
    db: Session,
    *,
    folder_id: int | None = None,
    error: str | None = None,
    edit_open: bool = False,
):
    settings = get_or_create_settings(db)
    tree = _tree(db)
    selected = get_folder(db, folder_id) if folder_id else None
    if selected is None and tree:
        selected = tree[0]["folder"]
    images = list_images_in_folder(db, int(selected.id)) if selected is not None and selected.id else []
    image_rows = [{"image": img, "reason": image_deploy_reason(img)} for img in images]
    return render(
        request,
        "boot_menu.html",
        settings=settings,
        tree=tree,
        selected=selected,
        image_rows=image_rows,
        folder_options=folder_options(db),
        parent_options=parent_folder_options(db, selected),
        delete_blocked=folder_delete_blocked(db, selected) if selected is not None else "",
        edit_open=edit_open,
        error=error,
    )


def _redirect(folder_id: int | None) -> RedirectResponse:
    url = "/boot-menu"
    if folder_id:
        url = f"/boot-menu?folder={int(folder_id)}"
    return RedirectResponse(url=url, status_code=HTTP_303_SEE_OTHER)


@router.get("/boot-menu", response_class=HTMLResponse)
def boot_menu_page(
    request: Request,
    folder: int | None = None,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    return _page(request, db, folder_id=folder)


@router.post("/boot-menu/settings")
def boot_menu_settings(
    request: Request,
    title: str = Form(""),
    continue_label: str = Form(""),
    unknown_timeout_seconds: int = Form(5),
    menu_timeout_seconds: int = Form(10),
    folder: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        save_settings(
            db,
            title=title,
            continue_label=continue_label,
            unknown_timeout_seconds=unknown_timeout_seconds,
            menu_timeout_seconds=menu_timeout_seconds,
            actor=user,
        )
        db.commit()
    except ValueError as extra:
        db.rollback()
        return _page(request, db, folder_id=_optional_id(folder), error=str(extra))
    return _redirect(_optional_id(folder))


@router.post("/boot-menu/folders")
def boot_menu_create_folder(
    request: Request,
    name: str = Form(""),
    parent_id: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        row = create_folder(db, name=name, parent_id=_optional_id(parent_id), actor=user)
        db.commit()
        return _redirect(row.id)
    except ValueError as extra:
        db.rollback()
        return _page(request, db, folder_id=_optional_id(parent_id), error=str(extra))


@router.post("/boot-menu/folders/{folder_id}")
def boot_menu_update_folder(
    request: Request,
    folder_id: int,
    name: str = Form(""),
    parent_id: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    folder = get_folder(db, folder_id)
    if folder is None:
        return _page(request, db, error="Unknown folder")
    try:
        update_folder(
            db,
            folder,
            name=name,
            parent_id=_optional_id(parent_id),
            actor=user,
            parent_specified=True,
        )
        db.commit()
    except ValueError as extra:
        db.rollback()
        return _page(request, db, folder_id=folder_id, error=str(extra), edit_open=True)
    return _redirect(folder_id)


@router.post("/boot-menu/folders/{folder_id}/delete")
def boot_menu_delete_folder(
    request: Request,
    folder_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    folder = get_folder(db, folder_id)
    if folder is None:
        return _page(request, db, error="Unknown folder")
    parent_id = folder.parent_id
    try:
        delete_folder(db, folder, actor=user)
        db.commit()
    except ValueError as extra:
        db.rollback()
        return _page(request, db, folder_id=folder_id, error=str(extra), edit_open=True)
    return _redirect(parent_id)


@router.post("/boot-menu/folders/{folder_id}/move")
def boot_menu_move_folder(
    request: Request,
    folder_id: int,
    direction: str = Form("up"),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    folder = get_folder(db, folder_id)
    if folder is None:
        return _page(request, db, error="Unknown folder")
    reorder_folder(db, folder, direction=direction, actor=user)
    db.commit()
    return _redirect(folder_id)


@router.post("/boot-menu/images/{image_id}/move")
def boot_menu_move_image(
    request: Request,
    image_id: int,
    direction: str = Form("up"),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    image = get_image(db, image_id)
    if image is None:
        return _page(request, db, error="Unknown image")
    reorder_image(db, image, direction=direction, actor=user)
    db.commit()
    return _redirect(image.folder_id)


@router.post("/boot-menu/images/{image_id}/folder")
def boot_menu_assign_image(
    request: Request,
    image_id: int,
    folder_id: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    image = get_image(db, image_id)
    if image is None:
        return _page(request, db, error="Unknown image")
    dest = get_folder(db, _optional_id(folder_id))
    if dest is None:
        return _page(request, db, folder_id=image.folder_id, error="Unknown folder")
    set_image_folder(db, image, dest, actor=user)
    db.commit()
    return _redirect(dest.id)
