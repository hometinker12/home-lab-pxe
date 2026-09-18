from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..file_browser import (
    DEFAULT_VOLUME,
    FileBrowserError,
    browser_context,
    delete_entry,
    files_href,
    is_protected,
    mkdir_volume,
    normalize_volume,
    parse_location,
    resolve_volume_path,
    save_upload,
)
from ..inventory.service import record_activity
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


def _dir_param(request: Request, override: str | None = None) -> str:
    raw = override if override is not None else (request.query_params.get("dir") or "")
    text = raw.strip().replace("\\", "/")
    if text in {"", "/", ".", "./"}:
        return ""
    return text.lstrip("/")


def _volume_param(request: Request, override: str | None = None) -> str:
    if override is not None:
        return normalize_volume(override)
    return normalize_volume(request.query_params.get("root") or "")


def _files_page(
    request: Request,
    *,
    error: str | None = None,
    volume: str | None = None,
    directory: str | None = None,
):
    vol = _volume_param(request, volume)
    directory_name = _dir_param(request, directory)
    vol, directory_name = parse_location(vol, directory_name)
    return render(
        request,
        "files.html",
        fm=browser_context(vol, directory_name),
        error=error,
    )


@router.get("/files", response_class=HTMLResponse)
def files_page(request: Request, user: str = Depends(require_user)):
    return _files_page(request)


@router.get("/files/download")
def files_download(path: str, root: str = DEFAULT_VOLUME, user: str = Depends(require_user)):
    volume = normalize_volume(root)
    if is_protected(volume, path):
        raise HTTPException(status_code=404, detail="file missing")
    try:
        dest = resolve_volume_path(volume, path)
    except FileBrowserError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    if not dest.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(dest, filename=dest.name)


@router.post("/files/upload")
async def files_upload(
    request: Request,
    file: UploadFile | None = File(None),
    dir: str = Form(""),
    root: str = Form(DEFAULT_VOLUME),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    volume = _volume_param(request, root)
    directory = _dir_param(request, dir)
    if file is None or not (file.filename or "").strip():
        return _files_page(request, error="Choose a file to upload", volume=volume, directory=directory)
    try:
        relative = save_upload(file, volume=volume, directory=directory)
    except FileBrowserError as extra:
        return _files_page(request, error=str(extra), volume=volume, directory=directory)
    record_activity(db, actor=user, action="files.upload", detail=f"{volume}:{relative}"[:500])
    db.commit()
    return RedirectResponse(url=files_href(volume, directory), status_code=HTTP_303_SEE_OTHER)


@router.post("/files/mkdir")
def files_mkdir(
    request: Request,
    name: str = Form(""),
    dir: str = Form(""),
    root: str = Form(DEFAULT_VOLUME),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    volume = _volume_param(request, root)
    directory = _dir_param(request, dir)
    try:
        relative = mkdir_volume(volume, directory, name)
    except FileBrowserError as extra:
        return _files_page(request, error=str(extra), volume=volume, directory=directory)
    record_activity(db, actor=user, action="files.mkdir", detail=f"{volume}:{relative}"[:500])
    db.commit()
    return RedirectResponse(url=files_href(volume, directory), status_code=HTTP_303_SEE_OTHER)


@router.post("/files/delete")
def files_delete(
    request: Request,
    path: str = Form(""),
    dir: str = Form(""),
    root: str = Form(DEFAULT_VOLUME),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    volume = _volume_param(request, root)
    directory = _dir_param(request, dir)
    try:
        delete_entry(volume, path)
    except FileBrowserError as extra:
        return _files_page(request, error=str(extra), volume=volume, directory=directory)
    record_activity(db, actor=user, action="files.delete", detail=f"{volume}:{path}"[:500])
    db.commit()
    return RedirectResponse(url=files_href(volume, directory), status_code=HTTP_303_SEE_OTHER)
