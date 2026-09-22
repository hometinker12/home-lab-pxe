from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select
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
from ..inventory.service import LAB_DEFAULT_MACHINE_ID, local_account_status, record_activity, upsert_local_account
from ..models import AccountKind, User
from ..netinfo import net_snapshot
from ..security import hash_password, verify_password
from ..settings import get_settings
from ..smb_runtime import rotate_smb_password, smb_password_configured
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
    notice_flag = (request.query_params.get("notice") or "").strip()
    if notice is None and notice_flag == "smb-rotated":
        notice = "Windows SMB password rotated. It is not displayed. New WinPE boots use the new password."
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
    imaging_timeout_minutes: str = Form("60"),
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


@router.post("/settings/smb/rotate")
def settings_smb_rotate(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        rotate_smb_password(db, actor=user)
    except (OSError, ValueError, RuntimeError) as exc:
        return _settings_context(request, db, error=str(exc), open_section="smb")
    return RedirectResponse(url="/settings?section=smb&notice=smb-rotated", status_code=HTTP_303_SEE_OTHER)


def _save_lab_account(
    db: Session,
    *,
    kind: AccountKind,
    username: str,
    password: str,
    default: str,
) -> None:
    status = local_account_status(db, LAB_DEFAULT_MACHINE_ID, kind)
    name = username.strip() or default
    secret = password or None
    if not secret and not status.get("set"):
        if name != default:
            raise ValueError("Password is required when creating a local account")
        return
    if status.get("unreadable") and not secret:
        raise ValueError("encryption key does not match stored accounts")
    upsert_local_account(
        db,
        machine_id=LAB_DEFAULT_MACHINE_ID,
        kind=kind,
        username=name,
        password=secret,
    )


@router.post("/settings/accounts")
def settings_accounts(
    request: Request,
    linux_username: str = Form("root"),
    linux_password: str = Form(""),
    windows_username: str = Form("Administrator"),
    windows_password: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    try:
        _save_lab_account(
            db,
            kind=AccountKind.linux_root,
            username=linux_username,
            password=linux_password,
            default="root",
        )
        _save_lab_account(
            db,
            kind=AccountKind.windows_administrator,
            username=windows_username,
            password=windows_password,
            default="Administrator",
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _settings_context(request, db, error=str(exc), open_section="accounts")
    return RedirectResponse(url="/settings?section=accounts", status_code=HTTP_303_SEE_OTHER)


@router.post("/settings/password")
def settings_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    row = db.exec(select(User).where(User.username == user)).first()
    if row is None or not verify_password(current_password, row.hashed_password):
        return _settings_context(request, db, error="Current password is incorrect", open_section="password")
    if len(new_password) < 8:
        return _settings_context(
            request, db, error="New password must be at least 8 characters", open_section="password"
        )
    if new_password != confirm_password:
        return _settings_context(
            request, db, error="New password and confirmation do not match", open_section="password"
        )
    row.hashed_password = hash_password(new_password)
    row.session_version = int(row.session_version or 0) + 1
    db.add(row)
    record_activity(db, actor=user, action="auth.password", detail="console password changed")
    db.commit()
    request.state.session_refresh = None
    response = RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
    response.delete_cookie("session", path="/")
    return response


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
