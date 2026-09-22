"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import session_cookie_secure, session_cookie_settings
from .csrf import csrf_origin_allowed, csrf_rejection_response
from .db import init_db
from .rate_limit import rate_limit_exceeded, rate_limit_rejection_response
from .routes.activity import router as activity_router
from .routes.auth_pages import router as auth_router
from .routes.boot_files import router as boot_files_router
from .routes.boot_menu import router as boot_menu_router
from .routes.cloudinit import router as cloudinit_router
from .routes.events import router as events_router
from .routes.files import router as files_router
from .routes.health import router as health_router
from .routes.images import router as images_router
from .routes.ipxe import router as ipxe_router
from .routes.machines import router as machines_router
from .routes.settings_pages import router as settings_router
from .routes.windows_seeds import router as windows_router
from .settings import get_settings
from .version import get_app_version
from .web import redirect_login, wants_html


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if rate_limit_exceeded(request):
            return rate_limit_rejection_response(request)
        if not csrf_origin_allowed(request):
            return csrf_rejection_response(request)
        response = await call_next(request)
        token = getattr(request.state, "session_refresh", None)
        if token:
            response.set_cookie(
                "session",
                token,
                **session_cookie_settings(secure=session_cookie_secure(request)),
            )
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    init_db()
    app = FastAPI(
        title="home-lab-pxe",
        version=get_app_version(),
        docs_url="/docs" if settings.openapi_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.openapi_enabled else None,
    )
    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(exist_ok=True)
    favicon_file = static_dir / "favicon.png"

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(favicon_file, media_type="image/png")

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.add_middleware(SecurityMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(ipxe_router)
    app.include_router(cloudinit_router)
    app.include_router(windows_router)
    app.include_router(events_router)
    app.include_router(boot_files_router)
    app.include_router(machines_router)
    app.include_router(images_router)
    app.include_router(boot_menu_router)
    app.include_router(files_router)
    app.include_router(settings_router)
    app.include_router(activity_router)

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException):
        if exc.status_code == 401 and wants_html(request):
            return redirect_login()
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app
