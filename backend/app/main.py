from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.health import router as health_router
from app.api.routes.helpdesk import router as helpdesk_router
from app.core.config import get_settings
from app.services.exceptions import AuthorizationError, IntegrationError, ResourceNotFoundError

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    summary="Orquestrador inicial de atendimento e automação de infraestrutura.",
)


@app.exception_handler(IntegrationError)
async def integration_error_handler(
    request: Request,
    exc: IntegrationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


@app.exception_handler(AuthorizationError)
async def authorization_error_handler(
    request: Request,
    exc: AuthorizationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


@app.exception_handler(ResourceNotFoundError)
async def resource_not_found_handler(
    request: Request,
    exc: ResourceNotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


app.include_router(health_router)
app.include_router(helpdesk_router, prefix=settings.api_prefix)
