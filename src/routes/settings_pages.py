from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session
from starlette.status import HTTP_303_SEE_OTHER

from ..auth import require_user
from ..db import get_db
from ..dhcp_runtime import (
    DhcpConfigError,
    external_dhcp_hints,
    helper_status,
    load_runtime,
    save_dhcp,
    save_imaging_timeout,
    save_pxe,
    save_tftp,
)
from ..inventory.service import LAB_DEFAULT_MACHINE_ID, local_account_status, upsert_local_account
from ..models import AccountKind
from ..netinfo import net_snapshot
from ..settings import get_settings, smb_password_configured
from ..timezones import timezone_choices
from ..tls_store import (
    MAX_PEM_BYTES,
    TlsError,
    console_https_url,
    ensure_tls_material,
    generate_self_signed,
    install_pem,
    read_pem_upload,
    request_https_reload,
)
from ..web import render

router = APIRouter(tags=["console"], include_in_schema=False)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "on", "yes"}


def _settings_context(
    request: Request,
    db: Session,
    *,
    error=None,
    notice=None,
    open_section: str | None = None,
):
    settings = get_settings()
    dhcp = load_runtime(db)
    snapshot = net_snapshot(request)
    selected_timezone = (dhcp.default_timezone or "").strip() or "UTC"
    if open_section is None:
        open_section = (request.query_params.get("section") or "").strip() or None
    try:
        tls = ensure_tls_material()
        tls_error = None
    except TlsError as exc:
        tls = None
        tls_error = str(exc)
    return render(
        request,
        "settings.html",
        settings=settings,
        dhcp=dhcp,
        dhcp_status=helper_status(),
        net=snapshot,
        pxe_hints=external_dhcp_hints(snapshot),
        tls=tls,
        tls_error=tls_error,
        https_url=console_https_url(),
        linux=local_account_status(db, LAB_DEFAULT_MACHINE_ID, AccountKind.linux_root),
        windows=local_account_status(db, LAB_DEFAULT_MACHINE_ID, AccountKind.windows_administrator),
        smb_host=settings.smb_host,
        smb_user=settings.smb_user,
        smb_password_set=smb_password_configured(),
        nfs_host=settings.nfs_host,
        nfs_export=settings.nfs_export,
        timezones=timezone_choices(selected_timezone),
        selected_timezone=selected_timezone,
        error=error,
        notice=notice,
        open_section=open_section,
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), user: str = Depends(require_user)):
    return _settings_context(request, db)


@router.post("/settings/pxe")
def settings_pxe(
    request: Request,
    bind_interface: str = Form("eth0"),
    extra_options: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        save_pxe(db, bind_interface=bind_interface, extra_options=extra_options, actor=user)
    except DhcpConfigError as exc:
        return _settings_context(request, db, error=str(exc), open_section="pxe")
    return RedirectResponse(url="/settings", status_code=HTTP_303_SEE_OTHER)


@router.post("/settings/dhcp")
def settings_dhcp(
    request: Request,
    enabled: str = Form("0"),
    mode: str = Form("proxy"),
    dhcp_range: str = Form(""),
    dhcp_router: str = Form(""),
    dhcp_dns: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        save_dhcp(
            db,
            enabled=_truthy(enabled),
            mode=mode,
            dhcp_range=dhcp_range,
            dhcp_router=dhcp_router,
            dhcp_dns=dhcp_dns,
            actor=user,
        )
    except DhcpConfigError as exc:
        return _settings_context(request, db, error=str(exc), open_section="dhcp")
    return RedirectResponse(url="/settings", status_code=HTTP_303_SEE_OTHER)


@router.post("/settings/tftp")
def settings_tftp(
    request: Request,
    tftp_enabled: str = Form("0"),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        save_tftp(db, tftp_enabled=_truthy(tftp_enabled), actor=user)
    except DhcpConfigError as exc:
        return _settings_context(request, db, error=str(exc), open_section="tftp")
    return RedirectResponse(url="/settings?section=tftp", status_code=HTTP_303_SEE_OTHER)


@router.post("/settings/machines")
def settings_machines(
    request: Request,
    imaging_timeout_minutes: str = Form("15"),
    default_timezone: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        minutes = int((imaging_timeout_minutes or "").strip())
    except ValueError:
        return _settings_context(
            request, db, error="Imaging timeout must be a whole number of minutes", open_section="machines"
        )
    try:
        save_imaging_timeout(
            db,
            minutes=minutes,
            actor=user,
            timezone=(default_timezone.strip() or None),
        )
    except DhcpConfigError as exc:
        return _settings_context(request, db, error=str(exc), open_section="machines")
    return RedirectResponse(url="/settings?section=machines", status_code=HTTP_303_SEE_OTHER)


@router.post("/settings/accounts")
def settings_accounts(
    linux_username: str = Form("root"),
    linux_password: str = Form(""),
    windows_username: str = Form("Administrator"),
    windows_password: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    if linux_password or linux_username:
        try:
            upsert_local_account(
                db,
                machine_id=LAB_DEFAULT_MACHINE_ID,
                kind=AccountKind.linux_root,
                username=linux_username.strip() or "root",
                password=linux_password or None,
            )
        except ValueError:
            pass
    if windows_password or windows_username:
        try:
            upsert_local_account(
                db,
                machine_id=LAB_DEFAULT_MACHINE_ID,
                kind=AccountKind.windows_administrator,
                username=windows_username.strip() or "Administrator",
                password=windows_password or None,
            )
        except ValueError:
            pass
    db.commit()
    return RedirectResponse(url="/settings", status_code=HTTP_303_SEE_OTHER)


async def _read_pem_file(upload: UploadFile | None, *, label: str) -> bytes:
    if upload is None or not (upload.filename or "").strip():
        raise TlsError(f"{label} file is required")
    data = await upload.read(MAX_PEM_BYTES + 1)
    return read_pem_upload(data, label=label)


@router.post("/settings/ssl")
async def settings_ssl(
    request: Request,
    cert_pem: UploadFile | None = File(None),
    key_pem: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        cert_bytes = await _read_pem_file(cert_pem, label="Certificate")
        key_bytes = await _read_pem_file(key_pem, label="Private key")
        install_pem(cert_bytes, key_bytes)
    except TlsError as exc:
        return _settings_context(request, db, error=str(exc), open_section="ssl")
    return RedirectResponse(url="/settings", status_code=HTTP_303_SEE_OTHER)


@router.post("/settings/ssl/regenerate")
def settings_ssl_regenerate(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        generate_self_signed()
        request_https_reload()
    except TlsError as exc:
        return _settings_context(request, db, error=str(exc), open_section="ssl")
    return RedirectResponse(url="/settings", status_code=HTTP_303_SEE_OTHER)
