from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()


@router.get("/health", tags=["health"])
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "supabase": {
            "configured": settings.supabase_configured,
            "service_role_configured": settings.service_role_configured,
        },
    }
