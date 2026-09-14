from fastapi import APIRouter

from ..version import get_app_version

router = APIRouter(tags=["health"])


@router.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "version": get_app_version()}
