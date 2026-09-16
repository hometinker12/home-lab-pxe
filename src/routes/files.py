from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..inventory.service import record_activity
from ..tftp_store import (
    TftpStoreError,
    browser_context,
    delete_tftp_entry,
    mkdir_tftp,
    resolve_tftp,
    save_tftp_upload,
)
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


def _dir_param(request: Request, override: str | None = None) -> str:
    raw = override if override is not None else (request.query_params.get("dir") or "")
    text = raw.strip().replace("\\", "/")
    if text in {"", "/", ".", "./"}:
        return ""
    return text.lstrip("/")


def _files_url(tftp_dir: str = "") -> str:
    if not tftp_dir:
        return "/files"
    return "/files?" + urlencode({"dir": tftp_dir})


def _files_page(request: Request, *, error: str | None = None, tftp_dir: str | None = None):
    directory = _dir_param(request, tftp_dir)
    return render(
        request,
        "files.html",
        tftp=browser_context(directory),
        error=error,
    )


@router.get("/files", response_class=HTMLResponse)
def files_page(request: Request, user: str = Depends(require_user)):
    return _files_page(request)


@router.get("/files/download")
def files_download(path: str, user: str = Depends(require_user)):
    try:
        dest = resolve_tftp(path)
    except TftpStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not dest.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(dest, filename=dest.name)


@router.post("/files/upload")
async def files_upload(
    request: Request,
    file: UploadFile | None = File(None),
    dir: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    directory = _dir_param(request, dir)
    if file is None or not (file.filename or "").strip():
        return _files_page(request, error="Choose a file to upload", tftp_dir=directory)
    try:
        relative = save_tftp_upload(file, directory=directory)
    except TftpStoreError as exc:
        return _files_page(request, error=str(exc), tftp_dir=directory)
    record_activity(db, actor=user, action="tftp.upload", detail=relative)
    db.commit()
    return RedirectResponse(url=_files_url(directory), status_code=HTTP_303_SEE_OTHER)


@router.post("/files/mkdir")
def files_mkdir(
    request: Request,
    name: str = Form(""),
    dir: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    directory = _dir_param(request, dir)
    try:
        relative = mkdir_tftp(directory, name)
    except TftpStoreError as exc:
        return _files_page(request, error=str(exc), tftp_dir=directory)
    record_activity(db, actor=user, action="tftp.mkdir", detail=relative)
    db.commit()
    return RedirectResponse(url=_files_url(directory), status_code=HTTP_303_SEE_OTHER)


@router.post("/files/delete")
def files_delete(
    request: Request,
    path: str = Form(""),
    dir: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    directory = _dir_param(request, dir)
    try:
        delete_tftp_entry(path)
    except TftpStoreError as extra:
        return _files_page(request, error=str(extra), tftp_dir=directory)
    record_activity(db, actor=user, action="tftp.delete", detail=path[:500])
    db.commit()
    return RedirectResponse(url=_files_url(directory), status_code=HTTP_303_SEE_OTHER)
