from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.api.runtime_dashboard import build_runtime_dashboard_html
from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def healthcheck(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@router.get("/ops", include_in_schema=False, response_class=HTMLResponse)
def runtime_dashboard(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    return HTMLResponse(build_runtime_dashboard_html(api_prefix=settings.api_prefix))
